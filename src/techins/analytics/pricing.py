"""精算定价算法库：文本因子解析、因子筛选、广义线性模型、梯度提升树与 Shapley 解释、信度理论、风险成本加成、
费率构成、蒙特卡洛情景模拟与 A/B 实验统计。"""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

from ..config import settings
from ..knowledge.catalog import FACTOR_DICTIONARY, LINES

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", module="statsmodels")

FACTOR_INDEX = {f["key"]: f for f in FACTOR_DICTIONARY}
_PROFILE_STRUCTURED = {
    "equipment": ["equipment_age", "equipment_concentration", "industry_risk"],
    "cyber": ["security_level", "data_scale", "industry_risk"],
    "liability": ["quality_cert", "industry_risk", "log_revenue"],
    "rd_cost": ["rd_intensity", "cash_months", "industry_risk"],
}
_PROFILE_TEXT = {
    "equipment": ["maintenance_score", "negative_sentiment", "supplier_concentration"],
    "cyber": ["security_incidents", "negative_sentiment", "team_stability"],
    "liability": ["negative_sentiment", "team_stability"],
    "rd_cost": ["team_stability", "supplier_concentration", "trl", "negative_sentiment"],
}
STRUCTURED_COLUMNS = {code: _PROFILE_STRUCTURED[spec["experience"]] for code, spec in LINES.items() if spec.get("experience") in _PROFILE_STRUCTURED}
TEXT_FACTORS = ["team_stability", "supplier_concentration", "maintenance_score", "negative_sentiment", "security_incidents", "trl"]
TEXT_FACTOR_LINES = {code: _PROFILE_TEXT[spec["experience"]] for code, spec in LINES.items() if spec.get("experience") in _PROFILE_TEXT}


def load_experience(line: str) -> pd.DataFrame | None:
    path = settings.datasets_dir / f"experience_{line}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if "revenue" in df.columns:
        df["log_revenue"] = np.log(df["revenue"] / 1e8)
    return df


# ---------------------------------------------------------------- 文本因子解析（大模型抽取的确定性复核器）
_MAINT = [("严格按计划", 0.95), ("维保记录完整", 0.92), ("偶有推迟", 0.72), ("延期约一个月", 0.68), ("推迟两个月", 0.45), ("多次延期", 0.38)]
_SENT = [("无负面舆情", 0.08), ("舆情平稳", 0.1), ("一则质量投诉", 0.35), ("一则经营调整", 0.32), ("多起负面报道", 0.72), ("负面舆情集中", 0.78)]


def parse_text_factors(text: str) -> dict:
    out: dict[str, tuple[float | None, float]] = {}
    m = re.search(r"离职(\d+)人，核心团队(\d+)人", text)
    out["team_stability"] = (round(1 - int(m.group(1)) / max(1, int(m.group(2))), 3), 0.95) if m else (None, 0.0)
    m = re.search(r"第一大供应商采购占比(\d+)%", text)
    out["supplier_concentration"] = (int(m.group(1)) / 100, 0.95) if m else (None, 0.0)
    hit = next(((v, 0.85) for k, v in _MAINT if k in text), (None, 0.0))
    out["maintenance_score"] = hit
    hit = next(((v, 0.8) for k, v in _SENT if k in text), (None, 0.0))
    out["negative_sentiment"] = hit
    m = re.search(r"安全事件(\d+)起", text)
    out["security_incidents"] = (float(m.group(1)), 0.95) if m else (None, 0.0)
    m = re.search(r"TRL(\d)", text)
    out["trl"] = (float(m.group(1)), 0.9) if m else (None, 0.0)
    return out


