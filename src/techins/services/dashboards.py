"""数据看板：经营、风险、智能体效能、云私计算四类看板与指标字典；自定义报表（自选指标与维度、订阅推送）。"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..knowledge.catalog import LINES, STAGES
from ..models import (
    Alert,
    Application,
    Artifact,
    ClaimCase,
    EgressLog,
    Enterprise,
    HitlCard,
    Incident,
    ModelCall,
    Organization,
    Policy,
    Product,
    ReportDefinition,
    Renewal,
    Run,
    User,
)
from .config_center import cfg
from .notify import notify

AGENT_NAMES = {"AG-RA": "风险评估", "AG-CD": "条款设计", "AG-AP": "精算定价", "AG-UW": "承保风控", "AG-RW": "风险预警", "AG-CL": "定损理赔"}

METRICS: dict[str, dict] = {
    "premium": {"name": "保费收入", "board": "经营", "unit": "元", "definition": "统计期内生效保单的签单保费（批改后保费）", "timing": "保单生效日"},
    "earned_premium": {"name": "已赚保费", "board": "经营", "unit": "元", "definition": "签单保费 × 统计期末已经过保险期间比例", "timing": "统计期末"},
    "policies_new": {"name": "新单件数", "board": "经营", "unit": "件", "definition": "统计期内生效的非续保保单件数", "timing": "保单生效日"},
    "renewal_rate": {"name": "续保率", "board": "经营", "unit": "%", "definition": "续保件数 ÷ 应续保件数（到期日在统计期内的保单）", "timing": "到期日"},
    "loss_ratio": {"name": "赔付率", "board": "经营", "unit": "%", "definition": "（已决赔款 + 未决估损）÷ 已赚保费", "timing": "统计期末"},
    "combined_ratio": {"name": "综合成本率", "board": "经营", "unit": "%", "definition": "赔付率 + 费用率（费用率取参数与字典中的系统参数）", "timing": "统计期末"},
    "claims_paid": {"name": "已决赔款", "board": "经营", "unit": "元", "definition": "统计期内结案支付的赔款", "timing": "结案日"},
    "applications": {"name": "投保件数", "board": "经营", "unit": "件", "definition": "统计期内创建的投保单", "timing": "投保单创建日"},
    "uw_pass_rate": {"name": "核保通过率", "board": "经营", "unit": "%", "definition": "已承保 ÷（已承保 + 已拒保）", "timing": "核保决定日"},
    "alerts": {"name": "预警数量", "board": "风险", "unit": "条", "definition": "统计期内发布的预警（不含被抑制信号）", "timing": "预警发布时"},
    "alert_precision": {"name": "预警精确率", "board": "风险", "unit": "%", "definition": "橙色及以上预警中被确认为有效（未标记误报）的比例，仅统计已处置预警", "timing": "处置完成时"},
    "incident_conversion": {"name": "事故转化率", "board": "风险", "unit": "%", "definition": "预警后 90 天内发生相关事故的比例", "timing": "事故启动时"},
    "fraud_flags": {"name": "欺诈识别件数", "board": "风险", "unit": "件", "definition": "核保欺诈概率达到转人工阈值或理赔命中欺诈模式的件数", "timing": "核保或理算完成时"},
    "fraud_amount": {"name": "欺诈涉及金额", "board": "风险", "unit": "元", "definition": "欺诈可疑投保的保额与理赔欺诈可疑的申报金额之和", "timing": "核保或理算完成时"},
    "runs": {"name": "运行量", "board": "效能", "unit": "次", "definition": "统计期内创建的顶层运行（不含子智能体与评测运行）", "timing": "运行创建时"},
    "success_rate": {"name": "成功率", "board": "效能", "unit": "%", "definition": "已完成 ÷（已完成 + 已失败）", "timing": "运行结束时"},
    "avg_score": {"name": "评估得分", "board": "效能", "unit": "分", "definition": "最终评估综合得分均值", "timing": "运行结束时"},
    "pass_rate": {"name": "达标率", "board": "效能", "unit": "%", "definition": "最终评估达标的运行占比", "timing": "运行结束时"},
    "reflection_rate": {"name": "反思触发率", "board": "效能", "unit": "%", "definition": "有反思的运行占比", "timing": "运行结束时"},
    "human_modification_rate": {"name": "人审修改率", "board": "效能", "unit": "%", "definition": "结果确认时被人工驳回、改选或修改的运行占比", "timing": "结果确认时"},
    "degraded_share": {"name": "降级运行占比", "board": "效能", "unit": "%", "definition": "降级等级 D2 及以上的运行占比", "timing": "运行结束时"},
    "avg_duration": {"name": "平均耗时", "board": "效能", "unit": "秒", "definition": "运行开始到结束的时长，扣除等待人工的时长", "timing": "运行结束时"},
    "cloud_calls": {"name": "云域调用", "board": "算力", "unit": "次", "definition": "统计期内云端模型调用次数", "timing": "调用时"},
    "private_calls": {"name": "私域调用", "board": "算力", "unit": "次", "definition": "统计期内私域模型（含内置引擎）调用次数", "timing": "调用时"},
    "egress": {"name": "出域次数", "board": "算力", "unit": "次", "definition": "私域数据经脱敏后出域到云端的次数", "timing": "出域时"},
    "cloud_cost": {"name": "云端费用", "board": "算力", "unit": "元", "definition": "云端模型调用费用（token × 单价）", "timing": "调用时"},
    "gpu_hours": {"name": "私域 GPU 时长", "board": "算力", "unit": "小时", "definition": "私域模型推理耗时合计（内置引擎不计）", "timing": "调用时"},
    "fallbacks": {"name": "兜底切换", "board": "算力", "unit": "次", "definition": "模型调用沿兜底链切换的次数", "timing": "调用时"},
}
DIMENSIONS = {"month": "月份", "line": "险种", "stage": "生命周期阶段", "industry": "客户行业", "org": "机构", "agent": "智能体", "product": "产品", "level": "预警等级"}


def _local(dt: datetime) -> datetime:
    return dt + timedelta(hours=8)


def _earned(p: Policy, at: datetime) -> float:
    total = max(1, (p.end_date - p.start_date).days)
    return p.premium * max(0, min((at - p.start_date).days, total)) / total


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


# ---------------------------------------------------------------- 经营看板


def business(days: int = 365, product_ids: list[int] | None = None) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    with session_scope() as s:
        q = select(Policy)
        if product_ids:
            q = q.where(Policy.product_id.in_(product_ids))
        pols = s.scalars(q).all()
        in_period = [p for p in pols if p.created_at >= since]
        products = {p.id: p for p in s.scalars(select(Product)).all()}
        ents = {e.id: e for e in s.scalars(select(Enterprise)).all()}
        apps = s.scalars(select(Application).where(Application.created_at >= since)).all()
        claims = s.scalars(select(ClaimCase)).all()
        renewals = s.scalars(select(Renewal)).all()
        users = {u.id: u for u in s.scalars(select(User)).all()}
        orgs = {o.id: o.name for o in s.scalars(select(Organization)).all()}
        app_by_id = {a.id: a for a in s.scalars(select(Application)).all()}
    pol_ids = {p.id for p in pols}
    claims = [c for c in claims if c.policy_id in pol_ids]
    premium = sum(p.premium for p in in_period)
    earned = sum(_earned(p, now) for p in pols)
    paid = sum(c.approved_amount or 0 for c in claims if c.status == "closed")
    outstanding = sum((c.suggested_amount or c.claimed_amount or 0) for c in claims if c.status != "closed")
    expense = float(cfg("params.dicts")["system"]["expense_ratio"])
    loss_ratio = _ratio(paid + outstanding, earned)
    renewal_new = {r.new_policy_id for r in renewals if r.new_policy_id}
    due = [r for r in renewals if r.due_date <= now + timedelta(days=60)]
    decided = [a for a in apps if a.status in ("accepted", "rejected")]
    by_month: dict[str, dict] = {}
    for p in in_period:
        m = _local(p.created_at).strftime("%Y-%m")
        r = by_month.setdefault(m, {"month": m, "premium": 0.0, "policies": 0, "claims_paid": 0.0})
        r["premium"] += p.premium
        r["policies"] += 1
    for c in claims:
        if c.status == "closed" and c.closed_at and c.closed_at >= since:
            m = _local(c.closed_at).strftime("%Y-%m")
            by_month.setdefault(m, {"month": m, "premium": 0.0, "policies": 0, "claims_paid": 0.0})["claims_paid"] += c.approved_amount or 0
    by_line: dict[str, float] = defaultdict(float)
    by_stage: dict[str, float] = defaultdict(float)
    by_industry: dict[str, float] = defaultdict(float)
    by_org: dict[str, float] = defaultdict(float)
    by_product: dict[str, dict] = {}
    for p in in_period:
        lines = (p.coverage or {}).get("lines") or products[p.product_id].lines
        for l in lines:
            by_line[LINES.get(l, {}).get("name", l)] += p.premium / max(1, len(lines))
            by_stage[STAGES.get(LINES.get(l, {}).get("stage", ""), "其他")] += p.premium / max(1, len(lines))
        e = ents.get(p.enterprise_id)
        by_industry[e.industry if e else "未知"] += p.premium
        app = app_by_id.get(p.application_id) if p.application_id else None
        u = users.get(app.assignee_id) if app and app.assignee_id else None
        by_org[orgs.get(u.org_id, "总公司") if u else "总公司"] += p.premium
        pr = products[p.product_id]
        row = by_product.setdefault(pr.name, {"product": pr.name, "premium": 0.0, "policies": 0})
        row["premium"] += p.premium
        row["policies"] += 1
    channels = Counter((app_by_id.get(p.application_id).intermediary if p.application_id and app_by_id.get(p.application_id) else None) or "直销" for p in in_period)
    return {
        "kpis": {
            "premium": round(premium, 2),
            "earned_premium": round(earned, 2),
            "policies_new": sum(1 for p in in_period if p.id not in renewal_new),
            "renewals": sum(1 for p in in_period if p.id in renewal_new),
            "renewal_rate": _ratio(sum(1 for r in due if r.status == "issued"), len(due)),
            "active_policies": sum(1 for p in pols if p.status == "active"),
            "claims_paid": round(paid, 2),
            "outstanding": round(outstanding, 2),
            "loss_ratio": loss_ratio,
            "expense_ratio": expense,
            "combined_ratio": round(loss_ratio + expense, 4) if loss_ratio is not None else None,
            "applications": len(apps),
            "uw_pass_rate": _ratio(sum(1 for a in decided if a.status == "accepted"), len(decided)),
        },
        "trend": sorted(by_month.values(), key=lambda r: r["month"]),
        "structure": {
            "line": [{"name": k, "value": round(v, 2)} for k, v in sorted(by_line.items(), key=lambda kv: -kv[1])],
            "stage": [{"name": k, "value": round(v, 2)} for k, v in by_stage.items()],
            "industry": [{"name": k, "value": round(v, 2)} for k, v in sorted(by_industry.items(), key=lambda kv: -kv[1])],
            "org": [{"name": k, "value": round(v, 2)} for k, v in by_org.items()],
            "channel": [{"name": k, "value": v} for k, v in channels.most_common()],
        },
        "products": sorted(by_product.values(), key=lambda r: -r["premium"]),
    }


# ---------------------------------------------------------------- 风险看板


def risk(days: int = 180) -> dict:
    now = utcnow()
    since = now - timedelta(days=days)
    with session_scope() as s:
        active = s.scalars(select(Policy).where(Policy.status == "active")).all()
        ents = {e.id: e for e in s.scalars(select(Enterprise)).all()}
        alerts = s.scalars(select(Alert).where(Alert.created_at >= since)).all()
        incidents = s.scalars(select(Incident)).all()
        reports = s.scalars(select(Artifact).where(Artifact.kind == "uw_report")).all()
        claim_results = s.scalars(select(Artifact).where(Artifact.kind == "claim_result")).all()
        claims = s.scalars(select(ClaimCase)).all()
        apps = {a.id: a for a in s.scalars(select(Application)).all()}
    levels = Counter((ents[p.enterprise_id].risk_level or "未评级") for p in active if p.enterprise_id in ents)
    by_week: dict[str, Counter] = defaultdict(Counter)
    for a in alerts:
        wk = _local(a.created_at).strftime("%m-%d")
        by_week[wk][a.level] += 1
    handled = [a for a in alerts if a.status in ("closed", "false_positive", "escalated")]
    orange = [a for a in handled if a.level in ("red", "orange")]
    converted = 0
    for a in alerts:
        if any(i.policy_id == a.policy_id and a.created_at <= i.created_at <= a.created_at + timedelta(days=90) for i in incidents):
            converted += 1
    threshold = cfg("uw.rules")["thresholds"]["manual_fraud"]
    uw_fraud = [(r, apps.get(r.ref_id)) for r in reports if (((r.data or {}).get("fraud") or {}).get("probability") or 0) >= threshold]
    cl_fraud = [r for r in claim_results if any(h.get("hit") for h in ((r.data or {}).get("fraud_checks") or []) if isinstance(h, dict))]
    claim_by_id = {c.id: c for c in claims}
    pattern_counts = Counter()
    for r, _ in uw_fraud:
        for p in ((r.data or {}).get("fraud") or {}).get("patterns", []) or []:
            pattern_counts[p.get("name") or p.get("code")] += 1
    drivers = Counter()
    for c in claims:
        drivers[(c.report or {}).get("incident_type_name") or (c.report or {}).get("incident_type") or "未分类"] += c.approved_amount or c.suggested_amount or c.claimed_amount or 0
    methods = Counter(m for a in alerts for m in a.methods or [])
    return {
        "kpis": {
            "alerts": len(alerts),
            "red": sum(1 for a in alerts if a.level == "red"),
            "orange": sum(1 for a in alerts if a.level == "orange"),
            "alert_precision": _ratio(sum(1 for a in orange if a.status != "false_positive"), len(orange)),
            "incident_conversion": _ratio(converted, len(alerts)),
            "fraud_flags": len(uw_fraud) + len(cl_fraud),
            "fraud_amount": round(sum((a.sum_insured if a else 0) for _, a in uw_fraud) + sum((claim_by_id[r.ref_id].claimed_amount if r.ref_id in claim_by_id else 0) for r in cl_fraud), 2),
            "incidents": len(incidents),
        },
        "risk_levels": [{"name": k, "value": v} for k, v in levels.items()],
        "alert_trend": [{"day": k, "red": v["red"], "orange": v["orange"], "yellow": v["yellow"], "blue": v["blue"]} for k, v in sorted(by_week.items())],
        "methods": [{"name": k, "value": v} for k, v in methods.most_common()],
        "fraud_patterns": [{"name": k, "value": v} for k, v in pattern_counts.most_common(8)],
        "loss_drivers": [{"name": k, "value": round(v, 2)} for k, v in drivers.most_common(8)],
    }


# ---------------------------------------------------------------- 智能体效能看板


def agents(days: int = 30, agent_id: str | None = None) -> dict:
    since = utcnow() - timedelta(days=days)
    with session_scope() as s:
        q = select(Run).where(Run.parent_run_id.is_(None), Run.created_at >= since)
        if agent_id:
            q = q.where(Run.agent_id == agent_id)
        runs = [r for r in s.scalars(q).all() if not (r.business_ref or {}).get("eval_job")]
        ids = [r.id for r in runs]
        cards = s.scalars(select(HitlCard).where(HitlCard.run_id.in_(ids))).all() if ids else []
    waits: dict[int, float] = defaultdict(float)
    finals_by_run: dict[int, list] = defaultdict(list)
    for c in cards:
        if c.resolved_at:
            waits[c.run_id] += (c.resolved_at - c.created_at).total_seconds()
        if (c.context or {}).get("final"):
            finals_by_run[c.run_id].append(c)

    def stats(rs: list[Run]) -> dict:
        done = [r for r in rs if r.status == "completed"]
        failed = [r for r in rs if r.status == "failed"]
        scores = [r.eval_score for r in done if r.eval_score is not None]
        from ..harness.timing import duration_seconds

        durations = [duration_seconds(r, wait_s=waits.get(r.id, 0)) for r in rs if r.ended_at and (r.started_at or (r.usage or {}).get("active_seconds") is not None)]
        finals = [c for r in rs for c in finals_by_run.get(r.id, []) if c.status != "open"]
        modified = [c for c in finals if (c.resolution or {}).get("action") == "reject" or (c.resolution or {}).get("edits") or str((c.resolution or {}).get("action") or "").startswith("option")]
        return {
            "runs": len(rs),
            "success_rate": _ratio(len(done), len(done) + len(failed)),
            "avg_score": round(statistics.mean(scores), 1) if scores else None,
            "pass_rate": _ratio(sum(1 for r in done if r.eval_passed), len(done)),
            "reflection_rate": _ratio(sum(1 for r in rs if r.reflection_rounds), len(rs)),
            "avg_reflection_rounds": round(statistics.mean([r.reflection_rounds for r in rs if r.reflection_rounds]), 2) if any(r.reflection_rounds for r in rs) else 0,
            "human_modification_rate": _ratio(len(modified), len(finals)),
            "degraded_share": _ratio(sum(1 for r in rs if (r.degrade_level or "D0") >= "D2"), len(rs)),
            "avg_duration": round(statistics.mean(durations), 1) if durations else None,
            "warm": sum(1 for r in rs if r.start_mode == "warm"),
            "cold": sum(1 for r in rs if r.start_mode == "cold"),
        }

    by_agent = defaultdict(list)
    for r in runs:
        by_agent[r.agent_id].append(r)
    by_day: dict[str, list] = defaultdict(list)
    for r in runs:
        by_day[_local(r.created_at).strftime("%m-%d")].append(r)
    return {
        "kpis": stats(runs),
        "by_agent": [{"agent_id": k, "name": AGENT_NAMES.get(k, k), **stats(v)} for k, v in sorted(by_agent.items())],
        "trend": [{"day": d, "runs": len(v), "avg_score": stats(v)["avg_score"], "success_rate": stats(v)["success_rate"]} for d, v in sorted(by_day.items())],
        "status": dict(Counter(r.status for r in runs)),
    }


# ---------------------------------------------------------------- 云私计算看板


def compute(days: int = 7) -> dict:
    since = utcnow() - timedelta(days=days)
    q = cfg("quota")
    with session_scope() as s:
        calls = s.scalars(select(ModelCall).where(ModelCall.ts >= since)).all()
        egress = s.scalars(select(EgressLog).where(EgressLog.ts >= since)).all()
        run_ids = {c.run_id for c in calls if c.run_id}
        runs = {r.id: r for r in s.scalars(select(Run).where(Run.id.in_(run_ids))).all()} if run_ids else {}
        users = {u.id: u for u in s.scalars(select(User)).all()}
        orgs = {o.id: o.name for o in s.scalars(select(Organization)).all()}
        cards = s.scalars(select(HitlCard).where(HitlCard.card_type == "egress_approval", HitlCard.created_at >= since)).all()
    zones = {"cloud": {"calls": 0, "tokens": 0, "cost": 0.0}, "private": {"calls": 0, "tokens": 0, "seconds": 0.0}}
    by_agent: dict[str, dict] = defaultdict(lambda: {"cloud": 0, "private": 0, "cost": 0.0, "gpu_cost": 0.0})
    by_org: dict[str, float] = defaultdict(float)
    fallbacks = Counter()
    by_day: dict[str, dict] = defaultdict(lambda: {"cloud": 0, "private": 0})
    for c in calls:
        z = zones["cloud" if c.zone == "cloud" else "private"]
        z["calls"] += 1
        z["tokens"] += c.tokens_in + c.tokens_out
        run = runs.get(c.run_id)
        agent = run.agent_id if run else "平台"
        if c.zone == "cloud":
            z["cost"] += c.cost
            by_agent[agent]["cloud"] += 1
            by_agent[agent]["cost"] += c.cost
        else:
            secs = c.latency_ms / 1000 if c.model_id != "private-builtin" else 0.0
            z["seconds"] += secs
            by_agent[agent]["private"] += 1
            by_agent[agent]["gpu_cost"] += secs / 3600 * float(q["gpu_hour_cost"])
        u = users.get(run.created_by) if run and run.created_by else None
        by_org[orgs.get(u.org_id, "系统任务") if u else "系统任务"] += c.cost
        if c.fallback:
            fallbacks[(c.error or "上游模型不可用或输出未通过校验")[:40]] += 1
        by_day[_local(c.ts).strftime("%m-%d")]["cloud" if c.zone == "cloud" else "private"] += 1
    rules = Counter()
    for e in egress:
        for k, v in (e.rules_hit or {}).items():
            rules[k] += v
    return {
        "kpis": {"cloud_calls": zones["cloud"]["calls"], "private_calls": zones["private"]["calls"], "cloud_tokens": zones["cloud"]["tokens"], "private_tokens": zones["private"]["tokens"], "cloud_cost": round(zones["cloud"]["cost"], 4), "gpu_hours": round(zones["private"]["seconds"] / 3600, 3), "gpu_cost": round(zones["private"]["seconds"] / 3600 * float(q["gpu_hour_cost"]), 2), "egress": len(egress), "egress_blocked": sum(1 for e in egress if e.scan_result != "pass"), "placeholders": sum(e.placeholder_count for e in egress), "fallbacks": sum(fallbacks.values()), "egress_approvals": len(cards), "egress_approval_pass": _ratio(sum(1 for c in cards if (c.resolution or {}).get("action") == "approve"), len([c for c in cards if c.status != "open"]))},
        "egress_by_level": [{"name": f"L{k}", "value": v} for k, v in sorted(Counter(e.source_level for e in egress).items())],
        "masking_rules": [{"name": k, "value": v} for k, v in rules.most_common(10)],
        "by_agent": [{"agent": AGENT_NAMES.get(k, k), **{kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}} for k, v in by_agent.items()],
        "by_org": [{"name": k, "value": round(v, 4)} for k, v in by_org.items()],
        "fallback_reasons": [{"name": k, "value": v} for k, v in fallbacks.most_common(6)],
        "trend": [{"day": k, **v} for k, v in sorted(by_day.items())],
    }


# ---------------------------------------------------------------- 自定义报表


def run_report(metrics: list[str], dimension: str, days: int, filters: dict | None = None) -> dict:
    """按维度聚合所选指标；指标口径与看板一致。"""
    bad = [m for m in metrics if m not in METRICS]
    if bad or not metrics:
        raise ValueError("指标无效：" + "、".join(bad) if bad else "至少选择一个指标")
    if dimension not in DIMENSIONS:
        raise ValueError("维度无效")
    now = utcnow()
    since = now - timedelta(days=days)
    with session_scope() as s:
        pols = s.scalars(select(Policy)).all()
        products = {p.id: p for p in s.scalars(select(Product)).all()}
        ents = {e.id: e for e in s.scalars(select(Enterprise)).all()}
        claims = s.scalars(select(ClaimCase)).all()
        alerts = s.scalars(select(Alert).where(Alert.created_at >= since)).all()
        runs = [r for r in s.scalars(select(Run).where(Run.parent_run_id.is_(None), Run.created_at >= since)).all() if not (r.business_ref or {}).get("eval_job")]
        calls = s.scalars(select(ModelCall).where(ModelCall.ts >= since)).all()
        apps = s.scalars(select(Application).where(Application.created_at >= since)).all()
        users = {u.id: u for u in s.scalars(select(User)).all()}
        orgs = {o.id: o.name for o in s.scalars(select(Organization)).all()}
        app_all = {a.id: a for a in s.scalars(select(Application)).all()}
        run_agent = {r.id: r.agent_id for r in s.scalars(select(Run)).all()}
    expense = float(cfg("params.dicts")["system"]["expense_ratio"])

    def pol_keys(p: Policy) -> list[str]:
        if dimension == "month":
            return [_local(p.created_at).strftime("%Y-%m")]
        if dimension == "line":
            return [LINES.get(l, {}).get("name", l) for l in ((p.coverage or {}).get("lines") or products[p.product_id].lines)]
        if dimension == "stage":
            return sorted({STAGES.get(LINES.get(l, {}).get("stage", ""), "其他") for l in ((p.coverage or {}).get("lines") or products[p.product_id].lines)})
        if dimension == "industry":
            return [ents[p.enterprise_id].industry if p.enterprise_id in ents else "未知"]
        if dimension == "product":
            return [products[p.product_id].name]
        if dimension == "org":
            app = app_all.get(p.application_id) if p.application_id else None
            u = users.get(app.assignee_id) if app and app.assignee_id else None
            return [orgs.get(u.org_id, "总公司") if u else "总公司"]
        return ["全部"]

    rows: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    pol_by_id = {p.id: p for p in pols}
    for p in pols:
        keys = pol_keys(p)
        share = 1 / max(1, len(keys))
        for k in keys:
            if p.created_at >= since:
                rows[k]["premium"] += p.premium * share
                rows[k]["policies_new"] += share
            rows[k]["earned_premium"] += _earned(p, now) * share
    for c in claims:
        p = pol_by_id.get(c.policy_id)
        if p is None:
            continue
        keys = pol_keys(p) if dimension != "month" else [_local(c.closed_at or c.created_at).strftime("%Y-%m")]
        for k in keys:
            if c.status == "closed":
                rows[k]["claims_paid"] += (c.approved_amount or 0) / max(1, len(keys))
            else:
                rows[k]["_outstanding"] += (c.suggested_amount or c.claimed_amount or 0) / max(1, len(keys))
    for a in alerts:
        p = pol_by_id.get(a.policy_id)
        keys = [_local(a.created_at).strftime("%Y-%m")] if dimension == "month" else ([{"red": "红色", "orange": "橙色", "yellow": "黄色", "blue": "蓝色"}[a.level]] if dimension == "level" else (pol_keys(p) if p else ["未知"]))
        for k in keys:
            rows[k]["alerts"] += 1
            if a.status in ("closed", "false_positive", "escalated") and a.level in ("red", "orange"):
                rows[k]["_orange_handled"] += 1
                rows[k]["_orange_valid"] += a.status != "false_positive"
    for a in apps:
        k = _local(a.created_at).strftime("%Y-%m") if dimension == "month" else (products[a.product_id].name if dimension == "product" and a.product_id in products else ("全部" if dimension not in ("industry",) else (ents[a.enterprise_id].industry if a.enterprise_id in ents else "未知")))
        rows[k]["applications"] += 1
        if a.status in ("accepted", "rejected"):
            rows[k]["_decided"] += 1
            rows[k]["_accepted"] += a.status == "accepted"
    for r in runs:
        k = _local(r.created_at).strftime("%Y-%m") if dimension == "month" else (AGENT_NAMES.get(r.agent_id, r.agent_id) if dimension == "agent" else "全部")
        rows[k]["runs"] += 1
        rows[k]["_done"] += r.status == "completed"
        rows[k]["_failed"] += r.status == "failed"
        rows[k]["_passed"] += bool(r.eval_passed)
        rows[k]["_reflected"] += bool(r.reflection_rounds)
        rows[k]["_degraded"] += (r.degrade_level or "D0") >= "D2"
        if r.eval_score is not None:
            rows[k]["_score_sum"] += r.eval_score
            rows[k]["_score_n"] += 1
    for c in calls:
        k = _local(c.ts).strftime("%Y-%m") if dimension == "month" else (AGENT_NAMES.get(run_agent.get(c.run_id), "平台") if dimension == "agent" else "全部")
        if c.zone == "cloud":
            rows[k]["cloud_calls"] += 1
            rows[k]["cloud_cost"] += c.cost
        else:
            rows[k]["private_calls"] += 1
            if c.model_id != "private-builtin":
                rows[k]["gpu_hours"] += c.latency_ms / 3_600_000
        rows[k]["fallbacks"] += bool(c.fallback)
    out = []
    for k, v in rows.items():
        lr = _ratio(v["claims_paid"] + v["_outstanding"], v["earned_premium"])
        derived = {
            "loss_ratio": lr,
            "combined_ratio": round(lr + expense, 4) if lr is not None else None,
            "uw_pass_rate": _ratio(v["_accepted"], v["_decided"]),
            "alert_precision": _ratio(v["_orange_valid"], v["_orange_handled"]),
            "success_rate": _ratio(v["_done"], v["_done"] + v["_failed"]),
            "pass_rate": _ratio(v["_passed"], v["_done"]),
            "reflection_rate": _ratio(v["_reflected"], v["runs"]),
            "degraded_share": _ratio(v["_degraded"], v["runs"]),
            "avg_score": round(v["_score_sum"] / v["_score_n"], 1) if v["_score_n"] else None,
        }
        row = {"key": k}
        for m in metrics:
            row[m] = derived[m] if m in derived else (round(v[m], 4) if m in v else None)
        if any(row[m] not in (None, 0) for m in metrics):
            out.append(row)
    out.sort(key=lambda r: r["key"])
    return {"metrics": [{"key": m, **METRICS[m]} for m in metrics], "dimension": {"key": dimension, "name": DIMENSIONS[dimension]}, "days": days, "rows": out, "generated_at": now.isoformat() + "Z"}


def report_dict(r: ReportDefinition) -> dict:
    return {"id": r.id, "name": r.name, "owner_id": r.owner_id, "metrics": r.metrics, "dimension": r.dimension, "period_days": r.period_days, "chart": r.chart, "filters": r.filters, "subscribers": r.subscribers, "schedule": r.schedule, "last_sent_at": r.last_sent_at.isoformat() + "Z" if r.last_sent_at else None, "created_at": r.created_at.isoformat() + "Z"}


def send_subscriptions() -> int:
    now = utcnow()
    period = {"daily": 1, "weekly": 7, "monthly": 30}
    sent = 0
    with session_scope() as s:
        defs = [(r.id, r.name, r.metrics, r.dimension, r.period_days, r.schedule, r.last_sent_at, list(r.subscribers or [])) for r in s.scalars(select(ReportDefinition).where(ReportDefinition.schedule != "none")).all()]
    for rid, name, metrics, dim, days, sched, last, subs in defs:
        if last and now - last < timedelta(days=period.get(sched, 7)):
            continue
        data = run_report(metrics, dim, days)
        top = data["rows"][-3:]
        body = "；".join(f"{r['key']}：" + "，".join(f"{METRICS[m]['name']} {r[m]}" for m in metrics[:3]) for r in top) or "统计期内无数据"
        with session_scope() as s:
            for uid in subs:
                notify(s, kind="report", title=f"报表订阅：{name}", body=body[:500], link=f"/dashboards/reports?id={rid}", user_id=uid)
            s.get(ReportDefinition, rid).last_sent_at = now
        sent += 1
    return sent
