"""知识治理：文档入库（解析、自动标注、抽检质检、发布与退回）、文档详情与版本、失效与影响分析、风险因子字典、欺诈模式库、带引用的知识问答、知识变更提案与审批。"""

from __future__ import annotations

import random
import re
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import case, exists, func, select

from ..db import session_scope, utcnow
from ..knowledge.catalog import CLAIM_FRAUD_PATTERNS, FACTOR_DICTIONARY, FRAUD_PATTERNS, LINES, SUBTYPES
from ..knowledge.index import KB_NAMES, PRIVATE_KBS, knowledge_index, record_citations
from ..knowledge.seed import add_document, auto_annotate, chunk_text
from ..models import Artifact, HistoricalApplication, IngestBatch, KnowledgeChunk, KnowledgeCitation, KnowledgeDoc, KnowledgeProposal, Product, Run, ToolCall
from ..privacy.classifier import detect_level
from . import approvals, platform_tasks  # noqa: F401
from .config_center import cfg

_STATS_TTL_SEC = 60.0
_stats_memo: dict = {"at": 0.0, "value": None}
_stats_lock = threading.Lock()


class KnowledgeError(ValueError):
    pass


def invalidate_library_stats() -> None:
    """入库、失效等变更后清掉总览缓存，下次请求重新聚合。"""
    with _stats_lock:
        _stats_memo["value"] = None
        _stats_memo["at"] = 0.0


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _no(s, model, kind: str, default: str) -> str:
    prefix = cfg("params.dicts")["numbering"].get(kind, default)
    n = (s.scalar(select(func.count(model.id))) or 0) + 1
    return f"{prefix}{utcnow():%Y%m%d}{n:04d}"


# ---------------------------------------------------------------- 入库与质检


