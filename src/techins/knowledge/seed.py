"""知识入库流水线：解析 → 切分 → 标注 → 写入文档与切片 → 刷新索引。"""

from __future__ import annotations

import re

from sqlalchemy import func, select

from ..db import session_scope
from ..models import ClauseModule, KnowledgeChunk, KnowledgeDoc
from .catalog import CAUSAL_TEMPLATES, CLAIM_FRAUD_PATTERNS, FRAUD_PATTERNS, INDUSTRY_PROFILES, LINES, PLAYBOOKS, RISK_TAXONOMY, SERVICE_PROVIDERS, STAGES
from .portfolio import DEFERRED_PRODUCTS, PRODUCTS
from .clause_library import ALL_MODULES
from .corpus import ACTUARIAL, CASES, INDUSTRY, POLICIES, REGULATIONS
from .index import knowledge_index

_LINE_HINTS = {code: [spec["name"].replace("（附加）", "").replace("险", "")] for code, spec in LINES.items()}


def chunk_text(text: str, max_len: int = 320) -> list[tuple[str, str]]:
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[tuple[str, str]] = []
    for pi, para in enumerate(paragraphs, start=1):
        if len(para) <= max_len:
            chunks.append((para, f"第{pi}段"))
            continue
        sentences = re.split(r"(?<=[。；])", para)
        buf, part = "", 1
        for sent in sentences:
            if len(buf) + len(sent) > max_len and buf:
                chunks.append((buf, f"第{pi}段-{part}"))
                buf, part = "", part + 1
            buf += sent
        if buf:
            chunks.append((buf, f"第{pi}段-{part}"))
    return chunks


def auto_annotate(text: str) -> dict:
    """自动标注：根据风险子类型识别模式与险种关键词，为文档打上风险类型与险种标签。"""
    risk_types = set()
    for code, rt in RISK_TAXONOMY.items():
        for sub_code, sub in rt["subtypes"].items():
            if sum(1 for p in sub["patterns"] if p in text) >= 1:
                risk_types.add(sub_code)
    lines = {code for code, hints in _LINE_HINTS.items() if any(h and h in text for h in hints)}
    stages = {stage for stage, name in STAGES.items() if name in text}
    return {"risk_types": sorted(risk_types), "lines": sorted(lines), "stages": sorted(stages)}


def add_document(
    s,
    *,
    kb: str,
    title: str,
    text: str,
    source: str,
    doc_type: str,
    effective_date: str | None = None,
    lines: list[str] | None = None,
    stages: list[str] | None = None,
    risk_types: list[str] | None = None,
    tags: list[str] | None = None,
    data_level: int = 0,
    enterprise_id: int | None = None,
    quality: str = "auto",
) -> KnowledgeDoc:
    ann = auto_annotate(title + text)
    doc = KnowledgeDoc(
        kb=kb,
        title=title,
        source=source,
        doc_type=doc_type,
        effective_date=effective_date,
        lines=sorted(set((lines or []) + ann["lines"])),
        stages=sorted(set((stages or []) + ann["stages"])),
        risk_types=sorted(set((risk_types or []) + ann["risk_types"])),
        tags=tags or [],
        data_level=data_level,
        enterprise_id=enterprise_id,
        text=text,
        quality=quality,
    )
    s.add(doc)
    s.flush()
    for seq, (chunk, position) in enumerate(chunk_text(text), start=1):
        s.add(KnowledgeChunk(doc_id=doc.id, seq=seq, text=chunk, position=position))
    s.flush()
    knowledge_index.sync_document(s, doc)
    knowledge_index.invalidate()
    return doc


def _taxonomy_doc_payload(code: str, rt: dict, sub_code: str, sub: dict) -> dict:
    lines_text = "、".join(LINES[l]["name"] for l in sub["lines"]) or "无直接对应险种，输出风险管理建议"
    text = (
        f"{rt['name']}之{sub['name']}（{sub_code}）。典型表现：{'、'.join(sub['patterns'])}。"
        f"主要出现阶段：{'、'.join(STAGES[x] for x in sub['stages'])}。可能后果：{sub['consequence']}。"
        f"可保性：{ {'yes': '可保', 'partial': '部分可保', 'no': '通常不可保'}[sub['insurable']] }。对应险种：{lines_text}。"
    )
    return {
        "kb": "K04",
        "title": f"风险分类体系：{sub['name']}",
        "text": text,
        "source": "风险知识库",
        "doc_type": "风险定义",
        "lines": sub["lines"],
        "stages": sub["stages"],
        "risk_types": [sub_code],
        "quality": "checked",
    }


