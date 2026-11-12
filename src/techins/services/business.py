"""承保之后的业务编排：核保决定落地、出单、领域事件驱动的监测方案生成、事故响应与立案。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..harness.executor import executor
from ..models import Application, ClaimCase, DomainEvent, Enterprise, Incident, KnowledgeDoc, MemoryEntry, MonitorPlan, Notification, Policy, Product


def emit_event(name: str, object_type: str, object_id: int, payload: dict | None = None) -> None:
    with session_scope() as s:
        s.add(DomainEvent(name=name, object_type=object_type, object_id=object_id, payload=payload or {}, status="done"))


def _policy_no(s) -> str:
    n = (s.scalar(select(func.count(Policy.id))) or 0) + 1
    return f"PKJ{utcnow():%Y%m%d}{n:04d}"


def apply_uw_decision(application_id: int, decision: str, suggestion: dict, resolution: dict, run_id: int | None) -> dict:
    with session_scope() as s:
        app = s.get(Application, application_id)
        product = s.get(Product, app.product_id)
        app.decision = {"decision": decision, "suggested": suggestion.get("decision"), "conditions": suggestion.get("conditions", []) if decision == "conditional" else [], "premium": suggestion.get("premium") if decision != "standard" else suggestion.get("base_premium"), "comment": resolution.get("comment"), "by": resolution.get("user_id"), "run_id": run_id, "consistent_with_agent": decision == suggestion.get("decision")}
        if decision == "reject":
            app.status = "rejected"
            enterprise_id = app.enterprise_id
            summary = "已拒保"
            policy_id = None
        else:
            app.status = "accepted"
            start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            renew_of = (app.declared or {}).get("renewal_of_policy_id")
            if renew_of and s.get(Policy, renew_of) is not None:
                start = max(start, s.get(Policy, renew_of).end_date)
            selected = [l for l in (app.declared or {}).get("lines") or product.lines if l in product.lines] or list(product.lines)
            coverage = {
                "lines": selected,
                "docs": [{"doc_id": d.get("doc_id"), "name": d.get("name"), "lines": [l for l in d.get("lines", []) if l in selected], "params": d.get("params")} for d in product.package.get("docs", []) if set(d.get("lines", [])) & set(selected)],
                "conditions": app.decision["conditions"],
                "tier": app.tier,
            }
            policy = Policy(policy_no=_policy_no(s), application_id=app.id, enterprise_id=app.enterprise_id, product_id=product.id, coverage=coverage, premium=float(app.decision["premium"] or 0), sum_insured=app.sum_insured, start_date=start, end_date=start + timedelta(days=365), status="active")
            s.add(policy)
            s.flush()
            policy_id = policy.id
            enterprise_id = app.enterprise_id
            summary = f"已出单 {policy.policy_no}，保费 {policy.premium:.0f} 元"
        s.add(Notification(role="service", kind="uw", title=f"核保决定：{app.app_no}", body=summary, link=f"/underwriting/{app.id}"))
        renewal_of = (app.declared or {}).get("renewal_of_policy_id")
    if policy_id:
        from .customers import ensure_legacy_authorizations

        ensure_legacy_authorizations(enterprise_id)
        emit_event("保单已生效", "policy", policy_id, {"application_id": application_id})
        start_monitor_plan(policy_id, resolution.get("user_id"))
    if renewal_of:
        from .policy_admin import on_renewal_policy_issued

        on_renewal_policy_issued(renewal_of, policy_id, decision)
    return {"summary": summary, "policy_id": policy_id, "enterprise_id": enterprise_id}


def start_monitor_plan(policy_id: int, user_id: int | None) -> int:
    with session_scope() as s:
        policy = s.get(Policy, policy_id)
        ent = s.get(Enterprise, policy.enterprise_id)
        title = f"监测方案：{policy.policy_no} {ent.name}"
    return executor.create_run(agent_id="AG-RW", purpose="plan", inputs={"policy_id": policy_id}, business_ref={"policy_id": policy_id}, mode="auto", user_id=user_id, title=title)


def start_incident(policy_id: int, *, alert_id: int | None, incident_type: str, description: str, occurred_at, user_id: int | None) -> int:
    with session_scope() as s:
        n = (s.scalar(select(func.count(Incident.id))) or 0) + 1
        inc = Incident(incident_no=f"SG{utcnow():%Y%m%d}{n:03d}", policy_id=policy_id, alert_id=alert_id, incident_type=incident_type, occurred_at=occurred_at, description=description, status="started")
        s.add(inc)
        s.flush()
        inc_id = inc.id
        policy = s.get(Policy, policy_id)
        ent = s.get(Enterprise, policy.enterprise_id)
        title = f"事故响应：{inc.incident_no} {ent.name}"
    run_id = executor.create_run(agent_id="AG-RW", purpose="incident", inputs={"incident_id": inc_id, "policy_id": policy_id}, business_ref={"policy_id": policy_id, "incident_id": inc_id}, mode="auto", user_id=user_id, title=title)
    with session_scope() as s:
        s.get(Incident, inc_id).run_id = run_id
    emit_event("疑似事故已识别", "incident", inc_id, {"alert_id": alert_id})
    return inc_id


def register_claim(policy_id: int, *, incident_id: int | None, report: dict, claimed_amount: float, parties: list[dict], user_id: int | None, start: bool = True) -> int:
    with session_scope() as s:
        n = (s.scalar(select(func.count(ClaimCase.id))) or 0) + 1
        case = ClaimCase(claim_no=f"LP{utcnow():%Y%m%d}{n:04d}", policy_id=policy_id, incident_id=incident_id, status="registered", report=report, parties=parties, claimed_amount=claimed_amount, assignee_id=user_id)
        s.add(case)
        s.flush()
        cid = case.id
        if incident_id:
            inc = s.get(Incident, incident_id)
            inc.claim_id = cid
            inc.status = "claimed"
    emit_event("报案已登记", "claim", cid, {"incident_id": incident_id})
    if start:
        start_claim_run(cid, user_id)
    return cid


def start_claim_run(claim_id: int, user_id: int | None) -> int:
    with session_scope() as s:
        case = s.get(ClaimCase, claim_id)
        policy = s.get(Policy, case.policy_id)
        ent = s.get(Enterprise, policy.enterprise_id)
        title = f"定损理赔：{case.claim_no} {ent.name}"
    run_id = executor.create_run(agent_id="AG-CL", inputs={"claim_id": claim_id}, business_ref={"claim_id": claim_id, "policy_id": case.policy_id}, mode="auto", user_id=user_id, title=title)
    with session_scope() as s:
        c = s.get(ClaimCase, claim_id)
        c.run_id = run_id
        c.status = "assessing"
    return run_id


def ensure_plan(policy_id: int) -> MonitorPlan | None:
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
        if plan is not None:
            s.expunge(plan)
        return plan


def create_application(*, enterprise_id: int, product_id: int, tier: str, sum_insured: float, period_months: int, declared: dict, contacts: dict, intermediary: str | None, user_id: int | None) -> int:
    with session_scope() as s:
        product = s.get(Product, product_id)
        if product is None or product.status != "published":
            raise ValueError("只能投保已发布的产品")
        n = (s.scalar(select(func.count(Application.id))) or 0) + 1
        lines = [l for l in declared.get("lines") or product.lines if l in product.lines]
        if not lines:
            raise ValueError("投保险种必须是产品包含的险种")
        declared = {**declared, "lines": lines}
        app = Application(app_no=f"TB{utcnow():%Y%m%d}{n:04d}", enterprise_id=enterprise_id, product_id=product_id, tier=tier, sum_insured=sum_insured, period_months=period_months, declared=declared, contacts=contacts, intermediary=intermediary, status="draft", assignee_id=user_id)
        s.add(app)
        s.flush()
        return app.id


def start_underwriting(application_id: int, user_id: int | None) -> int:
    with session_scope() as s:
        app = s.get(Application, application_id)
        if app is None:
            raise ValueError("投保单不存在")
        if app.status not in ("draft", "submitted", "returned", "pending_decision"):
            raise ValueError("投保单当前状态不能发起核保")
        ent = s.get(Enterprise, app.enterprise_id)
        app.status = "submitted"
        title = f"核保：{app.app_no} {ent.name}"
    emit_event("投保单已提交", "application", application_id, {})
    return executor.create_run(agent_id="AG-UW", inputs={"application_id": application_id}, business_ref={"application_id": application_id}, mode="auto", user_id=user_id, title=title)


def close_claim(claim_id: int, user_id: int | None) -> dict:
    """支付指令确认后结案，并回流：案例记忆与理赔案例库（K05）、承保历史库（出险标记，供反欺诈与精算经验使用）。"""
    from ..knowledge.seed import add_document
    from ..models import Artifact, HistoricalApplication

    with session_scope() as s:
        case = s.get(ClaimCase, claim_id)
        if case is None or case.status != "approved":
            raise ValueError("案件未审批通过，不能生成支付指令")
        policy = s.get(Policy, case.policy_id)
        ent = s.get(Enterprise, policy.enterprise_id)
        product = s.get(Product, policy.product_id)
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == case.result_artifact_key).order_by(Artifact.version.desc())).first() if case.result_artifact_key else None
        data = art.data if art else {}
        case.status = "closed"
        case.approvals = list(case.approvals or []) + [{"by": user_id, "action": "payment", "amount": case.approved_amount, "at": utcnow().isoformat() + "Z"}]
        liab = data.get("liability") or {}
        adj = data.get("adjustment") or {}
        text = (f"{ent.track}企业{(case.report or {}).get('incident_type_name', '')}。近因：{liab.get('proximate_cause', '')}。"
                f"核定损失 {adj.get('assessed', 0):.0f} 元，赔付 {case.approved_amount or 0:.0f} 元。"
                + ("多方责任比例：" + "、".join(f"{k} {v:.0%}" for k, v in (liab.get("shares") or {}).items()) + "。" if liab.get("multi_party") else ""))
        add_document(s, kb="K05", title=f"理赔案例：{case.claim_no}", text=text, source="理赔结案回流", doc_type="理赔案例", lines=product.lines, risk_types=[], quality="checked")
        s.add(HistoricalApplication(enterprise_name=ent.name, line=product.lines[0], year=utcnow().year, features={"claim_amount": case.approved_amount}, contacts={}, intermediary=None, fraud_label=0, claimed=1, fraud_pattern=None, statement=""))
        s.add(MemoryEntry(kind="case", agent_id="AG-CL", subject_key=ent.name, title=f"结案：{case.claim_no}", text=text, data_level=2))
        s.add(Notification(role="service", kind="claim", title=f"已结案：{case.claim_no}", body=f"支付 {case.approved_amount or 0:.0f} 元至 {ent.name}", link=f"/claims/{claim_id}"))
        incident_id = case.incident_id
    if incident_id:
        with session_scope() as s:
            inc = s.get(Incident, incident_id)
            if inc:
                inc.status = "closed"
    emit_event("理赔已结案", "claim", claim_id, {})
    return {"status": "closed"}


_ = KnowledgeDoc
