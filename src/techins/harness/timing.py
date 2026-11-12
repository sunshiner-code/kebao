"""运行有效执行时长：仅累计 planning / running，排队与等人审不计。"""

from __future__ import annotations

from datetime import datetime

from ..db import utcnow
from ..models import Run

ACTIVE_STATUSES = frozenset({"planning", "running"})


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip().replace("Z", "")
    if "+" in text[10:]:
        text = text.split("+", 1)[0]
    return datetime.fromisoformat(text)


def transfer_active_time(usage: dict | None, from_status: str | None, to_status: str, *, now: datetime | None = None) -> dict:
    """按状态迁移累计有效执行秒数。planning↔running 连续计时；进出排队/人审时启停。"""
    usage = dict(usage or {})
    now = now or utcnow()
    was = from_status in ACTIVE_STATUSES
    will = to_status in ACTIVE_STATUSES
    if was and not will:
        since = usage.pop("active_since", None)
        if since:
            try:
                usage["active_seconds"] = round(float(usage.get("active_seconds") or 0) + max(0.0, (now - _parse_ts(since)).total_seconds()), 3)
            except Exception:
                pass
    elif (not was) and will:
        usage["active_seconds"] = float(usage.get("active_seconds") or 0)
        usage["active_since"] = now.isoformat()
    return usage


def apply_status(run: Run, new_status: str, *, usage: dict | None = None, now: datetime | None = None) -> dict:
    """更新 run.status，并把有效时长写回 usage / run.usage。返回更新后的 usage。"""
    now = now or utcnow()
    if run.status == new_status:
        current = usage if usage is not None else dict(run.usage or {})
        return current
    updated = transfer_active_time(usage if usage is not None else run.usage, run.status, new_status, now=now)
    if usage is not None:
        usage.clear()
        usage.update(updated)
        run.usage = dict(usage)
    else:
        run.usage = updated
    run.status = new_status
    return updated


def duration_seconds(run: Run, *, now: datetime | None = None, wait_s: float = 0.0) -> float:
    """当前有效执行秒数。有累计字段时用累计；否则回退为 started→end 扣除已知人审等待。"""
    now = now or utcnow()
    usage = run.usage or {}
    if "active_seconds" in usage or usage.get("active_since"):
        total = float(usage.get("active_seconds") or 0)
        since = usage.get("active_since")
        if since and run.status in ACTIVE_STATUSES:
            try:
                total += max(0.0, (now - _parse_ts(since)).total_seconds())
            except Exception:
                pass
        return round(max(0.0, total), 1)
    if not run.started_at:
        return 0.0
    end = run.ended_at or now
    return round(max(0.0, (end - run.started_at).total_seconds() - wait_s), 1)
