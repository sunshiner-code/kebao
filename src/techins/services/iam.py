"""用户、组织与权限：权限点模型、岗位权限包与职责分离检测、数据范围、临时授权、账号生命周期。"""

from __future__ import annotations

import re
import threading
from datetime import timedelta

from sqlalchemy import or_, select

from ..db import session_scope, utcnow
from ..models import AccessGrant, Application, ClaimCase, Enterprise, HitlCard, Notification, Organization, PermissionPack, Policy, Run, User
from .config_center import cfg

# ---------------------------------------------------------------- 权限点
# kind：menu 菜单 / op 操作 / agent 智能体 / tool 工具级别 / model 模型 / privacy 隐私级别 / admin 管理
PERMISSIONS: list[dict] = [
    {"code": "menu.design", "name": "产品设计菜单", "kind": "menu", "roles": ["designer"]},
    {"code": "menu.service", "name": "产品服务菜单", "kind": "menu", "roles": ["service"]},
    {"code": "menu.dashboards", "name": "数据看板菜单", "kind": "menu", "roles": ["designer", "service"]},
    {"code": "menu.admin", "name": "系统管理菜单", "kind": "menu", "roles": []},
    {"code": "design.project.create", "name": "新建设计项目", "kind": "op", "roles": ["designer"]},
    {"code": "design.artifact.freeze", "name": "冻结条款、费率与报告版本", "kind": "op", "roles": ["designer"]},
    {"code": "design.clause.edit", "name": "编辑条款与局部再生成", "kind": "op", "roles": ["designer"]},
    {"code": "product.submit", "name": "提交产品包", "kind": "op", "roles": ["designer"]},
    {"code": "product.review1", "name": "产品一审", "kind": "op", "roles": ["designer"]},
    {"code": "product.review2", "name": "产品二审", "kind": "op", "roles": ["designer"]},
    {"code": "product.publish", "name": "产品终审发布与停售", "kind": "op", "roles": []},
    {"code": "product.iterate", "name": "发起产品迭代", "kind": "op", "roles": ["designer"]},
    {"code": "uw.decide", "name": "作出核保决定", "kind": "op", "roles": ["service"]},
    {"code": "uw.rules.edit", "name": "维护核保规则", "kind": "op", "roles": []},
    {"code": "alert.handle", "name": "处置预警", "kind": "op", "roles": ["service"]},
    {"code": "alert.false_positive", "name": "标记误报", "kind": "op", "roles": ["service"]},
    {"code": "monitor.plan.edit", "name": "调整监测方案", "kind": "op", "roles": ["service"]},
    {"code": "rw.templates.edit", "name": "维护预警模板", "kind": "op", "roles": []},
    {"code": "incident.start", "name": "启动事故响应", "kind": "op", "roles": ["service"]},
    {"code": "claim.register", "name": "报案登记", "kind": "op", "roles": ["service"]},
    {"code": "claim.approve", "name": "赔付审批", "kind": "op", "roles": ["service"]},
    {"code": "claim.pay", "name": "支付指令与结案", "kind": "op", "roles": ["service"]},
    {"code": "policy.endorse", "name": "发起批改", "kind": "op", "roles": ["service"]},
    {"code": "policy.endorse.approve", "name": "批改复核", "kind": "op", "roles": ["service"]},
    {"code": "policy.renew", "name": "续保办理", "kind": "op", "roles": ["service"]},
    {"code": "customer.dd.upload", "name": "上传尽调资料", "kind": "op", "roles": ["service", "designer"]},
    {"code": "customer.auth.manage", "name": "管理数据接入授权", "kind": "op", "roles": ["service"]},
    {"code": "customer.contact", "name": "维护联系与拜访记录", "kind": "op", "roles": ["service"]},
    {"code": "kb.edit", "name": "编辑知识库", "kind": "op", "roles": []},
    {"code": "kb.ingest", "name": "文档入库与质检", "kind": "op", "roles": []},
    {"code": "kb.proposal.submit", "name": "提交知识变更提案", "kind": "op", "roles": ["designer", "service"]},
    {"code": "kb.proposal.approve", "name": "审批知识变更提案", "kind": "op", "roles": []},
    {"code": "agent.run.start", "name": "发起智能体运行", "kind": "agent", "roles": ["designer", "service"]},
    {"code": "agent.mode.semi", "name": "使用半自动模式", "kind": "agent", "roles": ["designer", "service"]},
    {"code": "agent.run.force_stop", "name": "强制终止运行", "kind": "agent", "roles": []},
    {"code": "agent.config.edit", "name": "编辑智能体配置与发起评测", "kind": "agent", "roles": []},
    {"code": "agent.release", "name": "确认自进化发布与回滚", "kind": "agent", "roles": []},
    {"code": "tool.confirm.T2", "name": "确认写正式数据类工具（T2）", "kind": "tool", "roles": ["designer", "service"]},
    {"code": "tool.confirm.T3", "name": "确认对外发送类工具（T3）", "kind": "tool", "roles": ["service"]},
    {"code": "model.switch.cloud", "name": "专家模式切换云端模型", "kind": "model", "roles": ["designer", "service"]},
    {"code": "privacy.L2.view", "name": "查看 L2 原文", "kind": "privacy", "roles": ["service"]},
    {"code": "privacy.L3.view", "name": "查看 L3 经办信息", "kind": "privacy", "roles": ["service"]},
    {"code": "data.export", "name": "导出数据", "kind": "op", "roles": ["designer", "service"]},
    {"code": "dashboard.custom", "name": "配置自定义报表", "kind": "op", "roles": []},
    {"code": "admin.users", "name": "用户、组织与授权管理", "kind": "admin", "roles": []},
    {"code": "admin.roles", "name": "角色与岗位权限包", "kind": "admin", "roles": []},
    {"code": "admin.settings", "name": "系统配置", "kind": "admin", "roles": []},
    {"code": "admin.models", "name": "模型注册与路由策略", "kind": "admin", "roles": []},
    {"code": "admin.privacy", "name": "隐私与合规规则", "kind": "admin", "roles": []},
    {"code": "admin.audit", "name": "查询审计日志", "kind": "admin", "roles": []},
    {"code": "admin.audit.export", "name": "导出审计日志与审批导出", "kind": "admin", "roles": []},
    {"code": "admin.integration", "name": "数据源与集成", "kind": "admin", "roles": []},
]
PERM_INDEX = {p["code"]: p for p in PERMISSIONS}
ALL_CODES = [p["code"] for p in PERMISSIONS]
KIND_LABELS = {"menu": "菜单权限", "op": "操作权限", "agent": "智能体权限", "tool": "工具权限级别", "model": "模型权限", "privacy": "隐私级别", "admin": "管理权限"}

