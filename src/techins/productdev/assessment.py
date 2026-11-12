"""从背景和客户需求形成可编辑的风险评估，不计算概率或费率。"""

from __future__ import annotations

ACTIVITIES = [
    {
        "name": "技术活动损失",
        "signals": ["损坏", "受伤", "损失", "支出", "设备", "中断", "失败"],
        "risks": [
            {
                "key": "bodily",
                "signals": ["受伤", "人身", "第三者", "伤害"],
                "event": "活动过程中的约定事件造成人身伤害",
                "affected_parties": ["受影响的第三者"],
                "loss_types": ["人身伤害相关赔偿"],
                "bearers": ["依法应承担责任的组织者"],
                "product_form": "main",
                "product_category": "责任保险",
                "paired_main_category": None,
                "form_reason": "人身伤害赔偿责任可以独立构成责任保险项下的保障",
                "integration_group": "G-liability",
            },
            {
                "key": "property",
                "signals": ["设备", "财产", "损坏", "损毁"],
                "event": "约定事故造成设备或财产损坏",
                "affected_parties": ["财产所有人或使用人"],
                "loss_types": ["财产损失"],
                "bearers": ["承担修复或重置费用的一方"],
                "product_form": "main",
                "product_category": "财产损失保险",
                "paired_main_category": None,
                "form_reason": "物质损失可以独立构成财产损失保险",
                "integration_group": "G-property",
            },
            {
                "key": "extra-expense",
                "signals": ["额外费用", "停工费用", "中断费用"],
                "event": "约定事故导致活动中断并产生额外费用",
                "affected_parties": ["组织活动的一方"],
                "loss_types": ["额外费用"],
                "bearers": ["组织活动的一方"],
                "product_form": "rider",
                "product_category": None,
                "paired_main_category": "财产损失保险",
                "form_reason": "这类费用依赖已承保的财产事故，适合作为财产损失保险的附加责任，不宜单独作为主险",
                "integration_group": "G-property",
            },
        ],
    }
]


def _item(text: str, status: str) -> dict:
    return {"text": text, "status": status}


def clarify(background: str, customer_need: str) -> dict:
    text = f"{background}\n{customer_need}".strip()
    items: list[dict] = []
    questions: list[str] = []
    if customer_need.strip():
        items.append(_item(customer_need.strip(), "明确诉求"))
    if background.strip():
        items.append(_item(background.strip(), "系统推断"))
    ambiguous = any(word in text for word in ("失败", "中断")) and not any(word in text for word in ("事故", "技术结果", "商业收益", "额外费用", "中断费用"))
    if ambiguous:
        items.append(_item("该表述可能指事故或外部事件导致的中断", "待确认事项"))
        items.append(_item("该表述可能指技术结果未达预期", "待确认事项"))
        items.append(_item("该表述可能指商业收益不足。商业收益不足不能写成已经可保的事故损失", "待确认事项"))
        questions.append("这里的失败或中断，是指事故、技术结果未达预期，还是商业收益不足？")
    if "主体" in text and "明确" not in text:
        items.append(_item("受影响主体或损失承担主体仍不清楚", "待确认事项"))
        questions.append("损失由谁承担，受影响的是哪一类主体？")
    return {"demand_items": items, "questions": questions, "text": text}


def _matched(text: str, risk: dict) -> bool:
    return any(signal in text for signal in risk["signals"])


