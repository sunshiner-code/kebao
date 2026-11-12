"""通知分发：按订阅规则确定渠道，免打扰时段延后外部渠道，同类通知在合并窗口内聚合。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import utcnow
from ..models import Notification
from .config_center import cfg

LEVEL_RANK = {"info": 0, "warning": 1, "error": 2}


def _in_quiet_hours(now_local: datetime, start: str, end: str) -> bool:
    cur = now_local.strftime("%H:%M")
    return (start <= cur or cur < end) if start > end else (start <= cur < end)


def channels_for(kind: str, role: str | None, level: str) -> list[str]:
    conf = cfg("notify.config")
    enabled = {k for k, v in conf["channels"].items() if v}
    chans = {"inbox"}
    for sub in conf["subscriptions"]:
        if sub["kind"] != kind:
            continue
        if role and sub.get("roles") and role not in sub["roles"]:
            continue
        if LEVEL_RANK.get(level, 0) < LEVEL_RANK.get(sub.get("min_level", "info"), 0):
            continue
        chans |= set(sub.get("channels") or [])
    chans &= enabled | {"inbox"}
    quiet = conf["quiet_hours"]
    if quiet.get("enabled") and not (quiet.get("allow_error") and level == "error"):
        local = utcnow() + timedelta(hours=8)
        if _in_quiet_hours(local, quiet["start"], quiet["end"]):
            chans = {"inbox"}
    return sorted(chans)


def notify(s, *, kind: str, title: str, body: str = "", link: str | None = None, level: str = "info", user_id: int | None = None, role: str | None = None) -> Notification:
    """在调用方的事务内写通知；合并窗口内同对象、同类型、同标题的未读通知只累加计数。"""
    window = int(cfg("notify.config").get("aggregate_minutes") or 0)
    if window:
        since = utcnow() - timedelta(minutes=window)
        q = select(Notification).where(Notification.kind == kind, Notification.title == title, Notification.read.is_(False), Notification.ts >= since)
        q = q.where(Notification.user_id == user_id) if user_id is not None else q.where(Notification.user_id.is_(None), Notification.role == role)
        existing = s.scalars(q).first()
        if existing is not None:
            existing.count = (existing.count or 1) + 1
            existing.body = body
            existing.ts = utcnow()
            if LEVEL_RANK.get(level, 0) > LEVEL_RANK.get(existing.level, 0):
                existing.level = level
            return existing
    n = Notification(user_id=user_id, role=role, kind=kind, title=title, body=body, link=link, level=level, channels=channels_for(kind, role, level))
    s.add(n)
    return n
