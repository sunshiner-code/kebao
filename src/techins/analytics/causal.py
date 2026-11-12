"""关联分析与因果推断引擎（多方事故责任比例划分）。

流程：关联分析（滞后相关、格兰杰检验、关联规则）→ 因果图（知识模板骨架 + 数据驱动的条件独立性检验校正边强度）
→ 结构因果模型上的反事实干预（假设某方按合同或规范行事）→ Shapley 值分摊原因力 → 过错与合同调整 → 重抽样区间与敏感性。
另含损失范围认定用的反事实基线预测（季节项 + 趋势项回归）。
"""

from __future__ import annotations

import itertools
import math
from typing import Callable

import numpy as np
from scipy import stats


# ------------------------------------------------------------------ 关联分析
def lagged_correlation(x: np.ndarray, y: np.ndarray, max_lag: int = 12) -> dict:
    """x 领先 y 的滞后相关：返回相关系数最大的滞后期与相关系数。"""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    best = {"lag": 0, "corr": 0.0}
    for lag in range(0, max_lag + 1):
        a, b = (x[:-lag], y[lag:]) if lag else (x, y)
        if len(a) < 10 or a.std() == 0 or b.std() == 0:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if abs(c) > abs(best["corr"]):
            best = {"lag": lag, "corr": round(c, 4)}
    return best


def granger(x: np.ndarray, y: np.ndarray, max_lag: int = 6) -> dict:
    """格兰杰因果检验（x 是否有助于预测 y）：受限模型只含 y 的滞后，非受限模型加入 x 的滞后，F 检验取各滞后期最小 p 值。"""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = min(len(x), len(y))
    x, y = x[-n:], y[-n:]
    best = {"lag": None, "f": 0.0, "p": 1.0}
    for p in range(1, max_lag + 1):
        if n - p <= 3 * p + 5:
            break
        Y = y[p:]
        lags_y = np.column_stack([y[p - k : n - k] for k in range(1, p + 1)])
        lags_x = np.column_stack([x[p - k : n - k] for k in range(1, p + 1)])
        ones = np.ones((len(Y), 1))
        Xr = np.hstack([ones, lags_y])
        Xu = np.hstack([ones, lags_y, lags_x])
        rss_r = float(np.sum((Y - Xr @ np.linalg.lstsq(Xr, Y, rcond=None)[0]) ** 2))
        rss_u = float(np.sum((Y - Xu @ np.linalg.lstsq(Xu, Y, rcond=None)[0]) ** 2))
        df2 = len(Y) - Xu.shape[1]
        if rss_u <= 0 or df2 <= 0:
            continue
        f = ((rss_r - rss_u) / p) / (rss_u / df2)
        pval = float(1 - stats.f.cdf(f, p, df2))
        if pval < best["p"]:
            best = {"lag": p, "f": round(float(f), 3), "p": round(pval, 5)}
    best["significant"] = best["p"] < 0.05
    return best


def association_rules(events: list[tuple[float, str]], window: float, min_support: int = 2) -> list[dict]:
    """事件关联规则：A 发生后 window 时间内发生 B 的支持度、置信度与提升度。"""
    events = sorted(events)
    kinds = sorted({k for _, k in events})
    total_span = (events[-1][0] - events[0][0]) if len(events) > 1 else window
    rules = []
    for a in kinds:
        ta = [t for t, k in events if k == a]
        for b in kinds:
            if a == b:
                continue
            tb = [t for t, k in events if k == b]
            hits = sum(1 for t in ta if any(0 < u - t <= window for u in tb))
            if hits < min_support:
                continue
            conf = hits / len(ta)
            base = min(1.0, len(tb) * window / max(total_span, window))
            rules.append({"antecedent": a, "consequent": b, "support": hits, "confidence": round(conf, 3), "lift": round(conf / max(base, 1e-6), 3)})
    return sorted(rules, key=lambda r: -r["lift"])


