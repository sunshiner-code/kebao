"""承保风控智能体配置包（AG-UW）。"""

from __future__ import annotations

from ...analytics.fraud import fraud_models
from ...db import session_scope
from ...harness.agent import AgentProfile, Dimension, ReflectionRule, register_profile
from ...knowledge.maturity import component_start_mode
from ...models import Application, Product
from ..common import create_artifact, freeze_artifact
from . import llm_bindings  # noqa: F401
from . import reasoning  # noqa: F401
from . import tools  # noqa: F401


def _start_mode(inputs: dict) -> dict:
    with session_scope() as s:
        app = s.get(Application, inputs.get("application_id"))
        lines = ((app.declared or {}).get("lines") or s.get(Product, app.product_id).lines) if app else []
    sm = component_start_mode(lines, "cases")
    return {"start_mode": sm["start_mode"], "degrade_level": "D0" if sm["start_mode"] == "warm" else "D2", "reasons": sm["reasons"]}


def _initialize(ctx) -> None:
    with session_scope() as s:
        app = s.get(Application, ctx.inputs["application_id"])
        app.status = "underwriting"
        app.uw_run_id = ctx.run_id
        from ...models import Enterprise

        ent = s.get(Enterprise, app.enterprise_id)
        ctx.workspace["memory_subjects"] = [ent.name]
        ctx.workspace["memory_query"] = f"核保 {ent.track} {ent.tech_field}"


def _ws(ctx, key, default=None):
    return ctx.workspace.get(key, default)


def d_materials(ctx, _):
    res = _ws(ctx, "material_check", [])
    return len(res), bool(res) and all(r["status"] in ("provided", "missing", "invalid") for r in res), "材料清单逐项有结论"


def d_score_basis(ctx, _):
    subs = (_ws(ctx, "scorecard") or {}).get("subs", [])
    bad = [s["name"] for s in subs if not s.get("factors")]
    return len(subs), len(subs) == 5 and not bad, "五个分项均有贡献因素" if not bad else "缺少依据：" + "、".join(bad)


def d_pattern_evidence(ctx, _):
    pats = (_ws(ctx, "fraud") or {}).get("patterns", [])
    bad = [p["name"] for p in pats if not p.get("evidence")]
    return len(pats), not bad, "每个可疑模式均有证据" if not bad else "缺少证据：" + "、".join(bad)


def d_decision(ctx, _):
    d = _ws(ctx, "decision") or {}
    ok = bool(d.get("reasons")) and (d.get("decision") != "conditional" or bool(d.get("conditions")))
    return d.get("label"), ok, "决策有理由，附加条件有试算保费" if ok else "决策缺少理由或附加条件"


def d_extraction(ctx, _):
    docs = (_ws(ctx, "elements") or {}).get("docs", {})
    vals = [d["confidence"] for d in docs.values()]
    avg = sum(vals) / len(vals) if vals else 0
    return round(avg, 3), avg >= 0.85, f"关键要素平均置信度 {avg:.2f}"


def d_coverage(ctx, _):
    checks = _ws(ctx, "consistency", [])
    rate = sum(1 for c in checks if c["status"] != "无法核验") / len(checks) if checks else 0
    return round(rate, 3), rate >= 0.9, f"可核验项实际核验比例 {rate:.0%}"


def d_model(ctx, _):
    fraud_models.ensure()
    auc = fraud_models.metrics["auc"]
    ok = auc >= 0.8 if ctx.state.get("start_mode") == "warm" else True
    return auc, ok, f"监督模型保留集曲线下面积 {auc:.3f}，预警比例 10% 时召回 {fraud_models.metrics['recall_at_10pct']:.0%}"


def d_opinion(ctx, aux):
    item = (aux.get("review") or {}).get("scores", {}).get("opinion_quality")
    if not item:
        return None, False, aux.get("review_error", "评审不可用")
    return item["score"], item["score"] >= 4, item["reason"]


FINAL_RUBRIC = [
    Dimension("materials", "材料核对", "hard", d_materials, "100%", category="格式与契约错误"),
    Dimension("score_basis", "评分依据", "hard", d_score_basis, "100%", category="推理错误"),
    Dimension("pattern_evidence", "可疑模式证据", "hard", d_pattern_evidence, "100%", category="证据不足"),
    Dimension("decision", "决策说理", "hard", d_decision, "100%", category="推理错误"),
    Dimension("extraction", "抽取置信度", "metric", d_extraction, "≥ 0.85", category="工具失败"),
    Dimension("coverage", "核验覆盖", "metric", d_coverage, "≥ 90%", category="证据不足"),
    Dimension("model", "反欺诈区分度", "metric", d_model, "≥ 0.80", category="推理错误"),
    Dimension("opinion_quality", "意见书质量", "review", d_opinion, "≥ 4 分"),
]