def _title_exists(s, kb: str, title: str) -> bool:
    return bool(s.scalar(select(func.count(KnowledgeDoc.id)).where(KnowledgeDoc.kb == kb, KnowledgeDoc.title == title)))


def sync_risk_taxonomy_docs() -> int:
    """增量写入缺失的风险分类体系及相关联动文档，便于扩充类别后无需清空知识库。"""
    added = 0
    with session_scope() as s:
        for code, rt in RISK_TAXONOMY.items():
            for sub_code, sub in rt["subtypes"].items():
                payload = _taxonomy_doc_payload(code, rt, sub_code, sub)
                if _title_exists(s, "K04", payload["title"]):
                    continue
                add_document(s, **payload)
                added += 1
        from .catalog import SUBTYPES

        for track, prof in INDUSTRY_PROFILES.items():
            title = f"行业风险特征：{track}"
            if _title_exists(s, "K04", title):
                continue
            names = "、".join(SUBTYPES[c]["name"] for c in prof["high_incidence"] if c in SUBTYPES)
            add_document(
                s,
                kb="K04",
                title=title,
                text=f"{track}行业风险等级 {prof['risk_level']}（1–5）。高发风险：{names}。典型技术成熟度等级 {prof['trl_typical']}。",
                source="风险知识库",
                doc_type="行业风险特征",
                risk_types=prof["high_incidence"],
                tags=[track],
                quality="checked",
            )
            added += 1
        for title, line, risks, text in CASES:
            if _title_exists(s, "K05", title):
                continue
            add_document(s, kb="K05", title=title, text=text, source="理赔案例库", doc_type="理赔案例", lines=[line], risk_types=risks, quality="checked")
            added += 1
        for key, pb in PLAYBOOKS.items():
            title = f"应急预案：{pb['name']}"
            if _title_exists(s, "K10", title):
                continue
            steps = "；".join(f"{i + 1}. {st['title']}（{st['deadline_hours']} 小时内）" for i, st in enumerate(pb["steps"]))
            mitig = "；".join(f"{m['measure']}（{m['effect']}）" for m in pb["mitigation"])
            add_document(
                s,
                kb="K10",
                title=title,
                text=f"处置步骤：{steps}。\n减损措施：{mitig}。\n证据保全：{'、'.join(pb['evidence'])}。",
                source="应急与减损知识库",
                doc_type="应急预案",
                lines=pb["lines"],
                tags=[key],
                quality="checked",
            )
            added += 1
    return added


def seed_knowledge() -> None:
    with session_scope() as s:
        if not s.scalar(select(func.count(KnowledgeDoc.id))):
            _seed_knowledge_fresh(s)
        else:
            sync_clause_modules(s)
    sync_risk_taxonomy_docs()


def sync_clause_modules(s) -> int:
    """条款模块目录变更后同步到已有数据库：更新正文与元数据，补齐新增模块，不删除设计人员自定义模块。"""
    existing = {row.code: row for row in s.scalars(select(ClauseModule)).all()}
    changed = 0
    for module in ALL_MODULES:
        row = existing.get(module["code"])
        if row is None:
            s.add(ClauseModule(**module))
            changed += 1
            continue
        for field in ("line", "module_kind", "title", "text", "meta", "source", "filing_status"):
            if getattr(row, field) != module[field]:
                setattr(row, field, module[field])
                changed += 1
    return changed