# ------------------------------------------------------------------ 因果发现（PC 风格的条件独立性检验）
def _partial_corr(data: dict[str, np.ndarray], a: str, b: str, given: tuple[str, ...]) -> float:
    if not given:
        return float(np.corrcoef(data[a], data[b])[0, 1])
    Z = np.column_stack([data[g] for g in given] + [np.ones(len(data[a]))])
    ra = data[a] - Z @ np.linalg.lstsq(Z, data[a], rcond=None)[0]
    rb = data[b] - Z @ np.linalg.lstsq(Z, data[b], rcond=None)[0]
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def pc_skeleton(data: dict[str, np.ndarray], alpha: float = 0.01, max_cond: int = 1) -> dict:
    """PC 算法骨架阶段：从完全图出发，用费舍尔 z 检验删去条件独立的边，保留边的偏相关作为数据强度。"""
    names = [k for k, v in data.items() if len(v) > 10 and np.std(v) > 0]
    n = min(len(data[k]) for k in names) if names else 0
    data = {k: np.asarray(data[k], float)[-n:] for k in names}
    adj = {frozenset(e) for e in itertools.combinations(names, 2)}
    removed = []
    for size in range(0, max_cond + 1):
        for edge in sorted(adj, key=lambda e: tuple(sorted(e))):
            a, b = sorted(edge)
            others = [c for c in names if c not in edge and (frozenset((a, c)) in adj or frozenset((b, c)) in adj)]
            for given in itertools.combinations(others, size):
                r = max(-0.9999, min(0.9999, _partial_corr(data, a, b, given)))
                z = 0.5 * math.log((1 + r) / (1 - r)) * math.sqrt(max(1, n - len(given) - 3))
                p = 2 * (1 - stats.norm.cdf(abs(z)))
                if p > alpha:
                    adj.discard(edge)
                    removed.append({"edge": [a, b], "given": list(given), "p": round(p, 4)})
                    break
    strengths = {}
    for e in adj:
        a, b = sorted(e)
        strengths[f"{a}|{b}"] = round(abs(_partial_corr(data, a, b, ())), 4)
    return {"nodes": names, "edges": [sorted(e) for e in adj], "strengths": strengths, "removed": removed, "n": n}


# ------------------------------------------------------------------ 结构因果模型与反事实
class SCM:
    """离散化的结构因果模型：行为节点取值为"偏离规范程度"（0 = 按合同或规范行事，1 = 完全偏离），
    状态与事件节点按带外生泄漏项的噪声或模型生成：P(v) = 1 − (1 − leak) · Π(1 − w_e · x_parent)。"""

    def __init__(self, nodes: list[dict], edges: list[dict], leak: float = 0.05) -> None:
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = [dict(e) for e in edges]
        self.leak = leak
        self.order = self._topo()

    def _topo(self) -> list[str]:
        indeg = {n: 0 for n in self.nodes}
        for e in self.edges:
            indeg[e["to"]] += 1
        queue = [n for n, d in indeg.items() if d == 0]
        order = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for e in self.edges:
                if e["from"] == n:
                    indeg[e["to"]] -= 1
                    if indeg[e["to"]] == 0:
                        queue.append(e["to"])
        if len(order) != len(self.nodes):
            raise ValueError("因果图存在环，无法构建结构因果模型")
        return order

    def evaluate(self, behaviors: dict[str, float], interventions: dict[str, float] | None = None, strengths: dict[str, float] | None = None) -> dict[str, float]:
        values: dict[str, float] = {}
        interventions = interventions or {}
        for n in self.order:
            if n in interventions:
                values[n] = interventions[n]
                continue
            node = self.nodes[n]
            if node["kind"] == "behavior":
                values[n] = float(behaviors.get(n, 0.0))
                continue
            parents = [e for e in self.edges if e["to"] == n]
            if not parents:
                values[n] = self.leak
                continue
            keep = 1 - self.leak
            for e in parents:
                w = (strengths or {}).get(f"{e['from']}>{e['to']}", e["strength"])
                keep *= 1 - w * values[e["from"]]
            values[n] = 1 - keep
        return values

    def outcome(self, behaviors: dict[str, float], interventions: dict[str, float] | None = None, strengths: dict[str, float] | None = None, target: str = "loss") -> float:
        return self.evaluate(behaviors, interventions, strengths)[target]


