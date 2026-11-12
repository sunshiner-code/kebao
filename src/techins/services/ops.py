"""运维：外部数据源登记与健康探测、企业数据接入连接器与数据质量、系统监控与告警、审计导出与异常行为、留存清理。"""

from __future__ import annotations

import csv
import io
import logging
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select, text

from ..config import settings
from ..db import engine, session_scope, utcnow
from ..models import (
    AuditLog,
    Connector,
    DataAuthorization,
    DataSource,
    EgressLog,
    Enterprise,
    ExternalEvent,
    MonitorPlan,
    ModelCall,
    ModelRegistryEntry,
    Notification,
    Policy,
    PriceItem,
    Run,
    TelemetryPoint,
)
from .config_center import cfg
from .notify import notify

log = logging.getLogger("techins.ops")

# ---------------------------------------------------------------- 外部数据源

DATA_SOURCES = [
    ("patent", "专利数据库", "知识产权", 0.02),
    ("registry", "工商登记", "企业信息", 0.01),
    ("court", "司法与执行信息", "司法", 0.02),
    ("news", "新闻舆情", "舆情", 0.005),
    ("financial", "上市公司财报", "财务", 0.03),
    ("policy", "政策法规库", "政策法规", 0.0),
    ("report", "行业研究报告", "行业", 0.05),
    ("vuln", "漏洞情报", "安全", 0.01),
    ("price", "设备与部件价格库", "价格", 0.0),
    ("disaster", "气象与灾害预警", "灾害", 0.0),
    ("tax", "纳税与社保核验（企业授权）", "企业信息", 0.05),
]
TOOL_SOURCE = {"patent": "patent", "news": "news", "financial": "financial", "report": "report", "policy": "policy"}


class SourceDisabled(RuntimeError):
    pass


def seed_data_sources() -> None:
    with session_scope() as s:
        have = {d.code for d in s.scalars(select(DataSource)).all()}
        for code, name, cat, cost in DATA_SOURCES:
            if code not in have:
                s.add(DataSource(code=code, name=name, category=cat, unit_cost=cost, provider="演示数据集（可替换为商业数据源）", field_mapping={"enterprise": "企业名称", "date": "发布日期", "text": "正文"}))


