"""风险评估智能体配置包（AG-RA）。"""

from __future__ import annotations

import random
from collections import Counter

from ...harness.agent import AgentProfile, Dimension, ReflectionRule, register_profile
from ...knowledge.catalog import INDUSTRY_PROFILES, RISK_TAXONOMY, STAGES
from ...knowledge.maturity import component_start_mode
from ...textutil import cosine_tokens
from ..common import create_artifact, freeze_artifact, load_enterprises, load_materials, verify_card
from . import llm_bindings  # noqa: F401 - 大模型上下文与输出校验
from . import reasoning  # noqa: F401 - 注册推理任务
from . import tools  # noqa: F401 - 注册工具

SOURCES = {"patent": "专利检索", "news": "新闻舆情", "financial": "财报与工商", "report": "行业报告", "policy": "政策检索"}


# ---------------------------------------------------------------- 初始化与条件
def _initialize(ctx) -> None:
    ids = ctx.inputs.get("enterprise_ids") or []
    ents = load_enterprises(ids)
    if ctx.inputs.get("mode") == "need" and not ents:
        background = str(ctx.inputs.get("background") or "").strip()
        customer_need = str(ctx.inputs.get("customer_need") or "").strip()
        text = customer_need or background or "产品开发需求"
        ctx.workspace["enterprise"] = {
            "id": None,
            "name": str(ctx.inputs.get("subject_name") or text[:40]),
            "track": "产品开发",
            "tech_field": "需求描述",
            "region": "",
            "industry": "",
            "profile": {"products": [{"name": text[:80], "trl": 4, "description": f"{background} {customer_need}".strip() or text}]},
        }
        ctx.workspace["memory_query"] = f"产品开发 {text[:80]} 风险评估"
        return
    if not ents:
        raise ValueError("评估对象不存在")
    if ctx.inputs.get("mode") == "track":
        track = ctx.inputs.get("track") or ents[0]["track"]
        products = [p for e in ents for p in e["profile"].get("products", [])][:6]
        ctx.workspace["enterprise"] = {"id": None, "name": f"{track}赛道", "track": track, "tech_field": "、".join(sorted({e["tech_field"].split("、")[0] for e in ents})), "profile": {"products": products}}
        ctx.workspace["samples"] = [{"id": e["id"], "name": e["name"]} for e in ents]
    else:
        ctx.workspace["enterprise"] = ents[0]
        ctx.workspace["memory_subjects"] = [ents[0]["name"]]
    ent = ctx.workspace["enterprise"]
    ctx.workspace["memory_query"] = f"{ent['track']} {ent['tech_field']} 风险评估"


def _is_track(ctx) -> bool:
    return ctx.inputs.get("mode") == "track"


def _has_private(ctx) -> bool:
    if ctx.inputs.get("material_ids"):
        return True
    ent = ctx.workspace.get("enterprise", {})
    return bool(ent.get("id") and load_materials(enterprise_id=ent["id"]))


def _start_mode(inputs: dict) -> dict:
    if inputs.get("source"):
        return {"start_mode": "warm", "degrade_level": "D0", "reasons": ["公开信息采集子任务"]}
    if inputs.get("mode") == "need" and not (inputs.get("enterprise_ids") or []):
        return {"start_mode": "cold", "degrade_level": "D2", "reasons": ["按产品开发背景和客户需求评估，不依赖企业档案"]}
    ents = load_enterprises(inputs.get("enterprise_ids") or [])
    track = inputs.get("track") or (ents[0]["track"] if ents else "")
    reasons = []
    if track in INDUSTRY_PROFILES:
        reasons.append(f"风险知识库已覆盖「{track}」行业风险特征")
        mode, level = "warm", "D0"
    else:
        reasons.append(f"风险知识库未覆盖「{track or '未知'}」行业，采用第一性原理分解与相似行业迁移")
        mode, level = "cold", "D2"
    lines = inputs.get("target_lines") or []
    if lines:
        sm = component_start_mode(lines, "cases")
        reasons += sm["reasons"]
        if sm["start_mode"] == "cold":
            mode = "cold"
            level = max(level, sm["degrade_level"])
    if inputs.get("mode") != "track" and ents and not inputs.get("material_ids") and not load_materials(enterprise_id=ents[0]["id"]):
        reasons.append("未提供私有尽调材料，私有维度以客户档案与推断为主")
        level = max(level, "D1")
    return {"start_mode": mode, "degrade_level": level, "reasons": reasons}