# 职责分离：同一账号（含临时授权）不可同时持有
SOD_CONFLICTS: list[tuple[str, str, str]] = [
    ("product.submit", "product.publish", "提交产品包与终审发布产品不能为同一人"),
    ("agent.config.edit", "agent.release", "编辑智能体配置与确认自进化发布不能为同一人"),
    ("kb.edit", "kb.proposal.approve", "编辑知识库与审批知识变更不能为同一人"),
    ("uw.rules.edit", "uw.decide", "维护核保规则与作出核保决定不能为同一人"),
    ("admin.users", "admin.audit.export", "账号管理与审计导出不能为同一人"),
]

DEFAULT_PACKS: list[tuple[str, str, list[str], str]] = [
    ("产品经理", "designer", ["menu.design", "menu.dashboards", "design.project.create", "design.artifact.freeze", "design.clause.edit", "product.submit", "product.iterate", "agent.run.start", "agent.mode.semi", "tool.confirm.T2", "model.switch.cloud", "kb.proposal.submit", "customer.dd.upload", "data.export"], "负责设计项目全流程、提交产品包"),
    ("条款岗", "designer", ["menu.design", "design.clause.edit", "design.artifact.freeze", "agent.run.start", "kb.proposal.submit"], "条款编辑、合规整改与条款版本冻结"),
    ("精算岗", "designer", ["menu.design", "menu.dashboards", "design.artifact.freeze", "product.review1", "agent.run.start", "agent.mode.semi", "tool.confirm.T2", "kb.proposal.submit"], "定价任务、因子确认与产品一审"),
    ("产品负责人", "designer", ["menu.design", "menu.dashboards", "product.review1", "product.review2", "product.iterate", "data.export"], "产品二审与迭代决策"),
    ("核保岗", "service", ["menu.service", "menu.dashboards", "uw.decide", "agent.run.start", "agent.mode.semi", "tool.confirm.T2", "privacy.L2.view", "customer.dd.upload", "policy.renew", "kb.proposal.submit"], "核保决定、续保评估"),
    ("预警岗", "service", ["menu.service", "alert.handle", "alert.false_positive", "monitor.plan.edit", "incident.start", "agent.run.start", "tool.confirm.T2", "tool.confirm.T3"], "预警处置、监测方案调整、事故响应"),
    ("理赔岗", "service", ["menu.service", "menu.dashboards", "claim.register", "claim.approve", "claim.pay", "agent.run.start", "tool.confirm.T2", "tool.confirm.T3", "privacy.L2.view", "privacy.L3.view"], "报案、定损理赔、赔付审批与结案"),
    ("客户经理", "service", ["menu.service", "customer.dd.upload", "customer.auth.manage", "customer.contact", "policy.endorse", "policy.endorse.approve", "policy.renew", "privacy.L2.view", "data.export"], "客户维护、数据授权、批改与续保"),
    ("系统管理员", "admin", ["menu.admin", "menu.dashboards", "admin.users", "admin.roles", "admin.settings", "admin.models", "admin.privacy", "admin.integration", "admin.audit", "agent.config.edit", "agent.run.force_stop", "kb.edit", "kb.ingest", "dashboard.custom"], "账号权限、模型路由、系统配置、智能体配置与知识入库"),
    ("业务管理员", "admin", ["menu.admin", "menu.dashboards", "product.publish", "uw.rules.edit", "rw.templates.edit", "kb.proposal.approve", "agent.release", "admin.audit", "admin.settings", "dashboard.custom", "data.export"], "产品终审发布、业务规则、知识变更审批与自进化发布确认"),
    ("安全审计员", "admin", ["menu.admin", "admin.audit", "admin.audit.export", "privacy.L2.view"], "审计查询、导出审批与异常行为告警处理"),
    ("知识管理员", "admin", ["menu.admin", "kb.edit", "kb.ingest"], "知识入库、质检与发布"),
]

