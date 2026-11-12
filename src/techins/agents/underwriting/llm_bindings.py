"""承保风控推理任务的大模型输出校验。"""

from __future__ import annotations

from ...llm.tasks import attach
from . import reasoning  # noqa: F401
from .reasoning import extract_elements as builtin_extract


def _post_elements(inputs: dict, out: dict) -> dict:
    base = builtin_extract(inputs)
    fields = {str(k): str(v) for k, v in (out.get("fields") or {}).items() if v not in (None, "")}
    rows = [r for r in (out.get("rows") or []) if isinstance(r, dict)]
    merged = {**base["fields"], **fields}
    return {"kind": out.get("kind") if out.get("kind") in ("application", "license", "financial", "equipment_list", "invoices", "security", "social", "ip", "product", "other") else base["kind"], "fields": merged, "rows": rows or base["rows"], "confidence": max(float(out.get("confidence") or 0.8), base["confidence"] if merged else 0)}


def _post_image(inputs: dict, out: dict) -> dict:
    return {"equipment": str(out.get("equipment") or ""), "model": str(out.get("model") or ""), "serial": str(out.get("serial") or ""), "captured_at": str(out.get("captured_at") or ""), "hazards": [str(h) for h in out.get("hazards", [])][:6], "damages": [str(d) for d in out.get("damages", [])][:6], "confidence": float(out.get("confidence") or 0.7)}


def _post_review(inputs: dict, out: dict) -> dict:
    item = (out.get("scores") or {}).get("opinion_quality")
    if not isinstance(item, dict) or "score" not in item:
        raise ValueError("评审缺少 opinion_quality")
    return {"scores": {"opinion_quality": {"score": max(1.0, min(5.0, float(item["score"]))), "reason": str(item.get("reason") or "")}}}


def _post_opinion(_inputs: dict, out: dict) -> dict:
    from ...privacy.narrative import sanitize_narrative

    return sanitize_narrative(out if isinstance(out, dict) else {})


attach("uw.extract_elements", postprocess=_post_elements, guidance="字段名使用材料原文中的名称（如 名称、统一社会信用代码、营业收入、等保级别、高危漏洞）；表格逐行输出，列名保持原表头")
attach("uw.image_elements", postprocess=_post_image, guidance="只描述图片中可见的内容；看不清的字段留空")
attach("uw.review", postprocess=_post_review)
attach("uw.opinion", postprocess=_post_opinion, guidance="面向核保员撰写：使用产品与风险中文名，不要出现子类型编码或险种编码；【企业A】等占位符须原样保留")
