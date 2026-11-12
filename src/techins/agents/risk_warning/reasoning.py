"""风险预警智能体的推理任务（内置推理引擎实现 + 大模型任务说明）。"""

from __future__ import annotations

import re

from ...knowledge.catalog import DETECTION_METHODS, INCIDENT_TYPES, NEGATION_CUES, PLAYBOOKS, SEVERITY_CUES, SUBTYPES
from ...llm.tasks import reasoning_task
from ...textutil import format_amount

EVENT_KEYWORDS: dict[str, tuple[str, float, str]] = {
    "火灾": ("R02-1", 0.8, "实验室火灾"),
    "起火": ("R02-1", 0.8, "实验室火灾"),
    "水浸": ("R02-1", 0.7, "实验室水浸"),
    "停产": ("R08-3", 0.75, "停产"),
    "离职": ("R03-1", 0.5, "核心人员离职"),
    "辞任": ("R03-1", 0.5, "核心人员离职"),
    "起诉": ("R04-1", 0.6, "知识产权诉讼"),
    "诉讼": ("R04-1", 0.55, "诉讼"),
    "无效宣告": ("R04-2", 0.55, "专利无效请求"),
    "召回": ("R06-3", 0.7, "产品召回"),
    "攻击": ("R07-2", 0.7, "网络攻击"),
    "勒索": ("R07-2", 0.85, "勒索攻击"),
    "泄露": ("R07-1", 0.8, "数据泄露"),
    "断供": ("R08-2", 0.75, "关键部件断供"),
    "出口管制": ("R08-2", 0.7, "出口管制"),
    "交期延长": ("R08-2", 0.5, "交期延长"),
    "处罚": ("R09-3", 0.5, "行政处罚"),
    "漏洞": ("R07-2", 0.55, "高危漏洞披露"),
}


def _evidence_ids(text: str) -> list[str]:
    return re.findall(r"EV-\d+", text)


@reasoning_task(
    "rw.plan_rationale",
    "TT-05",
    "为保单监测方案中的每个关键风险指标说明监测要点（与哪项风险、哪项保险责任相关，为什么选这些检测方法），并指出中高风险中尚未被任何指标覆盖的缺口及补充建议。",
    '{"rationales": {"指标编号": "说明"}, "gaps": [{"risk": "风险名称", "suggestion": "补充建议"}]}',
    required_keys=("rationales", "gaps"),
)
def plan_rationale(inputs: dict) -> dict:
    risks = inputs.get("risks", [])
    rationales = {}
    covered_subtypes: set[str] = set()
    for ind in inputs.get("indicators", []):
        methods = "、".join(f"{m} {DETECTION_METHODS.get(m, m)}" for m in ind.get("methods", []))
        linked = [r for r in risks if r.get("subtype") in (ind.get("subtypes") or [])]
        covered_subtypes |= set(ind.get("subtypes") or [])
        risk_text = "、".join(f"{r['title']}（{r['level']}）" for r in linked[:3])
        basis = {"template": "产品监测模板", "uw": "核保监测要点", "risk": "风险评估中高风险", "external": "外部数据监测", "degraded": "授权缺失后的外部替代"}.get(ind.get("origin"), "监测模板")
        rationales[ind["id"]] = (
            f"{ind['name']}对应{ind.get('liability') or '风险管理'}；来源于{basis}"
            + (f"，用于提前发现{risk_text}" if risk_text else "")
            + f"。采用{methods}，"
            + ("单点越限与趋势突变分别由阈值与变点方法捕捉，组合判定降低误报。" if len(ind.get("methods", [])) > 1 else "按规则阈值判定。")
        )
    gaps = []
    for r in risks:
        if r.get("level") in ("高", "极高") and r.get("subtype") not in covered_subtypes:
            sub = SUBTYPES.get(r.get("subtype"), {})
            gaps.append({"risk": r["title"], "suggestion": f"建议增加{sub.get('name', r['title'])}相关的事件监测（舆情、司法、公告事件抽取），并在客户授权后接入对应运营数据"})
    return {"rationales": rationales, "gaps": gaps}


