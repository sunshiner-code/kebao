"""产品设计辅助：条款编辑器（增删移条款、局部再生成、合规复检、修订留痕、重新冻结）、条款模板库、定价任务配置。"""

from __future__ import annotations

import copy
import difflib
import json
from datetime import datetime

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..jsonutil import jsonable
from ..knowledge.catalog import LINES
from ..knowledge.clause_library import INNOVATION_SKELETONS, INNOVATION_SPECS
from ..models import Artifact, ClauseModule, DesignProject
from . import platform_tasks  # noqa: F401
from .config_center import cfg

PRICING_METHODS = {"PM1": "广义线性模型", "PM2": "梯度提升树 + Shapley", "PM3": "信度加权", "PM4": "风险成本加成法"}
DEFAULT_TARGETS = {"loss_ratio": [0.45, 0.68], "expense_ratio": 0.25, "profit_ratio": 0.05, "combined_cap": 0.95, "capital_cost": 0.10, "max_loss_prob": 0.25, "min_coverage": 0.85}
MODULE_KINDS = {"general": "总则", "subject": "保险标的", "liability": "保险责任", "exclusion": "责任免除", "amount": "保险金额与免赔", "period": "保险期间", "obligation": "义务", "settlement": "赔偿处理", "dispute": "争议处理", "definition": "释义", "extension": "扩展条款", "special": "特别约定"}


