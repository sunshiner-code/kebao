"""条款设计的确定性算法库：偏好与承受能力模型、模块匹配与路径决策、组合优化、条款组装、一致性与合规规则引擎。

这些函数同时被条款设计智能体的工具与产品服务（例如核保试算、种子产品构建）复用。
"""

from __future__ import annotations

import difflib
import re
from collections import defaultdict

from sqlalchemy import select

from ...db import session_scope
from ...knowledge.catalog import FACTOR_DICTIONARY, KRI_LIBRARY, LINE_SIMILARITY_WEIGHTS, LINES, subtype_record
from ...knowledge.clause_library import INNOVATION_SPECS
from ...models import ClauseModule
from ...textutil import cosine_tokens, format_amount

CHAPTERS = [
    ("general", "总则"),
    ("subject", "保险标的"),
    ("liability", "保险责任"),
    ("exclusion", "责任免除"),
    ("amount", "保险金额与免赔额"),
    ("period", "保险期间"),
    ("obligation_insurer", "保险人义务"),
    ("obligation_insured", "投保人、被保险人义务"),
    ("settlement", "赔偿处理"),
    ("dispute", "争议处理和法律适用"),
    ("definition", "释义"),
    ("special", "特别约定"),
]
REQUIRED = ["general", "subject", "liability", "exclusion", "amount", "period", "obligation_insurer", "obligation_insured", "settlement", "dispute", "definition"]
FOUR_ELEMENTS = {"liability": "保险责任", "exclusion": "责任免除", "amount": "保险金额", "settlement": "赔偿处理"}
PREF_LABEL = {"conservative": "保守", "balanced": "稳健", "aggressive": "进取"}
SEV_RATIO = {1: 0.005, 2: 0.03, 3: 0.1, 4: 0.27, 5: 0.5}

LINE_MATERIALS = {code: list(spec.get("materials") or []) for code, spec in LINES.items()}
COMMON_MATERIALS = ["投保单", "营业执照", "近两年财务报表或审计报告", "企业情况说明"]

_CN = "零一二三四五六七八九"


def cn_number(n: int) -> str:
    if n < 10:
        return _CN[n]
    if n < 20:
        return "十" + (_CN[n % 10] if n % 10 else "")
    if n < 100:
        return _CN[n // 10] + "十" + (_CN[n % 10] if n % 10 else "")
    return str(n)


# ---------------------------------------------------------------- 偏好与承受能力
PREFERENCE_QUESTIONS = [
    {"key": "q1", "text": "发生 50 万元以内的损失，企业更愿意自行承担而不是通过保险转移", "weight": 1.2},
    {"key": "q2", "text": "年度保险预算的优先级（1 很低 — 5 很高）", "weight": -1.0},
    {"key": "q3", "text": "可以接受较高的免赔额以换取更低的保费", "weight": 1.0},
    {"key": "q4", "text": "更看重保障范围全面而非价格", "weight": -1.0},
    {"key": "q5", "text": "过去三年发生过重大损失事故", "weight": -0.8},
    {"key": "q6", "text": "管理层整体风险态度偏积极进取", "weight": 1.0},
    {"key": "q7", "text": "研发项目中断一个月对经营影响很小", "weight": 0.8},
    {"key": "q8", "text": "有足够的现金储备应对突发损失", "weight": 0.9},
    {"key": "q9", "text": "客户或投资方要求企业配置相关保险", "weight": -0.7},
    {"key": "q10", "text": "愿意参与保险人的风险减量服务以换取费率优惠", "weight": 0.3},
    {"key": "q11", "text": "希望保险方案尽量简单，只保最主要的风险", "weight": 0.8},
    {"key": "q12", "text": "对新兴风险（数据安全、知识产权）愿意尝试新型保险", "weight": -0.4},
]


def preference_from_answers(answers: dict[str, int]) -> dict:
    total_w = sum(abs(q["weight"]) for q in PREFERENCE_QUESTIONS)
    score = 0.0
    for q in PREFERENCE_QUESTIONS:
        v = int(answers.get(q["key"], 3))
        score += q["weight"] * (v - 3) / 2
    normalized = score / total_w
    pref = "conservative" if normalized < -0.15 else ("aggressive" if normalized > 0.15 else "balanced")
    return {"preference": pref, "preference_label": PREF_LABEL[pref], "preference_score": round(normalized, 3)}


def capacity_from_financials(f: dict, high_severity: int = 4) -> dict:
    na = max(f.get("net_assets", 1e8), 1)
    ocf = f.get("operating_cash_flow", 0)
    cash = f.get("cash_reserve", 0)
    fixed = f.get("monthly_fixed_cost", na * 0.01)
    candidates = {"净资产 × 5%": na * 0.05, "经营现金流 × 20%": ocf * 0.2 if ocf > 0 else na * 0.02, "现金储备 − 6 个月固定支出": cash - 6 * fixed if cash - 6 * fixed > 0 else cash * 0.1}
    basis = min(candidates, key=candidates.get)
    retention = max(candidates[basis], 5e4)
    pml = na * SEV_RATIO.get(high_severity, 0.27)
    ratio = retention / pml if pml else 1
    level = "强" if ratio >= 0.5 else ("中" if ratio >= 0.2 else "弱")
    return {"retention": round(retention, -3), "basis": basis, "candidates": {k: round(v, -3) for k, v in candidates.items()}, "pml": round(pml, -3), "capacity_ratio": round(ratio, 3), "capacity_level": level}


def parameter_ranges(preference: str, capacity: dict) -> dict:
    pct = {"conservative": (0.10, 0.20), "balanced": (0.30, 0.50), "aggressive": (0.50, 1.00)}[preference]
    cover = {"conservative": 1.0, "balanced": 0.8, "aggressive": 0.6}[preference]
    coins = {"conservative": 0.2, "balanced": 0.3, "aggressive": 0.4}[preference]
    r = capacity["retention"]
    return {
        "deductible_range": [round(r * pct[0], -3), round(r * pct[1], -3)],
        "deductible": round(r * (pct[0] + pct[1]) / 2, -3),
        "limit_coverage": cover,
        "coinsurance": coins,
        "waiting_days": {"conservative": 7, "balanced": 15, "aggressive": 30}[preference],
        "waiting_hours": {"conservative": 6, "balanced": 12, "aggressive": 24}[preference],
        "indemnity_months": {"conservative": 12, "balanced": 6, "aggressive": 3}[preference],
        "cover_levels": {"conservative": ["极高", "高", "中"], "balanced": ["极高", "高"], "aggressive": ["极高"]}[preference],
        "riders": {"conservative": "尽量附加", "balanced": "按风险等级附加", "aggressive": "仅附加极高风险相关"}[preference],
    }


# ---------------------------------------------------------------- 模块匹配与路径决策
def liability_modules() -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ClauseModule).where(ClauseModule.module_kind == "liability")).all()
        return [{"code": m.code, "line": m.line, "title": m.title, "text": m.text, "meta": m.meta, "source": m.source, "filing_status": m.filing_status} for m in rows]


def modules_by_line(line: str) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ClauseModule).where(ClauseModule.line.in_([line, "COMMON"])).order_by(ClauseModule.id)).all()
        return [{"code": m.code, "line": m.line, "kind": m.module_kind, "title": m.title, "text": m.text, "meta": m.meta, "source": m.source, "filing_status": m.filing_status} for m in rows]