@reasoning_task(
    "rw.extract_event",
    "TT-02",
    "从新闻、公告、裁判文书等公开信息中抽取风险事件：事件类型、主体、严重度（0–1），判断是否为否定表述，以及与被保险企业保单责任的相关性。",
    '{"event_type": "事件类型或无关", "subtype": "风险子类型编号或空", "subject": "主体", "severity": 0.6, "negated": false, "relevance": "直接相关|间接相关|无直接关联", "summary": ""}',
    required_keys=("event_type", "severity", "negated"),
)
def extract_event(inputs: dict) -> dict:
    text = f"{inputs.get('title', '')}。{inputs.get('content', '')}"
    negated = any(cue in text for cue in NEGATION_CUES)
    hit = None
    for kw, (sub, sev, label) in EVENT_KEYWORDS.items():
        if kw in text:
            if hit is None or sev > hit[1]:
                hit = (sub, sev, label)
    if hit is None:
        return {"event_type": "无关", "subtype": "", "subject": inputs.get("subject", ""), "severity": 0.0, "negated": negated, "relevance": "无直接关联", "summary": "未识别到风险事件"}
    sub, sev, label = hit
    for cue, weight in SEVERITY_CUES.items():
        if cue in text:
            sev = max(sev, min(1.0, 0.5 + weight * 0.45))
    liabilities = inputs.get("liabilities", [])
    sub_name = SUBTYPES.get(sub, {}).get("name", label)
    lines = SUBTYPES.get(sub, {}).get("lines", [])
    direct = any(l in (inputs.get("lines") or []) for l in lines)
    relevance = "直接相关" if direct else ("间接相关" if liabilities else "无直接关联")
    if inputs.get("subject") and inputs.get("subject") != inputs.get("insured"):
        relevance = "间接相关" if relevance == "直接相关" else relevance
    return {"event_type": label, "subtype": sub, "subject": inputs.get("subject", ""), "severity": round(sev, 2), "negated": negated, "relevance": relevance, "summary": f"{inputs.get('subject') or '企业'}发生{label}（{sub_name}），严重度 {sev:.2f}"}


ACTION_LIBRARY = {
    "R02": [
        ("联系客户设备负责人核实设备运行状态与近期维保记录", 1, "产品服务人员"),
        ("建议客户安排停机检查轴承与润滑状态，必要时降低负载运行", 4, "客户设备负责人"),
        ("安排第三方振动诊断等风险减量服务", 24, "产品服务人员"),
        ("将该设备监测频率提高到每 10 分钟一次", 1, "平台"),
    ],
    "R07": [
        ("联系客户安全负责人核实登录失败与外联异常是否为已知运维操作", 1, "产品服务人员"),
        ("建议客户立即封禁异常来源地址、重置相关账号凭证并启用多因素认证", 2, "客户安全团队"),
        ("建议客户在 72 小时内修补高危漏洞，修补后提交复测报告", 72, "客户安全团队"),
        ("安排渗透测试或安全巡检服务", 48, "产品服务人员"),
    ],
    "R01": [
        ("联系客户研发负责人核实里程碑延误原因与恢复计划", 24, "客户经理"),
        ("评估延误是否由约定的意外事故引起，提示保留中断期间费用凭证", 48, "产品服务人员"),
    ],
    "R08": [
        ("联系客户采购负责人核实关键部件库存可支撑天数与替代供应商", 4, "客户经理"),
        ("建议客户启动备选供应商验证并提前锁定在途订单", 48, "客户采购负责人"),
        ("评估对研发中断责任的潜在影响并通知核保", 24, "产品服务人员"),
    ],
    "R04": [
        ("联系客户法务核实诉讼或无效请求的进展", 24, "客户经理"),
        ("提示客户保全证据并在规定时间内通知保险人", 24, "产品服务人员"),
    ],
    "R06": [
        ("联系客户质量负责人核实投诉与抽检情况", 4, "客户经理"),
        ("建议对相关批次暂停发货并封存样品", 24, "客户质量负责人"),
    ],
}


