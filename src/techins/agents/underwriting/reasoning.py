"""承保风控智能体的推理任务。"""

from __future__ import annotations

import re

from ...analytics import vision
from ...llm.tasks import reasoning_task
from ...textutil import format_amount, parse_amounts

KV = re.compile(r"([\u4e00-\u9fa5A-Za-z（）()]{2,14})[：:]\s*([^；;\n]+)")
DOC_KINDS = {"投保单": "application", "营业执照": "license", "财务": "financial", "审计": "financial", "设备清单": "equipment_list", "发票": "invoices", "测评": "security", "安全": "security", "社保": "social", "知识产权": "ip", "产品": "product"}


def classify_doc(name: str, text: str) -> str:
    for key, kind in DOC_KINDS.items():
        if key in name:
            return kind
    for key, kind in DOC_KINDS.items():
        if key in text[:80]:
            return kind
    return "other"


def parse_table(text: str) -> list[dict]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header_idx = next((i for i, l in enumerate(lines) if "名称" in l and ("型号" in l or "序列号" in l)), None)
    if header_idx is None:
        return []
    headers = re.split(r"\s*[|｜]\s*|\s{2,}|\t", lines[header_idx].strip("|｜ "))
    rows = []
    for l in lines[header_idx + 1 :]:
        cells = re.split(r"\s*[|｜]\s*|\s{2,}|\t", l.strip("|｜ "))
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


@reasoning_task(
    "uw.extract_elements",
    "TT-03",
    "从投保材料中抽取风险要素：识别材料类型（投保单、营业执照、财务报表、设备清单、购置发票、安全测评报告等），抽取键值要素与表格行，给出置信度。",
    '{"kind": "application|license|financial|equipment_list|invoices|security|social|other", "fields": {"字段": "值"}, "rows": [{"列": "值"}], "confidence": 0.9}',
    required_keys=("kind", "fields"),
)
def extract_elements(inputs: dict) -> dict:
    text = inputs.get("text", "")
    kind = classify_doc(inputs.get("name", ""), text)
    fields = {k.strip(): v.strip() for k, v in KV.findall(text)}
    rows = parse_table(text)
    conf = 0.95 if fields or rows else 0.4
    return {"kind": kind, "fields": fields, "rows": rows, "confidence": conf}


@reasoning_task(
    "uw.image_elements",
    "TT-10",
    "识别投保照片：设备类型、铭牌上的型号与序列号、拍摄场所的安全隐患（消防设施、线路、易燃物、积水等）、可见损伤。",
    '{"equipment": "", "model": "", "serial": "", "hazards": [""], "damages": [""], "confidence": 0.8}',
    required_keys=("hazards",),
)
def image_elements(inputs: dict) -> dict:
    meta = vision.metadata(vision.load(inputs["path"]))
    dmg = vision.assess_damage(vision.load(inputs["path"]))
    hazards = []
    if dmg["features"]["rust_ratio"] > 0.05:
        hazards.append("设备表面存在明显锈蚀，维护状况不佳")
    if dmg["features"]["water_ratio"] > 0.08:
        hazards.append("场所存在积水或水渍痕迹")
    if dmg["features"]["burn_ratio"] > 0.03:
        hazards.append("存在烧蚀或过热痕迹")
    return {
        "equipment": meta.get("equipment", ""),
        "model": meta.get("model", ""),
        "serial": meta.get("serial", ""),
        "captured_at": meta.get("captured_at") or meta.get("DateTimeOriginal", ""),
        "hazards": hazards,
        "damages": [f"{d['type']}（{d['grade']}）" for d in dmg["damages"]],
        "confidence": 0.9 if meta.get("serial") else 0.6,
    }