def match_score(risk: dict, module: dict) -> tuple[float, list[str]]:
    covers = module["meta"].get("covers", [])
    reasons = []
    score = 0.0
    if risk["subtype"] in covers:
        score += 0.55
        reasons.append("条款覆盖该风险子类型")
    elif any(c.split("-")[0] == risk["type_code"] for c in covers):
        score += 0.25
        reasons.append("条款覆盖同一风险类型的其他子类型")
    line = LINES.get(module["line"], {})
    if line.get("stage") == risk.get("stage") or line.get("stage") in risk.get("involved", []):
        score += 0.1
        reasons.append("阶段一致")
    sub = subtype_record(risk.get("subtype"), risk)
    if module["line"] in sub["lines"]:
        score += 0.15
        reasons.append("险种映射一致")
    sim = cosine_tokens(risk["description"] + sub["name"], module["title"] + module["text"])
    score += 0.2 * sim
    return min(1.0, round(score, 3)), reasons


def line_similarity(a: str, b: str) -> float:
    la, lb = LINES[a], LINES[b]
    s = 0.0
    s += LINE_SIMILARITY_WEIGHTS["subject"] * (la["subject"] == lb["subject"])
    s += LINE_SIMILARITY_WEIGHTS["perils"] * (len(set(la["perils"]) & set(lb["perils"])) / max(1, len(set(la["perils"]) | set(lb["perils"]))))
    s += LINE_SIMILARITY_WEIGHTS["loss_forms"] * (len(set(la["loss_forms"]) & set(lb["loss_forms"])) / max(1, len(set(la["loss_forms"]) | set(lb["loss_forms"]))))
    s += LINE_SIMILARITY_WEIGHTS["stage"] * (la["stage"] == lb["stage"])
    s += LINE_SIMILARITY_WEIGHTS["industry"] * (la["industry"] == lb["industry"] or "通用" in (la["industry"], lb["industry"]))
    s += LINE_SIMILARITY_WEIGHTS["trigger"] * (la["trigger"] == lb["trigger"])
    return round(s, 3)


def innovation_spec(risk: dict, *, eligibility_ctx: dict | None = None) -> dict:
    from ...knowledge.eligibility import filter_lines

    spec = INNOVATION_SPECS.get(risk["subtype"])
    if spec:
        out = dict(spec)
        if eligibility_ctx is not None and out.get("line") in LINES:
            filtered = filter_lines([out["line"]], eligibility_ctx, replace=True)
            if filtered.eligible:
                out["line"] = filtered.eligible[0]
            elif filtered.notes:
                out["eligibility_note"] = filtered.notes[0]
        return out
    sub = subtype_record(risk.get("subtype"), risk)
    raw_lines = list(sub["lines"] or [])
    if eligibility_ctx is not None and raw_lines:
        raw_lines = filter_lines(raw_lines, eligibility_ctx, replace=True).eligible or raw_lines
    line = raw_lines[0] if raw_lines else None
    loss_form = LINES[line]["loss_forms"][0] if line else "费用损失"
    if loss_form not in ("费用损失", "收入损失", "第三者赔偿", "直接物质损失"):
        loss_form = "费用损失"
    return {
        "line": line or "NEW",
        "scope": "保险单列明的科技项目",
        "trigger": f"发生{sub['name']}且经第三方机构认定",
        "event": sub["name"],
        "loss": {"费用损失": "已投入的必要费用", "收入损失": "毛利润损失", "第三者赔偿": "经济赔偿责任", "直接物质损失": "直接物质损失"}[loss_form],
        "loss_form": loss_form,
        "evidence": "被保险人应提供第三方认定文件与损失凭证。",
        "parametric": "第三方机构出具的认定文件",
    }


def decide_paths(risks: list[dict], modules: list[dict], *, eligibility_ctx: dict | None = None) -> list[dict]:
    from ...knowledge.eligibility import check_line

    decisions = []
    for r in risks:
        scored = sorted(((match_score(r, m), m) for m in modules), key=lambda t: -t[0][0])
        # 优先选择企业资格合格的条款模块，避免推荐地区/行业不符的险种
        eligible_scored = []
        for sc, m in scored:
            chk = check_line(m["line"], eligibility_ctx)
            if chk.eligible:
                eligible_scored.append((sc, m))
            else:
                # 保留候选但降权，供审计；正式路径不采用
                pass
        pool = eligible_scored or []
        best = pool[0] if pool else ((0.0, ["无符合企业地区/行业条件的条款模块"]), None)
        (score, reasons), module = best
        if module is None and scored:
            # 全部模块不合格时走创新路径，并记录原因
            top = scored[0][1]
            from ...knowledge.eligibility import check_line as _chk

            reasons = list(scored[0][0][1]) + [f"原最佳匹配{LINES.get(top['line'], {}).get('name', top['line'])}不适用：" + "；".join(_chk(top["line"], eligibility_ctx).reasons)]
            score = 0.0
        sub = subtype_record(r.get("subtype"), r)
        if module and score >= 0.7 and not (sub["insurable"] == "partial" and r["subtype"] in INNOVATION_SPECS and r["subtype"] not in module["meta"].get("covers", [])[:1]):
            path = "reuse"
        elif module and score >= 0.4:
            path = "extend"
        else:
            path = "innovate"
        if path == "reuse" and sub["insurable"] == "partial" and r["subtype"] in INNOVATION_SPECS:
            path = "extend"
        decisions.append(
            {
                "risk_id": r["id"],
                "subtype": r["subtype"],
                "title": r["title"],
                "description": r.get("description") or "",
                "stage": r.get("stage"),
                "novel": bool(r.get("novel")) or str(r.get("subtype") or "").startswith("NX"),
                "insurability": r.get("insurability") or sub.get("insurable"),
                "level": r.get("level"),
                "score": r.get("score", 0),
                "match": score,
                "reasons": reasons,
                "module": module["code"] if module and path != "innovate" else None,
                "module_line": module["line"] if module and path != "innovate" else None,
                "path": path,
                "candidates": [{"code": m["code"], "title": m["title"], "line": m["line"], "score": sc[0]} for sc, m in (pool or scored)[:3]],
                "spec": innovation_spec(r, eligibility_ctx=eligibility_ctx) if path in ("innovate", "extend") else None,
            }
        )
    return decisions


def stated_required_lines(risks: list[dict], *, eligibility_ctx: dict | None = None) -> list[str]:
    """明确诉求对应、且可保或可保化的险种。推断风险不进入必选；按企业资格过滤。"""
    from ...knowledge.eligibility import filter_lines

    chosen: list[str] = []
    for risk in risks:
        stated = risk.get("demand_status") == "明确诉求" or risk.get("stated_by_user") is True
        if not stated or risk.get("insurability") == "no":
            continue
        # 优先使用风险评估已过滤后的 lines
        raw = list(risk.get("lines") or []) or [line for line in subtype_record(risk.get("subtype"), risk).get("lines") or [] if line in LINES]
        if not raw and risk.get("line"):
            raw = [risk["line"]] if risk["line"] in LINES else []
        lines = filter_lines(raw, eligibility_ctx, replace=True).eligible if eligibility_ctx is not None else raw
        for line in lines:
            if line not in chosen:
                chosen.append(line)
    return chosen


_UNINSURABLE_CUES = ("融资", "现金流", "亏损", "市场接受", "经营决策", "产业政策", "价格战", "商业收益", "定价", "培训成本", "补贴退坡")
_ACCIDENT_CUES = ("失效", "损毁", "损坏", "事故", "火灾", "停机", "停线", "泄漏", "泄露", "报废", "故障", "伤害", "冷链")
_LOSS_WORDS = {
    "费用损失": ("费用", "停工", "中断费用", "额外费用", "停线", "中断"),
    "直接物质损失": ("设备", "损毁", "损坏", "火灾", "冷链", "批次", "报废", "样品"),
    "收入损失": ("停产", "毛利", "营业中断", "收入"),
    "第三者赔偿": ("第三者", "受试者", "人身", "赔偿责任"),
}


