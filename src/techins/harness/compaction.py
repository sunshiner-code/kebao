"""上下文压缩：微压缩（旧工具结果）、自动压缩（超过窗口 70%）、阶段压缩（阶段切换）。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..llm.gateway import ModelUnavailable, model_gateway
from ..llm.tasks import reasoning_task
from ..textutil import truncate

if TYPE_CHECKING:
    from .context import RunContext

MICRO_KEEP_ROUNDS = 3
AUTO_RATIO = 0.7
DEFAULT_WINDOW = 32000


@reasoning_task(
    "harness.compact",
    "TT-14",
    "把智能体运行的历史对话压缩为运行摘要，保留已完成事项、关键结论、未决问题与全部证据编号。",
    '{"completed": ["已完成事项"], "key_findings": ["关键结论"], "open_issues": ["未决问题"], "evidence_refs": ["证据编号"]}',
    required_keys=("completed", "key_findings"),
)
def _builtin_compact(inputs: dict) -> dict:
    completed, findings, issues, refs = [], [], [], set()
    for msg in inputs.get("messages", []):
        content = str(msg.get("content", ""))
        refs.update(re.findall(r"\bE-[\w-]+\b", content))
        if msg.get("role") == "tool":
            first = re.split(r"[。；\n]", content, maxsplit=1)[0]
            findings.append(f"{msg.get('name', '工具')}：{truncate(first, 120)}")
        elif msg.get("kind") == "todo":
            completed.extend(re.findall(r"✓ ([^\n]+)", content))
        elif "未达标" in content or "失败" in content:
            issues.append(truncate(content, 120))
    return {"completed": completed[-30:], "key_findings": findings[-40:], "open_issues": issues[-10:], "evidence_refs": sorted(refs)[:200]}


def micro_compact(ctx: "RunContext") -> int:
    current = ctx.state.get("round", 0)
    compacted = 0
    for msg in ctx.messages:
        if msg.get("role") != "tool" or msg.get("compacted"):
            continue
        if msg.get("round", current) < current - MICRO_KEEP_ROUNDS:
            archive = msg.get("archive")
            msg["content"] = f"[已压缩] {truncate(str(msg.get('content', '')), 90)}" + (f"（完整结果见存档 {archive}）" if archive else "")
            msg["compacted"] = True
            compacted += 1
    return compacted


def _summarize(ctx: "RunContext", messages: list[dict], title: str) -> str:
    level = max([m.get("level", 0) for m in messages] + [0])
    try:
        summary = model_gateway.invoke("harness.compact", {"messages": messages}, ctx=ctx, data_level=level)
    except ModelUnavailable:
        summary = _builtin_compact({"messages": messages})
    lines = [f"【{title}】"]
    if summary.get("completed"):
        lines.append("已完成：" + "；".join(summary["completed"]))
    if summary.get("key_findings"):
        lines.append("关键结论：" + "；".join(summary["key_findings"]))
    if summary.get("open_issues"):
        lines.append("未决问题：" + "；".join(summary["open_issues"]))
    if summary.get("evidence_refs"):
        lines.append("证据编号：" + "、".join(summary["evidence_refs"]))
    return "\n".join(lines)


PINNED_KINDS = ("system", "skill_catalog", "skill", "task", "memory")


def _content_level(messages: list[dict]) -> int:
    from ..privacy.classifier import classify_text

    text = "\n".join(str(m.get("content", "")) for m in messages)
    return max([f.level for f in classify_text(text)] + [1 if text else 0])


def auto_compact(ctx: "RunContext", window: int = DEFAULT_WINDOW) -> bool:
    tokens = ctx.context_tokens()
    if tokens <= window * AUTO_RATIO:
        return False
    head = [m for m in ctx.messages if m.get("kind") in PINNED_KINDS]
    body = [m for m in ctx.messages if m not in head]
    archive = ctx.archive(f"history-{ctx.state.get('round', 0)}", body)
    step_id = ctx.begin_step("compaction", "自动压缩上下文", zone="private" if ctx.context_level >= 2 else "cloud")
    text = _summarize(ctx, body, "运行摘要（自动压缩）")
    summary_msg = {"role": "system", "content": text + f"\n完整历史存档：{archive}", "kind": "summary"}
    summary_msg["level"] = _content_level([summary_msg])
    ctx.messages = head + [summary_msg]
    after = ctx.context_tokens()
    ctx.end_step(step_id, summary=f"上下文由约 {tokens} token 压缩为约 {after} token", detail={"layer": "auto", "before": tokens, "after": after, "archive": archive})
    ctx.emit("context.compacted", {"layer": "auto", "before": tokens, "after": after})
    return True


def stage_compact(ctx: "RunContext", stage_title: str) -> None:
    start = ctx.state.get("stage_start_index", 0)
    pinned = [m for m in ctx.messages[start:] if m.get("kind") in PINNED_KINDS]
    segment = [m for m in ctx.messages[start:] if m.get("kind") not in PINNED_KINDS]
    if len(segment) < 4:
        ctx.state["stage_start_index"] = len(ctx.messages)
        return
    before = ctx.context_tokens()
    archive = ctx.archive(f"stage-{ctx.state.get('round', 0)}", segment)
    text = _summarize(ctx, segment, f"阶段摘要：{stage_title}")
    summary_msg = {"role": "system", "content": text + f"\n阶段完整记录存档：{archive}", "kind": "summary"}
    summary_msg["level"] = _content_level([summary_msg])
    level = summary_msg["level"]
    ctx.messages = ctx.messages[:start] + pinned + [summary_msg]
    ctx.state["stage_start_index"] = len(ctx.messages)
    after = ctx.context_tokens()
    step_id = ctx.begin_step("compaction", f"阶段压缩：{stage_title}", zone="private" if level >= 2 else "cloud")
    ctx.end_step(step_id, summary=f"阶段记录由约 {before} token 压缩为约 {after} token", detail={"layer": "stage", "before": before, "after": after, "archive": archive})
    ctx.emit("context.compacted", {"layer": "stage", "before": before, "after": after})
