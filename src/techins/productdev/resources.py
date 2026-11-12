"""把八类产品开发资源写入现有知识库和经验数据，避免另建一套库。"""

from __future__ import annotations

import csv

from sqlalchemy import func, select

from ..config import settings
from ..db import session_scope
from ..knowledge.seed import add_document
from ..models import KnowledgeDoc

SHARED_TITLE = "设备损失场景的公开技术资料"
EXPERIENCE_HEADERS = ["liability", "claim_amount", "billing_amount", "period", "limit_amount", "risk_feature", "empirical_pure"]


def experience_path():
    return settings.datasets_dir / "experience_product_development.csv"


def ensure_experience_file() -> None:
    path = experience_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and SHARED_TITLE in path.read_text(encoding="utf-8"):
        return
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPERIENCE_HEADERS)
        if not existing.strip():
            writer.writeheader()
        writer.writerow({
            "liability": "约定事故造成的设备损失",
            "claim_amount": "3600",
            "billing_amount": "1000000",
            "period": "1年",
            "limit_amount": "1000000",
            "risk_feature": SHARED_TITLE,
            "empirical_pure": "0.0036",
        })


def ensure_documents() -> dict[str, int]:
    specs = [
        ("K04", "产品开发需求与场景样例", "需求来自产品开发人员描述。适用条件：尚未确定险种名称的新产品。证据类型：专家判断。", "场景样例", "需求访谈"),
        ("K04", "可保性判断规则", "损失必须能够界定、具有偶然性并且可以核实。证据不足时不得写成已经具备保障条件。证据类型：专家判断。", "专家规则", "可保性研究"),
        ("K03", "财产损失与责任保险条款参考", "条款按财产损失保险、责任保险检索，并记录产品版本 2024。适用条件：当前险种和场景。证据类型：公开证据。", "参考条款", "公司条款库"),
        ("K01", "财产保险条款管理办法摘录", "适用地区：全国。生效时间：2021-01-01。条款应当列明保险责任、责任免除和赔偿处理。证据类型：公开证据。", "法规", "监管文件"),
        ("K06", SHARED_TITLE, "来源：公开技术文献。时间：2026-01-01。适用场景：设备在约定地点运行。证据类型：公开证据。同一份资料同时供风险评估和成熟度评估使用。", "技术资料", "公开文献"),
        ("K07", "产品开发专家参数与参考纯风险损失率", "费用率以保费为基数，风险附加率以计费保额为基数。参考纯风险损失率有来源、版本和适用条件。证据类型：专家判断。", "参考参数", "精算参数"),
        ("K07", "承保与损失数据口径说明", "样本必须记录保障责任、赔款、计费保额、保险期间、赔偿限额、风险特征和经验纯风险损失率。缺少计费保额的样本不参与估计。证据类型：经验数据。", "数据口径", "承保理赔数据"),
        ("K07", "精算模型与附加参数", "损失率估计和分类定价由程序执行。费用率与风险附加率由精算人员维护。证据类型：专家判断。", "精算模型", "精算参数"),
    ]
    ids: dict[str, int] = {}
    with session_scope() as s:
        for kb, title, text, doc_type, source in specs:
            found_id = s.execute(select(KnowledgeDoc.id).where(KnowledgeDoc.kb == kb, KnowledgeDoc.title == title)).scalar_one_or_none()
            if found_id is None:
                found = add_document(s, kb=kb, title=title, text=text, source=source, doc_type=doc_type, effective_date="2026-01-01", tags=["产品开发资源", f"证据类型:{'经验数据' if '损失数据' in title else '专家判断' if '参数' in title or '规则' in title or '样例' in title else '公开证据'}"])
                found_id = found.id
            ids[title] = found_id
    ensure_experience_file()
    return ids


def shared_document_count() -> int:
    with session_scope() as s:
        return int(s.scalar(select(func.count(KnowledgeDoc.id)).where(KnowledgeDoc.title == SHARED_TITLE)) or 0)


def _shared_rows() -> list[tuple]:
    """只取引用需要的列，避免把大正文一并读出拖慢定价。"""
    with session_scope() as s:
        return s.execute(
            select(KnowledgeDoc.id, KnowledgeDoc.tags, KnowledgeDoc.cite_count).where(KnowledgeDoc.title == SHARED_TITLE)
        ).all()


def cite_shared_document(use: str) -> int:
    ensure_experience_file()
    rows = _shared_rows()
    if len(rows) != 1:
        # 缺文档时才走完整 ensure，且必须在 session 之外，避免嵌套会话
        ensure_documents()
        rows = _shared_rows()
    if len(rows) != 1:
        raise RuntimeError(f"公开技术资料文档数应为 1，实际 {len(rows)}")
    doc_id, tags, cite_count = rows[0]
    tags = list(tags or [])
    marker = f"用途:{use}"
    if marker not in tags:
        tags.append(marker)
    with session_scope() as s:
        s.execute(
            KnowledgeDoc.__table__.update()
            .where(KnowledgeDoc.id == doc_id)
            .values(tags=tags, cite_count=int(cite_count or 0) + 1)
        )
    return len(rows)
