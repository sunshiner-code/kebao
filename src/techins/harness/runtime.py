"""智能体主循环（Harness 核心，对应设计文档 05 号第 2 节）。"""

from __future__ import annotations

import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..models import Notification, Run
from ..privacy.classifier import classify_text
from ..textutil import truncate
from . import compaction, mailbox
from . import memory as memory_mod
from .background import background_manager
from .evaluation import evaluate
from .hitl import CARD_TYPE_LABELS, create_card
from .policy import Call, ProcedurePolicy, choose_policy, condition_ok
from .reflection import close_reflection, reflect
from .skills import skill_library
from .todo import TodoItem
from .tools import GuardRejection, ToolResult, ToolSpec, execute_tool, registry

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("techins.runtime")

MAX_ROUNDS = 400
NAG_ROUNDS = 3
_child_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="subagent")


def _platform(name: str, title: str, description: str, params: dict[str, str]) -> None:
    def _not_direct(ctx, **_):  # 平台工具由主循环处理
        raise RuntimeError("平台工具只能由主循环调度")

    registry.register(ToolSpec(name=name, title=title, description=description, handler=_not_direct, permission="T0", zone="platform", platform=True, params=params))


_platform("todo_write", "更新待办", "写入或更新计划待办；同一时刻只能有一项执行中", {"action": "plan/start/complete/fail/skip", "items": "JSON 数组（plan 时）", "todo_id": "待办编号", "note": "说明"})
_platform("load_skill", "加载技能", "按需加载技能全文或某一分区", {"skill_id": "技能编号", "section": "分区名称，可为空"})
_platform("stage_complete", "阶段完成", "声明当前阶段完成并触发阶段评估", {"todo_id": "待办编号"})
_platform("request_human", "请求人审", "生成人审卡片并暂停运行", {"card_type": "卡片类型", "todo_id": "待办编号"})
_platform("finish", "提交最终结果", "执行输出契约校验与最终评估", {})
_platform("spawn_subagents", "派生子智能体", "在独立上下文中并行执行子任务，只回传摘要", {"tasks": "JSON 数组"})
_platform("start_background", "启动后台任务", "长计算放到后台执行", {"name": "任务名称"})
_platform("wait_background", "等待后台任务", "等待后台任务完成", {"task_id": "任务编号"})
_platform("send_message", "发送信箱消息", "向其他智能体发送请求", {"to_agent": "目标智能体", "kind": "消息类型", "payload": "JSON"})
_platform("check_mailbox", "查看信箱", "读取未读消息", {})
_platform("read_workspace", "读取工作区", "读取工作区中的中间产物或存档全文", {"key": "工作区键或存档引用"})
_platform("search_knowledge", "检索知识库", "混合检索知识库并返回带引用的结果", {"query": "检索内容", "kbs": "知识库编号，逗号分隔"})


# 评测模式下不执行的人审后续动作：启动事故、通知服务商、向客户发送补充材料、支付、发起跨智能体修订
EVAL_SKIP_APPLIERS = {"incident_start", "provider_dispatch", "supplement_send", "payment", "decision"}


