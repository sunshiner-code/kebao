"""险种推荐资格过滤：地区、行业等条件。"""

from __future__ import annotations

from techins.agents.clause_design.library import decide_paths, stated_required_lines
from techins.knowledge.eligibility import check_line, enterprise_context, filter_lines, ineligible_line_codes
from techins.knowledge.portfolio import LINES


def test_region_rules_exclude_local_only_products():
    shenzhen = enterprise_context({"region": "广东省深圳市", "industry": "智能制造装备", "track": "工业机器人"})
    shaanxi = enterprise_context({"region": "陕西省西安市", "industry": "智能制造装备", "track": "工业机器人"})
    ningbo = enterprise_context({"region": "浙江省宁波市", "industry": "智能制造装备", "track": "工业机器人"})

    assert check_line("RD05", shenzhen).eligible is False  # 陕西限定
    assert check_line("RD05", shaanxi).eligible is True
    assert check_line("RD06", shenzhen).eligible is True  # 深圳普惠
    assert check_line("RD06", shaanxi).eligible is False
    assert check_line("RD01", shenzhen).eligible is True  # 全国
    assert check_line("IP02", ningbo).eligible is True  # 宁波 PCT
    assert check_line("IP02", shenzhen).eligible is False
    assert check_line("TR02", shenzhen).eligible is False  # 广东不含深圳
    assert check_line("TR02", {"region": "广东省广州市", "industry": "", "track": "", "tech_field": ""}).eligible is True


def test_industry_rules_for_specialized_lines():
    robot = enterprise_context({"region": "江苏省苏州市", "industry": "智能制造装备", "track": "工业机器人", "tech_field": "协作机器人"})
    ai = enterprise_context({"region": "浙江省杭州市", "industry": "人工智能", "track": "人工智能软件", "tech_field": "大模型"})
    assert check_line("AI01", ai).eligible is True
    assert check_line("AI01", robot).eligible is False
    assert check_line("CY01", robot).eligible is True  # 通用行业


def test_filter_lines_replaces_ineligible_with_national_peer():
    ctx = enterprise_context({"region": "广东省深圳市", "track": "工业机器人", "industry": "智能制造装备"})
    # RD05 陕西版应被替换为同类全国/深圳可用产品
    result = filter_lines(["RD05", "RD01"], ctx, replace=True)
    assert "RD05" not in result.eligible
    assert "RD01" in result.eligible
    assert result.replacements.get("RD05") in LINES
    assert check_line(result.replacements["RD05"], ctx).eligible


def test_analyze_insurability_style_mapping_drops_shaanxi_for_shenzhen_firm():
    from techins.knowledge.catalog import SUBTYPES

    ctx = enterprise_context({"region": "广东省深圳市", "track": "工业机器人", "industry": "智能制造装备"})
    raw = list(SUBTYPES["R01-1"]["lines"])
    assert "RD05" in raw
    filtered = filter_lines(raw, ctx, replace=True)
    assert "RD05" not in filtered.eligible
    assert all(check_line(code, ctx).eligible for code in filtered.eligible)


def test_decide_paths_skips_ineligible_module_lines():
    # 用内存模块，避免初始化全局业务库引擎污染同进程其它测试
    modules = [
        {
            "code": "RD05-L1",
            "line": "RD05",
            "title": "陕西研发费用损失",
            "text": "重大经营困难使研发无法持续",
            "module_kind": "liability",
            "meta": {"covers": ["R01-1"], "keywords": ["经营困难", "研发"]},
        }
    ]
    risks = [
        {
            "id": "R1",
            "subtype": "R01-1",
            "type_code": "R01",
            "title": "经营困难导致研发无法持续",
            "description": "重大经营困难使研发无法持续",
            "stage": "rd",
            "involved": ["rd"],
            "insurability": "yes",
            "level": "高",
            "score": 16,
        }
    ]
    ctx = enterprise_context({"region": "广东省深圳市", "track": "工业机器人", "industry": "智能制造装备"})
    decisions = decide_paths(risks, modules, eligibility_ctx=ctx)
    assert decisions[0]["module_line"] is None or check_line(decisions[0]["module_line"], ctx).eligible


def test_stated_required_lines_respects_eligibility():
    risks = [
        {
            "id": "R1",
            "subtype": "R01-1",
            "demand_status": "明确诉求",
            "stated_by_user": True,
            "insurability": "yes",
            "lines": ["RD05", "RD01"],
        }
    ]
    ctx = enterprise_context({"region": "广东省深圳市", "track": "工业机器人"})
    chosen = stated_required_lines(risks, eligibility_ctx=ctx)
    assert "RD05" not in chosen
    assert "RD01" in chosen


def test_ineligible_codes_include_local_products_for_beijing_firm():
    ctx = enterprise_context({"region": "北京市海淀区", "track": "医疗器械", "industry": "医疗器械"})
    blocked = set(ineligible_line_codes(ctx))
    assert "RD05" in blocked and "RD06" in blocked and "IP02" in blocked
    assert "RD01" not in blocked
