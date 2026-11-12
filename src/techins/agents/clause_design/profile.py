"""条款设计智能体配置包（AG-CD）。"""

from __future__ import annotations

from ...harness import mailbox
from ...harness.agent import AgentProfile, Dimension, ReflectionRule, register_profile
from ...knowledge.catalog import LINES, STAGES
from ...knowledge.maturity import component_start_mode
from ..common import create_artifact, freeze_artifact
from . import library as lib
from . import llm_bindings  # noqa: F401
from . import reasoning  # noqa: F401
from . import tools  # noqa: F401
from .tools import _clause_tasks, load_artifact

WORKSPACE_KEYS = ["report", "report_ref", "insurable", "non_insurable", "decisions", "recommendation", "capacity", "limits", "features", "tiers", "params", "docs", "consistency", "compliance", "backtests", "outputs", "fix_log", "doc_specs"]


def _initialize(ctx) -> None:
    ref = ctx.inputs.get("risk_report") or {}
    if ref.get("artifact_key") and ctx.profile.procedure_name is None:
        art = load_artifact(ref["artifact_key"], ref.get("version"))
        ent = art["data"].get("enterprise", {})
        ctx.workspace["memory_subjects"] = [ent.get("name")] if ent.get("id") else []
        ctx.workspace["memory_query"] = f"{ent.get('track', '')} 条款设计 " + " ".join(r["title"] for r in art["data"].get("risks", [])[:8])


def _start_mode(inputs: dict) -> dict:
    if inputs.get("spec"):
        return {"start_mode": inputs.get("start_mode") or "warm", "degrade_level": "D0", "reasons": ["条款生成子任务"]}
    lines = list(inputs.get("target_lines") or [])
    ref = inputs.get("risk_report") or {}
    if not lines and ref.get("artifact_key"):
        try:
            data = load_artifact(ref["artifact_key"], ref.get("version"))["data"]
            lines = [m["line"] for m in (data.get("insurability") or {}).get("line_needs", [])][:6]
        except Exception:  # noqa: BLE001
            lines = []
    sm = component_start_mode(lines, "clause")
    return {"start_mode": sm["start_mode"], "degrade_level": sm["degrade_level"], "reasons": sm["reasons"]}


def _docs(ctx) -> list[dict]:
    return ctx.workspace.get("docs", [])


# ---------------------------------------------------------------- 评估维度
def d_four_elements(ctx, _):
    bad = []
    for d in _docs(ctx):
        keys = {ch["key"] for ch in d["chapters"] if ch["articles"]}
        missing = [label for key, label in lib.FOUR_ELEMENTS.items() if key not in keys]
        if missing:
            bad.append(f"{d['name']}缺{'、'.join(missing)}")
    return len(bad), not bad and bool(_docs(ctx)), "；".join(bad) or "全部险种四要素完整"


def d_compliance(ctx, _):
    rules = (ctx.workspace.get("compliance") or {}).get("rules", [])
    fails = [f"{r['name']} {c['code']}" for r in rules for c in r["checks"] if not c["passed"]]
    return len(fails), bool(rules) and not fails, "未通过：" + "、".join(fails) if fails else "C01–C16 全部通过"


def d_risk_link(ctx, _):
    bad = [f"{d['name']}{a['no']}" for d in _docs(ctx) for ch in d["chapters"] if ch["key"] == "liability" for a in ch["articles"] if not a["risk_ids"]]
    return len(bad), not bad, "未关联风险：" + "、".join(bad) if bad else "每项保险责任均关联风险编号"


def d_high_risk(ctx, _):
    matrix = (ctx.workspace.get("outputs") or {}).get("coverage_matrix", [])
    bad = [row["title"] for row in matrix if row["level"] in ("高", "极高") and not any(c["status"] == "covered" for c in row["cells"].values()) and not row.get("uncovered_reason")]
    return len(bad), not bad, "未处置：" + "、".join(bad) if bad else "高风险均已保障或说明原因"


def d_stages(ctx, _):
    stages = (ctx.workspace.get("recommendation") or {}).get("stages", {})
    return len(stages), all(s in stages and stages[s].get("conclusion") for s in STAGES), "三阶段均有推荐结论"


