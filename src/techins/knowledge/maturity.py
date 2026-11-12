"""险种成熟度三维评分与启动方式判定（对应设计文档 07 号第 7 节）。

定价数据的承保件数、出险件数只统计数据库中的保单和赔案，不读取模拟经验 CSV。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select

from ..db import session_scope
from ..models import ClaimCase, ClauseModule, HistoricalApplication, KnowledgeDoc, Policy, Product
from .catalog import LINES

THRESHOLDS = {"clause": 60, "pricing": 60, "cases": 50}
DIMENSION_LABELS = {"clause": "条款材料", "pricing": "定价数据", "cases": "案例材料"}


@dataclass
class _MaturityStore:
    modules_by_line: dict[str, list[ClauseModule]] = field(default_factory=lambda: defaultdict(list))
    similar_liab_count: dict[str, int] = field(default_factory=dict)
    regulatory_lines: set[str] = field(default_factory=set)
    case_docs_by_line: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    history_by_line: dict[str, list[HistoricalApplication]] = field(default_factory=lambda: defaultdict(list))
    policies_by_line: dict[str, list[Policy]] = field(default_factory=lambda: defaultdict(list))
    live_claims_by_line: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    closed_claims_by_line: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _policy_lines(policy: Policy, products: dict[int, Product]) -> list[str]:
    coverage = policy.coverage or {}
    lines = list(coverage.get("lines") or [])
    product = products.get(policy.product_id)
    if product:
        lines.extend(line for line in (product.lines or []) if line not in lines)
    return lines


def _load_store(lines: list[str] | None = None) -> _MaturityStore:
    target = list(lines) if lines is not None else list(LINES)
    target_set = set(target)
    store = _MaturityStore()
    with session_scope() as s:
        modules = s.scalars(select(ClauseModule)).all()
        for module in modules:
            store.modules_by_line[module.line].append(module)

        subject_lines: dict[str, list[str]] = defaultdict(list)
        for code, spec in LINES.items():
            subject_lines[spec["subject"]].append(code)
        for line in target:
            subject = LINES[line]["subject"]
            similar = [code for code in subject_lines[subject] if code != line]
            store.similar_liab_count[line] = sum(
                1
                for code in similar
                for module in store.modules_by_line.get(code, [])
                if module.module_kind == "liability"
            )

        # 只扫评分用到的监管/案例库，避免每个险种重复全表加载
        for doc in s.scalars(select(KnowledgeDoc).where(KnowledgeDoc.kb.in_(["K02", "K05"]))).all():
            if doc.kb == "K02":
                store.regulatory_lines.update(doc.lines or [])
            elif doc.kb == "K05":
                for code in doc.lines or []:
                    store.case_docs_by_line[code] += 1

        history_q = select(HistoricalApplication)
        if lines is not None:
            history_q = history_q.where(HistoricalApplication.line.in_(target or [""]))
        for row in s.scalars(history_q).all():
            store.history_by_line[row.line].append(row)

        products = {product.id: product for product in s.scalars(select(Product)).all()}
        policies = s.scalars(select(Policy)).all()
        policy_to_lines: dict[int, list[str]] = {}
        for policy in policies:
            plines = [code for code in _policy_lines(policy, products) if code in target_set]
            if not plines:
                continue
            policy_to_lines[policy.id] = plines
            for code in plines:
                store.policies_by_line[code].append(policy)

        policy_ids = list(policy_to_lines)
        if policy_ids:
            live_rows = s.execute(
                select(ClaimCase.policy_id, func.count(ClaimCase.id)).where(ClaimCase.policy_id.in_(policy_ids)).group_by(ClaimCase.policy_id)
            ).all()
            closed_rows = s.execute(
                select(ClaimCase.policy_id, func.count(ClaimCase.id))
                .where(ClaimCase.policy_id.in_(policy_ids), ClaimCase.status == "closed")
                .group_by(ClaimCase.policy_id)
            ).all()
            live_map = {pid: int(cnt) for pid, cnt in live_rows}
            closed_map = {pid: int(cnt) for pid, cnt in closed_rows}
            for pid, plines in policy_to_lines.items():
                live = live_map.get(pid, 0)
                closed = closed_map.get(pid, 0)
                for code in plines:
                    store.live_claims_by_line[code] += live
                    store.closed_claims_by_line[code] += closed
    return store


def _score_line(line: str, store: _MaturityStore) -> dict:
    spec = LINES[line]
    modules = store.modules_by_line.get(line, [])
    liab_filed = sum(1 for m in modules if m.module_kind == "liability" and m.filing_status == "已备案")
    has_demo = any(m.source == "示范条款" for m in modules)
    similar_modules = store.similar_liab_count.get(line, 0)
    regulatory = line in store.regulatory_lines
    case_docs = store.case_docs_by_line.get(line, 0)
    history = store.history_by_line.get(line, [])
    policies = store.policies_by_line.get(line, [])
    live_claims = store.live_claims_by_line.get(line, 0)
    closed_claims = store.closed_claims_by_line.get(line, 0)
    fraud_cases = sum(1 for row in history if row.fraud_label == 1)

    clause_items = {
        "示范条款": 40 if has_demo else 0,
        "已备案责任条款": min(30, liab_filed * 10),
        "相似险种条款": min(20, similar_modules * 5),
        "监管规定明确度": 10 if regulatory or has_demo else 0,
    }
    years = {policy.start_date.year for policy in policies if policy.start_date}
    issued_count = len(policies)
    occurred_count = int(live_claims)
    history_claimed = sum(int(row.claimed or 0) for row in history)
    pricing_items = {
        "经验数据年数": min(30, len(years) * 10),
        "承保件数": 30 if issued_count >= 300 else (20 if issued_count >= 100 else int(issued_count / 100 * 20)),
        "出险件数": 30 if occurred_count >= 100 else (20 if occurred_count >= 30 else int(occurred_count / 30 * 20)),
        "外部基准": 10 if spec.get("benchmark_rate") else 0,
    }
    case_count = case_docs * 4 + closed_claims
    case_claims = history_claimed + occurred_count
    case_items = {
        "理赔案例": 50 if case_count >= 50 else (30 if case_count >= 20 else int(case_count / 20 * 30)),
        "风险事件案例": min(30, int(case_claims / 50 * 30)) if case_claims else 0,
        "欺诈案例": 20 if fraud_cases >= 20 else int(fraud_cases),
    }
    scores = {"clause": sum(clause_items.values()), "pricing": sum(pricing_items.values()), "cases": sum(case_items.values())}
    verdict = {k: ("warm" if v >= THRESHOLDS[k] else "cold") for k, v in scores.items()}
    gaps = {k: max(0, THRESHOLDS[k] - v) for k, v in scores.items()}
    return {
        "line": line,
        "name": spec["name"],
        "scores": scores,
        "items": {"clause": clause_items, "pricing": pricing_items, "cases": case_items},
        "verdict": verdict,
        "gaps": gaps,
        "thresholds": THRESHOLDS,
        "overall": "warm" if all(v == "warm" for v in verdict.values()) else ("cold" if all(v == "cold" for v in verdict.values()) else "mixed"),
        "_policies": len(policies),
    }


def line_maturity(line: str) -> dict:
    if line not in LINES:
        raise KeyError(line)
    return _score_line(line, _load_store([line]))


def all_maturity() -> list[dict]:
    store = _load_store()
    return [_score_line(code, store) for code in LINES]


def component_start_mode(lines: list[str], dimension: str) -> dict:
    """组件级启动方式：按智能体依据的维度判断；任一目标险种该维度为冷则整体按冷启动。"""
    if not lines:
        return {"start_mode": "cold", "degrade_level": "D2", "reasons": ["未指定险种，按冷启动处理"], "detail": []}
    wanted = [line for line in lines if line in LINES]
    store = _load_store(wanted)
    detail = [_score_line(line, store) for line in wanted]
    cold = [d for d in detail if d["verdict"][dimension] == "cold"]
    reasons = [f"{d['name']}的{DIMENSION_LABELS[dimension]}评分 {d['scores'][dimension]}（阈值 {THRESHOLDS[dimension]}）" for d in detail]
    if not cold:
        return {"start_mode": "warm", "degrade_level": "D0", "reasons": reasons, "detail": detail}
    worst = min(d["scores"][dimension] for d in cold)
    level = "D2" if worst >= THRESHOLDS[dimension] / 3 else "D3"
    return {"start_mode": "cold", "degrade_level": level, "reasons": reasons, "detail": detail}