ORG_TREE = [
    ("HQ", "科创财产保险股份有限公司（总公司）", "company", None, "all"),
    ("HQ-PD", "产品创新部", "department", "HQ", "department"),
    ("HQ-TECH", "科技保险事业部", "department", "HQ", "department"),
    ("HQ-TECH-UW", "核保理赔中心", "department", "HQ-TECH", "department"),
    ("HQ-IT", "信息技术部", "department", "HQ", "all"),
    ("HQ-RISK", "风险合规部", "department", "HQ", "all"),
    ("SZ", "苏州分公司", "branch", "HQ", "org"),
    ("SZ-SVC", "苏州分公司科技保险服务部", "department", "SZ", "department"),
]
USER_ORG = {"designer": "HQ-PD", "actuary": "HQ-PD", "service": "HQ-TECH-UW", "senior": "HQ-TECH-UW", "admin": "HQ-IT", "bizadmin": "HQ-RISK"}
ORG_LEADER = {"HQ-PD": "actuary", "HQ-TECH-UW": "senior", "HQ-IT": "admin", "HQ-RISK": "bizadmin", "HQ": "bizadmin", "HQ-TECH": "senior"}
DATA_SCOPES = {"self": "本人", "customers": "所辖客户", "department": "本部门", "org": "本机构", "all": "全部"}


class IamError(ValueError):
    pass


# ---------------------------------------------------------------- 权限包缓存


class _PackCache:
    def __init__(self) -> None:
        self._packs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        with session_scope() as s:
            packs = {p.name: {"base_role": p.base_role, "permissions": list(p.permissions or [])} for p in s.scalars(select(PermissionPack)).all()}
        with self._lock:
            self._packs = packs

    def get(self) -> dict[str, dict]:
        with self._lock:
            if self._packs:
                return dict(self._packs)
        from ..db import _local

        if getattr(_local, "depth", 0):
            return {name: {"base_role": role, "permissions": perms} for name, role, perms, _ in DEFAULT_PACKS}
        self.load()
        with self._lock:
            return dict(self._packs)


