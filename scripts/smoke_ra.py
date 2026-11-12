import json
import sys
import time

from sqlalchemy import select

from techins.seed.runner import seed_all

seed_all()
import techins.agents  # noqa: E402,F401
from techins.db import session_scope  # noqa: E402
from techins.harness.executor import executor  # noqa: E402
from techins.models import EgressLog, Evaluation, Reflection, Run, RunStep  # noqa: E402

ent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
t0 = time.time()
rid = executor.create_run(agent_id="AG-RA", inputs={"mode": "enterprise", "enterprise_ids": [ent_id], "lifecycle": True}, business_ref={}, submit=False, user_id=1)
status = executor.run_inline(rid)
print("status", status, "elapsed", round(time.time() - t0, 1))
with session_scope() as s:
    run = s.get(Run, rid)
    print("run", run.status, run.eval_score, run.eval_passed, run.degrade_level, run.start_mode, json.dumps(run.usage, ensure_ascii=False))
    print("error", (run.error or "")[:2000])
    print("output", json.dumps(run.output, ensure_ascii=False)[:800])
    for st in s.scalars(select(RunStep).where(RunStep.run_id == rid).order_by(RunStep.seq)).all():
        print(f"  [{st.seq:03d}] {st.step_type:11s} {st.zone:8s} {st.status:8s} {st.title} :: {st.summary[:140]}")
    for ev in s.scalars(select(Evaluation).where(Evaluation.run_id == rid)).all():
        print("EVAL", ev.kind, ev.target, ev.score, ev.passed, [ (d['name'], d['value'], d['passed']) for d in ev.dimensions])
    for rf in s.scalars(select(Reflection).where(Reflection.run_id == rid)).all():
        print("REFLECT", rf.round, rf.trigger, rf.categories, rf.plan)
    for eg in s.scalars(select(EgressLog).where(EgressLog.run_id == rid)).all():
        print("EGRESS", eg.task, eg.source_level, eg.rules_hit, eg.placeholder_count, eg.scan_result, eg.content_preview[:300])
    children = s.scalars(select(Run).where(Run.parent_run_id == rid)).all()
    print("children", [(c.id, c.title, c.status) for c in children])
