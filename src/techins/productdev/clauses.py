"""产品规则、条款正文、合规修订和 Word。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from ..agents.clause_design.library import assemble_doc, auto_fix, compliance_checks, doc_text, modules_by_line, number
from ..knowledge.catalog import LINES


class ClauseError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


CATEGORY_LINE = {"责任保险": "AI01", "财产损失保险": "RD08"}


def suggest_terms(rules: dict) -> dict:
    suggested = dict(rules)
    suggested.setdefault("period", "1年")
    if not suggested.get("billing_amount"):
        suggested["suggested_billing_amount"] = 1_000_000
        suggested["billing_confirmed"] = False
    else:
        suggested["billing_confirmed"] = True
    suggested["deductible"] = 0
    suggested["deductible_rate"] = 0
    return suggested


def build_rules(*, selected: list[dict], line_name: str, category: str, period: str | None, limits: dict, billing_base: str | None, billing_amount: float | int | None, confirmed: bool) -> dict:
    rules = {
        "applicable_scope": f"适用于{line_name}所约定的业务活动",
        "liability": [risk["event"] for risk in selected],
        "exclusions": ["未纳入保险责任的风险", "商业收益不足"],
        "period": period,
        "trigger": "保险期间内发生约定事件，并提供损失证明",
        "limits": limits,
        "billing_base": billing_base,
        "billing_amount": billing_amount,
        "claims_handling": {"evidence": ["损失证明", "事故说明"], "calculation": "按约定限额和实际损失计算", "order": ["核定损失", "扣除不属于保险责任的部分", "按限额给付"]},
        "deductible": 0,
        "deductible_rate": 0,
        "product_category": category,
        "line": CATEGORY_LINE.get(category),
        "selected_risk_ids": [risk["id"] for risk in selected],
        "source": "直接指定" if any(risk.get("direct") for risk in selected) else "圈定",
        "frozen": False,
    }
    if confirmed:
        if category == "责任保险" and billing_base not in ("每人限额", "每次限额", "累计限额"):
            raise ClauseError("必须指定一个计费基数")
        if not period or not billing_amount:
            raise ClauseError("需要先确认计费基数和保险期间")
        rules["frozen"] = True
        rules["billing_confirmed"] = True
    return rules


def applicable_references(items: list[dict], *, region: str, line: str | None) -> list[dict]:
    kept = []
    for item in items:
        if item.get("expired"):
            continue
        if item.get("region") not in (region, "全国", None):
            continue
        if item.get("line") not in (None, line, "通用"):
            continue
        kept.append(item)
    return kept


def _sync_amount(doc: dict, rules: dict) -> None:
    amount = f"累计赔偿限额为人民币{int(rules['billing_amount'])}元，免赔额为0元，免赔率为0。"
    if rules.get("billing_base"):
        amount += f"计费基数为{rules['billing_base']}。"
    for chapter in doc["chapters"]:
        if chapter["key"] == "amount" and chapter["articles"]:
            chapter["articles"][0]["text"] = amount
            return
    doc["chapters"].append({"key": "amount", "title": "保险金额", "articles": [{"title": "限额", "text": amount, "bold": False, "risk_ids": [], "origin": "rules"}]})


def _drop_unselected(doc: dict, selected_ids: set[str]) -> None:
    for chapter in doc["chapters"]:
        if chapter["key"] != "liability":
            continue
        kept = []
        for article in chapter["articles"]:
            ids = article.get("risk_ids") or []
            if ids and not set(ids) & selected_ids:
                continue
            kept.append(article)
        chapter["articles"] = kept


def build_clause(rules: dict, risks: list[dict], references: list[dict] | None = None) -> dict:
    if not rules.get("frozen"):
        raise ClauseError("产品规则确认前不能冻结条款")
    line = rules.get("line") or "AI01"
    if line not in LINES:
        raise ClauseError("没有可用于该险种的条款模块")
    selected_ids = set(rules["selected_risk_ids"])
    subtype = {
        "equipment": "R02-2",
        "rd_cost": "R01-2",
        "transfer": "R03-1",
        "ip": "R05-4",
        "quality": "R04-2",
        "cyber": "R07-1",
        "life": "R09-1",
        "aviation": "R10-4",
        "guarantee": "R03-2",
    }.get(LINES[line].get("family"), "R08-1")
    selected = [risk for risk in risks if risk["id"] in selected_ids] or risks
    by_subtype = {subtype: [{"id": risk["id"], "title": risk.get("title") or risk["id"]} for risk in selected]}
    modules = [item["code"] for item in modules_by_line(line)]
    spec = {
        "doc_id": f"doc-{line}",
        "name": rules["applicable_scope"],
        "lines": [line],
        "modules": modules,
        "params": {
            "deductible": 0,
            "limit": float(rules["billing_amount"]),
            "period_months": 12,
            "waiting_days": 0,
            "waiting_hours": 0,
            "indemnity_months": 12,
            "coinsurance": 0,
        },
    }
    doc = assemble_doc(spec, by_subtype, [], [])
    doc["params"]["deductible"] = 0
    _sync_amount(doc, rules)
    _drop_unselected(doc, selected_ids)
    rounds = []
    checks = compliance_checks(doc)
    for index in range(1, 6):
        failed = [item for item in checks if not item["passed"]]
        if not failed:
            break
        actions = auto_fix(doc, failed)
        number(doc)
        _sync_amount(doc, rules)
        doc["params"]["deductible"] = 0
        checks = compliance_checks(doc)
        rounds.append({"round": index, "failures": [item["code"] for item in failed], "actions": actions, "rechecked": True})
    passed = all(item["passed"] for item in checks)
    text = doc_text(doc)
    if str(int(rules["billing_amount"])) not in text:
        rounds.append({"round": len(rounds) + 1, "failures": ["amount-mismatch"], "actions": ["sync-amount"], "rechecked": True})
        _sync_amount(doc, rules)
        text = doc_text(doc)
        passed = passed and str(int(rules["billing_amount"])) in text
    return {
        "docs": [doc],
        "product_rules": rules,
        "chapters": doc["chapters"],
        "elements": {},
        "coverage_matrix": [],
        "diffs": [],
        "explanations": [{"doc_id": doc["doc_id"], "text": "本条款按已确认的产品规则编写，免赔额为 0。"}],
        "pricing_params": [{"doc_id": doc["doc_id"], "name": doc["name"], "lines": doc["lines"], "params": doc["params"], "deductible": 0}],
        "uw_requirements": [{"doc_id": doc["doc_id"], "name": doc["name"], "materials": ["投保资料"], "key_points": ["核实保障范围"]}],
        "monitoring_points": [],
        "compliance_passed": passed,
        "compliance_report": {
            "version_bound": True,
            "checks": checks,
            "rounds": rounds,
            "references": applicable_references(references or [], region=rules.get("region") or "全国", line=line),
            "review_status": "待专业人员审核",
            "filed": False,
        },
        "review_status": "待专业人员审核",
        "filed": False,
    }


def revise_amount_conflict(clause: dict) -> dict:
    """限额与产品规则不一致时不交付；修订正文和规则记录后再复核。"""
    doc = clause["docs"][0]
    number(doc)
    rules = dict(clause["product_rules"])
    expected = str(int(rules["billing_amount"]))
    text = doc_text(doc)
    rounds = list((clause.get("compliance_report") or {}).get("rounds") or [])
    if expected not in text:
        clause = {**clause, "compliance_passed": False, "clause_docx_name": None}
        rounds.append({"round": len(rounds) + 1, "failures": ["amount-mismatch"], "actions": ["sync-rules-and-text"], "rechecked": False})
        _sync_amount(doc, rules)
        rules["frozen"] = True
        text = doc_text(doc)
        passed = expected in text
        rounds[-1]["rechecked"] = True
        rounds[-1]["passed_after_revision"] = passed
        report = dict(clause.get("compliance_report") or {})
        report["rounds"] = rounds
        return {**clause, "docs": [doc], "chapters": doc["chapters"], "product_rules": rules, "compliance_passed": passed, "compliance_report": report}
    return clause


def write_docx(clause: dict, path: Path) -> str:
    if not clause.get("compliance_passed"):
        raise ClauseError("未通过校验，不能交付 Word")
    import docx

    document = docx.Document()
    for chapter in clause["chapters"]:
        document.add_heading(chapter["title"], level=1)
        for article in chapter["articles"]:
            document.add_paragraph(article["text"])
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    buffer = BytesIO()
    document.save(buffer)
    return path.name


def update_billing(clause: dict, *, billing_base: str, billing_amount: int) -> dict:
    rules = dict(clause["product_rules"])
    rules["billing_base"] = billing_base
    rules["billing_amount"] = billing_amount
    rules["frozen"] = True
    risks = [{"id": risk_id, "title": risk_id, "event": risk_id, "subtype": "R06-1"} for risk_id in rules["selected_risk_ids"]]
    # 重新生成时需要原风险标题；调用方应传入完整风险。
    rebuilt = build_clause(rules, clause.get("_risks") or risks)
    if not rebuilt["compliance_passed"]:
        raise ClauseError("未通过校验的正文不能作为费率依据")
    if str(billing_amount) not in doc_text(rebuilt["docs"][0]):
        raise ClauseError("条款金额尚未与新的计费基数一致")
    return rebuilt