def apply_text_factors(df: pd.DataFrame, keys: list[str], low_conf: float = 0.6) -> tuple[pd.DataFrame, dict]:
    parsed = df["due_diligence_summary"].map(parse_text_factors)
    stats_out = {}
    for k in keys:
        vals = parsed.map(lambda d: d[k][0])
        conf = parsed.map(lambda d: d[k][1])
        vals = vals.where(conf >= low_conf)
        stats_out[k] = {"coverage": float(vals.notna().mean()), "mean_conf": float(conf.mean()), "unknown": int(vals.isna().sum())}
        df[k] = vals.fillna(vals.median() if vals.notna().any() else 0.0)
    return df, stats_out


# ---------------------------------------------------------------- 因子筛选
def information_value(x: pd.Series, y: pd.Series, bins: int = 5) -> float:
    try:
        q = pd.qcut(x.rank(method="first"), bins, labels=False, duplicates="drop")
    except ValueError:
        return 0.0
    g = pd.DataFrame({"b": q, "y": (y > 0).astype(int)})
    tab = g.groupby("b")["y"].agg(["sum", "count"])
    tab["non"] = tab["count"] - tab["sum"]
    ev = (tab["sum"] + 0.5) / (tab["sum"].sum() + 0.5)
    ne = (tab["non"] + 0.5) / (tab["non"].sum() + 0.5)
    return float(((ev - ne) * np.log(ev / ne)).sum())


def monotonic_trend(x: pd.Series, freq: pd.Series, bins: int = 5) -> float:
    q = pd.qcut(x.rank(method="first"), bins, labels=False, duplicates="drop")
    means = freq.groupby(q).mean()
    if len(means) < 2:
        return 0.0
    rho = stats.spearmanr(range(len(means)), means.values).statistic
    return float(0.0 if np.isnan(rho) else rho)


def vif(df: pd.DataFrame, cols: list[str]) -> dict[str, float]:
    out = {}
    X = df[cols].astype(float).values
    for i, c in enumerate(cols):
        y = X[:, i]
        others = np.delete(X, i, axis=1)
        if others.shape[1] == 0:
            out[c] = 1.0
            continue
        A = np.column_stack([np.ones(len(y)), others])
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        resid = y - A @ coef
        r2 = 1 - resid.var() / y.var() if y.var() > 0 else 0
        out[c] = float(1 / max(1e-6, 1 - r2))
    return out


def psi(x: pd.Series, period: pd.Series, bins: int = 5) -> float:
    if period.nunique() < 2:
        return 0.0
    early = x[period <= period.median()]
    late = x[period > period.median()]
    if len(early) < 20 or len(late) < 20:
        return 0.0
    edges = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    e = np.histogram(early, edges)[0] / len(early) + 1e-4
    l = np.histogram(late, edges)[0] / len(late) + 1e-4
    return float(((l - e) * np.log(l / e)).sum())


def screen(df: pd.DataFrame, factors: list[str]) -> list[dict]:
    freq = df["claim_count"] / df["exposure"]
    v = vif(df, factors) if len(factors) > 1 else {factors[0]: 1.0}
    out = []
    for f in factors:
        meta = FACTOR_INDEX.get(f, {"name": f, "direction": 0, "source": "structured"})
        iv = information_value(df[f], df["claim_count"])
        trend = monotonic_trend(df[f], freq)
        p = psi(df[f], df["year"])
        expected = meta.get("direction", 0)
        direction_ok = expected == 0 or np.sign(trend) == np.sign(expected) or abs(trend) < 0.3
        compliant = f not in ("region",)
        recommend = iv >= 0.02 and v.get(f, 1) < 10 and p < 0.25 and compliant
        out.append(
            {
                "key": f,
                "name": meta.get("name", f),
                "source": meta.get("source", "structured"),
                "iv": round(iv, 4),
                "trend": round(trend, 3),
                "direction_expected": expected,
                "direction_ok": bool(direction_ok),
                "vif": round(v.get(f, 1.0), 2),
                "psi": round(p, 4),
                "compliant": compliant,
                "recommend": bool(recommend),
                "reason": "；".join(x for x in [f"信息价值 {iv:.3f}{'（偏低）' if iv < 0.02 else ''}", f"方差膨胀因子 {v.get(f, 1):.1f}{'（共线）' if v.get(f, 1) >= 10 else ''}", f"群体稳定性 {p:.3f}{'（不稳定）' if p >= 0.25 else ''}"] if x),
            }
        )
    return out


