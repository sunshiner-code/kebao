"""需求驱动的产品开发：创建、圈定、条款、两种费率和参数。"""

from __future__ import annotations

import os
import tempfile
import time

os.environ["TECHINS_DATA_DIR"] = tempfile.mkdtemp(prefix="techins-need-")
os.environ["TECHINS_DISABLE_LLM"] = "1"
os.environ["TECHINS_MONITOR_ENABLED"] = "0"
os.environ["TECHINS_DEMO_BOOTSTRAP"] = "0"

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from techins.api.app import app
from techins.productdev.assessment import assess, clarify
from techins.productdev.parameters import ParameterError, save_classification_factor
from techins.productdev.rates import RateError, convert_rate


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str) -> dict:
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _engine_project(background: str, customer_need: str) -> int:
    """直接准备一份需求评估产物，供条款和费率计算测试使用。"""
    from techins.agents.common import create_artifact
    from techins.db import session_scope
    from techins.models import DesignProject
    from techins.services.pipeline import _empty_pipeline

    with session_scope() as s:
        project = DesignProject(
            name=(customer_need or background)[:40] or "产品开发项目",
            project_type="need",
            enterprise_ids=[],
            target_lines=[],
            config={"background": background, "customer_need": customer_need, "mode": "auto", "auto_advance": True},
            pipeline=_empty_pipeline("need"),
            status="risk_assessment",
        )
        s.add(project)
        s.flush()
        project_id = project.id
    report = assess(background, customer_need)
    art = create_artifact(kind="risk_report", data=report, title=f"{(customer_need or background)[:40]}风险评估", project_id=project_id)
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        nodes["risk"] = {**nodes.get("risk", {}), "status": "waiting_confirm", "artifact_key": art["artifact_key"], "version": art["version"], "label": "风险评估"}
        nodes["scope"] = {"status": "pending", "label": "圈定险种和风险"}
        pipeline["nodes"] = nodes
        project.pipeline = pipeline
    return project_id


def test_convert_rate_matches_the_specified_numbers():
    result = convert_rate(
        pure_loss_ratio="0.002",
        risk_load_rate="0.001",
        expense_ratio="0.20",
        billing_amount="1000000",
        reference_pure_loss_ratio="0.002",
        pure_correction="1",
    )
    assert result["suggested_rate"] == Decimal("0.00375")
    assert result["premium"] == Decimal("3750.00")
    assert result["benchmark_rate"] == Decimal("0.00375")
    assert result["adjustment"] == Decimal("1")


def test_ambiguous_wording_and_related_risk():
    clarified = clarify("办公地址的门牌是 18 号", "设备在约定事故中损坏")
    assert not any("门牌" in question for question in clarified["questions"])
    report = assess("只希望保险分担设备损坏", "没有提到其他损失")
    assert any(not risk["stated_by_user"] for risk in report["risks"])
    assert all(risk["likelihood"] is None and risk["score"] is None for risk in report["risks"])
    ambiguous = assess("", "希望保险分担失败或中断的支出")
    statuses = {item["status"] for item in ambiguous["demand_items"]}
    assert {"明确诉求", "待确认事项"} <= statuses
    assert ambiguous["underwriting_decision"] is None
    assert ambiguous["rate"] is None


def test_need_project_starts_the_existing_agent(client: TestClient):
    headers = _login(client, "designer")
    created = client.post(
        "/api/design-projects",
        headers=headers,
        json={"project_type": "need", "background": "一项技术活动准备投入使用", "customer_need": "希望保险分担设备损坏的支出"},
    )
    assert created.status_code == 200, created.text
    project = client.get(f"/api/design-projects/{created.json()['id']}", headers=headers).json()
    assert project["project_type"] == "need"
    assert project["enterprise_ids"] == []
    assert project["pipeline"]["nodes"]["preference"]["status"] == "skipped"
    assert "scope" not in project["pipeline"]["nodes"]
    risk = project["pipeline"]["nodes"]["risk"]
    assert risk["run_id"]
    assert risk["status"] in ("running", "queued", "waiting_confirm")
    assert any(run["id"] == risk["run_id"] and run["agent_id"] == "AG-RA" for run in project["runs"])


