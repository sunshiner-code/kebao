"""定时任务：周期报告、续保提醒、知识成熟度重算、评测、进化分析与灰度检查、事后评估回填、数据源探测、
连接器质量、审批超时、授权到期、停售条件、报表订阅、异常行为、留存销毁、反欺诈模型重训；执行记录与失败重试。"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta
from typing import Callable

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..models import JobRun, MonitorPlan, Policy, ScheduledJob

log = logging.getLogger("techins.scheduler")


def _rw_reports(params: dict) -> dict:
    from ..harness.executor import executor
    from ..models import Enterprise

    with session_scope() as s:
        targets = [(p.id, s.get(Enterprise, p.enterprise_id).name) for p in s.scalars(select(Policy).where(Policy.status == "active")).all() if s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == p.id, MonitorPlan.status == "active")).first()]
    runs = [executor.create_run(agent_id="AG-RW", inputs={"policy_id": pid, "period": params.get("period", "month")}, business_ref={"policy_id": pid}, mode="auto", title=f"风险管理报告（定时）：{name}") for pid, name in targets[: int(params.get("max", 20))]]
    return {"runs": runs, "policies": len(targets)}


def _maturity(params: dict) -> dict:
    from ..knowledge.maturity import all_maturity

    rows = all_maturity()
    return {"lines": len(rows), "warm": sum(1 for r in rows if (r.get("overall") or r.get("verdict", {}).get("pricing")) in ("warm",)), "snapshot": [{k: r.get(k) for k in ("line", "name", "scores")} for r in rows]}


def _eval(params: dict) -> dict:
    from ..models import EvalSet
    from . import evals

    jobs = []
    with session_scope() as s:
        sets = s.scalars(select(EvalSet).where(EvalSet.split == params.get("split", "dev"))).all()
        latest: dict[str, int] = {}
        for e in sets:
            if e.agent_id in (params.get("agents") or [e.agent_id]):
                latest[e.agent_id] = max(latest.get(e.agent_id, 0), e.id)
    for agent, sid in latest.items():
        jobs.append(evals.create_job(sid, label=f"定时评测（{agent}）")["id"])
    return {"jobs": jobs}


def _evolution(params: dict) -> dict:
    from . import evals

    created = evals.analyze()
    return {"proposals": [p["proposal_no"] for p in created]}


def _canary(params: dict) -> dict:
    from . import evals

    return {"actions": evals.check_canaries()}


def _posthoc(params: dict) -> dict:
    from . import evals

    return {"backfilled": evals.backfill_posthoc()}


def _sources(params: dict) -> dict:
    from . import ops

    return {"probes": ops.probe_sources()}


def _connectors(params: dict) -> dict:
    from . import connectors, ops

    return {"pulled": connectors.pull_due(), "connectors": ops.sync_connectors()}


def _housekeeping(params: dict) -> dict:
    from ..harness import hitl
    from . import approvals, customers, iam

    return {"approval_timeouts": approvals.check_timeouts(), "grants_expired": iam.expire_grants(), "authorizations_expired": customers.expire_authorizations(), "cards_expired": hitl.expire_overdue()}


def _products(params: dict) -> dict:
    from . import product_admin

    return {"went_live": product_admin.go_live_due(), "stop_checks": product_admin.check_stop_conditions()}


def _renewals(params: dict) -> dict:
    from . import policy_admin

    return {"created": policy_admin.scan_renewals()}


def _subscriptions(params: dict) -> dict:
    from . import dashboards

    return {"sent": dashboards.send_subscriptions()}


def _security(params: dict) -> dict:
    from . import ops

    return {"anomalies": ops.detect_audit_anomalies(), "system_alerts": ops.push_system_alerts()}


def _retention(params: dict) -> dict:
    from . import ops

    return ops.retention_cleanup()


def _retrain(params: dict) -> dict:
    """反欺诈模型冠军挑战者：用最新承保历史训练挑战者，区分度不低于冠军时替换。"""
    from ..analytics.fraud import FraudModels, fraud_models

    fraud_models.ensure()
    champion = dict(fraud_models.metrics)
    challenger = FraudModels()
    challenger.ensure()
    promoted = challenger.metrics["auc"] >= champion.get("auc", 0) - float(params.get("tolerance", 0.01))
    if promoted:
        for k, v in challenger.__dict__.items():
            if k != "_lock":
                setattr(fraud_models, k, v)
    return {"champion": champion, "challenger": challenger.metrics, "promoted": promoted}


JOB_TYPES: dict[str, tuple[str, Callable[[dict], dict]]] = {
    "rw_reports": ("周期风险管理报告", _rw_reports),
    "renewal_scan": ("续保提醒与到期处理", _renewals),
    "kb_maturity": ("险种成熟度重算", _maturity),
    "eval_run": ("智能体定时评测", _eval),
    "evolution": ("进化分析（生成改进提案）", _evolution),
    "canary_check": ("灰度指标检查与自动回滚", _canary),
    "posthoc": ("事后评估回填（数据回流）", _posthoc),
    "source_probe": ("外部数据源健康探测", _sources),
    "connector_quality": ("企业数据接入质量", _connectors),
    "housekeeping": ("审批超时、授权到期与人审超时", _housekeeping),
    "product_watch": ("产品上线与停售条件检查", _products),
    "report_subscriptions": ("自定义报表订阅推送", _subscriptions),
    "security_watch": ("异常行为与系统告警", _security),
    "retention": ("临时工作区留存到期销毁", _retention),
    "model_retrain": ("反欺诈模型重训（冠军挑战者）", _retrain),
}

DEFAULT_JOBS = [
    ("housekeeping", "housekeeping", 5, {}),
    ("connector-quality", "connector_quality", 15, {}),
    ("source-probe", "source_probe", 60, {}),
    ("security-watch", "security_watch", 10, {}),
    ("product-watch", "product_watch", 60, {}),
    ("canary-check", "canary_check", 30, {}),
    ("posthoc", "posthoc", 360, {}),
    ("renewal-scan", "renewal_scan", 720, {}),
    ("evolution", "evolution", 1440, {}),
    ("report-subscriptions", "report_subscriptions", 1440, {}),
    ("kb-maturity", "kb_maturity", 1440, {}),
    ("retention", "retention", 1440, {}),
    ("rw-monthly-reports", "rw_reports", 43200, {"period": "month", "max": 20}),
    ("eval-weekly", "eval_run", 10080, {"split": "dev"}),
    ("model-retrain", "model_retrain", 10080, {"tolerance": 0.01}),
]


def seed_jobs() -> None:
    with session_scope() as s:
        have = {j.code for j in s.scalars(select(ScheduledJob)).all()}
        for code, jtype, minutes, params in DEFAULT_JOBS:
            if code in have:
                continue
            first = utcnow() + timedelta(minutes=min(minutes, 10))
            s.add(ScheduledJob(code=code, name=JOB_TYPES[jtype][0], job_type=jtype, interval_minutes=minutes, params=params, enabled=jtype not in ("rw_reports", "eval_run", "model_retrain"), next_run_at=first))


def run_job(job_id: int, trigger: str = "schedule") -> dict:
    with session_scope() as s:
        job = s.get(ScheduledJob, job_id)
        if job is None:
            raise ValueError("定时任务不存在")
        jtype, params, retries = job.job_type, dict(job.params or {}), job.max_retries
        job.last_run_at = utcnow()
        job.next_run_at = utcnow() + timedelta(minutes=job.interval_minutes)
    fn = JOB_TYPES[jtype][1]
    attempt, last_err = 0, None
    while attempt <= retries:
        attempt += 1
        with session_scope() as s:
            jr = JobRun(job_id=job_id, trigger=trigger, status="running", attempt=attempt)
            s.add(jr)
            s.flush()
            jr_id = jr.id
        try:
            out = fn(params)
            with session_scope() as s:
                jr = s.get(JobRun, jr_id)
                jr.status, jr.output, jr.ended_at = "success", _jsonable(out), utcnow()
                s.get(ScheduledJob, job_id).last_status = "success"
            return {"status": "success", "attempt": attempt, "output": _jsonable(out)}
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            log.exception("job %s failed (attempt %s)", job_id, attempt)
            with session_scope() as s:
                jr = s.get(JobRun, jr_id)
                jr.status, jr.error, jr.ended_at = "failed", last_err[:2000], utcnow()
                s.get(ScheduledJob, job_id).last_status = "failed"
            time.sleep(min(5, attempt))
    with session_scope() as s:
        from .notify import notify

        job = s.get(ScheduledJob, job_id)
        notify(s, kind="system", title=f"定时任务失败：{job.name}", body=(last_err or "")[:300], link="/admin/jobs", level="error", role="admin")
    return {"status": "failed", "attempt": attempt, "error": last_err}


def _jsonable(obj):
    from ..jsonutil import jsonable

    return jsonable(obj)


class JobScheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running: set[int] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="job-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def trigger(self, job_id: int) -> None:
        threading.Thread(target=self._run, args=(job_id, "manual"), name=f"job-{job_id}", daemon=True).start()

    def _run(self, job_id: int, trigger: str) -> None:
        with self._lock:
            if job_id in self._running:
                return
            self._running.add(job_id)
        try:
            run_job(job_id, trigger)
        finally:
            with self._lock:
                self._running.discard(job_id)

    def running(self) -> set[int]:
        with self._lock:
            return set(self._running)

    def _loop(self) -> None:
        while not self._stop.wait(30):
            try:
                with session_scope() as s:
                    due = [j.id for j in s.scalars(select(ScheduledJob).where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= utcnow())).all()]
                for jid in due:
                    self._run(jid, "schedule")
            except Exception:  # noqa: BLE001
                log.exception("scheduler loop failed")


job_scheduler = JobScheduler()
