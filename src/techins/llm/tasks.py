"""推理任务注册表。

每个推理任务同时定义：
- 面向大模型的任务说明、输出结构、补充的领域知识上下文（llm_context）与输出校验清洗（postprocess）；
- 内置推理引擎的实现（未配置大模型时使用；也是所有兜底链的最后一环）。
两条路径输出同一结构，调用方不感知具体由谁完成；大模型输出未通过校验时沿兜底链回退。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

BuiltinFn = Callable[[dict], Any]
ContextFn = Callable[[dict], dict]
PostFn = Callable[[dict, Any], Any]


@dataclass
class ReasoningTask:
    name: str
    task_type: str
    description: str
    output_hint: str
    builtin: BuiltinFn
    required_keys: tuple[str, ...] = field(default_factory=tuple)
    expect_json: bool = True
    llm_context: ContextFn | None = None
    postprocess: PostFn | None = None
    guidance: str = ""

    def validate(self, output: Any) -> bool:
        if not self.expect_json:
            return isinstance(output, str) and bool(output.strip())
        if not isinstance(output, dict):
            return False
        return all(k in output for k in self.required_keys)


_REGISTRY: dict[str, ReasoningTask] = {}


def reasoning_task(
    name: str,
    task_type: str,
    description: str,
    output_hint: str,
    required_keys: tuple[str, ...] = (),
    expect_json: bool = True,
    llm_context: ContextFn | None = None,
    postprocess: PostFn | None = None,
    guidance: str = "",
) -> Callable[[BuiltinFn], BuiltinFn]:
    def decorator(fn: BuiltinFn) -> BuiltinFn:
        _REGISTRY[name] = ReasoningTask(name, task_type, description, output_hint, fn, required_keys, expect_json, llm_context, postprocess, guidance)
        return fn

    return decorator


def attach(name: str, *, llm_context: ContextFn | None = None, postprocess: PostFn | None = None, guidance: str | None = None) -> None:
    """为已注册的推理任务补充大模型上下文与输出校验（便于在独立模块中维护）。"""
    task = _REGISTRY[name]
    if llm_context is not None:
        task.llm_context = llm_context
    if postprocess is not None:
        task.postprocess = postprocess
    if guidance is not None:
        task.guidance = guidance


def get_task(name: str) -> ReasoningTask:
    if name not in _REGISTRY:
        raise KeyError(f"未注册的推理任务：{name}")
    return _REGISTRY[name]


def all_tasks() -> dict[str, ReasoningTask]:
    return dict(_REGISTRY)