class DesignToolError(ValueError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _latest(s, key: str) -> Artifact:
    art = s.scalars(select(Artifact).where(Artifact.artifact_key == key).order_by(Artifact.version.desc())).first()
    if art is None:
        raise DesignToolError("条款方案不存在")
    if art.kind != "clause_plan":
        raise DesignToolError("只能编辑条款方案")
    return art


def _new_version(s, cur: Artifact, data: dict, revision: dict, user_id: int) -> Artifact:
    new = Artifact(artifact_key=cur.artifact_key, kind=cur.kind, version=cur.version + 1, title=cur.title, project_id=cur.project_id, ref_type=cur.ref_type, ref_id=cur.ref_id, data=jsonable(data), upstream=cur.upstream, run_id=cur.run_id, revisions=list(cur.revisions or []) + [revision], created_by=user_id)
    s.add(new)
    s.flush()
    return new


def editor_view(key: str, version: int | None = None) -> dict:
    with session_scope() as s:
        q = select(Artifact).where(Artifact.artifact_key == key)
        art = s.scalars(q.where(Artifact.version == version) if version else q.order_by(Artifact.version.desc())).first()
        if art is None:
            raise DesignToolError("条款方案不存在")
        versions = [{"version": a.version, "status": a.status, "created_at": iso(a.created_at), "revisions": len(a.revisions or [])} for a in s.scalars(select(Artifact).where(Artifact.artifact_key == key).order_by(Artifact.version)).all()]
        latest = max(v["version"] for v in versions)
        project = s.get(DesignProject, art.project_id) if art.project_id else None
        node = ((project.pipeline or {}).get("nodes", {}).get("clause") if project else None) or {}
        data = art.data or {}
        return {
            "artifact_key": key,
            "version": art.version,
            "latest_version": latest,
            "status": art.status,
            "title": art.title,
            "project_id": art.project_id,
            "pipeline_version": node.get("version"),
            "docs": data.get("docs", []),
            "compliance": data.get("compliance", {}),
            "recheck": data.get("recheck"),
            "revisions": list(reversed(art.revisions or []))[:80],
            "versions": versions,
            "editable": art.version == latest,
            "chapter_kinds": MODULE_KINDS,
        }


def _locate(data: dict, doc_index: int, chapter_key: str) -> tuple[dict, dict]:
    docs = data.get("docs", [])
    if not 0 <= doc_index < len(docs):
        raise DesignToolError("条款文件不存在")
    doc = docs[doc_index]
    chapter = next((c for c in doc["chapters"] if c["key"] == chapter_key), None)
    if chapter is None:
        raise DesignToolError("章节不存在")
    return doc, chapter


def edit(key: str, base_version: int, op: dict, reason: str, user: dict) -> dict:
    """结构化编辑：update 改写条款、add 新增条款（可引用模板）、remove 删除条款、move 调整顺序；每次编辑生成新版本并留痕。"""
    from ..agents.clause_design import library as lib

    if not reason.strip():
        raise DesignToolError("请填写修订原因")
    with session_scope() as s:
        cur = _latest(s, key)
        if cur.version != base_version:
            raise DesignToolError(f"条款方案已更新到 V{cur.version}，请刷新后再修改")
        data = copy.deepcopy(cur.data or {})
        doc, chapter = _locate(data, int(op["doc_index"]), op["chapter_key"])
        arts = chapter["articles"]
        kind = op["op"]
        before, after = None, None
        if kind == "update":
            i = int(op["article_index"])
            if not 0 <= i < len(arts):
                raise DesignToolError("条款不存在")
            before = {"title": arts[i]["title"], "text": arts[i]["text"]}
            text = str(op.get("text") or "").strip()
            if len(text) < 6:
                raise DesignToolError("条款正文过短")
            arts[i] = {**arts[i], "title": str(op.get("title") or arts[i]["title"]).strip(), "text": text, "origin": "human", "bold": bool(op.get("bold", arts[i].get("bold")))}
            after = {"title": arts[i]["title"], "text": arts[i]["text"]}
        elif kind == "add":
            module = None
            if op.get("module_code"):
                m = s.scalars(select(ClauseModule).where(ClauseModule.code == op["module_code"])).first()
                if m is None:
                    raise DesignToolError("条款模板不存在")
                module = m.code
                title, text = m.title, m.text
            else:
                title, text = str(op.get("title") or "").strip(), str(op.get("text") or "").strip()
            if not title or len(text) < 6:
                raise DesignToolError("请填写条款标题与正文")
            art = lib._article(title, text, module=module, origin="human", bold=chapter["key"] == "exclusion")
            pos = int(op.get("position", len(arts)))
            arts.insert(max(0, min(pos, len(arts))), art)
            after = {"title": title, "text": text, "module": module}
        elif kind == "remove":
            i = int(op["article_index"])
            if not 0 <= i < len(arts):
                raise DesignToolError("条款不存在")
            if chapter["key"] in lib.REQUIRED and len(arts) == 1:
                raise DesignToolError(f"「{chapter['title']}」是必备章节，至少保留一条")
            removed = arts.pop(i)
            before = {"title": removed["title"], "text": removed["text"]}
        elif kind == "move":
            i, j = int(op["article_index"]), int(op["to_index"])
            if not (0 <= i < len(arts) and 0 <= j < len(arts)):
                raise DesignToolError("位置无效")
            arts.insert(j, arts.pop(i))
            before, after = {"index": i}, {"index": j}
        else:
            raise DesignToolError("未知的编辑操作")
        lib.number(doc)
        data.setdefault("recheck", None)
        data["recheck"] = {**(data.get("recheck") or {}), "stale": True}
        revision = {"op": kind, "doc": doc["name"], "chapter": chapter["title"], "path": f"docs.{op['doc_index']}.{chapter['key']}", "old": before, "new": after, "reason": reason, "by": user["id"], "by_name": user["display_name"], "at": iso(utcnow())}
        new = _new_version(s, cur, data, revision, user["id"])
        version = new.version
    from ..harness.memory import human_revision_experience

    human_revision_experience("AG-CD", "SK-02", f"docs.{chapter['key']}", reason, None)
    return {"version": version, "revision": revision}


def regenerate(key: str, doc_index: int, chapter_key: str, article_index: int, instruction: str, user: dict) -> dict:
    """局部再生成：只重写选中的一条条款，结合同险种条款模块与合规规则库；返回候选文本，由用户确认后保存。"""
    from ..agents.clause_design import library as lib
    from ..llm.gateway import ModelUnavailable, model_gateway

    if not instruction.strip():
        raise DesignToolError("请填写修改要求")
    with session_scope() as s:
        cur = _latest(s, key)
        data = copy.deepcopy(cur.data or {})
        version = cur.version
    doc, chapter = _locate(data, doc_index, chapter_key)
    if not 0 <= article_index < len(chapter["articles"]):
        raise DesignToolError("条款不存在")
    art = chapter["articles"][article_index]
    kind_map = {"obligation_insurer": "obligation", "obligation_insured": "obligation"}
    want = kind_map.get(chapter_key, chapter_key)
    modules = []
    for line in doc.get("lines", []):
        modules += [m for m in lib.modules_by_line(line) if m["kind"] == want]
    rules = cfg("compliance.rules")
    try:
        out = model_gateway.invoke("cd.regenerate_article", {"doc_name": doc["name"], "lines": [LINES.get(l, {}).get("name", l) for l in doc.get("lines", [])], "chapter_key": chapter_key, "chapter_title": chapter["title"], "article": {"title": art["title"], "text": art["text"]}, "instruction": instruction, "related_modules": [{"code": m["code"], "title": m["title"], "text": m["text"]} for m in modules[:6]], "forbidden_words": rules["forbidden_words"], "ambiguous_words": rules["ambiguous_words"]}, data_level=1)
    except ModelUnavailable as exc:
        raise DesignToolError(f"再生成失败：{exc}") from exc
    trial = copy.deepcopy(doc)
    tch = next(c for c in trial["chapters"] if c["key"] == chapter_key)
    tch["articles"][article_index] = {**art, "title": out["title"], "text": out["text"]}
    before = {c["code"]: c["passed"] for c in lib.compliance_checks(doc)}
    after_checks = lib.compliance_checks(trial)
    changed = [{"code": c["code"], "name": c["name"], "before": before.get(c["code"]), "after": c["passed"]} for c in after_checks if before.get(c["code"]) != c["passed"]]
    diff = [{"op": tag, "a": art["text"][i1:i2], "b": out["text"][j1:j2]} for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, art["text"], out["text"]).get_opcodes()]
    return {"base_version": version, "original": {"title": art["title"], "text": art["text"]}, "candidate": out, "diff": diff, "compliance_changes": changed, "compliance_ok": all(c["passed"] for c in after_checks)}


