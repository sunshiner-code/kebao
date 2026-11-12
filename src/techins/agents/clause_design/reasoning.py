"""条款设计智能体的推理任务。"""

from __future__ import annotations

import re

from ...knowledge.catalog import LINES, SUBTYPES, family_of
from ...knowledge.clause_library import INNOVATION_SKELETONS
from ...llm.tasks import reasoning_task
from ...textutil import truncate

AMBIGUOUS = ["相关", "适当", "一定程度", "原则上", "视情况"]


def _ambiguous() -> list[str]:
    from ...services.config_center import cfg

    return cfg("compliance.rules").get("ambiguous_words") or AMBIGUOUS


ONE_SIDED = [r"保险人有权(单方|随时|拒绝赔偿|不予赔偿|解除合同)", r"以保险人认定为准", r"保险人可以不予"]


@reasoning_task(
    "cd.personalize",
    "TT-06",
    "依据风险情景与企业特征，为某一险种生成个性化的特别约定条款，并对保险责任的触发条件做针对性限定；不得扩大或缩小监管要求的必备内容。",
    '{"special_articles": [{"title": "", "text": ""}], "liability_notes": [{"module": "", "note": ""}], "rationale": ""}',
    required_keys=("special_articles",),
)
def personalize(inputs: dict) -> dict:
    line = inputs["line"]
    name = LINES.get(line, {}).get("name", inputs.get("line_name", ""))
    ent = inputs.get("enterprise", {})
    params = inputs.get("params", {})
    articles = []
    equipment = [e for e in ent.get("equipment", [])][:5]
    products = ent.get("products", [])
    roles = ent.get("roles", [])
    suppliers = ent.get("suppliers", [])
    family = family_of(line)
    if family in ("equipment", "rd_cost") and equipment:
        listing = "；".join(f"{e['name']}（型号 {e['model']}，序列号 {e['serial']}）" for e in equipment)
        articles.append({"title": "列明关键研发设备", "text": f"本保险合同项下列明的关键研发设备为：{listing}。存放地点以保险单载明的地址为准，变更存放地点应书面通知保险人。"})
    if family in ("rd_cost", "transfer"):
        project = products[-1]["name"] if products else "保险单列明的研发或转化项目"
        articles.append({"title": "约定项目与等待期", "text": f"约定项目为「{project}」。等待期为{params.get('waiting_days', 15)}日，赔偿期为{params.get('indemnity_months', 6)}个月。适用地区以保险单载明为准。"})
        if roles and family == "rd_cost":
            articles.append({"title": "列明核心研发人员", "text": f"本保险合同列明的核心研发人员以岗位确定，包括{'、'.join(roles)}，具体人员以投保时提交的人员清单为准。"})
    if family in ("rd_cost", "transfer") and suppliers:
        top = max(suppliers, key=lambda s: s["share"])
        articles.append({"title": "列明关键合作方", "text": f"本保险合同列明的关键合作方为提供{top['item']}的供应商（所在国家：{top['country']}），被保险人应在投保时提交相关合同。"})
    if family == "transfer" and products:
        target = next((p for p in products if 5 <= p.get("trl", 0) <= 7), products[0])
        articles.append({"title": "备案转化方案与关键技术指标", "text": f"被保险人应于投保时向保险人备案「{target['name']}」的成果转化或中试方案，载明关键技术指标、检测方法与约定的第三方鉴定机构。"})
    if family in ("liability", "quality", "life") and products:
        listing = "、".join(p["name"] for p in products)
        articles.append({"title": "被保险产品", "text": f"本保险合同的被保险产品为：{listing}。承保区域为中华人民共和国境内（不含港澳台地区），另有约定的除外。"})
    if family in ("ip", "guarantee"):
        patents = inputs.get("patents", [])
        if patents:
            articles.append({"title": "列明知识产权", "text": "本保险合同列明的知识产权为：" + "；".join(f"{p['title']}（{p['no']}）" for p in patents[:5]) + f"。追溯期自{params.get('retro_date', '保险单载明的日期')}起算。"})
    if family == "cyber":
        articles.append({"title": "列明计算机系统与漏洞修补期限", "text": f"本保险合同列明的计算机系统以投保时提交的资产清单为准。对已公开披露的高危漏洞，被保险人应在{params.get('patch_days', 30)}日内完成修补。利润损失责任的等待期为{params.get('waiting_hours', 12)}小时。"})
    for risk in inputs.get("risks", [])[:3]:
        if risk.get("level") in ("高", "极高"):
            articles.append({"title": f"风险管理约定：{risk['title']}", "text": f"鉴于投保时识别的「{risk['title']}」风险等级为{risk['level']}，被保险人应{risk.get('advice', '采取相应风险管理措施')}，并配合保险人开展风险减量服务。"})
    notes = [{"module": m, "note": f"触发条件按{name}的风险情景限定于列明标的"} for m in inputs.get("liability_modules", [])]
    return {"special_articles": articles, "liability_notes": notes, "rationale": f"依据 {len(inputs.get('risks', []))} 条关联风险与企业特征生成 {len(articles)} 条特别约定"}