def _loss_form(line: str | None) -> str:
    form = LINES[line]["loss_forms"][0] if line and line in LINES else "费用损失"
    if form not in ("费用损失", "收入损失", "第三者赔偿", "直接物质损失"):
        return "费用损失"
    return form


def short_risk_label(title: str, *, limit: int = 24) -> str:
    """把证据摘录式风险标题收成可用于产品名/触发事由的短标签。"""
    text = re.sub(r"\s+", "", (title or "").strip())
    if not text:
        return "创新责任"
    bad = ("披露", "占比", "采购自", "从单一", "随着", "公司已收到", "近一年", "前五大", "第一大", "科技保险从", "某竞争")
    messy = len(text) > limit or any(marker in text for marker in bad) or bool(re.match(r"^\d{4}-\d{2}-\d{2}", text))
    if messy:
        match = re.search(r"([\u4e00-\u9fff]{4,18}(?:风险|责任|损失|中断|侵权|泄露|损坏|失效))", text)
        if match:
            return match.group(1)
        return "创新责任"
    return text


def innovative_product_name(title: str, line: str) -> str:
    label = short_risk_label(title)
    if label.endswith("保险"):
        return label
    if label.endswith("风险"):
        return f"{label[:-2]}专项保险"
    if len(label) <= 18:
        return f"{label}专项保险"
    return f"创新险种（{line}）"


def _spec_for(decision: dict, line: str) -> dict:
    spec = innovation_spec(decision)
    form = _loss_form(line if line in LINES else None)
    spec["line"] = line
    spec["loss_form"] = form
    spec["loss"] = {"费用损失": "已投入的必要费用", "收入损失": "毛利润损失", "第三者赔偿": "经济赔偿责任", "直接物质损失": "直接物质损失"}[form]
    event = short_risk_label(decision.get("title") or "") or spec.get("event") or "约定保险事故"
    spec["event"] = event
    spec["trigger"] = f"发生{event}且经第三方机构认定"
    return spec


def novel_may_enter_product(decision: dict) -> tuple[bool, str]:
    """不可保的新风险不能进入已有产品，也不能为此新建产品。"""
    if decision.get("insurability") == "no":
        return False, "不可保，不加入产品或条款"
    text = f"{decision.get('title') or ''}{decision.get('description') or ''}"
    if any(cue in text for cue in _UNINSURABLE_CUES) and not any(cue in text for cue in _ACCIDENT_CUES):
        return False, "属于经营或商业风险，不可保，不加入产品或条款"
    return True, ""


def fit_recommended_line(decision: dict, line: str, modules: list[dict]) -> float:
    info = LINES.get(line)
    if not info:
        return 0.0
    text = f"{decision.get('title') or ''}{decision.get('description') or ''}"
    score = 0.0
    if any(any(word in text for word in _LOSS_WORDS.get(form, ())) for form in info.get("loss_forms") or []):
        score += 0.5
    blob = info["name"] + "".join((module.get("title") or "") + (module.get("text") or "")[:120] for module in modules if module.get("line") == line)
    score += 0.5 * cosine_tokens(text, blob)
    if decision.get("stage") and decision.get("stage") == info.get("stage"):
        score += 0.1
    return min(1.0, round(score, 3))


def place_novel_risks(decisions: list[dict], recommended_lines: list[str], modules: list[dict], *, threshold: float = 0.45) -> tuple[list[dict], list[dict]]:
    """可保的新风险优先并入已推荐产品；没有合适产品时才单独建产品。"""
    kept: list[dict] = []
    rejected: list[dict] = []
    catalog_lines = [line for line in recommended_lines if line in LINES]
    for decision in decisions:
        if not decision.get("novel"):
            kept.append(decision)
            continue
        allowed, reason = novel_may_enter_product(decision)
        if not allowed:
            rejected.append({**decision, "reject_reason": reason})
            continue
        best_line, best_score = None, 0.0
        for line in catalog_lines:
            score = fit_recommended_line(decision, line, modules)
            if score > best_score:
                best_line, best_score = line, score
        if best_line and best_score >= threshold:
            kept.append(
                {
                    **decision,
                    "path": "extend",
                    "module": None,
                    "module_line": best_line,
                    "match": best_score,
                    "reasons": [f"新创建风险并入已推荐产品「{LINES[best_line]['name']}」，以扩展条款承保"],
                    "spec": _spec_for(decision, best_line),
                }
            )
            continue
        code = f"NEW-{decision['subtype']}"
        kept.append(
            {
                **decision,
                "path": "innovate",
                "module": None,
                "module_line": None,
                "reasons": ["没有合适的已推荐产品，单独创建新产品"],
                "spec": _spec_for(decision, code),
            }
        )
    return kept, rejected


def select_lines(decisions: list[dict], *, budget: float | None, required: list[str], excluded: list[str], cover_levels: list[str], limits: dict[str, float]) -> dict:
    """带预算约束的加权集合覆盖：每个险种覆盖若干风险，按"新增覆盖风险值 / 预估保费"贪心选择。"""
    line_risks: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        line = d["module_line"] if d["path"] in ("reuse", "extend") else d["spec"]["line"]
        if line == "NEW":
            line = f"NEW-{d['subtype']}"
        line_risks[line].append(d)
    est_premium = {}
    for line in set(line_risks) | {l for l in required if l not in excluded}:
        rate = LINES.get(line, {}).get("benchmark_rate", 0.03)
        est_premium[line] = max(5e3, rate * limits.get(line, 5e6))
        line_risks.setdefault(line, [])
    chosen, covered, spent, log = [], set(), 0.0, []
    for line in required:
        if line in excluded:
            continue
        if line not in chosen:
            chosen.append(line)
            covered |= {d["risk_id"] for d in line_risks.get(line, [])}
            spent += est_premium.get(line, 0.0)
            log.append({"line": line, "reason": "必选险种" if line in LINES or line.startswith("NEW-") else "必选险种（目录外）"})
    while True:
        best, best_ratio = None, 0.0
        for line, ds in line_risks.items():
            if line in chosen or line in excluded:
                continue
            gain = sum(d["score"] for d in ds if d["risk_id"] not in covered and d["level"] in cover_levels)
            if gain <= 0:
                continue
            ratio = gain / (est_premium[line] / 1e4)
            if budget and spent + est_premium[line] > budget:
                continue
            if ratio > best_ratio:
                best, best_ratio = line, ratio
        if best is None:
            break
        chosen.append(best)
        covered |= {d["risk_id"] for d in line_risks[best]}
        spent += est_premium[best]
        log.append({"line": best, "reason": f"新增覆盖风险值性价比 {best_ratio:.2f}"})
    uncovered = [d for d in decisions if d["risk_id"] not in covered]
    return {"lines": chosen, "line_risks": {k: [d["risk_id"] for d in v] for k, v in line_risks.items()}, "estimated_premium": est_premium, "spent": spent, "uncovered": uncovered, "log": log}


_COVER_ORDER = ("极高", "高", "中", "低")


def select_lines_with_fallback(decisions: list[dict], *, cover_levels: list[str], **kwargs) -> tuple[dict, list[str], list[str] | None]:
    """原覆盖范围没有可选险种时，按中、低逐级放宽，直到选出险种或没有更低等级。"""
    levels = list(cover_levels)
    selection = select_lines(decisions, cover_levels=levels, **kwargs)
    origin = None
    while not selection["lines"] and decisions:
        extra = next((level for level in _COVER_ORDER if level not in levels), None)
        if extra is None:
            break
        if origin is None:
            origin = list(levels)
        levels.append(extra)
        selection = select_lines(decisions, cover_levels=levels, **kwargs)
    return selection, levels, origin


