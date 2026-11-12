"""运行上下文：一次智能体运行的工作记忆、运行状态、预算、工作区与检查点。"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import settings
from ..db import session_scope, utcnow
from ..models import Run, RunStep
from ..privacy.vault import TokenVault
from ..textutil import estimate_tokens, truncate
from .events import event_bus
from .todo import TodoList

if TYPE_CHECKING:
    from .agent import AgentProfile

log = logging.getLogger("techins.harness")

DEFAULT_BUDGET = {"cloud_tokens": 3_000_000, "cloud_cost": 200.0, "gpu_seconds": 7200.0, "steps": 600, "wall_seconds": 4 * 3600}


class RunContext:
    def __init__(self, run: Run, profile: "AgentProfile") -> None:
        self.run_id: int = run.id
        self.parent_run_id: int | None = run.parent_run_id
        self.root_run_id: int = run.business_ref.get("root_run_id", run.id) if run.business_ref else run.id
        self.agent_id: str = run.agent_id
        self.profile = profile
        self.mode: str = run.mode
        self.inputs: dict = dict(run.input or {})
        self.business_ref: dict = dict(run.business_ref or {})
        self.created_by: int | None = run.created_by
        self.workspace: dict[str, Any] = {}
        self.state: dict[str, Any] = {
            "skills_loaded": [],
            "todo_results": {},
            "hitl_results": {},
            "round": 0,
            "approvals": {},
            "reflection_rounds": 0,
            "eval_history": [],
            "memory_injected": [],
            "degrade_notes": [],
            "start_mode": run.start_mode,
            "degrade_level": run.degrade_level,
            "notified_background": [],
            "consumed_mail": [],
            "final_attempts": 0,
        }
        self.messages: list[dict] = []
        self.todos = TodoList()
        self.usage: dict[str, float] = dict(run.usage or {}) or {
            "cloud_tokens_in": 0,
            "cloud_tokens_out": 0,
            "cloud_cost": 0.0,
            "cloud_calls": 0,
            "private_tokens_in": 0,
            "private_tokens_out": 0,
            "private_calls": 0,
            "gpu_seconds": 0.0,
            "steps": 0,
            "tool_calls": 0,
            "egress": 0,
        }
        self.eval_mode: bool = bool(self.business_ref.get("eval_job"))
        self.config_overrides: dict = dict(self.inputs.get("_config") or {})
        self.budget = {**DEFAULT_BUDGET, **(self.config_overrides.get("budget") or {}), **(run.budget or {})}
        self.vault = TokenVault(f"run-{self.root_run_id}")
        self.current_step_id: int | None = None
        self._step_seq = 0
        self.started_monotonic = time.monotonic()
        self._usage_lock = threading.Lock()
        self.dir: Path = settings.workspaces_dir / f"run-{self.root_run_id}" / f"run-{self.run_id}"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- 事件与步骤
    def emit(self, event_type: str, payload: dict | None = None) -> None:
        payload = dict(payload or {})
        if event_type == "egress.done":
            self.usage["egress"] = self.usage.get("egress", 0) + 1
        event_bus.emit(self.run_id, event_type, payload)
        if self.parent_run_id and event_type in ("tool.finished", "run.finished", "model.routed"):
            event_bus.emit(self.parent_run_id, "subtask.progress", {"child_run_id": self.run_id, "event": event_type, **{k: payload.get(k) for k in ("title", "summary", "status", "model", "zone") if k in payload}})

    def begin_step(self, step_type: str, title: str, *, zone: str = "platform", todo_id: str | None = None, detail: dict | None = None, level: int = 0) -> int:
        self._step_seq += 1
        self.usage["steps"] = self.usage.get("steps", 0) + 1
        if step_type == "tool":
            self.usage["tool_calls"] = self.usage.get("tool_calls", 0) + 1
        with session_scope() as s:
            step = RunStep(run_id=self.run_id, seq=self._step_seq, step_type=step_type, title=title, zone=zone, todo_id=todo_id, detail=detail or {}, data_level=level)
            s.add(step)
            s.flush()
            step_id = step.id
        self.current_step_id = step_id
        self.emit("step.started", {"step_id": step_id, "seq": self._step_seq, "type": step_type, "title": title, "zone": zone, "todo_id": todo_id})
        return step_id

    def end_step(self, step_id: int, *, status: str = "done", summary: str = "", detail: dict | None = None, level: int = 0) -> None:
        with session_scope() as s:
            step = s.get(RunStep, step_id)
            if step is not None:
                step.status = status
                step.summary = truncate(summary, 2000)
                step.ended_at = utcnow()
                step.data_level = max(step.data_level, level)
                if detail:
                    step.detail = {**(step.detail or {}), **detail}
        self.emit("step.finished", {"step_id": step_id, "status": status, "summary": truncate(summary, 600)})

    def note(self, title: str, summary: str, *, step_type: str = "reasoning", zone: str = "platform", detail: dict | None = None) -> int:
        step_id = self.begin_step(step_type, title, zone=zone, detail=detail)
        self.end_step(step_id, summary=summary)
        self.emit("reasoning.summary", {"title": title, "summary": truncate(summary, 600)})
        return step_id

    # ------------------------------------------------------------- 工作区与存档
    def archive(self, name: str, data: Any) -> str:
        from ..jsonutil import jsonable

        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        path = self.dir / f"{safe}.json"
        path.write_text(json.dumps(jsonable(data), ensure_ascii=False, indent=1), encoding="utf-8")
        return str(path.relative_to(settings.workspaces_dir))

    @staticmethod
    def load_archive(ref: str) -> Any:
        return json.loads((settings.workspaces_dir / ref).read_text(encoding="utf-8"))

    # ------------------------------------------------------------- 用量与预算
    def add_usage(self, zone: str, tokens_in: int, tokens_out: int, cost: float, latency_ms: int) -> None:
        with self._usage_lock:
            if zone == "cloud":
                self.usage["cloud_tokens_in"] += tokens_in
                self.usage["cloud_tokens_out"] += tokens_out
                self.usage["cloud_cost"] = round(self.usage.get("cloud_cost", 0.0) + cost, 6)
                self.usage["cloud_calls"] = self.usage.get("cloud_calls", 0) + 1
            else:
                self.usage["private_tokens_in"] += tokens_in
                self.usage["private_tokens_out"] += tokens_out
                self.usage["private_calls"] = self.usage.get("private_calls", 0) + 1
                self.usage["gpu_seconds"] = round(self.usage.get("gpu_seconds", 0.0) + latency_ms / 1000, 3)

    def add_gpu_seconds(self, seconds: float) -> None:
        self.usage["gpu_seconds"] = round(self.usage.get("gpu_seconds", 0.0) + seconds, 3)

    def budget_status(self) -> dict:
        elapsed = time.monotonic() - self.started_monotonic + self.state.get("elapsed_before", 0.0)
        return {
            "cloud_tokens": (self.usage["cloud_tokens_in"] + self.usage["cloud_tokens_out"]) / self.budget["cloud_tokens"],
            "cloud_cost": self.usage.get("cloud_cost", 0.0) / self.budget["cloud_cost"],
            "gpu_seconds": self.usage.get("gpu_seconds", 0.0) / self.budget["gpu_seconds"],
            "steps": self.usage.get("steps", 0) / self.budget["steps"],
            "wall_seconds": elapsed / self.budget["wall_seconds"],
        }

    def budget_allows(self, extra_cost: float = 0.0) -> bool:
        status = self.budget_status()
        if extra_cost and (self.usage.get("cloud_cost", 0.0) + extra_cost) > self.budget["cloud_cost"]:
            return False
        return all(v < 1.0 for v in status.values())

    # ------------------------------------------------------------- 审批与级别
    def has_approval(self, tool_name: str) -> bool:
        return bool(self.state["approvals"].get(tool_name))

    def grant_approval(self, tool_name: str, card_id: int) -> None:
        self.state["approvals"][tool_name] = card_id

    @property
    def context_level(self) -> int:
        return max([m.get("level", 0) for m in self.messages] + [0])

    def context_tokens(self) -> int:
        return sum(estimate_tokens(str(m.get("content", ""))) for m in self.messages)

    def profile_role(self) -> str:
        return "designer" if self.agent_id in ("AG-RA", "AG-CD", "AG-AP") else "service"

    def log_debug(self, text: str) -> None:
        log.debug("run %s: %s", self.run_id, text)

    # ------------------------------------------------------------- 检查点
    def checkpoint(self) -> None:
        payload = {
            "state": self.state,
            "messages": self.messages,
            "todos": self.todos.to_list(),
            "rounds_since_update": self.todos.rounds_since_update,
            "workspace": self.workspace,
            "step_seq": self._step_seq,
            "elapsed": time.monotonic() - self.started_monotonic + self.state.get("elapsed_before", 0.0),
        }
        from ..jsonutil import jsonable

        path = self.dir / "checkpoint.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(jsonable(payload), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        with session_scope() as s:
            run = s.get(Run, self.run_id)
            if run is not None:
                run.checkpoint = {"path": str(path.relative_to(settings.workspaces_dir)), "ts": utcnow().isoformat(), "todos": len(self.todos.items)}
                run.usage = dict(self.usage)
                run.progress = round(self.todos.progress(), 3)
                run.degrade_level = self.state.get("degrade_level", run.degrade_level)
                run.reflection_rounds = self.state.get("reflection_rounds", 0)

    def restore(self) -> bool:
        path = self.dir / "checkpoint.json"
        if not path.exists():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.state.update(payload.get("state", {}))
        self.messages = payload.get("messages", [])
        self.todos = TodoList.from_list(payload.get("todos", []))
        self.todos.rounds_since_update = payload.get("rounds_since_update", 0)
        self.workspace = payload.get("workspace", {})
        self._step_seq = payload.get("step_seq", 0)
        self.state["elapsed_before"] = payload.get("elapsed", 0.0)
        return True

    def emit_todos(self) -> None:
        self.emit("plan.updated", {"todos": self.todos.to_list(), "progress": round(self.todos.progress(), 3)})
