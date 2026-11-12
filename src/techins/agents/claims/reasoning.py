"""定损理赔智能体的推理任务（内置推理引擎实现 + 大模型任务说明）。"""

from __future__ import annotations

import re

from ...analytics import vision
from ...llm.tasks import reasoning_task
from ...textutil import format_amount, parse_amounts

KV = re.compile(r"([\u4e00-\u9fa5A-Za-z（）()]{2,16})[：:]\s*([^；;\n]+)")
DOC_KINDS = [
    ("索赔申请", "claim_form"),
    ("出险通知", "notice"),
    ("事故声明", "statement"),
    ("损失清单", "loss_list"),
    ("维修报价", "repair_quote"),
    ("发票", "invoice"),
    ("检测报告", "inspection"),
    ("鉴定", "inspection"),
    ("取证报告", "forensic"),
    ("事故证明", "certificate"),
    ("项目台账", "rd_ledger"),
    ("费用凭证", "expense"),
    ("营业中断", "bi_proof"),
    ("维保记录", "maintenance"),
    ("监管通知", "regulator"),
    ("云服务", "service_contract"),
    ("服务合同", "service_contract"),
]
KIND_LABELS = {"claim_form": "索赔申请书", "notice": "出险通知", "statement": "客户事故声明", "loss_list": "损失清单", "repair_quote": "维修报价", "invoice": "购置发票", "inspection": "检测报告", "forensic": "网络安全取证报告", "certificate": "事故证明", "rd_ledger": "研发项目台账", "expense": "费用凭证", "bi_proof": "业务中断证明", "maintenance": "维保记录", "regulator": "监管通知", "service_contract": "服务合同", "other": "其他材料"}
TIME_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2})?[时:：]?(\d{1,2})?")


def classify_claim_doc(name: str, text: str) -> str:
    for key, kind in DOC_KINDS:
        if key in name:
            return kind
    for key, kind in DOC_KINDS:
        if key in text[:60]:
            return kind
    return "other"


