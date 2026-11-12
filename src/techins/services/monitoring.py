"""常驻监测调度：企业授权数据的流式接入（演示环境由仿真器产生）、检测方法执行、信号归并与抑制、触发预警研判运行。

监测状态（基线、阈值、静默期、历史信号、方法误报率、维护窗口）作为持久状态保存在监测方案上；
每个候选事件的研判是一次短运行（AG-RW · alert）。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import func, select

from ..analytics import detection as D
from ..config import settings
from ..db import session_scope, utcnow
from ..harness import hitl
from ..harness.executor import executor
from ..knowledge.catalog import LINES, SUBTYPES
from ..models import Alert, Connector, Enterprise, ExternalEvent, MonitorPlan, Policy, TelemetryPoint

log = logging.getLogger("techins.monitor")
SIM_START = datetime(2026, 10, 20)
DEFAULT_BACKFILL_HOURS = 24 * 14
METHOD_DEFAULT_FPR = 0.2


def t0(state: dict | None) -> datetime:
    """监测方案的仿真时钟零点：方案发布时刻减去回填窗口，回填数据止于发布时刻。"""
    raw = (state or {}).get("sim_start")
    return datetime.fromisoformat(raw) if raw else SIM_START


def sim_time(hour: float, state: dict | None = None) -> datetime:
    return t0(state) + timedelta(hours=hour)


def hour_of(ts: datetime, state: dict | None) -> float:
    return (ts - t0(state)).total_seconds() / 3600


def scenario_for(ent: Enterprise, lines: list[str], unpatched: int, authorizations: dict, start_h: int) -> dict:
    """仿真场景：按企业画像决定设备是否处于退化过程、信息系统是否处于攻击过程；事件时刻相对方案发布时刻设定。"""
    p = ent.profile or {}
    eq = sorted(p.get("equipment", []), key=lambda e: -e["value"])[:2]
    notes = " ".join(str(x) for x in p.get("milestones", []))
    from ..models import Material

    with session_scope() as s:
        texts = " ".join(m.text or "" for m in s.scalars(select(Material).where(Material.enterprise_id == ent.id)).all())
    degrade = "振动值有上升趋势" in texts or "过热" in texts
    monitors = {item for line in lines for item in (LINES.get(line, {}).get("monitors") or [])}
    return {
        "assets": [{"serial": e["serial"], "name": e["name"], "value": e["value"]} for e in eq],
        "device": "device" in monitors and bool(eq) and authorizations.get("iot", True),
        "device_mode": "degradation" if degrade else "normal",
        "degrade_start_h": start_h + 10,
        "failure_h": start_h + 58,
        "logs": "logs" in monitors and authorizations.get("log", True),
        "log_mode": "attack" if unpatched else "normal",
        "attack_h": start_h + 20,
        "unpatched": unpatched,
        "ops": "ops" in monitors and authorizations.get("ops", True),
        "ops_delayed": "延期" in notes,
    }


def _device_point(h: int, sc: dict, asset_idx: int, rng: np.random.Generator) -> dict:
    cyc = math.sin(2 * math.pi * (h % 24) / 24)
    vib = 2.8 + 0.3 * cyc + rng.normal(0, 0.15) + asset_idx * 0.2
    temp = 55 + 4 * cyc + rng.normal(0, 0.8)
    cur = 28 + 3 * cyc + rng.normal(0, 0.5)
    water = 0.0
    if sc["device_mode"] == "degradation" and asset_idx == 0 and h >= sc["degrade_start_h"]:
        d = h - sc["degrade_start_h"]
        vib += 0.085 * d
        temp += 0.55 * d
        cur += 0.08 * d
        if h >= sc["failure_h"]:
            if h < sc["failure_h"] + 2:
                vib, temp, cur = 11.5 + rng.normal(0, 0.4), 97 + rng.normal(0, 1.5), 46 + rng.normal(0, 1)
            else:
                vib, temp, cur = 0.05, 32 + rng.normal(0, 0.5), 0.0
    return {"vibration_rms": max(0.0, vib), "bearing_temp": temp, "motor_current": max(0.0, cur), "water_leak": water}


def _log_point(h: int, sc: dict, rng: np.random.Generator) -> dict:
    cyc = 1 + 0.6 * math.sin(2 * math.pi * ((h % 24) - 6) / 24)
    lf = rng.poisson(14 * cyc)
    eg = rng.lognormal(math.log(180 * cyc), 0.25)
    if sc["log_mode"] == "attack":
        if sc["attack_h"] <= h < sc["attack_h"] + 6:
            lf = rng.poisson(260)
        if sc["attack_h"] + 6 <= h < sc["attack_h"] + 10:
            eg = rng.lognormal(math.log(2400), 0.2)
    return {"login_failures": float(lf), "egress_mb": float(eg), "unpatched_critical": float(sc["unpatched"])}


def generate(plan: MonitorPlan, from_h: int, to_h: int, s) -> int:
    sc = plan.state["scenario"]
    outage = plan.state.get("outage", {})
    s.flush()
    live = {c.kind for c in s.scalars(select(Connector).where(Connector.policy_id == plan.policy_id, Connector.mode == "live")).all()}
    rng = np.random.default_rng(plan.policy_id * 1000 + from_h)
    rows = 0
    for h in range(from_h, to_h):
        ts = sim_time(h, plan.state)
        if sc["device"] and not outage.get("iot") and "iot" not in live:
            for i, a in enumerate(sc["assets"]):
                for metric, value in _device_point(h, sc, i, rng).items():
                    s.add(TelemetryPoint(policy_id=plan.policy_id, source="iot", asset=a["serial"], metric=metric, ts=ts, value=round(float(value), 4)))
                    rows += 1
        if sc["logs"] and not outage.get("log") and "log" not in live:
            for metric, value in _log_point(h, sc, rng).items():
                s.add(TelemetryPoint(policy_id=plan.policy_id, source="log", asset="核心业务系统", metric=metric, ts=ts, value=round(float(value), 3)))
                rows += 1
        if sc["ops"] and h % 24 == 0 and not outage.get("ops") and "ops" not in live:
            base_delay = 20 if sc.get("ops_delayed") else 0
            delay = base_delay + (h / 24 * 0.8 if sc.get("ops_delayed") else max(0.0, rng.normal(0, 1)))
            spend = 1.0 - (0.4 if sc.get("ops_delayed") and h > 24 * 20 else 0.0) + rng.normal(0, 0.05)
            s.add(TelemetryPoint(policy_id=plan.policy_id, source="ops", asset="研发项目", metric="milestone_delay_days", ts=ts, value=round(delay, 1)))
            s.add(TelemetryPoint(policy_id=plan.policy_id, source="ops", asset="研发项目", metric="rd_spend_index", ts=ts, value=round(spend, 3)))
            rows += 2
            # 险种级运营 KRI（机器人本体事件、控制系统事故、临床试验不良事件等）
            for ind in plan.indicators or []:
                if ind.get("source") != "ops" or ind.get("status") == "degraded":
                    continue
                key = ind.get("key") or ""
                if key in ("milestone_delay_days", "rd_spend_index", "cash_months") or key.startswith(("ext:", "risk:", "aux:")):
                    continue
                thr = float(ind.get("threshold") or 1)
                direction = ind.get("direction") or "up"
                spike = sc.get("ops_spike_keys") or {}
                if key in spike and h >= int(spike[key].get("at_hour", 0)):
                    value = float(spike[key].get("value", thr * 2.5))
                elif direction == "up":
                    value = max(0.0, rng.normal(thr * 0.35, thr * 0.1))
                else:
                    value = max(0.0, rng.normal(thr * 1.2, thr * 0.05))
                asset = (ind.get("assets") or ["运营监测"])[0]
                s.add(TelemetryPoint(policy_id=plan.policy_id, source="ops", asset=asset, metric=key, ts=ts, value=round(float(value), 3)))
                rows += 1
    return rows


def series(policy_id: int, metric: str, asset: str | None = None, hours: int = 24 * 14, end: datetime | None = None, start: datetime | None = None) -> tuple[list[datetime], np.ndarray]:
    with session_scope() as s:
        q = select(TelemetryPoint.ts, TelemetryPoint.value).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.metric == metric)
        if asset:
            q = q.where(TelemetryPoint.asset == asset)
        if end is not None:
            q = q.where(TelemetryPoint.ts <= end)
        if start is not None:
            rows = s.execute(q.where(TelemetryPoint.ts >= start).order_by(TelemetryPoint.ts)).all()
            return [r[0] for r in rows], np.array([r[1] for r in rows], dtype=float)
        rows = s.execute(q.order_by(TelemetryPoint.ts.desc()).limit(hours)).all()
    rows = rows[::-1]
    return [r[0] for r in rows], np.array([r[1] for r in rows], dtype=float)


def threshold(plan: MonitorPlan, key: str, default: float) -> float:
    for ind in plan.indicators or []:
        if ind.get("key") == key and ind.get("status") != "degraded" and ind.get("threshold") is not None:
            return float(ind["threshold"])
    return default


def detect(plan: MonitorPlan) -> list[dict]:
    """对一份监测方案运行全部适用的检测方法（DM1–DM5、TP1–TP5），返回候选信号。"""
    sc = plan.state["scenario"]
    hour = plan.state.get("hour", 0)
    sim_now = sim_time(hour, plan.state)
    with session_scope() as s:
        live = {c.kind for c in s.scalars(select(Connector).where(Connector.policy_id == plan.policy_id, Connector.mode == "live")).all()}
    now = utcnow() if "iot" in live else sim_now
    signals: list[dict] = []
    vib_thr = threshold(plan, "vibration_rms", 7.1)
    temp_thr = threshold(plan, "bearing_temp", 85.0)
    if sc["device"]:
        for a in sc["assets"]:
            ts, vib = series(plan.policy_id, "vibration_rms", a["serial"], end=now)
            _, temp = series(plan.policy_id, "bearing_temp", a["serial"], end=now)
            _, cur = series(plan.policy_id, "motor_current", a["serial"], end=now)
            if len(vib) < 72:
                continue
            base = {"asset": a["serial"], "asset_name": a["name"], "risk_type": "R02-2", "liability": "机器损坏责任"}
            if ts and (now - ts[-1]).total_seconds() > 3 * 3600:
                signals.append({**base, "risk_type": "DATA", "liability": "数据接入", "method": "DM1", "key": f"feed-{a['serial']}", "strength": 0.9, "severity": 0.45, "metrics": [], "detail": f"{a['name']}的物联网数据已中断 {(now - ts[-1]).total_seconds() / 3600:.0f} 小时，监测降级为外部数据"})
                continue
            hi = D.health_index(vib, temp, vib_thr, temp_thr)
            kri_v = D.kri_check(vib, vib_thr)
            kri_t = D.kri_check(temp, temp_thr)
            ra = D.robust_anomalies(vib)
            cs = D.cusum(vib[-168:])
            bo = D.bocpd(vib[-120:])
            iso = D.isolation_scores(np.column_stack([vib, temp, cur]))
            stopped = vib[-1] < 0.3 and cur[-1] < 1 and np.median(vib[-24:-3]) > 2
            if stopped or (kri_v["breach"] and kri_t["breach"]):
                signals.append({**base, "method": "DM1", "key": f"dev-fail-{a['serial']}", "strength": 0.95, "severity": 0.95, "incident": True, "metrics": ["vibration_rms", "bearing_temp", "motor_current"], "detail": f"{a['name']}振动 {kri_v['last']} mm/s（阈值 {vib_thr}）、温度 {kri_t['last']}℃（阈值 {temp_thr}），{'设备已停机' if stopped else '同时超过报警限值'}"})
            elif kri_v["breach"] or kri_t["breach"]:
                signals.append({**base, "method": "DM1", "key": f"dev-kri-{a['serial']}", "strength": 0.7, "severity": 0.7, "metrics": ["vibration_rms", "bearing_temp"], "detail": f"{a['name']}{'振动' if kri_v['breach'] else '温度'}超过报警限值（振动 {kri_v['last']} / {vib_thr}，温度 {kri_t['last']} / {temp_thr}）"})
            if hi.get("index") is not None and (hi["index"] < 55 or (hi.get("rul_days") and hi["rul_days"] < 10)):
                signals.append({**base, "method": "TP1", "key": f"dev-health-{a['serial']}", "strength": 0.75, "severity": 0.6 if hi["index"] >= 40 else 0.8, "metrics": ["vibration_rms", "bearing_temp"], "health": {k: hi[k] for k in ("index", "slope_per_day", "rul_days")}, "detail": f"{a['name']}健康指数 {hi['index']}，日均变化 {hi['slope_per_day']}，预计剩余可用 {hi.get('rul_days')} 天"})
            if cs["change"] and cs["direction"] == "上升" and cs["index"] is not None and cs["index"] >= 168 - 72:
                signals.append({**base, "method": "DM3", "key": f"dev-cusum-{a['serial']}", "strength": 0.6, "severity": 0.5, "metrics": ["vibration_rms"], "detail": f"振动均值发生上升变点（累积和统计量 {cs['statistic']}），贝叶斯在线变点概率 {bo['cp_prob']}"})
            if ra.get("max_recent_z", 0) > 5 or iso.get("share", 0) > 0.3:
                signals.append({**base, "method": "DM2", "key": f"dev-anom-{a['serial']}", "strength": 0.55, "severity": 0.5, "metrics": ["vibration_rms", "bearing_temp", "motor_current"], "detail": f"时序异常：鲁棒标准分 {ra.get('max_recent_z')}，多变量孤立森林异常占比 {iso.get('share')}"})
    if sc["logs"]:
        now = utcnow() if "log" in live else sim_now
        ts, lf = series(plan.policy_id, "login_failures", "核心业务系统", end=now)
        _, eg = series(plan.policy_id, "egress_mb", "核心业务系统", end=now)
        if len(lf) >= 72:
            base = {"asset": "核心业务系统", "asset_name": "核心业务系统", "risk_type": "R07-2", "liability": "数据安全事件责任"}
            if ts and (now - ts[-1]).total_seconds() > 3 * 3600:
                signals.append({**base, "risk_type": "DATA", "liability": "数据接入", "method": "DM1", "key": "feed-log", "strength": 0.9, "severity": 0.45, "metrics": [], "detail": f"信息安全日志已中断 {(now - ts[-1]).total_seconds() / 3600:.0f} 小时，监测降级为外部数据"})
            else:
                sec = D.log_security(lf, eg, sc["unpatched"])
                eg_z = D.robust_anomalies(eg).get("max_recent_z", 0)
                lf_z = D.robust_anomalies(lf).get("max_recent_z", 0)
                recent_lf_peak = float(np.max(lf[-24:]))
                lf_thr = threshold(plan, "login_failures", 120)
                eg_thr = threshold(plan, "egress_mb", 800)
                if eg_z > 6 and (lf_z > 4 or float(np.max(lf[-30:])) > lf_thr) and float(np.max(eg[-6:])) > eg_thr:
                    signals.append({**base, "method": "TP2", "key": "sec-breach", "strength": 0.9, "severity": 0.9, "incident": True, "metrics": ["login_failures", "egress_mb"], "detail": "登录失败激增后出现大规模数据外传：" + "；".join(sec["signals"])})
                elif sec["score"] >= 40 or recent_lf_peak > lf_thr:
                    signals.append({**base, "method": "TP2", "key": "sec-anom", "strength": 0.7, "severity": min(0.85, sec["score"] / 100 + 0.2), "metrics": ["login_failures", "egress_mb"], "detail": "；".join(sec["signals"]) or f"登录失败峰值 {recent_lf_peak:.0f} 次/小时超过阈值 {lf_thr}"})
                elif sc["unpatched"] and hour % (24 * 3) == 0:
                    signals.append({**base, "method": "DM1", "key": "sec-vuln", "strength": 0.5, "severity": 0.45, "metrics": ["unpatched_critical"], "detail": f"{sc['unpatched']} 个高危漏洞尚未修补"})
    if sc["ops"]:
        now = utcnow() if "ops" in live else sim_now
        ts, delay = series(plan.policy_id, "milestone_delay_days", "研发项目", end=now)
        _, spend = series(plan.policy_id, "rd_spend_index", "研发项目", end=now)
        thr = threshold(plan, "milestone_delay_days", 30)
        if len(delay) >= 72 and ts and (now - ts[-1]).total_seconds() > 3 * 3600:
            signals.append({"asset": "研发项目", "asset_name": "研发项目", "risk_type": "DATA", "liability": "数据接入", "method": "DM1", "key": "feed-ops", "strength": 0.9, "severity": 0.45, "metrics": [], "detail": f"研发与运营数据已中断 {(now - ts[-1]).total_seconds() / 3600:.0f} 小时，监测降级为外部数据"})
        elif len(delay) and delay[-1] > thr:
            drop = len(spend) >= 10 and float(np.mean(spend[-5:])) < 0.75 * float(np.mean(spend[:-5]))
            signals.append({"asset": "研发项目", "asset_name": "研发项目", "risk_type": "R01-2", "liability": "研发中断责任", "method": "TP3", "key": "rd-delay", "strength": 0.6 + (0.15 if drop else 0), "severity": 0.5 + (0.15 if drop else 0), "metrics": ["milestone_delay_days", "rd_spend_index"], "detail": f"研发里程碑延误 {delay[-1]:.0f} 天（阈值 {thr:.0f}）" + ("，同期研发费用骤降" if drop else "")})
        # 险种运营 KRI：按监测方案指标阈值判越限
        for ind in plan.indicators or []:
            if ind.get("source") != "ops" or ind.get("status") == "degraded":
                continue
            key = ind.get("key") or ""
            if key in ("milestone_delay_days", "rd_spend_index") or key.startswith(("ext:", "risk:", "aux:")):
                continue
            asset = (ind.get("assets") or ["运营监测"])[0]
            asset_name = (ind.get("asset_names") or [ind.get("name") or asset])[0]
            ts, vals = series(plan.policy_id, key, asset, end=now)
            if len(vals) == 0:
                ts, vals = series(plan.policy_id, key, None, end=now)
            if len(vals) == 0:
                continue
            thr = float(ind.get("threshold") or 0)
            direction = ind.get("direction") or "up"
            last = float(vals[-1])
            breach = last >= thr if direction == "up" else last <= thr
            if not breach:
                continue
            methods = ind.get("methods") or ["DM1"]
            signals.append({
                "asset": asset,
                "asset_name": asset_name,
                "risk_type": (ind.get("subtypes") or ["R06-1"])[0],
                "liability": ind.get("liability") or ind.get("name") or key,
                "method": methods[0],
                "key": f"ops-{key}",
                "strength": 0.75,
                "severity": min(0.95, 0.55 + abs(last - thr) / max(thr, 1e-6) * 0.2),
                "incident": last >= thr * 2 if direction == "up" else last <= thr * 0.5,
                "metrics": [key],
                "detail": f"{ind.get('name') or key} 当前 {last:.1f}{ind.get('unit') or ''}，已{'高于' if direction == 'up' else '低于'}阈值 {thr}",
            })
    signals += detect_external(plan)
    return signals


def detect_external(plan: MonitorPlan) -> list[dict]:
    """外部事件：DM4 事件抽取与舆情研判（大模型抽取，内置规则兜底）、DM5 供应链风险传导、TP4 知识产权状态、TP5 产品质量信号。"""
    from ..llm.gateway import model_gateway

    from ..models import Product

    with session_scope() as s:
        policy = s.get(Policy, plan.policy_id)
        ent = s.get(Enterprise, policy.enterprise_id)
        suppliers = [sp for sp in (ent.profile or {}).get("suppliers", [])]
        names = [ent.name] + [sp["name"] for sp in suppliers]
        events = s.scalars(select(ExternalEvent).where(ExternalEvent.enterprise_name.in_(names), ExternalEvent.consumed.is_(False))).all()
        pending = [(ev.id, ev.enterprise_name, ev.title, ev.content, ev.source) for ev in events]
        for ev in events:
            ev.consumed = True
        lines = list((policy.coverage or {}).get("lines") or s.get(Product, policy.product_id).lines or [])
        ent_name = ent.name
    if not pending:
        return []
    liabilities = sorted({ind.get("liability") for ind in plan.indicators or [] if ind.get("liability")})
    out = []
    for ev_id, subject, title, content, source in pending:
        info = model_gateway.invoke("rw.extract_event", {"title": title, "content": content, "subject": subject, "insured": ent_name, "lines": lines, "liabilities": liabilities}, data_level=0)
        if info.get("negated") or info.get("event_type") == "无关" or not info.get("subtype"):
            continue
        sub, sev = info["subtype"], float(info.get("severity") or 0.5)
        if subject != ent_name:
            share = next((sp["share"] for sp in suppliers if sp["name"] == subject), 0.5)
            score = D.propagation_score(sev, share)
            out.append({"method": "DM5", "key": f"ext-{ev_id}", "strength": 0.7, "severity": min(0.95, score + 0.3), "risk_type": "R08-2", "liability": "研发中断责任（关键部件断供）", "asset": subject, "asset_name": subject, "metrics": [], "detail": f"关键供应商事件经依赖关系传导（依赖度 {share:.0%}，传导得分 {score}）：{title}", "event_id": ev_id})
            continue
        method = "TP4" if sub.startswith("R04") else ("TP5" if sub.startswith("R06") else "DM4")
        out.append({"method": method, "key": f"ext-{ev_id}", "strength": 0.65, "severity": sev, "risk_type": sub, "liability": SUBTYPES.get(sub, {}).get("name", info.get("event_type")), "asset": ent_name, "asset_name": ent_name, "metrics": [], "detail": f"{'舆情事件抽取' if method == 'DM4' else ('知识产权状态检测' if method == 'TP4' else '产品质量信号')}：{title}（{info.get('summary', '')}）", "event_id": ev_id, "relevance": info.get("relevance")})
    return out


def tick_plan(plan_id: int, hours: int) -> list[dict]:
    with session_scope() as s:
        plan = s.get(MonitorPlan, plan_id)
        if plan is None or plan.status != "active":
            return []
        start = plan.state.get("hour", 0)
        generate(plan, start, start + hours, s)
        state = dict(plan.state)
        state["hour"] = start + hours
        events = []
        for ev in state.get("scheduled_events", []):
            if not ev.get("fired") and ev["at_hour"] <= state["hour"]:
                s.add(ExternalEvent(enterprise_name=ev["enterprise"], source=ev["source"], title=ev["title"], content=ev["content"], published_at=sim_time(ev["at_hour"], state)))
                ev = {**ev, "fired": True}
            events.append(ev)
        state["scheduled_events"] = events
        state["last_tick_at"] = utcnow().isoformat() + "Z"
        plan.state = state
        s.flush()
        s.expunge(plan)
    signals = detect(plan)
    _record_detection(plan_id, signals)
    return route_signals(plan_id, signals)


def _record_detection(plan_id: int, signals: list[dict]) -> None:
    with session_scope() as s:
        plan = s.get(MonitorPlan, plan_id)
        state = dict(plan.state)
        log_rows = list(state.get("detections", []))
        for sig in signals:
            log_rows.append({"hour": state.get("hour", 0), "method": sig["method"], "key": sig["key"], "risk_type": sig["risk_type"], "strength": sig["strength"], "severity": sig["severity"], "detail": sig["detail"][:160]})
        state["detections"] = log_rows[-300:]
        plan.state = state


def route_signals(plan_id: int, signals: list[dict]) -> list[dict]:
    """信号归并（同一对象、相关风险类型）→ 融合置信度 → 预定级 → 抑制（静默期、蓝色只入周报）→ 发起研判运行。"""
    if not signals:
        return []
    with session_scope() as s:
        plan = s.get(MonitorPlan, plan_id)
        state = dict(plan.state)
        last = dict(state.get("last_alerts", {}))
        hour = state.get("hour", 0)
        groups: dict[str, list[dict]] = {}
        for sig in signals:
            groups.setdefault(f"{sig['asset']}|{sig['risk_type'][:3]}", []).append(sig)
        launched, suppressed = [], list(state.get("suppressed", []))
        rank = ["blue", "yellow", "orange", "red"]
        for key, sigs in groups.items():
            fused = D.fuse(sigs, state.get("fpr", {}))
            level = D.grade(fused["severity"], fused["confidence"], True, any(x.get("incident") for x in sigs))
            prev = last.get(key)
            silence = state.get("silence_hours", 24)
            incident = any(x.get("incident") for x in sigs)
            if prev and hour - prev["hour"] < silence and rank.index(level) <= rank.index(prev["level"]) and not (incident and not prev.get("incident")):
                suppressed.append({"hour": hour, "key": key, "level": level, "reason": f"静默期内同类事件（{silence} 小时）", "methods": fused.get("methods")})
                continue
            if level == "blue":
                state.setdefault("blue_digest", []).append({"hour": hour, "key": key, "detail": sigs[0]["detail"]})
                continue
            last[key] = {"hour": hour, "level": level, "incident": incident or bool((prev or {}).get("incident"))}
            launched.append({"key": key, "signals": sigs, "fused": fused, "level_hint": level, "hour": hour, "sim_time": sim_time(hour, state).isoformat(), "created_at": utcnow().isoformat() + "Z"})
        from .config_center import cfg

        cap = int(cfg("rw.templates").get("storm_cap") or 5)
        if len(launched) > cap:
            state.setdefault("storm", []).append({"hour": hour, "count": len(launched)})
            launched = sorted(launched, key=lambda c: -rank.index(c["level_hint"]))[:cap]
        state["last_alerts"] = last
        state["blue_digest"] = state.get("blue_digest", [])[-50:]
        state["suppressed"] = suppressed[-100:]
        plan.state = state
        policy_id = plan.policy_id
    for cand in launched:
        executor.create_run(agent_id="AG-RW", purpose="alert", inputs={"policy_id": policy_id, "plan_id": plan_id, "candidate": cand}, business_ref={"policy_id": policy_id, "plan_id": plan_id}, mode="auto", title=f"预警研判：{cand['signals'][0]['asset_name']}")
    return launched


def register_plan(policy_id: int, indicators: list[dict], *, run_id: int | None, scenario: dict, scheduled_events: list[dict] | None = None, backfill_hours: int = DEFAULT_BACKFILL_HOURS) -> int:
    """发布监测方案并注册到常驻监测：写入方案、回填方案发布前的历史数据窗口作为基线。"""
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == policy_id)).first()
        sim_start = (utcnow() - timedelta(hours=backfill_hours)).replace(minute=0, second=0, microsecond=0)
        from .config_center import cfg

        state = {"hour": 0, "sim_start": sim_start.isoformat(), "scenario": scenario, "fpr": {}, "silence_hours": int(cfg("rw.templates").get("silence_hours") or 24), "maintenance_windows": [], "scheduled_events": scheduled_events or [], "threshold_changes": [], "feedback": []}
        if plan is None:
            plan = MonitorPlan(policy_id=policy_id, indicators=indicators, state=state, status="active", run_id=run_id)
            s.add(plan)
        else:
            old = dict(plan.state or {})
            state.update({k: old[k] for k in ("hour", "sim_start", "fpr", "maintenance_windows", "threshold_changes", "feedback", "last_alerts") if k in old})
            plan.indicators, plan.state, plan.status, plan.run_id = indicators, state, "active", run_id
        s.flush()
        plan_id = plan.id
        existing = s.scalar(select(func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == policy_id)) or 0
        if not existing and backfill_hours:
            generate(plan, 0, backfill_hours, s)
            st = dict(plan.state)
            st["hour"] = backfill_hours
            plan.state = st
    return plan_id


def advance(plan_id: int, hours: int) -> list[dict]:
    """推进仿真时钟（演示与回放用）：逐小时生成数据并检测，返回发起的研判候选。"""
    launched = []
    for _ in range(max(1, hours)):
        launched += tick_plan(plan_id, 1)
    return launched


def record_feedback(alert_id: int, outcome: str, reason: str, user_id: int | None) -> dict:
    """处置反馈回流：误报标记更新对应检测方法的误报率（贝塔后验均值），有效预警提高方法可信度。"""
    with session_scope() as s:
        alert = s.get(Alert, alert_id)
        if alert is None:
            raise ValueError("预警不存在")
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == alert.policy_id)).first()
        if plan is None:
            return {}
        state = dict(plan.state)
        fb = list(state.get("feedback", []))
        fb.append({"alert_id": alert_id, "methods": alert.methods, "outcome": outcome, "reason": reason, "by": user_id, "at": utcnow().isoformat() + "Z"})
        state["feedback"] = fb[-200:]
        fpr = dict(state.get("fpr", {}))
        for m in alert.methods or []:
            rows = [f for f in fb if m in (f.get("methods") or []) and f["outcome"] in ("valid", "false_positive", "incident")]
            fp = sum(1 for f in rows if f["outcome"] == "false_positive")
            fpr[m] = round((fp + 1) / (len(rows) + 1 / METHOD_DEFAULT_FPR), 4)
        state["fpr"] = fpr
        plan.state = state
        return fpr


def set_threshold(plan_id: int, key: str, value: float, reason: str, user_id: int | None) -> dict:
    with session_scope() as s:
        plan = s.get(MonitorPlan, plan_id)
        inds = [dict(i) for i in plan.indicators]
        changed = None
        for ind in inds:
            if ind.get("key") == key:
                changed = {"key": key, "name": ind["name"], "before": ind.get("threshold"), "after": value, "reason": reason, "by": user_id, "at": utcnow().isoformat() + "Z"}
                ind["threshold"] = value
                ind["threshold_basis"] = f"人工调整：{reason}"
        if changed is None:
            raise ValueError("监测方案中没有该指标")
        plan.indicators = inds
        state = dict(plan.state)
        state["threshold_changes"] = list(state.get("threshold_changes", [])) + [changed]
        plan.state = state
    return changed


def add_maintenance_window(plan_id: int, asset: str, start_hour: int, end_hour: int, note: str) -> dict:
    with session_scope() as s:
        plan = s.get(MonitorPlan, plan_id)
        state = dict(plan.state)
        win = {"asset": asset, "start_h": start_hour, "end_h": end_hour, "note": note}
        state["maintenance_windows"] = list(state.get("maintenance_windows", [])) + [win]
        plan.state = state
    return win


class MonitorScheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread or not settings.monitor_enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        hours = max(1, settings.simulated_minutes_per_tick // 60)
        while not self._stop.wait(settings.monitor_tick_seconds):
            try:
                hitl.expire_overdue()
                with session_scope() as s:
                    ids = [p.id for p in s.scalars(select(MonitorPlan).where(MonitorPlan.status == "active")).all()]
                for pid in ids:
                    tick_plan(pid, hours)
            except Exception:  # noqa: BLE001
                log.exception("monitor tick failed")
            time.sleep(0)


monitor_scheduler = MonitorScheduler()
