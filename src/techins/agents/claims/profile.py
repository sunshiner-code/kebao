"""定损理赔智能体配置包（AG-CL）。"""

from __future__ import annotations

from sqlalchemy import select

from ...db import session_scope, utcnow
from ...harness.agent import AgentProfile, Dimension, ReflectionRule, register_profile
from ...knowledge.maturity import component_start_mode
from ...models import ClaimCase, Notification, Policy, Product
from ...textutil import format_amount
from ..common import create_artifact, freeze_artifact
from . import llm_bindings  # noqa: F401
from . import reasoning  # noqa: F401
from . import tools  # noqa: F401
from .tools import _party_tasks

CHECK_ITEMS = ["保险责任触发", "责任免除", "保险期间", "标的与被保险人一致性", "义务履行", "免赔与限额", "比例赔付", "其他保险"]


def _start_mode(inputs: dict) -> dict:
    with session_scope() as s:
        case = s.get(ClaimCase, inputs.get("claim_id"))
        lines = []
        if case:
            pol = s.get(Policy, case.policy_id)
            lines = (pol.coverage or {}).get("lines") or s.get(Product, pol.product_id).lines
    sm = component_start_mode(lines, "cases")
    return {"start_mode": sm["start_mode"], "degrade_level": "D0" if sm["start_mode"] == "warm" else "D2", "reasons": sm["reasons"]}


def _initialize(ctx) -> None:
    from ...models import Enterprise

    with session_scope() as s:
        case = s.get(ClaimCase, ctx.inputs["claim_id"])
        pol = s.get(Policy, case.policy_id)
        ent = s.get(Enterprise, pol.enterprise_id)
        ctx.workspace["memory_subjects"] = [ent.name]
        ctx.workspace["memory_query"] = f"理赔 {ent.track} {(case.report or {}).get('incident_type_name', '')}"


def _multi_party(ctx) -> bool:
    parties = (ctx.workspace.get("claim") or {}).get("parties", [])
    return len([p for p in parties if p.get("role") != "被保险人"]) >= 1 and len(parties) >= 2


# ---------------------------------------------------------------- 评估
def d_checks(ctx, _):
    checks = ctx.workspace.get("checks", [])
    have = {c["item"] for c in checks if c.get("conclusion")}
    missing = [i for i in CHECK_ITEMS if i not in have]
    return len(have), not missing, "八项核查均有结论" if not missing else "缺少：" + "、".join(missing)


def d_evidence_refs(ctx, _):
    bad = [c["item"] for c in ctx.workspace.get("checks", []) if c["item"] in ("保险责任触发", "责任免除", "保险期间", "标的与被保险人一致性") and not c.get("facts") and c["conclusion"] not in ("待证实",)]
    bad += [i["item"] for i in (ctx.workspace.get("text_loss") or {}).get("items", []) if not i.get("basis")]
    liab = ctx.workspace.get("liability") or {}
    if liab and not any(r.get("facts") for r in liab.get("reasoning", [])):
        bad.append("责任认定说理")
    return len(bad), not bad, "核查、定损与责任结论均引用证据" if not bad else "缺少证据引用：" + "、".join(bad[:6])


def d_traceable(ctx, _):
    steps = (ctx.workspace.get("adjustment") or {}).get("steps", [])
    bad = [s["step"] for s in steps if not s.get("basis")]
    return len(steps), bool(steps) and not bad, f"理算 {len(steps)} 步均有依据" if not bad else "缺少依据：" + "、".join(bad)


def d_multi_party(ctx, _):
    if not _multi_party(ctx):
        return "单方", True, "单方事故，按近因原则认定"
    r = ctx.workspace.get("responsibility") or {}
    parts = {"关联分析": bool(ctx.workspace.get("associations")), "因果图": bool(ctx.workspace.get("causal_graph")), "反事实结果": bool(r.get("counterfactual")), "比例区间": bool(r.get("intervals"))}
    missing = [k for k, v in parts.items() if not v]
    return 4 - len(missing), not missing, "关联分析、因果图、反事实结果、比例区间齐全" if not missing else "缺少：" + "、".join(missing)


def d_two_types(ctx, _):
    has_img = bool((ctx.workspace.get("parsed") or {}).get("photos"))
    if not has_img:
        return "无图像", True, "无图像材料"
    ok = bool((ctx.workspace.get("image_loss") or {}).get("items")) and ctx.workspace.get("cross_check") is not None
    return ok, ok, "已执行图像定损并与文本交叉校验" if ok else "有图像材料但未完成图像定损或交叉校验"


