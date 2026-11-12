"""运行事件总线：事件按运行编号单调递增编号并持久化，前端事件流据此续传与回放。"""

from __future__ import annotations

import threading
from collections import defaultdict

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..models import RunEvent


class EventBus:
    def __init__(self) -> None:
        self._seq: dict[int, int] = {}
        self._locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._conditions: dict[int, threading.Condition] = defaultdict(threading.Condition)
        self._guard = threading.Lock()

    def _next_seq(self, run_id: int) -> int:
        if run_id not in self._seq:
            with session_scope() as s:
                current = s.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
            self._seq[run_id] = int(current or 0)
        self._seq[run_id] += 1
        return self._seq[run_id]

    def emit(self, run_id: int, event_type: str, payload: dict | None = None) -> int:
        with self._guard:
            lock = self._locks[run_id]
        with lock:
            seq = self._next_seq(run_id)
            with session_scope() as s:
                s.add(RunEvent(run_id=run_id, seq=seq, event_type=event_type, payload=payload or {}, ts=utcnow()))
        cond = self._conditions[run_id]
        with cond:
            cond.notify_all()
        return seq

    def wait(self, run_id: int, timeout: float) -> None:
        cond = self._conditions[run_id]
        with cond:
            cond.wait(timeout)

    def fetch(self, run_id: int, after: int = 0, limit: int = 500) -> list[dict]:
        with session_scope() as s:
            rows = s.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq).limit(limit)
            ).all()
            return [
                {"seq": r.seq, "type": r.event_type, "payload": r.payload, "ts": r.ts.isoformat() + "Z"}
                for r in rows
            ]


event_bus = EventBus()