@reasoning_task(
    "cd.innovate",
    "TT-06",
    "对没有现成条款覆盖的可保风险，按“保险标的→保险事故→损失形态→赔偿计算→责任免除→赔偿处理”的顺序创造新的条款模块，并列出待验证条款点。",
    '{"modules": [{"kind": "subject|liability|exclusion|amount|settlement|definition", "title": "", "text": ""}], "pending_points": [""], "parametric_trigger": ""}',
    required_keys=("modules",),
)
def innovate(inputs: dict) -> dict:
    spec = inputs["spec"]
    skeleton = INNOVATION_SKELETONS[spec["loss_form"]]
    fill = {"scope": spec["scope"], "trigger": spec["trigger"], "event": spec["event"], "loss": spec["loss"], "evidence": spec["evidence"], "coinsurance": int(round(inputs.get("coinsurance", 0.3) * 100))}
    risk_names = "、".join(r["title"] for r in inputs.get("risks", []))
    modules = [
        {"kind": "subject", "title": "保险标的", "text": skeleton["subject"].format(**fill)},
        {"kind": "liability", "title": f"{spec['event']}保险责任（创新）", "text": skeleton["liability"].format(**fill)},
        {"kind": "exclusion", "title": "创新责任特别免除", "text": "下列情形，保险人不负责赔偿：\n（一）投保前已经存在或被保险人已知的触发事由；\n（二）被保险人故意制造或扩大触发事由；\n（三）因被保险人资金不足、经营决策调整导致的损失；\n（四）触发事由发生后被保险人未采取合理减损措施而扩大的损失部分。"},
        {"kind": "amount", "title": "保险金额与分项限额", "text": skeleton["amount"].format(**fill)},
        {"kind": "settlement", "title": "赔偿处理", "text": skeleton["settlement"].format(**fill) + (f"触发事由以「{spec['parametric']}」为认定依据。" if spec.get("parametric") else "")},
    ]
    pending = [
        f"触发条件「{truncate(spec['trigger'], 40)}」的认定口径需与理赔团队确认",
        "缺乏经验数据，费率采用冷启动方法并在上线后按季度信度更新",
        f"需验证与现有险种在「{risk_names or spec['event']}」上的责任衔接",
    ]
    return {"modules": modules, "pending_points": pending, "parametric_trigger": spec.get("parametric", "")}


@reasoning_task(
    "cd.compliance_review",
    "TT-08",
    "以法规为依据审查条款的公平性、明确性与可读性，列出问题位置、严重程度、法规依据与整改建议。",
    '{"issues": [{"line": "", "doc_name": "", "article": "", "severity": "high|medium|low", "issue": "", "basis": "", "suggestion": ""}]}',
    required_keys=("issues",),
)
def compliance_review(inputs: dict) -> dict:
    issues = []
    regs = inputs.get("regulations", {})
    for doc in inputs.get("docs", []):
        line_ref = doc.get("doc_id") or doc["name"]
        doc_name = doc["name"]
        for ch in doc["chapters"]:
            for art in ch["articles"]:
                text = art["text"]
                for pattern in ONE_SIDED:
                    if re.search(pattern, text):
                        issues.append({"line": line_ref, "doc_name": doc_name, "article": art["no"], "severity": "high", "issue": "存在保险人单方决定权表述，可能加重投保人责任", "basis": regs.get("C04", "保险法第十九条"), "suggestion": "删除单方认定表述或增加客观认定标准"})
                amb = [w for w in _ambiguous() if w in text and ch["key"] == "liability"]
                if amb:
                    issues.append({"line": line_ref, "doc_name": doc_name, "article": art["no"], "severity": "medium", "issue": f"保险责任中出现模糊表述：{'、'.join(amb)}", "basis": regs.get("C11", "条款应文字准确、表述严谨"), "suggestion": "以列举或定义替代模糊词"})
                long_sents = [s for s in re.split(r"[。；]", text) if len(s) > 160]
                if long_sents:
                    issues.append({"line": line_ref, "doc_name": doc_name, "article": art["no"], "severity": "low", "issue": "句子过长影响可读性", "basis": "条款应通俗易懂", "suggestion": "拆分为分项列举"})
    return {"issues": issues}


