"""精算定价智能体配置包（AG-AP）。"""

from __future__ import annotations

from ...harness.agent import AgentProfile, Dimension, ReflectionRule, register_profile
from ...harness.todo import TodoItem
from ...knowledge.catalog import LINES
from ...knowledge.maturity import component_start_mode
from ..common import create_artifact, freeze_artifact
from . import llm_bindings  # noqa: F401
from . import reasoning  # noqa: F401
from . import tools  # noqa: F401
from .tools import load_artifact


def _start_mode(inputs: dict) -> dict:
    ref = inputs.get("clause_plan") or {}
    lines: list[str] = []
    if ref.get("artifact_key"):
        try:
            data = load_artifact(ref["artifact_key"], ref.get("version"))["data"]
            lines = sorted({l for p in data.get("pricing_params", []) for l in p["lines"] if l in LINES})
        except Exception:  # noqa: BLE001
            lines = []
    sm = component_start_mode(lines, "pricing")
    return {"start_mode": sm["start_mode"], "degrade_level": sm["degrade_level"], "reasons": sm["reasons"]}


def _initialize(ctx) -> None:
    ref = ctx.inputs.get("clause_plan") or {}
    if ref.get("artifact_key"):
        data = load_artifact(ref["artifact_key"], ref.get("version"))["data"]
        ent = data.get("enterprise") or {}
        ctx.workspace["memory_subjects"] = [ent.get("name")] if ent.get("id") else []
        ctx.workspace["memory_query"] = "精算定价 " + " ".join(p["name"] for p in data.get("pricing_params", []))


def _comps(ctx) -> list[dict]:
    return ctx.workspace.get("components", [])


def _rounds(ctx) -> int:
    exp = ctx.inputs.get("experiment") or {}
    return max(2, min(10, int(exp.get("rounds", 3)))) if isinstance(exp, dict) else 3


# ---------------------------------------------------------------- 评估维度
def d_methods(ctx, _):
    bad = [c["name"] for c in _comps(ctx) if len([m for m in c.get("models", {}).values() if m.get("pure_rate")]) < 2]
    return len(bad), not bad and bool(_comps(ctx)), "方法数不足：" + "、".join(bad) if bad else "每个组件至少 2 种可解释方法有效"


def d_scenarios(ctx, _):
    bad = [c["name"] for c in _comps(ctx) if len(c.get("scenarios_result") or []) < 5 or any(s["n_sims"] < 10000 for s in c.get("scenarios_result") or [])]
    return len(bad), not bad, "情景数或模拟次数不足：" + "、".join(bad) if bad else "≥5 个情景、每个 ≥10,000 次"


def d_rounds(ctx, _):
    done = sorted({e["round"] for e in ctx.workspace.get("experiments", [])})
    return len(done), len(done) >= _rounds(ctx), f"完成 A/B 迭代 {len(done)} 轮（配置 {_rounds(ctx)} 轮）"


def d_factor_compliance(ctx, _):
    bad = [f"{c['name']}:{s['key']}" for c in _comps(ctx) for s in c.get("screening", []) if s["key"] in c.get("selected", []) and not s.get("compliant", True)]
    return len(bad), not bad, "入模因子均通过合规检查" if not bad else "不合规：" + "、".join(bad)


def d_filing(ctx, _):
    bad = []
    for c in _comps(ctx):
        rm = (c.get("experiment_state") or {}).get("rate_mult", 1.0)
        band = ctx.workspace.get("filing_band", {}).get(c["id"], 0.25)
        if abs(rm - 1) > band:
            bad.append(c["name"])
    return len(bad), not bad, "建议费率在报备区间内" if not bad else "超出区间：" + "、".join(bad)


def d_gini(ctx, _):
    vals = [(c["name"], c["models"]["PM1"]["summary"]["gini"]) for c in _comps(ctx) if c["start_mode"] == "warm" and "PM1" in c.get("models", {})]
    if not vals:
        return "冷启动", True, "冷启动组件不考核区分度"
    worst = min(vals, key=lambda t: t[1])
    return worst[1], worst[1] >= 0.2, "；".join(f"{n} 基尼 {g:.3f}" for n, g in vals)