def check_source(code: str) -> None:
    """数据源调用前检查：停用或当日调用额度用尽时拒绝。"""
    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == code)).first()
        if ds is None:
            return
        today = (utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")
        if not ds.enabled:
            raise SourceDisabled(f"数据源「{ds.name}」已停用")
        if ds.usage_date == today and ds.used_today >= ds.daily_quota:
            raise SourceDisabled(f"数据源「{ds.name}」今日调用额度（{ds.daily_quota} 次）已用尽")


def record_source_call(code: str, ok: bool, latency_ms: float, error: str | None = None) -> None:
    with session_scope() as s:
        ds = s.scalars(select(DataSource).where(DataSource.code == code)).first()
        if ds is None:
            return
        today = (utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d")
        if ds.usage_date != today:
            ds.usage_date, ds.used_today = today, 0
        ds.used_today += 1
        ds.calls += 1
        ds.failures += 0 if ok else 1
        ds.latency_ms = round(0.8 * (ds.latency_ms or latency_ms) + 0.2 * latency_ms, 1)
        ds.failure_rate = round(ds.failures / max(1, ds.calls), 4)
        # 成功后清空历史错误，否则页面会一直挂着已恢复的报错
        ds.last_error = None if ok else (error or "")[:400]


def probe_sources() -> list[dict]:
    """健康探测：对每个启用的数据源以样本企业做一次真实查询，记录可用性与延迟；连续失败告警。"""
    from ..datasources.public import get_source

    with session_scope() as s:
        sample = s.scalars(select(Enterprise).order_by(Enterprise.id)).first()
        rows = [(d.code, d.enabled) for d in s.scalars(select(DataSource)).all()]
    name, track = (sample.name, sample.track) if sample else ("", "")
    ds = get_source()
    probes = {
        "patent": lambda: ds.patents(name),
        "registry": lambda: ds.registry(name),
        "court": lambda: ds.court(name),
        "news": lambda: ds.news(name),
        "financial": lambda: ds.financial_report(name) or ds.registry(name),
        "policy": lambda: ds.policies("科技保险"),
        "report": lambda: ds.industry_report(track),
        "tax": lambda: ds.tax_social_security(name),
    }
    out = []
    for code, enabled in rows:
        if not enabled:
            continue
        started = time.perf_counter()
        ok, err, n = True, None, 0
        try:
            if code in probes:
                res = probes[code]()
                n = len(res) if isinstance(res, list) else (1 if res else 0)
            elif code == "price":
                with session_scope() as s:
                    n = s.scalar(select(func.count(PriceItem.id))) or 0
            else:
                with session_scope() as s:
                    n = s.scalar(select(func.count(ExternalEvent.id)).where(ExternalEvent.source == code)) or 0
                ok = True
        except Exception as exc:  # noqa: BLE001
            ok, err = False, str(exc)
        latency = (time.perf_counter() - started) * 1000
        with session_scope() as s:
            d = s.scalars(select(DataSource).where(DataSource.code == code)).first()
            hist = list(d.probe_history or [])
            hist.append({"at": utcnow().isoformat() + "Z", "ok": ok, "latency_ms": round(latency, 1), "records": n})
            d.probe_history = hist[-48:]
            fails = 0
            for h in reversed(d.probe_history):
                if h["ok"]:
                    break
                fails += 1
            d.health = "healthy" if ok else ("down" if fails >= 3 else "degraded")
            d.latency_ms = round(latency, 1)
            d.last_checked_at = utcnow()
            # 探测同样是一次外部调用：计入累计次数与失败率，成功时清空历史错误，
            # 避免出现「healthy + 失败率 0% + 红色报错」自相矛盾的展示
            d.calls += 1
            d.failures += 0 if ok else 1
            d.failure_rate = round(d.failures / max(1, d.calls), 4)
            d.last_error = None if ok else (err or "")[:400]
            if fails >= 3:
                notify(s, kind="system", title=f"外部数据源连续失败：{d.name}", body=err or "", link="/admin/integration", level="error", role="admin")
            out.append({"code": code, "ok": ok, "latency_ms": round(latency, 1), "records": n})
    return out


# ---------------------------------------------------------------- 企业数据接入连接器

CONNECTOR_NAMES = {"iot": "物联网网关", "log": "安全日志采集", "ops": "研发与运营系统接口"}
SCENARIO_FLAG = {"iot": "device", "log": "logs", "ops": "ops"}


def sync_connectors() -> int:
    """按在保保单的监测方案与数据接入授权维护连接器，并计算心跳与数据质量（缺失率、延迟、异常值比例）。"""
    from .monitoring import hour_of, sim_time

    with session_scope() as s:
        plans = s.scalars(select(MonitorPlan)).all()
        existing = {(c.policy_id, c.kind): c for c in s.scalars(select(Connector)).all()}
        auths = s.scalars(select(DataAuthorization).where(DataAuthorization.status == "active")).all()
        n = 0
        for plan in plans:
            policy = s.get(Policy, plan.policy_id)
            if policy is None:
                continue
            sc = (plan.state or {}).get("scenario") or {}
            ent = s.get(Enterprise, policy.enterprise_id)
            for kind in ("iot", "log", "ops"):
                declared = (policy.coverage or {}).get("data_authorizations") or {}
                in_scope = bool(sc.get(SCENARIO_FLAG[kind])) or (kind in declared and not declared.get(kind, True))
                if not in_scope and (plan.policy_id, kind) not in existing:
                    continue
                c = existing.get((plan.policy_id, kind))
                if c is None:
                    detail = {"iot": f"设备 {len(sc.get('assets', []))} 台", "log": "核心业务系统", "ops": "研发项目管理"}[kind]
                    c = Connector(enterprise_id=ent.id, policy_id=policy.id, kind=kind, name=f"{ent.name} · {CONNECTOR_NAMES[kind]}（{detail}）", endpoint=f"{'mqtt' if kind == 'iot' else 'https'}://gw.{ent.credit_code[-6:].lower()}.techins-edge.cn/{kind}")
                    s.add(c)
                    s.flush()
                auth = next((a for a in auths if a.enterprise_id == ent.id and kind in (a.scopes or []) and (a.policy_id in (None, policy.id))), None)
                c.authorization_id = auth.id if auth else c.authorization_id
                state = plan.state or {}
                live = (c.mode or "sim") == "live"
                now = utcnow() if live else sim_time(state.get("hour", 0), state)
                last = s.scalar(select(func.max(TelemetryPoint.ts)).where(TelemetryPoint.policy_id == policy.id, TelemetryPoint.source == kind))
                c.last_heartbeat = last
                window_start = now - timedelta(hours=24)
                vals = [v for (v,) in s.execute(select(TelemetryPoint.value).where(TelemetryPoint.policy_id == policy.id, TelemetryPoint.source == kind, TelemetryPoint.ts > window_start)).all()]
                metrics = {"iot": 4 * max(1, len(sc.get("assets", []))), "log": 3, "ops": 2}[kind]
                expected = metrics * (24 if kind != "ops" else 1)
                missing = max(0.0, 1 - len(vals) / expected) if expected else 0.0
                lag_h = round((now - last).total_seconds() / 3600, 1) if last else None
                anomaly = 0.0
                if len(vals) >= 10:
                    arr = np.array(vals, dtype=float)
                    med = np.median(arr)
                    mad = np.median(np.abs(arr - med)) or 1e-9
                    anomaly = float(np.mean(np.abs(arr - med) / (1.4826 * mad) > 6))
                prev_pull = (c.quality or {}).get("last_pull_at")
                c.quality = {"window_hours": 24, "expected": expected, "received": len(vals), "missing_rate": round(missing, 3), "lag_hours": lag_h, "anomaly_rate": round(anomaly, 3), "sim_now": now.isoformat(), "plan_hour": round(hour_of(now, state), 1), "clock": "wall" if live else "sim", "last_pull_at": prev_pull}
                outage = False if live else (state.get("outage") or {}).get(kind)
                if not sc.get(SCENARIO_FLAG[kind]):
                    c.status = "suspended"
                elif outage or lag_h is None or lag_h > 3:
                    c.status = "offline"
                elif missing > 0.2 or anomaly > 0.05:
                    c.status = "degraded"
                else:
                    c.status = "online"
                n += 1
    return n


def set_connector_outage(connector_id: int, outage: bool) -> dict:
    with session_scope() as s:
        c = s.get(Connector, connector_id)
        if c is None:
            raise ValueError("连接器不存在")
        if (c.mode or "sim") == "live":
            raise ValueError("实接连接器不能模拟中断")
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == c.policy_id)).first()
        if plan is None:
            raise ValueError("连接器没有关联的监测方案")
        state = dict(plan.state or {})
        out = dict(state.get("outage") or {})
        out[c.kind] = outage
        state["outage"] = out
        plan.state = state
    sync_connectors()
    return {"ok": True}


