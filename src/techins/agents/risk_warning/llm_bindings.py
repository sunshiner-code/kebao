"""风险预警推理任务的大模型输出校验与清洗：编号必须来自输入、枚举必须合法，缺失部分以内置推理补齐。"""

from __future__ import annotations

from ...knowledge.catalog import PLAYBOOKS, SUBTYPES
from ...llm.tasks import attach
from . import reasoning  # noqa: F401
from .reasoning import REPORT_SECTIONS, alert_analysis as builtin_alert, extract_event as builtin_event, plan_rationale as builtin_plan, report as builtin_report


def _post_plan(inputs: dict, out: dict) -> dict:
    base = builtin_plan(inputs)
    ids = {i["id"] for i in inputs.get("indicators", [])}
    rats = {k: str(v) for k, v in (out.get("rationales") or {}).items() if k in ids and str(v).strip()}
    gaps = [{"risk": str(g.get("risk", "")), "suggestion": str(g.get("suggestion", ""))} for g in (out.get("gaps") or []) if isinstance(g, dict) and g.get("suggestion")]
    return {"rationales": {**base["rationales"], **rats}, "gaps": gaps or base["gaps"]}


def _post_event(inputs: dict, out: dict) -> dict:
    base = builtin_event(inputs)
    sub = str(out.get("subtype") or "")
    if sub and sub not in SUBTYPES:
        sub = base["subtype"]
    try:
        sev = max(0.0, min(1.0, float(out.get("severity"))))
    except (TypeError, ValueError):
        sev = base["severity"]
    relevance = out.get("relevance") if out.get("relevance") in ("直接相关", "间接相关", "无直接关联") else base["relevance"]
    return {"event_type": str(out.get("event_type") or base["event_type"]), "subtype": sub, "subject": str(out.get("subject") or inputs.get("subject", "")), "severity": round(sev, 2), "negated": bool(out.get("negated")), "relevance": relevance, "summary": str(out.get("summary") or base["summary"])[:200]}


def _clean_items(items, valid: set[str], fields: tuple[str, ...]) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        refs = [r for r in (it.get("evidence") or []) if r in valid]
        row = {f: it.get(f) for f in fields}
        row["evidence"] = refs
        out.append(row)
    return out


def _post_alert(inputs: dict, out: dict) -> dict:
    base = builtin_alert(inputs)
    valid = {e["id"] for e in inputs.get("evidence", [])}
    causes = [c for c in _clean_items(out.get("causes"), valid, ("text",)) if c["text"]]
    actions = []
    for a in _clean_items(out.get("actions"), valid, ("action", "deadline_hours", "owner")):
        try:
            a["deadline_hours"] = float(a["deadline_hours"])
        except (TypeError, ValueError):
            a["deadline_hours"] = 24.0
        if a["action"] and a["owner"]:
            actions.append(a)
    statements = [s for s in _clean_items(out.get("statements"), valid, ("text",)) if s["text"]]
    return {"explanation": str(out.get("explanation") or base["explanation"]), "causes": causes or base["causes"], "actions": actions or base["actions"], "statements": statements or base["statements"]}


def _post_triage(inputs: dict, out: dict) -> dict:
    if out.get("incident_type") not in PLAYBOOKS:
        raise ValueError("事故类型不在预案库中")
    return {"incident_type": out["incident_type"], "liabilities": [str(x) for x in out.get("liabilities") or []] or ["待核实"], "urgency": out.get("urgency") if out.get("urgency") in ("紧急", "较急", "一般") else "较急", "summary": str(out.get("summary") or "")}


def _post_guidance(inputs: dict, out: dict) -> dict:
    paras = [str(p) for p in out.get("guidance") or [] if str(p).strip()]
    if len(paras) < 2:
        raise ValueError("减损指导内容过少")
    return {"guidance": paras, "customer_notice": str(out.get("customer_notice") or "")}


def _post_report(inputs: dict, out: dict) -> dict:
    from ...privacy.narrative import sanitize_narrative

    base = builtin_report(inputs)
    by_key = {s["key"]: s for s in base["sections"]}
    titles = dict(REPORT_SECTIONS)
    for s in out.get("sections") or []:
        if not isinstance(s, dict) or s.get("key") not in titles:
            continue
        paras = [str(p) for p in s.get("paragraphs") or [] if str(p).strip()]
        if paras:
            by_key[s["key"]] = {"key": s["key"], "title": titles[s["key"]], "paragraphs": paras, "refs": [str(r) for r in s.get("refs") or []] or by_key[s["key"]]["refs"]}
    cleaned = sanitize_narrative({"summary": str(out.get("summary") or base["summary"]), "sections": [by_key[k] for k, _ in REPORT_SECTIONS]})
    return cleaned


def _post_review(inputs: dict, out: dict) -> dict:
    item = (out.get("scores") or {}).get("analysis_quality")
    if not isinstance(item, dict) or "score" not in item:
        raise ValueError("评审缺少 analysis_quality")
    return {"scores": {"analysis_quality": {"score": max(1.0, min(5.0, float(item["score"]))), "reason": str(item.get("reason") or "")}}}


attach("rw.plan_rationale", postprocess=_post_plan, guidance="指标编号必须使用输入中的 KRI-xx；说明控制在 80 字以内")
attach("rw.extract_event", postprocess=_post_event, guidance="subtype 从风险子类型编号中选择（如 R02-1、R07-2、R08-2）；出现“未发生”“不存在”等否定表述时 negated 为 true")
attach("rw.alert_analysis", postprocess=_post_alert, guidance="只能引用输入中出现的证据编号；建议动作必须包含动作、时限（小时）与负责方")
attach("rw.incident_triage", postprocess=_post_triage)
attach("rw.response_guidance", postprocess=_post_guidance)
attach("rw.report", postprocess=_post_report, guidance="段落叙述使用可读名称，不要写风险子类型编码或险种编码（如 R05-5、CY01）；refs 中可填写输入中的指标编号（KRI-xx）、预警编号（YJ...）或 risk_index、lifecycle、authorizations、coverage")
attach("rw.review", postprocess=_post_review)