def split_documents(raw: str, filename: str) -> list[dict]:
    """一个上传文件可含多篇文档：以“# 标题”或“【标题】”开头的段落切分；否则整篇作为一个文档。"""
    parts = re.split(r"\n(?=#\s|【[^】]{2,40}】\s*\n)", "\n" + raw.strip())
    docs = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^(?:#\s*(.+)|【([^】]+)】)\s*\n", p)
        if m:
            title = (m.group(1) or m.group(2)).strip()
            body = p[m.end():].strip()
        else:
            title, body = re.sub(r"\.\w+$", "", filename), p
        if len(body) >= 20:
            docs.append({"title": title[:120], "text": body})
    return docs


def create_batch(kb: str, source: str, files: list[tuple[str, str]], doc_type: str, user_id: int) -> dict:
    if kb not in KB_NAMES:
        raise KnowledgeError("知识库不存在")
    items = []
    for filename, text in files:
        for d in split_documents(text, filename):
            ann = auto_annotate(d["title"] + d["text"])
            level = detect_level(d["text"], private_context=kb in PRIVATE_KBS)
            chunks = chunk_text(d["text"])
            issues = []
            if level >= 2 and kb not in PRIVATE_KBS:
                issues.append(f"检出 L{level} 敏感信息，公域知识库不允许入库，请脱敏后重新上传或改投私域知识库")
            if len(d["text"]) < 60:
                issues.append("正文过短")
            if not ann["risk_types"] and not ann["lines"] and kb in ("K03", "K04", "K05"):
                issues.append("未识别到险种或风险类型标签，请人工标注")
            items.append({"idx": len(items), "title": d["title"], "text": d["text"], "file": filename, "doc_type": doc_type, "lines": ann["lines"], "stages": ann["stages"], "risk_types": ann["risk_types"], "tags": [], "data_level": level, "chunks": len(chunks), "chunk_preview": [{"position": pos, "text": c[:120]} for c, pos in chunks[:3]], "issues": issues, "qc": None})
    if not items:
        raise KnowledgeError("没有解析出有效文档（每篇正文不少于 20 字）")
    conf = cfg("kb.ingest")
    n = max(int(conf["min_samples"]), round(len(items) * float(conf["sample_ratio"])))
    rnd = random.Random(len(items) * 7919 + sum(len(i["text"]) for i in items))
    flagged = [i["idx"] for i in items if i["issues"]]
    others = [i["idx"] for i in items if not i["issues"]]
    rnd.shuffle(others)
    sample = sorted(set(flagged) | set(others[: max(0, n - len(flagged))]))
    with session_scope() as s:
        b = IngestBatch(batch_no=_no(s, IngestBatch, "ingest_batch", "RK"), kb=kb, source=source, status="qc", items=items, sample_ids=sample, qc={"sample_ratio": conf["sample_ratio"], "reviewed": 0, "passed": 0}, created_by=user_id)
        s.add(b)
        s.flush()
        return batch_dict(b)


def batch_dict(b: IngestBatch, full: bool = True) -> dict:
    d = {"id": b.id, "batch_no": b.batch_no, "kb": b.kb, "kb_name": KB_NAMES.get(b.kb, b.kb), "source": b.source, "status": b.status, "docs": len(b.items or []), "sample_ids": b.sample_ids, "qc": b.qc, "created_at": iso(b.created_at), "published_at": iso(b.published_at)}
    if full:
        d["items"] = b.items
    return d


def library_stats() -> dict:
    """知识库总览：所属域、规模、健康度和近 30 天引用。没有文档的库也返回，近 30 天没有记录时为 0。

    只用聚合与元数据列，不拉文档正文。切片表可达千万行，精确计数会扫全索引；
    因此切片相关指标走短 TTL 单飞缓存，避免首页并发把磁盘打满。
    """
    now = time.monotonic()
    hit = _stats_memo["value"]
    if hit is not None and now - _stats_memo["at"] < _STATS_TTL_SEC:
        return hit
    with _stats_lock:
        now = time.monotonic()
        hit = _stats_memo["value"]
        if hit is not None and now - _stats_memo["at"] < _STATS_TTL_SEC:
            return hit
        value = _library_stats_compute()
        _stats_memo["value"] = value
        _stats_memo["at"] = time.monotonic()
        return value


def _library_stats_compute() -> dict:
    since = utcnow() - timedelta(days=30)
    with session_scope() as s:
        # max(id) 走主键，用来判断切片表是否已大到不能每次全量 COUNT
        chunk_ceiling = int(s.scalar(select(func.max(KnowledgeChunk.id))) or 0)
        large_chunks = chunk_ceiling > 200_000
        has_chunk = exists(select(KnowledgeChunk.id).where(KnowledgeChunk.doc_id == KnowledgeDoc.id).limit(1))
        parsed_col = func.count().label("parsed_docs") if large_chunks else func.sum(case((has_chunk, 1), else_=0)).label("parsed_docs")

        # 文档规模；切片表过大时不再逐文档 EXISTS（入库路径保证有切片），以保证首页秒开
        doc_rows = s.execute(
            select(
                KnowledgeDoc.kb,
                func.count().label("docs"),
                func.coalesce(func.sum(KnowledgeDoc.cite_count), 0).label("citations"),
                func.max(KnowledgeDoc.created_at).label("updated_at"),
                func.sum(case((KnowledgeDoc.status == "effective", 1), else_=0)).label("effective"),
                func.sum(case((KnowledgeDoc.status != "effective", 1), else_=0)).label("inactive"),
                parsed_col,
            ).group_by(KnowledgeDoc.kb)
        ).all()
        by_kb = {
            kb: {
                "docs": int(docs or 0),
                "citations": int(citations or 0),
                "updated_at": updated_at,
                "effective": int(effective or 0),
                "inactive": int(inactive or 0),
                "chunks": 0,
                "parsed_docs": int(parsed_docs or 0),
                "annotated": 0,
            }
            for kb, docs, citations, updated_at, effective, inactive, parsed_docs in doc_rows
        }

        # 标注完整率只读 JSON 标签列，不读 text
        for kb, status, lines, stages, risk_types in s.execute(
            select(KnowledgeDoc.kb, KnowledgeDoc.status, KnowledgeDoc.lines, KnowledgeDoc.stages, KnowledgeDoc.risk_types)
        ):
            if status == "effective" and (lines or stages or risk_types) and kb in by_kb:
                by_kb[kb]["annotated"] += 1

        # 切片总数：规模可控时精确统计；过大时用已切片文档数占位，避免扫千万行
        if not large_chunks:
            doc_kb = {id_: kb for id_, kb in s.execute(select(KnowledgeDoc.id, KnowledgeDoc.kb))}
            for doc_id, n in s.execute(select(KnowledgeChunk.doc_id, func.count()).group_by(KnowledgeChunk.doc_id)):
                kb = doc_kb.get(doc_id)
                if kb in by_kb:
                    by_kb[kb]["chunks"] += int(n or 0)
        else:
            for row in by_kb.values():
                row["chunks"] = row["parsed_docs"]

        recent = {
            kb: int(n or 0)
            for kb, n in s.execute(
                select(KnowledgeDoc.kb, func.count(KnowledgeCitation.id))
                .join(KnowledgeCitation, KnowledgeCitation.doc_id == KnowledgeDoc.id)
                .where(KnowledgeCitation.cited_at >= since)
                .group_by(KnowledgeDoc.kb)
            )
        }

        top_out = [
            {"id": id_, "kb": kb, "title": title, "doc_type": doc_type, "cite_count": cite_count, "quality": quality}
            for id_, kb, title, doc_type, cite_count, quality in s.execute(
                select(
                    KnowledgeDoc.id,
                    KnowledgeDoc.kb,
                    KnowledgeDoc.title,
                    KnowledgeDoc.doc_type,
                    KnowledgeDoc.cite_count,
                    KnowledgeDoc.quality,
                )
                .order_by(KnowledgeDoc.cite_count.desc(), KnowledgeDoc.id.desc())
                .limit(20)
            )
        ]

        kbs = []
        for kb, name in KB_NAMES.items():
            row = by_kb.get(kb) or {"docs": 0, "citations": 0, "updated_at": None, "effective": 0, "inactive": 0, "chunks": 0, "parsed_docs": 0, "annotated": 0}
            total = row["docs"]
            effective = row["effective"]
            kbs.append(
                {
                    "kb": kb,
                    "name": name,
                    "domain": "private" if kb in PRIVATE_KBS else "public",
                    "domain_label": "私域" if kb in PRIVATE_KBS else "公域",
                    "docs": total,
                    "chunks": row["chunks"],
                    "updated_at": iso(row["updated_at"]),
                    "parse_rate": round(row["parsed_docs"] / total, 3) if total else 0.0,
                    "annotation_rate": round(row["annotated"] / effective, 3) if effective else 0.0,
                    "inactive_rate": round(row["inactive"] / total, 3) if total else 0.0,
                    "citations_30d": int(recent.get(kb, 0)),
                    "citations": row["citations"],
                }
            )
        return {"kbs": kbs, "top": top_out}


def list_batches() -> list[dict]:
    with session_scope() as s:
        return [batch_dict(b, full=False) for b in s.scalars(select(IngestBatch).order_by(IngestBatch.id.desc())).all()]


def get_batch(batch_id: int) -> dict:
    with session_scope() as s:
        b = s.get(IngestBatch, batch_id)
        if b is None:
            raise KnowledgeError("入库批次不存在")
        return batch_dict(b)


def qc_item(batch_id: int, idx: int, verdict: str, corrections: dict, comment: str, user: dict) -> dict:
    if verdict not in ("pass", "fail"):
        raise KnowledgeError("质检结论无效")
    with session_scope() as s:
        b = s.get(IngestBatch, batch_id)
        if b is None or b.status != "qc":
            raise KnowledgeError("批次不在质检中")
        items = [dict(i) for i in b.items]
        if not 0 <= idx < len(items):
            raise KnowledgeError("文档不存在")
        it = items[idx]
        for k in ("title", "lines", "stages", "risk_types", "tags", "doc_type"):
            if k in corrections and corrections[k] is not None:
                it[k] = corrections[k]
        unknown = [r for r in it.get("risk_types", []) if r not in SUBTYPES]
        if unknown:
            raise KnowledgeError("风险类型编码无效：" + "、".join(unknown))
        it["qc"] = {"verdict": verdict, "comment": comment, "by": user["display_name"], "at": iso(utcnow()), "corrected": sorted(k for k in corrections if corrections[k] is not None)}
        items[idx] = it
        b.items = items
        reviewed = [i for i in items if i.get("qc")]
        b.qc = {**(b.qc or {}), "reviewed": len(reviewed), "passed": sum(1 for i in reviewed if i["qc"]["verdict"] == "pass"), "sample_done": all(items[i].get("qc") for i in b.sample_ids)}
        return batch_dict(b)


def publish_batch(batch_id: int, user: dict) -> dict:
    conf = cfg("kb.ingest")
    with session_scope() as s:
        b = s.get(IngestBatch, batch_id)
        if b is None or b.status != "qc":
            raise KnowledgeError("批次不在质检中")
        items = b.items
        pending = [i for i in b.sample_ids if not items[i].get("qc")]
        if pending:
            raise KnowledgeError(f"还有 {len(pending)} 篇抽检文档未质检")
        sampled = [items[i] for i in b.sample_ids]
        rate = sum(1 for i in sampled if i["qc"]["verdict"] == "pass") / len(sampled) if sampled else 1.0
        if rate < float(conf["pass_ratio"]):
            raise KnowledgeError(f"抽检合格率 {rate:.0%} 低于发布要求 {float(conf['pass_ratio']):.0%}，请退回批次")
        blocked = [i for i in items if any("不允许入库" in x for x in i.get("issues", []))]
        published = []
        for it in items:
            if it in blocked or (it.get("qc") and it["qc"]["verdict"] == "fail"):
                continue
            doc = add_document(s, kb=b.kb, title=it["title"], text=it["text"], source=b.source, doc_type=it["doc_type"], lines=it.get("lines"), stages=it.get("stages"), risk_types=it.get("risk_types"), tags=it.get("tags"), data_level=it.get("data_level", 0) if b.kb in PRIVATE_KBS else min(1, it.get("data_level", 0)), quality="checked" if it.get("qc") else "auto")
            published.append(doc.id)
        b.status = "published"
        b.published_at = utcnow()
        b.qc = {**(b.qc or {}), "pass_rate": round(rate, 3), "published_doc_ids": published, "skipped": len(items) - len(published), "by": user["display_name"]}
        return batch_dict(b)


def return_batch(batch_id: int, reason: str, user: dict) -> dict:
    with session_scope() as s:
        b = s.get(IngestBatch, batch_id)
        if b is None or b.status != "qc":
            raise KnowledgeError("批次不在质检中")
        b.status = "returned"
        b.qc = {**(b.qc or {}), "returned_reason": reason, "by": user["display_name"], "at": iso(utcnow())}
        return batch_dict(b)


# ---------------------------------------------------------------- 文档详情、版本与失效


def doc_detail(doc_id: int, max_level: int) -> dict:
    with session_scope() as s:
        d = s.get(KnowledgeDoc, doc_id)
        if d is None:
            raise KnowledgeError("文档不存在")
        if d.data_level > max_level:
            raise KnowledgeError("该文档的数据级别超出你的可见范围")
        chunks = s.scalars(select(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id).order_by(KnowledgeChunk.seq)).all()
        versions = s.scalars(select(KnowledgeDoc).where(KnowledgeDoc.kb == d.kb, KnowledgeDoc.title == d.title).order_by(KnowledgeDoc.version)).all()
        proposals = s.scalars(select(KnowledgeProposal).where(KnowledgeProposal.doc_id == doc_id).order_by(KnowledgeProposal.id.desc())).all()
        return {"id": d.id, "kb": d.kb, "kb_name": KB_NAMES.get(d.kb, d.kb), "title": d.title, "source": d.source, "doc_type": d.doc_type, "status": d.status, "effective_date": d.effective_date, "lines": d.lines, "stages": d.stages, "risk_types": d.risk_types, "tags": d.tags, "data_level": d.data_level, "text": d.text, "version": d.version, "quality": d.quality, "cite_count": d.cite_count, "created_at": iso(d.created_at), "chunks": [{"seq": c.seq, "position": c.position, "text": c.text} for c in chunks], "versions": [{"id": v.id, "version": v.version, "status": v.status, "created_at": iso(v.created_at)} for v in versions], "proposals": [proposal_dict(p) for p in proposals]}


def impact(doc_id: int) -> dict:
    """失效影响分析：引用该文档的运行（检索与工具调用记录）、使用相同险种或风险类型的产品与产物。"""
    with session_scope() as s:
        d = s.get(KnowledgeDoc, doc_id)
        if d is None:
            raise KnowledgeError("文档不存在")
        title = d.title
        lines, risks = set(d.lines or []), set(d.risk_types or [])
        marks = [f"#{doc_id}]", f"kb:{doc_id}", title[:20]]
        calls = s.scalars(select(ToolCall).where(ToolCall.result_summary.contains(title[:16])).limit(200)).all()
        run_ids = sorted({c.run_id for c in calls})
        runs = s.scalars(select(Run).where(Run.id.in_(run_ids))).all() if run_ids else []
        products = [p for p in s.scalars(select(Product).where(Product.status.in_(["published", "scheduled", "pending_approval", "draft"]))).all() if lines & set(p.lines or [])]
        arts = []
        if risks:
            for a in s.scalars(select(Artifact).where(Artifact.kind.in_(["risk_report", "clause_plan"])).order_by(Artifact.id.desc()).limit(300)).all():
                found = {r.get("subtype") for r in (a.data or {}).get("risks", [])} | {r.get("subtype") for r in (a.data or {}).get("insurable", [])}
                if found & risks:
                    arts.append({"artifact_key": a.artifact_key, "version": a.version, "kind": a.kind, "title": a.title})
        return {"doc_id": doc_id, "title": title, "cite_count": d.cite_count, "runs": [{"id": r.id, "agent_id": r.agent_id, "title": r.title, "status": r.status} for r in runs][:30], "products": [{"id": p.id, "name": p.name, "status": p.status} for p in products], "artifacts": arts[:30], "marks": marks}


def set_doc_status(s, doc_id: int, status: str) -> None:
    d = s.get(KnowledgeDoc, doc_id)
    if d is None:
        raise KnowledgeError("文档不存在")
    d.status = status
    knowledge_index.invalidate()


def new_version(s, doc_id: int, text: str, meta: dict) -> KnowledgeDoc:
    old = s.get(KnowledgeDoc, doc_id)
    if old is None:
        raise KnowledgeError("文档不存在")
    old.status = "superseded"
    doc = add_document(s, kb=old.kb, title=meta.get("title") or old.title, text=text, source=old.source, doc_type=old.doc_type, effective_date=meta.get("effective_date") or old.effective_date, lines=meta.get("lines") or old.lines, stages=old.stages, risk_types=meta.get("risk_types") or old.risk_types, tags=old.tags, data_level=old.data_level, enterprise_id=old.enterprise_id, quality="checked")
    doc.version = (old.version or 1) + 1
    return doc


# ---------------------------------------------------------------- 风险因子字典与欺诈模式库


def factor_dictionary() -> list[dict]:
    usage: dict[str, dict] = {f["key"]: {"artifacts": 0, "selected": 0, "importance": []} for f in FACTOR_DICTIONARY}
    with session_scope() as s:
        for a in s.scalars(select(Artifact).where(Artifact.kind == "pricing_plan")).all():
            for c in (a.data or {}).get("components", []):
                used = set()
                for f in c.get("factors", []) or []:
                    key = f.get("key") if isinstance(f, dict) else f
                    if key in usage:
                        used.add(key)
                for key in c.get("selected", []) or []:
                    if key in usage:
                        usage[key]["selected"] += 1
                        used.add(key)
                for key, v in ((c.get("models") or {}).get("PM2") or {}).get("importance", {}).items() if isinstance(((c.get("models") or {}).get("PM2") or {}).get("importance"), dict) else []:
                    if key in usage:
                        usage[key]["importance"].append(float(v))
                for key in used:
                    usage[key]["artifacts"] += 1
    out = []
    for f in FACTOR_DICTIONARY:
        u = usage[f["key"]]
        out.append({**f, "source_label": {"llm": "大模型从文本提取", "structured": "结构化数据", "knowledge": "知识库"}.get(f["source"], f["source"]), "kind_label": {"numeric": "数值", "ordinal": "有序等级", "categorical": "分类"}.get(f["kind"], f["kind"]), "direction_label": "越高风险越高" if f["direction"] > 0 else "越高风险越低", "line_names": [LINES.get(l, {}).get("name", l) for l in f["lines"]], "usage": {"artifacts": u["artifacts"], "selected": u["selected"], "avg_importance": round(sum(u["importance"]) / len(u["importance"]), 4) if u["importance"] else None}})
    return out


def fraud_library() -> list[dict]:
    with session_scope() as s:
        hist = s.scalars(select(HistoricalApplication).where(HistoricalApplication.fraud_pattern.is_not(None))).all()
        counts: dict[str, list] = {}
        for h in hist:
            counts.setdefault(h.fraud_pattern, []).append(h)
        uw_hits: dict[str, int] = {}
        for a in s.scalars(select(Artifact).where(Artifact.kind == "uw_report")).all():
            for p in ((a.data or {}).get("fraud") or {}).get("patterns", []) or []:
                code = p.get("code") or p.get("pattern")
                if code:
                    uw_hits[code] = uw_hits.get(code, 0) + 1
        claim_hits: dict[str, int] = {}
        for a in s.scalars(select(Artifact).where(Artifact.kind == "claim_result")).all():
            for p in ((a.data or {}).get("fraud") or {}).get("hits", []) or ((a.data or {}).get("fraud_checks") or []):
                code = p.get("code") or p.get("pattern")
                if code and p.get("hit", True):
                    claim_hits[code] = claim_hits.get(code, 0) + 1
    out = []
    from ..analytics.fraud import rule_hits  # noqa: F401

    for code, fp in FRAUD_PATTERNS.items():
        cases = counts.get(code, [])
        out.append({"code": code, "stage": "承保", "name": fp["name"], "desc": fp["desc"], "rules": _rules_for(code), "history_cases": len(cases), "claimed_rate": round(sum(1 for c in cases if c.claimed) / len(cases), 3) if cases else None, "hits": uw_hits.get(code, 0), "examples": [{"enterprise": c.enterprise_name, "line": LINES.get(c.line, {}).get("name", c.line), "year": c.year, "statement": (c.statement or "")[:120], "intermediary": c.intermediary} for c in cases[:3]]})
    for code, fp in CLAIM_FRAUD_PATTERNS.items():
        out.append({"code": code, "stage": "理赔", "name": fp["name"], "desc": fp["desc"], "rules": _rules_for(code), "history_cases": 0, "claimed_rate": None, "hits": claim_hits.get(code, 0), "examples": []})
    return out


RULE_TEXT = {
    "P01": ["设备购置日期距投保日 ≤ 30 天的金额占比 ≥ 40%", "发票与铭牌型号或序列号不一致"],
    "P02": ["保额 ÷ 账面价值或重置价值 > 1.2"],
    "P03": ["投保前 90 天内出现事故、纠纷或研发中断信号"],
    "P04": ["文档元数据创建时间晚于声称日期", "图像误差水平分析得分 > 3 且比值 ≥ 1.8", "营业执照信用代码校验失败（一票否决）"],
    "P05": ["关系网络社区内 12 个月投保同类产品 ≥ 3 家", "社区历史欺诈率 ≥ 30%"],
    "P06": ["同一电话或地址对应 ≥ 2 个无关主体"],
    "P07": ["参保人数为 0 或纳税记录缺失"],
    "P08": ["12 个月内法定代表人或注册地址变更 ≥ 2 次"],
    "P09": ["中介渠道可疑率高于平均 2 倍"],
    "P10": ["申报员工数、营收与公开信息偏差 > 30%"],
    "P11": ["材料文本字符相似度 ≥ 0.85", "照片感知哈希汉明距离 ≤ 4"],
    "P12": ["纳税额 ÷ 营收低于行业下限", "审计意见为非标准意见"],
    "C01": ["出险时间距保单生效或到期 < 15 天"],
    "C02": ["报损金额 ÷ 价格库参考价 > 1.5"],
    "C03": ["同一标的序列号在其他案件中已索赔"],
    "C04": ["受损照片与承保照片的损伤区域相似度高"],
    "C05": ["事故前 30 天健康指数 < 55 且有预警未处置"],
    "C06": ["关联主体 6 个月内相继出险"],
}


def _rules_for(code: str) -> list[str]:
    return RULE_TEXT.get(code, [])


# ---------------------------------------------------------------- 知识问答


def answer(question: str, kbs: list[str] | None, max_level: int) -> dict:
    from ..llm.gateway import ModelUnavailable, model_gateway

    hits = knowledge_index.search(question, kbs=kbs or None, max_level=max_level, top_k=6)
    passages = [{"n": i + 1, "doc_id": h["doc_id"], "kb": h["kb"], "title": h["title"], "position": h["position"], "text": h["text"]} for i, h in enumerate(hits)]
    if not passages:
        return {"answer": "知识库中没有检索到相关内容。", "citations": [], "passages": [], "insufficient": True}
    try:
        out = model_gateway.invoke("kb.answer", {"question": question, "passages": [{"n": p["n"], "title": p["title"], "kb": p["kb"], "text": p["text"]} for p in passages]}, data_level=max(h["data_level"] for h in hits))
    except ModelUnavailable as exc:
        raise KnowledgeError(f"问答失败：{exc}") from exc
    record_citations([passages[c - 1]["doc_id"] for c in out["citations"] if 0 < c <= len(passages)])
    return {**out, "passages": passages}


# ---------------------------------------------------------------- 知识变更提案


def proposal_dict(p: KnowledgeProposal) -> dict:
    return {"id": p.id, "proposal_no": p.proposal_no, "kb": p.kb, "kb_name": KB_NAMES.get(p.kb, p.kb), "action": p.action, "action_label": {"add": "新增", "modify": "修改", "retire": "废止"}[p.action], "doc_id": p.doc_id, "title": p.title, "content": p.content, "meta": p.meta, "rationale": p.rationale, "impact": p.impact, "status": p.status, "approval_id": p.approval_id, "submitted_by": p.submitted_by, "created_at": iso(p.created_at), "decided_at": iso(p.decided_at)}


def submit_proposal(kb: str, action: str, doc_id: int | None, title: str, content: str, meta: dict, rationale: str, user: dict) -> dict:
    if action not in ("add", "modify", "retire"):
        raise KnowledgeError("提案类型无效")
    if kb not in KB_NAMES:
        raise KnowledgeError("知识库不存在")
    if not rationale.strip():
        raise KnowledgeError("请填写提案理由")
    if action in ("modify", "retire"):
        with session_scope() as s:
            d = s.get(KnowledgeDoc, doc_id) if doc_id else None
            if d is None:
                raise KnowledgeError("要修改或废止的文档不存在")
            if d.status != "effective":
                raise KnowledgeError("只能对生效中的文档提案")
            title = title or d.title
            kb = d.kb
    if action in ("add", "modify") and len(content.strip()) < 30:
        raise KnowledgeError("文档内容不少于 30 字")
    if action == "add" and kb not in PRIVATE_KBS and detect_level(content) >= 2:
        raise KnowledgeError("内容含 L2 以上敏感信息，公域知识库不能收录")
    imp = impact(doc_id) if doc_id else {}
    with session_scope() as s:
        p = KnowledgeProposal(proposal_no=_no(s, KnowledgeProposal, "knowledge_proposal", "ZB"), kb=kb, action=action, doc_id=doc_id, title=title, content=content, meta=meta or {}, rationale=rationale, impact={k: imp.get(k) for k in ("runs", "products", "artifacts", "cite_count")} if imp else {}, status="pending", submitted_by=user["id"])
        s.add(p)
        s.flush()
        pid, no = p.id, p.proposal_no
    req = approvals.start("knowledge_change", object_type="knowledge_proposal", object_id=pid, title=f"知识{'新增' if action == 'add' else ('修改' if action == 'modify' else '废止')}：{title}", summary={"提案编号": no, "知识库": KB_NAMES.get(kb, kb), "文档": title, "理由": rationale, "影响": f"引用运行 {len(imp.get('runs', []))} 个、相关产品 {len(imp.get('products', []))} 个" if imp else "新增文档"}, applicant=user)
    with session_scope() as s:
        s.get(KnowledgeProposal, pid).approval_id = req["id"]
        return proposal_dict(s.get(KnowledgeProposal, pid))


def _proposal_approved(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        p = s.get(KnowledgeProposal, int(req["object_id"]))
        if p is None or p.status != "pending":
            return
        if p.action == "add":
            doc = add_document(s, kb=p.kb, title=p.title, text=p.content, source=f"知识变更提案 {p.proposal_no}", doc_type=p.meta.get("doc_type") or "提案新增", lines=p.meta.get("lines"), risk_types=p.meta.get("risk_types"), tags=p.meta.get("tags"), data_level=1 if p.kb in PRIVATE_KBS else 0, quality="checked")
            p.doc_id = doc.id
        elif p.action == "modify":
            doc = new_version(s, p.doc_id, p.content, {"title": p.title, **(p.meta or {})})
            p.meta = {**(p.meta or {}), "new_doc_id": doc.id}
        else:
            set_doc_status(s, p.doc_id, "retired")
        p.status = "approved"
        p.decided_at = utcnow()
    knowledge_index.invalidate()


def _proposal_rejected(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        p = s.get(KnowledgeProposal, int(req["object_id"]))
        if p is not None and p.status == "pending":
            p.status = "withdrawn" if (req.get("result") or {}).get("action") == "withdraw" else "rejected"
            p.decided_at = utcnow()


def list_proposals(status: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(KnowledgeProposal)
        if status:
            q = q.where(KnowledgeProposal.status == status)
        return [proposal_dict(p) for p in s.scalars(q.order_by(KnowledgeProposal.id.desc())).all()]


def register_handlers() -> None:
    approvals.register("knowledge_change", _proposal_approved, _proposal_rejected)