# ---------------------------------------------------------------- 系统监控

class ApiMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: deque = deque(maxlen=20000)

    def record(self, path: str, status: int, ms: float) -> None:
        with self._lock:
            self._rows.append((time.time(), path, status, ms))

    def summary(self, minutes: int = 15) -> dict:
        cutoff = time.time() - minutes * 60
        with self._lock:
            rows = [r for r in self._rows if r[0] >= cutoff]
        lat = np.array([r[3] for r in rows]) if rows else np.array([0.0])
        per_min: dict[int, list] = {}
        for t, _, st, ms in rows:
            per_min.setdefault(int(t // 60), []).append((st, ms))
        series = [{"minute": datetime.utcfromtimestamp(k * 60).isoformat() + "Z", "requests": len(v), "errors": sum(1 for st, _ in v if st >= 500), "p95_ms": round(float(np.percentile([ms for _, ms in v], 95)), 1)} for k, v in sorted(per_min.items())]
        slow: dict[str, list] = {}
        for _, p, _, ms in rows:
            slow.setdefault(p, []).append(ms)
        top = sorted(({"path": p, "count": len(v), "p95_ms": round(float(np.percentile(v, 95)), 1)} for p, v in slow.items()), key=lambda x: -x["p95_ms"])[:8]
        return {"window_minutes": minutes, "requests": len(rows), "errors": sum(1 for r in rows if r[2] >= 500), "error_rate": round(sum(1 for r in rows if r[2] >= 500) / max(1, len(rows)), 4), "p95_ms": round(float(np.percentile(lat, 95)), 1), "p50_ms": round(float(np.percentile(lat, 50)), 1), "series": series, "slowest": top}


api_metrics = ApiMetrics()


def _dir_size(path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def system_status() -> dict:
    from ..harness.executor import executor

    now = utcnow()
    api = api_metrics.summary(15)
    with session_scope() as s:
        queued = s.execute(select(Run.agent_id, func.count(Run.id)).where(Run.status == "queued").group_by(Run.agent_id)).all()
        day_runs = s.scalars(select(Run).where(Run.created_at >= now - timedelta(hours=24), Run.parent_run_id.is_(None))).all()
        calls = s.scalars(select(ModelCall).where(ModelCall.ts >= now - timedelta(hours=1))).all()
        models = s.scalars(select(ModelRegistryEntry)).all()
        sources = s.scalars(select(DataSource)).all()
        plans = s.scalars(select(MonitorPlan).where(MonitorPlan.status == "active")).all()
        blocked = s.scalar(select(func.count(EgressLog.id)).where(EgressLog.ts >= now - timedelta(hours=24), EgressLog.scan_result != "pass")) or 0
        egress_hour = s.scalar(select(func.count(EgressLog.id)).where(EgressLog.ts >= now - timedelta(hours=1))) or 0
        model_rows = []
        for m in models:
            mc = [c for c in calls if c.model_id == m.id]
            err = sum(1 for c in mc if c.status != "ok") / len(mc) if mc else 0.0
            lat = float(np.mean([c.latency_ms for c in mc])) if mc else 0.0
            state = "disabled" if not m.enabled else ("unconfigured" if not m.configured else ("circuit_open" if mc and err > 0.5 and len(mc) >= 4 else ("degraded" if err > 0.2 else "healthy")))
            model_rows.append({"id": m.id, "name": m.name, "zone": m.zone, "calls_1h": len(mc), "error_rate": round(err, 3), "avg_latency_ms": round(lat), "state": state})
        private_ms = sum(c.latency_ms for c in calls if c.zone == "private" and c.model_id != "private-builtin")
        last_ticks = [p.state.get("last_tick_at") for p in plans if (p.state or {}).get("last_tick_at")]
        last_tick = max(last_ticks) if last_ticks else None
        finished = [r for r in day_runs if r.status in ("completed", "failed")]
        fail_rate = sum(1 for r in finished if r.status == "failed") / len(finished) if finished else 0.0
        src_rows = [{"code": d.code, "name": d.name, "enabled": d.enabled, "health": d.health, "latency_ms": d.latency_ms, "failure_rate": d.failure_rate} for d in sources]
    db_size = _database_bytes()
    backlog = {a: n for a, n in queued}
    tick_lag = (now - datetime.fromisoformat(last_tick.rstrip("Z"))).total_seconds() if last_tick else None
    alerts = []
    if api["p95_ms"] > 1000 and api["requests"] > 20:
        alerts.append({"level": "warning", "object": "接口服务", "message": f"近 15 分钟 95 分位延迟 {api['p95_ms']:.0f} ms 超过 1 秒"})
    if api["error_rate"] > 0.05 and api["requests"] > 20:
        alerts.append({"level": "error", "object": "接口服务", "message": f"错误率 {api['error_rate']:.1%}"})
    for agent in ("AG-RW", "AG-CL"):
        if backlog.get(agent, 0) > 10:
            alerts.append({"level": "error", "object": "运行执行器", "message": f"{'事故与预警' if agent == 'AG-RW' else '理赔'}队列积压 {backlog[agent]} 个运行", "notify_business": True})
    if fail_rate > 0.2 and len(finished) >= 5:
        alerts.append({"level": "warning", "object": "运行执行器", "message": f"近 24 小时运行失败率 {fail_rate:.0%}"})
    for m in model_rows:
        if m["state"] == "circuit_open":
            alerts.append({"level": "error", "object": "模型服务", "message": f"{m['name']} 错误率 {m['error_rate']:.0%}，已熔断，路由沿兜底链回退"})
    for d in src_rows:
        if d["enabled"] and d["health"] == "down":
            alerts.append({"level": "warning", "object": "数据源", "message": f"{d['name']} 连续探测失败"})
    if blocked:
        alerts.append({"level": "warning", "object": "隐私", "message": f"近 24 小时出域二次扫描拦截 {blocked} 次"})
    if egress_hour > int(cfg("privacy.egress")["spike_per_hour"]):
        alerts.append({"level": "warning", "object": "隐私", "message": f"近 1 小时出域 {egress_hour} 次，超过突增阈值"})
    if settings.monitor_enabled and tick_lag is not None and tick_lag > max(180, settings.monitor_tick_seconds * 3):
        alerts.append({"level": "warning", "object": "流处理", "message": f"监测调度已 {tick_lag / 60:.0f} 分钟未运行"})
    return {
        "api": api,
        "executor": {"workers": executor._size, "active": len(executor._active), "queue": executor._queue.qsize(), "backlog_by_agent": backlog, "runs_24h": len(day_runs), "fail_rate_24h": round(fail_rate, 3)},
        "models": model_rows,
        "private_inference": {"busy_seconds_1h": round(private_ms / 1000, 1), "gpu_utilization": round(min(1.0, private_ms / 1000 / 3600), 4), "queue": backlog.get("AG-CL", 0) + backlog.get("AG-UW", 0)},
        "stream": {"enabled": settings.monitor_enabled, "plans": len(plans), "last_tick_at": last_tick, "tick_lag_seconds": round(tick_lag) if tick_lag is not None else None, "tick_seconds": settings.monitor_tick_seconds},
        "sources": src_rows,
        "privacy": {"blocked_24h": blocked, "egress_1h": egress_hour},
        "storage": {"db_bytes": db_size},
        "alerts": alerts,
    }


def _database_bytes() -> int:
    if engine.dialect.name == "sqlite":
        db_file = settings.data_dir / "techins.db"
        return db_file.stat().st_size if db_file.exists() else 0
    with engine.connect() as conn:
        size = conn.execute(text("SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.tables WHERE table_schema = DATABASE()")).scalar()
    return int(size or 0)


def push_system_alerts() -> int:
    st = system_status()
    with session_scope() as s:
        for a in st["alerts"]:
            notify(s, kind="system", title=f"系统告警：{a['object']}", body=a["message"], link="/admin/monitor", level=a["level"], role="admin")
            if a.get("notify_business"):
                notify(s, kind="system", title=f"业务阻塞告警：{a['object']}", body=a["message"], link="/admin/monitor", level="error", role="service")
    return len(st["alerts"])


# ---------------------------------------------------------------- 审计导出与异常行为


AUDIT_COLUMNS = ["编号", "时间（北京时间）", "用户", "类别", "操作", "对象类型", "对象", "来源地址", "临时授权", "详情"]


def audit_query(s, *, category: str | None = None, username: str | None = None, action: str | None = None, start: datetime | None = None, end: datetime | None = None, limit: int = 500):
    q = select(AuditLog)
    if category:
        q = q.where(AuditLog.category == category)
    if username:
        q = q.where(AuditLog.username == username)
    if action:
        q = q.where(AuditLog.action.contains(action))
    if start:
        q = q.where(AuditLog.ts >= start)
    if end:
        q = q.where(AuditLog.ts <= end)
    return s.scalars(q.order_by(AuditLog.id.desc()).limit(limit)).all()


def audit_csv(filters: dict) -> str:
    import json

    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(AUDIT_COLUMNS)
    with session_scope() as s:
        rows = audit_query(s, category=filters.get("category"), username=filters.get("username"), action=filters.get("action"), start=datetime.fromisoformat(filters["start"]) if filters.get("start") else None, end=datetime.fromisoformat(filters["end"]) if filters.get("end") else None, limit=int(filters.get("limit") or 5000))
        for a in rows:
            d = dict(a.detail or {})
            w.writerow([a.id, (a.ts + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"), a.username or "系统", a.category, a.action, a.object_type or "", a.object_id or "", d.pop("ip", ""), ",".join(str(x) for x in d.pop("temp_grants", [])), json.dumps(d, ensure_ascii=False)[:500]])
    return buf.getvalue()


def detect_audit_anomalies() -> list[dict]:
    """异常行为：短时间大量查看 L2 原文、非工作时间导出。"""
    sec = cfg("security")
    now = utcnow()
    found = []
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.category == "data_access", AuditLog.ts >= now - timedelta(minutes=10))).all()
        by_user: dict[str, int] = {}
        for a in rows:
            if (a.detail or {}).get("privacy") == "L2原文" or "原文" in a.action:
                by_user[a.username or "?"] = by_user.get(a.username or "?", 0) + 1
        for user, n in by_user.items():
            if n > int(sec["l2_view_alert"]):
                found.append({"user": user, "rule": "短时间大量查看 L2 原文", "count": n})
        exports = s.scalars(select(AuditLog).where(AuditLog.action.contains("导出"), AuditLog.ts >= now - timedelta(hours=1))).all()
        for a in exports:
            local = a.ts + timedelta(hours=8)
            if local.hour >= 22 or local.hour < 7:
                found.append({"user": a.username, "rule": "非工作时间导出", "count": 1})
        for f in found:
            notify(s, kind="system", title=f"异常行为告警：{f['rule']}", body=f"用户 {f['user']}，{f['count']} 次", link="/admin/audit", level="error", role="admin")
            s.add(AuditLog(username="系统", category="operation", action="异常行为告警", object_type="user", object_id=f["user"], detail=f))
    return found


# ---------------------------------------------------------------- 留存清理


def retention_cleanup() -> dict:
    """临时工作区到期清理：已结束且超过保留期的运行工作区删除并记录；运行轨迹与出域日志保留。"""
    days = int(cfg("params.dicts")["system"]["workspace_retention_days"])
    cutoff = utcnow() - timedelta(days=days)
    with session_scope() as s:
        old = s.scalars(select(Run).where(Run.ended_at.is_not(None), Run.ended_at < cutoff, Run.parent_run_id.is_(None))).all()
        roots = [r.id for r in old]
    removed, freed = 0, 0
    for rid in roots:
        path = settings.workspaces_dir / f"run-{rid}"
        if path.exists():
            size = _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
            freed += size
    if removed:
        with session_scope() as s:
            s.add(AuditLog(username="系统", category="operation", action="临时工作区到期销毁", detail={"runs": removed, "bytes": freed, "retention_days": days}))
    return {"checked": len(roots), "removed": removed, "freed_bytes": freed, "retention_days": days}


_ = Notification
