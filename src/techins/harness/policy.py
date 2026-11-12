"""决策策略。

- 大模型策略：配置了支持工具调用的大模型时，由模型阅读技能与上下文自行决定下一步调用哪个工具。
- 技能规程策略：未配置大模型或大模型调用失败时，严格按照已加载技能的"作业规程"逐步推进，
  遇到工具失败时重试或改用替代工具，遇到评估未达标时执行反思生成的修正待办。
两种策略都只产出"工具调用"，由同一主循环执行，因此守卫、日志、评估、反思对两者一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..llm.gateway import model_gateway
from ..llm.providers import ProviderError
from .skills import skill_library
from .tools import registry

if TYPE_CHECKING:
    from .context import RunContext


@dataclass
class Call:
    name: str
    args: dict = field(default_factory=dict)
    todo_id: str | None = None


@dataclass
class Decision:
    calls: list[Call]
    policy: str
    rationale: str = ""


def resolve_args(ctx: "RunContext", args: dict) -> dict:
    """规程参数中以 @ 开头的值由智能体配置包中的解析器在运行时计算（例如按样本企业生成子任务）。"""
    out = {}
    for key, value in args.items():
        if isinstance(value, str) and value.startswith("@"):
            resolver = ctx.profile.resolvers.get(value[1:])
            if resolver is None:
                raise KeyError(f"未定义的参数解析器：{value}")
            resolved = resolver(ctx)
            out[key] = json.dumps(resolved, ensure_ascii=False) if isinstance(resolved, (list, dict)) else resolved
        else:
            out[key] = value
    return out


def condition_ok(ctx: "RunContext", condition: str | None) -> bool:
    if not condition:
        return True
    negate = condition.startswith("非")
    name = condition[1:] if negate else condition
    fn = ctx.profile.conditions.get(name)
    ok = bool(fn(ctx)) if fn else True
    return not ok if negate else ok


class ProcedurePolicy:
    name = "技能规程策略"

    def _procedure(self, ctx: "RunContext"):
        skill = skill_library.get(ctx.profile.core_skill)
        if ctx.profile.procedure_name:
            return skill.subprocedures.get(ctx.profile.procedure_name, [])
        return skill.procedure

    def plan_items(self, ctx: "RunContext") -> list[dict]:
        items = []
        for step in self._procedure(ctx):
            items.append(
                {
                    "content": step.title,
                    "tool": step.tool,
                    "args": step.args,
                    "stage_eval": step.stage_eval,
                    "hitl": step.hitl,
                    "step_key": step.key,
                    "background": step.background,
                    "condition": step.condition,
                }
            )
        return items

    def decide(self, ctx: "RunContext") -> Decision:
        core = ctx.profile.core_skill
        loaded = ctx.state["skills_loaded"]
        if core not in loaded:
            section = None
            return Decision([Call("load_skill", {"skill_id": core, "section": section or ""})], self.name, "先加载核心技能的完整作业规程")
        if ctx.state.get("start_mode") == "cold" and "SK-S2" in ctx.profile.shared_skills and "SK-S2" not in loaded:
            return Decision([Call("load_skill", {"skill_id": "SK-S2", "section": "作业规程"})], self.name, "冷启动：加载泛化与降级技能")
        if not ctx.todos.items:
            return Decision([Call("todo_write", {"action": "plan", "items": json.dumps(self.plan_items(ctx), ensure_ascii=False)})], self.name, "依据作业规程生成待办计划")
        current = ctx.todos.current()
        if current is None:
            nxt = ctx.todos.next_pending()
            if nxt is None:
                return Decision([Call("finish", {})], self.name, "全部待办已完成，提交最终结果")
            if not condition_ok(ctx, nxt.condition):
                return Decision([Call("todo_write", {"action": "skip", "todo_id": nxt.id, "note": f"条件「{nxt.condition}」不满足"})], self.name, f"跳过待办：{nxt.content}（条件不满足）")
            calls = [Call("todo_write", {"action": "start", "todo_id": nxt.id})]
            if nxt.tool:
                calls.append(Call(nxt.tool, resolve_args(ctx, nxt.args), nxt.id))
            return Decision(calls, self.name, f"开始待办：{nxt.content}")
        result = ctx.state["todo_results"].get(current.id)
        if current.tool and (result is None or result["status"] == "waiting"):
            return Decision([Call(current.tool, resolve_args(ctx, current.args), current.id)], self.name, f"执行待办：{current.content}" if result is None else f"继续等待：{current.content}")
        if current.tool and result and result["status"] == "failed":
            if current.attempts < 2:
                return Decision([Call(current.tool, resolve_args(ctx, current.args), current.id)], self.name, "工具失败，重试")
            return Decision([Call("todo_write", {"action": "fail", "todo_id": current.id, "note": result.get("error", "")})], self.name, "多次失败，标记失败并按降级继续")
        if current.stage_eval and current.id not in ctx.state.setdefault("stage_evaluated", {}):
            return Decision([Call("stage_complete", {"todo_id": current.id})], self.name, "阶段完成，执行阶段评估")
        if current.hitl and current.id not in ctx.state["hitl_results"]:
            return Decision([Call("request_human", {"card_type": current.hitl, "todo_id": current.id})], self.name, "需要人工确认")
        return Decision([Call("todo_write", {"action": "complete", "todo_id": current.id})], self.name, f"完成待办：{current.content}")


PLANNER_RULES = """你运行在科技保险平台的 Harness 中，只能通过调用工具推进任务，规则如下：
1. 第一步用 load_skill 加载核心技能；冷启动时再加载 SK-S2 的作业规程。
2. 用 todo_write(action=plan) 生成计划；items 留空表示严格按已加载技能的作业规程生成。
3. 开始一个待办时先调用 todo_write(action=start, todo_id=…)，随后调用该待办指定的工具（可以在同一次回复中给出两个工具调用）。工具参数中形如 @xxx 的值由平台自动补全，你无需构造。
4. 待办的工具成功后：如果该待办需要阶段评估，调用 stage_complete(todo_id)；如果需要人审，调用 request_human(card_type, todo_id)；否则调用 todo_write(action=complete, todo_id)。
5. 工具失败时可重试一次；仍失败调用 todo_write(action=fail, todo_id, note) 并继续后续待办。
6. 可以随时调用 search_knowledge 查询知识库，但不得跳过或改变作业规程中的步骤顺序。
7. 全部待办完成后调用 finish。"""


class LLMPolicy:
    name = "大模型工具调用策略"

    def __init__(self, model) -> None:
        self.model = model

    def _state(self, ctx: "RunContext") -> dict:
        current = ctx.todos.current()
        last_tool = next((m for m in reversed(ctx.messages) if m.get("role") == "tool"), None)
        return {
            "todos": [
                {"id": t.id, "content": t.content, "status": t.status, "tool": t.tool, "stage_eval": t.stage_eval, "hitl": t.hitl, "tool_done": ctx.state["todo_results"].get(t.id, {}).get("status"), "stage_evaluated": t.id in ctx.state.get("stage_evaluated", {}), "hitl_done": t.id in ctx.state["hitl_results"]}
                for t in ctx.todos.items
            ],
            "current_todo": current.id if current else None,
            "skills_loaded": ctx.state["skills_loaded"],
            "start_mode": ctx.state.get("start_mode"),
            "last_tool_result": (last_tool or {}).get("content", "")[:600],
        }

    def _repair(self, ctx: "RunContext", calls: list[Call]) -> list[Call]:
        fixed: list[Call] = []
        for call in calls:
            args = dict(call.args or {})
            if call.name == "todo_write" and args.get("action", "plan") == "plan":
                raw = args.get("items")
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
                except json.JSONDecodeError:
                    parsed = None
                if not parsed:
                    args["items"] = json.dumps(ProcedurePolicy().plan_items(ctx), ensure_ascii=False)
            if call.name == "finish" and not ctx.todos.all_done():
                ctx.note("规划纠偏", "规划模型试图在待办未完成时提交结果，改为按作业规程继续", zone="cloud")
                return ProcedurePolicy().decide(ctx).calls
            if call.name == "request_human" and args.get("card_type") not in ctx.profile.hitl_builders:
                ctx.note("规划纠偏", f"未定义的人审节点 {args.get('card_type')}，改为按作业规程继续", zone="cloud")
                return ProcedurePolicy().decide(ctx).calls
            todo = ctx.todos.get(args.get("todo_id") or call.todo_id or "") if (args.get("todo_id") or call.todo_id) else None
            if todo is None and call.name not in ("todo_write", "load_skill", "stage_complete", "request_human", "finish"):
                todo = ctx.todos.current() if (ctx.todos.current() and ctx.todos.current().tool == call.name) else next((t for t in ctx.todos.items if t.tool == call.name and t.status in ("pending", "in_progress")), None)
            if todo is not None and todo.tool == call.name:
                resolved = resolve_args(ctx, todo.args)
                for key, value in todo.args.items():
                    if isinstance(value, str) and value.startswith("@") or key not in args:
                        args[key] = resolved[key]
            if call.name not in ("todo_write", "stage_complete", "request_human"):
                args.pop("todo_id", None)
            fixed.append(Call(call.name, args, todo.id if todo is not None else call.todo_id or call.args.get("todo_id")))
        return fixed

    def decide(self, ctx: "RunContext") -> Decision:
        names = sorted(ctx.profile.tool_whitelist())
        tools = [registry.get(n).openai_schema() for n in names if registry.has(n)]
        messages = [{"role": "system", "content": PLANNER_RULES}]
        messages += [{"role": "system" if m["role"] == "system" else ("assistant" if m["role"] == "assistant" else "user"), "content": str(m.get("content", ""))} for m in ctx.messages]
        messages.append({"role": "user", "content": "当前运行状态：" + json.dumps(self._state(ctx), ensure_ascii=False) + "\n请给出下一步的工具调用。"})
        try:
            out = model_gateway.chat_with_tools(self.model, messages, tools, ctx=ctx, level=ctx.context_level)
        except Exception as exc:  # noqa: BLE001 - 规划模型的任何异常都转为可降级错误
            raise RuntimeError(f"规划模型不可用：{type(exc).__name__}: {exc}") from exc
        calls = [Call(c["name"], c.get("arguments") or {}, (c.get("arguments") or {}).get("todo_id")) for c in out["tool_calls"] if c.get("name") and (registry.has(c["name"]) or c["name"] in ("todo_write",))]
        if not calls:
            raise RuntimeError("规划模型未给出可执行的工具调用")
        return Decision(self._repair(ctx, calls), f"{self.name}（{self.model.name}）", out.get("content", "") or "")


def choose_policy(ctx: "RunContext"):
    if ctx.profile.planner == "procedure":
        return ProcedurePolicy()
    model = model_gateway.planning_model(ctx.context_level, ctx.agent_id, ctx)
    if model is not None and not ctx.state.get("llm_policy_failed"):
        return LLMPolicy(model)
    return ProcedurePolicy()