def d_coverage(ctx, _):
    matrix = (ctx.workspace.get("outputs") or {}).get("coverage_matrix", [])
    risks = {r["id"]: r for r in ctx.workspace.get("insurable", [])}
    total = sum(risks[row["risk_id"]].get("score", 1) for row in matrix if row["risk_id"] in risks)
    covered = sum(risks[row["risk_id"]].get("score", 1) for row in matrix if row["risk_id"] in risks and any(c["status"] == "covered" for c in row["cells"].values()))
    rate = covered / total if total else 0
    return rate, rate >= 0.8, f"被保障的可保风险值 {covered}/{total}"


def d_overlap(ctx, _):
    open_issues = [i for i in ctx.workspace.get("consistency", []) if not i["resolved"] and i["type"] in ("overlap", "number")]
    return len(open_issues), not open_issues, "；".join(i["message"] for i in open_issues) or "无未协调的重复保障"


def d_params(ctx, _):
    cap = ctx.workspace.get("capacity", {})
    lo, hi = cap.get("deductible_range", [0, float("inf")])
    bad = []
    for d in _docs(ctx):
        ded, limit = d["params"].get("deductible", 0), d["params"].get("limit") or 0
        upper = max(hi * 1.5, 5e4)
        if not (1e4 <= ded <= upper) or (limit and ded > limit * 0.05 + 1):
            bad.append(d["name"])
    return len(bad), not bad, "免赔额超出偏好区间或超过限额的 5%：" + "、".join(bad) if bad else "免赔额落在偏好区间且不超过限额的 5%"


def d_backtest(ctx, _):
    results = ctx.workspace.get("backtests")
    if not results:
        return "无创新条款", True, "无需理赔反推测试"
    total = sum(len(r["scenarios"]) for r in results)
    ok = sum(1 for r in results for s in r["scenarios"] if s["passed"])
    return ok / total if total else 1.0, ok == total, f"反推测试通过 {ok}/{total}"


def d_severe_issues(ctx, _):
    compliance = ctx.workspace.get("compliance") or {}
    rules = compliance.get("rules") or []
    rule_failed = any(not check.get("passed", True) for report in rules for check in report.get("checks", []))
    issues = compliance.get("issues") or []
    high = [item for item in issues if item.get("severity") == "high"]
    detail = "；".join(f"{item.get('line', '')}{item.get('article', '')}：{item.get('issue', '')}" for item in high) or "无严重合规问题"
    if not rule_failed:
        return len(high), True, ("规则已通过。模型审查意见仅记录：" + detail) if high else "规则已通过，无严重合规问题"
    return len(high), not high, detail


def _builtin_scores(ctx) -> dict:
    cached = ctx.workspace.get("_builtin_scores")
    if cached is None:
        cached = reasoning.review(_review_inputs(ctx))
        ctx.workspace["_builtin_scores"] = cached
    return cached


def _review_dim(key: str, threshold: float):
    def fn(ctx, aux):
        item = (_builtin_scores(ctx).get("scores") or {}).get(key)
        if not item:
            return None, False, aux.get("review_error", "评审不可用")
        return item["score"], item["score"] >= threshold, item["reason"]

    return fn


def _review_inputs(ctx) -> dict:
    docs = []
    for d in _docs(ctx):
        liab_text = "\n".join(a["text"] for ch in d["chapters"] if ch["key"] == "liability" for a in ch["articles"])
        settle = "\n".join(a["text"] for ch in d["chapters"] if ch["key"] == "settlement" for a in ch["articles"])
        all_text = lib.doc_text(d)
        docs.append(
            {
                "name": d["name"],
                "special_count": sum(len(ch["articles"]) for ch in d["chapters"] if ch["key"] == "special"),
                "linked_liabilities": sum(1 for ch in d["chapters"] if ch["key"] == "liability" for a in ch["articles"] if a["risk_ids"]),
                "ambiguous": sum(liab_text.count(w) for w in reasoning.AMBIGUOUS),
                "one_sided": sum(1 for p in reasoning.ONE_SIDED for _ in __import__("re").finditer(p, all_text)),
                "has_formula": any(x in settle for x in ("=", "乘以", "×", "按照实际修复费用", "以生效判决", "扣除免赔额", "核定的", "为依据计算")),
                "has_materials": "提供" in settle or "提交" in settle or "提供" in all_text,
                "has_timeline": "三十日" in all_text,
                "liability_text": liab_text[:3000],
            }
        )
    return {"docs": docs, "_level": 1}


