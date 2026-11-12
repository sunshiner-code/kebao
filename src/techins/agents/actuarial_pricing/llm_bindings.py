"""精算定价推理任务的大模型上下文与输出校验。"""

from __future__ import annotations

from ...knowledge.catalog import FACTOR_DICTIONARY
from ...llm.tasks import attach
from . import reasoning  # noqa: F401 - 先注册推理任务

FACTOR_INDEX = {f["key"]: f for f in FACTOR_DICTIONARY}


def _post_factors(inputs: dict, out: dict) -> dict:
    available = set(inputs.get("available", []))
    seen, factors = set(), []
    for f in out.get("factors", []):
        key = f.get("key")
        if key in available and key not in seen:
            seen.add(key)
            direction = int(f.get("expected_direction") or FACTOR_INDEX.get(key, {}).get("direction", 0))
            factors.append({"key": key, "rationale": str(f.get("rationale") or ""), "expected_direction": max(-1, min(1, direction))})
    if not factors:
        raise ValueError("未提出可用因子")
    return {"factors": factors}


def _post_values(inputs: dict, out: dict) -> dict:
    keys = set(inputs.get("factors", []))
    ids = {t["id"] for t in inputs.get("texts", [])}
    rows = []
    for r in out.get("rows", []):
        rid = str(r.get("id"))
        if rid not in ids:
            continue
        values = {}
        for k, v in (r.get("values") or {}).items():
            if k in keys:
                try:
                    values[k] = None if v is None else float(v)
                except (TypeError, ValueError):
                    values[k] = None
        rows.append({"id": rid, "values": values, "confidence": {k: float(v) for k, v in (r.get("confidence") or {}).items() if k in keys and isinstance(v, (int, float))}})
    if len(rows) < len(ids) * 0.6:
        raise ValueError("抽取结果覆盖的文本过少")
    return {"rows": rows}


def _post_weights(inputs: dict, out: dict) -> dict:
    methods = set(inputs.get("results", {}))
    weights = {m: float(w) for m, w in (out.get("weights") or {}).items() if m in methods and isinstance(w, (int, float)) and w >= 0}
    if not weights or sum(weights.values()) <= 0:
        raise ValueError("合成权重无效")
    return {"analysis": str(out.get("analysis") or ""), "weights": weights}


def _post_review(inputs: dict, out: dict) -> dict:
    item = (out.get("scores") or {}).get("report_quality")
    if not isinstance(item, dict) or "score" not in item:
        raise ValueError("评审缺少 report_quality")
    return {"scores": {"report_quality": {"score": max(1.0, min(5.0, float(item["score"]))), "reason": str(item.get("reason") or "")}}}


def _post_report(_inputs: dict, out: dict) -> dict:
    from ...privacy.narrative import sanitize_narrative

    return sanitize_narrative(out if isinstance(out, dict) else {})


attach("ap.factor_proposals", llm_context=lambda i: {"可用因子": [{"key": k, **{kk: FACTOR_INDEX[k][kk] for kk in ("name", "definition", "direction", "source")}} for k in i.get("available", []) if k in FACTOR_INDEX]}, postprocess=_post_factors, guidance="key 只能取自 available；按与风险的相关程度排序；说明每个因子度量的是哪类风险暴露")
attach("ap.extract_factor_values", postprocess=_post_values, guidance="id 使用输入中的 id；数值保留两位小数；无法从文本判断时取 null")
attach("ap.method_diff", postprocess=_post_weights, guidance="weights 的键只能是 results 中的方法编号（PM1–PM4），权重之和为 1")
attach("ap.review", postprocess=_post_review)
attach("ap.report", postprocess=_post_report, guidance="结论文本使用险种与因子中文名，不要出现子类型编码或险种编码；【企业A】等占位符须原样保留")
