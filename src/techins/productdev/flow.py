"""把需求驱动项目的评估、圈定、条款和费率写进现有产物。"""

from __future__ import annotations

from decimal import Decimal

from ..agents.common import create_artifact, freeze_artifact
from ..config import settings
from ..db import session_scope, utcnow
from ..knowledge.catalog import LINES
from ..models import DesignProject
from ..services.pipeline import PipelineError, _log
from . import parameters
from . import resources
from .assessment import edit_risk
from .clauses import ClauseError, applicable_references, build_clause, build_rules, suggest_terms, update_billing, write_docx
from .rates import RateError, actuarial_report, classification_method, commit_report, compare_schemes, example_classification, example_weighted, price_named_methods, select_scheme, sensitivity, weighted_score_method


def _project(project_id: int) -> DesignProject:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        if project is None:
            raise PipelineError("设计项目不存在")
        s.expunge(project)
        return project


def _save_config(project_id: int, config: dict, pipeline: dict, status: str | None = None) -> None:
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        project.config = config
        project.pipeline = pipeline
        if status:
            project.status = status


def update_risk(project_id: int, risk_id: str, changes: dict) -> dict:
    project = _project(project_id)
    node = project.pipeline["nodes"]["risk"]
    from ..agents.common import latest_artifact

    current = latest_artifact(node["artifact_key"])
    report = edit_risk(current.data, risk_id, changes)
    art = create_artifact(kind="risk_report", data=report, title=current.title, project_id=project_id, key=current.artifact_key)
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["risk"] = {**nodes["risk"], "version": art["version"], "artifact_key": art["artifact_key"]}
    pipeline["nodes"] = nodes
    _save_config(project_id, dict(project.config or {}), pipeline)
    return {"report": report, **art}


def save_scope(project_id: int, *, risk_ids: list[str], line_name: str, category: str, direct: bool, period: str | None, limits: dict, billing_base: str | None, billing_amount: int | None, confirm_rules: bool) -> dict:
    project = _project(project_id)
    node = project.pipeline["nodes"]["risk"]
    from ..agents.common import latest_artifact

    report = latest_artifact(node["artifact_key"]).data if node.get("artifact_key") else {"risks": []}
    selected = []
    if direct:
        selected = [{"id": "R-direct", "title": line_name, "event": line_name, "subtype": "R06-1", "direct": True}]
    else:
        by_id = {risk["id"]: risk for risk in report.get("risks", [])}
        missing = [risk_id for risk_id in risk_ids if risk_id not in by_id]
        if missing:
            raise PipelineError("圈定的风险不在风险评估清单中")
        selected = [by_id[risk_id] for risk_id in risk_ids]
    selected_ids = {risk["id"] for risk in selected}
    excluded = [risk["id"] for risk in report.get("risks", []) if risk["id"] not in selected_ids]
    rules = suggest_terms(build_rules(selected=selected, line_name=line_name, category=category, period=period, limits=limits, billing_base=billing_base, billing_amount=billing_amount, confirmed=False))
    if confirm_rules:
        rules = build_rules(selected=selected, line_name=line_name, category=category, period=period or rules.get("period"), limits=limits, billing_base=billing_base, billing_amount=billing_amount or rules.get("suggested_billing_amount"), confirmed=True)
    config = dict(project.config or {})
    config["scope"] = {"risk_ids": risk_ids, "line_name": line_name, "category": category, "direct": direct, "excluded_risk_ids": excluded}
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["scope"] = {**nodes.get("scope", {}), "status": "done", "result": config["scope"], "label": "圈定险种和风险"}
    pipeline["nodes"] = nodes
    _save_config(project_id, config, pipeline, "clause_design")
    if not confirm_rules:
        return {"product_rules": rules, "clause": None}
    try:
        clause = build_clause(rules, selected if not direct else selected, references=_references())
    except ClauseError as exc:
        raise PipelineError(str(exc)) from exc
    clause["_risks"] = selected
    if clause["compliance_passed"]:
        path = settings.data_dir / "clauses" / f"{project_id}.docx"
        clause["clause_docx_name"] = write_docx(clause, path)
    art = create_artifact(kind="clause_plan", data=clause, title=f"{project.name}条款", project_id=project_id)
    if clause["compliance_passed"]:
        freeze_artifact(art["artifact_key"], art["version"])
    nodes["clause"] = {"status": "done" if clause["compliance_passed"] else "failed", "artifact_key": art["artifact_key"], "version": art["version"], "label": "条款设计"}
    pipeline["nodes"] = nodes
    _save_config(project_id, config, pipeline, "clause_design")
    return {"product_rules": rules, "clause": clause, **art}


