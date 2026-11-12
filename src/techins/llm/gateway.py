"""模型网关：按"任务类型 × 数据级别"路由、云域出域脱敏与回填、兜底链、调用日志。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select

from ..db import session_scope
from ..models import ModelCall, ModelRegistryEntry, RoutingRule
from ..privacy.classifier import classify_text
from ..privacy.gateway import PrivacyViolation, privacy_gateway
from ..privacy.vault import TokenVault
from ..textutil import estimate_tokens, truncate
from .providers import OpenAICompatibleClient, ProviderError, parse_json_content
from .tasks import ReasoningTask, get_task


class GatewayContext(Protocol):
    run_id: int | None
    agent_id: str | None
    current_step_id: int | None
    vault: TokenVault

    def emit(self, event_type: str, payload: dict) -> None: ...

    def add_usage(self, zone: str, tokens_in: int, tokens_out: int, cost: float, latency_ms: int) -> None: ...


class ModelUnavailable(RuntimeError):
    pass


def risk_warning_call(agent_id: str | None, task_name: str | None = None) -> bool:
    """预警研判、监测方案、事故响应、周期报告，以及无运行上下文的 rw.* 任务（如外部事件抽取）。"""
    return agent_id == "AG-RW" or (task_name or "").startswith("rw.")


def risk_warning_llm_blocked(agent_id: str | None, task_name: str | None = None) -> bool:
    """只有总开关与风险预警开关同时为 0 时，风险预警才使用大模型。"""
    if not risk_warning_call(agent_id, task_name):
        return False
    from ..config import settings

    return bool(settings.llm_disabled or settings.rw_llm_disabled)


@dataclass
class RouteDecision:
    rule_id: str | None
    chain: list[ModelRegistryEntry]
    level: int
    params: dict | None = None
    max_tokens: int = 16000


def _detect_level(payload: Any) -> int:
    """纵深防御：即使调用方声明的级别偏低，只要载荷中出现 L3 个人信息就上调。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    level = 0
    for f in classify_text(text, include_amounts=False, include_companies=False):
        level = max(level, f.level)
    return level


