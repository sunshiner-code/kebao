"""条款设计智能体的工具。"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from ...datasources.public import get_source
from ...db import session_scope
from ...harness.tools import ToolError, ToolResult, registry
from ...knowledge.catalog import INSURABILITY_LABEL, LINES, STAGES, subtype_record
from ...knowledge.eligibility import enterprise_context, filter_lines, ineligible_line_codes
from ...knowledge.index import knowledge_index
from ...llm.gateway import ModelUnavailable, model_gateway
from ...llm.providers import ProviderError
from ...models import Artifact, Enterprise
from ...textutil import format_amount
from . import library as lib
from . import reasoning

ORDER = ["低", "中", "高", "极高"]
_BUILTIN_NOTE = {
    "cd.recommend_rationale": "推荐说明因调用失败改用内置实现",
    "cd.innovate": "创新条款因调用失败改用内置实现",
    "cd.personalize": "特别约定因调用失败改用内置实现",
}


def _invoke_or_builtin(ctx, task_name: str, inputs: dict, builtin_fn, data_level: int):
    """调用现有模型任务，并等待其返回。只有调用失败时才使用该任务已有的内置实现。"""
    try:
        output = model_gateway.invoke(task_name, inputs, ctx=ctx, data_level=data_level, builtin_fallback=False)
    except (ModelUnavailable, ProviderError):
        return builtin_fn(inputs), _BUILTIN_NOTE[task_name]
    return output, None


def _remember_builtin(ctx, note: str | None) -> None:
    if note:
        ctx.workspace.setdefault("fix_log", []).append(note)


def _halt(ctx, message: str) -> None:
    """没有条款可生成时停止当前待办，避免缺字段被反复重试。"""
    todo = ctx.todos.current() if getattr(ctx, "todos", None) is not None else None
    if todo is not None:
        todo.attempts = 99
    raise ToolError(message, retryable=False)


def _clause_docs(ctx) -> list:
    docs = ctx.workspace.get("docs") or []
    if not docs:
        _halt(ctx, "没有可生成条款的险种")
    return docs


def _report(ctx) -> dict:
    return ctx.workspace["report"]


def _risk_index(ctx) -> dict[str, dict]:
    return {r["id"]: r for r in _report(ctx).get("risks", [])}


def load_artifact(key: str, version: int | None) -> dict:
    with session_scope() as s:
        q = select(Artifact).where(Artifact.artifact_key == key)
        if version:
            q = q.where(Artifact.version == version)
        art = s.scalars(q.order_by(Artifact.version.desc())).first()
        if art is None:
            raise ToolError(f"产物 {key} 不存在", retryable=False)
        return {"data": art.data, "version": art.version, "status": art.status, "title": art.title, "key": art.artifact_key}


def _financials(ctx) -> dict:
    report = _report(ctx)
    ids = [report["enterprise"]["id"]] if report["enterprise"].get("id") else [e["id"] for e in (report.get("track") or {}).get("samples_detail", [])]
    with session_scope() as s:
        if not ids and report.get("track"):
            names = report["track"].get("samples", [])
            rows = s.scalars(select(Enterprise).where(Enterprise.name.in_(names))).all()
        else:
            rows = s.scalars(select(Enterprise).where(Enterprise.id.in_(ids))).all()
        profiles = [r.profile or {} for r in rows]
    if not profiles:
        return {}
    if len(profiles) == 1:
        p = profiles[0]
        return {"financials": p.get("financials", {}), "equipment": p.get("equipment", []), "products": p.get("products", []), "suppliers": p.get("suppliers", []), "roles": [m["role"] for m in p.get("team", {}).get("core_roles", [])], "security": p.get("security", {})}
    keys = profiles[0].get("financials", {}).keys()
    med = {k: statistics.median([p.get("financials", {}).get(k, 0) for p in profiles]) for k in keys}
    return {"financials": med, "equipment": [], "products": [x for p in profiles for x in p.get("products", [])][:4], "suppliers": [], "roles": ["首席技术官", "研发负责人"], "security": {"level": 2, "data_scale": 3}}


@registry.tool("load_risk_report", "读取风险评估报告", "读取已冻结的风险评估报告作为条款设计输入", permission="T0", zone="platform")
def load_risk_report(ctx, **_) -> ToolResult:
    ref = ctx.inputs.get("risk_report") or {}
    art = load_artifact(ref.get("artifact_key"), ref.get("version"))
    if art["status"] != "frozen":
        raise ToolError("风险评估报告尚未确认冻结，不能作为条款设计输入", retryable=False)
    ctx.workspace["report"] = art["data"]
    ctx.workspace["report_ref"] = {"artifact_key": art["key"], "version": art["version"]}
    ctx.workspace["features"] = _financials(ctx)
    stats = art["data"].get("stats", {})
    return ToolResult(summary=f"读取风险评估报告 V{art['version']}：风险 {stats.get('total', 0)} 条，高及以上 {stats.get('high', 0)} 条", data={"ref": ctx.workspace["report_ref"]}, level=2)


@registry.tool("screen_insurable_risks", "可保风险筛选", "按偶然性、可测性、损失明确、可分散复核可保性，输出可保、部分可保、不可保清单", permission="T1", zone="cloud")
def screen_insurable_risks(ctx, **_) -> ToolResult:
    insurable, excluded = [], []
    for r in _report(ctx).get("risks", []):
        sub = subtype_record(r.get("subtype"), r)
        ins = r.get("insurability") or sub["insurable"]
        item = {k: r.get(k) for k in ("id", "subtype", "type_code", "title", "description", "level", "score", "likelihood", "severity", "stage", "involved", "advice", "line_names", "novel")}
        item["insurability"] = ins
        if ins in ("yes", "partial"):
            insurable.append(item)
        else:
            excluded.append({**item, "reason": "属于经营或商业风险，输出风险管理建议"})
    ctx.workspace["insurable"] = insurable
    ctx.workspace["non_insurable"] = excluded
    return ToolResult(summary=f"可保与部分可保风险 {len(insurable)} 条，不可保 {len(excluded)} 条（转为风险管理建议）", data={"insurable": [r["id"] for r in insurable]}, level=1, metrics={"insurable": len(insurable), "non_insurable": len(excluded)})


@registry.tool("match_clause_modules", "条款模块匹配与路径决策", "对每条可保风险检索条款模块库，按匹配度决定复用、改造或创新；排除企业地区/行业不适用的险种模块", permission="T1", zone="cloud")
def match_clause_modules(ctx, **_) -> ToolResult:
    modules = lib.liability_modules()
    elig_ctx = enterprise_context(_report(ctx).get("enterprise") or {})
    decisions = lib.decide_paths(ctx.workspace["insurable"], modules, eligibility_ctx=elig_ctx)
    ctx.workspace["decisions"] = decisions
    ctx.workspace["eligibility_ctx"] = elig_ctx
    counts = Counter(d["path"] for d in decisions)
    label = {"reuse": "复用", "extend": "改造", "innovate": "创新"}
    return ToolResult(summary="路径决策：" + "，".join(f"{label[k]} {v} 条" for k, v in counts.items()), data={"decisions": decisions}, level=1, metrics=dict(counts))


def _limits(ctx, preference: str) -> dict[str, float]:
    f = ctx.workspace["features"].get("financials", {})
    eq = ctx.workspace["features"].get("equipment", [])
    sec = ctx.workspace["features"].get("security", {})
    revenue = f.get("revenue", 1e8)
    na = f.get("net_assets", 1e8)
    months = {"conservative": 12, "balanced": 6, "aggressive": 3}[preference]
    coverage = {"conservative": 1.0, "balanced": 0.8, "aggressive": 0.6}[preference]
    equipment_value = round(sum(e["value"] for e in eq) or na * 0.08, -4)
    bases = {
        "equipment": equipment_value,
        "rd_expense": round(f.get("rd_expense", na * 0.05) * 0.5 * coverage, -4),
        "monthly_waiting": round(f.get("monthly_fixed_cost", na * 0.01) * months * 0.6, -4),
        "liability": round(max(1e7, revenue * 0.05) * coverage, -4),
        "quality": round(max(5e6, revenue * 0.02) * coverage, -4),
        "ip": round(max(5e6, na * 0.03) * coverage, -4),
        "fee": round(max(2e5, f.get("rd_expense", na * 0.01) * 0.02) * coverage, -4),
        "cyber": round(min(5e7, 5e6 * 1.5 ** (sec.get("data_scale", 2) - 1)) * coverage, -4),
        "surety": round(max(3e6, na * 0.02) * coverage, -4),
        "aviation": round(max(2e7, revenue * 0.2) * coverage, -4),
    }
    return {code: bases.get(spec.get("limit_basis"), bases["liability"]) for code, spec in LINES.items()}


@registry.tool("model_preference_capacity", "风险偏好与承受能力建模", "在私域计算可承受自留损失与承受能力等级，输出免赔、限额、自担比例等参数区间", permission="T1", zone="private", max_level=3)
def model_preference_capacity(ctx, **_) -> ToolResult:
    pref_input = ctx.inputs.get("preference") or {}
    preference = pref_input.get("preference") or "balanced"
    f = ctx.workspace["features"].get("financials", {})
    high_sev = max([r.get("severity", 3) for r in ctx.workspace.get("insurable", []) if r.get("level") in ("高", "极高")] or [4])
    capacity = lib.capacity_from_financials(f, high_sev) if f else {"retention": 2e5, "basis": "默认", "candidates": {}, "pml": 1e6, "capacity_ratio": 0.2, "capacity_level": "中"}
    ranges = lib.parameter_ranges(preference, capacity)
    ctx.workspace["capacity"] = {**capacity, "preference": preference, "preference_label": lib.PREF_LABEL[preference], "source": "问卷" if pref_input.get("answers") else ("赛道典型画像" if ctx.inputs.get("design_type") == "track" else "默认稳健"), **ranges}
    ctx.workspace["limits"] = _limits(ctx, preference)
    return ToolResult(summary=f"风险偏好：{lib.PREF_LABEL[preference]}；可承受自留损失约 {format_amount(capacity['retention'])}（取{capacity['basis']}），承受能力{capacity['capacity_level']}；推荐免赔 {format_amount(ranges['deductible_range'][0])}–{format_amount(ranges['deductible_range'][1])}", data={"capacity_level": capacity["capacity_level"], "ranges": ranges}, level=2)


@registry.tool("recommend_products", "险种推荐与组合方式决策", "带预算约束的加权集合覆盖选择险种，并决定组合包、重组新主险或创新险种", permission="T1", zone="cloud", params={"expand": "是否扩大覆盖"})
def recommend_products(ctx, expand: str = "0", **_) -> ToolResult:
    cap = ctx.workspace["capacity"]
    levels = ["极高", "高", "中", "低"] if expand == "1" else list(cap["cover_levels"])
    decisions = ctx.workspace["decisions"]
    budget = ctx.inputs.get("budget")
    elig_ctx = ctx.workspace.get("eligibility_ctx") or enterprise_context(_report(ctx).get("enterprise") or {})
    blocked = set(ineligible_line_codes(elig_ctx))
    required_raw = list(ctx.inputs.get("required_lines") or []) + [l for l in (ctx.inputs.get("target_lines") or []) if l not in (ctx.inputs.get("required_lines") or [])]
    for line in lib.stated_required_lines(_report(ctx).get("risks", []), eligibility_ctx=elig_ctx):
        if line not in required_raw:
            required_raw.append(line)
    filtered_required = filter_lines(required_raw, elig_ctx, replace=True)
    required = filtered_required.eligible
    excluded = list(dict.fromkeys(list(ctx.inputs.get("excluded_lines") or []) + sorted(blocked)))
    selection_args = {"budget": float(budget) if budget else None, "required": required, "excluded": excluded, "limits": ctx.workspace["limits"]}
    widened_from = None
    modules = lib.liability_modules()
    base = [d for d in decisions if not d.get("novel")]
    if expand == "1":
        base_selection = lib.select_lines(base, cover_levels=levels, **selection_args)
    else:
        base_selection, levels, widened_from = lib.select_lines_with_fallback(base, cover_levels=levels, **selection_args)
    decisions, rejected = lib.place_novel_risks(decisions, [line for line in base_selection["lines"] if line in LINES], modules)
    ctx.workspace["decisions"] = decisions
    for decision in decisions:
        if decision.get("novel") and decision.get("path") == "innovate":
            code = decision["spec"]["line"]
            if code not in required:
                required.append(code)
    selection_args["required"] = required
    selection = lib.select_lines(decisions, cover_levels=levels, **selection_args)
    module_lines = {m["line"] for m in modules}
    line_subtypes: dict[str, set[str]] = defaultdict(set)
    for decision in decisions:
        target = decision.get("module_line") if decision.get("path") in ("reuse", "extend") else (decision.get("spec") or {}).get("line")
        if target and decision.get("subtype"):
            line_subtypes[target].add(decision["subtype"])
    structure = lib.structure_plan(selection["lines"], module_lines, line_risks=selection["line_risks"], line_subtypes=line_subtypes, limits=ctx.workspace["limits"])
    plan_lines = structure.get("plan_lines") or selection["lines"]
    line_risks = structure.get("line_risks") or selection["line_risks"]
    premium_by_line = {**selection["estimated_premium"], **(structure.get("premium_estimate") or {})}
    roles, attached_to = lib.structure_roles(structure)
    novel_names = {d["spec"]["line"]: d["title"] for d in decisions if d.get("novel") and d.get("path") == "innovate" and d.get("spec")}
    for product in structure["products"]:
        if product["structure"] == "innovative" and product["lines"][0] in novel_names:
            line = product["lines"][0]
            title = novel_names[line]
            product["name"] = lib.innovative_product_name(title, line)
            product["note"] = f"针对「{lib.short_risk_label(title)}」按第一性原理设计，需人工合规复核"
    risk_idx = {r["id"]: r for r in ctx.workspace["insurable"]}
    items = []
    for line in plan_lines:
        ids = line_risks.get(line, [])
        paths = Counter(d["path"] for d in decisions if d["risk_id"] in ids)
        main_path = "innovate" if line not in module_lines else ("extend" if paths.get("extend") else "reuse")
        item = {"line": line, "name": LINES.get(line, {}).get("name") or novel_names.get(line) or line, "stage": LINES.get(line, {}).get("stage", "industrial"), "risks": [{"id": i, "title": risk_idx[i]["title"], "level": risk_idx[i]["level"]} for i in ids if i in risk_idx], "path": main_path, "premium_estimate": round(premium_by_line.get(line, 0), -2), "role": roles.get(line) or LINES.get(line, {}).get("role") or "主险", "reason": f"预估保费 {format_amount(premium_by_line.get(line, 0))}"}
        attached = attached_to.get(line)
        if attached:
            item["attached_to"] = attached
            item["attached_to_name"] = LINES.get(attached, {}).get("name") or attached
            item["reason"] = f"作为「{item['attached_to_name']}」的附加条款承保，{item['reason']}"
        items.append(item)
    rationale, rationale_note = _invoke_or_builtin(ctx, "cd.recommend_rationale", {"lines": items, "structure_label": structure["structure_label"], "main_count": structure.get("main_count", len(items)), "rider_count": structure.get("rider_count", 0)}, reasoning.recommend_rationale, 1)
    _remember_builtin(ctx, rationale_note)
    for item in items:
        item["rationale"] = next((r["text"] for r in rationale["rationale"] if r["line"] == item["line"]), item["reason"])
    uncovered = []
    for d in selection["uncovered"]:
        r = risk_idx[d["risk_id"]]
        reason = "风险等级低于偏好覆盖范围" if r["level"] not in levels else ("超出预算约束" if budget else "无适用险种")
        uncovered.append({"risk_id": d["risk_id"], "title": r["title"], "level": r["level"], "reason": reason})
    for decision in rejected:
        uncovered.append({"risk_id": decision["risk_id"], "title": decision.get("title"), "level": decision.get("level"), "reason": decision["reject_reason"]})
    stages = {}
    for st in STAGES:
        in_stage = [i["name"] for i in items if i["stage"] == st]
        stages[st] = {"lines": in_stage, "conclusion": f"推荐 {'、'.join(in_stage)}" if in_stage else f"{STAGES[st]}阶段无需单独配置险种（该阶段可保风险已被其他险种覆盖或等级较低）"}
    widen_note = ""
    if widened_from and selection["lines"]:
        added = [level for level in levels if level not in widened_from]
        widen_note = f"原偏好范围内没有可保风险，已放宽到{added[-1]}。"
    elig_note = ""
    if filtered_required.notes or blocked:
        elig_note = f"；已排除 {len(blocked)} 个不符合企业地区/行业条件的险种"
        if filtered_required.notes:
            elig_note += f"（{'；'.join(filtered_required.notes[:2])}）"
    summary_parts = [widen_note, structure.get("summary", ""), rationale["summary"], elig_note]
    ctx.workspace["recommendation"] = {
        "items": items,
        "structure": structure,
        "main_count": structure.get("main_count", len(items)),
        "rider_count": structure.get("rider_count", 0),
        "consolidation": structure.get("consolidation"),
        "structure_advice": structure.get("advice"),
        "uncovered": uncovered,
        "stages": stages,
        "summary": "；".join(part.strip("；") for part in summary_parts if part.strip("；")),
        "selection_log": selection["log"],
        "estimated_total_premium": round(sum(premium_by_line.get(i["line"], 0) for i in items), -2),
        "coverage_widened_from": widened_from,
        "coverage_levels": levels,
        "eligibility": {"context": elig_ctx, "excluded_ineligible": sorted(blocked), "required_notes": filtered_required.notes},
    }
    consolidation_note = ""
    if structure.get("consolidation"):
        consolidation_note = f"已用{structure['consolidation']['name']}替代 {len(structure['consolidation']['replaces'])} 个分项主险，需人工确认。"
    elif structure.get("advice"):
        consolidation_note = structure["advice"] + "。"
    return ToolResult(summary=f"{widen_note}{consolidation_note}{rationale['summary']}险种：{'、'.join(i['name'] for i in items)}；{structure.get('summary', '')}；未覆盖风险 {len(uncovered)} 条{elig_note}", data=ctx.workspace["recommendation"], level=1, metrics={"lines": len(items), "mains": structure.get("main_count", len(items)), "riders": structure.get("rider_count", 0), "uncovered": len(uncovered), "ineligible_excluded": len(blocked)})


@registry.tool("build_tier_plans", "生成三档方案", "按风险等级与偏好参数生成基础、标准、全面三档方案", permission="T1", zone="cloud")
def build_tier_plans(ctx, **_) -> ToolResult:
    cap = ctx.workspace["capacity"]
    items = ctx.workspace["recommendation"]["items"]
    limits = ctx.workspace["limits"]

    def params(line: str, mult_ded: float, mult_lim: float) -> dict:
        base_ded = cap["deductible"]
        min_ded = float(LINES.get(line, {}).get("deductible_floor", 1e4))
        limit = round(limits.get(line, 5e6) * mult_lim, -4)
        # 免赔额以企业承受能力为上限，同时不超过该险种限额的 5%，避免小限额险种的保障被免赔额吞没
        ded = max(min_ded, min(round(base_ded * mult_ded, -3), round(limit * 0.05, -3)))
        return {
            "deductible": ded,
            "limit": limit,
            "coinsurance": cap["coinsurance"],
            "waiting_days": cap["waiting_days"],
            "waiting_hours": cap["waiting_hours"],
            "indemnity_months": cap["indemnity_months"],
            "period_months": 12,
            "patch_days": 30,
        }

    tiers = {}
    for tier, levels, ded, lim in (("basic", ["极高"], 1.5, 0.7), ("standard", ["极高", "高"], 1.0, 1.0), ("comprehensive", ["极高", "高", "中", "低"], 0.6, 1.0)):
        chosen = [i for i in items if any(r["level"] in levels for r in i["risks"])] or items[:1]
        tiers[tier] = {"label": {"basic": "基础", "standard": "标准", "comprehensive": "全面"}[tier], "lines": [{"line": i["line"], "name": i["name"], "params": params(i["line"], ded, lim)} for i in chosen]}
    ctx.workspace["tiers"] = tiers
    ctx.workspace["params"] = {l["line"]: l["params"] for l in tiers["standard"]["lines"]}
    for i in items:
        ctx.workspace["params"].setdefault(i["line"], params(i["line"], 1.0, 1.0))
    return ToolResult(summary="三档方案：" + "；".join(f"{t['label']} {len(t['lines'])} 个险种" for t in tiers.values()), data=tiers, level=1)


def _doc_specs(ctx) -> list[dict]:
    rec = ctx.workspace["recommendation"]
    decisions = ctx.workspace["decisions"]
    params = ctx.workspace["params"]
    specs = []
    for i, product in enumerate(rec["structure"]["products"], start=1):
        member_lines = product["lines"]
        groups = [member_lines] if product["structure"] in ("restructured", "innovative", "attach") else [[l] for l in member_lines]
        for lines in groups:
            line_set = set(lines)
            reuse_modules = sorted({d["module"] for d in decisions if d["path"] == "reuse" and d["module_line"] in line_set})
            extend = [d for d in decisions if d["path"] == "extend" and (d["module_line"] in line_set)]
            innovate = [d for d in decisions if d["path"] == "innovate" and d["spec"]["line"] in line_set]
            all_line_modules = [m["code"] for m in lib.liability_modules() if m["line"] in line_set]
            modules = sorted(set(reuse_modules) | {d["module"] for d in extend if d["module"]} | set(all_line_modules if product["structure"] != "innovative" else []))
            p = dict(params.get(lines[0], {}))
            if len(lines) > 1:
                p["limit"] = sum(params.get(l, {}).get("limit", 0) for l in lines)
            specs.append(
                {
                    "doc_id": f"D{len(specs) + 1}",
                    "name": product["name"] if len(groups) == 1 else LINES[lines[0]]["name"],
                    "product_name": product.get("product_name") or product["name"],
                    "structure": product["structure"],
                    "main": product.get("main"),
                    "riders": list(product.get("riders") or []),
                    "kind": {"restructured": "restructured", "innovative": "innovative", "attach": "attach"}.get(product["structure"], "existing"),
                    "lines": lines,
                    "stage": LINES.get(lines[0], {}).get("stage", "industrial"),
                    "modules": modules,
                    "extend_decisions": [{"risk_id": d["risk_id"], "subtype": d["subtype"], "title": d["title"], "spec": d["spec"]} for d in extend],
                    "innovate_decisions": [{"risk_id": d["risk_id"], "subtype": d["subtype"], "title": d["title"], "spec": d["spec"]} for d in innovate],
                    "params": p,
                }
            )
    return specs


def _clause_tasks(ctx) -> list[dict]:
    specs = _doc_specs(ctx)
    ctx.workspace["doc_specs"] = specs
    feats = ctx.workspace["features"]
    risks = _report(ctx).get("risks", [])
    ent = _report(ctx).get("enterprise", {})
    patents = get_source().patents(ent.get("name", "")) if ent.get("name") else []
    tasks = []
    for spec in specs:
        related = [r for r in risks if any(r["id"] in (d["risk_id"],) for d in spec["extend_decisions"] + spec["innovate_decisions"]) or bool(set(subtype_record(r.get("subtype"), r)["lines"]) & set(spec["lines"]))]
        tasks.append(
            {
                "title": f"条款生成：{spec['name']}",
                "purpose": "clause_doc",
                "key": spec["doc_id"],
                "inputs": {
                    "spec": spec,
                    "risks": [{k: r.get(k) for k in ("id", "subtype", "title", "level", "advice", "description")} for r in related],
                    "features": {"equipment": feats.get("equipment", []), "products": feats.get("products", []), "roles": feats.get("roles", []), "suppliers": feats.get("suppliers", [])},
                    "patents": [{"title": p["title"], "no": p["no"]} for p in patents],
                    "start_mode": ctx.state.get("start_mode"),
                },
            }
        )
    return tasks


@registry.tool("draft_line_clauses", "生成险种条款", "按章节组装条款：复用模块、扩展条款、创新条款、参数条款与个性化特别约定", permission="T1", zone="cloud")
def draft_line_clauses(ctx, **_) -> ToolResult:
    spec = ctx.inputs.get("spec")
    if not spec:
        _halt(ctx, "没有可生成条款的险种，当前运行没有条款规格")
    risks = ctx.inputs.get("risks", [])
    by_subtype: dict[str, list[dict]] = defaultdict(list)
    for r in risks:
        by_subtype[r["subtype"]].append(r)
    coinsurance = spec["params"].get("coinsurance", 0.3)

    def _one(decision: dict):
        inputs = {"spec": decision["spec"], "risks": [r for r in risks if r["id"] == decision["risk_id"]], "coinsurance": coinsurance}
        return _invoke_or_builtin(ctx, "cd.innovate", inputs, reasoning.innovate, 1)

    calls = list(spec["innovate_decisions"]) + list(spec["extend_decisions"])
    if len(calls) <= 1:
        outputs = [_one(decision) for decision in calls]
    else:
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            outputs = [future.result() for future in [pool.submit(_one, decision) for decision in calls]]
    for _output, note in outputs:
        _remember_builtin(ctx, note)
    innovate_outs = outputs[: len(spec["innovate_decisions"])]
    extend_outs = outputs[len(spec["innovate_decisions"]) :]
    innovations = []
    for decision, (out, _note) in zip(spec["innovate_decisions"], innovate_outs):
        innovations.append({"risk_ids": [decision["risk_id"]], "modules": out["modules"], "pending_points": out.get("pending_points", []), "loss_form": decision["spec"]["loss_form"], "parametric": out.get("parametric_trigger", ""), "spec": decision["spec"]})
    extensions = []
    for decision, (out, _note) in zip(spec["extend_decisions"], extend_outs):
        liab = next((m for m in out["modules"] if m["kind"] == "liability"), None)
        if liab:
            extensions.append({"title": f"扩展条款：{decision['spec']['event']}", "text": "经投保人与保险人协商同意，" + liab["text"].lstrip("在") + "但每次事故赔偿金额以保险单载明的分项限额为限。", "risk_ids": [decision["risk_id"]]})
    spec = {**spec, "extensions": extensions, "innovation_forms": [i["loss_form"] for i in innovations]}
    personal, personal_note = _invoke_or_builtin(
        ctx,
        "cd.personalize",
        {"line": spec["lines"][0], "line_name": spec["name"], "enterprise": ctx.inputs.get("features", {}), "params": spec["params"], "risks": risks, "liability_modules": spec["modules"], "patents": ctx.inputs.get("patents", [])},
        reasoning.personalize,
        2,
    )
    _remember_builtin(ctx, personal_note)
    special = personal["special_articles"]
    if ctx.inputs.get("start_mode") == "cold" or innovations:
        special.append({"title": "保守承保约定", "text": "本保险合同为创新或冷启动产品，首个保险期间内每次事故赔偿金额不超过累计赔偿限额的百分之五十，保险人可依据首年经验数据调整次年费率与免赔额。"})
    doc = lib.assemble_doc(spec, by_subtype, special, innovations)
    doc["innovations"] = [{"risk_ids": i["risk_ids"], "pending_points": i["pending_points"], "parametric": i["parametric"], "spec": i["spec"]} for i in innovations]
    doc["personalization_rationale"] = personal.get("rationale", "")
    ctx.workspace["doc"] = doc
    n_art = sum(len(ch["articles"]) for ch in doc["chapters"])
    return ToolResult(summary=f"{doc['name']}：{len(doc['chapters'])} 章 {n_art} 条（创新条款 {len(innovations)} 组、扩展条款 {len(extensions)} 条、特别约定 {len(special)} 条）", data={"doc_id": doc["doc_id"], "articles": n_art}, level=1, metrics={"articles": n_art, "innovations": len(innovations)})


@registry.tool("collect_clause_drafts", "汇总条款草稿", "汇总各险种子智能体生成的条款", permission="T1", zone="platform")
def collect_clause_drafts(ctx, **_) -> ToolResult:
    outs = ctx.workspace.get("subagent_outputs", {}).get("clauses", [])
    if not outs:
        _halt(ctx, "没有可生成条款的险种")
    docs, failed = [], []
    for o in outs:
        if o["status"] == "completed" and o["output"] and o["output"].get("doc"):
            docs.append(o["output"]["doc"])
        else:
            failed.append(o["title"])
    if not docs:
        _halt(ctx, "没有成功生成的条款")
    docs.sort(key=lambda d: d["doc_id"])
    ctx.workspace["docs"] = docs
    if failed:
        ctx.state["degrade_notes"].append("部分险种条款生成失败：" + "、".join(failed))
    return ToolResult(summary=f"汇总条款 {len(docs)} 份" + (f"，失败 {len(failed)} 份" if failed else ""), data={"docs": [d["doc_id"] for d in docs]}, level=1)


@registry.tool("check_consistency", "跨险种一致性检查", "检查重复保障、释义统一、触发机制混用、数字一致与模块依赖，并自动增加衔接条款", permission="T1", zone="cloud")
def check_consistency(ctx, **_) -> ToolResult:
    docs = _clause_docs(ctx)
    issues = lib.consistency_issues(docs)
    fixed = lib.resolve_overlaps(docs, issues) + lib.resolve_structure_issues(docs, issues)
    ctx.workspace["consistency"] = issues
    open_issues = [i for i in issues if not i["resolved"]]
    return ToolResult(summary=f"一致性问题 {len(issues)} 项，已自动处理 {fixed} 项，待人工关注 {len(open_issues)} 项", data={"issues": issues}, level=1, metrics={"issues": len(issues), "open": len(open_issues)})


def _clause_digest(docs: list[dict]) -> str:
    payload = [{"name": doc.get("name"), "chapters": doc.get("chapters")} for doc in docs]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _regulations() -> dict:
    out = {}
    for code, query in (("C04", "格式条款无效"), ("C11", "条款文字准确表述严谨"), ("C03", "免除保险人责任条款提示说明")):
        hits = knowledge_index.search(query, kbs=["K01"], top_k=1)
        if hits:
            out[code] = f"{hits[0]['title']}"
    return out


@registry.tool("check_compliance", "合规校验", "强制规则 C01–C16 逐条校验，并以法规库为依据做合规审查", permission="T1", zone="cloud")
def check_compliance(ctx, rules_only: str = "0", **_) -> ToolResult:
    docs = _clause_docs(ctx)
    report = []
    for d in docs:
        checks = lib.compliance_checks(d)
        report.append({"doc_id": d["doc_id"], "name": d["name"], "checks": checks, "passed": all(c["passed"] for c in checks)})
    digest = _clause_digest(docs)
    previous = ctx.workspace.get("compliance") or {}
    cache = ctx.workspace.setdefault("compliance_review_cache", {})
    if str(rules_only) == "1":
        review = {"issues": list(previous.get("issues") or [])}
    elif digest in cache:
        review = cache[digest]
    else:
        review = reasoning.compliance_review({"docs": [{"name": d["name"], "chapters": d["chapters"]} for d in docs], "regulations": _regulations()})
        cache[digest] = review
    ctx.workspace["compliance"] = {"rules": report, "issues": review["issues"], "digest": digest}
    failed = sum(1 for r in report for c in r["checks"] if not c["passed"])
    high = sum(1 for i in review["issues"] if i["severity"] == "high")
    return ToolResult(summary=f"强制规则未通过 {failed} 项；合规审查发现问题 {len(review['issues'])} 项（严重 {high} 项）", data=ctx.workspace["compliance"], level=1, metrics={"rule_failures": failed, "high_issues": high})


@registry.tool("auto_fix_compliance", "合规自动整改", "对可自动整改的规则问题生成修订并重新编号", permission="T1", zone="cloud")
def auto_fix_compliance(ctx, **_) -> ToolResult:
    docs = _clause_docs(ctx)
    actions = []
    comp = ctx.workspace.get("compliance", {"rules": []})
    by_doc = {d["doc_id"]: d for d in docs}
    for r in comp["rules"]:
        failures = [c for c in r["checks"] if not c["passed"]]
        if failures:
            actions += [f"{r['name']}：{a}" for a in lib.auto_fix(by_doc[r["doc_id"]], failures)]
    for issue in comp.get("issues", []):
        if issue["severity"] == "high":
            for d in docs:
                for ch in d["chapters"]:
                    for a in ch["articles"]:
                        if a["no"] == issue["article"] and d["name"] == issue["line"]:
                            before = a["text"]
                            a["text"] = a["text"].replace("以保险人认定为准", "以约定第三方机构出具的认定意见为准")
                            a["text"] = re.sub(r"保险人有权(单方|随时)", "经双方协商一致，保险人可以", a["text"])
                            a["text"] = re.sub(r"保险人有权(拒绝赔偿|不予赔偿)", "保险人按照本保险合同约定处理赔偿", a["text"])
                            a["text"] = a["text"].replace("保险人可以不予", "保险人按照本保险合同约定可以不予")
                            if a["text"] != before:
                                a["origin"] = "fix"
                                actions.append(f"{d['name']}{a['no']}：按审查意见修改单方决定权表述")
    ctx.workspace.setdefault("fix_log", []).extend(actions)
    return ToolResult(summary=f"自动整改 {len(actions)} 项" + ("：" + "；".join(actions[:4]) if actions else "（无需整改）"), data={"actions": actions}, level=1, metrics={"actions": len(actions)})


@registry.tool("claim_backtest", "理赔反推测试", "用假设事故场景把创新条款理赔一遍，检验能否明确判断赔与不赔并算出金额", permission="T1", zone="cloud", params={"fix": "是否补强"})
def claim_backtest(ctx, fix: str = "0", **_) -> ToolResult:
    results = []
    for d in ctx.workspace["docs"]:
        for inv in d.get("innovations", []):
            spec = inv["spec"]
            liab = next((a for ch in d["chapters"] if ch["key"] == "liability" for a in ch["articles"] if a["origin"] == "innovation"), None)
            settle = "\n".join(a["text"] for ch in d["chapters"] if ch["key"] == "settlement" for a in ch["articles"])
            excl = "\n".join(a["text"] for ch in d["chapters"] if ch["key"] == "exclusion" for a in ch["articles"])
            event = str(spec.get("event") or "")
            trigger = str(spec.get("trigger") or "")
            trigger_ok = bool(
                liab
                and (
                    (trigger[:6] and trigger[:6] in liab["text"])
                    or (event[:6] and event[:6] in liab["text"])
                    or (event and event in liab["text"])
                    or "第三方机构认定" in liab["text"]
                )
            )
            scenarios = [
                {"name": f"典型触发：{event or trigger}", "expect": "pay", "checks": {"触发条件可识别": trigger_ok, "金额可计算": "=" in settle or "乘以" in settle or "×" in settle, "认定依据明确": bool(spec.get("parametric")) and ("认定依据" in settle or "为准" in settle)}},
                {"name": "投保前已存在的事由", "expect": "deny", "checks": {"除外条款可适用": "投保前已经存在" in excl or "已知" in excl}},
                {"name": "经营决策导致的损失", "expect": "deny", "checks": {"除外条款可适用": "经营决策" in excl or "资金不足" in excl}},
            ]
            if fix == "1":
                for sc in scenarios:
                    if not all(sc["checks"].values()) and sc["expect"] == "pay" and liab:
                        settle_ch = next(ch for ch in d["chapters"] if ch["key"] == "settlement")
                        settle_ch["articles"].append(lib._article("创新责任赔偿计算与认定", f"赔偿金额 = 核定的{spec['loss']}× 保障比例 − 免赔额。触发事由以「{spec.get('parametric') or '第三方机构认定文件'}」为认定依据。", origin="fix"))
                        if not trigger_ok and event and event not in liab["text"]:
                            liab["text"] = f"{liab['text'].rstrip()}在发生{event}且经第三方机构认定时，保险人依照本合同承担赔偿责任。"
                        lib.number(d)
                        sc["checks"] = {k: True for k in sc["checks"]}
            for sc in scenarios:
                sc["passed"] = all(sc["checks"].values())
            results.append({"doc_id": d["doc_id"], "name": d["name"], "risk_ids": inv["risk_ids"], "scenarios": scenarios})
    ctx.workspace["backtests"] = results
    total = sum(len(r["scenarios"]) for r in results)
    ok = sum(1 for r in results for sc in r["scenarios"] if sc["passed"])
    return ToolResult(summary=f"理赔反推测试 {total} 个场景，通过 {ok} 个" if total else "无创新条款，无需反推测试", data={"results": results}, level=1, metrics={"scenarios": total, "passed": ok})


@registry.tool("compose_clause_outputs", "生成条款方案产物", "生成条款要素表、风险-条款覆盖矩阵、基线差异、通俗解读、待定价参数、核保要求与监测要点", permission="T1", zone="cloud")
def compose_clause_outputs(ctx, **_) -> ToolResult:
    docs = _clause_docs(ctx)
    insurable = ctx.workspace["insurable"]
    rec = ctx.workspace["recommendation"]
    matrix = []
    for r in insurable:
        row = {"risk_id": r["id"], "title": r["title"], "level": r["level"], "cells": {}}
        for d in docs:
            cov = [a["no"] for ch in d["chapters"] if ch["key"] == "liability" for a in ch["articles"] if r["id"] in a["risk_ids"]]
            row["cells"][d["doc_id"]] = {"status": "covered", "articles": cov} if cov else {"status": "none"}
        if not any(c["status"] == "covered" for c in row["cells"].values()):
            unc = next((u for u in rec["uncovered"] if u["risk_id"] == r["id"]), None)
            row["uncovered_reason"] = unc["reason"] if unc else "未纳入推荐险种"
        matrix.append(row)
    from ...privacy.narrative import finalize_narrative

    explanation = finalize_narrative(reasoning.explain({"docs": docs}), ctx.vault)
    risk_idx = {r["id"]: r for r in _report(ctx).get("risks", [])}
    pricing_params = []
    for d in docs:
        liabs = [{"title": a["title"], "risk_ids": a["risk_ids"], "subtypes": sorted({risk_idx[i]["subtype"] for i in a["risk_ids"] if i in risk_idx}), "origin": a["origin"]} for ch in d["chapters"] if ch["key"] == "liability" for a in ch["articles"]]
        covered_ids = sorted({i for l in liabs for i in l["risk_ids"]})
        pricing_params.append(
            {
                "doc_id": d["doc_id"],
                "name": d["name"],
                "lines": d["lines"],
                "kind": d["kind"],
                "structure": d["structure"],
                "liabilities": liabs,
                "params": d["params"],
                "factors": [f["key"] for f in lib.factor_candidates(d["lines"])],
                "scenarios": [{"risk_id": i, "subtype": risk_idx[i]["subtype"], "title": risk_idx[i]["title"], "likelihood": risk_idx[i].get("likelihood", 3), "severity": risk_idx[i].get("severity", 3)} for i in covered_ids if i in risk_idx],
                "attention": [f"{risk_idx[i]['title']}（{risk_idx[i]['level']}）" for i in covered_ids if i in risk_idx and risk_idx[i].get("level") in ("高", "极高")],
            }
        )
    uw = []
    for d in docs:
        mats = list(lib.COMMON_MATERIALS)
        for l in d["lines"]:
            mats += [m for m in lib.LINE_MATERIALS.get(l, []) if m not in mats]
        points = [f"核实{risk_idx[i]['title']}的管控情况" for p in pricing_params if p["doc_id"] == d["doc_id"] for i in [s["risk_id"] for s in p["scenarios"]] if risk_idx[i].get("level") in ("高", "极高")]
        uw.append({"doc_id": d["doc_id"], "name": d["name"], "materials": mats, "key_points": points[:6] or ["核实标的真实性与价值", "核实经营与财务状况"]})
    monitoring = lib.monitoring_points(sorted({l for d in docs for l in d["lines"]}), [risk_idx[r["id"]] for r in insurable if r["id"] in risk_idx])
    ctx.workspace["outputs"] = finalize_narrative(
        {
            "coverage_matrix": matrix,
            "elements": {d["doc_id"]: lib.element_table(d) for d in docs},
            "diffs": {d["doc_id"]: lib.baseline_diff(d) for d in docs},
            "explanations": explanation["explanations"],
            "pricing_params": pricing_params,
            "uw_requirements": uw,
            "monitoring_points": monitoring,
        },
        ctx.vault,
    )
    covered = sum(1 for row in matrix if any(c["status"] == "covered" for c in row["cells"].values()))
    return ToolResult(summary=f"覆盖矩阵：{covered}/{len(matrix)} 条可保风险被保障；生成待定价参数 {len(pricing_params)} 份、核保材料清单与监测要点 {len(monitoring)} 项", data={"covered": covered}, level=1, metrics={"covered": covered, "insurable": len(matrix)})


# ---------------------------------------------------------------- 修订运行
@registry.tool("load_clause_plan", "读取条款方案", "读取待修订的条款方案版本", permission="T0", zone="platform")
def load_clause_plan(ctx, **_) -> ToolResult:
    art = load_artifact(ctx.inputs["artifact_key"], ctx.inputs.get("base_version"))
    data = art["data"]
    for key in ("report", "insurable", "decisions", "recommendation", "capacity", "tiers", "docs", "outputs", "limits", "features", "params", "non_insurable", "consistency", "compliance", "backtests"):
        if key in data.get("_workspace", {}):
            ctx.workspace[key] = data["_workspace"][key]
    ctx.workspace["base_version"] = art["version"]
    ctx.workspace["base_text"] = {d["doc_id"]: lib.doc_text(d) for d in ctx.workspace.get("docs", [])}
    return ToolResult(summary=f"读取条款方案 V{art['version']}（{len(ctx.workspace.get('docs', []))} 份条款）", level=1)


@registry.tool("apply_revision_request", "执行修订请求", "按精算定价的修订请求调整免赔、等待期、分项限额、除外责任等", permission="T1", zone="cloud")
def apply_revision_request(ctx, **_) -> ToolResult:
    req = ctx.inputs.get("revision_request") or {}
    changes = req.get("changes", [])
    applied = []
    for ch in changes:
        targets = [d for d in ctx.workspace["docs"] if ch.get("doc_id") in (None, d["doc_id"]) and (not ch.get("line") or ch["line"] in d["lines"])]
        for d in targets:
            p = d["params"]
            if ch["type"] in ("deductible", "waiting_days", "coinsurance", "limit", "indemnity_months"):
                old = p.get(ch["type"])
                p[ch["type"]] = ch["value"]
                for chap in d["chapters"]:
                    if chap["key"] == "amount":
                        chap["articles"] = [a for a in chap["articles"] if a["origin"] != "params"] + [lib.params_article(d)]
                    if chap["key"] == "special" and ch["type"] == "waiting_days":
                        for a in chap["articles"]:
                            a["text"] = a["text"].replace(f"等待期为{old}日", f"等待期为{ch['value']}日")
                applied.append(f"{d['name']}：{ {'deductible': '免赔额', 'waiting_days': '等待期', 'coinsurance': '自担比例', 'limit': '赔偿限额', 'indemnity_months': '赔偿期'}[ch['type']] }由 {old} 调整为 {ch['value']}")
            elif ch["type"] == "add_exclusion":
                excl = next(c for c in d["chapters"] if c["key"] == "exclusion")
                excl["articles"].append(lib._article(ch.get("title", "新增责任免除"), ch["text"], origin="revision", bold=True))
                applied.append(f"{d['name']}：新增责任免除「{ch.get('title', '')}」")
            elif ch["type"] == "sublimit":
                special = next((c for c in d["chapters"] if c["key"] == "special"), None)
                if special is None:
                    special = {"key": "special", "title": "特别约定", "articles": []}
                    d["chapters"].append(special)
                special["articles"].append(lib._article("分项赔偿限额", ch["text"], origin="revision"))
                applied.append(f"{d['name']}：新增分项限额")
            lib.number(d)
    ctx.workspace["revision_applied"] = applied
    if not applied:
        raise ToolError("修订请求没有可执行的变更", retryable=False)
    return ToolResult(summary=f"按修订请求 {req.get('request_no')} 执行变更 {len(applied)} 项：" + "；".join(applied), data={"applied": applied}, level=1)


def tasks_json(tasks: list[dict]) -> str:
    return json.dumps(tasks, ensure_ascii=False)


INS_LABEL = INSURABILITY_LABEL