def reprice_after_rule_change(project_id: int, *, billing_base: str, billing_amount: int) -> dict:
    project = _project(project_id)
    from ..agents.common import latest_artifact

    node = project.pipeline["nodes"]["clause"]
    current = latest_artifact(node["artifact_key"])
    try:
        clause = update_billing(current.data, billing_base=billing_base, billing_amount=billing_amount)
    except ClauseError as exc:
        raise PipelineError(str(exc)) from exc
    if clause["compliance_passed"]:
        path = settings.data_dir / "clauses" / f"{project_id}.docx"
        clause["clause_docx_name"] = write_docx(clause, path)
    art = create_artifact(kind="clause_plan", data=clause, title=current.title, project_id=project_id, key=current.artifact_key)
    priced = price_project(project_id, clause)
    project = _project(project_id)
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["clause"] = {**nodes.get("clause", node), "artifact_key": art["artifact_key"], "version": art["version"], "status": "done"}
    pipeline["nodes"] = nodes
    _save_config(project_id, dict(project.config or {}), pipeline)
    freeze_artifact(art["artifact_key"], art["version"])
    return {"clause": clause, "pricing": priced, **art}


def build_document_pricing(rules: dict) -> dict:
    """按需求文档的两种方法计算建议费率，不改原有定价页使用的其他字段。"""
    rules = {**rules, "deductible": 0, "deductible_rate": 0}
    expense = parameters.expense_ratio()
    load = parameters.risk_load_rate()
    score_parameters = parameters.score_pricing()
    assumptions = parameters.classification_assumptions()
    usable, rejected = parameters.experience_rows()
    weighted = weighted_score_method(parameters=score_parameters)
    classification = classification_method(
        factors=assumptions["factors"] + [{"name": "风险暴露的另一种说法", "risk_key": "exposure", "condition": "与上一个因子同一风险", "coefficient": "1.5"}],
        benchmark_pure=assumptions["benchmark_pure"],
        benchmark_condition=assumptions["benchmark_condition"],
        samples=usable + rejected,
        assumptions=assumptions,
    )
    resource = parameters.register_resource({"id": "TECH-DOC-1", "source": "公开技术资料", "applicability": rules.get("applicable_scope") or "目标场景", "evidence_class": "公开证据", "version": "2026-01", "time": "2026-01-01"})
    parameters.cite_resource(resource["id"], "风险评估")
    parameters.cite_resource(resource["id"], "创新积分制评分")
    resources.cite_shared_document("风险评估")
    if resources.cite_shared_document("创新积分制评分") != 1:
        raise RateError("同一份技术资料被重复入库", code="duplicate_resource")
    schemes = price_named_methods(rules=rules, results={"weighted": weighted, "classification": classification}, expense_ratio=expense["value"], risk_load_rate=load["value"])
    for scheme in schemes.values():
        scheme["other_limits"] = {key: value for key, value in (rules.get("limits") or {}).items() if key != rules.get("billing_base")}
        scheme["expense_base"] = expense["base"]
        scheme["risk_load_base"] = load["base"]
    before = {key: schemes[key]["suggested_rate"] for key in schemes}
    sensitive = sensitivity(schemes, expense_ratio=Decimal("0.10"))
    if any(schemes[key]["suggested_rate"] != before[key] for key in schemes):
        raise RateError("敏感性结果覆盖了当前建议费率", code="sensitivity")
    comparison = compare_schemes(schemes)
    report = actuarial_report(schemes, comparison, sensitive, provisional=bool(classification["provisional"]), rules=rules, sources=parameters.report_sources())
    commit_report(schemes, report)
    return {"rules": rules, "schemes": schemes, "comparison": comparison, "sensitivity": sensitive, "actuarial_report": report}