def _public_tasks(ctx) -> list[dict]:
    ent = ctx.workspace["enterprise"]
    enabled = ctx.inputs.get("sources") or list(SOURCES)
    return [
        {"title": SOURCES[s], "purpose": "public_source", "key": s, "inputs": {"source": s, "enterprise_id": ent["id"], "enterprise_name": ent["name"], "track": ent["track"], "tech_field": ent["tech_field"], "years": ctx.inputs.get("years", 3)}}
        for s in enabled
        if s in SOURCES
    ]


def _enterprise_tasks(ctx) -> list[dict]:
    return [
        {"title": f"样本企业评估：{s['name']}", "purpose": "enterprise", "key": str(s["id"]), "inputs": {**{k: v for k, v in ctx.inputs.items() if not k.startswith("_")}, "mode": "enterprise", "enterprise_ids": [s["id"]], "material_ids": []}}
        for s in ctx.workspace.get("samples", [])
    ]


# ---------------------------------------------------------------- 评估维度
def _risks(ctx) -> list[dict]:
    return ctx.workspace.get("risks", [])


def _cold(ctx) -> bool:
    return ctx.state.get("start_mode") == "cold"


def d_has_risks(ctx, _):
    n = len(_risks(ctx))
    return n, n > 0, f"风险清单 {n} 条" if n else "风险清单为空，不能交付"


def d_type_coverage(ctx, _):
    types = {r["type_code"] for r in _risks(ctx) if r.get("type_code") in RISK_TAXONOMY}
    novel = sum(1 for r in _risks(ctx) if r.get("novel"))
    names = "、".join(RISK_TAXONOMY[t]["name"] for t in sorted(types))
    detail = f"已识别类型：{names}" + (f"；另有新创建风险 {novel} 条" if novel else "")
    return len(types), len(types) >= 5, detail


def d_type_conclusion(ctx, _):
    cov = ctx.workspace.get("report", {}).get("coverage", [])
    return len(cov), len(cov) == len(RISK_TAXONOMY), f"{len(RISK_TAXONOMY)} 个一级类型逐一给出结论"


def d_evidence_or_inferred(ctx, _):
    bad = [r["id"] for r in _risks(ctx) if not r.get("evidence_ids") and not r.get("inferred")]
    return len(bad), not bad, ("无证据且未标注推断：" + "、".join(bad)) if bad else "全部满足"


def d_three_stage(ctx, _):
    counts = Counter(r.get("stage") for r in _risks(ctx))
    statements = ctx.workspace.get("report", {}).get("stage_statements", {})
    missing = [STAGES[s] for s in STAGES if not counts.get(s) and s not in statements]
    return 3 - len(missing), not missing, ("缺少判断：" + "、".join(missing)) if missing else "三阶段均有判断"


def d_insurable_mapped(ctx, _):
    bad = [r["id"] for r in _risks(ctx) if r.get("insurability") in ("yes", "partial") and not r.get("lines") and not r.get("insurability_reason")]
    return len(bad), not bad, "可保风险均有险种映射或说明"


def d_lifecycle_basis(ctx, _):
    if not ctx.inputs.get("lifecycle", True):
        return "未启用", True, "未启用全生命周期评估"
    items = ctx.workspace.get("lifecycle", [])
    ok = bool(items) and all(p.get("basis") for p in items)
    return len(items), ok, "每个产品的当前阶段有判定依据" if ok else "缺少生命周期判定"


