"""风险预警智能体配置包（AG-RW）：风险管理报告（主运行）、监测方案生成、预警研判、事故响应四种运行方式。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from ...db import session_scope, utcnow
from ...harness.agent import AgentProfile, Dimension, ReflectionRule, register_profile
from ...knowledge.catalog import LINES, PLAYBOOKS
from ...knowledge.maturity import component_start_mode
from ...models import Alert, Incident, MemoryEntry, Notification, Policy, Product
from ...textutil import format_amount
from ..common import create_artifact, freeze_artifact, load_policy_bundle
from . import llm_bindings  # noqa: F401
from . import reasoning  # noqa: F401
from . import tools  # noqa: F401
from .reasoning import REPORT_SECTIONS
from .tools import LEVEL_LABELS, _authorizations

SHARED = ["SK-S1", "SK-S2", "SK-S3"]


def _start_mode(inputs: dict) -> dict:
    pid = inputs.get("policy_id")
    lines: list[str] = []
    if pid:
        with session_scope() as s:
            pol = s.get(Policy, pid)
            if pol is not None:
                lines = (pol.coverage or {}).get("lines") or s.get(Product, pol.product_id).lines
    sm = component_start_mode(lines, "cases")
    return {"start_mode": sm["start_mode"], "degrade_level": "D0" if sm["start_mode"] == "warm" else "D1", "reasons": sm["reasons"]}


def _initialize(ctx) -> None:
    b = load_policy_bundle(ctx.inputs["policy_id"])
    ctx.workspace["bundle"] = b
    ent = b["enterprise"]
    ctx.workspace["memory_subjects"] = [ent["name"]]
    ctx.workspace["memory_query"] = f"风险预警 {ent['track']} " + " ".join(LINES[l]["name"] for l in b["product"]["lines"] if l in LINES)
    with session_scope() as s:
        rows = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "experience", MemoryEntry.skill_id == "SK-05", MemoryEntry.status.in_(["active", "trial"]))).all()
        ctx.workspace["memory_hints"] = [r.text for r in rows]


# ================================================================ 风险管理报告（主运行）
def d_sections(ctx, _):
    secs = (ctx.workspace.get("report") or {}).get("sections", [])
    keys = {s["key"] for s in secs if s.get("paragraphs")}
    missing = [t for k, t in REPORT_SECTIONS if k not in keys]
    return len(keys), not missing, "七个部分齐全" if not missing else "缺少：" + "、".join(missing)


def d_grounded(ctx, _):
    secs = (ctx.workspace.get("report") or {}).get("sections", [])
    bad = [s["title"] for s in secs if not s.get("refs")]
    return len(bad), not bad, "每部分均引用指标、预警或统计依据" if not bad else "未引用依据：" + "、".join(bad)


def d_data(ctx, _):
    inds = (ctx.workspace.get("stats") or {}).get("indicators", [])
    with_data = sum(1 for i in inds if i.get("points"))
    auth = _authorizations(ctx.workspace["bundle"])
    ok = with_data > 0 or not any(auth.values())
    return with_data, ok, f"有数据的企业指标 {with_data} 项" if with_data else "无企业授权数据，报告以外部数据为主"


def _review_dim(ctx, aux):
    item = (aux.get("review") or {}).get("scores", {}).get("analysis_quality")
    if not item:
        return None, False, aux.get("review_error", "评审不可用")
    return item["score"], item["score"] >= 4, item["reason"]


REPORT_RUBRIC = [
    Dimension("sections", "报告七部分齐全", "hard", d_sections, "100%", category="格式与契约错误"),
    Dimension("grounded", "引用数据依据", "hard", d_grounded, "100%", category="证据不足"),
    Dimension("data", "指标数据覆盖", "metric", d_data, "≥ 1 项", category="工具失败"),
    Dimension("quality", "报告质量", "review", _review_dim, "≥ 4 分"),
]


def _report_review_inputs(ctx) -> dict:
    rep = ctx.workspace.get("report") or {}
    statements = [{"text": p, "evidence": s.get("refs", [])} for s in rep.get("sections", []) for p in s.get("paragraphs", [])]
    actions = [{"action": a["text"], "deadline_hours": 24 * 30, "owner": "客户与产品服务人员", "evidence": [a["ref"]]} for a in (ctx.workspace.get("stats") or {}).get("advice_candidates", [])]
    return {"statements": statements, "actions": actions, "sections": rep.get("sections", []), "_level": 1}


REPORT_REFLECTION = [
    ReflectionRule(["sections", "grounded", "quality"], "证据不足", "回到统计结果重新组织报告，只保留有数据依据的陈述", lambda ctx, info: [{"content": "重新撰写风险管理报告", "tool": "compose_risk_mgmt_report"}]),
    ReflectionRule(["data"], "工具失败", "扩大统计窗口重新统计", lambda ctx, info: [{"content": "重新统计", "tool": "compute_risk_statistics"}, {"content": "重新撰写风险管理报告", "tool": "compose_risk_mgmt_report"}]),
    ReflectionRule(["*human"], "推理错误", "按服务人员意见重写报告", lambda ctx, info: [{"content": "重新撰写风险管理报告", "tool": "compose_risk_mgmt_report"}]),
]


def _report_finalize(ctx) -> dict:
    b = ctx.workspace["bundle"]
    sc = ctx.workspace["scope"]
    st = ctx.workspace["stats"]
    data = {"policy": b["policy"], "enterprise": {k: b["enterprise"][k] for k in ("id", "name", "track")}, "scope": sc, "stats": st, "report": ctx.workspace["report"], "start_mode": ctx.state.get("start_mode"), "sent": None}
    art = create_artifact(kind="risk_mgmt_report", data=data, title=f"{b['enterprise']['name']}{sc['period_label']}", run_id=ctx.run_id, ref_type="policy", ref_id=b["policy"]["id"], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    ctx.inputs["artifact_key"] = art["artifact_key"]
    return {"summary": ctx.workspace["report"]["summary"], "artifact_key": art["artifact_key"], "version": art["version"]}


def _report_card(ctx) -> dict:
    out = ctx.workspace.get("final_output", {})
    return {
        "card_type": "result_confirm",
        "title": f"审阅风险管理报告：{ctx.workspace['bundle']['enterprise']['name']}",
        "reason": ctx.workspace["report"]["summary"],
        "impact": "确认后报告定稿，可编辑后发送客户（对外发送需再次确认）",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "link": f"/monitoring/reports/{out.get('artifact_key')}"},
        "options": [{"key": "approve", "label": "确认定稿", "primary": True}, {"key": "reject", "label": "退回修改", "requires_comment": True}],
        "assignee_role": "service",
        "deadline_hours": 72,
    }


def _report_confirm(ctx, res: dict) -> dict:
    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    with session_scope() as s:
        s.add(Notification(role="service", kind="report", title=f"风险管理报告已定稿：{ctx.workspace['bundle']['enterprise']['name']}", body="可在报告页编辑后发送客户", link=f"/monitoring/reports/{out['artifact_key']}"))
    return {"summary": f"{ctx.workspace['scope']['period_label']} V{out['version']} 已定稿", **out}


# ================================================================ 监测方案生成
def d_methods(ctx, _):
    cov = ctx.workspace.get("method_coverage", [])
    bad = [c["name"] for c in cov if not c["ok"]]
    return len(cov) - len(bad), bool(cov) and not bad, "每个险种至少 2 种方法且含专项检测" if not bad else "未满足：" + "、".join(bad)


def d_indicator_fields(ctx, _):
    inds = ctx.workspace.get("indicators", [])
    bad = [i["id"] for i in inds if not (i.get("source") and i.get("methods") and i.get("frequency") and i.get("threshold") is not None and i.get("levels") and i.get("liability"))]
    return len(inds), bool(inds) and not bad, f"{len(inds)} 项指标字段完整" if not bad else "字段缺失：" + "、".join(bad)


def d_risk_coverage(ctx, _):
    risks = [r for r in ctx.workspace.get("risks", []) if r.get("level") in ("高", "极高")]
    if not risks:
        return 1.0, True, "无高风险需要覆盖"
    subs = {s for i in ctx.workspace.get("indicators", []) if i.get("status") == "active" for s in i.get("subtypes", [])}
    covered = sum(1 for r in risks if r["subtype"] in subs)
    rate = covered / len(risks)
    return round(rate, 3), rate >= 0.9, f"高风险被指标覆盖 {covered}/{len(risks)}"


def d_authorization(ctx, _):
    inds = ctx.workspace.get("indicators", [])
    auth = ctx.workspace.get("authorizations", {})
    bad = [i["id"] for i in inds if i["source"] in ("iot", "log", "ops") and not auth.get(i["source"], True) and i.get("status") != "degraded"]
    return len(bad), not bad, "未授权指标均已降级" if not bad else "未授权仍启用：" + "、".join(bad)


PLAN_RUBRIC = [
    Dimension("methods", "方法覆盖", "hard", d_methods, "100%", category="约束冲突"),
    Dimension("fields", "指标字段完整", "hard", d_indicator_fields, "100%", category="格式与契约错误"),
    Dimension("authorization", "授权校验", "hard", d_authorization, "100%", category="约束冲突"),
    Dimension("risk_coverage", "高风险覆盖", "metric", d_risk_coverage, "≥ 90%", category="约束冲突"),
]

PLAN_REFLECTION = [
    ReflectionRule(["methods", "risk_coverage"], "约束冲突", "补充专项检测与事件监测指标", lambda ctx, info: [{"content": "补充监测方法", "tool": "supplement_indicators"}]),
    ReflectionRule(["authorization", "fields"], "格式与契约错误", "重新生成指标并校验授权", lambda ctx, info: [{"content": "重新生成监测指标", "tool": "generate_indicators"}, {"content": "重新校验授权", "tool": "check_data_authorization"}]),
    ReflectionRule(["*human"], "约束冲突", "按服务人员意见调整监测方案", lambda ctx, info: [{"content": "重新生成监测指标", "tool": "generate_indicators"}, {"content": "重新校验授权", "tool": "check_data_authorization"}, {"content": "补充监测方法", "tool": "supplement_indicators"}]),
]


def _plan_finalize(ctx) -> dict:
    b = ctx.workspace["bundle"]
    data = {"policy": b["policy"], "enterprise": {k: b["enterprise"][k] for k in ("id", "name", "track")}, "indicators": ctx.workspace["indicators"], "method_coverage": ctx.workspace.get("method_coverage"), "authorizations": ctx.workspace["authorizations"], "gaps": ctx.workspace.get("plan_gaps", []), "uw_points": ctx.workspace.get("uw_points", []), "risk_report_ref": ctx.workspace.get("risk_report_ref"), "report_cycle": "月度", "start_mode": ctx.state.get("start_mode")}
    art = create_artifact(kind="monitor_plan", data=data, title=f"监测方案：{b['policy']['policy_no']}", run_id=ctx.run_id, ref_type="policy", ref_id=b["policy"]["id"], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    ctx.inputs["artifact_key"] = art["artifact_key"]
    n = len(ctx.workspace["indicators"])
    deg = sum(1 for i in ctx.workspace["indicators"] if i.get("status") == "degraded")
    return {"summary": f"监测方案：指标 {n} 项（降级 {deg} 项），方法 {'、'.join(sorted({m for i in ctx.workspace['indicators'] for m in i['methods']}))}", "artifact_key": art["artifact_key"], "version": art["version"]}


def _plan_card(ctx) -> dict:
    out = ctx.workspace.get("final_output", {})
    return {
        "card_type": "result_confirm",
        "title": f"确认监测方案：{ctx.workspace['bundle']['policy']['policy_no']} {ctx.workspace['bundle']['enterprise']['name']}",
        "reason": out.get("summary", ""),
        "impact": "确认后监测方案发布并注册到常驻监测；超时按自动方案生效",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "link": f"/policies/{ctx.workspace['bundle']['policy']['id']}?tab=plan"},
        "options": [{"key": "approve", "label": "确认发布", "primary": True}, {"key": "reject", "label": "调整后发布", "requires_comment": True}],
        "assignee_role": "service",
        "default_action": "approve",
        "deadline_hours": 24,
    }


def _plan_confirm(ctx, res: dict) -> dict:
    from ...models import Enterprise
    from ...services import monitoring

    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    b = ctx.workspace["bundle"]
    with session_scope() as s:
        ent = s.get(Enterprise, b["enterprise"]["id"])
        s.expunge(ent)
    scenario = monitoring.scenario_for(ent, b["product"]["lines"], ctx.workspace.get("unpatched", 0), ctx.workspace["authorizations"], monitoring.DEFAULT_BACKFILL_HOURS)
    sim_events = [{"at_hour": monitoring.DEFAULT_BACKFILL_HOURS + e["offset_h"], **{k: e[k] for k in ("enterprise", "source", "title", "content")}} for e in (ent.profile or {}).get("simulation", {}).get("events", [])]
    plan_id = monitoring.register_plan(b["policy"]["id"], ctx.workspace["indicators"], run_id=ctx.run_id, scenario=scenario, scheduled_events=sim_events)
    with session_scope() as s:
        s.add(Notification(role="service", kind="monitor", title=f"监测方案已发布：{b['policy']['policy_no']}", body=out["summary"], link=f"/policies/{b['policy']['id']}?tab=monitor"))
    return {"summary": f"监测方案已发布并注册到常驻监测（方案 {plan_id}）", "plan_id": plan_id, **out}


# ================================================================ 预警研判
def d_evidence(ctx, _):
    if ctx.workspace.get("alert") is None and (ctx.workspace.get("grading") or {}).get("suppressed"):
        return "已抑制", True, "信号被抑制，未发布预警"
    an = ctx.workspace.get("analysis") or {}
    ev = {e["id"] for e in ctx.workspace.get("evidence", [])}
    items = an.get("causes", []) + an.get("statements", [])
    bad = [i.get("text", "")[:20] for i in items if not set(i.get("evidence") or []) & ev]
    return len(ev), bool(ev) and not bad, f"{len(ev)} 条证据，陈述均有引用" if not bad else "无证据引用：" + "；".join(bad)


def d_liability(ctx, _):
    g = ctx.workspace.get("grading") or {}
    if g.get("level") not in ("orange", "red"):
        return g.get("level"), True, "黄色及以下不强制"
    imp = ctx.workspace.get("impact") or {}
    ok = bool(imp.get("liabilities")) or imp.get("relation") == "无直接关联"
    return imp.get("relation"), ok, f"关联保险责任：{'、'.join(imp.get('liabilities', [])) or imp.get('relation')}"


def d_actions(ctx, _):
    acts = (ctx.workspace.get("analysis") or {}).get("actions", [])
    ok_n = sum(1 for a in acts if a.get("deadline_hours") and a.get("owner"))
    rate = ok_n / len(acts) if acts else 0
    return round(rate, 3), rate >= 1.0, f"建议动作 {len(acts)} 条，含时限与负责方 {ok_n} 条"


def d_latency(ctx, _):
    lat = ctx.workspace.get("latency_s")
    if lat is None:
        return None, True, "无时延记录"
    return round(lat, 1), lat <= 60, f"从候选事件生成到发布 {lat:.1f} 秒"


ALERT_RUBRIC = [
    Dimension("evidence", "预警依据", "hard", d_evidence, "100%", category="证据不足"),
    Dimension("liability", "责任关联", "hard", d_liability, "100%", category="推理错误"),
    Dimension("actions", "建议可执行", "metric", d_actions, "100%", category="推理错误"),
    Dimension("latency", "检测时延", "metric", d_latency, "≤ 60 秒", category="工具失败"),
    Dimension("quality", "研判质量", "review", _review_dim, "≥ 4 分"),
]

ALERT_REFLECTION = [
    ReflectionRule(["evidence", "quality", "actions"], "证据不足", "重新组织研判说明，只保留有证据的陈述", lambda ctx, info: [{"content": "重新研判（严格证据）", "tool": "analyze_alert", "args": {"strict": "1"}}, {"content": "更新预警", "tool": "publish_alert"}]),
    ReflectionRule(["liability"], "推理错误", "重新评估与保险责任的关联", lambda ctx, info: [{"content": "重新评估影响", "tool": "assess_alert_impact"}, {"content": "重新研判", "tool": "analyze_alert", "args": {"strict": "1"}}, {"content": "更新预警", "tool": "publish_alert"}]),
]


def _alert_review_inputs(ctx) -> dict:
    an = ctx.workspace.get("analysis") or {}
    return {"statements": an.get("causes", []) + an.get("statements", []), "actions": an.get("actions", []), "_level": 1}


def _suspected_incident(ctx) -> bool:
    g = ctx.workspace.get("grading") or {}
    al = ctx.workspace.get("alert") or {}
    return bool(al) and g.get("level") == "red" and g.get("incident")


def _incident_card(ctx) -> dict | None:
    if not _suspected_incident(ctx):
        return None
    al = ctx.workspace["alert"]
    imp = ctx.workspace["impact"]
    with session_scope() as s:
        existing = s.scalars(select(Incident).where(Incident.alert_id == al["id"])).first()
    if existing is not None:
        return None
    return {
        "card_type": "incident_start",
        "title": f"启动事故响应：{al['title']}",
        "reason": f"红色预警 {al['alert_no']} 判定为疑似事故；潜在损失 {format_amount(imp['loss_range'][0])}–{format_amount(imp['loss_range'][1])}。" if imp.get("loss_range") else f"红色预警 {al['alert_no']} 判定为疑似事故。",
        "impact": "事故响应宁早勿晚：1 小时内未处理将自动启动并通知负责人",
        "context": {
            "alert_id": al["id"],
            "link": f"/monitoring/alerts/{al['id']}",
            "analysis": {
                "explanation": (ctx.workspace.get("analysis") or {}).get("explanation"),
                "causes": (ctx.workspace.get("analysis") or {}).get("causes") or [],
                "actions": (ctx.workspace.get("analysis") or {}).get("actions") or [],
                "loss_range": imp.get("loss_range"),
                "liabilities": imp.get("liabilities") or [],
                "relation": imp.get("relation"),
            },
        },
        "options": [{"key": "approve", "label": "立即启动事故响应", "primary": True}, {"key": "option:hold", "label": "暂不启动（已核实非事故）", "requires_comment": True}],
        "assignee_role": "service",
        "default_action": "approve",
        "deadline_hours": 1,
    }


def _apply_incident(ctx, res: dict) -> str:
    from ...services import business

    al = ctx.workspace["alert"]
    if res.get("action") == "approve":
        imp = ctx.workspace["impact"]
        occurred = datetime.fromisoformat(ctx.workspace["candidate"]["sim_time"])
        desc = (ctx.workspace.get("analysis") or {}).get("explanation", al["title"])
        inc_id = business.start_incident(ctx.inputs["policy_id"], alert_id=al["id"], incident_type=imp["risk_name"], description=desc, occurred_at=occurred, user_id=res.get("user_id"))
        with session_scope() as s:
            a = s.get(Alert, al["id"])
            a.status = "escalated"
            a.handling = list(a.handling or []) + [{"at": utcnow().isoformat() + "Z", "action": "启动事故响应", "note": "超时自动启动" if res.get("system") else (res.get("comment") or "服务人员确认启动"), "by": res.get("user_id") or "系统"}]
        ctx.workspace["incident_id"] = inc_id
        return f"已启动事故响应（事故 {inc_id}）"
    with session_scope() as s:
        a = s.get(Alert, al["id"])
        a.status = "processing"
        a.handling = list(a.handling or []) + [{"at": utcnow().isoformat() + "Z", "action": "暂不启动事故响应", "note": res.get("comment", ""), "by": res.get("user_id")}]
    return "服务人员确认暂不启动事故响应"


def _alert_finalize(ctx) -> dict:
    al = ctx.workspace.get("alert")
    if al is None:
        return {"summary": f"候选事件已抑制：{ctx.workspace['grading']['suppressed']}", "suppressed": True}
    return {"summary": f"{LEVEL_LABELS[al['level']]}预警 {al['alert_no']}：{al['title']}", "alert_id": al["id"], "alert_no": al["alert_no"], "level": al["level"], "incident_id": ctx.workspace.get("incident_id")}


# ================================================================ 事故响应
def d_response_complete(ctx, _):
    r = ctx.workspace.get("response") or {}
    parts = {"预案步骤": bool(r.get("steps")), "减损措施": bool(r.get("mitigation")), "证据保全": bool(r.get("evidence")), "数据窗口": bool(ctx.workspace.get("data_window"))}
    missing = [k for k, v in parts.items() if not v]
    return 4 - len(missing), not missing, "预案、减损、证据保全、数据窗口齐全" if not missing else "缺少：" + "、".join(missing)


def d_deadlines(ctx, _):
    steps = (ctx.workspace.get("response") or {}).get("steps", [])
    bad = [s["title"] for s in steps if not s.get("due") or not s.get("owner")]
    return len(steps), bool(steps) and not bad, "每个步骤均有时限与负责方" if not bad else "缺少时限：" + "、".join(bad)


def d_providers(ctx, _):
    ps = ctx.workspace.get("providers", [])
    need = len(PLAYBOOKS.get((ctx.workspace.get("triage") or {}).get("incident_type", ""), {}).get("providers", []))
    return len(ps), len(ps) >= min(1, need), f"推荐服务商 {len(ps)} 家（预案需要 {need} 类）"


def d_mitigation_quant(ctx, _):
    mit = (ctx.workspace.get("response") or {}).get("mitigation", [])
    q = sum(1 for m in mit if m.get("quantified"))
    return q, q >= 1 or not mit, f"可量化的减损措施 {q}/{len(mit)}"


INCIDENT_RUBRIC = [
    Dimension("complete", "预案完整", "hard", d_response_complete, "100%", category="格式与契约错误"),
    Dimension("deadlines", "步骤时限", "hard", d_deadlines, "100%", category="格式与契约错误"),
    Dimension("providers", "服务商推荐", "metric", d_providers, "≥ 1", category="工具失败"),
    Dimension("mitigation", "减损效果量化", "metric", d_mitigation_quant, "≥ 1", category="推理错误"),
]

INCIDENT_REFLECTION = [
    ReflectionRule(["complete", "deadlines", "mitigation"], "格式与契约错误", "重新调取预案并补全", lambda ctx, info: [{"content": "重新生成应急预案", "tool": "build_response_plan"}, {"content": "重新打包数据窗口", "tool": "package_data_window"}, {"content": "更新预报案草稿", "tool": "draft_pre_claim"}]),
    ReflectionRule(["providers"], "工具失败", "扩大服务商检索范围", lambda ctx, info: [{"content": "重新推荐服务商", "tool": "recommend_providers"}]),
    ReflectionRule(["*human"], "推理错误", "按服务人员意见修改预报案", lambda ctx, info: [{"content": "重新初判", "tool": "triage_incident"}, {"content": "重新生成应急预案", "tool": "build_response_plan"}, {"content": "更新预报案草稿", "tool": "draft_pre_claim"}]),
]


def _dispatch_card(ctx) -> dict:
    ps = ctx.workspace.get("providers", [])
    return {
        "card_type": "provider_dispatch",
        "title": f"服务商调度：{ctx.workspace['incident']['incident_no']}",
        "reason": "建议通知以下约定服务商：" + "；".join(f"{p['name']}（{p['category']}，承诺 {p['response_hours']} 小时响应）" for p in ps),
        "impact": "服务商通知属于对外动作，确认后发送；未确认将保持等待并升级提醒",
        "context": {"providers": ps, "incident_id": ctx.inputs["incident_id"], "link": f"/monitoring/incidents/{ctx.inputs['incident_id']}"},
        "options": [{"key": "approve", "label": "确认并通知服务商", "primary": True}, {"key": "option:self", "label": "客户自行安排", "requires_comment": True}],
        "assignee_role": "service",
        "deadline_hours": 2,
    }


def _apply_dispatch(ctx, res: dict) -> str:
    ps = ctx.workspace.get("providers", [])
    if res.get("action") == "approve":
        now = utcnow().isoformat() + "Z"
        ctx.workspace["dispatched"] = [{**p, "notified_at": now, "by": res.get("user_id")} for p in ps]
        with session_scope() as s:
            for p in ps:
                s.add(Notification(role="service", kind="dispatch", title=f"已通知服务商：{p['name']}", body=f"事故 {ctx.workspace['incident']['incident_no']}，要求 {p['response_hours']} 小时内响应", link=f"/monitoring/incidents/{ctx.inputs['incident_id']}"))
            inc = s.get(Incident, ctx.inputs["incident_id"])
            inc.response = {**(inc.response or {}), "dispatched": ctx.workspace["dispatched"]}
        return f"已通知服务商 {len(ps)} 家"
    ctx.workspace["dispatched"] = []
    return "客户自行安排服务商：" + (res.get("comment") or "")


def _incident_finalize(ctx) -> dict:
    draft = ctx.workspace["pre_claim"]
    with session_scope() as s:
        inc = s.get(Incident, ctx.inputs["incident_id"])
        inc.status = "awaiting_claim"
        inc.response = {**(inc.response or {}), **{k: ctx.workspace.get(k) for k in ("triage", "response", "providers", "dispatched", "data_window")}, "pre_claim": draft}
    return {"summary": f"事故响应完成：{draft['incident_type_name']}，估损 {format_amount(draft['estimated_loss'])}，待确认预报案", "incident_id": ctx.inputs["incident_id"], "pre_claim": draft}


def _pre_claim_card(ctx) -> dict:
    d = ctx.workspace["pre_claim"]
    window = d.get("data_window") if isinstance(d.get("data_window"), dict) else {}
    return {
        "card_type": "result_confirm",
        "title": f"确认预报案：{d['incident_no']}",
        "reason": f"{d['incident_type_name']}；涉及{'、'.join(d['liabilities'])}；估损 {format_amount(d['estimated_loss'])}；附数据窗口与处置记录。",
        "impact": "确认后生成理赔案件并移交定损理赔智能体",
        "context": {
            "incident_id": ctx.inputs["incident_id"],
            "link": f"/monitoring/incidents/{ctx.inputs['incident_id']}",
            "artifact_key": window.get("artifact_key"),
            "version": window.get("version"),
            "preview": {
                "incident_type_name": d.get("incident_type_name"),
                "description": d.get("description"),
                "liabilities": d.get("liabilities") or [],
                "estimated_loss": d.get("estimated_loss"),
                "loss_range": d.get("loss_range"),
                "parties": d.get("parties") or [],
                "handling": d.get("handling") or [],
                "providers": d.get("providers") or [],
                "data_window": {k: window.get(k) for k in ("artifact_key", "from", "to", "points", "series", "alerts")},
            },
        },
        "options": [{"key": "approve", "label": "确认并移交理赔", "primary": True}, {"key": "reject", "label": "退回修改", "requires_comment": True}],
        "assignee_role": "service",
        "deadline_hours": 24,
    }


def _pre_claim_confirm(ctx, res: dict) -> dict:
    from ...services import business

    d = ctx.workspace["pre_claim"]
    report = {"source": "pre_claim", "occurred_at": d["occurred_at"], "description": d["description"], "incident_type": d["incident_type"], "incident_type_name": d["incident_type_name"], "liabilities": d["liabilities"], "data_window": d["data_window"], "alert": d["alert"], "handling": d["handling"], "providers": d["providers"], "estimated_loss": d["estimated_loss"], "loss_range": d["loss_range"]}
    cid = business.register_claim(ctx.inputs["policy_id"], incident_id=ctx.inputs["incident_id"], report=report, claimed_amount=d["estimated_loss"], parties=d["parties"], user_id=res.get("user_id"), start=False)
    return {"summary": f"预报案已确认，生成理赔案件 {cid}，待被保险人提交理赔材料后启动定损", "claim_id": cid, "incident_id": ctx.inputs["incident_id"]}


def _entity_facts(ctx) -> list[dict]:
    ent = ctx.workspace["bundle"]["enterprise"]["name"]
    al = ctx.workspace.get("alert")
    if al:
        return [{"subject": ent, "facts": {"最近预警": f"{al['alert_no']} {LEVEL_LABELS[al['level']]} {al['title']}"}}]
    if ctx.workspace.get("pre_claim"):
        return [{"subject": ent, "facts": {"最近事故": ctx.workspace["pre_claim"]["incident_type_name"]}}]
    return []


def _alert_case(ctx) -> dict | None:
    al = ctx.workspace.get("alert")
    if not al:
        return None
    return {"subject": ctx.workspace["bundle"]["enterprise"]["name"], "title": f"预警案例：{al['title']}", "text": f"{LEVEL_LABELS[al['level']]}；方法 {'、'.join(sorted({s['method'] for s in ctx.workspace['candidate']['signals']}))}；{(ctx.workspace.get('analysis') or {}).get('explanation', '')[:160]}", "level": 1}


BASE = dict(agent_id="AG-RW", version="1.0.0", core_skill="SK-05", start_mode=_start_mode, priority=1)

plan_profile = AgentProfile(
    name="监测方案生成",
    shared_skills=SHARED,
    description="保单生效后承接风险评估与核保结论生成监测方案，校验数据接入授权并注册到常驻监测。",
    initialize=_initialize,
    finalize=_plan_finalize,
    final_rubric=PLAN_RUBRIC,
    reflection_rules=PLAN_REFLECTION,
    hitl_builders={"result_confirm": _plan_card},
    final_confirm="result_confirm",
    on_confirm=_plan_confirm,
    conditions={"coverage_gap": lambda ctx: any(not c["ok"] for c in ctx.workspace.get("method_coverage", []))},
    procedure_name="监测方案生成",
    pass_score=80,
    **BASE,
)

alert_profile = AgentProfile(
    name="预警研判",
    shared_skills=[],
    description="对常驻监测发现的候选事件做证据整理、影响评估、定级抑制、研判建议并发布预警；红色疑似事故时请求启动事故响应。",
    initialize=_initialize,
    finalize=_alert_finalize,
    final_rubric=ALERT_RUBRIC,
    review_task="rw.review",
    review_inputs=_alert_review_inputs,
    reflection_rules=ALERT_REFLECTION,
    hitl_builders={"incident_start": _incident_card},
    hitl_appliers={"incident_start": _apply_incident},
    conditions={"suspected_incident": _suspected_incident},
    entity_facts=_entity_facts,
    case_summary=_alert_case,
    procedure_name="预警研判",
    planner="procedure",
    plan_confirm=False,
    max_reflection_rounds=2,
    pass_score=75,
    **BASE,
)

incident_profile = AgentProfile(
    name="事故响应",
    shared_skills=["SK-S1", "SK-S3"],
    description="事故发生时初判事故类型与涉及责任，调取应急预案生成带时限的处置步骤、减损措施与证据保全指引，调度服务商，打包事故前后数据窗口并生成预报案。",
    initialize=_initialize,
    finalize=_incident_finalize,
    final_rubric=INCIDENT_RUBRIC,
    reflection_rules=INCIDENT_REFLECTION,
    hitl_builders={"provider_dispatch": _dispatch_card, "result_confirm": _pre_claim_card},
    hitl_appliers={"provider_dispatch": _apply_dispatch},
    final_confirm="result_confirm",
    on_confirm=_pre_claim_confirm,
    entity_facts=_entity_facts,
    procedure_name="事故响应",
    pass_score=80,
    **BASE,
)

profile = register_profile(
    AgentProfile(
        name="风险预警智能体",
        shared_skills=SHARED,
        description="保单生效后生成监测方案并常驻监测企业授权数据与外部事件，用五种通用检测与五种科技产品专项检测融合研判，分级推送预警；周期生成风险管理报告，事故时启动应急响应、打包数据窗口并移交理赔。",
        initialize=_initialize,
        finalize=_report_finalize,
        final_rubric=REPORT_RUBRIC,
        review_task="rw.review",
        review_inputs=_report_review_inputs,
        reflection_rules=REPORT_REFLECTION,
        hitl_builders={"result_confirm": _report_card},
        final_confirm="result_confirm",
        on_confirm=_report_confirm,
        entity_facts=lambda ctx: [{"subject": ctx.workspace["bundle"]["enterprise"]["name"], "facts": {"风险指数": ctx.workspace["stats"]["risk_index"]["end"], "风险等级": ctx.workspace["stats"]["risk_index"]["level"]}}],
        purposes={"plan": plan_profile, "alert": alert_profile, "incident": incident_profile},
        pass_score=75,
        **BASE,
    )
)
