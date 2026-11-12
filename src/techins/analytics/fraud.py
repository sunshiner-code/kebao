"""反欺诈算法库：监督预测模型、无监督异常检测、关系网络分析、文本相似度与融合模型。

模型以承保历史库训练，训练结果在进程内缓存；历史库更新后调用 reset() 触发重训。
"""

from __future__ import annotations

import math
import threading

import networkx as nx
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sqlalchemy import select

from ..db import session_scope
from ..models import HistoricalApplication

FEATURES = ["si_to_value", "recent_purchase_share", "log_employees", "social_security_ratio", "tax_to_revenue", "change_count_1y", "prior_claims", "industry_risk"]
FEATURE_LABELS = {
    "si_to_value": "保额与价值之比",
    "recent_purchase_share": "投保前 30 天购置占比",
    "log_employees": "员工规模（对数）",
    "social_security_ratio": "参保人数占员工比例",
    "tax_to_revenue": "纳税与营收之比",
    "change_count_1y": "近一年工商变更次数",
    "prior_claims": "历史出险次数",
    "industry_risk": "行业风险等级",
}


def vectorize(f: dict) -> list[float]:
    return [
        float(f.get("si_to_value", 1.0)),
        float(f.get("recent_purchase_share", 0.0)),
        math.log1p(float(f.get("employees", 50))),
        float(f.get("social_security_ratio", 0.9)),
        float(f.get("tax_to_revenue", 0.05)),
        float(f.get("change_count_1y", 0)),
        float(f.get("prior_claims", 0)),
        float(f.get("industry_risk", 3)),
    ]