@reasoning_task(
    "rw.alert_analysis",
    "TT-05",
    "对预警候选事件做研判：解释事件、分析可能原因、给出具体可执行的建议动作（含时限与负责方）。每一条陈述与原因都必须引用输入中的证据编号（EV-n），没有证据支持的内容不要写。",
    '{"explanation": "", "causes": [{"text": "", "evidence": ["EV-1"]}], "actions": [{"action": "", "deadline_hours": 4, "owner": "", "evidence": ["EV-1"]}], "statements": [{"text": "", "evidence": ["EV-1"]}]}',
    required_keys=("explanation", "causes", "actions"),
)
def alert_analysis(inputs: dict) -> dict:
    evidence = inputs.get("evidence", [])
    ids = [e["id"] for e in evidence]
    impact = inputs.get("impact", {})
    rt = inputs.get("risk_type", "")
    type_code = rt[:3]
    level_label = {"red": "红色", "orange": "橙色", "yellow": "黄色", "blue": "蓝色"}.get(inputs.get("level"), "")
    statements = []
    causes = []
    for e in evidence:
        statements.append({"text": f"{e['title']}：{e['detail']}", "evidence": [e["id"]]})
    metric_ev = [e for e in evidence if e.get("kind") == "metric"]
    event_ev = [e for e in evidence if e.get("kind") == "event"]
    if type_code == "R02":
        rising = [e for e in metric_ev if "上升" in e["detail"] or "健康指数" in e["detail"] or "超过" in e["detail"]]
        if rising:
            causes.append({"text": "振动与温度同步上升、健康指数持续下降，符合轴承磨损或润滑不良导致的渐进性退化特征", "evidence": [e["id"] for e in rising]})
        if any("停机" in e["detail"] for e in metric_ev):
            causes.append({"text": "设备在高振动、高温后停机，疑似发生机械故障", "evidence": [e["id"] for e in metric_ev if "停机" in e["detail"]]})
    elif type_code == "R07":
        if metric_ev:
            causes.append({"text": "登录失败激增后伴随数据外传量异常，符合暴力破解后数据外传的攻击链特征", "evidence": [e["id"] for e in metric_ev]})
        if any("漏洞" in e["detail"] for e in evidence):
            causes.append({"text": "存在未修补的高危漏洞，扩大了被利用的可能性", "evidence": [e["id"] for e in evidence if "漏洞" in e["detail"]]})
    elif type_code == "R08":
        causes.append({"text": "关键供应商发生经营事件，经供应依赖关系传导至被保险企业", "evidence": [e["id"] for e in event_ev] or ids[:1]})
    elif type_code == "R01":
        causes.append({"text": "研发里程碑延误持续扩大，研发进度偏离计划", "evidence": [e["id"] for e in metric_ev] or ids[:1]})
    else:
        causes.append({"text": "外部事件显示该风险正在显现", "evidence": [e["id"] for e in event_ev] or ids[:1]})
    if not causes and ids:
        causes.append({"text": "多个检测方法同时提示异常", "evidence": ids})
    actions = []
    for action, hours, owner in ACTION_LIBRARY.get(type_code, [("联系客户核实事件情况", 4, "客户经理")]):
        actions.append({"action": action, "deadline_hours": hours, "owner": owner, "evidence": ids[:2]})
    if inputs.get("incident"):
        actions.insert(0, {"action": "判定为疑似事故，建议立即启动事故响应并指导客户减损", "deadline_hours": 1, "owner": "产品服务人员", "evidence": ids})
    loss = impact.get("loss_range")
    explanation = (
        f"{inputs.get('asset_name', '')}出现{inputs.get('risk_name', '风险')}信号，融合置信度 {inputs.get('confidence', 0):.0%}，定为{level_label}预警。"
        + (f"与保单「{'、'.join(impact.get('liabilities', []))}」{impact.get('relation', '')}，" if impact.get("liabilities") else "与保单责任无直接关联，")
        + (f"潜在损失区间 {format_amount(loss[0])}–{format_amount(loss[1])}。" if loss else "")
    )
    return {"explanation": explanation, "causes": causes, "actions": actions, "statements": statements}


