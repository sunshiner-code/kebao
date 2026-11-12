"""修复知识库中异常标题：MD5 哈希名、尾部乱码、双语书名拼音/英文尾巴、Excel 表名双下划线等。

优先从正文开头标题还原；否则用来源路径中的中文片段。

用法：
  .venv/bin/python scripts/fix_knowledge_titles.py            # 实改
  .venv/bin/python scripts/fix_knowledge_titles.py --dry-run  # 只预览
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import PurePosixPath

from sqlalchemy import text

from techins.db import session_scope
from techins.knowledge.index import knowledge_index
from techins.services.knowledge_admin import invalidate_library_stats

HASH_TITLE = re.compile(r"^[0-9a-f]{32}$", re.I)
SHEET_TAIL = re.compile(r"^(Sheet\s*\d+|现金价值表_?|费率表_?|表\d*)$", re.I)
ASCII_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{4,}$")
CJK = re.compile(r"[\u4e00-\u9fff]")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
BATCH = 200


def has_cjk(s: str) -> bool:
    return bool(CJK.search(s or ""))


def normalize(s: str) -> str:
    s = (s or "").replace("\ufffd", "").replace("�", "")
    s = re.sub(r"\s+", " ", s).strip(" .\t\r\n-_")
    return s[:240]


def title_from_preview(preview: str) -> str | None:
    if not preview:
        return None
    for raw in preview.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = MD_HEADING.match(line)
        if m:
            line = m.group(2).strip()
        if line.startswith("<") or line.startswith("|") or line.startswith("```"):
            continue
        if line.startswith("![") or line.startswith("http"):
            continue
        # 跳过纯公式/表格碎片
        if len(line) < 4:
            continue
        if HASH_TITLE.match(line):
            continue
        # 去掉 markdown 加粗
        line = re.sub(r"[*_`]+", "", line).strip()
        line = normalize(line)
        if len(line) >= 4:
            return line
    return None


def company_from_source(source: str) -> str:
    parts = PurePosixPath((source or "").replace("\\", "/")).parts
    skip = {"md_data", "data", "import_md", "保险_250807", "xls_to_md", "download", "auto", "vlm", "解压后"}
    for p in parts:
        if p in skip or p.endswith(".md") or HASH_TITLE.match(p):
            continue
        if has_cjk(p) and 2 <= len(p) <= 40:
            return p
    return "精算资料"


def path_candidate(source: str) -> str | None:
    """从来源路径挑一段更像书名/产品名的中文。"""
    parts = list(PurePosixPath((source or "").replace("\\", "/")).parts)
    skip = {"md_data", "data", "import_md", "保险_250807", "xls_to_md", "download", "auto", "vlm", "解压后", "保险书籍_md"}
    cands: list[str] = []
    for p in parts:
        stem = p[:-3] if p.endswith(".md") else p
        if stem.endswith("__index"):
            stem = stem[: -len("__index")]
        if stem in skip or HASH_TITLE.match(stem) or stem.startswith("._"):
            continue
        if has_cjk(stem) and len(stem) >= 4:
            cands.append(stem)
    if not cands:
        return None
    # 优先更长的中文名（通常是产品目录）
    cands.sort(key=lambda x: (len(CJK.findall(x)), len(x)), reverse=True)
    return normalize(cands[0].split("__", 1)[0])


def needs_fix(title: str) -> bool:
    t = title or ""
    if not t:
        return True
    if HASH_TITLE.match(t):
        return True
    if "�" in t or "\ufffd" in t:
        return True
    if "__" in t:
        return True
    if " = " in t and has_cjk(t):
        return True
    if ASCII_CODE.match(t) and not has_cjk(t):
        return True
    return False


def propose_title(title: str, source: str, preview: str) -> str | None:
    original = title or ""
    t = normalize(original.replace("�", "").replace("\ufffd", ""))

    if HASH_TITLE.match(original) or HASH_TITLE.match(t):
        got = title_from_preview(preview)
        if got and not HASH_TITLE.match(got):
            return got
        path_t = path_candidate(source)
        if path_t:
            return path_t
        return normalize(f"{company_from_source(source)}精算资料（{original[:8]}）")

    if " = " in t and has_cjk(t):
        left, right = [x.strip() for x in t.split(" = ", 1)]
        # 常见「中文 = 英文/拼音」取左侧；少数「英文 = 中文」取右侧
        if has_cjk(left) and len(left) >= 2:
            left = re.split(r"\s+_\s+", left, maxsplit=1)[0].strip()
            t = left
        elif has_cjk(right) and len(right) >= 2:
            t = right.split(" = ", 1)[0].strip()

    if "__" in t:
        left, right = t.split("__", 1)
        left, right = left.strip(), right.strip()
        if SHEET_TAIL.match(right) or right.lower().startswith("sheet"):
            t = left
        elif has_cjk(left) and (not has_cjk(right) or len(right) <= 6):
            t = left if len(left) >= len(right) else f"{left} · {right}"
        else:
            t = f"{left} · {right}" if right else left

    if ASCII_CODE.match(t) and not has_cjk(t):
        got = title_from_preview(preview)
        if got and (has_cjk(got) or len(got) > len(t)):
            t = got
        else:
            path_t = path_candidate(source)
            if path_t:
                t = path_t

    t = normalize(t)
    if not t or t == normalize(original):
        # 仅去尾部乱码也算修复
        cleaned = normalize(original.replace("�", "").replace("\ufffd", ""))
        if cleaned and cleaned != original:
            return cleaned
        return None
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条，0 表示全部")
    args = ap.parse_args()

    with session_scope() as s:
        meta = s.execute(text("SELECT id, title, source FROM knowledge_docs ORDER BY id")).all()
    targets = [(i, t, src) for i, t, src in meta if needs_fix(t or "")]
    if args.limit:
        targets = targets[: args.limit]
    print(f"candidates {len(targets)} / total {len(meta)}", flush=True)

    changed = 0
    samples: list[tuple[int, str, str]] = []
    for i in range(0, len(targets), BATCH):
        batch = targets[i : i + BATCH]
        ids = [x[0] for x in batch]
        id_list = ",".join(str(x) for x in ids)
        with session_scope() as s:
            previews = {
                int(r.id): (r.preview or "")
                for r in s.execute(
                    text(f"SELECT id, SUBSTR(text, 1, 800) AS preview FROM knowledge_docs WHERE id IN ({id_list})")
                )
            }
            updates: list[tuple[str, int]] = []
            for doc_id, title, source in batch:
                new_title = propose_title(title or "", source or "", previews.get(doc_id, ""))
                if not new_title or new_title == title:
                    continue
                updates.append((new_title, doc_id))
                if len(samples) < 30:
                    samples.append((doc_id, title or "", new_title))
            if updates and not args.dry_run:
                for new_title, doc_id in updates:
                    s.execute(text("UPDATE knowledge_docs SET title = :t WHERE id = :id"), {"t": new_title, "id": doc_id})
            changed += len(updates)
        print(f"batch {i // BATCH + 1}: +{len(updates) if 'updates' in dir() else 0}, changed_total={changed}", flush=True)

    print("samples:", flush=True)
    for doc_id, old, new in samples:
        print(f"  #{doc_id}\n    OLD {old}\n    NEW {new}", flush=True)
    print(f"done changed={changed} dry_run={args.dry_run}", flush=True)

    if changed and not args.dry_run:
        knowledge_index.invalidate()
        invalidate_library_stats()
        print("index/stats cache invalidated", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
