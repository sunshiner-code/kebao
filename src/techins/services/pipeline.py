"""产品设计流水线编排（任务图）。需求描述、赛道和企业定制都走原有智能体阶段。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..harness.executor import executor
from ..models import Artifact, DesignProject, Notification, Run

STAGE_ORDER = ["risk", "preference", "clause", "pricing", "package", "approval"]
STAGE_LABELS = {"risk": "风险评估", "preference": "风险偏好与承受能力", "clause": "条款设计", "pricing": "精算定价", "package": "产品包", "approval": "审批发布"}
STAGE_AGENT = {"risk": "AG-RA", "clause": "AG-CD", "pricing": "AG-AP"}
PROJECT_STATUS = {"risk": "risk_assessment", "preference": "clause_design", "clause": "clause_design", "pricing": "pricing", "package": "packaging", "approval": "approval"}


class PipelineError(ValueError):
    pass


def _empty_pipeline(project_type: str) -> dict:
    nodes = {s: {"status": "pending", "label": STAGE_LABELS[s]} for s in STAGE_ORDER}
    if project_type == "track":
        nodes["preference"] = {"status": "skipped", "label": STAGE_LABELS["preference"], "note": "赛道通用产品按赛道典型企业画像"}
    if project_type == "need":
        nodes["preference"] = {"status": "skipped", "label": STAGE_LABELS["preference"], "note": "需求描述项目不填写风险偏好问卷"}
    return {"nodes": nodes, "revisions": [], "history": []}


def _log(pipeline: dict, text: str) -> None:
    pipeline.setdefault("history", []).append({"ts": utcnow().isoformat() + "Z", "text": text})


def create_project(*, name: str, project_type: str, track: str | None, enterprise_ids: list[int], target_lines: list[str], config: dict, owner_id: int | None) -> int:
    if project_type == "need":
        background = (config.get("background") or "").strip()
        customer_need = (config.get("customer_need") or "").strip()
        if not background and not customer_need:
            raise PipelineError("需要描述产品开发背景或客户希望解决的问题")
        if not name:
            name = (customer_need or background)[:40]
    elif project_type == "enterprise" and len(enterprise_ids) != 1:
        raise PipelineError("企业定制项目需要且只能选择一家企业")
    elif project_type == "track" and not enterprise_ids:
        raise PipelineError("赛道通用项目需要选择样本企业")
    elif project_type not in ("need", "enterprise", "track"):
        raise PipelineError("未知的项目类型")
    with session_scope() as s:
        project = DesignProject(name=name, project_type=project_type, track=track, enterprise_ids=enterprise_ids or [], target_lines=target_lines, config=config, owner_id=owner_id, pipeline=_empty_pipeline(project_type), status="risk_assessment")
        s.add(project)
        s.flush()
        pid = project.id
    start_stage(pid, "risk", owner_id)
    return pid


def _upstream(project: DesignProject, stage: str) -> dict:
    return project.pipeline["nodes"].get(stage, {})


def _ensure_upstream_ready(project: DesignProject, stage: str, nodes: dict) -> None:
    """前序未完成时禁止启动后续智能体阶段。"""
    if stage == "risk":
        return
    if stage == "clause":
        if nodes.get("risk", {}).get("status") != "done":
            raise PipelineError("风险评估报告尚未确认，不能开始条款设计")
        if project.project_type == "enterprise" and nodes.get("preference", {}).get("status") != "done":
            raise PipelineError("企业定制项目需先完成风险偏好与承受能力评估")
        return
    if stage == "pricing":
        if nodes.get("clause", {}).get("status") != "done":
            raise PipelineError("条款方案尚未冻结，不能开始定价")
        return
    raise PipelineError(f"阶段 {stage} 不由智能体执行")


def _cancel_run_quiet(run_id: int | None) -> None:
    if not run_id:
        return
    try:
        executor.control(int(run_id), "cancel")
    except Exception:
        pass


def _invalidate_downstream(nodes: dict, stage: str, *, reason: str) -> list[int]:
    """重跑某阶段时，清空后续阶段并取消其未完成运行，等待本阶段完成后再按流程推进。"""
    if stage not in STAGE_ORDER:
        return []
    cancel_ids: list[int] = []
    idx = STAGE_ORDER.index(stage)
    for later in STAGE_ORDER[idx + 1 :]:
        node = dict(nodes.get(later) or {})
        if node.get("status") == "skipped":
            continue
        rid = node.get("run_id")
        if rid and node.get("status") in ("running", "waiting_confirm", "waiting_human", "queued", "planning", "paused"):
            cancel_ids.append(int(rid))
        keep_label = node.get("label") or STAGE_LABELS.get(later, later)
        nodes[later] = {
            "status": "pending",
            "label": keep_label,
            "note": reason,
            "upstream_version": None,
            "run_id": None,
            "draft_key": None,
            "draft_version": None,
            "progress": None,
            "artifact_key": node.get("artifact_key"),
            "version": node.get("version"),
            "result": node.get("result") if later == "preference" else None,
        }
        if later == "preference" and node.get("result"):
            # 偏好问卷结果可复用，但仍需等风险确认后再进入条款
            nodes[later]["status"] = "done"
            nodes[later]["result"] = node["result"]
            nodes[later]["note"] = None
    return cancel_ids


def start_stage(project_id: int, stage: str, user_id: int | None, extra_inputs: dict | None = None) -> int:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            raise PipelineError("设计项目不存在")
    cancel_ids: list[int] = []
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        cfg = project.config or {}
        mode = cfg.get("mode", "auto")
        _ensure_upstream_ready(project, stage, nodes)
        prev = nodes.get(stage, {})
        rerunning = prev.get("status") in ("done", "stale", "failed", "cancelled", "waiting_confirm") or bool(prev.get("run_id") or prev.get("artifact_key") or prev.get("draft_key"))
        if rerunning:
            cancel_ids = _invalidate_downstream(nodes, stage, reason=f"上游「{STAGE_LABELS[stage]}」已重跑，等待其完成后按流程重新执行")
            if prev.get("run_id") and prev.get("status") in ("running", "waiting_confirm", "waiting_human", "queued", "planning", "paused"):
                cancel_ids.append(int(prev["run_id"]))
        if stage == "risk":
            inputs = {
                "mode": project.project_type,
                "enterprise_ids": project.enterprise_ids,
                "track": project.track,
                "target_lines": project.target_lines,
                "sources": cfg.get("sources"),
                "years": cfg.get("years", 3),
                "material_ids": cfg.get("material_ids", []),
                "focus_types": cfg.get("focus_types", []),
                "lifecycle": cfg.get("lifecycle", True),
                "background": cfg.get("background") or "",
                "customer_need": cfg.get("customer_need") or "",
                "subject_name": project.name,
                "artifact_key": nodes["risk"].get("artifact_key"),
            }
        elif stage == "clause":
            risk = nodes["risk"]
            inputs = {
                "risk_report": {"artifact_key": risk["artifact_key"], "version": risk["version"]},
                "design_type": project.project_type,
                "preference": nodes["preference"].get("result"),
                "target_lines": project.target_lines,
                "required_lines": cfg.get("required_lines", []),
                "excluded_lines": cfg.get("excluded_lines", []),
                "budget": cfg.get("budget"),
                "artifact_key": nodes["clause"].get("artifact_key"),
            }
        elif stage == "pricing":
            clause = nodes["clause"]
            inputs = {
                "clause_plan": {"artifact_key": clause.get("artifact_key"), "version": clause.get("version")},
                "artifact_key": nodes["pricing"].get("artifact_key"),
                "_config": {"planner": "auto", "plan_confirm": False, "allow_cloud": True, "experience_injection": False, "tool_blacklist": [], "pass_score": 60, "max_reflection_rounds": 0},
                "_config_label": "pricing",
            }
        else:
            raise PipelineError(f"阶段 {stage} 不由智能体执行")
        inputs.update(extra_inputs or {})
        name = project.name
        pricing_agent = stage == "pricing"
        if rerunning:
            pipeline["nodes"] = nodes
            _log(pipeline, f"重跑{STAGE_LABELS[stage]}：后续阶段已重置，待本阶段完成后再推进")
            project.pipeline = pipeline
    for rid in cancel_ids:
        _cancel_run_quiet(rid)
    if pricing_agent:
        run_id = executor.create_run(agent_id="AG-AP", purpose="need_pricing", inputs=inputs, business_ref={"project_id": project_id, "stage": stage}, mode=mode, user_id=user_id, title=f"{STAGE_LABELS[stage]}：{name}")
        with session_scope() as s:
            project = s.get(DesignProject, project_id)
            pipeline = dict(project.pipeline)
            nodes = dict(pipeline["nodes"])
            nodes[stage] = {**nodes.get(stage, {}), "status": "running", "run_id": run_id, "started_at": utcnow().isoformat() + "Z", "note": None}
            pipeline["nodes"] = nodes
            _log(pipeline, f"启动{STAGE_LABELS[stage]}（运行 {run_id}）")
            project.pipeline = pipeline
            project.status = PROJECT_STATUS[stage]
        return run_id
    run_id = executor.create_run(agent_id=STAGE_AGENT[stage], inputs=inputs, business_ref={"project_id": project_id, "stage": stage}, mode=mode, user_id=user_id, title=f"{STAGE_LABELS[stage]}：{name}")
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        nodes[stage] = {**nodes.get(stage, {}), "status": "running", "run_id": run_id, "started_at": utcnow().isoformat() + "Z", "note": None}
        pipeline["nodes"] = nodes
        _log(pipeline, f"启动{STAGE_LABELS[stage]}（运行 {run_id}）")
        project.pipeline = pipeline
        project.status = PROJECT_STATUS[stage]
    return run_id


def on_stage_confirmed(project_id: int | None, stage: str, artifact_key: str, version: int, run_id: int, user_id: int | None) -> None:
    if not project_id:
        return
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            return
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        with_score = s.get(Run, run_id)
        nodes[stage] = {
            **nodes.get(stage, {}),
            "status": "done",
            "artifact_key": artifact_key,
            "version": version,
            "run_id": run_id,
            "eval_score": with_score.eval_score if with_score else None,
            "confirmed_at": utcnow().isoformat() + "Z",
            "note": None,
        }
        idx = STAGE_ORDER.index(stage)
        for later in STAGE_ORDER[idx + 1 :]:
            node = dict(nodes.get(later) or {})
            if node.get("status") == "skipped":
                continue
            # 下游已有产出或仍挂着旧运行时，一律标记需基于新上游重跑
            if node.get("status") in ("done", "ready", "waiting_confirm", "waiting_approval", "waiting_submit", "stale") or node.get("artifact_key") or node.get("draft_key") or node.get("product_id"):
                prev_up = node.get("upstream_version")
                if prev_up not in (None, version) or node.get("status") in ("done", "ready", "waiting_confirm", "waiting_approval", "waiting_submit", "stale"):
                    node["status"] = "stale"
                    node["note"] = f"上游{STAGE_LABELS[stage]}已更新为 V{version}，请基于新版本重跑"
                    node["upstream_version"] = version
                    nodes[later] = node
            elif node.get("status") == "pending":
                node["upstream_version"] = version
                node["note"] = None
                nodes[later] = node
        pipeline["nodes"] = nodes
        _log(pipeline, f"{STAGE_LABELS[stage]} V{version} 已确认冻结")
        project.pipeline = pipeline
        auto = (project.config or {}).get("auto_advance", True)
        project_type = project.project_type
        owner = project.owner_id
        s.add(Notification(user_id=owner, kind="pipeline", title=f"{project.name}：{STAGE_LABELS[stage]}已确认", body=f"版本 V{version}", link=f"/design/{project_id}"))
    nxt = {"risk": "clause", "clause": "pricing"}.get(stage)
    if stage == "risk" and project_type == "enterprise":
        with session_scope() as s:
            project = s.get(DesignProject, project_id)
            pipeline = dict(project.pipeline)
            nodes = dict(pipeline["nodes"])
            if nodes["preference"].get("status") != "done":
                nodes["preference"] = {**nodes["preference"], "status": "waiting_input", "note": None}
                pipeline["nodes"] = nodes
                project.pipeline = pipeline
                return
    if stage == "pricing":
        with session_scope() as s:
            project = s.get(DesignProject, project_id)
            pipeline = dict(project.pipeline)
            nodes = dict(pipeline["nodes"])
            nodes["package"] = {**nodes["package"], "status": "ready", "note": None, "upstream_version": version}
            pipeline["nodes"] = nodes
            project.pipeline = pipeline
            project.status = "packaging"
        return
    if nxt and auto:
        with session_scope() as s:
            project = s.get(DesignProject, project_id)
            nxt_status = ((project.pipeline or {}).get("nodes") or {}).get(nxt, {}).get("status") if project else None
        # 仅当下游处于可启动的等待态时自动推进，避免打断仍在跑的旧下游
        if nxt_status not in (None, "pending", "stale", "failed", "cancelled"):
            return
        try:
            start_stage(project_id, nxt, user_id)
        except PipelineError as exc:
            with session_scope() as s:
                project = s.get(DesignProject, project_id)
                if project is None:
                    return
                pipeline = dict(project.pipeline)
                nodes = dict(pipeline["nodes"])
                nodes[nxt] = {**nodes.get(nxt, {}), "status": "pending", "note": str(exc)}
                pipeline["nodes"] = nodes
                _log(pipeline, f"自动进入{STAGE_LABELS.get(nxt, nxt)}失败：{exc}")
                project.pipeline = pipeline


def set_preference(project_id: int, result: dict, user_id: int | None) -> None:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            raise PipelineError("设计项目不存在")
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        cancel_ids = _invalidate_downstream(nodes, "preference", reason="风险偏好已更新，等待完成后按流程重新执行条款与后续阶段")
        nodes["preference"] = {**nodes["preference"], "status": "done", "result": result, "note": None}
        pipeline["nodes"] = nodes
        _log(pipeline, f"风险偏好评估完成：{result.get('preference_label')}，承受能力 {result.get('capacity_level')}")
        project.pipeline = pipeline
        ready = nodes["risk"].get("status") == "done" and (project.config or {}).get("auto_advance", True)
    for rid in cancel_ids:
        _cancel_run_quiet(rid)
    if ready:
        start_stage(project_id, "clause", user_id)


def update_node(project_id: int, stage: str, **fields: Any) -> None:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            return
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        nodes[stage] = {**nodes.get(stage, {}), **fields}
        pipeline["nodes"] = nodes
        project.pipeline = pipeline


def add_revision(project_id: int, revision: dict) -> None:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            return
        pipeline = dict(project.pipeline)
        revisions = list(pipeline.get("revisions", []))
        existing = next((r for r in revisions if r["request_no"] == revision["request_no"]), None)
        if existing:
            existing.update(revision)
        else:
            revisions.append(revision)
        pipeline["revisions"] = revisions
        _log(pipeline, f"修订请求 {revision['request_no']}：{revision.get('status_label', revision.get('status'))}")
        project.pipeline = pipeline


def refresh_project(project: DesignProject, s) -> dict:
    """根据运行状态刷新流水线节点（运行中、待确认、失败）。"""
    pipeline = dict(project.pipeline or {})
    nodes = dict(pipeline.get("nodes", {}))
    changed = False
    for stage in ("risk", "clause", "pricing"):
        node = nodes.get(stage, {})
        rid = node.get("run_id")
        if not rid or node.get("status") in ("done", "stale"):
            continue
        run = s.get(Run, rid)
        if run is None:
            continue
        mapped = {"waiting_human": "waiting_confirm", "failed": "failed", "cancelled": "cancelled", "completed": "done"}.get(run.status, "running")
        if mapped == "done" and node.get("status") != "done":
            mapped = "waiting_confirm"
        if node.get("status") != mapped or node.get("progress") != run.progress:
            node = {**node, "status": mapped, "progress": run.progress, "eval_score": run.eval_score, "degrade_level": run.degrade_level, "start_mode": run.start_mode, "waiting": run.waiting_for}
            out = run.output or {}
            if out.get("artifact_key"):
                node["draft_key"] = out["artifact_key"]
                node["draft_version"] = out.get("version")
            nodes[stage] = node
            changed = True
    if changed:
        pipeline["nodes"] = nodes
        project.pipeline = pipeline
    return pipeline


def lineage(artifact_key: str, version: int) -> list[dict]:
    chain = []
    with session_scope() as s:
        current = s.scalars(select(Artifact).where(Artifact.artifact_key == artifact_key, Artifact.version == version)).first()
        while current is not None:
            chain.append({"kind": current.kind, "artifact_key": current.artifact_key, "version": current.version, "title": current.title, "status": current.status})
            if not current.upstream:
                break
            up = current.upstream[0]
            current = s.scalars(select(Artifact).where(Artifact.artifact_key == up["artifact_key"], Artifact.version == up["version"])).first()
    return chain