def d_parse(ctx, _):
    c = ctx.workspace.get("parse_confidence", 0)
    return c, bool(c >= 0.9 or ctx.state.get("fields_checked")), f"关键字段平均置信度 {c:.2f}" + ("（已人工核对）" if ctx.state.get("fields_checked") else "")


def d_diff(ctx, _):
    cc = ctx.workspace.get("cross_check") or {}
    diff = cc.get("text_image_diff")
    if diff is None:
        return None, True, "无可比的图像定损"
    explained = any(i["type"] in ("新旧损", "维修或更换不一致") for i in cc.get("issues", []))
    return round(diff, 3), diff <= 0.15 or bool(ctx.state.get("reconciled")), f"文本与图像差异 {diff:.0%}" + ("（已逐项修正说明）" if ctx.state.get("reconciled") else ("（存在可解释差异）" if explained else ""))


def d_width(ctx, _):
    r = ctx.workspace.get("responsibility") or {}
    if not r.get("multi_party"):
        return None, True, "非多方事故"
    w = r.get("max_width", 0)
    return round(w, 3), w <= 0.2, f"比例区间最大宽度 {w:.0%}" + ("，超出 20 个百分点须标注高不确定并转二级审批" if w > 0.2 else "")


def d_review(ctx, aux):
    item = (aux.get("review") or {}).get("scores", {}).get("reasoning_quality")
    if not item:
        return None, False, aux.get("review_error", "评审不可用")
    return item["score"], item["score"] >= 4, item["reason"]


FINAL_RUBRIC = [
    Dimension("checks", "条款核查完整", "hard", d_checks, "100%", category="格式与契约错误"),
    Dimension("evidence", "证据引用", "hard", d_evidence_refs, "100%", category="证据不足"),
    Dimension("traceable", "理算可追溯", "hard", d_traceable, "100%", category="格式与契约错误"),
    Dimension("multi_party", "多方责任方法", "hard", d_multi_party, "100%", category="推理错误"),
    Dimension("two_types", "两类定损", "hard", d_two_types, "100%", category="推理错误"),
    Dimension("parse", "解析准确", "metric", d_parse, "≥ 0.9", category="工具失败"),
    Dimension("diff", "交叉一致", "metric", d_diff, "≤ 15%", category="约束冲突"),
    Dimension("width", "比例稳定", "metric", d_width, "≤ 20 个百分点", category="证据不足"),
    Dimension("reasoning", "说理质量", "review", d_review, "≥ 4 分"),
]

STAGE_RUBRICS = {"verify_clause_coverage": [Dimension("checks", "条款核查完整", "hard", d_checks, "100%", category="格式与契约错误")]}

_tail = [{"content": "重新理算", "tool": "adjust_claim"}, {"content": "重写理赔意见", "tool": "draft_claim_opinion"}]

REFLECTION_RULES = [
    ReflectionRule(["evidence"], "证据不足", "回到材料与数据补全事实引用，缺失项标待证实", lambda ctx, info: [{"content": "重新核查条款", "tool": "verify_clause_coverage"}, {"content": "重新责任认定", "tool": "assess_liability"}] + _tail),
    ReflectionRule(["diff"], "约束冲突", "逐项比对定位文本与图像差异来源并修正", lambda ctx, info: [{"content": "差异修正", "tool": "reconcile_losses"}] + _tail),
    ReflectionRule(["width"], "证据不足", "请求人工确认关键因果边后重算比例", lambda ctx, info: [{"content": "人工确认关键因果边", "hitl": "causal_confirm"}, {"content": "重新估计责任比例", "tool": "estimate_responsibility"}, {"content": "重新责任认定", "tool": "assess_liability"}] + _tail),
    ReflectionRule(["reasoning", "multi_party"], "推理错误", "回到因果链与事实重组认定说理", lambda ctx, info: [{"content": "重新责任认定", "tool": "assess_liability"}] + _tail),
    ReflectionRule(["checks", "traceable", "two_types"], "格式与契约错误", "补齐缺失环节", lambda ctx, info: [{"content": "重新核查条款", "tool": "verify_clause_coverage"}, {"content": "图像定损", "tool": "assess_image_loss"}, {"content": "交叉校验", "tool": "cross_validate"}] + _tail),
    ReflectionRule(["*human"], "证据不足", "按审批意见回到材料补全事实并扩大数据窗口复核", lambda ctx, info: [{"content": "重新核查条款（补全事实引用）", "tool": "verify_clause_coverage"}, {"content": "重新分析证据", "tool": "analyze_evidence"}, {"content": "重新确定损失范围", "tool": "determine_loss_scope"}, {"content": "重新责任认定", "tool": "assess_liability"}, {"content": "重新文本定损", "tool": "assess_text_loss"}, {"content": "交叉校验", "tool": "cross_validate"}] + _tail),
    ReflectionRule(["*human"], "推理错误", "按审批意见重新认定与理算", lambda ctx, info: [{"content": "重新责任认定", "tool": "assess_liability"}] + _tail),
]