def rules_from_clause(data: dict) -> dict:
    """从条款产物读取计费口径。已有产品规则时沿用，否则用条款里的限额。"""
    existing = data.get("product_rules") or {}
    if existing.get("billing_amount") and existing.get("period"):
        return {**existing, "deductible": 0, "deductible_rate": 0}
    docs = data.get("docs") or []
    params_list = data.get("pricing_params") or []
    source = params_list[0] if params_list else (docs[0] if docs else {})
    params = source.get("params") or {}
    limit = params.get("limit") or existing.get("billing_amount")
    if not limit:
        raise RateError("需要先确认计费基数", code="missing_billing_base")
    line = (source.get("lines") or [existing.get("line") or "RD08"])[0]
    months = int(params.get("period_months") or 12)
    return {
        "applicable_scope": source.get("name") or existing.get("applicable_scope") or "约定保险责任",
        "liability": [source.get("name") or existing.get("applicable_scope") or "约定保险责任"],
        "billing_amount": limit,
        "billing_base": existing.get("billing_base") or "累计限额",
        "period": "1年" if months == 12 else f"{months}个月",
        "product_category": "责任保险" if LINES.get(line, {}).get("subject") == "责任" else "财产损失保险",
        "line": line,
        "deductible": 0,
        "deductible_rate": 0,
        "limits": {"累计限额": limit},
    }


def document_pricing_for_clause(data: dict) -> dict:
    priced = build_document_pricing(rules_from_clause(data))
    return _pricing_payload(priced["rules"], priced["schemes"], priced["comparison"], priced["sensitivity"], priced["actuarial_report"], clause=data)


def price_project(project_id: int, clause: dict | None = None) -> dict:
    project = _project(project_id)
    from ..agents.common import latest_artifact

    if clause is None:
        node = project.pipeline["nodes"].get("clause") or {}
        if not node.get("artifact_key"):
            raise PipelineError("条款尚未生成")
        clause = latest_artifact(node["artifact_key"]).data
    if not clause.get("compliance_passed") and not (clause.get("product_rules") or clause.get("pricing_params") or clause.get("docs")):
        raise PipelineError("未通过校验的正文不能作为费率依据")
    if not (clause.get("pricing_params") or clause.get("docs") or clause.get("product_rules")):
        raise PipelineError("条款方案没有待定价参数，请先重新生成条款")
    rules = rules_from_clause(clause)
    preference = (project.pipeline["nodes"].get("preference") or {}).get("result") or {}
    if preference.get("deductible"):
        rules["deductible"] = 0
        rules["deductible_rate"] = 0
    try:
        priced = build_document_pricing(rules)
    except (RateError, parameters.ParameterError) as exc:
        raise PipelineError(str(exc)) from exc
    payload = _pricing_payload(priced["rules"], priced["schemes"], priced["comparison"], priced["sensitivity"], priced["actuarial_report"], clause=clause)
    art = create_artifact(kind="pricing_plan", data=payload, title=f"{project.name}费率", project_id=project_id)
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["pricing"] = {"status": "waiting_confirm", "artifact_key": art["artifact_key"], "version": art["version"], "label": "精算定价"}
    pipeline["nodes"] = nodes
    _log(pipeline, f"两种费率已并列算出，尚未选定（{len(payload['components'])} 个定价组件）")
    _save_config(project_id, dict(project.config or {}), pipeline, "pricing")
    return payload | art


