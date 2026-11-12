"""人审节点：卡片创建、领取、处理与超时默认策略；处理结果经信箱回到运行并恢复执行。"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..models import HitlCard, Run
from . import mailbox
from .events import event_bus

if TYPE_CHECKING:
    from .context import RunContext

CARD_TYPE_LABELS = {
    "plan_confirm": "计划确认",
    "result_confirm": "结果确认",
    "supplement": "补充材料",
    "option_select": "方案选择",
    "clarify": "澄清问题",
    "egress_approval": "出域审批",
    "action_confirm": "对外动作确认",
    "iteration_limit": "迭代超限",
    "budget": "预算耗尽",
    "decision": "业务决定",
    "incident_start": "启动事故响应",
    "provider_dispatch": "服务商调度",
    "supplement_send": "补充材料发送",
    "causal_confirm": "因果边确认",
    "field_check": "关键字段核对",
    "claim_approval": "赔付审批",
    "payment": "支付指令",
    "manual_compliance": "人工合规复核",
    "factor_confirm": "因子确认",
    "ab_next": "实验下一轮",
}


class HitlError(ValueError):
    pass


def create_card(ctx: "RunContext | None", spec: dict, *, run_id: int | None = None, business_ref: dict | None = None) -> int:
    rid = ctx.run_id if ctx else run_id
    deadline = utcnow() + timedelta(hours=spec.get("deadline_hours", 24))
    with session_scope() as s:
        card = HitlCard(
            run_id=rid,
            card_type=spec["card_type"],
            title=spec["title"],
            reason=spec["reason"],
            impact=spec.get("impact", "不处理时运行将保持暂停"),
            context=spec.get("context", {}),
            options=spec.get("options") or [{"key": "approve", "label": "确认", "primary": True}, {"key": "reject", "label": "驳回", "requires_comment": True}],
            mandatory=spec.get("mandatory", True),
            default_action=spec.get("default_action"),
            deadline=deadline,
            assignee_role=spec.get("assignee_role", "designer"),
            assignee_id=spec.get("assignee_id") or (ctx.created_by if ctx else None),
            business_ref=business_ref or (ctx.business_ref if ctx else {}),
        )
        s.add(card)
        s.flush()
        card_id = card.id
        if not spec.get("silent"):
            from ..services.notify import notify

            notify(s, kind="hitl", title=f"待处理：{spec['title']}", body=spec["reason"], link=f"/inbox?card={card_id}", level="warning" if spec.get("mandatory", True) else "info", user_id=card.assignee_id, role=card.assignee_role)
    if rid:
        event_bus.emit(rid, "hitl.requested", {"card_id": card_id, "card_type": spec["card_type"], "type_label": CARD_TYPE_LABELS.get(spec["card_type"], spec["card_type"]), "title": spec["title"], "reason": spec["reason"], "options": spec.get("options"), "deadline": deadline.isoformat() + "Z"})
    return card_id


def resolve(card_id: int, *, user_id: int | None, action: str, comment: str = "", edits: dict | None = None, system: bool = False) -> dict:
    from .executor import executor

    with session_scope() as s:
        card = s.get(HitlCard, card_id)
        if card is None:
            raise HitlError("人审卡片不存在")
        if card.status != "open":
            raise HitlError("该卡片已被处理")
        option = next((o for o in card.options if o["key"] == action), None)
        if option is None:
            raise HitlError(f"无效的处理动作：{action}")
        if option.get("requires_comment") and not comment.strip():
            raise HitlError("该动作需要填写说明")
        card.status = "resolved" if not system else "expired"
        card.resolved_by = user_id
        card.resolution = {"action": action, "label": option["label"], "comment": comment, "edits": edits or {}, "system": system}
        card.resolved_at = utcnow()
        run_id = card.run_id
        payload = {"card_id": card.id, "card_type": card.card_type, "context": card.context, "user_id": user_id, **card.resolution}
        resubmit = False
        if run_id:
            from .timing import apply_status

            run = s.get(Run, run_id)
            if run is not None and run.status in ("waiting_human", "paused"):
                apply_status(run, "queued")
                run.waiting_for = {}
                resubmit = True
    if run_id:
        mailbox.send(kind="hitl_resolution", payload=payload, to_run_id=run_id)
        event_bus.emit(run_id, "hitl.resolved", {"card_id": card_id, "action": action, "label": payload["label"], "comment": comment, "by": user_id})
        if resubmit:
            executor.submit(run_id)
    return payload


def expire_overdue() -> int:
    """超时卡片按默认策略处理；没有默认策略的保持等待并提醒。"""
    count = 0
    with session_scope() as s:
        overdue = s.scalars(select(HitlCard).where(HitlCard.status == "open", HitlCard.deadline < utcnow())).all()
        targets = [(c.id, c.default_action) for c in overdue if c.default_action]
    for card_id, action in targets:
        try:
            resolve(card_id, user_id=None, action=action, comment="超时按默认策略处理", system=True)
            count += 1
        except HitlError:
            continue
    return count