def assess(background: str, customer_need: str, *, pending_kept: bool = False) -> dict:
    clarified = clarify(background, customer_need)
    text = clarified["text"]
    found: list[dict] = []
    for activity in ACTIVITIES:
        if not any(signal in text for signal in activity["signals"]):
            continue
        stated = [risk for risk in activity["risks"] if _matched(text, risk)]
        if not stated:
            stated = [activity["risks"][0]]
        keys = {risk["key"] for risk in stated}
        related = next((risk for risk in activity["risks"] if risk["key"] not in keys), None)
        for risk in stated:
            found.append(_risk(risk, stated_by_user=True, text=text))
        if related is not None:
            found.append(_risk(related, stated_by_user=False, text=text))
    if not found:
        found.append(
            _risk(
                {
                    "key": "unspecified",
                    "event": "描述中的损失事件仍不能界定",
                    "affected_parties": ["待确认"],
                    "loss_types": ["待确认"],
                    "bearers": ["待确认"],
                    "product_form": "main",
                    "product_category": "待确认",
                    "paired_main_category": None,
                    "form_reason": "损失范围还不能界定，不能写成已经具备完整保障条件",
                    "integration_group": None,
                },
                stated_by_user=True,
                text=text,
                undefined=True,
            )
        )
    commercial = any(word in text for word in ("商业收益", "销售收入", "市场份额"))
    if commercial:
        found.append(
            _risk(
                {
                    "key": "commercial",
                    "event": "商业收益不足",
                    "affected_parties": ["经营主体"],
                    "loss_types": ["商业收益"],
                    "bearers": ["经营主体"],
                    "product_form": "main",
                    "product_category": None,
                    "paired_main_category": None,
                    "form_reason": "商业收益不足不属于事故损失，不能按可保事故承保",
                    "integration_group": None,
                },
                stated_by_user=True,
                text=text,
                commercial=True,
            )
        )
    report = _report(clarified, found)
    if pending_kept:
        report["continued_with_pending"] = True
    return report


def _risk(template: dict, *, stated_by_user: bool, text: str, undefined: bool = False, commercial: bool = False) -> dict:
    undefined = undefined or ("主体" in text and "明确" not in text and template.get("key") != "commercial")
    obstacle = []
    conditions = []
    if undefined:
        obstacle.append("损失能否界定")
    else:
        conditions.append("事件和损失可以从现有描述中区分")
    if commercial:
        obstacle.append("不具有可保的偶然事故特征，属于商业结果")
    analysis = {
        "judgment": "证据不足" if undefined or commercial else "初步可保",
        "conditions": conditions,
        "obstacles": obstacle,
        "open_questions": ["需要补充损失范围和证明材料"] if undefined else [],
        "fortuitous": not commercial,
        "verifiable": not undefined,
        "moral_hazard": "中断费用" in text,
    }
    if analysis["moral_hazard"]:
        analysis["obstacles"].append("需要限制费用范围，以控制道德风险")
    return {
        "id": f"R-{template['key']}",
        "title": template["event"],
        "type_name": template.get("product_category") or template.get("paired_main_category") or "待确认",
        "subtype": template["key"],
        "description": template["event"],
        "stage_label": None,
        "insurability": "no" if commercial or undefined else "yes",
        "insurability_label": "不可保" if commercial else ("待补充证据" if undefined else "初步可保"),
        "line_names": [],
        "evidence_ids": [],
        "likelihood": None,
        "severity": None,
        "score": None,
        "level": None,
        "stated_by_user": stated_by_user,
        "event": template["event"],
        "affected_parties": template["affected_parties"],
        "loss_types": template["loss_types"],
        "bearers": template["bearers"],
        "insurability_analysis": analysis,
        "product_form": template["product_form"],
        "product_category": template["product_category"],
        "paired_main_category": template["paired_main_category"],
        "form_reason": template["form_reason"],
        "integration_group": template["integration_group"],
        "basis": {
            "sources": [{"kind": "用户描述", "text": text[:240]}],
            "reference_clauses": [{"name": "财产损失保险示范条款", "version": "2024", "applicability": "仅在损失属于物质损失时参考，不代替法规合规结论"}],
            "applicability": "仅作场景判断，不代表法规合规结论",
        },
    }


def _report(clarified: dict, risks: list[dict]) -> dict:
    return {
        "scene_summary": clarified["text"],
        "demand_items": clarified["demand_items"],
        "questions": clarified["questions"],
        "risks": risks,
        "stats": {"total": len(risks)},
        "coverage": [],
        "matrix": {},
        "heatmap": [],
        "gaps": [item["text"] for item in clarified["demand_items"] if item["status"] == "待确认事项"],
        "graph": {},
        "lifecycle": [],
        "regulatory_conclusion": None,
        "probability": None,
        "expected_claim": None,
        "rate": None,
        "underwriting_decision": None,
        "review_status": "待专业人员审核",
    }


def edit_risk(report: dict, risk_id: str, changes: dict) -> dict:
    updated = {**report, "risks": [dict(risk) for risk in report["risks"]]}
    for risk in updated["risks"]:
        if risk["id"] == risk_id:
            risk.update(changes)
            return updated
    raise KeyError(risk_id)
