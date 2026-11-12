"""风险评估智能体的推理任务（大模型路径与内置引擎路径输出同一结构）。"""

from __future__ import annotations

import re
from collections import defaultdict

from ...knowledge.catalog import INDUSTRY_PROFILES, NEGATION_CUES, RISK_TAXONOMY, SEVERITY_CUES, STAGES, SUB_PHASES, SUBTYPES
from ...llm.tasks import reasoning_task
from ...textutil import parse_amounts, parse_percents, split_sentences, truncate

RISK_CUES = ["风险", "延期", "延误", "离职", "依赖", "唯一", "故障", "火灾", "火情", "诉讼", "起诉", "召回", "泄露", "攻击", "不良事件", "亏损", "滞后", "断供", "管制", "侵权", "无效", "过热", "崩缺", "误判", "停线", "停机", "漏洞", "融资", "现金流", "未达", "推迟", "单一", "集中"]
STAGE_WORDS = {
    "rd": ["研发", "实验室", "原理样机", "原型", "立项", "算法", "研发投入", "临床试验", "受试者"],
    "transfer": ["中试", "验证", "示范", "首台套", "首批次", "注册", "工程样机", "工艺放大", "试产", "认证", "装车试验"],
    "industrial": ["量产", "批量", "交付", "销售", "召回", "客户", "出货", "集采", "产线", "部署", "平台"],
}


def _sentence_score(text: str) -> tuple[float, list[str]]:
    subtypes = []
    score = 0.0
    for code, sub in SUBTYPES.items():
        hits = [p for p in sub["patterns"] if p in text]
        if hits:
            subtypes.append(code)
            score += 0.8 * len(hits)
    score += 0.5 * sum(1 for c in RISK_CUES if c in text)
    if parse_amounts(text) or parse_percents(text):
        score += 0.3
    return score, subtypes


@reasoning_task(
    "ra.extract_private_facts",
    "TT-03",
    "从企业私有材料（尽调报告、访谈纪要）中逐句抽取与风险相关的事实陈述，给出位置、类型与相关的风险子类型编号。",
    '{"facts": [{"text": "事实原文", "position": "第x段第y句", "kind": "risk_fact|profile_fact", "score": 0.0, "subtypes": ["R01-2"]}]}',
    required_keys=("facts",),
)
def extract_private_facts(inputs: dict) -> dict:
    facts = []
    paragraphs = [p for p in re.split(r"\n+", inputs.get("text", "")) if p.strip()]
    for pi, para in enumerate(paragraphs, start=1):
        for si, sent in enumerate(split_sentences(para), start=1):
            score, subtypes = _sentence_score(sent)
            if score < 1.0 and not subtypes:
                continue
            kind = "risk_fact" if any(c in sent for c in RISK_CUES) or subtypes else "profile_fact"
            facts.append({"text": sent, "position": f"第{pi}段第{si}句", "kind": kind, "score": round(score, 2), "subtypes": subtypes})
    facts.sort(key=lambda f: -f["score"])
    return {"facts": facts[: int(inputs.get("limit", 60))]}


def _negated(text: str, pattern: str) -> bool:
    idx = text.find(pattern)
    if idx < 0:
        return False
    window = text[max(0, idx - 8) : idx + len(pattern) + 6]
    return any(n in window for n in NEGATION_CUES)


def _source_phrase(text: str) -> str:
    first = re.split(r"[，,；;。]", text)[0]
    return truncate(first, 48)


def _sentence_has_subtype(text: str) -> bool:
    for sub in SUBTYPES.values():
        if any(p in text and not _negated(text, p) for p in sub["patterns"]):
            return True
    return False


_NOVEL_EFFECTS = ("造成", "导致", "引发", "损失", "失效", "失败", "中断", "无法", "被迫", "停滞", "报废", "赔偿", "违约")


def _has_risk_cue(text: str) -> bool:
    strong = [cue for cue in RISK_CUES if cue != "风险" and cue in text and not _negated(text, cue)]
    if strong:
        return True
    return "风险" in text and not _negated(text, "风险") and any(word in text for word in _NOVEL_EFFECTS)