def recheck(key: str, user: dict) -> dict:
    """合规复检：对当前版本逐份条款执行强制规则 C01–C16 与跨险种一致性检查，并由大模型按法规库复审，结果写入新版本。"""
    from ..agents.clause_design import library as lib
    from ..agents.clause_design.tools import _regulations
    from ..llm.gateway import ModelUnavailable, model_gateway

    with session_scope() as s:
        cur = _latest(s, key)
        data = copy.deepcopy(cur.data or {})
        base = cur.version
    docs = data.get("docs", [])
    rules = [{"doc_id": d["doc_id"], "name": d["name"], "checks": lib.compliance_checks(d), "passed": False} for d in docs]
    for r in rules:
        r["passed"] = all(c["passed"] for c in r["checks"])
    consistency = lib.consistency_issues(copy.deepcopy(docs))
    try:
        review = model_gateway.invoke("cd.compliance_review", {"docs": [{"name": d["name"], "chapters": d["chapters"]} for d in docs], "regulations": _regulations()}, data_level=1)
        issues = review.get("issues", [])
    except ModelUnavailable as exc:
        issues = [{"line": "全部", "article": "", "severity": "medium", "issue": f"大模型合规复审不可用：{exc}", "basis": "", "suggestion": "以强制规则结果为准"}]
    failed = sum(1 for r in rules for c in r["checks"] if not c["passed"])
    result = {"at": iso(utcnow()), "by": user["display_name"], "base_version": base, "rule_failures": failed, "high_issues": sum(1 for i in issues if i.get("severity") == "high"), "consistency_open": sum(1 for i in consistency if not i.get("resolved")), "stale": False}
    data["compliance"] = {**(data.get("compliance") or {}), "rules": rules, "issues": issues}
    data["consistency"] = consistency
    data["recheck"] = result
    with session_scope() as s:
        cur = _latest(s, key)
        if cur.version != base:
            raise DesignToolError("复检期间条款方案已被修改，请重新复检")
        new = _new_version(s, cur, data, {"op": "recheck", "reason": "合规复检", "new": {k: result[k] for k in ("rule_failures", "high_issues", "consistency_open")}, "by": user["id"], "by_name": user["display_name"], "at": result["at"]}, user["id"])
        version = new.version
    return {"version": version, **result, "rules": rules, "issues": issues, "consistency": consistency}


