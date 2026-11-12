"""初始化演示数据并跑通全生命周期（产品设计 → 核保出单 → 监测预警 → 事故响应 → 定损理赔）。

用法：
    TECHINS_DISABLE_LLM=1 python scripts/bootstrap_demo.py      # 使用内置推理引擎（快速、离线）
    python scripts/bootstrap_demo.py                            # 使用 .env 中配置的大模型
"""

import json
import logging
import sys

from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

from techins.seed.runner import seed_all  # noqa: E402

seed_all()
import techins.agents  # noqa: E402,F401
from techins.db import session_scope  # noqa: E402
from techins.models import Alert, Application, ClaimCase, Evaluation, HitlCard, Incident, Policy, Product, Run, RunStep  # noqa: E402
from techins.seed.demo import bootstrap_demo  # noqa: E402

result = bootstrap_demo(float(sys.argv[1]) if len(sys.argv) > 1 else 1500)
print(json.dumps(result, ensure_ascii=False, indent=1))
with session_scope() as s:
    print("PRODUCTS", [(p.id, p.name, p.status, p.lines) for p in s.scalars(select(Product)).all()])
    print("APPS", [(a.id, a.app_no, a.status, (a.decision or {}).get("decision")) for a in s.scalars(select(Application)).all()])
    print("POLICIES", [(p.id, p.policy_no, p.premium) for p in s.scalars(select(Policy)).all()])
    print("ALERTS", [(a.alert_no, a.level, a.title, a.status) for a in s.scalars(select(Alert)).all()])
    print("INCIDENTS", [(i.incident_no, i.incident_type, i.status) for i in s.scalars(select(Incident)).all()])
    print("CLAIMS", [(c.claim_no, c.status, c.claimed_amount, c.suggested_amount) for c in s.scalars(select(ClaimCase)).all()])
    print("OPEN CARDS", [(c.id, c.card_type, c.title) for c in s.scalars(select(HitlCard).where(HitlCard.status == "open")).all()])
    for run in s.scalars(select(Run).where(Run.parent_run_id.is_(None))).all():
        flag = "" if run.status in ("completed", "waiting_human") and (run.eval_passed in (True, None)) else "  <<<"
        print("RUN", run.id, run.agent_id, run.purpose, run.status, run.eval_score, run.eval_passed, run.degrade_level, (run.error or "")[-500:], flag)
        if flag:
            for ev in s.scalars(select(Evaluation).where(Evaluation.run_id == run.id, Evaluation.kind == "final")).all():
                print("   EVAL", ev.score, ev.passed, [(d["name"], d["value"], d["detail"][:120]) for d in ev.dimensions if not d["passed"]])
            for st in s.scalars(select(RunStep).where(RunStep.run_id == run.id, RunStep.status.in_(["failed", "rejected"]))).all():
                print("   STEP", st.seq, st.title, st.summary[:300])