def novel_from_uncovered(cards: list[dict], candidates: list[dict]) -> list[dict]:
    """没有合适子类型，或风险事实未被已有候选覆盖时，按证据创建新风险。"""
    covered_cards = {e for c in candidates if c.get("novel") for e in c.get("evidence_ids") or []}
    created = []
    for card in cards:
        cid = card.get("id")
        text = card.get("text") or ""
        if not cid or cid in covered_cards:
            continue
        sentences = split_sentences(text) or ([text.strip()] if len(text.strip()) >= 4 else [])
        leftover = [sent for sent in sentences if not _sentence_has_subtype(sent) and _has_risk_cue(sent)]
        if not leftover:
            continue
        phrase = _source_phrase(leftover[0])
        cues = sorted({cue for sent in leftover for cue in RISK_CUES if cue in sent})[:8]
        effect = next((word for word in _NOVEL_EFFECTS if any(word in sent for sent in leftover)), "损失")
        title = f"{truncate(phrase, 16)}相关{effect}风险" if phrase else "未归类风险"
        created.append(
            {
                "subtype": "NEW",
                "novel": True,
                "type_name": "新创建风险",
                "title": title,
                "description": f"风险源：{phrase}。事件：{'；'.join(truncate(sent, 80) for sent in leftover[:3])}。后果：现有分类体系未覆盖该风险，可能造成费用、进度或责任损失。",
                "evidence_ids": [cid],
                "cues": cues,
                "strength": 0.7,
                "basis": "现有分类体系没有合适类型，或该风险事实未被已识别风险覆盖，据此创建",
            }
        )
    return created


@reasoning_task(
    "ra.extract_risks",
    "TT-05",
    "依据风险分类体系，从证据卡片中抽取企业的科技创新、研发、成果转化、产业化相关风险。能归入现有子类型的使用体系编码；没有合适子类型，或仍有风险事实未被已识别风险覆盖时，创建新风险：subtype 填 NEW，并给出 type_name。每条风险必须引用至少一张证据卡片编号，并以“风险源→事件→后果”的方式给出情景化描述。",
    '{"candidates": [{"subtype": "R02-2", "title": "风险名称", "description": "风险源→事件→后果", "evidence_ids": ["E-..."], "cues": ["命中线索"], "strength": 0.0, "novel": false, "type_name": ""}]}',
    required_keys=("candidates",),
)
def extract_risks(inputs: dict) -> dict:
    enterprise = inputs.get("enterprise", {})
    focus = set(inputs.get("focus") or [])
    groups: dict[str, list[tuple[dict, list[str], float]]] = defaultdict(list)
    for card in inputs.get("cards", []):
        text = card.get("text", "")
        for code, sub in SUBTYPES.items():
            hits = [p for p in sub["patterns"] if p in text and not _negated(text, p)]
            if not hits:
                continue
            strength = 0.6 * len(hits) + sum(w for cue, w in SEVERITY_CUES.items() if cue in text) * 0.3
            if card.get("source_type") == "business_data":
                strength += 0.4
            groups[code].append((card, hits, strength))
    candidates = []
    for code, items in groups.items():
        sub = SUBTYPES[code]
        total = sum(s for _, _, s in items)
        if total < 0.6 and code.split("-")[0] not in focus:
            continue
        items.sort(key=lambda t: -t[2])
        lead = items[0][0]
        source = _source_phrase(lead["text"])
        product = (enterprise.get("products") or [{}])[0].get("name", "")
        description = f"风险源：{source}。事件：可能发生{sub['name']}{('，影响' + product) if product else ''}。后果：{sub['consequence']}。"
        candidates.append(
            {
                "subtype": code,
                "novel": False,
                "type_name": sub["type_name"],
                "title": sub["name"],
                "description": description,
                "evidence_ids": [c["id"] for c, _, _ in items[:6]],
                "cues": sorted({h for _, hits, _ in items for h in hits})[:8],
                "strength": round(total, 2),
            }
        )
    candidates.extend(novel_from_uncovered(inputs.get("cards") or [], candidates))
    candidates.sort(key=lambda c: -c["strength"])
    return {"candidates": candidates}


