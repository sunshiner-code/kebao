"""从上传文件抽出正文。文本、Markdown、CSV、PDF 和 Word 与材料上传保持一致，其他格式返回空。"""

from __future__ import annotations

import io
from pathlib import Path

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def extract_text(name: str, raw: bytes) -> str | None:
    suffix = Path(name).suffix.lower()
    if suffix in (".txt", ".md", ".csv"):
        return raw.decode("utf-8", errors="ignore")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)
    if suffix == ".docx":
        import docx

        return "\n".join(par.text for par in docx.Document(io.BytesIO(raw)).paragraphs)
    return None
