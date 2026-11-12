"""精算定价智能体的推理任务。"""

from __future__ import annotations

from ...analytics.pricing import TEXT_FACTOR_LINES, parse_text_factors
from ...knowledge.catalog import FACTOR_DICTIONARY, SUBTYPES
from ...llm.tasks import reasoning_task
from ...textutil import format_amount

FACTOR_INDEX = {f["key"]: f for f in FACTOR_DICTIONARY}
RISK_TO_FACTOR = {
    "R01": ["trl", "rd_intensity", "cash_months"],
    "R02": ["equipment_age", "equipment_concentration", "maintenance_score"],
    "R03": ["team_stability"],
    "R04": ["patent_quality", "litigation_exposure"],
    "R05": ["trl", "cash_months"],
    "R06": ["quality_cert", "negative_sentiment"],
    "R07": ["security_level", "data_scale", "security_incidents"],
    "R08": ["supplier_concentration"],
    "R10": ["cash_months"],
}


@reasoning_task(
    "ap.factor_proposals",
    "TT-05",
    "根据风险评估结论与条款责任结构，为某一险种提出候选风险因子：定义、度量方式、取值类型、预期方向、数据来源（结构化或需从文本提取）与理由。只能从可用因子中选择。",
    '{"factors": [{"key": "", "rationale": "", "expected_direction": 1}]}',
    required_keys=("factors",),
)
def factor_proposals(inputs: dict) -> dict:
    available = set(inputs.get("available", []))
    risk_types = [r["subtype"].split("-")[0] for r in inputs.get("risks", [])]
    picked = []
    for t in risk_types:
        for key in RISK_TO_FACTOR.get(t, []):
            if key in available and key not in [p["key"] for p in picked]:
                related = [r["title"] for r in inputs.get("risks", []) if r["subtype"].startswith(t)]
                picked.append({"key": key, "rationale": f"度量{('、'.join(related[:2]) or SUBTYPES.get(t + '-1', {}).get('type_name', t))}的暴露程度", "expected_direction": FACTOR_INDEX.get(key, {}).get("direction", 0)})
    for key in sorted(available):
        if key not in [p["key"] for p in picked]:
            picked.append({"key": key, "rationale": "险种常用结构化因子", "expected_direction": FACTOR_INDEX.get(key, {}).get("direction", 0)})
    return {"factors": picked}


@reasoning_task(
    "ap.extract_factor_values",
    "TT-03",
    "从每段尽调摘要文本中抽取指定风险因子的取值与置信度：team_stability = 1 − 离职人数/核心团队人数；supplier_concentration 为第一大供应商采购占比（0–1）；maintenance_score 为维保规范度（0–1，按期执行接近 1，多次延期接近 0.4）；negative_sentiment 为负面舆情指数（0–1）；security_incidents 为安全事件数；trl 为技术成熟度等级。无法判断时取值为 null。",
    '{"rows": [{"id": "", "values": {"team_stability": 0.9}, "confidence": {"team_stability": 0.9}}]}',
    required_keys=("rows",),
)
def extract_factor_values(inputs: dict) -> dict:
    keys = inputs.get("factors", [])
    rows = []
    for item in inputs.get("texts", []):
        parsed = parse_text_factors(item["text"])
        rows.append({"id": item["id"], "values": {k: parsed[k][0] for k in keys}, "confidence": {k: parsed[k][1] for k in keys}})
    return {"rows": rows}


@reasoning_task(
    "ap.method_diff",
    "TT-05",
    "分析不同定价方法结果差异的原因（数据稀疏分组、交互效应、基准偏差、先验偏保守等），并给出合成方式建议。",
    '{"analysis": "", "weights": {"PM1": 0.5}}',
    required_keys=("analysis", "weights"),
)
def method_diff(inputs: dict) -> dict:
    results = inputs.get("results", {})
    methods = [m for m in results if results[m] is not None]
    if not methods:
        return {"analysis": "无可比较的方法结果", "weights": {}}
    vals = {m: results[m] for m in methods}
    hi = max(vals, key=vals.get)
    lo = min(vals, key=vals.get)
    spread = (vals[hi] - vals[lo]) / max(vals[lo], 1e-9)
    reasons = []
    if "PM4" in vals and abs(vals["PM4"] - sum(vals.values()) / len(vals)) > 0.15 * vals["PM4"]:
        reasons.append("风险成本加成法基于风险等级映射的先验频率与强度，较经验数据偏保守")
    if "PM2" in vals and "PM1" in vals and abs(vals["PM2"] - vals["PM1"]) / vals["PM1"] > 0.1:
        reasons.append("梯度提升树捕捉到了广义线性模型未表达的非线性与交互效应")
    if "PM3" in vals:
        reasons.append("信度加权把自有经验向行业基准收缩，经验越少收缩越强")
    weights = inputs.get("default_weights", {})
    total = sum(weights.get(m, 0) for m in methods) or len(methods)
    w = {m: round((weights.get(m, 1.0 if not weights else 0) or 0) / total, 3) for m in methods}
    return {"analysis": f"方法间最大差异 {spread * 100:.1f}%（最高 {hi}、最低 {lo}）。" + "；".join(reasons), "weights": w}


