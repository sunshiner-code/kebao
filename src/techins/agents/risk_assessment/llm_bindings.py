"""风险评估推理任务的大模型上下文与输出校验：大模型拿到与内置引擎相同的领域知识，输出经校验清洗后才被采用。"""

from __future__ import annotations

from ...knowledge.catalog import RISK_TAXONOMY, STAGES, SUB_PHASES, SUBTYPES
from ...llm.tasks import attach
from ...privacy.narrative import sanitize_narrative
from .reasoning import lifecycle as builtin_lifecycle
from .reasoning import novel_from_uncovered as _novel_from_uncovered


def _post_report(_inputs: dict, out: dict) -> dict:
    cleaned = sanitize_narrative(out if isinstance(out, dict) else {})
    return {
        "summary": str(cleaned.get("summary") or ""),
        "highlights": [str(x) for x in (cleaned.get("highlights") or []) if str(x).strip()],
        "uncertainties": [str(x) for x in (cleaned.get("uncertainties") or []) if str(x).strip()],
        "management_focus": [str(x) for x in (cleaned.get("management_focus") or []) if str(x).strip()],
    }


def _taxonomy(_inputs: dict | None = None) -> dict:
    return {
        "风险分类体系": [
            {"subtype": code, "type": sub["type_name"], "name": sub["name"], "signals": sub["patterns"], "stages": sub["stages"], "consequence": sub["consequence"]}
            for code, sub in SUBTYPES.items()
        ],
        "阶段编码": STAGES,
    }


def _post_facts(inputs: dict, out: dict) -> dict:
    text = inputs.get("text", "")
    facts = []
    for f in out.get("facts", []):
        t = str(f.get("text", "")).strip()
        if not t or t[:12] not in text:
            continue
        facts.append({"text": t, "position": f.get("position") or "原文", "kind": f.get("kind") if f.get("kind") in ("risk_fact", "profile_fact") else "risk_fact", "score": float(f.get("score") or 1.0), "subtypes": [s for s in f.get("subtypes", []) if s in SUBTYPES]})
    if not facts:
        raise ValueError("未抽取到可在原文中定位的事实")
    return {"facts": facts}


def _post_risks(inputs: dict, out: dict) -> dict:
    ids = {c["id"] for c in inputs.get("cards", [])}
    cands = []
    for c in out.get("candidates", []):
        code = str(c.get("subtype") or "").strip()
        ev = [e for e in c.get("evidence_ids", []) if e in ids]
        if not ev:
            continue
        title = str(c.get("title") or "").strip()
        if code in SUBTYPES:
            cands.append({"subtype": code, "novel": False, "type_name": SUBTYPES[code]["type_name"], "title": title or SUBTYPES[code]["name"], "description": str(c.get("description") or ""), "evidence_ids": ev, "cues": [str(x) for x in c.get("cues", [])][:8], "strength": float(c.get("strength") or 1.0)})
            continue
        if not title:
            continue
        cands.append(
            {
                "subtype": "NEW",
                "novel": True,
                "type_name": str(c.get("type_name") or "").strip() or "新创建风险",
                "title": title,
                "description": str(c.get("description") or ""),
                "evidence_ids": ev,
                "cues": [str(x) for x in c.get("cues", [])][:8],
                "strength": float(c.get("strength") or 0.7),
                "basis": str(c.get("basis") or "现有风险分类体系没有合适类型，据此创建"),
            }
        )
    cands.extend(_novel_from_uncovered(inputs.get("cards") or [], cands))
    if inputs.get("cards") and not cands:
        raise ValueError("模型未给出可引用证据的风险")
    return {"candidates": cands}


def _post_stages(inputs: dict, out: dict) -> dict:
    by_id = {s.get("id"): s for s in out.get("stages", [])}
    stages = []
    for r in inputs.get("risks", []):
        s = by_id.get(r["id"], {})
        stage = s.get("stage") if s.get("stage") in r.get("allowed", STAGES) else r["rule_stage"]
        involved = [x for x in s.get("involved", [stage]) if x in STAGES] or [stage]
        stages.append({"id": r["id"], "stage": stage, "involved": sorted(set(involved) | {stage}), "rationale": str(s.get("rationale") or "按规则判定")})
    return {"stages": stages}


def _post_direct(inputs: dict, out: dict) -> dict:
    required = [r["id"] for r in inputs.get("risks", [])]
    by_id = {item.get("id"): item for item in out.get("scores") or []}
    scores = []
    for rid in required:
        item = by_id.get(rid)
        if not isinstance(item, dict) or "likelihood" not in item or "severity" not in item:
            raise ValueError(f"直接分数缺少 {rid}")
        likelihood = int(item["likelihood"])
        severity = int(item["severity"])
        if not (1 <= likelihood <= 5 and 1 <= severity <= 5):
            raise ValueError(f"{rid} 的可能性或严重性超出 1 到 5")
        scores.append({"id": rid, "likelihood": likelihood, "severity": severity, "reason": str(item.get("reason") or "")})
    return {"scores": scores}