class FraudModels:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False

    def reset(self) -> None:
        self._ready = False

    def _train(self) -> None:
        with session_scope() as s:
            rows = s.scalars(select(HistoricalApplication)).all()
            self.history = [{"id": r.id, "name": r.enterprise_name, "features": r.features, "contacts": r.contacts, "intermediary": r.intermediary, "statement": r.statement, "label": r.fraud_label, "pattern": r.fraud_pattern, "line": r.line} for r in rows]
        X = np.array([vectorize(h["features"]) for h in self.history])
        y = np.array([h["label"] for h in self.history])
        rng = np.random.default_rng(21)
        idx = rng.permutation(len(y))
        cut = int(len(y) * 0.75)
        tr, te = idx[:cut], idx[cut:]
        self.sup = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, max_depth=3, random_state=4).fit(X[tr], y[tr])
        p_te = self.sup.predict_proba(X[te])[:, 1]
        self.metrics = {"auc": round(float(roc_auc_score(y[te], p_te)), 4), "holdout": int(len(te)), "fraud_rate": round(float(y.mean()), 4)}
        k = max(1, int(len(te) * 0.1))
        top = np.argsort(-p_te)[:k]
        self.metrics["recall_at_10pct"] = round(float(y[te][top].sum() / max(1, y[te].sum())), 4)
        self.metrics["precision_at_10pct"] = round(float(y[te][top].mean()), 4)
        self.sup = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, max_depth=3, random_state=4).fit(X, y)
        self.iso = IsolationForest(n_estimators=200, contamination=0.08, random_state=5).fit(X)
        self.graph = self._build_graph(self.history)
        self.fraud_nodes = [f"APP:{h['id']}" for h in self.history if h["label"] == 1]
        self.pagerank = nx.pagerank(self.graph, personalization={n: 1.0 for n in self.fraud_nodes} or None, alpha=0.85)
        self.communities = {}
        for i, comm in enumerate(nx.community.louvain_communities(self.graph, seed=3)):
            apps = [n for n in comm if n.startswith("APP:")]
            rate = sum(1 for n in apps if n in set(self.fraud_nodes)) / len(apps) if apps else 0.0
            for n in comm:
                self.communities[n] = {"id": i, "size": len(apps), "fraud_rate": rate}
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), sublinear_tf=True).fit([h["statement"] for h in self.history])
        self.stmt_matrix = self.vectorizer.transform([h["statement"] for h in self.history])
        meta = np.zeros((len(y), 5))
        skf = StratifiedKFold(n_splits=4, shuffle=True, random_state=8)
        for a, b in skf.split(X, y):
            m = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.06, max_depth=3, random_state=4).fit(X[a], y[a])
            meta[b, 0] = m.predict_proba(X[b])[:, 1]
        meta[:, 1] = -self.iso.score_samples(X)
        meta[:, 2] = [self._graph_score(f"APP:{h['id']}", exclude_self=True) for h in self.history]
        sims = (self.stmt_matrix @ self.stmt_matrix.T).toarray()
        np.fill_diagonal(sims, 0)
        meta[:, 3] = sims.max(axis=1)
        meta[:, 4] = [len(rule_hits(h["features"], {})) for h in self.history]
        self.fusion = LogisticRegression(max_iter=1000).fit(meta, y)
        self.metrics["fusion_auc_in_sample"] = round(float(roc_auc_score(y, self.fusion.predict_proba(meta)[:, 1])), 4)
        self._ready = True

    def ensure(self) -> None:
        with self._lock:
            if not self._ready:
                self._train()

    @staticmethod
    def _build_graph(history: list[dict]) -> nx.Graph:
        g = nx.Graph()
        for h in history:
            app = f"APP:{h['id']}"
            g.add_node(app, kind="投保主体", label=h["name"], fraud=h["label"])
            for key, prefix in (("phone", "PHONE"), ("address", "ADDR"), ("contact", "CONTACT")):
                val = (h["contacts"] or {}).get(key)
                if val:
                    g.add_node(f"{prefix}:{val}", kind={"PHONE": "电话", "ADDR": "地址", "CONTACT": "联系人"}[prefix], label=val)
                    g.add_edge(app, f"{prefix}:{val}")
            if h.get("intermediary"):
                g.add_node(f"INT:{h['intermediary']}", kind="中介", label=h["intermediary"])
                g.add_edge(app, f"INT:{h['intermediary']}")
        return g

    def _graph_score(self, node: str, exclude_self: bool = False) -> float:
        pr = self.pagerank.get(node, 0.0) * len(self.graph)
        comm = self.communities.get(node, {"fraud_rate": 0.0})
        return float(min(1.0, 0.5 * comm["fraud_rate"] + 0.5 * min(1.0, pr)))

    # ------------------------------------------------------------------ 评分
    def score(self, features: dict, contacts: dict, intermediary: str | None, statement: str, name: str) -> dict:
        self.ensure()
        x = np.array([vectorize(features)])
        sup = float(self.sup.predict_proba(x)[0, 1])
        iso_raw = float(-self.iso.score_samples(x)[0])
        iso_pct = float((-self.iso.score_samples(np.array([vectorize(h["features"]) for h in self.history[:600]])) < iso_raw).mean())
        g = self.graph.copy()
        node = "APP:current"
        g.add_node(node, kind="投保主体", label=name, fraud=0)
        links = []
        for key, prefix in (("phone", "PHONE"), ("address", "ADDR"), ("contact", "CONTACT")):
            val = (contacts or {}).get(key)
            if val and g.has_node(f"{prefix}:{val}"):
                g.add_edge(node, f"{prefix}:{val}")
                links.append(f"{prefix}:{val}")
        if intermediary and g.has_node(f"INT:{intermediary}"):
            g.add_edge(node, f"INT:{intermediary}")
            links.append(f"INT:{intermediary}")
        fraud_set = set(self.fraud_nodes)
        nearest, path = None, []
        if links:
            lengths = nx.single_source_shortest_path_length(g, node, cutoff=4)
            reach = [(n, d) for n, d in lengths.items() if n in fraud_set]
            if reach:
                nearest = min(reach, key=lambda t: t[1])
                path = nx.shortest_path(g, node, nearest[0])
        shared = {l: [n for n in g.neighbors(l) if n.startswith("APP:") and n != node] for l in links if not l.startswith("INT:")}
        comm_rates = [self.communities.get(l, {"fraud_rate": 0}).get("fraud_rate", 0) for l in links]
        graph_score = float(min(1.0, (max(comm_rates) if comm_rates else 0) * 0.6 + (0.4 if nearest and nearest[1] <= 2 else (0.2 if nearest else 0))))
        sims = (self.stmt_matrix @ self.vectorizer.transform([statement]).T).toarray().ravel()
        top = int(np.argmax(sims)) if len(sims) else 0
        text_sim = float(sims[top]) if len(sims) else 0.0
        hits = rule_hits(features, {"shared": shared, "text_sim": text_sim})
        meta = np.array([[sup, iso_raw, graph_score, text_sim, len(hits)]])
        prob = float(self.fusion.predict_proba(meta)[0, 1])
        coefs = self.fusion.coef_[0]
        contrib = {name: round(float(c * v), 4) for name, c, v in zip(["监督模型", "无监督异常", "关系网络", "文本相似", "专家规则"], coefs, meta[0])}
        sub_graph = {"nodes": [], "edges": []}
        keep = {node} | set(links) | {n for l in links for n in list(g.neighbors(l))[:8]} | set(path)
        for n in keep:
            d = g.nodes[n]
            sub_graph["nodes"].append({"id": n, "kind": d.get("kind"), "label": d.get("label") if n != node else name, "fraud": int(d.get("fraud", 0)) if n.startswith("APP:") else None})
        for u, v in g.subgraph(keep).edges():
            sub_graph["edges"].append({"source": u, "target": v})
        return {
            "probability": round(prob, 4),
            "submodels": {"F1 专家规则": len(hits), "F2 监督模型": round(sup, 4), "F3 无监督异常": round(iso_pct, 4), "F4 关系网络": round(graph_score, 4), "F5 文本线索": round(text_sim, 4)},
            "contributions": contrib,
            "rule_hits": hits,
            "graph": sub_graph,
            "nearest_fraud": {"node": nearest[0], "distance": nearest[1], "path": path} if nearest else None,
            "shared_contacts": {k: len(v) for k, v in shared.items()},
            "similar_statement": {"text": self.history[top]["statement"], "similarity": round(text_sim, 3), "fraud": self.history[top]["label"]} if len(sims) else None,
            "feature_contrib": {FEATURE_LABELS[f]: round(float(v), 4) for f, v in zip(FEATURES, x[0])},
            "model_metrics": self.metrics,
        }