def d_evidence_support(ctx, _):
    risks = _risks(ctx)
    pool = ctx.workspace.get("evidence", {})
    ok = sum(1 for r in risks if not r.get("inferred") and any(e in pool and pool[e]["source_type"] != "model" for e in r.get("evidence_ids", [])))
    rate = ok / max(1, len(risks))
    threshold = 0.70 if _cold(ctx) else 0.95
    return rate, rate >= threshold, f"有证据支撑 {ok}/{len(risks)}（阈值 {threshold:.0%}）"


def d_inferred_ratio(ctx, _):
    risks = _risks(ctx)
    rate = sum(1 for r in risks if r.get("inferred")) / max(1, len(risks))
    threshold = 0.60 if _cold(ctx) else 0.30
    return rate, rate <= threshold, f"推断占比 {rate:.0%}（上限 {threshold:.0%}）"


def d_duplicate(ctx, _):
    risks = _risks(ctx)
    pairs = sum(1 for i in range(len(risks)) for j in range(i + 1, len(risks)) if cosine_tokens(risks[i]["description"], risks[j]["description"]) > 0.9)
    rate = pairs / max(1, len(risks))
    return rate, rate <= 0.05, f"语义重复 {pairs} 对"


def d_citation(ctx, _):
    pool = ctx.workspace.get("evidence", {})
    refs = [e for r in _risks(ctx) for e in r.get("evidence_ids", []) if e in pool and pool[e]["source_type"] != "model"]
    rng = random.Random(ctx.run_id)
    sample = rng.sample(refs, min(20, len(refs))) if refs else []
    verified = sum(1 for e in sample if verify_card(pool[e]))
    rate = verified / len(sample) if sample else 1.0
    return rate, rate >= 0.98, f"抽查 {len(sample)} 处引用，核验通过 {verified} 处"


def _review_dim(key: str, threshold: float):
    def fn(ctx, aux):
        review = (aux.get("review") or {}).get("scores", {}).get(key)
        if not review:
            return None, False, aux.get("review_error", "评审不可用")
        return review["score"], review["score"] >= threshold, review["reason"]

    return fn


def _review_inputs(ctx) -> dict:
    ent = ctx.workspace.get("enterprise", {})
    p = ent.get("profile", {})
    terms = [x["name"] for x in p.get("products", [])] + [x["name"] for x in p.get("equipment", [])] + [x["item"] for x in p.get("suppliers", [])] + [ent.get("name", "")]
    return {
        "risks": [{"title": r["title"], "subtype": r["subtype"], "description": r["description"], "evidence_count": len(r.get("evidence_ids", [])), "inferred": r.get("inferred", False), "level": r.get("level", "中"), "stage": r.get("stage"), "allowed": r.get("allowed_stages", []), "advice": r.get("advice", "")} for r in _risks(ctx)],
        "industry_prior": INDUSTRY_PROFILES.get(ent.get("track", ""), {}).get("high_incidence", []),
        "enterprise_terms": terms,
        "_level": 2,
    }


FINAL_RUBRIC = [
    Dimension("has_risks", "风险清单非空", "hard", d_has_risks, "≥ 1", category="证据不足"),
    Dimension("type_coverage", "风险类型覆盖（≥5 种）", "hard", d_type_coverage, "≥ 5", category="证据不足"),
    Dimension("type_conclusion", "全部一级风险类型均有结论", "hard", d_type_conclusion, "100%", category="格式与契约错误"),
    Dimension("evidence_or_inferred", "证据或推断标注", "hard", d_evidence_or_inferred, "100%", category="证据不足"),
    Dimension("three_stage", "三阶段判断", "hard", d_three_stage, "100%", category="推理错误"),
    Dimension("insurable_mapped", "可保风险险种映射", "hard", d_insurable_mapped, "100%", category="格式与契约错误"),
    Dimension("lifecycle_basis", "生命周期判定依据", "hard", d_lifecycle_basis, "100%", category="推理错误"),
    Dimension("evidence_support", "证据支撑率", "metric", d_evidence_support, "热 ≥95% / 冷 ≥70%", weight=2, category="证据不足"),
    Dimension("inferred_ratio", "推断占比", "metric", d_inferred_ratio, "热 ≤30% / 冷 ≤60%", category="证据不足"),
    Dimension("duplicate", "重复率", "metric", d_duplicate, "≤ 5%", category="格式与契约错误"),
    Dimension("citation", "引用真实性", "evidence", d_citation, "≥ 98%", category="检索偏差"),
    Dimension("specificity", "具体性", "review", _review_dim("specificity", 4), "≥ 4 分"),
    Dimension("completeness", "完整性", "review", _review_dim("completeness", 4), "≥ 4 分", category="证据不足"),
    Dimension("consistency", "一致性", "review", _review_dim("consistency", 4), "≥ 4 分"),
    Dimension("stage_reasonableness", "阶段归类合理性", "review", _review_dim("stage_reasonableness", 4), "≥ 4 分"),
    Dimension("actionability", "建议可操作性", "review", _review_dim("actionability", 3.5), "≥ 3.5 分"),
]