def _component_sources(clause: dict | None, rules: dict) -> list[tuple[dict | None, str]]:
    """按条款定价参数拆成「文档 × 险种」组件；没有参数时退回单组件。"""
    sources: list[tuple[dict | None, str]] = []
    if clause:
        for pp in clause.get("pricing_params") or []:
            for line in pp.get("lines") or []:
                sources.append((pp, line))
        if not sources:
            for doc in clause.get("docs") or []:
                for line in doc.get("lines") or []:
                    sources.append((doc, line))
    if not sources:
        sources.append((None, rules.get("line") or "AI01"))
    return sources


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _pricing_payload(rules: dict, schemes: dict, comparison: dict, sensitive: dict, report: dict, clause: dict | None = None) -> dict:
    schemes = _jsonable(schemes)
    comparison = _jsonable(comparison)
    sensitive = _jsonable(sensitive)
    report = _jsonable(report)
    pure = schemes.get("weighted", {}).get("pure_loss_ratio")
    components = []
    for src, line in _component_sources(clause, rules):
        params = (src or {}).get("params") or {}
        limit = float(params.get("limit") or rules["billing_amount"])
        deductible = float(params.get("deductible") or 0)
        doc_id = (src or {}).get("doc_id")
        doc_name = (src or {}).get("name") or rules.get("applicable_scope") or "约定保险责任"
        line_name = LINES.get(line, {}).get("name", line)
        components.append(
            {
                "id": f"{doc_id}-{line}" if doc_id else "C1",
                "doc_id": doc_id,
                "doc_name": doc_name,
                "name": line_name if src else rules["applicable_scope"],
                "line": line,
                "params": {"limit": limit, "deductible": deductible},
                "rate": {"gross_rate": None, "band": None, "pure_rate": pure},
                "methods": [],
                "models": {},
                "scenarios": [],
                "experiments": [],
                "start_mode": "cold",
            }
        )
    return {
        "schemes": schemes,
        "comparison": comparison,
        "sensitivity": sensitive,
        "actuarial_report": report,
        "product_rules": _jsonable(rules),
        "report": {"conclusion": comparison["recommendation"], "sections": [report]},
        "components": components,
        "experiments": [],
    }


def choose_rate(project_id: int, method: str) -> dict:
    project = _project(project_id)
    from ..agents.common import latest_artifact

    node = project.pipeline["nodes"]["pricing"]
    current = latest_artifact(node["artifact_key"])
    data = current.data
    try:
        from decimal import Decimal as D

        schemes = data["schemes"]
        # select_scheme expects Decimal rates already stored as strings.
        normalized = {}
        for key, scheme in schemes.items():
            normalized[key] = {**scheme, "suggested_rate": D(scheme["suggested_rate"]), "billing_amount": D(scheme["billing_amount"]), "method": scheme["method"]}
        chosen = select_scheme(normalized, method)
    except RateError as exc:
        raise PipelineError(str(exc)) from exc
    chosen_rate = float(chosen["gross_rate"])
    band = [float(item) for item in chosen["band"]]
    pure = float(data["schemes"][method]["pure_loss_ratio"])
    method_rows = [{"name": item["name"], "suggested_rate": str(item["suggested_rate"])} for item in chosen["methods"]]
    for component in data.get("components") or []:
        rate = component.setdefault("rate", {})
        rate["gross_rate"] = chosen_rate
        rate["band"] = band
        rate["pure_rate"] = pure
        params = component.setdefault("params", {})
        limit = float(params.get("limit") or chosen["limit"])
        params["limit"] = limit
        params.setdefault("deductible", float(params.get("deductible") or 0))
        rate["premium_at_limit"] = round(chosen_rate * limit, 2)
        component["methods"] = method_rows
    data["selected_method"] = method
    art = create_artifact(kind="pricing_plan", data=data, title=current.title, project_id=project_id, key=current.artifact_key)
    freeze_artifact(art["artifact_key"], art["version"])
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["pricing"] = {**node, "status": "done", "artifact_key": art["artifact_key"], "version": art["version"]}
    nodes["package"] = {**nodes.get("package", {}), "status": "ready", "label": "产品包"}
    pipeline["nodes"] = nodes
    _save_config(project_id, dict(project.config or {}), pipeline, "packaging")
    return data | art