FINAL_RUBRIC = [
    Dimension("four_elements", "四要素完整", "hard", d_four_elements, "100%", category="格式与契约错误"),
    Dimension("compliance", "强制合规规则 C01–C16", "hard", d_compliance, "全部通过", category="格式与契约错误"),
    Dimension("risk_link", "保险责任关联风险", "hard", d_risk_link, "100%", category="推理错误"),
    Dimension("high_risk", "高风险处置", "hard", d_high_risk, "100%", category="约束冲突"),
    Dimension("stages", "三阶段推荐结论", "hard", d_stages, "100%", category="推理错误"),
    Dimension("coverage", "标准档风险覆盖率", "metric", d_coverage, "≥ 80%", weight=2, category="约束冲突"),
    Dimension("overlap", "未协调的重复保障", "metric", d_overlap, "0", category="格式与契约错误"),
    Dimension("params", "参数在偏好区间内", "metric", d_params, "100%", category="约束冲突"),
    Dimension("backtest", "创新条款理赔反推", "metric", d_backtest, "100%", category="推理错误"),
    Dimension("severe_issues", "严重合规问题", "metric", d_severe_issues, "0", weight=2, category="格式与契约错误"),
    Dimension("personalization", "个性化", "review", _review_dim("personalization", 4), "≥ 4 分"),
    Dimension("clarity", "清晰无歧义", "review", _review_dim("clarity", 4), "≥ 4 分"),
    Dimension("fairness", "公平性", "review", _review_dim("fairness", 4), "≥ 4 分"),
    Dimension("payability", "可赔付性", "review", _review_dim("payability", 4), "≥ 4 分"),
]

STAGE_RUBRICS = {
    "check_compliance": [Dimension("compliance", "强制合规规则 C01–C16", "hard", d_compliance, "全部通过", category="格式与契约错误")],
}

def _check_steps() -> list[dict]:
    return [
        {"content": "一致性检查", "tool": "check_consistency"},
        {"content": "合规校验", "tool": "check_compliance"},
        {"content": "合规自动整改", "tool": "auto_fix_compliance"},
        {"content": "合规复核校验", "tool": "check_compliance"},
        {"content": "更新条款方案产物", "tool": "compose_clause_outputs"},
    ]


def _text_fix_steps() -> list[dict]:
    return [
        {"content": "合规自动整改", "tool": "auto_fix_compliance"},
        {"content": "合规复核校验", "tool": "check_compliance", "args": {"rules_only": "1"}},
        {"content": "更新条款方案产物", "tool": "compose_clause_outputs"},
    ]


def _regen_steps(expand: bool) -> list[dict]:
    steps = []
    if expand:
        steps += [{"content": "扩大覆盖重新推荐险种", "tool": "recommend_products", "args": {"expand": "1"}}, {"content": "重新生成三档方案", "tool": "build_tier_plans"}]
    steps += [{"content": "重新生成条款", "tool": "spawn_subagents", "args": {"tasks": "@clause_tasks", "group": "clauses"}}, {"content": "汇总条款草稿", "tool": "collect_clause_drafts"}]
    return steps + _check_steps() + [{"content": "理赔反推测试", "tool": "claim_backtest", "args": {"fix": "1"}}]


REFLECTION_RULES = [
    ReflectionRule(["compliance", "four_elements", "overlap"], "格式与契约错误", "按规则定位条款位置自动整改并重新校验", lambda ctx, info: _text_fix_steps()),
    ReflectionRule(["*human"], "约束冲突", "按人工意见调整险种组合", lambda ctx, info: _regen_steps(True)),
    ReflectionRule(["*human"], "推理错误", "按人工意见重新生成条款", lambda ctx, info: _regen_steps(False)),
]


# ---------------------------------------------------------------- 人审节点
def _recommend_card(ctx) -> dict:
    rec = ctx.workspace["recommendation"]
    return {
        "card_type": "option_select",
        "title": "确认产品推荐方案",
        "reason": f"{rec['summary']}推荐险种：{'、'.join(i['name'] for i in rec['items'])}；未覆盖风险 {len(rec['uncovered'])} 条。",
        "impact": "确认后按推荐方案生成条款",
        "context": {"items": rec["items"], "structure": rec["structure"], "uncovered": rec["uncovered"]},
        "options": [{"key": "approve", "label": "按推荐方案生成条款", "primary": True}, {"key": "option:expand", "label": "扩大覆盖后生成"}, {"key": "reject", "label": "调整后重新推荐", "requires_comment": True}],
        "assignee_role": "designer",
        "mandatory": True,
        "default_action": "approve",
        "deadline_hours": 48,
    }


