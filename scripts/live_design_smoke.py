"""通过线上 API 从设计项目起跑智能体，写入与界面同一套业务库，控制台可见。

用法：
  .venv/bin/python scripts/live_design_smoke.py
  .venv/bin/python scripts/live_design_smoke.py --base http://127.0.0.1:8742 --timeout 900
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def req(base: str, method: str, path: str, token: str | None = None, body: dict | None = None) -> Any:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:500]}") from exc


def login(base: str, username: str) -> str:
    out = req(base, "POST", "/api/auth/login", body={"username": username, "password": "demo1234"})
    return out["token"]


def resolve_open_cards(base: str, tokens: dict[str, str], handled: set[int]) -> int:
    n = 0
    for role, token in tokens.items():
        cards = req(base, "GET", "/api/hitl/cards?status=open&scope=mine", token) or []
        for c in cards:
            cid = c["id"]
            if cid in handled:
                continue
            keys = [o["key"] for o in (c.get("options") or [])]
            action = "approve" if "approve" in keys else ("accept" if "accept" in keys else (keys[0] if keys else None))
            if not action:
                continue
            # 二级审批用 actuary/senior
            use = token
            if c.get("card_type") in ("decision", "claim_approval") or (c.get("context") or {}).get("approval_level", 1) >= 2:
                use = tokens.get("actuary") or tokens.get("senior") or token
            try:
                req(base, "POST", f"/api/hitl/cards/{cid}/resolve", use, {"action": action, "comment": "界面联调：按建议处理"})
                handled.add(cid)
                n += 1
                print(f"  人审 #{cid} {c.get('card_type')} {c.get('title','')[:40]} -> {action} ({role})")
            except Exception as exc:  # noqa: BLE001
                print(f"  人审失败 #{cid}: {exc}")
    return n


def project_nodes(proj: dict) -> dict:
    return ((proj.get("pipeline") or {}).get("nodes") or {})


def drive_project(base: str, tokens: dict[str, str], pid: int, timeout: float, handled: set[int]) -> dict:
    t0 = time.time()
    pref_done = False
    rate_done = False
    designer = tokens["designer"]
    while time.time() - t0 < timeout:
        resolve_open_cards(base, tokens, handled)
        proj = req(base, "GET", f"/api/design-projects/{pid}", designer)
        nodes = project_nodes(proj)
        ptype = proj.get("project_type")
        # 企业项目：风险完成后填偏好
        if (
            not pref_done
            and ptype == "enterprise"
            and nodes.get("risk", {}).get("status") == "done"
            and nodes.get("preference", {}).get("status") in ("waiting_input", "pending")
        ):
            try:
                req(
                    base,
                    "POST",
                    f"/api/design-projects/{pid}/preference",
                    designer,
                    {"answers": {"q1": 2, "q2": 3, "q3": 3, "q4": 4, "q5": 2, "q6": 3}},
                )
                pref_done = True
                print(f"  项目 {pid} 已提交风险偏好")
            except Exception as exc:  # noqa: BLE001
                print(f"  项目 {pid} 偏好失败: {exc}")
        # 费率 waiting_confirm：选定 weighted
        pricing = nodes.get("pricing") or {}
        if not rate_done and pricing.get("status") == "waiting_confirm":
            try:
                req(base, "POST", f"/api/design-projects/{pid}/rates/select", tokens.get("actuary") or designer, {"method": "weighted"})
                rate_done = True
                print(f"  项目 {pid} 已选定费率 weighted")
            except Exception as exc:  # noqa: BLE001
                # 可能还没有 schemes，稍后再试；或已是 agent 人审路径
                if "必须选择" not in str(exc) and "unknown_method" not in str(exc):
                    print(f"  项目 {pid} 选费率: {exc}")
        if pricing.get("status") == "done" or nodes.get("package", {}).get("status") in ("ready", "done"):
            return {"id": pid, "name": proj.get("name"), "ok": True, "nodes": {k: v.get("status") for k, v in nodes.items()}, "elapsed": round(time.time() - t0, 1)}
        # 若 risk/clause 失败则提前结束
        for stage in ("risk", "clause", "pricing"):
            if nodes.get(stage, {}).get("status") in ("failed", "cancelled"):
                return {"id": pid, "name": proj.get("name"), "ok": False, "nodes": {k: v.get("status") for k, v in nodes.items()}, "elapsed": round(time.time() - t0, 1)}
        time.sleep(2)
    proj = req(base, "GET", f"/api/design-projects/{pid}", designer)
    nodes = project_nodes(proj)
    return {"id": pid, "name": proj.get("name"), "ok": False, "timeout": True, "nodes": {k: v.get("status") for k, v in nodes.items()}, "elapsed": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8742")
    ap.add_argument("--timeout", type=float, default=900)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    health = req(base, "GET", "/api/health")
    print("health", health)

    tokens = {
        "designer": login(base, "designer"),
        "actuary": login(base, "actuary"),
        "service": login(base, "service"),
        "senior": login(base, "senior"),
        "admin": login(base, "admin"),
    }
    ents = req(base, "GET", "/api/enterprises", tokens["designer"])
    by_name = {e["name"]: e["id"] for e in ents}
    print(f"企业 {len(ents)} 家")

    specs = [
        {
            "name": "联调·需求驱动·精密设备损坏与研发中断",
            "project_type": "need",
            "background": "面向精密制造企业的科技保险产品开发，覆盖中试与量产阶段研发设备。",
            "customer_need": "希望保险分担关键研发设备在约定事故中损坏的损失，以及因设备损坏导致的研发中断费用。",
        },
        {
            "name": "联调·需求驱动·人工智能网络安全责任",
            "project_type": "need",
            "background": "人工智能与云计算企业面临勒索与数据泄露风险。",
            "customer_need": "希望保障网络攻击导致的业务中断、应急响应费用、客户通知与监管责任相关损失。",
        },
        {
            "name": "联调·企业定制·星衡智能装备",
            "project_type": "enterprise",
            "enterprise_ids": [by_name["星衡智能装备股份有限公司"]],
            "target_lines": ["RD08", "RD09"],
        },
        {
            "name": "联调·企业定制·康衡医疗器械",
            "project_type": "enterprise",
            "enterprise_ids": [by_name["康衡医疗器械有限公司"]],
            "target_lines": ["RD08", "RD01"],
        },
        {
            "name": "联调·赛道通用·工业机器人设备与中断",
            "project_type": "track",
            "track": "工业机器人",
            "enterprise_ids": [
                by_name["星衡智能装备股份有限公司"],
                by_name["锐驰精密机器人科技有限公司"],
                by_name["擎川机器人技术有限公司"],
            ],
            "target_lines": ["RD08", "RD09"],
        },
        {
            "name": "联调·赛道通用·人工智能网络安全与专利",
            "project_type": "track",
            "track": "人工智能软件",
            "enterprise_ids": [by_name["深瞳智算科技有限公司"], by_name["明澜数据科技有限公司"]],
            "target_lines": ["CY01", "IP06"],
        },
    ]

    created = []
    for spec in specs:
        body = {
            "name": spec["name"],
            "project_type": spec["project_type"],
            "track": spec.get("track"),
            "enterprise_ids": spec.get("enterprise_ids") or [],
            "target_lines": spec.get("target_lines") or [],
            "mode": "auto",
            "auto_advance": True,
            "background": spec.get("background") or "",
            "customer_need": spec.get("customer_need") or "",
        }
        out = req(base, "POST", "/api/design-projects", tokens["designer"], body)
        pid = out["id"]
        created.append(pid)
        print(f"已创建设计项目 #{pid} {spec['name']} ({spec['project_type']})")

    handled: set[int] = set()
    results = []
    for pid in created:
        print(f"\n=== 推进项目 {pid} ===")
        results.append(drive_project(base, tokens, pid, args.timeout, handled))
        print("结果", results[-1])

    # 组装可组装的产品包
    for r in results:
        if not r.get("ok"):
            continue
        pid = r["id"]
        try:
            pack = req(base, "POST", f"/api/design-projects/{pid}/package", tokens["designer"])
            print(f"组装产品包 project={pid} -> product={pack}")
        except Exception as exc:  # noqa: BLE001
            print(f"组装跳过 project={pid}: {exc}")

    projects = req(base, "GET", "/api/design-projects", tokens["designer"])
    runs = req(base, "GET", "/api/runs?limit=200", tokens["admin"]) or []
    summary = {
        "projects_created": created,
        "project_results": results,
        "projects_now": [{"id": p["id"], "name": p["name"], "status": p["status"], "nodes": {k: v.get("status") for k, v in ((p.get("pipeline") or {}).get("nodes") or {}).items()}} for p in projects],
        "runs": len(runs),
        "run_agents": {},
    }
    for run in runs:
        aid = run.get("agent_id") or "?"
        summary["run_agents"][aid] = summary["run_agents"].get(aid, 0) + 1

    print("\n==== 汇总（应已出现在控制台）====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    ok = sum(1 for r in results if r.get("ok"))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