@reasoning_task(
    "ap.experiment_insight",
    "TT-05",
    "解读一轮 A/B 实验结果：胜出变体是否显著、约束是否满足、是否需要修改条款，以及下一轮调整方向。",
    '{"conclusion": "", "next_direction": "", "needs_clause_change": false}',
    required_keys=("conclusion", "next_direction"),
)
def experiment_insight(inputs: dict) -> dict:
    winner = inputs.get("winner")
    if not winner:
        return {"conclusion": "本轮没有变体显著优于对照组，维持对照方案。", "next_direction": "缩小调整幅度或换用其他维度。", "needs_clause_change": False}
    w = next(v for v in inputs["variants"] if v["key"] == winner)
    clause = bool(w.get("clause_changes"))
    return {
        "conclusion": f"变体「{w['label']}」以 {w['stats']['prob_better'] * 100:.1f}% 的概率优于对照组，预期利润提升 {format_amount(w['stats']['mean_diff'])}，约束全部满足。",
        "next_direction": "在胜出变体基础上微调费率并检验免赔额的边际效应" if inputs.get("round", 1) < inputs.get("max_rounds", 3) else "实验结束，以胜出变体作为定价依据",
        "needs_clause_change": clause,
    }


@reasoning_task(
    "ap.report",
    "TT-06",
    "撰写精算报告：定价目标、数据说明、因子说明、方法与结果、情景测试、A/B 迭代记录、费率表说明、充足性与合理性论证、假设与局限。",
    '{"sections": [{"title": "", "content": ""}], "conclusion": ""}',
    required_keys=("sections", "conclusion"),
)
def report(inputs: dict) -> dict:
    comps = inputs.get("components", [])
    sections = [
        {"title": "一、定价目标", "content": f"目标赔付率 {inputs['targets']['loss_ratio'][0]:.0%}–{inputs['targets']['loss_ratio'][1]:.0%}，费用率 {inputs['targets']['expense_ratio']:.0%}，利润率 {inputs['targets']['profit_ratio']:.0%}，综合成本率上限 {inputs['targets']['combined_cap']:.0%}。"},
        {"title": "二、数据说明", "content": "；".join(f"{c['name']}：{c.get('data_note', '无经验数据，采用冷启动方法')}" for c in comps)},
        {"title": "三、风险因子", "content": "；".join(f"{c['name']}入模因子 {len(c.get('factors', []))} 个（其中文本提取 {c.get('text_factors', 0)} 个）" for c in comps)},
        {"title": "四、定价方法与结果", "content": "；".join(f"{c['name']}采用{'、'.join(c.get('methods', []))}，建议毛费率 {c.get('gross_rate', 0) * 100:.3f}%（区间 {c.get('band', [0, 0])[0] * 100:.3f}%–{c.get('band', [0, 0])[1] * 100:.3f}%）" for c in comps)},
        {"title": "五、情景测试", "content": "；".join(f"{s['name']}情景下平均赔付率 {s['loss_ratio']['mean']:.1%}，亏损概率 {s['loss_probability']:.1%}" for s in inputs.get("scenarios", []))},
        {"title": "六、A/B 迭代", "content": "；".join(f"第 {r['round']} 轮：{r['insight']['conclusion']}" for r in inputs.get("experiments", []))},
        {"title": "七、充足性与合理性", "content": inputs.get("adequacy", "")},
        {"title": "八、假设与局限", "content": "；".join(inputs.get("limitations", []))},
    ]
    return {"sections": sections, "conclusion": f"共 {len(comps)} 个定价组件，基准情景下预期综合成本率 {inputs.get('combined', 0):.1%}，费率落在报备浮动区间内。"}


@reasoning_task(
    "ap.review",
    "TT-08",
    "评审精算报告的说理质量：假设披露是否充分、结论是否有依据（1–5 分）。",
    '{"scores": {"report_quality": {"score": 4, "reason": ""}}}',
    required_keys=("scores",),
)
def review(inputs: dict) -> dict:
    sections = inputs.get("sections", [])
    filled = sum(1 for s in sections if len(s.get("content", "")) >= 20)
    limitations = next((s for s in sections if "假设" in s["title"]), {"content": ""})
    score = 1 + 4 * (filled / max(1, len(sections))) * (1.0 if len(limitations["content"]) >= 20 else 0.8)
    return {"scores": {"report_quality": {"score": round(score, 2), "reason": f"{filled}/{len(sections)} 个章节有实质内容，假设披露{'充分' if len(limitations['content']) >= 20 else '不足'}"}}}


def text_factor_keys(line: str) -> list[str]:
    return TEXT_FACTOR_LINES.get(line, [])
