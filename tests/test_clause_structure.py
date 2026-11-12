"""主险与附加险的组合方式：同一事故链才合并，互不依附的主险打包，主险过多先用综合险替代。"""

from techins.agents.clause_design import library as lib
from techins.knowledge.catalog import LINES


def _modules() -> set[str]:
    return {module["line"] for module in lib.liability_modules()}


def test_rider_attaches_to_its_main_instead_of_staying_a_second_main():
    plan = lib.structure_plan(["RD08", "RD09"], _modules())
    product = next(item for item in plan["products"] if item["structure"] == "attach")
    assert product["main"] == "RD08"
    assert product["riders"] == ["RD09"]
    assert product["lines"] == ["RD08", "RD09"]
    assert plan["main_count"] == 1
    assert plan["rider_count"] == 1
    roles, attached = lib.structure_roles(plan)
    assert roles == {"RD08": "主险", "RD09": "附加险"}
    assert attached == {"RD09": "RD08"}


def test_attach_requires_the_declared_main_line():
    plan = lib.structure_plan(["RD09"], _modules())
    assert plan["products"][0]["structure"] == "single"
    assert lib.structure_roles(plan)[0] == {"RD09": "主险"}


def test_independent_mains_are_packaged_not_attached():
    plan = lib.structure_plan(["RD05", "IP06"], _modules())
    assert plan["products"][0]["structure"] == "package"
    assert plan["rider_count"] == 0
    assert lib.structure_roles(plan)[0] == {"RD05": "主险", "IP06": "主险"}


def test_too_many_mains_are_consolidated_into_the_comprehensive_product():
    risks = {"RD08": ["R02-1"], "RD05": ["R01-1"], "IP06": ["R05-5"], "TR01": ["R03-1"]}
    plan = lib.structure_plan(list(risks), _modules(), line_risks=risks, line_subtypes={k: {v[0]} for k, v in risks.items()}, limits={line: 5e6 for line in LINES})
    consolidation = plan["consolidation"]
    assert consolidation["line"] == "RD01"
    assert set(consolidation["replaces"]) == {"RD05", "TR01"}
    assert plan["plan_lines"] == ["RD08", "RD01", "IP06"]
    assert plan["main_count"] <= lib.MAX_MAIN_PRODUCTS
    assert plan["line_risks"]["RD01"] == ["R01-1", "R03-1"]


def test_consolidation_never_drops_risks_outside_the_comprehensive_product():
    risks = {"RD05": ["R01-1"], "TR01": ["R03-1"], "IP06": ["R05-5"]}
    plan = lib.structure_plan(list(risks), _modules(), line_risks=risks, line_subtypes={k: {v[0]} for k, v in risks.items()}, limits={line: 5e6 for line in LINES})
    assert plan["consolidation"] is None
    assert plan["plan_lines"] == ["RD05", "TR01", "IP06"]


def test_production_risk_ids_are_matched_by_subtype_when_consolidating():
    # 真实运行里 line_risks 存的是报告内风险编号（R-001），与目录中的风险子类型不是同一命名空间
    risks = {"RD08": ["R-001"], "IP06": ["R-002"], "IP10": ["R-003"], "RD01": ["R-004"], "TR08": ["R-005", "R-006"], "RD05": ["R-007"], "NEW-NX-006": ["R-008"]}
    subtypes = {
        "RD08": {"R02-1"},
        "IP06": {"R05-5"},
        "IP10": {"R05-7"},
        "RD01": {"R01-1", "R03-1", "R01-9"},
        "TR08": {"R01-1", "R01-2"},
        "RD05": {"R01-1", "R12-1"},
        "NEW-NX-006": {"NX-006"},
    }
    plan = lib.structure_plan(list(risks), _modules(), line_risks=risks, line_subtypes=subtypes, limits={line: 5e6 for line in LINES})
    consolidation = plan["consolidation"]
    assert consolidation is not None, "综合险已在方案内时仍应吸收可被其覆盖的分项主险"
    assert consolidation["line"] == "RD01"
    assert set(consolidation["replaces"]) == {"TR08", "RD05"}
    assert plan["plan_lines"].count("RD01") == 1
    assert "TR08" not in plan["plan_lines"] and "RD05" not in plan["plan_lines"]
    assert set(plan["line_risks"]["RD01"]) == {"R-004", "R-005", "R-006", "R-007"}
    assert plan["main_count"] == 5
    assert plan["advice"]