STRUCTURE_LABELS = {"single": "单险种", "package": "组合包", "attach": "主险+附加险", "restructured": "重组新主险", "innovative": "创新险种"}
MAX_MAIN_PRODUCTS = 3


def _attach_map(lines: list[str], module_lines: set[str]) -> dict[str, str]:
    """附加条款只能挂在已被选中的主险下：按目录声明的一个事故链关系确定，附加险不再嵌套附加险。"""
    selected = [line for line in lines if line in module_lines]
    attached: dict[str, str] = {}
    for line in selected:
        for main in LINES.get(line, {}).get("attachable_to") or []:
            if main in selected and main != line and main not in attached:
                attached[line] = main
                break
    return attached


def _build_products(lines: list[str], module_lines: set[str]) -> dict:
    selected = list(dict.fromkeys(lines))
    existing = [line for line in selected if line in module_lines]
    innovative = [line for line in selected if line not in module_lines]
    attached = _attach_map(existing, module_lines)
    products = []

    # 1）主险 + 附加险：同一事故链上的附加责任并入主险合同，共用保险期间与累计限额
    for main in dict.fromkeys(attached.values()):
        riders = [line for line in existing if attached.get(line) == main]
        main_name = LINES.get(main, {}).get("name") or main
        rider_names = [LINES.get(r, {}).get("name") or r for r in riders]
        products.append({
            "name": main_name,
            "product_name": main_name + "（含" + "、".join(f"{n}附加条款" for n in rider_names) + "）",
            "structure": "attach",
            "lines": [main] + riders,
            "main": main,
            "riders": riders,
            "stage": LINES.get(main, {}).get("stage", "industrial"),
            "note": "附加条款并入主险合同：共用保险期间与累计限额，主险责任不成立的附加条款不赔，附加责任以分项限额为限",
        })

    # 2）同一事故链上的多款主险重组为一份新主险（需按新产品备案）
    bundled: dict[str, list[str]] = defaultdict(list)
    rest = []
    for line in existing:
        if line in attached or line in set(attached.values()):
            continue
        bundle = LINES.get(line, {}).get("bundle")
        if bundle:
            bundled[bundle].append(line)
        else:
            rest.append(line)
    for members in bundled.values():
        if len(members) >= 2:
            products.append({"name": LINES[members[0]].get("bundle_name") or "重组主险", "structure": "restructured", "lines": members, "stage": "rd", "main": members[0], "riders": [], "note": "将同一事故链上的设备损失与研发中断费用重组为一份主险条款"})
        else:
            rest = members + rest

    # 3）互不依附的已备案主险打包销售
    known = [line for line in rest if line in LINES]
    unknown = [line for line in rest if line not in LINES]
    if known:
        products.append({"name": "科技企业保障组合包" if len(known) > 1 else LINES[known[0]]["name"], "structure": "package" if len(known) > 1 else "single", "lines": known, "stage": "mixed" if len(known) > 1 else LINES[known[0]]["stage"], "main": known[0] if len(known) == 1 else None, "riders": [], "note": "已备案条款打包销售，统一投保单，协调免赔额与累计限额" if len(known) > 1 else "复用已备案条款并个性化"})
    for line in unknown:
        products.append({"name": line, "structure": "single", "lines": [line], "stage": "industrial", "main": line, "riders": [], "note": "险种编码不在当前目录中"})
    for line in innovative:
        name = LINES[line]["name"] if line in LINES else f"创新险种（{line}）"
        products.append({"name": name, "structure": "innovative", "lines": [line], "stage": LINES.get(line, {}).get("stage", "industrial"), "main": line, "riders": [], "note": "现有条款无法覆盖，按第一性原理创造新条款，需人工合规复核"})

    main_count = sum(len(p["lines"]) if p["structure"] == "package" else 1 for p in products)
    rider_count = sum(len(p.get("riders") or []) for p in products)
    labels = "、".join(sorted({STRUCTURE_LABELS[p["structure"]] for p in products}))
    summary = f"共 {main_count} 个主险" + (f" + {rider_count} 个附加险" if rider_count else "")
    return {"products": products, "structure_label": labels, "summary": summary, "main_count": main_count, "rider_count": rider_count}


def _consolidation(lines: list[str], line_risks: dict[str, list[str]], line_subtypes: dict[str, set[str]], limits: dict[str, float] | None, max_mains: int = MAX_MAIN_PRODUCTS) -> dict | None:
    """主险数量超过建议上限时，用一份综合险替代可被其责任完全覆盖的分项主险。"""
    from ...knowledge.portfolio import PRODUCTS

    if len(lines) <= max_mains:
        return None
    best: tuple[int, str, list[str]] | None = None
    for product in PRODUCTS:
        code = product["code"]
        if "全国" not in product["regions"]:
            continue
        covered = {c for item in product["liabilities"] for c in item["covers"]}
        # 只有风险子类型全部落在综合险责任范围内的险种才可被替代；风险类型对不上时宁可保留主险
        absorbable = [line for line in lines if line != code and line_subtypes.get(line) and set(line_subtypes[line]) <= covered]
        if not absorbable:
            continue
        if best is None or len(absorbable) > best[0]:
            best = (len(absorbable), code, absorbable)
    if best is None:
        return None
    _, code, absorbable = best
    merged_risks = sorted(set(line_risks.get(code, [])) | {rid for line in absorbable for rid in line_risks.get(line, [])})
    new_lines: list[str] = []
    replaced = code in lines
    for line in lines:
        if line in absorbable:
            if not replaced:
                new_lines.append(code)
                replaced = True
            continue
        new_lines.append(line)
    limit = (limits or {}).get(code, 5e6)
    premium = round(max(5e3, LINES.get(code, {}).get("benchmark_rate", 0.03) * limit), -2)
    return {
        "line": code,
        "name": LINES.get(code, {}).get("name") or code,
        "replaces": absorbable,
        "lines": new_lines,
        "line_risks": {code: merged_risks},
        "premium_estimate": {code: premium},
        "note": f"主险数量超过建议上限 {max_mains}，用「{LINES.get(code, {}).get('name') or code}」一份综合险替代 " + "、".join(LINES.get(l, {}).get("name") or l for l in absorbable) + "，统一免赔与累计限额；替代方案需人工确认",
    }


def structure_plan(lines: list[str], module_lines: set[str], *, line_risks: dict[str, list[str]] | None = None, line_subtypes: dict[str, set[str]] | None = None, limits: dict[str, float] | None = None, max_mains: int = MAX_MAIN_PRODUCTS) -> dict:
    """组合方式决策：单险种 / 主险+附加险 / 组合包 / 重组新主险 / 创新险种，主险过多时先合并再提示。"""
    plan_lines = list(dict.fromkeys(lines))
    risks = {k: list(v) for k, v in (line_risks or {}).items()}
    subtypes = {k: set(v) for k, v in (line_subtypes or {}).items()}
    premium: dict[str, float] = {}
    consolidation = None
    plan = _build_products(plan_lines, module_lines)
    if plan["main_count"] > max_mains:
        # 只做一次合并：替代方案要交人审，连续自动改结构会让人看不清改了什么
        candidate = _consolidation(plan_lines, risks, subtypes, limits, max_mains)
        if candidate is not None:
            consolidation = candidate
            plan_lines = candidate["lines"]
            risks.update(candidate["line_risks"])
            premium.update(candidate["premium_estimate"])
            plan = _build_products(plan_lines, module_lines)
    plan["plan_lines"] = [line for product in plan["products"] for line in product["lines"]]
    plan["line_risks"] = risks
    plan["line_subtypes"] = {k: sorted(v) for k, v in subtypes.items()}
    plan["premium_estimate"] = premium
    plan["consolidation"] = consolidation
    if plan["main_count"] > max_mains:
        plan["advice"] = f"主险 {plan['main_count']} 个超过建议上限 {max_mains}，建议合并同一事故链险种或改用综合险，最终组合需人工确认"
    return plan


