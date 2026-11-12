"""并行推进线上已有设计项目：自动人审、填偏好、选费率，直到完成或超时。

.venv/bin/python -u scripts/live_design_drive.py --timeout 1800
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
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:400]}") from exc


def login(base: str, username: str) -> str:
    return req(base, "POST", "/api/auth/login", body={"username": username, "password": "demo1234"})["token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8742")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--prefix", default="联调·")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    tokens = {u: login(base, u) for u in ("designer", "actuary", "service", "senior", "admin")}
    handled: set[int] = set()
    pref_done: set[int] = set()
    rate_done: set[int] = set()
    t0 = time.time()

    while time.time() - t0 < args.timeout:
        # 人审（admin 看全部）
        cards = req(base, "GET", "/api/hitl/cards?status=open&scope=all", tokens["admin"]) or []
        for c in cards:
            cid = c["id"]
            if cid in handled:
                continue
            keys = [o["key"] for o in (c.get("options") or [])]
            action = "approve" if "approve" in keys else ("accept" if "accept" in keys else (keys[0] if keys else None))
            if not action:
                continue
            need = int((c.get("context") or {}).get("approval_level") or 1)
            use = tokens["admin"] if need >= 3 else (tokens["actuary"] if need >= 2 or c.get("card_type") in ("decision", "claim_approval") else tokens["designer"])
            # 设计人审多数分给 designer
            if c.get("assignee_role") == "designer":
                use = tokens["designer"] if need <= 1 else tokens["actuary"]
            elif c.get("assignee_role") == "service":
                use = tokens["senior"] if need >= 2 else tokens["service"]
            try:
                req(base, "POST", f"/api/hitl/cards/{cid}/resolve", use, {"action": action, "comment": "界面联调：按建议处理"})
                handled.add(cid)
                print(f"人审 #{cid} {c.get('card_type')} {c.get('title','')[:48]} -> {action}", flush=True)
            except Exception as exc:  # noqa: BLE001
                # 权限不够换 admin
                try:
                    req(base, "POST", f"/api/hitl/cards/{cid}/resolve", tokens["admin"], {"action": action, "comment": "界面联调：管理员代处理"})
                    handled.add(cid)
                    print(f"人审 #{cid} (admin) -> {action}", flush=True)
                except Exception as exc2:  # noqa: BLE001
                    print(f"人审失败 #{cid}: {exc2}", flush=True)

        projects = req(base, "GET", "/api/design-projects", tokens["designer"]) or []
        targets = [p for p in projects if str(p.get("name") or "").startswith(args.prefix)]
        all_done = True
        for p in targets:
            pid = p["id"]
            nodes = ((p.get("pipeline") or {}).get("nodes") or {})
            ptype = p.get("project_type")
            pricing = nodes.get("pricing") or {}
            if pricing.get("status") != "done" and nodes.get("package", {}).get("status") not in ("ready", "done"):
                all_done = False
            if (
                pid not in pref_done
                and ptype == "enterprise"
                and nodes.get("risk", {}).get("status") == "done"
                and nodes.get("preference", {}).get("status") in ("waiting_input", "pending")
            ):
                try:
                    req(base, "POST", f"/api/design-projects/{pid}/preference", tokens["designer"], {"answers": {"q1": 2, "q2": 3, "q3": 3, "q4": 4, "q5": 2, "q6": 3}})
                    pref_done.add(pid)
                    print(f"项目 {pid} 已填偏好", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"项目 {pid} 偏好失败: {exc}", flush=True)
            if pid not in rate_done and pricing.get("status") == "waiting_confirm":
                for method in ("weighted", "classification", "maturity"):
                    try:
                        req(base, "POST", f"/api/design-projects/{pid}/rates/select", tokens["actuary"], {"method": method})
                        rate_done.add(pid)
                        print(f"项目 {pid} 选定费率 {method}", flush=True)
                        break
                    except Exception:
                        continue
            # 可组装则组装
            if pricing.get("status") == "done" and nodes.get("package", {}).get("status") == "ready":
                try:
                    out = req(base, "POST", f"/api/design-projects/{pid}/package", tokens["designer"])
                    print(f"项目 {pid} 组装产品包 -> {out}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    pass

        # 状态快照
        snap = []
        for p in targets:
            nodes = ((p.get("pipeline") or {}).get("nodes") or {})
            snap.append(f"{p['id']}:{ {k: nodes.get(k,{}).get('status') for k in ('risk','preference','clause','pricing','package')} }")
        print(f"[{round(time.time()-t0)}s] 联调项目 {len(targets)} 个 | " + " | ".join(snap[:6]), flush=True)

        if targets and all_done:
            print("全部联调项目定价完成", flush=True)
            break
        time.sleep(3)

    projects = req(base, "GET", "/api/design-projects", tokens["designer"]) or []
    runs = req(base, "GET", "/api/runs?limit=300", tokens["admin"]) or []
    targets = [p for p in projects if str(p.get("name") or "").startswith(args.prefix)]
    summary = {
        "elapsed_s": round(time.time() - t0, 1),
        "hitl_resolved": len(handled),
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "type": p["project_type"],
                "status": p["status"],
                "nodes": {k: v.get("status") for k, v in ((p.get("pipeline") or {}).get("nodes") or {}).items()},
            }
            for p in targets
        ],
        "runs_total": len(runs),
        "runs_by_agent": {},
        "runs_by_status": {},
    }
    for r in runs:
        summary["runs_by_agent"][r.get("agent_id") or "?"] = summary["runs_by_agent"].get(r.get("agent_id") or "?", 0) + 1
        summary["runs_by_status"][r.get("status") or "?"] = summary["runs_by_status"].get(r.get("status") or "?", 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    done = sum(1 for p in summary["projects"] if (p["nodes"].get("pricing") == "done"))
    return 0 if done == len(summary["projects"]) and summary["projects"] else 1


if __name__ == "__main__":
    sys.exit(main())