def _apply_recommend(ctx, res: dict) -> str:
    edits = res.get("edits") or {}
    removed = set(edits.get("remove_lines", []))
    if removed:
        rec = ctx.workspace["recommendation"]
        rec["items"] = [i for i in rec["items"] if i["line"] not in removed]
        structure = lib.trim_structure(rec["structure"], removed)
        rec["structure"] = structure
        rec["main_count"] = structure.get("main_count", len(rec["items"]))
        rec["rider_count"] = structure.get("rider_count", 0)
        roles, attached = lib.structure_roles(structure)
        for item in rec["items"]:
            item["role"] = roles.get(item["line"], item.get("role") or "主险")
            if item["line"] in attached:
                item["attached_to"] = attached[item["line"]]
                item["attached_to_name"] = LINES.get(attached[item["line"]], {}).get("name") or attached[item["line"]]
            else:
                item.pop("attached_to", None)
                item.pop("attached_to_name", None)
    if res.get("action") == "option:expand":
        from ...harness.todo import TodoItem

        cur = ctx.todos.current()
        ctx.todos.insert_after(cur.id if cur else None, [TodoItem(id=ctx.todos.new_id(), content="扩大覆盖重新推荐险种", tool="recommend_products", args={"expand": "1"}, origin="human"), TodoItem(id=ctx.todos.new_id(), content="重新生成三档方案", tool="build_tier_plans", origin="human")])
        return "按人工选择扩大覆盖范围"
    return "推荐方案已确认" + (f"，移除险种 {len(removed)} 个" if removed else "")


def clause_preview(docs: list[dict]) -> list[dict]:
    """人审卡片上展示的条款正文，只保留阅读所需字段。"""
    preview = []
    for doc in docs:
        chapters = []
        for chapter in doc.get("chapters") or []:
            articles = [
                {
                    "no": article.get("no"),
                    "title": article.get("title") or "",
                    "text": article.get("text") or "",
                    "origin": article.get("origin") or "",
                    "bold": bool(article.get("bold")),
                    "risk_ids": list(article.get("risk_ids") or []),
                }
                for article in chapter.get("articles") or []
            ]
            if articles:
                chapters.append({"key": chapter.get("key"), "title": chapter.get("title"), "articles": articles})
        preview.append({"doc_id": doc.get("doc_id"), "name": doc.get("name"), "kind": doc.get("kind"), "lines": list(doc.get("lines") or []), "innovations": len(doc.get("innovations") or []), "chapters": chapters})
    return preview


def _manual_compliance_card(ctx) -> dict | None:
    docs = _docs(ctx)
    innov = [d for d in docs if d.get("innovations") or d["kind"] == "innovative"]
    cold = ctx.state.get("start_mode") == "cold"
    if not innov and not cold:
        return None
    shown = docs if cold else innov
    names = "、".join(d["name"] for d in shown)
    if cold and innov:
        reason = f"本方案按冷启动生成，且含创新条款，须人工复核已生成的条款：{names}"
    elif innov:
        reason = f"本方案含创新条款，须人工复核：{names}"
    else:
        reason = f"本方案按冷启动生成，须人工复核已生成的条款：{names}"
    issues = ((ctx.workspace.get("compliance") or {}).get("issues") or [])
    high = [i for i in issues if i.get("severity") == "high" and not i.get("resolved")]
    return {
        "card_type": "result_confirm",
        "title": "人工合规复核：创新条款",
        "reason": reason + (f"；另有未整改严重意见 {len(high)} 项，请先核对下方意见再决定是否通过" if high else ""),
        "context": {
            "docs": [d["doc_id"] for d in shown],
            "backtests": ctx.workspace.get("backtests", []),
            "clauses": clause_preview(shown),
            "compliance_issues": issues,
            "start_mode": ctx.state.get("start_mode"),
        },
        "options": [{"key": "approve", "label": "复核通过", "primary": True}, {"key": "reject", "label": "退回修改", "requires_comment": True}],
        "assignee_role": "designer",
        "mandatory": True,
        "deadline_hours": 72,
    }


