"""各智能体共用的业务辅助：企业与材料读取、证据卡片、产物版本、证据核验。"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..jsonutil import jsonable
from ..models import Artifact, Enterprise, Material
from ..seed.enterprises import ENTERPRISES, INDUSTRY_REPORTS, PUBLIC_DATA

LEVEL_LABELS = {0: "L0 公开", 1: "L1 内部", 2: "L2 敏感", 3: "L3 高敏"}


def enterprise_dict(e: Enterprise) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "credit_code": e.credit_code,
        "industry": e.industry,
        "track": e.track,
        "tech_field": e.tech_field,
        "region": e.region,
        "founded_year": e.founded_year,
        "employees": e.employees,
        "listed": e.listed,
        "profile": e.profile or {},
        "risk_level": e.risk_level,
    }


def load_enterprises(ids: list[int]) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(Enterprise).where(Enterprise.id.in_(ids))).all()
        by_id = {r.id: enterprise_dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def load_materials(ids: list[int] | None = None, *, enterprise_id: int | None = None, owner: tuple[str, int] | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Material)
        if ids:
            q = q.where(Material.id.in_(ids))
        elif enterprise_id is not None:
            q = q.where(Material.enterprise_id == enterprise_id, Material.owner_type == "enterprise")
        elif owner is not None:
            q = q.where(Material.owner_type == owner[0], Material.owner_id == owner[1])
        else:
            return []
        rows = s.scalars(q).all()
        return [
            {"id": m.id, "name": m.name, "type": m.material_type, "level": m.data_level, "text": m.text or "", "parsed": m.parsed or {}, "path": m.file_path, "mime": m.mime, "enterprise_id": m.enterprise_id, "created_at": m.created_at.isoformat()}
            for m in rows
        ]


def card_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8]
    return f"E-{prefix}-{digest}"


def new_card(
    *,
    prefix: str,
    source_type: str,
    source_name: str,
    source_ref: str,
    text: str,
    position: str = "",
    date: str | None = None,
    level: int = 0,
    confidence: float = 0.9,
    extractor: str = "",
    zone: str = "cloud",
    enterprise: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    return {
        "id": card_id(prefix, source_ref, position, text[:60]),
        "source_type": source_type,
        "source_name": source_name,
        "source_ref": source_ref,
        "position": position,
        "text": text,
        "date": date,
        "level": level,
        "level_label": LEVEL_LABELS.get(level, ""),
        "confidence": round(confidence, 3),
        "extractor": extractor,
        "zone": zone,
        "enterprise": enterprise,
        "tags": tags or [],
    }


def verify_card(card: dict) -> bool:
    """证据核验：回到原始来源确认证据原文确实存在。"""
    ref = card.get("source_ref", "")
    text = card.get("text", "").strip()
    if not text:
        return False
    snippet = text[: min(len(text), 40)]
    if ref.startswith("material:"):
        mid = int(ref.split(":")[1])
        mats = load_materials([mid])
        return bool(mats) and snippet in mats[0]["text"]
    if ref.startswith(("news:", "patent:", "report:", "court:")):
        name = card.get("enterprise") or ""
        data = PUBLIC_DATA.get(name, {})
        pool = " ".join(n["text"] + n["title"] for n in data.get("news", []))
        pool += " ".join(p["title"] + " ".join(p.get("events", [])) for p in data.get("patents", []))
        pool += data.get("financial_report", "")
        return snippet[:20] in pool or any(snippet[:20] in s for s in pool.split("。"))
    if ref.startswith("industry:"):
        return snippet[:20] in INDUSTRY_REPORTS.get(ref.split(":", 1)[1], "")
    if ref.startswith("profile:"):
        name = ref.split(":", 2)[1]
        return any(e["name"] == name for e in ENTERPRISES) or bool(card.get("derived"))
    if ref.startswith(("kb:", "event:", "registry:", "telemetry:", "dataset:", "inferred:")):
        return True
    return False


def create_artifact(
    *,
    kind: str,
    data: dict,
    title: str,
    run_id: int | None = None,
    project_id: int | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    upstream: list[dict] | None = None,
    key: str | None = None,
    created_by: int | None = None,
    vault: Any | None = None,
    strip_codes: bool = True,
) -> dict:
    from ..models import Run
    from ..privacy.narrative import finalize_narrative

    # 落库前统一：叙述去内部编码 + 脱敏占位还原真值
    payload = finalize_narrative(data, vault, strip_codes=strip_codes)
    title = finalize_narrative(title, vault, strip_codes=strip_codes) if isinstance(title, str) else title

    with session_scope() as s:
        run = s.get(Run, run_id) if run_id else None
        if run is not None and (run.business_ref or {}).get("eval_job"):
            # 评测运行的产物与业务产物隔离：不挂到设计项目或业务对象上
            ref_type, ref_id, project_id = "eval", int(run.business_ref["eval_job"]), None
            if key and not key.startswith("eval-"):
                key = None
        if key:
            version = (s.scalar(select(func.max(Artifact.version)).where(Artifact.artifact_key == key)) or 0) + 1
        else:
            key = f"{'eval-' if ref_type == 'eval' else ''}{kind}-{uuid.uuid4().hex[:10]}"
            version = 1
        art = Artifact(artifact_key=key, kind=kind, version=version, title=title, data=jsonable(payload), run_id=run_id, project_id=project_id, ref_type=ref_type, ref_id=ref_id, upstream=upstream or [], created_by=created_by)
        s.add(art)
        s.flush()
        return {"artifact_key": key, "version": version, "id": art.id}


def latest_artifact(key: str, *, frozen_only: bool = False) -> Artifact | None:
    with session_scope() as s:
        q = select(Artifact).where(Artifact.artifact_key == key)
        if frozen_only:
            q = q.where(Artifact.status == "frozen")
        art = s.scalars(q.order_by(Artifact.version.desc())).first()
        if art is not None:
            s.expunge(art)
        return art


def freeze_artifact(key: str, version: int) -> None:
    with session_scope() as s:
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == key, Artifact.version == version)).first()
        if art is None:
            raise ValueError("产物不存在")
        art.status = "frozen"
        art.frozen_at = utcnow()


def load_policy_bundle(policy_id: int) -> dict:
    """保单、产品条款（按保单出单时的产品版本）、企业、投保单与核保决定的统一读取。"""
    from ..models import Application, Policy, Product

    with session_scope() as s:
        policy = s.get(Policy, policy_id)
        if policy is None:
            raise ValueError("保单不存在")
        product = s.get(Product, policy.product_id)
        ent = s.get(Enterprise, policy.enterprise_id)
        app = s.get(Application, policy.application_id) if policy.application_id else None
        package = product.package or {}
        selected = (policy.coverage or {}).get("lines") or list(product.lines)
        liabilities = []
        for d in package.get("docs", []):
            if not set(d.get("lines", [])) & set(selected):
                continue
            for ch in d.get("chapters", []):
                if ch.get("key") != "liability":
                    continue
                for a in ch.get("articles", []):
                    liabilities.append({"doc_id": d.get("doc_id"), "doc_name": d.get("name"), "lines": d.get("lines", []), "no": a.get("no"), "title": a.get("title"), "text": a.get("text", ""), "risk_ids": a.get("risk_ids", [])})
        bundle = {
            "policy": {"id": policy.id, "policy_no": policy.policy_no, "premium": policy.premium, "sum_insured": policy.sum_insured, "start_date": policy.start_date.isoformat(), "end_date": policy.end_date.isoformat(), "status": policy.status, "coverage": policy.coverage or {}},
            "product": {"id": product.id, "code": product.code, "name": product.name, "lines": [l for l in product.lines if l in selected], "product_lines": product.lines, "version": product.version, "start_mode": product.start_mode},
            "package": package,
            "enterprise": enterprise_dict(ent),
            "application": {"id": app.id, "app_no": app.app_no, "declared": app.declared, "decision": app.decision, "report_artifact_key": app.report_artifact_key, "tier": app.tier} if app else None,
            "liabilities": liabilities,
        }
    return bundle


def policy_doc_params(bundle: dict) -> list[dict]:
    """保单各险种的免赔额、限额、自担比例等参数：以保单出单时记录的参数为准，缺失时取产品条款参数。"""
    cov_docs = {d.get("doc_id"): d for d in (bundle["policy"]["coverage"].get("docs") or [])}
    out = []
    for d in bundle["package"].get("docs", []):
        if cov_docs and d.get("doc_id") not in cov_docs:
            continue
        params = dict(d.get("params") or {})
        params.update({k: v for k, v in ((cov_docs.get(d.get("doc_id")) or {}).get("params") or {}).items() if v is not None})
        out.append({"doc_id": d.get("doc_id"), "name": d.get("name"), "lines": d.get("lines", []), "params": params})
    return out


def level_of_text(text: str, private: bool) -> int:
    from ..privacy.classifier import detect_level

    return detect_level(text, private_context=private)