def refreeze(key: str, version: int, user: dict) -> dict:
    """编辑后重新冻结：更新设计项目的条款节点；若定价已基于旧版本完成，标记定价需重新确认。"""
    from ..agents.common import freeze_artifact
    from .pipeline import _log

    with session_scope() as s:
        art = s.scalars(select(Artifact).where(Artifact.artifact_key == key, Artifact.version == version)).first()
        if art is None:
            raise DesignToolError("版本不存在")
        rc = (art.data or {}).get("recheck") or {}
        if rc.get("stale") or (rc and rc.get("rule_failures")):
            raise DesignToolError("冻结前请先执行合规复检并确保强制规则全部通过")
        project_id = art.project_id
    freeze_artifact(key, version)
    stale = False
    if project_id:
        with session_scope() as s:
            project = s.get(DesignProject, project_id)
            pipeline = dict(project.pipeline)
            nodes = dict(pipeline["nodes"])
            old = nodes.get("clause", {}).get("version")
            nodes["clause"] = {**nodes.get("clause", {}), "artifact_key": key, "version": version, "status": "done"}
            if nodes.get("pricing", {}).get("status") == "done" and old != version:
                nodes["pricing"] = {**nodes["pricing"], "stale": True, "stale_reason": f"条款由 V{old} 修订为 V{version}，定价需基于新条款重新确认"}
                stale = True
            pipeline["nodes"] = nodes
            _log(pipeline, f"条款方案人工修订后重新冻结 V{version}（{user['display_name']}）" + ("；定价需重新确认" if stale else ""))
            project.pipeline = pipeline
    return {"version": version, "pricing_stale": stale}


# ---------------------------------------------------------------- 条款模板库


def templates(line: str | None = None, kind: str | None = None, q: str | None = None) -> dict:
    with session_scope() as s:
        query = select(ClauseModule)
        if line:
            query = query.where(ClauseModule.line.in_([line, "COMMON"]))
        if kind:
            query = query.where(ClauseModule.module_kind == kind)
        rows = s.scalars(query.order_by(ClauseModule.line, ClauseModule.id)).all()
        modules = [{"code": m.code, "line": m.line, "line_name": LINES.get(m.line, {}).get("name", "通用条款"), "kind": m.module_kind, "kind_label": MODULE_KINDS.get(m.module_kind, m.module_kind), "title": m.title, "text": m.text, "source": m.source, "filing_status": m.filing_status, "version": m.version, "meta": m.meta} for m in rows]
    if q:
        modules = [m for m in modules if q in m["title"] or q in m["text"]]
    usage: dict[str, int] = {}
    with session_scope() as s:
        for a in s.scalars(select(Artifact).where(Artifact.kind == "clause_plan")).all():
            for d in (a.data or {}).get("docs", []):
                for ch in d.get("chapters", []):
                    for art in ch.get("articles", []):
                        if art.get("module"):
                            usage[art["module"]] = usage.get(art["module"], 0) + 1
    for m in modules:
        m["used"] = usage.get(m["code"], 0)
    skeletons = [{"loss_form": k, **v} for k, v in INNOVATION_SKELETONS.items()]
    specs = [{"subtype": k, **v, "line_name": LINES.get(v["line"], {}).get("name", v["line"])} for k, v in INNOVATION_SPECS.items()]
    return {"modules": modules, "skeletons": skeletons, "innovation_specs": specs, "kinds": MODULE_KINDS}


# ---------------------------------------------------------------- 定价任务配置