@reasoning_task(
    "ra.stage_confirm",
    "TT-05",
    "结合技术成熟度信号与证据，确认每条风险所属的生命周期主阶段与涉及阶段（研发 rd、成果转化 transfer、产业化 industrial），说明理由。",
    '{"stages": [{"id": "风险编号", "stage": "rd|transfer|industrial", "involved": ["rd"], "rationale": "理由"}]}',
    required_keys=("stages",),
)
def stage_confirm(inputs: dict) -> dict:
    out = []
    for r in inputs.get("risks", []):
        allowed = r.get("allowed") or ["rd", "transfer", "industrial"]
        counts = {k: sum(1 for t in r.get("evidence_texts", []) for w in words if w in t) for k, words in STAGE_WORDS.items()}
        trl = r.get("product_trl")
        if trl:
            trl_stage = "rd" if trl <= 4 else ("transfer" if trl <= 7 else "industrial")
            counts[trl_stage] = counts.get(trl_stage, 0) + 1
        ranked = sorted((k for k in counts if k in allowed), key=lambda k: -counts[k])
        rule_stage = r.get("rule_stage")
        stage = rule_stage
        rationale = f"按风险分类体系默认归入{STAGES[rule_stage]}"
        if ranked and counts[ranked[0]] >= 2 and ranked[0] != rule_stage:
            stage = ranked[0]
            rationale = f"证据中{STAGES[stage]}阶段信号最强（{counts[stage]} 处），且该子类型允许出现在此阶段"
        elif ranked and counts.get(rule_stage, 0) > 0:
            rationale += f"，证据中有 {counts[rule_stage]} 处该阶段信号支持"
        involved = sorted({stage} | {k for k in allowed if counts.get(k, 0) > 0})
        out.append({"id": r["id"], "stage": stage, "involved": involved, "rationale": rationale})
    return {"stages": out}


@reasoning_task(
    "ra.adjust_scores",
    "TT-05",
    "在指标规则给出的可能性与严重度基线分（1–5）上，依据证据作不超过 1 分的调整并说明理由。",
    '{"adjustments": [{"id": "风险编号", "dl": 0, "ds": 0, "reason": "理由"}]}',
    required_keys=("adjustments",),
)
def adjust_scores(inputs: dict) -> dict:
    out = []
    for r in inputs.get("risks", []):
        texts = r.get("evidence_texts", [])
        dl, ds, reasons = 0, 0, []
        recent = [t for t in r.get("evidence_dates", []) if t and t >= "2026-01-01"]
        if len(texts) >= 3 or recent:
            dl = 1
            reasons.append(f"证据 {len(texts)} 条{('，含 ' + str(len(recent)) + ' 条近期事件') if recent else ''}，提高可能性")
        elif r.get("inferred") and len(texts) <= 1:
            dl = -1
            reasons.append("仅为推断且证据单薄，降低可能性")
        severity_hits = [cue for cue in SEVERITY_CUES if any(cue in t for t in texts)]
        if severity_hits:
            ds = 1
            reasons.append("证据出现严重性线索：" + "、".join(severity_hits[:3]))
        out.append({"id": r["id"], "dl": dl, "ds": ds, "reason": "；".join(reasons) or "证据与基线一致，不调整"})
    return {"adjustments": out}


@reasoning_task(
    "ra.direct_scores",
    "TT-05",
    "根据客户原话、风险描述和证据，直接给出每条风险 1 到 5 的可能性和严重性。",
    '{"scores": [{"id": "R-001", "likelihood": 3, "severity": 3, "reason": "理由"}]}',
    required_keys=("scores",),
    guidance="likelihood 与 severity 必须是 1 到 5 的整数。这是绝对分数，不是在基线上的加减。只依据输入中的客户原话、描述和证据。没有依据的风险不要打分。",
)
def direct_scores(_inputs: dict) -> dict:
    raise ValueError("没有模型判定时不能用类型或财务基线代替直接分数")


