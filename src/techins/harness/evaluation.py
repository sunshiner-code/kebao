"""自我评估：硬规则、指标、大模型评审、证据核验四层评估器（对应设计文档 05 号第 7 节）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..jsonutil import jsonable
from ..db import session_scope
from ..llm.gateway import ModelUnavailable, model_gateway
from ..models import Evaluation
from .agent import Dimension

if TYPE_CHECKING:
    from .context import RunContext

LAYER_LABELS = {"hard": "硬规则", "metric": "指标", "review": "大模型评审", "evidence": "证据核验"}


def _fmt(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    return value


def evaluate(ctx: "RunContext", dims: list[Dimension], *, kind: str, target: str, rubric_version: str | None = None) -> dict:
    step_id = ctx.begin_step("evaluation", f"{'阶段' if kind == 'stage' else '最终'}评估：{target}", zone="platform")
    aux: dict[str, Any] = {}
    reviewer_model = None
    review_dims = [d for d in dims if d.layer == "review"]
    if review_dims and ctx.profile.review_task and ctx.profile.review_inputs:
        try:
            review_inputs = ctx.profile.review_inputs(ctx)
            review_inputs["dimensions"] = [{"key": d.key, "name": d.name, "threshold": d.threshold} for d in review_dims]
            aux["review"] = model_gateway.invoke(ctx.profile.review_task, review_inputs, ctx=ctx, data_level=review_inputs.get("_level", 1))
            reviewer_model = "routed"
        except ModelUnavailable as exc:
            aux["review_error"] = str(exc)
    results = []
    for dim in dims:
        try:
            value, passed, detail = dim.fn(ctx, aux)
        except Exception as exc:  # noqa: BLE001 - 维度计算异常视为不通过并记录
            value, passed, detail = None, False, f"评估异常：{exc}"
        results.append(
            {
                "key": dim.key,
                "name": dim.name,
                "layer": dim.layer,
                "layer_label": LAYER_LABELS[dim.layer],
                "value": _fmt(value),
                "threshold": dim.threshold,
                "passed": bool(passed),
                "detail": detail,
                "category": dim.category,
                "weight": dim.weight,
            }
        )
    hard_ok = all(r["passed"] for r in results if r["layer"] == "hard")

    def ratio(layer: str) -> float | None:
        items = [r for r in results if r["layer"] == layer]
        if not items:
            return None
        total = sum(r["weight"] for r in items)
        return sum(r["weight"] for r in items if r["passed"]) / total if total else None

    review_items = [r for r in results if r["layer"] == "review" and isinstance(r["value"], (int, float))]
    review_score = (sum(r["value"] for r in review_items) / len(review_items) / 5) if review_items else None
    parts = [(0.4, ratio("metric")), (0.4, review_score), (0.2, ratio("evidence"))]
    available = [(w, v) for w, v in parts if v is not None]
    if available:
        total_w = sum(w for w, _ in available)
        score = sum(w * v for w, v in available) / total_w * 100
    else:
        score = 100.0 if hard_ok else 0.0
    if not hard_ok:
        score = min(score, 59.0)
    threshold = ctx.profile.pass_score if kind == "final" else 60.0
    passed = hard_ok and score >= threshold and all(r["passed"] for r in results if r["layer"] == "metric" and r["weight"] >= 2)
    failed = [r["key"] for r in results if not r["passed"]]
    with session_scope() as s:
        rec = Evaluation(
            run_id=ctx.run_id,
            kind=kind,
            target=target,
            rubric_version=rubric_version or ctx.profile.version,
            dimensions=jsonable(results),
            score=round(score, 2),
            passed=passed,
            failed_dims=failed,
            reviewer_model=reviewer_model,
        )
        s.add(rec)
        s.flush()
        eval_id = rec.id
    record = {"id": eval_id, "kind": kind, "target": target, "score": round(score, 2), "passed": passed, "failed": failed, "dimensions": results, "threshold": threshold}
    ctx.state["eval_history"].append({"id": eval_id, "kind": kind, "target": target, "score": round(score, 2), "passed": passed})
    summary = f"综合得分 {score:.1f}（达标线 {threshold:.0f}），{'达标' if passed else '未达标'}" + (f"；未通过维度：{'、'.join(r['name'] for r in results if not r['passed'])}" if failed else "")
    ctx.end_step(step_id, status="done" if passed else "warning", summary=summary, detail={"evaluation_id": eval_id})
    ctx.emit("evaluation.finished", record)
    return record
