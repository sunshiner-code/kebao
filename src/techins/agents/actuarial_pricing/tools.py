"""精算定价智能体的工具。"""

from __future__ import annotations

import math
import random

import numpy as np
import pandas as pd

from ...analytics import pricing as P
from ...harness import hitl
from ...harness.background import background_manager
from ...harness.tools import ToolError, ToolResult, registry
from ...knowledge.catalog import FACTOR_DICTIONARY, LINES
from ...knowledge.maturity import line_maturity
from ...llm.gateway import model_gateway
from ...textutil import format_amount
from ..clause_design.tools import load_artifact

FACTOR_INDEX = {f["key"]: f for f in FACTOR_DICTIONARY}
DEFAULT_TARGETS = {"loss_ratio": [0.45, 0.68], "expense_ratio": 0.25, "profit_ratio": 0.05, "combined_cap": 0.95, "capital_cost": 0.10, "max_loss_prob": 0.25, "min_coverage": 0.85}
_FRAMES: dict[tuple[int, str], pd.DataFrame] = {}
_MODELS: dict[tuple[int, str], object] = {}


def _comps(ctx) -> list[dict]:
    return ctx.workspace["components"]


def _frame(ctx, comp: dict) -> pd.DataFrame | None:
    key = (ctx.root_run_id, comp["id"])
    if key not in _FRAMES:
        df = P.load_experience(comp["line"])
        if df is None:
            return None
        text_keys = [k for k in comp.get("text_factors", [])]
        if text_keys:
            df, _ = P.apply_text_factors(df, text_keys)
        _FRAMES[key] = df
    return _FRAMES[key]


@registry.tool("load_clause_plan_for_pricing", "读取条款方案与待定价参数", "读取冻结的条款方案与风险评估报告，拆分定价组件并按定价数据成熟度选择方法组合", permission="T0", zone="platform")
def load_clause_plan_for_pricing(ctx, **_) -> ToolResult:
    ref = ctx.inputs.get("clause_plan") or {}
    clause = load_artifact(ref["artifact_key"], ref.get("version"))
    if clause["status"] != "frozen":
        raise ToolError("条款方案尚未冻结，不能定价", retryable=False)
    data = clause["data"]
    ws_params = data.get("_workspace", {}).get("params", {})
    comps = []
    for pp in data.get("pricing_params", []):
        for line in pp["lines"]:
            mat = line_maturity(line) if line in LINES else {"scores": {"pricing": 0}, "verdict": {"pricing": "cold"}}
            score = mat["scores"]["pricing"]
            methods = ["PM1", "PM2", "PM3"] if score >= 60 else (["PM1", "PM3", "PM4"] if score >= 30 else ["PM4", "PM3"])
            if ctx.inputs.get("methods"):
                methods = [m for m in ctx.inputs["methods"] if m in ("PM1", "PM2", "PM3", "PM4")] or methods
            params = dict(ws_params.get(line) or pp["params"])
            scenarios = [s for s in pp["scenarios"] if any(line in (LINES.get(l, {}) and [l]) for l in [line])] or pp["scenarios"]
            comps.append(
                {
                    "id": f"{pp['doc_id']}-{line}",
                    "doc_id": pp["doc_id"],
                    "doc_name": pp["name"],
                    "line": line,
                    "name": LINES.get(line, {}).get("name", line),
                    "structure": pp["structure"],
                    "params": params,
                    "scenarios": scenarios,
                    "maturity": score,
                    "start_mode": "warm" if score >= 60 else "cold",
                    "methods": methods,
                    "factor_keys": pp.get("factors", []),
                }
            )
    if not comps:
        raise ToolError("条款方案中没有待定价参数", retryable=False)
    ctx.workspace["components"] = comps
    ctx.workspace["clause_ref"] = {"artifact_key": clause["key"], "version": clause["version"]}
    ctx.workspace["report_ref"] = data.get("report_ref")
    ctx.workspace["enterprise"] = data.get("enterprise") or {}
    ctx.workspace["targets"] = {**DEFAULT_TARGETS, **(ctx.inputs.get("targets") or {})}
    from ...services.config_center import cfg

    bands = cfg("compliance.rules")["rate_bands"]
    ctx.workspace["filing_band"] = {c["id"]: (float(bands["warm"]) if c["start_mode"] == "warm" else float(bands["cold"])) for c in comps}
    return ToolResult(summary=f"定价组件 {len(comps)} 个：" + "；".join(f"{c['name']}（定价数据 {c['maturity']} 分，{'热' if c['start_mode'] == 'warm' else '冷'}启动，方法 {'+'.join(c['methods'])}）" for c in comps), data={"components": [c["id"] for c in comps]}, level=1, metrics={"components": len(comps)})


@registry.tool("prepare_experience_data", "经验数据准备", "加载并清洗经验数据，计算暴露、出险频率、案均损失与数据质量指标", permission="T0", zone="private", max_level=2)
def prepare_experience_data(ctx, **_) -> ToolResult:
    notes = []
    for c in _comps(ctx):
        df = P.load_experience(c["line"])
        if df is None:
            c["data"] = None
            c["data_note"] = "无经验数据"
            notes.append(f"{c['name']}：无经验数据")
            continue
        exposure = float(df["exposure"].sum())
        claims = int(df["claim_count"].sum())
        loss = float(df["claim_amount"].sum())
        over_si = int((df["claim_amount"] > df["sum_insured"]).sum())
        c["data"] = {"rows": int(len(df)), "years": sorted(df["year"].unique().tolist()), "exposure": round(exposure, 1), "claims": claims, "frequency": round(claims / exposure, 4), "severity": round(loss / max(claims, 1), 0), "pure_rate": round(loss / float((df["sum_insured"] * df["exposure"]).sum()), 6), "missing": int(df.isna().sum().sum()), "over_si": over_si}
        c["data_note"] = f"{len(df)} 条保单年记录（{min(c['data']['years'])}–{max(c['data']['years'])}），暴露 {exposure:.0f}，出险 {claims} 次"
        notes.append(f"{c['name']}：{c['data_note']}，频率 {c['data']['frequency']:.3f}，案均 {format_amount(c['data']['severity'])}")
    return ToolResult(summary="；".join(notes), data={c["id"]: c.get("data") for c in _comps(ctx)}, level=1)