def rule_hits(f: dict, ctx: dict) -> list[dict]:
    hits = []
    if f.get("si_to_value", 1) > 1.2:
        hits.append({"pattern": "P02", "rule": "保额超过价值 1.2 倍", "value": round(f["si_to_value"], 3)})
    if f.get("recent_purchase_share", 0) > 0.4:
        hits.append({"pattern": "P01", "rule": "投保前 30 天购置标的占比超过 40%", "value": round(f["recent_purchase_share"], 3)})
    if f.get("social_security_ratio", 1) < 0.3:
        hits.append({"pattern": "P07", "rule": "参保人数占员工比例低于 30%", "value": round(f["social_security_ratio"], 3)})
    if f.get("change_count_1y", 0) >= 3:
        hits.append({"pattern": "P08", "rule": "近一年工商变更 3 次及以上", "value": f["change_count_1y"]})
    if f.get("tax_to_revenue", 0.05) < 0.005:
        hits.append({"pattern": "P12", "rule": "纳税与营收之比异常偏低", "value": round(f.get("tax_to_revenue", 0), 4)})
    for k, apps in (ctx.get("shared") or {}).items():
        if len(apps) >= 2:
            hits.append({"pattern": "P06", "rule": f"联系信息与 {len(apps)} 个其他投保主体共用", "value": k.split(":", 1)[1]})
    if ctx.get("text_sim", 0) > 0.85:
        hits.append({"pattern": "P11", "rule": "投保声明与历史材料高度相似", "value": round(ctx["text_sim"], 3)})
    return hits


fraud_models = FraudModels()