def s_candidates_have_evidence(ctx, _):
    bad = [c["title"] for c in ctx.workspace.get("candidates", []) if not c.get("evidence_ids")]
    return len(bad), not bad, "每条候选风险均引用证据" if not bad else "缺少证据：" + "、".join(bad)


def s_types_found(ctx, _):
    types = {c["subtype"].split("-")[0] for c in ctx.workspace.get("candidates", [])}
    return len(types), len(types) >= 3, f"候选风险覆盖 {len(types)} 个一级类型"


def s_all_scored(ctx, _):
    bad = [r["id"] for r in _risks(ctx) if "score" not in r]
    return len(bad), not bad, "全部风险完成量化" if not bad else "未评分：" + "、".join(bad)


STAGE_RUBRICS = {
    "extract_risks": [
        Dimension("candidates_have_evidence", "候选风险引用证据", "hard", s_candidates_have_evidence, "100%", category="证据不足"),
        Dimension("types_found", "候选风险类型数", "metric", s_types_found, "≥ 3", category="证据不足"),
    ],
    "score_risks": [Dimension("all_scored", "全部风险完成量化", "hard", s_all_scored, "100%", category="格式与契约错误")],
}


# ---------------------------------------------------------------- 反思规则
def _downstream(include_extract: bool = True) -> list[dict]:
    steps = []
    if include_extract:
        steps.append({"content": "对新增证据增量抽取风险", "tool": "extract_risks", "args": {"scope": "new"}})
    steps += [
        {"content": "重新归一化与去重", "tool": "normalize_risks"},
        {"content": "重新三阶段归类", "tool": "classify_stages"},
        {"content": "重新量化评分", "tool": "score_risks"},
        {"content": "更新风险图谱", "tool": "build_risk_graph"},
        {"content": "更新可保性分析", "tool": "analyze_insurability"},
        {"content": "更新风险评估报告", "tool": "compose_risk_report"},
    ]
    return steps


def _coverage_fix(ctx, info) -> list[dict]:
    missing = [c["code"] for c in ctx.workspace.get("report", {}).get("coverage", []) if c["status"] != "identified"]
    ctx.workspace["supplement_requested"] = missing
    steps = [{"content": f"定向补充检索：{'、'.join(RISK_TAXONOMY[m]['name'] for m in missing[:5])}", "tool": "targeted_retrieval", "args": {"types": ",".join(missing)}}]
    steps += [{"content": "对新增证据增量抽取风险", "tool": "extract_risks", "args": {"scope": "new"}}]
    steps += [{"content": "请求补充材料", "hitl": "supplement"}]
    steps += [{"content": "对仍无证据的类型做第一性原理推断", "tool": "infer_missing_risks", "args": {"min_types": "5"}}]
    return steps + _downstream(include_extract=False)