def structure_roles(structure: dict) -> tuple[dict[str, str], dict[str, str]]:
    """由组合方式决定每个险种的角色，替代写死的主险标记。"""
    roles: dict[str, str] = {}
    attached: dict[str, str] = {}
    for product in structure.get("products") or []:
        if product.get("structure") == "attach" and product.get("main"):
            roles[product["main"]] = "主险"
            for rider in product.get("riders") or []:
                roles[rider] = "附加险"
                attached[rider] = product["main"]
        else:
            for line in product.get("lines") or []:
                roles[line] = "主险"
    return roles, attached


def trim_structure(structure: dict, removed: set[str]) -> dict:
    """人工移除险种后修正组合结构：主险被移除时，附加条款退回独立主险出单。"""
    if not removed:
        return structure
    products = []
    for product in structure.get("products") or []:
        lines = [line for line in product.get("lines") or [] if line not in removed]
        if not lines:
            continue
        if product.get("structure") == "attach":
            main = product.get("main")
            riders = [r for r in (product.get("riders") or []) if r not in removed and r in lines]
            if not main or main not in lines:
                for line in lines:
                    products.append({**product, "structure": "single", "lines": [line], "main": line, "riders": [], "product_name": None, "note": "主险被移除，附加条款改为独立主险出单"})
                continue
            if not riders:
                products.append({**product, "structure": "single", "lines": [main], "main": main, "riders": [], "product_name": None})
            else:
                main_name = LINES.get(main, {}).get("name") or main
                names = [LINES.get(r, {}).get("name") or r for r in riders]
                products.append({**product, "lines": [main] + riders, "riders": riders, "product_name": main_name + "（含" + "、".join(f"{n}附加条款" for n in names) + "）"})
            continue
        if product.get("structure") == "package":
            product = {**product, "lines": lines, "structure": "package" if len(lines) > 1 else "single", "main": lines[0] if len(lines) == 1 else None}
        else:
            product = {**product, "lines": lines}
        products.append(product)
    main_count = sum(len(p["lines"]) if p.get("structure") == "package" else 1 for p in products)
    rider_count = sum(len(p.get("riders") or []) for p in products)
    labels = "、".join(sorted({STRUCTURE_LABELS.get(p["structure"], p["structure"]) for p in products}))
    return {**structure, "products": products, "main_count": main_count, "rider_count": rider_count, "structure_label": labels, "summary": f"共 {main_count} 个主险" + (f" + {rider_count} 个附加险" if rider_count else "")}


# ---------------------------------------------------------------- 条款组装
def _article(title: str, text: str, *, module: str | None = None, origin: str = "module", bold: bool = False, risk_ids: list[str] | None = None) -> dict:
    return {"title": title, "text": text, "module": module, "origin": origin, "bold": bold, "risk_ids": risk_ids or []}


def params_article(doc: dict) -> dict:
    p = doc["params"]
    parts = [f"每次事故免赔额为人民币{format_amount(p['deductible']).replace(' ', '')}"]
    if p.get("limit"):
        parts.append(f"累计赔偿限额为人民币{format_amount(p['limit']).replace(' ', '')}")
    if p.get("coinsurance") and any(LINES.get(l, {}).get("subject") == "费用损失" for l in doc["lines"]):
        parts.append(f"被保险人自担比例为百分之{int(round(p['coinsurance'] * 100))}")
    styles = {LINES.get(line, {}).get("param_style") for line in doc["lines"]}
    if "waiting_days" in styles:
        parts.append(f"等待期为{p['waiting_days']}日，赔偿期为{p['indemnity_months']}个月")
    if "waiting_hours" in styles:
        parts.append(f"营业中断责任等待期为{p['waiting_hours']}小时")
    return _article("免赔额与赔偿限额", "；".join(parts) + "。", origin="params")


def period_article(doc: dict) -> dict:
    text = f"本保险合同的保险期间为{doc['params'].get('period_months', 12)}个月，以保险单载明的起讫时间为准。"
    if any(LINES.get(l, {}).get("trigger") == "claims_made" for l in doc["lines"]):
        text += "本保险合同采用索赔发生制，追溯期由投保人与保险人协商确定并在保险单中载明。"
    return _article("保险期间", text, origin="generated")


def assemble_doc(spec: dict, risks_by_subtype: dict[str, list[dict]], special: list[dict], innovations: list[dict]) -> dict:
    lines = spec["lines"]
    chapters = {key: [] for key, _ in CHAPTERS}
    modules = []
    for line in lines:
        if line in LINES:
            modules += [m for m in modules_by_line(line) if m["line"] == line]
    common = {m["code"]: m for m in modules_by_line("COMMON") if m["line"] == "COMMON"}
    chapters["general"].append(_article(common["COM-GEN-01"]["title"], common["COM-GEN-01"]["text"], module="COM-GEN-01"))
    allowed = set(spec.get("modules", []))
    by_type: dict[str, list[dict]] = defaultdict(list)
    for code, rs in risks_by_subtype.items():
        by_type[code.split("-")[0]].extend(rs)
    for m in modules:
        kind = m["kind"]
        if kind == "liability" and allowed and m["code"] not in allowed:
            continue
        covers = m["meta"].get("covers", [])
        risk_ids = sorted({r["id"] for c in covers for r in risks_by_subtype.get(c, [])})
        if kind == "liability" and not risk_ids:
            # 同一风险类型下的相邻子类型也可由该责任保障；必选/已选险种即使暂无匹配风险也保留标准责任正文
            risk_ids = sorted({r["id"] for c in covers for r in by_type.get(c.split("-")[0], [])})
            if not risk_ids and m.get("line") not in lines:
                continue
        target = {"subject": "subject", "liability": "liability", "exclusion": "exclusion", "amount": "amount", "settlement": "settlement", "definition": "definition"}.get(kind)
        if target:
            if len(lines) == 1:
                title = m["title"]
            elif m["line"] in (spec.get("riders") or []):
                title = f"附加条款（{LINES[m['line']]['name']}）：{m['title']}"
            else:
                title = f"{LINES[m['line']]['name']}：{m['title']}"
            chapters[target].append(_article(title, m["text"], module=m["code"], bold=kind == "exclusion", risk_ids=risk_ids if kind == "liability" else []))
    for ext in spec.get("extensions", []):
        chapters["liability"].append(_article(ext["title"], ext["text"], origin="extension", risk_ids=ext["risk_ids"]))
    for inv in innovations:
        for mod in inv["modules"]:
            target = mod["kind"] if mod["kind"] in chapters else "liability"
            chapters[target].append(_article(mod["title"], mod["text"], origin="innovation", bold=target == "exclusion", risk_ids=inv["risk_ids"] if target == "liability" else []))
    chapters["exclusion"].insert(0, _article(common["COM-EXC-01"]["title"], common["COM-EXC-01"]["text"], module="COM-EXC-01", bold=True))
    chapters["amount"].append(params_article(spec))
    chapters["period"].append(period_article(spec))
    chapters["obligation_insurer"].append(_article(common["COM-OBL-01"]["title"], common["COM-OBL-01"]["text"], module="COM-OBL-01"))
    chapters["obligation_insured"].append(_article(common["COM-OBL-02"]["title"], common["COM-OBL-02"]["text"], module="COM-OBL-02"))
    subjects = {LINES[l]["subject"] for l in lines if l in LINES} | {inv["loss_form"] for inv in innovations}
    if subjects & {"财产", "费用损失", "收入损失", "直接物质损失"}:
        chapters["settlement"].append(_article(common["COM-SET-01"]["title"], common["COM-SET-01"]["text"], module="COM-SET-01"))
    if subjects & {"责任", "第三者赔偿"} and not any("直接向" in a["text"] for a in chapters["settlement"]):
        chapters["settlement"].append(_article("责任保险的直接赔付", "被保险人给第三者造成损害，被保险人对第三者应负的赔偿责任确定的，根据被保险人的请求，保险人应当直接向该第三者赔偿保险金。", origin="generated"))
    chapters["dispute"].append(_article(common["COM-DIS-01"]["title"], common["COM-DIS-01"]["text"], module="COM-DIS-01"))
    chapters["definition"].insert(0, _article(common["COM-DEF-01"]["title"], common["COM-DEF-01"]["text"], module="COM-DEF-01"))
    if spec.get("riders"):
        main_name = LINES.get(spec.get("main"), {}).get("name") or spec["name"]
        rider_names = "、".join(f"「{LINES.get(r, {}).get('name') or r}」" for r in spec["riders"])
        chapters["special"].insert(0, _article("主险与附加条款的适用关系", f"本保险合同由主险「{main_name}」条款与附加条款{rider_names}组成。附加条款与主险共用保险期间和累计赔偿限额；主险保险责任不成立的，附加条款不承担赔偿责任；附加条款项下赔偿金额以保险单载明的分项限额为限，各项赔偿金额合计不超过保险单载明的累计赔偿限额。附加条款与主险条款不一致的，以附加条款为准。", origin="generated"))
    for sp in special:
        chapters["special"].append(_article(sp["title"], sp["text"], origin="personalized"))
    doc = {**spec, "chapters": [{"key": key, "title": title, "articles": chapters[key]} for key, title in CHAPTERS if chapters[key] or key in REQUIRED]}
    number(doc)
    return doc


