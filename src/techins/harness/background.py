"""后台任务：长计算（模型训练、蒙特卡洛模拟、因果发现）在后台线程执行，完成后以通知注入主循环。"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Callable

from ..db import session_scope, utcnow
from ..models import BackgroundTask
from ..textutil import truncate
from .events import event_bus

if TYPE_CHECKING:
    from .context import RunContext


class BackgroundManager:
    def __init__(self, workers: int = 3) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bg-task")
        self._futures: dict[int, Future] = {}
        self._results: dict[int, Any] = {}
        self._lock = threading.Lock()

    def submit(self, ctx: "RunContext", name: str, fn: Callable[..., Any], **kwargs: Any) -> int:
        with session_scope() as s:
            task = BackgroundTask(run_id=ctx.run_id, name=name)
            s.add(task)
            s.flush()
            task_id = task.id
        run_id = ctx.run_id

        def progress(fraction: float, message: str = "") -> None:
            with session_scope() as s2:
                t = s2.get(BackgroundTask, task_id)
                if t is not None:
                    t.progress = round(max(0.0, min(1.0, fraction)), 3)
            event_bus.emit(run_id, "background.progress", {"task_id": task_id, "name": name, "progress": round(fraction, 3), "message": message})

        def runner() -> Any:
            started = time.perf_counter()
            try:
                result = fn(progress=progress, **kwargs)
                ref = ctx.archive(f"bg-{task_id}", result)
                with session_scope() as s3:
                    t = s3.get(BackgroundTask, task_id)
                    t.status, t.progress, t.result_ref, t.ended_at = "done", 1.0, ref, utcnow()
                    t.summary = truncate(str(result.get("summary", "")) if isinstance(result, dict) else "", 500)
                ctx.add_gpu_seconds(time.perf_counter() - started)
                event_bus.emit(run_id, "background.finished", {"task_id": task_id, "name": name, "status": "done"})
                with self._lock:
                    self._results[task_id] = result
                return result
            except Exception as exc:  # noqa: BLE001
                with session_scope() as s3:
                    t = s3.get(BackgroundTask, task_id)
                    t.status, t.ended_at, t.summary = "failed", utcnow(), truncate(f"{exc}\n{traceback.format_exc()}", 1500)
                event_bus.emit(run_id, "background.finished", {"task_id": task_id, "name": name, "status": "failed", "error": str(exc)})
                raise

        with self._lock:
            self._futures[task_id] = self._pool.submit(runner)
        event_bus.emit(run_id, "background.started", {"task_id": task_id, "name": name})
        return task_id

    def wait(self, task_id: int, timeout: float = 3600.0) -> Any:
        with self._lock:
            future = self._futures.get(task_id)
            if task_id in self._results:
                return self._results[task_id]
        if future is None:
            with session_scope() as s:
                t = s.get(BackgroundTask, task_id)
                if t is not None and t.status == "done" and t.result_ref:
                    from .context import RunContext

                    return RunContext.load_archive(t.result_ref)
            raise RuntimeError(f"后台任务 {task_id} 不可恢复（服务可能已重启），需要重新提交")
        return future.result(timeout=timeout)

    def finished_unnotified(self, ctx: "RunContext") -> list[dict]:
        notified = set(ctx.state.get("notified_background", []))
        with session_scope() as s:
            rows = s.query(BackgroundTask).filter(BackgroundTask.run_id == ctx.run_id, BackgroundTask.status.in_(["done", "failed"])).all()
            out = [{"id": r.id, "name": r.name, "status": r.status, "summary": r.summary, "ref": r.result_ref} for r in rows if r.id not in notified]
        ctx.state["notified_background"] = sorted(notified | {r["id"] for r in out})
        return out


background_manager = BackgroundManager()
