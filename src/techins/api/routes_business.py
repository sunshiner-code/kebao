"""业务资源接口：设计项目与流水线、产品、客户与材料、投保与核保、保单与监测、预警、事故响应、理赔。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from ..agents.clause_design.library import PREFERENCE_QUESTIONS, capacity_from_financials, parameter_ranges, preference_from_answers
from ..agents.common import enterprise_dict
from ..config import settings
from ..db import session_scope, utcnow
from ..harness.executor import executor
from ..knowledge.catalog import LINES, PLAYBOOKS, STAGES
from ..models import Alert, Application, Artifact, ClaimCase, DesignProject, Enterprise, Incident, Material, MemoryEntry, MonitorPlan, Policy, Product, Run, TelemetryPoint
from ..extract import MAX_UPLOAD_BYTES, extract_text
from ..privacy.classifier import detect_level
from ..services import business, customers, monitoring, pipeline, products
from ..services.customers import CustomerError, EnterpriseConflict
from ..services.pipeline import PipelineError
from .deps import ApiError, audit, current_user, iso, require, require_perm
from .routes_core import run_dict

router = APIRouter(prefix="/api")

LEVEL_LABELS = {"red": "红色", "orange": "橙色", "yellow": "黄色", "blue": "蓝色"}
APP_STATUS = {"draft": "草稿", "submitted": "已提交", "underwriting": "核保中", "pending_decision": "待核保决定", "accepted": "已承保", "rejected": "已拒保", "returned": "已退回"}
CLAIM_STATUS = {"registered": "已登记待材料", "reported": "已报案", "assessing": "定损中", "pending_approval": "待审批", "approved": "已审批待支付", "closed": "已结案"}
ALERT_STATUS = {"new": "待处置", "processing": "处置中", "closed": "已关闭", "false_positive": "误报", "escalated": "已升级为事故"}
INCIDENT_STATUS = {"started": "已启动", "responding": "响应中", "awaiting_claim": "待确认预报案", "claimed": "已移交理赔", "closed": "已结束"}


def _pipe_err(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (PipelineError, ValueError) as exc:
        raise ApiError(409, "状态冲突", str(exc)) from exc


# ================================================================ 设计项目
class ProjectBody(BaseModel):
    name: str = ""
    project_type: str = "need"
    track: str | None = None
    enterprise_ids: list[int] = []
    target_lines: list[str] = []
    mode: str = "auto"
    auto_advance: bool = True
    budget: float | None = None
    required_lines: list[str] = []
    excluded_lines: list[str] = []
    material_ids: list[int] = []
    years: int = 3
    background: str = ""
    customer_need: str = ""


def project_dict(p: DesignProject, s) -> dict:
    pipe = pipeline.refresh_project(p, s)
    ents = s.scalars(select(Enterprise).where(Enterprise.id.in_(p.enterprise_ids or []))).all()
    return {"id": p.id, "name": p.name, "project_type": p.project_type, "track": p.track, "enterprise_ids": p.enterprise_ids, "enterprises": [{"id": e.id, "name": e.name} for e in ents], "target_lines": p.target_lines, "status": p.status, "config": p.config, "pipeline": pipe, "created_at": iso(p.created_at)}


@router.get("/design-projects")
def list_projects(user: dict = Depends(require("designer"))) -> list[dict]:
    with session_scope() as s:
        return [project_dict(p, s) for p in s.scalars(select(DesignProject).order_by(DesignProject.id.desc())).all()]


@router.post("/design-projects")
def create_project(body: ProjectBody, user: dict = Depends(require("designer"))) -> dict:
    cfg = {"mode": body.mode, "auto_advance": body.auto_advance, "budget": body.budget, "required_lines": body.required_lines, "excluded_lines": body.excluded_lines, "material_ids": body.material_ids, "years": body.years, "background": body.background, "customer_need": body.customer_need}
    pid = _pipe_err(pipeline.create_project, name=body.name, project_type=body.project_type, track=body.track, enterprise_ids=body.enterprise_ids, target_lines=body.target_lines, config=cfg, owner_id=user["id"])
    audit(user, "operation", "创建设计项目", "design_project", pid)
    return {"id": pid}


@router.get("/design-projects/{pid}")
def get_project(pid: int, user: dict = Depends(require("designer"))) -> dict:
    with session_scope() as s:
        p = s.get(DesignProject, pid)
        if p is None:
            raise ApiError(404, "状态冲突", "设计项目不存在")
        d = project_dict(p, s)
        d["runs"] = [run_dict(r) for r in s.scalars(select(Run).where(Run.parent_run_id.is_(None))).all() if (r.business_ref or {}).get("project_id") == pid]
        d["artifacts"] = [{"artifact_key": a.artifact_key, "kind": a.kind, "version": a.version, "status": a.status, "title": a.title, "created_at": iso(a.created_at)} for a in s.scalars(select(Artifact).where(Artifact.project_id == pid).order_by(Artifact.id)).all()]
        product_id = p.pipeline["nodes"].get("package", {}).get("product_id")
        d["product"] = None
        if product_id:
            pr = s.get(Product, product_id)
            d["product"] = {"id": pr.id, "name": pr.name, "status": pr.status, "code": pr.code}
    return d


@router.post("/design-projects/{pid}/stages/{stage}/start")
def start_stage(pid: int, stage: str, user: dict = Depends(require("designer"))) -> dict:
    rid = _pipe_err(pipeline.start_stage, pid, stage, user["id"])
    audit(user, "operation", f"启动阶段 {stage}", "design_project", pid)
    return {"run_id": rid}


class ScopeBody(BaseModel):
    risk_ids: list[str] = []
    line_name: str
    category: str
    direct: bool = False
    period: str | None = None
    limits: dict = {}
    billing_base: str | None = None
    billing_amount: int | None = None
    confirm_rules: bool = False


class RiskEditBody(BaseModel):
    changes: dict


class RateChoiceBody(BaseModel):
    method: str


class RuleChangeBody(BaseModel):
    billing_base: str
    billing_amount: int


@router.post("/design-projects/{pid}/scope")
def save_scope(pid: int, body: ScopeBody, user: dict = Depends(require("designer"))) -> dict:
    from ..productdev.flow import save_scope as write_scope

    out = _pipe_err(
        write_scope,
        pid,
        risk_ids=body.risk_ids,
        line_name=body.line_name,
        category=body.category,
        direct=body.direct,
        period=body.period,
        limits=body.limits,
        billing_base=body.billing_base,
        billing_amount=body.billing_amount,
        confirm_rules=body.confirm_rules,
    )
    audit(user, "operation", "圈定险种和风险", "design_project", pid)
    return {"product_rules": out["product_rules"], "compliance_passed": None if out["clause"] is None else out["clause"]["compliance_passed"], "artifact_key": out.get("artifact_key")}


@router.patch("/design-projects/{pid}/risks/{risk_id}")
def edit_project_risk(pid: int, risk_id: str, body: RiskEditBody, user: dict = Depends(require("designer"))) -> dict:
    from ..productdev.flow import update_risk

    out = _pipe_err(update_risk, pid, risk_id, body.changes)
    return {"artifact_key": out["artifact_key"], "version": out["version"], "risk": next(risk for risk in out["report"]["risks"] if risk["id"] == risk_id)}


@router.post("/design-projects/{pid}/rules/billing")
def change_billing(pid: int, body: RuleChangeBody, user: dict = Depends(require("designer"))) -> dict:
    from ..productdev.flow import reprice_after_rule_change

    out = _pipe_err(reprice_after_rule_change, pid, billing_base=body.billing_base, billing_amount=body.billing_amount)
    return {"artifact_key": out["artifact_key"], "billing_amount": body.billing_amount}


@router.post("/design-projects/{pid}/rates/select")
def select_rate(pid: int, body: RateChoiceBody, user: dict = Depends(require("designer", "actuary"))) -> dict:
    from ..productdev.flow import choose_rate

    out = _pipe_err(choose_rate, pid, body.method)
    audit(user, "operation", "选定费率", "design_project", pid)
    component = out["components"][0]
    return {"gross_rate": component["rate"]["gross_rate"], "deductible": component["params"]["deductible"], "limit": component["params"]["limit"], "band": component["rate"]["band"], "methods": component["methods"]}


class RateExampleBody(BaseModel):
    method: str
    score: str | float | None = None
    coefficients: dict[str, str | float] = {}


@router.post("/design-projects/{pid}/rates/example")
def example_rate(pid: int, body: RateExampleBody, user: dict = Depends(require("designer", "actuary"))) -> dict:
    from ..productdev.flow import example_pricing

    return _pipe_err(example_pricing, pid, body.method, score=body.score, coefficients=body.coefficients)


class ReportReviewBody(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/design-projects/{pid}/rates/review")
def review_pricing_report(pid: int, body: ReportReviewBody, user: dict = Depends(require_perm("product.review1"))) -> dict:
    from ..productdev.flow import review_report

    out = _pipe_err(review_report, pid, approve=body.approve, comment=body.comment, reviewer=user)
    audit(user, "operation", "审定精算报告", "design_project", pid, {"approve": body.approve})
    return {"artifact_key": out["artifact_key"], "version": out["version"], "actuarial_report": out["actuarial_report"]}


@router.get("/design-projects/{pid}/pricing-report.docx")
def pricing_report_docx(pid: int, version: int | None = None, user: dict = Depends(require("designer", "actuary"))):
    from ..productdev.report_docx import write_report_docx

    with session_scope() as s:
        p = s.get(DesignProject, pid)
        if p is None:
            raise ApiError(404, "状态冲突", "设计项目不存在")
        node = (p.pipeline or {}).get("nodes", {}).get("pricing") or {}
        name = p.name
        if not node.get("artifact_key"):
            raise ApiError(409, "状态冲突", "尚未生成精算报告")
        q = select(Artifact).where(Artifact.artifact_key == node["artifact_key"])
        if version is not None:
            q = q.where(Artifact.version == version)
        art = s.scalars(q.order_by(Artifact.version.desc())).first()
        if art is None:
            raise ApiError(404, "状态冲突", "报告版本不存在")
        report = (art.data or {}).get("actuarial_report") or {}
        art_version = art.version
    if not report.get("sections"):
        raise ApiError(409, "状态冲突", "报告缺少结构化内容，请重新生成定价")
    path = settings.data_dir / "reports" / f"{pid}_v{art_version}.docx"
    write_report_docx(report, path, project_name=name, version=art_version)
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{name}-保险费率精算报告-V{art_version}.docx")


class FactorBody(BaseModel):
    name: str
    risk_key: str
    condition: str
    coefficient: str
    source: str = ""
    applicability: str = ""
    evidence_class: str = "专家判断"
    version: str = "2026-01"
    time: str = "2026-01-01"


@router.post("/development-parameters/factors")
def add_factor(body: FactorBody, user: dict = Depends(require("designer", "actuary"))) -> dict:
    from ..productdev.parameters import ParameterError, save_classification_factor

    try:
        return save_classification_factor(body.model_dump())
    except ParameterError as exc:
        raise ApiError(422, "参数不可用", str(exc)) from exc


class ExperienceBody(BaseModel):
    liability: str | None = None
    billing_amount: str | None = None
    period: str | None = None
    claim_amount: str | None = None
    limit_amount: str | None = None
    risk_feature: str | None = None
    empirical_pure: str | None = None


@router.post("/development-parameters/experience")
def add_experience(body: ExperienceBody, user: dict = Depends(require("designer", "actuary"))) -> dict:
    from ..productdev import parameters

    data = parameters.load()
    data["experience"].append(body.model_dump())
    parameters.save(data)
    usable, rejected = parameters.experience_rows()
    return {"usable": len(usable), "rejected": len(rejected)}


@router.get("/preference-questions")
def preference_questions(user: dict = Depends(current_user)) -> list[dict]:
    return PREFERENCE_QUESTIONS


class PreferenceBody(BaseModel):
    answers: dict[str, int]


@router.post("/design-projects/{pid}/preference")
def set_preference(pid: int, body: PreferenceBody, user: dict = Depends(require("designer"))) -> dict:
    with session_scope() as s:
        p = s.get(DesignProject, pid)
        if p is None or p.project_type != "enterprise":
            raise ApiError(409, "状态冲突", "只有企业定制项目需要风险偏好评估")
        fin = s.get(Enterprise, p.enterprise_ids[0]).profile.get("financials", {})
    pref = preference_from_answers(body.answers)
    cap = capacity_from_financials(fin)
    result = {**pref, **cap, "answers": body.answers, "ranges": parameter_ranges(pref["preference"], cap)}
    _pipe_err(pipeline.set_preference, pid, result, user["id"])
    return result


@router.post("/design-projects/{pid}/package")
def package(pid: int, user: dict = Depends(require("designer"))) -> dict:
    product_id = _pipe_err(products.assemble_package, pid, user["id"])
    audit(user, "operation", "组装产品包", "product", product_id)
    return {"product_id": product_id}


# ================================================================ 产品
def _product_line_name(code: str, package: dict | None = None) -> str:
    """目录内险种用标准名；创新/未入库编码回退到条款文档或费率组件名称，避免产品库空白标签。"""
    if code in LINES:
        return LINES[code]["name"]
    pkg = package or {}
    for doc in pkg.get("docs") or []:
        if code in (doc.get("lines") or []) and doc.get("name"):
            return doc["name"]
    for comp in pkg.get("components") or []:
        if comp.get("line") == code and comp.get("name"):
            return comp["name"]
    return code


def _component_methods(methods) -> list:
    """列表接口只回传可读方法名，避免前端把 {name,...} 对象 join 成 [object Object]。"""
    out: list[str] = []
    labels = {
        "weighted": "创新积分制定价法",
        "maturity": "纯风险损失率法（旧方法）",
        "classification": "分类费率厘定法",
        "PM1": "广义线性模型",
        "PM2": "梯度提升树 + Shapley",
        "PM3": "信度加权",
        "PM4": "风险成本加成法",
    }
    for m in methods or []:
        if isinstance(m, dict):
            name = m.get("name") or m.get("method") or labels.get(str(m.get("key") or ""), m.get("key"))
            if name:
                out.append(str(name))
        elif isinstance(m, str):
            out.append(labels.get(m, m))
    return out


def product_dict(p: Product, full: bool = False) -> dict:
    package = p.package or {}
    d = {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "lines": p.lines,
        "line_names": [_product_line_name(l, package) for l in (p.lines or [])],
        "stages": p.stages,
        "stage_names": [STAGES.get(x, x) for x in (p.stages or [])],
        "status": p.status,
        "version": p.version,
        "start_mode": p.start_mode,
        "project_id": p.project_id,
        "approvals": p.approvals,
        "created_at": iso(p.created_at),
        "published_at": iso(p.published_at),
        "components": [
            {
                **{k: c.get(k) for k in ("name", "line", "gross_rate", "band", "limit", "deductible", "start_mode")},
                "methods": _component_methods(c.get("methods")),
            }
            for c in package.get("components", [])
        ],
    }
    if full:
        d["package"] = package
    return d


@router.get("/products")
def list_products(user: dict = Depends(current_user), status: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Product)
        if status:
            q = q.where(Product.status == status)
        rows = s.scalars(q.order_by(Product.id.desc())).all()
        counts = {pid: n for pid, n in s.execute(select(Policy.product_id, func.count(Policy.id)).group_by(Policy.product_id)).all()}
        return [product_dict(p) | {"policies": counts.get(p.id, 0)} for p in rows]


@router.get("/products/{pid}")
def get_product(pid: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        p = s.get(Product, pid)
        if p is None:
            raise ApiError(404, "状态冲突", "产品不存在")
        return product_dict(p, full=True)


class ApproveBody(BaseModel):
    action: str = "approve"
    comment: str = ""


@router.post("/products/{pid}/approve")
def approve(pid: int, body: ApproveBody, user: dict = Depends(current_user)) -> dict:
    out = _pipe_err(products.approve_product, pid, user, body.action, body.comment)
    audit(user, "operation", "产品审批", "product", pid, {"action": body.action})
    return out


# ================================================================ 客户与材料
class EnterpriseCreate(BaseModel):
    name: str
    credit_code: str
    industry: str = ""
    track: str = ""
    tech_field: str = ""
    region: str = ""
    founded_year: int | None = None
    employees: int | None = None
    listed: bool = False


@router.post("/enterprises")
def create_enterprise(body: EnterpriseCreate, user: dict = Depends(current_user)) -> dict:
    if user["role"] not in ("designer", "service"):
        raise ApiError(403, "权限不足", "只有产品设计人员和产品服务人员可以新建企业客户", "联系管理员")
    try:
        row = customers.create_enterprise(body.model_dump(), user)
    except EnterpriseConflict as exc:
        suggestion = "将使用已有客户" if exc.existing else "请更换名称"
        raise ApiError(409, "状态冲突", str(exc), suggestion, obj=exc.existing) from exc
    except CustomerError as exc:
        raise ApiError(422, "输入契约不满足", str(exc)) from exc
    audit(user, "operation", "新建企业客户", "enterprise", row["id"], {"name": row["name"], "credit_code": row["credit_code"]})
    return row


@router.get("/enterprises")
def list_enterprises(user: dict = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        ents = s.scalars(select(Enterprise).order_by(Enterprise.id)).all()
        pol = {eid: n for eid, n in s.execute(select(Policy.enterprise_id, func.count(Policy.id)).group_by(Policy.enterprise_id)).all()}
        al = {eid: n for eid, n in s.execute(select(Alert.enterprise_id, func.count(Alert.id)).where(Alert.status.in_(["new", "processing"])).group_by(Alert.enterprise_id)).all()}
        return [{"id": e.id, "name": e.name, "track": e.track, "industry": e.industry, "tech_field": e.tech_field, "region": e.region, "employees": e.employees, "founded_year": e.founded_year, "listed": e.listed, "risk_level": e.risk_level, "revenue": (e.profile or {}).get("financials", {}).get("revenue"), "policies": pol.get(e.id, 0), "open_alerts": al.get(e.id, 0), "trl": min((p["trl"] for p in (e.profile or {}).get("products", [])), default=None)} for e in ents]


@router.get("/enterprises/{eid}")
def get_enterprise(eid: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        e = s.get(Enterprise, eid)
        if e is None:
            raise ApiError(404, "状态冲突", "企业不存在")
        d = enterprise_dict(e)
        if user["role"] == "designer":
            prof = dict(d["profile"])
            for k in ("phone", "address", "legal_rep"):
                prof.pop(k, None)
            d["profile"] = prof
        d["materials"] = [_material_dict(m) for m in s.scalars(select(Material).where(Material.enterprise_id == eid, Material.owner_type == "enterprise")).all()]
        d["risk_reports"] = [{"artifact_key": a.artifact_key, "version": a.version, "status": a.status, "title": a.title, "created_at": iso(a.created_at), "risks": len(a.data.get("risks", []))} for a in s.scalars(select(Artifact).where(Artifact.kind == "risk_report", Artifact.ref_id == eid).order_by(Artifact.id.desc())).all()]
        d["applications"] = [_app_dict(a, s) for a in s.scalars(select(Application).where(Application.enterprise_id == eid)).all()]
        d["policies"] = [_policy_dict(p, s) for p in s.scalars(select(Policy).where(Policy.enterprise_id == eid)).all()]
        d["alerts"] = [_alert_dict(a) for a in s.scalars(select(Alert).where(Alert.enterprise_id == eid).order_by(Alert.id.desc())).all()]
        pids = [p["id"] for p in d["policies"]]
        d["claims"] = [_claim_dict(c, s) for c in s.scalars(select(ClaimCase).where(ClaimCase.policy_id.in_(pids))).all()] if pids else []
        mem = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "entity", MemoryEntry.subject_key == e.name)).first()
        d["memory"] = {"text": mem.text, "content": mem.content} if mem else None
    audit(user, "data_access", "查看客户档案", "enterprise", eid)
    return d


def _material_dict(m: Material) -> dict:
    return {"id": m.id, "name": m.name, "type": m.material_type, "data_level": m.data_level, "detected_level": m.detected_level, "mime": m.mime, "status": m.status, "owner_type": m.owner_type, "owner_id": m.owner_id, "chars": len(m.text or ""), "preview": (m.text or "")[:400], "created_at": iso(m.created_at)}


@router.post("/materials")
async def upload_material(file: UploadFile = File(...), owner_type: str = Form(...), owner_id: int = Form(...), enterprise_id: int = Form(...), material_type: str = Form(...), data_level: int = Form(2), user: dict = Depends(current_user)) -> dict:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "输入契约不满足", "单个文件不超过 30MB")
    folder = settings.materials_dir / f"{owner_type}-{owner_id}"
    folder.mkdir(parents=True, exist_ok=True)
    safe = f"{uuid.uuid4().hex[:6]}-{Path(file.filename or 'upload').name}"
    path = folder / safe
    path.write_bytes(raw)
    text = extract_text(file.filename or "", raw)
    detected = detect_level(text, private_context=True) if text else 2
    mime = file.content_type or ("text/plain" if text is not None else "application/octet-stream")
    # 图片原文按 L3 高敏：后续多模态理解只走私域模型
    if (mime or "").startswith("image/") or (file.filename or "").lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")):
        detected = max(detected, 3)
    with session_scope() as s:
        m = Material(enterprise_id=enterprise_id, owner_type=owner_type, owner_id=owner_id, name=file.filename or safe, material_type=material_type, data_level=max(data_level, detected), detected_level=detected, file_path=str(path), mime=mime, text=text, status="parsed" if text else "uploaded", parsed={"chars": len(text or "")}, uploaded_by=user["id"])
        s.add(m)
        s.flush()
        out = _material_dict(m)
    audit(user, "operation", "上传材料", "material", out["id"], {"level": out["data_level"]})
    return out


@router.get("/materials")
def list_materials(owner_type: str, owner_id: int, user: dict = Depends(current_user)) -> list[dict]:
    with session_scope() as s:
        return [_material_dict(m) for m in s.scalars(select(Material).where(Material.owner_type == owner_type, Material.owner_id == owner_id)).all()]


@router.get("/materials/{mid}")
def get_material(mid: int, user: dict = Depends(current_user)) -> dict:
    with session_scope() as s:
        m = s.get(Material, mid)
        if m is None:
            raise ApiError(404, "状态冲突", "材料不存在")
        d = _material_dict(m)
        if m.data_level >= 3 and user["role"] == "designer":
            d["preview"] = "（L3 高敏材料，设计人员不可查看原文）"
            d["text"] = None
        else:
            d["text"] = m.text
    audit(user, "data_access", "查看材料", "material", mid)
    return d


@router.get("/materials/{mid}/file")
def material_file(mid: int, user: dict = Depends(current_user)):
    with session_scope() as s:
        m = s.get(Material, mid)
        if m is None or not Path(m.file_path).exists():
            raise ApiError(404, "状态冲突", "文件不存在")
        path, mime = m.file_path, m.mime
    return FileResponse(path, media_type=mime)


# ================================================================ 投保与核保
def _app_dict(a: Application, s) -> dict:
    e = s.get(Enterprise, a.enterprise_id)
    p = s.get(Product, a.product_id)
    run = s.get(Run, a.uw_run_id) if a.uw_run_id else None
    return {"id": a.id, "app_no": a.app_no, "enterprise_id": a.enterprise_id, "enterprise": e.name if e else None, "track": e.track if e else None, "product_id": a.product_id, "product": p.name if p else None, "tier": a.tier, "sum_insured": a.sum_insured, "period_months": a.period_months, "status": a.status, "status_label": APP_STATUS.get(a.status, a.status), "decision": a.decision, "intermediary": a.intermediary, "uw_run_id": a.uw_run_id, "uw_run_status": run.status if run else None, "report_artifact_key": a.report_artifact_key, "created_at": iso(a.created_at)}


@router.get("/applications")
def list_applications(user: dict = Depends(require("service")), status: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Application)
        if status:
            q = q.where(Application.status.in_(status.split(",")))
        return [_app_dict(a, s) for a in s.scalars(q.order_by(Application.id.desc())).all()]


class AppBody(BaseModel):
    enterprise_id: int
    product_id: int
    tier: str = "standard"
    sum_insured: float
    period_months: int = 12
    statement: str = ""
    intermediary: str | None = None
    lines: list[str] = []
    authorizations: dict = {"iot": True, "log": True, "ops": True}


@router.post("/applications")
def create_application(body: AppBody, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        e = s.get(Enterprise, body.enterprise_id)
        if e is None:
            raise ApiError(404, "状态冲突", "企业不存在")
        p = e.profile or {}
        contacts = {"phone": p.get("phone"), "address": p.get("address"), "contact": p.get("legal_rep")}
        eq_total = sum(x["value"] for x in p.get("equipment", []))
    aid = _pipe_err(business.create_application, enterprise_id=body.enterprise_id, product_id=body.product_id, tier=body.tier, sum_insured=body.sum_insured, period_months=body.period_months, declared={"statement": body.statement, "lines": body.lines, "authorizations": body.authorizations, "equipment_total": eq_total}, contacts=contacts, intermediary=body.intermediary, user_id=user["id"])
    audit(user, "operation", "创建投保单", "application", aid)
    return {"id": aid}


@router.get("/applications/{aid}")
def get_application(aid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        a = s.get(Application, aid)
        if a is None:
            raise ApiError(404, "状态冲突", "投保单不存在")
        d = _app_dict(a, s)
        d["declared"] = a.declared
        d["contacts"] = a.contacts
        d["materials"] = [_material_dict(m) for m in s.scalars(select(Material).where(Material.owner_type == "application", Material.owner_id == aid)).all()]
        prod = s.get(Product, a.product_id)
        from ..agents.clause_design.library import COMMON_MATERIALS, LINE_MATERIALS

        sel = (a.declared or {}).get("lines") or prod.lines
        d["lines"] = sel
        d["requirements"] = [{"doc_id": "COMMON", "name": "通用", "materials": COMMON_MATERIALS}] + [{"doc_id": l, "name": LINES[l]["name"], "materials": LINE_MATERIALS.get(l, [])} for l in sel if l in LINES]
        report = s.scalars(select(Artifact).where(Artifact.artifact_key == a.report_artifact_key).order_by(Artifact.version.desc())).first() if a.report_artifact_key else None
        d["report"] = {k: v for k, v in report.data.items() if k != "_workspace"} if report else None
        d["report_ref"] = {"artifact_key": report.artifact_key, "version": report.version, "status": report.status} if report else None
        pol = s.scalars(select(Policy).where(Policy.application_id == aid)).first()
        d["policy_id"] = pol.id if pol else None
    return d


@router.post("/applications/{aid}/underwrite")
def underwrite(aid: int, user: dict = Depends(require("service"))) -> dict:
    rid = _pipe_err(business.start_underwriting, aid, user["id"])
    audit(user, "operation", "发起核保", "application", aid)
    return {"run_id": rid}


# ================================================================ 保单与监测
def _policy_dict(p: Policy, s) -> dict:
    e = s.get(Enterprise, p.enterprise_id)
    pr = s.get(Product, p.product_id)
    plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == p.id)).first()
    return {"id": p.id, "policy_no": p.policy_no, "enterprise_id": p.enterprise_id, "enterprise": e.name if e else None, "track": e.track if e else None, "product_id": p.product_id, "product": pr.name if pr else None, "lines": pr.lines if pr else [], "premium": p.premium, "sum_insured": p.sum_insured, "start_date": iso(p.start_date), "end_date": iso(p.end_date), "status": p.status, "plan_id": plan.id if plan else None, "sim_hour": (plan.state or {}).get("hour") if plan else None, "sim_now": iso(monitoring.sim_time((plan.state or {}).get("hour", 0), plan.state)) if plan else None}


@router.get("/policies")
def list_policies(user: dict = Depends(require("service"))) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Policy).order_by(Policy.id.desc())).all()
        out = []
        for p in rows:
            d = _policy_dict(p, s)
            d["open_alerts"] = s.scalar(select(func.count(Alert.id)).where(Alert.policy_id == p.id, Alert.status.in_(["new", "processing"]))) or 0
            d["max_level"] = next((lv for lv in ("red", "orange", "yellow") if s.scalar(select(func.count(Alert.id)).where(Alert.policy_id == p.id, Alert.level == lv, Alert.status.in_(["new", "processing", "escalated"])))), None)
            out.append(d)
        return out


@router.get("/policies/{pid}")
def get_policy(pid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        p = s.get(Policy, pid)
        if p is None:
            raise ApiError(404, "状态冲突", "保单不存在")
        d = _policy_dict(p, s)
        d["coverage"] = p.coverage
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == pid)).first()
        if plan:
            st = plan.state or {}
            d["plan"] = {"id": plan.id, "status": plan.status, "run_id": plan.run_id, "indicators": plan.indicators, "hour": st.get("hour"), "fpr": st.get("fpr"), "maintenance_windows": st.get("maintenance_windows", []), "threshold_changes": st.get("threshold_changes", []), "suppressed": st.get("suppressed", [])[-30:], "blue_digest": st.get("blue_digest", [])[-20:], "detections": st.get("detections", [])[-60:], "scenario": {k: st.get("scenario", {}).get(k) for k in ("device", "logs", "ops", "assets")}, "scheduled_events": st.get("scheduled_events", []), "feedback": st.get("feedback", [])[-20:]}
            metrics = s.execute(select(TelemetryPoint.source, TelemetryPoint.asset, TelemetryPoint.metric, func.count(TelemetryPoint.id)).where(TelemetryPoint.policy_id == pid).group_by(TelemetryPoint.source, TelemetryPoint.asset, TelemetryPoint.metric)).all()
            d["metrics"] = [{"source": a, "asset": b, "metric": c, "points": n} for a, b, c, n in metrics]
        else:
            d["plan"] = None
            d["metrics"] = []
            art = s.scalars(select(Artifact).where(Artifact.kind == "monitor_plan", Artifact.ref_type == "policy", Artifact.ref_id == pid).order_by(Artifact.id.desc())).first()
            d["draft_plan"] = {"artifact_key": art.artifact_key, "version": art.version, "status": art.status, "run_id": art.run_id, "data": {k: v for k, v in (art.data or {}).items() if not str(k).startswith("_")}} if art else None
        d["alerts"] = [_alert_dict(a) for a in s.scalars(select(Alert).where(Alert.policy_id == pid).order_by(Alert.id.desc())).all()]
        d["incidents"] = [_incident_dict(i, s) for i in s.scalars(select(Incident).where(Incident.policy_id == pid).order_by(Incident.id.desc())).all()]
        d["claims"] = [_claim_dict(c, s) for c in s.scalars(select(ClaimCase).where(ClaimCase.policy_id == pid).order_by(ClaimCase.id.desc())).all()]
        d["reports"] = [{"artifact_key": a.artifact_key, "version": a.version, "status": a.status, "title": a.title, "created_at": iso(a.created_at)} for a in s.scalars(select(Artifact).where(Artifact.kind == "risk_mgmt_report", Artifact.ref_id == pid).order_by(Artifact.id.desc())).all()]
        d["runs"] = [run_dict(r) for r in s.scalars(select(Run).where(Run.agent_id == "AG-RW", Run.parent_run_id.is_(None)).order_by(Run.id.desc()).limit(300)).all() if (r.business_ref or {}).get("policy_id") == pid][:30]
    return d


@router.get("/policies/{pid}/telemetry")
def telemetry(pid: int, metric: str, asset: str | None = None, hours: int = 24 * 7, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == pid)).first()
        state = dict(plan.state) if plan else {}
    end = monitoring.sim_time(state.get("hour", 0), state) if plan else None
    ts, vals = monitoring.series(pid, metric, asset, hours=hours, end=end)
    thr = None
    if plan:
        thr = next((i.get("threshold") for i in plan.indicators if i.get("key") == metric and i.get("status") != "degraded"), None)
    return {"metric": metric, "asset": asset, "threshold": thr, "points": [[t.isoformat(), round(float(v), 3)] for t, v in zip(ts, vals)]}


class AdvanceBody(BaseModel):
    hours: int = 6


@router.post("/policies/{pid}/advance")
def advance(pid: int, body: AdvanceBody, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        plan = s.scalars(select(MonitorPlan).where(MonitorPlan.policy_id == pid)).first()
        if plan is None:
            raise ApiError(409, "状态冲突", "该保单尚未发布监测方案")
        plan_id = plan.id
    if not 1 <= body.hours <= 72:
        raise ApiError(422, "输入契约不满足", "每次推进 1–72 小时")
    launched = monitoring.advance(plan_id, body.hours)
    audit(user, "operation", f"推进仿真时钟 {body.hours} 小时", "policy", pid)
    return {"launched": [{"key": c["key"], "level_hint": c["level_hint"], "methods": c["fused"].get("methods")} for c in launched]}


class ReportBody(BaseModel):
    period: str = "month"


@router.post("/policies/{pid}/reports")
def start_report(pid: int, body: ReportBody, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        p = s.get(Policy, pid)
        name = s.get(Enterprise, p.enterprise_id).name
    rid = executor.create_run(agent_id="AG-RW", inputs={"policy_id": pid, "period": body.period}, business_ref={"policy_id": pid}, mode="auto", user_id=user["id"], title=f"风险管理报告：{name}")
    return {"run_id": rid}


class ThresholdBody(BaseModel):
    key: str
    value: float
    reason: str


@router.post("/monitor-plans/{plan_id}/threshold")
def set_threshold(plan_id: int, body: ThresholdBody, user: dict = Depends(require("service"))) -> dict:
    out = _pipe_err(monitoring.set_threshold, plan_id, body.key, body.value, body.reason, user["id"])
    audit(user, "config", "调整监测阈值", "monitor_plan", plan_id, out)
    return out


class WindowBody(BaseModel):
    asset: str
    start_hour: int
    end_hour: int
    note: str


@router.post("/monitor-plans/{plan_id}/maintenance")
def add_window(plan_id: int, body: WindowBody, user: dict = Depends(require("service"))) -> dict:
    return monitoring.add_maintenance_window(plan_id, body.asset, body.start_hour, body.end_hour, body.note)


# ================================================================ 预警
def _alert_dict(a: Alert) -> dict:
    return {"id": a.id, "alert_no": a.alert_no, "policy_id": a.policy_id, "enterprise_id": a.enterprise_id, "level": a.level, "level_label": LEVEL_LABELS.get(a.level, a.level), "risk_type": a.risk_type, "methods": a.methods, "title": a.title, "status": a.status, "status_label": ALERT_STATUS.get(a.status, a.status), "run_id": a.run_id, "sla_due": iso(a.sla_due), "overdue": bool(a.sla_due and a.sla_due < utcnow() and a.status in ("new",)), "created_at": iso(a.created_at), "sim_time": (a.evidence or {}).get("sim_time"), "explanation": (a.analysis or {}).get("explanation"), "confidence": ((a.evidence or {}).get("fused") or {}).get("confidence"), "relation": ((a.evidence or {}).get("impact") or {}).get("relation")}


@router.get("/alerts")
def list_alerts(user: dict = Depends(require("service")), level: str | None = None, status: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Alert)
        if level:
            q = q.where(Alert.level.in_(level.split(",")))
        if status:
            q = q.where(Alert.status.in_(status.split(",")))
        rows = s.scalars(q.order_by(Alert.id.desc()).limit(300)).all()
        names = {e.id: e.name for e in s.scalars(select(Enterprise)).all()}
        return [_alert_dict(a) | {"enterprise": names.get(a.enterprise_id)} for a in rows]


@router.get("/alerts/{aid}")
def get_alert(aid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        a = s.get(Alert, aid)
        if a is None:
            raise ApiError(404, "状态冲突", "预警不存在")
        d = _alert_dict(a)
        d["evidence"] = a.evidence
        d["analysis"] = a.analysis
        d["handling"] = a.handling
        d["enterprise"] = s.get(Enterprise, a.enterprise_id).name
        p = s.get(Policy, a.policy_id)
        d["policy_no"] = p.policy_no
        inc = s.scalars(select(Incident).where(Incident.alert_id == aid)).first()
        d["incident_id"] = inc.id if inc else None
    return d


class HandleBody(BaseModel):
    action: str
    note: str = ""
    reason: str = ""


HANDLE_ACTIONS = {"contact": "联系客户核实", "notice": "发送风险提示函", "advice": "建议客户采取防损措施", "service": "安排风险减量服务", "adjust": "调整监测频率或阈值", "false_positive": "标记误报", "escalate": "升级等级", "incident": "启动事故响应", "close": "处置完成关闭"}


@router.post("/alerts/{aid}/handle")
def handle_alert(aid: int, body: HandleBody, user: dict = Depends(require("service"))) -> dict:
    if body.action not in HANDLE_ACTIONS:
        raise ApiError(422, "输入契约不满足", "未知的处置动作")
    if body.action == "false_positive" and not body.reason:
        raise ApiError(422, "输入契约不满足", "标记误报必须选择原因")
    rank = ["blue", "yellow", "orange", "red"]
    incident_id = None
    with session_scope() as s:
        a = s.get(Alert, aid)
        if a is None:
            raise ApiError(404, "状态冲突", "预警不存在")
        a.handling = list(a.handling or []) + [{"at": iso(utcnow()), "action": HANDLE_ACTIONS[body.action], "note": body.note or body.reason, "by": user["display_name"]}]
        if body.action == "false_positive":
            a.status = "false_positive"
        elif body.action == "close":
            a.status = "closed"
        elif body.action == "escalate":
            a.level = rank[min(3, rank.index(a.level) + 1)]
            a.status = "processing"
        elif body.action == "incident":
            a.status = "escalated"
        elif a.status == "new":
            a.status = "processing"
        policy_id, title, sim = a.policy_id, a.title, (a.evidence or {}).get("sim_time")
        expl = (a.analysis or {}).get("explanation", "")
        impact = (a.evidence or {}).get("impact", {})
    if body.action in ("false_positive", "close", "incident"):
        monitoring.record_feedback(aid, {"false_positive": "false_positive", "close": "valid", "incident": "incident"}[body.action], body.reason, user["id"])
    if body.action == "incident":
        with session_scope() as s:
            existing = s.scalars(select(Incident).where(Incident.alert_id == aid)).first()
        if existing:
            raise ApiError(409, "状态冲突", f"已存在事故响应 {existing.incident_no}")
        incident_id = business.start_incident(policy_id, alert_id=aid, incident_type=impact.get("risk_name", title), description=expl or title, occurred_at=datetime.fromisoformat(sim) if sim else utcnow(), user_id=user["id"])
    audit(user, "operation", f"预警处置：{HANDLE_ACTIONS[body.action]}", "alert", aid)
    return {"ok": True, "incident_id": incident_id}


# ================================================================ 事故响应
def _incident_dict(i: Incident, s) -> dict:
    p = s.get(Policy, i.policy_id)
    e = s.get(Enterprise, p.enterprise_id)
    resp = (i.response or {}).get("response") or {}
    return {"id": i.id, "incident_no": i.incident_no, "policy_id": i.policy_id, "policy_no": p.policy_no, "enterprise": e.name, "alert_id": i.alert_id, "incident_type": i.incident_type, "occurred_at": iso(i.occurred_at), "description": i.description, "status": i.status, "status_label": INCIDENT_STATUS.get(i.status, i.status), "run_id": i.run_id, "claim_id": i.claim_id, "steps_done": sum(1 for st in resp.get("steps", []) if st.get("status") == "done"), "steps_total": len(resp.get("steps", [])), "created_at": iso(i.created_at)}


@router.get("/incidents")
def list_incidents(user: dict = Depends(require("service"))) -> list[dict]:
    with session_scope() as s:
        return [_incident_dict(i, s) for i in s.scalars(select(Incident).order_by(Incident.id.desc())).all()]


@router.get("/incidents/{iid}")
def get_incident(iid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        i = s.get(Incident, iid)
        if i is None:
            raise ApiError(404, "状态冲突", "事故记录不存在")
        d = _incident_dict(i, s)
        d["response"] = i.response
    return d


class StepBody(BaseModel):
    status: str
    note: str = ""


@router.post("/incidents/{iid}/steps/{no}")
def update_step(iid: int, no: int, body: StepBody, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        i = s.get(Incident, iid)
        resp = json.loads(json.dumps(i.response or {}))
        steps = (resp.get("response") or {}).get("steps", [])
        st = next((x for x in steps if x["no"] == no), None)
        if st is None:
            raise ApiError(404, "状态冲突", "步骤不存在")
        st["status"] = body.status
        st["updated_at"] = iso(utcnow())
        st["note"] = body.note
        st["by"] = user["display_name"]
        i.response = resp
    return {"ok": True}


class IncidentBody(BaseModel):
    description: str
    occurred_at: str
    incident_type: str = "待初判"


@router.post("/policies/{pid}/incidents")
def manual_incident(pid: int, body: IncidentBody, user: dict = Depends(require("service"))) -> dict:
    iid = business.start_incident(pid, alert_id=None, incident_type=body.incident_type, description=body.description, occurred_at=datetime.fromisoformat(body.occurred_at), user_id=user["id"])
    return {"incident_id": iid}


# ================================================================ 理赔
def _claim_dict(c: ClaimCase, s) -> dict:
    p = s.get(Policy, c.policy_id)
    e = s.get(Enterprise, p.enterprise_id)
    run = s.get(Run, c.run_id) if c.run_id else None
    return {"id": c.id, "claim_no": c.claim_no, "policy_id": c.policy_id, "policy_no": p.policy_no, "enterprise": e.name, "enterprise_id": e.id, "incident_id": c.incident_id, "status": c.status, "status_label": CLAIM_STATUS.get(c.status, c.status), "claimed_amount": c.claimed_amount, "suggested_amount": c.suggested_amount, "approved_amount": c.approved_amount, "run_id": c.run_id, "run_status": run.status if run else None, "incident_type": (c.report or {}).get("incident_type_name") or (c.report or {}).get("incident_type"), "created_at": iso(c.created_at), "materials": s.scalar(select(func.count(Material.id)).where(Material.owner_type == "claim", Material.owner_id == c.id)) or 0}


@router.get("/claims")
def list_claims(user: dict = Depends(require("service"))) -> list[dict]:
    with session_scope() as s:
        return [_claim_dict(c, s) for c in s.scalars(select(ClaimCase).order_by(ClaimCase.id.desc())).all()]


@router.get("/claims/{cid}")
def get_claim(cid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        c = s.get(ClaimCase, cid)
        if c is None:
            raise ApiError(404, "状态冲突", "案件不存在")
        d = _claim_dict(c, s)
        d["report"] = c.report
        d["parties"] = c.parties
        d["approvals"] = c.approvals
        d["materials_list"] = [_material_dict(m) for m in s.scalars(select(Material).where(Material.owner_type == "claim", Material.owner_id == cid)).all()]
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == c.result_artifact_key).order_by(Artifact.version.desc())).first() if c.result_artifact_key else None
        d["result"] = {k: v for k, v in art.data.items() if not k.startswith("_")} if art else None
        d["result_ref"] = {"artifact_key": art.artifact_key, "version": art.version, "status": art.status} if art else None
        p = s.get(Policy, c.policy_id)
        prod = s.get(Product, p.product_id)
        d["required_materials"] = (prod.package or {}).get("claim_materials", {})
    audit(user, "data_access", "查看理赔案件", "claim", cid)
    return d


class ClaimBody(BaseModel):
    description: str
    occurred_at: str
    claimed_amount: float
    incident_type: str = ""
    parties: list[dict] = []


@router.post("/policies/{pid}/claims")
def report_claim(pid: int, body: ClaimBody, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        p = s.get(Policy, pid)
        if p is None:
            raise ApiError(404, "状态冲突", "保单不存在")
        ent = s.get(Enterprise, p.enterprise_id).name
    parties = body.parties or [{"name": ent, "role": "被保险人", "relation": "投保人与被保险人"}]
    cid = business.register_claim(pid, incident_id=None, report={"source": "report", "occurred_at": body.occurred_at, "description": body.description, "incident_type": body.incident_type or None, "incident_type_name": PLAYBOOKS.get(body.incident_type, {}).get("name", body.incident_type)}, claimed_amount=body.claimed_amount, parties=parties, user_id=user["id"], start=False)
    audit(user, "operation", "报案登记", "claim", cid)
    return {"claim_id": cid}


@router.post("/claims/{cid}/start")
def start_claim(cid: int, user: dict = Depends(require("service"))) -> dict:
    with session_scope() as s:
        c = s.get(ClaimCase, cid)
        if c is None:
            raise ApiError(404, "状态冲突", "案件不存在")
        if c.status not in ("registered", "reported", "pending_approval"):
            raise ApiError(409, "状态冲突", f"案件当前状态为{CLAIM_STATUS.get(c.status, c.status)}，不能启动定损")
        n = s.scalar(select(func.count(Material.id)).where(Material.owner_type == "claim", Material.owner_id == cid)) or 0
    if n == 0:
        raise ApiError(422, "输入契约不满足", "请先上传理赔材料")
    rid = business.start_claim_run(cid, user["id"])
    audit(user, "operation", "启动定损理赔", "claim", cid)
    return {"run_id": rid}


@router.post("/claims/{cid}/pay")
def pay_claim(cid: int, user: dict = Depends(require("service"))) -> dict:
    out = _pipe_err(business.close_claim, cid, user["id"])
    audit(user, "operation", "确认支付指令并结案", "claim", cid)
    return out


_ = timedelta