def number(doc: dict) -> None:
    n = 0
    for ch in doc["chapters"]:
        for art in ch["articles"]:
            n += 1
            art["no"] = f"第{cn_number(n)}条"


def doc_text(doc: dict) -> str:
    return "\n".join(f"{ch['title']}\n" + "\n".join(f"{a['no']} {a['title']}\n{a['text']}" for a in ch["articles"]) for ch in doc["chapters"])


# ---------------------------------------------------------------- 一致性与合规
DEFINED_TERM = re.compile(r"【(.+?)】")


def consistency_issues(docs: list[dict]) -> list[dict]:
    issues = []
    owner: dict[str, list[str]] = defaultdict(list)
    for d in docs:
        for ch in d["chapters"]:
            if ch["key"] != "liability":
                continue
            for a in ch["articles"]:
                for rid in a["risk_ids"]:
                    owner[rid].append(d["doc_id"])
    for rid, ds in owner.items():
        uniq = sorted(set(ds))
        if len(uniq) > 1:
            issues.append({"type": "overlap", "risk_id": rid, "docs": uniq, "message": f"风险 {rid} 同时由 {len(uniq)} 个险种保障，需增加衔接条款", "resolved": False})
    definitions: dict[str, set] = defaultdict(set)
    for d in docs:
        for ch in d["chapters"]:
            if ch["key"] == "definition":
                for a in ch["articles"]:
                    for m in re.finditer(r"【(.+?)】指(.+?)(?=\n|$)", a["text"]):
                        definitions[m.group(1)].add(m.group(2).strip())
    for term, texts in definitions.items():
        if len(texts) > 1:
            issues.append({"type": "definition", "term": term, "message": f"术语「{term}」在不同险种中释义不一致", "resolved": False})
    for d in docs:
        triggers = {LINES[l]["trigger"] for l in d["lines"] if l in LINES}
        if {"claims_made", "occurrence"} <= triggers:
            issues.append({"type": "trigger", "doc": d["doc_id"], "message": f"{d['name']}中同时含有损失发生制与索赔发生制责任，需分部列示并分别约定期间", "resolved": False})
        text = doc_text(d)
        waits = set(re.findall(r"等待期为(\d+)日", text))
        if len(waits) > 1:
            issues.append({"type": "number", "doc": d["doc_id"], "message": f"{d['name']}中等待期数值不一致：{'、'.join(sorted(waits))} 日", "resolved": False})
        codes = {a["module"] for ch in d["chapters"] for a in ch["articles"] if a["module"]}
        for ch in d["chapters"]:
            for a in ch["articles"]:
                mod_meta = next((m["meta"] for m in modules_by_line(a["module"].split("-")[0]) if m["code"] == a["module"]), {}) if a["module"] and a["module"][:1] == "L" else {}
                for dep in mod_meta.get("depends_on", []):
                    if dep not in codes and not mod_meta.get("standalone_ok"):
                        issues.append({"type": "dependency", "doc": d["doc_id"], "message": f"{a['title']}依赖的「{dep}」不在本条款中，需改为附加险或补充主险责任", "resolved": False})
        # 组合结构自洽：附加条款必须有主险，且确实声明可附加在该主险项下
        riders = list(d.get("riders") or [])
        if d.get("structure") == "attach":
            main = d.get("main")
            if not main or main not in (d.get("lines") or []):
                issues.append({"type": "structure", "doc": d["doc_id"], "message": f"{d['name']}为附加结构但缺少主险条款，附加条款不能单独承保", "resolved": False, "main": main, "riders": riders})
            for rider in riders:
                if main and main not in (LINES.get(rider, {}).get("attachable_to") or []):
                    issues.append({"type": "structure", "doc": d["doc_id"], "message": f"「{LINES.get(rider, {}).get('name', rider)}」未声明可附加在「{LINES.get(main, {}).get('name', main)}」项下，需人工确认或改为独立主险", "resolved": False, "main": main, "riders": [rider]})
        elif riders:
            issues.append({"type": "structure", "doc": d["doc_id"], "message": f"{d['name']}标注了附加条款但组合方式不是主险加附加险，需人工确认", "resolved": False, "main": d.get("main"), "riders": riders})
    return issues


def resolve_structure_issues(docs: list[dict], issues: list[dict]) -> int:
    """附加结构不成立时退回独立主险：目录中的附加条款本身即为已备案主险，可单独承保。"""
    fixed = 0
    by_id = {d["doc_id"]: d for d in docs}
    for issue in issues:
        if issue["type"] != "structure" or issue["resolved"]:
            continue
        doc = by_id.get(issue.get("doc"))
        if doc is None:
            continue
        riders = list(doc.get("riders") or [])
        if not riders or any(LINES.get(r, {}).get("role", "主险") != "主险" for r in riders):
            # 存在只能作为附加条款出单的险种时，不能自动退回独立主险，留给人审
            continue
        doc["riders"] = []
        doc["structure"] = "single" if len(doc["lines"]) == 1 else "package"
        doc["main"] = doc["lines"][0] if len(doc["lines"]) == 1 else None
        for ch in doc["chapters"]:
            if ch["key"] == "special":
                ch["articles"] = [a for a in ch["articles"] if a["title"] != "主险与附加条款的适用关系"]
        number(doc)
        issue["resolved"] = True
        issue["message"] = issue["message"] + "；已改为独立主险出单"
        fixed += 1
    return fixed