REFLECTION_RULES = [
    ReflectionRule(["extraction"], "工具失败", "重新抽取低置信度材料要素", lambda ctx, info: [{"content": "重新抽取材料要素", "tool": "extract_material_elements"}, {"content": "重新核验", "tool": "verify_consistency"}]),
    ReflectionRule(["coverage", "pattern_evidence"], "证据不足", "补充外部核验并重新研判欺诈", lambda ctx, info: [{"content": "重新核验", "tool": "verify_consistency"}, {"content": "重新研判欺诈", "tool": "run_fraud_models"}, {"content": "重新评分", "tool": "score_application"}, {"content": "重新给出决策建议", "tool": "recommend_decision"}, {"content": "重写核保意见书", "tool": "draft_uw_opinion"}]),
    ReflectionRule(["decision", "opinion_quality", "score_basis"], "推理错误", "回到证据重新组织理由与意见书", lambda ctx, info: [{"content": "重新评分", "tool": "score_application"}, {"content": "重新给出决策建议", "tool": "recommend_decision"}, {"content": "重写核保意见书", "tool": "draft_uw_opinion"}]),
    ReflectionRule(["*human"], "证据不足", "按核保员意见补充核验", lambda ctx, info: [{"content": "重新核验", "tool": "verify_consistency"}, {"content": "重新研判欺诈", "tool": "run_fraud_models"}, {"content": "重新评分", "tool": "score_application"}, {"content": "重新给出决策建议", "tool": "recommend_decision"}, {"content": "重写核保意见书", "tool": "draft_uw_opinion"}]),
    ReflectionRule(["*human"], "推理错误", "按核保员意见重新评分", lambda ctx, info: [{"content": "重新评分", "tool": "score_application"}, {"content": "重新给出决策建议", "tool": "recommend_decision"}, {"content": "重写核保意见书", "tool": "draft_uw_opinion"}]),
]


def _decision_card(ctx) -> dict:
    d = ctx.workspace["decision"]
    out = ctx.workspace.get("final_output", {})
    return {
        "card_type": "decision",
        "title": f"核保决定：{ctx.workspace['application']['app_no']} {ctx.workspace['enterprise']['name']}",
        "reason": f"智能体建议{d['label']}（总分 {ctx.workspace['scorecard']['total']:.0f}，欺诈概率 {ctx.workspace['fraud']['probability']:.0%}）。需 {d['approval_level']} 级核保权限。",
        "impact": "核保决定后生成保单或拒保通知",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "application_id": ctx.inputs["application_id"], "approval_level": d["approval_level"], "link": f"/underwriting/{ctx.inputs['application_id']}"},
        "options": [
            {"key": "approve", "label": f"按建议决定（{d['label']}）", "primary": True},
            {"key": "option:standard", "label": "标准承保"},
            {"key": "option:conditional", "label": "附条件承保"},
            {"key": "option:decline", "label": "拒保", "requires_comment": True},
            {"key": "reject", "label": "退回重新核保", "requires_comment": True},
        ],
        "assignee_role": "service",
        "deadline_hours": 24,
    }


def _finalize(ctx) -> dict:
    ws = ctx.workspace
    data = {k: ws.get(k) for k in ("application", "product", "material_check", "elements", "consistency", "background", "technical", "fraud", "scorecard", "decision", "opinion")}
    data["enterprise"] = {k: ws["enterprise"][k] for k in ("id", "name", "credit_code", "track", "tech_field", "region")}
    data["product"] = {k: ws["product"][k] for k in ("id", "name", "lines", "version")}
    data["start_mode"] = ctx.state.get("start_mode")
    art = create_artifact(kind="uw_report", data=data, title=f"{ws['enterprise']['name']}核保报告", run_id=ctx.run_id, ref_type="application", ref_id=ws["application"]["id"], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    ctx.inputs["artifact_key"] = art["artifact_key"]
    with session_scope() as s:
        app = s.get(Application, ws["application"]["id"])
        app.status = "pending_decision"
        app.report_artifact_key = art["artifact_key"]
    return {"summary": f"建议{ws['decision']['label']}，保费 {ws['decision']['premium']:.0f} 元", "artifact_key": art["artifact_key"], "version": art["version"], "decision": ws["decision"]["decision"]}


def _on_confirm(ctx, res: dict) -> dict:
    from ...services import business

    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    action = res.get("action")
    chosen = {"approve": ctx.workspace["decision"]["decision"], "option:standard": "standard", "option:conditional": "conditional", "option:decline": "reject"}.get(action, ctx.workspace["decision"]["decision"])
    if chosen == "manual":
        chosen = "conditional"
    result = business.apply_uw_decision(ctx.inputs["application_id"], chosen, ctx.workspace["decision"], res, ctx.run_id)
    return {"summary": result["summary"], **out}


profile = register_profile(
    AgentProfile(
        agent_id="AG-UW",
        name="承保风控智能体",
        version="1.0.0",
        core_skill="SK-04",
        shared_skills=["SK-S1", "SK-S2", "SK-S3"],
        description="基于多模态材料与大模型提取风险要素，融合历史数据、经营状况、行业背景的反欺诈模型识别可疑模式，输出承保前风险评分与决策建议。",
        initialize=_initialize,
        finalize=_finalize,
        start_mode=_start_mode,
        final_rubric=FINAL_RUBRIC,
        review_task="uw.review",
        review_inputs=lambda ctx: {"paragraphs": (ctx.workspace.get("opinion") or {}).get("paragraphs", []), "needs_conditions": (ctx.workspace.get("decision") or {}).get("decision") == "conditional", "_level": 1},
        reflection_rules=REFLECTION_RULES,
        hitl_builders={"decision": _decision_card},
        final_confirm="decision",
        on_confirm=_on_confirm,
        entity_facts=lambda ctx: [{"subject": ctx.workspace["enterprise"]["name"], "facts": {"核保结论": ctx.workspace["decision"]["label"], "核保评分": ctx.workspace["scorecard"]["total"], "欺诈概率": ctx.workspace["fraud"]["probability"]}}],
        case_summary=lambda ctx: {"subject": ctx.workspace["enterprise"]["name"], "title": f"核保案例：{ctx.workspace['enterprise']['name']}", "text": f"{ctx.workspace['product']['name']}；评分 {ctx.workspace['scorecard']['total']:.0f}；建议 {ctx.workspace['decision']['label']}；可疑模式 {'、'.join(p['name'] for p in ctx.workspace['fraud']['patterns']) or '无'}", "level": 2},
        priority=1,
        pass_score=75,
    )
)
