"""审批流引擎：按配置生成节点（含条件分支）、或签与会签、驳回目标、转办、撤回、超时升级与业务回调。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..models import ApprovalRequest, Notification, User
from .config_center import cfg

log = logging.getLogger("techins.approvals")

Handler = Callable[[dict, dict | None], None]
_HANDLERS: dict[str, tuple[Handler | None, Handler | None]] = {}


class ApprovalError(ValueError):
    pass


def register(flow_type: str, on_approved: Handler | None, on_rejected: Handler | None = None) -> None:
    _HANDLERS[flow_type] = (on_approved, on_rejected)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def request_dict(r: ApprovalRequest) -> dict:
    return {
        "id": r.id,
        "request_no": r.request_no,
        "flow_type": r.flow_type,
        "flow_name": (cfg("approval.flows")["flows"].get(r.flow_type) or {}).get("name", r.flow_type),
        "object_type": r.object_type,
        "object_id": r.object_id,
        "title": r.title,
        "summary": r.summary,
        "stages": r.stages,
        "current_stage": r.current_stage,
        "status": r.status,
        "applicant_id": r.applicant_id,
        "applicant_name": r.applicant_name,
        "context": r.context,
        "result": r.result,
        "created_at": _iso(r.created_at),
        "updated_at": _iso(r.updated_at),
        "finished_at": _iso(r.finished_at),
    }


def _stage_list(flow: dict, context: dict) -> list[dict]:
    stages = [dict(s) for s in flow["stages"]]
    for c in flow.get("conditional_stages") or []:
        hit = (
            (c["when"] == "cold_start" and context.get("cold_start"))
            or (c["when"] == "large_amount" and abs(float(context.get("amount") or 0)) >= float(c.get("amount") or 0))
            or (c["when"] == "fraud_suspect" and context.get("fraud_suspect"))
            or (c["when"] == "high_risk" and context.get("risk_level") in ("高", "极高"))
        )
        if hit:
            stage = {k: c[k] for k in ("key", "name", "role", "permission", "min_level") if k in c}
            stage.update({"mode": "any", "deadline_hours": c.get("deadline_hours", 48), "conditional": c["when"]})
            final_idx = len(stages) - 1 if len(stages) > 1 else len(stages)
            stages.insert(final_idx, stage)
    for st in stages:
        st.update({"status": "waiting", "decisions": [], "assignee_id": None, "started_at": None, "deadline": None, "required": []})
    return stages


def eligible(user: dict, stage: dict) -> bool:
    if stage.get("assignee_id"):
        return user["id"] == stage["assignee_id"]
    if user["role"] != stage["role"] and not (stage["role"] != "admin" and user["role"] == "admin" and stage.get("permission") in (user.get("permissions") or [])):
        return False
    if stage.get("permission") and stage["permission"] not in (user.get("permissions") or []):
        return False
    return int(user.get("approval_level") or 1) >= int(stage.get("min_level") or 1)


def _candidates(s, stage: dict, exclude: set[int]) -> list[User]:
    from .iam import active_grants, effective_permissions

    out = []
    for u in s.scalars(select(User).where(User.enabled.is_(True))).all():
        if u.id in exclude:
            continue
        perms, _ = effective_permissions(u.role, u.positions, active_grants(u.id, s))
        info = {"id": u.id, "role": u.role, "permissions": perms, "approval_level": u.approval_level}
        if eligible(info, {**stage, "assignee_id": None}):
            out.append(u)
    return out


def _activate(s, req: ApprovalRequest, idx: int) -> None:
    stages = [dict(x) for x in req.stages]
    st = stages[idx]
    st["status"] = "pending"
    st["started_at"] = _iso(utcnow())
    st["deadline"] = _iso(utcnow() + timedelta(hours=float(st.get("deadline_hours") or 48)))
    exclude = {req.applicant_id} if req.applicant_id else set()
    cands = _candidates(s, st, exclude)
    if st.get("mode") == "all":
        st["required"] = [u.id for u in cands]
    st["candidate_names"] = [u.display_name for u in cands][:8]
    req.stages = stages
    req.current_stage = idx
    targets = [st["assignee_id"]] if st.get("assignee_id") else [u.id for u in cands]
    for uid in targets:
        s.add(Notification(user_id=uid, kind="approval", title=f"待审批：{req.title}", body=f"{st['name']} · {req.request_no}", link=f"/inbox?approval={req.id}", level="warning"))
    if not cands and not st.get("assignee_id"):
        s.add(Notification(role="admin", kind="system", title=f"审批节点无可用审批人：{req.title}", body=f"节点「{st['name']}」需要{st['role']}角色、权限 {st.get('permission')}、{st.get('min_level')} 级及以上", link=f"/inbox?approval={req.id}", level="error"))


def _next_no(s) -> str:
    prefix = cfg("params.dicts")["numbering"].get("approval", "SP")
    n = (s.scalar(select(func.count(ApprovalRequest.id))) or 0) + 1
    return f"{prefix}{utcnow():%Y%m%d}{n:04d}"


def start(flow_type: str, *, object_type: str, object_id: Any, title: str, summary: dict, applicant: dict | None, context: dict | None = None) -> dict:
    flows = cfg("approval.flows")["flows"]
    flow = flows.get(flow_type)
    if flow is None:
        raise ApprovalError(f"未配置审批流：{flow_type}")
    context = context or {}
    with session_scope() as s:
        open_req = s.scalars(select(ApprovalRequest).where(ApprovalRequest.flow_type == flow_type, ApprovalRequest.object_type == object_type, ApprovalRequest.object_id == str(object_id), ApprovalRequest.status == "pending")).first()
        if open_req is not None:
            raise ApprovalError(f"该对象已有审批中的申请（{open_req.request_no}）")
        req = ApprovalRequest(request_no=_next_no(s), flow_type=flow_type, object_type=object_type, object_id=str(object_id), title=title, summary=summary, stages=_stage_list(flow, context), status="pending", applicant_id=applicant["id"] if applicant else None, applicant_name=applicant["display_name"] if applicant else "系统", context=context)
        s.add(req)
        s.flush()
        _activate(s, req, 0)
        out = request_dict(req)
    return out


def act(request_id: int, user: dict, action: str, comment: str = "", transfer_to: int | None = None) -> dict:
    callback: tuple[str, dict] | None = None
    with session_scope() as s:
        req = s.get(ApprovalRequest, request_id)
        if req is None:
            raise ApprovalError("审批单不存在")
        if req.status != "pending":
            raise ApprovalError("审批单已结束")
        if action == "withdraw":
            if user["id"] != req.applicant_id:
                raise ApprovalError("只有申请人可以撤回")
            req.status = "withdrawn"
            req.finished_at = utcnow()
            req.result = {"action": "withdraw", "by": user["display_name"], "comment": comment}
            callback = ("rejected", request_dict(req))
        else:
            stages = [dict(x) for x in req.stages]
            idx = req.current_stage
            st = stages[idx]
            if not eligible(user, st):
                raise ApprovalError(f"你不是节点「{st['name']}」的审批人（需要{ {'designer': '产品设计', 'service': '产品服务', 'admin': '管理'}.get(st['role'], st['role']) }角色、{st.get('min_level', 1)} 级及以上并具备相应权限）")
            if user["id"] == req.applicant_id:
                raise ApprovalError("申请人不能审批自己的申请")
            if any(d["user_id"] == user["id"] for d in st["decisions"]):
                raise ApprovalError("你已在该节点表态")
            if action in ("reject",) and not comment.strip():
                raise ApprovalError("驳回需要填写意见")
            decision = {"user_id": user["id"], "name": user["display_name"], "action": action, "comment": comment, "at": _iso(utcnow())}
            if action == "transfer":
                if not transfer_to:
                    raise ApprovalError("请选择转办对象")
                target = s.get(User, transfer_to)
                if target is None or not target.enabled:
                    raise ApprovalError("转办对象不可用")
                from .iam import active_grants, effective_permissions

                perms, _ = effective_permissions(target.role, target.positions, active_grants(target.id, s))
                if not eligible({"id": target.id, "role": target.role, "permissions": perms, "approval_level": target.approval_level}, {**st, "assignee_id": None}):
                    raise ApprovalError(f"{target.display_name} 不具备该节点的审批权限")
                decision["to"] = target.display_name
                st["decisions"] = st["decisions"] + [decision]
                st["assignee_id"] = target.id
                stages[idx] = st
                req.stages = stages
                s.add(Notification(user_id=target.id, kind="approval", title=f"转办待审批：{req.title}", body=f"{user['display_name']} 转办：{comment}", link=f"/inbox?approval={req.id}", level="warning"))
                return request_dict(req)
            if action not in ("approve", "reject"):
                raise ApprovalError("无效的审批动作")
            st["decisions"] = st["decisions"] + [decision]
            if action == "reject":
                st["status"] = "rejected"
                stages[idx] = st
                flow = cfg("approval.flows")["flows"].get(req.flow_type, {})
                if flow.get("reject_to") == "previous" and idx > 0:
                    prev = dict(stages[idx - 1])
                    prev["status"] = "waiting"
                    prev["decisions"] = prev["decisions"] + [{"user_id": None, "name": "系统", "action": "returned", "comment": f"被「{st['name']}」退回：{comment}", "at": _iso(utcnow())}]
                    stages[idx - 1] = prev
                    st = dict(stages[idx])
                    st["status"] = "waiting"
                    stages[idx] = st
                    req.stages = stages
                    _activate(s, req, idx - 1)
                    return request_dict(req)
                req.stages = stages
                req.status = "rejected"
                req.finished_at = utcnow()
                req.result = {"action": "reject", "stage": st["name"], "by": user["display_name"], "comment": comment}
                if req.applicant_id:
                    s.add(Notification(user_id=req.applicant_id, kind="approval", title=f"审批驳回：{req.title}", body=f"{st['name']}：{comment}", link=f"/inbox?approval={req.id}&tab=mine", level="warning"))
                callback = ("rejected", request_dict(req))
            else:
                done = st.get("mode") != "all" or {d["user_id"] for d in st["decisions"] if d["action"] == "approve"} >= set(st.get("required") or [user["id"]])
                if done:
                    st["status"] = "approved"
                    stages[idx] = st
                    req.stages = stages
                    if idx + 1 < len(stages):
                        _activate(s, req, idx + 1)
                    else:
                        req.status = "approved"
                        req.finished_at = utcnow()
                        req.result = {"action": "approve", "by": user["display_name"], "comment": comment}
                        if req.applicant_id:
                            s.add(Notification(user_id=req.applicant_id, kind="approval", title=f"审批通过：{req.title}", body=f"全部 {len(stages)} 个节点已通过", link=f"/inbox?approval={req.id}&tab=mine"))
                        callback = ("approved", request_dict(req))
                else:
                    stages[idx] = st
                    req.stages = stages
        out = request_dict(req)
    if callback:
        kind, payload = callback
        handlers = _HANDLERS.get(payload["flow_type"], (None, None))
        fn = handlers[0] if kind == "approved" else handlers[1]
        if fn:
            fn(payload, user)
        with session_scope() as s:
            out = request_dict(s.get(ApprovalRequest, request_id))
    return out


def get(request_id: int) -> dict:
    with session_scope() as s:
        r = s.get(ApprovalRequest, request_id)
        if r is None:
            raise ApprovalError("审批单不存在")
        return request_dict(r)


def for_object(object_type: str, object_id: Any) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ApprovalRequest).where(ApprovalRequest.object_type == object_type, ApprovalRequest.object_id == str(object_id)).order_by(ApprovalRequest.id.desc())).all()
        return [request_dict(r) for r in rows]


def pending_for(user: dict) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ApprovalRequest).where(ApprovalRequest.status == "pending").order_by(ApprovalRequest.id.desc())).all()
        out = []
        for r in rows:
            st = r.stages[r.current_stage]
            if r.applicant_id == user["id"]:
                continue
            if any(d.get("user_id") == user["id"] for d in st.get("decisions", [])):
                continue
            if eligible(user, st):
                out.append(request_dict(r))
        return out


def mine(user: dict, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ApprovalRequest).where(ApprovalRequest.applicant_id == user["id"]).order_by(ApprovalRequest.id.desc()).limit(limit)).all()
        return [request_dict(r) for r in rows]


def handled_by(user: dict, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ApprovalRequest).order_by(ApprovalRequest.id.desc()).limit(500)).all()
        return [request_dict(r) for r in rows if any(d.get("user_id") == user["id"] for st in r.stages for d in st.get("decisions", []))][:limit]


def check_timeouts() -> int:
    """超时节点按流程策略提醒或升级到申请人所在机构负责人。"""
    from .iam import leader_of

    count = 0
    now = utcnow()
    with session_scope() as s:
        for r in s.scalars(select(ApprovalRequest).where(ApprovalRequest.status == "pending")).all():
            st = dict(r.stages[r.current_stage])
            if not st.get("deadline") or st.get("escalated"):
                continue
            if datetime.fromisoformat(st["deadline"].rstrip("Z")) > now:
                continue
            flow = cfg("approval.flows")["flows"].get(r.flow_type, {})
            leader = leader_of(s, r.applicant_id) if r.applicant_id and flow.get("timeout") == "escalate" else None
            s.add(Notification(user_id=leader, role=None if leader else st["role"], kind="approval", title=f"审批超时{'升级' if leader else '提醒'}：{r.title}", body=f"节点「{st['name']}」已超过 {st.get('deadline_hours')} 小时未处理", link=f"/inbox?approval={r.id}", level="error"))
            st["escalated"] = True
            stages = [dict(x) for x in r.stages]
            stages[r.current_stage] = st
            r.stages = stages
            count += 1
    return count