@reasoning_task(
    "ra.lifecycle",
    "TT-05",
    "判定每个科技产品当前所处的生命周期子阶段（立项、研发、中试验证、转化示范、量产、市场推广、迭代或退出），给出判定依据，并对后续子阶段做前瞻风险评估与阶段跃迁风险说明。",
    '{"products": [{"name": "", "current_phase": "P3", "basis": [""], "phases": [{"code": "P1", "name": "", "stage": "rd", "risk_ids": [], "forward": [""]}], "transitions": [{"from": "P3", "to": "P4", "risk": ""}]}]}',
    required_keys=("products",),
)
def lifecycle(inputs: dict) -> dict:
    transitions_text = {
        ("P1", "P2"): "技术路线从概念走向原型，关键技术可行性未验证",
        ("P2", "P3"): "原理样机向工程样机转化，设计冻结与可靠性验证带来新的研发投入与设备依赖",
        ("P3", "P4"): "工艺放大与批次一致性风险集中暴露，中试失败将造成转化费用损失",
        ("P4", "P5"): "首台套或首批次应用结果决定能否量产，质量缺陷导致更换退货与责任风险",
        ("P5", "P6"): "量产爬坡后产品责任、召回与供应链中断风险放大",
        ("P6", "P7"): "技术迭代与替代风险上升，存量产品质量责任仍需覆盖",
    }
    track = inputs.get("track", "")
    prior = INDUSTRY_PROFILES.get(track, {}).get("high_incidence", [])
    risks = inputs.get("risks", [])
    products_out = []
    for p in inputs.get("products", []):
        trl = int(p.get("trl") or 5)
        desc = p.get("description", "")
        phase = next((ph for ph in SUB_PHASES if ph["trl"][0] <= trl <= ph["trl"][1]), SUB_PHASES[1])
        basis = [f"技术成熟度 TRL{trl} 对应「{phase['name']}」"]
        signal_hits = [(ph, [s for s in ph["signals"] if s in desc]) for ph in SUB_PHASES]
        strongest = max(signal_hits, key=lambda t: len(t[1]))
        if strongest[1] and abs(SUB_PHASES.index(strongest[0]) - SUB_PHASES.index(phase)) == 1:
            phase = strongest[0]
            basis.append(f"产品描述出现「{'、'.join(strongest[1])}」信号，调整为「{phase['name']}」")
        elif strongest[1]:
            basis.append(f"产品描述中的阶段信号：{'、'.join(strongest[1])}")
        idx = SUB_PHASES.index(phase)
        phases = []
        for j, ph in enumerate(SUB_PHASES):
            ids = [r["id"] for r in risks if r.get("stage") == ph["stage"] and (j >= idx or ph["stage"] == phase["stage"])]
            forward = []
            if j > idx:
                for code in prior:
                    sub = SUBTYPES[code]
                    if ph["stage"] in sub["stages"] and not any(r["subtype"] == code for r in risks):
                        forward.append(f"{sub['name']}（行业高发，进入该阶段后需关注）")
            phases.append({"code": ph["code"], "name": ph["name"], "stage": ph["stage"], "risk_ids": ids if j >= idx - 1 else [], "forward": forward[:4], "is_current": j == idx, "is_past": j < idx})
        transitions = [{"from": SUB_PHASES[k]["code"], "to": SUB_PHASES[k + 1]["code"], "risk": transitions_text[(SUB_PHASES[k]["code"], SUB_PHASES[k + 1]["code"])]} for k in range(idx, min(idx + 2, len(SUB_PHASES) - 1))]
        products_out.append({"name": p.get("name"), "trl": trl, "current_phase": phase["code"], "current_phase_name": phase["name"], "current_stage": phase["stage"], "basis": basis, "phases": phases, "transitions": transitions})
    return {"products": products_out}


@reasoning_task(
    "ra.infer_risks",
    "TT-05",
    "对证据不足或冷启动的风险类型，按“风险源→风险事件→损失形态→责任主体”的第一性原理分解，结合行业知识推断潜在风险，所有结果必须标注为推断。",
    '{"risks": [{"subtype": "", "title": "", "description": "", "basis": ""}]}',
    required_keys=("risks",),
)
def infer_risks(inputs: dict) -> dict:
    track = inputs.get("track", "")
    prior = INDUSTRY_PROFILES.get(track, {}).get("high_incidence", [])
    missing_types = inputs.get("missing_types") or []
    products = inputs.get("products", [])
    product = products[0]["name"] if products else "主要科技产品"
    out = []
    for type_code in missing_types:
        rt = RISK_TAXONOMY[type_code]
        ranked = sorted(rt["subtypes"].items(), key=lambda kv: (kv[0] not in prior, kv[0]))
        code, sub = ranked[0]
        knowledge = inputs.get("knowledge", {}).get(type_code, "")
        out.append(
            {
                "subtype": code,
                "title": sub["name"],
                "description": f"风险源：{track or '该行业'}企业普遍存在{rt['name']}暴露。事件：{product}在{STAGES[sub['stages'][0]]}阶段可能发生{sub['name']}。后果：{sub['consequence']}。",
                "basis": ("行业高发风险（风险知识库）" if code in prior else "风险分类体系第一性原理分解") + (f"；参考：{truncate(knowledge, 60)}" if knowledge else ""),
            }
        )
    return {"risks": out}