pack_cache = _PackCache()


def role_ceiling(role: str) -> set[str]:
    if role == "admin":
        return set(ALL_CODES)
    return {p["code"] for p in PERMISSIONS if role in p["roles"]}


def active_grants(user_id: int, s=None) -> list[AccessGrant]:
    def _q(sess):
        now = utcnow()
        rows = sess.scalars(select(AccessGrant).where(AccessGrant.user_id == user_id, AccessGrant.status == "active")).all()
        out = []
        for g in rows:
            if g.expires_at and g.expires_at < now:
                g.status = "expired"
                continue
            out.append(g)
        return out

    if s is not None:
        return _q(s)
    with session_scope() as sess:
        rows = _q(sess)
        for r in rows:
            sess.expunge(r)
        return rows


def effective_permissions(role: str, positions: list[str], grants: list[AccessGrant] | None = None) -> tuple[list[str], list[dict]]:
    packs = pack_cache.get()
    perms: set[str] = set()
    for name in positions or []:
        p = packs.get(name)
        if p:
            perms |= set(p["permissions"])
    granted = []
    for g in grants or []:
        perms |= set(g.permissions or [])
        granted.append({"id": g.id, "permissions": g.permissions, "data_scope": g.data_scope, "expires_at": g.expires_at.isoformat() + "Z" if g.expires_at else None})
    ceiling = role_ceiling(role)
    return sorted(perms & ceiling), granted


def sod_violations(perms: set[str]) -> list[str]:
    return [msg for a, b, msg in SOD_CONFLICTS if a in perms and b in perms]


# ---------------------------------------------------------------- 数据范围


def org_subtree(s, org_id: int | None) -> set[int]:
    if org_id is None:
        return set()
    orgs = s.scalars(select(Organization)).all()
    children: dict[int | None, list[int]] = {}
    for o in orgs:
        children.setdefault(o.parent_id, []).append(o.id)
    out, stack = set(), [org_id]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack += children.get(cur, [])
    return out


def company_of(s, org_id: int | None) -> int | None:
    seen = set()
    cur = s.get(Organization, org_id) if org_id else None
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        if cur.kind in ("company", "branch"):
            return cur.id
        cur = s.get(Organization, cur.parent_id) if cur.parent_id else None
    return None


def enterprise_scope(user: dict) -> set[int] | None:
    """按数据范围返回可见的企业编号集合；None 表示不限。产品设计人员按项目授权看脱敏企业，不在此过滤。"""
    scope = user.get("effective_scope") or user.get("data_scope") or "self"
    if user["role"] != "service" or scope == "all":
        return None
    with session_scope() as s:
        if scope in ("self", "customers"):
            managers = {user["id"]}
        else:
            me = s.get(User, user["id"])
            root = me.org_id if scope == "department" else company_of(s, me.org_id)
            orgs = org_subtree(s, root)
            managers = {u.id for u in s.scalars(select(User).where(User.org_id.in_(orgs))).all()} | {user["id"]}
        return {e.id for e in s.scalars(select(Enterprise).where(Enterprise.manager_id.in_(managers))).all()}


SCOPE_ORDER = ["self", "customers", "department", "org", "all"]


def widest_scope(a: str, b: str | None) -> str:
    if not b:
        return a
    return a if SCOPE_ORDER.index(a) >= SCOPE_ORDER.index(b) else b


# ---------------------------------------------------------------- 初始化


