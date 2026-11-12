"""文档/需求定价应按条款定价参数拆出多组件。"""

from decimal import Decimal

from techins.productdev.flow import _pricing_payload


def test_pricing_payload_builds_one_component_per_line():
    rules = {
        "applicable_scope": "网络安全保险",
        "billing_amount": Decimal("6000000"),
        "line": "CY01",
    }
    schemes = {
        "weighted": {"pure_loss_ratio": "0.003", "suggested_rate": "0.005", "billing_amount": "6000000", "method": "weighted"},
        "classification": {"pure_loss_ratio": "0.004", "suggested_rate": "0.006", "billing_amount": "6000000", "method": "classification"},
    }
    comparison = {"recommendation": "建议采用创新积分制评分"}
    sensitive = {}
    report = {"conclusion": "ok"}
    clause = {
        "pricing_params": [
            {"doc_id": "D1", "name": "网络安全保险", "lines": ["CY01"], "params": {"limit": 6000000, "deductible": 80000}},
            {"doc_id": "D2", "name": "侵犯专利权责任保险", "lines": ["IP06"], "params": {"limit": 4000000, "deductible": 80000}},
            {"doc_id": "D3", "name": "关键研发设备与研发中断费用重组主险", "lines": ["RD09", "RD08"], "params": {"limit": 11600000, "deductible": 80000}},
        ]
    }
    payload = _pricing_payload(rules, schemes, comparison, sensitive, report, clause=clause)
    ids = [c["id"] for c in payload["components"]]
    assert ids == ["D1-CY01", "D2-IP06", "D3-RD09", "D3-RD08"]
    assert payload["components"][1]["params"]["limit"] == 4000000.0
    assert payload["components"][2]["line"] == "RD09"
    assert payload["components"][3]["doc_name"] == "关键研发设备与研发中断费用重组主险"


def test_innovative_product_name_rejects_evidence_snippet():
    from techins.agents.clause_design.library import innovative_product_name, short_risk_label

    assert short_risk_label("核心技术人员依赖与关键研发人员流失风险") == "核心技术人员依赖与关键研发人员流失风险"
    assert short_risk_label("2025-07-11 某竞争对手提出无效宣告请求") == "创新责任"
    assert innovative_product_name("科技保险从单一险种向覆盖研发、转化、产业化全链条的组合产品演进", "NEW-NX-012") == "创新责任专项保险"
    assert innovative_product_name("算法误诊造成患者赔偿责任", "NEW-NX-008").endswith("保险")