@reasoning_task(
    "uw.background",
    "TT-05",
    "结合财务指标、经营稳定性、司法与处罚记录、行业景气，研判投保企业的经营与行业背景，列出主要关注点。",
    '{"summary": "", "concerns": [""], "positives": [""]}',
    required_keys=("summary", "concerns"),
)
def background(inputs: dict) -> dict:
    f = inputs.get("indicators", {})
    concerns, positives = [], []
    if f.get("net_profit", 0) < 0:
        concerns.append("企业仍处于亏损状态")
    if f.get("cash_months", 99) < 12:
        concerns.append(f"现金储备仅可支撑约 {f.get('cash_months'):.1f} 个月固定支出")
    if f.get("litigation", 0):
        concerns.append(f"存在 {f['litigation']} 项诉讼或法律事件")
    if f.get("social_security_ratio", 1) < 0.3:
        concerns.append("参保人数与员工规模明显不匹配")
    if f.get("rd_intensity", 0) > 0.15:
        positives.append(f"研发投入强度 {f['rd_intensity']:.0%}，技术投入持续")
    if f.get("operating_cash_flow", 0) > 0:
        positives.append("经营现金流为正")
    return {"summary": f"{inputs.get('name')}属于{inputs.get('track')}赛道，营业收入约 {format_amount(f.get('revenue', 0))}。" + ("主要关注：" + "；".join(concerns) + "。" if concerns else "未发现重大经营异常。"), "concerns": concerns, "positives": positives}


@reasoning_task(
    "uw.opinion",
    "TT-06",
    "起草核保意见书：风险概述、评分结论、欺诈研判、决策建议与附加条件、需补充材料，措辞客观规范。",
    '{"title": "", "paragraphs": [""], "conditions": [""]}',
    required_keys=("paragraphs",),
)
def opinion(inputs: dict) -> dict:
    d = inputs["decision"]
    sc = inputs["scorecard"]
    paras = [
        f"一、投保概况：{inputs['enterprise']}投保{inputs['product']}，保险金额 {format_amount(inputs['sum_insured'])}，保险期间 12 个月。",
        f"二、风险评分：总分 {sc['total']:.1f}（{sc['level']}），其中" + "、".join(f"{s['name']} {s['score']:.0f}" for s in sc["subs"]) + "。",
        f"三、欺诈研判：融合欺诈概率 {inputs['fraud']['probability']:.1%}" + ("，命中可疑模式：" + "、".join(f"{h['pattern']} {h['rule']}" for h in inputs["fraud"]["rule_hits"]) if inputs["fraud"]["rule_hits"] else "，未命中可疑模式") + "。",
        f"四、核保意见：建议{d['label']}。理由：{'；'.join(d['reasons'])}。",
    ]
    if d.get("conditions"):
        paras.append("五、附加条件：" + "；".join(c["text"] for c in d["conditions"]) + f"。调整后保费 {format_amount(d['premium'])}。")
    if inputs.get("supplements"):
        paras.append("六、需补充材料：" + "、".join(inputs["supplements"]) + "。")
    return {"title": f"{inputs['enterprise']}核保意见书", "paragraphs": paras, "conditions": [c["text"] for c in d.get("conditions", [])]}


@reasoning_task(
    "uw.review",
    "TT-08",
    "评审核保意见书质量：说理是否充分、条件是否明确、语言是否规范（1–5 分）。",
    '{"scores": {"opinion_quality": {"score": 4, "reason": ""}}}',
    required_keys=("scores",),
)
def review(inputs: dict) -> dict:
    paras = inputs.get("paragraphs", [])
    has_reason = any("理由" in p for p in paras)
    has_score = any("风险评分" in p for p in paras)
    cond_ok = not inputs.get("needs_conditions") or any("附加条件" in p for p in paras)
    score = 1 + 4 * (has_reason + has_score + cond_ok) / 3
    return {"scores": {"opinion_quality": {"score": round(score, 2), "reason": f"{'含' if has_reason else '缺少'}决策理由、{'含' if has_score else '缺少'}评分结论、附加条件{'明确' if cond_ok else '缺失'}"}}}


def amount_of(text: str) -> float | None:
    vals = parse_amounts(text or "")
    return vals[0] if vals else None
