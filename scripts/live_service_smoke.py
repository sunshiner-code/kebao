"""按产品库已发布产品，走页面同款 API 冒烟产品服务工具（承保风控 / 风险预警 / 定损理赔）。

用法：
  .venv/bin/python scripts/live_service_smoke.py
  .venv/bin/python scripts/live_service_smoke.py --base http://127.0.0.1:8742 --timeout 600
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# multipart helper
BOUNDARY = "----TechinsBoundary7MA4YWxkTrZu0gW"


def req(base: str, method: str, path: str, token: str | None = None, body: dict | None = None) -> Any:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:600]}") from exc


def upload_material(base: str, token: str, *, enterprise_id: int, owner_type: str, owner_id: int, material_type: str, filename: str, text: str) -> dict:
    fields = {
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "enterprise_id": str(enterprise_id),
        "material_type": material_type,
        "data_level": "2",
    }
    body = bytearray()
    for k, v in fields.items():
        body.extend(f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    body.extend(
        (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        ).encode()
    )
    body.extend(text.encode("utf-8"))
    body.extend(f"\r\n--{BOUNDARY}--\r\n".encode())
    headers = {"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}
    r = urllib.request.Request(base + "/api/materials", data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST /api/materials -> {exc.code}: {detail[:600]}") from exc


def login(base: str, username: str) -> str:
    return req(base, "POST", "/api/auth/login", body={"username": username, "password": "demo1234"})["token"]


def resolve_open_cards(base: str, tokens: dict[str, str], handled: set[int]) -> int:
    n = 0
    for role, token in tokens.items():
        try:
            cards = req(base, "GET", "/api/hitl/cards?status=open&scope=mine", token) or []
        except Exception:
            cards = req(base, "GET", "/api/hitl/cards?status=open", token) or []
        for c in cards:
            cid = c["id"]
            if cid in handled:
                continue
            keys = [o["key"] for o in (c.get("options") or [])]
            # 联调目标是走通出单→监测→理赔：核保决定优先选标准承保，避免“按建议拒保”
            if c.get("card_type") == "decision" and "option:standard" in keys:
                action = "option:standard"
            elif c.get("card_type") == "claim_approval" and "approve" in keys:
                action = "approve"
            elif "approve" in keys:
                action = "approve"
            elif "accept" in keys:
                action = "accept"
            else:
                action = keys[0] if keys else None
            if not action:
                continue
            use = token
            if c.get("card_type") in ("decision", "claim_approval") or (c.get("context") or {}).get("approval_level", 1) >= 2:
                use = tokens.get("senior") or tokens.get("actuary") or token
            try:
                req(base, "POST", f"/api/hitl/cards/{cid}/resolve", use, {"action": action, "comment": "产品服务联调：选标准承保以打通后续工具"})
                handled.add(cid)
                n += 1
                print(f"    人审 #{cid} [{c.get('card_type')}] {c.get('title','')[:48]} -> {action} ({role})")
            except Exception as exc:  # noqa: BLE001
                print(f"    人审失败 #{cid}: {exc}")
    return n


def wait_run(base: str, token: str, tokens: dict[str, str], run_id: int, handled: set[int], timeout: float) -> dict:
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        resolve_open_cards(base, tokens, handled)
        last = req(base, "GET", f"/api/runs/{run_id}", token) or {}
        st = last.get("status")
        if st in ("completed", "failed", "cancelled", "rejected"):
            return last
        time.sleep(1.2)
    # 超时也尽量再读一次状态
    try:
        last = req(base, "GET", f"/api/runs/{run_id}", token) or last
    except Exception:
        pass
    if last.get("status") in ("queued", "planning", "running", "waiting_human"):
        last = {**last, "status": "timeout"}
    return last or {"status": "timeout", "id": run_id}


def pick_enterprise(ents: list[dict], product_lines: list[str], used: set[int] | None = None) -> dict:
    prefer_name = {
        "LS03": ["康衡", "瑞声"],
        "AI02": ["深瞳", "明澜", "星衡"],
        "RB01": ["锐驰", "擎川", "星衡"],
        "RB02": ["锐驰", "擎川", "星衡"],
    }
    prefer_track = {
        "LS03": ["医疗器械"],
        "AI02": ["人工智能"],
        "RB01": ["工业机器人"],
        "RB02": ["工业机器人"],
    }
    used = used or set()
    name_keys, track_keys = [], []
    for line in product_lines:
        name_keys.extend(prefer_name.get(line, []))
        track_keys.extend(prefer_track.get(line, []))
    ranked = []
    for e in ents:
        if e["id"] in used:
            continue
        blob = f"{e.get('track','')}{e.get('industry','')}{e.get('name','')}"
        score = 0
        if any(k in (e.get("name") or "") for k in name_keys):
            score += 3
        if any(k in blob for k in track_keys):
            score += 2
        ranked.append((score, e))
    ranked.sort(key=lambda x: -x[0])
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]
    for e in ents:
        if e["id"] not in used:
            return e
    return ents[0]


def material_bundle_for_app(ent: dict, lines: list[str], product_name: str) -> list[tuple[str, str, str]]:
    ent_name = ent.get("name") or "投保企业"
    credit = ent.get("credit_code") or (ent.get("profile") or {}).get("credit_code") or "91310000MA1REAL001X"
    equipment = list((ent.get("profile") or {}).get("equipment") or [])
    common = [
        ("投保单", "投保单.txt", f"投保单\n投保人：{ent_name}\n统一社会信用代码：{credit}\n产品：{product_name}\n险种：{','.join(lines)}\n声明属实。\n"),
        ("营业执照", "营业执照.txt", f"营业执照副本\n企业名称：{ent_name}\n统一社会信用代码：{credit}\n"),
        ("近两年财务报表或审计报告", "财务报表.txt", f"{ent_name}近两年简要财务\n营业收入 1.2亿\n净资产 0.8亿\n研发费用 0.3亿\n"),
        ("企业情况说明", "企业情况说明.txt", f"{ent_name}情况说明：主营业务与风险状况简述，用于核保联调。\n统一社会信用代码：{credit}\n险种：{','.join(lines)}\n"),
    ]
    extras: list[tuple[str, str, str]] = []
    if "LS03" in lines:
        extras.append(("临床试验方案摘要", "临床试验方案.txt", "临床试验方案摘要：II期受试者约 120 人，主要终点安全性与有效性。\n"))
        extras.append(("伦理批件", "伦理批件.txt", "伦理委员会批件：同意开展本试验，批件号 ETH-2024-088。\n"))
    if "AI02" in lines:
        extras.append(("系统功能说明", "系统功能说明.txt", "智能控制系统功能说明：负责产线调度与安全联锁，已通过功能安全评估。\n"))
        extras.append(("历史事故记录", "历史事故记录.txt", "近三年控制系统相关责任事故：0 起。\n"))
    if "RB01" in lines or "RB02" in lines:
        if equipment:
            lines_eq = []
            inv_lines = []
            plate_lines = []
            for i, eq in enumerate(equipment[:4], 1):
                nm = eq.get("name") or f"设备{i}"
                model = eq.get("model") or "N/A"
                serial = eq.get("serial") or f"SN-{i:04d}"
                value = eq.get("value") or 0
                lines_eq.append(f"{i}. {nm} 型号{model} 序列号{serial} 估值{value}元 存放{(eq.get('location') or '厂区')}")
                inv_lines.append(f"发票号 INV-2024-{i:04d} 设备{nm}/{serial} 金额{value}元 购买方{ent_name}")
                plate_lines.append(f"铭牌照片说明：{nm} 型号{model} 序列号{serial}（照片已核对）")
            extras.append(("设备清单", "设备清单.txt", "设备清单\n" + "\n".join(lines_eq) + "\n"))
            extras.append(("设备购置发票", "设备购置发票.txt", "设备购置发票汇总\n" + "\n".join(inv_lines) + "\n"))
            extras.append(("设备与铭牌照片", "设备与铭牌照片.txt", "设备与铭牌照片说明\n" + "\n".join(plate_lines) + "\n"))
            extras.append(("实验室或存放场所照片", "存放场所照片.txt", f"存放场所照片说明：{ent_name} 机器人本体存放于厂区洁净装配区，已拍照留存。\n"))
        else:
            extras.append(("设备清单", "设备清单.txt", "机器人本体清单：型号 XR-7 价值 180 万；XR-9 价值 260 万。\n"))
            extras.append(("设备购置发票", "设备购置发票.txt", "发票 INV-2024-0001 XR-7 180万；INV-2024-0002 XR-9 260万。\n"))
            extras.append(("设备与铭牌照片", "设备与铭牌照片.txt", "铭牌照片说明：XR-7 / XR-9 序列号已核对。\n"))
            extras.append(("实验室或存放场所照片", "存放场所照片.txt", f"{ent_name} 存放场所照片说明。\n"))
        extras.append(("维保记录", "维保记录.txt", "近一年按厂商规范维保，无重大本体损坏记录。\n"))
    return common + extras


def claim_materials(ent: dict, policy_no: str, incident_type: str, occurred: datetime, lines: list[str]) -> list[tuple[str, str, str]]:
    ent_name = ent.get("name") or "被保险人"
    eq = max(((ent.get("profile") or {}).get("equipment") or [{"name": "关键设备", "serial": "SN-DEMO-001", "value": 800000}]), key=lambda e: e.get("value") or 0)
    occ = occurred.astimezone(timezone.utc)
    occ_cn = f"{occ:%Y年%m月%d日 %H时%M分}"
    rep_cn = f"{(occ + timedelta(hours=2)):%Y年%m月%d日 %H时%M分}"
    if "LS03" in lines:
        rows = [
            ("受试者医疗费用", "事件响应与法律费用", "1", "120000", "120000"),
            ("受试者补偿金", "事件响应与法律费用", "1", "80000", "80000"),
            ("律师服务", "事件响应与法律费用", "40", "2500", "100000"),
            ("应急处置与检测", "事件响应与法律费用", "1", "50000", "50000"),
        ]
        statement = (
            f"{occ_cn}，试验过程中受试者出现严重不良事件，已按方案停药并送医。"
            f"序列相关设备/方案编号与被保险人一致。我司于{rep_cn}向保险人报案，索赔金额 350000 元。"
        )
        extra = [
            ("伦理批件", "伦理批件.txt", "伦理批件号 ETH-2024-088，同意开展本试验。\n"),
            ("损害证明", "损害证明.txt", f"医院诊断证明：受试者于{occ_cn}出现严重不良事件，已住院治疗。\n"),
            ("责任认定文件", "责任认定文件.txt", "试验相关不良事件与试验用医疗器械使用存在时间关联，初步责任认定待正式鉴定。\n"),
        ]
    elif "AI02" in lines:
        rows = [
            ("第三者设备修理", "设备修理", "1", "180000", "180000"),
            ("现场检测鉴定", "设备修理", "1", "28000", "28000"),
            ("律师服务", "事件响应与法律费用", "20", "2500", "50000"),
            ("施救与隔离费用", "施救费用", "1", "42000", "42000"),
        ]
        statement = (
            f"{occ_cn}，智能控制系统误发指令导致产线设备碰撞，造成第三者财产损失。"
            f"系统名称与保单标的一致。我司立即停机隔离，于{rep_cn}向保险人报案，索赔金额 300000 元。"
        )
        extra = [
            ("损害证明", "损害证明.txt", "第三者财产损害清单与维修评估已附。\n"),
            ("责任认定文件", "责任认定文件.txt", "初步认定控制系统输出异常为近因，待正式责任鉴定。\n"),
        ]
    else:
        rows = [
            ("主控板更换", "设备修理", "1", "86000", "86000"),
            ("伺服模组修理", "设备修理", "2", "28000", "56000"),
            ("设备维修工时", "设备修理", "80", "650", "52000"),
            ("现场检测鉴定", "设备修理", "1", "28000", "28000"),
            ("设备租赁（替代设备）", "施救费用", "1", "68000", "68000"),
            ("研发中断持续费用", "研发中断持续费用", "10", "6000", "60000"),
        ]
        statement = (
            f"{occ_cn}左右，我司{eq.get('location') or '厂区'}的{eq.get('name')}（序列号 {eq.get('serial')}）"
            f"在作业中突发碰撞异响并停机，本体结构与伺服模组受损。事故后立即断电隔离并拍照保全，"
            f"于{rep_cn}向保险人报案，初步估计损失约 350000 元。"
        )
        extra = [
            ("购置发票", "购置发票.txt", f"名称：{eq.get('name')}\n序列号：{eq.get('serial')}\n金额：{eq.get('value') or 0:.0f}元\n"),
            ("检测报告", "检测报告.txt", f"鉴定对象：{eq.get('name')}（序列号 {eq.get('serial')}）\n鉴定结论：突发碰撞导致本体结构与伺服模组损坏，属保险事故范畴的意外损坏。\n"),
            ("维修报价", "维修报价.txt", "报价单位：原厂服务中心\n项目 | 数量 | 单价 | 金额\n主控板更换 | 1 | 86000 | 86000\n伺服模组修理 | 2 | 28000 | 56000\n设备维修工时 | 80 | 650 | 52000\n"),
        ]
    total = sum(float(r[4]) for r in rows)
    loss_table = "损失清单（单位：元）\n项目 | 类别 | 数量 | 单价 | 金额\n" + "\n".join(" | ".join(r) for r in rows) + "\n"
    base = [
        ("索赔申请书", "索赔申请书.txt", f"被保险人：{ent_name}\n保单号：{policy_no}\n出险时间：{occ_cn}\n出险地点：生产/试验现场\n事故类型：{incident_type}\n索赔金额：{total:.0f}元\n其他保险：无\n"),
        ("出险通知", "出险通知.txt", f"通知时间：{rep_cn}\n通知方式：电话及平台报案\n保单号：{policy_no}\n"),
        ("客户事故声明", "客户事故声明.txt", statement + "\n"),
        ("损失清单", "损失清单.txt", loss_table),
        ("保险单", "保险单.txt", f"保单号：{policy_no}\n被保险人：{ent_name}\n险种：{','.join(lines)}\n"),
    ]
    return base + extra


def incident_for_lines(lines: list[str]) -> tuple[str, str]:
    if "LS03" in lines:
        return "受试者不良事件", "临床试验中受试者出现严重不良事件，已按方案报告伦理与药监。"
    if "AI02" in lines:
        return "控制系统责任事故", "控制系统误动作导致产线设备碰撞，造成第三者财产损失。"
    if "RB01" in lines or "RB02" in lines:
        return "机器人本体损失", "机器人本体在作业中发生碰撞损坏，需维修更换关键部件。"
    return "一般事故", "产品服务联调事故。"


def drive_product(base: str, tokens: dict[str, str], product: dict, ent: dict, handled: set[int], timeout: float) -> dict:
    service = tokens["service"]
    pid = product["id"]
    lines = list(product.get("lines") or [])
    name = product.get("name")
    print(f"\n=== 产品 #{pid} {name} lines={lines} / 企业 #{ent['id']} {ent['name']} ===")
    out: dict[str, Any] = {"product_id": pid, "product": name, "lines": lines, "enterprise": ent["name"], "steps": {}}

    # 1) 承保风控：新建投保单 → 上传材料 → 核保
    t0 = time.time()
    app = req(
        base,
        "POST",
        "/api/applications",
        service,
        {
            "enterprise_id": ent["id"],
            "product_id": pid,
            "sum_insured": 5_000_000 if "LS03" in lines else 8_000_000,
            "tier": "standard",
            "statement": f"产品服务联调投保：{name}",
            "authorizations": {"iot": True, "log": True, "ops": True},
            "lines": lines,
        },
    )
    aid = app["id"]
    print(f"  投保单 #{aid}")
    # 取企业详情（含 profile.equipment / credit_code），便于材料与工商登记一致
    ent_detail = req(base, "GET", f"/api/enterprises/{ent['id']}", service) or ent
    for mtype, fname, text in material_bundle_for_app(ent_detail, lines, name):
        upload_material(base, service, enterprise_id=ent["id"], owner_type="application", owner_id=aid, material_type=mtype, filename=fname, text=text)
    uw = req(base, "POST", f"/api/applications/{aid}/underwrite", service)
    run = wait_run(base, service, tokens, uw["run_id"], handled, timeout)
    app_d = req(base, "GET", f"/api/applications/{aid}", service)
    # 若仍无保单，再等一会儿让出单事件落地
    t1 = time.time()
    while time.time() - t1 < 90 and not app_d.get("policy_id"):
        resolve_open_cards(base, tokens, handled)
        time.sleep(1.5)
        app_d = req(base, "GET", f"/api/applications/{aid}", service)
    policy_id = app_d.get("policy_id")
    out["steps"]["underwriting"] = {
        "ok": run.get("status") == "completed" and bool(policy_id),
        "application_id": aid,
        "run_id": uw["run_id"],
        "run_status": run.get("status"),
        "app_status": app_d.get("status"),
        "decision": (app_d.get("decision") or {}).get("decision") if isinstance(app_d.get("decision"), dict) else app_d.get("decision"),
        "policy_id": policy_id,
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(f"  核保 run={uw['run_id']} status={run.get('status')} app={app_d.get('status')} policy={policy_id}")
    if not policy_id:
        out["ok"] = False
        return out

    # 2) 风险预警：等监测方案 → 推进时钟 → 拉预警/报告
    t0 = time.time()
    pol = None
    for _ in range(40):
        resolve_open_cards(base, tokens, handled)
        pol = req(base, "GET", f"/api/policies/{policy_id}", service)
        if pol.get("plan_id") or (pol.get("plan") or {}).get("id"):
            break
        # 监测方案可能还在 AG-RW 规划中
        time.sleep(2)
    plan_id = pol.get("plan_id") or (pol.get("plan") or {}).get("id")
    launched = []
    alerts = []
    report_run = None
    if plan_id:
        # 多轮推进，尽量触发预警
        for hours in (12, 24, 24, 24):
            try:
                adv = req(base, "POST", f"/api/policies/{policy_id}/advance", service, {"hours": hours})
                launched.extend(adv.get("launched") or [])
            except Exception as exc:  # noqa: BLE001
                print(f"  推进失败: {exc}")
            resolve_open_cards(base, tokens, handled)
            time.sleep(2)
        alerts = req(base, "GET", f"/api/alerts", service) or []
        alerts = [a for a in alerts if a.get("policy_id") == policy_id]
        # 若有新预警，做一次联系核实
        for a in alerts[:2]:
            if a.get("status") == "new":
                try:
                    req(base, "POST", f"/api/alerts/{a['id']}/handle", service, {"action": "contact", "note": "联调核实"})
                except Exception as exc:  # noqa: BLE001
                    print(f"  预警处置失败: {exc}")
        # 风险管理报告
        try:
            report_run = req(base, "POST", f"/api/policies/{policy_id}/reports", service, {"period": "month"})
            wait_run(base, service, tokens, report_run["run_id"], handled, min(timeout, 240))
        except Exception as exc:  # noqa: BLE001
            print(f"  风险报告失败: {exc}")
    out["steps"]["risk_warning"] = {
        "ok": bool(plan_id),
        "plan_id": plan_id,
        "launched_candidates": len(launched),
        "alerts": len(alerts),
        "report_run_id": (report_run or {}).get("run_id"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(f"  监测 plan={plan_id} launched={len(launched)} alerts={len(alerts)}")

    # 3) 定损理赔：报案 → 材料 → 启动定损 → 人审 →（若批准）结案
    t0 = time.time()
    itype, desc = incident_for_lines(lines)
    # 出险时间必须落在保单起期内，否则条款核查会判“不在期内”
    start_raw = pol.get("start_date")
    try:
        start_dt = datetime.fromisoformat((start_raw or "").replace("Z", "+00:00"))
    except Exception:
        start_dt = datetime.now(timezone.utc) - timedelta(hours=1)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    occurred_dt = max(start_dt + timedelta(hours=6), datetime.now(timezone.utc) - timedelta(hours=2))
    occurred = occurred_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    claim = req(
        base,
        "POST",
        f"/api/policies/{policy_id}/claims",
        service,
        {"description": desc, "occurred_at": occurred, "claimed_amount": 350000, "incident_type": itype},
    )
    cid = claim["claim_id"]
    pol_no = pol.get("policy_no") or f"policy-{policy_id}"
    ent_detail = req(base, "GET", f"/api/enterprises/{ent['id']}", service) or ent
    for mtype, fname, text in claim_materials(ent_detail, pol_no, itype, occurred_dt, lines):
        upload_material(base, service, enterprise_id=ent["id"], owner_type="claim", owner_id=cid, material_type=mtype, filename=fname, text=text)
    start = req(base, "POST", f"/api/claims/{cid}/start", service)
    crun = wait_run(base, service, tokens, start["run_id"], handled, timeout)
    # 再清一轮理赔审批
    for _ in range(20):
        resolve_open_cards(base, tokens, handled)
        cdetail = req(base, "GET", f"/api/claims/{cid}", service)
        if cdetail.get("status") in ("approved", "closed", "rejected", "declined"):
            break
        time.sleep(1.5)
    pay = None
    if cdetail.get("status") == "approved":
        try:
            pay = req(base, "POST", f"/api/claims/{cid}/pay", service)
            cdetail = req(base, "GET", f"/api/claims/{cid}", service)
        except Exception as exc:  # noqa: BLE001
            print(f"  结案失败: {exc}")
    out["steps"]["claims"] = {
        "ok": crun.get("status") == "completed",
        "claim_id": cid,
        "run_id": start["run_id"],
        "run_status": crun.get("status"),
        "claim_status": cdetail.get("status"),
        "suggested_amount": cdetail.get("suggested_amount"),
        "approved_amount": cdetail.get("approved_amount"),
        "paid": bool(pay),
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(
        f"  理赔 claim={cid} run={start['run_id']} status={crun.get('status')} "
        f"case={cdetail.get('status')} suggested={cdetail.get('suggested_amount')}"
    )
    out["ok"] = all(step.get("ok") for step in out["steps"].values())
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8742")
    parser.add_argument("--timeout", type=float, default=480)
    parser.add_argument("--limit", type=int, default=0, help="只测前 N 个已发布产品，0=全部")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    tokens = {
        "service": login(base, "service"),
        "senior": login(base, "senior"),
        "designer": login(base, "designer"),
    }
    try:
        tokens["actuary"] = login(base, "actuary")
    except Exception:
        pass

    products = req(base, "GET", "/api/products?status=published", tokens["service"]) or []
    if args.limit:
        products = products[: args.limit]
    ents = req(base, "GET", "/api/enterprises", tokens["service"]) or []
    if not products:
        print("无已发布产品")
        return 2
    if not ents:
        print("无企业客户")
        return 2

    # 页面列表接口可达性
    page_checks = {}
    for path in ("/api/applications", "/api/policies", "/api/alerts", "/api/claims", "/api/incidents", "/api/hitl/cards"):
        try:
            rows = req(base, "GET", path, tokens["service"])
            page_checks[path] = {"ok": True, "count": len(rows) if isinstance(rows, list) else 1}
        except Exception as exc:  # noqa: BLE001
            page_checks[path] = {"ok": False, "error": str(exc)[:200]}

    handled: set[int] = set()
    used_ents: set[int] = set()
    results = []
    t0 = time.time()
    for p in products:
        ent = pick_enterprise(ents, list(p.get("lines") or []), used_ents)
        used_ents.add(ent["id"])
        try:
            results.append(drive_product(base, tokens, p, ent, handled, args.timeout))
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {exc}")
            results.append({"product_id": p["id"], "product": p.get("name"), "ok": False, "error": str(exc)[:400]})

    summary = {
        "elapsed_s": round(time.time() - t0, 1),
        "products_tested": len(results),
        "passed": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "page_checks": page_checks,
        "results": results,
    }
    out_path = Path("/tmp/live_service_smoke_report.json")
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n======== 汇总 ========")
    print(f"产品 {summary['products_tested']}  通过 {summary['passed']}  失败 {summary['failed']}  耗时 {summary['elapsed_s']}s")
    for r in results:
        mark = "OK" if r.get("ok") else "FAIL"
        steps = r.get("steps") or {}
        uw = (steps.get("underwriting") or {}).get("app_status")
        rw = (steps.get("risk_warning") or {}).get("plan_id")
        cl = (steps.get("claims") or {}).get("claim_status")
        print(f"  [{mark}] #{r.get('product_id')} {r.get('product')}  核保={uw} 监测={rw} 理赔={cl}  {r.get('error','')[:80]}")
    print(f"报告: {out_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
