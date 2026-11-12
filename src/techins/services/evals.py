"""评测与自进化：评测集（线上运行沉淀与专家样本）、评测任务（评测模式重放、与基线对比、失败归因）、线上评估、
进化分析器（周期生成改进提案）、提案评测、发布审批、灰度、发布与回滚、灰度自动回滚。"""

from __future__ import annotations

import json
import logging
import statistics
import threading
import time
import zlib
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..harness.agent import all_profiles, get_profile
from ..models import AgentConfig, Alert, Application, Artifact, ClaimCase, EvalJob, EvalSet, Evaluation, EvolutionProposal, HitlCard, MemoryEntry, Policy, Reflection, Run
from . import agent_config, approvals
from .config_center import cfg
from .notify import notify

log = logging.getLogger("techins.evals")
AGENT_NAMES = {"AG-RA": "风险评估", "AG-CD": "条款设计", "AG-AP": "精算定价", "AG-UW": "承保风控", "AG-RW": "风险预警", "AG-CL": "定损理赔"}
FINAL_CARDS = {"result_confirm", "decision", "claim_approval", "option_select"}
SAMPLE_TIMEOUT_S = 20 * 60


class EvalError(ValueError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


# ---------------------------------------------------------------- 期望值与比对


def _artifact(s, out: dict) -> dict:
    key = (out or {}).get("artifact_key")
    if not key:
        return {}
    q = select(Artifact).where(Artifact.artifact_key == key)
    art = s.scalars(q.where(Artifact.version == out["version"]) if out.get("version") else q.order_by(Artifact.version.desc())).first()
    return dict(art.data or {}) if art else {}


def extract_expected(s, run: Run) -> dict:
    """从运行结果抽取用于比对的关键结论（各智能体不同）。"""
    out = run.output or {}
    data = _artifact(s, out)
    exp: dict = {"eval_score": run.eval_score, "eval_passed": run.eval_passed}
    if run.agent_id == "AG-UW":
        d = data.get("decision") or {}
        exp.update({"decision": d.get("decision") or out.get("decision"), "premium": d.get("premium"), "fraud_probability": (data.get("fraud") or {}).get("probability")})
    elif run.agent_id == "AG-CL":
        adj = data.get("adjustment") or {}
        exp.update({"payout": adj.get("payout"), "insured_event": (data.get("liability") or {}).get("insured_event"), "proximate_cause": (data.get("liability") or {}).get("proximate_cause")})
    elif run.agent_id == "AG-RA":
        exp.update({"subtypes": sorted({r.get("subtype") for r in data.get("risks", []) if r.get("subtype")}), "overall_level": ((data.get("report") or {}).get("stats") or data.get("stats") or {}).get("overall_level")})
    elif run.agent_id == "AG-CD":
        exp.update({"docs": sorted(d.get("name") for d in data.get("docs", [])), "rule_failures": sum(1 for r in (data.get("compliance") or {}).get("rules", []) for c in r.get("checks", []) if not c.get("passed"))})
    elif run.agent_id == "AG-AP":
        exp.update({"rates": {c.get("name"): (c.get("rate") or {}).get("gross_rate") for c in data.get("components", [])}})
    elif run.agent_id == "AG-RW":
        if run.purpose == "alert":
            exp.update({"level": out.get("level"), "suppressed": bool(out.get("suppressed"))})
        elif run.purpose == "main":
            exp.update({"risk_level": ((data.get("stats") or {}).get("risk_index") or {}).get("level")})
        else:
            exp.update({"summary": out.get("summary")})
    return exp


def compare(agent_id: str, purpose: str, expected: dict, actual: dict) -> dict:
    """样本结论比对：返回 match（是否一致）与说明。"""
    if agent_id == "AG-UW":
        ok = expected.get("decision") == actual.get("decision")
        dev = abs((actual.get("premium") or 0) - (expected.get("premium") or 0)) / max(1.0, expected.get("premium") or 1.0) if expected.get("premium") else None
        return {"match": ok, "detail": f"决策 {expected.get('decision')} → {actual.get('decision')}" + (f"，保费偏差 {dev:.1%}" if dev is not None else ""), "deviation": dev}
    if agent_id == "AG-CL":
        e, a = expected.get("payout") or 0.0, actual.get("payout") or 0.0
        dev = abs(a - e) / max(1.0, e) if e else (0.0 if not a else 1.0)
        return {"match": dev <= 0.1 and expected.get("insured_event") == actual.get("insured_event"), "detail": f"赔付 {e:,.0f} → {a:,.0f}（偏差 {dev:.1%}）", "deviation": dev}
    if agent_id == "AG-RA":
        e, a = set(expected.get("subtypes") or []), set(actual.get("subtypes") or [])
        j = len(e & a) / len(e | a) if e | a else 1.0
        return {"match": j >= 0.6, "detail": f"风险子类型重合度 {j:.0%}（期望 {len(e)}，实际 {len(a)}）", "deviation": 1 - j}
    if agent_id == "AG-CD":
        same = expected.get("docs") == actual.get("docs")
        return {"match": same and (actual.get("rule_failures") or 0) == 0, "detail": f"条款 {'一致' if same else '不一致'}，强制规则未通过 {actual.get('rule_failures') or 0} 项", "deviation": None}
    if agent_id == "AG-AP":
        e, a = expected.get("rates") or {}, actual.get("rates") or {}
        devs = [abs((a.get(k) or 0) - v) / v for k, v in e.items() if v]
        worst = max(devs) if devs else 0.0
        return {"match": bool(devs) and worst <= 0.15, "detail": f"费率最大偏差 {worst:.1%}", "deviation": worst}
    if agent_id == "AG-RW" and purpose == "alert":
        ok = expected.get("level") == actual.get("level") and expected.get("suppressed") == actual.get("suppressed")
        return {"match": ok, "detail": f"预警等级 {expected.get('level') or '抑制'} → {actual.get('level') or '抑制'}", "deviation": None}
    if agent_id == "AG-RW" and purpose == "main":
        return {"match": expected.get("risk_level") == actual.get("risk_level"), "detail": f"风险等级 {expected.get('risk_level')} → {actual.get('risk_level')}", "deviation": None}
    return {"match": bool(actual.get("eval_passed")), "detail": "以最终评估是否达标判定", "deviation": None}


def _clean_input(inp: dict) -> dict:
    return {k: v for k, v in (inp or {}).items() if not k.startswith("_") and k not in ("artifact_key", "base_version")}


def _tags(run: Run) -> dict:
    inp = run.input or {}
    lines = sorted({l for l in (inp.get("lines") or inp.get("target_lines") or [])}) if isinstance(inp.get("lines") or inp.get("target_lines"), list) else []
    return {"start_mode": run.start_mode, "difficulty": "难" if run.reflection_rounds >= 2 or run.degrade_level not in ("D0", "D1") else ("中" if run.reflection_rounds else "易"), "lines": lines}


# ---------------------------------------------------------------- 评测集


def set_dict(e: EvalSet, full: bool = False) -> dict:
    d = {"id": e.id, "agent_id": e.agent_id, "agent_name": AGENT_NAMES.get(e.agent_id, e.agent_id), "purpose": e.purpose, "name": e.name, "split": e.split, "split_label": "开发集" if e.split == "dev" else "保留集", "version": e.version, "samples": len(e.samples or []), "description": e.description, "created_at": iso(e.created_at), "updated_at": iso(e.updated_at), "tag_stats": dict(Counter((x.get("tags") or {}).get("start_mode") for x in e.samples or []))}
    if full:
        d["items"] = e.samples
    return d


def build_from_runs(agent_id: str, purpose: str, name: str, limit: int, user_id: int | None, description: str = "") -> list[dict]:
    """从已完成的线上运行沉淀样本：按运行编号哈希稳定地划分开发集与保留集（约 7:3）。"""
    if agent_id not in all_profiles():
        raise EvalError("智能体不存在")
    try:
        get_profile(agent_id, purpose)
    except KeyError as exc:
        raise EvalError("运行目的不存在") from exc
    with session_scope() as s:
        runs = s.scalars(select(Run).where(Run.agent_id == agent_id, Run.purpose == purpose, Run.status == "completed", Run.parent_run_id.is_(None)).order_by(Run.id.desc()).limit(500)).all()
        runs = [r for r in runs if not (r.business_ref or {}).get("eval_job")][:limit]
        if not runs:
            raise EvalError("该智能体还没有可沉淀为样本的已完成运行")
        dev, hold = [], []
        for r in runs:
            sample = {"id": f"S{r.id}", "source": "线上运行沉淀", "source_run_id": r.id, "title": r.title, "input": _clean_input(r.input), "business_ref": {k: v for k, v in (r.business_ref or {}).items() if k != "root_run_id"}, "expected": extract_expected(s, r), "tags": _tags(r), "note": ""}
            (hold if zlib.crc32(str(r.id).encode()) % 10 >= 7 else dev).append(sample)
        out = []
        for split, items in (("dev", dev), ("holdout", hold)):
            if not items:
                continue
            e = EvalSet(agent_id=agent_id, purpose=purpose, name=f"{name}·{'开发集' if split == 'dev' else '保留集'}", split=split, version=1, samples=items, description=description or f"从 {len(runs)} 个已完成运行沉淀", created_by=user_id)
            s.add(e)
            s.flush()
            out.append(set_dict(e))
        return out


def list_sets(agent_id: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(EvalSet)
        if agent_id:
            q = q.where(EvalSet.agent_id == agent_id)
        return [set_dict(e) for e in s.scalars(q.order_by(EvalSet.id.desc())).all()]


def get_set(set_id: int) -> dict:
    with session_scope() as s:
        e = s.get(EvalSet, set_id)
        if e is None:
            raise EvalError("评测集不存在")
        return set_dict(e, full=True)


def upsert_sample(set_id: int, sample: dict) -> dict:
    """新增或修改样本（专家构造或人工标注），评测集版本号递增。"""
    if not isinstance(sample.get("input"), dict) or not sample["input"]:
        raise EvalError("样本输入必须是非空 JSON 对象")
    with session_scope() as s:
        e = s.get(EvalSet, set_id)
        if e is None:
            raise EvalError("评测集不存在")
        items = list(e.samples or [])
        sid = sample.get("id") or f"E{int(time.time() * 1000) % 10_000_000}"
        row = {"id": sid, "source": sample.get("source") or "专家构造", "source_run_id": sample.get("source_run_id"), "title": sample.get("title") or sid, "input": sample["input"], "business_ref": sample.get("business_ref") or {k: v for k, v in sample["input"].items() if k.endswith("_id")}, "expected": sample.get("expected") or {}, "tags": sample.get("tags") or {}, "note": sample.get("note", "")}
        idx = next((i for i, x in enumerate(items) if x["id"] == sid), None)
        if idx is None:
            items.append(row)
        else:
            items[idx] = {**items[idx], **row}
        e.samples = items
        e.version += 1
        return set_dict(e, full=True)


def delete_sample(set_id: int, sample_id: str) -> dict:
    with session_scope() as s:
        e = s.get(EvalSet, set_id)
        if e is None:
            raise EvalError("评测集不存在")
        e.samples = [x for x in e.samples or [] if x["id"] != sample_id]
        e.version += 1
        return set_dict(e, full=True)


# ---------------------------------------------------------------- 评测任务


def job_dict(j: EvalJob, full: bool = False) -> dict:
    d = {"id": j.id, "eval_set_id": j.eval_set_id, "eval_set_version": j.eval_set_version, "agent_id": j.agent_id, "agent_name": AGENT_NAMES.get(j.agent_id, j.agent_id), "purpose": j.purpose, "label": j.label, "config_version": j.config_version, "status": j.status, "progress": j.progress, "metrics": j.metrics, "baseline_job_id": j.baseline_job_id, "proposal_id": j.proposal_id, "error": j.error, "created_at": iso(j.created_at), "started_at": iso(j.started_at), "ended_at": iso(j.ended_at)}
    if full:
        d["results"] = j.results
        d["candidate_config"] = j.candidate_config
    return d


def create_job(set_id: int, *, label: str, config_version_id: int | None = None, candidate_config: dict | None = None, baseline_job_id: int | None = None, proposal_id: int | None = None, user_id: int | None = None) -> dict:
    with session_scope() as s:
        e = s.get(EvalSet, set_id)
        if e is None:
            raise EvalError("评测集不存在")
        if not e.samples:
            raise EvalError("评测集没有样本")
        if candidate_config is not None:
            cfg_dict, version = agent_config._validate(e.agent_id, candidate_config), "候选配置"
        else:
            if config_version_id:
                c = s.get(AgentConfig, config_version_id)
            else:
                c = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == e.agent_id, AgentConfig.status == "active")).first()
            if c is None or c.agent_id != e.agent_id:
                raise EvalError("配置版本不存在")
            cfg_dict, version = dict(c.config), c.version
        cfg_dict = {k: cfg_dict.get(k) for k in ("pass_score", "max_reflection_rounds", "planner", "plan_confirm", "allow_cloud", "experience_injection", "tool_blacklist", "budget")}
        j = EvalJob(eval_set_id=e.id, eval_set_version=e.version, agent_id=e.agent_id, purpose=e.purpose, label=label or f"{version} @ {e.name}", config_version=version, candidate_config=cfg_dict, status="queued", baseline_job_id=baseline_job_id, proposal_id=proposal_id, created_by=user_id)
        s.add(j)
        s.flush()
        jid = j.id
    eval_runner.enqueue(jid)
    with session_scope() as s:
        return job_dict(s.get(EvalJob, jid))