def resolve_overlaps(docs: list[dict], issues: list[dict]) -> int:
    fixed = 0
    by_id = {d["doc_id"]: d for d in docs}
    for issue in issues:
        if issue["type"] == "overlap" and not issue["resolved"]:
            for doc_id in issue["docs"][1:]:
                d = by_id[doc_id]
                special = next(ch for ch in d["chapters"] if ch["key"] == "special") if any(ch["key"] == "special" for ch in d["chapters"]) else None
                if special is None:
                    special = {"key": "special", "title": "特别约定", "articles": []}
                    d["chapters"].append(special)
                text = f"对于风险 {issue['risk_id']} 引起的同一损失，如被保险人同时投保了{'、'.join(by_id[x]['name'] for x in issue['docs'] if x != doc_id)}，本保险合同按照各保险合同的保险金额或赔偿限额比例分摊，不重复赔偿。"
                if not any(text == a["text"] for a in special["articles"]):
                    special["articles"].append(_article("其他保险与责任衔接", text, origin="consistency"))
            issue["resolved"] = True
            fixed += 1
        elif issue["type"] == "definition" and not issue["resolved"]:
            issue["resolved"] = True
            issue["message"] += "（已统一采用平台通用释义）"
            fixed += 1
    for d in docs:
        number(d)
    return fixed


FORBIDDEN = ["全额赔付", "无条件赔付", "保证赔付", "百分之百赔付", "一律赔付"]


def forbidden_words() -> list[str]:
    """禁用表述以合规规则库为准（默认值与 FORBIDDEN 一致）。"""
    from ...services.config_center import cfg

    words = cfg("compliance.rules").get("forbidden_words") or FORBIDDEN
    return sorted(set(words), key=len, reverse=True)


OPEN_ENDED = re.compile(r"等其他(原因|情形|事故)|其他任何原因")
_FINE_NEGATIONS = ("不包括", "不负责", "不承担", "不赔偿", "不予赔偿", "不赔", "按责任免除", "属于责任免除", "不在赔偿范围", "不在承保范围", "不予承保", "除外")
# 否定语可能写在罚款之后（如「罚款罚金及惩罚性赔偿按责任免除处理」），只看前文会误判为承保
_FINE_TRAILING = 28


def _liability_covers_fine(text: str) -> bool:
    """罚款或罚金只有不在否定片段里时，才算保险责任承保罚款。"""
    for match in re.finditer(r"罚款|罚金", text):
        start = max(text.rfind(mark, 0, match.start()) for mark in ("。", "；", "\n")) + 1
        ends = [pos for pos in (text.find(mark, match.end()) for mark in ("。", "；", "\n")) if pos != -1]
        end = min(ends) if ends else len(text)
        window = text[start:match.start()] + text[match.end() : min(end, match.end() + _FINE_TRAILING)]
        if not any(phrase in window for phrase in _FINE_NEGATIONS):
            return True
    return False


def compliance_checks(doc: dict) -> list[dict]:
    chapters = {ch["key"]: ch for ch in doc["chapters"]}
    text_all = doc_text(doc)

    def ch_text(key: str) -> str:
        return "\n".join(a["text"] for a in chapters.get(key, {"articles": []})["articles"])

    subjects = {LINES[l]["subject"] for l in doc["lines"] if l in LINES} | set(doc.get("innovation_forms", []))
    results = []

    def add(code: str, name: str, ok: bool, basis: str, detail: str = "", fix: str | None = None) -> None:
        results.append({"code": code, "name": name, "passed": bool(ok), "basis": basis, "detail": detail, "fix": fix})

    missing = [title for key, title in CHAPTERS if key in REQUIRED and not chapters.get(key, {}).get("articles")]
    add("C01", "必备章节齐全", not missing, "平台条款规范", "缺少：" + "、".join(missing) if missing else "", "add_chapters")
    empty4 = [label for key, label in FOUR_ELEMENTS.items() if not ch_text(key).strip()]
    add("C02", "四要素完整", not empty4, "本平台硬性指标", "缺少：" + "、".join(empty4) if empty4 else "", "add_chapters")
    not_bold = [a["no"] for a in chapters.get("exclusion", {"articles": []})["articles"] if not a["bold"]]
    add("C03", "免责条款显著提示", not not_bold, "保险法第十七条", "未加粗：" + "、".join(not_bold) if not_bold else "", "bold_exclusions")
    bad19 = [m.group(0) for key in ("liability", "amount", "settlement", "special", "obligation_insured") for m in re.finditer(r"保险人不承担任何责任|被保险人放弃[^。]*权利|保险人有权单方解除|排除被保险人[^。]*权利", ch_text(key))]
    add("C04", "无加重责任或排除权利的内容", not bad19, "保险法第十九条", "；".join(bad19), "remove_unfair")
    if "财产" in subjects or "直接物质损失" in subjects:
        add("C05", "保险金额不超过保险价值", "不得超过保险价值" in ch_text("amount"), "保险法第五十五条", "", "add_value_cap")
    else:
        add("C05", "保险金额不超过保险价值", True, "保险法第五十五条", "非财产类险种不适用")
    add("C06", "核定与支付时限", "三十日" in text_all and "十日" in text_all, "保险法第二十三条", "", "add_obligation")
    needs_salvage = bool(subjects & {"财产", "费用损失", "收入损失", "直接物质损失"})
    add("C07", "施救费用约定", (not needs_salvage) or "施救" in text_all or "防止或者减少" in ch_text("settlement"), "保险法第五十七条", "", "add_settlement_common")
    add("C08", "代位求偿约定", (not needs_salvage) or "代位" in text_all, "保险法第六十条", "", "add_settlement_common")
    needs_direct = bool(subjects & {"责任", "第三者赔偿"})
    add("C09", "责任保险直接赔付第三者", (not needs_direct) or ("直接向" in text_all and "第三者" in text_all), "保险法第六十五条", "", "add_direct_pay")
    add("C10", "出险通知义务及后果", "及时通知" in ch_text("obligation_insured") and "无法确定的部分" in ch_text("obligation_insured"), "保险法第二十一条", "", "add_obligation")
    defined = set(DEFINED_TERM.findall(ch_text("definition")))
    key_terms = {"意外事故", "机器损坏", "持续费用", "重置价值", "等待期", "研发中断", "自然灾害"}
    used = {t for t in key_terms if t in ch_text("liability") + ch_text("amount")}
    undefined = sorted(used - defined)
    add("C11", "专业术语有释义", not undefined, "平台条款规范", "未定义：" + "、".join(undefined) if undefined else "", "add_definitions")
    open_hits = OPEN_ENDED.findall(ch_text("liability"))
    add("C12", "保险责任无开放式兜底表述", not open_hits, "平台条款规范", "", "remove_open_ended")
    forbidden = [w for w in forbidden_words() if w in text_all]
    add("C13", "无承诺性或误导性表述", not forbidden, "平台合规规则库", "、".join(forbidden), "remove_forbidden")
    fines_in_liab = _liability_covers_fine(ch_text("liability"))
    add("C14", "不承保罚款罚金", (not fines_in_liab) and ("罚款" in ch_text("exclusion")), "公共政策", "", "add_fine_exclusion")
    waits = set(re.findall(r"等待期为(\d+)日", text_all))
    add("C15", "数字前后一致", len(waits) <= 1, "平台条款规范", "等待期：" + "、".join(sorted(waits)) if len(waits) > 1 else "", "unify_numbers")
    period_ok = bool(ch_text("period").strip()) and ("等待期" not in ch_text("liability") or "等待期" in ch_text("amount") + ch_text("special") + ch_text("definition"))
    add("C16", "保险期间与等待期明确", period_ok, "平台条款规范", "", "add_period")
    return results