class ModelGateway:
    def route(self, task_type: str, level: int, scope: str | None = None, *, private_only: str | None = None, skip_llm: bool = False) -> RouteDecision:
        with session_scope() as s:
            rules = s.scalars(select(RoutingRule).where(RoutingRule.enabled.is_(True))).all()
            models = {m.id: m for m in s.scalars(select(ModelRegistryEntry)).all()}
            for m in models.values():
                s.expunge(m)
        matching = [r for r in rules if task_type in (r.task_types or []) and level in (r.data_levels or [])]
        scoped = [r for r in matching if r.scope in (scope, "global")]
        scoped.sort(key=lambda r: (0 if r.scope == scope else 1, r.priority))
        rule = scoped[0] if scoped else None
        names = (list(rule.preferred) + list(rule.fallback)) if rule else ["private-builtin"]
        chain: list[ModelRegistryEntry] = []
        for name in names:
            model = models.get(name)
            if not model or not model.enabled or not model.configured:
                continue
            if model.zone == "cloud" and level >= 3:
                continue
            if model.zone == "cloud" and model.max_level < 1:
                continue
            if model.zone == "cloud" and private_only:
                continue
            if skip_llm and model.kind == "llm":
                continue
            chain.append(model)
        if not any(m.kind == "builtin" and m.zone == "private" for m in chain) and models.get("private-builtin"):
            chain.append(models["private-builtin"])
        return RouteDecision(rule.id if rule else None, chain, level, dict(rule.params or {}) if rule else {}, rule.max_tokens if rule else 16000)

    # ------------------------------------------------------------------ 推理任务调用
    def invoke(self, task_name: str, inputs: dict, *, ctx: GatewayContext | None = None, data_level: int = 0, builtin_fallback: bool = True) -> Any:
        task = get_task(task_name)
        level = max(data_level, _detect_level({k: v for k, v in inputs.items() if k != "_images"}))
        # 图片原文视为 L3 高敏：一律私域多模态，禁止出域到云端
        if inputs.get("_images"):
            level = max(level, 3)
        restriction = self._cloud_restriction(ctx)
        if inputs.get("_images"):
            restriction = restriction or "图片内容按 L3 高敏处理，仅允许私域多模态模型"
        agent_id = ctx.agent_id if ctx else None
        skip_llm = risk_warning_llm_blocked(agent_id, task_name)
        if skip_llm and ctx and not getattr(ctx, "_rw_llm_noted", False):
            ctx._rw_llm_noted = True
            ctx.emit("model.restricted", {"reason": "风险预警未启用大模型（TECHINS_DISABLE_LLM 与 TECHINS_DISABLE_RW_LLM 需同时为 0），本次改由内置推理引擎完成"})
        decision = self.route(task.task_type, level, agent_id, private_only=restriction, skip_llm=skip_llm)
        if restriction and ctx and not getattr(ctx, "_restriction_noted", False):
            ctx._restriction_noted = True
            ctx.emit("model.restricted", {"reason": restriction})
        errors: list[str] = []
        chain = decision.chain if builtin_fallback else [model for model in decision.chain if model.kind != "builtin"]
        if not chain:
            raise ModelUnavailable(f"任务 {task_name} 无可用模型")
        for index, model in enumerate(chain):
            try:
                return self._invoke_on(task, model, inputs, decision, ctx, fallback=index > 0)
            except PrivacyViolation as exc:
                errors.append(f"{model.id}: {exc}")
                if ctx:
                    ctx.emit("egress.blocked", {"model": model.name, "findings": exc.findings})
            except (ProviderError, ValueError, json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
                errors.append(f"{model.id}: {exc}")
                self._log_call(ctx, task, model, decision, 0, 0, 0, "error", str(exc), index > 0, None)
        raise ModelUnavailable(f"任务 {task_name} 无可用模型：{'; '.join(errors)}")

    @staticmethod
    def _cloud_restriction(ctx: GatewayContext | None) -> str | None:
        """智能体配置版本禁止云端、或当日额度用尽（降级策略）时，本次调用只使用私域模型。"""
        if ctx is None:
            return None
        overrides = getattr(ctx, "config_overrides", {}) or {}
        if overrides.get("allow_cloud") is False:
            return "当前智能体配置版本不允许使用云端模型"
        if (getattr(ctx, "inputs", {}) or {}).get("_quota_degrade"):
            return f"{ctx.inputs['_quota_degrade']}的当日云端额度已用尽，已降级到私域"
        from ..services.quota import cloud_blocked

        return cloud_blocked(ctx.agent_id, getattr(ctx, "created_by", None))

    def _invoke_on(
        self,
        task: ReasoningTask,
        model: ModelRegistryEntry,
        inputs: dict,
        decision: RouteDecision,
        ctx: GatewayContext | None,
        fallback: bool,
    ) -> Any:
        vault = ctx.vault if ctx else TokenVault("adhoc")
        payload = inputs
        egress_id = None
        if inputs.get("_images") and model.zone == "cloud":
            raise PrivacyViolation("图片内容不得出域到云端模型", [{"kind": "图像出域禁令", "text": task.name}])
        if model.zone == "cloud" and decision.level >= 2:
            from ..services.config_center import cfg

            policy = cfg("privacy.egress")
            if policy.get("whitelist_enabled") and task.name not in set(policy.get("task_whitelist") or []):
                raise PrivacyViolation(f"推理任务 {task.name} 不在出域白名单内，改由私域模型处理", [{"kind": "出域白名单", "text": task.name}])
            payload, egress_id, report = privacy_gateway.egress(
                inputs,
                vault,
                run_id=ctx.run_id if ctx else None,
                step_id=ctx.current_step_id if ctx else None,
                agent_id=ctx.agent_id if ctx else None,
                task=task.name,
                task_type=task.task_type,
                source_level=decision.level,
                target_model=model.name,
            )
            if ctx:
                ctx.emit(
                    "egress.done",
                    {
                        "egress_id": egress_id,
                        "task": task.name,
                        "model": model.name,
                        "rules_hit": report["rules_hit"],
                        "placeholder_count": report["placeholder_count"],
                        "field_count": len(report["fields"]),
                    },
                )
        if ctx:
            ctx.emit(
                "model.routed",
                {
                    "task": task.name,
                    "task_type": task.task_type,
                    "model_id": model.id,
                    "model": model.name,
                    "zone": model.zone,
                    "rule_id": decision.rule_id,
                    "level": decision.level,
                    "fallback": fallback,
                    "desensitized": egress_id is not None,
                },
            )
        started = time.perf_counter()
        if model.kind == "builtin":
            output = task.builtin(payload)
            tokens_in = estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str))
            tokens_out = estimate_tokens(json.dumps(output, ensure_ascii=False, default=str))
        else:
            from ..config import settings

            client = OpenAICompatibleClient(model.base_url or "", self._api_key(model), model.model_name or "", timeout=settings.llm_timeout_s)
            llm_payload = dict(payload)
            images = llm_payload.pop("_images", None) or []
            if images and "vision" not in (model.capabilities or []):
                raise ValueError(f"{model.name} 不支持图像输入")
            if task.llm_context:
                llm_payload["领域知识"] = task.llm_context(payload)
            system = (
                f"你是科技保险智能体业务平台中的{model.name}，运行在{'云域' if model.zone == 'cloud' else '私域'}。"
                f"任务：{task.description}\n"
                + (f"要求：{task.guidance}\n" if task.guidance else "")
                + ("只输出 JSON，不要输出其他文字。输出结构：" + task.output_hint if task.expect_json else "输出要求：" + task.output_hint)
                + "\n输入中形如【企业A】【人员P1】的占位符是脱敏标识，请原样保留在输出中，不要猜测其真实内容。"
                + "结构化字段中的编号须与输入一致；面向业务人员的结论文本请使用风险名称、险种中文名等可读表述，不要输出子类型编码或险种编码（如 R05-5、CY01）。"
            )
            user_text = json.dumps(llm_payload, ensure_ascii=False, default=str)
            user_content = [{"type": "text", "text": user_text}] + [{"type": "image_url", "image_url": {"url": u}} for u in images] if images else user_text
            result = client.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                json_mode=task.expect_json,
                max_tokens=decision.max_tokens,
                extra={**(model.params or {}), **(decision.params or {})},
            )
            from ..services.config_center import cfg as _cfg

            injections = privacy_gateway.scan_inbound(result.content) if _cfg("privacy.egress").get("inbound_scan", True) else []
            if injections:
                raise ValueError(f"返回内容含可疑指令，已拦截：{injections[0]}")
            output = parse_json_content(result.content) if task.expect_json else result.content
            if not task.validate(output):
                raise ValueError("模型输出不符合任务结构要求")
            if task.postprocess:
                output = task.postprocess(payload, output)
            tokens_in, tokens_out = result.tokens_in, result.tokens_out
        latency = int((time.perf_counter() - started) * 1000)
        if not task.validate(output):
            raise ValueError("模型输出不符合任务结构要求")
        # 只要本轮有运行上下文 vault，就回填脱敏真值（不限是否本次出域）
        if ctx is not None or egress_id is not None:
            output = privacy_gateway.rehydrate(output, vault)
        cost = (tokens_in + tokens_out) / 1000 * (model.price_per_1k or 0)
        self._log_call(ctx, task, model, decision, tokens_in, tokens_out, latency, "ok", None, fallback, egress_id, cost)
        if ctx:
            ctx.add_usage(model.zone, tokens_in, tokens_out, cost, latency)
        return output

    def _api_key(self, model: ModelRegistryEntry) -> str | None:
        from ..config import settings

        return settings.cloud_llm_api_key if model.zone == "cloud" else settings.private_llm_api_key

    def _log_call(
        self,
        ctx: GatewayContext | None,
        task: ReasoningTask,
        model: ModelRegistryEntry,
        decision: RouteDecision,
        tokens_in: int,
        tokens_out: int,
        latency: int,
        status: str,
        error: str | None,
        fallback: bool,
        egress_id: int | None,
        cost: float = 0.0,
    ) -> None:
        with session_scope() as s:
            s.add(
                ModelCall(
                    run_id=ctx.run_id if ctx else None,
                    step_id=ctx.current_step_id if ctx else None,
                    task=task.name,
                    task_type=task.task_type,
                    model_id=model.id,
                    zone=model.zone,
                    rule_id=decision.rule_id,
                    candidates=[m.id for m in decision.chain],
                    data_level=decision.level,
                    fallback=fallback,
                    egress_id=egress_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost=cost,
                    latency_ms=latency,
                    status=status,
                    error=truncate(error or "", 500) or None,
                )
            )

    # ------------------------------------------------------------------ 规划模型（工具调用）
    def planning_model(self, level: int, scope: str | None, ctx: GatewayContext | None = None) -> ModelRegistryEntry | None:
        """返回可执行工具调用的大模型；没有时返回 None，由 Harness 使用技能规程策略驱动。"""
        skip_llm = risk_warning_llm_blocked(scope)
        if skip_llm and ctx and not getattr(ctx, "_rw_llm_noted", False):
            ctx._rw_llm_noted = True
            ctx.emit("model.restricted", {"reason": "风险预警未启用大模型（TECHINS_DISABLE_LLM 与 TECHINS_DISABLE_RW_LLM 需同时为 0），规划改为按技能规程执行"})
        decision = self.route("TT-01", level, scope, private_only=self._cloud_restriction(ctx), skip_llm=skip_llm)
        for model in decision.chain:
            if model.kind == "llm" and "tool_calling" in (model.capabilities or []):
                return model
        return None

    def chat_with_tools(
        self,
        model: ModelRegistryEntry,
        messages: list[dict],
        tools: list[dict],
        *,
        ctx: GatewayContext,
        level: int,
    ) -> dict:
        payload = messages
        egress_id = None
        if model.zone == "cloud" and level >= 2:
            payload, egress_id, _ = privacy_gateway.egress(
                messages,
                ctx.vault,
                run_id=ctx.run_id,
                step_id=ctx.current_step_id,
                agent_id=ctx.agent_id,
                task="harness.plan",
                task_type="TT-01",
                source_level=level,
                target_model=model.name,
            )
        from ..config import settings

        client = OpenAICompatibleClient(model.base_url or "", self._api_key(model), model.model_name or "", timeout=settings.planner_timeout_s, retries=2)
        result = client.chat(payload, tools=tools, extra={**(model.params or {}), **settings.planner_params})
        content = result.content
        calls = result.tool_calls
        content = privacy_gateway.rehydrate(content, ctx.vault)
        calls = privacy_gateway.rehydrate(calls, ctx.vault)
        ctx.add_usage(model.zone, result.tokens_in, result.tokens_out, 0.0, result.latency_ms)
        with session_scope() as s:
            s.add(
                ModelCall(
                    run_id=ctx.run_id,
                    step_id=ctx.current_step_id,
                    task="harness.plan",
                    task_type="TT-01",
                    model_id=model.id,
                    zone=model.zone,
                    data_level=level,
                    egress_id=egress_id,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=result.latency_ms,
                )
            )
        return {"content": content, "tool_calls": calls}


model_gateway = ModelGateway()
