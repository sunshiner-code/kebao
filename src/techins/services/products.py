"""产品包组装与审批发布：冻结的条款方案 + 冻结的定价方案 → 产品（条款、费率组件、核保要求、监测要点、理赔材料清单）。"""

from __future__ import annotations

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..knowledge.catalog import CLAIM_MATERIALS, LINES, STAGES
from ..models import Artifact, DesignProject, Notification, Product
from .pipeline import PipelineError, _log


def _stored(key: str, version: int, *, allow_draft: bool) -> Artifact:
    with session_scope() as s:
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == key, Artifact.version == version)).first()
        if art is None or (art.status != "frozen" and not allow_draft):
            raise PipelineError("上游产物不存在或尚未冻结")
        s.expunge(art)
        return art


def build_package(clause: dict, pricing: dict) -> dict:
    docs = clause.get("docs", [])
    comps = []
    for c in pricing.get("components", []):
        rate = c.get("rate") or {}
        comps.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "line": c.get("line"),
            "doc_id": c.get("doc_id"),
            "limit": float((c.get("params") or {}).get("limit") or 0) or None,
            "deductible": float((c.get("params") or {}).get("deductible") or 0),
            "gross_rate": rate.get("gross_rate"),
            "pure_rate": rate.get("pure_rate"),
            "band": rate.get("band"),
            "relativities": rate.get("relativities", []),
            "deductible_factors": rate.get("deductible_factors", []),
            "ilf": rate.get("ilf", []),
            "methods": c.get("methods", []),
            "start_mode": c.get("start_mode"),
        })
    lines = sorted({l for d in docs for l in d.get("lines", [])})
    claim_materials = {"通用": CLAIM_MATERIALS["通用"]}
    for l in lines:
        if l in CLAIM_MATERIALS:
            claim_materials[l] = CLAIM_MATERIALS[l]
        else:
            # 创新险种 / 未入库编码：按责任险通用材料兜底，保证清单可交付
            claim_materials[l] = {
                "required": ["事故认定文件", "损失计算说明", "费用或损害凭证"],
                "optional": ["第三方鉴定报告", "合同或权属证明", "法律费用凭证"],
            }
    return {
        "docs": docs,
        "components": comps,
        "lines": lines,
        "tiers": clause.get("tiers") or {},
        # 只写入有内容的推荐字段，避免产品库出现空表头白块
        "recommendation": {
            k: v
            for k, v in {
                kk: (clause.get("recommendation") or {}).get(kk)
                for kk in ("summary", "structure", "items", "stages", "main_count", "rider_count", "consolidation", "structure_advice")
            }.items()
            if v not in (None, {}, [])
        },
        "coverage_matrix": clause.get("coverage_matrix", []),
        "explanations": clause.get("explanations", []),
        "elements": clause.get("elements", {}),
        "uw_requirements": clause.get("uw_requirements", []),
        "monitoring_points": clause.get("monitoring_points", []),
        "claim_materials": claim_materials,
        "actuarial": {
            "conclusion": (pricing.get("actuarial_report") or {}).get("conclusion") or (pricing.get("report") or {}).get("conclusion"),
            "sections": (pricing.get("actuarial_report") or {}).get("sections") or (pricing.get("report") or {}).get("sections"),
            "combined": (pricing.get("report") or {}).get("combined"),
            "status": (pricing.get("actuarial_report") or {}).get("status"),
            "signed": (pricing.get("actuarial_report") or {}).get("signed"),
            "reviewed_by": (pricing.get("actuarial_report") or {}).get("reviewed_by"),
            "reviewed_at": (pricing.get("actuarial_report") or {}).get("reviewed_at"),
            "review_comment": (pricing.get("actuarial_report") or {}).get("review_comment"),
            "provisional": (pricing.get("actuarial_report") or {}).get("provisional"),
            "filed": (pricing.get("actuarial_report") or {}).get("filed"),
        },
        # 新定价口径：两种并列方案、方法对照、敏感性、选定方法与精算报告，供产品库直接展示
        "pricing": {
            "schemes": pricing.get("schemes") or {},
            "comparison": pricing.get("comparison") or {},
            "sensitivity": pricing.get("sensitivity") or {},
            "selected_method": pricing.get("selected_method"),
            "actuarial_report": pricing.get("actuarial_report") or {},
        },
        "experiments": pricing.get("experiments", []),
        # 与设计智能体定价页一致，供产品库「条款与方案」展示计费规则
        "product_rules": pricing.get("product_rules") or {},
        # 产品页「条款与方案」需能直接看到阻断提交的审查意见
        "compliance": clause.get("compliance") or {},
        "fix_log": clause.get("fix_log") or [],
        "backtests": clause.get("backtests") or [],
    }