def d_calibration(ctx, _):
    """校准度只考核热启动组件：测试集按预测值分五组，中间三组实际与预测之比应在 0.7–1.3。"""
    bad, checked = [], []
    for c in _comps(ctx):
        if c["start_mode"] != "warm":
            continue
        cal = (c.get("models", {}).get("PM1") or {}).get("summary", {}).get("calibration")
        if not cal:
            continue
        groups = [cal[i : i + 2] for i in range(0, len(cal), 2)]
        ratios = [sum(g["actual"] for g in grp) / max(sum(g["expected"] for g in grp), 1e-9) for grp in groups]
        checked.append(c["name"])
        if any(not (0.7 <= r <= 1.3) for r in ratios[1:4]):
            bad.append(f"{c['name']}（{'、'.join(f'{r:.2f}' for r in ratios)}）")
    return len(bad), not bad, ("校准偏差：" + "、".join(bad)) if bad else (f"{'、'.join(checked)}中间三组实际与预测之比均在 0.7–1.3" if checked else "无热启动组件")


def d_method_spread(ctx, _):
    bad = [c["name"] for c in _comps(ctx) if c.get("comparison", {}).get("spread", 0) > 0.15 and not c["comparison"].get("analysis")]
    worst = max((c.get("comparison", {}).get("spread", 0) for c in _comps(ctx)), default=0)
    return round(worst, 4), not bad, "方法差异超过 15% 的组件均有差异分析与合成说明"


def d_adequacy(ctx, _):
    cap = ctx.workspace.get("targets", {}).get("combined_cap", 0.95)
    bad = [(c["name"], c["scenarios_result"][0]["combined_ratio"]) for c in _comps(ctx) if c.get("scenarios_result") and c["scenarios_result"][0]["combined_ratio"] > cap]
    return len(bad), not bad, "基准情景综合成本率均不高于上限" if not bad else "；".join(f"{n} {r:.1%}" for n, r in bad)


def d_reasonable(ctx, _):
    out = []
    for c in _comps(ctx):
        bench = LINES.get(c["line"], {}).get("benchmark_rate")
        if bench and c.get("rate"):
            out.append((c["name"], c["rate"]["gross_rate"] / bench, c["start_mode"]))
    bad = [n for n, r, mode in out if not ((0.5 <= r <= 2.0) if mode == "warm" else (0.33 <= r <= 3.0))]
    return round(max((r for _, r, _ in out), default=1.0), 3), not bad, "；".join(f"{n} 与基准费率之比 {r:.2f}（{'热' if m == 'warm' else '冷'}启动区间 {'0.5–2.0' if m == 'warm' else '0.33–3.0'}）" for n, r, m in out)


def d_text_lift(ctx, _):
    lifts = [(c["name"], c["models"]["PM1"].get("factor_lift")) for c in _comps(ctx) if "PM1" in c.get("models", {})]
    lifts = [(n, l) for n, l in lifts if l]
    if not lifts:
        return "无", True, "无文本提取因子入模"
    return round(min(l["improvement"] for _, l in lifts), 4), all(l["improvement"] >= 0 for _, l in lifts), "；".join(f"{n} 加入文本因子后基尼 {l['without_text']:.3f}→{l['with_text']:.3f}" for n, l in lifts)


def _review_dim(key: str, threshold: float):
    def fn(ctx, aux):
        item = (aux.get("review") or {}).get("scores", {}).get(key)
        if not item:
            return None, False, aux.get("review_error", "评审不可用")
        return item["score"], item["score"] >= threshold, item["reason"]

    return fn


FINAL_RUBRIC = [
    Dimension("methods", "可解释定价方法数（≥2）", "hard", d_methods, "≥ 2", category="工具失败"),
    Dimension("scenarios", "情景与模拟次数", "hard", d_scenarios, "≥5 个、≥10,000 次", category="工具失败"),
    Dimension("rounds", "A/B 迭代轮次", "hard", d_rounds, "≥ 配置轮数", category="格式与契约错误"),
    Dimension("factor_compliance", "因子合规", "hard", d_factor_compliance, "100%", category="约束冲突"),
    Dimension("filing", "报备区间", "hard", d_filing, "100%", category="约束冲突"),
    Dimension("gini", "区分度（归一化基尼）", "metric", d_gini, "≥ 0.20", category="推理错误"),
    Dimension("calibration", "校准度", "metric", d_calibration, "0.7–1.3", category="推理错误"),
    Dimension("method_spread", "方法差异说明", "metric", d_method_spread, "≤15% 或有分析", category="推理错误"),
    Dimension("adequacy", "充足性（综合成本率）", "metric", d_adequacy, "≤ 上限", weight=2, category="约束冲突"),
    Dimension("reasonable", "合理性（与基准费率之比）", "metric", d_reasonable, "0.5–2.0", category="约束冲突"),
    Dimension("text_lift", "文本因子提升", "metric", d_text_lift, "≥ 0", category="证据不足"),
    Dimension("report_quality", "精算报告说理", "review", _review_dim("report_quality", 4), "≥ 4 分"),
]