@registry.tool("generate_candidate_factors", "大模型提出候选因子", "依据风险评估结论与条款责任提出候选风险因子，区分结构化因子与需从文本提取的因子", permission="T1", zone="cloud")
def generate_candidate_factors(ctx, **_) -> ToolResult:
    report = load_artifact(ctx.workspace["report_ref"]["artifact_key"], ctx.workspace["report_ref"]["version"])["data"] if ctx.workspace.get("report_ref") else {"risks": []}
    from .reasoning import text_factor_keys

    lines_out = []
    for c in _comps(ctx):
        structured = list(P.STRUCTURED_COLUMNS.get(c["line"], []))
        text = text_factor_keys(c["line"])
        available = [k for k in structured if k in FACTOR_INDEX or k in ("log_revenue",)] + text
        risks = [{"subtype": s["subtype"], "title": s["title"]} for s in c["scenarios"]] or [{"subtype": r["subtype"], "title": r["title"]} for r in report.get("risks", [])[:8]]
        if c.get("data") is None:
            available = [k for k in c.get("factor_keys", []) if k in FACTOR_INDEX][:6]
        out = model_gateway.invoke("ap.factor_proposals", {"line": c["line"], "line_name": c["name"], "risks": risks, "available": available, "dictionary": [{k: FACTOR_INDEX[k][k2] for k2 in ("name", "definition", "direction")} | {"key": k} for k in available if k in FACTOR_INDEX]}, ctx=ctx, data_level=1)
        cands = []
        for f in out["factors"]:
            key = f["key"]
            if key not in available:
                continue
            meta = FACTOR_INDEX.get(key, {"name": "营业收入（对数）", "definition": "营业收入的对数", "source": "structured", "kind": "numeric", "direction": 1})
            cands.append({"key": key, "name": meta["name"], "definition": meta["definition"], "source": "llm_text" if key in text else "structured", "direction": int(f.get("expected_direction", meta.get("direction", 0)) or 0), "rationale": f.get("rationale", "")})
        c["candidates"] = cands
        c["text_factors"] = [x["key"] for x in cands if x["source"] == "llm_text"]
        lines_out.append(f"{c['name']}：候选 {len(cands)} 个（文本提取 {len(c['text_factors'])} 个）")
    return ToolResult(summary="；".join(lines_out), data={c["id"]: c["candidates"] for c in _comps(ctx)}, level=1)


@registry.tool("extract_factor_values", "因子取值抽取", "私域模型对抽样文本抽取因子取值并与确定性复核器比对一致率，复核器把取值推广到全部记录，低置信度取值归入未知分箱", permission="T1", zone="private", max_level=2)
def extract_factor_values(ctx, **_) -> ToolResult:
    notes = []
    for c in _comps(ctx):
        keys = c.get("text_factors", [])
        df_raw = P.load_experience(c["line"])
        if not keys or df_raw is None:
            continue
        rng = random.Random(ctx.run_id)
        idx = rng.sample(range(len(df_raw)), min(40, len(df_raw)))
        texts = [{"id": str(i), "text": df_raw.iloc[i]["due_diligence_summary"]} for i in idx]
        agree = {k: [0, 0] for k in keys}
        for start in range(0, len(texts), 20):
            out = model_gateway.invoke("ap.extract_factor_values", {"texts": texts[start : start + 20], "factors": keys}, ctx=ctx, data_level=2)
            for row in out["rows"]:
                parsed = P.parse_text_factors(df_raw.iloc[int(row["id"])]["due_diligence_summary"])
                for k in keys:
                    llm_v = (row.get("values") or {}).get(k)
                    ref_v = parsed[k][0]
                    if llm_v is None or ref_v is None:
                        continue
                    agree[k][1] += 1
                    tol = 0.08 if k in ("maintenance_score", "negative_sentiment") else 0.02
                    if abs(float(llm_v) - float(ref_v)) <= max(tol, abs(ref_v) * 0.05):
                        agree[k][0] += 1
        _FRAMES.pop((ctx.root_run_id, c["id"]), None)
        df = _frame(ctx, c)
        _, stats_out = P.apply_text_factors(df_raw.copy(), keys)
        c["extraction"] = {k: {**stats_out[k], "sample": agree[k][1], "agreement": round(agree[k][0] / agree[k][1], 3) if agree[k][1] else None} for k in keys}
        notes.append(f"{c['name']}：" + "，".join(f"{FACTOR_INDEX[k]['name']}一致率 {c['extraction'][k]['agreement'] if c['extraction'][k]['agreement'] is not None else '—'}" for k in keys))
        del df
    return ToolResult(summary="；".join(notes) or "无需从文本提取因子", data={c["id"]: c.get("extraction") for c in _comps(ctx)}, level=2)