@reasoning_task(
    "rw.incident_triage",
    "TT-05",
    "事故初判：根据事故描述与关联预警判定事故类型（从给定类型中选择）、可能涉及的保险责任、紧急程度，并用一句话概述。",
    '{"incident_type": "data_breach|equipment_damage|lab_fire|product_liability|ip_infringement", "liabilities": [""], "urgency": "紧急|较急|一般", "summary": ""}',
    required_keys=("incident_type", "liabilities", "urgency"),
)
def incident_triage(inputs: dict) -> dict:
    text = f"{inputs.get('description', '')} {inputs.get('alert_detail', '')}"
    rt = inputs.get("risk_type", "") or ""
    scores = {}
    for key, spec in INCIDENT_TYPES.items():
        score = sum(2 if kw in text else 0 for kw in spec["keywords"])
        score += 3 if set(spec["lines"]) & set(inputs.get("lines", [])) else 0
        if (rt.startswith("R07") and key == "data_breach") or (rt.startswith("R02") and key == "equipment_damage"):
            score += 4
        scores[key] = score
    best = max(scores, key=lambda k: scores[k])
    spec = INCIDENT_TYPES[best]
    covered = [l for l in inputs.get("coverage_liabilities", []) if any(x[:4] in l for x in spec["liabilities"])]
    return {"incident_type": best, "liabilities": covered or spec["liabilities"], "urgency": spec["urgency"], "summary": f"初判为{PLAYBOOKS[best]['name']}，可能涉及{'、'.join(covered or spec['liabilities'])}，紧急程度：{spec['urgency']}"}


@reasoning_task(
    "rw.response_guidance",
    "TT-06",
    "根据应急预案步骤、减损措施与企业情况，撰写给客户的事故减损指导（分段、具体、带时限），以及一段对外通知措辞。",
    '{"guidance": ["段落"], "customer_notice": ""}',
    required_keys=("guidance", "customer_notice"),
)
def response_guidance(inputs: dict) -> dict:
    steps = inputs.get("steps", [])
    paras = [f"{inputs.get('enterprise', '贵司')}：经初步研判，本次事件为{inputs.get('incident_name', '')}，请按以下步骤处置。"]
    for i, st in enumerate(steps, 1):
        paras.append(f"{i}. {st['title']}（{st['deadline_hours']} 小时内，负责：{st['owner']}）。")
    if inputs.get("mitigation"):
        paras.append("减损建议：" + "；".join(f"{m['measure']}（预期效果：{m['effect']}）" for m in inputs["mitigation"]) + "。")
    if inputs.get("evidence"):
        paras.append("请注意保全以下证据，未经保险人同意不要清理现场或处置受损部件：" + "、".join(e["item"] for e in inputs["evidence"]) + "。")
    notice = f"尊敬的客户：我们已收到{inputs.get('incident_name', '事故')}相关信息并启动事故响应，产品服务人员将在 1 小时内与您联系。为保障您的权益，请按减损指导处置并保全证据。"
    return {"guidance": paras, "customer_notice": notice}


REPORT_SECTIONS = [
    ("situation", "风险态势"),
    ("indicators", "指标变化"),
    ("alerts", "预警回顾"),
    ("lifecycle", "生命周期"),
    ("advice", "应对建议"),
    ("cooperation", "需客户配合事项"),
    ("coverage", "保障调整建议"),
]


