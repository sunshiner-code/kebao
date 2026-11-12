"""定损理赔推理任务的大模型输出校验与清洗。"""

from __future__ import annotations

from datetime import datetime

from ...llm.tasks import attach
from . import reasoning  # noqa: F401
from .reasoning import KIND_LABELS, causal_integrate as builtin_causal, clause_check as builtin_clause, extract_claim_doc as builtin_extract, statement_points as builtin_statement


def _post_extract(inputs: dict, out: dict) -> dict:
    base = builtin_extract(inputs)
    fields = {str(k): str(v) for k, v in (out.get("fields") or {}).items() if v not in (None, "")}
    rows = [{str(k): str(v) for k, v in r.items()} for r in (out.get("rows") or []) if isinstance(r, dict)]
    kind = out.get("kind") if out.get("kind") in KIND_LABELS else base["kind"]
    merged = {**base["fields"], **fields}
    try:
        conf = float(out.get("confidence") or 0.85)
    except (TypeError, ValueError):
        conf = 0.85
    return {"kind": kind, "fields": merged, "rows": rows if len(rows) >= len(base["rows"]) else base["rows"], "confidence": round(max(conf, base["confidence"]) if merged else conf, 3)}


def _iso(v) -> str:
    s = str(v or "")[:19].replace(" ", "T")
    try:
        datetime.fromisoformat(s)
        return s
    except ValueError:
        return ""


def _post_statement(inputs: dict, out: dict) -> dict:
    base = builtin_statement(inputs)
    try:
        amount = float(out.get("claimed_amount") or 0)
    except (TypeError, ValueError):
        amount = base["claimed_amount"]
    return {
        "occurred_at": _iso(out.get("occurred_at")) or base["occurred_at"],
        "place": str(out.get("place") or base["place"]),
        "process": str(out.get("process") or base["process"])[:400],
        "stated_cause": str(out.get("stated_cause") or base["stated_cause"])[:200],
        "objects": sorted(set([str(o) for o in out.get("objects") or []] + base["objects"])),
        "reported_at": _iso(out.get("reported_at")) or base["reported_at"],
        "claimed_amount": amount or base["claimed_amount"],
        "mitigation": [str(m) for m in out.get("mitigation") or []][:6] or base["mitigation"],
    }


def _post_image(inputs: dict, out: dict) -> dict:
    action = out.get("action") if out.get("action") in ("维修", "更换", "无需处理") else "维修"
    return {"part": str(out.get("part") or ""), "damages": [{"type": str(d.get("type", "")), "grade": str(d.get("grade", ""))} for d in out.get("damages") or [] if isinstance(d, dict)], "action": action, "old_features": [str(x) for x in out.get("old_features") or []], "confidence": float(out.get("confidence") or 0.75)}


def _post_clause(inputs: dict, out: dict) -> dict:
    base = builtin_clause(inputs)
    valid = {f["id"] for f in inputs.get("facts", [])}
    trig = {t["condition"]: t for t in base["triggers"]}
    for t in out.get("triggers") or []:
        if isinstance(t, dict) and t.get("condition") in trig and t.get("conclusion") in ("满足", "不满足", "待证实"):
            trig[t["condition"]] = {"condition": t["condition"], "conclusion": t["conclusion"], "facts": [f for f in t.get("facts") or [] if f in valid] or trig[t["condition"]]["facts"], "reason": str(t.get("reason") or "")}
    exc = {e["title"]: e for e in base["exclusions"]}
    for e in out.get("exclusions") or []:
        if isinstance(e, dict) and e.get("title") in exc and e.get("conclusion") in ("适用", "不适用", "待证实"):
            exc[e["title"]] = {"title": e["title"], "conclusion": e["conclusion"], "facts": [f for f in e.get("facts") or [] if f in valid] or exc[e["title"]]["facts"], "reason": str(e.get("reason") or "")}
    return {"triggers": list(trig.values()), "exclusions": list(exc.values())}


def _post_causal(inputs: dict, out: dict) -> dict:
    base = builtin_causal(inputs)
    nodes = {n["id"] for n in inputs.get("nodes", [])}
    edges = {}
    for e in base["edges"]:
        edges[(e["from"], e["to"])] = e
    for e in out.get("edges") or []:
        if not isinstance(e, dict) or e.get("from") not in nodes or e.get("to") not in nodes or e.get("from") == e.get("to"):
            continue
        try:
            s = max(0.02, min(0.98, float(e.get("strength"))))
        except (TypeError, ValueError):
            continue
        key = (e["from"], e["to"])
        prev = edges.get(key)
        edges[key] = {"from": e["from"], "to": e["to"], "strength": round(s, 3), "source": e.get("source") if e.get("source") in ("template", "data", "both") else (prev or {}).get("source", "data"), "key": bool(e.get("key", (prev or {}).get("key", False))), "note": str(e.get("note") or (prev or {}).get("note", ""))}
    return {"edges": list(edges.values()), "summary": str(out.get("summary") or base["summary"])}


def _post_liability(inputs: dict, out: dict) -> dict:
    valid = {f["id"] for f in inputs.get("facts", [])}
    reasoning_rows = [{"text": str(r.get("text", "")), "facts": [f for f in r.get("facts") or [] if f in valid]} for r in out.get("reasoning") or [] if isinstance(r, dict) and r.get("text")]
    if not reasoning_rows:
        raise ValueError("说理缺少依据")
    try:
        conf = max(0.0, min(1.0, float(out.get("confidence") or 0.7)))
    except (TypeError, ValueError):
        conf = 0.7
    return {"proximate_cause": str(out.get("proximate_cause") or ""), "chain": [str(c) for c in out.get("chain") or inputs.get("chain", [])], "insured_event": bool(out.get("insured_event")), "confidence": conf, "reasoning": reasoning_rows}


def _post_opinion(inputs: dict, out: dict) -> dict:
    from ...privacy.narrative import sanitize_narrative

    paras = [str(p) for p in out.get("paragraphs") or [] if str(p).strip()]
    if len(paras) < 4:
        raise ValueError("理赔意见内容过少")
    return sanitize_narrative({"paragraphs": paras, "rejection": str(out.get("rejection") or "")})


def _post_review(inputs: dict, out: dict) -> dict:
    item = (out.get("scores") or {}).get("reasoning_quality")
    if not isinstance(item, dict) or "score" not in item:
        raise ValueError("评审缺少 reasoning_quality")
    return {"scores": {"reasoning_quality": {"score": max(1.0, min(5.0, float(item["score"]))), "reason": str(item.get("reason") or "")}}}


attach("cl.extract_claim_doc", postprocess=_post_extract, guidance="字段名使用材料原文中的名称；表格逐行输出且列名保持原表头；金额保留原文单位")
attach("cl.statement_points", postprocess=_post_statement, guidance="时间统一为 ISO 格式（YYYY-MM-DDTHH:MM:SS）；objects 填写设备序列号或系统名称")
attach("cl.image_damage", postprocess=_post_image, guidance="只描述图片中可见的损伤")
attach("cl.clause_check", postprocess=_post_clause, guidance="condition 与 title 必须原样使用输入中的文字；facts 只能引用输入中的事实编号")
attach("cl.causal_integrate", postprocess=_post_causal, guidance="节点编号只能使用输入中的 id；不得形成环")
attach("cl.liability", postprocess=_post_liability)
attach("cl.opinion", postprocess=_post_opinion, guidance="面向业务人员撰写：用险种中文名与风险名称，不要出现子类型编码或险种编码；【企业A】等占位符须原样保留")
attach("cl.review", postprocess=_post_review)
