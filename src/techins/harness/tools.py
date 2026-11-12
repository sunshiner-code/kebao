"""工具注册表与守卫钩子（对应设计文档 05 号第 5 节）。"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..db import session_scope
from ..models import ToolCall
from ..privacy.classifier import classify_text
from ..privacy.gateway import EgressReport, privacy_gateway
from ..textutil import truncate

if TYPE_CHECKING:
    from .context import RunContext

PERMISSION_LABELS = {"T0": "只读", "T1": "写草稿", "T2": "写正式数据", "T3": "对外发送"}
ZONE_LABELS = {"cloud": "云域", "private": "私域", "platform": "平台", "follow": "跟随数据"}


@dataclass
class ToolResult:
    summary: str
    data: Any = None
    level: int = 0
    control: str | None = None  # suspend / finish / none
    ok: bool = True
    metrics: dict = field(default_factory=dict)


class ToolError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class GuardRejection(RuntimeError):
    def __init__(self, guard: str, message: str, needs_human: bool = False) -> None:
        super().__init__(message)
        self.guard = guard
        self.needs_human = needs_human


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    handler: Callable[..., ToolResult]
    permission: str = "T0"
    zone: str = "private"
    max_level: int = 3
    params: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 600.0
    retries: int = 1
    idempotent: bool = True
    alternatives: list[str] = field(default_factory=list)
    platform: bool = False
    cost_estimate: float = 0.0
    side_effect: bool = False  # 写入业务对象（评测模式下以演练方式执行）

    def openai_schema(self) -> dict:
        properties = {k: {"type": "string", "description": v} for k, v in self.params.items()}
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"{self.title}：{self.description}（权限 {self.permission}，计算域 {ZONE_LABELS[self.zone]}）",
                "parameters": {"type": "object", "properties": properties},
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        existing = self._tools.get(spec.name)
        if existing is not None and existing.handler.__module__ != spec.handler.__module__:
            raise ValueError(f"工具名称冲突：{spec.name} 已由 {existing.handler.__module__} 注册")
        self._tools[spec.name] = spec
        return spec

    def tool(self, name: str, title: str, description: str, **kwargs: Any) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
        def decorator(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            self.register(ToolSpec(name=name, title=title, description=description, handler=fn, **kwargs))
            return fn

        return decorator

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"未注册的工具：{name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> dict[str, ToolSpec]:
        return dict(self._tools)


registry = ToolRegistry()


def _offline_tools() -> set[str]:
    from ..services.config_center import cfg

    return set(cfg("agents.tools").get("disabled_tools") or [])


def _summarize_args(args: dict) -> str:
    try:
        return truncate(json.dumps(args, ensure_ascii=False, default=str), 300)
    except TypeError:
        return truncate(str(args), 300)


def _arg_level(ctx: "RunContext", args: dict) -> int:
    text = json.dumps(args, ensure_ascii=False, default=str)
    level = max((f.level for f in classify_text(text, include_amounts=False, include_companies=False)), default=0)
    for original in ctx.vault.originals():
        if len(original) >= 2 and original in text:
            level = max(level, 2)
    return level


def execute_tool(ctx: "RunContext", name: str, args: dict, *, todo_id: str | None = None) -> ToolResult:
    spec = registry.get(name)
    if not spec.platform and name not in ctx.profile.tool_whitelist():
        raise GuardRejection("permission", f"工具 {name} 不在智能体 {ctx.profile.agent_id} 的白名单内")
    if not spec.platform and (name in _offline_tools() or name in set(ctx.config_overrides.get("tool_blacklist") or [])):
        raise GuardRejection("permission", f"工具 {spec.title}（{name}）已下线或被当前配置版本禁用")
    zone = spec.zone if spec.zone != "follow" else ("private" if ctx.context_level >= 2 else "cloud")
    step_id = ctx.begin_step("tool", spec.title, zone=zone, todo_id=todo_id, detail={"tool": name, "args": args, "permission": spec.permission})
    guard = {"permission": "pass", "privacy": "pass", "budget": "pass", "result": "pass"}
    started = time.perf_counter()
    retries = 0
    alternative_used = None
    status, error = "ok", None
    result: ToolResult | None = None
    try:
        # 调用前守卫：权限
        if spec.permission in ("T2", "T3") and not ctx.has_approval(name):
            guard["permission"] = "need_human"
            raise GuardRejection("permission", f"{PERMISSION_LABELS[spec.permission]}类工具 {spec.title} 需要人工确认", needs_human=True)
        # 调用前守卫：隐私
        if spec.zone == "cloud" and _arg_level(ctx, args) >= 2:
            guard["privacy"] = "reject"
            raise GuardRejection("privacy", f"云域工具 {spec.title} 的参数含有私域敏感内容，已拒绝；请改用私域工具或先脱敏")
        # 调用前守卫：预算
        if not ctx.budget_allows(spec.cost_estimate):
            guard["budget"] = "reject"
            raise GuardRejection("budget", "本次运行预算不足以执行该工具", needs_human=True)
        ctx.emit("tool.started", {"step_id": step_id, "tool": name, "title": spec.title, "zone": zone, "args": _summarize_args(args)})
        attempt_names = [name] + [a for a in spec.alternatives if a not in _offline_tools()]
        last_exc: Exception | None = None
        if spec.side_effect and ctx.eval_mode:
            result = ToolResult(summary=f"评测模式：{spec.title}以演练方式执行，未写入业务数据", data={"dry_run": True}, level=0)
            attempt_names = []
        for tool_name in attempt_names:
            current = registry.get(tool_name)
            for attempt in range(current.retries + 1):
                try:
                    result = current.handler(ctx, **args)
                    if tool_name != name:
                        alternative_used = tool_name
                    break
                except ToolError as exc:
                    last_exc = exc
                    retries += 1
                    if not exc.retryable:
                        break
                    time.sleep(min(2.0, 0.2 * (attempt + 1)))
            if result is not None:
                break
        if result is None:
            raise last_exc or ToolError("工具执行失败")
        # 调用后守卫：结果中的敏感信息与长度
        if zone != "private" or result.level < 2:
            findings = [f for f in classify_text(result.summary, include_amounts=False, include_companies=False) if f.level >= 3]
            if findings:
                guard["result"] = "desensitized"
                result.summary = privacy_gateway.desensitize_text(result.summary, ctx.vault, EgressReport())
        if len(result.summary) > 1800:
            guard["result"] = "truncated"
            result.summary = truncate(result.summary, 1800)
        return result
    except GuardRejection:
        status = "rejected"
        raise
    except Exception as exc:  # noqa: BLE001 - 工具异常统一记录
        status, error = "error", f"{exc}"
        ctx.log_debug(traceback.format_exc())
        raise
    finally:
        duration = int((time.perf_counter() - started) * 1000)
        archive_ref = None
        if result is not None and result.data is not None:
            archive_ref = ctx.archive(f"{name}-{step_id}", result.data)
        with session_scope() as s:
            s.add(
                ToolCall(
                    run_id=ctx.run_id,
                    step_id=step_id,
                    tool=name,
                    permission=spec.permission,
                    zone=zone,
                    args_summary=_summarize_args(args),
                    result_summary=truncate(result.summary if result else (error or ""), 1000),
                    archive_ref=archive_ref,
                    guard=guard,
                    retries=retries,
                    alternative_used=alternative_used,
                    duration_ms=duration,
                    status=status,
                    error=error,
                )
            )
        ctx.end_step(
            step_id,
            status="done" if status == "ok" else ("failed" if status == "error" else "rejected"),
            summary=(result.summary if result else error or "") or "",
            detail={"tool": name, "guard": guard, "duration_ms": duration, "archive": archive_ref, "metrics": result.metrics if result else {}},
            level=result.level if result else 0,
        )
        ctx.emit(
            "tool.finished",
            {
                "step_id": step_id,
                "tool": name,
                "title": spec.title,
                "zone": zone,
                "status": status,
                "summary": truncate(result.summary if result else error or "", 400),
                "duration_ms": duration,
                "metrics": result.metrics if result else {},
            },
        )