def test_need_project_flow(client: TestClient):
    headers = _login(client, "designer")
    empty = client.post("/api/design-projects", headers=headers, json={"project_type": "need", "background": "  ", "customer_need": ""})
    assert empty.status_code == 409
    assert "背景" in empty.json()["error"]["message"]
    project_id = _engine_project("一项技术活动准备投入使用", "希望保险分担设备损坏的支出")
    project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    assert project["project_type"] == "need"
    assert project["enterprise_ids"] == []
    assert project["pipeline"]["nodes"]["preference"]["status"] == "skipped"
    report_key = project["pipeline"]["nodes"]["risk"]["artifact_key"]
    report = client.get(f"/api/artifacts/{report_key}", headers=headers)
    assert report.status_code == 200, report.text
    risks = report.json()["data"]["risks"]
    assert any(not item["stated_by_user"] for item in risks)
    assert all(item["likelihood"] is None and item["score"] is None for item in risks)
    chosen = next(item for item in risks if item["stated_by_user"])
    scoped = client.post(
        f"/api/design-projects/{project['id']}/scope",
        headers=headers,
        json={
            "risk_ids": [chosen["id"]],
            "line_name": "设备损失保险",
            "category": "财产损失保险",
            "period": "1年",
            "limits": {"累计限额": 1000000},
            "billing_base": "累计限额",
            "billing_amount": 1000000,
            "confirm_rules": True,
        },
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["product_rules"]["deductible"] == 0
    assert scoped.json()["compliance_passed"] is True
    saved = client.get(f"/api/design-projects/{project['id']}", headers=headers).json()
    excluded = saved["config"]["scope"]["excluded_risk_ids"]
    assert excluded
    assert chosen["id"] not in excluded
    clause_key = scoped.json()["artifact_key"]
    clause = client.get(f"/api/artifacts/{clause_key}", headers=headers).json()["data"]
    texts = [article["text"] for chapter in clause["chapters"] for article in chapter["articles"]]
    assert any("1000000" in text for text in texts)
    assert clause["clause_docx_name"]
    assert clause["compliance_report"]["review_status"] == "待专业人员审核"
    assert clause["filed"] is False
    missing_base = _engine_project("责任场景", "希望保险分担第三者受伤的赔偿")
    liability_project = client.get(f"/api/design-projects/{missing_base}", headers=headers).json()
    liability_risks = client.get(f"/api/artifacts/{liability_project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]["risks"]
    liability = next(item for item in liability_risks if item["stated_by_user"])
    refused = client.post(
        f"/api/design-projects/{liability_project['id']}/scope",
        headers=headers,
        json={"risk_ids": [liability["id"]], "line_name": "责任保险", "category": "责任保险", "period": "1年", "limits": {"累计限额": 1000000, "每人限额": 50000}, "confirm_rules": True},
    )
    assert refused.status_code == 409
    assert "计费基数" in refused.json()["error"]["message"]
    edited_liability = client.patch(
        f"/api/design-projects/{liability_project['id']}/risks/{liability['id']}",
        headers=headers,
        json={"changes": {"event": "约定活动造成第三者人身伤害"}},
    )
    assert edited_liability.status_code == 200
    reread = client.get(f"/api/artifacts/{edited_liability.json()['artifact_key']}", headers=headers).json()["data"]
    assert any(item["event"] == "约定活动造成第三者人身伤害" for item in reread["risks"])
    accepted = client.post(
        f"/api/design-projects/{liability_project['id']}/scope",
        headers=headers,
        json={"risk_ids": [liability["id"]], "line_name": "责任保险", "category": "责任保险", "period": "1年", "limits": {"累计限额": 1000000, "每人限额": 50000}, "billing_base": "累计限额", "billing_amount": 1000000, "confirm_rules": True},
    )
    assert accepted.status_code == 200, accepted.text
    assert "约定活动造成第三者人身伤害" in accepted.json()["product_rules"]["liability"]
    assert len(reread["risks"]) < 5
    from techins.productdev.flow import price_project

    price_project(project["id"])
    again = client.get(f"/api/design-projects/{project['id']}", headers=headers).json()
    pricing_key = again["pipeline"]["nodes"]["pricing"]["artifact_key"]
    pricing = client.get(f"/api/artifacts/{pricing_key}", headers=headers).json()["data"]
    assert pricing["components"][0]["rate"]["gross_rate"] is None
    assert pricing["comparison"]["blended_rate"] is None
    assert pricing["schemes"]["classification"]["provisional"] is True
    assert pricing["schemes"]["classification"]["fitted"] is False
    assert all(factor["risk_key"] != "maturity" for factor in pricing["schemes"]["classification"]["factors"])
    assert len(pricing["schemes"]["classification"]["factors"]) == len({factor["risk_key"] for factor in pricing["schemes"]["classification"]["factors"]})
    assert pricing["schemes"]["weighted"]["billing_amount"] == pricing["schemes"]["classification"]["billing_amount"] == "1000000"
    assert pricing["schemes"]["weighted"]["method"] == "创新积分制定价法"
    assert pricing["schemes"]["weighted"]["expense_base"] == "保费"
    assert pricing["schemes"]["weighted"]["risk_load_base"] == "计费保额"
    assert "maturity" not in pricing["schemes"]
    original_rates = {key: pricing["schemes"][key]["suggested_rate"] for key in ("weighted", "classification")}
    assert pricing["sensitivity"]["weighted"]["expense_ratio"] == "0.10"
    assert pricing["schemes"]["weighted"]["suggested_rate"] == original_rates["weighted"]
    assert pricing["actuarial_report"]["status"] == "待精算人员审定"
    assert pricing["actuarial_report"]["filed"] is False
    assert "赔案" in pricing["actuarial_report"]["calibration"]
    refs = clause["compliance_report"]["references"]
    assert any(item["id"] == "REG-VALID" for item in refs)
    assert all(item["id"] != "REG-EXPIRED" for item in refs)
    assert chosen["basis"]["reference_clauses"][0]["applicability"]
    assert report.json()["data"]["regulatory_conclusion"] is None
    edited = client.patch(
        f"/api/design-projects/{project['id']}/risks/{chosen['id']}",
        headers=headers,
        json={"changes": {"event": "约定事故造成约定设备损坏", "product_form": "main", "product_category": "财产损失保险"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["risk"]["event"] == "约定事故造成约定设备损坏"
    early = client.post(f"/api/design-projects/{project['id']}/package", headers=headers)
    assert early.status_code == 200, early.text
    early_product = client.get(f"/api/products/{early.json()['product_id']}", headers=headers).json()
    assert early_product["components"][0]["gross_rate"] in (None, 0)
    rejected_submit = client.post(f"/api/products/{early.json()['product_id']}/submit", headers=headers)
    assert rejected_submit.status_code >= 400
    selected = client.post(f"/api/design-projects/{project['id']}/rates/select", headers=headers, json={"method": "weighted"})
    assert selected.status_code == 200, selected.text
    body = selected.json()
    assert body["deductible"] == 0
    assert body["limit"] == 1000000
    assert body["gross_rate"]
    assert len(body["methods"]) == 2
    assert body["band"][0] == min(float(original_rates["weighted"]), float(original_rates["classification"]))
    assert body["band"][1] == max(float(original_rates["weighted"]), float(original_rates["classification"]))
    chosen_plan = client.get(f"/api/artifacts/{pricing_key}", headers=headers).json()["data"]
    assert chosen_plan["components"][0]["models"] == {}
    assert chosen_plan["components"][0]["scenarios"] == []
    assert chosen_plan["components"][0]["experiments"] == []
    assert chosen_plan["experiments"] == []
    changed = client.post(
        f"/api/design-projects/{project['id']}/rules/billing",
        headers=headers,
        json={"billing_base": "累计限额", "billing_amount": 2000000},
    )
    assert changed.status_code == 200, changed.text
    repriced = client.get(f"/api/design-projects/{project['id']}", headers=headers).json()
    new_clause = client.get(f"/api/artifacts/{repriced['pipeline']['nodes']['clause']['artifact_key']}", headers=headers).json()["data"]
    new_pricing = client.get(f"/api/artifacts/{repriced['pipeline']['nodes']['pricing']['artifact_key']}", headers=headers).json()["data"]
    assert any("2000000" in article["text"] for chapter in new_clause["chapters"] for article in chapter["articles"])
    assert new_pricing["schemes"]["weighted"]["billing_amount"] == "2000000"
    selected_again = client.post(f"/api/design-projects/{project['id']}/rates/select", headers=headers, json={"method": "weighted"})
    assert selected_again.status_code == 200, selected_again.text
    packed = client.post(f"/api/design-projects/{project['id']}/package", headers=headers)
    assert packed.status_code == 200, packed.text
    product = client.get(f"/api/products/{packed.json()['product_id']}", headers=headers).json()
    component = product["components"][0]
    assert component["deductible"] == 0
    assert component["limit"] == 2000000
    assert component["gross_rate"]
    assert len(component["methods"]) == 2
    from techins.agents.underwriting.tools import quote_premium

    quote = quote_premium(product["package"], float(component["limit"]), {}, [])
    assert quote["detail"][0]["gross_rate"] == component["gross_rate"]
    assert quote["detail"][0]["applied_rate"] == component["gross_rate"]
    published = client.get("/api/products?status=published", headers=headers).json()
    if published:
        before = published[0]["components"]
        again_published = client.get(f"/api/products/{published[0]['id']}", headers=headers).json()
        assert again_published["components"] == before or again_published["package"]["components"]
        listed = client.get("/api/products?status=published", headers=headers).json()
        assert listed[0]["components"] == before
    submitted = client.post(f"/api/products/{packed.json()['product_id']}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text


def test_amount_conflict_is_not_delivered_until_revised():
    from techins.productdev.clauses import revise_amount_conflict, write_docx

    clause = {
        "compliance_passed": True,
        "product_rules": {"billing_amount": 1000000, "billing_base": "累计限额", "frozen": True},
        "docs": [{"chapters": [{"key": "amount", "title": "保险金额", "articles": [{"title": "限额", "text": "累计赔偿限额为人民币1元。"}]}]}],
        "chapters": [{"key": "amount", "title": "保险金额", "articles": [{"title": "限额", "text": "累计赔偿限额为人民币1元。"}]}],
        "compliance_report": {"rounds": []},
    }
    with pytest.raises(Exception):
        write_docx({**clause, "compliance_passed": False}, __import__("pathlib").Path("/tmp/should-not-write.docx"))
    fixed = revise_amount_conflict(clause)
    assert fixed["compliance_passed"] is True
    assert "1000000" in fixed["docs"][0]["chapters"][0]["articles"][0]["text"]
    assert fixed["compliance_report"]["rounds"][-1]["rechecked"] is True


def test_report_mismatch_is_not_saved():
    from decimal import Decimal
    from techins.productdev.rates import RateError, commit_report

    schemes = {"weighted": {"suggested_rate": Decimal("0.00375"), "premium": Decimal("3750.00")}}
    report = {"schemes": {"weighted": {"suggested_rate": Decimal("0.009"), "premium": Decimal("1.00")}}}
    with pytest.raises(RateError) as raised:
        commit_report(schemes, report)
    assert raised.value.code == "report_mismatch"


def test_same_resource_is_not_duplicated():
    from techins.productdev import parameters

    resource = {"id": "TECH-SHARED", "source": "同一份技术资料", "applicability": "设备损失", "evidence_class": "公开证据", "version": "2026-01", "time": "2026-01-01"}
    parameters.register_resource(resource)
    before = len(parameters.load()["resources"])
    parameters.register_resource(resource)
    data = parameters.load()
    existing = next(item for item in data["resources"] if item["id"] == "TECH-SHARED")
    existing["uses"] = []
    parameters.save(data)
    parameters.cite_resource("TECH-SHARED", "风险评估")
    cited = parameters.cite_resource("TECH-SHARED", "成熟度评估")
    after = parameters.load()["resources"]
    assert len(after) == before
    assert cited["uses"] == ["风险评估", "成熟度评估"]


def test_enterprise_project_still_requires_preference(client: TestClient):
    from techins.db import session_scope
    from techins.models import DesignProject, Enterprise
    from techins.services.pipeline import PipelineError, _empty_pipeline, start_stage
    from sqlalchemy import select

    with session_scope() as s:
        enterprise = s.scalars(select(Enterprise)).first()
        pipeline = _empty_pipeline("enterprise")
        pipeline["nodes"]["risk"] = {"status": "done", "artifact_key": "risk-test", "version": 1, "label": "风险评估"}
        project = DesignProject(name="偏好门禁", project_type="enterprise", enterprise_ids=[enterprise.id], target_lines=[], config={}, pipeline=pipeline, status="clause_design")
        s.add(project)
        s.flush()
        project_id = project.id
    with pytest.raises(PipelineError) as raised:
        start_stage(project_id, "clause", None)
    assert "风险偏好" in str(raised.value)
    headers = _login(client, "actuary")
    rejected = client.post(
        "/api/development-parameters/factors",
        headers=headers,
        json={"name": "技术成熟度", "risk_key": "maturity", "condition": "低", "coefficient": "1.2", "source": "专家", "applicability": "分类费率", "evidence_class": "专家判断"},
    )
    assert rejected.status_code == 422
    assert "不能作为分类费率因子" in rejected.json()["error"]["message"]
    incomplete = client.post("/api/development-parameters/experience", headers=headers, json={"liability": "设备损失", "claim_amount": "1000"})
    assert incomplete.status_code == 200, incomplete.text
    assert incomplete.json()["rejected"] >= 1
    with pytest.raises(ParameterError):
        save_classification_factor({"name": "无来源因子", "risk_key": "exposure", "condition": "高", "coefficient": "1.1", "source": "", "applicability": "", "evidence_class": "专家判断"})


def test_pricing_rejects_parameter_without_provenance(client: TestClient):
    from techins.productdev import parameters

    headers = _login(client, "designer")
    original = parameters.load()
    broken = parameters.load()
    broken["expense_ratio"] = {**broken["expense_ratio"], "source": "", "applicability": ""}
    parameters.save(broken)
    try:
        created_id = _engine_project("参数追溯", "希望保险分担设备损坏")
        project_id = created_id
        project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
        risks = client.get(f"/api/artifacts/{project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]["risks"]
        chosen = next(item for item in risks if item["stated_by_user"])
        scoped = client.post(
            f"/api/design-projects/{project_id}/scope",
            headers=headers,
            json={"risk_ids": [chosen["id"]], "line_name": "设备损失保险", "category": "财产损失保险", "period": "1年", "limits": {"累计限额": 1000000}, "billing_base": "累计限额", "billing_amount": 1000000, "confirm_rules": True},
        )
        assert scoped.status_code == 200, scoped.text
        from techins.productdev.flow import price_project
        from techins.services.pipeline import PipelineError

        with pytest.raises(PipelineError) as raised:
            price_project(project_id)
        assert "可追溯" in str(raised.value)
    finally:
        parameters.save(original)


def test_one_description_keeps_three_demand_statuses():
    report = assess("一项技术活动正在准备，受影响主体仍不清楚", "希望保险分担设备损坏")
    statuses = {item["status"] for item in report["demand_items"]}
    assert {"明确诉求", "系统推断", "待确认事项"} <= statuses


def test_insufficient_evidence_is_not_written_as_full_cover():
    report = assess("受影响主体不清楚", "只希望保险分担设备损坏")
    risk = next(item for item in report["risks"] if item["insurability_analysis"]["judgment"] == "证据不足")
    conditions = "；".join(risk["insurability_analysis"]["conditions"])
    assert "已经具备" not in conditions
    assert risk["insurability_analysis"]["obstacles"]
    assert risk["form_reason"]
    assert "已经具备完整保障条件" not in risk["form_reason"] or "不能写成" in risk["form_reason"]


def test_unconfirmed_period_or_limit_is_only_suggested(client: TestClient):
    headers = _login(client, "designer")
    project_id = _engine_project("活动准备", "希望保险分担设备损坏")
    project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    risks = client.get(f"/api/artifacts/{project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]["risks"]
    chosen = next(item for item in risks if item["stated_by_user"])
    suggested = client.post(
        f"/api/design-projects/{project['id']}/scope",
        headers=headers,
        json={"risk_ids": [chosen["id"]], "line_name": "设备损失保险", "category": "财产损失保险", "confirm_rules": False},
    )
    assert suggested.status_code == 200, suggested.text
    rules = suggested.json()["product_rules"]
    assert rules["frozen"] is False
    assert rules["billing_confirmed"] is False
    assert rules["suggested_billing_amount"]
    assert suggested.json()["artifact_key"] is None
    assert suggested.json()["compliance_passed"] is None


def test_enterprise_preference_deductible_stays_out_of_rules(client: TestClient):
    from techins.db import session_scope
    from techins.models import DesignProject

    headers = _login(client, "designer")
    project_id = _engine_project("企业现场", "希望保险分担设备损坏")
    with session_scope() as s:
        project = s.get(DesignProject, project_id)
        pipeline = dict(project.pipeline)
        nodes = dict(pipeline["nodes"])
        nodes["preference"] = {"status": "done", "result": {"deductible": 800000, "deductible_rate": 0.2}, "label": "风险偏好"}
        pipeline["nodes"] = nodes
        project.project_type = "enterprise"
        project.pipeline = pipeline
    project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    assert project["pipeline"]["nodes"]["preference"]["result"]["deductible"] == 800000
    risks = client.get(f"/api/artifacts/{project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]["risks"]
    chosen = next(item for item in risks if item["stated_by_user"])
    scoped = client.post(
        f"/api/design-projects/{project_id}/scope",
        headers=headers,
        json={"risk_ids": [chosen["id"]], "line_name": "设备损失保险", "category": "财产损失保险", "period": "1年", "limits": {"累计限额": 1000000}, "billing_base": "累计限额", "billing_amount": 1000000, "confirm_rules": True},
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["product_rules"]["deductible"] == 0
    assert scoped.json()["product_rules"]["deductible_rate"] == 0
    kept = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    assert kept["pipeline"]["nodes"]["preference"]["result"]["deductible"] == 800000


def test_direct_scope_excludes_unselected_events(client: TestClient):
    headers = _login(client, "designer")
    project_id = _engine_project("直接指定", "希望保险分担设备损坏和第三者受伤")
    project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    report = client.get(f"/api/artifacts/{project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]
    events = [item["event"] for item in report["risks"]]
    scoped = client.post(
        f"/api/design-projects/{project['id']}/scope",
        headers=headers,
        json={"direct": True, "line_name": "设备损失保险", "category": "财产损失保险", "period": "1年", "limits": {"累计限额": 1000000}, "billing_base": "累计限额", "billing_amount": 1000000, "confirm_rules": True},
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["product_rules"]["source"] == "直接指定"
    saved = client.get(f"/api/design-projects/{project['id']}", headers=headers).json()
    assert set(saved["config"]["scope"]["excluded_risk_ids"]) == {item["id"] for item in report["risks"]}
    clause = client.get(f"/api/artifacts/{scoped.json()['artifact_key']}", headers=headers).json()["data"]
    liability = " ".join(scoped.json()["product_rules"]["liability"])
    liability += " ".join(article["text"] for chapter in clause["chapters"] if chapter["key"] == "liability" for article in chapter["articles"])
    assert all(event not in liability for event in events)


def test_designer_creates_customer_and_selects_target(client: TestClient):
    headers = _login(client, "designer")
    suffix = __import__("uuid").uuid4().hex[:6].upper()
    name = f"临港设备验证{suffix}有限公司"
    credit_code = f"91310000MA1F{suffix}"
    created = client.post(
        "/api/enterprises",
        headers=headers,
        json={"name": name, "credit_code": credit_code},
    )
    assert created.status_code == 200, created.text
    enterprise_id = created.json()["id"]
    project = client.post(
        "/api/design-projects",
        headers=headers,
        json={"name": f"临港企业定制{suffix}", "project_type": "enterprise", "enterprise_ids": [enterprise_id]},
    )
    assert project.status_code == 200, project.text
    loaded = client.get(f"/api/design-projects/{project.json()['id']}", headers=headers).json()
    assert loaded["enterprise_ids"] == [enterprise_id]
    assert loaded["enterprises"][0]["name"] == name


def test_premium_uses_aggregate_limit_and_rejects_bad_scope():
    from techins.productdev.rates import price_named_methods, refuse_inconsistent

    rules = {
        "liability": ["约定事故造成第三者人身伤害"],
        "deductible": 0,
        "deductible_rate": 0,
        "billing_amount": 1000000,
        "billing_base": "累计限额",
        "period": "1年",
        "product_category": "责任保险",
        "limits": {"累计限额": 1000000, "每人限额": 50000},
    }
    weighted = {"pure_loss_ratio": Decimal("0.002"), "reference_pure_loss_ratio": Decimal("0.002"), "pure_correction": Decimal("1"), "method": "创新积分制定价法"}
    classification = {"pure_loss_ratio": Decimal("0.0025"), "reference_pure_loss_ratio": Decimal("0.002"), "pure_correction": Decimal("1.2"), "method": "分类费率厘定法"}
    results = {"weighted": weighted, "classification": classification}
    schemes = price_named_methods(rules=rules, results=results, expense_ratio="0.20", risk_load_rate="0.001")
    assert schemes["weighted"]["premium"] == Decimal("3750.00")
    assert schemes["weighted"]["billing_amount"] == Decimal("1000000")
    assert schemes["classification"]["billing_amount"] == Decimal("1000000")
    assert schemes["weighted"]["limits"]["每人限额"] == 50000
    with pytest.raises(RateError) as deductible:
        price_named_methods(rules={**rules, "deductible": 1000}, results=results, expense_ratio="0.20", risk_load_rate="0.001")
    assert deductible.value.code == "deductible"
    broken = {key: {**scheme, "billing_amount": Decimal("50000")} for key, scheme in schemes.items()}
    with pytest.raises(RateError) as mismatch:
        refuse_inconsistent(rules, broken)
    assert mismatch.value.code == "scope_mismatch"


def test_development_resources_are_stored_once():
    from techins.productdev import resources
    from techins.productdev.parameters import experience_rows

    ids = resources.ensure_documents()
    assert len(ids) == 8
    assert resources.shared_document_count() == 1
    assert resources.cite_shared_document("风险评估") == 1
    assert resources.cite_shared_document("成熟度评估") == 1
    text = resources.experience_path().read_text(encoding="utf-8")
    assert "billing_amount" in text
    assert "empirical_pure" in text
    usable, _rejected = experience_rows()
    assert any(row.get("billing_amount") == "1000000" and row.get("usable") for row in usable)


def test_medium_risks_widen_cover_and_high_risks_stay():
    from techins.agents.clause_design.library import select_lines_with_fallback

    medium = [
        {"risk_id": "R1", "path": "reuse", "module_line": "L02", "level": "中", "score": 6, "spec": None},
        {"risk_id": "R2", "path": "reuse", "module_line": "L05", "level": "低", "score": 3, "spec": None},
    ]
    selection, levels, origin = select_lines_with_fallback(medium, cover_levels=["极高", "高"], budget=None, required=[], excluded=[], limits={})
    assert origin == ["极高", "高"]
    assert levels == ["极高", "高", "中"]
    assert selection["lines"] == ["L02"]

    high = [{"risk_id": "R1", "path": "reuse", "module_line": "L02", "level": "高", "score": 12, "spec": None}]
    kept, kept_levels, kept_origin = select_lines_with_fallback(high, cover_levels=["极高", "高"], budget=None, required=[], excluded=[], limits={})
    assert kept_origin is None
    assert kept_levels == ["极高", "高"]
    assert kept["lines"] == ["L02"]


def test_clause_tools_stop_when_no_draft_exists():
    from techins.agents.clause_design.tools import check_consistency, draft_line_clauses
    from techins.harness.tools import ToolError

    class Todo:
        attempts = 1

    class Todos:
        def __init__(self):
            self.item = Todo()

        def current(self):
            return self.item

    class Ctx:
        inputs: dict = {}
        workspace: dict = {}

        def __init__(self):
            self.todos = Todos()

    ctx = Ctx()
    with pytest.raises(ToolError) as raised:
        draft_line_clauses(ctx)
    assert raised.value.retryable is False
    assert "没有可生成条款的险种" in str(raised.value)
    assert ctx.todos.item.attempts == 99
    with pytest.raises(ToolError) as consistency:
        check_consistency(Ctx())
    assert consistency.value.retryable is False
    assert "没有可生成条款的险种" in str(consistency.value)


def test_direct_scores_reject_values_outside_one_to_five():
    from techins.agents.risk_assessment.llm_bindings import _post_direct

    with pytest.raises(ValueError):
        _post_direct({"risks": [{"id": "R-010"}]}, {"scores": [{"id": "R-010", "likelihood": 6, "severity": 5}]})
    with pytest.raises(ValueError):
        _post_direct({"risks": [{"id": "R-010"}]}, {"scores": [{"id": "R-010", "likelihood": 0, "severity": 2}]})


def test_need_scoring_uses_direct_judgment_not_financial_baseline(monkeypatch):
    from techins.agents.risk_assessment import tools as risk_tools

    def fake_invoke(task_name, inputs, **_kwargs):
        assert task_name == "ra.direct_scores"
        return {"scores": [{"id": "R-010", "likelihood": 4, "severity": 5, "reason": "原话要保受试者"}, {"id": "R-007", "likelihood": 2, "severity": 3, "reason": "推断"}]}

    monkeypatch.setattr(risk_tools.model_gateway, "invoke", fake_invoke)

    class Ctx:
        inputs = {"mode": "need", "customer_need": "生物医药公司，保受试者的保险"}
        workspace = {
            "evidence": {"E1": {"text": "受试者在试验中受伤", "level": 0}, "E2": {"text": "推断商业秘密可能泄露", "level": 0}},
            "risks": [
                {"id": "R-010", "subtype": "R09-3", "title": "临床试验受试者损害", "description": "受试者人身损害", "inferred": False, "evidence_ids": ["E1"]},
                {"id": "R-007", "subtype": "R04-4", "title": "临床试验数据与工艺商业秘密泄露", "description": "推断泄密", "inferred": True, "evidence_ids": ["E2"]},
                {"id": "R-empty", "subtype": "R09-1", "title": "", "description": "", "inferred": True, "evidence_ids": []},
            ],
        }

    result = risk_tools.score_risks(Ctx())
    stored = {item["id"]: item for item in result.data["records"]}
    subject = next(item for item in Ctx.workspace["risks"] if item["id"] == "R-010")
    secret = next(item for item in Ctx.workspace["risks"] if item["id"] == "R-007")
    empty = next(item for item in Ctx.workspace["risks"] if item["id"] == "R-empty")
    assert subject["level"] in ("高", "极高")
    assert subject["score"] == 20
    assert subject["demand_status"] == "明确诉求"
    assert subject["score_basis"] == "排序分数，不是发生概率"
    assert secret["demand_status"] == "推断"
    assert secret["likelihood"] == 2
    assert empty["likelihood"] is None and empty["score"] is None
    assert stored["R-empty"]["likelihood"] is None
    assert "概率" not in result.summary and "费率" not in result.summary
    # 直接判定路径同样补齐可控性、趋势、置信度与管理建议，避免报告展开区出现空字段
    for risk in (subject, secret, empty):
        assert risk["controllability"] in ("高", "中", "低")
        assert risk["trend"] in ("上升", "稳定")
        assert 0 < risk["confidence"] <= 0.95
        assert risk["advice"]
    assert subject["confidence"] > secret["confidence"]


def test_stated_subject_line_is_required_even_if_inferred_ip_scores_higher():
    from techins.agents.clause_design.library import select_lines, stated_required_lines

    risks = [
        {"id": "R-010", "subtype": "R09-3", "title": "临床试验受试者损害", "demand_status": "明确诉求", "insurability": "yes", "stated_by_user": True},
        {"id": "R-007", "subtype": "R06-1", "title": "商业秘密泄露", "demand_status": "推断", "insurability": "partial", "inferred": True},
    ]
    assert stated_required_lines(risks) == ["LS03", "LS01"]
    decisions = [
        {"risk_id": "R-010", "path": "reuse", "module_line": "L10", "level": "低", "score": 4, "spec": {"line": "L10"}},
        {"risk_id": "R-007", "path": "extend", "module_line": "L04", "level": "高", "score": 12, "spec": {"line": "L04"}},
    ]
    selected = select_lines(decisions, budget=None, required=["L10"], excluded=[], cover_levels=["极高", "高"], limits={"L10": 1_000_000, "L04": 1_000_000})
    assert "L10" in selected["lines"]
    assert selected["lines"] != ["L04"]


def test_compliance_review_stays_local_and_reuses_unchanged_text(client: TestClient, monkeypatch):
    from techins.agents.clause_design import tools as clause_tools

    def forbid_invoke(*_args, **_kwargs):
        raise AssertionError("合规审查不应调用模型")

    monkeypatch.setattr(clause_tools.model_gateway, "invoke", forbid_invoke)

    class Ctx:
        workspace = {"docs": [{"doc_id": "D1", "name": "临床试验责任险", "lines": ["L10"], "chapters": [{"key": "liability", "title": "保险责任", "articles": [{"no": "第一条", "title": "保险责任", "text": "赔偿金额以保险人认定为准。"}]}]}], "compliance_review_cache": {}}

    clause_tools.check_compliance(Ctx())
    first = Ctx.workspace["compliance"]["issues"]
    clause_tools.check_compliance(Ctx())
    assert Ctx.workspace["compliance"]["issues"] == first
    assert first and first[0]["severity"] == "high" and first[0]["line"] == "临床试验责任险" and first[0]["article"] == "第一条"
    assert Ctx.workspace["compliance"]["rules"][0]["checks"]


def test_rule_conflict_blocks_and_model_severity_does_not_fail_evaluation(client: TestClient):
    from techins.agents.clause_design.library import compliance_checks
    from techins.agents.clause_design.profile import d_severe_issues

    conflict = {"name": "设备损失保险", "lines": ["L02"], "params": {"limit": 1000000, "deductible": 0}, "chapters": [{"key": "amount", "title": "保险金额", "articles": [{"no": "第一条", "title": "限额", "text": "累计赔偿限额为人民币1元。"}]}]}
    checks = compliance_checks(conflict)
    assert any(not item["passed"] for item in checks)

    class Ctx:
        workspace = {"compliance": {"rules": [{"checks": [{"passed": True}]}], "issues": [{"severity": "high", "line": "临床试验责任险", "article": "第一条", "issue": "表述可以更明确"}]}}

    count, passed, detail = d_severe_issues(Ctx(), {})
    assert count == 1
    assert passed is True
    assert "仅记录" in detail


def test_weighted_score_follows_the_maintained_table_not_score_direction():
    from techins.productdev.rates import RateError, weighted_score_method

    parameters = {
        "source": "专家确认的映射表",
        "applicability": "目标险种",
        "mapping_basis": "区间费率按表取值",
        "example_score": "20",
        "formula": "综合得分等于各项指标得分乘以权重后相加",
        "indicators": [
            {"name": "创新投入", "definition": "投入强度", "weight": "0.50"},
            {"name": "创新产出", "definition": "产出情况", "weight": "0.50"},
        ],
        "bands": [
            {"label": "低分", "min": "0", "max": "50", "pure": "0.002", "reference": True},
            {"label": "高分", "min": "50", "max": "100", "pure": "0.008", "reference": False, "closed_max": True},
        ],
    }
    low = weighted_score_method(parameters=parameters, example_score="20")
    high = weighted_score_method(parameters=parameters, example_score="80")
    assert low["pure_loss_ratio"] == Decimal("0.002")
    assert high["pure_loss_ratio"] == Decimal("0.008")
    assert high["pure_loss_ratio"] > low["pure_loss_ratio"]
    parameters["indicators"][0]["weight"] = "0.10"
    with pytest.raises(RateError) as raised:
        weighted_score_method(parameters=parameters)
    assert raised.value.code == "weight_sum"


def test_classification_rejects_score_indicator_and_report_mismatch():
    from techins.productdev.rates import RateError, classification_method, commit_report

    assumptions = {"source": "分类费率基准假设", "applicability": "新产品", "factors": [{"name": "综合评分得分", "risk_key": "score", "condition": "高分", "coefficient": "0.8"}]}
    with pytest.raises(RateError) as raised:
        classification_method(factors=assumptions["factors"], benchmark_pure="0.002", benchmark_condition="基准", samples=[], assumptions=assumptions)
    assert raised.value.code == "maturity_factor"
    schemes = {"weighted": {"suggested_rate": Decimal("0.004"), "premium": Decimal("4000.00")}}
    report = {"schemes": {"weighted": {"suggested_rate": Decimal("0.009"), "premium": Decimal("1.00")}}}
    with pytest.raises(RateError) as mismatch:
        commit_report(schemes, report)
    assert mismatch.value.code == "report_mismatch"


def test_need_pricing_run_records_four_steps(client: TestClient):
    from techins.db import session_scope
    from techins.models import Run, RunStep
    from techins.services.pipeline import start_stage
    from sqlalchemy import select

    project_id = _engine_project("一项技术活动准备投入使用", "希望保险分担设备损坏的支出")
    headers = _login(client, "designer")
    project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    report = client.get(f"/api/artifacts/{project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]
    chosen = next(item for item in report["risks"] if item["stated_by_user"])
    scoped = client.post(
        f"/api/design-projects/{project_id}/scope",
        headers=headers,
        json={"risk_ids": [chosen["id"]], "line_name": "设备损失保险", "category": "财产损失保险", "period": "1年", "limits": {"累计限额": 1000000}, "billing_base": "累计限额", "billing_amount": 1000000, "confirm_rules": True},
    )
    assert scoped.status_code == 200, scoped.text
    from techins.db import session_scope as scope
    from techins.models import DesignProject

    with scope() as s:
        project_row = s.get(DesignProject, project_id)
        nodes = dict(project_row.pipeline["nodes"])
        nodes["clause"]["status"] = "done"
        project_row.pipeline = {**project_row.pipeline, "nodes": nodes}
    run_id = start_stage(project_id, "pricing", None)
    assert run_id
    deadline = time.time() + 30
    status = ""
    while time.time() < deadline:
        with session_scope() as s:
            run = s.get(Run, run_id)
            status = run.status if run else ""
        if status in ("completed", "failed", "waiting_human"):
            break
        time.sleep(0.2)
    with session_scope() as s:
        run = s.get(Run, run_id)
        steps = list(s.scalars(select(RunStep).where(RunStep.run_id == run_id)))
        assert run.status == "completed", run.error
        titles = [step.title for step in steps]
    assert "读取产品规则" in titles
    assert "计算创新积分制评分" in titles
    assert "计算分类费率" in titles
    assert "比较并生成报告" in titles
    again = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    pricing = client.get(f"/api/artifacts/{again['pipeline']['nodes']['pricing']['artifact_key']}", headers=headers).json()["data"]
    assert pricing["components"][0]["rate"]["gross_rate"] is None
    assert pricing["schemes"]["weighted"]["method"] == "创新积分制定价法"
    assert all(factor["risk_key"] not in ("score", "maturity") for factor in pricing["schemes"]["classification"]["factors"])
    refused = client.post(f"/api/design-projects/{project_id}/package", headers=headers)
    assert refused.status_code == 200
    product_id = refused.json()["product_id"]
    rejected = client.post(f"/api/products/{product_id}/submit", headers=headers)
    assert rejected.status_code >= 400
    selected = client.post(f"/api/design-projects/{project_id}/rates/select", headers=headers, json={"method": "weighted"})
    assert selected.status_code == 200, selected.text
    body = selected.json()
    assert body["gross_rate"]
    assert body["limit"] == 1000000
    assert {item["name"] for item in body["methods"]} == {"创新积分制定价法", "分类费率厘定法"}


def test_clause_profiles_stay_on_procedure_after_config_override():
    import techins.agents.clause_design.profile  # noqa: F401
    from techins.harness.agent import get_profile
    from techins.services.agent_config import apply

    for purpose in ("main", "clause_doc", "revision"):
        profile = get_profile("AG-CD", purpose)
        assert profile.planner == "procedure"
        assert profile.plan_confirm is False
        assert profile.max_reflection_rounds == 1
        merged = apply(profile, {"planner": "auto", "plan_confirm": True, "max_reflection_rounds": 6, "pass_score": 70})
        assert merged.planner == "procedure"
        assert merged.plan_confirm is False
        assert merged.max_reflection_rounds == 1
        assert merged.pass_score == 70


def test_recommendation_step_does_not_wait_and_recheck_is_rules_only():
    from techins.harness.skills import skill_library

    skill = skill_library.get("SK-02")
    recommend = next(step for step in skill.procedure if step.title == "险种推荐与组合方式决策")
    manual = next(step for step in skill.procedure if step.title == "人工合规复核")
    recheck = next(step for step in skill.procedure if step.title == "合规复核校验")
    revised = next(step for step in skill.subprocedures["修订运行"] if step.title == "合规复核校验")
    assert recommend.hitl is None
    assert manual.hitl == "manual_compliance"
    assert recheck.hitl is None and recheck.stage_eval is False and recheck.args["rules_only"] == "1"
    assert revised.stage_eval is False and revised.args["rules_only"] == "1"


def _innovation_spec(events: list[str], *, extend: str | None = None) -> dict:
    decisions = []
    for index, event in enumerate(events, start=1):
        decisions.append(
            {
                "risk_id": f"R{index}",
                "spec": {
                    "line": "L04",
                    "event": event,
                    "loss_form": "费用损失",
                    "scope": "列明商业秘密",
                    "trigger": "第三方以不正当手段获取",
                    "loss": "维权费用",
                    "evidence": "被保险人应提供裁判文书。",
                    "parametric": "生效裁判文书",
                },
            }
        )
    extend_decisions = []
    if extend:
        extend_decisions.append({"risk_id": "E1", "spec": {**decisions[0]["spec"], "event": extend}})
    return {
        "doc_id": "D1",
        "name": "知识产权险",
        "lines": ["L04"],
        "modules": [],
        "params": {"deductible": 1000, "limit": 1_000_000, "period_months": 12, "coinsurance": 0.3},
        "innovate_decisions": decisions,
        "extend_decisions": extend_decisions,
    }


def _draft_ctx(spec: dict, elapsed: float = 0.0):
    class Ctx:
        inputs = {
            "spec": spec,
            "risks": [{"id": item["risk_id"], "subtype": "R04-4", "title": item["spec"]["event"], "level": "中"} for item in spec["innovate_decisions"] + spec["extend_decisions"]],
            "features": {},
            "patents": [],
            "start_mode": "warm",
        }
        workspace = {"fix_log": []}
        state = {"elapsed_before": elapsed}
        started_monotonic = time.monotonic()
        todos = None

    return Ctx()


def _fake_modules(event: str) -> dict:
    return {
        "modules": [
            {"kind": "subject", "title": "标的", "text": f"{event}的保险标的为列明商业秘密。"},
            {"kind": "liability", "title": event, "text": f"在保险期间内，因约定原因导致{event}的，保险人按照本保险合同约定赔偿。"},
            {"kind": "exclusion", "title": "免除", "text": "投保前已经存在的事由，保险人不负责赔偿。"},
            {"kind": "amount", "title": "金额", "text": "设每次事故分项赔偿限额。"},
            {"kind": "settlement", "title": "赔偿", "text": "赔偿金额 = 核定的维权费用 × 保障比例 − 免赔额。"},
        ],
        "pending_points": [],
        "parametric_trigger": "生效裁判文书",
    }


def test_innovations_keep_separate_prompts_and_run_concurrently(client: TestClient, monkeypatch):
    from techins.agents.clause_design import tools as clause_tools

    spec = _innovation_spec(["第一创新", "第二创新"], extend="扩展责任")
    calls = []

    def fake_invoke(task_name, inputs, builtin_fallback=True, **kwargs):
        calls.append({"task": task_name, "event": (inputs.get("spec") or {}).get("event"), "start": time.perf_counter(), "timeout_s": kwargs.get("timeout_s"), "builtin_fallback": builtin_fallback})
        time.sleep(0.4)
        if task_name == "cd.personalize":
            return {"special_articles": [{"title": "列明标的", "text": "本保险合同列明的标的以投保清单为准。"}], "rationale": "按企业特征生成"}
        return _fake_modules(inputs["spec"]["event"])

    monkeypatch.setattr(clause_tools.model_gateway, "invoke", fake_invoke)
    ctx = _draft_ctx(spec)
    clause_tools.draft_line_clauses(ctx)
    innovate_calls = [item for item in calls if item["task"] == "cd.innovate"]
    assert {item["event"] for item in innovate_calls} == {"第一创新", "第二创新", "扩展责任"}
    assert max(item["start"] for item in innovate_calls) - min(item["start"] for item in innovate_calls) < 0.4
    assert all(item["builtin_fallback"] is False and item["timeout_s"] is None for item in innovate_calls)
    produced = ctx.workspace["doc"]
    innovation_titles = [article["title"] for chapter in produced["chapters"] if chapter["key"] == "liability" for article in chapter["articles"] if article["origin"] == "innovation"]
    assert innovation_titles == ["第一创新", "第二创新"]
    assert any(article["title"] == "扩展条款：扩展责任" for chapter in produced["chapters"] if chapter["key"] == "liability" for article in chapter["articles"])
    assert not ctx.workspace["fix_log"]


def test_model_failure_uses_builtin_clause_text(client: TestClient, monkeypatch):
    from techins.llm.gateway import ModelUnavailable
    from techins.agents.clause_design import tools as clause_tools

    spec = _innovation_spec(["失败创新"])
    calls = []

    def fail_invoke(task_name, inputs, builtin_fallback=True, **kwargs):
        calls.append({"task": task_name, "timeout_s": kwargs.get("timeout_s"), "builtin_fallback": builtin_fallback})
        raise ModelUnavailable("调用失败")

    monkeypatch.setattr(clause_tools.model_gateway, "invoke", fail_invoke)
    ctx = _draft_ctx(spec)
    clause_tools.draft_line_clauses(ctx)
    assert calls and all(item["builtin_fallback"] is False and item["timeout_s"] is None for item in calls)
    assert any("调用失败改用内置实现" in note for note in ctx.workspace["fix_log"])
    text = "\n".join(article["text"] for chapter in ctx.workspace["doc"]["chapters"] for article in chapter["articles"])
    assert "保险人不负责赔偿" in text
    keys = [chapter["key"] for chapter in ctx.workspace["doc"]["chapters"]]
    assert {"general", "subject", "liability", "exclusion", "amount", "settlement"} <= set(keys)


def test_rules_only_recheck_keeps_local_issues_without_calling_the_model(client: TestClient, monkeypatch):
    from techins.agents.clause_design import tools as clause_tools

    def forbid_invoke(*_args, **_kwargs):
        raise AssertionError("合规复核不应调用模型")

    monkeypatch.setattr(clause_tools.model_gateway, "invoke", forbid_invoke)

    class Ctx:
        workspace = {"docs": [{"doc_id": "D1", "name": "临床试验责任险", "lines": ["L10"], "chapters": [{"key": "liability", "title": "保险责任", "articles": [{"no": "第一条", "title": "保险责任", "text": "赔偿金额以保险人认定为准。"}]}]}], "compliance_review_cache": {}, "fix_log": []}
        state = {}
        started_monotonic = time.monotonic()

    clause_tools.check_compliance(Ctx())
    kept = Ctx.workspace["compliance"]["issues"][0]["issue"]
    Ctx.workspace["docs"][0]["chapters"][0]["articles"][0]["text"] = "赔偿受试者损害，并明确不包括罚款、罚金。"
    clause_tools.check_compliance(Ctx(), rules_only="1")
    assert Ctx.workspace["compliance"]["issues"][0]["issue"] == kept
    assert Ctx.workspace["compliance"]["rules"][0]["checks"]


def test_negated_fine_wording_passes_and_affirmative_fine_fails(client: TestClient):
    from techins.agents.clause_design.library import compliance_checks

    def c14(liability: str, exclusion: str) -> bool:
        doc = {
            "lines": ["L04"],
            "chapters": [
                {"key": "liability", "title": "保险责任", "articles": [{"no": "第一条", "title": "保险责任", "text": liability}]},
                {"key": "exclusion", "title": "责任免除", "articles": [{"no": "第二条", "title": "责任免除", "text": exclusion, "bold": True}]},
            ],
        }
        return next(item for item in compliance_checks(doc) if item["code"] == "C14")["passed"]

    assert c14("保险人不承担下列损失：不包括试验失败、罚款、罚金。", "罚款、罚金不在保障范围内。") is True
    assert c14("保险人负责赔偿行政罚款。", "战争、核风险不在保障范围内。") is False


def test_explain_and_scores_do_not_call_the_model(client: TestClient, monkeypatch):
    from techins.agents.clause_design import tools as clause_tools
    from techins.agents.clause_design.profile import _review_dim

    def forbid_invoke(*_args, **_kwargs):
        raise AssertionError("通俗解读和评分不应调用模型")

    monkeypatch.setattr(clause_tools.model_gateway, "invoke", forbid_invoke)
    article = {"no": "第一条", "title": "保险责任", "text": "赔偿列明损失。", "origin": "generated", "bold": False, "risk_ids": ["R1"], "module": ""}
    doc = {
        "doc_id": "D1",
        "name": "知识产权险",
        "lines": ["L04"],
        "kind": "existing",
        "structure": "single",
        "params": {"deductible": 0, "limit": 1000000},
        "chapters": [
            {"key": "liability", "title": "保险责任", "articles": [article]},
            {"key": "exclusion", "title": "责任免除", "articles": [{**article, "title": "责任免除", "text": "罚款不保。", "bold": True}]},
            {"key": "amount", "title": "保险金额", "articles": [{**article, "title": "限额", "text": "累计赔偿限额为人民币100万元。"}]},
            {"key": "settlement", "title": "赔偿处理", "articles": [{**article, "title": "赔偿处理", "text": "赔偿金额 = 核定损失。被保险人提供材料后三十日内核定。"}]},
        ],
    }

    class Ctx:
        workspace = {
            "docs": [doc],
            "insurable": [{"id": "R1", "title": "商业秘密泄露", "level": "高"}],
            "recommendation": {"uncovered": []},
            "report": {"risks": [{"id": "R1", "title": "商业秘密泄露", "level": "高", "subtype": "R04-4", "likelihood": 3, "severity": 3}]},
            "fix_log": [],
        }
        vault = None

    result = clause_tools.compose_clause_outputs(Ctx())
    explanations = Ctx.workspace["outputs"]["explanations"]
    assert explanations[0]["line"] == "D1"
    assert explanations[0]["covers"] and explanations[0]["not_covered"] and explanations[0]["how_to_claim"]
    assert result.metrics["covered"] == 1
    for key in ("personalization", "clarity", "fairness", "payability"):
        score, _passed, reason = _review_dim(key, 4)(Ctx(), {})
        assert isinstance(score, float)
        assert reason


def test_automatic_reflection_only_schedules_one_text_fix(client: TestClient, monkeypatch):
    from techins.harness.agent import get_profile
    from techins.harness.reflection import reflect
    from techins.harness.todo import TodoItem, TodoList

    called = []
    monkeypatch.setattr("techins.harness.reflection.model_gateway.invoke", lambda *args, **kwargs: called.append(args) or (_ for _ in ()).throw(AssertionError("自动反思不应调用模型")))

    class Ctx:
        agent_id = "AG-CD"
        run_id = 1
        profile = get_profile("AG-CD")
        state = {"reflection_rounds": 0, "eval_history": []}
        todos = TodoList([TodoItem(id="T01", content="生成条款方案产物", status="completed")])

        def begin_step(self, *_args, **_kwargs):
            return 1

        def end_step(self, *_args, **_kwargs):
            return None

        def emit(self, *_args, **_kwargs):
            return None

        def emit_todos(self):
            return None

    ctx = Ctx()
    evaluation = {
        "kind": "final",
        "score": 59,
        "dimensions": [
            {"key": "compliance", "name": "强制合规规则 C01–C16", "passed": False, "value": 1, "threshold": "全部通过", "detail": "未通过：C14", "category": "格式与契约错误"},
            {"key": "coverage", "name": "标准档风险覆盖率", "passed": False, "value": 0.2, "threshold": "≥ 80%", "detail": "覆盖不足", "category": "约束冲突"},
        ],
    }
    items = reflect(ctx, evaluation, trigger="final_eval")
    assert called == []
    assert [(item.tool, item.args) for item in items] == [("auto_fix_compliance", {}), ("check_compliance", {"rules_only": "1"}), ("compose_clause_outputs", {})]
    assert reflect(ctx, evaluation, trigger="final_eval") == []
    assert ctx.state["reflection_rounds"] == 1


def test_recommend_rationale_uses_builtin_when_the_model_fails(client: TestClient, monkeypatch):
    from techins.agents.clause_design import tools as clause_tools
    from techins.llm.gateway import ModelUnavailable

    calls = []

    def fail_invoke(*_args, **_kwargs):
        calls.append(True)
        raise ModelUnavailable("超时")

    monkeypatch.setattr(clause_tools.model_gateway, "invoke", fail_invoke)
    monkeypatch.setattr(
        clause_tools.lib,
        "select_lines_with_fallback",
        lambda *_args, **_kwargs: ({"lines": ["IP08"], "line_risks": {"IP08": ["R1"]}, "uncovered": [], "log": [], "estimated_premium": {"IP08": 1000}, "spent": 1000}, ["高"], None),
    )
    monkeypatch.setattr(clause_tools.lib, "structure_plan", lambda lines, _modules, **_kwargs: {"structure_label": "单险种", "products": [{"lines": lines, "name": "商业秘密被侵权损失保险", "structure": "single"}]})
    monkeypatch.setattr(clause_tools.lib, "liability_modules", lambda: [{"line": "IP08"}])

    class Ctx:
        inputs = {}
        workspace = {
            "fix_log": [],
            "capacity": {"cover_levels": ["高", "极高"]},
            "decisions": [{"risk_id": "R1", "path": "reuse", "module_line": "IP08", "level": "高", "score": 12, "novel": False, "spec": None}],
            "insurable": [{"id": "R1", "title": "商业秘密泄露", "level": "高"}],
            "limits": {"IP08": 1_000_000},
            "report": {"risks": []},
        }
        state = {}
        started_monotonic = time.monotonic()
        todos = None

    result = clause_tools.recommend_products(Ctx())
    assert calls
    assert "推荐" in result.summary
    assert any("推荐说明因调用失败改用内置实现" in note for note in Ctx.workspace["fix_log"])
    assert Ctx.workspace["recommendation"]["items"][0]["rationale"]


def test_clause_prompts_and_plan_fields_stay_in_place():
    import techins.agents.clause_design.llm_bindings  # noqa: F401
    from techins.agents.clause_design.library import CHAPTERS
    from techins.agents.clause_design.profile import _artifact_data
    from techins.harness.policy import PLANNER_RULES
    from techins.llm.tasks import get_task

    expected = {
        "cd.innovate": (
            "对没有现成条款覆盖的可保风险，按“保险标的→保险事故→损失形态→赔偿计算→责任免除→赔偿处理”的顺序创造新的条款模块，并列出待验证条款点。",
            '{"modules": [{"kind": "subject|liability|exclusion|amount|settlement|definition", "title": "", "text": ""}], "pending_points": [""], "parametric_trigger": ""}',
            "modules 至少包含 subject、liability、exclusion、amount、settlement 五类；赔偿处理必须给出计算公式与认定依据；责任免除必须包含投保前已存在事由与经营决策导致的损失",
        ),
        "cd.personalize": (
            "依据风险情景与企业特征，为某一险种生成个性化的特别约定条款，并对保险责任的触发条件做针对性限定；不得扩大或缩小监管要求的必备内容。",
            '{"special_articles": [{"title": "", "text": ""}], "liability_notes": [{"module": "", "note": ""}], "rationale": ""}',
            "特别约定应列明标的、项目、人员岗位、供应商或系统等企业特定信息，并对高风险提出风险管理约定；不得出现承诺性表述，不得扩大保险责任",
        ),
        "cd.compliance_review": (
            "以法规为依据审查条款的公平性、明确性与可读性，列出问题位置、严重程度、法规依据与整改建议。",
            '{"issues": [{"line": "", "doc_name": "", "article": "", "severity": "high|medium|low", "issue": "", "basis": "", "suggestion": ""}]}',
            "article 使用输入中的条号（如第十二条），line 使用险种名称；只输出确有依据的问题",
        ),
        "cd.explain": (
            "为每个险种写面向客户的通俗保障说明（不替代条款）：保什么、不保什么、怎么赔。",
            '{"explanations": [{"line": "", "covers": "", "not_covered": "", "how_to_claim": ""}]}',
            "line 使用输入中每份条款的 doc_id；语言通俗，不替代条款，不作承诺",
        ),
        "cd.review": (
            "按评估量表对条款方案打分（1–5 分）：个性化、清晰无歧义、公平性、可赔付性，并说明理由。",
            '{"scores": {"personalization": {"score": 4, "reason": ""}}}',
            "",
        ),
    }
    for name, (description, output_hint, guidance) in expected.items():
        task = get_task(name)
        assert task.description == description
        assert task.output_hint == output_hint
        assert task.guidance == guidance
    assert PLANNER_RULES == (
        "你运行在科技保险平台的 Harness 中，只能通过调用工具推进任务，规则如下：\n"
        "1. 第一步用 load_skill 加载核心技能；冷启动时再加载 SK-S2 的作业规程。\n"
        "2. 用 todo_write(action=plan) 生成计划；items 留空表示严格按已加载技能的作业规程生成。\n"
        "3. 开始一个待办时先调用 todo_write(action=start, todo_id=…)，随后调用该待办指定的工具（可以在同一次回复中给出两个工具调用）。工具参数中形如 @xxx 的值由平台自动补全，你无需构造。\n"
        "4. 待办的工具成功后：如果该待办需要阶段评估，调用 stage_complete(todo_id)；如果需要人审，调用 request_human(card_type, todo_id)；否则调用 todo_write(action=complete, todo_id)。\n"
        "5. 工具失败时可重试一次；仍失败调用 todo_write(action=fail, todo_id, note) 并继续后续待办。\n"
        "6. 可以随时调用 search_knowledge 查询知识库，但不得跳过或改变作业规程中的步骤顺序。\n"
        "7. 全部待办完成后调用 finish。"
    )
    assert [key for key, _title in CHAPTERS] == ["general", "subject", "liability", "exclusion", "amount", "period", "obligation_insurer", "obligation_insured", "settlement", "dispute", "definition", "special"]

    class Ctx:
        workspace = {}
        inputs = {}
        state = {}

    data = _artifact_data(Ctx())
    for key in ("report_ref", "enterprise", "insurable", "non_insurable", "decisions", "recommendation", "capacity", "tiers", "docs", "consistency", "compliance", "backtests", "fix_log", "start_mode"):
        assert key in data


def test_report_sections_examples_review_and_word_download(client: TestClient):
    from techins.productdev.flow import price_project

    project_id = _engine_project("一项技术活动准备投入使用", "希望保险分担设备损坏的支出")
    headers = _login(client, "designer")
    project = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    report = client.get(f"/api/artifacts/{project['pipeline']['nodes']['risk']['artifact_key']}", headers=headers).json()["data"]
    chosen = next(item for item in report["risks"] if item["stated_by_user"])
    scoped = client.post(
        f"/api/design-projects/{project_id}/scope",
        headers=headers,
        json={"risk_ids": [chosen["id"]], "line_name": "设备损失保险", "category": "财产损失保险", "period": "1年", "limits": {"累计限额": 1000000}, "billing_base": "累计限额", "billing_amount": 1000000, "confirm_rules": True},
    )
    assert scoped.status_code == 200, scoped.text
    price_project(project_id)
    again = client.get(f"/api/design-projects/{project_id}", headers=headers).json()
    pricing_key = again["pipeline"]["nodes"]["pricing"]["artifact_key"]
    pricing = client.get(f"/api/artifacts/{pricing_key}", headers=headers).json()["data"]
    act_report = pricing["actuarial_report"]
    assert [section["id"] for section in act_report["sections"]] == ["scope", "sources", "methods", "verification", "assumptions"]
    sources_block = act_report["sections"][1]["blocks"][0]
    assert sources_block["type"] == "table"
    assert any("创新积分制" in str(row) for row in sources_block["rows"])
    checks = next(block for section in act_report["sections"] for block in section["blocks"] if block["type"] == "checks")
    assert all(item["passed"] for item in checks["items"])
    assert act_report["reviewed_by"] is None and act_report["signed"] is False

    example = client.post(f"/api/design-projects/{project_id}/rates/example", headers=headers, json={"method": "weighted", "score": "95"})
    assert example.status_code == 200, example.text
    assert example.json()["score_band"]["label"] == "90–100"
    assert example.json()["suggested_rate"]
    bad = client.post(f"/api/design-projects/{project_id}/rates/example", headers=headers, json={"method": "weighted", "score": "120"})
    assert bad.status_code == 409
    cls = client.post(f"/api/design-projects/{project_id}/rates/example", headers=headers, json={"method": "classification", "coefficients": {"exposure": "1.2"}})
    assert cls.status_code == 200, cls.text
    assert cls.json()["combined_coefficient"]
    after = client.get(f"/api/artifacts/{pricing_key}", headers=headers).json()["data"]
    assert after["schemes"]["weighted"]["suggested_rate"] == pricing["schemes"]["weighted"]["suggested_rate"]
    assert after["components"][0]["rate"]["gross_rate"] is None

    actuary = _login(client, "actuary")
    forbidden = client.post(f"/api/design-projects/{project_id}/rates/review", headers=headers, json={"approve": True})
    assert forbidden.status_code == 403
    returned = client.post(f"/api/design-projects/{project_id}/rates/review", headers=actuary, json={"approve": False, "comment": "映射区间需要复核"})
    assert returned.status_code == 200, returned.text
    assert returned.json()["actuarial_report"]["status"] == "退回修改"
    approved = client.post(f"/api/design-projects/{project_id}/rates/review", headers=actuary, json={"approve": True, "comment": "同意作为暂定费率依据"})
    assert approved.status_code == 200, approved.text
    reviewed = approved.json()["actuarial_report"]
    assert reviewed["signed"] is True and reviewed["status"] == "已审定签署"
    assert reviewed["reviewed_by"] and reviewed["review_comment"] == "同意作为暂定费率依据"
    assert approved.json()["version"] > returned.json()["version"]

    docx = client.get(f"/api/design-projects/{project_id}/pricing-report.docx", headers=actuary)
    assert docx.status_code == 200, docx.text
    assert docx.content[:2] == b"PK"
    assert "wordprocessingml" in docx.headers["content-type"]
    old_version = client.get(f"/api/design-projects/{project_id}/pricing-report.docx?version={returned.json()['version']}", headers=actuary)
    assert old_version.status_code == 200

    selected = client.post(f"/api/design-projects/{project_id}/rates/select", headers=headers, json={"method": "weighted"})
    assert selected.status_code == 200, selected.text
    late_review = client.post(f"/api/design-projects/{project_id}/rates/review", headers=actuary, json={"approve": True})
    assert late_review.status_code == 409
