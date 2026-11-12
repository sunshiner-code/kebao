"""信箱：智能体之间以及人审回复的异步消息，请求与回复以编号一一对应。"""

from __future__ import annotations

from sqlalchemy import select

from ..db import session_scope
from ..models import MailboxMessage


def send(*, kind: str, payload: dict, to_run_id: int | None = None, to_agent: str | None = None, from_run_id: int | None = None, from_agent: str | None = None, request_no: str | None = None) -> int:
    with session_scope() as s:
        msg = MailboxMessage(to_run_id=to_run_id, to_agent=to_agent, from_run_id=from_run_id, from_agent=from_agent, kind=kind, payload=payload, request_no=request_no)
        s.add(msg)
        s.flush()
        return msg.id


def unread(run_id: int) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(MailboxMessage).where(MailboxMessage.to_run_id == run_id, MailboxMessage.status == "unread").order_by(MailboxMessage.id)).all()
        return [{"id": r.id, "kind": r.kind, "payload": r.payload, "request_no": r.request_no, "from_agent": r.from_agent, "from_run_id": r.from_run_id} for r in rows]


def mark_read(message_ids: list[int]) -> None:
    if not message_ids:
        return
    with session_scope() as s:
        for r in s.scalars(select(MailboxMessage).where(MailboxMessage.id.in_(message_ids))):
            r.status = "read"