REFLECTION_RULES = [
    ReflectionRule(["type_coverage", "types_found"], "证据不足", "针对未覆盖类型定向检索；仍不足时请求补充材料，无法提供则用第一性原理推断补齐并标注", _coverage_fix),
    ReflectionRule(["evidence_support", "evidence_or_inferred", "citation", "candidates_have_evidence"], "检索偏差", "回到原始来源核验引用，删除不实引用并把无证据风险改标为推断", lambda ctx, info: [{"content": "证据回检", "tool": "recheck_evidence"}, {"content": "更新风险评估报告", "tool": "compose_risk_report"}]),
    ReflectionRule(["completeness"], "证据不足", "对照行业高发风险清单补充遗漏项", lambda ctx, info: [{"content": "按行业先验补齐遗漏风险", "tool": "infer_missing_risks", "args": {"min_types": "6"}}] + _downstream(include_extract=False)),
    ReflectionRule(["consistency", "all_scored"], "推理错误", "重新执行评分，要求每次调整说明证据", lambda ctx, info: [{"content": "严格模式重新评分", "tool": "score_risks", "args": {"strict": "1"}}, {"content": "更新风险评估报告", "tool": "compose_risk_report"}]),
    ReflectionRule(["stage_reasonableness", "three_stage"], "推理错误", "重新执行阶段归类", lambda ctx, info: [{"content": "重新三阶段归类", "tool": "classify_stages", "args": {"strict": "1"}}, {"content": "更新风险评估报告", "tool": "compose_risk_report"}]),
    ReflectionRule(["*human"], "证据不足", "按人工意见补充检索与推断", lambda ctx, info: [{"content": "按人工意见定向补充检索", "tool": "targeted_retrieval", "args": {}}] + _downstream()),
    ReflectionRule(["*human"], "推理错误", "按人工意见重新归类与评分", lambda ctx, info: [{"content": "重新三阶段归类", "tool": "classify_stages", "args": {"strict": "1"}}, {"content": "严格模式重新评分", "tool": "score_risks", "args": {"strict": "1"}}, {"content": "更新风险评估报告", "tool": "compose_risk_report"}]),
]


# ---------------------------------------------------------------- 人审节点
def _confirm_card(ctx) -> dict:
    out = ctx.workspace.get("final_output", {})
    stats = ctx.workspace.get("report", {}).get("stats", {})
    return {
        "card_type": "result_confirm",
        "title": f"确认风险清单：{ctx.workspace['enterprise']['name']}",
        "reason": f"识别风险 {stats.get('total', 0)} 条（高及以上 {stats.get('high', 0)} 条），覆盖 {stats.get('types_covered', 0)} 个风险类型。确认后报告冻结，作为条款设计的输入。",
        "impact": "未确认的报告不能被条款设计引用",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "link": f"/design/risk/{out.get('artifact_key')}"},
        "options": [{"key": "approve", "label": "确认并冻结报告", "primary": True}, {"key": "reject", "label": "驳回并说明", "requires_comment": True}],
        "assignee_role": "designer",
        "deadline_hours": 72,
    }


def _supplement_card(ctx) -> dict | None:
    missing = ctx.workspace.get("supplement_requested", [])
    if not missing:
        return None
    return {
        "card_type": "supplement",
        "title": "补充材料以覆盖缺失的风险类型",
        "reason": f"以下风险类型缺少证据：{'、'.join(RISK_TAXONOMY[m]['name'] for m in missing)}。可上传相关材料，或选择无法提供后由智能体按第一性原理推断并标注。",
        "impact": "不补充时，相关风险以推断结论输出，置信度较低",
        "context": {"missing": missing, "enterprise_id": ctx.workspace["enterprise"].get("id")},
        "options": [{"key": "upload", "label": "已上传补充材料", "primary": True}, {"key": "unavailable", "label": "无法提供，按推断继续"}],
        "mandatory": False,
        "default_action": "unavailable",
        "assignee_role": "designer",
        "deadline_hours": 24,
    }