def assemble_package(project_id: int, user_id: int | None) -> int:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            raise PipelineError("设计项目不存在")
        nodes = project.pipeline["nodes"]
        clause_ready = nodes["clause"].get("status") == "done"
        pricing_status = nodes["pricing"].get("status")
        if project.project_type == "need":
            if not clause_ready or pricing_status not in ("done", "waiting_confirm"):
                raise PipelineError("条款通过校验并且两种费率已经算出后才能组装产品包")
        elif not clause_ready or pricing_status != "done":
            raise PipelineError("条款方案与定价方案均冻结后才能组装产品包")
        clause_ref = (nodes["clause"]["artifact_key"], nodes["clause"]["version"])
        pricing_ref = (nodes["pricing"]["artifact_key"], nodes["pricing"]["version"])
        name, track, ptype = project.name, project.track, project.project_type
        existing_id = nodes["package"].get("product_id")
        allow_draft = project.project_type == "need" and pricing_status != "done"
    clause = _stored(*clause_ref, allow_draft=allow_draft).data
    pricing = _stored(*pricing_ref, allow_draft=allow_draft).data
    package = build_package(clause, pricing)
    rec = (clause.get("recommendation") or {}).get("structure", {})
    products = rec.get("products") or []
    title = (products[0].get("product_name") or products[0]["name"]) if len(products) == 1 else (f"{track}科技保险保障组合" if track else f"{name}保障方案")
    stages = sorted({LINES[l]["stage"] for l in package["lines"] if l in LINES}, key=list(STAGES).index)
    start_mode = "cold" if any(c.get("start_mode") == "cold" for c in package["components"]) else "warm"
    from .product_admin import default_publish, snapshot

    with session_scope() as s:
        if existing_id:
            product = s.get(Product, existing_id)
            if product.status == "pending_approval":
                raise PipelineError("产品正在审批中，请先撤回审批再重新组装")
            product.version += 1
            product.package, product.lines, product.stages, product.status, product.start_mode = package, package["lines"], stages, "draft", start_mode
            product.approvals = []
            product.approval_id = None
        else:
            n = (s.scalar(select(func.count(Product.id))) or 0) + 1
            product = Product(code=f"KJ{utcnow():%y}{n:04d}", name=title, lines=package["lines"], stages=stages, status="draft", version=1, start_mode=start_mode, package=package, project_id=project_id, publish=default_publish())
            s.add(product)
        s.flush()
        if not product.publish:
            product.publish = default_publish()
        snapshot(s, product, "draft", "产品包组装" if product.version == 1 else f"重新组装（条款 V{clause_ref[1]}、费率 V{pricing_ref[1]}）")
        pid = product.id
        project = s.get(DesignProject, project_id)
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        nodes["package"] = {**nodes["package"], "status": "done", "product_id": pid, "clause_ref": list(clause_ref), "pricing_ref": list(pricing_ref), "at": utcnow().isoformat() + "Z"}
        nodes["approval"] = {**nodes["approval"], "status": "waiting_submit", "product_id": pid, "required_level": 2}
        pipeline["nodes"] = nodes
        _log(pipeline, f"产品包已组装：{title}（{'、'.join(LINES[l]['name'] for l in package['lines'] if l in LINES)}），待填写发布信息并提交审批")
        project.pipeline = pipeline
        project.status = "approval"
        s.add(Notification(role="designer", kind="product", title=f"产品包待提交审批：{title}", body=f"险种 {len(package['lines'])} 个，费率方案 {len(package['components'])} 个；请检查完整性、填写发布信息后提交", link=f"/products/{pid}"))
    return pid


def approve_product(product_id: int, user, action: str = "approve", comment: str = "") -> dict:
    """在产品当前审批单上表态。传入用户编号时按演示兼容路径直接发布。"""
    if isinstance(user, int):
        from .product_admin import publish_direct

        return publish_direct(product_id, user, action, comment)
    from . import approvals

    with session_scope() as s:
        product = s.get(Product, product_id)
        if product is None:
            raise PipelineError("产品不存在")
        approval_id = product.approval_id
        if product.status != "pending_approval" or not approval_id:
            raise PipelineError("产品当前不在审批中，请先提交发布审批")
    try:
        req = approvals.act(approval_id, user, action, comment)
    except approvals.ApprovalError as exc:
        raise PipelineError(str(exc)) from exc
    with session_scope() as s:
        status = s.get(Product, product_id).status
    return {"status": status, "approval": req}