def seed_iam() -> None:
    with session_scope() as s:
        if not s.scalars(select(PermissionPack)).first():
            for name, role, perms, desc in DEFAULT_PACKS:
                s.add(PermissionPack(name=name, base_role=role, permissions=perms, description=desc, builtin=True))
        if not s.scalars(select(Organization)).first():
            ids: dict[str, int] = {}
            for i, (code, name, kind, parent, scope) in enumerate(ORG_TREE):
                o = Organization(code=code, name=name, kind=kind, parent_id=ids.get(parent) if parent else None, data_scope=scope, sort=i)
                s.add(o)
                s.flush()
                ids[code] = o.id
            users = {u.username: u for u in s.scalars(select(User)).all()}
            for uname, code in USER_ORG.items():
                if uname in users:
                    users[uname].org_id = ids[code]
                    users[uname].org = next(n for c, n, *_ in ORG_TREE if c == code)
            for code, uname in ORG_LEADER.items():
                if uname in users:
                    s.get(Organization, ids[code]).leader_id = users[uname].id
    pack_cache.load()


# ---------------------------------------------------------------- 账号生命周期


USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,31}$")


def check_password_policy(password: str) -> None:
    sec = cfg("security")
    if not password:
        raise IamError("请填写密码")
    if len(password) < int(sec["password_min_length"]):
        raise IamError(f"密码长度不少于 {sec['password_min_length']} 位")
    if sec["password_require_mixed"] and not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise IamError("密码须同时包含字母与数字")
    if password != password.strip():
        raise IamError("密码首尾不能包含空格")


def validate_new_account(username: str, display_name: str, password: str, password_confirm: str) -> tuple[str, str]:
    """新建账号的必填、格式、复杂度与确认校验。通过后返回去空白的用户名和姓名。"""
    errors: list[str] = []
    username = (username or "").strip()
    display_name = (display_name or "").strip()
    if not username:
        errors.append("请填写用户名")
    elif not USERNAME_RE.fullmatch(username):
        errors.append("用户名需以字母开头，3–32 位，只能包含字母、数字、点、下划线或短横线，例如 zhangsan")
    if not display_name:
        errors.append("请填写姓名")
    elif len(display_name) > 32:
        errors.append("姓名不超过 32 个字")
    if not password:
        errors.append("请填写初始密码")
    else:
        try:
            check_password_policy(password)
        except IamError as exc:
            errors.append(str(exc))
        if username and password.lower() == username.lower():
            errors.append("密码不能与用户名相同")
    if password != (password_confirm or ""):
        errors.append("两次输入的密码不一致")
    if errors:
        raise IamError("；".join(errors))
    return username, display_name


def validate_positions(role: str, positions: list[str]) -> list[str]:
    packs = pack_cache.get()
    unknown = [p for p in positions if p not in packs]
    if unknown:
        raise IamError("岗位权限包不存在：" + "、".join(unknown))
    over = [p for p in positions if packs[p]["base_role"] != role and role != "admin"]
    if over:
        raise IamError("岗位权限包超出基础角色上限：" + "、".join(over))
    perms = set()
    for p in positions:
        perms |= set(packs[p]["permissions"])
    violations = sod_violations(perms & role_ceiling(role))
    if violations:
        raise IamError("职责分离冲突：" + "；".join(violations))
    return positions


def handover(s, from_user: User, to_user_id: int) -> dict:
    """离职或停用交接：未处理的人审卡片、负责的投保单、理赔案件、客户转交给指定人员。"""
    moved = {"cards": 0, "applications": 0, "claims": 0, "enterprises": 0}
    for c in s.scalars(select(HitlCard).where(HitlCard.status == "open", HitlCard.assignee_id == from_user.id)).all():
        c.assignee_id = to_user_id
        c.history = list(c.history or []) + [{"action": "handover", "from": from_user.id, "to": to_user_id, "at": utcnow().isoformat() + "Z"}]
        moved["cards"] += 1
    for a in s.scalars(select(Application).where(Application.assignee_id == from_user.id, Application.status.notin_(["accepted", "rejected", "withdrawn"]))).all():
        a.assignee_id = to_user_id
        moved["applications"] += 1
    for c in s.scalars(select(ClaimCase).where(ClaimCase.assignee_id == from_user.id, ClaimCase.status != "closed")).all():
        c.assignee_id = to_user_id
        moved["claims"] += 1
    for e in s.scalars(select(Enterprise).where(Enterprise.manager_id == from_user.id)).all():
        e.manager_id = to_user_id
        moved["enterprises"] += 1
    s.add(Notification(user_id=to_user_id, kind="system", title=f"工作交接：来自 {from_user.display_name}", body=f"人审卡片 {moved['cards']} 张、投保单 {moved['applications']} 件、理赔案件 {moved['claims']} 件、客户 {moved['enterprises']} 家已转交给你", link="/inbox"))
    return moved


