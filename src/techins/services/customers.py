"""客户管理：尽调资料库（分级、版本、访问记录）、数据接入授权（授权、撤回、到期与监测方案联动）、联系与拜访记录、360 动态时间线。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..models import (
    Alert,
    Application,
    AuditLog,
    ClaimCase,
    ContactRecord,
    DataAuthorization,
    DesignProject,
    Endorsement,
    Enterprise,
    Incident,
    Material,
    MonitorPlan,
    Policy,
    Product,
    Renewal,
)
from ..privacy.classifier import detect_level
from . import platform_tasks  # noqa: F401
from .config_center import cfg
from .notify import notify

SCOPE_LABELS = {"iot": "物联网设备数据", "log": "信息安全日志", "ops": "研发与运营数据"}
SCENARIO_FLAG = {"iot": "device", "log": "logs", "ops": "ops"}
FALLBACK = {"iot": "所在区域灾害预警与设备事故舆情（DM4）", "log": "漏洞情报与勒索相关舆情（DM4）", "ops": "里程碑相关公告与人员变动舆情（DM4）"}


class CustomerError(ValueError):
    pass


class EnterpriseConflict(CustomerError):
    def __init__(self, message: str, *, existing: dict | None = None) -> None:
        super().__init__(message)
        self.existing = existing


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


# ---------------------------------------------------------------- 尽调资料库


def dd_library(enterprise_id: int) -> list[dict]:
    with session_scope() as s:
        mats = s.scalars(select(Material).where(Material.enterprise_id == enterprise_id).order_by(Material.created_at.desc())).all()
        access = dict(s.execute(select(AuditLog.object_id, func.count(AuditLog.id)).where(AuditLog.object_type == "material", AuditLog.category == "data_access").group_by(AuditLog.object_id)).all())
        last = {}
        for a in s.scalars(select(AuditLog).where(AuditLog.object_type == "material", AuditLog.category == "data_access").order_by(AuditLog.id.desc()).limit(500)).all():
            last.setdefault(a.object_id, {"by": a.username, "at": iso(a.ts), "action": a.action})
        latest = {}
        for m in mats:
            key = (m.owner_type, m.owner_id, m.name)
            latest[key] = max(latest.get(key, 0), m.version or 1)
        out = []
        for m in mats:
            out.append({"id": m.id, "name": m.name, "type": m.material_type, "owner_type": m.owner_type, "owner_id": m.owner_id, "data_level": m.data_level, "detected_level": m.detected_level, "version": m.version or 1, "is_latest": (m.version or 1) == latest[(m.owner_type, m.owner_id, m.name)], "prev_id": m.prev_id, "status": m.status, "chars": len(m.text or ""), "mime": m.mime, "access_count": access.get(str(m.id), 0), "last_access": last.get(str(m.id)), "created_at": iso(m.created_at)})
        return out


def material_access_log(material_id: int) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.object_type == "material", AuditLog.object_id == str(material_id)).order_by(AuditLog.id.desc()).limit(100)).all()
        return [{"username": a.username, "category": a.category, "action": a.action, "detail": a.detail, "ts": iso(a.ts)} for a in rows]


def designer_can_view(user_id: int, enterprise_id: int) -> bool:
    """产品设计人员按项目授权访问：只能查看自己参与的设计项目所含企业的资料。"""
    with session_scope() as s:
        for p in s.scalars(select(DesignProject)).all():
            if enterprise_id in (p.enterprise_ids or []) and (p.owner_id in (None, user_id) or p.project_type == "track"):
                return True
    return False


def _structurally_possible(sc: dict, lines: list[str]) -> dict[str, bool]:
    from ..knowledge.catalog import LINES

    monitors = {item for line in lines for item in (LINES.get(line, {}).get("monitors") or [])}
    return {"device": "device" in monitors and bool(sc.get("assets")), "logs": "logs" in monitors, "ops": "ops" in monitors}


# ---------------------------------------------------------------- 数据接入授权


def _auth_no(s) -> str:
    prefix = cfg("params.dicts")["numbering"].get("authorization", "SQ")
    n = (s.scalar(select(func.count(DataAuthorization.id))) or 0) + 1
    return f"{prefix}{utcnow():%Y%m%d}{n:04d}"


def auth_dict(a: DataAuthorization, s=None) -> dict:
    now = utcnow()
    days_left = (a.valid_to - now).days if a.valid_to else None
    return {"id": a.id, "auth_no": a.auth_no, "enterprise_id": a.enterprise_id, "policy_id": a.policy_id, "scopes": a.scopes, "scope_labels": [SCOPE_LABELS.get(x, x) for x in a.scopes or []], "items": a.items, "purpose": a.purpose, "grantor": a.grantor, "valid_from": iso(a.valid_from), "valid_to": iso(a.valid_to), "days_left": days_left, "status": a.status, "material_id": a.material_id, "revoked_at": iso(a.revoked_at), "revoke_reason": a.revoke_reason, "effects": a.effects, "created_at": iso(a.created_at)}


def ensure_legacy_authorizations(enterprise_id: int | None = None) -> int:
    """历史保单的授权来自投保申报：为尚无授权记录的在保保单补登授权记录，之后以授权记录为准。"""
    created = 0
    with session_scope() as s:
        q = select(Policy).where(Policy.status == "active")
        if enterprise_id:
            q = q.where(Policy.enterprise_id == enterprise_id)
        for p in s.scalars(q).all():
            if s.scalars(select(DataAuthorization).where(DataAuthorization.policy_id == p.id)).first():
                continue
            app = s.get(Application, p.application_id) if p.application_id else None
            declared = ((app.declared if app else {}) or {}).get("authorizations") or {}
            cov = (p.coverage or {}).get("data_authorizations") or {}
            merged = {**{"iot": True, "log": True, "ops": True}, **declared, **cov}
            scopes = [k for k in ("iot", "log", "ops") if merged.get(k)]
            if not scopes:
                continue
            s.add(DataAuthorization(auth_no=_auth_no(s), enterprise_id=p.enterprise_id, policy_id=p.id, scopes=scopes, items=[SCOPE_LABELS[k] for k in scopes], purpose="承保期间风险监测与事故响应（投保时签署）", grantor="投保人", valid_from=p.start_date - timedelta(days=1), valid_to=p.end_date, status="active"))
            s.flush()
            created += 1
    return created


def _effective(s, enterprise_id: int, policy_id: int) -> dict[str, bool]:
    now = utcnow()
    rows = s.scalars(select(DataAuthorization).where(DataAuthorization.enterprise_id == enterprise_id, DataAuthorization.status == "active")).all()
    out = {k: False for k in ("iot", "log", "ops")}
    for a in rows:
        if a.policy_id not in (None, policy_id):
            continue
        if a.valid_to and a.valid_to < now:
            continue
        for k in a.scopes or []:
            out[k] = True
    return out


def apply_authorizations(enterprise_id: int, *, reason: str, auth_id: int | None = None) -> list[dict]:
    """授权变化联动监测：未授权的数据源停止接入，对应指标降级为外部数据监测；恢复授权后指标恢复。"""
    effects: list[dict] = []
    with session_scope() as s:
        for p in s.scalars(select(Policy).where(Policy.enterprise_id == enterprise_id, Policy.status == "active")).all():
            eff = _effective(s, enterprise_id, p.id)
            cov = dict(p.coverage or {})
            app = s.get(Application, p.application_id) if p.application_id else None
            before = cov.get("data_authorizations") or ((app.declared if app else {}) or {}).get("authorizations") or {}
            cov["data_authorizations"] = eff
            p.coverage = cov
            plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == p.id)).first()
            if plan is None:
                continue
            state = dict(plan.state or {})
            sc = dict(state.get("scenario") or {})
            structural = _structurally_possible(sc, list(cov.get("lines") or []))
            possible = dict(sc.get("possible") or {flag: bool(sc.get(flag)) or (before.get(src) is False and structural[flag]) for src, flag in SCENARIO_FLAG.items()})
            sc["possible"] = possible
            changed = []
            for src, flag in SCENARIO_FLAG.items():
                new = bool(possible.get(flag)) and eff[src]
                if bool(sc.get(flag)) != new:
                    changed.append({"source": src, "label": SCOPE_LABELS[src], "action": "恢复接入" if new else "停止接入"})
                sc[flag] = new
            inds = []
            for ind in plan.indicators or []:
                ind = dict(ind)
                src = ind.get("source")
                if src in SCENARIO_FLAG:
                    if not eff[src] and ind.get("status") == "active":
                        ind.update({"status": "degraded", "degraded_reason": f"{SCOPE_LABELS[src]}授权已撤回或到期", "fallback": FALLBACK[src], "degraded_by": "authorization"})
                    elif eff[src] and ind.get("status") == "degraded" and ind.get("degraded_by") == "authorization":
                        ind.update({"status": "active"})
                        ind.pop("degraded_reason", None)
                        ind.pop("degraded_by", None)
                inds.append(ind)
            if changed:
                plan.indicators = inds
                state["scenario"] = sc
                state["authorization_changes"] = list(state.get("authorization_changes", [])) + [{"at": iso(utcnow()), "hour": state.get("hour"), "reason": reason, "changes": changed}]
                plan.state = state
                effect = {"policy_id": p.id, "policy_no": p.policy_no, "changes": changed, "degraded_indicators": [i["name"] for i in inds if i.get("degraded_by") == "authorization"]}
                effects.append(effect)
                notify(s, kind="alert", title=f"监测方案调整：{p.policy_no}", body="；".join(f"{c['label']}{c['action']}" for c in changed) + f"（{reason}）", link=f"/policies/{p.id}", level="warning", role="service")
        if auth_id and effects:
            a = s.get(DataAuthorization, auth_id)
            a.effects = list(a.effects or []) + [{"at": iso(utcnow()), "reason": reason, "effects": effects}]
    return effects


def list_authorizations(enterprise_id: int) -> list[dict]:
    ensure_legacy_authorizations(enterprise_id)
    with session_scope() as s:
        return [auth_dict(a) for a in s.scalars(select(DataAuthorization).where(DataAuthorization.enterprise_id == enterprise_id).order_by(DataAuthorization.id.desc())).all()]


def grant_authorization(enterprise_id: int, *, policy_id: int | None, scopes: list[str], items: list[str], purpose: str, grantor: str, valid_days: int, material_id: int | None, user_id: int) -> dict:
    scopes = [x for x in scopes if x in SCOPE_LABELS]
    if not scopes:
        raise CustomerError("至少选择一类授权数据")
    if valid_days < 1 or valid_days > 3 * 365:
        raise CustomerError("授权期限需在 1 天到 3 年之间")
    if not grantor.strip():
        raise CustomerError("请填写企业授权人")
    ensure_legacy_authorizations(enterprise_id)
    with session_scope() as s:
        if policy_id:
            p = s.get(Policy, policy_id)
            if p is None or p.enterprise_id != enterprise_id:
                raise CustomerError("保单不属于该企业")
        a = DataAuthorization(auth_no=_auth_no(s), enterprise_id=enterprise_id, policy_id=policy_id, scopes=scopes, items=items or [SCOPE_LABELS[x] for x in scopes], purpose=purpose, grantor=grantor, valid_from=utcnow(), valid_to=utcnow() + timedelta(days=valid_days), status="active", material_id=material_id, recorded_by=user_id)
        s.add(a)
        s.flush()
        aid = a.id
    effects = apply_authorizations(enterprise_id, reason=f"新增授权（{'、'.join(SCOPE_LABELS[x] for x in scopes)}）", auth_id=aid)
    from .ops import sync_connectors

    sync_connectors()
    with session_scope() as s:
        out = auth_dict(s.get(DataAuthorization, aid))
    out["applied"] = effects
    return out


def revoke_authorization(auth_id: int, reason: str) -> dict:
    if not reason.strip():
        raise CustomerError("请填写撤回原因")
    with session_scope() as s:
        a = s.get(DataAuthorization, auth_id)
        if a is None:
            raise CustomerError("授权不存在")
        if a.status != "active":
            raise CustomerError("授权当前不是生效状态")
        a.status, a.revoked_at, a.revoke_reason = "revoked", utcnow(), reason
        ent_id = a.enterprise_id
        labels = "、".join(SCOPE_LABELS.get(x, x) for x in a.scopes or [])
    effects = apply_authorizations(ent_id, reason=f"企业撤回授权（{labels}）：{reason}", auth_id=auth_id)
    from .ops import sync_connectors

    sync_connectors()
    with session_scope() as s:
        out = auth_dict(s.get(DataAuthorization, auth_id))
    out["applied"] = effects
    return out


def expire_authorizations() -> int:
    now = utcnow()
    with session_scope() as s:
        rows = s.scalars(select(DataAuthorization).where(DataAuthorization.status == "active", DataAuthorization.valid_to < now)).all()
        ents = set()
        for a in rows:
            a.status = "expired"
            ents.add((a.enterprise_id, a.id))
    for ent, aid in ents:
        apply_authorizations(ent, reason="授权到期", auth_id=aid)
    return len(ents)


# ---------------------------------------------------------------- 联系与拜访记录

KIND_LABELS = {"visit": "现场拜访", "call": "电话沟通", "meeting": "线上会议", "interview": "管理层访谈"}


def contact_dict(c: ContactRecord, mask: bool) -> dict:
    from ..privacy.gateway import EgressReport, privacy_gateway
    from ..privacy.vault import TokenVault

    content = c.content
    phone = c.contact_phone
    if mask:
        content = privacy_gateway.desensitize_text(content, TokenVault("view"), EgressReport())
        phone = (phone[:3] + "****" + phone[-4:]) if len(phone) >= 7 else ("****" if phone else "")
    return {"id": c.id, "enterprise_id": c.enterprise_id, "kind": c.kind, "kind_label": KIND_LABELS.get(c.kind, c.kind), "subject": c.subject, "contact_name": c.contact_name if not mask else (c.contact_name[:1] + "**" if c.contact_name else ""), "contact_title": c.contact_title, "contact_phone": phone, "content": content, "summary": c.summary, "transcript_status": c.transcript_status, "material_id": c.material_id, "data_level": c.data_level, "occurred_at": iso(c.occurred_at), "created_at": iso(c.created_at), "masked": mask}


def add_contact(enterprise_id: int, *, kind: str, subject: str, contact_name: str, contact_title: str, contact_phone: str, content: str, occurred_at: datetime | None, material_id: int | None, user_id: int) -> dict:
    from ..llm.gateway import ModelUnavailable, model_gateway

    if not subject.strip():
        raise CustomerError("请填写主题")
    transcript = "none"
    if material_id:
        with session_scope() as s:
            m = s.get(Material, material_id)
            if m is None:
                raise CustomerError("录音或附件不存在")
            if (m.mime or "").startswith("audio"):
                transcript = "unavailable" if not m.text else "done"
                content = (content + "\n" + (m.text or "")).strip()
            elif m.text:
                content = (content + "\n" + m.text).strip()
                transcript = "done"
    level = max(2, detect_level(content + contact_name + contact_phone, private_context=True))
    summary: dict = {}
    if content.strip():
        try:
            summary = model_gateway.invoke("cm.contact_summary", {"content": content, "kind": KIND_LABELS.get(kind, kind)}, data_level=level)
        except ModelUnavailable as exc:
            summary = {"error": str(exc)}
    with session_scope() as s:
        c = ContactRecord(enterprise_id=enterprise_id, kind=kind, subject=subject, contact_name=contact_name, contact_title=contact_title, contact_phone=contact_phone, content=content, summary=summary, transcript_status=transcript, material_id=material_id, data_level=level, occurred_at=occurred_at or utcnow(), created_by=user_id)
        s.add(c)
        s.flush()
        if summary.get("risks"):
            notify(s, kind="system", title=f"拜访纪要识别到风险信号：{s.get(Enterprise, enterprise_id).name}", body="；".join(r["text"] for r in summary["risks"][:2]), link=f"/customers/{enterprise_id}", level="warning", user_id=user_id)
        return contact_dict(c, mask=False)


def create_enterprise(data: dict, user: dict) -> dict:
    """按统一社会信用代码建档。代码已存在时不插入，抛出带已有客户的冲突。"""
    import re

    from sqlalchemy.exc import IntegrityError

    name = str(data.get("name") or "").strip()
    code = re.sub(r"\s+", "", str(data.get("credit_code") or "")).upper()
    if not name:
        raise CustomerError("请填写企业名称")
    if not re.fullmatch(r"[0-9A-Z]{18}", code):
        raise CustomerError("统一社会信用代码须为 18 位数字或大写字母")

    def _text(key: str) -> str:
        return str(data.get(key) or "").strip()

    founded = data.get("founded_year")
    employees = data.get("employees")
    row = Enterprise(
        name=name,
        credit_code=code,
        industry=_text("industry"),
        track=_text("track"),
        tech_field=_text("tech_field"),
        region=_text("region"),
        founded_year=int(founded) if founded not in (None, "") else 0,
        employees=int(employees) if employees not in (None, "") else 0,
        listed=bool(data.get("listed") or False),
        profile={},
        manager_id=user["id"] if user.get("role") == "service" else None,
    )
    try:
        with session_scope() as s:
            taken_code = s.scalars(select(Enterprise).where(func.upper(Enterprise.credit_code) == code)).first()
            if taken_code is not None:
                raise EnterpriseConflict("该统一社会信用代码已建档", existing={"id": taken_code.id, "name": taken_code.name})
            taken_name = s.scalars(select(Enterprise).where(Enterprise.name == name)).first()
            if taken_name is not None:
                raise EnterpriseConflict("企业名称已被占用")
            s.add(row)
            s.flush()
            return {"id": row.id, "name": row.name, "credit_code": row.credit_code, "manager_id": row.manager_id}
    except IntegrityError as exc:
        with session_scope() as s:
            taken_code = s.scalars(select(Enterprise).where(func.upper(Enterprise.credit_code) == code)).first()
            if taken_code is not None:
                raise EnterpriseConflict("该统一社会信用代码已建档", existing={"id": taken_code.id, "name": taken_code.name}) from exc
        raise EnterpriseConflict("企业名称已被占用") from exc


def list_contacts(enterprise_id: int, mask: bool) -> list[dict]:
    with session_scope() as s:
        return [contact_dict(c, mask) for c in s.scalars(select(ContactRecord).where(ContactRecord.enterprise_id == enterprise_id).order_by(ContactRecord.occurred_at.desc())).all()]


# ---------------------------------------------------------------- 360 动态时间线


def timeline(enterprise_id: int) -> list[dict]:
    ev: list[dict] = []
    with session_scope() as s:
        for a in s.scalars(select(Application).where(Application.enterprise_id == enterprise_id)).all():
            prod = s.get(Product, a.product_id)
            ev.append({"at": iso(a.created_at), "type": "application", "title": f"投保 {a.app_no}", "detail": f"{prod.name if prod else ''}，保额 {a.sum_insured / 1e4:.0f} 万元，状态 {a.status}", "link": f"/underwriting/{a.id}"})
        policies = s.scalars(select(Policy).where(Policy.enterprise_id == enterprise_id)).all()
        for p in policies:
            ev.append({"at": iso(p.created_at), "type": "policy", "title": f"出单 {p.policy_no}", "detail": f"保费 {p.premium:.0f} 元，保险期间至 {(p.end_date + timedelta(hours=8)):%Y-%m-%d}", "link": f"/policies/{p.id}"})
        pids = [p.id for p in policies]
        if pids:
            for a in s.scalars(select(Alert).where(Alert.policy_id.in_(pids))).all():
                ev.append({"at": iso(a.created_at), "type": "alert", "level": a.level, "title": f"预警 {a.alert_no}", "detail": a.title, "link": f"/monitoring/alerts/{a.id}"})
            for i in s.scalars(select(Incident).where(Incident.policy_id.in_(pids))).all():
                ev.append({"at": iso(i.created_at), "type": "incident", "title": f"事故响应 {i.incident_no}", "detail": i.description[:80], "link": f"/monitoring/incidents/{i.id}"})
            for c in s.scalars(select(ClaimCase).where(ClaimCase.policy_id.in_(pids))).all():
                ev.append({"at": iso(c.created_at), "type": "claim", "title": f"报案 {c.claim_no}", "detail": f"申报 {c.claimed_amount / 1e4:.1f} 万元，状态 {c.status}", "link": f"/claims/{c.id}"})
                if c.closed_at:
                    ev.append({"at": iso(c.closed_at), "type": "claim", "title": f"结案 {c.claim_no}", "detail": f"赔付 {(c.approved_amount or 0) / 1e4:.1f} 万元", "link": f"/claims/{c.id}"})
            for e in s.scalars(select(Endorsement).where(Endorsement.policy_id.in_(pids))).all():
                ev.append({"at": iso(e.created_at), "type": "endorsement", "title": f"批改 {e.endorse_no}", "detail": f"{e.change_type}，{e.status}", "link": f"/policies/{e.policy_id}"})
            for r in s.scalars(select(Renewal).where(Renewal.policy_id.in_(pids))).all():
                ev.append({"at": iso(r.created_at), "type": "renewal", "title": "续保提醒", "detail": f"到期日 {(r.due_date + timedelta(hours=8)):%Y-%m-%d}，状态 {r.status}", "link": f"/policies/{r.policy_id}"})
        for m in s.scalars(select(Material).where(Material.enterprise_id == enterprise_id, Material.owner_type == "enterprise")).all():
            ev.append({"at": iso(m.created_at), "type": "material", "title": f"资料入库：{m.name}", "detail": f"{m.material_type} · L{m.data_level} · V{m.version or 1}", "link": None})
        for c in s.scalars(select(ContactRecord).where(ContactRecord.enterprise_id == enterprise_id)).all():
            ev.append({"at": iso(c.occurred_at), "type": "contact", "title": f"{KIND_LABELS.get(c.kind, c.kind)}：{c.subject}", "detail": "；".join((c.summary or {}).get("key_points", [])[:1]), "link": None})
        for a in s.scalars(select(DataAuthorization).where(DataAuthorization.enterprise_id == enterprise_id)).all():
            ev.append({"at": iso(a.created_at), "type": "authorization", "title": f"数据授权 {a.auth_no}", "detail": "、".join(SCOPE_LABELS.get(x, x) for x in a.scopes or []), "link": None})
            if a.revoked_at:
                ev.append({"at": iso(a.revoked_at), "type": "authorization", "level": "orange", "title": f"授权撤回 {a.auth_no}", "detail": a.revoke_reason or "", "link": None})
    return sorted(ev, key=lambda e: e["at"] or "", reverse=True)