def _confirm_card(ctx) -> dict:
    out = ctx.workspace.get("final_output", {})
    return {
        "card_type": "result_confirm",
        "title": "确认条款方案",
        "reason": f"共 {len(_docs(ctx))} 份条款：{'、'.join(d['name'] for d in _docs(ctx))}。确认后冻结版本并交由精算定价。",
        "impact": "未冻结的条款不能定价",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "link": f"/design/clauses/{out.get('artifact_key')}"},
        "options": [{"key": "approve", "label": "确认并冻结", "primary": True}, {"key": "reject", "label": "驳回并说明", "requires_comment": True}],
        "assignee_role": "designer",
        "deadline_hours": 72,
    }


def _revision_card(ctx) -> dict:
    out = ctx.workspace.get("final_output", {})
    req = ctx.inputs.get("revision_request", {})
    return {
        "card_type": "result_confirm",
        "title": f"确认条款修订：{req.get('request_no')}",
        "reason": "；".join(ctx.workspace.get("revision_applied", [])) or "修订已完成",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "request_no": req.get("request_no"), "link": f"/design/clauses/{out.get('artifact_key')}"},
        "options": [{"key": "approve", "label": "确认修订并冻结", "primary": True}, {"key": "reject", "label": "驳回修订", "requires_comment": True}],
        "assignee_role": "designer",
        "deadline_hours": 48,
    }


# ---------------------------------------------------------------- 结果
def _artifact_data(ctx) -> dict:
    return {
        "report_ref": ctx.workspace.get("report_ref"),
        "enterprise": ctx.workspace.get("report", {}).get("enterprise"),
        "insurable": ctx.workspace.get("insurable", []),
        "non_insurable": ctx.workspace.get("non_insurable", []),
        "decisions": ctx.workspace.get("decisions", []),
        "recommendation": ctx.workspace.get("recommendation", {}),
        "capacity": ctx.workspace.get("capacity", {}),
        "tiers": ctx.workspace.get("tiers", {}),
        "docs": _docs(ctx),
        "consistency": ctx.workspace.get("consistency", []),
        "compliance": ctx.workspace.get("compliance", {}),
        "backtests": ctx.workspace.get("backtests", []),
        "fix_log": ctx.workspace.get("fix_log", []),
        **(ctx.workspace.get("outputs") or {}),
        "start_mode": ctx.state.get("start_mode"),
        "degrade_level": ctx.state.get("degrade_level"),
        "revision": {"request": ctx.inputs.get("revision_request"), "applied": ctx.workspace.get("revision_applied", []), "base_version": ctx.workspace.get("base_version")} if ctx.inputs.get("revision_request") else None,
        "_workspace": {k: ctx.workspace.get(k) for k in WORKSPACE_KEYS if k in ctx.workspace},
    }