@reasoning_task(
    "cd.review",
    "TT-08",
    "按评估量表对条款方案打分（1–5 分）：个性化、清晰无歧义、公平性、可赔付性，并说明理由。",
    '{"scores": {"personalization": {"score": 4, "reason": ""}}}',
    required_keys=("scores",),
)
def review(inputs: dict) -> dict:
    docs = inputs.get("docs", [])
    n = max(1, len(docs))
    personalized = sum(1 for d in docs if d.get("special_count", 0) > 0 and d.get("linked_liabilities", 0) > 0)
    amb = sum(d.get("ambiguous", 0) for d in docs)
    one_sided = sum(d.get("one_sided", 0) for d in docs)
    payable = sum(1 for d in docs if d.get("has_formula") and d.get("has_materials") and d.get("has_timeline"))

    def score(r: float) -> float:
        return round(1 + 4 * max(0.0, min(1.0, r)), 2)

    return {
        "scores": {
            "personalization": {"score": score(personalized / n), "reason": f"{personalized}/{n} 个险种含针对企业风险情景的特别约定"},
            "clarity": {"score": score(1 - amb / (5 * n)), "reason": f"保险责任中模糊表述 {amb} 处"},
            "fairness": {"score": score(1 - one_sided / n), "reason": f"单方决定权表述 {one_sided} 处"},
            "payability": {"score": score(payable / n), "reason": f"{payable}/{n} 个险种的赔偿处理包含计算方式、材料要求与时限"},
        }
    }


@reasoning_task(
    "cd.explain",
    "TT-06",
    "为每个险种写面向客户的通俗保障说明（不替代条款）：保什么、不保什么、怎么赔。",
    '{"explanations": [{"line": "", "covers": "", "not_covered": "", "how_to_claim": ""}]}',
    required_keys=("explanations",),
)
def explain(inputs: dict) -> dict:
    out = []
    for doc in inputs.get("docs", []):
        liabs = [a["title"] for ch in doc["chapters"] if ch["key"] == "liability" for a in ch["articles"]]
        excl = [a["title"] for ch in doc["chapters"] if ch["key"] == "exclusion" for a in ch["articles"]]
        out.append(
            {
                "line": doc["doc_id"],
                "name": doc["name"],
                "covers": "本产品主要保障：" + "；".join(liabs) + "。",
                "not_covered": "以下情形不在保障范围内（详见责任免除条款）：" + "；".join(excl) + "。",
                "how_to_claim": "出险后请及时通知保险人并保护现场，按赔偿处理条款提交材料；保险人在法定时限内核定并赔付。",
            }
        )
    return {"explanations": out}


@reasoning_task(
    "cd.recommend_rationale",
    "TT-05",
    "解释险种推荐与组合方式：每个险种覆盖哪些风险、为何选择复用、改造、组合或创新的路径。",
    '{"rationale": [{"line": "", "text": ""}], "summary": ""}',
    required_keys=("rationale", "summary"),
)
def recommend_rationale(inputs: dict) -> dict:
    out = []
    path_label = {"reuse": "复用现有条款", "extend": "在现有条款上扩展", "innovate": "创造新条款", "combine": "组合现有条款"}
    role_label = {"附加险": "附加险", "主险": "主险"}
    for item in inputs.get("lines", []):
        risks = "、".join(r["title"] for r in item.get("risks", []))
        role = role_label.get(item.get("role"), "主险")
        attached = f"，作为「{item['attached_to_name']}」的附加条款" if item.get("attached_to_name") else ""
        out.append({"line": item["line"], "text": f"{item['name']}（{role}{attached}）覆盖{risks or '相关风险'}，路径：{path_label.get(item['path'], item['path'])}，{item.get('reason', '')}"})
    summary = f"推荐 {len(out)} 个险种，组合方式：{inputs.get('structure_label', '')}。"
    return {"rationale": out, "summary": summary}


def subtype_name(code: str) -> str:
    return SUBTYPES.get(code, {}).get("name", code)
