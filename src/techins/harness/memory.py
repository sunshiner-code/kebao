"""记忆管理：企业实体记忆、案例记忆、经验记忆的注入与提炼（对应设计文档 05 号第 6 节）。"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db import session_scope
from ..models import MemoryEntry, Reflection
from ..textutil import cosine_tokens, truncate

if TYPE_CHECKING:
    from .context import RunContext

TRIAL_SAMPLE_RATE = 0.2
MAX_EXPERIENCES = 5
MAX_CASES = 3


def _trial_selected(run_id: int, entry_id: int) -> bool:
    digest = hashlib.sha256(f"{run_id}:{entry_id}".encode()).digest()
    return digest[0] / 255 < TRIAL_SAMPLE_RATE


def _matches(conditions: list[str], context_text: str) -> float:
    if not conditions:
        return 0.5
    hit = sum(1 for c in conditions if c and c in context_text)
    return hit / len(conditions)


def inject(ctx: "RunContext", context_text: str, subjects: list[str]) -> list[dict]:
    injected: list[dict] = []
    with session_scope() as s:
        experiences = s.scalars(
            select(MemoryEntry).where(
                MemoryEntry.kind == "experience",
                MemoryEntry.skill_id.in_(ctx.profile.skill_ids()),
                MemoryEntry.status.in_(["active", "trial"]),
            )
        ).all()
        ranked = []
        for e in experiences:
            if e.status == "trial" and not _trial_selected(ctx.run_id, e.id):
                continue
            rate = (e.successes + 1) / (e.hits + 2)
            ranked.append((_matches(e.conditions or [], context_text) * 0.6 + rate * 0.4, e))
        ranked.sort(key=lambda t: -t[0])
        for score, e in ranked[:MAX_EXPERIENCES]:
            e.hits += 1
            injected.append({"id": e.id, "kind": "experience", "title": e.title, "text": e.text, "status": e.status, "level": e.data_level})
        for subject in subjects:
            entity = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "entity", MemoryEntry.subject_key == subject)).first()
            if entity is not None:
                entity.hits += 1
                injected.append({"id": entity.id, "kind": "entity", "title": entity.title, "text": entity.text, "level": entity.data_level})
        cases = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "case", MemoryEntry.agent_id == ctx.agent_id).order_by(MemoryEntry.created_at.desc()).limit(200)).all()
        scored = sorted(((cosine_tokens(context_text, c.text), c) for c in cases), key=lambda t: -t[0])
        for sim, c in scored[:MAX_CASES]:
            if sim < 0.15:
                continue
            c.hits += 1
            injected.append({"id": c.id, "kind": "case", "title": c.title, "text": truncate(c.text, 300), "similarity": round(sim, 3), "level": c.data_level})
    ctx.state["memory_injected"] = [m["id"] for m in injected]
    if injected:
        content = "【记忆注入】\n" + "\n".join(f"- （{ {'experience': '经验', 'entity': '企业记忆', 'case': '相似案例'}[m['kind']] }）{m['title']}：{m['text']}" for m in injected)
        ctx.messages.append({"role": "system", "content": content, "level": max(m["level"] for m in injected), "kind": "memory"})
    step_id = ctx.begin_step("memory", "记忆注入", zone="private")
    ctx.end_step(step_id, summary=f"注入经验 {sum(1 for m in injected if m['kind'] == 'experience')} 条、企业记忆 {sum(1 for m in injected if m['kind'] == 'entity')} 份、相似案例 {sum(1 for m in injected if m['kind'] == 'case')} 份", detail={"injected": injected})
    ctx.emit("memory.injected", {"items": [{k: m[k] for k in ("kind", "title")} for m in injected]})
    return injected


def distill(ctx: "RunContext", final_passed: bool) -> dict:
    """运行结束后的记忆提炼：更新企业实体记忆、写入案例记忆、从成功的反思中生成候选经验。"""
    created = {"entity": 0, "case": 0, "experience": 0}
    with session_scope() as s:
        for mid in ctx.state.get("memory_injected", []):
            entry = s.get(MemoryEntry, mid)
            if entry is not None and entry.kind == "experience" and final_passed:
                entry.successes += 1
        if ctx.profile.entity_facts:
            for fact in ctx.profile.entity_facts(ctx):
                entry = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "entity", MemoryEntry.subject_key == fact["subject"])).first()
                if entry is None:
                    entry = MemoryEntry(kind="entity", subject_key=fact["subject"], title=f"企业记忆：{fact['subject']}", text="", content={}, data_level=2)
                    s.add(entry)
                merged = dict(entry.content or {})
                for key, value in fact["facts"].items():
                    history = merged.get(key, [])
                    if not history or history[-1]["value"] != value:
                        history.append({"value": value, "run_id": ctx.run_id, "agent": ctx.agent_id})
                    merged[key] = history[-5:]
                entry.content = merged
                entry.text = "；".join(f"{k}：{v[-1]['value']}" for k, v in merged.items())
                entry.source_run_id = ctx.run_id
                created["entity"] += 1
        if ctx.profile.case_summary:
            case = ctx.profile.case_summary(ctx)
            if case:
                s.add(MemoryEntry(kind="case", agent_id=ctx.agent_id, subject_key=case.get("subject"), title=case["title"], text=case["text"], content=case.get("content", {}), data_level=case.get("level", 2), source_run_id=ctx.run_id))
                created["case"] += 1
        reflections = s.scalars(select(Reflection).where(Reflection.run_id == ctx.run_id)).all()
        for r in reflections:
            if r.outcome != "resolved" or r.experience_id:
                continue
            conditions = list(r.categories or [])
            text = f"当出现「{'、'.join(r.categories or ['评估未达标'])}」时：{r.strategy}"
            exp = MemoryEntry(kind="experience", agent_id=ctx.agent_id, skill_id=ctx.profile.core_skill, title=f"反思沉淀：{truncate(r.diagnosis, 40)}", text=truncate(text, 400), conditions=conditions, status="trial", data_level=1, source_run_id=ctx.run_id)
            s.add(exp)
            s.flush()
            r.experience_id = exp.id
            created["experience"] += 1
    return created


def human_revision_experience(agent_id: str, skill_id: str, field_path: str, reason: str, run_id: int | None) -> None:
    """人工修订沉淀：同一字段被反复修订时，生成候选经验条目。"""
    with session_scope() as s:
        key = f"revision:{agent_id}:{field_path}"
        entry = s.scalars(select(MemoryEntry).where(MemoryEntry.kind == "revision", MemoryEntry.subject_key == key)).first()
        if entry is None:
            entry = MemoryEntry(kind="revision", agent_id=agent_id, skill_id=skill_id, subject_key=key, title=f"人工修订聚类：{field_path}", text="", content={"reasons": []})
            s.add(entry)
        reasons = list((entry.content or {}).get("reasons", [])) + ([reason] if reason else [])
        entry.content = {"reasons": reasons[-20:]}
        entry.hits += 1
        entry.text = f"字段 {field_path} 已被人工修订 {entry.hits} 次"
        if entry.hits >= 3 and not (entry.content or {}).get("promoted"):
            s.add(MemoryEntry(kind="experience", agent_id=agent_id, skill_id=skill_id, title=f"人工修订沉淀：{field_path}", text=f"生成「{field_path}」时需特别注意：{'；'.join(r for r in reasons[-3:] if r) or '人工多次调整该字段，应在输出前复核其依据'}", conditions=[field_path], status="trial", source_run_id=run_id))
            entry.content = {**entry.content, "promoted": True}