STAGE_RUBRICS = {"fit_pricing_models": [Dimension("methods", "可解释定价方法数（≥2）", "hard", d_methods, "≥ 2", category="工具失败")]}


def _raise_rates(ctx, info) -> list[dict]:
    for c in _comps(ctx):
        if c.get("scenarios_result") and c["scenarios_result"][0]["combined_ratio"] > ctx.workspace["targets"]["combined_cap"]:
            c["comparison"]["blended_pure_rate"] *= 1.12
    return [{"content": "提高纯风险费率后重建费率表", "tool": "build_rate_table"}, {"content": "重新情景模拟", "tool": "run_scenarios"}, {"content": "更新精算报告", "tool": "compose_actuarial_report"}]


REFLECTION_RULES = [
    ReflectionRule(["adequacy"], "约束冲突", "上调纯风险费率并重新模拟验证充足性", _raise_rates),
    ReflectionRule(["methods", "scenarios"], "工具失败", "重新执行建模与情景模拟", lambda ctx, info: [{"content": "重新建模", "tool": "fit_pricing_models"}, {"content": "方法对比", "tool": "compare_methods"}, {"content": "重建费率表", "tool": "build_rate_table"}, {"content": "重新情景模拟", "tool": "run_scenarios"}, {"content": "更新精算报告", "tool": "compose_actuarial_report"}]),
    ReflectionRule(["rounds"], "格式与契约错误", "补足 A/B 迭代轮次", lambda ctx, info: [{"content": f"补充 A/B 实验第 {r} 轮", "tool": "ab_experiment_round", "args": {"round": str(r)}} for r in range(len({e['round'] for e in ctx.workspace.get('experiments', [])}) + 1, _rounds(ctx) + 1)] + [{"content": "更新精算报告", "tool": "compose_actuarial_report"}]),
    ReflectionRule(["gini", "calibration", "text_lift"], "推理错误", "剔除提升为负或区分度弱的因子后重新建模", lambda ctx, info: _refit_with_pruned(ctx)),
    ReflectionRule(["report_quality"], "格式与契约错误", "补充假设披露与依据后重写报告", lambda ctx, info: [{"content": "重写精算报告", "tool": "compose_actuarial_report"}]),
    ReflectionRule(["*human"], "约束冲突", "按精算岗意见调整费率", _raise_rates),
    ReflectionRule(["*human"], "推理错误", "按精算岗意见重新建模", lambda ctx, info: [{"content": "重新建模", "tool": "fit_pricing_models"}, {"content": "方法对比", "tool": "compare_methods"}, {"content": "重建费率表", "tool": "build_rate_table"}, {"content": "重新情景模拟", "tool": "run_scenarios"}, {"content": "更新精算报告", "tool": "compose_actuarial_report"}]),
]


def _refit_with_pruned(ctx) -> list[dict]:
    for c in _comps(ctx):
        scr = {s["key"]: s for s in c.get("screening", [])}
        if len(c.get("selected", [])) > 2:
            weakest = min(c["selected"], key=lambda k: scr.get(k, {}).get("iv", 1))
            c["selected"] = [k for k in c["selected"] if k != weakest]
    return [{"content": "剔除弱因子后重新建模", "tool": "fit_pricing_models"}, {"content": "方法对比", "tool": "compare_methods"}, {"content": "重建费率表", "tool": "build_rate_table"}, {"content": "重新情景模拟", "tool": "run_scenarios"}, {"content": "更新精算报告", "tool": "compose_actuarial_report"}]


