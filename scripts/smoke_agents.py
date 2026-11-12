"""批量冒烟：按边界用例跑六个智能体，自动处理人审，输出汇总结果。

用法（隔离临时库，不影响线上服务）：
  TECHINS_DATABASE_URL= TECHINS_DATA_DIR=/tmp/techins-agent-smoke \\
  TECHINS_DISABLE_LLM=1 TECHINS_MONITOR_ENABLED=0 \\
  .venv/bin/python scripts/smoke_agents.py

可选参数：
  --timeout 单用例最长等待秒数（默认 420）
  --skip-track  跳过赛道多样本评估（更快）
  --ra-only / --design-only / --service-only  只跑某一段
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 必须在 import techins 之前设定隔离环境
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("TECHINS_DATA_DIR") or "/tmp/techins-agent-smoke")
DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("TECHINS_DATA_DIR", str(DATA_DIR))
os.environ["TECHINS_DATABASE_URL"] = ""
os.environ.setdefault("TECHINS_DISABLE_LLM", "1")
os.environ.setdefault("TECHINS_MONITOR_ENABLED", "0")
os.environ.setdefault("TECHINS_EXECUTOR_WORKERS", "2")
os.environ.setdefault("TECHINS_DEMO_BOOTSTRAP", "0")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("smoke_agents")

from sqlalchemy import select  # noqa: E402

from techins.seed.runner import seed_all  # noqa: E402

seed_all()
import techins.agents  # noqa: E402,F401
from techins.agents.clause_design.library import capacity_from_financials, preference_from_answers  # noqa: E402
from techins.db import session_scope  # noqa: E402
from techins.harness import hitl  # noqa: E402
from techins.harness.executor import executor  # noqa: E402
from techins.models import (  # noqa: E402
    Alert,
    Application,
    ClaimCase,
    DesignProject,
    Enterprise,
    Evaluation,
    HitlCard,
    Incident,
    Material,
    MonitorPlan,
    Policy,
    Product,
    Run,
    RunStep,
    User,
)
from techins.seed import demo as demo_seed  # noqa: E402
from techins.seed.materials import dd_report, interview_notes  # noqa: E402
from techins.services import business, monitoring, pipeline, products  # noqa: E402
from techins.config import settings  # noqa: E402
from techins.db import utcnow  # noqa: E402
from datetime import timedelta  # noqa: E402

CASES_PATH = ROOT / "tests" / "fixtures" / "agent_boundary_cases.json"
REPORT_PATH = DATA_DIR / "smoke_agents_report.json"


def load_cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def uid(username: str) -> int:
    with session_scope() as s:
        return s.scalars(select(User).where(User.username == username)).first().id


def ents_by_name() -> dict[str, int]:
    with session_scope() as s:
        return {e.name: e.id for e in s.scalars(select(Enterprise)).all()}


def ensure_cold_enterprise() -> int:
    """未知赛道企业：触发 AG-RA 冷启动。"""
    name = "量子隧穿计算有限公司"
    with session_scope() as s:
        row = s.scalars(select(Enterprise).where(Enterprise.name == name)).first()
        if row:
            return row.id
    manager_id = uid("service")
    profile = {
        "legal_rep": "陆衡舟",
        "phone": "13900008888",
        "address": "安徽省合肥市高新区创新大道88号",
        "financials": {
            "revenue": 2.4e7, "net_profit": -8.6e6, "total_assets": 1.1e8, "net_assets": 7.2e7,
            "operating_cash_flow": -3.1e6, "cash_reserve": 2.8e7, "monthly_fixed_cost": 1.6e6,
            "rd_expense": 1.5e7, "social_security_count": 86, "tax_paid": 4.2e5,
        },
        "products": [{"name": "QT-1 量子退火求解器", "tech_field": "量子计算", "trl": 4, "description": "面向组合优化的量子退火原型机，处于实验室验证阶段。"}],
        "equipment": [{"name": "稀释制冷机", "model": "DR-10", "serial": "DR10-2024-0001", "value": 1.8e7, "purchase_date": "2024-05-12", "location": "合肥实验室"}],
        "suppliers": [{"name": "荷兰某低温设备厂商", "item": "稀释制冷机", "share": 0.9, "country": "荷兰"}],
        "team": {"core_size": 18, "left_last_year": 3, "core_roles": [{"role": "首席科学家", "name": "叶知微"}]},
        "security": {"level": 2, "data_scale": 2, "incidents": 0},
        "milestones": [{"name": "量子比特相干时间突破", "planned": "2027-06-30", "status": "进行中"}],
    }
    with session_scope() as s:
        row = Enterprise(
            name=name, credit_code="91340100MA8QT0012X", industry="量子信息", track="量子计算",
            tech_field="量子退火、低温控制", region="安徽省合肥市", founded_year=2022, employees=92,
            listed=False, profile=profile, manager_id=manager_id,
        )
        s.add(row)
        s.flush()
        eid = row.id
        folder = settings.materials_dir / f"ent-{eid}"
        folder.mkdir(parents=True, exist_ok=True)
        ent_dict = {"name": name, "track": "量子计算", "tech_field": "量子退火、低温控制", "region": "安徽省合肥市", "founded_year": 2022, "employees": 92, "listed": False, **profile}
        for kind, title, text in (
            ("尽调报告", f"{name}尽职调查报告.txt", dd_report(ent_dict, 888)),
            ("访谈纪要", f"{name}管理层访谈纪要.txt", interview_notes(ent_dict, 888)),
        ):
            path = folder / title
            path.write_text(text, encoding="utf-8")
            s.add(Material(
                enterprise_id=eid, owner_type="enterprise", owner_id=eid, name=title, material_type=kind,
                data_level=2, detected_level=3, file_path=str(path), mime="text/plain", text=text,
                status="parsed", parsed={"chars": len(text)},
            ))
        return eid


class AutoDriver:
    def __init__(self) -> None:
        self.reviewer = uid("service")
        self.approver = uid("senior")
        self.designer = uid("designer")
        self.actuary = uid("actuary")
        self.handled: set[int] = set()
        self.hold_apps: set[int] = set()

    def step(self) -> int:
        with session_scope() as s:
            cards = list(s.scalars(select(HitlCard).where(HitlCard.status == "open")).all())
            for c in cards:
                s.expunge(c)
        n = 0
        for c in cards:
            if c.id in self.handled:
                continue
            ref = c.business_ref or {}
            if c.card_type == "decision" and ref.get("application_id") in self.hold_apps:
                continue
            if c.card_type == "claim_approval":
                # 理赔审批也自动通过，方便出结果
                pass
            keys = [o["key"] for o in (c.options or [])]
            action = "approve" if "approve" in keys else ("accept" if "accept" in keys else (keys[0] if keys else None))
            if not action:
                continue
            self.handled.add(c.id)
            user = self.approver if c.card_type in ("decision", "claim_approval") else self.reviewer
            try:
                hitl.resolve(c.id, user_id=user, action=action, comment="冒烟测试：按建议处理")
                n += 1
            except hitl.HitlError:
                continue
        return n

    def until(self, pred, timeout: float, label: str) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.step()
            if pred():
                return True
            time.sleep(0.6)
        log.warning("等待超时：%s", label)
        return False


def run_snapshot(run_id: int) -> dict:
    with session_scope() as s:
        run = s.get(Run, run_id)
        if run is None:
            return {"run_id": run_id, "missing": True}
        failed_dims = []
        for ev in s.scalars(select(Evaluation).where(Evaluation.run_id == run_id, Evaluation.kind == "final")).all():
            failed_dims = [(d["name"], d.get("value"), d.get("detail", "")[:120]) for d in (ev.dimensions or []) if not d.get("passed")]
        bad_steps = [
            {"seq": st.seq, "title": st.title, "status": st.status, "summary": (st.summary or "")[:160]}
            for st in s.scalars(select(RunStep).where(RunStep.run_id == run_id, RunStep.status.in_(["failed", "rejected"]))).all()
        ]
        return {
            "run_id": run.id,
            "agent_id": run.agent_id,
            "purpose": run.purpose,
            "title": run.title,
            "status": run.status,
            "eval_score": run.eval_score,
            "eval_passed": run.eval_passed,
            "start_mode": run.start_mode,
            "degrade_level": run.degrade_level,
            "error": (run.error or "")[-400:],
            "output_summary": (run.output or {}).get("summary") or (run.output or {}).get("decision"),
            "failed_dims": failed_dims,
            "bad_steps": bad_steps,
        }


def wait_run(drv: AutoDriver, run_id: int, timeout: float) -> dict:
    ok = drv.until(lambda: run_snapshot(run_id)["status"] in ("completed", "failed", "cancelled"), timeout, f"run {run_id}")
    snap = run_snapshot(run_id)
    snap["wait_ok"] = ok
    return snap


def record(results: list, case_id: str, agent: str, title: str, snap: dict, extra: dict | None = None) -> None:
    row = {"case_id": case_id, "agent": agent, "title": title, **snap, **(extra or {})}
    results.append(row)
    flag = "OK" if snap.get("status") == "completed" and snap.get("eval_passed") in (True, None) else "!!"
    log.info("[%s] %s %s status=%s score=%s passed=%s mode=%s/%s", flag, case_id, title, snap.get("status"), snap.get("eval_score"), snap.get("eval_passed"), snap.get("start_mode"), snap.get("degrade_level"))


# ---------------------------------------------------------------- AG-RA
def run_ra_cases(drv: AutoDriver, cases: list[dict], timeout: float, skip_track: bool) -> list[dict]:
    results = []
    name_map = ents_by_name()
    cold_id = ensure_cold_enterprise()
    name_map["量子隧穿计算有限公司"] = cold_id
    for case in cases:
        if skip_track and case.get("mode") == "track":
            log.info("跳过赛道用例 %s", case["id"])
            continue
        t0 = time.time()
        inputs: dict = {"mode": case["mode"], "lifecycle": case.get("lifecycle", True)}
        if case["mode"] == "enterprise":
            inputs["enterprise_ids"] = [name_map[case["enterprise"]]]
            if case.get("target_lines"):
                inputs["target_lines"] = case["target_lines"]
        elif case["mode"] == "need":
            inputs.update({
                "background": case["background"],
                "customer_need": case["customer_need"],
                "subject_name": case.get("subject_name") or case["title"],
                "enterprise_ids": [],
            })
        elif case["mode"] == "track":
            inputs.update({
                "track": case["track"],
                "enterprise_ids": [name_map[n] for n in case["members"]],
                "target_lines": case.get("target_lines") or [],
            })
        rid = executor.create_run(
            agent_id="AG-RA", inputs=inputs, business_ref={}, mode="auto",
            user_id=drv.designer, title=case["title"], submit=True,
        )
        snap = wait_run(drv, rid, timeout)
        snap["elapsed_s"] = round(time.time() - t0, 1)
        record(results, case["id"], "AG-RA", case["title"], snap, {"boundaries": case.get("boundary")})
    return results


# ---------------------------------------------------------------- 设计流水线（RA→CD→AP）
def run_design_pipelines(drv: AutoDriver, cases: list[dict], timeout: float) -> tuple[list[dict], list[int]]:
    results = []
    project_ids = []
    name_map = ents_by_name()
    for case in cases:
        t0 = time.time()
        ptype = case["project_type"]
        cfg = {"mode": "auto", "auto_advance": True}
        enterprise_ids: list[int] = []
        track = case.get("track")
        target_lines = case.get("target_lines") or []
        name = case.get("name") or case["title"]
        if ptype == "enterprise":
            enterprise_ids = [name_map[case["enterprise"]]]
        elif ptype == "track":
            enterprise_ids = [name_map[n] for n in case["members"]]
        elif ptype == "need":
            cfg["background"] = case["background"]
            cfg["customer_need"] = case["customer_need"]
        pid = pipeline.create_project(
            name=name, project_type=ptype, track=track, enterprise_ids=enterprise_ids,
            target_lines=target_lines, config=cfg, owner_id=drv.designer,
        )
        project_ids.append(pid)
        pref_set = False
        rate_chosen = False
        answers = case.get("preference") or {"q1": 2, "q3": 3, "q4": 4}

        def pricing_done() -> bool:
            nonlocal pref_set, rate_chosen
            need_pref = False
            financials = None
            confirm_pricing = False
            pricing_method = "weighted"
            with session_scope() as s:
                project = s.get(DesignProject, pid)
                pipeline.refresh_project(project, s)
                nodes = project.pipeline["nodes"]
                if ptype == "enterprise" and nodes["risk"].get("status") == "done" and nodes["preference"].get("status") == "waiting_input" and not pref_set:
                    financials = dict(s.get(Enterprise, enterprise_ids[0]).profile["financials"])
                    need_pref = True
                pricing = nodes.get("pricing") or {}
                # 企业/赛道定价走文档费率：waiting_confirm 且无 run，需选定费率方法后才 done
                # 企业/赛道文档费率、需求 need_pricing 都会停在 waiting_confirm，需选定方法
                if not rate_chosen and pricing.get("status") == "waiting_confirm" and pricing.get("artifact_key"):
                    confirm_pricing = True
                done = pricing.get("status") == "done"
            if need_pref and financials is not None:
                pref = {**preference_from_answers(answers), **capacity_from_financials(financials)}
                pipeline.set_preference(pid, pref, drv.designer)
                pref_set = True
            if confirm_pricing:
                from techins.productdev.flow import choose_rate
                from techins.agents.common import latest_artifact

                with session_scope() as s:
                    project = s.get(DesignProject, pid)
                    key = project.pipeline["nodes"]["pricing"].get("artifact_key")
                if key:
                    art = latest_artifact(key)
                    schemes = (art.data or {}).get("schemes") or {}
                    pricing_method = "weighted" if "weighted" in schemes else (next(iter(schemes), "weighted"))
                try:
                    choose_rate(pid, pricing_method)
                    rate_chosen = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("选定费率失败 project=%s: %s", pid, exc)
                    rate_chosen = True  # 避免死循环
            return done

        ok = drv.until(pricing_done, timeout, f"pipeline {case['id']}")
        with session_scope() as s:
            project = s.get(DesignProject, pid)
            pipeline.refresh_project(project, s)
            nodes = {k: {kk: v.get(kk) for kk in ("status", "run_id", "eval_score", "artifact_key", "version")} for k, v in project.pipeline["nodes"].items()}
        for stage, agent in (("risk", "AG-RA"), ("clause", "AG-CD"), ("pricing", "AG-AP")):
            rid = nodes.get(stage, {}).get("run_id")
            if rid:
                snap = run_snapshot(rid)
                snap["elapsed_s"] = round(time.time() - t0, 1)
                record(results, f"{case['id']}-{stage}", agent, f"{case['title']}·{stage}", snap, {"project_id": pid, "nodes": nodes[stage], "wait_ok": ok})
            elif stage == "pricing" and nodes.get("pricing", {}).get("status") == "done":
                # 文档费率路径无 AG-AP 运行，记一条合成结果
                snap = {"run_id": None, "agent_id": "AG-AP", "purpose": "document_pricing", "status": "completed", "eval_score": None, "eval_passed": True, "start_mode": "document", "degrade_level": "D0", "error": "", "output_summary": "文档费率已选定", "failed_dims": [], "bad_steps": [], "elapsed_s": round(time.time() - t0, 1), "wait_ok": ok}
                record(results, f"{case['id']}-{stage}", "AG-AP", f"{case['title']}·pricing", snap, {"project_id": pid, "nodes": nodes.get("pricing"), "document_pricing": True})
        log.info("流水线 %s 完成=%s nodes=%s", case["id"], ok, {k: v.get("status") for k, v in nodes.items() if k in ("risk", "clause", "pricing")})
    return results, project_ids


def publish_from_projects(drv: AutoDriver, project_ids: list[int]) -> list[int]:
    product_ids = []
    for pid in project_ids:
        with session_scope() as s:
            project = s.get(DesignProject, pid)
            nodes = (project.pipeline or {}).get("nodes") or {}
            if nodes.get("pricing", {}).get("status") != "done" or nodes.get("clause", {}).get("status") != "done":
                log.warning("项目 %s 未完成定价，跳过发布", pid)
                continue
            existing = nodes.get("package", {}).get("product_id")
            if existing and s.get(Product, existing) and s.get(Product, existing).status == "published":
                product_ids.append(existing)
                continue
        try:
            prod = products.assemble_package(pid, drv.designer)
            products.approve_product(prod, drv.actuary, "approve", "冒烟测试发布")
            product_ids.append(prod)
            log.info("已发布产品 %s <- project %s", prod, pid)
        except Exception as exc:  # noqa: BLE001
            log.warning("发布失败 project=%s: %s", pid, exc)
    return product_ids


# ---------------------------------------------------------------- AG-UW / RW / CL
def pick_product_for_lines(product_ids: list[int], lines: list[str]) -> tuple[int | None, list[str]]:
    """返回 (product_id, 实际可投保的险种)。优先完全覆盖，否则取交集，再否则用产品全部险种。"""
    with session_scope() as s:
        published = []
        for pid in product_ids:
            p = s.get(Product, pid)
            if p and p.status == "published" and p.lines:
                published.append((pid, list(p.lines)))
        if not published:
            return None, []
        want = set(lines)
        for pid, plines in published:
            if want.issubset(set(plines)):
                return pid, list(lines)
        best = None
        best_overlap = -1
        for pid, plines in published:
            overlap = len(want & set(plines))
            if overlap > best_overlap:
                best_overlap = overlap
                best = (pid, [l for l in lines if l in plines] or list(plines))
        if best and best_overlap > 0:
            return best
        pid, plines = published[0]
        return pid, list(plines)


def run_uw_cases(drv: AutoDriver, cases: list[dict], product_ids: list[int], timeout: float) -> tuple[list[dict], dict[str, int]]:
    results = []
    apps: dict[str, int] = {}
    for case in cases:
        t0 = time.time()
        product_id, lines = pick_product_for_lines(product_ids, case["lines"])
        if product_id is None or not lines:
            record(results, case["id"], "AG-UW", case["title"], {"status": "skipped", "error": "无可用已发布产品"}, {"boundaries": case.get("boundary")})
            continue
        app_id = demo_seed.create_app(case["enterprise"], product_id, lines, case["sum_insured"], variant=case["variant"], user_id=drv.reviewer)
        apps[case["id"]] = app_id
        if case["variant"] == "fraud":
            drv.hold_apps.add(app_id)
        rid = business.start_underwriting(app_id, drv.reviewer)
        snap = wait_run(drv, rid, timeout)
        snap["elapsed_s"] = round(time.time() - t0, 1)
        with session_scope() as s:
            app = s.get(Application, app_id)
            decision = (app.decision or {}).get("decision") if app else None
            status = app.status if app else None
        record(results, case["id"], "AG-UW", case["title"], snap, {"application_id": app_id, "app_status": status, "decision": decision, "boundaries": case.get("boundary")})
    return results, apps


def run_rw_and_claims(drv: AutoDriver, timeout: float) -> list[dict]:
    results = []
    # 给仿真事件
    for name, events in demo_seed.SIM_EVENTS.items():
        with session_scope() as s:
            e = s.scalars(select(Enterprise).where(Enterprise.name == name)).first()
            if e:
                e.profile = {**e.profile, "simulation": {"events": events}}

    with session_scope() as s:
        policies = list(s.scalars(select(Policy)).all())
        for p in policies:
            s.expunge(p)
    if not policies:
        record(results, "RW-NONE", "AG-RW", "无保单可测", {"status": "skipped", "error": "无保单"})
        return results

    # 推进仿真时钟触发预警
    with session_scope() as s:
        plans = list(s.scalars(select(MonitorPlan)).all())
        plan_ids = [p.id for p in plans]

    for plan_id in plan_ids:
        for _ in range(40):
            monitoring.tick_plan(plan_id, 1)
            drv.step()
        drv.until(lambda: not any(run_snapshot(r.id)["status"] in ("queued", "planning", "running") for r in _open_runs()), 180, f"plan {plan_id} alerts")

    # 收集 RW 运行
    with session_scope() as s:
        rw_runs = list(s.scalars(select(Run).where(Run.agent_id == "AG-RW", Run.parent_run_id.is_(None)).order_by(Run.id)).all())
        for r in rw_runs:
            s.expunge(r)
    seen_purpose = set()
    for r in rw_runs:
        key = f"RW-{r.purpose}-{r.id}"
        snap = run_snapshot(r.id)
        # 若还在跑则再等
        if snap["status"] in ("queued", "planning", "running", "waiting_human"):
            snap = wait_run(drv, r.id, min(timeout, 240))
        record(results, key, "AG-RW", r.title or r.purpose, snap, {"purpose": r.purpose})
        seen_purpose.add(r.purpose)

    # 主动补跑月报
    if "main" not in seen_purpose and policies:
        pid = policies[0].id
        rid = executor.create_run(
            agent_id="AG-RW", inputs={"policy_id": pid, "period": "month"},
            business_ref={"policy_id": pid}, mode="auto", user_id=drv.reviewer,
            title="风险管理报告（冒烟）",
        )
        snap = wait_run(drv, rid, timeout)
        record(results, "RW-REPORT-MANUAL", "AG-RW", "风险管理月报", snap, {"purpose": "main"})

    # 理赔：对已有事故立案并补材料
    with session_scope() as s:
        incidents = list(s.scalars(select(Incident)).all())
        for i in incidents:
            s.expunge(i)
        claims = list(s.scalars(select(ClaimCase)).all())
        for c in claims:
            s.expunge(c)

    # 设备损坏与数据泄露材料（复用 demo 逻辑）
    ruichi = demo_seed._ent("锐驰精密机器人科技有限公司")
    shentong = demo_seed._ent("深瞳智算科技有限公司")

    # 若还没有理赔，尝试从事故创建
    if not claims and incidents:
        for inc in incidents[:2]:
            with session_scope() as s:
                pol = s.get(Policy, inc.policy_id)
                ent = s.get(Enterprise, pol.enterprise_id) if pol else None
                ent_name = ent.name if ent else "被保险人"
            business.register_claim(
                inc.policy_id, incident_id=inc.id,
                report={"incident_type_name": inc.incident_type, "occurred_at": (inc.occurred_at or utcnow()).isoformat()},
                claimed_amount=500000, parties=[{"role": "被保险人", "name": ent_name}],
                user_id=drv.reviewer, start=False,
            )

    with session_scope() as s:
        claims = list(s.scalars(select(ClaimCase)).all())
        for c in claims:
            s.expunge(c)

    for case in claims:
        with session_scope() as s:
            pol = s.get(Policy, case.policy_id)
            policy_no, ent_id = pol.policy_no, pol.enterprise_id
        t0 = time.time()
        if ent_id == ruichi.id:
            failure, degrade = demo_seed._failure_time(case.policy_id, max(ruichi.profile["equipment"], key=lambda e: e["value"])["serial"])
            if failure:
                demo_seed.equipment_claim_materials(case.id, ruichi, policy_no, failure, degrade)
            else:
                # 无遥测停机点时造一份材料
                demo_seed.equipment_claim_materials(case.id, ruichi, policy_no, utcnow() - timedelta(days=2), utcnow() - timedelta(days=4))
            kind = "equipment"
        elif ent_id == shentong.id:
            demo_seed.breach_claim_materials(case.id, shentong, policy_no, utcnow() - timedelta(days=3), utcnow() - timedelta(days=2))
            kind = "breach"
        else:
            # 材料不全边界：只写索赔申请书
            folder = settings.materials_dir / f"claim-{case.id}"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "索赔申请书.txt"
            path.write_text(f"被保险人索赔申请（材料不全冒烟）\n保单号：{policy_no}\n", encoding="utf-8")
            with session_scope() as s:
                s.add(Material(
                    enterprise_id=ent_id, owner_type="claim", owner_id=case.id, name=path.name,
                    material_type="索赔申请书", data_level=2, detected_level=2, file_path=str(path),
                    mime="text/plain", text=path.read_text(encoding="utf-8"), status="parsed", parsed={},
                ))
            kind = "partial"
        rid = business.start_claim_run(case.id, drv.reviewer)
        snap = wait_run(drv, rid, timeout)
        snap["elapsed_s"] = round(time.time() - t0, 1)
        with session_scope() as s:
            c = s.get(ClaimCase, case.id)
            status = c.status if c else None
            suggested = c.suggested_amount if c else None
        record(results, f"CL-{kind}-{case.id}", "AG-CL", f"定损理赔·{kind}", snap, {"claim_id": case.id, "claim_status": status, "suggested_amount": suggested, "kind": kind})

    # 若完全没有理赔，主动登记一件材料不全件
    if not claims and policies:
        pol = policies[0]
        with session_scope() as s:
            ent = s.get(Enterprise, pol.enterprise_id)
            ent_name = ent.name
        cid = business.register_claim(
            pol.id, incident_id=None,
            report={"incident_type_name": "设备损坏", "description": "冒烟测试材料不全案件"},
            claimed_amount=120000,
            parties=[{"role": "被保险人", "name": ent_name}],
            user_id=drv.reviewer, start=False,
        )
        folder = settings.materials_dir / f"claim-{cid}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "索赔申请书.txt"
        path.write_text("材料不全冒烟案件\n", encoding="utf-8")
        with session_scope() as s:
            s.add(Material(
                enterprise_id=pol.enterprise_id, owner_type="claim", owner_id=cid, name=path.name,
                material_type="索赔申请书", data_level=2, detected_level=2, file_path=str(path),
                mime="text/plain", text="材料不全冒烟案件\n", status="parsed", parsed={},
            ))
        rid = business.start_claim_run(cid, drv.reviewer)
        snap = wait_run(drv, rid, timeout)
        record(results, "CL-PARTIAL-MANUAL", "AG-CL", "定损理赔·材料不全", snap, {"claim_id": cid, "kind": "partial"})

    with session_scope() as s:
        alerts = [(a.alert_no, a.level, a.title, a.status) for a in s.scalars(select(Alert)).all()]
        incs = [(i.incident_no, i.incident_type, i.status) for i in s.scalars(select(Incident)).all()]
    log.info("ALERTS %s", alerts)
    log.info("INCIDENTS %s", incs)
    return results


def _open_runs():
    with session_scope() as s:
        rows = list(s.scalars(select(Run).where(Run.status.in_(["queued", "planning", "running", "waiting_human"]), Run.parent_run_id.is_(None))).all())
        for r in rows:
            s.expunge(r)
        return rows


def summarize(all_results: list[dict]) -> dict:
    by_agent: dict[str, dict] = {}
    for r in all_results:
        a = r.get("agent") or "?"
        bucket = by_agent.setdefault(a, {"total": 0, "completed": 0, "passed": 0, "failed": 0, "skipped": 0})
        bucket["total"] += 1
        st = r.get("status")
        if st == "skipped":
            bucket["skipped"] += 1
        elif st == "completed":
            bucket["completed"] += 1
            if r.get("eval_passed") in (True, None):
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1
        else:
            bucket["failed"] += 1
    return {
        "total": len(all_results),
        "completed": sum(1 for r in all_results if r.get("status") == "completed"),
        "eval_passed": sum(1 for r in all_results if r.get("status") == "completed" and r.get("eval_passed") in (True, None)),
        "failed_or_incomplete": sum(1 for r in all_results if r.get("status") not in ("completed", "skipped")),
        "by_agent": by_agent,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--skip-track", action="store_true")
    parser.add_argument("--ra-only", action="store_true")
    parser.add_argument("--design-only", action="store_true")
    parser.add_argument("--service-only", action="store_true")
    args = parser.parse_args()

    cases = load_cases()
    executor.start()
    drv = AutoDriver()
    all_results: list[dict] = []
    t0 = time.time()

    log.info("数据目录 %s", settings.data_dir)
    ensure_cold_enterprise()

    if not args.service_only and not args.design_only:
        all_results += run_ra_cases(drv, cases["agents"]["AG-RA"]["cases"], args.timeout, args.skip_track)

    product_ids: list[int] = []
    if not args.ra_only and not args.service_only:
        design_results, project_ids = run_design_pipelines(drv, cases["agents"]["AG-CD"]["cases"], args.timeout)
        all_results += design_results
        product_ids = publish_from_projects(drv, project_ids)

    if not args.ra_only and not args.design_only:
        if not product_ids:
            with session_scope() as s:
                product_ids = [p.id for p in s.scalars(select(Product).where(Product.status == "published")).all()]
        if not product_ids:
            log.info("无已发布产品，补跑一条赛道流水线用于服务链路")
            _, pids = run_design_pipelines(drv, [c for c in cases["agents"]["AG-CD"]["cases"] if c["project_type"] == "track"][:1], args.timeout)
            product_ids = publish_from_projects(drv, pids)
        uw_results, _apps = run_uw_cases(drv, cases["agents"]["AG-UW"]["cases"], product_ids, args.timeout)
        all_results += uw_results

        def policies_ready() -> bool:
            with session_scope() as s:
                return len(s.scalars(select(Policy)).all()) >= 1 and len(s.scalars(select(MonitorPlan)).all()) >= 1

        drv.until(policies_ready, 300, "核保出单")
        all_results += run_rw_and_claims(drv, args.timeout)

    summary = summarize(all_results)
    report = {
        "elapsed_s": round(time.time() - t0, 1),
        "data_dir": str(settings.data_dir),
        "summary": summary,
        "results": all_results,
        "open_cards": [],
        "products": [],
        "policies": [],
        "claims": [],
    }
    with session_scope() as s:
        report["open_cards"] = [(c.id, c.card_type, c.title) for c in s.scalars(select(HitlCard).where(HitlCard.status == "open")).all()]
        report["products"] = [(p.id, p.name, p.status, p.lines) for p in s.scalars(select(Product)).all()]
        report["policies"] = [(p.id, p.policy_no, p.premium) for p in s.scalars(select(Policy)).all()]
        report["claims"] = [(c.id, c.claim_no, c.status, c.claimed_amount, c.suggested_amount) for c in s.scalars(select(ClaimCase)).all()]
        report["runs"] = [
            {
                "id": r.id, "agent": r.agent_id, "purpose": r.purpose, "status": r.status,
                "score": r.eval_score, "passed": r.eval_passed, "mode": r.start_mode, "degrade": r.degrade_level,
                "title": r.title, "error": (r.error or "")[-200:],
            }
            for r in s.scalars(select(Run).where(Run.parent_run_id.is_(None)).order_by(Run.id)).all()
        ]

    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary, "elapsed_s": report["elapsed_s"], "report": str(REPORT_PATH), "products": report["products"], "policies": report["policies"], "claims": report["claims"]}, ensure_ascii=False, indent=2))
    # 退出码：有失败则 1
    return 0 if summary["failed_or_incomplete"] == 0 else 1


if __name__ == "__main__":
    # 清理上面故意写砸的 noop——实际 main 里那段 policies_ready 前的 noop 要删掉
    sys.exit(main())
