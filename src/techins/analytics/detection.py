"""风险检测算法库：关键风险指标规则、时序异常（鲁棒分解 + MAD）、多变量孤立森林、变点检测（累积和、贝叶斯在线变点）、
设备健康指数与剩余寿命、数据安全日志检测、风险传导评分。"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats
from sklearn.ensemble import IsolationForest


def kri_check(values: np.ndarray, threshold: float, direction: str = "up") -> dict:
    if len(values) == 0:
        return {"breach": False}
    last = float(values[-1])
    breach = last > threshold if direction == "up" else last < threshold
    recent = values[-24:] if len(values) >= 24 else values
    share = float(((recent > threshold) if direction == "up" else (recent < threshold)).mean())
    return {"breach": bool(breach), "last": round(last, 4), "threshold": threshold, "breach_share_24": round(share, 3)}


def robust_anomalies(values: np.ndarray, period: int = 24, z_threshold: float = 4.0) -> dict:
    """季节与趋势分解：按周期相位取中位数为季节项，滚动中位数为趋势项，残差以 MAD 标准化。"""
    n = len(values)
    if n < period * 3:
        return {"anomalies": [], "z": []}
    phase = np.arange(n) % period
    detr = values - np.array([np.median(values[max(0, i - period * 3) : i + 1]) for i in range(n)])
    seasonal = np.array([np.median(detr[phase == p]) for p in range(period)])[phase]
    resid = detr - seasonal
    mad = np.median(np.abs(resid - np.median(resid))) * 1.4826 or 1e-9
    z = (resid - np.median(resid)) / mad
    idx = [int(i) for i in np.where(np.abs(z) > z_threshold)[0] if i >= n - period * 2]
    return {"anomalies": idx, "z": z.round(3).tolist(), "max_recent_z": round(float(np.abs(z[-period:]).max()), 3)}


def isolation_scores(matrix: np.ndarray, window: int = 48) -> dict:
    if len(matrix) < window * 2:
        return {"recent_score": 0.0, "share": 0.0}
    model = IsolationForest(n_estimators=150, contamination=0.03, random_state=3).fit(matrix[:-window])
    scores = -model.score_samples(matrix)
    base = scores[:-window]
    thr = float(np.quantile(base, 0.99))
    recent = scores[-window:]
    return {"recent_score": round(float(recent.max()), 4), "threshold": round(thr, 4), "share": round(float((recent > thr).mean()), 3)}


def cusum(values: np.ndarray, k: float = 0.5, h: float = 6.0) -> dict:
    if len(values) < 30:
        return {"change": False}
    ref = values[: len(values) // 2]
    mu, sd = float(ref.mean()), float(ref.std() or 1e-9)
    z = (values - mu) / sd
    pos, neg, cp = 0.0, 0.0, None
    for i, x in enumerate(z):
        pos = max(0.0, pos + x - k)
        neg = max(0.0, neg - x - k)
        if (pos > h or neg > h) and cp is None:
            cp = i
    return {"change": cp is not None, "index": cp, "direction": "上升" if pos >= neg else "下降", "statistic": round(max(pos, neg), 3)}


def bocpd(values: np.ndarray, hazard: float = 1 / 200) -> dict:
    """贝叶斯在线变点检测（正态—逆伽马共轭），返回最近时刻发生变点的后验概率。"""
    x = np.asarray(values, dtype=float)
    if len(x) < 20:
        return {"cp_prob": 0.0}
    x = (x - x[: len(x) // 2].mean()) / (x[: len(x) // 2].std() or 1.0)
    T = len(x)
    mu0, kappa0, alpha0, beta0 = 0.0, 1.0, 1.0, 1.0
    R = np.zeros(T + 1)
    R[0] = 1.0
    mu, kappa, alpha, beta = np.array([mu0]), np.array([kappa0]), np.array([alpha0]), np.array([beta0])
    cp_probs = []
    for t in range(T):
        df = 2 * alpha
        scale = np.sqrt(beta * (kappa + 1) / (alpha * kappa))
        pred = stats.t.pdf(x[t], df, loc=mu, scale=scale)
        growth = R[: t + 1] * pred * (1 - hazard)
        cp = float((R[: t + 1] * pred * hazard).sum())
        new_R = np.append(cp, growth)
        new_R /= new_R.sum() or 1.0
        R[: t + 2] = new_R
        cp_probs.append(float(new_R[:6].sum()))
        mu_new = (kappa * mu + x[t]) / (kappa + 1)
        kappa_new = kappa + 1
        alpha_new = alpha + 0.5
        beta_new = beta + kappa * (x[t] - mu) ** 2 / (2 * (kappa + 1))
        mu, kappa, alpha, beta = np.append(mu0, mu_new), np.append(kappa0, kappa_new), np.append(alpha0, alpha_new), np.append(beta0, beta_new)
    recent = cp_probs[-24:]
    return {"cp_prob": round(float(max(recent)), 4), "at": int(len(cp_probs) - len(recent) + int(np.argmax(recent)))}


def health_index(vibration: np.ndarray, temperature: np.ndarray, vib_limit: float = 7.1, temp_limit: float = 85.0) -> dict:
    """设备健康指数（0–100）：以设备自身前段运行数据为健康基线，衡量当前振动与温度向报警限值逼近的程度；
    对近 7 天趋势线性外推估计剩余可用时间（降到 20 分所需时间）。"""
    if len(vibration) < 48:
        return {"index": None}
    half = max(24, len(vibration) // 2)
    v_base = float(np.median(vibration[:half]))
    t_base = float(np.median(temperature[:half]))
    v_margin = np.clip((vib_limit - vibration) / max(vib_limit - v_base, 1e-6), 0, 1)
    t_margin = np.clip((temp_limit - temperature) / max(temp_limit - t_base, 1e-6), 0, 1)
    hi = 100 * (0.6 * v_margin + 0.4 * t_margin)
    window = hi[-168:] if len(hi) >= 168 else hi
    slope, _ = np.polyfit(np.arange(len(window)), window, 1)
    current = float(np.mean(hi[-6:]))
    rul_hours = (current - 20) / -slope if slope < -1e-3 else None
    return {"index": round(current, 1), "slope_per_day": round(float(slope * 24), 3), "rul_days": round(rul_hours / 24, 1) if rul_hours and rul_hours > 0 else None, "baseline": {"vibration": round(v_base, 3), "temperature": round(t_base, 2)}, "series": hi.round(1).tolist()}


def log_security(login_failures: np.ndarray, egress_mb: np.ndarray, unpatched: int) -> dict:
    lf = robust_anomalies(login_failures)
    eg = robust_anomalies(egress_mb)
    signals = []
    if lf.get("max_recent_z", 0) > 4:
        signals.append(f"登录失败次数异常激增（鲁棒标准分 {lf['max_recent_z']}）")
    if eg.get("max_recent_z", 0) > 4:
        signals.append(f"数据外传量异常（鲁棒标准分 {eg['max_recent_z']}）")
    if unpatched:
        signals.append(f"存在 {unpatched} 个未修补高危漏洞")
    score = min(100.0, 20 * len(signals) + 8 * max(0.0, lf.get("max_recent_z", 0) - 3) + 8 * max(0.0, eg.get("max_recent_z", 0) - 3))
    return {"score": round(score, 1), "signals": signals}


def propagation_score(event_severity: float, dependency_share: float, distance: int = 1, decay: float = 0.6) -> float:
    return round(float(event_severity * dependency_share * (decay ** max(0, distance - 1))), 4)


def fuse(signals: list[dict], false_positive_rate: dict[str, float] | None = None) -> dict:
    """多方法信号融合：命中方法越多、方法历史误报率越低，置信度越高（噪声或模型）。"""
    if not signals:
        return {"confidence": 0.0, "severity": 0.0}
    fpr = false_positive_rate or {}
    miss = 1.0
    for s in signals:
        miss *= 1 - s.get("strength", 0.5) * (1 - fpr.get(s["method"], 0.2))
    severity = max(s.get("severity", 0.5) for s in signals)
    return {"confidence": round(1 - miss, 4), "severity": round(severity, 3), "methods": sorted({s["method"] for s in signals})}


def grade(severity: float, confidence: float, related: bool, incident: bool) -> str:
    if incident or (severity >= 0.8 and confidence >= 0.8 and related):
        return "red"
    if (severity >= 0.7 and confidence >= 0.5) or (severity >= 0.5 and related and confidence >= 0.6):
        return "orange"
    if severity >= 0.4 and confidence >= 0.4:
        return "yellow"
    return "blue"


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))