# ---------------------------------------------------------------- 人审
def _factor_card(ctx) -> dict:
    lines = []
    for c in _comps(ctx):
        sel = c.get("selected", [])
        lines.append(f"{c['name']}：{'、'.join(s['name'] for s in c.get('screening', []) if s['key'] in sel)}")
    return {
        "card_type": "result_confirm",
        "title": "确认入模风险因子",
        "reason": "因子筛选完成，请精算岗确认入模因子（可剔除或保留）：" + "；".join(lines),
        "context": {"components": [{"id": c["id"], "name": c["name"], "screening": c.get("screening", []), "selected": c.get("selected", []), "extraction": c.get("extraction")} for c in _comps(ctx)]},
        "options": [{"key": "approve", "label": "确认入模", "primary": True}, {"key": "reject", "label": "退回重新筛选", "requires_comment": True}],
        "assignee_role": "designer",
        "mandatory": True,
        "default_action": "approve",
        "deadline_hours": 48,
    }


def _apply_factors(ctx, res: dict) -> str:
    edits = (res.get("edits") or {}).get("selected") or {}
    changed = 0
    for c in _comps(ctx):
        if c["id"] in edits and isinstance(edits[c["id"]], list) and edits[c["id"]]:
            c["selected"] = [k for k in edits[c["id"]] if k in {s["key"] for s in c.get("screening", [])}] or c["selected"]
            changed += 1
    return f"入模因子已确认（人工调整 {changed} 个组件）"


def _round_card(ctx) -> dict:
    exps = ctx.workspace.get("experiments", [])
    last = [e for e in exps if e["round"] == max(e["round"] for e in exps)] if exps else []
    return {
        "card_type": "decision",
        "title": f"A/B 实验第 {last[0]['round'] if last else 0} 轮结论",
        "reason": "；".join(f"{e['name']}：{e['insight']['conclusion']}" for e in last),
        "context": {"experiments": last},
        "options": [{"key": "approve", "label": "进入下一步", "primary": True}, {"key": "reject", "label": "调整后重做本轮", "requires_comment": True}],
        "assignee_role": "designer",
        "mandatory": False,
        "default_action": "approve",
        "deadline_hours": 24,
    }


def _apply_revision_decision(ctx, res: dict) -> str:
    if not (res.get("context") or {}).get("revision"):
        return "按实验结论进入下一步" if res.get("action") == "approve" else "按人工意见调整"
    state = ctx.workspace.setdefault("revision", {})
    if res.get("action") != "approve":
        state["status"] = "rejected"
        from ...services import pipeline

        pipeline.add_revision(ctx.business_ref.get("project_id"), {"request_no": state.get("request_no"), "status": "rejected", "status_label": "设计人员驳回", "comment": res.get("comment")})
        return "修订请求被驳回，按原条款继续"
    from ...harness.executor import executor
    from ...services import pipeline

    clause_ref = ctx.workspace["clause_ref"]
    run_id = executor.create_run(
        agent_id="AG-CD",
        purpose="revision",
        inputs={"artifact_key": clause_ref["artifact_key"], "base_version": clause_ref["version"], "revision_request": {"request_no": state["request_no"], "changes": state["changes"], "from_run_id": ctx.run_id}},
        business_ref={**ctx.business_ref, "stage": "clause"},
        mode=ctx.mode,
        user_id=res.get("user_id") or ctx.created_by,
        title=f"条款修订：{state['request_no']}",
    )
    state.update({"status": "in_progress", "revision_run_id": run_id})
    pipeline.add_revision(ctx.business_ref.get("project_id"), {"request_no": state["request_no"], "status": "in_progress", "status_label": "条款修订中", "revision_run_id": run_id})
    cur = ctx.todos.current()
    if cur and cur.tool == "coordinate_clause_revision":
        pass
    return f"已发起条款修订运行 {run_id}"


def _confirm_card(ctx) -> dict:
    out = ctx.workspace.get("final_output", {})
    schemes = ctx.workspace.get("document_schemes") or {}
    if schemes:
        reason = "；".join(f"{item.get('method')}建议费率 {float(item.get('suggested_rate') or 0) * 100:.4f}%" for item in schemes.values())
        reason += "。尚未选定毛费率"
    else:
        reason = "；".join(f"{c['name']}毛费率 {c['rate']['gross_rate'] * 100:.3f}%（区间 {c['rate']['band'][0] * 100:.3f}%–{c['rate']['band'][1] * 100:.3f}%）" for c in _comps(ctx) if c.get("rate") and c["rate"].get("gross_rate"))
    return {
        "card_type": "result_confirm",
        "title": "精算岗确认定价",
        "reason": reason,
        "impact": "确认后定价方案冻结，可组装产品包",
        "context": {"artifact_key": out.get("artifact_key"), "version": out.get("version"), "link": f"/design/pricing/{out.get('artifact_key')}"},
        "options": [{"key": "approve", "label": "确认定价", "primary": True}, {"key": "reject", "label": "驳回并说明", "requires_comment": True}],
        "assignee_role": "designer",
        "deadline_hours": 72,
    }


