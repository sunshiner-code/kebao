"""产品设计流水线冒烟测试：风险评估 → 条款设计 → 精算定价（含修订联动），自动处理人审卡片。"""

import json
import sys
import time

from sqlalchemy import select

from techins.seed.runner import seed_all

seed_all()
import techins.agents  # noqa: E402,F401
from techins.agents.clause_design.library import capacity_from_financials, preference_from_answers  # noqa: E402
from techins.db import session_scope  # noqa: E402
from techins.harness import hitl  # noqa: E402
from techins.harness.executor import executor  # noqa: E402
from techins.models import DesignProject, Enterprise, Evaluation, HitlCard, Run, RunStep  # noqa: E402
from techins.services import pipeline  # noqa: E402

ent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 900
executor.start()
pid = pipeline.create_project(name="冒烟测试项目", project_type="enterprise", track=None, enterprise_ids=[ent_id], target_lines=[], config={"mode": "auto", "auto_advance": True}, owner_id=1)
with session_scope() as s:
    fin = s.get(Enterprise, ent_id).profile["financials"]
pref = {**preference_from_answers({"q1": 2, "q3": 3, "q4": 4}), **capacity_from_financials(fin)}
t0 = time.time()
pref_set = False
handled = set()
while time.time() - t0 < timeout:
    time.sleep(1.5)
    with session_scope() as s:
        project = s.get(DesignProject, pid)
        pipeline.refresh_project(project, s)
        nodes = project.pipeline["nodes"]
        cards = s.scalars(select(HitlCard).where(HitlCard.status == "open")).all()
        card_info = [(c.id, c.card_type, c.title, [o["key"] for o in c.options]) for c in cards]
    if nodes["risk"].get("status") == "done" and nodes["preference"].get("status") == "waiting_input" and not pref_set:
        pipeline.set_preference(pid, pref, 1)
        pref_set = True
    for cid, ctype, title, opts in card_info:
        if cid in handled:
            continue
        handled.add(cid)
        print(f"[{time.time() - t0:6.1f}s] 处理人审：{title} -> approve")
        hitl.resolve(cid, user_id=2, action="approve", comment="")
    if nodes["pricing"].get("status") == "done":
        break
print(json.dumps({k: {kk: v.get(kk) for kk in ("status", "version", "eval_score", "run_id")} for k, v in nodes.items()}, ensure_ascii=False, indent=1))
with session_scope() as s:
    project = s.get(DesignProject, pid)
    print("revisions", json.dumps(project.pipeline.get("revisions"), ensure_ascii=False)[:800])
    for run in s.scalars(select(Run).where(Run.parent_run_id.is_(None))).all():
        print("RUN", run.id, run.agent_id, run.purpose, run.status, run.eval_score, run.eval_passed, run.degrade_level, (run.error or "")[-600:])
        for ev in s.scalars(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.kind == "final")).all():
            print("   EVAL", ev.score, ev.passed, [(d["name"], d["value"], d["passed"]) for d in ev.dimensions if not d["passed"]])
        for st in s.scalars(select(RunStep).where(RunStep.run_id == run.id, RunStep.status.in_(["failed", "rejected", "warning"]))).all():
            print("   STEP", st.seq, st.title, st.summary[:200])
print("elapsed", round(time.time() - t0, 1))