def _apply_supplement(ctx, res: dict) -> str:
    ids = (res.get("edits") or {}).get("material_ids") or []
    if res.get("action") == "upload" and ids:
        ctx.inputs["material_ids"] = list(dict.fromkeys((ctx.inputs.get("material_ids") or []) + ids))
        from ...harness.todo import TodoItem

        current = ctx.todos.current()
        ctx.todos.insert_after(current.id if current else None, [TodoItem(id=ctx.todos.new_id(), content="处理补充材料", tool="process_private_materials", origin="human")])
        return f"已接收补充材料 {len(ids)} 份"
    return "无法提供补充材料，按推断继续"


# ---------------------------------------------------------------- 结果与记忆
def _artifact_data(ctx) -> dict:
    from ...privacy.narrative import finalize_narrative

    report = finalize_narrative(ctx.workspace.get("report", {}), ctx.vault)
    ent = ctx.workspace["enterprise"]
    data = {
        "enterprise": {k: ent.get(k) for k in ("id", "name", "track", "tech_field", "region", "industry")},
        "mode": ctx.inputs.get("mode", "enterprise"),
        "summary": report.get("summary"),
        "highlights": report.get("highlights", []),
        "uncertainties": report.get("uncertainties", []),
        "management_focus": report.get("management_focus", []),
        "stats": report.get("stats", {}),
        "coverage": report.get("coverage", []),
        "matrix": report.get("matrix", {}),
        "heatmap": report.get("heatmap", []),
        "stage_statements": report.get("stage_statements", {}),
        "gaps": report.get("gaps", []),
        "risks": finalize_narrative(_risks(ctx), ctx.vault),
        "evidence": finalize_narrative(ctx.workspace.get("evidence", {}), ctx.vault),
        "graph": ctx.workspace.get("graph", {}),
        "lifecycle": ctx.workspace.get("lifecycle", []),
        "insurability": ctx.workspace.get("insurability", {}),
        "track": ctx.workspace.get("track_summary"),
        "egress_plan": ctx.workspace.get("egress_plan"),
        "records": {"stage": ctx.workspace.get("stage_records", []), "score": ctx.workspace.get("score_records", []), "merges": ctx.workspace.get("merges", [])},
        "start_mode": ctx.state.get("start_mode"),
        "degrade_level": ctx.state.get("degrade_level"),
        "degrade_notes": ctx.state.get("degrade_notes", []),
    }
    ctx.workspace["report"] = report
    if ctx.inputs.get("mode") == "need":
        background = str(ctx.inputs.get("background") or "").strip()
        customer_need = str(ctx.inputs.get("customer_need") or "").strip()
        data["scene_summary"] = f"{background} {customer_need}".strip()
        data["demand_items"] = []
        if customer_need:
            data["demand_items"].append({"text": customer_need, "status": "明确诉求"})
        if background:
            data["demand_items"].append({"text": background, "status": "系统推断"})
        data["probability"] = None
        data["expected_claim"] = None
        data["rate"] = None
        data["underwriting_decision"] = None
        data["score_meaning"] = "可能性与严重性是 1 到 5 的排序分数，不是发生概率、赔款或费率"
    return data


def _finalize(ctx) -> dict:
    data = _artifact_data(ctx)
    summary = data.get("summary") or "风险评估完成"
    if ctx.parent_run_id:
        return {"summary": summary, "data": data}
    art = create_artifact(
        kind="risk_report",
        data=data,
        title=f"{data['enterprise']['name']}风险评估报告",
        run_id=ctx.run_id,
        project_id=ctx.business_ref.get("project_id"),
        ref_type="enterprise" if data["mode"] != "track" else "track",
        ref_id=data["enterprise"].get("id"),
        key=ctx.inputs.get("artifact_key"),
        created_by=ctx.created_by,
        vault=ctx.vault,
    )
    ctx.inputs["artifact_key"] = art["artifact_key"]
    return {"summary": summary, "artifact_key": art["artifact_key"], "version": art["version"], "stats": data["stats"]}


def _on_confirm(ctx, res: dict) -> dict:
    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    from ...services import pipeline

    pipeline.on_stage_confirmed(ctx.business_ref.get("project_id"), "risk", out["artifact_key"], out["version"], ctx.run_id, res.get("user_id"))
    return {"summary": f"风险评估报告 V{out['version']} 已确认并冻结", **out}