def test_single_duplicated_line_is_absorbed_when_mains_exceed_the_cap():
    risks = {"RD08": ["R-001"], "IP06": ["R-002"], "IP10": ["R-003"], "RD01": ["R-004"], "TR08": ["R-005"], "NEW-NX-006": ["R-006"], "NEW-NX-007": ["R-007"]}
    subtypes = {
        "RD08": {"R02-1"},
        "IP06": {"R05-5"},
        "IP10": {"R05-7"},
        "RD01": {"R01-1", "R03-1", "R01-9"},
        "TR08": {"R01-1", "R01-2"},
        "NEW-NX-006": {"NX-006"},
        "NEW-NX-007": {"NX-007"},
    }
    plan = lib.structure_plan(list(risks), _modules(), line_risks=risks, line_subtypes=subtypes, limits={line: 5e6 for line in LINES})
    assert plan["consolidation"]["line"] == "RD01"
    assert plan["consolidation"]["replaces"] == ["TR08"]
    assert "TR08" not in plan["plan_lines"]
    assert plan["main_count"] == 6
    assert set(plan["line_risks"]["RD01"]) == {"R-004", "R-005"}


def test_consolidation_does_not_absorb_when_subtypes_are_missing():
    risks = {"RD08": ["R-001"], "IP06": ["R-002"], "TR08": ["R-003"]}
    plan = lib.structure_plan(list(risks), _modules(), line_risks=risks, line_subtypes={"RD08": {"R02-1"}}, limits={line: 5e6 for line in LINES})
    assert plan["consolidation"] is None
    assert plan["main_count"] == 3


def test_attach_doc_carries_the_relationship_clause_and_dependency_metadata():
    modules = lib.liability_modules()
    rider_meta = next(m["meta"] for m in modules if m["code"] == "RD09-LIA-01")
    assert "RD08-LIA-01" in rider_meta["depends_on"]
    assert rider_meta["standalone_ok"] is True

    codes = [m["code"] for m in modules if m["line"] in ("RD08", "RD09")]
    spec = {
        "doc_id": "D1",
        "name": "关键研发设备保险",
        "structure": "attach",
        "main": "RD08",
        "riders": ["RD09"],
        "kind": "attach",
        "lines": ["RD08", "RD09"],
        "stage": "rd",
        "modules": codes,
        "extend_decisions": [],
        "innovate_decisions": [],
        "params": {"deductible": 10000, "limit": 1000000, "coinsurance": 0.3, "waiting_days": 15, "indemnity_months": 6, "period_months": 12},
    }
    doc = lib.assemble_doc(spec, {}, [], [])
    titles = [a["title"] for ch in doc["chapters"] if ch["key"] == "special" for a in ch["articles"]]
    assert "主险与附加条款的适用关系" in titles
    liability_titles = [a["title"] for ch in doc["chapters"] if ch["key"] == "liability" for a in ch["articles"]]
    assert "附加条款（研发中断研发费用补偿保险）：研发中断研发费用" in liability_titles
    assert lib.consistency_issues([doc]) == []


def test_missing_main_is_repaired_back_to_a_standalone_product():
    modules = lib.liability_modules()
    codes = [m["code"] for m in modules if m["line"] in ("RD08", "RD09")]
    spec = {
        "doc_id": "D1",
        "name": "关键研发设备保险",
        "structure": "attach",
        "main": "RD08",
        "riders": ["RD09"],
        "kind": "attach",
        "lines": ["RD08", "RD09"],
        "stage": "rd",
        "modules": codes,
        "extend_decisions": [],
        "innovate_decisions": [],
        "params": {"deductible": 10000, "limit": 1000000, "coinsurance": 0.3, "waiting_days": 15, "indemnity_months": 6, "period_months": 12},
    }
    doc = lib.assemble_doc(spec, {}, [], [])
    doc["lines"] = ["RD09"]
    issues = lib.consistency_issues([doc])
    assert any(issue["type"] == "structure" for issue in issues)
    assert lib.resolve_structure_issues([doc], issues) == 1
    assert doc["structure"] == "single"
    assert doc["riders"] == []
    assert all(a["title"] != "主险与附加条款的适用关系" for ch in doc["chapters"] for a in ch["articles"])


def test_human_removal_of_the_main_promotes_the_rider_to_a_main_product():
    plan = lib.structure_plan(["RD08", "RD09"], _modules())
    trimmed = lib.trim_structure(plan, {"RD08"})
    assert trimmed["products"][0]["structure"] == "single"
    assert trimmed["products"][0]["lines"] == ["RD09"]
    assert trimmed["main_count"] == 1
    assert trimmed["rider_count"] == 0
    assert lib.structure_roles(trimmed)[0] == {"RD09": "主险"}