def parse_rows(text: str) -> list[dict]:
    """解析竖线或制表符分隔的表格（表头含"项目"或"名称"）。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header_idx = next((i for i, l in enumerate(lines) if ("项目" in l or "名称" in l) and ("|" in l or "｜" in l or "\t" in l)), None)
    if header_idx is None:
        return []
    split = lambda s: [c.strip() for c in re.split(r"\s*[|｜]\s*|\t", s.strip("|｜ "))]  # noqa: E731
    headers = split(lines[header_idx])
    rows = []
    for l in lines[header_idx + 1 :]:
        if set(l) <= set("-|｜ "):
            continue
        cells = split(l)
        if len(cells) != len(headers):
            break
        rows.append(dict(zip(headers, cells)))
    return rows


def find_time(text: str) -> str | None:
    m = TIME_RE.search(text or "")
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}T{int(h or 0):02d}:{int(mi or 0):02d}:00"


@reasoning_task(
    "cl.extract_claim_doc",
    "TT-03",
    "解析理赔材料：识别材料类型（索赔申请书、出险通知、客户事故声明、损失清单、维修报价、购置发票、检测报告、网络安全取证报告、事故证明、研发项目台账、费用凭证、业务中断证明、维保记录、监管通知、服务合同等），抽取关键键值要素与表格行，给出置信度。金额、日期、对象（设备序列号、系统名称）必须与原文一致。",
    '{"kind": "claim_form|notice|statement|loss_list|repair_quote|invoice|inspection|forensic|certificate|rd_ledger|expense|bi_proof|maintenance|regulator|service_contract|other", "fields": {"字段": "值"}, "rows": [{"列": "值"}], "confidence": 0.9}',
    required_keys=("kind", "fields"),
)
def extract_claim_doc(inputs: dict) -> dict:
    text = inputs.get("text", "")
    kind = classify_claim_doc(inputs.get("name", ""), text)
    fields = {k.strip(): v.strip() for k, v in KV.findall(text) if len(v.strip()) <= 120}
    rows = parse_rows(text)
    key_hits = sum(1 for k in fields if any(x in k for x in ("时间", "金额", "日期", "序列号", "保单", "合计", "结论", "原因")))
    conf = min(0.97, 0.7 + 0.05 * key_hits + (0.1 if rows else 0)) if (fields or rows) else 0.4
    return {"kind": kind, "fields": fields, "rows": rows, "confidence": round(conf, 3)}


@reasoning_task(
    "cl.statement_points",
    "TT-03",
    "从客户事故声明中抽取要点：出险时间、地点、经过、原因（客户陈述）、受损对象、发现人、报案时间、报损估计、已采取的施救措施。",
    '{"occurred_at": "YYYY-MM-DDTHH:MM:SS", "place": "", "process": "", "stated_cause": "", "objects": [""], "reported_at": "", "claimed_amount": 0, "mitigation": [""]}',
    required_keys=("occurred_at", "process"),
)
def statement_points(inputs: dict) -> dict:
    text = inputs.get("text", "")
    sentences = [s for s in re.split(r"[。！\n]", text) if s.strip()]
    occurred = find_time(text)
    place = next((m.group(1) for m in [re.search(r"(?:在|于)([\u4e00-\u9fa5A-Za-z0-9]{2,16}(?:车间|实验室|研发中心|机房|基地|研发楼|中心))", text)] if m), "")
    cause = next((s for s in sentences if any(k in s for k in ("原因", "导致", "由于", "因"))), "")
    objects = re.findall(r"[（(]序列号\s*([A-Z0-9-]+)[)）]", text) + re.findall(r"(核心业务系统|数据库|服务器|客户数据)", text)
    reported = None
    m = re.search(r"(?:报案|通知保险人|报险)[^。]*?(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*\d{1,2}[时:：]\d{0,2})", text)
    if m:
        reported = find_time(m.group(1))
    amounts = parse_amounts(next((s for s in sentences if any(k in s for k in ("损失", "索赔", "报损"))), ""))
    mitig = [s.strip() for s in sentences if any(k in s for k in ("断电", "隔离", "停机", "封存", "备份", "抢救", "租赁", "通知"))][:4]
    return {"occurred_at": occurred or "", "place": place, "process": "。".join(sentences[:3])[:300], "stated_cause": cause[:160], "objects": sorted(set(objects)), "reported_at": reported or "", "claimed_amount": max(amounts) if amounts else 0, "mitigation": mitig}


@reasoning_task(
    "cl.image_damage",
    "TT-10",
    "识别理赔照片：受损部位、损伤类型（烧蚀、变形、破裂、水渍、腐蚀、电弧痕迹等）、程度（轻微、中度、严重、全损）、维修或更换建议、可见的陈旧特征（锈蚀氧化等）。",
    '{"part": "", "damages": [{"type": "", "grade": ""}], "action": "维修|更换|无需处理", "old_features": [""], "confidence": 0.8}',
    required_keys=("damages", "action"),
)
def image_damage(inputs: dict) -> dict:
    img = vision.load(inputs["path"])
    meta = vision.metadata(img)
    dmg = vision.assess_damage(img)
    old = []
    if dmg["features"]["rust_ratio"] > 0.05:
        old.append("表面锈蚀氧化，属陈旧特征")
    return {"part": meta.get("part") or meta.get("equipment") or "", "damages": [{"type": d["type"], "grade": d["grade"]} for d in dmg["damages"]], "action": dmg["action"], "old_features": old, "confidence": dmg["confidence"]}


TRIGGER_RULES = [
    ("列明", "subject_listed", "受损标的为保险单列明的标的"),
    ("意外", "accidental", "损失由意外事故（不可预料、突然发生）造成"),
    ("突然", "accidental", "损失由意外事故（不可预料、突然发生）造成"),
    ("物质损失", "physical_loss", "存在直接物质损失"),
    ("损坏", "physical_loss", "存在直接物质损失"),
    ("中断", "interruption", "研发或营业活动发生中断"),
    ("数据", "data_event", "发生数据安全事件"),
    ("网络", "data_event", "发生数据安全事件"),
]
EXCLUSION_RULES = [
    ("自然磨损", "wear", "自然磨损、氧化、锈蚀"),
    ("磨损", "wear", "自然磨损、氧化、锈蚀"),
    ("维护", "maintenance", "被保险人未按规定维护保养"),
    ("保养", "maintenance", "被保险人未按规定维护保养"),
    ("故意", "intent", "投保人、被保险人的故意行为"),
    ("战争", "war", "战争、军事行动、网络战"),
    ("赎金", "ransom", "支付的赎金"),
    ("罚款", "fine", "罚款、罚金"),
    ("间接损失", "indirect", "间接损失（另有约定除外）"),
]


@reasoning_task(
    "cl.clause_check",
    "TT-05",
    "条款核查：把保险责任拆成触发条件逐个与事实匹配（满足 / 不满足 / 待证实），逐条判断责任免除是否适用（适用 / 不适用 / 待证实），每个结论写明依据的事实编号（F-n）。证据不足时必须给出“待证实”，不得臆测。",
    '{"triggers": [{"condition": "", "conclusion": "满足|不满足|待证实", "facts": ["F-1"], "reason": ""}], "exclusions": [{"title": "", "conclusion": "适用|不适用|待证实", "facts": ["F-1"], "reason": ""}]}',
    required_keys=("triggers", "exclusions"),
)
def clause_check(inputs: dict) -> dict:
    facts = {f["id"]: f for f in inputs.get("facts", [])}
    flags = inputs.get("flags", {})

    def fact_ids(*keys: str) -> list[str]:
        return [fid for fid, f in facts.items() if any(k in f.get("tags", []) for k in keys)]

    triggers = []
    for cond in inputs.get("trigger_conditions", []):
        kind = cond.get("kind")
        val = flags.get(kind)
        conclusion = "满足" if val is True else ("不满足" if val is False else "待证实")
        triggers.append({"condition": cond["text"], "conclusion": conclusion, "facts": fact_ids(kind), "reason": cond.get("note") or {"满足": "事实与证据支持该条件", "不满足": "事实不支持该条件", "待证实": "关键证据缺失或相互矛盾"}[conclusion]})
    exclusions = []
    for ex in inputs.get("exclusions", []):
        kind = ex.get("kind")
        val = flags.get(f"ex_{kind}")
        conclusion = "适用" if val is True else ("不适用" if val is False else "待证实")
        reason = {"适用": "事实显示该除外原因是近因或独立造成损失", "不适用": "无证据显示该除外原因存在或成为近因", "待证实": "该除外原因存在，但是否为近因需结合责任认定"}[conclusion]
        if kind == "maintenance" and val is None and flags.get("maintenance_delay"):
            reason = "维保存在推迟，但是否为主要原因需结合因果分析与第三方鉴定判断"
        exclusions.append({"title": ex["title"], "conclusion": conclusion, "facts": fact_ids(f"ex_{kind}", kind), "reason": reason})
    return {"triggers": triggers, "exclusions": exclusions}


@reasoning_task(
    "cl.causal_integrate",
    "TT-05",
    "整合事故因果模板与数据驱动的因果发现结果，形成有向因果图：保留模板骨架，按数据证据调整边强度，可补充有充分证据的边，并标出需要理赔人员确认的关键边。",
    '{"edges": [{"from": "", "to": "", "strength": 0.5, "source": "template|data|both", "key": true, "note": ""}], "summary": ""}',
    required_keys=("edges",),
)
def causal_integrate(inputs: dict) -> dict:
    data_edges = inputs.get("data_edges", {})
    out = []
    for e in inputs.get("template_edges", []):
        k = f"{e['from']}>{e['to']}"
        d = data_edges.get(k)
        if d is not None and d.get("significant"):
            strength = round(0.5 * e["strength"] + 0.5 * min(0.95, max(0.05, abs(d["corr"]))), 3)
            source, note = "both", f"数据支持（滞后 {d['lag']} 小时相关 {d['corr']}，格兰杰 p={d['p']}）"
        elif d is not None:
            strength = round(e["strength"] * 0.85, 3)
            source, note = "template", f"数据未显著支持（格兰杰 p={d['p']}），按模板强度下调"
        else:
            strength = e["strength"]
            source, note = "template", "无对应数据，采用模板强度"
        out.append({"from": e["from"], "to": e["to"], "strength": strength, "source": source, "key": e["from"] in inputs.get("behavior_nodes", []) or strength >= 0.6, "note": note})
    return {"edges": out, "summary": f"因果图 {len(out)} 条边，其中数据支持 {sum(1 for e in out if e['source'] == 'both')} 条"}


@reasoning_task(
    "cl.liability",
    "TT-05",
    "责任认定说理：依据因果链、第三方报告与条款核查结论，按近因原则判断近因是否为承保风险、是否有除外原因介入成为近因；多方事故说明各方原因力、过错程度与建议比例的依据。所有陈述引用事实编号（F-n）。",
    '{"proximate_cause": "", "chain": [""], "insured_event": true, "confidence": 0.8, "reasoning": [{"text": "", "facts": ["F-1"]}]}',
    required_keys=("proximate_cause", "insured_event", "reasoning"),
)
def liability(inputs: dict) -> dict:
    chain = inputs.get("chain", [])
    flags = inputs.get("flags", {})
    facts = inputs.get("facts", [])
    third = [f for f in facts if "third_party" in f.get("tags", [])]
    excluded_proximate = flags.get("ex_wear") is True or flags.get("ex_intent") is True
    proximate = chain[-2] if len(chain) >= 2 else (chain[0] if chain else "待证实")
    insured = (not excluded_proximate) and flags.get("accidental") is not False
    reasoning = []
    if third:
        reasoning.append({"text": f"第三方报告结论：{third[0]['text'][:120]}", "facts": [third[0]["id"]]})
    if chain:
        reasoning.append({"text": "因果链：" + " → ".join(chain), "facts": [f["id"] for f in facts if "timeline" in f.get("tags", [])][:3]})
    if flags.get("maintenance_delay"):
        direct = chain[-2] if len(chain) >= 2 else "设备故障"
        reasoning.append({"text": f"维保推迟是{chain[1] if len(chain) > 2 else '部件劣化'}的促成因素，但损失的直接原因是{direct}，属于突然发生的事故；维保推迟{'已' if flags.get('ex_maintenance') else '不'}构成独立造成损失的近因，其作用按原因力在各方之间分摊。", "facts": [f["id"] for f in facts if "maintenance" in f.get("tags", [])][:2]})
    shares = inputs.get("shares") or {}
    if shares:
        reasoning.append({"text": "原因力分摊（Shapley 值）与过错调整后建议比例：" + "、".join(f"{k} {v:.0%}" for k, v in shares.items()), "facts": [f["id"] for f in facts][:1]})
    conf = 0.85 if third else 0.65
    return {"proximate_cause": proximate, "chain": chain, "insured_event": bool(insured), "confidence": conf, "reasoning": reasoning}


@reasoning_task(
    "cl.opinion",
    "TT-06",
    "撰写理赔意见：案件概述、条款核查结论、责任认定、定损结论、理算过程摘要、赔付建议与代位求偿建议；如有拒赔或部分拒赔，单独写明依据的条款与事实。措辞客观、规范。",
    '{"paragraphs": [""], "rejection": ""}',
    required_keys=("paragraphs",),
)
def opinion(inputs: dict) -> dict:
    adj = inputs.get("adjustment", {})
    paras = [
        f"一、案件概述：{inputs.get('summary', '')}",
        "二、条款核查：" + "；".join(f"{c['item']}——{c['conclusion']}" for c in inputs.get("checks", [])[:8]) + "。",
        f"三、责任认定：近因为{inputs.get('proximate_cause', '待证实')}，{'属于' if inputs.get('insured_event') else '不属于'}保险事故。" + (("多方责任建议比例：" + "、".join(f"{k} {v:.0%}" for k, v in inputs.get("shares", {}).items()) + "。") if inputs.get("shares") else ""),
        f"四、定损结论：申报损失 {format_amount(inputs.get('claimed', 0))}，核定损失 {format_amount(adj.get('assessed', 0))}。",
        "五、理算过程：" + "；".join(f"{s['step']} {format_amount(s['amount'])}" for s in adj.get("steps", [])) + "。",
        f"六、赔付建议：建议赔付 {format_amount(adj.get('payout', 0))}" + (f"；建议向责任方代位求偿 {format_amount(adj.get('subrogation', 0))}" if adj.get("subrogation") else "") + f"；需 {inputs.get('approval_level', 1)} 级审批。",
    ]
    rejection = ""
    if inputs.get("rejected_items"):
        rejection = "部分拒赔说明：" + "；".join(f"{r['item']}（{r['reason']}）" for r in inputs["rejected_items"]) + "。"
        paras.append("七、" + rejection)
    return {"paragraphs": paras, "rejection": rejection}


@reasoning_task(
    "cl.review",
    "TT-08",
    "评审理赔说理质量（1–5 分）：责任认定与拒赔说明是否依据充分（引用事实与条款）、表述是否规范。",
    '{"scores": {"reasoning_quality": {"score": 4, "reason": ""}}}',
    required_keys=("scores",),
)
def review(inputs: dict) -> dict:
    rs = inputs.get("reasoning", [])
    grounded = sum(1 for r in rs if r.get("facts")) / len(rs) if rs else 0
    has_clause = any("条" in p or "条款" in p for p in inputs.get("paragraphs", []))
    has_calc = any("理算" in p for p in inputs.get("paragraphs", []))
    score = 1 + 4 * (0.5 * grounded + 0.25 * has_clause + 0.25 * has_calc)
    return {"scores": {"reasoning_quality": {"score": round(score, 2), "reason": f"有事实依据的论证占比 {grounded:.0%}，{'引用' if has_clause else '未引用'}条款，{'含' if has_calc else '缺少'}理算过程"}}}