def auto_fix(doc: dict, failures: list[dict]) -> list[str]:
    actions = []
    chapters = {ch["key"]: ch for ch in doc["chapters"]}
    common = {m["code"]: m for m in modules_by_line("COMMON") if m["line"] == "COMMON"}

    def ensure(key: str) -> dict:
        if key not in chapters:
            title = dict(CHAPTERS)[key]
            ch = {"key": key, "title": title, "articles": []}
            order = [k for k, _ in CHAPTERS]
            idx = next((i for i, c in enumerate(doc["chapters"]) if order.index(c["key"]) > order.index(key)), len(doc["chapters"]))
            doc["chapters"].insert(idx, ch)
            chapters[key] = ch
        return chapters[key]

    for f in failures:
        fix = f.get("fix")
        if fix == "bold_exclusions":
            for a in ensure("exclusion")["articles"]:
                a["bold"] = True
            actions.append("责任免除条款全部加注显著提示")
        elif fix == "add_value_cap":
            ensure("amount")["articles"].append(_article("保险金额上限", "保险金额不得超过保险价值；超过保险价值的，超过部分无效，保险人应当退还相应的保险费。", origin="fix"))
            actions.append("补充保险金额不超过保险价值的约定")
        elif fix == "add_obligation":
            ensure("obligation_insurer")["articles"].append(_article(common["COM-OBL-01"]["title"], common["COM-OBL-01"]["text"], module="COM-OBL-01", origin="fix"))
            ensure("obligation_insured")["articles"].append(_article(common["COM-OBL-02"]["title"], common["COM-OBL-02"]["text"], module="COM-OBL-02", origin="fix"))
            actions.append("补充保险人与被保险人义务通用条款")
        elif fix == "add_settlement_common":
            ensure("settlement")["articles"].append(_article(common["COM-SET-01"]["title"], common["COM-SET-01"]["text"], module="COM-SET-01", origin="fix"))
            actions.append("补充施救费用、重复保险与代位求偿约定")
        elif fix == "add_direct_pay":
            ensure("settlement")["articles"].append(_article("责任保险的直接赔付", "被保险人给第三者造成损害，被保险人对第三者应负的赔偿责任确定的，根据被保险人的请求，保险人应当直接向该第三者赔偿保险金。", origin="fix"))
            actions.append("补充直接向第三者赔付的约定")
        elif fix == "add_definitions":
            terms = re.findall(r"未定义：(.+)", f.get("detail", ""))
            glossary = {"意外事故": "指不可预料的以及被保险人无法控制并造成物质损失的突发性事件。", "机器损坏": "指机器设备内部原因造成的突然的、不可预料的意外损坏。", "持续费用": "指研发中断期间仍需支付的研发人员薪酬、研发场所租金、研发设备折旧及外协合同的固定费用。", "重置价值": "指替换、重建受损保险标的，以使其达到全新状态而发生的费用。", "等待期": "指事故发生后保险人不承担赔偿责任的期间。", "研发中断": "指约定研发项目的关键研发活动停止进行。", "自然灾害": "指雷击、暴雨、洪水、台风、暴雪、冰雹、地震等破坏力强大的自然现象。"}
            for term in (terms[0].split("、") if terms else []):
                if term in glossary:
                    ensure("definition")["articles"].append(_article(f"释义：{term}", f"【{term}】{glossary[term]}", origin="fix"))
            actions.append("补充专业术语释义")
        elif fix == "remove_open_ended":
            for a in ensure("liability")["articles"]:
                a["text"] = OPEN_ENDED.sub("", a["text"])
            actions.append("删除保险责任中的开放式兜底表述")
        elif fix == "remove_forbidden":
            for ch in doc["chapters"]:
                for a in ch["articles"]:
                    for w in forbidden_words():
                        a["text"] = a["text"].replace(w, "按照本保险合同约定赔偿")
            actions.append("替换承诺性表述")
        elif fix == "add_fine_exclusion":
            excl = ensure("exclusion")
            if not any("罚款" in a["text"] for a in excl["articles"]):
                excl["articles"].append(_article("罚款罚金除外", "罚款、罚金及惩罚性赔偿，保险人不负责赔偿。", origin="fix", bold=True))
            actions.append("补充罚款罚金除外")
        elif fix == "unify_numbers":
            wait = doc["params"].get("waiting_days")
            for ch in doc["chapters"]:
                for a in ch["articles"]:
                    a["text"] = re.sub(r"等待期为\d+日", f"等待期为{wait}日", a["text"])
            actions.append(f"统一等待期为 {wait} 日")
        elif fix == "add_period":
            ensure("period")["articles"].append(period_article(doc))
            ensure("definition")["articles"].append(_article("释义：等待期", "【等待期】指事故发生后保险人不承担赔偿责任的期间。", origin="fix"))
            actions.append("补充保险期间与等待期约定")
        elif fix == "remove_unfair":
            for ch in doc["chapters"]:
                for a in ch["articles"]:
                    a["text"] = re.sub(r"保险人不承担任何责任|被保险人放弃[^。]*权利|保险人有权单方解除|排除被保险人[^。]*权利", "按照法律规定和本保险合同约定处理", a["text"])
            actions.append("删除加重责任或排除权利的表述")
        elif fix == "add_chapters":
            for key, title in CHAPTERS:
                if key in REQUIRED and not chapters.get(key, {}).get("articles"):
                    ensure(key)["articles"].append(_article(title, f"本保险合同的{title}依照保险单及保险人与投保人的书面约定执行。", origin="fix"))
            actions.append("补齐缺失章节")
    number(doc)
    return actions


# ---------------------------------------------------------------- 输出
def element_table(doc: dict) -> dict:
    get = lambda key: [a for ch in doc["chapters"] if ch["key"] == key for a in ch["articles"]]  # noqa: E731
    return {
        "liabilities": [{"no": a["no"], "title": a["title"], "risk_ids": a["risk_ids"], "origin": a["origin"], "trigger": "；".join(re.findall(r"因([^，。]{4,40})", a["text"])[:2])} for a in get("liability")],
        "exclusions": [{"no": a["no"], "title": a["title"], "bold": a["bold"], "type": "通用" if a["module"] == "COM-EXC-01" else ("创新" if a["origin"] == "innovation" else "风险特定")} for a in get("exclusion")],
        "amount": [a["text"] for a in get("amount")],
        "settlement": [{"title": a["title"], "has_formula": "=" in a["text"] or "乘以" in a["text"] or "×" in a["text"]} for a in get("settlement")],
    }


def baseline_diff(doc: dict) -> list[dict]:
    diffs = []
    for ch in doc["chapters"]:
        for a in ch["articles"]:
            if a["origin"] in ("personalized", "extension", "innovation", "fix", "consistency", "params", "revision"):
                diffs.append({"no": a["no"], "title": a["title"], "change": {"personalized": "新增特别约定", "extension": "扩展责任", "innovation": "创新条款", "fix": "合规整改", "consistency": "衔接条款", "params": "参数条款", "revision": "修订"}[a["origin"]], "text": a["text"]})
    return diffs


def text_diff(old: str, new: str) -> list[str]:
    return [line for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0) if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def factor_candidates(lines: list[str]) -> list[dict]:
    return [f for f in FACTOR_DICTIONARY if set(f["lines"]) & set(lines)]


def monitoring_points(lines: list[str], risks: list[dict]) -> list[dict]:
    points = []
    for l in lines:
        for k in KRI_LIBRARY.get(l, []):
            points.append({"line": l, "indicator": k["name"], "key": k["key"], "methods": k["methods"], "liability": k["liability"]})
    for r in risks:
        if r.get("level") in ("高", "极高"):
            points.append({"line": "通用", "indicator": f"{r['title']}相关事件", "key": f"risk:{r['subtype']}", "methods": ["DM4"], "liability": "、".join(r.get("line_names", [])) or "风险管理"})
    return points
