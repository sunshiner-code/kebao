"""可保的新风险先并入已推荐产品，没有合适产品才新建；不可保风险不进入产品。"""

from techins.agents.clause_design.library import decide_paths, liability_modules, place_novel_risks, select_lines, structure_plan
from techins.knowledge.catalog import LINES


def _place(risks: list[dict]):
    modules = liability_modules()
    decisions = decide_paths(risks, modules)
    base = [item for item in decisions if not item.get("novel")]
    selected = select_lines(base, budget=None, required=[], excluded=[], cover_levels=["极高", "高", "中", "低"], limits={})
    recommended = [line for line in selected["lines"] if line in LINES]
    placed, rejected = place_novel_risks(decisions, recommended, modules)
    return placed, rejected, recommended


def test_insurable_novel_risk_extends_recommended_product():
    placed, rejected, recommended = _place(
        [
            {"id": "R-003", "subtype": "R02-2", "type_code": "R02", "title": "关键测试设备故障停机风险", "description": "关键测试设备发生故障并停机", "stage": "rd", "level": "中", "score": 9, "insurability": "yes"},
            {"id": "R-001", "subtype": "NX-002", "type_code": "NX", "novel": True, "title": "冷链失效导致在研批次报废与停线风险", "description": "细胞治疗产品在院内转运时冷链失效，在研批次报废并停线", "stage": "rd", "level": "高", "score": 16, "insurability": "partial"},
        ]
    )
    equipment = next(code for code, spec in LINES.items() if spec["name"] == "关键研发设备保险")
    assert equipment in recommended
    novel = next(item for item in placed if item["risk_id"] == "R-001")
    assert novel["path"] == "extend"
    assert novel["module_line"] == equipment
    assert novel["spec"]["line"] == equipment
    assert rejected == []
    codes = [item["spec"]["line"] for item in placed if item["path"] == "innovate" and item.get("novel")]
    assert codes == []


def test_novel_risk_without_suitable_product_creates_one_and_clause_attaches():
    placed, rejected, _recommended = _place(
        [
            {"id": "R-003", "subtype": "R02-2", "type_code": "R02", "title": "关键测试设备故障停机风险", "description": "关键测试设备发生故障并停机", "stage": "rd", "level": "中", "score": 9, "insurability": "yes"},
            {"id": "R-008", "subtype": "NX-008", "type_code": "NX", "novel": True, "title": "算法误诊造成患者赔偿责任", "description": "算法输出错误导致医院误诊，需要对患者承担赔偿责任", "stage": "industrial", "level": "高", "score": 12, "insurability": "partial"},
        ]
    )
    novel = next(item for item in placed if item["risk_id"] == "R-008")
    assert novel["path"] == "innovate"
    assert novel["spec"]["line"] == "NEW-NX-008"
    assert rejected == []
    selected = select_lines(placed, budget=None, required=["NEW-NX-008"], excluded=[], cover_levels=["极高", "高", "中"], limits={})
    plan = structure_plan(selected["lines"], {module["line"] for module in liability_modules()})
    product = next(item for item in plan["products"] if item["structure"] == "innovative")
    attached = [item for item in placed if item["path"] == "innovate" and item["spec"]["line"] in set(product["lines"])]
    assert product["lines"] == ["NEW-NX-008"]
    assert [item["risk_id"] for item in attached] == ["R-008"]


def test_uninsurable_novel_risk_is_not_added_to_any_product():
    placed, rejected, _recommended = _place(
        [
            {"id": "R-003", "subtype": "R02-2", "type_code": "R02", "title": "关键测试设备故障停机风险", "description": "关键测试设备发生故障并停机", "stage": "rd", "level": "中", "score": 9, "insurability": "yes"},
            {"id": "R-004", "subtype": "NX-004", "type_code": "NX", "novel": True, "title": "融资与市场接受不足", "description": "后续融资中断导致现金流紧张，市场接受不足", "stage": "industrial", "level": "高", "score": 12, "insurability": "partial"},
            {"id": "R-009", "subtype": "NX-009", "type_code": "NX", "novel": True, "title": "经营性亏损", "description": "冷链设备发生火灾", "stage": "rd", "level": "高", "score": 12, "insurability": "no"},
        ]
    )
    assert {item["risk_id"] for item in rejected} == {"R-004", "R-009"}
    assert all("不可保" in item["reject_reason"] for item in rejected)
    assert all(item["risk_id"] not in {item["risk_id"] for item in placed} for item in rejected)
    assert not any(item.get("novel") and item["risk_id"] in {"R-004", "R-009"} for item in placed)