def _wait_run(run_id: int) -> Run:
    deadline = time.time() + SAMPLE_TIMEOUT_S
    while time.time() < deadline:
        with session_scope() as s:
            r = s.get(Run, run_id)
            if r.status in ("completed", "failed", "cancelled"):
                s.expunge(r)
                return r
        time.sleep(1.0)
    from ..harness.executor import executor

    try:
        executor.control(run_id, "cancel")
    except ValueError:
        pass
    with session_scope() as s:
        r = s.get(Run, run_id)
        s.expunge(r)
        return r


def run_job(job_id: int) -> None:
    from ..harness.executor import executor

    with session_scope() as s:
        j = s.get(EvalJob, job_id)
        if j is None or j.status not in ("queued", "running"):
            return
        e = s.get(EvalSet, j.eval_set_id)
        samples = list(e.samples or [])
        j.status, j.started_at = "running", utcnow()
        agent_id, purpose, conf, label = j.agent_id, j.purpose, dict(j.candidate_config or {}), j.config_version
    results = []
    for i, sm in enumerate(samples):
        started = time.time()
        try:
            rid = executor.create_run(agent_id=agent_id, purpose=purpose, inputs={**sm["input"], "_config": conf, "_config_label": label}, business_ref={**(sm.get("business_ref") or {}), "eval_job": job_id, "eval_sample": sm["id"]}, mode="auto", user_id=None, title=f"评测 #{job_id} · {sm.get('title') or sm['id']}")
            run = _wait_run(rid)
            with session_scope() as s:
                actual = extract_expected(s, run)
                finals = s.scalars(select(Evaluation).where(Evaluation.run_id == rid, Evaluation.kind == "final").order_by(Evaluation.id.desc())).first()
                failed_dims = list(finals.failed_dims or []) if finals else []
                dims = [{"name": d.get("name"), "passed": d.get("passed"), "value": d.get("value")} for d in (finals.dimensions or [])] if finals else []
            cmp = compare(agent_id, purpose, sm.get("expected") or {}, actual) if sm.get("expected") else {"match": None, "detail": "无期望值", "deviation": None}
            from ..harness.timing import duration_seconds

            waited = sum(((c.resolved_at or utcnow()) - c.created_at).total_seconds() for c in _cards(rid))
            results.append({"sample_id": sm["id"], "title": sm.get("title"), "tags": sm.get("tags") or {}, "run_id": rid, "status": run.status, "score": run.eval_score, "passed": run.eval_passed, "reflection_rounds": run.reflection_rounds, "degrade_level": run.degrade_level, "duration_s": duration_seconds(run, wait_s=waited), "cloud_cost": round(float((run.usage or {}).get("cloud_cost", 0) or 0), 4), "cloud_tokens": int((run.usage or {}).get("cloud_tokens_in", 0) or 0) + int((run.usage or {}).get("cloud_tokens_out", 0) or 0), "failed_dims": failed_dims, "dims": dims, "match": cmp["match"], "match_detail": cmp["detail"], "deviation": cmp["deviation"], "error": (run.error or "")[:300] if run.status == "failed" else None})
        except Exception as exc:  # noqa: BLE001
            log.exception("eval sample failed")
            results.append({"sample_id": sm["id"], "title": sm.get("title"), "tags": sm.get("tags") or {}, "run_id": None, "status": "failed", "score": None, "passed": False, "error": str(exc)[:300], "duration_s": round(time.time() - started, 1), "match": None, "failed_dims": []})
        with session_scope() as s:
            j = s.get(EvalJob, job_id)
            j.results = results
            j.progress = round((i + 1) / len(samples), 3)
    metrics = summarize(results)
    with session_scope() as s:
        j = s.get(EvalJob, job_id)
        base = s.get(EvalJob, j.baseline_job_id) if j.baseline_job_id else None
        if base and base.metrics:
            metrics["baseline"] = {k: base.metrics.get(k) for k in ("pass_rate", "avg_score", "match_rate", "avg_duration_s", "avg_rounds", "avg_cost")}
            metrics["delta"] = {k: (round(metrics[k] - base.metrics[k], 4) if metrics.get(k) is not None and base.metrics.get(k) is not None else None) for k in ("pass_rate", "avg_score", "match_rate", "avg_duration_s", "avg_rounds", "avg_cost")}
            base_by = {r["sample_id"]: r for r in base.results or []}
            for r in results:
                b = base_by.get(r["sample_id"])
                if b and r.get("score") is not None and b.get("score") is not None:
                    r["score_delta"] = round(r["score"] - b["score"], 1)
            j.results = results
        j.metrics = metrics
        j.status = "completed"
        j.ended_at = utcnow()
        proposal_id = j.proposal_id
    if proposal_id:
        _on_proposal_eval_done(proposal_id)


