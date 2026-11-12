"""报告/结论文本通用后处理：去掉内部业务编码，并把脱敏占位符还原为真值。

供各智能体 compose/draft 出口与 create_artifact 落库前复用。
"""

from __future__ import annotations

import re
from typing import Any

from ..knowledge.catalog import LINES, SUBTYPES
from .gateway import privacy_gateway
from .vault import TokenVault

# 结构化字段保留原值，不在这些键上做「编码→名称」替换（避免破坏下游契约）
_SKIP_KEYS = {
    "id",
    "ids",
    "code",
    "codes",
    "subtype",
    "subtypes",
    "type_code",
    "lines",
    "line",
    "line_codes",
    "evidence_ids",
    "artifact_key",
    "draft_key",
    "run_id",
    "key",
    "keys",
    "model_id",
    "agent_id",
    "credit_code",
    "policy_no",
    "claim_no",
    "version",
    "upstream",
    "upstream_version",
    "heatmap",
    "matrix",
    "stats",
}

_CODE_PATTERNS = [
    (re.compile(r"\bR\d{2}-\d+\b"), ""),
    (re.compile(r"\bNX-\d{3}\b"), ""),
    (re.compile(r"\bR-\d{3}\b"), ""),
    (re.compile(r"\b(?:RD|TR|IP|CY|AI|SP)\d{2}\b"), ""),
]

# 叙述性字段优先清洗；未命中时仍递归处理普通字符串
_NARRATIVE_KEYS = {
    "summary",
    "highlights",
    "uncertainties",
    "management_focus",
    "gaps",
    "conclusion",
    "conclusions",
    "opinion",
    "rationale",
    "reason",
    "reasons",
    "advice",
    "description",
    "analysis",
    "insight",
    "guidance",
    "paragraph",
    "paragraphs",
    "text",
    "body",
    "title",
    "note",
    "notes",
    "focus",
    "suggestion",
    "suggestions",
    "basis",
    "judgment",
    "scene_summary",
    "stage_statements",
    "recommend_rationale",
}


def strip_domain_codes(text: str) -> str:
    """把险种子类型/险种等内部编码换成可读名称，无法映射的则去掉。"""
    if not text or not isinstance(text, str):
        return text
    out = text
    # 先按编码长度降序替换，避免短码误伤长码前缀
    for code, meta in sorted(LINES.items(), key=lambda kv: -len(kv[0])):
        name = (meta or {}).get("name") or code
        if code in out:
            out = out.replace(code, name)
    for code, meta in sorted(SUBTYPES.items(), key=lambda kv: -len(kv[0])):
        name = (meta or {}).get("name") or code
        if code in out:
            out = out.replace(code, name)
    for pat, repl in _CODE_PATTERNS:
        out = pat.sub(repl, out)
    out = re.sub(r"[（(]\s*[，,、;；]?\s*[）)]", "", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def sanitize_narrative(obj: Any, *, strip_codes: bool = True) -> Any:
    """递归清洗叙述文本中的内部编码；结构化编码字段原样保留。"""
    if not strip_codes:
        return obj
    if isinstance(obj, str):
        return strip_domain_codes(obj)
    if isinstance(obj, list):
        return [sanitize_narrative(v, strip_codes=strip_codes) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_narrative(v, strip_codes=strip_codes) for v in obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            key = str(k)
            if key in _SKIP_KEYS or key.endswith("_id") or key.endswith("_ids") or key.endswith("_code") or key.endswith("_codes"):
                out[k] = v
            elif key in _NARRATIVE_KEYS or isinstance(v, (str, list, dict)):
                out[k] = sanitize_narrative(v, strip_codes=strip_codes)
            else:
                out[k] = v
        return out
    return obj


def rehydrate_true_values(obj: Any, vault: TokenVault | None) -> Any:
    """把【企业A】等脱敏占位还原为 vault 中的真值；无 vault 时原样返回。"""
    if vault is None or obj is None:
        return obj
    return privacy_gateway.rehydrate(obj, vault)


def finalize_narrative(obj: Any, vault: TokenVault | None = None, *, strip_codes: bool = True) -> Any:
    """报告/结论通用出口：先去内部编码，再还原脱敏真值。"""
    cleaned = sanitize_narrative(obj, strip_codes=strip_codes)
    return rehydrate_true_values(cleaned, vault)
