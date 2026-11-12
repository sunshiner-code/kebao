"""补齐的平台接口：搜索与个人中心、审批与人审流转、配置中心、组织权限、数据源与监控、
客户授权与拜访、批改续保与单证、产品版本与表现、知识治理、评测与自进化、看板与报表、协作面板。"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_, select

from ..db import session_scope, utcnow
from ..extract import MAX_UPLOAD_BYTES, extract_text
from ..harness import hitl
from ..harness.tools import registry
from ..knowledge.catalog import LINES
from ..knowledge.index import KB_NAMES
from ..models import (
    AccessGrant,
    Alert,
    Application,
    ApprovalRequest,
    Artifact,
    AuditLog,
    ClaimCase,
    Connector,
    CopilotSession,
    DataSource,
    Enterprise,
    EvalJob,
    HitlCard,
    Incident,
    JobRun,
    KnowledgeDoc,
    Material,
    Organization,
    PermissionPack,
    Policy,
    PolicyDocument,
    Product,
    ReportDefinition,
    Run,
    ScheduledJob,
    ToolCall,
    User,
)
from ..services import agent_config, approvals, connectors as live_connectors, customers, dashboards, design_tools, evals, iam, knowledge_admin, ops, policy_admin, product_admin
from ..services.config_center import SPECS, ConfigError, config_center
from ..services.iam import DATA_SCOPES, IamError, PERMISSIONS
from ..services.scheduler import JOB_TYPES, job_scheduler
from .deps import ApiError, audit, bearer, current_user, has_perm, iso, require, require_perm, user_dict
from .routes_core import AGENT_NAMES

router = APIRouter(prefix="/api")


def _err(exc: Exception) -> ApiError:
    return ApiError(409, "状态冲突", str(exc))


def _value(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ConfigError, IamError, approvals.ApprovalError, customers.CustomerError, policy_admin.PolicyAdminError, product_admin.ProductAdminError, design_tools.DesignToolError, knowledge_admin.KnowledgeError, evals.EvalError, ops.SourceDisabled, ValueError) as exc:
        raise _err(exc) from exc


# ================================================================ 搜索与个人中心
@router.get("/search")
def search(q: str, user: dict = Depends(current_user)) -> dict:
    q = q.strip()
    if len(q) < 1:
        raise ApiError(422, "输入契约不满足", "请输入搜索内容")
    like = f"%{q}%"
    scope = customers.enterprise_scope(user) if False else None
    from ..services.iam import enterprise_scope

    scope = enterprise_scope(user)
    with session_scope() as s:
        ents = s.scalars(select(Enterprise).where(or_(Enterprise.name.contains(q), Enterprise.credit_code.contains(q))).limit(8)).all()
        if scope is not None:
            ents = [e for e in ents if e.id in scope]
        pols = s.scalars(select(Policy).where(Policy.policy_no.contains(q)).limit(8)).all()
        claims = s.scalars(select(ClaimCase).where(ClaimCase.claim_no.contains(q)).limit(8)).all()
        products = s.scalars(select(Product).where(or_(Product.name.contains(q), Product.code.contains(q))).limit(8)).all()
        apps = s.scalars(select(Application).where(Application.app_no.contains(q)).limit(8)).all()
        alerts = s.scalars(select(Alert).where(or_(Alert.alert_no.contains(q), Alert.title.contains(q))).limit(8)).all()
        runs = s.scalars(select(Run).where(Run.title.contains(q), Run.parent_run_id.is_(None)).order_by(Run.id.desc()).limit(8)).all() if not q.isdigit() else s.scalars(select(Run).where(Run.id == int(q))).all()
        names = {e.id: e.name for e in s.scalars(select(Enterprise)).all()}
    return {
        "enterprises": [{"id": e.id, "title": e.name, "hint": f"{e.track} · {e.region}", "href": f"/customers/{e.id}"} for e in ents],
        "policies": [{"id": p.id, "title": p.policy_no, "hint": names.get(p.enterprise_id, ""), "href": f"/policies/{p.id}"} for p in pols],
        "claims": [{"id": c.id, "title": c.claim_no, "hint": c.status, "href": f"/claims/{c.id}"} for c in claims],
        "products": [{"id": p.id, "title": p.name, "hint": p.code, "href": f"/products/{p.id}"} for p in products],
        "applications": [{"id": a.id, "title": a.app_no, "hint": names.get(a.enterprise_id, ""), "href": f"/underwriting/{a.id}"} for a in apps],
        "alerts": [{"id": a.id, "title": a.alert_no, "hint": a.title, "href": f"/monitoring/alerts/{a.id}"} for a in alerts],
        "runs": [{"id": r.id, "title": f"#{r.id} {r.title}", "hint": AGENT_NAMES.get(r.agent_id, r.agent_id), "href": f"/runs/{r.id}"} for r in runs],
    }


class PrefsBody(BaseModel):
    email: str | None = None
    prefs: dict | None = None


@router.get("/me")
def my_profile(user: dict = Depends(current_user)) -> dict:
    from ..services.quota import limits_for

    with session_scope() as s:
        u = s.get(User, user["id"])
        grants = [g for g in iam.active_grants(u.id, s)]
        history = s.scalars(select(AccessGrant).where(AccessGrant.user_id == u.id).order_by(AccessGrant.id.desc()).limit(20)).all()
        info = user_dict(u, with_permissions=True, s=s)
        info.update({"email": u.email, "prefs": u.prefs or {}, "enabled": u.enabled, "last_login_at": iso(u.last_login_at), "created_at": iso(u.created_at)})
        info["grant_history"] = [{"id": g.id, "permissions": g.permissions, "data_scope": g.data_scope, "reason": g.reason, "hours": g.hours, "status": g.status, "expires_at": iso(g.expires_at), "created_at": iso(g.created_at)} for g in history]
    info["quota"] = limits_for(user["id"], None)
    info["active_grants"] = len(grants)
    return info


@router.patch("/me")
def update_me(body: PrefsBody, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        u = s.get(User, user["id"])
        if body.email is not None:
            u.email = body.email
        if body.prefs is not None:
            u.prefs = {**(u.prefs or {}), **body.prefs}
    return {"ok": True}


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@router.post("/me/password")
def change_my_password(body: PasswordChangeBody, user: dict = Depends(current_user)) -> dict:
    from ..seed.runner import hash_password, verify_password

    errors: list[str] = []
    if not body.current_password:
        errors.append("请填写当前密码")
    if not body.new_password:
        errors.append("请填写新密码")
    else:
        try:
            iam.check_password_policy(body.new_password)
        except IamError as exc:
            errors.append(str(exc))
        if body.new_password.lower() == str(user.get("username") or "").lower():
            errors.append("新密码不能与用户名相同")
    if body.new_password != body.confirm_password:
        errors.append("两次输入的新密码不一致")
    if errors:
        raise ApiError(422, "输入契约不满足", "；".join(errors))
    with session_scope() as s:
        u = s.get(User, user["id"])
        if u is None:
            raise ApiError(404, "状态冲突", "用户不存在")
        if not verify_password(body.current_password, u.password_hash):
            raise ApiError(422, "输入契约不满足", "当前密码不正确")
        if verify_password(body.new_password, u.password_hash):
            raise ApiError(422, "输入契约不满足", "新密码不能与当前密码相同")
        u.password_hash = hash_password(body.new_password)
        u.password_changed_at = utcnow()
        u.failed_logins = 0
        u.locked_until = None
    audit(user, "operation", "修改登录密码", "user", user["id"], {})
    return {"ok": True}


class GrantBody(BaseModel):
    permissions: list[str]
    data_scope: str | None = None
    hours: float = 2
    reason: str


@router.post("/me/grants")
def request_my_grant(body: GrantBody, user: dict = Depends(current_user)) -> dict:
    out = _value(iam.request_grant, user, body.permissions, body.data_scope, body.hours, body.reason)
    audit(user, "operation", "申请临时授权", "access_grant", out["grant_id"], {"permissions": body.permissions, "hours": body.hours})
    return out


@router.get("/me/permissions")
def permission_catalog(user: dict = Depends(current_user)) -> dict:
    ceiling = iam.role_ceiling(user["role"])
    return {"items": [p for p in PERMISSIONS if p["code"] in ceiling], "scopes": DATA_SCOPES}


# ================================================================ 审批与人审流转
@router.get("/approvals")
def list_approvals(scope: str = "pending", user: dict = Depends(current_user)) -> list[dict]:
    if scope == "mine":
        return approvals.mine(user)
    if scope == "handled":
        return approvals.handled_by(user)
    return approvals.pending_for(user)


@router.get("/approvals/{rid}")
def get_approval(rid: int, user: dict = Depends(current_user)) -> dict:
    return _value(approvals.get, rid)


class ActBody(BaseModel):
    action: str
    comment: str = ""
    transfer_to: int | None = None


@router.post("/approvals/{rid}/act")
def act_approval(rid: int, body: ActBody, user: dict = Depends(current_user)) -> dict:
    out = _value(approvals.act, rid, user, body.action, body.comment, body.transfer_to)
    audit(user, "operation", f"审批{body.action}", "approval", rid, {"comment": body.comment})
    return out


class TransferBody(BaseModel):
    to_user_id: int
    comment: str = ""


@router.post("/hitl/cards/{card_id}/claim")
def claim_card(card_id: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        c = s.get(HitlCard, card_id)
        if c is None or c.status != "open":
            raise ApiError(409, "状态冲突", "卡片不存在或已处理")
        if c.assignee_id and c.assignee_id != user["id"] and user["role"] != "admin":
            raise ApiError(409, "状态冲突", "该卡片已被其他人领取")
        c.assignee_id = user["id"]
        c.history = list(c.history or []) + [{"action": "claim", "by": user["id"], "name": user["display_name"], "at": iso(utcnow())}]
    audit(user, "operation", "领取人审卡片", "hitl_card", card_id)
    return {"ok": True}


@router.post("/hitl/cards/{card_id}/transfer")
def transfer_card(card_id: int, body: TransferBody, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        c = s.get(HitlCard, card_id)
        target = s.get(User, body.to_user_id)
        if c is None or c.status != "open":
            raise ApiError(409, "状态冲突", "卡片不存在或已处理")
        if target is None or not target.enabled:
            raise ApiError(409, "状态冲突", "转办对象不可用")
        if user["role"] != "admin" and c.assignee_id not in (None, user["id"]) and c.assignee_role != user["role"]:
            raise ApiError(403, "权限不足", "只能转办分派给自己的卡片")
        c.assignee_id = target.id
        c.assignee_role = target.role
        c.history = list(c.history or []) + [{"action": "transfer", "by": user["id"], "to": target.id, "name": target.display_name, "comment": body.comment, "at": iso(utcnow())}]
        from ..services.notify import notify

        notify(s, kind="hitl", title=f"转办待办：{c.title}", body=f"{user['display_name']} 转办：{body.comment}", link=f"/inbox?card={c.id}", level="warning", user_id=target.id)
    audit(user, "operation", "转办人审卡片", "hitl_card", card_id, {"to": body.to_user_id})
    return {"ok": True}


# ================================================================ 配置中心
@router.get("/admin/settings")
def list_settings(user: dict = Depends(require_perm("admin.settings", "uw.rules.edit", "rw.templates.edit", "admin.privacy"))) -> list[dict]:
    return config_center.list()


@router.get("/admin/settings/{key:path}")
def get_setting(key: str, user: dict = Depends(require_perm("admin.settings", "uw.rules.edit", "rw.templates.edit", "admin.privacy"))) -> dict:
    if key not in SPECS:
        raise ApiError(404, "状态冲突", "配置项不存在")
    return config_center.describe(key)


class SettingBody(BaseModel):
    value: dict
    reason: str


@router.put("/admin/settings/{key:path}")
def put_setting(key: str, body: SettingBody, user: dict = Depends(require_perm("admin.settings", "uw.rules.edit", "rw.templates.edit", "admin.privacy"))) -> dict:
    out = _value(config_center.update, key, body.value, user=user["display_name"], reason=body.reason)
    audit(user, "config", "修改系统配置", "setting", key, {"reason": body.reason, "version": out["version"]})
    return out


class RollbackBody(BaseModel):
    version: int
    reason: str


@router.post("/admin/settings/{key:path}/rollback")
def rollback_setting(key: str, body: RollbackBody, user: dict = Depends(require_perm("admin.settings", "uw.rules.edit", "rw.templates.edit", "admin.privacy"))) -> dict:
    out = _value(config_center.rollback, key, body.version, user=user["display_name"], reason=body.reason)
    audit(user, "config", "回滚系统配置", "setting", key, {"to": body.version, "reason": body.reason})
    return out


# ================================================================ 组织、用户、权限包
def _org_dict(o: Organization, users: dict[int, str]) -> dict:
    return {"id": o.id, "code": o.code, "name": o.name, "kind": o.kind, "parent_id": o.parent_id, "leader_id": o.leader_id, "leader": users.get(o.leader_id or 0), "data_scope": o.data_scope, "overrides": o.overrides, "enabled": o.enabled, "sort": o.sort}


@router.get("/admin/orgs")
def list_orgs(user: dict = Depends(require_perm("admin.users"))) -> list[dict]:
    with session_scope() as s:
        users = {u.id: u.display_name for u in s.scalars(select(User)).all()}
        return [_org_dict(o, users) for o in s.scalars(select(Organization).order_by(Organization.sort)).all()]


class OrgBody(BaseModel):
    code: str
    name: str
    kind: str = "department"
    parent_id: int | None = None
    leader_id: int | None = None
    data_scope: str = "department"
    overrides: dict = {}


@router.post("/admin/orgs")
def create_org(body: OrgBody, user: dict = Depends(require_perm("admin.users"))) -> dict:
    if body.data_scope not in DATA_SCOPES:
        raise ApiError(422, "输入契约不满足", "数据范围无效")
    with session_scope() as s:
        if body.parent_id and s.get(Organization, body.parent_id) is None:
            raise ApiError(404, "状态冲突", "上级机构不存在")
        o = Organization(code=body.code, name=body.name, kind=body.kind, parent_id=body.parent_id, leader_id=body.leader_id, data_scope=body.data_scope, overrides={k: v for k, v in body.overrides.items() if k in ("approval_thresholds", "notify_channels")})
        s.add(o)
        s.flush()
        oid = o.id
    audit(user, "config", "新建机构", "organization", oid)
    return {"id": oid}


class OrgPatch(BaseModel):
    name: str | None = None
    leader_id: int | None = None
    data_scope: str | None = None
    overrides: dict | None = None
    enabled: bool | None = None


@router.patch("/admin/orgs/{oid}")
def patch_org(oid: int, body: OrgPatch, user: dict = Depends(require_perm("admin.users"))) -> dict:
    with session_scope() as s:
        o = s.get(Organization, oid)
        if o is None:
            raise ApiError(404, "状态冲突", "机构不存在")
        for k, v in body.model_dump(exclude_none=True).items():
            if k == "overrides":
                v = {kk: vv for kk, vv in v.items() if kk in ("approval_thresholds", "notify_channels")}
            setattr(o, k, v)
    audit(user, "config", "修改机构", "organization", oid, body.model_dump(exclude_none=True))
    return {"ok": True}


class UserBody(BaseModel):
    username: str
    display_name: str
    password: str
    password_confirm: str = ""
    role: str
    positions: list[str] = []
    org_id: int | None = None
    approval_level: int = 1
    data_scope: str = "self"
    email: str | None = None


@router.post("/admin/users")
def create_user(body: UserBody, user: dict = Depends(require_perm("admin.users"))) -> dict:
    from ..seed.runner import hash_password

    if body.role not in ("designer", "service", "admin"):
        raise ApiError(422, "输入契约不满足", "基础角色只能是设计、服务或管理")
    try:
        username, display_name = iam.validate_new_account(body.username, body.display_name, body.password, body.password_confirm)
    except IamError as exc:
        raise ApiError(422, "输入契约不满足", str(exc)) from exc
    positions = _value(iam.validate_positions, body.role, body.positions)
    with session_scope() as s:
        if s.scalars(select(User).where(User.username == username)).first():
            raise ApiError(409, "状态冲突", "用户名已存在")
        org = s.get(Organization, body.org_id) if body.org_id else None
        u = User(username=username, display_name=display_name, role=body.role, positions=positions, org_id=body.org_id, org=org.name if org else "总公司", approval_level=body.approval_level, data_scope=body.data_scope, email=body.email, password_hash=hash_password(body.password), password_changed_at=utcnow())
        s.add(u)
        s.flush()
        info = user_dict(u)
    audit(user, "config", "创建账号", "user", info["id"], {"role": body.role, "positions": positions})
    return info


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    positions: list[str] | None = None
    org_id: int | None = None
    approval_level: int | None = None
    data_scope: str | None = None
    email: str | None = None
    enabled: bool | None = None
    quota: dict | None = None
    handover_to: int | None = None
    password: str | None = None
    confirm_role_change: bool = False


@router.patch("/admin/users/{uid}")
def patch_user(uid: int, body: UserPatch, user: dict = Depends(require_perm("admin.users"))) -> dict:
    from ..seed.runner import hash_password

    with session_scope() as s:
        u = s.get(User, uid)
        if u is None:
            raise ApiError(404, "状态冲突", "用户不存在")
        if body.role and body.role != u.role and not body.confirm_role_change:
            raise ApiError(409, "状态冲突", "变更基础角色属于高风险操作，需要二次确认")
        role = body.role or u.role
        positions = body.positions if body.positions is not None else list(u.positions or [])
        positions = _value(iam.validate_positions, role, positions)
        if body.password:
            _value(iam.check_password_policy, body.password)
            u.password_hash = hash_password(body.password)
            u.password_changed_at = utcnow()
            u.failed_logins = 0
            u.locked_until = None
        if body.enabled is False and u.enabled:
            if not body.handover_to:
                raise ApiError(409, "状态冲突", "停用账号前请指定交接人")
            target = s.get(User, body.handover_to)
            if target is None or not target.enabled or target.id == u.id:
                raise ApiError(409, "状态冲突", "交接人不可用")
            moved = iam.handover(s, u, target.id)
            running = iam.running_runs_of(s, u.id)
            u.enabled = False
            audit(user, "config", "停用账号并交接", "user", uid, {"to": body.handover_to, "moved": moved, "running_runs": running})
        elif body.enabled is True:
            u.enabled = True
        for k in ("display_name", "role", "approval_level", "data_scope", "email", "quota"):
            v = getattr(body, k)
            if v is not None:
                setattr(u, k, v)
        u.positions = positions
        if body.org_id is not None:
            org = s.get(Organization, body.org_id)
            u.org_id = body.org_id
            u.org = org.name if org else u.org
        if body.role and body.role != user.get("_"):
            pass
    if body.role:
        audit(user, "config", "变更基础角色", "user", uid, {"role": body.role, "confirmed": True})
    audit(user, "config", "修改账号", "user", uid, {k: v for k, v in body.model_dump(exclude_none=True).items() if k not in ("password", "confirm_role_change")})
    return {"ok": True}


class ImportBody(BaseModel):
    rows: list[UserBody]


@router.post("/admin/users/import")
def import_users(body: ImportBody, user: dict = Depends(require_perm("admin.users"))) -> dict:
    created, failed = [], []
    for row in body.rows:
        try:
            created.append(create_user(row, user)["username"])
        except ApiError as exc:
            failed.append({"username": row.username, "message": exc.detail["message"] if isinstance(exc.detail, dict) else str(exc.detail)})
    return {"created": created, "failed": failed}


@router.get("/admin/permission-packs")
def list_packs(user: dict = Depends(require_perm("admin.roles", "admin.users"))) -> dict:
    with session_scope() as s:
        packs = [{"id": p.id, "name": p.name, "base_role": p.base_role, "permissions": p.permissions, "description": p.description, "builtin": p.builtin} for p in s.scalars(select(PermissionPack).order_by(PermissionPack.id)).all()]
    return {"packs": packs, "permissions": PERMISSIONS, "conflicts": [{"a": a, "b": b, "message": m} for a, b, m in iam.SOD_CONFLICTS]}


class PackBody(BaseModel):
    name: str
    base_role: str
    permissions: list[str]
    description: str = ""


@router.post("/admin/permission-packs")
def create_pack(body: PackBody, user: dict = Depends(require_perm("admin.roles"))) -> dict:
    ceiling = iam.role_ceiling(body.base_role)
    over = [p for p in body.permissions if p not in ceiling]
    if over:
        raise ApiError(422, "输入契约不满足", "超出基础角色上限：" + "、".join(over))
    violations = iam.sod_violations(set(body.permissions))
    if violations:
        raise ApiError(422, "输入契约不满足", "职责分离冲突：" + "；".join(violations))
    with session_scope() as s:
        if s.scalars(select(PermissionPack).where(PermissionPack.name == body.name)).first():
            raise ApiError(409, "状态冲突", "岗位包名称已存在")
        p = PermissionPack(name=body.name, base_role=body.base_role, permissions=body.permissions, description=body.description, builtin=False)
        s.add(p)
        s.flush()
        pid = p.id
    iam.pack_cache.load()
    audit(user, "config", "新建岗位权限包", "permission_pack", pid)
    return {"id": pid}


@router.patch("/admin/permission-packs/{pid}")
def patch_pack(pid: int, body: PackBody, user: dict = Depends(require_perm("admin.roles"))) -> dict:
    ceiling = iam.role_ceiling(body.base_role)
    if any(p not in ceiling for p in body.permissions) or iam.sod_violations(set(body.permissions)):
        raise ApiError(422, "输入契约不满足", "权限超出角色上限或存在职责分离冲突")
    with session_scope() as s:
        p = s.get(PermissionPack, pid)
        if p is None:
            raise ApiError(404, "状态冲突", "岗位包不存在")
        p.permissions, p.description, p.base_role = body.permissions, body.description, body.base_role
    iam.pack_cache.load()
    audit(user, "config", "修改岗位权限包", "permission_pack", pid)
    return {"ok": True}


# ================================================================ 数据源、连接器、监控、任务、额度
@router.get("/admin/data-sources")
def data_sources(user: dict = Depends(require_perm("admin.integration"))) -> list[dict]:
    with session_scope() as s:
        return [{"id": d.id, "code": d.code, "name": d.name, "category": d.category, "provider": d.provider, "enabled": d.enabled, "health": d.health, "latency_ms": d.latency_ms, "calls": d.calls, "failures": d.failures, "failure_rate": d.failure_rate, "daily_quota": d.daily_quota, "used_today": d.used_today, "unit_cost": d.unit_cost, "field_mapping": d.field_mapping, "probe_history": d.probe_history[-12:], "last_checked_at": iso(d.last_checked_at), "last_error": d.last_error} for d in s.scalars(select(DataSource).order_by(DataSource.id)).all()]


class SourcePatch(BaseModel):
    enabled: bool | None = None
    daily_quota: int | None = None
    provider: str | None = None


@router.patch("/admin/data-sources/{code}")
def patch_source(code: str, body: SourcePatch, user: dict = Depends(require_perm("admin.integration"))) -> dict:
    with session_scope() as s:
        d = s.scalars(select(DataSource).where(DataSource.code == code)).first()
        if d is None:
            raise ApiError(404, "状态冲突", "数据源不存在")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(d, k, v)
    audit(user, "config", "修改数据源", "data_source", code, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/admin/data-sources/probe")
def probe(user: dict = Depends(require_perm("admin.integration"))) -> dict:
    return {"probes": ops.probe_sources()}


@router.get("/admin/connectors")
def connectors(user: dict = Depends(require_perm("admin.integration"))) -> list[dict]:
    ops.sync_connectors()
    with session_scope() as s:
        ents = {e.id: e.name for e in s.scalars(select(Enterprise)).all()}
        return [{"id": c.id, "enterprise": ents.get(c.enterprise_id), "enterprise_id": c.enterprise_id, "policy_id": c.policy_id, "kind": c.kind, "name": c.name, "endpoint": c.endpoint, "status": c.status, "authorization_id": c.authorization_id, "last_heartbeat": iso(c.last_heartbeat), "quality": c.quality, **live_connectors.public_view(c)} for c in s.scalars(select(Connector).order_by(Connector.id)).all()]


@router.post("/admin/connectors/{cid}/outage")
def connector_outage(cid: int, outage: bool, user: dict = Depends(require_perm("admin.integration"))) -> dict:
    return _value(ops.set_connector_outage, cid, outage)


class ConnectorPatch(BaseModel):
    mode: str | None = None
    direction: str | None = None
    host: str | None = None
    port: int | None = None
    path: str | None = None
    allow_ips: list[str] | None = None
    field_map: dict | None = None
    asset_map: dict | None = None
    pull_interval_minutes: int | None = None
    secret: str | None = None


def _connector_error(exc: live_connectors.ConnectorError) -> ApiError:
    category = "权限不足" if exc.status in (401, 403) else ("状态冲突" if exc.status == 409 else "输入契约不满足")
    return ApiError(exc.status, category, str(exc))


@router.patch("/admin/connectors/{cid}")
def patch_connector(cid: int, body: ConnectorPatch, user: dict = Depends(require_perm("admin.integration"))) -> dict:
    try:
        out = live_connectors.configure(cid, body.model_dump(exclude_none=True))
    except live_connectors.ConnectorError as exc:
        raise _connector_error(exc) from exc
    audit(user, "config", "配置企业连接器", "connector", cid, {k: v for k, v in body.model_dump(exclude_none=True).items() if k != "secret"})
    return out


@router.post("/admin/connectors/{cid}/token")
def rotate_connector_token(cid: int, user: dict = Depends(require_perm("admin.integration"))) -> dict:
    try:
        out = live_connectors.rotate_token(cid)
    except live_connectors.ConnectorError as exc:
        raise _connector_error(exc) from exc
    audit(user, "config", "轮换连接器推送令牌", "connector", cid)
    return out


class IngestPoint(BaseModel):
    asset: str = ""
    metric: str
    ts: str
    value: float


class IngestBody(BaseModel):
    points: list[IngestPoint]


@router.post("/ingest/connectors/{cid}")
def ingest_connector(cid: int, body: IngestBody, request: Request, creds=Depends(bearer)) -> dict:
    token = creds.credentials if creds else ""
    ip = request.client.host if request.client else ""
    try:
        return live_connectors.ingest(cid, token, ip, [p.model_dump() for p in body.points])
    except live_connectors.ConnectorError as exc:
        raise _connector_error(exc) from exc


@router.get("/admin/monitor")
def system_monitor(user: dict = Depends(require_perm("admin.settings", "admin.integration"))) -> dict:
    return ops.system_status()


@router.get("/admin/jobs")
def jobs(user: dict = Depends(require_perm("admin.settings"))) -> list[dict]:
    running = job_scheduler.running()
    with session_scope() as s:
        out = []
        for j in s.scalars(select(ScheduledJob).order_by(ScheduledJob.id)).all():
            last = s.scalars(select(JobRun).where(JobRun.job_id == j.id).order_by(JobRun.id.desc()).limit(5)).all()
            out.append({"id": j.id, "code": j.code, "name": j.name, "job_type": j.job_type, "interval_minutes": j.interval_minutes, "params": j.params, "enabled": j.enabled, "last_run_at": iso(j.last_run_at), "next_run_at": iso(j.next_run_at), "last_status": j.last_status, "running": j.id in running, "recent": [{"id": r.id, "trigger": r.trigger, "status": r.status, "attempt": r.attempt, "output": r.output, "error": r.error, "started_at": iso(r.started_at), "ended_at": iso(r.ended_at)} for r in last]})
    return out


class JobPatch(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = None
    params: dict | None = None


@router.patch("/admin/jobs/{jid}")
def patch_job(jid: int, body: JobPatch, user: dict = Depends(require_perm("admin.settings"))) -> dict:
    with session_scope() as s:
        j = s.get(ScheduledJob, jid)
        if j is None:
            raise ApiError(404, "状态冲突", "任务不存在")
        if body.enabled is not None:
            j.enabled = body.enabled
        if body.interval_minutes:
            j.interval_minutes = body.interval_minutes
            j.next_run_at = utcnow() + timedelta(minutes=body.interval_minutes)
        if body.params is not None:
            j.params = body.params
    audit(user, "config", "修改定时任务", "job", jid, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/admin/jobs/{jid}/run")
def run_job_now(jid: int, user: dict = Depends(require_perm("admin.settings"))) -> dict:
    job_scheduler.trigger(jid)
    audit(user, "operation", "手动执行定时任务", "job", jid)
    return {"ok": True}


@router.get("/admin/job-types")
def job_types(user: dict = Depends(require_perm("admin.settings"))) -> list[dict]:
    return [{"type": k, "name": v[0]} for k, v in JOB_TYPES.items()]


@router.get("/admin/quota")
def quota_overview(user: dict = Depends(require_perm("admin.models", "admin.settings"))) -> dict:
    from ..services.quota import usage_today

    usage = usage_today(force=True)
    with session_scope() as s:
        users = {u.id: u.display_name for u in s.scalars(select(User)).all()}
        orgs = {o.id: o.name for o in s.scalars(select(Organization)).all()}
    return {"usage": {"total": usage["total"], "users": [{"id": k, "name": users.get(k, str(k)), **v} for k, v in usage["users"].items()], "orgs": [{"id": k, "name": orgs.get(k, str(k)), **v} for k, v in usage["orgs"].items()], "agents": [{"agent_id": k, "name": AGENT_NAMES.get(k, k), **v} for k, v in usage["agents"].items()]}, "config_key": "quota"}


class ExportBody(BaseModel):
    category: str | None = None
    username: str | None = None
    action: str | None = None
    start: str | None = None
    end: str | None = None
    reason: str


@router.post("/admin/audit/export")
def request_audit_export(body: ExportBody, user: dict = Depends(require_perm("admin.audit"))) -> dict:
    from ..services.config_center import cfg

    if not cfg("security").get("export_approval", True) or has_perm(user, "admin.audit.export"):
        csv_text = ops.audit_csv(body.model_dump())
        audit(user, "data_access", "导出审计日志", "audit", None, {"filters": body.model_dump()})
        return Response(content=csv_text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit.csv"})
    req = approvals.start("export", object_type="audit_export", object_id=user["id"], title=f"审计日志导出：{user['display_name']}", summary={"筛选": body.model_dump(), "理由": body.reason}, applicant=user, context={"filters": body.model_dump()})
    audit(user, "operation", "申请导出审计日志", "approval", req["id"])
    return req


@router.get("/admin/audit/export/{approval_id}")
def download_audit_export(approval_id: int, user: dict = Depends(require_perm("admin.audit.export"))) -> Response:
    req = approvals.get(approval_id)
    if req["status"] != "approved" or req["flow_type"] != "export":
        raise ApiError(409, "状态冲突", "导出申请尚未通过")
    csv_text = ops.audit_csv((req.get("context") or {}).get("filters") or {})
    audit(user, "data_access", "导出审计日志", "approval", approval_id)
    return Response(content=csv_text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit.csv"})


# ================================================================ 客户
@router.get("/enterprises/{eid}/library")
def dd_library(eid: int, user: dict = Depends(current_user)) -> list[dict]:
    if user["role"] == "designer" and not customers.designer_can_view(user["id"], eid):
        raise ApiError(403, "权限不足", "只能查看自己参与的设计项目中的企业资料")
    return customers.dd_library(eid)


@router.get("/materials/{mid}/access")
def material_access(mid: int, user: dict = Depends(current_user)) -> list[dict]:
    return customers.material_access_log(mid)


@router.get("/enterprises/{eid}/authorizations")
def auths(eid: int, user: dict = Depends(require("service"))) -> list[dict]:
    return customers.list_authorizations(eid)


class AuthBody(BaseModel):
    policy_id: int | None = None
    scopes: list[str]
    items: list[str] = []
    purpose: str = "承保期间风险监测与事故响应"
    grantor: str
    valid_days: int = 365
    material_id: int | None = None


@router.post("/enterprises/{eid}/authorizations")
def grant_auth(eid: int, body: AuthBody, user: dict = Depends(require_perm("customer.auth.manage"))) -> dict:
    out = _value(customers.grant_authorization, eid, policy_id=body.policy_id, scopes=body.scopes, items=body.items, purpose=body.purpose, grantor=body.grantor, valid_days=body.valid_days, material_id=body.material_id, user_id=user["id"])
    audit(user, "operation", "登记数据接入授权", "authorization", out["id"], {"scopes": body.scopes})
    return out


class RevokeBody(BaseModel):
    reason: str


@router.post("/authorizations/{aid}/revoke")
def revoke_auth(aid: int, body: RevokeBody, user: dict = Depends(require_perm("customer.auth.manage"))) -> dict:
    out = _value(customers.revoke_authorization, aid, body.reason)
    audit(user, "operation", "撤回数据接入授权", "authorization", aid, {"reason": body.reason})
    return out


@router.get("/enterprises/{eid}/contacts")
def contacts(eid: int, user: dict = Depends(require("service"))) -> list[dict]:
    return customers.list_contacts(eid, mask=not has_perm(user, "privacy.L2.view"))


class ContactBody(BaseModel):
    kind: str = "visit"
    subject: str
    contact_name: str = ""
    contact_title: str = ""
    contact_phone: str = ""
    content: str = ""
    occurred_at: str | None = None
    material_id: int | None = None


@router.post("/enterprises/{eid}/contacts")
def add_contact(eid: int, body: ContactBody, user: dict = Depends(require_perm("customer.contact"))) -> dict:
    occurred = datetime.fromisoformat(body.occurred_at) if body.occurred_at else None
    out = _value(customers.add_contact, eid, kind=body.kind, subject=body.subject, contact_name=body.contact_name, contact_title=body.contact_title, contact_phone=body.contact_phone, content=body.content, occurred_at=occurred, material_id=body.material_id, user_id=user["id"])
    audit(user, "operation", "登记拜访记录", "enterprise", eid, {"privacy": "L2原文"})
    return out


@router.get("/enterprises/{eid}/timeline")
def timeline(eid: int, user: dict = Depends(current_user)) -> list[dict]:
    return customers.timeline(eid)


# ================================================================ 批改、续保、单证
@router.get("/policies/{pid}/endorsements")
def endorsements(pid: int, user: dict = Depends(require("service"))) -> list[dict]:
    with session_scope() as s:
        from ..models import Endorsement

        return [policy_admin.endorsement_dict(e) for e in s.scalars(select(Endorsement).where(Endorsement.policy_id == pid).order_by(Endorsement.id.desc())).all()]


class EndorseBody(BaseModel):
    change_type: str
    after: dict
    reason: str


@router.post("/policies/{pid}/endorsements")
def create_endorsement(pid: int, body: EndorseBody, user: dict = Depends(require_perm("policy.endorse"))) -> dict:
    out = _value(policy_admin.create_endorsement, pid, body.change_type, body.after, body.reason, user)
    audit(user, "operation", "发起批改", "endorsement", out["id"])
    return out


@router.get("/renewals")
def renewals(user: dict = Depends(require("service"))) -> list[dict]:
    policy_admin.scan_renewals()
    return policy_admin.list_renewals()


@router.post("/renewals/{rid}/assess")
def assess_renewal(rid: int, user: dict = Depends(require_perm("policy.renew"))) -> dict:
    out = _value(policy_admin.start_renewal_assessment, rid, user)
    audit(user, "operation", "发起续保评估", "renewal", rid)
    return out


@router.post("/renewals/{rid}/decline")
def decline_renewal(rid: int, body: RevokeBody, user: dict = Depends(require_perm("policy.renew"))) -> dict:
    return _value(policy_admin.decline_renewal, rid, body.reason, user)


@router.get("/policies/{pid}/documents")
def documents(pid: int, user: dict = Depends(require("service"))) -> list[dict]:
    return policy_admin.list_documents(pid)


@router.post("/policies/{pid}/documents")
def make_document(pid: int, doc_type: str, ref_id: int | None = None, user: dict = Depends(require("service"))) -> dict:
    out = _value(policy_admin.generate_document, pid, doc_type, ref_id=ref_id, user_id=user["id"])
    audit(user, "operation", "生成单证", "policy_document", out["id"], {"type": doc_type})
    return out


@router.get("/documents/{did}", response_class=HTMLResponse)
def document_html(did: int, user: dict = Depends(require("service"))):
    with session_scope() as s:
        d = s.get(PolicyDocument, did)
        if d is None:
            raise ApiError(404, "状态冲突", "单证不存在")
        html, no = d.html, d.doc_no
    audit(user, "data_access", "查看电子单证", "policy_document", did)
    return HTMLResponse(html)


# ================================================================ 产品治理
@router.get("/products/{pid}/completeness")
def completeness(pid: int, user: dict = Depends(current_user)) -> dict:
    return _value(product_admin.completeness, pid)


class PublishBody(BaseModel):
    online_at: str
    regions: list[str]
    channels: list[str] = []
    stop_conditions: list[dict] = []
    note: str = ""


@router.put("/products/{pid}/publish")
def set_publish(pid: int, body: PublishBody, user: dict = Depends(require_perm("product.submit"))) -> dict:
    out = _value(product_admin.set_publish, pid, body.model_dump())
    audit(user, "operation", "填写发布配置", "product", pid)
    return out


@router.post("/products/{pid}/submit")
def submit_product(pid: int, note: str = "", user: dict = Depends(require_perm("product.submit"))) -> dict:
    out = _value(product_admin.submit, pid, user, note)
    audit(user, "operation", "提交产品发布审批", "product", pid)
    return out


@router.get("/products/{pid}/versions")
def product_versions(pid: int, user: dict = Depends(current_user)) -> list[dict]:
    return _value(product_admin.versions, pid)


@router.get("/products/{pid}/compare")
def product_compare(pid: int, a: int, b: int, user: dict = Depends(current_user)) -> dict:
    return _value(product_admin.compare, pid, a, b)


@router.get("/products/{pid}/performance")
def product_performance(pid: int, user: dict = Depends(current_user)) -> dict:
    return _value(product_admin.performance, pid)


class SaleBody(BaseModel):
    action: str
    reason: str


@router.post("/products/{pid}/sale")
def product_sale(pid: int, body: SaleBody, user: dict = Depends(require_perm("product.publish"))) -> dict:
    out = _value(product_admin.set_sale_status, pid, body.action, body.reason)
    audit(user, "operation", "调整产品销售状态", "product", pid, body.model_dump())
    return out


@router.post("/products/{pid}/iterate")
def iterate_product(pid: int, reason: str, user: dict = Depends(require_perm("product.iterate"))) -> dict:
    project_id = _value(product_admin.start_iteration, pid, reason, user["id"])
    audit(user, "operation", "发起产品迭代", "product", pid)
    return {"project_id": project_id}


# ================================================================ 条款编辑与定价配置
@router.get("/clause-plans/{key}/editor")
def clause_editor(key: str, version: int | None = None, user: dict = Depends(require("designer"))) -> dict:
    return _value(design_tools.editor_view, key, version)


class EditBody(BaseModel):
    base_version: int
    op: dict
    reason: str


@router.post("/clause-plans/{key}/edit")
def clause_edit(key: str, body: EditBody, user: dict = Depends(require_perm("design.clause.edit"))) -> dict:
    out = _value(design_tools.edit, key, body.base_version, body.op, body.reason, user)
    audit(user, "operation", "编辑条款", "artifact", key, {"op": body.op.get("op"), "reason": body.reason})
    return out


class RegenBody(BaseModel):
    doc_index: int
    chapter_key: str
    article_index: int
    instruction: str


@router.post("/clause-plans/{key}/regenerate")
def clause_regen(key: str, body: RegenBody, user: dict = Depends(require_perm("design.clause.edit"))) -> dict:
    return _value(design_tools.regenerate, key, body.doc_index, body.chapter_key, body.article_index, body.instruction, user)


@router.post("/clause-plans/{key}/recheck")
def clause_recheck(key: str, user: dict = Depends(require_perm("design.clause.edit"))) -> dict:
    return _value(design_tools.recheck, key, user)


@router.post("/clause-plans/{key}/refreeze")
def clause_refreeze(key: str, version: int, user: dict = Depends(require_perm("design.artifact.freeze"))) -> dict:
    out = _value(design_tools.refreeze, key, version, user)
    audit(user, "operation", "重新冻结条款", "artifact", f"{key}@{version}")
    return out


@router.get("/clause-templates")
def clause_templates(line: str | None = None, kind: str | None = None, q: str | None = None, user: dict = Depends(require("designer"))) -> dict:
    return design_tools.templates(line, kind, q)


@router.get("/design-projects/{pid}/pricing-config")
def get_pricing_config(pid: int, user: dict = Depends(require("designer"))) -> dict:
    return _value(design_tools.pricing_config, pid)


class PricingCfg(BaseModel):
    methods: list[str] = []
    targets: dict = {}
    experiment: dict = {}
    clause_version: int | None = None


@router.put("/design-projects/{pid}/pricing-config")
def put_pricing_config(pid: int, body: PricingCfg, user: dict = Depends(require("designer"))) -> dict:
    return _value(design_tools.set_pricing_config, pid, body.model_dump(), user)


# ================================================================ 知识治理
@router.get("/knowledge/docs/{did}")
def kb_doc(did: int, user: dict = Depends(current_user)) -> dict:
    return _value(knowledge_admin.doc_detail, did, 3 if has_perm(user, "privacy.L2.view") or user["role"] == "admin" else 1)


@router.get("/knowledge/docs/{did}/impact")
def kb_impact(did: int, user: dict = Depends(current_user)) -> dict:
    return _value(knowledge_admin.impact, did)


@router.get("/knowledge/batches")
def batches(user: dict = Depends(require_perm("kb.ingest", "kb.edit"))) -> list[dict]:
    return knowledge_admin.list_batches()


@router.get("/knowledge/batches/{bid}")
def batch(bid: int, user: dict = Depends(require_perm("kb.ingest", "kb.edit"))) -> dict:
    return _value(knowledge_admin.get_batch, bid)


@router.post("/knowledge/ingest")
async def ingest(kb: str = Form(...), source: str = Form(...), doc_type: str = Form("知识文档"), files: list[UploadFile] = File(...), user: dict = Depends(require_perm("kb.ingest"))) -> dict:
    parsed = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ApiError(413, "输入契约不满足", "单个文件不超过 30MB")
        name = f.filename or "upload.txt"
        text = extract_text(name, raw)
        if text is None:
            raise ApiError(422, "输入契约不满足", "文件格式不支持，请上传文本、Markdown、PDF 或 Word")
        if not text.strip():
            raise ApiError(422, "输入契约不满足", "没有解析出正文")
        parsed.append((name, text))
    out = _value(knowledge_admin.create_batch, kb, source, parsed, doc_type, user["id"])
    audit(user, "operation", "知识入库", "ingest_batch", out["id"])
    return out


class QcBody(BaseModel):
    verdict: str
    corrections: dict = {}
    comment: str = ""


@router.post("/knowledge/batches/{bid}/items/{idx}/qc")
def qc(bid: int, idx: int, body: QcBody, user: dict = Depends(require_perm("kb.ingest"))) -> dict:
    return _value(knowledge_admin.qc_item, bid, idx, body.verdict, body.corrections, body.comment, user)


@router.post("/knowledge/batches/{bid}/publish")
def publish_batch(bid: int, user: dict = Depends(require_perm("kb.ingest"))) -> dict:
    out = _value(knowledge_admin.publish_batch, bid, user)
    audit(user, "operation", "发布知识批次", "ingest_batch", bid)
    return out


@router.post("/knowledge/batches/{bid}/return")
def return_batch(bid: int, body: RevokeBody, user: dict = Depends(require_perm("kb.ingest"))) -> dict:
    return _value(knowledge_admin.return_batch, bid, body.reason, user)


@router.get("/knowledge/factors")
def factors(user: dict = Depends(current_user)) -> list[dict]:
    return knowledge_admin.factor_dictionary()


@router.get("/knowledge/fraud-patterns")
def fraud_patterns(user: dict = Depends(current_user)) -> list[dict]:
    return knowledge_admin.fraud_library()


class AskBody(BaseModel):
    question: str
    kbs: list[str] = []


@router.post("/knowledge/answer")
def kb_answer(body: AskBody, user: dict = Depends(current_user)) -> dict:
    level = 2 if has_perm(user, "privacy.L2.view") else 1
    return _value(knowledge_admin.answer, body.question, body.kbs or None, level)


@router.get("/knowledge/proposals")
def kb_proposals(status: str | None = None, user: dict = Depends(current_user)) -> list[dict]:
    return knowledge_admin.list_proposals(status)


class ProposalBody(BaseModel):
    kb: str
    action: str
    doc_id: int | None = None
    title: str = ""
    content: str = ""
    meta: dict = {}
    rationale: str


@router.post("/knowledge/proposals")
def kb_propose(body: ProposalBody, user: dict = Depends(require_perm("kb.proposal.submit"))) -> dict:
    out = _value(knowledge_admin.submit_proposal, body.kb, body.action, body.doc_id, body.title, body.content, body.meta, body.rationale, user)
    audit(user, "operation", "提交知识变更提案", "knowledge_proposal", out["id"])
    return out


@router.get("/knowledge/kbs")
def kb_names(user: dict = Depends(current_user)) -> dict:
    return KB_NAMES


# ================================================================ 智能体配置、评测、自进化
@router.get("/agents/{agent_id}/configs")
def agent_configs(agent_id: str, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> dict:
    return {"base": agent_config.base_config(agent_id), "fields": agent_config.FIELDS, "versions": agent_config.list_versions(agent_id)}


class ConfigBody(BaseModel):
    config: dict
    note: str = ""


@router.post("/agents/{agent_id}/configs")
def new_agent_config(agent_id: str, body: ConfigBody, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    out = _value(agent_config.create_version, agent_id, body.config, body.note, user["display_name"])
    audit(user, "config", "新建智能体配置版本", "agent_config", out["id"])
    return out


@router.post("/agent-configs/{vid}/canary")
def canary(vid: int, ratio: float = 0.2, user: dict = Depends(require_perm("agent.release"))) -> dict:
    return _value(agent_config.start_canary, vid, ratio)


@router.post("/agent-configs/{vid}/activate")
def activate_config(vid: int, user: dict = Depends(require_perm("agent.release"))) -> dict:
    out = _value(agent_config.activate, vid)
    audit(user, "config", "发布智能体配置", "agent_config", vid)
    return out


@router.post("/agents/{agent_id}/rollback")
def rollback_agent(agent_id: str, user: dict = Depends(require_perm("agent.release"))) -> dict:
    out = _value(agent_config.rollback, agent_id)
    audit(user, "config", "回滚智能体配置", "agent", agent_id)
    return out


@router.get("/tools/stats")
def tool_stats(user: dict = Depends(require("admin"))) -> list[dict]:
    disabled = set((__import__("techins.services.config_center", fromlist=["cfg"]).cfg("agents.tools").get("disabled_tools") or []))
    with session_scope() as s:
        rows = s.execute(select(ToolCall.tool, ToolCall.status, ToolCall.duration_ms)).all()
    agg: dict[str, dict] = {}
    for name, status, ms in rows:
        a = agg.setdefault(name, {"calls": 0, "errors": 0, "duration": 0})
        a["calls"] += 1
        a["errors"] += status != "ok"
        a["duration"] += ms or 0
    out = []
    for spec in registry.all().values():
        a = agg.get(spec.name, {"calls": 0, "errors": 0, "duration": 0})
        out.append({"name": spec.name, "title": spec.title, "permission": spec.permission, "zone": spec.zone, "timeout_s": spec.timeout_s, "retries": spec.retries, "idempotent": spec.idempotent, "alternatives": spec.alternatives, "platform": spec.platform, "offline": spec.name in disabled, "calls": a["calls"], "error_rate": round(a["errors"] / a["calls"], 3) if a["calls"] else 0, "avg_ms": round(a["duration"] / a["calls"]) if a["calls"] else 0})
    return out


class ToolToggle(BaseModel):
    name: str
    offline: bool
    reason: str


@router.post("/tools/offline")
def toggle_tool(body: ToolToggle, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    if not registry.has(body.name) or registry.get(body.name).platform:
        raise ApiError(422, "输入契约不满足", "只能下线已注册的业务工具")
    conf = config_center.get("agents.tools")
    names = set(conf.get("disabled_tools") or [])
    names.add(body.name) if body.offline else names.discard(body.name)
    out = _value(config_center.update, "agents.tools", {"disabled_tools": sorted(names)}, user=user["display_name"], reason=body.reason)
    audit(user, "config", "调整工具上下线", "tool", body.name, {"offline": body.offline})
    return out


@router.get("/eval-sets")
def eval_sets(agent_id: str | None = None, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> list[dict]:
    return evals.list_sets(agent_id)


@router.get("/eval-sets/{sid}")
def eval_set(sid: int, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> dict:
    return _value(evals.get_set, sid)


class BuildSetBody(BaseModel):
    agent_id: str
    purpose: str = "main"
    name: str
    limit: int = 12
    description: str = ""


@router.post("/eval-sets/from-runs")
def build_eval_set(body: BuildSetBody, user: dict = Depends(require_perm("agent.config.edit"))) -> list[dict]:
    return _value(evals.build_from_runs, body.agent_id, body.purpose, body.name, body.limit, user["id"], body.description)


class SampleBody(BaseModel):
    sample: dict


@router.post("/eval-sets/{sid}/samples")
def upsert_sample(sid: int, body: SampleBody, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    return _value(evals.upsert_sample, sid, body.sample)


@router.delete("/eval-sets/{sid}/samples/{sample_id}")
def delete_sample(sid: int, sample_id: str, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    return _value(evals.delete_sample, sid, sample_id)


@router.get("/eval-jobs")
def eval_jobs(agent_id: str | None = None, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> list[dict]:
    return evals.list_jobs(agent_id)


@router.get("/eval-jobs/{jid}")
def eval_job(jid: int, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> dict:
    return _value(evals.get_job, jid)


class JobBody(BaseModel):
    eval_set_id: int
    label: str = ""
    config_version_id: int | None = None
    candidate_config: dict | None = None
    baseline_job_id: int | None = None


@router.post("/eval-jobs")
def start_eval_job(body: JobBody, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    out = _value(evals.create_job, body.eval_set_id, label=body.label, config_version_id=body.config_version_id, candidate_config=body.candidate_config, baseline_job_id=body.baseline_job_id, user_id=user["id"])
    audit(user, "operation", "发起评测", "eval_job", out["id"])
    return out


@router.get("/evals/online")
def online_eval(agent_id: str | None = None, days: int = 30, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> dict:
    return evals.online(agent_id, days)


@router.get("/evolution")
def evolution(agent_id: str | None = None, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> list[dict]:
    return evals.list_proposals(agent_id)


@router.get("/evolution/{pid}")
def evolution_one(pid: int, user: dict = Depends(require_perm("agent.config.edit", "agent.release"))) -> dict:
    d = _value(evals.get_proposal, pid)
    if d["status"] == "canary":
        d["canary_metrics"] = evals.canary_metrics(pid)
    return d


@router.post("/evolution/analyze")
def evolution_analyze(agent_id: str | None = None, user: dict = Depends(require_perm("agent.config.edit"))) -> list[dict]:
    return evals.analyze(agent_id)


@router.post("/evolution/{pid}/evaluate")
def evolution_eval(pid: int, eval_set_id: int | None = None, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    return _value(evals.evaluate_proposal, pid, eval_set_id, user)


@router.post("/evolution/{pid}/submit")
def evolution_submit(pid: int, user: dict = Depends(require_perm("agent.config.edit"))) -> dict:
    return _value(evals.submit_proposal, pid, user)


@router.post("/evolution/{pid}/release")
def evolution_release(pid: int, user: dict = Depends(require_perm("agent.release"))) -> dict:
    out = _value(evals.release_proposal, pid, user)
    audit(user, "config", "全量发布自进化提案", "evolution", pid)
    return out


@router.post("/evolution/{pid}/rollback")
def evolution_rollback(pid: int, body: RevokeBody, user: dict = Depends(require_perm("agent.release"))) -> dict:
    out = _value(evals.rollback_proposal, pid, user, body.reason)
    audit(user, "config", "回滚自进化提案", "evolution", pid, {"reason": body.reason})
    return out


# ================================================================ 看板与报表
@router.get("/dashboards/metrics")
def metric_dict(user: dict = Depends(current_user)) -> dict:
    return {"metrics": [{"key": k, **v} for k, v in dashboards.METRICS.items()], "dimensions": dashboards.DIMENSIONS}


@router.get("/dashboards/business")
def dash_business(days: int = 365, product_id: int | None = None, user: dict = Depends(current_user)) -> dict:
    return dashboards.business(days, [product_id] if product_id else None)


@router.get("/dashboards/risk")
def dash_risk(days: int = 180, user: dict = Depends(current_user)) -> dict:
    return dashboards.risk(days)


@router.get("/dashboards/agents")
def dash_agents(days: int = 30, agent_id: str | None = None, user: dict = Depends(current_user)) -> dict:
    return dashboards.agents(days, agent_id)


@router.get("/dashboards/compute")
def dash_compute(days: int = 7, user: dict = Depends(require_perm("admin.models", "admin.settings"))) -> dict:
    return dashboards.compute(days)


class ReportBody(BaseModel):
    name: str
    metrics: list[str]
    dimension: str = "month"
    period_days: int = 180
    chart: str = "bar"
    schedule: str = "none"
    subscribers: list[int] = []


@router.get("/reports")
def reports(user: dict = Depends(require_perm("dashboard.custom", "admin.settings"))) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ReportDefinition).where(or_(ReportDefinition.owner_id == user["id"], user["role"] == "admin")).order_by(ReportDefinition.id.desc())).all()
        return [dashboards.report_dict(r) for r in rows]


@router.post("/reports")
def create_report(body: ReportBody, user: dict = Depends(require_perm("dashboard.custom"))) -> dict:
    _value(dashboards.run_report, body.metrics, body.dimension, body.period_days)
    with session_scope() as s:
        r = ReportDefinition(name=body.name, owner_id=user["id"], metrics=body.metrics, dimension=body.dimension, period_days=body.period_days, chart=body.chart, schedule=body.schedule, subscribers=body.subscribers or [user["id"]])
        s.add(r)
        s.flush()
        return dashboards.report_dict(r)


@router.get("/reports/{rid}/run")
def run_saved_report(rid: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        r = s.get(ReportDefinition, rid)
        if r is None:
            raise ApiError(404, "状态冲突", "报表不存在")
        spec = dashboards.report_dict(r)
    return {"report": spec, "data": _value(dashboards.run_report, spec["metrics"], spec["dimension"], spec["period_days"])}


@router.post("/reports/preview")
def preview_report(body: ReportBody, user: dict = Depends(require_perm("dashboard.custom"))) -> dict:
    return _value(dashboards.run_report, body.metrics, body.dimension, body.period_days)


@router.delete("/reports/{rid}")
def delete_report(rid: int, user: dict = Depends(require_perm("dashboard.custom"))) -> dict:
    with session_scope() as s:
        r = s.get(ReportDefinition, rid)
        if r is None or (r.owner_id != user["id"] and user["role"] != "admin"):
            raise ApiError(404, "状态冲突", "报表不存在")
        s.delete(r)
    return {"ok": True}


# ================================================================ 证据、差异、协作面板、欺诈线索、回访
@router.get("/evidence")
def evidence(ref: str, user: dict = Depends(current_user)) -> dict:
    """按证据引用回到原文：材料、知识文档或产物字段，并按隐私权限给出脱敏视图。"""
    from ..privacy.gateway import EgressReport, privacy_gateway
    from ..privacy.vault import TokenVault

    level, title, text, where = 0, ref, "", ""
    with session_scope() as s:
        if ref.startswith("material:"):
            m = s.get(Material, int(ref.split(":")[1]))
            if m is None:
                raise ApiError(404, "状态冲突", "材料不存在")
            level, title, text, where = m.data_level, m.name, m.text or "", m.material_type
        elif ref.startswith("kb:"):
            d = s.get(KnowledgeDoc, int(ref.split(":")[1]))
            if d is None:
                raise ApiError(404, "状态冲突", "知识文档不存在")
            level, title, text, where = d.data_level, d.title, d.text, d.kb
        elif ref.startswith("artifact:"):
            key = ref.split(":", 1)[1]
            a = s.scalars(select(Artifact).where(Artifact.artifact_key == key).order_by(Artifact.version.desc())).first()
            if a is None:
                raise ApiError(404, "状态冲突", "产物不存在")
            import json

            text, title, where = json.dumps(a.data, ensure_ascii=False)[:4000], a.title, f"{a.kind} V{a.version}"
            level = 1
        else:
            raise ApiError(422, "输入契约不满足", "无法识别的证据引用")
    masked = text
    view = "原文"
    if level >= 3 and not has_perm(user, "privacy.L3.view"):
        masked, view = "（L3 高敏信息，仅经办岗位可查看原文）", "隐藏"
    elif level >= 2 and not has_perm(user, "privacy.L2.view"):
        masked, view = privacy_gateway.desensitize_text(text, TokenVault("evidence"), EgressReport()), "脱敏"
    if view == "原文" and level >= 2:
        audit(user, "data_access", "查看证据原文", "evidence", ref, {"privacy": "L2原文" if level == 2 else "L3原文"})
    return {"ref": ref, "title": title, "where": where, "level": level, "view": view, "text": masked[:6000]}


@router.get("/artifacts/{key}/diff")
def artifact_diff(key: str, a: int, b: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        va = s.scalars(select(Artifact).where(Artifact.artifact_key == key, Artifact.version == a)).first()
        vb = s.scalars(select(Artifact).where(Artifact.artifact_key == key, Artifact.version == b)).first()
        if va is None or vb is None:
            raise ApiError(404, "状态冲突", "版本不存在")
        da, db = va.data or {}, vb.data or {}
        revisions = [r for r in (vb.revisions or []) if True]
    import difflib

    changes = []
    docs_a = {d.get("doc_id"): d for d in da.get("docs", [])} if "docs" in da else {}
    docs_b = {d.get("doc_id"): d for d in db.get("docs", [])} if "docs" in db else {}
    if docs_a or docs_b:
        for did in sorted(set(docs_a) | set(docs_b), key=str):
            ta = "\n".join(x.get("text", "") for c in (docs_a.get(did) or {}).get("chapters", []) for x in c.get("articles", []))
            tb = "\n".join(x.get("text", "") for c in (docs_b.get(did) or {}).get("chapters", []) for x in c.get("articles", []))
            if ta != tb:
                changes.append({"doc": (docs_b.get(did) or docs_a.get(did) or {}).get("name", did), "diff": [{"op": t, "a": ta[i1:i2], "b": tb[j1:j2]} for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb).get_opcodes() if t != "equal"][:80]})
    return {"a": a, "b": b, "kind": vb.kind, "changes": changes, "revisions": revisions[-40:]}


def _copilot_object(ctx: dict) -> dict:
    kind, oid = ctx.get("kind"), ctx.get("id")
    facts, reasons, risks, steps, status, title = {}, [], [], [], "", ""
    with session_scope() as s:
        if kind == "policy":
            p = s.get(Policy, int(oid))
            e = s.get(Enterprise, p.enterprise_id) if p else None
            title, status = (p.policy_no if p else ""), (p.status if p else "")
            facts = {"保费": f"{p.premium:.0f} 元", "保额": f"{p.sum_insured:.0f} 元"} if p else {}
            steps = ["查看监测方案", "发起风险管理报告", "办理批改或续保"]
        elif kind == "claim":
            c = s.get(ClaimCase, int(oid))
            title, status = (c.claim_no if c else ""), (c.status if c else "")
            facts = {"申报": c.claimed_amount, "建议赔付": c.suggested_amount, "审批金额": c.approved_amount} if c else {}
            steps = ["补齐材料后启动定损", "复核理算并审批", "结案回访"]
        elif kind == "alert":
            a = s.get(Alert, int(oid))
            title, status = (a.alert_no if a else ""), (a.status if a else "")
            reasons = [a.title] if a else []
            risks = [(a.analysis or {}).get("explanation", "")[:160]] if a else []
            steps = ["核实触发依据", "标记有效或误报", "必要时启动事故响应"]
        elif kind == "application":
            a = s.get(Application, int(oid))
            title, status = (a.app_no if a else ""), (a.status if a else "")
            facts = {"保额": a.sum_insured, "建议": (a.decision or {}).get("label")} if a else {}
            steps = ["上传材料", "发起核保", "作出核保决定"]
        elif kind == "run":
            r = s.get(Run, int(oid))
            title, status = (r.title if r else ""), (r.status if r else "")
            facts = {"得分": r.eval_score, "降级": r.degrade_level} if r else {}
            reasons = [((r.output or {}).get("summary") or "")[:200]] if r else []
    return {"kind": kind, "kind_label": {"policy": "保单", "claim": "理赔案件", "alert": "预警", "application": "投保单", "run": "运行"}.get(kind, "页面"), "title": title, "status": status, "facts": {k: v for k, v in facts.items() if v is not None}, "reasons": [r for r in reasons if r], "risks": [r for r in risks if r], "next_steps": steps}


COPILOT_ACTIONS = {
    "policy": [{"key": "risk_report", "label": "生成风险管理报告", "keywords": ["报告", "风险"], "suggest": True, "reason": "为该保单发起一期风险管理报告"}],
    "claim": [{"key": "start_claim", "label": "启动定损", "keywords": ["定损", "启动", "开始"], "suggest": True, "reason": "材料齐备后启动定损理赔"}],
    "application": [{"key": "underwrite", "label": "发起核保", "keywords": ["核保"], "suggest": True, "reason": "对该投保单发起核保"}],
    "alert": [{"key": "false_positive", "label": "标记误报", "keywords": ["误报"], "reason": "将预警标记为误报并回流检测方法"}],
}


class CopilotBody(BaseModel):
    context: dict = {}
    question: str


@router.get("/copilot/sessions")
def copilot_sessions(user: dict = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(CopilotSession).where(CopilotSession.user_id == user["id"]).order_by(CopilotSession.updated_at.desc()).limit(20)).all()
        return [{"id": c.id, "title": c.title, "context": c.context, "updated_at": iso(c.updated_at), "messages": len(c.messages or [])} for c in rows]


@router.post("/copilot/sessions")
def copilot_ask(body: CopilotBody, session_id: int | None = None, user: dict = Depends(current_user)) -> dict:
    from ..knowledge.index import knowledge_index
    from ..llm.gateway import ModelUnavailable, model_gateway

    obj = _copilot_object(body.context) if body.context.get("kind") else {"kind_label": "当前页面", "title": "", "facts": {}, "reasons": [], "risks": [], "next_steps": []}
    hits = knowledge_index.search(body.question, max_level=1, top_k=4)
    passages = [{"n": i + 1, "doc_id": h["doc_id"], "title": h["title"], "text": h["text"]} for i, h in enumerate(hits)]
    actions = COPILOT_ACTIONS.get(body.context.get("kind"), [])
    try:
        out = model_gateway.invoke("copilot.answer", {"question": body.question, "object": obj, "passages": passages, "actions_available": actions}, data_level=1)
    except ModelUnavailable as exc:
        raise ApiError(503, "外部依赖不可用", str(exc)) from exc
    msg_user = {"role": "user", "content": body.question, "at": iso(utcnow())}
    msg_ai = {"role": "assistant", "content": out["answer"], "citations": [{"n": passages[c - 1]["n"], "doc_id": passages[c - 1]["doc_id"], "title": passages[c - 1]["title"]} for c in out.get("citations", []) if 0 < c <= len(passages)], "actions": [{**a, "label": next((x["label"] for x in actions if x["key"] == a["key"]), a["key"])} for a in out.get("actions", [])], "at": iso(utcnow())}
    with session_scope() as s:
        sess = s.get(CopilotSession, session_id) if session_id else None
        if sess is None or sess.user_id != user["id"]:
            sess = CopilotSession(user_id=user["id"], title=body.question[:40], context=body.context, messages=[])
            s.add(sess)
        sess.messages = list(sess.messages or []) + [msg_user, msg_ai]
        sess.context = body.context
        sess.updated_at = utcnow()
        s.flush()
        sid = sess.id
    return {"session_id": sid, "message": msg_ai}


class CopilotAct(BaseModel):
    key: str
    context: dict


@router.post("/copilot/actions")
def copilot_action(body: CopilotAct, user: dict = Depends(current_user)) -> dict:
    kind, oid = body.context.get("kind"), body.context.get("id")
    if body.key == "risk_report" and kind == "policy":
        from ..harness.executor import executor

        rid = executor.create_run(agent_id="AG-RW", inputs={"policy_id": int(oid), "period": "month"}, business_ref={"policy_id": int(oid)}, mode="auto", user_id=user["id"], title="风险管理报告（协作面板）")
        return {"run_id": rid, "href": f"/runs/{rid}"}
    if body.key == "start_claim" and kind == "claim":
        from ..services.business import start_claim_run

        rid = _value(start_claim_run, int(oid), user["id"])
        return {"run_id": rid, "href": f"/runs/{rid}"}
    if body.key == "underwrite" and kind == "application":
        from ..services.business import start_underwriting

        rid = _value(start_underwriting, int(oid), user["id"])
        return {"run_id": rid, "href": f"/runs/{rid}"}
    if body.key == "false_positive" and kind == "alert":
        from ..services.monitoring import record_feedback

        with session_scope() as s:
            a = s.get(Alert, int(oid))
            if a is None:
                raise ApiError(404, "状态冲突", "预警不存在")
            a.status = "false_positive"
        record_feedback(int(oid), "false_positive", "协作面板标记误报", user["id"])
        return {"ok": True}
    raise ApiError(422, "输入契约不满足", "该动作在当前页面不可用")


@router.get("/applications/{aid}/fraud")
def fraud_clues(aid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        app = s.get(Application, aid)
        if app is None:
            raise ApiError(404, "状态冲突", "投保单不存在")
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == app.report_artifact_key).order_by(Artifact.version.desc())).first() if app.report_artifact_key else None
        data = (art.data or {}) if art else {}
        from ..models import HistoricalApplication

        ent = s.get(Enterprise, app.enterprise_id)
        similar = s.scalars(select(HistoricalApplication).where(HistoricalApplication.fraud_label == 1).limit(200)).all()
        patterns = {p.get("code") or p.get("pattern") for p in (data.get("fraud") or {}).get("patterns", [])}
        cases = [{"enterprise": h.enterprise_name, "line": h.line, "year": h.year, "pattern": h.fraud_pattern, "statement": (h.statement or "")[:160], "intermediary": h.intermediary} for h in similar if h.fraud_pattern in patterns][:8]
    return {"application_id": aid, "enterprise": ent.name if ent else None, "fraud": data.get("fraud"), "scorecard": data.get("scorecard"), "similar_cases": cases, "graph": (data.get("fraud") or {}).get("graph")}


class FollowBody(BaseModel):
    channel: str = "电话"
    result: str
    satisfied: bool | None = None
    note: str = ""


@router.get("/claims/{cid}/followups")
def followups(cid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        c = s.get(ClaimCase, cid)
        if c is None:
            raise ApiError(404, "状态冲突", "案件不存在")
        return {"status": c.status, "closed_at": iso(c.closed_at), "followups": c.followups or [], "reflow": c.status == "closed"}


@router.post("/claims/{cid}/followups")
def add_followup(cid: int, body: FollowBody, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        c = s.get(ClaimCase, cid)
        if c is None or c.status != "closed":
            raise ApiError(409, "状态冲突", "结案后才能登记回访")
        row = {"at": iso(utcnow()), "by": user["display_name"], "channel": body.channel, "result": body.result, "satisfied": body.satisfied, "note": body.note}
        c.followups = list(c.followups or []) + [row]
    audit(user, "operation", "登记理赔回访", "claim", cid)
    return row


@router.get("/monitor-plans/{plan_id}")
def monitor_plan(plan_id: int, user: dict = Depends(require("service"))) -> dict:
    from ..models import MonitorPlan

    with session_scope() as s:
        p = s.get(MonitorPlan, plan_id)
        if p is None:
            raise ApiError(404, "状态冲突", "监测方案不存在")
        return {"id": p.id, "policy_id": p.policy_id, "status": p.status, "indicators": p.indicators, "silence_hours": (p.state or {}).get("silence_hours"), "maintenance_windows": (p.state or {}).get("maintenance_windows", []), "threshold_changes": (p.state or {}).get("threshold_changes", []), "fpr": (p.state or {}).get("fpr", {}), "hour": (p.state or {}).get("hour")}


class IndicatorPatch(BaseModel):
    indicators: list[dict]
    reason: str


@router.put("/monitor-plans/{plan_id}/indicators")
def save_indicators(plan_id: int, body: IndicatorPatch, user: dict = Depends(require_perm("monitor.plan.edit"))) -> dict:
    from ..models import MonitorPlan

    if not body.reason.strip():
        raise ApiError(422, "输入契约不满足", "请填写调整原因")
    with session_scope() as s:
        p = s.get(MonitorPlan, plan_id)
        if p is None:
            raise ApiError(404, "状态冲突", "监测方案不存在")
        before = {i.get("key"): i.get("threshold") for i in p.indicators or []}
        p.indicators = body.indicators
        state = dict(p.state or {})
        state["threshold_changes"] = list(state.get("threshold_changes", [])) + [{"at": iso(utcnow()), "by": user["id"], "reason": body.reason, "before": before}]
        p.state = state
    audit(user, "config", "调整监测方案", "monitor_plan", plan_id, {"reason": body.reason})
    return {"ok": True}


_ = (LINES, ApprovalRequest, Incident, EvalJob, AuditLog, or_)