def shapley(players: list[str], value: Callable[[frozenset], float]) -> dict[str, float]:
    """精确 Shapley 值：v(S) 为联盟 S 中各方都按规范行事时损失的减少量。"""
    n = len(players)
    phi = {p: 0.0 for p in players}
    cache: dict[frozenset, float] = {}

    def v(s: frozenset) -> float:
        if s not in cache:
            cache[s] = value(s)
        return cache[s]

    for p in players:
        others = [q for q in players if q != p]
        for k in range(len(others) + 1):
            for combo in itertools.combinations(others, k):
                s = frozenset(combo)
                w = math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n)
                phi[p] += w * (v(s | {p}) - v(s))
    return phi


def responsibility(scm: SCM, behaviors: dict[str, float], party_nodes: dict[str, list[str]], loss: float, strengths: dict[str, float] | None = None) -> dict:
    """反事实效应与原因力：对每一方的行为节点做干预（置 0），按 Shapley 值分摊各方对事故概率的贡献。"""
    actual = scm.outcome(behaviors, strengths=strengths)
    parties = list(party_nodes)

    def value(coalition: frozenset) -> float:
        inter = {node: 0.0 for p in coalition for node in party_nodes[p]}
        return actual - scm.outcome(behaviors, inter, strengths)

    phi = shapley(parties, value)
    total_explained = value(frozenset(parties))
    residual = max(0.0, actual - total_explained)
    denom = sum(max(0.0, v) for v in phi.values()) + residual
    shares = {p: max(0.0, v) / denom if denom > 0 else 0.0 for p, v in phi.items()}
    shares["意外与不可归责因素"] = residual / denom if denom > 0 else 1.0
    single = {p: {"p_actual": round(actual, 4), "p_counterfactual": round(scm.outcome(behaviors, {n: 0.0 for n in party_nodes[p]}, strengths), 4)} for p in parties}
    for p in parties:
        single[p]["loss_change"] = round(loss * (single[p]["p_actual"] - single[p]["p_counterfactual"]) / max(actual, 1e-9), 2)
    return {"p_actual": round(actual, 4), "shapley": {k: round(v, 5) for k, v in phi.items()}, "shares": {k: round(v, 4) for k, v in shares.items()}, "counterfactual": single}


def fault_adjust(shares: dict[str, float], faults: dict[str, float], caps: dict[str, float] | None = None, loss: float = 0.0) -> dict:
    """过错与合同调整：原因力 × 过错系数（无过错方不承担）后重新归一；合同责任上限折算为比例封顶，超出部分回归被保险人与意外因素。"""
    raw = {p: s * (faults.get(p, 0.6) if p != "意外与不可归责因素" else 1.0) for p, s in shares.items()}
    total = sum(raw.values()) or 1.0
    adj = {p: v / total for p, v in raw.items()}
    notes = []
    for p, cap in (caps or {}).items():
        if p in adj and loss > 0 and adj[p] * loss > cap:
            excess = adj[p] - cap / loss
            adj[p] = cap / loss
            adj["意外与不可归责因素"] = adj.get("意外与不可归责因素", 0.0) + excess
            notes.append(f"{p}按合同责任上限 {cap:.0f} 元封顶")
    return {"shares": {k: round(v, 4) for k, v in adj.items()}, "notes": notes}


