"""额度与成本：按用户、部门、智能体统计当日云端费用与 token，超额时按策略降级到私域、排队或拒绝。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from ..db import _local, session_scope, utcnow
from ..models import ModelCall, Organization, Run, User
from .config_center import cfg

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
TTL = 30.0


class QuotaExceeded(RuntimeError):
    pass


def day_start_utc() -> datetime:
    local = utcnow() + timedelta(hours=8)
    return local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)


def usage_today(force: bool = False) -> dict:
    """当日云端用量：{"users": {id: {...}}, "agents": {...}, "orgs": {...}, "total": {...}}。30 秒缓存。"""
    with _lock:
        hit = _cache.get("today")
        if hit and not force and time.time() - hit[0] < TTL:
            return hit[1]
        if hit and getattr(_local, "depth", 0):
            return hit[1]
    if getattr(_local, "depth", 0):
        return {"users": {}, "agents": {}, "orgs": {}, "total": {"cost": 0.0, "tokens": 0, "calls": 0}}
    since = day_start_utc()
    with session_scope() as s:
        calls = s.execute(select(ModelCall.run_id, ModelCall.zone, ModelCall.cost, ModelCall.tokens_in, ModelCall.tokens_out, ModelCall.latency_ms, ModelCall.model_id).where(ModelCall.ts >= since)).all()
        run_ids = {c[0] for c in calls if c[0]}
        runs = {r.id: (r.agent_id, r.created_by, (r.business_ref or {}).get("root_run_id")) for r in s.scalars(select(Run).where(Run.id.in_(run_ids))).all()} if run_ids else {}
        users = {u.id: u.org_id for u in s.scalars(select(User)).all()}
    out: dict = {"users": {}, "agents": {}, "orgs": {}, "total": {"cost": 0.0, "tokens": 0, "calls": 0, "private_seconds": 0.0}}

    def bump(bucket: dict, key, cost: float, tokens: int) -> None:
        b = bucket.setdefault(key, {"cost": 0.0, "tokens": 0, "calls": 0})
        b["cost"] += cost
        b["tokens"] += tokens
        b["calls"] += 1

    for run_id, zone, cost, tin, tout, latency, model_id in calls:
        if zone != "cloud":
            if model_id != "private-builtin":
                out["total"]["private_seconds"] += (latency or 0) / 1000
            continue
        agent, user, _ = runs.get(run_id, (None, None, None))
        tokens = (tin or 0) + (tout or 0)
        out["total"]["cost"] += cost or 0
        out["total"]["tokens"] += tokens
        out["total"]["calls"] += 1
        if agent:
            bump(out["agents"], agent, cost or 0, tokens)
        if user:
            bump(out["users"], user, cost or 0, tokens)
            if users.get(user):
                bump(out["orgs"], users[user], cost or 0, tokens)
    with _lock:
        _cache["today"] = (time.time(), out)
    return out


def limits_for(user_id: int | None, agent_id: str | None) -> list[dict]:
    q = cfg("quota")
    usage = usage_today()
    rows = []
    if user_id:
        with session_scope() as s:
            u = s.get(User, user_id)
            org = s.get(Organization, u.org_id) if u and u.org_id else None
            personal = dict(u.quota or {}) if u else {}
            org_code, org_name, org_id = (org.code, org.name, org.id) if org else (None, None, None)
        used = usage["users"].get(user_id, {"cost": 0.0, "tokens": 0})
        rows.append({"scope": "用户", "name": "本人", "limit_cost": float(personal.get("daily_cost") or q["user_daily_cost"]), "used_cost": round(used["cost"], 4), "limit_tokens": int(personal.get("daily_tokens") or q["user_daily_tokens"]), "used_tokens": used["tokens"]})
        if org_code and org_code in (q.get("department_daily_cost") or {}):
            o = usage["orgs"].get(org_id, {"cost": 0.0, "tokens": 0})
            rows.append({"scope": "部门", "name": org_name, "limit_cost": float(q["department_daily_cost"][org_code]), "used_cost": round(o["cost"], 4), "limit_tokens": None, "used_tokens": o["tokens"]})
    if agent_id and agent_id in (q.get("agent_daily_cost") or {}):
        a = usage["agents"].get(agent_id, {"cost": 0.0, "tokens": 0})
        rows.append({"scope": "智能体", "name": agent_id, "limit_cost": float(q["agent_daily_cost"][agent_id]), "used_cost": round(a["cost"], 4), "limit_tokens": None, "used_tokens": a["tokens"]})
    for r in rows:
        r["exceeded"] = r["used_cost"] >= r["limit_cost"] or (r["limit_tokens"] is not None and r["used_tokens"] >= r["limit_tokens"])
        r["ratio"] = round(max(r["used_cost"] / r["limit_cost"] if r["limit_cost"] else 0, (r["used_tokens"] / r["limit_tokens"]) if r.get("limit_tokens") else 0), 4)
    return rows


def check_start(user_id: int | None, agent_id: str) -> dict:
    """发起运行前的额度检查：拒绝或排队策略下超额直接拦截；降级策略下标记该运行只用私域模型。"""
    if not user_id or getattr(_local, "depth", 0):
        return {}
    rows = limits_for(user_id, agent_id)
    exceeded = [r for r in rows if r["exceeded"]]
    if not exceeded:
        return {}
    strategy = cfg("quota")["over_limit"]
    names = "、".join(f"{r['scope']}{r['name']}" for r in exceeded)
    if strategy == "reject":
        raise QuotaExceeded(f"{names}的当日云端额度已用尽，按超额策略拒绝发起新运行")
    if strategy == "queue":
        raise QuotaExceeded(f"{names}的当日云端额度已用尽，按超额策略排队到次日；如需立即执行请申请追加额度")
    return {"_quota_degrade": names}


def cloud_blocked(agent_id: str | None, user_id: int | None) -> str | None:
    """运行中途的检查：智能体或发起人当日额度用尽且策略为降级时，后续推理只走私域。"""
    if cfg("quota")["over_limit"] != "degrade":
        return None
    usage = usage_today()
    q = cfg("quota")
    if agent_id and agent_id in (q.get("agent_daily_cost") or {}) and usage["agents"].get(agent_id, {}).get("cost", 0) >= float(q["agent_daily_cost"][agent_id]):
        return f"智能体 {agent_id} 当日云端额度已用尽"
    if user_id and usage["users"].get(user_id, {}).get("cost", 0) >= float(q["user_daily_cost"]):
        return "发起人当日云端额度已用尽"
    return None