def running_runs_of(s, user_id: int) -> int:
    return len(s.scalars(select(Run.id).where(Run.created_by == user_id, Run.status.in_(["queued", "planning", "running", "waiting_human"]))).all())


def leader_of(s, user_id: int) -> int | None:
    u = s.get(User, user_id)
    seen = set()
    org = s.get(Organization, u.org_id) if u and u.org_id else None
    while org is not None and org.id not in seen:
        seen.add(org.id)
        if org.leader_id and org.leader_id != user_id:
            return org.leader_id
        org = s.get(Organization, org.parent_id) if org.parent_id else None
    return None


# ---------------------------------------------------------------- 临时授权


def request_grant(user: dict, permissions: list[str], data_scope: str | None, hours: float, reason: str) -> dict:
    from . import approvals

    sec = cfg("security")
    if hours <= 0 or hours > float(sec["temp_grant_max_hours"]):
        raise IamError(f"临时授权时长需在 0–{sec['temp_grant_max_hours']} 小时之间")
    if not reason.strip():
        raise IamError("请填写授权理由")
    unknown = [p for p in permissions if p not in PERM_INDEX]
    if unknown:
        raise IamError("权限点不存在：" + "、".join(unknown))
    ceiling = role_ceiling(user["role"])
    over = [p for p in permissions if p not in ceiling]
    if over:
        raise IamError("临时授权不能超出基础角色上限：" + "、".join(PERM_INDEX[p]["name"] for p in over))
    current = set(user.get("permissions") or [])
    violations = sod_violations(current | set(permissions))
    if violations:
        raise IamError("职责分离冲突：" + "；".join(violations))
    if data_scope and data_scope not in DATA_SCOPES:
        raise IamError("数据范围无效")
    with session_scope() as s:
        g = AccessGrant(user_id=user["id"], permissions=permissions, data_scope=data_scope, reason=reason, hours=hours, status="pending")
        s.add(g)
        s.flush()
        gid = g.id
    names = "、".join(PERM_INDEX[p]["name"] for p in permissions) + (f"；数据范围 {DATA_SCOPES[data_scope]}" if data_scope else "")
    req = approvals.start("temp_grant", object_type="access_grant", object_id=gid, title=f"临时授权：{user['display_name']} 申请 {hours:g} 小时", summary={"申请人": user["display_name"], "权限": names, "时长": f"{hours:g} 小时", "理由": reason}, applicant=user)
    with session_scope() as s:
        s.get(AccessGrant, gid).approval_id = req["id"]
    return {"grant_id": gid, "approval": req}


def _grant_approved(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        g = s.get(AccessGrant, int(req["object_id"]))
        if g is None or g.status != "pending":
            return
        now = utcnow()
        g.status, g.starts_at, g.expires_at = "active", now, now + timedelta(hours=g.hours)
        s.add(Notification(user_id=g.user_id, kind="system", title="临时授权已生效", body=f"有效期至 {(g.expires_at + timedelta(hours=8)):%m-%d %H:%M}（北京时间），期间操作将标记审计", link="/me"))


def _grant_rejected(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        g = s.get(AccessGrant, int(req["object_id"]))
        if g is not None and g.status == "pending":
            g.status = "rejected"


def expire_grants() -> int:
    with session_scope() as s:
        now = utcnow()
        rows = s.scalars(select(AccessGrant).where(AccessGrant.status == "active", AccessGrant.expires_at < now)).all()
        for g in rows:
            g.status = "expired"
            s.add(Notification(user_id=g.user_id, kind="system", title="临时授权已到期回收", body="、".join(PERM_INDEX.get(p, {}).get("name", p) for p in g.permissions or []), link="/me"))
        return len(rows)


def register_handlers() -> None:
    from . import approvals

    approvals.register("temp_grant", _grant_approved, _grant_rejected)


_ = (or_, Policy)
