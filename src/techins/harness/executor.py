"""运行执行器：按优先级排队、工作线程执行、检查点恢复、暂停与取消。"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time

from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import Run
from .agent import get_profile
from .context import RunContext
from .events import event_bus
from .runtime import runtime

log = logging.getLogger("techins.executor")

PRIORITY = {"AG-CL": 0, "AG-RW": 1, "AG-UW": 1, "AG-AP": 3, "AG-CD": 3, "AG-RA": 3}


class RunExecutor:
    def __init__(self, workers: int) -> None:
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._workers: list[threading.Thread] = []
        self._size = workers
        self._active: set[int] = set()
        self._deferred: set[int] = set()
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self._size):
            t = threading.Thread(target=self._worker, name=f"run-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        self._recover()

    def _recover(self) -> None:
        with session_scope() as s:
            rows = s.scalars(select(Run).where(Run.status.in_(["queued", "planning", "running"]), Run.parent_run_id.is_(None))).all()
            ids = [r.id for r in rows]
        for rid in ids:
            self.submit(rid)

    def create_run(
        self,
        *,
        agent_id: str,
        inputs: dict,
        business_ref: dict,
        mode: str = "auto",
        user_id: int | None = None,
        title: str = "",
        purpose: str = "main",
        parent_run_id: int | None = None,
        budget: dict | None = None,
        submit: bool = True,
    ) -> int:
        from ..services import agent_config, quota

        inputs = dict(inputs)
        if parent_run_id is None and user_id and not (business_ref or {}).get("eval_job"):
            inputs.update(quota.check_start(user_id, agent_id))
        version_label = None
        if "_config" not in inputs:
            chosen, cfg = agent_config.choose_for_new_run(agent_id, f"{agent_id}|{title}|{time.time_ns()}")
            if cfg:
                inputs["_config"] = {k: cfg.get(k) for k in ("pass_score", "max_reflection_rounds", "planner", "plan_confirm", "allow_cloud", "experience_injection", "tool_blacklist", "budget")}
                inputs["_config_id"] = chosen.id if chosen else None
                version_label = agent_config.version_label(chosen, get_profile(agent_id).version)
                if chosen is not None and chosen.status == "canary":
                    inputs["_canary"] = True
        else:
            version_label = f"{get_profile(agent_id).version}+{inputs.get('_config_label', 'candidate')}"
        profile = agent_config.apply(get_profile(agent_id, purpose), inputs.get("_config") or {})
        start = profile.start_mode(inputs)
        with session_scope() as s:
            run = Run(
                agent_id=agent_id,
                agent_version=version_label or profile.version,
                purpose=purpose,
                title=title or profile.name,
                status="queued",
                mode=mode,
                start_mode=start.get("start_mode", "warm"),
                degrade_level=start.get("degrade_level", "D0"),
                business_ref=business_ref,
                input={**inputs, "_start_reasons": start.get("reasons", [])},
                budget=budget or {},
                parent_run_id=parent_run_id,
                created_by=user_id,
            )
            s.add(run)
            s.flush()
            run_id = run.id
        event_bus.emit(run_id, "run.created", {"agent_id": agent_id, "title": title, "start_mode": start.get("start_mode"), "degrade_level": start.get("degrade_level"), "reasons": start.get("reasons", [])})
        if submit:
            self.submit(run_id)
        return run_id

    def submit(self, run_id: int) -> None:
        with session_scope() as s:
            run = s.get(Run, run_id)
            priority = PRIORITY.get(run.agent_id, 5) if run else 9
        self._queue.put((priority, next(self._counter), run_id))
        event_bus.emit(run_id, "run.queued", {"position": self._queue.qsize()})

    def _worker(self) -> None:
        while True:
            priority, _, run_id = self._queue.get()
            with self._lock:
                if run_id in self._active:
                    # 运行仍在上一次执行的收尾阶段（例如刚挂起等待人审即被处理），待其退出后再执行
                    self._deferred.add(run_id)
                    self._queue.task_done()
                    continue
                self._active.add(run_id)
            try:
                self.run_inline(run_id)
            except Exception:  # noqa: BLE001
                log.exception("worker failed on run %s", run_id)
            finally:
                with self._lock:
                    self._active.discard(run_id)
                    again = run_id in self._deferred
                    self._deferred.discard(run_id)
                if again:
                    self._queue.put((priority, next(self._counter), run_id))
                self._queue.task_done()

    def run_inline(self, run_id: int) -> str:
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None or run.status in ("completed", "failed", "cancelled", "waiting_human"):
                return run.status if run else "missing"
            s.expunge(run)
        from ..services import agent_config

        profile = agent_config.apply(get_profile(run.agent_id, run.purpose), (run.input or {}).get("_config") or {})
        ctx = RunContext(run, profile)
        ctx.restore()
        return runtime.execute(ctx)

    def control(self, run_id: int, command: str) -> str:
        with session_scope() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise ValueError("运行不存在")
            if command == "resume":
                from .timing import apply_status

                if run.status != "paused":
                    raise ValueError("只有已暂停的运行可以继续")
                apply_status(run, "queued")
                run.control = {}
                resubmit = True
            elif command in ("pause", "cancel"):
                if run.status in ("completed", "failed", "cancelled"):
                    raise ValueError("运行已结束")
                if run.status in ("waiting_human", "paused", "queued") and command == "cancel":
                    from .timing import apply_status

                    apply_status(run, "cancelled")
                    run.control = {}
                    resubmit = False
                else:
                    run.control = {"command": command}
                    resubmit = False
            elif command in ("mode:auto", "mode:semi"):
                run.mode = command.split(":")[1]
                resubmit = False
            else:
                raise ValueError(f"未知指令：{command}")
            status = run.status
        event_bus.emit(run_id, "control", {"command": command})
        if resubmit:
            self.submit(run_id)
        return status


executor = RunExecutor(settings.executor_workers)
