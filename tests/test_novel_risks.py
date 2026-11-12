"""没有合适类型或证据未被覆盖时，创建新风险并保留标注。"""

from techins.agents.clause_design.library import decide_paths
from techins.agents.risk_assessment.llm_bindings import _post_risks
from techins.agents.risk_assessment.profile import d_type_coverage
from techins.agents.risk_assessment.reasoning import extract_risks
from techins.agents.risk_assessment.tools import analyze_insurability, normalize_risks


def test_unmatched_evidence_creates_novel_risk_and_keeps_taxonomy_match():
    out = extract_risks(
        {
            "enterprise": {"products": [{"name": "细胞治疗产品"}]},
            "cards": [
                {"id": "E1", "text": "研发里程碑延期三个月。"},
                {"id": "E2", "text": "冷链运输中断造成在研批次全部失效，形成履约风险。"},
                {"id": "E3", "text": "研发里程碑延期。冷链运输中断造成在研批次全部失效，形成履约风险。"},
            ],
        }
    )
    taxonomy = [c for c in out["candidates"] if not c["novel"]]
    assert any(c["subtype"] == "R01-2" and "E1" in c["evidence_ids"] for c in taxonomy)
    assert all("E2" not in c["evidence_ids"] for c in taxonomy)
    novel = [c for c in out["candidates"] if c["novel"]]
    assert {c["evidence_ids"][0] for c in novel} == {"E2", "E3"}
    assert all(c["subtype"] == "NEW" and c["type_name"] == "新创建风险" and "据此创建" in c["basis"] for c in novel)


def test_model_output_keeps_explicit_new_type_and_fills_uncovered_card():
    out = _post_risks(
        {
            "cards": [
                {"id": "E1", "text": "研发里程碑延期三个月。"},
                {"id": "E2", "text": "冷链运输中断造成在研批次全部失效，形成履约风险。"},
                {"id": "E3", "text": "合作方临时撤出共享实验室，试验被迫中断，带来履约风险。"},
            ]
        },
        {
            "candidates": [
                {"subtype": "R01-2", "title": "研发进度延误", "description": "里程碑延期", "evidence_ids": ["E1"], "strength": 1},
                {"subtype": "R99-9", "title": "冷链失效", "type_name": "冷链风险", "description": "批次失效", "evidence_ids": ["E2"], "basis": "没有合适类型"},
                {"subtype": "UNKNOWN", "title": "", "description": "无标题", "evidence_ids": ["E2"]},
            ]
        },
    )
    codes = {c["subtype"] for c in out["candidates"]}
    assert "R01-2" in codes
    created = [c for c in out["candidates"] if c["novel"]]
    assert any(c["title"] == "冷链失效" and c["type_name"] == "冷链风险" and c["evidence_ids"] == ["E2"] for c in created)
    assert any(c["evidence_ids"] == ["E3"] for c in created)
    assert all(c["title"] for c in created)


class Ctx:
    def __init__(self, candidates):
        self.workspace = {"candidates": candidates, "risks": []}


def test_normalize_assigns_created_code_without_dropping():
    ctx = Ctx(
        [
            {"subtype": "R01-2", "novel": False, "title": "研发进度延误", "description": "里程碑延期造成进度损失", "evidence_ids": ["E1"], "strength": 1.2, "cues": ["延期"]},
            {"subtype": "NEW", "novel": True, "type_name": "冷链风险", "title": "冷链失效", "description": "冷链中断造成批次失效", "evidence_ids": ["E2"], "strength": 0.7, "basis": "没有合适类型", "cues": ["风险"]},
            {"subtype": "NEW", "novel": True, "type_name": "冷链风险", "title": "冷链失效", "description": "另一份材料同样描述冷链失效", "evidence_ids": ["E4"], "strength": 0.4, "cues": []},
        ]
    )
    result = normalize_risks(ctx)
    risks = ctx.workspace["risks"]
    assert result.metrics["risks"] == 2
    created = next(r for r in risks if r["novel"])
    assert created["subtype"] == "NX-001"
    assert created["type_code"] == "NX"
    assert created["type_name"] == "冷链风险"
    assert created["origin_label"] == "新创建"
    assert created["evidence_ids"] == ["E2", "E4"]
    assert next(r for r in risks if not r["novel"])["subtype"] == "R01-2"


def test_downstream_accepts_created_risk():
    risk = {
        "id": "R-009",
        "subtype": "NX-001",
        "type_code": "NX",
        "type_name": "冷链风险",
        "title": "冷链失效",
        "description": "冷链运输中断造成在研批次全部失效，形成履约风险",
        "novel": True,
        "stage": "rd",
        "involved": ["rd"],
        "level": "中",
        "score": 6,
        "evidence_ids": ["E2"],
    }
    ctx = Ctx([])
    ctx.workspace["risks"] = [dict(risk)]
    analyze_insurability(ctx)
    assert ctx.workspace["risks"][0]["insurability"] == "partial"
    assert ctx.workspace["risks"][0]["lines"] == []
    assert "待确认" in ctx.workspace["risks"][0]["insurability_reason"]
    decisions = decide_paths(
        [risk],
        [{"code": "M1", "line": "L01", "title": "研发中断", "text": "研发进度延误", "meta": {"covers": ["R01-2"]}}],
    )
    assert decisions[0]["path"] == "innovate"
    assert decisions[0]["spec"]["event"]
    coverage = d_type_coverage(type("C", (), {"workspace": {"risks": [risk]}})(), None)
    assert coverage[1] is False
    assert "新创建风险 1 条" in coverage[2]