def _entity_facts(ctx) -> list[dict]:
    ent = ctx.workspace.get("enterprise", {})
    if not ent.get("id"):
        return []
    risks = sorted(_risks(ctx), key=lambda r: -r.get("score", 0))
    report = ctx.workspace.get("report", {})
    return [{"subject": ent["name"], "facts": {"总体风险等级": report.get("stats", {}).get("overall_level"), "高风险": "、".join(r["title"] for r in risks if r.get("level") in ("高", "极高"))[:120], "技术领域": ent.get("tech_field")}}]


def _case_summary(ctx) -> dict:
    ent = ctx.workspace.get("enterprise", {})
    risks = sorted(_risks(ctx), key=lambda r: -r.get("score", 0))
    return {"subject": ent.get("name"), "title": f"风险评估案例：{ent.get('name')}", "text": f"{ent.get('track')} {ent.get('tech_field')}；主要风险：" + "；".join(f"{r['title']}（{r.get('level')}）" for r in risks[:6]), "level": 2}


def _child_finalize(ctx) -> dict:
    return {"summary": f"采集证据卡片 {len(ctx.workspace.get('cards', []))} 张", "cards": ctx.workspace.get("cards", [])}


BASE = dict(
    agent_id="AG-RA",
    version="1.3.0",
    core_skill="SK-01",
    start_mode=_start_mode,
    conditions={"is_track": _is_track, "has_private": _has_private, "lifecycle_enabled": lambda ctx: bool(ctx.inputs.get("lifecycle", True)), "cold_start": lambda ctx: ctx.state.get("start_mode") == "cold"},
    resolvers={"public_source_tasks": _public_tasks, "enterprise_tasks": _enterprise_tasks},
    priority=3,
)

public_source_profile = AgentProfile(
    name="公开信息采集子智能体",
    shared_skills=[],
    description="在独立上下文中采集一类公开信息源并生成证据卡片，只回传卡片。",
    initialize=lambda ctx: None,
    finalize=_child_finalize,
    procedure_name="公开信息采集",
    **BASE,
)

enterprise_profile = AgentProfile(
    name="样本企业评估子智能体",
    shared_skills=["SK-S1", "SK-S2", "SK-S3"],
    description="对单家样本企业完成企业级风险评估，只回传结构化结果。",
    initialize=_initialize,
    finalize=_finalize,
    final_rubric=FINAL_RUBRIC,
    stage_rubrics=STAGE_RUBRICS,
    review_task="ra.review",
    review_inputs=_review_inputs,
    reflection_rules=REFLECTION_RULES,
    hitl_builders={"supplement": _supplement_card},
    hitl_appliers={"supplement": _apply_supplement},
    pass_score=75,
    **BASE,
)

profile = register_profile(
    AgentProfile(
        name="风险评估智能体",
        shared_skills=["SK-S1", "SK-S2", "SK-S3"],
        description="融合公开信息与尽调、访谈等私有信息，按科技保险风险分类体系提取研发、成果转化、产业化阶段的风险，给出可能性与严重度评分、可保性与险种建议，输出一级风险类型覆盖、企业风险图谱与全生命周期评估。",
        initialize=_initialize,
        finalize=_finalize,
        final_rubric=FINAL_RUBRIC,
        stage_rubrics=STAGE_RUBRICS,
        review_task="ra.review",
        review_inputs=_review_inputs,
        reflection_rules=REFLECTION_RULES,
        hitl_builders={"result_confirm": _confirm_card, "supplement": _supplement_card},
        hitl_appliers={"supplement": _apply_supplement},
        final_confirm="result_confirm",
        on_confirm=_on_confirm,
        entity_facts=_entity_facts,
        case_summary=_case_summary,
        pass_score=75,
        purposes={"public_source": public_source_profile, "enterprise": enterprise_profile},
        **BASE,
    )
)