@registry.tool("screen_factors", "因子筛选", "信息价值、单调性、共线性、群体稳定性与合规检查，给出入模建议", permission="T1", zone="private")
def screen_factors(ctx, **_) -> ToolResult:
    notes = []
    for c in _comps(ctx):
        df = _frame(ctx, c)
        keys = [x["key"] for x in c.get("candidates", [])]
        if df is None or not keys:
            c["screening"] = [{"key": k, "name": FACTOR_INDEX.get(k, {}).get("name", k), "recommend": True, "reason": "冷启动：因子用于分档调整，系数采用先验", "source": "prior"} for k in keys]
            c["selected"] = keys
            continue
        res = P.screen(df, [k for k in keys if k in df.columns])
        for r in res:
            r["source"] = next((x["source"] for x in c["candidates"] if x["key"] == r["key"]), "structured")
        claims = int(df["claim_count"].sum())
        cap = max(1, claims // 40)
        recommended = sorted((r for r in res if r["recommend"]), key=lambda r: -r["iv"])
        if len(recommended) > cap:
            for r in recommended[cap:]:
                r["recommend"] = False
                r["reason"] += f"；样本出险 {claims} 次，为防止过拟合最多入模 {cap} 个因子"
            recommended = recommended[:cap]
        c["screening"] = res
        c["selected"] = [r["key"] for r in recommended] or [max(res, key=lambda r: r["iv"])["key"]]
        notes.append(f"{c['name']}：建议入模 {len(c['selected'])}/{len(res)}（{'、'.join(FACTOR_INDEX.get(k, {}).get('name', k) for k in c['selected'])}）")
    return ToolResult(summary="；".join(notes), data={c["id"]: c["screening"] for c in _comps(ctx)}, level=1)


def _fit_component(c: dict, df: pd.DataFrame | None, progress) -> dict:
    out: dict = {"methods": {}}
    params = c["params"]
    limit = float(params.get("limit") or 5e6)
    deductible = float(params.get("deductible") or 1e4)
    if df is not None and ("PM1" in c["methods"] or "PM2" in c["methods"]):
        factors = [f for f in c["selected"] if f in df.columns]
        structured_only = [f for f in factors if f not in c.get("text_factors", [])]
        glm = P.fit_glm(df, factors)
        progress(0.3, "广义线性模型拟合完成")
        rate_rows = P.glm_rate_table(glm)
        glm_freq, glm_sev = P.glm_predict(glm, df)
        pp_rate = float((glm_freq * df["exposure"] * glm_sev).sum() / (df["sum_insured"] * df["exposure"]).sum())
        lift = None
        if structured_only and len(structured_only) < len(factors):
            base = P.fit_glm(df, structured_only)
            lift = {"without_text": base.summary["gini"], "with_text": glm.summary["gini"], "improvement": round(glm.summary["gini"] - base.summary["gini"], 4)}
        out["methods"]["PM1"] = {"name": "广义线性模型乘法费率", "pure_rate": pp_rate, "summary": glm.summary, "rate_table": rate_rows, "factor_lift": lift, "_glm": {"freq": glm.freq_params, "sev": glm.sev_params, "levels": glm.levels, "edges": glm.edges, "factors": glm.factors}}
        if "PM2" in c["methods"]:
            directions = {f: FACTOR_INDEX.get(f, {}).get("direction", 0) for f in factors}
            gbm = P.fit_gbm(df, factors, directions)
            progress(0.55, "梯度提升树训练完成")
            pf = gbm.freq_model.predict(df[factors].values) * df["exposure"].values
            ps = gbm.sev_model.predict(df[factors].values)
            shap = P.shap_summary(gbm, df)
            progress(0.75, "Shapley 解释计算完成")
            out["methods"]["PM2"] = {"name": "梯度提升树 + Shapley 解释", "pure_rate": float((pf * ps).sum() / (df["sum_insured"] * df["exposure"]).sum()), "summary": gbm.summary, "shap": shap}
            out["_gbm"] = gbm
    if "PM3" in c["methods"]:
        bench_rate = P.benchmark_pure_premium(c["line"], 1.0)
        if df is not None:
            bs = P.buhlmann_straub(df)
            own_rate = float(df["claim_amount"].sum() / (df["sum_insured"] * df["exposure"]).sum())
            exposure = float(df["exposure"].sum())
            k_rate = bs["k"] / max(float(df["sum_insured"].mean()), 1)
            blend = P.credibility_blend(own_rate, exposure, bench_rate, max(bs["k"] / 1e6, 20.0))
            out["methods"]["PM3"] = {"name": "Bühlmann-Straub 信度加权", "pure_rate": blend["blended"] if blend["blended"] > 0 else own_rate, "summary": {**bs, "blend": blend, "k_rate": k_rate}}
        else:
            out["methods"]["PM3"] = {"name": "Bühlmann-Straub 信度加权", "pure_rate": bench_rate, "summary": {"blend": {"z": 0.0, "own": None, "benchmark": bench_rate, "blended": bench_rate}, "note": "无自有经验，信度为 0，完全采用行业基准"}}
        progress(0.85, "信度加权完成")
    if "PM4" in c["methods"]:
        rc = P.risk_cost_loading(c["scenarios"], limit, 0.0, c.get("expert_priors"))
        out["methods"]["PM4"] = {"name": "风险成本加成法", "pure_rate": rc["expected_loss"] / limit if limit else 0, "summary": rc}
    progress(1.0, "建模完成")
    return out


@registry.tool("fit_pricing_models", "建模定价（后台任务）", "按方法组合在后台训练广义线性模型、梯度提升树与 Shapley 解释、信度加权、风险成本加成", permission="T1", zone="private", max_level=2)
def fit_pricing_models(ctx, **_) -> ToolResult:
    comps = _comps(ctx)
    frames = {c["id"]: _frame(ctx, c) for c in comps}

    def job(progress):
        results = {}
        for i, c in enumerate(comps):
            r = _fit_component(c, frames[c["id"]], lambda f, m, i=i: progress((i + f) / len(comps), f"{c['name']}：{m}"))
            gbm = r.pop("_gbm", None)
            if gbm is not None:
                _MODELS[(ctx.root_run_id, c["id"])] = gbm
            results[c["id"]] = r
        return results

    task_id = background_manager.submit(ctx, "定价建模", job)
    results = background_manager.wait(task_id)
    notes = []
    for c in comps:
        r = results[c["id"]]
        c["models"] = r["methods"]
        parts = []
        for m, v in r["methods"].items():
            extra = f"，基尼 {v['summary']['gini']:.3f}" if isinstance(v.get("summary"), dict) and "gini" in v["summary"] else ""
            parts.append(f"{m} 纯风险费率 {v['pure_rate'] * 100:.3f}%{extra}")
        notes.append(f"{c['name']}：" + "；".join(parts))
    return ToolResult(summary="；".join(notes), data={c["id"]: {m: {k: v for k, v in mv.items() if k != "_glm"} for m, mv in c["models"].items()} for c in comps}, level=1, metrics={"background_task": task_id})


@registry.tool("compare_methods", "方法对比与合成", "对比各方法纯风险费率，差异超过 15% 时分析原因并给出合成权重", permission="T1", zone="cloud")
def compare_methods(ctx, **_) -> ToolResult:
    notes = []
    for c in _comps(ctx):
        results = {m: v["pure_rate"] for m, v in c["models"].items() if v.get("pure_rate")}
        default = {"PM1": 0.5, "PM2": 0.3, "PM3": 0.2} if c["start_mode"] == "warm" else {"PM4": 0.6, "PM3": 0.4, "PM1": 0.0}
        vals = list(results.values())
        spread = (max(vals) - min(vals)) / max(min(vals), 1e-12) if len(vals) > 1 else 0.0
        diff = model_gateway.invoke("ap.method_diff", {"line": c["name"], "results": results, "default_weights": default, "spread": spread}, ctx=ctx, data_level=1) if spread > 0.15 else {"analysis": f"方法间差异 {spread * 100:.1f}%，在 15% 以内，按默认权重合成", "weights": {m: default.get(m, 0) for m in results}}
        weights = {m: float(w) for m, w in diff["weights"].items() if m in results and float(w) > 0} or {m: 1 / len(results) for m in results}
        total = sum(weights.values())
        blended = sum(results[m] * w for m, w in weights.items()) / total
        c["comparison"] = {"results": results, "spread": round(spread, 4), "analysis": diff["analysis"], "weights": {m: round(w / total, 3) for m, w in weights.items()}, "blended_pure_rate": blended}
        notes.append(f"{c['name']}：差异 {spread * 100:.1f}%，合成纯风险费率 {blended * 100:.3f}%")
    return ToolResult(summary="；".join(notes), data={c["id"]: c["comparison"] for c in _comps(ctx)}, level=1)


def _severity_params(c: dict, df: pd.DataFrame | None) -> tuple[float, float]:
    limit = float(c["params"].get("limit") or 5e6)
    if df is not None and (df["claim_count"] > 0).sum() >= 30:
        sev = (df.loc[df["claim_count"] > 0, "claim_amount"] / df.loc[df["claim_count"] > 0, "claim_count"]).values
        sev = sev[sev > 0]
        # 经验数据的案均损失按合同限额与样本平均保额之比缩放，与情景模拟的组合口径一致
        scale = limit / float(df["sum_insured"].mean())
        return float(np.log(sev).mean() + math.log(scale)), float(max(0.4, np.log(sev).std()))
    return P.lognormal_params(limit * 0.15, 1.3)


@registry.tool("build_rate_table", "费率构成与费率表", "计算风险附加、费用与利润附加、毛费率、免赔与限额调整系数、报备浮动区间", permission="T1", zone="private")
def build_rate_table(ctx, **_) -> ToolResult:
    t = ctx.workspace["targets"]
    notes = []
    for c in _comps(ctx):
        df = _frame(ctx, c)
        ground_up = c["comparison"]["blended_pure_rate"]
        mu, sigma = _severity_params(c, df)
        limit = float(c["params"].get("limit") or 5e6)
        contract_ler = P.loss_elimination_ratio(mu, sigma, float(c["params"].get("deductible") or 0), limit)
        pure = ground_up * (1 - contract_ler)
        uncertainty = 0.0 if c["start_mode"] == "warm" else (0.3 if c["maturity"] >= 20 else 0.5)
        risk_load = pure * (0.15 + uncertainty)
        gross = P.gross_premium(pure, risk_load, t["expense_ratio"], t["profit_ratio"])
        ded_factors = []
        base_ded = float(c["params"].get("deductible") or 1e4)
        base_ler = P.loss_elimination_ratio(mu, sigma, base_ded, limit)
        for mult in (0.5, 1.0, 2.0, 3.0):
            d = base_ded * mult
            ler = P.loss_elimination_ratio(mu, sigma, d, limit)
            ded_factors.append({"deductible": round(d, -2), "ler": ler, "factor": round((1 - ler) / max(1e-9, 1 - base_ler), 3)})
        ilf = []
        full = P.limited_expected(mu, sigma, limit, 0)
        for mult in (0.5, 1.0, 1.5, 2.0):
            l = limit * mult
            ilf.append({"limit": round(l, -3), "factor": round(P.limited_expected(mu, sigma, l, 0) / max(full, 1e-9), 3)})
        band = ctx.workspace["filing_band"][c["id"]]
        c["rate"] = {
            "ground_up_pure_rate": ground_up,
            "contract_ler": contract_ler,
            "pure_rate": pure,
            "risk_loading_rate": risk_load,
            "uncertainty_loading": uncertainty,
            "gross_rate": gross,
            "band": [gross * (1 - band), gross * (1 + band)],
            "deductible_factors": ded_factors,
            "ilf": ilf,
            "relativities": (c["models"].get("PM1") or {}).get("rate_table", []),
            "severity": {"mu": mu, "sigma": sigma},
            "premium_at_limit": round(gross * limit, 2),
        }
        notes.append(f"{c['name']}：毛费率 {gross * 100:.3f}%（浮动区间 ±{band:.0%}），按限额 {format_amount(limit)} 计保费约 {format_amount(gross * limit)}")
    return ToolResult(summary="；".join(notes), data={c["id"]: c["rate"] for c in _comps(ctx)}, level=1)


def _portfolio(ctx, c: dict, size: int = 300) -> dict:
    df = _frame(ctx, c)
    limit = float(c["params"].get("limit") or 5e6)
    rng = np.random.default_rng(17)
    if df is not None and "PM1" in c["models"]:
        g = c["models"]["PM1"]["_glm"]
        res = P.GLMResult(g["freq"], g["sev"], g["levels"], g["edges"], g["factors"], {})
        sample = df.iloc[rng.choice(len(df), size=min(size, len(df)), replace=False)].copy()
        freq, sev = P.glm_predict(res, sample)
        scale = limit / float(sample["sum_insured"].mean())
        si = sample["sum_insured"].values * scale
        lam = freq
        sev_mean = sev * scale
        rel = freq * sev / float((freq * sev).mean())
    else:
        n = size
        si = np.full(n, limit)
        el = c["comparison"]["blended_pure_rate"] * limit
        freq0 = sum(P.LIKELIHOOD_FREQ[int(s.get("likelihood", 3))] for s in c["scenarios"]) or 0.08
        lam = rng.gamma(4, freq0 / 4, size=n)
        sev_mean = np.full(n, el / max(freq0, 1e-6))
        rel = lam / lam.mean()
    return {"lam": lam, "sev_mean": sev_mean, "si": si, "rel": rel}


def _premiums(c: dict, port: dict, rate_mult: float = 1.0) -> np.ndarray:
    return c["rate"]["gross_rate"] * rate_mult * port["si"] * port["rel"]


@registry.tool("run_scenarios", "情景模拟（后台任务）", "基准、经济下行、技术迭代加速、政策收紧、系统性事件五个情景各 10,000 次蒙特卡洛模拟", permission="T1", zone="private")
def run_scenarios(ctx, n_sims: str = "10000", **_) -> ToolResult:
    t = ctx.workspace["targets"]
    sims = max(10000, int(n_sims))
    comps = _comps(ctx)
    ports = {c["id"]: _portfolio(ctx, c) for c in comps}

    def job(progress):
        out = {}
        total = len(comps) * len(P.DEFAULT_SCENARIOS)
        k = 0
        for c in comps:
            port = ports[c["id"]]
            res = []
            for sc in P.DEFAULT_SCENARIOS:
                r = P.simulate(port["lam"], port["sev_mean"], deductible=float(c["params"].get("deductible") or 1e4), limit=float(c["params"].get("limit") or 5e6), premium=_premiums(c, port), scenario=sc, n_sims=sims, expense_ratio=t["expense_ratio"], seed=1000 + k)
                r.pop("_profit", None)
                res.append(r)
                k += 1
                progress(k / total, f"{c['name']}·{sc['name']}")
            out[c["id"]] = res
        return out

    task_id = background_manager.submit(ctx, "情景模拟", job)
    results = background_manager.wait(task_id)
    notes = []
    for c in comps:
        c["scenarios_result"] = results[c["id"]]
        base = results[c["id"]][0]
        cap = base["capital"]
        cap_load = t["capital_cost"] * cap / max(base["expected_income"], 1e-9)
        c["rate"]["capital_load_ratio"] = round(cap_load, 4)
        worst = max(results[c["id"]], key=lambda r: r["loss_ratio"]["mean"])
        notes.append(f"{c['name']}：基准赔付率 {base['loss_ratio']['mean']:.1%}、综合成本率 {base['combined_ratio']:.1%}、亏损概率 {base['loss_probability']:.1%}；最不利情景「{worst['name']}」赔付率 {worst['loss_ratio']['mean']:.1%}")
    return ToolResult(summary="；".join(notes), data={c["id"]: c["scenarios_result"] for c in comps}, level=1, metrics={"simulations": sims * len(P.DEFAULT_SCENARIOS) * len(comps)})


def _variants(c: dict, base: dict, rnd: int) -> list[dict]:
    step = {1: 1.0, 2: 0.5, 3: 0.25}.get(rnd, 0.25)
    out = [{"key": "A", "label": "对照组（当前方案）", "rate_mult": base["rate_mult"], "ded_mult": base["ded_mult"], "clause_changes": []}]
    out.append({"key": "B", "label": f"免赔额提高至 {base['ded_mult'] * (1 + step):.2f} 倍、费率下调 {6 * step:.0f}%", "rate_mult": base["rate_mult"] * (1 - 0.06 * step), "ded_mult": base["ded_mult"] * (1 + step), "clause_changes": [{"type": "deductible", "line": c["line"], "doc_id": c["doc_id"]}]})
    out.append({"key": "C", "label": f"费率下调 {8 * step:.0f}%", "rate_mult": base["rate_mult"] * (1 - 0.08 * step), "ded_mult": base["ded_mult"], "clause_changes": []})
    out.append({"key": "D", "label": f"费率上调 {5 * step:.0f}%", "rate_mult": base["rate_mult"] * (1 + 0.05 * step), "ded_mult": base["ded_mult"], "clause_changes": []})
    return out


@registry.tool("ab_experiment_round", "A/B 实验（一轮）", "生成条款与费率变体，在基准情景下以共同随机数做配对模拟，结合投保需求模型做统计决策并检查约束", permission="T1", zone="private", params={"round": "轮次"})
def ab_experiment_round(ctx, **kwargs) -> ToolResult:
    rnd = int(kwargs.get("round") or 1)
    t = ctx.workspace["targets"]
    exps = ctx.workspace.setdefault("experiments", [])
    sims = 4000
    notes = []
    for c in _comps(ctx):
        port = _portfolio(ctx, c)
        state = c.setdefault("experiment_state", {"rate_mult": 1.0, "ded_mult": 1.0})
        variants = _variants(c, state, rnd)
        base_ded = float(c["params"].get("deductible") or 1e4)
        limit = float(c["params"].get("limit") or 5e6)
        mu, sigma = c["rate"]["severity"]["mu"], c["rate"]["severity"]["sigma"]
        ref_paid = P.limited_expected(mu, sigma, limit, base_ded)
        profits = {}
        for v in variants:
            ded = base_ded * v["ded_mult"]
            paid = P.limited_expected(mu, sigma, limit, ded)
            coverage = paid / max(ref_paid, 1e-9)
            take = P.demand_rate(v["rate_mult"] * coverage, coverage)
            # 免赔额变化时，费率按损失消除率同步折减（与费率表口径一致）
            ded_rate_adj = coverage
            r = P.simulate(port["lam"], port["sev_mean"], deductible=ded, limit=limit, premium=_premiums(c, port, v["rate_mult"] * ded_rate_adj), scenario=P.DEFAULT_SCENARIOS[0], n_sims=sims, expense_ratio=t["expense_ratio"], seed=7000 + rnd, demand_base=take)
            v["premium_multiplier"] = round(v["rate_mult"] * ded_rate_adj, 4)
            profits[v["key"]] = r.pop("_profit")
            band = ctx.workspace["filing_band"][c["id"]]
            v["metrics"] = {"take_rate": round(take, 4), "coverage_ratio": round(coverage, 4), "loss_ratio": r["loss_ratio"]["mean"], "loss_probability": r["loss_probability"], "expected_profit": round(float(profits[v["key"]].mean()), 2), "income": r["expected_income"]}
            v["constraints"] = {
                "赔付率在目标区间": t["loss_ratio"][0] <= r["loss_ratio"]["mean"] <= t["loss_ratio"][1] or v["key"] == "A",
                "亏损概率不超上限": r["loss_probability"] <= t["max_loss_prob"],
                "保障充分度": coverage >= t["min_coverage"],
                "费率在报备区间": abs(v["rate_mult"] - 1) <= band,
            }
        for v in variants[1:]:
            v["stats"] = P.paired_better(profits["A"], profits[v["key"]])
        eligible = [v for v in variants[1:] if v["stats"]["prob_better"] >= 0.95 and all(v["constraints"].values())]
        winner = max(eligible, key=lambda v: v["stats"]["mean_diff"]) if eligible else None
        insight = model_gateway.invoke("ap.experiment_insight", {"round": rnd, "max_rounds": int(ctx.inputs.get("experiment", {}).get("rounds", 3) if isinstance(ctx.inputs.get("experiment"), dict) else 3), "variants": [{k: v[k] for k in ("key", "label", "metrics", "constraints", "clause_changes") if k in v} | {"stats": v.get("stats", {})} for v in variants], "winner": winner["key"] if winner else None}, ctx=ctx, data_level=1)
        if winner:
            state["rate_mult"], state["ded_mult"] = winner["rate_mult"], winner["ded_mult"]
            if winner["clause_changes"]:
                pend = ctx.workspace.setdefault("pending_revision", [])
                pend.append({"type": "deductible", "doc_id": c["doc_id"], "line": c["line"], "value": round(base_ded * winner["ded_mult"], -2), "reason": f"第 {rnd} 轮 A/B 实验胜出：{winner['label']}"})
        exps.append({"round": rnd, "component": c["id"], "name": c["name"], "variants": variants, "winner": winner["key"] if winner else None, "insight": insight, "method": "离线配对模拟（共同随机数）", "sims": sims})
        notes.append(f"{c['name']}第 {rnd} 轮：{insight['conclusion']}")
    return ToolResult(summary="；".join(notes), data={"round": rnd}, level=1, metrics={"round": rnd})


@registry.tool("coordinate_clause_revision", "条款修订联动", "胜出变体涉及条款修改时，向条款设计发起修订请求（需设计人员确认）", permission="T1", zone="platform")
def coordinate_clause_revision(ctx, **_) -> ToolResult:
    pending = ctx.workspace.get("pending_revision") or []
    state = ctx.workspace.setdefault("revision", {})
    if not pending or state.get("status") in ("requested", "in_progress", "done", "rejected"):
        return ToolResult(summary="本阶段无需修改条款" if not pending else f"修订请求已处于 {state.get('status')} 状态", level=0)
    latest: dict[str, dict] = {}
    for p in pending:
        latest[(p["doc_id"], p["line"])] = p
    changes = list(latest.values())
    request_no = f"RV-{ctx.run_id}-{len(changes)}"
    state.update({"request_no": request_no, "changes": changes, "status": "requested"})
    from ...services import pipeline

    if not ctx.eval_mode:
        pipeline.add_revision(ctx.business_ref.get("project_id"), {"request_no": request_no, "from": "AG-AP", "changes": changes, "status": "pending_confirm", "status_label": "待设计人员确认", "run_id": ctx.run_id})
    card_id = hitl.create_card(
        ctx,
        {
            "card_type": "decision",
            "title": f"条款修订请求 {request_no}",
            "reason": "A/B 实验胜出变体需要修改条款：" + "；".join(f"{c['line']} 免赔额调整为 {format_amount(c['value'])}（{c['reason']}）" for c in changes),
            "impact": "确认后由条款设计智能体修订并重新合规校验，精算定价在收到修订版本后继续",
            "context": {"request_no": request_no, "changes": changes, "todo_id": ctx.todos.current().id if ctx.todos.current() else None, "revision": True},
            "options": [{"key": "approve", "label": "接受并发起条款修订", "primary": True}, {"key": "skip", "label": "驳回，按原条款继续", "requires_comment": True}],
            "assignee_role": "designer",
            "deadline_hours": 48,
        },
    )
    state["card_id"] = card_id
    return ToolResult(summary=f"已发起条款修订请求 {request_no}，等待设计人员确认", data={"waiting_for": {"card_id": card_id, "card_type": "decision", "request_no": request_no}}, control="suspend")


@registry.tool("await_clause_revision", "等待条款修订完成", "等待条款设计智能体完成修订并回复，收到新版本后更新定价参数", permission="T0", zone="platform")
def await_clause_revision(ctx, **_) -> ToolResult:
    state = ctx.workspace.get("revision") or {}
    if state.get("status") in (None, "rejected"):
        return ToolResult(summary="无进行中的条款修订" if not state else "修订请求被驳回，按原条款继续", level=0)
    if state.get("status") == "done":
        return ToolResult(summary=f"修订 {state['request_no']} 已完成", level=0)
    reply = next((m for m in ctx.workspace.get("mail", []) if m["kind"] == "revision_reply" and m.get("request_no") == state.get("request_no")), None)
    if reply is None:
        return ToolResult(summary=f"等待条款设计完成修订 {state.get('request_no')}", data={"waiting_for": {"card_type": "agent", "request_no": state.get("request_no"), "agent": "AG-CD"}}, control="suspend")
    payload = reply["payload"]
    ctx.workspace["clause_ref"] = {"artifact_key": payload["artifact_key"], "version": payload["version"]}
    for ch in state.get("changes", []):
        for c in _comps(ctx):
            if c["doc_id"] == ch["doc_id"] and c["line"] == ch["line"]:
                c["params"]["deductible"] = ch["value"]
                c.setdefault("experiment_state", {})["ded_mult"] = 1.0
    state.update({"status": "done", "new_version": payload["version"], "applied": payload.get("applied", [])})
    return ToolResult(summary=f"收到条款修订回复：条款方案 V{payload['version']}，" + "；".join(payload.get("applied", [])), level=1)


@registry.tool("finalize_rates", "应用实验结论", "把 A/B 实验胜出的费率调整与条款修订后的免赔额应用到费率表", permission="T1", zone="private")
def finalize_rates(ctx, **_) -> ToolResult:
    notes = []
    for c in _comps(ctx):
        state = c.get("experiment_state") or {}
        mult = float(state.get("rate_mult", 1.0))
        if "base_gross_rate" not in c["rate"]:
            c["rate"]["base_gross_rate"] = c["rate"]["gross_rate"]
        c["rate"]["gross_rate"] = c["rate"]["base_gross_rate"] * mult
        c["rate"]["experiment_multiplier"] = round(mult, 4)
        notes.append(f"{c['name']}：费率乘数 {mult:.3f}，最终毛费率 {c['rate']['gross_rate'] * 100:.3f}%，免赔额 {format_amount(float(c['params'].get('deductible') or 0))}")
    return ToolResult(summary="；".join(notes), level=1)


def enterprise_factor_values(profile: dict, risks: list[dict]) -> dict:
    """把企业画像与风险评估结论映射为定价因子取值（与经验数据口径一致）。"""
    f = profile.get("financials", {})
    eq = profile.get("equipment", [])
    team = profile.get("team", {})
    sup = profile.get("suppliers", [])
    sec = profile.get("security", {})
    total = sum(e["value"] for e in eq) or 1
    from datetime import date

    ages = [max(0.3, (date(2026, 11, 1) - date.fromisoformat(e["purchase_date"])).days / 365) for e in eq]
    neg = min(1.0, 0.1 + 0.15 * sum(1 for r in risks if r.get("level") in ("高", "极高")))
    maint = 0.45 if any(r["subtype"] == "R02-2" and r.get("level") in ("高", "极高") for r in risks) else 0.8
    return {
        "equipment_age": round(sum(ages) / len(ages), 2) if ages else 4.0,
        "equipment_concentration": round(max((e["value"] for e in eq), default=0) / total, 3),
        "industry_risk": 4 if any(r["type_code"] in ("R05", "R08") and r.get("level") == "极高" for r in risks) else 3,
        "security_level": sec.get("level", 2),
        "data_scale": sec.get("data_scale", 2),
        "security_incidents": sec.get("incidents", 0),
        "quality_cert": 1,
        "log_revenue": math.log(max(f.get("revenue", 1e8), 1e6) / 1e8),
        "rd_intensity": round(f.get("rd_expense", 0) / max(f.get("revenue", 1), 1), 3),
        "cash_months": round(f.get("cash_reserve", 0) / max(f.get("monthly_fixed_cost", 1), 1), 1),
        "team_stability": round(1 - team.get("left_last_year", 0) / max(team.get("core_size", 1), 1), 3),
        "supplier_concentration": max((s["share"] for s in sup), default=0.3),
        "maintenance_score": maint,
        "negative_sentiment": neg,
        "trl": min((p["trl"] for p in profile.get("products", [])), default=6),
    }


@registry.tool("quote_enterprise", "企业报价与贡献分解", "按产品费率表与企业因子取值计算企业报价，并用 Shapley 值分解各因子对频率的贡献", permission="T1", zone="private", max_level=2)
def quote_enterprise(ctx, **_) -> ToolResult:
    ent = ctx.workspace.get("enterprise") or {}
    if not ent.get("id"):
        return ToolResult(summary="赛道通用产品，无需单一企业报价", level=0)
    from ...db import session_scope
    from ...models import Enterprise

    with session_scope() as s:
        profile = (s.get(Enterprise, ent["id"]).profile or {})
    report = load_artifact(ctx.workspace["report_ref"]["artifact_key"], ctx.workspace["report_ref"]["version"])["data"] if ctx.workspace.get("report_ref") else {"risks": []}
    values = enterprise_factor_values(profile, report.get("risks", []))
    notes = []
    for c in _comps(ctx):
        rel = 1.0
        detail = []
        if "PM1" in c["models"]:
            g = c["models"]["PM1"]["_glm"]
            res = P.GLMResult(g["freq"], g["sev"], g["levels"], g["edges"], g["factors"], {})
            row = pd.DataFrame([{f: values.get(f, 0.0) for f in g["factors"]}])
            freq, sev = P.glm_predict(res, row)
            base = math.exp(g["freq"]["截距"]) * math.exp(g["sev"]["截距"])
            z = float(c["models"]["PM1"]["summary"].get("credibility_z", 1.0))
            rel = float((freq[0] * sev[0] / base) ** z)
            detail = [{"factor": f, "name": FACTOR_INDEX.get(f, {}).get("name", f), "value": values.get(f)} for f in g["factors"]]
        gbm = _MODELS.get((ctx.root_run_id, c["id"]))
        shap = P.explain_row(gbm, values, _frame(ctx, c)) if gbm is not None and _frame(ctx, c) is not None else None
        limit = float(c["params"].get("limit") or 5e6)
        premium = c["rate"]["gross_rate"] * rel * limit
        lo, hi = c["rate"]["band"]
        c["quote"] = {"factor_values": detail, "relativity": round(rel, 3), "premium": round(premium, 2), "premium_range": [round(lo * rel * limit, 2), round(hi * rel * limit, 2)], "shap": shap}
        notes.append(f"{c['name']}：企业相对系数 {rel:.2f}，报价 {format_amount(premium)}")
    ctx.workspace["enterprise_factor_values"] = values
    return ToolResult(summary="；".join(notes), data={c["id"]: c.get("quote") for c in _comps(ctx)}, level=2)


@registry.tool("compose_actuarial_report", "生成精算报告", "汇总定价目标、数据、因子、方法与结果、情景、实验、费率表与假设局限", permission="T1", zone="cloud")
def compose_actuarial_report(ctx, **_) -> ToolResult:
    t = ctx.workspace["targets"]
    comps = _comps(ctx)
    base = [c["scenarios_result"][0] for c in comps if c.get("scenarios_result")]
    combined = float(np.mean([b["combined_ratio"] for b in base])) if base else 0.0
    limitations = [f"{c['name']}为冷启动定价，自有经验权重为 0，建议上线后按季度信度更新" for c in comps if c["start_mode"] == "cold"]
    limitations += ["需求响应模型的价格弹性为假设参数，线上分组试点后应校准", "情景参数为专家设定，极端系统性事件的频率存在不确定性"]
    adequacy = "；".join(f"{c['name']}基准情景综合成本率 {c['scenarios_result'][0]['combined_ratio']:.1%}（上限 {t['combined_cap']:.0%}），毛费率与行业基准费率之比 {c['rate']['gross_rate'] / max(LINES.get(c['line'], {}).get('benchmark_rate', c['rate']['gross_rate']), 1e-9):.2f}" for c in comps if c.get("scenarios_result") and c["rate"].get("gross_rate"))
    payload = {
        "targets": t,
        "components": [{"name": c["name"], "data_note": c.get("data_note"), "factors": c.get("selected", []), "text_factors": len([f for f in c.get("selected", []) if f in c.get("text_factors", [])]), "methods": c["methods"], "gross_rate": c["rate"]["gross_rate"], "band": c["rate"]["band"]} for c in comps],
        "scenarios": [s for c in comps for s in (c.get("scenarios_result") or [])[:5]],
        "experiments": ctx.workspace.get("experiments", []),
        "adequacy": adequacy,
        "limitations": limitations,
        "combined": combined,
    }
    from ...privacy.narrative import finalize_narrative

    out = finalize_narrative(model_gateway.invoke("ap.report", payload, ctx=ctx, data_level=1), ctx.vault)
    ctx.workspace["actuarial_report"] = {**out, "combined": combined}
    return ToolResult(summary=out["conclusion"], data=out, level=1)


@registry.tool("read_need_pricing_rules", "读取产品规则", "从已冻结条款读取保障责任、计费保额和保险期间", permission="T0", zone="platform")
def read_need_pricing_rules(ctx, **_) -> ToolResult:
    from ...productdev.flow import rules_from_clause
    from ...productdev.rates import RateError

    ref = ctx.inputs.get("clause_plan") or {}
    if not ref.get("artifact_key"):
        raise ToolError("没有已冻结的条款方案", retryable=False)
    clause = load_artifact(ref["artifact_key"], ref.get("version"))
    try:
        rules = rules_from_clause(clause["data"])
    except RateError as exc:
        raise ToolError(str(exc), retryable=False) from exc
    ctx.workspace["clause_ref"] = {"artifact_key": ref["artifact_key"], "version": clause["version"]}
    ctx.workspace["need_rules"] = rules
    return ToolResult(summary=f"读取产品规则：计费保额 {rules.get('billing_amount')}，期间 {rules.get('period')}", data={"billing_amount": rules.get("billing_amount")}, level=1)


@registry.tool("calculate_weighted_score", "计算创新积分制评分", "按已维护指标、权重和得分映射计算建议费率", permission="T1", zone="platform")
def calculate_weighted_score(ctx, **_) -> ToolResult:
    from ...productdev import parameters
    from ...productdev.rates import weighted_score_method

    rules = ctx.workspace.get("need_rules")
    if not rules:
        raise ToolError("尚未读取产品规则", retryable=False)
    result = weighted_score_method(parameters=parameters.score_pricing())
    ctx.workspace["weighted_result"] = result
    return ToolResult(summary=f"创新积分制评分：假设得分 {result['example_score']}，纯风险费率 {result['pure_loss_ratio']}", data={"example_score": str(result["example_score"]), "pure_loss_ratio": str(result["pure_loss_ratio"])}, level=1)


@registry.tool("calculate_classification_rate", "计算分类费率", "按基准条件和费率因子计算建议费率", permission="T1", zone="platform")
def calculate_classification_rate(ctx, **_) -> ToolResult:
    from ...productdev import parameters
    from ...productdev.rates import classification_method

    assumptions = parameters.classification_assumptions()
    usable, rejected = parameters.experience_rows()
    result = classification_method(
        factors=list(assumptions["factors"]),
        benchmark_pure=assumptions["benchmark_pure"],
        benchmark_condition=assumptions["benchmark_condition"],
        samples=usable + rejected,
        assumptions=assumptions,
    )
    if any(factor.get("risk_key") in ("score", "innovation_score", "maturity") or "综合评分" in str(factor.get("name")) for factor in result["factors"]):
        raise ToolError("分类费率因子包含综合评分指标", retryable=False)
    ctx.workspace["classification_result"] = result
    return ToolResult(summary=f"分类费率：纯风险费率 {result['pure_loss_ratio']}，因子 {len(result['factors'])} 个", data={"pure_loss_ratio": str(result["pure_loss_ratio"]), "factor_count": len(result["factors"])}, level=1)


@registry.tool("publish_need_pricing", "比较并生成报告", "核对两种数字并写出比较和精算报告，尚未选定毛费率", permission="T1", zone="platform")
def publish_need_pricing(ctx, **_) -> ToolResult:
    from ...productdev import parameters
    from ...productdev.flow import build_document_pricing
    from ...productdev.rates import RateError

    rules = ctx.workspace.get("need_rules")
    if not rules or not ctx.workspace.get("weighted_result") or not ctx.workspace.get("classification_result"):
        raise ToolError("两种方法尚未算完", retryable=False)
    try:
        priced = build_document_pricing(rules)
    except (RateError, parameters.ParameterError) as exc:
        raise ToolError(str(exc), retryable=False) from exc
    ctx.workspace["need_schemes"] = priced["schemes"]
    ctx.workspace["need_comparison"] = priced["comparison"]
    ctx.workspace["need_sensitivity"] = priced["sensitivity"]
    ctx.workspace["need_report"] = priced["actuarial_report"]
    ctx.workspace["pricing_published"] = True
    return ToolResult(summary="两种费率已并列算出，尚未选定", data={"methods": [scheme["method"] for scheme in priced["schemes"].values()]}, level=1)
