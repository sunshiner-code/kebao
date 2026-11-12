"""技能加载器：两层加载（目录常驻、全文按需），并解析作业规程供规程策略执行。

技能文件为 Markdown，固定七个分区：元信息、作业规程、领域参考、输出规范、评估量表、经验条目、工具白名单。
作业规程中的每个步骤用"### 步骤 N：标题"开头，下面的"- 键：值"行描述工具、参数、阶段评估、人审、条件等。
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import MemoryEntry

SECTION_NAMES = ["元信息", "作业规程", "领域参考", "输出规范", "评估量表", "经验条目", "工具白名单"]


@dataclass
class ProcedureStep:
    key: str
    title: str
    tool: str | None
    args: dict[str, str] = field(default_factory=dict)
    stage_eval: bool = False
    hitl: str | None = None
    condition: str | None = None
    purpose: str = ""
    note: str = ""
    background: bool = False
    group: str | None = None


@dataclass
class Skill:
    skill_id: str
    title: str
    summary: str
    when: str
    version: str
    sections: dict[str, str]
    procedure: list[ProcedureStep]
    tools: list[str]
    digest: str
    path: Path
    subprocedures: dict[str, list[ProcedureStep]] = field(default_factory=dict)

    def catalog_line(self) -> str:
        return f"{self.skill_id} {self.title}：{self.summary}（{self.when}）"


_ATTR = re.compile(r"^-\s*([^：:]+)[：:]\s*(.*)$")


def _parse_args(raw: str) -> dict[str, str]:
    args = {}
    for part in re.split(r"[;；]", raw):
        if "=" in part:
            k, v = part.split("=", 1)
            args[k.strip()] = v.strip()
    return args


def _parse_steps(block: str, prefix: str) -> list[ProcedureStep]:
    steps: list[ProcedureStep] = []
    for m in re.finditer(r"^###\s*(?:步骤|子步骤)\s*([\w.-]+)[：:]\s*(.+?)\s*$([\s\S]*?)(?=^###\s|\Z)", block, re.M):
        number, title, body = m.group(1), m.group(2), m.group(3)
        attrs: dict[str, str] = {}
        for line in body.strip().splitlines():
            am = _ATTR.match(line.strip())
            if am:
                attrs[am.group(1).strip()] = am.group(2).strip()
        steps.append(
            ProcedureStep(
                key=f"{prefix}-{number}",
                title=title,
                tool=attrs.get("工具") or None,
                args=_parse_args(attrs.get("参数", "")),
                stage_eval=attrs.get("阶段评估", "否") == "是",
                hitl=attrs.get("人审") or None,
                condition=attrs.get("条件") or None,
                purpose=attrs.get("目的", ""),
                note=attrs.get("说明", ""),
                background=attrs.get("后台", "否") == "是",
                group=attrs.get("分组") or None,
            )
        )
    return steps


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    header = re.search(r"^#\s*(SK-[\w]+)\s+(.+)$", text, re.M)
    if not header:
        raise ValueError(f"技能文件缺少标题：{path}")
    skill_id, title = header.group(1), header.group(2).strip()
    sections: dict[str, str] = {}
    for m in re.finditer(r"^##\s*(.+?)\s*$([\s\S]*?)(?=^##\s|\Z)", text, re.M):
        sections[m.group(1).strip()] = m.group(2).strip()
    meta = {}
    for line in sections.get("元信息", "").splitlines():
        am = _ATTR.match(line.strip())
        if am:
            meta[am.group(1).strip()] = am.group(2).strip()
    procedure = _parse_steps(sections.get("作业规程", ""), skill_id)
    subprocedures: dict[str, list[ProcedureStep]] = {}
    for name, body in sections.items():
        if name.startswith("子规程"):
            sub_key = name.split("：", 1)[-1].strip() if "：" in name else name
            subprocedures[sub_key] = _parse_steps(body, f"{skill_id}-{sub_key}")
    tools = [line.strip("- ").strip() for line in sections.get("工具白名单", "").splitlines() if line.strip().startswith("-")]
    return Skill(
        skill_id=skill_id,
        title=title,
        summary=meta.get("简介", ""),
        when=meta.get("何时使用", ""),
        version=meta.get("版本", "1.0.0"),
        sections=sections,
        procedure=procedure,
        tools=tools,
        digest=hashlib.sha256(text.encode()).hexdigest()[:12],
        path=path,
        subprocedures=subprocedures,
    )


class SkillLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._skills: dict[str, Skill] = {}
        self._mtimes: dict[Path, float] = {}
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        for path in sorted(self.root.glob("*.md")):
            mtime = path.stat().st_mtime
            if self._mtimes.get(path) == mtime:
                continue
            skill = parse_skill(path)
            self._skills[skill.skill_id] = skill
            self._mtimes[path] = mtime

    def get(self, skill_id: str) -> Skill:
        with self._lock:
            self._refresh()
            if skill_id not in self._skills:
                raise KeyError(f"未找到技能：{skill_id}")
            return self._skills[skill_id]

    def all(self) -> list[Skill]:
        with self._lock:
            self._refresh()
            return sorted(self._skills.values(), key=lambda s: s.skill_id)

    def catalog(self, skill_ids: list[str]) -> str:
        return "\n".join(self.get(sid).catalog_line() for sid in skill_ids)

    def render(self, skill_id: str, section: str | None = None, experience_limit: int = 6) -> str:
        """第二层加载：返回技能全文或某一分区；经验条目分区会合并经验库中生效与试用中的条目。"""
        skill = self.get(skill_id)
        parts = [f"# {skill.skill_id} {skill.title}（版本 {skill.version}）"]
        names = [section] if section else SECTION_NAMES + [n for n in skill.sections if n.startswith("子规程")]
        for name in names:
            if name not in skill.sections and name != "经验条目":
                continue
            body = skill.sections.get(name, "")
            if name == "经验条目":
                body = (body + "\n" + self._dynamic_experiences(skill_id, experience_limit)).strip()
            parts.append(f"## {name}\n{body}")
        return "\n\n".join(parts)

    def _dynamic_experiences(self, skill_id: str, limit: int) -> str:
        with session_scope() as s:
            rows = s.scalars(
                select(MemoryEntry)
                .where(MemoryEntry.kind == "experience", MemoryEntry.skill_id == skill_id, MemoryEntry.status.in_(["active", "trial"]))
                .order_by(MemoryEntry.successes.desc(), MemoryEntry.updated_at.desc())
                .limit(limit)
            ).all()
            return "\n".join(f"- [{'生效' if r.status == 'active' else '试用'}] {r.text}" for r in rows)


skill_library = SkillLibrary(settings.skills_dir)