class AgentRuntime:
    # ------------------------------------------------------------------ 生命周期
    def start(self, ctx: "RunContext") -> None:
        from .timing import apply_status

        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            apply_status(run, "planning", usage=ctx.usage)
            run.started_at = run.started_at or utcnow()
            run.locked_versions = {
                "agent": ctx.profile.version,
                "skills": {sid: skill_library.get(sid).version + "#" + skill_library.get(sid).digest for sid in ctx.profile.skill_ids()},
            }
        catalog = skill_library.catalog(ctx.profile.skill_ids())
        ctx.messages.append({"role": "system", "content": f"你是{ctx.profile.name}（{ctx.profile.agent_id}）。{ctx.profile.description}\n可用技能：\n{catalog}", "level": 0, "kind": "system"})
        ctx.messages.append({"role": "user", "content": "任务输入：" + truncate(json.dumps(ctx.inputs, ensure_ascii=False, default=str), 1500), "level": 1, "kind": "task"})
        ctx.emit("run.started", {"agent": ctx.profile.name, "start_mode": ctx.state.get("start_mode"), "degrade_level": ctx.state.get("degrade_level"), "mode": ctx.mode})
        ctx.profile.initialize(ctx)
        subjects = ctx.workspace.get("memory_subjects", [])
        if ctx.config_overrides.get("experience_injection", True):
            memory_mod.inject(ctx, ctx.workspace.get("memory_query", json.dumps(ctx.inputs, ensure_ascii=False, default=str)), subjects)
        else:
            ctx.note("经验注入已关闭", "当前智能体配置版本关闭了经验与记忆注入")
        ctx.state["initialized"] = True
        ctx.checkpoint()

    def execute(self, ctx: "RunContext") -> str:
        if not ctx.state.get("initialized"):
            self.start(ctx)
        self._set_status(ctx, "running")
        try:
            outcome = self._handle_mailbox(ctx)
            if outcome:
                return outcome
            while True:
                control = self._control(ctx)
                if control == "cancel":
                    return self._finish_run(ctx, "cancelled", "运行已被取消，已有产物保存为草稿")
                if control == "pause":
                    ctx.checkpoint()
                    self._set_status(ctx, "paused")
                    ctx.emit("status.changed", {"status": "paused"})
                    return "paused"
                if not ctx.budget_allows():
                    return self._suspend_for_card(ctx, self._budget_card(ctx))
                ctx.state["round"] += 1
                ctx.todos.rounds_since_update += 1
                if ctx.state["round"] > MAX_ROUNDS:
                    return self._suspend_for_card(ctx, self._iteration_card(ctx, "主循环轮次达到上限"))
                compaction.micro_compact(ctx)
                compaction.auto_compact(ctx)
                self._inject(ctx)
                policy = choose_policy(ctx)
                try:
                    decision = policy.decide(ctx)
                except RuntimeError as exc:
                    ctx.state["llm_policy_failures"] = ctx.state.get("llm_policy_failures", 0) + 1
                    if ctx.state["llm_policy_failures"] >= 3:
                        ctx.state["llm_policy_failed"] = True
                    ctx.note("规划模型降级", f"{exc}；本轮改由技能规程策略驱动" + ("，后续轮次不再调用规划模型" if ctx.state.get("llm_policy_failed") else ""), zone="cloud")
                    decision = ProcedurePolicy().decide(ctx)
                if decision.rationale:
                    ctx.emit("reasoning.summary", {"title": decision.policy, "summary": decision.rationale})
                for call in decision.calls:
                    outcome = self._dispatch(ctx, call)
                    if outcome in ("suspend", "done"):
                        ctx.checkpoint()
                        return "waiting" if outcome == "suspend" else "completed"
                ctx.checkpoint()
                ctx.emit("progress", {"progress": round(ctx.todos.progress(), 3), "usage": ctx.usage, "budget": ctx.budget_status()})
        except Exception as exc:  # noqa: BLE001 - 运行级异常
            log.exception("run %s failed", ctx.run_id)
            ctx.checkpoint()
            return self._finish_run(ctx, "failed", f"{exc}", error=traceback.format_exc())

    # ------------------------------------------------------------------ 注入
    def _inject(self, ctx: "RunContext") -> None:
        current = ctx.todos.current()
        if current and ctx.todos.rounds_since_update >= NAG_ROUNDS:
            ctx.messages.append({"role": "user", "content": f"提醒：待办「{current.content}」已连续 {ctx.todos.rounds_since_update} 轮未更新，请更新待办状态。", "level": 0, "kind": "nag", "round": ctx.state["round"]})
            ctx.emit("todo.nag", {"todo_id": current.id, "rounds": ctx.todos.rounds_since_update})
        for task in background_manager.finished_unnotified(ctx):
            ctx.messages.append({"role": "user", "content": f"后台任务完成通知：{task['name']}（{task['status']}）{task['summary']}", "level": 1, "kind": "background", "round": ctx.state["round"]})
        for msg in mailbox.unread(ctx.run_id):
            if msg["kind"] == "hitl_resolution":
                continue
            ctx.messages.append({"role": "user", "content": f"信箱消息（{msg['kind']}，来自 {msg.get('from_agent') or '平台'}）：{json.dumps(msg['payload'], ensure_ascii=False)[:600]}", "level": 1, "kind": "mail", "round": ctx.state["round"]})
            ctx.workspace.setdefault("mail", []).append(msg)
            mailbox.mark_read([msg["id"]])

    # ------------------------------------------------------------------ 调度
    def _dispatch(self, ctx: "RunContext", call: Call) -> str | None:
        handler = getattr(self, f"_p_{call.name}", None)
        if handler is not None:
            try:
                outcome = handler(ctx, call)
            except (KeyError, ValueError, TypeError, FileNotFoundError, json.JSONDecodeError, AttributeError) as exc:
                ctx.state["platform_errors"] = ctx.state.get("platform_errors", 0) + 1
                message = f"平台工具 {call.name} 调用失败：{exc}。参数：{truncate(json.dumps(call.args, ensure_ascii=False, default=str), 200)}"
                ctx.messages.append({"role": "tool", "name": call.name, "content": message, "level": 0, "round": ctx.state["round"]})
                ctx.note("平台工具调用失败", message)
                if ctx.state["platform_errors"] >= 4 and not ctx.state.get("llm_policy_failed"):
                    ctx.state["llm_policy_failed"] = True
                    ctx.note("规划策略切换", "规划模型连续产生无效的平台工具调用，后续由技能规程策略驱动", zone="cloud")
                return None
            ctx.state["platform_errors"] = 0
            if call.todo_id and call.name in ("spawn_subagents", "search_knowledge", "read_workspace", "send_message", "check_mailbox", "wait_background"):
                current = ctx.state["todo_results"].get(call.todo_id)
                if not current or current.get("status") != "failed":
                    ctx.state["todo_results"][call.todo_id] = {"status": "ok", "summary": call.name}
            return outcome
        return self._business_tool(ctx, call)

    def _business_tool(self, ctx: "RunContext", call: Call) -> str | None:
        todo = ctx.todos.get(call.todo_id) if call.todo_id else ctx.todos.current()
        if todo is not None and todo.status == "skipped":
            ctx.messages.append({"role": "tool", "name": call.name, "content": f"待办「{todo.content}」已跳过（{todo.note}），未执行该工具", "level": 0, "round": ctx.state["round"]})
            return None
        if todo is not None:
            todo.attempts += 1
        try:
            result: ToolResult = execute_tool(ctx, call.name, call.args, todo_id=todo.id if todo else None)
        except GuardRejection as exc:
            ctx.messages.append({"role": "tool", "name": call.name, "content": f"守卫拒绝：{exc}", "level": 0, "round": ctx.state["round"]})
            if exc.needs_human:
                card = {
                    "card_type": "action_confirm" if exc.guard == "permission" else "budget",
                    "title": f"确认执行：{registry.get(call.name).title}",
                    "reason": str(exc),
                    "impact": "未确认前该操作不会执行",
                    "context": {"tool": call.name, "args": call.args, "todo_id": todo.id if todo else None},
                    "options": [{"key": "approve", "label": "确认执行", "primary": True}, {"key": "reject", "label": "不执行", "requires_comment": True}],
                    "default_action": None,
                    "assignee_role": ctx.profile_role(),
                }
                return self._suspend_for_card(ctx, card)
            if todo:
                ctx.state["todo_results"][todo.id] = {"status": "failed", "error": str(exc)}
                todo.attempts = 99
            return None
        except Exception as exc:  # noqa: BLE001
            ctx.messages.append({"role": "tool", "name": call.name, "content": f"工具执行失败：{exc}", "level": 0, "round": ctx.state["round"]})
            if todo:
                ctx.state["todo_results"][todo.id] = {"status": "failed", "error": str(exc)}
            return None
        content_level = max([f.level for f in classify_text(result.summary)] + [min(1, result.level)])
        ctx.messages.append({"role": "tool", "name": call.name, "content": result.summary, "level": content_level, "data_level": result.level, "round": ctx.state["round"], "archive": None})
        if todo:
            ctx.state["todo_results"][todo.id] = {"status": "waiting" if result.control == "suspend" else "ok", "summary": truncate(result.summary, 300), "metrics": result.metrics}
        if result.control == "suspend":
            from .timing import apply_status

            waiting = (result.data or {}).get("waiting_for", {}) if isinstance(result.data, dict) else {}
            with session_scope() as s:
                run = s.get(Run, ctx.run_id)
                apply_status(run, "waiting_human", usage=ctx.usage)
                run.waiting_for = {"card_type": "agent", "title": result.summary, **waiting}
            ctx.emit("status.changed", {"status": "waiting_human", "waiting_for": waiting})
            if ctx.eval_mode:
                from ..models import HitlCard

                with session_scope() as s:
                    open_cards = [(c.id, {"options": c.options, "default_action": c.default_action}) for c in s.scalars(select(HitlCard).where(HitlCard.run_id == ctx.run_id, HitlCard.status == "open")).all()]
                for cid, spec in open_cards:
                    self._simulated_review(ctx, cid, spec)
            return "suspend"
        return None

    # ------------------------------------------------------------------ 平台工具
    def _p_todo_write(self, ctx: "RunContext", call: Call) -> None:
        action = call.args.get("action", "plan")
        step_id = ctx.begin_step("plan", "更新待办", zone="platform")
        if action == "plan":
            raw = call.args.get("items") or "[]"
            specs = json.loads(raw) if isinstance(raw, str) else raw
            whitelist = ctx.profile.tool_whitelist()
            valid = isinstance(specs, list) and bool(specs) and all(isinstance(sp, dict) and sp.get("content") and (not sp.get("tool") or sp["tool"] in whitelist) for sp in specs)
            if not valid:
                specs = ProcedurePolicy().plan_items(ctx)
                ctx.note("计划校正", "规划模型给出的计划为空或格式不符，改按已加载技能的作业规程生成计划")
            items = [
                TodoItem(id=f"T{i + 1:02d}", content=sp["content"], tool=sp.get("tool"), args=sp.get("args", {}), stage_eval=sp.get("stage_eval", False), hitl=sp.get("hitl"), step_key=sp.get("step_key"), background=sp.get("background", False), condition=sp.get("condition"))
                for i, sp in enumerate(specs)
            ]
            ctx.todos.write(items)
            summary = f"生成计划 {len(items)} 项"
            if not ctx.parent_run_id and ctx.profile.plan_confirm and (ctx.mode == "semi" or ctx.state.get("start_mode") == "cold"):
                ctx.state["plan_confirm_pending"] = True
        elif action == "start":
            item = ctx.todos.get(call.args["todo_id"])
            if item is not None and not condition_ok(ctx, item.condition):
                ctx.todos.set_status(item.id, "skipped", f"条件「{item.condition}」不满足")
                summary = f"跳过：{item.content}（条件不满足）"
            else:
                ctx.todos.set_status(call.args["todo_id"], "in_progress")
                summary = f"开始：{ctx.todos.get(call.args['todo_id']).content}"
        elif action == "complete":
            ctx.todos.set_status(call.args["todo_id"], "completed", call.args.get("note", ""))
            summary = f"完成：{ctx.todos.get(call.args['todo_id']).content}"
        elif action in ("fail", "skip"):
            status = "failed" if action == "fail" else "skipped"
            ctx.todos.set_status(call.args["todo_id"], status, call.args.get("note", ""))
            item = ctx.todos.get(call.args["todo_id"])
            summary = f"{'失败' if status == 'failed' else '跳过'}：{item.content}"
            if status == "failed":
                self._degrade(ctx, f"步骤「{item.content}」执行失败：{call.args.get('note', '')}")
        else:
            raise ValueError(f"未知的待办动作：{action}")
        ctx.end_step(step_id, summary=summary)
        ctx.messages.append({"role": "assistant", "content": "待办：" + "；".join(("✓ " if t.status == "completed" else "→ " if t.status == "in_progress" else "· ") + t.content for t in ctx.todos.items), "level": 0, "kind": "todo", "round": ctx.state["round"]})
        ctx.emit_todos()
        if action == "plan" and ctx.state.pop("plan_confirm_pending", False):
            card = {
                "card_type": "plan_confirm",
                "title": f"确认{ctx.profile.name}的执行计划",
                "reason": "冷启动或半自动模式下，计划需经人工确认后执行" if ctx.state.get("start_mode") == "cold" else "半自动模式下每个阶段需人工确认",
                "context": {"todos": ctx.todos.to_list()},
                "options": [{"key": "approve", "label": "按计划执行", "primary": True}, {"key": "reject", "label": "调整后执行", "requires_comment": True}],
                "default_action": "approve",
                "deadline_hours": 24,
                "assignee_role": ctx.profile_role(),
                "mandatory": ctx.state.get("start_mode") == "cold",
            }
            self._suspend_for_card(ctx, card)
            return "suspend"
        return None

    def _p_load_skill(self, ctx: "RunContext", call: Call) -> None:
        skill_id = call.args.get("skill_id")
        section = call.args.get("section") or None
        if skill_id in ctx.state["skills_loaded"] and any(m.get("kind") == "skill" and m.get("skill_id") == skill_id for m in ctx.messages):
            ctx.messages.append({"role": "tool", "name": "load_skill", "content": f"技能 {skill_id} 已在上下文中，无需重复加载，请继续执行待办。", "level": 0, "round": ctx.state["round"]})
            return None
        step_id = ctx.begin_step("skill", f"加载技能 {skill_id}" + (f"·{section}" if section else ""), zone="platform")
        text = skill_library.render(skill_id, section)
        ctx.messages.append({"role": "system", "content": text, "level": 0, "kind": "skill", "skill_id": skill_id, "round": ctx.state["round"]})
        if skill_id not in ctx.state["skills_loaded"]:
            ctx.state["skills_loaded"].append(skill_id)
        skill = skill_library.get(skill_id)
        ctx.end_step(step_id, summary=f"已加载 {skill.title}（{len(text)} 字），作业规程 {len(skill.procedure)} 步", detail={"skill_id": skill_id, "version": skill.version, "section": section})
        ctx.emit("skill.loaded", {"skill_id": skill_id, "title": skill.title, "section": section, "chars": len(text)})
        return None

    def _p_stage_complete(self, ctx: "RunContext", call: Call) -> None:
        todo = ctx.todos.get(call.args.get("todo_id")) or ctx.todos.current()
        ctx.state.setdefault("stage_evaluated", {})[todo.id] = True
        dims = ctx.profile.stage_rubrics.get(todo.tool or "", [])
        if not dims:
            ctx.todos.set_status(todo.id, "completed")
            ctx.emit_todos()
            return None
        record = evaluate(ctx, dims, kind="stage", target=todo.content)
        record["target_tool"] = todo.tool
        if record["passed"]:
            ctx.todos.set_status(todo.id, "completed", f"阶段评估 {record['score']:.0f} 分")
            compaction.stage_compact(ctx, todo.content)
        else:
            ctx.todos.set_status(todo.id, "completed", f"阶段评估未达标（{record['score']:.0f} 分），已生成修正待办")
            if ctx.state.get("reflection_rounds", 0) < ctx.profile.max_reflection_rounds * 2:
                reflect(ctx, record, trigger="stage_eval", anchor_todo=todo.id)
            else:
                self._degrade(ctx, f"阶段「{todo.content}」评估未达标且已达到反思上限")
        ctx.emit_todos()
        return None

    def _p_request_human(self, ctx: "RunContext", call: Call) -> str | None:
        card_type = call.args.get("card_type")
        todo_id = call.args.get("todo_id")
        builder = ctx.profile.hitl_builders.get(card_type)
        if builder is None:
            raise ValueError(f"智能体未定义人审节点：{card_type}")
        spec = builder(ctx)
        if spec is None:
            ctx.state["hitl_results"][todo_id] = {"action": "skipped", "comment": "无需人工处理"}
            return None
        if not spec.get("mandatory", True) and ctx.mode == "auto":
            ctx.state["hitl_results"][todo_id] = {"action": spec.get("default_action") or "approve", "comment": "自动模式：非强制人审节点按默认策略处理", "system": True}
            applier = None if (ctx.eval_mode and card_type in EVAL_SKIP_APPLIERS) else ctx.profile.hitl_appliers.get(card_type)
            if applier:
                applier(ctx, ctx.state["hitl_results"][todo_id])
            ctx.note("人审节点（自动通过）", f"{spec['title']}：自动模式下按默认策略处理")
            return None
        spec.setdefault("context", {})["todo_id"] = todo_id
        return self._suspend_for_card(ctx, spec)

    def _p_finish(self, ctx: "RunContext", call: Call) -> str | None:
        ctx.state["final_attempts"] = ctx.state.get("final_attempts", 0) + 1
        record = evaluate(ctx, ctx.profile.final_rubric, kind="final", target="最终产物")
        history = [h for h in ctx.state["eval_history"] if h["kind"] == "final"]
        converged = len(history) >= 2 and (history[-1]["score"] - history[-2]["score"]) < 2.0
        rounds = ctx.state.get("final_reflections", 0)
        if not record["passed"] and rounds < ctx.profile.max_reflection_rounds and not converged:
            ctx.state["final_reflections"] = rounds + 1
            items = reflect(ctx, record, trigger="final_eval")
            if items:
                return None
        close_reflection(ctx, record["score"], "resolved" if record["passed"] else "unresolved")
        if not record["passed"]:
            self._degrade(ctx, "最终评估未达标，已达到反思上限或收敛，结果带未达标标注")
        from ..jsonutil import jsonable

        output = jsonable(ctx.profile.finalize(ctx))
        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            run.output = output
            run.eval_score = record["score"]
            run.eval_passed = record["passed"]
        ctx.workspace["final_output"] = output
        if ctx.profile.final_confirm and not ctx.parent_run_id:
            spec = ctx.profile.hitl_builders[ctx.profile.final_confirm](ctx)
            spec.setdefault("context", {})["final"] = True
            ctx.state["awaiting_final_confirm"] = True
            if not record["passed"]:
                spec["reason"] = spec["reason"] + "（注意：最终评估未达标，请重点复核）"
            return self._suspend_for_card(ctx, spec)
        self._finish_run(ctx, "completed", output.get("summary", "运行完成"))
        return "done"

    def _reject_spawn(self, ctx: "RunContext", call: Call, message: str) -> None:
        ctx.messages.append({"role": "tool", "name": "spawn_subagents", "content": message, "level": 0, "round": ctx.state["round"]})
        ctx.note("派生子任务失败", message)
        if not call.todo_id:
            return
        todo = ctx.todos.get(call.todo_id)
        ctx.state["todo_results"][call.todo_id] = {"status": "failed", "error": message}
        if todo is not None:
            todo.attempts = 99

    def _p_spawn_subagents(self, ctx: "RunContext", call: Call) -> None:
        from .executor import executor
        from .policy import resolve_args

        raw = call.args.get("tasks") or "[]"
        if isinstance(raw, str) and raw.startswith("@"):
            raw = resolve_args(ctx, {"tasks": raw})["tasks"]
        try:
            tasks = json.loads(raw) if isinstance(raw, str) else list(raw)
        except json.JSONDecodeError as exc:
            self._reject_spawn(ctx, call, f"子任务不是有效的任务列表：{exc}")
            return None
        if not isinstance(tasks, list) or not tasks:
            self._reject_spawn(ctx, call, "没有可生成条款的险种")
            return None
        if any(not isinstance(task, dict) or "purpose" not in task or "title" not in task or "inputs" not in task for task in tasks):
            self._reject_spawn(ctx, call, "子任务缺少 purpose、title 或 inputs")
            return None
        step_id = ctx.begin_step("subagent", f"派生 {len(tasks)} 个子智能体", zone="platform")
        child_ids = [executor.create_run(agent_id=ctx.agent_id, purpose=t["purpose"], inputs=t["inputs"], business_ref={**ctx.business_ref, "root_run_id": ctx.root_run_id}, mode=ctx.mode, user_id=ctx.created_by, title=t["title"], parent_run_id=ctx.run_id, submit=False) for t in tasks]
        ctx.emit("subtask.spawned", {"children": [{"run_id": cid, "title": t["title"]} for cid, t in zip(child_ids, tasks)]})
        futures = [_child_pool.submit(executor.run_inline, cid) for cid in child_ids]
        outputs = []
        for cid, fut, t in zip(child_ids, futures, tasks):
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("subagent %s failed: %s", cid, exc)
            with session_scope() as s:
                child = s.get(Run, cid)
                outputs.append({"run_id": cid, "title": t["title"], "status": child.status, "output": child.output, "key": t.get("key")})
        ctx.workspace.setdefault("subagent_outputs", {})[call.args.get("group", "default")] = outputs
        ok = sum(1 for o in outputs if o["status"] == "completed")
        ctx.end_step(step_id, summary=f"子智能体完成 {ok}/{len(outputs)}：" + "；".join(f"{o['title']}（{o['output'].get('summary', o['status']) if o['output'] else o['status']}）" for o in outputs), detail={"children": child_ids})
        ctx.messages.append({"role": "tool", "name": "spawn_subagents", "content": f"子智能体完成 {ok}/{len(outputs)}", "level": 1, "round": ctx.state["round"]})
        return None

    def _p_check_mailbox(self, ctx: "RunContext", call: Call) -> None:
        self._inject(ctx)
        return None

    def _p_read_workspace(self, ctx: "RunContext", call: Call) -> None:
        key = call.args.get("key", "")
        if key in ctx.workspace:
            content = json.dumps(ctx.workspace[key], ensure_ascii=False, default=str)
        elif key and (ctx.dir.parent.parent / key).is_file():
            content = json.dumps(ctx.load_archive(key), ensure_ascii=False, default=str)
        else:
            content = f"工作区中没有「{key}」。可读取的键：{'、'.join(sorted(ctx.workspace.keys())) or '（暂无）'}"
        ctx.messages.append({"role": "tool", "name": "read_workspace", "content": truncate(content, 4000), "level": 2, "round": ctx.state["round"]})
        return None

    def _p_search_knowledge(self, ctx: "RunContext", call: Call) -> None:
        from ..knowledge.index import knowledge_index

        kbs = [k for k in (call.args.get("kbs") or "").split(",") if k]
        hits = knowledge_index.search(call.args.get("query", ""), kbs=kbs or None, max_level=1)
        ctx.messages.append({"role": "tool", "name": "search_knowledge", "content": "\n".join(f"[{h['kb']}#{h['doc_id']}] {h['title']}：{truncate(h['text'], 120)}" for h in hits), "level": 0, "round": ctx.state["round"]})
        return None

    def _p_send_message(self, ctx: "RunContext", call: Call) -> None:
        payload = call.args.get("payload")
        mailbox.send(kind=call.args.get("kind", "message"), payload=json.loads(payload) if isinstance(payload, str) else (payload or {}), to_agent=call.args.get("to_agent"), from_run_id=ctx.run_id, from_agent=ctx.agent_id)
        return None

    def _p_start_background(self, ctx: "RunContext", call: Call) -> None:
        raise RuntimeError("后台任务由具体工具内部提交，请调用对应业务工具")

    def _p_wait_background(self, ctx: "RunContext", call: Call) -> None:
        background_manager.wait(int(call.args["task_id"]))
        return None

    # ------------------------------------------------------------------ 人审恢复
    def _handle_mailbox(self, ctx: "RunContext") -> str | None:
        for msg in mailbox.unread(ctx.run_id):
            if msg["kind"] != "hitl_resolution":
                continue
            mailbox.mark_read([msg["id"]])
            res = msg["payload"]
            card_ctx = res.get("context") or {}
            action = res.get("action")
            ctx.messages.append({"role": "user", "content": f"人审结果（{CARD_TYPE_LABELS.get(res['card_type'], res['card_type'])}）：{res.get('label')}。{res.get('comment') or ''}", "level": 1, "kind": "hitl", "round": ctx.state["round"]})
            ctx.note("人审结果", f"{res.get('label')}：{res.get('comment') or '无说明'}", step_type="hitl")
            if card_ctx.get("final"):
                ctx.state.pop("awaiting_final_confirm", None)
                if ctx.eval_mode:
                    return self._finish_run(ctx, "completed", (ctx.workspace.get("final_output") or {}).get("summary", "评测运行完成") + "（评测模式：未执行业务确认）")
                if action in ("approve",) or (action and action.startswith("option")):
                    result = ctx.profile.on_confirm(ctx, res) if ctx.profile.on_confirm else {}
                    ctx.workspace["confirm_result"] = result
                    return self._finish_run(ctx, "completed", result.get("summary", "已确认"))
                ctx.state["final_reflections"] = 0
                reflect(ctx, None, trigger="human_reject", human_comment=res.get("comment"))
                if not ctx.todos.next_pending():
                    applier = ctx.profile.hitl_appliers.get("final_reject")
                    if applier:
                        applier(ctx, res)
                continue
            if res["card_type"] == "budget":
                if action == "approve":
                    for key in ("cloud_tokens", "cloud_cost", "gpu_seconds", "steps", "wall_seconds"):
                        ctx.budget[key] = ctx.budget[key] * 1.5
                    with session_scope() as s:
                        s.get(Run, ctx.run_id).budget = ctx.budget
                else:
                    ctx.todos.write([*(t for t in ctx.todos.items if t.status != "pending")])
                continue
            if res["card_type"] == "plan_confirm":
                if action == "reject" and res.get("comment"):
                    reflect(ctx, None, trigger="plan_adjust", human_comment=res.get("comment"))
                continue
            if res["card_type"] == "action_confirm":
                tool = card_ctx.get("tool")
                if action == "approve" and tool:
                    ctx.grant_approval(tool, res["card_id"])
                elif card_ctx.get("todo_id"):
                    ctx.todos.set_status(card_ctx["todo_id"], "skipped", "人工拒绝执行")
                continue
            if res["card_type"] == "iteration_limit":
                if action == "accept":
                    ctx.state["final_reflections"] = ctx.profile.max_reflection_rounds
                continue
            todo_id = card_ctx.get("todo_id")
            if todo_id:
                ctx.state["hitl_results"][todo_id] = res
                if ctx.eval_mode and res["card_type"] in EVAL_SKIP_APPLIERS:
                    applier = None
                    ctx.note("评测模式", f"{CARD_TYPE_LABELS.get(res['card_type'], res['card_type'])}属于对外动作，评测中不执行")
                else:
                    applier = ctx.profile.hitl_appliers.get(res["card_type"])
                note = applier(ctx, res) if applier else ""
                if action == "reject":
                    reflect(ctx, None, trigger="human_reject", human_comment=res.get("comment"), anchor_todo=todo_id)
                    ctx.state["hitl_results"].pop(todo_id, None)
                    ctx.todos.set_status(todo_id, "completed", "人工驳回，已生成修正待办")
                    item = ctx.todos.get(todo_id)
                    ctx.todos.insert_after(ctx.todos.items[-1].id, [TodoItem(id=ctx.todos.new_id(), content=f"再次确认：{item.content}", hitl=item.hitl, origin="human")])
                else:
                    ctx.todos.set_status(todo_id, "completed", note or res.get("label", ""))
                ctx.emit_todos()
        return None

    # ------------------------------------------------------------------ 辅助
    def _suspend_for_card(self, ctx: "RunContext", spec: dict) -> str:
        from .timing import apply_status

        if ctx.eval_mode:
            spec = {**spec, "assignee_role": "admin", "mandatory": False, "title": "【评测】" + spec["title"], "silent": True}
        card_id = create_card(ctx, spec)
        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            apply_status(run, "waiting_human", usage=ctx.usage)
            run.waiting_for = {"card_id": card_id, "card_type": spec["card_type"], "title": spec["title"]}
        ctx.checkpoint()
        ctx.emit("status.changed", {"status": "waiting_human", "card_id": card_id})
        if ctx.eval_mode:
            self._simulated_review(ctx, card_id, spec)
        return "suspend"

    def _simulated_review(self, ctx: "RunContext", card_id: int, spec: dict) -> None:
        """评测模式的模拟审核人：按卡片的主选项（或默认动作）处理，保证评测运行可无人值守完成。"""
        from .hitl import HitlError, resolve

        options = spec.get("options") or [{"key": "approve", "primary": True}]
        choice = spec.get("default_action") or next((o["key"] for o in options if o.get("primary")), None) or next((o["key"] for o in options if not o.get("requires_comment")), options[0]["key"])
        try:
            resolve(card_id, user_id=None, action=choice, comment="评测模式：模拟审核人按主选项处理", system=True)
        except HitlError as exc:
            log.warning("simulated review failed on card %s: %s", card_id, exc)

    def _budget_card(self, ctx: "RunContext") -> dict:
        status = ctx.budget_status()
        return {
            "card_type": "budget",
            "title": "运行预算已用尽",
            "reason": "本次运行已达到预算上限：" + "、".join(f"{k} {v * 100:.0f}%" for k, v in status.items() if v >= 1.0),
            "options": [{"key": "approve", "label": "追加 50% 额度后继续", "primary": True}, {"key": "reject", "label": "以当前结果结束"}],
            "assignee_role": ctx.profile_role(),
        }

    def _iteration_card(self, ctx: "RunContext", reason: str) -> dict:
        return {
            "card_type": "iteration_limit",
            "title": "迭代超限",
            "reason": reason,
            "options": [{"key": "accept", "label": "接受当前结果", "primary": True}, {"key": "reject", "label": "转人工处理", "requires_comment": True}],
            "assignee_role": ctx.profile_role(),
        }

    def _degrade(self, ctx: "RunContext", note: str) -> None:
        ctx.state["degrade_notes"].append(note)
        levels = ["D0", "D1", "D2", "D3", "D4"]
        current = ctx.state.get("degrade_level", "D0")
        idx = min(len(levels) - 1, levels.index(current) + 1) if current in levels else 1
        ctx.state["degrade_level"] = levels[idx]
        ctx.emit("degrade.changed", {"level": levels[idx], "note": note})

    def _control(self, ctx: "RunContext") -> str | None:
        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            return (run.control or {}).get("command")

    def _set_status(self, ctx: "RunContext", status: str) -> None:
        from .timing import apply_status

        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            if run.status != status:
                apply_status(run, status, usage=ctx.usage)
                if run.control:
                    run.control = {}
        ctx.emit("status.changed", {"status": status})

    def _finish_run(self, ctx: "RunContext", status: str, summary: str, error: str | None = None) -> str:
        from .timing import apply_status

        created = {}
        if status == "completed" and not ctx.eval_mode:
            try:
                created = memory_mod.distill(ctx, bool(ctx.workspace.get("final_output")) and ctx.state.get("degrade_level", "D0") in ("D0", "D1", "D2"))
            except Exception as exc:  # noqa: BLE001
                log.warning("memory distill failed: %s", exc)
        with session_scope() as s:
            run = s.get(Run, ctx.run_id)
            apply_status(run, status, usage=ctx.usage)
            run.ended_at = utcnow()
            run.usage = dict(ctx.usage)
            run.progress = 1.0 if status == "completed" else run.progress
            run.degrade_level = ctx.state.get("degrade_level", run.degrade_level)
            run.reflection_rounds = ctx.state.get("reflection_rounds", 0)
            run.waiting_for = {}
            run.control = {}
            if error:
                run.error = truncate(error, 4000)
            if not ctx.parent_run_id and run.created_by and not ctx.eval_mode:
                s.add(Notification(user_id=run.created_by, kind="run", title=f"{ctx.profile.name}运行{ {'completed': '已完成', 'failed': '失败', 'cancelled': '已取消'}.get(status, status) }", body=truncate(summary, 300), link=f"/runs/{run.id}", level="error" if status == "failed" else "info"))
        ctx.checkpoint()
        ctx.emit("run.finished", {"status": status, "summary": truncate(summary, 600), "usage": ctx.usage, "memory": created, "degrade_level": ctx.state.get("degrade_level"), "degrade_notes": ctx.state.get("degrade_notes", [])})
        return status


runtime = AgentRuntime()