def _post_adjust(inputs: dict, out: dict) -> dict:
    by_id = {a.get("id"): a for a in out.get("adjustments", [])}
    adjustments = []
    for r in inputs.get("risks", []):
        a = by_id.get(r["id"], {})
        adjustments.append({"id": r["id"], "dl": max(-1, min(1, int(a.get("dl") or 0))), "ds": max(-1, min(1, int(a.get("ds") or 0))), "reason": str(a.get("reason") or "不调整")})
    return {"adjustments": adjustments}


def _post_lifecycle(inputs: dict, out: dict) -> dict:
    skeleton = builtin_lifecycle(inputs)
    codes = {p["code"]: p for p in SUB_PHASES}
    by_name = {p.get("name"): p for p in out.get("products", [])}
    for prod in skeleton["products"]:
        llm = by_name.get(prod["name"])
        if not llm:
            continue
        phase = llm.get("current_phase")
        if phase in codes and abs(int(phase[1]) - int(prod["current_phase"][1])) <= 1:
            prod["current_phase"] = phase
            prod["current_phase_name"] = codes[phase]["name"]
            prod["current_stage"] = codes[phase]["stage"]
            for ph in prod["phases"]:
                ph["is_current"] = ph["code"] == phase
                ph["is_past"] = int(ph["code"][1]) < int(phase[1])
        if llm.get("basis"):
            prod["basis"] = [str(b) for b in llm["basis"]][:4]
        if llm.get("transitions"):
            texts = {(t.get("from"), t.get("to")): t.get("risk") for t in llm["transitions"]}
            for t in prod["transitions"]:
                t["risk"] = texts.get((t["from"], t["to"])) or t["risk"]
    return skeleton


def _post_infer(inputs: dict, out: dict) -> dict:
    wanted = set(inputs.get("missing_types", []))
    risks = [r for r in out.get("risks", []) if r.get("subtype") in SUBTYPES and r["subtype"].split("-")[0] in wanted]
    if not risks:
        raise ValueError("推断结果不在缺失的风险类型内")
    return {"risks": [{"subtype": r["subtype"], "title": str(r.get("title") or SUBTYPES[r["subtype"]]["name"]), "description": str(r.get("description") or ""), "basis": str(r.get("basis") or "第一性原理推断")} for r in risks]}


def _post_review(inputs: dict, out: dict) -> dict:
    scores = {}
    for dim in inputs.get("dimensions", []):
        item = (out.get("scores") or {}).get(dim["key"])
        if not isinstance(item, dict) or "score" not in item:
            raise ValueError(f"评审缺少维度 {dim['key']}")
        scores[dim["key"]] = {"score": max(1.0, min(5.0, float(item["score"]))), "reason": str(item.get("reason") or "")}
    return {"scores": scores}


attach("ra.extract_private_facts", llm_context=_taxonomy, postprocess=_post_facts, guidance="逐句判断，原文照录事实句，不要改写；与风险无关的句子不要输出")
attach("ra.extract_risks", llm_context=_taxonomy, postprocess=_post_risks, guidance="能归入现有子类型时，subtype 必须取自风险分类体系中的编码。没有合适子类型，或仍有风险事实未被已识别风险覆盖时，另建一条新风险：subtype 填 NEW，并给出 type_name，不要硬套相近类型。evidence_ids 只能引用输入证据卡片的编号；一条证据可支撑多条风险；不要虚构证据中不存在的事实")
attach("ra.stage_confirm", llm_context=lambda i: {"阶段编码": STAGES, "说明": "stage 只能在每条风险的 allowed 中选择"}, postprocess=_post_stages)
attach("ra.adjust_scores", postprocess=_post_adjust, guidance="dl、ds 只能是 -1、0、1，理由必须引用证据中的具体事实")
attach("ra.direct_scores", postprocess=_post_direct, guidance="likelihood 与 severity 只能是 1 到 5 的整数，缺依据的风险不要输出")
attach("ra.lifecycle", llm_context=lambda i: {"生命周期子阶段": [{"code": p["code"], "name": p["name"], "stage": p["stage"], "trl": p["trl"], "signals": p["signals"]} for p in SUB_PHASES]}, postprocess=_post_lifecycle)
attach("ra.infer_risks", llm_context=lambda i: {"可选子类型": [{"subtype": c, "name": s["name"], "type": s["type_name"]} for c, s in SUBTYPES.items() if c.split("-")[0] in set(i.get("missing_types", []))], "一级类型": {k: v["name"] for k, v in RISK_TAXONOMY.items()}}, postprocess=_post_infer, guidance="每个缺失的一级类型推断一条最可能的风险，并说明推断依据")
attach("ra.review", postprocess=_post_review, guidance="对 dimensions 中的每个维度给出 1–5 分（可带小数）与理由，scores 的键使用维度的 key")
attach(
    "ra.report",
    postprocess=_post_report,
    guidance="撰写面向业务人员的评估结论：只使用风险名称、等级、阶段与险种中文名；不要出现子类型编码、险种编码、证据编号或内部代码（如 R05-5、CY01、RD08）；【企业A】等占位符须原样保留",
)