# ---------------------------------------------------------------- 结果
def _artifact_data(ctx) -> dict:
    comps = []
    for c in _comps(ctx):
        cc = {k: v for k, v in c.items() if k not in ("models",)}
        cc["models"] = {m: {k: v for k, v in mv.items() if k != "_glm"} for m, mv in c.get("models", {}).items()}
        comps.append(cc)
    data = {
        "clause_ref": ctx.workspace.get("clause_ref"),
        "report_ref": ctx.workspace.get("report_ref"),
        "enterprise": ctx.workspace.get("enterprise"),
        "targets": ctx.workspace.get("targets"),
        "components": comps,
        "experiments": ctx.workspace.get("experiments", []),
        "revision": ctx.workspace.get("revision"),
        "report": ctx.workspace.get("actuarial_report"),
        "enterprise_factor_values": ctx.workspace.get("enterprise_factor_values"),
        "start_mode": ctx.state.get("start_mode"),
        "degrade_level": ctx.state.get("degrade_level"),
        "_glm": {c["id"]: c["models"]["PM1"]["_glm"] for c in _comps(ctx) if "PM1" in c.get("models", {})},
    }
    clause_ref = ctx.workspace.get("clause_ref") or {}
    if clause_ref.get("artifact_key"):
        from ..clause_design.tools import load_artifact
        from ...productdev.flow import document_pricing_for_clause
        from ...productdev.parameters import ParameterError
        from ...productdev.rates import RateError

        try:
            clause = load_artifact(clause_ref["artifact_key"], clause_ref.get("version"))
            document = document_pricing_for_clause(clause["data"])
            data["schemes"] = document["schemes"]
            data["comparison"] = document["comparison"]
            data["sensitivity"] = document["sensitivity"]
            data["actuarial_report"] = document["actuarial_report"]
            rates = [float(item["suggested_rate"]) for item in document["schemes"].values()]
            band = [min(rates), max(rates)] if rates else None
            for component in data["components"]:
                rate = component.setdefault("rate", {})
                rate["gross_rate"] = None
                rate["premium_at_limit"] = None
                if band:
                    rate["band"] = band
                params = component.setdefault("params", {})
                params["deductible"] = 0
            ctx.workspace["document_schemes"] = document["schemes"]
        except (RateError, ParameterError, KeyError, TypeError, ValueError):
            pass
    return data