def pricing_config(project_id: int) -> dict:
    from ..config import settings

    with session_scope() as s:
        p = s.get(DesignProject, project_id)
        if p is None:
            raise DesignToolError("设计项目不存在")
        conf = dict(p.config or {})
        nodes = (p.pipeline or {}).get("nodes", {})
        clause = nodes.get("clause", {})
        frozen = [{"version": a.version, "created_at": iso(a.created_at), "revisions": len(a.revisions or [])} for a in s.scalars(select(Artifact).where(Artifact.artifact_key == clause.get("artifact_key"), Artifact.status == "frozen").order_by(Artifact.version)).all()] if clause.get("artifact_key") else []
        lines = sorted({l for a in s.scalars(select(Artifact).where(Artifact.artifact_key == clause.get("artifact_key"), Artifact.version == clause.get("version"))).all() for d in (a.data or {}).get("docs", []) for l in d.get("lines", [])}) if clause.get("artifact_key") else list(p.target_lines or [])
    datasets = []
    for line in lines:
        path = settings.datasets_dir / f"experience_{line}.csv"
        rows = sum(1 for _ in path.open(encoding="utf-8")) - 1 if path.exists() else 0
        datasets.append({"line": line, "name": LINES.get(line, {}).get("name", line), "file": path.name if path.exists() else None, "rows": max(0, rows), "available": path.exists()})
    return {
        "methods": conf.get("methods") or [],
        "targets": {**DEFAULT_TARGETS, **(conf.get("pricing_targets") or {})},
        "experiment": {"rounds": 3, **(conf.get("experiment") or {})},
        "clause": {"artifact_key": clause.get("artifact_key"), "version": clause.get("version"), "frozen_versions": frozen},
        "datasets": datasets,
        "method_labels": PRICING_METHODS,
        "pricing_status": nodes.get("pricing", {}).get("status"),
    }


def set_pricing_config(project_id: int, body: dict, user: dict) -> dict:
    methods = [m for m in body.get("methods") or [] if m in PRICING_METHODS]
    if methods and len(methods) < 2:
        raise DesignToolError("定价方法至少选择两种（可解释定价的硬性要求）")
    t = {**DEFAULT_TARGETS, **(body.get("targets") or {})}
    lr = t["loss_ratio"]
    if not (isinstance(lr, list) and len(lr) == 2 and 0 < float(lr[0]) < float(lr[1]) < 1):
        raise DesignToolError("目标赔付率区间无效")
    if float(t["expense_ratio"]) + float(t["profit_ratio"]) + float(lr[1]) > 1.2:
        raise DesignToolError("目标赔付率上限、费用率与利润率之和过高")
    if not 0.8 <= float(t["combined_cap"]) <= 1.1:
        raise DesignToolError("综合成本率上限需在 80%–110% 之间")
    rounds = int((body.get("experiment") or {}).get("rounds", 3))
    if not 2 <= rounds <= 10:
        raise DesignToolError("实验轮次需在 2–10 之间")
    with session_scope() as s:
        p = s.get(DesignProject, project_id)
        if p is None:
            raise DesignToolError("设计项目不存在")
        nodes = dict((p.pipeline or {}).get("nodes", {}))
        if nodes.get("pricing", {}).get("status") == "running":
            raise DesignToolError("精算定价运行中，不能修改配置")
        cv = body.get("clause_version")
        if cv:
            clause = nodes.get("clause", {})
            art = s.scalars(select(Artifact).where(Artifact.artifact_key == clause.get("artifact_key"), Artifact.version == int(cv), Artifact.status == "frozen")).first()
            if art is None:
                raise DesignToolError("只能选择已冻结的条款版本")
            pipeline = dict(p.pipeline)
            nodes["clause"] = {**clause, "version": int(cv)}
            pipeline["nodes"] = nodes
            p.pipeline = pipeline
        conf = dict(p.config or {})
        conf.update({"methods": methods or None, "pricing_targets": {k: (v if not isinstance(v, (int, float)) else float(v)) for k, v in t.items()}, "experiment": {"rounds": rounds}})
        p.config = conf
    return pricing_config(project_id)


_ = json