def bootstrap_shares(scm: SCM, behaviors: dict[str, float], party_nodes: dict[str, list[str]], loss: float, faults: dict[str, float], rounds: int = 400, seed: int = 11, progress: Callable[[float, str], None] | None = None, caps: dict[str, float] | None = None) -> dict:
    """重抽样：边强度按贝塔分布扰动（集中度 30）、行为偏离度 ±0.15 均匀扰动，得到各方建议比例的 5%–95% 区间。"""
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {}
    for i in range(rounds):
        strengths = {}
        for e in scm.edges:
            m = min(0.98, max(0.02, e["strength"]))
            strengths[f"{e['from']}>{e['to']}"] = float(rng.beta(m * 30, (1 - m) * 30))
        beh = {k: float(np.clip(v + rng.uniform(-0.15, 0.15), 0, 1)) for k, v in behaviors.items()}
        res = responsibility(scm, beh, party_nodes, loss, strengths)
        adj = fault_adjust(res["shares"], faults, caps, loss)["shares"]
        for p, v in adj.items():
            samples.setdefault(p, []).append(v)
        if progress and i % 50 == 0:
            progress(i / rounds, f"重抽样 {i}/{rounds}")
    return {p: {"p05": round(float(np.quantile(v, 0.05)), 4), "p50": round(float(np.median(v)), 4), "p95": round(float(np.quantile(v, 0.95)), 4)} for p, v in samples.items()}


def sensitivity(scm: SCM, behaviors: dict[str, float], party_nodes: dict[str, list[str]], loss: float, faults: dict[str, float], delta: float = 0.2, caps: dict[str, float] | None = None) -> list[dict]:
    """关键假设敏感性：逐条边强度与逐个行为偏离度 ±20%，记录各方建议比例的最大变化。"""
    base = fault_adjust(responsibility(scm, behaviors, party_nodes, loss)["shares"], faults, caps, loss)["shares"]
    rows = []
    for e in scm.edges:
        key = f"{e['from']}>{e['to']}"
        changes = {}
        for sign in (-1, 1):
            s = {key: min(0.99, max(0.01, e["strength"] * (1 + sign * delta)))}
            adj = fault_adjust(responsibility(scm, behaviors, party_nodes, loss, s)["shares"], faults, caps, loss)["shares"]
            for p in base:
                changes[p] = max(changes.get(p, 0.0), abs(adj.get(p, 0) - base[p]))
        rows.append({"assumption": f"因果边 {e['from']}→{e['to']} 强度 ±{int(delta * 100)}%", "max_change": round(max(changes.values()), 4), "by_party": {k: round(v, 4) for k, v in changes.items()}})
    for b, v in behaviors.items():
        changes = {}
        for sign in (-1, 1):
            beh = dict(behaviors)
            beh[b] = float(np.clip(v * (1 + sign * delta), 0, 1))
            adj = fault_adjust(responsibility(scm, beh, party_nodes, loss)["shares"], faults, caps, loss)["shares"]
            for p in base:
                changes[p] = max(changes.get(p, 0.0), abs(adj.get(p, 0) - base[p]))
        rows.append({"assumption": f"行为偏离度 {b} ±{int(delta * 100)}%", "max_change": round(max(changes.values()), 4), "by_party": {k: round(v, 4) for k, v in changes.items()}})
    return sorted(rows, key=lambda r: -r["max_change"])


# ------------------------------------------------------------------ 反事实基线（损失范围认定）
def counterfactual_baseline(values: np.ndarray, train_end: int, period: int = 24) -> dict:
    """以事故前数据训练"日内季节项 + 线性趋势"回归，预测事故后的反事实基线，返回逐点缺口。"""
    y = np.asarray(values, float)
    t = np.arange(len(y))
    X = np.column_stack([np.ones(len(y)), t] + [f(2 * np.pi * k * t / period) for k in (1, 2) for f in (np.sin, np.cos)])
    coef = np.linalg.lstsq(X[:train_end], y[:train_end], rcond=None)[0]
    pred = X @ coef
    resid_sd = float(np.std(y[:train_end] - pred[:train_end])) or 1e-9
    gap = pred[train_end:] - y[train_end:]
    return {"pred": pred.round(4).tolist(), "gap": gap.round(4).tolist(), "resid_sd": round(resid_sd, 4), "total_gap": round(float(gap.sum()), 3), "relative_gap": round(float(gap.sum() / max(pred[train_end:].sum(), 1e-9)), 4)}
