"""反思器：失败归因 → 修正策略 → 可执行的修正计划（对应设计文档 05 号第 8.1 节）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..db import session_scope
from ..llm.gateway import ModelUnavailable, model_gateway
from ..llm.tasks import attach, reasoning_task
from ..models import Reflection
from .todo import TodoItem

if TYPE_CHECKING:
    from .context import RunContext

CATEGORY_HINTS = {
    "证据不足": ["证据", "缺", "不足", "没有", "补充", "覆盖"],
    "检索偏差": ["不相关", "检索", "无关"],
    "推理错误": ["错误", "不对", "不合理", "归类", "等级", "评分"],
    "约束冲突": ["预算", "冲突", "区间", "超出", "无法同时"],
    "格式与契约错误": ["格式", "字段", "缺少", "结构"],
    "工具失败": ["失败", "不可用", "超时"],
}


@reasoning_task(
    "harness.reflect",
    "TT-09",
    "根据评估未通过的维度、历史得分与人工意见，诊断问题原因并说明修正思路。",
    '{"diagnosis": "问题诊断", "root_causes": ["原因"], "strategy": "修正思路", "risk_if_unfixed": "不修正的影响"}',
    required_keys=("diagnosis", "strategy"),
)
def _builtin_reflect(inputs: dict) -> dict:
    failed = inputs.get("failed", [])
    history = inputs.get("history", [])
    comment = inputs.get("human_comment") or ""
    causes = []
    for dim in failed:
        detail = dim.get("detail") or ""
        causes.append(f"{dim['name']}未达标（当前 {dim.get('value')}，要求 {dim.get('threshold')}）{('：' + detail) if detail else ''}")
    if comment:
        causes.append(f"人工意见：{comment}")
    trend = ""
    if len(history) >= 2:
        delta = history[-1]["score"] - history[-2]["score"]
        trend = f"与上一轮相比得分{'提升' if delta >= 0 else '下降'} {abs(delta):.1f} 分。"
    categories = inputs.get("categories", [])
    strategy_lines = inputs.get("strategies", [])
    return {
        "diagnosis": f"共有 {len(failed)} 个维度未达标。" + trend + ("主要归因：" + "、".join(categories) + "。" if categories else ""),
        "root_causes": causes,
        "strategy": "；".join(strategy_lines) if strategy_lines else "按未达标维度逐项定向修正，只重做受影响的部分。",
        "risk_if_unfixed": "输出将带有未达标标注并转人工处理。",
    }


def _as_text(value: object, fallback: str = "") -> str:
    """反思结果里的说明字段入库前必须是字符串。模型有时把 strategy 返回成列表。"""
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = [_as_text(item) for item in value]
        return "；".join(part for part in parts if part)
    if isinstance(value, dict):
        return "；".join(f"{key}：{_as_text(item)}" for key, item in value.items())
    return str(value)


def normalize_reflect_output(out: dict) -> dict:
    causes = out.get("root_causes") or []
    if isinstance(causes, str):
        causes = [causes] if causes else []
    elif not isinstance(causes, list):
        causes = [_as_text(causes)]
    return {
        "diagnosis": _as_text(out.get("diagnosis")),
        "root_causes": [_as_text(item) for item in causes if _as_text(item)],
        "strategy": _as_text(out.get("strategy")),
        "risk_if_unfixed": _as_text(out.get("risk_if_unfixed")),
    }


def _post_reflect(_inputs: dict, out: dict) -> dict:
    return normalize_reflect_output(out)


attach("harness.reflect", postprocess=_post_reflect, guidance="diagnosis 和 strategy 必须是字符串。如果有多条修正思路，写成一条用分号连接的字符串，不要返回数组。root_causes 是字符串数组。")


def categorize_comment(comment: str) -> list[str]:
    found = [cat for cat, hints in CATEGORY_HINTS.items() if any(h in comment for h in hints)]
    return found or ["推理错误"]


def reflect(ctx: "RunContext", evaluation: dict | None, *, trigger: str, human_comment: str | None = None, anchor_todo: str | None = None) -> list[TodoItem]:
    automatic = ctx.agent_id == "AG-CD" and not human_comment and trigger in ("stage_eval", "final_eval")
    if automatic and ctx.state.get("reflection_rounds", 0) >= ctx.profile.max_reflection_rounds:
        return []
    ctx.state["reflection_rounds"] = ctx.state.get("reflection_rounds", 0) + 1
    rnd = ctx.state["reflection_rounds"]
    failed_dims = [d for d in (evaluation or {}).get("dimensions", []) if not d["passed"]]
    failed_keys = {d["key"] for d in failed_dims}
    matched = [rule for rule in ctx.profile.reflection_rules if failed_keys & set(rule.dims)]
    if human_comment:
        comment_cats = categorize_comment(human_comment)
        matched += [rule for rule in ctx.profile.reflection_rules if rule.category in comment_cats and rule not in matched and "*human" in rule.dims]
    categories = sorted({r.category for r in matched} | ({d["category"] for d in failed_dims} if not matched else set()))
    step_id = ctx.begin_step("reflection", f"反思第 {rnd} 轮", zone="platform")
    builtin_inputs = {"failed": failed_dims, "history": ctx.state.get("eval_history", [])[-4:], "human_comment": human_comment, "categories": categories, "strategies": [r.strategy for r in matched]}
    if automatic:
        diagnosis = _builtin_reflect(builtin_inputs)
    else:
        try:
            diagnosis = model_gateway.invoke(
                "harness.reflect",
                {
                    "agent": ctx.profile.name,
                    "failed": [{k: d.get(k) for k in ("key", "name", "value", "threshold", "detail")} for d in failed_dims],
                    "history": builtin_inputs["history"],
                    "human_comment": human_comment,
                    "categories": categories,
                    "strategies": builtin_inputs["strategies"],
                },
                ctx=ctx,
                data_level=1,
            )
        except ModelUnavailable:
            diagnosis = _builtin_reflect(builtin_inputs)
    diagnosis = normalize_reflect_output(diagnosis)
    specs: list[dict] = []
    for rule in matched:
        specs.extend(rule.steps(ctx, {"evaluation": evaluation, "failed": failed_dims, "comment": human_comment}))
    if not specs and evaluation and evaluation.get("kind") == "stage":
        target_tool = evaluation.get("target_tool")
        if target_tool:
            specs.append({"content": f"重新执行：{evaluation.get('target')}", "tool": target_tool, "args": {}, "stage_eval": True})
    new_items = []
    for spec in specs:
        new_items.append(
            TodoItem(
                id=ctx.todos.new_id(),
                content=spec["content"],
                tool=spec.get("tool"),
                args=spec.get("args", {}),
                stage_eval=spec.get("stage_eval", False),
                hitl=spec.get("hitl"),
                origin="reflection",
            )
        )
    ctx.todos.insert_after(anchor_todo, new_items)
    with session_scope() as s:
        s.add(
            Reflection(
                run_id=ctx.run_id,
                round=rnd,
                trigger=trigger,
                diagnosis=diagnosis["diagnosis"],
                categories=categories,
                strategy=diagnosis["strategy"],
                plan=[{"content": i.content, "tool": i.tool, "args": i.args} for i in new_items],
                score_before=(evaluation or {}).get("score"),
            )
        )
    summary = f"{diagnosis.get('diagnosis', '')} 修正计划 {len(new_items)} 项：" + "；".join(i.content for i in new_items)
    ctx.end_step(step_id, summary=summary, detail={"categories": categories, "root_causes": diagnosis.get("root_causes", [])})
    ctx.emit("reflection.started", {"round": rnd, "trigger": trigger, "categories": categories, "diagnosis": diagnosis.get("diagnosis"), "strategy": diagnosis.get("strategy"), "plan": [i.content for i in new_items]})
    ctx.emit_todos()
    return new_items


def close_reflection(ctx: "RunContext", score_after: float, outcome: str) -> None:
    with session_scope() as s:
        rec = s.query(Reflection).filter(Reflection.run_id == ctx.run_id).order_by(Reflection.round.desc()).first()
        if rec is not None and rec.score_after is None:
            rec.score_after = score_after
            rec.outcome = outcome