def _finalize(ctx) -> dict:
    data = _artifact_data(ctx)
    names = "、".join(d["name"] for d in data["docs"])
    summary = f"生成条款 {len(data['docs'])} 份：{names}；组合方式：{data['recommendation'].get('structure', {}).get('structure_label', '')}"
    ent = data.get("enterprise") or {}
    art = create_artifact(kind="clause_plan", data=data, title=f"{ent.get('name', '')}条款方案", run_id=ctx.run_id, project_id=ctx.business_ref.get("project_id"), upstream=[ctx.workspace["report_ref"]] if ctx.workspace.get("report_ref") else [], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    ctx.inputs["artifact_key"] = art["artifact_key"]
    return {"summary": summary, "artifact_key": art["artifact_key"], "version": art["version"], "docs": [d["name"] for d in data["docs"]]}


def _on_confirm(ctx, res: dict) -> dict:
    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    from ...services import pipeline

    pipeline.on_stage_confirmed(ctx.business_ref.get("project_id"), "clause", out["artifact_key"], out["version"], ctx.run_id, res.get("user_id"))
    return {"summary": f"条款方案 V{out['version']} 已确认冻结", **out}


def _on_revision_confirm(ctx, res: dict) -> dict:
    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    req = ctx.inputs.get("revision_request", {})
    from ...db import session_scope
    from ...harness.executor import executor
    from ...models import Run
    from ...services import pipeline

    pipeline.update_node(ctx.business_ref.get("project_id"), "clause", status="done", artifact_key=out["artifact_key"], version=out["version"])
    pipeline.add_revision(ctx.business_ref.get("project_id"), {"request_no": req.get("request_no"), "status": "done", "status_label": "已修订", "new_version": out["version"], "applied": ctx.workspace.get("revision_applied", [])})
    if req.get("from_run_id"):
        mailbox.send(kind="revision_reply", payload={"request_no": req.get("request_no"), "artifact_key": out["artifact_key"], "version": out["version"], "applied": ctx.workspace.get("revision_applied", []), "status": "done"}, to_run_id=req["from_run_id"], from_run_id=ctx.run_id, from_agent="AG-CD", request_no=req.get("request_no"))
        with session_scope() as s:
            from ...harness.timing import apply_status

            run = s.get(Run, req["from_run_id"])
            resubmit = run is not None and run.status == "waiting_human" and (run.waiting_for or {}).get("card_type") == "agent"
            if resubmit:
                apply_status(run, "queued")
                run.waiting_for = {}
        if resubmit:
            executor.submit(req["from_run_id"])
    return {"summary": f"修订 {req.get('request_no')} 完成，条款方案 V{out['version']} 已冻结并回复精算定价", **out}


def _entity_facts(ctx) -> list[dict]:
    ent = (ctx.workspace.get("report") or {}).get("enterprise", {})
    if not ent.get("id"):
        return []
    return [{"subject": ent["name"], "facts": {"推荐险种": "、".join(d["name"] for d in _docs(ctx)), "风险偏好": (ctx.workspace.get("capacity") or {}).get("preference_label")}}]


def _case_summary(ctx) -> dict:
    ent = (ctx.workspace.get("report") or {}).get("enterprise", {})
    rec = ctx.workspace.get("recommendation", {})
    return {"subject": ent.get("name"), "title": f"条款设计案例：{ent.get('name')}", "text": f"{ent.get('track')}；险种：{'、'.join(i['name'] for i in rec.get('items', []))}；组合方式：{rec.get('structure', {}).get('structure_label')}", "level": 1}


def _child_finalize(ctx) -> dict:
    doc = ctx.workspace.get("doc")
    return {"summary": f"{doc['name']}条款生成完成" if doc else "条款生成失败", "doc": doc}


BASE = dict(agent_id="AG-CD", version="1.2.0", core_skill="SK-02", start_mode=_start_mode, priority=3, planner="procedure", plan_confirm=False, max_reflection_rounds=1, resolvers={"clause_tasks": _clause_tasks}, conditions={"has_innovation": lambda ctx: any(d["path"] in ("innovate",) for d in ctx.workspace.get("decisions", [])) or ctx.state.get("start_mode") == "cold"})

clause_doc_profile = AgentProfile(name="险种条款生成子智能体", shared_skills=[], description="在独立上下文中为一个险种生成条款。", initialize=lambda ctx: None, finalize=_child_finalize, procedure_name="条款生成", **BASE)

revision_profile = AgentProfile(
    name="条款修订智能体",
    shared_skills=["SK-S3"],
    description="响应精算定价的修订请求，修订条款并重新合规校验。",
    initialize=lambda ctx: None,
    finalize=_finalize,
    final_rubric=[d for d in FINAL_RUBRIC if d.layer != "review"],
    stage_rubrics=STAGE_RUBRICS,
    reflection_rules=REFLECTION_RULES,
    hitl_builders={"result_confirm": _revision_card},
    final_confirm="result_confirm",
    on_confirm=_on_revision_confirm,
    procedure_name="修订运行",
    pass_score=80,
    **BASE,
)

profile = register_profile(
    AgentProfile(
        name="条款设计智能体",
        shared_skills=["SK-S1", "SK-S2", "SK-S3"],
        description="基于已圈定的险种和风险推荐分阶段保险产品，先形成零免赔的产品规则，再按复用、改造、组合或创新路径生成条款正文，完成合规校验、风险覆盖矩阵和通俗解读。",
        initialize=_initialize,
        finalize=_finalize,
        final_rubric=FINAL_RUBRIC,
        stage_rubrics=STAGE_RUBRICS,
        review_inputs=_review_inputs,
        reflection_rules=REFLECTION_RULES,
        hitl_builders={"option_select": _recommend_card, "manual_compliance": _manual_compliance_card, "result_confirm": _confirm_card},
        hitl_appliers={"option_select": _apply_recommend},
        final_confirm="result_confirm",
        on_confirm=_on_confirm,
        entity_facts=_entity_facts,
        case_summary=_case_summary,
        pass_score=80,
        purposes={"clause_doc": clause_doc_profile, "revision": revision_profile},
        **BASE,
    )
)
