"""把结构化精算报告渲染成 Word。数字直接来自报告，不做二次计算。"""

from __future__ import annotations

from pathlib import Path


def _add_table(document, columns: list, rows: list) -> None:
    table = document.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    for index, column in enumerate(columns):
        run = table.rows[0].cells[index].paragraphs[0].add_run(str(column))
        run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = "" if value is None else str(value)


def write_report_docx(report: dict, path: Path, *, project_name: str = "", version: int | None = None) -> str:
    import docx

    document = docx.Document()
    document.add_heading(report.get("title") or "保险费率精算报告", level=0)
    meta = [f"状态：{report.get('status') or '待精算人员审定'}"]
    if project_name:
        meta.insert(0, f"项目：{project_name}")
    if version is not None:
        meta.insert(1 if project_name else 0, f"版本：V{version}")
    meta.append("已签署" if report.get("signed") else "未签署")
    meta.append("已报批" if report.get("filed") else "未报批")
    document.add_paragraph("　|　".join(meta))
    if report.get("reviewed_by"):
        document.add_paragraph(f"审定人：{report['reviewed_by']}　时间：{report.get('reviewed_at') or '—'}")
    if report.get("review_comment"):
        document.add_paragraph(f"审定意见：{report['review_comment']}")
    if report.get("provisional"):
        document.add_paragraph("提示：数据不足，报告中的数值为暂定，须经精算人员复核。")
    for section in report.get("sections") or []:
        document.add_heading(section.get("title") or "", level=1)
        for block in section.get("blocks") or []:
            kind = block.get("type")
            if kind == "kv":
                _add_table(document, ["项目", "内容"], [[item.get("label"), item.get("value")] for item in block.get("items") or []])
            elif kind == "table":
                _add_table(document, block.get("columns") or [], block.get("rows") or [])
            elif kind == "list":
                for item in block.get("items") or []:
                    document.add_paragraph(str(item), style="List Bullet")
            elif kind == "checks":
                for item in block.get("items") or []:
                    mark = "通过" if item.get("passed") else "未通过"
                    note = f"（{item['note']}）" if item.get("note") else ""
                    document.add_paragraph(f"[{mark}] {item.get('item')}{note}")
            elif kind == "text":
                document.add_paragraph(str(block.get("text") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path.name