@reasoning_task(
    "rw.report",
    "TT-06",
    "依据统计与检测结果撰写风险管理报告，固定七个部分：风险态势、指标变化、预警回顾、生命周期、应对建议（按优先级，说明预期效果）、需客户配合事项、保障调整建议。只依据输入中的数字与事件，每部分的 refs 列出引用的指标编号或预警编号。",
    '{"summary": "", "sections": [{"key": "situation|indicators|alerts|lifecycle|advice|cooperation|coverage", "title": "", "paragraphs": [""], "refs": [""]}]}',
    required_keys=("summary", "sections"),
)
def report(inputs: dict) -> dict:
    st = inputs.get("stats", {})
    alerts = st.get("alerts", {})
    idx = st.get("risk_index", {})
    inds = st.get("indicators", [])
    sections = []
    trend = "上升" if idx.get("change", 0) > 3 else ("下降" if idx.get("change", 0) < -3 else "基本平稳")
    sections.append({"key": "situation", "title": "风险态势", "paragraphs": [f"报告期内企业风险指数由 {idx.get('start', 0):.0f} 变为 {idx.get('end', 0):.0f}（满分 100，越高风险越大），整体{trend}；当前风险等级为{idx.get('level', '中')}。"] + [f"{t['name']}：{t['comment']}" for t in st.get("type_changes", [])], "refs": ["risk_index"]})
    ind_paras, ind_refs = [], []
    for i in inds:
        if i.get("points", 0) == 0:
            continue
        ind_refs.append(i["id"])
        ind_paras.append(f"{i['name']}（{i['asset']}）：均值 {i['mean']}，95 分位 {i['p95']}，最新 {i['last']}，日均变化 {i['slope_per_day']:+}" + (f"，异常点 {i['anomalies']} 个" if i.get("anomalies") else "") + (f"，超过阈值 {i['threshold']} 的时长占比 {i['breach_share']:.0%}" if i.get("breach_share") else "") + "。")
    sections.append({"key": "indicators", "title": "指标变化", "paragraphs": ind_paras or ["报告期内无企业授权数据接入，指标监测以外部数据为主。"], "refs": ind_refs})
    by = alerts.get("by_level", {})
    sections.append({"key": "alerts", "title": "预警回顾", "paragraphs": [f"报告期内共发布预警 {alerts.get('total', 0)} 条（红 {by.get('red', 0)}、橙 {by.get('orange', 0)}、黄 {by.get('yellow', 0)}），时限内处置率 {alerts.get('sla_rate', 1):.0%}，误报率 {alerts.get('false_positive_rate', 0):.0%}。"] + [f"{a['alert_no']}（{a['level_label']}）{a['title']}：{a['status_label']}" for a in alerts.get("items", [])[:6]] + ([f"期内发生事故 {st.get('incidents', 0)} 起。"] if st.get("incidents") else []), "refs": [a["alert_no"] for a in alerts.get("items", [])[:6]]})
    lc = st.get("lifecycle", {})
    sections.append({"key": "lifecycle", "title": "生命周期", "paragraphs": [lc.get("comment", "科技产品生命周期子阶段未发生变化。")] + [f"里程碑「{m['name']}」：{m['status']}" for m in lc.get("milestones", [])], "refs": ["lifecycle"]})
    advice = []
    for i, a in enumerate(st.get("advice_candidates", []), 1):
        advice.append(f"{i}. {a['text']}（依据：{a['basis']}；预期效果：{a['effect']}）")
    sections.append({"key": "advice", "title": "应对建议", "paragraphs": advice or ["维持现有风险管控措施，按月复核关键风险指标。"], "refs": [a["ref"] for a in st.get("advice_candidates", [])]})
    coop = st.get("cooperation", [])
    sections.append({"key": "cooperation", "title": "需客户配合事项", "paragraphs": coop or ["请继续保持数据接入稳定，如有设备维保、系统变更等计划请提前告知以设置维护窗口。"], "refs": ["authorizations"]})
    cov = st.get("coverage_gaps", [])
    sections.append({"key": "coverage", "title": "保障调整建议", "paragraphs": cov or ["现有保障与风险结构匹配，暂无调整建议。"], "refs": ["coverage"]})
    summary = f"报告期风险指数 {idx.get('end', 0):.0f}（{trend}），预警 {alerts.get('total', 0)} 条，事故 {st.get('incidents', 0)} 起；首要建议：{(st.get('advice_candidates') or [{'text': '维持现有管控'}])[0]['text']}"
    return {"summary": summary, "sections": sections}


@reasoning_task(
    "rw.review",
    "TT-08",
    "评审预警研判或风险管理报告的质量（1–5 分）：解释是否都有证据或数据依据、建议是否具体可执行（有动作、时限、负责方）。",
    '{"scores": {"analysis_quality": {"score": 4, "reason": ""}}}',
    required_keys=("scores",),
)
def review(inputs: dict) -> dict:
    statements = inputs.get("statements", [])
    actions = inputs.get("actions", [])
    grounded = sum(1 for s in statements if s.get("evidence")) / len(statements) if statements else 0
    specific = sum(1 for a in actions if a.get("deadline_hours") and a.get("owner") and len(a.get("action", "")) >= 8) / len(actions) if actions else 0
    sections_ok = 1.0
    if inputs.get("sections") is not None:
        keys = {s.get("key") for s in inputs["sections"] if s.get("paragraphs")}
        sections_ok = len(keys & {k for k, _ in REPORT_SECTIONS}) / len(REPORT_SECTIONS)
    score = 1 + 4 * (0.45 * grounded + 0.35 * specific + 0.2 * sections_ok)
    return {"scores": {"analysis_quality": {"score": round(score, 2), "reason": f"有依据的陈述占比 {grounded:.0%}，建议动作具体（含时限与负责方）占比 {specific:.0%}" + ("" if inputs.get("sections") is None else f"，报告章节完整度 {sections_ok:.0%}")}}}


__all__ = ["EVENT_KEYWORDS", "REPORT_SECTIONS", "_evidence_ids"]