def _cards(run_id: int) -> list[HitlCard]:
    with session_scope() as s:
        rows = s.scalars(select(HitlCard).where(HitlCard.run_id == run_id)).all()
        for r in rows:
            s.expunge(r)
        return rows


def summarize(results: list[dict]) -> dict:
    done = [r for r in results if r["status"] == "completed"]
    scores = [r["score"] for r in done if r.get("score") is not None]
    matches = [r["match"] for r in results if r.get("match") is not None]
    fails = Counter(d for r in results for d in r.get("failed_dims") or [])
    by_mode: dict[str, dict] = {}
    for r in results:
        m = (r.get("tags") or {}).get("start_mode") or "未标注"
        b = by_mode.setdefault(m, {"n": 0, "passed": 0, "scores": []})
        b["n"] += 1
        b["passed"] += bool(r.get("passed"))
        if r.get("score") is not None:
            b["scores"].append(r["score"])
    return {
        "samples": len(results),
        "completed": len(done),
        "failed": sum(1 for r in results if r["status"] != "completed"),
        "pass_rate": round(sum(1 for r in results if r.get("passed")) / len(results), 4) if results else None,
        "avg_score": round(statistics.mean(scores), 2) if scores else None,
        "match_rate": round(sum(1 for m in matches if m) / len(matches), 4) if matches else None,
        "avg_rounds": round(statistics.mean([r.get("reflection_rounds") or 0 for r in done]), 2) if done else None,
        "avg_duration_s": round(statistics.mean([r.get("duration_s") or 0 for r in done]), 1) if done else None,
        "avg_cost": round(statistics.mean([r.get("cloud_cost") or 0 for r in done]), 4) if done else None,
        "degraded": sum(1 for r in done if (r.get("degrade_level") or "D0") >= "D2"),
        "failure_attribution": [{"dim": k, "count": v} for k, v in fails.most_common(8)],
        "by_start_mode": {k: {"n": v["n"], "pass_rate": round(v["passed"] / v["n"], 3), "avg_score": round(statistics.mean(v["scores"]), 1) if v["scores"] else None} for k, v in by_mode.items()},
    }