# ---------------------------------------------------------------- 人审节点
def _supplement_card(ctx) -> dict | None:
    missing = ctx.workspace.get("missing_critical", [])
    if not missing:
        return None
    return {
        "card_type": "supplement_send",
        "title": f"补充材料通知：{ctx.workspace['claim']['claim_no']}",
        "reason": "以下必要材料缺失：" + "、".join(missing) + "。建议向被保险人发送补充材料通知；补充前相关结论将标注为待证实。",
        "impact": "对外发送需确认；不发送时按现有材料继续定损并降级处理",
        "context": {"missing": missing, "claim_id": ctx.inputs["claim_id"], "link": f"/claims/{ctx.inputs['claim_id']}"},
        "options": [{"key": "approve", "label": "发送补充材料通知", "primary": True}, {"key": "option:skip", "label": "不发送，按现有材料继续"}],
        "assignee_role": "service",
        "deadline_hours": 24,
    }


def _apply_supplement(ctx, res: dict) -> str:
    missing = ctx.workspace.get("missing_critical", [])
    if res.get("action") == "approve":
        with session_scope() as s:
            s.add(Notification(role="service", kind="claim", title=f"已发送补充材料通知：{ctx.workspace['claim']['claim_no']}", body="、".join(missing), link=f"/claims/{ctx.inputs['claim_id']}"))
            case = s.get(ClaimCase, ctx.inputs["claim_id"])
            case.report = {**(case.report or {}), "supplement_requested": {"items": missing, "at": utcnow().isoformat() + "Z"}}
        ctx.workspace["supplement_sent"] = True
        return f"已发送补充材料通知（{len(missing)} 项），相关结论暂标待证实"
    return "按现有材料继续定损"


def _field_card(ctx) -> dict | None:
    if ctx.workspace.get("parse_confidence", 1) >= 0.9:
        return None
    low = [d for d in ctx.workspace["parsed"]["docs"].values() if d["confidence"] < 0.9]
    return {
        "card_type": "field_check",
        "title": f"关键字段核对：{ctx.workspace['claim']['claim_no']}",
        "reason": "以下材料抽取置信度低于 0.9，请核对金额、日期与对象：" + "；".join(f"{d['name']}（{d['confidence']:.2f}）" for d in low),
        "impact": "核对完成后继续条款核查与定损",
        "context": {"docs": [{"kind": d["kind"], "name": d["name"], "fields": d["fields"], "confidence": d["confidence"]} for d in low], "link": f"/claims/{ctx.inputs['claim_id']}?tab=materials"},
        "options": [{"key": "approve", "label": "核对无误", "primary": True}, {"key": "option:edit", "label": "提交修正"}],
        "assignee_role": "service",
        "deadline_hours": 24,
    }


def _apply_fields(ctx, res: dict) -> str:
    edits = (res.get("edits") or {}).get("fields") or {}
    n = 0
    for kind, fields in edits.items():
        doc = ctx.workspace["parsed"]["docs"].get(kind)
        if doc:
            doc["fields"].update({str(k): str(v) for k, v in fields.items()})
            doc["confidence"] = 0.99
            n += len(fields)
    ctx.state["fields_checked"] = True
    return "关键字段已人工核对" + (f"，修正 {n} 个字段" if n else "")


def _causal_card(ctx) -> dict | None:
    g = ctx.workspace.get("causal_graph")
    if not g:
        return None
    key_edges = [e for e in g["edges"] if e.get("key") or ctx.state.get("start_mode") == "cold"]
    labels = {n["id"]: n["label"] for n in g["nodes"]}
    return {
        "card_type": "causal_confirm",
        "title": f"确认关键因果边：{ctx.workspace['claim']['claim_no']}",
        "reason": "多方事故的关键因果边需理赔人员确认：" + "；".join(f"{labels[e['from']]}→{labels[e['to']]}（强度 {e['strength']}，{e.get('note', '')}）" for e in key_edges),
        "impact": "确认后按因果图做反事实干预与原因力分摊；可修改边强度",
        "context": {"graph": g, "key_edges": key_edges, "link": f"/claims/{ctx.inputs['claim_id']}?tab=liability"},
        "options": [{"key": "approve", "label": "确认因果图", "primary": True}, {"key": "option:edit", "label": "修改边强度后确认"}],
        "assignee_role": "service",
        "deadline_hours": 48,
    }


