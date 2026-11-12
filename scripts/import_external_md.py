"""把外置科技保险 Markdown 目录导入知识库，按正文哈希去重。

用法（在项目根目录）：
  .venv/bin/python scripts/import_external_md.py data/import_md
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import func, select

from techins.db import session_scope
from techins.knowledge.index import knowledge_index
from techins.knowledge.seed import auto_annotate, chunk_text
from techins.models import KnowledgeChunk, KnowledgeDoc

STUB_PREFIX = "(2012年11月12日国务院令第629号发布"
SKIP_NAMES = {
    "readme.md",
    "deploy.md",
    "readme-zh.md",
}
SKIP_NAME_PARTS = ("cursor", "uninstall", "install_openclaw")
RULES = [
    ("K09", "欺诈研究", ("欺诈",)),
    ("K07", "精算资料", ("精算", "偿付能力", "现金价值")),
    ("K10", "定损公估", ("公估", "定损", "核赔", "查勘")),
    ("K05", "案例资料", ("案例", "判例", "判案", "裁判", "纠纷")),
    ("K03", "条款费率", ("条款", "费率", "保险条款")),
    ("K01", "法律法规", ("保险法", "法律制度", "条例", "监管", "合规", "司法解释", "办法")),
    ("K02", "政策研究", ("科技保险", "补贴", "补偿机制", "试点", "政策")),
]
DEFAULT_KB = ("K06", "行业技术")
CHUNK_LEN = 1000
COMMIT_EVERY = 25
MAX_TEXT_CHARS = 12_000_000  # mediumtext 安全上限


def is_junk_path(path: Path) -> bool:
    return any(part.startswith("._") for part in path.parts)


def choose_book_mds(books_root: Path) -> list[Path]:
    by_book: dict[str, list[Path]] = defaultdict(list)
    for path in books_root.rglob("*.md"):
        if is_junk_path(path):
            continue
        by_book[path.relative_to(books_root).parts[0]].append(path)
    chosen: list[Path] = []
    for paths in by_book.values():
        autos = [p for p in paths if p.parent.name == "auto"]
        vlms = [p for p in paths if p.parent.name == "vlm"]
        others = [p for p in paths if p.parent.name not in ("auto", "vlm")]
        chosen.extend(autos or vlms or others)
    return chosen


def collect_candidates(root: Path) -> list[Path]:
    out: list[Path] = []
    books = root / "保险书籍_md"
    if books.exists():
        out.extend(choose_book_mds(books))
    for name in ("md_data", "data", "产险理赔机器人数据", "科技保险数据"):
        sub = root / name
        if not sub.exists():
            continue
        for path in sub.rglob("*.md"):
            if is_junk_path(path):
                continue
            lower = path.name.lower()
            if lower in SKIP_NAMES or any(part in lower for part in SKIP_NAME_PARTS):
                continue
            out.append(path)
    return out


def classify(title: str, rel: str) -> tuple[str, str]:
    blob = f"{title} {rel}"
    if "科技保险条款" in rel or "科技成果转化保险条款" in rel:
        return "K03", "科技保险条款"
    if "科技保险论文" in rel or "行业报告" in rel or "中国保险学会" in rel or "中国保险年鉴" in rel:
        return "K06", "行业研究"
    if "金融监管总局" in rel or "政策法规" in rel:
        return "K01", "法律法规"
    if "精算" in rel:
        return "K07", "精算资料"
    if "保险_250807" in rel:
        return "K07", "产品精算表"
    for kb, doc_type, keys in RULES:
        if any(k in blob for k in keys):
            return kb, doc_type
    return DEFAULT_KB


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_source(rel: str) -> str:
    rel = rel.replace("\\", "/")
    if len(rel) <= 120:
        return rel
    return rel[:60] + "…" + rel[-57:]


def _clean_title(raw: str) -> str:
    title = raw.replace("\ufffd", "").replace("�", "").strip()
    if " = " in title:
        left = title.split(" = ", 1)[0].strip()
        if any("\u4e00" <= ch <= "\u9fff" for ch in left):
            title = left
    if "__" in title:
        left, right = title.split("__", 1)
        right = right.strip()
        if right.lower().startswith("sheet") or right.rstrip("_") in {"现金价值表", "费率表"}:
            title = left.strip()
        else:
            title = f"{left.strip()} · {right}" if right else left.strip()
    return title[:240]


def title_of(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    if rel.parts and rel.parts[0] == "保险书籍_md" and len(rel.parts) >= 2:
        return _clean_title(rel.parts[1])
    stem = path.stem
    if stem.endswith("__index"):
        stem = stem[: -len("__index")]
    # 文件名是 32 位哈希时，改用更有意义的上级中文目录名
    if re.fullmatch(r"[0-9a-f]{32}", stem, re.I):
        for part in reversed(rel.parts[:-1]):
            if part in {"auto", "vlm", "download", "xls_to_md", "解压后"}:
                continue
            if re.fullmatch(r"[0-9a-f]{32}", part, re.I):
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in part):
                return _clean_title(part)
        # 最后再尝试读正文第一行标题
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:800]
            for line in head.splitlines():
                line = line.strip()
                if line.startswith("#"):
                    return _clean_title(re.sub(r"^#+\s*", "", line))
        except OSError:
            pass
    return _clean_title(stem)


def existing_hashes(session) -> set[str]:
    rows = session.scalars(select(KnowledgeDoc.tags)).all()
    out: set[str] = set()
    for tags in rows:
        for tag in tags or []:
            if isinstance(tag, str) and tag.startswith("sha256:"):
                out.add(tag.split(":", 1)[1])
    # 兼容尚未打哈希标签的已有文档：用正文哈希
    for text_body in session.scalars(select(KnowledgeDoc.text)).all():
        if text_body:
            out.add(content_hash(text_body.strip()))
    return out


def existing_titles(session) -> set[tuple[str, str]]:
    return {(kb, title) for kb, title in session.execute(select(KnowledgeDoc.kb, KnowledgeDoc.title)).all()}


def insert_doc(session, *, kb: str, title: str, source: str, doc_type: str, body: str, digest: str) -> int:
    ann = auto_annotate(title + body[:8000])
    doc = KnowledgeDoc(
        kb=kb,
        title=title,
        source=source,
        doc_type=doc_type,
        lines=ann["lines"],
        stages=ann["stages"],
        risk_types=ann["risk_types"],
        tags=["公开资料", f"sha256:{digest}", "外置导入"],
        text=body,
        quality="auto",
        data_level=0,
    )
    session.add(doc)
    session.flush()
    chunks = chunk_text(body, max_len=CHUNK_LEN)
    if not chunks:
        chunks = [(body[:CHUNK_LEN], "全文")]
    session.bulk_save_objects(
        [KnowledgeChunk(doc_id=doc.id, seq=i, text=chunk, position=pos) for i, (chunk, pos) in enumerate(chunks, start=1)]
    )
    return len(chunks)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: import_external_md.py <root_dir>")
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.exists():
        print("missing root", root)
        return 2

    candidates = collect_candidates(root)
    print("candidates", len(candidates), flush=True)

    with session_scope() as s:
        seen_hash = existing_hashes(s)
        seen_title = existing_titles(s)
    print("existing_hash", len(seen_hash), "existing_title", len(seen_title), flush=True)

    stats = defaultdict(int)
    started = time.time()
    pending = 0
    session = session_scope()
    s = session.__enter__()
    try:
        for idx, path in enumerate(candidates, start=1):
            try:
                body = path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception as exc:
                stats["read_error"] += 1
                print("READ_FAIL", path, exc, flush=True)
                continue
            if len(body) < 60:
                stats["too_short"] += 1
                continue
            if body.startswith(STUB_PREFIX):
                stats["stub"] += 1
                continue
            if len(body) > MAX_TEXT_CHARS:
                body = body[:MAX_TEXT_CHARS]
                stats["truncated"] += 1
            digest = content_hash(body)
            if digest in seen_hash:
                stats["dup_hash"] += 1
                continue
            rel = str(path.relative_to(root))
            title = title_of(path, root)
            kb, doc_type = classify(title, rel)
            if (kb, title) in seen_title:
                # 同库同标题：若正文不同则改标题后缀，避免静默丢文档
                title = f"{title}（{digest[:8]}）"[:240]
            seen_hash.add(digest)
            seen_title.add((kb, title))
            source = short_source(rel)
            n_chunks = insert_doc(s, kb=kb, title=title, source=source, doc_type=doc_type, body=body, digest=digest)
            stats["imported"] += 1
            stats[f"kb_{kb}"] += 1
            stats["chunks"] += n_chunks
            pending += 1
            if pending >= COMMIT_EVERY:
                s.commit()
                s.expire_all()
                pending = 0
            if idx % 50 == 0 or stats["imported"] % 50 == 0:
                elapsed = time.time() - started
                print(
                    json.dumps(
                        {
                            "scanned": idx,
                            "imported": stats["imported"],
                            "dup_hash": stats["dup_hash"],
                            "stub": stats["stub"],
                            "too_short": stats["too_short"],
                            "chunks": stats["chunks"],
                            "elapsed_s": int(elapsed),
                            "rate_doc_per_min": round(stats["imported"] / max(elapsed / 60, 1e-6), 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        if pending:
            s.commit()
    finally:
        session.__exit__(None, None, None)

    knowledge_index.invalidate()
    with session_scope() as s:
        by_kb = s.execute(select(KnowledgeDoc.kb, func.count()).group_by(KnowledgeDoc.kb).order_by(KnowledgeDoc.kb)).all()
        docs = s.scalar(select(func.count(KnowledgeDoc.id))) or 0
        chunks = s.scalar(select(func.count(KnowledgeChunk.id))) or 0
    print("DONE", dict(stats), flush=True)
    print("DB", {"docs": docs, "chunks": chunks, "by_kb": {k: n for k, n in by_kb}}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
