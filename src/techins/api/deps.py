"""认证、角色与权限点、数据范围、审计与统一错误结构。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import settings
from ..db import session_scope
from ..models import AuditLog, User

ROLE_LABELS = {"designer": "产品设计人员", "actuary": "精算人员", "service": "产品服务人员", "senior": "高级核保人员", "admin": "管理人员", "bizadmin": "业务管理人员"}
bearer = HTTPBearer(auto_error=False)


class ApiError(HTTPException):
    def __init__(self, status: int, category: str, message: str, suggestion: str = "", obj: Any = None) -> None:
        super().__init__(status_code=status, detail={"category": category, "message": message, "suggestion": suggestion, "object": obj})


def issue_token(user: User) -> str:
    from ..services.config_center import cfg

    ttl = float(cfg("security").get("session_ttl_hours") or settings.jwt_ttl_hours)
    payload = {"sub": str(user.id), "role": user.role, "exp": datetime.now(timezone.utc) + timedelta(hours=ttl)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def user_dict(u: User, *, with_permissions: bool = False, s=None) -> dict:
    d = {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "role_label": ROLE_LABELS.get(u.role, u.role), "positions": u.positions, "org": u.org, "org_id": u.org_id, "approval_level": u.approval_level, "data_scope": u.data_scope, "email": u.email}
    if with_permissions:
        from ..services.iam import active_grants, effective_permissions, widest_scope

        grants = active_grants(u.id, s)
        perms, granted = effective_permissions(u.role, u.positions, grants)
        scope = u.data_scope
        for g in grants:
            scope = widest_scope(scope, g.data_scope)
        d.update({"permissions": perms, "grants": granted, "effective_scope": scope})
    return d


def _check_ip(ip: str | None) -> None:
    from ..services.config_center import cfg

    sec = cfg("security")
    if not sec.get("ip_whitelist_enabled"):
        return
    prefixes = sec.get("ip_whitelist") or []
    if not ip or not any(ip.startswith(p) for p in prefixes):
        raise ApiError(403, "权限不足", "当前网络地址不在登录白名单内", "联系系统管理员")


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer), token: str | None = Query(default=None)) -> dict:
    raw = creds.credentials if creds else token
    if not raw:
        raise ApiError(401, "权限不足", "请先登录", "使用演示账号登录")
    try:
        payload = jwt.decode(raw, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ApiError(401, "权限不足", "登录已失效，请重新登录") from exc
    with session_scope() as s:
        u = s.get(User, int(payload["sub"]))
        if u is None or not u.enabled:
            raise ApiError(401, "权限不足", "账号不可用")
        info = user_dict(u, with_permissions=True, s=s)
    info["client_ip"] = request.client.host if request.client else None
    _check_ip(info["client_ip"])
    return info


def require(*roles: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin" and user["role"] not in roles:
            raise ApiError(403, "权限不足", f"该功能需要{'或'.join(ROLE_LABELS.get(r, r) for r in roles)}角色", "联系管理员申请权限")
        return user

    return dep


def require_perm(*codes: str):
    """权限点校验：满足任一权限点即可（含临时授权）。"""

    def dep(user: dict = Depends(current_user)) -> dict:
        have = set(user.get("permissions") or [])
        if not have & set(codes):
            from ..services.iam import PERM_INDEX

            names = "或".join(PERM_INDEX.get(c, {}).get("name", c) for c in codes)
            raise ApiError(403, "权限不足", f"该操作需要权限：{names}", "在个人中心申请临时授权，或联系管理员调整岗位权限包")
        return user

    return dep


def has_perm(user: dict, code: str) -> bool:
    return code in (user.get("permissions") or [])


def audit(user: dict | None, category: str, action: str, object_type: str | None = None, object_id: Any = None, detail: dict | None = None) -> None:
    detail = dict(detail or {})
    if user and user.get("grants"):
        detail["temp_grants"] = [g["id"] for g in user["grants"]]
    if user and user.get("client_ip"):
        detail.setdefault("ip", user["client_ip"])
    with session_scope() as s:
        s.add(AuditLog(user_id=user["id"] if user else None, username=user["username"] if user else None, category=category, action=action, object_type=object_type, object_id=str(object_id) if object_id is not None else None, detail=detail))


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None