def list_jobs(agent_id: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(EvalJob)
        if agent_id:
            q = q.where(EvalJob.agent_id == agent_id)
        return [job_dict(j) for j in s.scalars(q.order_by(EvalJob.id.desc()).limit(100)).all()]


def get_job(job_id: int) -> dict:
    with session_scope() as s:
        j = s.get(EvalJob, job_id)
        if j is None:
            raise EvalError("评测任务不存在")
        return job_dict(j, full=True)


class EvalRunner:
    """评测任务串行执行（每个样本仍经运行执行器排队），避免评测挤占业务运行。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[int] = []
        self._thread: threading.Thread | None = None

    def enqueue(self, job_id: int) -> None:
        with self._lock:
            self._pending.append(job_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="eval-runner", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._thread = None
                    return
                jid = self._pending.pop(0)
            try:
                run_job(jid)
            except Exception as exc:  # noqa: BLE001
                log.exception("eval job %s failed", jid)
                with session_scope() as s:
                    j = s.get(EvalJob, jid)
                    if j:
                        j.status, j.error, j.ended_at = "failed", str(exc)[:500], utcnow()

    def recover(self) -> None:
        with session_scope() as s:
            ids = [j.id for j in s.scalars(select(EvalJob).where(EvalJob.status.in_(["queued", "running"]))).all()]
        for jid in ids:
            self.enqueue(jid)


eval_runner = EvalRunner()


# ---------------------------------------------------------------- 线上评估


def online(agent_id: str | None = None, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    with session_scope() as s:
        q = select(Run).where(Run.parent_run_id.is_(None), Run.created_at >= since)
        if agent_id:
            q = q.where(Run.agent_id == agent_id)
        runs = [r for r in s.scalars(q).all() if not (r.business_ref or {}).get("eval_job")]
        run_ids = [r.id for r in runs]
        cards = s.scalars(select(HitlCard).where(HitlCard.run_id.in_(run_ids), HitlCard.status != "open")).all() if run_ids else []
        posthoc = s.scalars(select(Evaluation).where(Evaluation.kind == "posthoc", Evaluation.run_id.in_(run_ids))).all() if run_ids else []
        alerts = s.scalars(select(Alert).where(Alert.created_at >= since)).all()
        claims = s.scalars(select(ClaimCase).where(ClaimCase.created_at >= since)).all()
        revs = s.scalars(select(Artifact).where(Artifact.created_at >= since, Artifact.kind.in_(["risk_report", "clause_plan", "pricing_plan", "uw_report", "claim_result", "risk_mgmt_report"]))).all()
    by_day: dict[str, dict] = {}
    for r in runs:
        d = (r.created_at + timedelta(hours=8)).strftime("%m-%d")
        b = by_day.setdefault(d, {"day": d, "runs": 0, "scores": [], "passed": 0, "reflected": 0})
        b["runs"] += 1
        if r.eval_score is not None:
            b["scores"].append(r.eval_score)
        b["passed"] += bool(r.eval_passed)
        b["reflected"] += bool(r.reflection_rounds)
    finals = [c for c in cards if (c.context or {}).get("final") or c.card_type in FINAL_CARDS]
    modified = [c for c in finals if (c.resolution or {}).get("action") == "reject" or (c.resolution or {}).get("edits") or ((c.resolution or {}).get("action") or "").startswith("option")]
    field_counts = Counter()
    for a in revs:
        for rv in a.revisions or []:
            path = str(rv.get("path") or rv.get("op") or "")
            field_counts[".".join(p for p in path.split(".") if not p.isdigit())[:40]] += 1
    handled = [a for a in alerts if a.status in ("closed", "false_positive", "escalated")]
    orange = [a for a in handled if a.level in ("red", "orange")]
    precision = sum(1 for a in orange if a.status != "false_positive") / len(orange) if orange else None
    devs = [ap.get("deviation") for c in claims for ap in (c.approvals or []) if ap.get("deviation") is not None]
    return {
        "days": days,
        "agent_id": agent_id,
        "runs": len(runs),
        "trend": [{"day": b["day"], "runs": b["runs"], "avg_score": round(statistics.mean(b["scores"]), 1) if b["scores"] else None, "pass_rate": round(b["passed"] / b["runs"], 3), "reflection_rate": round(b["reflected"] / b["runs"], 3)} for b in sorted(by_day.values(), key=lambda x: x["day"])],
        "human_modification_rate": round(len(modified) / len(finals), 4) if finals else None,
        "final_cards": len(finals),
        "modified_cards": len(modified),
        "revision_fields": [{"field": k or "（结构编辑）", "count": v} for k, v in field_counts.most_common(10)],
        "posthoc": [{"run_id": p.run_id, "target": p.target, "score": p.score, "passed": p.passed, "dimensions": p.dimensions, "ts": iso(p.ts)} for p in posthoc][-30:],
        "business": {"alert_precision": round(precision, 4) if precision is not None else None, "alerts_handled": len(handled), "claim_payout_deviation": round(statistics.mean(devs), 4) if devs else None},
    }


def backfill_posthoc() -> int:
    """事后评估回填：订阅业务结局（出险、赔付、预警处置），回填到对应运行。"""
    n = 0
    with session_scope() as s:
        done = {(e.run_id, e.target) for e in s.scalars(select(Evaluation).where(Evaluation.kind == "posthoc")).all()}
        for app in s.scalars(select(Application).where(Application.status == "accepted", Application.uw_run_id.is_not(None))).all():
            pol = s.scalars(select(Policy).where(Policy.application_id == app.id)).first()
            if pol is None:
                continue
            claims = s.scalars(select(ClaimCase).where(ClaimCase.policy_id == pol.id)).all()
            target = "承保后出险回溯"
            if not claims or (app.uw_run_id, target) in done:
                continue
            paid = sum(c.approved_amount or 0 for c in claims if c.status == "closed")
            decision = (app.decision or {}).get("decision")
            ok = not (decision == "standard" and paid > pol.premium * 3)
            s.add(Evaluation(run_id=app.uw_run_id, kind="posthoc", target=target, rubric_version="posthoc-1", dimensions=[{"key": "claims", "name": "承保后出险", "value": len(claims), "passed": ok}, {"key": "paid", "name": "已决赔款", "value": round(paid, 2), "passed": ok}], score=100.0 if ok else 40.0, passed=ok, failed_dims=[] if ok else ["承保后出险"]))
            n += 1
        for a in s.scalars(select(Alert).where(Alert.status.in_(["false_positive", "escalated", "closed"]), Alert.run_id.is_not(None))).all():
            target = "预警处置结局回溯"
            if (a.run_id, target) in done:
                continue
            ok = a.status != "false_positive"
            s.add(Evaluation(run_id=a.run_id, kind="posthoc", target=target, rubric_version="posthoc-1", dimensions=[{"key": "outcome", "name": "处置结局", "value": a.status, "passed": ok}], score=100.0 if ok else 30.0, passed=ok, failed_dims=[] if ok else ["误报"]))
            n += 1
        for c in s.scalars(select(ClaimCase).where(ClaimCase.status.in_(["approved", "closed"]), ClaimCase.run_id.is_not(None))).all():
            target = "赔付审批偏差回溯"
            dev = next((ap.get("deviation") for ap in reversed(c.approvals or []) if ap.get("deviation") is not None), None)
            if dev is None or (c.run_id, target) in done:
                continue
            ok = dev <= 0.1
            s.add(Evaluation(run_id=c.run_id, kind="posthoc", target=target, rubric_version="posthoc-1", dimensions=[{"key": "deviation", "name": "审批金额与建议偏差", "value": dev, "passed": ok}], score=round(max(0.0, 100 - dev * 300), 1), passed=ok, failed_dims=[] if ok else ["赔付偏差"]))
            n += 1
    return n


# ---------------------------------------------------------------- 进化分析器与改进提案


def proposal_dict(p: EvolutionProposal) -> dict:
    return {"id": p.id, "proposal_no": p.proposal_no, "agent_id": p.agent_id, "agent_name": AGENT_NAMES.get(p.agent_id, p.agent_id), "kind": p.kind, "kind_label": {"config": "配置调整", "experience": "经验治理"}.get(p.kind, p.kind), "title": p.title, "rationale": p.rationale, "evidence": p.evidence, "change": p.change, "status": p.status, "eval_job_id": p.eval_job_id, "baseline_job_id": p.baseline_job_id, "eval_summary": p.eval_summary, "approval_id": p.approval_id, "canary": p.canary, "released_version": p.released_version, "previous_version": p.previous_version, "history": p.history, "created_at": iso(p.created_at), "updated_at": iso(p.updated_at)}


def _hist(p: EvolutionProposal, text: str, by: str = "系统") -> None:
    p.history = list(p.history or []) + [{"at": iso(utcnow()), "text": text, "by": by}]


def _proposal_no(s) -> str:
    prefix = cfg("params.dicts")["numbering"].get("evolution_proposal", "JH")
    n = (s.scalar(select(func.count(EvolutionProposal.id))) or 0) + 1
    return f"{prefix}{utcnow():%Y%m%d}{n:04d}"


def analyze(agent_id: str | None = None, window: int = 40) -> list[dict]:
    """进化分析器：基于最近运行的评估、反思、人审修改与经验命中统计生成改进提案（同类未结提案不重复生成）。"""
    created = []
    agents = [agent_id] if agent_id else list(all_profiles())
    for aid in agents:
        with session_scope() as s:
            runs = [r for r in s.scalars(select(Run).where(Run.agent_id == aid, Run.parent_run_id.is_(None), Run.status.in_(["completed", "failed"])).order_by(Run.id.desc()).limit(window)).all() if not (r.business_ref or {}).get("eval_job")]
            if len(runs) < 3:
                continue
            active = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == aid, AgentConfig.status == "active")).first()
            conf = dict(active.config) if active else agent_config.base_config(aid)
            open_titles = {p.title for p in s.scalars(select(EvolutionProposal).where(EvolutionProposal.agent_id == aid, EvolutionProposal.status.notin_(["released", "rejected", "rolled_back"]))).all()}
            ids = [r.id for r in runs]
            refl = s.scalars(select(Reflection).where(Reflection.run_id.in_(ids))).all()
            finals = s.scalars(select(Evaluation).where(Evaluation.run_id.in_(ids), Evaluation.kind == "final")).all()
            exps = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "experience", MemoryEntry.agent_id == aid)).all()
            cards = s.scalars(select(HitlCard).where(HitlCard.run_id.in_(ids), HitlCard.status != "open")).all()
            proposals: list[dict] = []
            done = [r for r in runs if r.status == "completed"]
            pass_rate = sum(1 for r in done if r.eval_passed) / len(done) if done else 0
            unresolved = sum(1 for x in refl if x.outcome == "unresolved")
            maxed = sum(1 for r in done if r.reflection_rounds >= 2 * int(conf.get("max_reflection_rounds") or 3) - 1)
            if done and pass_rate < 0.85 and unresolved >= 2 and int(conf.get("max_reflection_rounds") or 3) < 5:
                proposals.append({"kind": "config", "title": f"最终评估反思上限由 {conf['max_reflection_rounds']} 轮提高到 {int(conf['max_reflection_rounds']) + 1} 轮", "rationale": f"最近 {len(done)} 次运行达标率 {pass_rate:.0%}，有 {unresolved} 次反思以未解决结束、{maxed} 次接近反思上限；增加一轮反思可给修正待办留出空间。", "evidence": {"runs": len(done), "pass_rate": round(pass_rate, 3), "unresolved_reflections": unresolved}, "change": {"config": {"max_reflection_rounds": int(conf["max_reflection_rounds"]) + 1}}})
            dim_fail = Counter(d for f in finals for d in f.failed_dims or [])
            policy_fail = sum(1 for r in runs if "规划模型" in (r.error or ""))
            if conf.get("planner") == "auto" and policy_fail >= 2:
                proposals.append({"kind": "config", "title": "改为固定按技能规程执行（关闭大模型规划）", "rationale": f"最近有 {policy_fail} 次运行因规划模型异常失败或降级，固定规程可提高稳定性。", "evidence": {"policy_failures": policy_fail}, "change": {"config": {"planner": "procedure"}}})
            finals_c = [c for c in cards if (c.context or {}).get("final") or c.card_type in FINAL_CARDS]
            rejected = [c for c in finals_c if (c.resolution or {}).get("action") == "reject"]
            cold = [r for r in runs if r.start_mode == "cold"]
            if finals_c and len(rejected) / len(finals_c) >= 0.3 and not conf.get("plan_confirm") and cold:
                proposals.append({"kind": "config", "title": "冷启动运行开启计划人工确认", "rationale": f"结果确认被驳回比例 {len(rejected) / len(finals_c):.0%}，冷启动运行 {len(cold)} 次；在执行前确认计划可减少返工。", "evidence": {"final_cards": len(finals_c), "rejected": len(rejected)}, "change": {"config": {"plan_confirm": True}}})
            promote = [e for e in exps if e.status == "trial" and e.hits >= 3 and e.successes / max(1, e.hits) >= 0.7]
            retire = [e for e in exps if e.status == "active" and e.hits >= 5 and e.successes / max(1, e.hits) < 0.4]
            if promote:
                proposals.append({"kind": "experience", "title": f"{len(promote)} 条试用经验转为生效", "rationale": "试用期内命中次数与成功率达到生效标准（命中 ≥ 3 次、成功率 ≥ 70%）。", "evidence": {"entries": [{"id": e.id, "title": e.title, "hits": e.hits, "successes": e.successes} for e in promote]}, "change": {"experiences": [{"id": e.id, "status": "active"} for e in promote]}})
            if retire:
                proposals.append({"kind": "experience", "title": f"{len(retire)} 条低效经验退役", "rationale": "生效经验命中后成功率低于 40%，继续注入可能误导规划。", "evidence": {"entries": [{"id": e.id, "title": e.title, "hits": e.hits, "successes": e.successes} for e in retire]}, "change": {"experiences": [{"id": e.id, "status": "retired"} for e in retire]}})
            if dim_fail:
                top, cnt = dim_fail.most_common(1)[0]
                if cnt >= max(3, len(finals) // 3) and float(conf.get("pass_score") or 75) > 70 and not any("达标线" in t for t in open_titles):
                    proposals.append({"kind": "config", "title": f"维度「{top}」频繁未通过：增加一轮反思并保持达标线", "rationale": f"最近 {len(finals)} 次最终评估中「{top}」未通过 {cnt} 次。建议先通过增加反思轮次修正，不降低达标线。", "evidence": {"dimension": top, "failures": cnt, "evaluations": len(finals)}, "change": {"config": {"max_reflection_rounds": min(6, int(conf.get("max_reflection_rounds") or 3) + 1)}}})
            for pr in proposals:
                if pr["title"] in open_titles:
                    continue
                p = EvolutionProposal(proposal_no=_proposal_no(s), agent_id=aid, kind=pr["kind"], title=pr["title"], rationale=pr["rationale"], evidence=pr["evidence"], change=pr["change"], status="draft", history=[{"at": iso(utcnow()), "text": "进化分析器生成提案", "by": "进化分析器"}])
                s.add(p)
                s.flush()
                open_titles.add(pr["title"])
                created.append(proposal_dict(p))
    return created


def list_proposals(agent_id: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(EvolutionProposal)
        if agent_id:
            q = q.where(EvolutionProposal.agent_id == agent_id)
        return [proposal_dict(p) for p in s.scalars(q.order_by(EvolutionProposal.id.desc())).all()]


def get_proposal(pid: int) -> dict:
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None:
            raise EvalError("提案不存在")
        return proposal_dict(p)


def evaluate_proposal(pid: int, set_id: int | None, user: dict) -> dict:
    """提案评测：配置类提案在开发集上分别评测当前生效配置（基线）与候选配置；经验类提案以命中统计为证据。"""
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None:
            raise EvalError("提案不存在")
        if p.status not in ("draft", "evaluated"):
            raise EvalError("提案当前状态不能评测")
        agent = p.agent_id
        kind, change = p.kind, dict(p.change or {})
        if kind == "experience":
            p.status = "evaluated"
            p.eval_summary = {"method": "命中统计", "entries": len(change.get("experiences", [])), "conclusion": "经验命中与成功率满足治理规则"}
            _hist(p, "以经验命中统计完成评测", user["display_name"])
            return proposal_dict(p)
        if set_id is None:
            es = s.scalars(select(EvalSet).where(EvalSet.agent_id == agent, EvalSet.split == "dev").order_by(EvalSet.id.desc())).first()
            if es is None:
                raise EvalError("该智能体还没有开发集，请先在评测集管理中沉淀样本")
            set_id = es.id
        active = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent, AgentConfig.status == "active")).first()
        base_conf = dict(active.config) if active else agent_config.base_config(agent)
    candidate = {**{k: base_conf.get(k) for k in ("pass_score", "max_reflection_rounds", "planner", "plan_confirm", "allow_cloud", "experience_injection", "tool_blacklist", "budget")}, **(change.get("config") or {})}
    base_job = create_job(set_id, label=f"提案 {pid} 基线（{active.version if active else '默认'}）", config_version_id=active.id if active else None, proposal_id=None, user_id=user["id"])
    cand_job = create_job(set_id, label=f"提案 {pid} 候选配置", candidate_config=candidate, baseline_job_id=base_job["id"], proposal_id=pid, user_id=user["id"])
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        p.status = "evaluating"
        p.eval_job_id, p.baseline_job_id = cand_job["id"], base_job["id"]
        _hist(p, f"发起评测：基线任务 #{base_job['id']}，候选任务 #{cand_job['id']}", user["display_name"])
        return proposal_dict(p)


def _on_proposal_eval_done(pid: int) -> None:
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        j = s.get(EvalJob, p.eval_job_id) if p and p.eval_job_id else None
        if p is None or j is None:
            return
        m = j.metrics or {}
        delta = m.get("delta") or {}
        better = (delta.get("avg_score") or 0) >= -1 and (delta.get("pass_rate") or 0) >= -0.05
        p.eval_summary = {"method": "评测集对比", "candidate": {k: m.get(k) for k in ("pass_rate", "avg_score", "match_rate", "avg_rounds", "avg_duration_s", "avg_cost")}, "baseline": m.get("baseline"), "delta": delta, "recommend": better, "conclusion": "候选配置不劣于基线，建议进入业务确认" if better else "候选配置劣于基线，不建议发布"}
        p.status = "evaluated"
        _hist(p, p.eval_summary["conclusion"])


def submit_proposal(pid: int, user: dict) -> dict:
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None or p.status != "evaluated":
            raise EvalError("提案评测完成后才能提交发布审批")
        title, agent, summary = p.title, p.agent_id, dict(p.eval_summary or {})
    req = approvals.start("evolution_release", object_type="evolution_proposal", object_id=pid, title=f"自进化发布：{AGENT_NAMES.get(agent, agent)} · {title}", summary={"提案": title, "智能体": AGENT_NAMES.get(agent, agent), "评测结论": summary.get("conclusion"), "变化": summary.get("delta")}, applicant=user)
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        p.status = "pending_approval"
        p.approval_id = req["id"]
        _hist(p, f"提交发布审批 {req['request_no']}", user["display_name"])
        return proposal_dict(p)


def _approved(req: dict, actor: dict | None) -> None:
    pid = int(req["object_id"])
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None or p.status != "pending_approval":
            return
        kind, change, agent = p.kind, dict(p.change or {}), p.agent_id
    if kind == "experience":
        with session_scope() as s:
            for e in change.get("experiences", []):
                m = s.get(MemoryEntry, e["id"])
                if m:
                    m.status = e["status"]
                    m.updated_at = utcnow()
            p = s.get(EvolutionProposal, pid)
            p.status = "released"
            _hist(p, "审批通过，经验状态已更新", actor["display_name"] if actor else "系统")
        return
    with session_scope() as s:
        active = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent, AgentConfig.status == "active")).first()
        base = dict(active.config) if active else agent_config.base_config(agent)
        prev = active.version if active else None
    version = agent_config.create_version(agent, {**base, **(change.get("config") or {})}, f"改进提案 {pid}", actor["display_name"] if actor else "系统", proposal_id=pid)
    agent_config.start_canary(version["id"], 0.2)
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        p.status = "canary"
        p.released_version, p.previous_version = version["version"], prev
        p.canary = {"version_id": version["id"], "ratio": 0.2, "started_at": iso(utcnow())}
        _hist(p, f"审批通过，配置 {version['version']} 以 20% 比例灰度", actor["display_name"] if actor else "系统")
        notify(s, kind="system", title=f"自进化灰度开始：{AGENT_NAMES.get(agent, agent)}", body=p.title, link="/agents?tab=evolution", role="admin")


def _rejected(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        p = s.get(EvolutionProposal, int(req["object_id"]))
        if p is not None and p.status == "pending_approval":
            p.status = "evaluated" if (req.get("result") or {}).get("action") == "withdraw" else "rejected"
            _hist(p, f"审批{'撤回' if p.status == 'evaluated' else '驳回'}：{(req.get('result') or {}).get('comment') or ''}")


def canary_metrics(pid: int) -> dict:
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None or not p.canary:
            return {}
        started = datetime.fromisoformat(p.canary["started_at"].rstrip("Z"))
        vid = p.canary["version_id"]
        runs = [r for r in s.scalars(select(Run).where(Run.agent_id == p.agent_id, Run.parent_run_id.is_(None), Run.created_at >= started, Run.status.in_(["completed", "failed"]))).all() if not (r.business_ref or {}).get("eval_job")]
    canary = [r for r in runs if (r.input or {}).get("_config_id") == vid]
    control = [r for r in runs if (r.input or {}).get("_config_id") != vid]

    def stat(rs: list[Run]) -> dict:
        sc = [r.eval_score for r in rs if r.eval_score is not None]
        return {"runs": len(rs), "avg_score": round(statistics.mean(sc), 2) if sc else None, "pass_rate": round(sum(1 for r in rs if r.eval_passed) / len(rs), 3) if rs else None, "failed": sum(1 for r in rs if r.status == "failed")}

    return {"canary": stat(canary), "control": stat(control)}


def check_canaries() -> list[dict]:
    """灰度检查：灰度运行 ≥ 3 次后，若平均分低于对照组 5 分以上或失败率更高则自动回滚。"""
    out = []
    with session_scope() as s:
        ids = [p.id for p in s.scalars(select(EvolutionProposal).where(EvolutionProposal.status == "canary")).all()]
    for pid in ids:
        m = canary_metrics(pid)
        c, k = m.get("canary") or {}, m.get("control") or {}
        with session_scope() as s:
            p = s.get(EvolutionProposal, pid)
            p.canary = {**(p.canary or {}), "metrics": m, "checked_at": iso(utcnow())}
        if (c.get("runs") or 0) < 3:
            continue
        worse = (c.get("avg_score") is not None and k.get("avg_score") is not None and c["avg_score"] < k["avg_score"] - 5) or (c.get("failed", 0) / max(1, c["runs"]) > k.get("failed", 0) / max(1, k.get("runs") or 1) + 0.2)
        if worse:
            rollback_proposal(pid, None, "灰度指标劣于对照组，自动回滚")
            out.append({"proposal_id": pid, "action": "rolled_back"})
    return out


def release_proposal(pid: int, user: dict) -> dict:
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None or p.status != "canary":
            raise EvalError("只有灰度中的提案可以全量发布")
        vid = (p.canary or {}).get("version_id")
    agent_config.activate(vid)
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        p.status = "released"
        _hist(p, f"全量发布配置 {p.released_version}", user["display_name"])
        return proposal_dict(p)


def rollback_proposal(pid: int, user: dict | None, reason: str) -> dict:
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        if p is None or p.status not in ("canary", "released"):
            raise EvalError("只有灰度中或已发布的提案可以回滚")
        status, vid, agent = p.status, (p.canary or {}).get("version_id"), p.agent_id
    if status == "canary":
        agent_config.stop_canary(vid)
    else:
        agent_config.rollback(agent)
    with session_scope() as s:
        p = s.get(EvolutionProposal, pid)
        p.status = "rolled_back"
        _hist(p, f"回滚：{reason}", user["display_name"] if user else "系统")
        notify(s, kind="system", title=f"自进化回滚：{AGENT_NAMES.get(agent, agent)}", body=f"{p.title}（{reason}）", link="/agents?tab=evolution", level="warning", role="admin")
        return proposal_dict(p)


def register_handlers() -> None:
    approvals.register("evolution_release", _approved, _rejected)


_ = json