def _finalize(ctx) -> dict:
    data = _artifact_data(ctx)
    ent = data.get("enterprise") or {}
    art = create_artifact(kind="pricing_plan", data=data, title=f"{ent.get('name', '')}精算定价方案", run_id=ctx.run_id, project_id=ctx.business_ref.get("project_id"), upstream=[ctx.workspace["clause_ref"]] if ctx.workspace.get("clause_ref") else [], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    ctx.inputs["artifact_key"] = art["artifact_key"]
    summary = "；".join(f"{c['name']}毛费率 {c['rate']['gross_rate'] * 100:.3f}%" for c in _comps(ctx) if c.get("rate"))
    return {"summary": summary, "artifact_key": art["artifact_key"], "version": art["version"]}


def _on_confirm(ctx, res: dict) -> dict:
    out = ctx.workspace["final_output"]
    freeze_artifact(out["artifact_key"], out["version"])
    from ...services import pipeline

    pipeline.on_stage_confirmed(ctx.business_ref.get("project_id"), "pricing", out["artifact_key"], out["version"], ctx.run_id, res.get("user_id"))
    return {"summary": f"精算定价 V{out['version']} 已确认冻结", **out}


def _need_done(ctx, _):
    ready = bool(ctx.workspace.get("pricing_published")) and "weighted" in (ctx.workspace.get("need_schemes") or {}) and "classification" in (ctx.workspace.get("need_schemes") or {})
    return ready, ready, "创新积分制评分和分类费率均已计算" if ready else "两种定价方案不完整"


def _need_finalize(ctx) -> dict:
    from ...productdev.flow import _pricing_payload
    from ..common import create_artifact

    rules = ctx.workspace.get("need_rules") or {}
    schemes = ctx.workspace.get("need_schemes") or {}
    clause = {}
    clause_ref = ctx.workspace.get("clause_ref") or {}
    if clause_ref.get("artifact_key"):
        from ..clause_design.tools import load_artifact

        try:
            clause = load_artifact(clause_ref["artifact_key"], clause_ref.get("version")).get("data") or {}
        except Exception:
            clause = {}
    payload = _pricing_payload(rules, schemes, ctx.workspace.get("need_comparison") or {}, ctx.workspace.get("need_sensitivity") or {}, ctx.workspace.get("need_report") or {}, clause=clause or None)
    project_id = ctx.business_ref.get("project_id")
    title = "精算定价方案"
    if project_id:
        from ...db import session_scope
        from ...models import DesignProject

        with session_scope() as s:
            proj = s.get(DesignProject, project_id)
            if proj is not None:
                title = f"{proj.name}费率"
    art = create_artifact(kind="pricing_plan", data=payload, title=title, run_id=ctx.run_id, project_id=project_id, upstream=[ctx.workspace["clause_ref"]] if ctx.workspace.get("clause_ref") else [], key=ctx.inputs.get("artifact_key"), created_by=ctx.created_by, vault=ctx.vault)
    if project_id:
        from ...services import pipeline

        pipeline.update_node(project_id, "pricing", status="waiting_confirm", artifact_key=art["artifact_key"], version=art["version"], run_id=ctx.run_id, label="精算定价")
    return {"summary": "两种费率已并列算出，尚未选定", "artifact_key": art["artifact_key"], "version": art["version"]}


def _case_summary(ctx) -> dict:
    return {"subject": (ctx.workspace.get("enterprise") or {}).get("name"), "title": "精算定价案例", "text": "；".join(f"{c['name']}：方法 {'+'.join(c['methods'])}，毛费率 {c['rate']['gross_rate'] * 100:.3f}%" for c in _comps(ctx) if c.get("rate")), "level": 1}


profile = register_profile(
    AgentProfile(
        agent_id="AG-AP",
        name="精算定价智能体",
        version="1.1.0",
        core_skill="SK-03",
        shared_skills=["SK-S1", "SK-S2", "SK-S3"],
        description="按创新积分制定价法和分类费率厘定法两种方法并列测算建议费率、调整系数与建议保费，数字由确定性计算工具产生，输出可审定的保险费率精算报告；选定后写入正式毛费率。",
        initialize=_initialize,
        finalize=_finalize,
        start_mode=_start_mode,
        conditions={"is_enterprise": lambda ctx: ctx.inputs.get("scope") == "enterprise"},
        stage_rubrics=STAGE_RUBRICS,
        final_rubric=FINAL_RUBRIC,
        review_task="ap.review",
        review_inputs=lambda ctx: {"sections": (ctx.workspace.get("actuarial_report") or {}).get("sections", []), "_level": 1},
        reflection_rules=REFLECTION_RULES,
        hitl_builders={"factor_confirm": _factor_card, "ab_next": _round_card, "result_confirm": _confirm_card},
        hitl_appliers={"result_confirm": _apply_factors, "decision": _apply_revision_decision},
        final_confirm="result_confirm",
        on_confirm=_on_confirm,
        case_summary=_case_summary,
        priority=3,
        pass_score=75,
    )
)

profile.purposes["need_pricing"] = AgentProfile(
    agent_id="AG-AP",
    name="精算定价智能体",
    version="1.1.0",
    core_skill="SK-03",
    shared_skills=[],
    description="按创新积分制评分和分类费率两种方法，为产品设计项目计算可复核的建议费率，输出保险费率精算报告。",
    initialize=lambda ctx: None,
    finalize=_need_finalize,
    start_mode=lambda _inputs: {"start_mode": "warm", "degrade_level": "D0", "reasons": ["精算定价按技能规程推进，数字由确定性工具计算"]},
    procedure_name="产品定价",
    planner="auto",
    plan_confirm=False,
    final_rubric=[Dimension("need_methods", "两种定价方案", "hard", _need_done, "两种方法都已计算", category="工具失败")],
    extra_tools=["read_need_pricing_rules", "calculate_weighted_score", "calculate_classification_rate", "publish_need_pricing"],
    priority=3,
    pass_score=60,
)

_ = TodoItem