def example_pricing(project_id: int, method: str, *, score=None, coefficients: dict | None = None) -> dict:
    """按已交付方案维护的规则做示例测算，结果不写回正式费率。"""
    project = _project(project_id)
    node = (project.pipeline or {}).get("nodes", {}).get("pricing") or {}
    if not node.get("artifact_key"):
        raise PipelineError("尚无可示例测算的定价方案")
    from ..agents.common import latest_artifact

    current = latest_artifact(node["artifact_key"])
    schemes = (current.data or {}).get("schemes") or {}
    if method not in schemes:
        raise PipelineError("示例测算必须针对已算出的方法")
    try:
        if method == "weighted":
            if score is None or str(score).strip() == "":
                raise RateError("请填写示例综合得分", code="missing_score")
            result = example_weighted(schemes[method], score)
        else:
            result = example_classification(schemes[method], coefficients)
    except RateError as exc:
        raise PipelineError(str(exc)) from exc
    return {**_jsonable(result), "artifact_key": current.artifact_key, "version": current.version}


def review_report(project_id: int, *, approve: bool, comment: str, reviewer: dict) -> dict:
    """精算人员审定报告。审定意见写入报告并升版本，不改变两种方法的数值。"""
    project = _project(project_id)
    node = (project.pipeline or {}).get("nodes", {}).get("pricing") or {}
    if node.get("status") != "waiting_confirm" or not node.get("artifact_key"):
        raise PipelineError("精算定价尚未提交审定，不能审阅报告")
    from ..agents.common import latest_artifact

    current = latest_artifact(node["artifact_key"])
    data = dict(current.data or {})
    report = dict(data.get("actuarial_report") or {})
    if not report.get("sections"):
        raise PipelineError("报告缺少结构化内容，请重新生成定价")
    report["reviewed_by"] = reviewer.get("display_name") or reviewer.get("name") or reviewer.get("username")
    report["reviewed_at"] = utcnow().isoformat()
    report["review_comment"] = comment or None
    report["signed"] = bool(approve)
    report["status"] = "已审定签署" if approve else "退回修改"
    data["actuarial_report"] = report
    if data.get("report"):
        data["report"] = {**data["report"], "sections": [report]}
    art = create_artifact(kind="pricing_plan", data=data, title=current.title, project_id=project_id, key=current.artifact_key)
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["pricing"] = {**node, "artifact_key": art["artifact_key"], "version": art["version"]}
    pipeline["nodes"] = nodes
    _log(pipeline, f"精算报告{'已审定签署' if approve else '被退回修改'}：{comment or '无意见'}")
    _save_config(project_id, dict(project.config or {}), pipeline)
    return {"actuarial_report": report, **art}


def _references() -> list[dict]:
    return [
        {"id": "REG-VALID", "title": "财产保险条款管理办法", "region": "全国", "effective": "2021-01-01", "expired": False, "line": "通用", "version": "2021", "article": "条款应当列明保险责任和责任免除"},
        {"id": "REG-EXPIRED", "title": "已废止的条款费率规定", "region": "全国", "effective": "2005-01-01", "expired": True, "line": "通用", "version": "2005", "article": "不得再引用"},
        {"id": "REG-OTHER", "title": "其他地区的特别规定", "region": "某省", "effective": "2024-01-01", "expired": False, "line": "CY01", "version": "2024", "article": "仅适用于网络安全保险"},
    ]


def references_for(region: str, line: str | None, items: list[dict]) -> list[dict]:
    return applicable_references(items, region=region, line=line)