@reasoning_task(
    "ra.report",
    "TT-06",
    "根据风险清单、类型覆盖、可保性与生命周期结论，撰写风险评估报告的画像摘要、关键发现与主要不确定性，措辞客观，不作承诺。",
    '{"summary": "", "highlights": [""], "uncertainties": [""], "management_focus": [""]}',
    required_keys=("summary", "highlights"),
)
def report(inputs: dict) -> dict:
    ent = inputs.get("enterprise", {})
    stats = inputs.get("stats", {})
    top = inputs.get("top_risks", [])
    stage_counts = stats.get("stage_counts", {})
    summary = (
        f"{ent.get('name', '被评估对象')}属于{ent.get('track', '')}赛道，主营{ent.get('tech_field', '')}。"
        f"本次识别风险 {stats.get('total', 0)} 条，覆盖 {stats.get('types_covered', 0)} 个一级风险类型，其中高及以上等级 {stats.get('high', 0)} 条；"
        f"研发阶段 {stage_counts.get('rd', 0)} 条、成果转化阶段 {stage_counts.get('transfer', 0)} 条、产业化阶段 {stage_counts.get('industrial', 0)} 条。"
        f"总体风险等级为{stats.get('overall_level', '中')}。"
    )
    highlights = [f"{r['title']}（{r['level']}，风险值 {r['score']}）：{truncate(r['description'], 80)}" for r in top[:5]]
    uncertainties = list(inputs.get("gaps", []))[:5]
    if stats.get("inferred", 0):
        uncertainties.append(f"有 {stats['inferred']} 条风险为推断结论，需补充材料验证")
    if stats.get("novel", 0):
        uncertainties.append(f"有 {stats['novel']} 条风险为新创建，现有分类体系没有对应类型，需人工确认")
    focus = [f"优先处置{r['title']}：{r.get('advice', '')}" for r in top[:3] if r.get("advice")]
    return {"summary": summary, "highlights": highlights, "uncertainties": uncertainties, "management_focus": focus}


@reasoning_task(
    "ra.review",
    "TT-08",
    "按评估量表对风险评估结果打分（1–5 分）：具体性、完整性、一致性、阶段归类合理性、建议可操作性，并给出理由。",
    '{"scores": {"specificity": {"score": 4, "reason": ""}}}',
    required_keys=("scores",),
)
def review(inputs: dict) -> dict:
    risks = inputs.get("risks", [])
    n = max(1, len(risks))
    terms = [t for t in inputs.get("enterprise_terms", []) if t]
    specific = sum(1 for r in risks if any(t in r["description"] for t in terms) or re.search(r"\d", r["description"]))
    prior = inputs.get("industry_prior", [])
    covered = {r["subtype"] for r in risks} | {r["subtype"].split("-")[0] for r in risks}
    prior_hit = sum(1 for p in prior if p in covered or p.split("-")[0] in covered)
    consistent = sum(1 for r in risks if not (r["level"] in ("高", "极高") and r["evidence_count"] == 0))
    stage_ok = sum(1 for r in risks if r["stage"] in r.get("allowed", [r["stage"]]))
    actionable = sum(1 for r in risks if len(r.get("advice", "")) >= 12)

    def to_score(ratio: float) -> float:
        return round(1 + 4 * max(0.0, min(1.0, ratio)), 2)

    return {
        "scores": {
            "specificity": {"score": to_score(specific / n), "reason": f"{specific}/{n} 条风险描述包含企业特定信息或量化事实"},
            "completeness": {"score": to_score(prior_hit / max(1, len(prior))), "reason": f"覆盖行业高发风险 {prior_hit}/{len(prior)} 项"},
            "consistency": {"score": to_score(consistent / n), "reason": f"{consistent}/{n} 条风险的等级与证据支撑一致"},
            "stage_reasonableness": {"score": to_score(stage_ok / n), "reason": f"{stage_ok}/{n} 条风险的阶段归类符合分类体系允许范围"},
            "actionability": {"score": to_score(actionable / n), "reason": f"{actionable}/{n} 条风险有具体的管理建议"},
        }
    }