def _seed_knowledge_fresh(s) -> None:
    for title, doc_type, date, text, tags in REGULATIONS:
        add_document(s, kb="K01", title=title, text=text, source="法规监管库", doc_type=doc_type, effective_date=date, tags=tags, quality="checked")
    for title, doc_type, date, text, lines, stages in POLICIES:
        add_document(s, kb="K02", title=title, text=text, source="科技政策库", doc_type=doc_type, effective_date=date, lines=lines, stages=stages, quality="checked")
    for module in ALL_MODULES:
        s.add(ClauseModule(**module))
        meta = module["meta"]
        line_name = LINES.get(module["line"], {}).get("name", "通用条款")
        add_document(
            s,
            kb="K03",
            title=f"{line_name}·{module['title']}",
            text=module["text"],
            source=module["source"],
            doc_type="条款模块",
            lines=[module["line"]] if module["line"] in LINES else [],
            risk_types=meta.get("covers", []),
            tags=[module["module_kind"], module["code"]],
            quality="checked",
        )
    for code, rt in RISK_TAXONOMY.items():
        for sub_code, sub in rt["subtypes"].items():
            add_document(s, **_taxonomy_doc_payload(code, rt, sub_code, sub))
    from .catalog import SUBTYPES

    for track, prof in INDUSTRY_PROFILES.items():
        names = "、".join(SUBTYPES[c]["name"] for c in prof["high_incidence"])
        add_document(s, kb="K04", title=f"行业风险特征：{track}", text=f"{track}行业风险等级 {prof['risk_level']}（1–5）。高发风险：{names}。典型技术成熟度等级 {prof['trl_typical']}。", source="风险知识库", doc_type="行业风险特征", risk_types=prof["high_incidence"], tags=[track], quality="checked")
    for title, line, risks, text in CASES:
        add_document(s, kb="K05", title=title, text=text, source="理赔案例库", doc_type="理赔案例", lines=[line], risk_types=risks, quality="checked")
    for title, track, text in INDUSTRY:
        add_document(s, kb="K06", title=title, text=text, source="行业研究", doc_type="行业知识", tags=[track], quality="checked")
    for title, doc_type, text in ACTUARIAL:
        add_document(s, kb="K07", title=title, text=text, source="精算数据库", doc_type=doc_type, quality="checked")
    for code, fp in FRAUD_PATTERNS.items():
        add_document(s, kb="K09", title=f"欺诈模式 {code}：{fp['name']}", text=fp["desc"], source="欺诈模式库", doc_type="欺诈模式", tags=[code], quality="checked")
    for key, pb in PLAYBOOKS.items():
        steps = "；".join(f"{i + 1}. {st['title']}（{st['deadline_hours']} 小时内）" for i, st in enumerate(pb["steps"]))
        mitig = "；".join(f"{m['measure']}（{m['effect']}）" for m in pb["mitigation"])
        add_document(s, kb="K10", title=f"应急预案：{pb['name']}", text=f"处置步骤：{steps}。\n减损措施：{mitig}。\n证据保全：{'、'.join(pb['evidence'])}。", source="应急与减损知识库", doc_type="应急预案", lines=pb["lines"], tags=[key], quality="checked")
    for sp in SERVICE_PROVIDERS:
        add_document(s, kb="K10", title=f"约定服务商：{sp['name']}", text=f"{sp['category']}，服务区域 {'、'.join(sp['regions'])}，承诺响应 {sp['response_hours']} 小时内到场，评分 {sp['rating']}。能力：{'、'.join(sp['capabilities'])}。", source="服务商目录", doc_type="服务商", tags=[sp["id"], sp["category"]], quality="checked")
    for key, tpl in CAUSAL_TEMPLATES.items():
        chain = "；".join(f"{e['from']}→{e['to']}（强度 {e['strength']}）" for e in tpl["edges"])
        nodes = "、".join(f"{n['label']}" + (f"（{n['party_role']}行为）" if n.get("party_role") else "") for n in tpl["nodes"])
        add_document(s, kb="K05", title=f"事故因果模板：{tpl['name']}", text=f"节点：{nodes}。因果边：{chain}。承保风险：{tpl['peril']}；常见除外原因：{'、'.join(tpl['excluded_causes'])}。", source="理赔案例库", doc_type="因果模板", tags=[key], quality="checked")
    for code, fp in CLAIM_FRAUD_PATTERNS.items():
        add_document(s, kb="K09", title=f"理赔欺诈模式 {code}：{fp['name']}", text=fp["desc"], source="欺诈模式库", doc_type="欺诈模式", tags=[code], quality="checked")
    catalog_text = "\n".join(
        f"{spec['code']} {spec['name']}（{spec['clause_title']}）。类别：{spec['category']}。适用地区：{'、'.join(spec['regions'])}。保险责任：" + "；".join(item["title"] for item in spec["liabilities"]) + (f"。说明：{spec['source_note']}" if spec.get("source_note") else "。")
        for spec in PRODUCTS
    )
    add_document(s, kb="K04", title="科技保险产品目录（2026）", text=catalog_text, source="公司产品目录", doc_type="险种目录", lines=list(LINES), quality="checked")
    deferred = "\n".join(f"{item['name']}：{item['reason']}" for item in DEFERRED_PRODUCTS)
    add_document(s, kb="K03", title="暂缓入库的险种", text="下列产品因文档条款不完整，尚未进入险种目录：\n" + deferred, source="公司产品目录", doc_type="待补充条款", quality="checked")
