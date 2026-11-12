"""平台核心接口：登录、工作台、通知、运行与事件流、人审、产物、智能体中心、知识库、运维管理。"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sse_starlette.sse import EventSourceResponse

from ..config import settings
from ..db import session_scope, utcnow
from ..harness import hitl
from ..harness.agent import all_profiles, get_profile
from ..harness.events import event_bus
from ..harness.executor import executor
from ..harness.skills import skill_library
from ..harness.tools import registry
from ..knowledge.catalog import DETECTION_METHODS, LINES
from ..knowledge.portfolio import DEFERRED_PRODUCTS
from ..knowledge.index import knowledge_index
from ..knowledge.maturity import all_maturity
from ..llm.tasks import all_tasks
from ..models import (
    Alert,
    Application,
    Artifact,
    AuditLog,
    BackgroundTask,
    ClaimCase,
    DesignProject,
    EgressLog,
    Evaluation,
    HitlCard,
    Incident,
    KnowledgeDoc,
    MailboxMessage,
    MemoryEntry,
    ModelCall,
    ModelRegistryEntry,
    Notification,
    Policy,
    Product,
    Reflection,
    RoutingRule,
    Run,
    RunStep,
    ToolCall,
    User,
)
from ..seed.runner import verify_password
from .deps import ApiError, audit, current_user, iso, issue_token, require, user_dict

router = APIRouter(prefix="/api")

AGENT_NAMES = {"AG-RA": "风险评估", "AG-CD": "条款设计", "AG-AP": "精算定价", "AG-UW": "承保风控", "AG-RW": "风险预警", "AG-CL": "定损理赔"}
RUN_STATUS = {"queued": "排队中", "planning": "规划中", "running": "执行中", "waiting_human": "等待人工", "paused": "已暂停", "completed": "已完成", "failed": "失败", "cancelled": "已取消"}


def _active_since_iso(value) -> str | None:
    if not value:
        return None
    text = value if isinstance(value, str) else value.isoformat()
    return text if text.endswith("Z") else text + "Z"


def run_dict(r: Run, brief: bool = True) -> dict:
    from ..harness.timing import ACTIVE_STATUSES, duration_seconds

    usage = r.usage or {}
    d = {
        "id": r.id,
        "agent_id": r.agent_id,
        "agent_name": AGENT_NAMES.get(r.agent_id, r.agent_id),
        "purpose": r.purpose,
        "title": r.title,
        "status": r.status,
        "status_label": RUN_STATUS.get(r.status, r.status),
        "mode": r.mode,
        "start_mode": r.start_mode,
        "degrade_level": r.degrade_level,
        "progress": r.progress,
        "eval_score": r.eval_score,
        "eval_passed": r.eval_passed,
        "reflection_rounds": r.reflection_rounds,
        "parent_run_id": r.parent_run_id,
        "business_ref": r.business_ref,
        "waiting_for": r.waiting_for,
        "usage": usage,
        "duration_s": duration_seconds(r),
        "active_since": _active_since_iso(usage.get("active_since")) if r.status in ACTIVE_STATUSES else None,
        "created_at": iso(r.created_at),
        "started_at": iso(r.started_at),
        "ended_at": iso(r.ended_at),
        "summary": (r.output or {}).get("summary"),
    }
    if not brief:
        d.update({"input": {k: v for k, v in (r.input or {}).items() if k != "candidate"} | ({"candidate": {"key": r.input["candidate"]["key"], "level_hint": r.input["candidate"]["level_hint"]}} if (r.input or {}).get("candidate") else {}), "output": r.output, "budget": r.budget, "locked_versions": r.locked_versions, "error": r.error, "agent_version": r.agent_version})
    return d


def card_dict(c: HitlCard) -> dict:
    return {"id": c.id, "run_id": c.run_id, "card_type": c.card_type, "type_label": hitl.CARD_TYPE_LABELS.get(c.card_type, c.card_type), "title": c.title, "reason": c.reason, "impact": c.impact, "context": c.context, "options": c.options, "mandatory": c.mandatory, "default_action": c.default_action, "deadline": iso(c.deadline), "assignee_role": c.assignee_role, "assignee_id": c.assignee_id, "status": c.status, "resolution": c.resolution, "business_ref": c.business_ref, "created_at": iso(c.created_at) if hasattr(c, "created_at") else None, "resolved_at": iso(c.resolved_at)}


# ================================================================ 登录
class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request) -> dict:
    from ..services.config_center import cfg
    from ..services.iam import IamError
    from .deps import _check_ip

    ip = request.client.host if request.client else ""
    try:
        _check_ip(ip)
    except ApiError:
        audit(None, "login", "地址不在白名单", "user", body.username, {"ip": ip})
        raise
    sec = cfg("security")
    failure: dict | None = None
    with session_scope() as s:
        u = s.scalars(select(User).where(User.username == body.username)).first()
        if u is None:
            failure = {"status": 401, "message": "用户名或密码错误", "suggestion": "演示账号密码为 demo1234", "audit": ("登录失败", body.username, {"ip": ip})}
        elif not u.enabled:
            failure = {"status": 403, "message": "账号已停用", "suggestion": "联系管理员"}
        elif u.locked_until and u.locked_until > utcnow():
            failure = {"status": 423, "message": f"连续登录失败，账号锁定至 {(u.locked_until + timedelta(hours=8)):%m-%d %H:%M}（北京时间）"}
        elif not verify_password(body.password, u.password_hash):
            u.failed_logins = (u.failed_logins or 0) + 1
            detail = {"ip": ip}
            action = "登录失败"
            if u.failed_logins >= int(sec["lockout_threshold"]):
                u.locked_until = utcnow() + timedelta(minutes=int(sec["lockout_minutes"]))
                action = "登录失败锁定"
                detail["failures"] = u.failed_logins
            failure = {"status": 401, "message": "用户名或密码错误", "suggestion": "演示账号密码为 demo1234", "audit": (action, u.username, detail)}
        else:
            u.failed_logins = 0
            u.locked_until = None
            u.last_login_at = utcnow()
            token = issue_token(u)
            info = user_dict(u, with_permissions=True, s=s)
    # 审计单独开事务。写在当前登录事务里会在 SQLite 上嵌套 BEGIN IMMEDIATE，同一线程自我死锁。
    if failure:
        audited = failure.get("audit")
        if audited:
            audit(None, "login", audited[0], "user", audited[1], audited[2])
        raise ApiError(failure["status"], "权限不足", failure["message"], failure.get("suggestion", ""))
    info["client_ip"] = ip
    audit(info, "login", "登录成功", "user", info["id"])
    _ = IamError
    return {"token": token, "user": info}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


@router.get("/auth/options")
def auth_options() -> dict:
    return {"show_demo_accounts": settings.show_demo_accounts}


@router.get("/auth/demo-users")
def demo_users() -> list[dict]:
    with session_scope() as s:
        return [user_dict(u) for u in s.scalars(select(User).order_by(User.id)).all()]


# ================================================================ 工作台
@router.get("/dashboard")
def dashboard(user: dict = Depends(current_user)) -> dict:
    now = utcnow()
    with session_scope() as s:
        cards = s.scalars(select(HitlCard).where(HitlCard.status == "open").order_by(HitlCard.deadline)).all()
        mine = [c for c in cards if user["role"] == "admin" or c.assignee_role == user["role"] or c.assignee_id == user["id"]]
        runs = s.scalars(select(Run).where(Run.parent_run_id.is_(None)).order_by(Run.id.desc()).limit(400)).all()
        active = [r for r in runs if r.status in ("queued", "planning", "running", "waiting_human", "paused")]
        out: dict[str, Any] = {
            "todo_cards": [card_dict(c) for c in mine[:12]],
            "todo_count": len(mine),
            "overdue_count": sum(1 for c in mine if c.deadline and c.deadline < now),
            "active_runs": [run_dict(r) for r in active[:10]],
            "recent_runs": [run_dict(r) for r in runs[:8]],
        }
        if user["role"] in ("designer", "admin"):
            projects = s.scalars(select(DesignProject).order_by(DesignProject.id.desc())).all()
            out["projects"] = [{"id": p.id, "name": p.name, "status": p.status, "project_type": p.project_type, "track": p.track, "nodes": {k: v.get("status") for k, v in (p.pipeline or {}).get("nodes", {}).items()}} for p in projects[:8]]
            out["products"] = {st: s.scalar(select(func.count(Product.id)).where(Product.status == st)) or 0 for st in ("published", "pending_approval", "draft")}
        if user["role"] in ("service", "admin"):
            alerts = s.scalars(select(Alert).order_by(Alert.created_at.desc())).all()
            out["alerts"] = {"open": {lv: sum(1 for a in alerts if a.level == lv and a.status in ("new", "processing")) for lv in ("red", "orange", "yellow")}, "recent": [{"id": a.id, "alert_no": a.alert_no, "level": a.level, "title": a.title, "status": a.status, "created_at": iso(a.created_at), "sla_due": iso(a.sla_due)} for a in alerts[:6]]}
            out["uw_queue"] = s.scalar(select(func.count(Application.id)).where(Application.status.in_(["submitted", "underwriting", "pending_decision"]))) or 0
            out["claims_open"] = s.scalar(select(func.count(ClaimCase.id)).where(ClaimCase.status.notin_(["closed"]))) or 0
            out["incidents_open"] = s.scalar(select(func.count(Incident.id)).where(Incident.status.notin_(["closed"]))) or 0
            out["policies_active"] = s.scalar(select(func.count(Policy.id)).where(Policy.status == "active")) or 0
        if user["role"] == "admin":
            calls = s.scalars(select(ModelCall).where(ModelCall.ts >= now - timedelta(days=7))).all()
            out["compute"] = _compute_summary(calls)
            out["egress_count"] = s.scalar(select(func.count(EgressLog.id))) or 0
            out["run_stats"] = {st: sum(1 for r in runs if r.status == st) for st in RUN_STATUS}
    return out


def _compute_summary(calls: list[ModelCall]) -> dict:
    zones: dict[str, dict] = {}
    for c in calls:
        z = zones.setdefault(c.zone, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "latency_ms": 0, "errors": 0, "fallbacks": 0})
        z["calls"] += 1
        z["tokens_in"] += c.tokens_in
        z["tokens_out"] += c.tokens_out
        z["cost"] += c.cost
        z["latency_ms"] += c.latency_ms
        z["errors"] += c.status != "ok"
        z["fallbacks"] += bool(c.fallback)
    for z in zones.values():
        z["avg_latency_ms"] = round(z["latency_ms"] / z["calls"]) if z["calls"] else 0
        z["cost"] = round(z["cost"], 4)
    return zones


# ================================================================ 通知
@router.get("/notifications")
def notifications(user: dict = Depends(current_user), unread: bool = False) -> dict:
    with session_scope() as s:
        q = select(Notification).where(or_(Notification.user_id == user["id"], Notification.role == user["role"], user["role"] == "admin"))
        if unread:
            q = q.where(Notification.read.is_(False))
        rows = s.scalars(q.order_by(Notification.ts.desc()).limit(60)).all()
        count = s.scalar(select(func.count(Notification.id)).where(or_(Notification.user_id == user["id"], Notification.role == user["role"]), Notification.read.is_(False))) or 0
        return {"unread": count, "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "level": n.level, "read": n.read, "ts": iso(n.ts)} for n in rows]}


@router.post("/notifications/read")
def read_notifications(user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        for n in s.scalars(select(Notification).where(or_(Notification.user_id == user["id"], Notification.role == user["role"]), Notification.read.is_(False))).all():
            n.read = True
    return {"ok": True}


# ================================================================ 运行
@router.get("/runs")
def list_runs(user: dict = Depends(current_user), agent_id: str | None = None, status: str | None = None, include_children: bool = False, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        q = select(Run)
        if not include_children:
            q = q.where(Run.parent_run_id.is_(None))
        if agent_id:
            q = q.where(Run.agent_id == agent_id)
        if status:
            q = q.where(Run.status.in_(status.split(",")))
        return [run_dict(r) for r in s.scalars(q.order_by(Run.id.desc()).limit(limit)).all()]


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        r = s.get(Run, run_id)
        if r is None:
            raise ApiError(404, "状态冲突", "运行不存在")
        d = run_dict(r, brief=False)
        ckpt = (r.checkpoint or {}).get("path")
        todos, skills = [], []
        if ckpt:
            path = settings.workspaces_dir / ckpt
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                todos = payload.get("todos", [])
                skills = payload.get("state", {}).get("skills_loaded", [])
                d["state"] = {k: payload.get("state", {}).get(k) for k in ("start_mode", "degrade_level", "degrade_notes", "round", "reflection_rounds", "llm_policy_failed")}
        d["todos"] = todos
        d["skills_loaded"] = skills
        d["steps"] = [{"id": st.id, "seq": st.seq, "type": st.step_type, "title": st.title, "zone": st.zone, "status": st.status, "todo_id": st.todo_id, "summary": st.summary, "detail": st.detail, "data_level": st.data_level, "started_at": iso(st.started_at), "ended_at": iso(st.ended_at)} for st in s.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)).all()]
        d["evaluations"] = [{"id": e.id, "kind": e.kind, "target": e.target, "score": e.score, "passed": e.passed, "dimensions": e.dimensions, "failed_dims": e.failed_dims, "reviewer_model": e.reviewer_model, "ts": iso(e.ts)} for e in s.scalars(select(Evaluation).where(Evaluation.run_id == run_id).order_by(Evaluation.id)).all()]
        d["reflections"] = [{"round": x.round, "trigger": x.trigger, "diagnosis": x.diagnosis, "categories": x.categories, "strategy": x.strategy, "plan": x.plan, "score_before": x.score_before, "score_after": x.score_after, "outcome": x.outcome, "experience_id": x.experience_id} for x in s.scalars(select(Reflection).where(Reflection.run_id == run_id).order_by(Reflection.round)).all()]
        d["model_calls"] = [{"id": c.id, "task": c.task, "task_type": c.task_type, "model_id": c.model_id, "zone": c.zone, "rule_id": c.rule_id, "data_level": c.data_level, "fallback": c.fallback, "egress_id": c.egress_id, "tokens_in": c.tokens_in, "tokens_out": c.tokens_out, "cost": c.cost, "latency_ms": c.latency_ms, "status": c.status, "error": c.error, "ts": iso(c.ts)} for c in s.scalars(select(ModelCall).where(ModelCall.run_id == run_id).order_by(ModelCall.id)).all()]
        d["tool_calls"] = [{"id": t.id, "tool": t.tool, "permission": t.permission, "zone": t.zone, "args": t.args_summary, "result": t.result_summary, "guard": t.guard, "retries": t.retries, "duration_ms": t.duration_ms, "status": t.status, "error": t.error} for t in s.scalars(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.id)).all()]
        d["egress"] = [_egress_dict(e, mask=user["role"] != "admin") for e in s.scalars(select(EgressLog).where(EgressLog.run_id == run_id).order_by(EgressLog.id)).all()]
        d["children"] = [run_dict(c) for c in s.scalars(select(Run).where(Run.parent_run_id == run_id).order_by(Run.id)).all()]
        d["background"] = [{"id": b.id, "name": b.name, "status": b.status, "progress": b.progress, "summary": b.summary} for b in s.scalars(select(BackgroundTask).where(BackgroundTask.run_id == run_id)).all()]
        d["mail"] = [{"id": m.id, "kind": m.kind, "from_agent": m.from_agent, "request_no": m.request_no, "payload": m.payload, "status": m.status, "ts": iso(m.ts)} for m in s.scalars(select(MailboxMessage).where(or_(MailboxMessage.to_run_id == run_id, MailboxMessage.from_run_id == run_id))).all() if m.kind != "hitl_resolution"]
        d["cards"] = [card_dict(c) for c in s.scalars(select(HitlCard).where(HitlCard.run_id == run_id).order_by(HitlCard.id)).all()]
    return d


def _egress_dict(e: EgressLog, mask: bool) -> dict:
    return {"id": e.id, "run_id": e.run_id, "agent_id": e.agent_id, "task": e.task, "task_type": e.task_type, "source_level": e.source_level, "rules_hit": e.rules_hit, "fields": e.fields, "placeholder_count": e.placeholder_count, "scan_result": e.scan_result, "scan_findings": e.scan_findings, "target_model": e.target_model, "content_preview": e.content_preview, "original_preview": "（原文需管理员权限查看）" if mask else e.original_preview, "ts": iso(e.ts)}


@router.get("/runs/{run_id}/events")
def run_events(run_id: int, after: int = 0, user: dict = Depends(current_user)) -> list[dict]:
    return event_bus.fetch(run_id, after)


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: int, after: int = 0, user: dict = Depends(current_user)):
    async def gen():
        last = after
        idle = 0.0
        while True:
            events = await asyncio.to_thread(event_bus.fetch, run_id, last)
            for ev in events:
                last = ev["seq"]
                yield {"event": "message", "id": str(ev["seq"]), "data": json.dumps(ev, ensure_ascii=False)}
                idle = 0.0
            if not events:
                await asyncio.to_thread(event_bus.wait, run_id, 2.0)
                idle += 2.0
                if idle >= 15:
                    idle = 0.0
                    yield {"event": "heartbeat", "data": json.dumps({"ts": iso(utcnow())})}

    return EventSourceResponse(gen())


class ControlBody(BaseModel):
    command: str


@router.post("/runs/{run_id}/control")
def control_run(run_id: int, body: ControlBody, user: dict = Depends(current_user)) -> dict:
    try:
        status = executor.control(run_id, body.command)
    except ValueError as exc:
        raise ApiError(409, "状态冲突", str(exc)) from exc
    audit(user, "operation", f"运行控制：{body.command}", "run", run_id)
    return {"status": status}


class StartRunBody(BaseModel):
    inputs: dict
    purpose: str = "main"
    mode: str = "auto"
    title: str = ""
    business_ref: dict = {}


@router.post("/agents/{agent_id}/runs")
def start_run(agent_id: str, body: StartRunBody, user: dict = Depends(current_user)) -> dict:
    try:
        get_profile(agent_id, body.purpose)
    except KeyError as exc:
        raise ApiError(404, "输入契约不满足", "智能体不存在") from exc
    rid = executor.create_run(agent_id=agent_id, purpose=body.purpose, inputs=body.inputs, business_ref=body.business_ref or {k: v for k, v in body.inputs.items() if k.endswith("_id")}, mode=body.mode, user_id=user["id"], title=body.title)
    audit(user, "operation", "发起运行", "run", rid, {"agent_id": agent_id})
    return {"run_id": rid}


# ================================================================ 人审
@router.get("/hitl/cards")
def list_cards(user: dict = Depends(current_user), status: str = "open", scope: str = "mine") -> list[dict]:
    with session_scope() as s:
        q = select(HitlCard)
        if status != "all":
            q = q.where(HitlCard.status == status)
        rows = s.scalars(q.order_by(HitlCard.id.desc()).limit(200)).all()
        if scope == "mine" and user["role"] != "admin":
            rows = [c for c in rows if c.assignee_role == user["role"] or c.assignee_id == user["id"]]
        return [card_dict(c) for c in rows]


@router.get("/hitl/cards/{card_id}")
def get_card(card_id: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        c = s.get(HitlCard, card_id)
        if c is None:
            raise ApiError(404, "状态冲突", "卡片不存在")
        return card_dict(c)


class ResolveBody(BaseModel):
    action: str
    comment: str = ""
    edits: dict | None = None


@router.post("/hitl/cards/{card_id}/resolve")
def resolve_card(card_id: int, body: ResolveBody, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        c = s.get(HitlCard, card_id)
        if c is None:
            raise ApiError(404, "状态冲突", "卡片不存在")
        need = int((c.context or {}).get("approval_level") or 1)
        role = c.assignee_role
    if user["role"] != "admin" and role != user["role"]:
        raise ApiError(403, "权限不足", "该卡片分派给其他岗位")
    if need > user["approval_level"] and body.action in ("approve",) or (need > user["approval_level"] and body.action.startswith("option")):
        raise ApiError(403, "权限不足", f"该事项需要 {need} 级审批权限，你当前为 {user['approval_level']} 级", "转交具备相应审批等级的人员处理")
    try:
        out = hitl.resolve(card_id, user_id=user["id"], action=body.action, comment=body.comment, edits=body.edits)
    except hitl.HitlError as exc:
        raise ApiError(409, "状态冲突", str(exc)) from exc
    audit(user, "operation", f"处理人审：{out['label']}", "hitl_card", card_id, {"comment": body.comment})
    return out


# ================================================================ 产物
def artifact_dict(a: Artifact, with_data: bool = True) -> dict:
    d = {"id": a.id, "artifact_key": a.artifact_key, "kind": a.kind, "version": a.version, "status": a.status, "title": a.title, "project_id": a.project_id, "ref_type": a.ref_type, "ref_id": a.ref_id, "upstream": a.upstream, "run_id": a.run_id, "revisions": a.revisions, "created_at": iso(a.created_at), "frozen_at": iso(a.frozen_at)}
    if with_data:
        d["data"] = {k: v for k, v in (a.data or {}).items() if not k.startswith("_")}
    return d


@router.get("/artifacts/{key}")
def get_artifact(key: str, version: int | None = None, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        q = select(Artifact).where(Artifact.artifact_key == key)
        q = q.where(Artifact.version == version) if version else q.order_by(Artifact.version.desc())
        a = s.scalars(q).first()
        if a is None:
            raise ApiError(404, "状态冲突", "产物不存在")
        d = artifact_dict(a)
        d["versions"] = [{"version": v.version, "status": v.status, "created_at": iso(v.created_at)} for v in s.scalars(select(Artifact).where(Artifact.artifact_key == key).order_by(Artifact.version)).all()]
    audit(user, "data_access", "查看产物", "artifact", f"{key}@{d['version']}")
    return d


@router.get("/artifacts")
def list_artifacts(kind: str | None = None, ref_type: str | None = None, ref_id: int | None = None, project_id: int | None = None, user: dict = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        q = select(Artifact)
        if kind:
            q = q.where(Artifact.kind == kind)
        if ref_type:
            q = q.where(Artifact.ref_type == ref_type)
        if ref_id is not None:
            q = q.where(Artifact.ref_id == ref_id)
        if project_id is not None:
            q = q.where(Artifact.project_id == project_id)
        return [artifact_dict(a, with_data=False) for a in s.scalars(q.order_by(Artifact.id.desc()).limit(200)).all()]


class RevisionBody(BaseModel):
    path: str
    value: Any
    reason: str
    base_version: int


@router.patch("/artifacts/{key}")
def revise_artifact(key: str, body: RevisionBody, user: dict = Depends(current_user)) -> dict:
    """人工修订：按字段路径写入新值，生成新版本并记录修订；携带基线版本号做乐观并发控制。"""
    from ..harness.memory import human_revision_experience
    from ..jsonutil import jsonable

    with session_scope() as s:
        cur = s.scalars(select(Artifact).where(Artifact.artifact_key == key).order_by(Artifact.version.desc())).first()
        if cur is None:
            raise ApiError(404, "状态冲突", "产物不存在")
        if cur.version != body.base_version:
            raise ApiError(409, "并发修改冲突", f"产物已更新到 V{cur.version}，请刷新后再修改")
        data = json.loads(json.dumps(cur.data))
        node = data
        parts = [p for p in body.path.split(".") if p]
        for p in parts[:-1]:
            node = node[int(p)] if isinstance(node, list) else node[p]
        last = parts[-1]
        old = node[int(last)] if isinstance(node, list) else node.get(last)
        if isinstance(node, list):
            node[int(last)] = body.value
        else:
            node[last] = body.value
        new = Artifact(artifact_key=key, kind=cur.kind, version=cur.version + 1, title=cur.title, project_id=cur.project_id, ref_type=cur.ref_type, ref_id=cur.ref_id, data=jsonable(data), upstream=cur.upstream, run_id=cur.run_id, revisions=list(cur.revisions or []) + [{"path": body.path, "old": old, "new": body.value, "reason": body.reason, "by": user["id"], "at": iso(utcnow())}], created_by=user["id"])
        s.add(new)
        s.flush()
        agent = {"risk_report": "AG-RA", "clause_plan": "AG-CD", "pricing_plan": "AG-AP", "uw_report": "AG-UW", "claim_result": "AG-CL", "risk_mgmt_report": "AG-RW"}.get(cur.kind)
        out = artifact_dict(new)
    if agent:
        human_revision_experience(agent, {"AG-RA": "SK-01", "AG-CD": "SK-02", "AG-AP": "SK-03", "AG-UW": "SK-04", "AG-RW": "SK-05", "AG-CL": "SK-06"}[agent], ".".join(p for p in parts if not p.isdigit()), body.reason, None)
    audit(user, "operation", "人工修订产物", "artifact", key, {"path": body.path, "reason": body.reason})
    return out


@router.post("/artifacts/{key}/freeze")
def freeze(key: str, version: int, user: dict = Depends(current_user)) -> dict:
    from ..agents.common import freeze_artifact

    freeze_artifact(key, version)
    audit(user, "operation", "冻结产物", "artifact", f"{key}@{version}")
    return {"ok": True}


# ================================================================ 智能体中心
@router.get("/agents")
def agents(user: dict = Depends(current_user)) -> list[dict]:
    out = []
    with session_scope() as s:
        stats = {row[0]: {"runs": row[1], "avg_score": round(row[2] or 0, 1)} for row in s.execute(select(Run.agent_id, func.count(Run.id), func.avg(Run.eval_score)).where(Run.parent_run_id.is_(None)).group_by(Run.agent_id)).all()}
        passed = {row[0]: row[1] for row in s.execute(select(Run.agent_id, func.count(Run.id)).where(Run.eval_passed.is_(True), Run.parent_run_id.is_(None)).group_by(Run.agent_id)).all()}
        exps = {row[0]: row[1] for row in s.execute(select(MemoryEntry.agent_id, func.count(MemoryEntry.id)).where(MemoryEntry.kind == "experience").group_by(MemoryEntry.agent_id)).all()}
    for aid, p in sorted(all_profiles().items(), key=lambda kv: list(AGENT_NAMES).index(kv[0])):
        st = stats.get(aid, {"runs": 0, "avg_score": 0})
        out.append({
            "agent_id": aid,
            "name": p.name,
            "short": AGENT_NAMES[aid],
            "version": p.version,
            "description": p.description,
            "core_skill": p.core_skill,
            "shared_skills": p.shared_skills,
            "purposes": {k: v.name for k, v in p.purposes.items()},
            "tools": sorted(t for t in p.tool_whitelist() if registry.has(t) and not registry.get(t).platform),
            "rubric": [{"key": d.key, "name": d.name, "layer": d.layer, "threshold": d.threshold} for d in p.final_rubric],
            "reflection": [{"dims": r.dims, "category": r.category, "strategy": r.strategy} for r in p.reflection_rules],
            "hitl": list(p.hitl_builders),
            "pass_score": p.pass_score,
            "runs": st["runs"],
            "avg_score": st["avg_score"],
            "pass_rate": round(passed.get(aid, 0) / st["runs"], 3) if st["runs"] else None,
            "experiences": exps.get(aid, 0),
        })
    return out


@router.get("/tools")
def tools(user: dict = Depends(current_user)) -> list[dict]:
    return [{"name": t.name, "title": t.title, "description": t.description, "permission": t.permission, "zone": t.zone, "max_level": t.max_level, "platform": t.platform} for t in registry.all().values()]


@router.get("/skills")
def skills(user: dict = Depends(current_user)) -> list[dict]:
    return [{"skill_id": sk.skill_id, "title": sk.title, "summary": sk.summary, "when": sk.when, "version": sk.version, "steps": len(sk.procedure), "subprocedures": {k: len(v) for k, v in sk.subprocedures.items()}, "tools": sk.tools, "digest": sk.digest} for sk in skill_library.all()]


@router.get("/skills/{skill_id}")
def skill_detail(skill_id: str, user: dict = Depends(current_user)) -> dict:
    sk = skill_library.get(skill_id)
    return {"skill_id": sk.skill_id, "title": sk.title, "version": sk.version, "text": skill_library.render(skill_id), "procedure": [{"key": p.key, "title": p.title, "tool": p.tool, "hitl": p.hitl, "condition": p.condition, "stage_eval": p.stage_eval, "purpose": p.purpose} for p in sk.procedure], "subprocedures": {k: [{"key": p.key, "title": p.title, "tool": p.tool, "hitl": p.hitl, "condition": p.condition} for p in v] for k, v in sk.subprocedures.items()}}


@router.get("/reasoning-tasks")
def reasoning_tasks(user: dict = Depends(current_user)) -> list[dict]:
    return [{"name": t.name, "task_type": t.task_type, "description": t.description, "has_llm_postprocess": t.postprocess is not None} for t in all_tasks().values()]


@router.get("/memory")
def memory(kind: str | None = None, agent_id: str | None = None, user: dict = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        q = select(MemoryEntry)
        if kind:
            q = q.where(MemoryEntry.kind == kind)
        if agent_id:
            q = q.where(MemoryEntry.agent_id == agent_id)
        rows = s.scalars(q.order_by(MemoryEntry.updated_at.desc()).limit(300)).all()
        return [{"id": m.id, "kind": m.kind, "agent_id": m.agent_id, "skill_id": m.skill_id, "subject": m.subject_key, "title": m.title, "text": m.text, "conditions": m.conditions, "status": m.status, "hits": m.hits, "successes": m.successes, "data_level": m.data_level, "source_run_id": m.source_run_id, "updated_at": iso(m.updated_at)} for m in rows]


class MemoryStatusBody(BaseModel):
    status: str
    reason: str = ""


@router.post("/memory/{entry_id}/status")
def memory_status(entry_id: int, body: MemoryStatusBody, user: dict = Depends(require("admin"))) -> dict:
    """自进化治理：候选经验经人工评审后生效、退役或保持试用。"""
    if body.status not in ("active", "trial", "retired"):
        raise ApiError(422, "输入契约不满足", "状态只能为 active、trial 或 retired")
    with session_scope() as s:
        m = s.get(MemoryEntry, entry_id)
        if m is None:
            raise ApiError(404, "状态冲突", "记忆条目不存在")
        m.status = body.status
        m.updated_at = utcnow()
    audit(user, "config", f"经验条目状态改为 {body.status}", "memory", entry_id, {"reason": body.reason})
    return {"ok": True}


# ================================================================ 知识库
@router.get("/knowledge/search")
def kb_search(q: str = Query(..., min_length=1), kbs: str | None = None, user: dict = Depends(current_user)) -> list[dict]:
    hits = knowledge_index.search(q, kbs=[k for k in (kbs or "").split(",") if k] or None, max_level=1 if user["role"] == "designer" else 2, top_k=12)
    return hits


@router.get("/knowledge/stats")
def kb_stats(user: dict = Depends(current_user)) -> dict:
    from ..services.knowledge_admin import library_stats

    return library_stats()


@router.get("/knowledge/docs")
def kb_docs(
    kb: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(current_user),
) -> dict:
    """知识库文档列表：服务端分页，正文只返回前 600 字摘要。"""
    with session_scope() as s:
        count_q = select(func.count()).select_from(KnowledgeDoc)
        q = select(
            KnowledgeDoc.id,
            KnowledgeDoc.kb,
            KnowledgeDoc.title,
            KnowledgeDoc.source,
            KnowledgeDoc.doc_type,
            KnowledgeDoc.status,
            KnowledgeDoc.lines,
            KnowledgeDoc.risk_types,
            KnowledgeDoc.tags,
            KnowledgeDoc.quality,
            KnowledgeDoc.cite_count,
            func.substr(KnowledgeDoc.text, 1, 600).label("text"),
        )
        if kb:
            count_q = count_q.where(KnowledgeDoc.kb == kb)
            q = q.where(KnowledgeDoc.kb == kb)
        total = int(s.scalar(count_q) or 0)
        pages = (total + page_size - 1) // page_size if total else 0
        current = min(page, pages) if pages else 1
        offset = (current - 1) * page_size
        q = q.order_by(KnowledgeDoc.id).offset(offset).limit(page_size)
        items = [
            {
                "id": id_,
                "kb": kb_,
                "title": title,
                "source": source,
                "doc_type": doc_type,
                "status": status,
                "lines": lines,
                "risk_types": risk_types,
                "tags": tags,
                "quality": quality,
                "cite_count": cite_count,
                "text": text or "",
            }
            for id_, kb_, title, source, doc_type, status, lines, risk_types, tags, quality, cite_count, text in s.execute(q)
        ]
        return {"items": items, "total": total, "page": current, "page_size": page_size, "pages": pages}


@router.get("/knowledge/maturity")
def maturity(user: dict = Depends(current_user)) -> list[dict]:
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in all_maturity()]


@router.get("/catalog")
def catalog(user: dict = Depends(current_user)) -> dict:
    from ..knowledge.catalog import FRAUD_PATTERNS, RISK_TAXONOMY, STAGES, SUB_PHASES

    return {"lines": LINES, "deferred": DEFERRED_PRODUCTS, "stages": STAGES, "sub_phases": SUB_PHASES, "risk_taxonomy": {k: {"name": v["name"], "subtypes": {sk: {"name": sv["name"], "insurable": sv["insurable"], "patterns": sv["patterns"], "stages": sv["stages"], "lines": sv["lines"], "consequence": sv["consequence"]} for sk, sv in v["subtypes"].items()}} for k, v in RISK_TAXONOMY.items()}, "detection_methods": DETECTION_METHODS, "fraud_patterns": FRAUD_PATTERNS, "agents": AGENT_NAMES}


# ================================================================ 运维管理
def _display_model(model_id: str) -> str | None:
    """展示名只给前端卡片用，每次从 .env 读取，不参与实际调用。"""
    from dotenv import dotenv_values

    values = dotenv_values(settings.root_dir / ".env")
    key = "CLOUD_LLM_DISPLAY_MODEL" if model_id == "cloud-llm" else "PRIVATE_LLM_DISPLAY_MODEL" if model_id == "private-llm" else ""
    if not key:
        return None
    name = (values.get(key) or "").strip()
    return name or None


@router.get("/admin/models")
def admin_models(user: dict = Depends(require("admin"))) -> list[dict]:
    with session_scope() as s:
        return [{"id": m.id, "name": m.name, "zone": m.zone, "kind": m.kind, "tier": m.tier, "capabilities": m.capabilities, "max_level": m.max_level, "base_url": m.base_url, "model_name": m.model_name, "display_model": _display_model(m.id), "context_window": m.context_window, "price_per_1k": m.price_per_1k, "params": m.params, "enabled": m.enabled, "configured": m.configured, "health": m.health, "error_rate": m.error_rate, "avg_latency_ms": m.avg_latency_ms} for m in s.scalars(select(ModelRegistryEntry).order_by(ModelRegistryEntry.zone, ModelRegistryEntry.id)).all()]


class ModelPatch(BaseModel):
    enabled: bool | None = None
    params: dict | None = None
    max_level: int | None = None


@router.patch("/admin/models/{model_id}")
def patch_model(model_id: str, body: ModelPatch, user: dict = Depends(require("admin"))) -> dict:
    with session_scope() as s:
        m = s.get(ModelRegistryEntry, model_id)
        if m is None:
            raise ApiError(404, "状态冲突", "模型不存在")
        if body.max_level is not None and m.zone == "cloud" and body.max_level > 2:
            raise ApiError(422, "隐私约束拒绝", "云端模型最高只能处理 L2 脱敏后的数据")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(m, k, v)
    audit(user, "config", "修改模型注册", "model", model_id, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.get("/admin/routing")
def routing(user: dict = Depends(require("admin"))) -> list[dict]:
    with session_scope() as s:
        return [{"id": r.id, "name": r.name, "scope": r.scope, "task_types": r.task_types, "data_levels": r.data_levels, "preferred": r.preferred, "fallback": r.fallback, "priority": r.priority, "max_tokens": r.max_tokens, "temperature": r.temperature, "params": r.params, "version": r.version, "enabled": r.enabled} for r in s.scalars(select(RoutingRule).order_by(RoutingRule.id)).all()]


class RulePatch(BaseModel):
    preferred: list[str] | None = None
    fallback: list[str] | None = None
    enabled: bool | None = None
    max_tokens: int | None = None


@router.patch("/admin/routing/{rule_id}")
def patch_rule(rule_id: str, body: RulePatch, user: dict = Depends(require("admin"))) -> dict:
    from ..llm.registry import validate_rule

    with session_scope() as s:
        r = s.get(RoutingRule, rule_id)
        if r is None:
            raise ApiError(404, "状态冲突", "规则不存在")
        models = {m.id: m for m in s.scalars(select(ModelRegistryEntry)).all()}
        merged = {"preferred": body.preferred if body.preferred is not None else r.preferred, "fallback": body.fallback if body.fallback is not None else r.fallback, "data_levels": r.data_levels, "task_types": r.task_types}
        try:
            validate_rule(merged, models)
        except ValueError as exc:
            raise ApiError(422, "隐私约束拒绝", str(exc)) from exc
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(r, k, v)
        r.version += 1
    audit(user, "config", "修改路由规则", "routing_rule", rule_id, body.model_dump(exclude_none=True))
    return {"ok": True}


@router.get("/admin/compute")
def compute(days: int = 7, user: dict = Depends(require("admin"))) -> dict:
    since = utcnow() - timedelta(days=days)
    with session_scope() as s:
        calls = s.scalars(select(ModelCall).where(ModelCall.ts >= since)).all()
        by_agent: dict[str, dict] = {}
        by_task: dict[str, dict] = {}
        run_agent = {r.id: r.agent_id for r in s.scalars(select(Run)).all()}
        for c in calls:
            a = by_agent.setdefault(run_agent.get(c.run_id, "平台"), {"cloud": 0, "private": 0, "tokens": 0, "cost": 0.0})
            a["cloud" if c.zone == "cloud" else "private"] += 1
            a["tokens"] += c.tokens_in + c.tokens_out
            a["cost"] += c.cost
            t = by_task.setdefault(c.task_type, {"cloud": 0, "private": 0, "fallback": 0})
            t["cloud" if c.zone == "cloud" else "private"] += 1
            t["fallback"] += bool(c.fallback)
        egress = s.scalars(select(EgressLog).where(EgressLog.ts >= since)).all()
        return {"zones": _compute_summary(calls), "by_agent": by_agent, "by_task_type": by_task, "egress": {"count": len(egress), "placeholders": sum(e.placeholder_count for e in egress), "blocked": sum(1 for e in egress if e.scan_result != "pass")}, "recent_calls": [{"id": c.id, "run_id": c.run_id, "task": c.task, "model_id": c.model_id, "zone": c.zone, "data_level": c.data_level, "fallback": c.fallback, "latency_ms": c.latency_ms, "tokens": c.tokens_in + c.tokens_out, "status": c.status, "error": (c.error or "")[:200], "ts": iso(c.ts)} for c in sorted(calls, key=lambda x: -x.id)[:60]]}


@router.get("/admin/egress")
def egress(user: dict = Depends(require("admin"))) -> list[dict]:
    with session_scope() as s:
        rows = [_egress_dict(e, mask=False) for e in s.scalars(select(EgressLog).order_by(EgressLog.id.desc()).limit(200)).all()]
    audit(user, "data_access", "查看出域日志原文对照", "egress", None)
    return rows


@router.get("/admin/audit")
def audit_logs(user: dict = Depends(require("admin")), category: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(AuditLog)
        if category:
            q = q.where(AuditLog.category == category)
        return [{"id": a.id, "username": a.username, "category": a.category, "action": a.action, "object_type": a.object_type, "object_id": a.object_id, "detail": a.detail, "ts": iso(a.ts)} for a in s.scalars(q.order_by(AuditLog.id.desc()).limit(300)).all()]


@router.get("/admin/users")
def users(user: dict = Depends(require("admin"))) -> list[dict]:
    with session_scope() as s:
        return [user_dict(u) | {"enabled": u.enabled} for u in s.scalars(select(User).order_by(User.id)).all()]


@router.get("/admin/runtime")
def runtime_status(user: dict = Depends(require("admin"))) -> dict:
    from ..models import MonitorPlan

    with session_scope() as s:
        plans = s.scalars(select(MonitorPlan)).all()
        return {
            "executor_workers": settings.executor_workers,
            "queue": executor._queue.qsize(),
            "active": sorted(executor._active),
            "monitor": {"enabled": settings.monitor_enabled, "tick_seconds": settings.monitor_tick_seconds, "sim_minutes_per_tick": settings.simulated_minutes_per_tick, "plans": len(plans)},
            "llm_disabled": settings.llm_disabled,
            "rw_llm_disabled": settings.rw_llm_disabled,
        }