# ---------------------------------------------------------------- 广义线性模型
def _bins(df: pd.DataFrame, factors: list[str], edges: dict | None = None) -> tuple[pd.DataFrame, dict]:
    out = pd.DataFrame(index=df.index)
    edges = dict(edges or {})
    for f in factors:
        if f not in edges:
            uniq = df[f].nunique()
            if uniq <= 5:
                edges[f] = sorted(df[f].unique().tolist())
            else:
                qs = np.unique(np.quantile(df[f], [0, 0.2, 0.4, 0.6, 0.8, 1.0]))
                edges[f] = qs.tolist()
        e = edges[f]
        if len(e) <= 5 and all(float(x).is_integer() for x in e) and df[f].nunique() <= 5:
            out[f] = df[f].map(lambda v, e=e: str(int(v)) if v in e else str(int(min(e, key=lambda t: abs(t - v)))))
        else:
            cut = pd.cut(df[f], bins=[-np.inf] + list(e[1:-1]) + [np.inf], labels=False)
            out[f] = cut.astype(int).astype(str)
    return out, edges


def _bin_labels(edges: list[float], discrete: bool) -> list[str]:
    if discrete:
        return [str(int(x)) for x in edges]
    bounds = [-np.inf] + list(edges[1:-1]) + [np.inf]
    labels = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        labels.append(("≤" + f"{hi:.3g}") if lo == -np.inf else (f">{lo:.3g}" if hi == np.inf else f"{lo:.3g}–{hi:.3g}"))
    return labels


def _design(binned: pd.DataFrame, factors: list[str], levels: dict | None = None) -> tuple[np.ndarray, list[str], dict]:
    cols, mats = ["截距"], [np.ones(len(binned))]
    levels = dict(levels or {})
    for f in factors:
        if f not in levels:
            counts = binned[f].value_counts()
            base = counts.idxmax()
            levels[f] = {"base": base, "others": sorted(l for l in counts.index if l != base)}
        for lvl in levels[f]["others"]:
            cols.append(f"{f}={lvl}")
            mats.append((binned[f] == lvl).astype(float).values)
    return np.column_stack(mats), cols, levels


def gini(actual: np.ndarray, pred: np.ndarray, weight: np.ndarray | None = None) -> float:
    weight = np.ones_like(actual) if weight is None else weight
    order = np.argsort(pred)
    a, w = actual[order], weight[order]
    cum_w = np.cumsum(w) / w.sum()
    cum_a = np.cumsum(a) / max(a.sum(), 1e-9)
    g = 1 - 2 * np.trapezoid(cum_a, cum_w)
    order_best = np.argsort(actual / np.maximum(weight, 1e-9))
    a2, w2 = actual[order_best], weight[order_best]
    g_best = 1 - 2 * np.trapezoid(np.cumsum(a2) / max(a2.sum(), 1e-9), np.cumsum(w2) / w2.sum())
    return float(g / g_best) if g_best else 0.0


def calibration(actual: np.ndarray, pred: np.ndarray, deciles: int = 10) -> list[dict]:
    order = np.argsort(pred)
    groups = np.array_split(order, deciles)
    out = []
    for i, g in enumerate(groups, start=1):
        ae = actual[g].sum() / max(pred[g].sum(), 1e-9)
        out.append({"decile": i, "actual": round(float(actual[g].sum()), 2), "expected": round(float(pred[g].sum()), 2), "ratio": round(float(ae), 3)})
    return out


@dataclass
class GLMResult:
    freq_params: dict
    sev_params: dict
    levels: dict
    edges: dict
    factors: list[str]
    summary: dict