def _apply_causal(ctx, res: dict) -> str:
    g = ctx.workspace["causal_graph"]
    changed = 0
    for e in (res.get("edits") or {}).get("edges", []):
        for ge in g["edges"]:
            if ge["from"] == e.get("from") and ge["to"] == e.get("to"):
                ge["strength"] = max(0.02, min(0.98, float(e.get("strength", ge["strength"]))))
                ge["note"] = (ge.get("note", "") + "；人工调整").strip("；")
                changed += 1
    g["confirmed"] = True
    g["confirmed_by"] = res.get("user_id")
    return "因果图已确认" + (f"，调整边强度 {changed} 条" if changed else "")


def _approval_card(ctx) -> dict:
    adj = ctx.workspace["adjustment"]
    out = ctx.workspace.get("final_output", {})
    return {
        "card_type": "claim_approval",
        "title": f"赔付审批：{ctx.workspace['claim']['claim_no']} {ctx.workspace['bundle']['enterprise']['name']}",
        "reason": f"申报 {format_amount(ctx.workspace['text_loss']['declared'])}，核定 {format_amount(adj['assessed'])}，建议赔付 {format_amount(adj['payout'])}" + (f"，代位求偿 {format_amount(adj['subrogation'])}" if adj["subrogation"] else "") + (f"，部分拒赔 {len(adj['rejected'])} 项" if adj["rejected"] else "") + f"。需 {adj['approval_level']} 级审批。",
        "impact": "审批通过后生成支付指令（需再次确认）并结案回流",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "claim_id": ctx.inputs["claim_id"], "approval_level": adj["approval_level"], "link": f"/claims/{ctx.inputs['claim_id']}"},
        "options": [{"key": "approve", "label": f"同意赔付 {format_amount(adj['payout'])}", "primary": True}, {"key": "option:adjust", "label": "调整金额后同意", "requires_comment": True}, {"key": "reject", "label": "退回复核", "requires_comment": True}],
        "assignee_role": "service",
        "deadline_hours": 48,
    }


