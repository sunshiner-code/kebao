"""风险预警智能体的工具：监测方案生成、预警研判、事故响应、风险管理报告。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select

from ...analytics import detection as D
from ...db import session_scope, utcnow
from ...harness.tools import ToolError, ToolResult, registry
from ...knowledge.catalog import (
    COMMON_EXTERNAL_MONITORS,
    DETECTION_METHODS,
    INCIDENT_TYPES,
    KRI_LIBRARY,
    LINES,
    family_of,
    has_family,
    PLAYBOOKS,
    REGION_OF_PROVINCE,
    SERVICE_PROVIDERS,
    SUB_PHASES,
    SUBTYPES,
)
from ...llm.gateway import model_gateway
from ...models import Alert, Artifact, ExternalEvent, Incident, MonitorPlan, Notification, PriceItem, TelemetryPoint
from ...services import monitoring as M
from ...textutil import format_amount
from ..common import create_artifact, load_policy_bundle, policy_doc_params

LEVEL_LABELS = {"red": "红色", "orange": "橙色", "yellow": "黄色", "blue": "蓝色"}
LEVEL_SLA_HOURS = {"red": 1, "orange": 4, "yellow": 24, "blue": 24 * 7}


def _sla_hours(level: str) -> float:
    from ...services.config_center import cfg

    return float(cfg("rw.templates")["sla_hours"].get(level, LEVEL_SLA_HOURS[level]))
LEVEL_CHANNELS = {"red": ["站内", "短信", "即时通讯"], "orange": ["站内", "即时通讯"], "yellow": ["站内"], "blue": ["周报"]}
RANK = ["blue", "yellow", "orange", "red"]
FREQ = {"iot": "每小时", "log": "每小时", "ops": "每日", "news": "每小时", "court": "每日", "supplier": "每日", "patent": "每日", "vuln": "每小时"}
METRIC_NAMES = {"vibration_rms": "振动有效值", "bearing_temp": "轴承温度", "motor_current": "运行电流", "water_leak": "环境水浸", "login_failures": "登录失败次数", "egress_mb": "数据外传量", "unpatched_critical": "未修补高危漏洞", "milestone_delay_days": "里程碑延误天数", "rd_spend_index": "研发费用指数"}


def _bundle(ctx) -> dict:
    if "bundle" not in ctx.workspace:
        ctx.workspace["bundle"] = load_policy_bundle(ctx.inputs["policy_id"])
    return ctx.workspace["bundle"]


def _authorizations(bundle: dict) -> dict:
    cov = bundle["policy"]["coverage"]
    declared = (bundle.get("application") or {}).get("declared") or {}
    auth = cov.get("data_authorizations") or declared.get("authorizations") or {}
    return {"iot": bool(auth.get("iot", True)), "log": bool(auth.get("log", True)), "ops": bool(auth.get("ops", True))}


def _uw_report(bundle: dict) -> dict | None:
    key = (bundle.get("application") or {}).get("report_artifact_key")
    if not key:
        return None
    with session_scope() as s:
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == key).order_by(Artifact.version.desc())).first()
        return dict(art.data) if art else None


def _risk_report(ent_id: int) -> dict | None:
    with session_scope() as s:
        art = s.scalars(select(Artifact).where(Artifact.kind == "risk_report", Artifact.ref_id == ent_id, Artifact.status == "frozen").order_by(Artifact.created_at.desc())).first()
        return {"artifact_key": art.artifact_key, "version": art.version, "risks": art.data.get("risks", [])} if art else None


def _plan_row(plan_id: int) -> dict:
    with session_scope() as s:
        p = s.get(MonitorPlan, plan_id)
        return {"id": p.id, "policy_id": p.policy_id, "indicators": p.indicators, "state": p.state, "status": p.status}


# ============================================================== 监测方案生成
@registry.tool("load_policy_context", "读取保单与监测输入", "读取保单、产品条款与监测要点、核保报告监测要点、风险评估中高风险与企业授权状态", permission="T0", zone="platform")
def load_policy_context(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    ent = b["enterprise"]
    uw = _uw_report(b)
    rr = _risk_report(ent["id"])
    risks = [r for r in (rr or {}).get("risks", []) if r.get("level") in ("中", "高", "极高")]
    uw_points = []
    if uw:
        for c in (uw.get("decision") or {}).get("conditions", []) or []:
            uw_points.append({"text": c["text"], "kind": "condition"})
        for c in uw.get("consistency", []) or []:
            if c["status"] == "不一致":
                uw_points.append({"text": f"{c['item']}：{c['note']}", "kind": "inconsistency"})
        for f in ((uw.get("background") or {}).get("concerns") or []):
            uw_points.append({"text": f, "kind": "concern"})
    unpatched = 0
    if uw:
        for c in uw.get("consistency", []) or []:
            if c["item"] == "安全测评报告":
                m = re.search(r"(\d+)\s*个未修补", c.get("note") or "")
                unpatched = int(m.group(1)) if m else 0
    ctx.workspace.update({"uw_points": uw_points, "risks": risks, "risk_report_ref": {k: rr[k] for k in ("artifact_key", "version")} if rr else None, "authorizations": _authorizations(b), "unpatched": unpatched})
    auth = ctx.workspace["authorizations"]
    return ToolResult(
        summary=f"保单 {b['policy']['policy_no']}（{b['product']['name']}，险种 {'、'.join(LINES[l]['name'] for l in b['product']['lines'] if l in LINES)}）；核保监测要点 {len(uw_points)} 条；中高风险 {len(risks)} 条；数据授权：物联网{'已' if auth['iot'] else '未'}授权、日志{'已' if auth['log'] else '未'}授权、运营数据{'已' if auth['ops'] else '未'}授权",
        data={"policy": b["policy"]["policy_no"], "risks": len(risks), "uw_points": len(uw_points)},
        level=2,
    )


def _peer_threshold(metric: str, policy_id: int, fallback: float) -> tuple[float, str]:
    """冷启动阈值：同行业在保企业该指标的 99 分位（无同业数据时用通用模板）。"""
    with session_scope() as s:
        vals = [v for (v,) in s.execute(select(TelemetryPoint.value).where(TelemetryPoint.metric == metric, TelemetryPoint.policy_id != policy_id).limit(20000)).all()]
    if len(vals) >= 500:
        p99 = float(np.quantile(vals, 0.99))
        return round(min(fallback, max(p99 * 1.15, fallback * 0.7)), 2), f"通用模板 {fallback} 与同业 99 分位 {p99:.2f} 的较小者（上浮 15%）"
    return fallback, "通用阈值模板（同业数据不足）"


def _calibrated_threshold(metric: str, base: float, asset: dict | None) -> tuple[float, str]:
    """热启动阈值：以关键风险指标库的校准阈值为基准，按设备役龄修正（役龄越长，报警线越早）。"""
    if asset and asset.get("purchase_date"):
        age = max(0.0, (datetime(2026, 11, 1) - datetime.fromisoformat(asset["purchase_date"])).days / 365)
        factor = max(0.85, 1 - 0.02 * max(0.0, age - 3))
        return round(base * factor, 2), f"校准阈值 {base} × 役龄修正 {factor:.2f}（役龄 {age:.1f} 年）"
    return base, "按历史预警与事故数据校准的阈值"


def _levels(threshold: float, direction: str) -> dict:
    if direction == "down":
        return {"yellow": round(threshold * 1.15, 3), "orange": round(threshold, 3), "red": round(threshold * 0.8, 3)}
    return {"yellow": round(threshold * 0.85, 3), "orange": round(threshold, 3), "red": round(threshold * 1.2, 3)}


@registry.tool("generate_indicators", "生成监测指标清单", "按产品监测模板、核保监测要点与中高风险生成关键风险指标，匹配检测方法、频率、阈值与等级映射、关联保险责任", permission="T1", zone="private", max_level=2)
def generate_indicators(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    ent = b["enterprise"]
    lines = b["product"]["lines"]
    eq = sorted(ent["profile"].get("equipment", []), key=lambda e: -e["value"])[:2]
    cold = ctx.state.get("start_mode") == "cold"
    inds: list[dict] = []
    seen = set()

    def add(ind: dict) -> None:
        sig = (ind["key"], tuple(ind.get("assets", [])))
        if sig in seen:
            return
        seen.add(sig)
        ind["id"] = f"KRI-{len(inds) + 1:02d}"
        inds.append(ind)

    from ...services.config_center import cfg, config_center

    templates = cfg("rw.templates")["lines"]
    tpl_version = config_center.version("rw.templates")
    for line in lines:
        lib = {k["key"]: k for k in KRI_LIBRARY.get(line, [])}
        for t in templates.get(line, KRI_LIBRARY.get(line, [])):
            if not t.get("enabled", True):
                continue
            k = {**lib.get(t["key"], {}), **t}
            k.setdefault("liability", f"{LINES.get(line, {}).get('name', line)}保险责任")
            assets = [e["serial"] for e in eq] if k["source"] == "iot" else (["核心业务系统"] if k["source"] == "log" else ["研发项目"] if k["source"] == "ops" else [])
            if k["source"] == "iot" and not eq:
                continue
            if cold:
                thr, basis = _peer_threshold(k["key"], b["policy"]["id"], float(k["threshold"]))
            else:
                thr, basis = _calibrated_threshold(k["key"], float(k["threshold"]), eq[0] if k["source"] == "iot" and eq else None)
            add({"key": k["key"], "name": k["name"], "source": k["source"], "unit": k["unit"], "assets": assets, "asset_names": [e["name"] for e in eq] if k["source"] == "iot" else assets, "methods": list(k["methods"]), "frequency": k.get("frequency") or FREQ.get(k["source"], "每日"), "threshold": thr, "direction": k["direction"], "threshold_basis": basis + f"（预警模板 V{tpl_version}）", "levels": _levels(thr, k["direction"]), "liability": k["liability"], "line": line, "origin": "template", "subtypes": [s for s, v in SUBTYPES.items() if line in v["lines"]][:6], "status": "active"})
    for p in ctx.workspace.get("uw_points", []):
        cyber_line = next((line for line in lines if family_of(line) == "cyber"), None)
        equipment_line = next((line for line in lines if family_of(line) == "equipment"), None)
        if "漏洞" in p["text"] and cyber_line:
            add({"key": "unpatched_days", "name": "高危漏洞未修补天数", "source": "log", "unit": "天", "assets": ["核心业务系统"], "asset_names": ["核心业务系统"], "methods": ["TP2", "DM1"], "frequency": "每日", "threshold": 30, "direction": "up", "threshold_basis": "核保监测要点：漏洞修补时限", "levels": _levels(30, "up"), "liability": "网络安全损失", "line": cyber_line, "origin": "uw", "subtypes": ["R07-2"], "status": "active", "note": p["text"]})
        elif "运行状态报告" in p["text"] and equipment_line:
            add({"key": "status_report_overdue", "name": "设备运行状态报告逾期天数", "source": "ops", "unit": "天", "assets": ["研发项目"], "asset_names": ["设备管理"], "methods": ["DM1"], "frequency": "每日", "threshold": 7, "direction": "up", "threshold_basis": "核保附加条件：每季度提交运行状态报告", "levels": _levels(7, "up"), "liability": "设备物质损失", "line": equipment_line, "origin": "uw", "subtypes": ["R02-2"], "status": "active", "note": p["text"]})
        elif "现金" in p["text"] or "亏损" in p["text"]:
            add({"key": "cash_months", "name": "现金可支撑月数", "source": "ops", "unit": "月", "assets": ["财务"], "asset_names": ["财务"], "methods": ["DM1", "DM3"], "frequency": "每月", "threshold": 9, "direction": "down", "threshold_basis": "核保关注点：现金流紧张", "levels": _levels(9, "down"), "liability": "风险管理（无直接保险责任）", "line": "通用", "origin": "uw", "subtypes": ["R11-2"], "status": "active", "note": p["text"]})
    for r in ctx.workspace.get("risks", []):
        if r.get("level") not in ("高", "极高"):
            continue
        sub = r.get("subtype", "")
        rd_line = next((line for line in lines if family_of(line) in ("rd_cost", "transfer")), "通用")
        ip_line = next((line for line in lines if family_of(line) in ("ip", "guarantee")), "通用")
        liability_line = next((line for line in lines if family_of(line) in ("liability", "quality", "life")), "通用")
        if sub in ("R01-3", "R01-7", "R03-2") or sub.startswith("R13"):
            for sp in ent["profile"].get("suppliers", []):
                if sp["share"] >= 0.3:
                    add({"key": f"supplier:{sp['name']}", "name": f"关键合作方事件：{sp['name']}（{sp['item']}）", "source": "supplier", "unit": "事件", "assets": [sp["name"]], "asset_names": [sp["name"]], "methods": ["DM5", "DM4"], "frequency": "每日", "threshold": 0.35, "direction": "up", "threshold_basis": f"传导得分阈值（依赖度 {sp['share']:.0%}）", "levels": {"yellow": 0.25, "orange": 0.35, "red": 0.6}, "liability": "研发或中试中断", "line": rd_line, "origin": "risk", "subtypes": [sub], "status": "active"})
        elif sub.startswith("R05") or sub.startswith("R06"):
            add({"key": f"ip:{sub}", "name": f"知识产权状态：{r['title']}", "source": "patent", "unit": "事件", "assets": [ent["name"]], "asset_names": [ent["name"]], "methods": ["TP4", "DM4"], "frequency": "每日", "threshold": 0.5, "direction": "up", "threshold_basis": "事件严重度阈值", "levels": {"yellow": 0.4, "orange": 0.5, "red": 0.8}, "liability": "知识产权责任" if ip_line != "通用" else "无直接关联", "line": ip_line, "origin": "risk", "subtypes": [sub], "status": "active"})
        elif sub.startswith(("R04", "R08", "R09")):
            add({"key": f"quality:{sub}", "name": f"产品质量与责任信号：{r['title']}", "source": "news", "unit": "事件", "assets": [ent["name"]], "asset_names": [ent["name"]], "methods": ["TP5", "DM4"], "frequency": "每小时", "threshold": 0.5, "direction": "up", "threshold_basis": "事件严重度阈值", "levels": {"yellow": 0.4, "orange": 0.5, "red": 0.8}, "liability": "产品或质量责任" if liability_line != "通用" else "无直接关联", "line": liability_line, "origin": "risk", "subtypes": [sub], "status": "active"})
        else:
            add({"key": f"risk:{sub}", "name": f"{r['title']}相关事件", "source": "news", "unit": "事件", "assets": [ent["name"]], "asset_names": [ent["name"]], "methods": ["DM4"], "frequency": "每小时", "threshold": 0.5, "direction": "up", "threshold_basis": "事件严重度阈值", "levels": {"yellow": 0.4, "orange": 0.5, "red": 0.8}, "liability": "、".join(LINES[l]["name"] for l in SUBTYPES.get(sub, {}).get("lines", []) if l in lines) or "无直接关联", "line": "通用", "origin": "risk", "subtypes": [sub], "status": "active"})
    for ext in COMMON_EXTERNAL_MONITORS:
        add({"key": f"ext:{ext['key']}", "name": ext["name"], "source": ext["source"], "unit": "事件", "assets": [ent["name"]], "asset_names": [ent["name"]], "methods": list(ext["methods"]), "frequency": FREQ.get(ext["source"], "每日"), "threshold": 0.5, "direction": "up", "threshold_basis": "事件严重度阈值", "levels": {"yellow": 0.4, "orange": 0.5, "red": 0.8}, "liability": "全部责任（事件相关性研判）", "line": "全部", "origin": "external", "subtypes": [], "status": "active"})
    out = model_gateway.invoke("rw.plan_rationale", {"track": ent["track"], "lines": [LINES[l]["name"] for l in lines if l in LINES], "indicators": [{k: i.get(k) for k in ("id", "name", "methods", "liability", "origin", "subtypes")} for i in inds], "risks": [{"title": r["title"], "level": r["level"], "subtype": r["subtype"]} for r in ctx.workspace.get("risks", [])]}, ctx=ctx, data_level=1)
    for i in inds:
        i["rationale"] = out["rationales"].get(i["id"], "")
    ctx.workspace["indicators"] = inds
    ctx.workspace["plan_gaps"] = out["gaps"]
    tp = sorted({m for i in inds for m in i["methods"] if m.startswith("TP")})
    return ToolResult(
        summary=f"生成关键风险指标 {len(inds)} 项（企业数据 {sum(1 for i in inds if i['source'] in ('iot', 'log', 'ops'))} 项、外部数据 {sum(1 for i in inds if i['source'] not in ('iot', 'log', 'ops'))} 项）；检测方法 {'、'.join(sorted({m for i in inds for m in i['methods']}))}，其中专项检测 {'、'.join(tp) or '无'}；阈值依据：{'通用模板与同业分位（冷启动）' if cold else '校准阈值（热启动）'}" + (f"；覆盖缺口 {len(out['gaps'])} 条" if out["gaps"] else ""),
        data={"indicators": [i["id"] for i in inds]},
        level=1,
        metrics={"indicators": len(inds)},
    )


@registry.tool("check_data_authorization", "数据接入授权校验", "核对企业数据接入授权，未授权指标降级为外部数据监测，并检查每个险种的方法覆盖", permission="T0", zone="platform")
def check_data_authorization(ctx, **_) -> ToolResult:
    auth = ctx.workspace["authorizations"]
    inds = ctx.workspace["indicators"]
    degraded = []
    for i in inds:
        if i["source"] in ("iot", "log", "ops") and not auth.get(i["source"], True):
            i["status"] = "degraded"
            i["degraded_reason"] = f"{ {'iot': '物联网设备', 'log': '信息安全日志', 'ops': '研发与运营'}[i['source']]}数据未授权"
            i["fallback"] = {"iot": "所在区域灾害预警与设备事故舆情（DM4）", "log": "漏洞情报与勒索相关舆情（DM4）", "ops": "里程碑相关公告与人员变动舆情（DM4）"}[i["source"]]
            degraded.append(i["name"])
    coverage = []
    lines = _bundle(ctx)["product"]["lines"]
    for line in lines:
        specific = {m for i in inds if i["line"] == line and i["status"] == "active" for m in i["methods"]}
        general = {m for i in inds if i["line"] in ("全部", "通用") and i["status"] == "active" for m in i["methods"]}
        methods = sorted(specific | general)
        coverage.append({"line": line, "name": LINES.get(line, {}).get("name", line), "methods": methods, "specific": sorted(specific), "ok": len(methods) >= 2 and any(m.startswith("TP") for m in specific)})
    ctx.workspace["method_coverage"] = coverage
    bad = [c["name"] for c in coverage if not c["ok"]]
    return ToolResult(summary=f"授权校验完成：降级 {len(degraded)} 项" + (f"（{'、'.join(degraded)}）" if degraded else "") + "；险种方法覆盖：" + "；".join(f"{c['name']} {'+'.join(c['methods']) or '无'}" for c in coverage) + (f"；未满足「至少 2 种方法且含专项检测」：{'、'.join(bad)}" if bad else ""), data=coverage, level=1, metrics={"degraded": len(degraded)})


@registry.tool("supplement_indicators", "补充监测方法", "为未满足方法覆盖要求的险种补充科技产品专项检测或辅助方法", permission="T1", zone="private")
def supplement_indicators(ctx, **_) -> ToolResult:
    added = []
    ent = _bundle(ctx)["enterprise"]
    for c in ctx.workspace.get("method_coverage", []):
        if c["ok"]:
            continue
        line = c["line"]
        tp = LINES.get(line, {}).get("detect", "TP5")
        uploaded = tp in ("TP1", "TP2", "TP3")
        ind = {"id": f"KRI-{len(ctx.workspace['indicators']) + 1:02d}", "key": f"aux:{line}", "name": f"{LINES[line]['name']}专项检测：{DETECTION_METHODS[tp]}" + ("（客户定期上传的巡检、测评与台账）" if uploaded else ""), "source": "upload" if uploaded else "news", "unit": "份" if uploaded else "事件", "assets": [ent["name"]], "asset_names": [ent["name"]], "methods": [tp, "DM1" if uploaded else "DM4"], "frequency": "每月" if uploaded else "每日", "threshold": 0.5, "direction": "up", "threshold_basis": "上传材料要素抽取后按规则判定" if uploaded else "事件严重度阈值（补充方法）", "levels": {"yellow": 0.4, "orange": 0.5, "red": 0.8}, "liability": f"{LINES[line]['name']}保险责任", "line": line, "origin": "supplement", "subtypes": [s for s, v in SUBTYPES.items() if line in v["lines"]][:4], "status": "active", "rationale": f"为满足{LINES[line]['name']}至少两种方法且含专项检测的要求补充"}
        ctx.workspace["indicators"].append(ind)
        added.append(ind["name"])
    check_data_authorization(ctx)
    return ToolResult(summary=f"补充指标 {len(added)} 项" + (f"：{'、'.join(added)}" if added else ""), level=1)


# ============================================================== 预警研判
@registry.tool("load_alert_candidate", "读取候选事件", "从事件队列认领候选事件：读取信号、融合结果、监测方案状态与保单责任", permission="T0", zone="platform")
def load_alert_candidate(ctx, **_) -> ToolResult:
    cand = ctx.inputs["candidate"]
    b = _bundle(ctx)
    plan = _plan_row(ctx.inputs["plan_id"])
    ctx.workspace["candidate"] = cand
    ctx.workspace["plan"] = {"id": plan["id"], "hour": plan["state"].get("hour", 0), "fpr": plan["state"].get("fpr", {}), "maintenance_windows": plan["state"].get("maintenance_windows", []), "indicators": plan["indicators"]}
    sigs = cand["signals"]
    return ToolResult(summary=f"认领候选事件 {cand['key']}：{len(sigs)} 个信号（{'、'.join(sorted({s['method'] for s in sigs}))}），融合置信度 {cand['fused']['confidence']:.0%}、严重度 {cand['fused']['severity']:.2f}，预定级 {LEVEL_LABELS[cand['level_hint']]}；保单 {b['policy']['policy_no']}", data={"key": cand["key"]}, level=1)


@registry.tool("collect_signal_evidence", "整理触发依据", "为每个信号提取指标曲线窗口或事件原文，形成带编号的证据", permission="T0", zone="private", max_level=2)
def collect_signal_evidence(ctx, **_) -> ToolResult:
    cand = ctx.workspace["candidate"]
    end = datetime.fromisoformat(cand["sim_time"])
    evidence = []
    for sig in cand["signals"]:
        eid = f"EV-{len(evidence) + 1}"
        item = {"id": eid, "method": sig["method"], "method_name": DETECTION_METHODS.get(sig["method"], sig["method"]), "title": f"{sig['method']} {DETECTION_METHODS.get(sig['method'], '')}", "detail": sig["detail"], "kind": "metric" if sig.get("metrics") else "event", "strength": sig["strength"], "severity": sig["severity"]}
        if sig.get("metrics"):
            series = {}
            for metric in sig["metrics"]:
                ts, vals = M.series(ctx.inputs["policy_id"], metric, sig["asset"] if sig["asset"] != "研发项目" or metric.startswith(("milestone", "rd_")) else None, hours=96, end=end)
                if len(vals):
                    series[metric] = {"name": METRIC_NAMES.get(metric, metric), "ts": [t.isoformat() for t in ts], "values": [round(float(v), 3) for v in vals]}
            item["series"] = series
            item["source_ref"] = f"telemetry:{ctx.inputs['policy_id']}:{sig['asset']}"
            if sig.get("health"):
                item["health"] = sig["health"]
        if sig.get("event_id"):
            with session_scope() as s:
                ev = s.get(ExternalEvent, sig["event_id"])
                if ev:
                    item["event"] = {"title": ev.title, "content": ev.content, "source": ev.source, "published_at": ev.published_at.isoformat(), "subject": ev.enterprise_name}
            item["source_ref"] = f"event:{sig['event_id']}"
        evidence.append(item)
    ctx.workspace["evidence"] = evidence
    return ToolResult(summary=f"整理触发依据 {len(evidence)} 条：" + "；".join(f"{e['id']} {e['method_name']}" for e in evidence), data={"evidence": [e["id"] for e in evidence]}, level=2)


def _match_liabilities(bundle: dict, risk_type: str, signal_liability: str) -> tuple[str, list[str]]:
    lines = bundle["product"]["lines"]
    sub_lines = SUBTYPES.get(risk_type, {}).get("lines", [])
    titles = sorted({l["title"] for l in bundle["liabilities"] if set(l["lines"]) & set(sub_lines)})
    if set(sub_lines) & set(lines):
        return "直接相关", titles or [LINES[l]["name"] for l in sub_lines if l in lines]
    core = re.sub(r"[（(].*?[)）]|责任", "", signal_liability or "")
    loose = sorted({l["title"] for l in bundle["liabilities"] if core and (core[:2] in l["title"] or core[:2] in l["text"][:120])})
    if loose:
        return "间接相关", loose
    if risk_type.startswith("R02") and has_family(lines, "rd_cost"):
        return "间接相关", [LINES[line]["name"] for line in lines if family_of(line) == "rd_cost"]
    return "无直接关联", []


def _loss_range(bundle: dict, risk_type: str, asset: str, severity: float) -> tuple[list[float], str]:
    ent = bundle["enterprise"]
    f = ent["profile"].get("financials", {})
    params = {d["doc_id"]: d for d in policy_doc_params(bundle)}
    limit = max([float(d["params"].get("limit") or 0) for d in params.values()] or [bundle["policy"]["sum_insured"]]) or bundle["policy"]["sum_insured"]
    daily_rd = f.get("rd_expense", 0) / 365
    t = risk_type[:3]
    if t == "R02":
        eq = next((e for e in ent["profile"].get("equipment", []) if e["serial"] == asset), None)
        v = eq["value"] if eq else bundle["policy"]["sum_insured"] * 0.3
        lo, hi = v * (0.03 + 0.1 * severity), v * (0.1 + 0.4 * severity)
        basis = f"设备价值 {format_amount(v)} × 修复比例 {0.03 + 0.1 * severity:.0%}–{0.1 + 0.4 * severity:.0%}"
        if has_family(bundle["product"]["lines"], "rd_cost"):
            lo += daily_rd * 3
            hi += daily_rd * 20
            basis += f"；研发中断 3–20 天 × 日均研发费用 {format_amount(daily_rd)}"
    elif t == "R07":
        lo, hi = limit * 0.05 * severity, limit * 0.45 * severity
        basis = f"数据安全险限额 {format_amount(limit)} × 事件损失比例 {0.05 * severity:.0%}–{0.45 * severity:.0%}（取证、恢复、营业中断与第三者赔偿）"
    elif t == "R08":
        share = next((sp["share"] for sp in ent["profile"].get("suppliers", []) if sp["name"] == asset), 0.5)
        daily_gp = f.get("revenue", 0) * 0.3 / 365
        lo, hi = daily_gp * 7 * share, daily_gp * 45 * share
        basis = f"日均毛利 {format_amount(daily_gp)} × 断供 7–45 天 × 依赖度 {share:.0%}"
    elif t == "R01":
        monthly = f.get("monthly_fixed_cost", 0)
        lo, hi = monthly * 0.5, monthly * 3
        basis = f"月固定费用 {format_amount(monthly)} × 中断 0.5–3 个月"
    else:
        lo, hi = bundle["policy"]["sum_insured"] * 0.01 * severity, bundle["policy"]["sum_insured"] * 0.1 * severity
        basis = "保险金额 × 1%–10% × 严重度"
    return [round(min(lo, limit), -2), round(min(hi, limit), -2)], basis


@registry.tool("assess_alert_impact", "影响评估", "判断候选事件与保单保险责任的关联、潜在损失区间与对生命周期阶段的影响", permission="T0", zone="private", max_level=2)
def assess_alert_impact(ctx, **_) -> ToolResult:
    cand = ctx.workspace["candidate"]
    b = _bundle(ctx)
    sig = max(cand["signals"], key=lambda s: s["severity"])
    rt = sig["risk_type"]
    if rt == "DATA":
        impact = {"relation": "无直接关联", "liabilities": [], "loss_range": None, "loss_basis": "数据接入异常不直接产生损失，但监测能力下降", "lifecycle": "无", "risk_type": rt, "risk_name": "数据接入异常"}
    else:
        relation, titles = _match_liabilities(b, rt, sig.get("liability", ""))
        loss, basis = _loss_range(b, rt, sig["asset"], cand["fused"]["severity"])
        lifecycle = "无明显影响"
        ms = [m for m in b["enterprise"]["profile"].get("milestones", []) if m.get("status") != "已完成"]
        if rt[:3] in ("R01", "R08", "R02") and ms:
            lifecycle = f"可能推迟里程碑「{ms[0]['name']}」（计划 {ms[0]['planned']}）"
        eq = next((e for e in b["enterprise"]["profile"].get("equipment", []) if e["serial"] == sig["asset"]), None)
        if eq and "中试" in eq.get("location", ""):
            lifecycle = f"受影响设备位于{eq['location']}，可能影响中试验证（成果转化阶段）进度"
        impact = {"relation": relation, "liabilities": titles, "loss_range": loss, "loss_basis": basis, "lifecycle": lifecycle, "risk_type": rt, "risk_name": SUBTYPES.get(rt, {}).get("name", rt)}
    ctx.workspace["impact"] = impact
    return ToolResult(summary=f"{impact['risk_name']}：与保险责任{impact['relation']}" + (f"（{'、'.join(impact['liabilities'])}）" if impact["liabilities"] else "") + (f"；潜在损失 {format_amount(impact['loss_range'][0])}–{format_amount(impact['loss_range'][1])}" if impact["loss_range"] else "") + f"；生命周期影响：{impact['lifecycle']}", data=impact, level=2)


@registry.tool("grade_alert", "定级与抑制", "按定级矩阵确定等级；冷启动除规则明确的红色外降一档；检查维护窗口、静默期与重复事件", permission="T0", zone="private")
def grade_alert(ctx, **_) -> ToolResult:
    cand = ctx.workspace["candidate"]
    impact = ctx.workspace["impact"]
    fused = cand["fused"]
    incident = any(s.get("incident") for s in cand["signals"])
    related = impact["relation"] == "直接相关"
    level = D.grade(fused["severity"], fused["confidence"], related, incident)
    notes = [f"严重度 {fused['severity']:.2f}、置信度 {fused['confidence']:.0%}、{'直接相关' if related else impact['relation']}" + ("、判定为疑似事故" if incident else "")]
    if impact["relation"] == "间接相关" and level == "red" and not incident:
        level = "orange"
        notes.append("与保险责任间接相关，红色降为橙色")
    if ctx.state.get("start_mode") == "cold" and not incident and level != "blue":
        level = RANK[RANK.index(level) - 1]
        notes.append("冷启动：非规则明确的红色预警默认降一档发布")
    suppressed, merge_into = None, None
    hour = cand["hour"]
    asset = cand["signals"][0]["asset"]
    for w in ctx.workspace["plan"]["maintenance_windows"]:
        if w["asset"] in (asset, "全部") and w["start_h"] <= hour <= w["end_h"] and not incident:
            suppressed = f"处于维护窗口（{w['note']}）"
    for m in ctx.workspace.get("memory_hints", []):
        if "开机" in m and "振动" in m and cand["signals"][0]["risk_type"].startswith("R02") and not incident and fused["severity"] < 0.6:
            suppressed = suppressed or f"命中经验：{m}"
    with session_scope() as s:
        recent = s.scalars(select(Alert).where(Alert.policy_id == ctx.inputs["policy_id"], Alert.status.in_(["new", "processing"])).order_by(Alert.created_at.desc())).all()
        for a in recent:
            if (a.evidence or {}).get("group_key") == cand["key"] and hour - (a.evidence or {}).get("hour", -999) <= 72:
                merge_into = {"id": a.id, "alert_no": a.alert_no, "level": a.level}
                break
    if level == "blue" and not suppressed:
        suppressed = "蓝色预警纳入周报与月报，不单独推送"
    ctx.workspace["grading"] = {"level": level, "notes": notes, "incident": incident, "suppressed": suppressed, "merge_into": merge_into, "sla_hours": _sla_hours(level), "channels": LEVEL_CHANNELS[level]}
    return ToolResult(summary=f"定级：{LEVEL_LABELS[level]}（{'；'.join(notes)}）" + (f"；抑制：{suppressed}" if suppressed else "") + (f"；与在途预警 {merge_into['alert_no']} 合并" if merge_into else ""), data=ctx.workspace["grading"], level=1)


@registry.tool("analyze_alert", "研判与建议", "云端大模型依据证据生成事件解释、可能原因与建议动作（脱敏出域，私域回填），只保留有证据的陈述", permission="T0", zone="cloud", max_level=1, params={"strict": "为 1 时只保留引用了有效证据编号的陈述"})
def analyze_alert(ctx, strict: str = "0", **_) -> ToolResult:
    ev = ctx.workspace["evidence"]
    g = ctx.workspace["grading"]
    impact = ctx.workspace["impact"]
    cand = ctx.workspace["candidate"]
    payload = {
        "level": g["level"],
        "risk_type": impact["risk_type"],
        "risk_name": impact["risk_name"],
        "asset_name": cand["signals"][0]["asset_name"],
        "confidence": cand["fused"]["confidence"],
        "incident": g["incident"],
        "evidence": [{"id": e["id"], "kind": e["kind"], "title": e["title"], "detail": e["detail"]} for e in ev],
        "impact": {"relation": impact["relation"], "liabilities": impact["liabilities"], "loss_range": impact["loss_range"]},
        "playbook_hint": PLAYBOOKS[{"R07": "data_breach", "R02": "equipment_damage"}[impact["risk_type"][:3]]]["name"] if impact["risk_type"][:3] in ("R07", "R02") else None,
    }
    out = model_gateway.invoke("rw.alert_analysis", payload, ctx=ctx, data_level=1)
    valid = {e["id"] for e in ev}
    dropped = 0
    for key in ("causes", "statements", "actions"):
        items = []
        for it in out.get(key, []):
            refs = [r for r in it.get("evidence", []) if r in valid]
            if not refs and (strict == "1" or key != "actions"):
                dropped += 1
                continue
            items.append({**it, "evidence": refs or [ev[0]["id"]] if ev else []})
        out[key] = items
    ctx.workspace["analysis"] = out
    return ToolResult(summary=out["explanation"] + f"；原因 {len(out['causes'])} 条，建议动作 {len(out['actions'])} 条" + (f"；剔除无证据陈述 {dropped} 条" if dropped else ""), data=out, level=1)


def _alert_no(s) -> str:
    from sqlalchemy import func

    n = (s.scalar(select(func.count(Alert.id))) or 0) + 1
    return f"YJ{utcnow():%Y%m%d}{n:04d}"


@registry.tool("publish_alert", "发布预警", "写入预警事件并按等级推送；被抑制的信号写入抑制记录；与在途预警合并时追加证据或升级", permission="T1", zone="platform")
def publish_alert(ctx, **_) -> ToolResult:
    g = ctx.workspace["grading"]
    cand = ctx.workspace["candidate"]
    impact = ctx.workspace["impact"]
    an = ctx.workspace["analysis"]
    b = _bundle(ctx)
    sig0 = max(cand["signals"], key=lambda s: s["severity"])
    title = f"{sig0['asset_name']}：{impact['risk_name']}" + ("（疑似事故）" if g["incident"] else "")
    evidence = {"group_key": cand["key"], "hour": cand["hour"], "sim_time": cand["sim_time"], "signals": ctx.workspace["evidence"], "fused": cand["fused"], "impact": impact, "grading": {k: g[k] for k in ("notes", "channels", "sla_hours")}}
    if ctx.eval_mode:
        ctx.workspace["alert"] = None if g["suppressed"] else {"id": None, "alert_no": "评测演练", "level": g["level"], "created": False, "title": title}
        return ToolResult(summary="评测模式：" + (f"信号已抑制（{g['suppressed']}）" if g["suppressed"] else f"{LEVEL_LABELS[g['level']]}预警以演练方式发布：{title}") + "，未写入业务数据", data={"dry_run": True, "level": g["level"]}, level=1)
    created = None
    if g["suppressed"]:
        with session_scope() as s:
            plan = s.get(MonitorPlan, ctx.inputs["plan_id"])
            st = dict(plan.state)
            st["suppressed"] = list(st.get("suppressed", [])) + [{"hour": cand["hour"], "key": cand["key"], "level": g["level"], "reason": g["suppressed"], "methods": cand["fused"].get("methods")}]
            if g["level"] == "blue":
                st["blue_digest"] = list(st.get("blue_digest", [])) + [{"hour": cand["hour"], "key": cand["key"], "detail": an["explanation"]}]
            plan.state = st
        ctx.workspace["alert"] = None
        return ToolResult(summary=f"信号已抑制：{g['suppressed']}（写入抑制记录）", level=1)
    with session_scope() as s:
        if g["merge_into"]:
            a = s.get(Alert, g["merge_into"]["id"])
            ev = dict(a.evidence or {})
            ev["signals"] = list(ev.get("signals", [])) + ctx.workspace["evidence"]
            ev["merged"] = list(ev.get("merged", [])) + [{"hour": cand["hour"], "methods": cand["fused"].get("methods"), "level": g["level"]}]
            a.evidence = ev
            escalated = RANK.index(g["level"]) > RANK.index(a.level)
            if escalated:
                a.handling = list(a.handling or []) + [{"at": utcnow().isoformat() + "Z", "action": "升级", "note": f"由{LEVEL_LABELS[a.level]}升级为{LEVEL_LABELS[g['level']]}", "by": "AG-RW"}]
                a.level = g["level"]
                a.analysis = an
                a.title = title
                a.sla_due = utcnow() + timedelta(hours=g["sla_hours"])
            alert_id, alert_no, level = a.id, a.alert_no, a.level
            summary = f"信号并入在途预警 {alert_no}" + ("，等级升级为" + LEVEL_LABELS[level] if escalated else "")
        else:
            alert_no = _alert_no(s)
            a = Alert(alert_no=alert_no, policy_id=b["policy"]["id"], enterprise_id=b["enterprise"]["id"], level=g["level"], risk_type=impact["risk_type"], methods=sorted({s["method"] for s in cand["signals"]}), title=title, evidence=evidence, analysis=an, status="new", run_id=ctx.run_id, handling=[{"at": utcnow().isoformat() + "Z", "action": "发布", "note": f"推送渠道：{'、'.join(g['channels'])}", "by": "AG-RW"}], sla_due=utcnow() + timedelta(hours=g["sla_hours"]))
            s.add(a)
            s.flush()
            alert_id, level = a.id, a.level
            summary = f"发布{LEVEL_LABELS[level]}预警 {alert_no}：{title}，{g['sla_hours']} 小时内需处置，推送：{'、'.join(g['channels'])}"
            created = alert_id
        s.add(Notification(role="service", kind="alert", title=f"{LEVEL_LABELS[level]}预警：{title}", body=an["explanation"][:280], link=f"/monitoring/alerts/{alert_id}", level="error" if level == "red" else ("warning" if level == "orange" else "info")))
    ctx.workspace["alert"] = {"id": alert_id, "alert_no": alert_no, "level": level, "created": bool(created), "title": title}
    if ctx.inputs.get("candidate", {}).get("created_at"):
        started = datetime.fromisoformat(ctx.inputs["candidate"]["created_at"].rstrip("Z"))
        ctx.workspace["latency_s"] = (utcnow() - started).total_seconds()
    return ToolResult(summary=summary, data=ctx.workspace["alert"], level=1, metrics={"latency_s": round(ctx.workspace.get("latency_s", 0), 1)})


# ============================================================== 事故响应
@registry.tool("load_incident", "读取事故信息", "读取事故响应记录、关联预警、保单责任与企业信息", permission="T0", zone="platform")
def load_incident(ctx, **_) -> ToolResult:
    with session_scope() as s:
        inc = s.get(Incident, ctx.inputs["incident_id"])
        if inc is None:
            raise ToolError("事故记录不存在", retryable=False)
        alert = s.get(Alert, inc.alert_id) if inc.alert_id else None
        ctx.workspace["incident"] = {"id": inc.id, "incident_no": inc.incident_no, "type_hint": inc.incident_type, "occurred_at": inc.occurred_at.isoformat(), "description": inc.description, "alert_id": inc.alert_id}
        ctx.workspace["alert_ref"] = {"id": alert.id, "alert_no": alert.alert_no, "risk_type": alert.risk_type, "title": alert.title, "detail": (alert.analysis or {}).get("explanation", ""), "impact": (alert.evidence or {}).get("impact", {}), "asset": ((alert.evidence or {}).get("signals") or [{}])[0].get("detail", "")} if alert else None
        if not ctx.eval_mode:
            inc.status = "responding"
    b = _bundle(ctx)
    return ToolResult(summary=f"事故 {ctx.workspace['incident']['incident_no']}：{ctx.workspace['incident']['description'][:80]}；保单 {b['policy']['policy_no']}" + (f"；来源预警 {ctx.workspace['alert_ref']['alert_no']}" if ctx.workspace["alert_ref"] else "；来源：报案或手动发起"), level=2)


@registry.tool("triage_incident", "事故初判", "判定事故类型、可能涉及的保险责任与紧急程度", permission="T0", zone="follow", max_level=2)
def triage_incident(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    inc = ctx.workspace["incident"]
    al = ctx.workspace.get("alert_ref") or {}
    payload = {"description": inc["description"], "type_hint": inc["type_hint"], "alert_detail": al.get("detail", ""), "risk_type": al.get("risk_type", ""), "lines": b["product"]["lines"], "coverage_liabilities": sorted({l["title"] for l in b["liabilities"]})}
    out = model_gateway.invoke("rw.incident_triage", payload, ctx=ctx, data_level=2)
    if out["incident_type"] not in PLAYBOOKS:
        out["incident_type"] = "equipment_damage" if al.get("risk_type", "").startswith("R02") else "data_breach"
    ctx.workspace["triage"] = out
    with session_scope() as s:
        s.get(Incident, inc["id"]).incident_type = PLAYBOOKS[out["incident_type"]]["name"]
    return ToolResult(summary=out.get("summary") or f"初判为{PLAYBOOKS[out['incident_type']]['name']}", data=out, level=2)


def _price(name_kw: str, default: float) -> float:
    with session_scope() as s:
        for p in s.scalars(select(PriceItem)).all():
            if name_kw in p.name:
                return p.reference_price
    return default


@registry.tool("build_response_plan", "生成应急预案", "调取对应险种与事故类型的预案，结合企业情况生成带时限的步骤、量化的减损措施与证据保全清单", permission="T1", zone="private", max_level=2)
def build_response_plan(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    key = ctx.workspace["triage"]["incident_type"]
    pb = PLAYBOOKS[key]
    occurred = datetime.fromisoformat(ctx.workspace["incident"]["occurred_at"])
    f = b["enterprise"]["profile"].get("financials", {})
    al = ctx.workspace.get("alert_ref") or {}
    asset_name = ""
    if al:
        m = re.match(r"^(.+?)振动|^(.+?)健康", al.get("asset", ""))
        asset_name = (m.group(1) or m.group(2)) if m else ""
    steps = []
    for i, st in enumerate(pb["steps"], 1):
        title = st["title"] + (f"（{asset_name}）" if asset_name and i <= 2 and key == "equipment_damage" else "")
        steps.append({"no": i, "title": title, "deadline_hours": st["deadline_hours"], "due": (occurred + timedelta(hours=st["deadline_hours"])).isoformat(), "owner": st["owner"], "status": "pending"})
    daily_rd = f.get("rd_expense", 0) / 365
    hourly_rev = f.get("revenue", 0) / 365 / 24
    mitigation = []
    for m in pb["mitigation"]:
        q = None
        if "租赁" in m["measure"]:
            rent = _price("租赁", 68000)
            saved_days = 15
            q = {"saving": round(daily_rd * saved_days - rent * 0.5, -2), "basis": f"维修周期约 30 天，租赁后中断天数减少约 {saved_days} 天 × 日均研发费用 {format_amount(daily_rd)} − 半个月租金 {format_amount(rent * 0.5)}"}
        elif "备份恢复" in m["measure"]:
            q = {"saving": round(hourly_rev * 72 * 0.5, -2), "basis": f"预计中断 72 小时 × 小时营业收入 {format_amount(hourly_rev)} × 缩短 50%"}
        elif "合作实验室" in m["measure"]:
            q = {"saving": round(daily_rd * 0.3 * 20, -2), "basis": f"20 天中断期内约 30% 的实验可迁移 × 日均研发费用 {format_amount(daily_rd)}"}
        mitigation.append({**m, "quantified": q})
    methods = {"日志": "只读复制并计算哈希值，原始介质封存", "镜像": "制作只读镜像并计算哈希值", "照片": "多角度拍照录像，带时间水印", "视频": "多角度拍照录像，带时间水印", "物联网": "平台自动打包事故前后数据窗口", "报告": "由服务商出具后上传原件", "凭证": "保留原件并扫描上传", "样品": "封存并贴封条，双方签字", "公证": "公证机构现场保全", "记录": "导出系统记录并盖章"}
    evidence = []
    for item in pb["evidence"]:
        method = next((v for k, v in methods.items() if k in item), "收集原件并扫描上传")
        evidence.append({"item": item, "method": method, "deadline_hours": 24 if any(k in item for k in ("日志", "物联网", "照片", "现场", "镜像")) else 72, "status": "pending"})
    guidance = model_gateway.invoke("rw.response_guidance", {"enterprise": b["enterprise"]["name"], "incident_name": pb["name"], "steps": steps, "mitigation": [{"measure": m["measure"], "effect": m["effect"]} for m in mitigation], "evidence": evidence}, ctx=ctx, data_level=2)
    ctx.workspace["response"] = {"playbook": key, "playbook_name": pb["name"], "steps": steps, "mitigation": mitigation, "evidence": evidence, "guidance": guidance["guidance"], "customer_notice": guidance["customer_notice"]}
    saving = sum((m.get("quantified") or {}).get("saving", 0) for m in mitigation)
    return ToolResult(summary=f"应急预案「{pb['name']}」：步骤 {len(steps)} 项（首项时限 {steps[0]['deadline_hours']} 小时），减损措施 {len(mitigation)} 项（可量化减损约 {format_amount(saving)}），证据保全 {len(evidence)} 项", data=ctx.workspace["response"], level=2)


@registry.tool("recommend_providers", "服务商推荐", "按预案所需服务类别与企业所在区域推荐约定服务商", permission="T0", zone="cloud", max_level=1)
def recommend_providers(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    pb = PLAYBOOKS[ctx.workspace["triage"]["incident_type"]]
    region_city = next((c for c in REGION_OF_PROVINCE if c in b["enterprise"]["region"] or c in str(b["enterprise"]["profile"].get("address", ""))), None)
    region = REGION_OF_PROVINCE.get(region_city or "", "全国")
    picks = []
    for cat in pb["providers"]:
        cands = [p for p in SERVICE_PROVIDERS if p["category"] == cat and (region in p["regions"] or "全国" in p["regions"])]
        cands.sort(key=lambda p: (p["response_hours"], -p["rating"]))
        if cands:
            picks.append({**cands[0], "reason": f"{cat}；覆盖{region}；承诺 {cands[0]['response_hours']} 小时响应；评分 {cands[0]['rating']}"})
    ctx.workspace["providers"] = picks
    return ToolResult(summary="推荐服务商：" + "；".join(f"{p['name']}（{p['category']}，{p['response_hours']} 小时响应）" for p in picks), data=picks, level=0)


@registry.tool("package_data_window", "数据窗口打包", "打包事故前 30 天至事故后 7 天的物联网、日志、运营数据与预警记录，私域保存并生成引用", permission="T1", zone="private", max_level=3)
def package_data_window(ctx, **_) -> ToolResult:
    pid = ctx.inputs["policy_id"]
    occurred = datetime.fromisoformat(ctx.workspace["incident"]["occurred_at"])
    start, end = occurred - timedelta(days=30), occurred + timedelta(days=7)
    with session_scope() as s:
        rows = s.execute(select(TelemetryPoint.source, TelemetryPoint.asset, TelemetryPoint.metric, TelemetryPoint.ts, TelemetryPoint.value).where(TelemetryPoint.policy_id == pid, TelemetryPoint.ts >= start, TelemetryPoint.ts <= end).order_by(TelemetryPoint.ts)).all()
        alerts = s.scalars(select(Alert).where(Alert.policy_id == pid)).all()
        alert_rows = [{"alert_no": a.alert_no, "level": a.level, "title": a.title, "sim_time": (a.evidence or {}).get("sim_time"), "methods": a.methods, "status": a.status} for a in alerts]
    groups: dict[tuple, list] = {}
    for src, asset, metric, ts, val in rows:
        groups.setdefault((src, asset, metric), []).append((ts, val))
    summary = []
    data = {}
    for (src, asset, metric), pts in groups.items():
        vals = np.array([p[1] for p in pts])
        before = vals[[p[0] <= occurred for p in pts]]
        cp = D.cusum(vals[-min(len(vals), 240):]) if len(vals) >= 30 else {"change": False}
        summary.append({"source": src, "asset": asset, "metric": metric, "name": METRIC_NAMES.get(metric, metric), "points": len(pts), "from": pts[0][0].isoformat(), "to": pts[-1][0].isoformat(), "mean_before": round(float(before.mean()), 3) if len(before) else None, "max": round(float(vals.max()), 3), "last": round(float(vals[-1]), 3), "change_point": bool(cp.get("change"))})
        data[f"{src}|{asset}|{metric}"] = [[p[0].isoformat(), p[1]] for p in pts]
    art = create_artifact(kind="data_window", data={"incident_id": ctx.inputs["incident_id"], "window": {"from": start.isoformat(), "to": end.isoformat()}, "summary": summary, "alerts": alert_rows, "series": data}, title=f"事故数据窗口：{ctx.workspace['incident']['incident_no']}", run_id=ctx.run_id, ref_type="incident", ref_id=ctx.inputs["incident_id"], created_by=ctx.created_by, vault=ctx.vault)
    ref = {"artifact_key": art["artifact_key"], "version": art["version"], "from": start.isoformat(), "to": end.isoformat(), "points": len(rows), "series": len(summary), "alerts": len(alert_rows)}
    ctx.workspace["data_window"] = ref
    ctx.workspace["data_window_summary"] = summary
    return ToolResult(summary=f"数据窗口已打包（私域）：{start:%m-%d}至{end:%m-%d}，{len(summary)} 条序列、{len(rows)} 个数据点、预警记录 {len(alert_rows)} 条" + ("；未接入企业数据，仅含预警记录" if not rows else ""), data=ref, level=3, metrics={"points": len(rows)})


def _parties(bundle: dict, key: str) -> list[dict]:
    ent = bundle["enterprise"]
    parties = [{"name": ent["name"], "role": "被保险人", "relation": "投保人与被保险人"}]
    sups = ent["profile"].get("suppliers", [])
    if key == "equipment_damage":
        maint = next((sp for sp in sups if "维保" in sp["item"]), None)
        parties.append({"name": maint["name"] if maint else "设备维保服务商", "role": "维保服务商", "relation": "设备维保服务合同"})
        parties.append({"name": "设备原厂", "role": "设备供应商", "relation": "设备采购合同（质保期外）"})
    elif key == "data_breach":
        parties.append({"name": "云服务商", "role": "云服务商", "relation": "云主机与安全服务合同"})
        parties.append({"name": "外部攻击者", "role": "外部攻击者", "relation": "无"})
    return parties


@registry.tool("draft_pre_claim", "生成预报案草稿", "带入事故信息、数据窗口引用、处置记录与估损，生成理赔案件草稿", permission="T1", zone="platform", max_level=2)
def draft_pre_claim(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    inc = ctx.workspace["incident"]
    resp = ctx.workspace["response"]
    al = ctx.workspace.get("alert_ref") or {}
    loss = (al.get("impact") or {}).get("loss_range")
    if not loss:
        risk_type = {"equipment_damage": "R02-2", "data_breach": "R07-2", "lab_fire": "R02-1", "product_liability": "R06-1", "ip_infringement": "R04-1"}[resp["playbook"]]
        loss, _ = _loss_range(b, risk_type, "", 0.8)
    estimate = round((loss[0] + loss[1]) / 2, -2)
    draft = {
        "occurred_at": inc["occurred_at"],
        "incident_no": inc["incident_no"],
        "incident_type": resp["playbook"],
        "incident_type_name": resp["playbook_name"],
        "description": inc["description"],
        "liabilities": ctx.workspace["triage"]["liabilities"],
        "estimated_loss": estimate,
        "loss_range": loss,
        "data_window": ctx.workspace.get("data_window"),
        "alert": {k: al.get(k) for k in ("id", "alert_no", "title")} if al else None,
        "handling": [{"step": st["title"], "due": st["due"], "status": st["status"]} for st in resp["steps"]],
        "providers": [p["name"] for p in ctx.workspace.get("dispatched", [])],
        "parties": _parties(b, resp["playbook"]),
        "reporter": "风险预警智能体（预报案）",
    }
    ctx.workspace["pre_claim"] = draft
    with session_scope() as s:
        inc_row = s.get(Incident, inc["id"])
        inc_row.response = {**(inc_row.response or {}), **{k: ctx.workspace.get(k) for k in ("triage", "response", "providers", "dispatched", "data_window")}, "pre_claim": draft}
    return ToolResult(summary=f"预报案草稿：{resp['playbook_name']}，估损 {format_amount(estimate)}（区间 {format_amount(loss[0])}–{format_amount(loss[1])}），参与方 {len(draft['parties'])} 个，附数据窗口引用", data=draft, level=2)


# ============================================================== 风险管理报告
@registry.tool("load_report_scope", "读取报告范围", "读取保单、监测方案与报告周期", permission="T0", zone="platform")
def load_report_scope(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == b["policy"]["id"])).first()
        if plan is None:
            raise ToolError("该保单尚未发布监测方案", retryable=False)
        plan_id, hour, inds, pstate = plan.id, plan.state.get("hour", 0), plan.indicators, dict(plan.state)
    period = ctx.inputs.get("period", "month")
    hours = {"week": 24 * 7, "month": 24 * 30, "quarter": 24 * 90, "renewal": hour}.get(period, 24 * 30)
    start_h = max(0, hour - hours)
    ctx.workspace["scope"] = {"plan_id": plan_id, "period": period, "period_label": {"week": "周报", "month": "月度报告", "quarter": "季度报告", "renewal": "续保风险摘要"}.get(period, "月度报告"), "start_h": start_h, "end_h": hour, "start": M.sim_time(start_h, pstate).isoformat(), "end": M.sim_time(hour, pstate).isoformat(), "sim_start": M.t0(pstate).isoformat()}
    ctx.workspace["indicators"] = inds
    return ToolResult(summary=f"{ctx.workspace['scope']['period_label']}：{M.sim_time(start_h, pstate):%Y-%m-%d} 至 {M.sim_time(hour, pstate):%Y-%m-%d}，监测指标 {len(inds)} 项", level=1)


@registry.tool("compute_risk_statistics", "风险统计", "私域统计指标趋势与异常、预警与处置、事故、风险指数序列、生命周期变化，生成建议候选", permission="T0", zone="private", max_level=2)
def compute_risk_statistics(ctx, **_) -> ToolResult:
    b = _bundle(ctx)
    sc = ctx.workspace["scope"]
    pstate = {"sim_start": sc["sim_start"]}
    start, end = M.sim_time(sc["start_h"], pstate), M.sim_time(sc["end_h"], pstate)
    pid = b["policy"]["id"]
    inds_out = []
    health_min = 100.0
    for ind in ctx.workspace["indicators"]:
        if ind["source"] not in ("iot", "log", "ops") or ind.get("status") == "degraded":
            continue
        metric = ind["key"]
        for asset, aname in zip(ind.get("assets") or [None], ind.get("asset_names") or [""]):
            ts, vals = M.series(pid, metric, asset, start=start, end=end)
            if not len(vals):
                inds_out.append({"id": ind["id"], "name": ind["name"], "asset": aname, "points": 0})
                continue
            slope = float(np.polyfit(np.arange(len(vals)), vals, 1)[0]) * (24 if ind["source"] != "ops" else 1) if len(vals) > 3 else 0.0
            ra = D.robust_anomalies(vals) if len(vals) >= 72 else {"anomalies": []}
            thr = ind.get("threshold")
            breach = float(((vals > thr) if ind.get("direction") != "down" else (vals < thr)).mean()) if thr is not None else 0.0
            inds_out.append({"id": ind["id"], "name": ind["name"], "asset": aname, "points": len(vals), "mean": round(float(vals.mean()), 3), "p95": round(float(np.quantile(vals, 0.95)), 3), "last": round(float(vals[-1]), 3), "slope_per_day": round(slope, 3), "anomalies": len(ra.get("anomalies", [])), "threshold": thr, "breach_share": round(breach, 3), "series": [[t.isoformat(), round(float(v), 3)] for t, v in list(zip(ts, vals))[:: max(1, len(vals) // 120)]]})
            if metric == "vibration_rms":
                _, temp = M.series(pid, "bearing_temp", asset, start=start, end=end)
                if len(temp) == len(vals) and len(vals) >= 24:
                    hi = D.health_index(vals, temp)
                    if hi.get("index") is not None:
                        health_min = min(health_min, hi["index"])
    with session_scope() as s:
        alerts = s.scalars(select(Alert).where(Alert.policy_id == pid).order_by(Alert.created_at)).all()
        incidents = s.scalars(select(Incident).where(Incident.policy_id == pid)).all()
        a_rows = []
        for a in alerts:
            h = (a.evidence or {}).get("hour", 0)
            if not (sc["start_h"] <= h <= sc["end_h"]):
                continue
            handled = [x for x in (a.handling or []) if x.get("action") not in ("发布", "升级")]
            on_time = bool(handled) and a.sla_due is not None and datetime.fromisoformat(handled[0]["at"].rstrip("Z")) <= a.sla_due
            a_rows.append({"alert_no": a.alert_no, "level": a.level, "level_label": LEVEL_LABELS[a.level], "title": a.title, "risk_type": a.risk_type, "status": a.status, "status_label": {"new": "待处置", "processing": "处置中", "closed": "已关闭", "false_positive": "误报", "escalated": "已升级为事故"}.get(a.status, a.status), "hour": h, "on_time": on_time, "handled": bool(handled)})
        n_inc = len([i for i in incidents if start <= i.occurred_at <= end + timedelta(days=1)])
    by_level = {lv: sum(1 for a in a_rows if a["level"] == lv) for lv in ("red", "orange", "yellow", "blue")}
    handled = [a for a in a_rows if a["handled"]]
    sla_rate = sum(1 for a in handled if a["on_time"]) / len(handled) if handled else 1.0
    fp = sum(1 for a in a_rows if a["status"] == "false_positive")
    closed = sum(1 for a in a_rows if a["status"] in ("closed", "false_positive", "escalated"))
    # 风险指数：基础分（企业风险等级）+ 预警冲击（按等级加权、按天衰减）+ 设备健康缺口 + 未修补漏洞
    base = {"低": 25, "中": 35, "高": 50, "极高": 65}.get(b["enterprise"].get("risk_level") or "中", 35)
    days = max(1, (sc["end_h"] - sc["start_h"]) // 24)
    weights = {"red": 25, "orange": 15, "yellow": 8, "blue": 2}
    index_series = []
    for d in range(days + 1):
        hour = sc["start_h"] + d * 24
        shock = sum(weights[a["level"]] * (0.85 ** max(0, (hour - a["hour"]) / 24)) for a in a_rows if a["hour"] <= hour)
        val = min(100.0, base + shock + max(0.0, 70 - health_min) * 0.3 * (d / days) + 4 * ctx.workspace.get("unpatched", 0))
        index_series.append([M.sim_time(hour, pstate).date().isoformat(), round(val, 1)])
    end_val = index_series[-1][1]
    level = "低" if end_val < 40 else ("中" if end_val < 60 else ("高" if end_val < 80 else "极高"))
    type_changes = []
    for rt in sorted({a["risk_type"] for a in a_rows}):
        n = sum(1 for a in a_rows if a["risk_type"] == rt)
        type_changes.append({"name": SUBTYPES.get(rt, {}).get("name", "数据接入" if rt == "DATA" else rt), "comment": f"报告期内触发预警 {n} 条，最高等级{LEVEL_LABELS[max((a['level'] for a in a_rows if a['risk_type'] == rt), key=RANK.index)]}"})
    prof = b["enterprise"]["profile"]
    ms = prof.get("milestones", [])
    trl = min((p["trl"] for p in prof.get("products", [])), default=6)
    phase = next((p for p in SUB_PHASES if p["trl"][0] <= trl <= p["trl"][1]), SUB_PHASES[0])
    delay_ind = next((i for i in inds_out if i["name"].startswith("里程碑延误") and i.get("points")), None)
    lc_comment = f"主要产品技术成熟度等级 {trl}，处于{phase['name']}子阶段。"
    if delay_ind and delay_ind["last"] > 30:
        lc_comment += f"研发里程碑延误已达 {delay_ind['last']:.0f} 天，存在阶段跃迁推迟风险。"
    advice = []
    for a in a_rows:
        if a["status"] in ("new", "processing") and a["level"] in ("red", "orange"):
            advice.append({"text": f"尽快完成预警 {a['alert_no']}（{a['title']}）的处置闭环", "basis": f"{a['level_label']}预警未关闭", "effect": "降低事故发生概率与损失扩大", "ref": a["alert_no"]})
    if health_min < 60:
        advice.append({"text": "对健康指数偏低的关键设备安排预测性维护（轴承与润滑检查）", "basis": f"报告期最低健康指数 {health_min:.0f}", "effect": "预计将突发故障概率降低 40% 以上", "ref": "TP1"})
    if ctx.workspace.get("unpatched") or any(i["name"].startswith("登录失败") and i.get("anomalies") for i in inds_out):
        advice.append({"text": "限期修补高危漏洞并对外网暴露面开展渗透测试", "basis": "存在未修补高危漏洞或登录异常", "effect": "显著降低数据安全事件概率", "ref": "TP2"})
    if delay_ind and delay_ind["last"] > 30:
        advice.append({"text": "与客户共同复盘研发里程碑延误原因，制定恢复计划并保留费用凭证", "basis": f"里程碑延误 {delay_ind['last']:.0f} 天", "effect": "降低研发中断损失并为可能的理赔保留依据", "ref": delay_ind["id"]})
    if not advice:
        advice.append({"text": "维持现有风险管控措施，按月复核关键风险指标", "basis": "报告期内无高等级预警", "effect": "保持低风险水平", "ref": "risk_index"})
    auth = _authorizations(b)
    cooperation = [f"请授权接入{name}数据，以便开展{what}" for k, name, what in (("iot", "物联网设备", "设备健康监测"), ("log", "信息安全日志", "数据安全检测"), ("ops", "研发与运营", "研发进度偏离检测")) if not auth.get(k)]
    for c in ((b.get("application") or {}).get("decision") or {}).get("conditions", []) or []:
        cooperation.append(f"按核保约定履行：{c['text']}")
    lines = set(b["product"]["lines"])
    gaps = []
    if any(a["risk_type"].startswith("R07") for a in a_rows) and not has_family(lines, "cyber"):
        gaps.append("报告期内出现网络安全事件信号，建议评估网络安全保险")
    if phase["stage"] == "industrial" and not has_family(lines, "liability", "quality", "life"):
        gaps.append("产品已进入产业化阶段，建议评估人工智能产品责任、首台套责任、质量保证或疫苗安全等责任类保险")
    if any(a["risk_type"].startswith("R05") for a in a_rows) and not has_family(lines, "ip", "liability"):
        gaps.append("报告期内出现专利或商业秘密信号，建议评估专利执行、专利被侵权损失或侵犯专利权责任保险")
    stats = {"indicators": inds_out, "alerts": {"total": len(a_rows), "by_level": by_level, "sla_rate": round(sla_rate, 3), "false_positive_rate": round(fp / closed, 3) if closed else 0.0, "items": a_rows[-12:]}, "incidents": n_inc, "risk_index": {"start": index_series[0][1], "end": end_val, "change": round(end_val - index_series[0][1], 1), "level": level, "series": index_series}, "type_changes": type_changes, "lifecycle": {"trl": trl, "phase": phase["name"], "comment": lc_comment, "milestones": ms}, "advice_candidates": advice, "cooperation": cooperation, "coverage_gaps": gaps, "health_min": None if health_min == 100.0 else round(health_min, 1)}
    ctx.workspace["stats"] = stats
    return ToolResult(summary=f"统计完成：有数据的指标 {sum(1 for i in inds_out if i.get('points'))} 项，预警 {len(a_rows)} 条（红 {by_level['red']}、橙 {by_level['orange']}、黄 {by_level['yellow']}），事故 {n_inc} 起，风险指数 {index_series[0][1]:.0f}→{end_val:.0f}（{level}），建议候选 {len(advice)} 条", data={k: v for k, v in stats.items() if k != "indicators"}, level=2)


@registry.tool("compose_risk_mgmt_report", "撰写风险管理报告", "以统计与检测结果为事实，由大模型撰写七部分报告（脱敏出域、私域回填）", permission="T1", zone="cloud", max_level=1)
def compose_risk_mgmt_report(ctx, **_) -> ToolResult:
    st = ctx.workspace["stats"]
    slim = {**st, "indicators": [{k: v for k, v in i.items() if k != "series"} for i in st["indicators"]], "risk_index": {k: v for k, v in st["risk_index"].items() if k != "series"}}
    from ...privacy.narrative import finalize_narrative

    out = model_gateway.invoke("rw.report", {"period": ctx.workspace["scope"]["period_label"], "stats": slim}, ctx=ctx, data_level=2)
    out = finalize_narrative(out if isinstance(out, dict) else {}, ctx.vault)
    ctx.workspace["report"] = out
    return ToolResult(summary=f"报告已撰写：{len(out['sections'])} 个部分；摘要：{out['summary'][:120]}", data={"sections": [s["title"] for s in out["sections"]]}, level=1)


_ = (math, Artifact)