def split(df: pd.DataFrame, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    mask = rng.random(len(df)) < 0.75
    return df[mask].copy(), df[~mask].copy()


def fit_glm(df: pd.DataFrame, factors: list[str]) -> GLMResult:
    train, test = split(df)
    binned, edges = _bins(train, factors)
    X, cols, levels = _design(binned, factors)
    freq_model = sm.GLM(train["claim_count"].values, X, family=sm.families.Poisson(), offset=np.log(train["exposure"].values)).fit()
    sev_rows = train["claim_count"] > 0
    sev = (train.loc[sev_rows, "claim_amount"] / train.loc[sev_rows, "claim_count"]).values
    Xs = X[sev_rows.values]
    sev_model = sm.GLM(sev, Xs, family=sm.families.Gamma(sm.families.links.Log()), var_weights=train.loc[sev_rows, "claim_count"].values).fit()
    tb, _ = _bins(test, factors, edges)
    Xt, _, _ = _design(tb, factors, levels)
    pred_freq = np.exp(Xt @ freq_model.params) * test["exposure"].values
    pred_pp = pred_freq * np.exp(Xt @ sev_model.params)
    actual_loss = test["claim_amount"].values
    relativities = []
    ci = freq_model.conf_int()
    for i, c in enumerate(cols):
        if i == 0:
            continue
        f, lvl = c.split("=", 1)
        relativities.append({"factor": f, "level": lvl, "freq_rel": round(float(np.exp(freq_model.params[i])), 3), "freq_ci": [round(float(np.exp(ci[i][0])), 3), round(float(np.exp(ci[i][1])), 3)], "sev_rel": round(float(np.exp(sev_model.params[i])), 3), "p_value": round(float(freq_model.pvalues[i]), 4)})
    claims = float(train["claim_count"].sum())
    summary = {
        "credibility_z": round(min(1.0, math.sqrt(claims / 1082.0)), 4),
        "train_claims": int(claims),
        "freq_deviance": round(float(freq_model.deviance), 2),
        "freq_null_deviance": round(float(freq_model.null_deviance), 2),
        "deviance_explained": round(float(1 - freq_model.deviance / freq_model.null_deviance), 4) if freq_model.null_deviance else 0.0,
        "aic": round(float(freq_model.aic), 1),
        "gini": round(gini(actual_loss, pred_pp, test["exposure"].values), 4),
        "calibration": calibration(actual_loss, pred_pp),
        "base_frequency": round(float(np.exp(freq_model.params[0])), 5),
        "base_severity": round(float(np.exp(sev_model.params[0])), 2),
        "relativities": relativities,
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "test_pure_premium_mean": round(float(pred_pp.sum() / test["exposure"].sum()), 2),
        "test_actual_mean": round(float(actual_loss.sum() / test["exposure"].sum()), 2),
    }
    return GLMResult(dict(zip(cols, freq_model.params.tolist())), dict(zip(cols, sev_model.params.tolist())), levels, edges, factors, summary)


def glm_predict(res: GLMResult, df: pd.DataFrame) -> np.ndarray:
    binned, _ = _bins(df, res.factors, res.edges)
    X, cols, _ = _design(binned, res.factors, res.levels)
    fp = np.array([res.freq_params.get(c, 0.0) for c in cols])
    sp = np.array([res.sev_params.get(c, 0.0) for c in cols])
    return np.exp(X @ fp), np.exp(X @ sp)


def glm_rate_table(res: GLMResult) -> list[dict]:
    """费率因子表：相对系数按分类定价信度（平方根法则，完全信度 1082 次出险）向 1 收缩。"""
    z = float(res.summary.get("credibility_z", 1.0))
    rows = []
    for f in res.factors:
        lv = res.levels[f]
        discrete = all(float(x).is_integer() for x in res.edges[f]) and len(res.edges[f]) <= 5
        labels = _bin_labels(res.edges[f], discrete)
        for lvl in [lv["base"]] + lv["others"]:
            fr = 1.0 if lvl == lv["base"] else math.exp(res.freq_params.get(f"{f}={lvl}", 0.0))
            sr = 1.0 if lvl == lv["base"] else math.exp(res.sev_params.get(f"{f}={lvl}", 0.0))
            idx = int(lvl) if lvl.isdigit() and not discrete else None
            label = labels[idx] if idx is not None and idx < len(labels) else lvl
            raw = fr * sr
            rows.append({"factor": f, "name": FACTOR_INDEX.get(f, {}).get("name", f), "level": label, "raw_relativity": round(raw, 3), "relativity": round(raw**z, 3), "credibility_z": round(z, 3), "is_base": lvl == lv["base"]})
    return rows


# ---------------------------------------------------------------- 梯度提升树与 Shapley 解释
@dataclass
class GBMResult:
    freq_model: HistGradientBoostingRegressor
    sev_model: HistGradientBoostingRegressor
    factors: list[str]
    summary: dict


def fit_gbm(df: pd.DataFrame, factors: list[str], directions: dict[str, int]) -> GBMResult:
    train, test = split(df)
    mono = [int(directions.get(f, 0)) for f in factors]
    fm = HistGradientBoostingRegressor(loss="poisson", max_iter=250, learning_rate=0.06, max_depth=4, min_samples_leaf=40, monotonic_cst=mono, random_state=11)
    fm.fit(train[factors].values, (train["claim_count"] / train["exposure"]).values, sample_weight=train["exposure"].values)
    pos = train[train["claim_count"] > 0]
    sm_ = HistGradientBoostingRegressor(loss="gamma", max_iter=150, learning_rate=0.06, max_depth=3, min_samples_leaf=30, random_state=12)
    sm_.fit(pos[factors].values, (pos["claim_amount"] / pos["claim_count"]).values, sample_weight=pos["claim_count"].values)
    pf = fm.predict(test[factors].values) * test["exposure"].values
    ps = sm_.predict(test[factors].values)
    pp = pf * ps
    summary = {
        "gini": round(gini(test["claim_amount"].values, pp, test["exposure"].values), 4),
        "calibration": calibration(test["claim_amount"].values, pp),
        "monotonic_constraints": {f: m for f, m in zip(factors, mono)},
        "test_pure_premium_mean": round(float(pp.sum() / test["exposure"].sum()), 2),
    }
    return GBMResult(fm, sm_, factors, summary)


def shapley(model: HistGradientBoostingRegressor, X: np.ndarray, background: np.ndarray, n_perm: int = 24, seed: int = 3) -> np.ndarray:
    """干预式 Shapley 值（置换采样）：对数尺度上解释每个样本的预测，返回 (样本数, 因子数)。"""
    rng = np.random.default_rng(seed)
    n, f = X.shape
    phi = np.zeros((n, f))
    for _ in range(n_perm):
        perm = rng.permutation(f)
        bg = background[rng.integers(0, len(background), size=n)]
        current = bg.copy()
        prev = np.log(np.maximum(model.predict(current), 1e-12))
        for j in perm:
            current[:, j] = X[:, j]
            now = np.log(np.maximum(model.predict(current), 1e-12))
            phi[:, j] += now - prev
            prev = now
    return phi / n_perm


def shap_summary(res: GBMResult, df: pd.DataFrame, sample: int = 160) -> dict:
    rng = np.random.default_rng(5)
    idx = rng.choice(len(df), size=min(sample, len(df)), replace=False)
    X = df[res.factors].values[idx]
    phi = shapley(res.freq_model, X, df[res.factors].values)
    importance = np.abs(phi).mean(axis=0)
    dependence = {}
    for j, f in enumerate(res.factors):
        order = np.argsort(X[:, j])
        dependence[f] = [{"x": round(float(X[k, j]), 4), "phi": round(float(phi[k, j]), 4)} for k in order[:: max(1, len(order) // 40)]]
    return {"importance": sorted(({"factor": f, "name": FACTOR_INDEX.get(f, {}).get("name", f), "mean_abs_phi": round(float(v), 4)} for f, v in zip(res.factors, importance)), key=lambda d: -d["mean_abs_phi"]), "dependence": dependence}


def explain_row(res: GBMResult, row: dict, df: pd.DataFrame) -> dict:
    x = np.array([[float(row.get(f, df[f].median())) for f in res.factors]])
    phi = shapley(res.freq_model, x, df[res.factors].values, n_perm=64)[0]
    base = float(np.log(np.maximum(res.freq_model.predict(df[res.factors].values), 1e-12)).mean())
    pred = float(res.freq_model.predict(x)[0])
    return {"base_log_freq": round(base, 4), "pred_freq": round(pred, 5), "contributions": [{"factor": f, "name": FACTOR_INDEX.get(f, {}).get("name", f), "value": round(float(x[0, j]), 4), "phi": round(float(phi[j]), 4), "multiplier": round(float(math.exp(phi[j])), 3)} for j, f in enumerate(res.factors)]}


# ---------------------------------------------------------------- 信度理论
def buhlmann_straub(df: pd.DataFrame, group: str = "track") -> dict:
    df = df.copy()
    df["pp"] = df["claim_amount"] / df["exposure"]
    cells = df.groupby([group, "year"]).agg(loss=("claim_amount", "sum"), w=("exposure", "sum")).reset_index()
    cells["x"] = cells["loss"] / cells["w"]
    groups = cells.groupby(group)
    w_i = groups["w"].sum()
    x_i = groups.apply(lambda g: (g["x"] * g["w"]).sum() / g["w"].sum(), include_groups=False)
    x_bar = (x_i * w_i).sum() / w_i.sum()
    n_i = groups.size()
    epv_num = sum(((g["x"] - x_i[k]) ** 2 * g["w"]).sum() for k, g in groups)
    epv = epv_num / max(1, (n_i - 1).clip(lower=0).sum())
    w = w_i.sum()
    vhm = ((w_i * (x_i - x_bar) ** 2).sum() - (len(w_i) - 1) * epv) / (w - (w_i**2).sum() / w)
    vhm = max(float(vhm), 1e-9)
    k = float(epv / vhm)
    z = w_i / (w_i + k)
    blended = z * x_i + (1 - z) * x_bar
    return {"k": round(k, 2), "epv": round(float(epv), 2), "vhm": round(vhm, 2), "overall_mean": round(float(x_bar), 2), "groups": [{"group": g, "exposure": round(float(w_i[g]), 1), "own": round(float(x_i[g]), 2), "z": round(float(z[g]), 4), "credibility_estimate": round(float(blended[g]), 2)} for g in w_i.index]}


def credibility_blend(own_pp: float, own_exposure: float, benchmark_pp: float, k: float) -> dict:
    z = own_exposure / (own_exposure + k) if own_exposure > 0 else 0.0
    return {"z": round(z, 4), "own": round(own_pp, 2), "benchmark": round(benchmark_pp, 2), "blended": round(z * own_pp + (1 - z) * benchmark_pp, 2)}


# ---------------------------------------------------------------- 风险成本加成（冷启动）
LIKELIHOOD_FREQ = {1: 0.005, 2: 0.015, 3: 0.04, 4: 0.08, 5: 0.15}
SEVERITY_SHARE = {1: 0.01, 2: 0.03, 3: 0.06, 4: 0.12, 5: 0.2}


def lognormal_params(mean: float, cv: float = 1.2) -> tuple[float, float]:
    sigma2 = math.log(1 + cv**2)
    return math.log(mean) - sigma2 / 2, math.sqrt(sigma2)


def limited_expected(mu: float, sigma: float, limit: float, deductible: float = 0.0) -> float:
    def lev(d: float) -> float:
        if d <= 0:
            return 0.0
        z = (math.log(d) - mu - sigma**2) / sigma
        return math.exp(mu + sigma**2 / 2) * stats.norm.cdf(z) + d * (1 - stats.norm.cdf((math.log(d) - mu) / sigma))

    return max(0.0, lev(deductible + limit) - lev(deductible))


def risk_cost_loading(scenarios: list[dict], limit: float, deductible: float, expert: dict | None = None) -> dict:
    """按风险情景估计期望损失。多条风险往往对应同一类保险事故，用"至少一次出险概率"对频率之和做重叠折减。"""
    items, raw_total, freq_sum, survive = [], 0.0, 0.0, 1.0
    for sc in scenarios:
        prior = (expert or {}).get(sc["subtype"], {})
        freq = prior.get("frequency", LIKELIHOOD_FREQ[int(sc.get("likelihood", 3))])
        mean_sev = prior.get("severity", limit * SEVERITY_SHARE[int(sc.get("severity", 3))])
        mu, sigma = lognormal_params(mean_sev, prior.get("cv", 1.3))
        el = freq * limited_expected(mu, sigma, limit, deductible)
        raw_total += el
        freq_sum += freq
        survive *= 1 - min(freq, 0.99)
        items.append({"risk_id": sc.get("risk_id"), "title": sc.get("title"), "frequency": round(freq, 4), "severity_mean": round(mean_sev, 0), "expected_loss": round(el, 2), "source": "专家先验" if prior else "风险等级映射"})
    overlap = (1 - survive) / freq_sum if freq_sum else 1.0
    total = raw_total * overlap
    for it in items:
        it["expected_loss"] = round(it["expected_loss"] * overlap, 2)
        it["share"] = round(it["expected_loss"] / total, 4) if total else 0
    return {"expected_loss": round(total, 2), "overlap_factor": round(overlap, 4), "items": items}


# ---------------------------------------------------------------- 费率构成
def loss_elimination_ratio(mu: float, sigma: float, deductible: float, limit: float) -> float:
    full = limited_expected(mu, sigma, limit, 0.0)
    after = limited_expected(mu, sigma, limit, deductible)
    return round(1 - after / full, 4) if full else 0.0


def gross_premium(pure: float, risk_loading: float, expense_ratio: float, profit_ratio: float) -> float:
    return (pure + risk_loading) / max(0.05, 1 - expense_ratio - profit_ratio)


# ---------------------------------------------------------------- 情景模拟
DEFAULT_SCENARIOS = [
    {"key": "SC1", "name": "基准", "freq": 1.0, "sev": 1.0, "demand": 1.0, "expense_add": 0.0, "shock_prob": 0.0, "shock_mult": 1.0},
    {"key": "SC2", "name": "经济下行", "freq": 1.3, "sev": 1.0, "demand": 0.8, "expense_add": 0.02, "shock_prob": 0.0, "shock_mult": 1.0},
    {"key": "SC3", "name": "技术迭代加速", "freq": 1.4, "sev": 1.05, "demand": 0.95, "expense_add": 0.0, "shock_prob": 0.0, "shock_mult": 1.0},
    {"key": "SC4", "name": "政策与监管收紧", "freq": 1.05, "sev": 1.3, "demand": 0.7, "expense_add": 0.01, "shock_prob": 0.0, "shock_mult": 1.0},
    {"key": "SC5", "name": "系统性事件", "freq": 1.0, "sev": 1.0, "demand": 1.0, "expense_add": 0.0, "shock_prob": 0.04, "shock_mult": 6.0},
]


def simulate(lam: np.ndarray, sev_mean: np.ndarray, *, deductible: float, limit: float, premium: np.ndarray, scenario: dict, n_sims: int, expense_ratio: float, seed: int, demand_base: float = 1.0, cv: float = 1.2) -> dict:
    rng = np.random.default_rng(seed)
    n = len(lam)
    shock = np.where(rng.random(n_sims) < scenario.get("shock_prob", 0.0), scenario.get("shock_mult", 1.0), 1.0)
    take = rng.random((n_sims, n)) < min(1.0, demand_base * scenario.get("demand", 1.0))
    counts = rng.poisson(lam[None, :] * scenario.get("freq", 1.0) * shock[:, None]) * take
    total_claims = int(counts.sum())
    sim_idx = np.repeat(np.arange(n_sims), counts.sum(axis=1))
    pol_idx = np.concatenate([np.repeat(np.arange(n), counts[s]) for s in range(n_sims)]) if total_claims else np.array([], dtype=int)
    mu_sigma = [lognormal_params(max(1.0, m * scenario.get("sev", 1.0)), cv) for m in sev_mean]
    mus = np.array([m for m, _ in mu_sigma])
    sigmas = np.array([s for _, s in mu_sigma])
    if total_claims:
        x = rng.lognormal(mus[pol_idx], sigmas[pol_idx])
        paid = np.clip(x - deductible, 0, limit)
        losses = np.bincount(sim_idx, weights=paid, minlength=n_sims)
    else:
        losses = np.zeros(n_sims)
    income = (premium[None, :] * take).sum(axis=1)
    lr = losses / np.maximum(income, 1e-9)
    combined = lr + expense_ratio + scenario.get("expense_add", 0.0)
    profit = income * (1 - expense_ratio - scenario.get("expense_add", 0.0)) - losses
    var995 = float(np.quantile(losses, 0.995))
    tail = losses[losses >= var995]
    return {
        "scenario": scenario["key"],
        "name": scenario["name"],
        "n_sims": n_sims,
        "loss_ratio": {"mean": round(float(lr.mean()), 4), "p50": round(float(np.quantile(lr, 0.5)), 4), "p75": round(float(np.quantile(lr, 0.75)), 4), "p90": round(float(np.quantile(lr, 0.9)), 4), "p95": round(float(np.quantile(lr, 0.95)), 4), "p995": round(float(np.quantile(lr, 0.995)), 4)},
        "expected_income": round(float(income.mean()), 2),
        "expected_loss": round(float(losses.mean()), 2),
        "combined_ratio": round(float(combined.mean()), 4),
        "loss_probability": round(float((profit < 0).mean()), 4),
        "var995": round(var995, 2),
        "tvar995": round(float(tail.mean()) if len(tail) else var995, 2),
        "capital": round(max(0.0, var995 - float(losses.mean())), 2),
        "histogram": np.histogram(np.clip(lr, 0, 3), bins=24, range=(0, 3))[0].tolist(),
        "_profit": profit,
    }


def demand_rate(price_ratio: float, coverage_ratio: float, elasticity: float = 2.2, coverage_value: float = 1.0, base: float = 0.55) -> float:
    """投保转化需求模型：价格相对基准越高转化越低，保障越充分转化越高（logit 形式）。"""
    logit = math.log(base / (1 - base)) - elasticity * (price_ratio - 1) + coverage_value * (coverage_ratio - 1)
    return 1 / (1 + math.exp(-logit))


def paired_better(profit_a: np.ndarray, profit_b: np.ndarray) -> dict:
    """配对差值的均值检验：以近似后验 P(E[B−A] > 0) = Φ(均值 / 标准误) 判断变体是否优于对照组。"""
    diff = profit_b - profit_a
    se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else 0.0
    mean = float(diff.mean())
    prob = float(stats.norm.cdf(mean / se)) if se > 0 else (1.0 if mean > 0 else 0.0)
    t_stat = mean / se if se > 0 else 0.0
    return {"prob_better": round(prob, 4), "mean_diff": round(mean, 2), "t_stat": round(t_stat, 3), "share_sims_better": round(float((diff > 0).mean()), 4), "ci95": [round(mean - 1.96 * se, 2), round(mean + 1.96 * se, 2)]}


def benchmark_pure_premium(line: str, sum_insured: float) -> float:
    rate = LINES.get(line, {}).get("benchmark_rate", 0.02)
    loss_ratio = float(LINES.get(line, {}).get("loss_ratio", 0.55))
    return rate * sum_insured * loss_ratio