# ---------------------------------------------------------------- 结果
def _finalize(ctx) -> dict:
    ws = ctx.workspace
    if not ws.get("adjustment"):
        raise ValueError("adjustment")
    keys = ("claim", "material_check", "parsed", "clause", "facts", "flags", "checks", "clause_detail", "evidence_analysis", "loss_scope", "parties", "associations", "causal_graph", "responsibility", "liability", "text_loss", "image_loss", "cross_check", "adjustment", "opinion", "incident_type", "lines")
    data = {k: ws.get(k) for k in keys}
    data["enterprise"] = {k: ws["bundle"]["enterprise"][k] for k in ("id", "name", "track")}
    data["policy"] = ws["bundle"]["policy"]
    data["start_mode"] = ctx.state.get("start_mode")
    art = create_artifact(kind="claim_result", data=data, title=f"{ws['claim']['claim_no']}定损理赔结果", run_id=ctx.run_id, ref_type="claim", ref_id=ctx.inputs["claim_id"], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    ctx.inputs["artifact_key"] = art["artifact_key"]
    adj = ws["adjustment"]
    with session_scope() as s:
        case = s.get(ClaimCase, ctx.inputs["claim_id"])
        case.suggested_amount = adj["payout"]
        case.result_artifact_key = art["artifact_key"]
        case.status = "pending_approval"
    return {"summary": f"建议赔付 {format_amount(adj['payout'])}（核定 {format_amount(adj['assessed'])}）", "artifact_key": art["artifact_key"], "version": art["version"], "payout": adj["payout"]}


def _on_confirm(ctx, res: dict) -> dict:
    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    adj = ctx.workspace["adjustment"]
    amount = adj["payout"]
    if res.get("action") == "option:adjust":
        from ...textutil import parse_amounts

        vals = parse_amounts(res.get("comment") or "")
        if vals:
            amount = float(vals[0])
    with session_scope() as s:
        case = s.get(ClaimCase, ctx.inputs["claim_id"])
        case.approved_amount = amount
        case.status = "approved"
        case.approvals = list(case.approvals or []) + [{"by": res.get("user_id"), "action": res.get("action"), "amount": amount, "comment": res.get("comment"), "level": adj["approval_level"], "at": utcnow().isoformat() + "Z", "deviation": round(abs(amount - adj["payout"]) / max(adj["payout"], 1), 4)}]
        s.add(Notification(role="service", kind="claim", title=f"赔付审批通过：{case.claim_no}", body=f"核定赔付 {format_amount(amount)}，请确认支付指令", link=f"/claims/{case.id}"))
    return {"summary": f"赔付审批通过，核定赔付 {format_amount(amount)}，待确认支付指令", "approved_amount": amount, **out}


def _review_inputs(ctx) -> dict:
    liab = ctx.workspace.get("liability") or {}
    return {"reasoning": liab.get("reasoning", []), "paragraphs": (ctx.workspace.get("opinion") or {}).get("paragraphs", []), "_level": 1}


def _case_summary(ctx) -> dict:
    adj = ctx.workspace.get("adjustment") or {}
    liab = ctx.workspace.get("liability") or {}
    return {"subject": ctx.workspace["bundle"]["enterprise"]["name"], "title": f"理赔案例：{ctx.workspace['claim']['claim_no']}", "text": f"{ctx.workspace['incident_type']}；近因 {liab.get('proximate_cause')}；核定 {format_amount(adj.get('assessed', 0))}，建议赔付 {format_amount(adj.get('payout', 0))}" + ("；多方比例 " + "、".join(f"{k} {v:.0%}" for k, v in (liab.get("shares") or {}).items()) if liab.get("multi_party") else ""), "level": 2}


def _child_finalize(ctx) -> dict:
    r = ctx.workspace.get("party_result")
    return {"summary": f"{r['name']}：偏离度 {r['deviation']:.2f}，{r['fault']}" if r else "参与方证据整理失败", "party": r}


BASE = dict(agent_id="AG-CL", version="1.0.0", core_skill="SK-06", start_mode=_start_mode, priority=0)

party_profile = AgentProfile(name="参与方证据整理子智能体", shared_skills=[], description="在独立上下文中整理单个参与方的行为证据。", initialize=lambda ctx: None, finalize=_child_finalize, procedure_name="参与方证据整理", planner="procedure", **BASE)

profile = register_profile(
    AgentProfile(
        name="定损理赔智能体",
        shared_skills=["SK-S1", "SK-S2", "SK-S3"],
        description="自动解析理赔文件、客户声明与保险合同条款，完成条款核查与损失证据分析；融合物联网与日志数据确定损失范围；结合事故前后数据与第三方报告认定责任，多方事故调用关联分析与因果推断引擎划分责任比例；文本与图像两类定损并理算给出赔付建议。",
        initialize=_initialize,
        finalize=_finalize,
        final_rubric=FINAL_RUBRIC,
        stage_rubrics=STAGE_RUBRICS,
        review_task="cl.review",
        review_inputs=_review_inputs,
        reflection_rules=REFLECTION_RULES,
        hitl_builders={"supplement_send": _supplement_card, "field_check": _field_card, "causal_confirm": _causal_card, "claim_approval": _approval_card},
        hitl_appliers={"supplement_send": _apply_supplement, "field_check": _apply_fields, "causal_confirm": _apply_causal},
        final_confirm="claim_approval",
        on_confirm=_on_confirm,
        conditions={
            "missing_critical": lambda ctx: bool(ctx.workspace.get("missing_critical")),
            "low_confidence": lambda ctx: ctx.workspace.get("parse_confidence", 1) < 0.9,
            "multi_party": _multi_party,
            "has_images": lambda ctx: bool((ctx.workspace.get("parsed") or {}).get("photos")),
            "needs_reconcile": lambda ctx: bool((ctx.workspace.get("cross_check") or {}).get("text_image_diff") and ctx.workspace["cross_check"]["text_image_diff"] > 0.15) or any(i["type"] in ("新旧损", "维修或更换不一致") for i in (ctx.workspace.get("cross_check") or {}).get("issues", [])),
        },
        resolvers={"party_tasks": _party_tasks},
        entity_facts=lambda ctx: [{"subject": ctx.workspace["bundle"]["enterprise"]["name"], "facts": {"最近理赔": f"{ctx.workspace['claim']['claim_no']} 建议赔付 {format_amount(ctx.workspace['adjustment']['payout'])}"}}],
        case_summary=_case_summary,
        purposes={"party_evidence": party_profile},
        pass_score=80,
    **BASE,
    )
)

_ = select
