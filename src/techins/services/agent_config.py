"""智能体配置包版本：系统指令外的可调参数（达标线、反思上限、规划方式、计划确认、模型策略、工具禁用、预算、经验注入）。

版本状态：draft 草稿 → canary 灰度（按比例分流新运行）→ active 生效 → retired 退役；支持回滚到上一生效版本。
"""

from __future__ import annotations

import dataclasses
import zlib

from sqlalchemy import select

from ..db import session_scope, utcnow
from ..harness.agent import AgentProfile, all_profiles, get_profile
from ..models import AgentConfig

FIELDS = [
    {"key": "pass_score", "label": "最终评估达标线", "type": "number", "min": 50, "max": 95},
    {"key": "max_reflection_rounds", "label": "最终评估反思上限（轮）", "type": "number", "min": 0, "max": 6, "integer": True},
    {"key": "planner", "label": "规划方式", "type": "select", "options": [{"value": "auto", "label": "配置大模型时由大模型规划"}, {"value": "procedure", "label": "固定按技能规程执行"}]},
    {"key": "plan_confirm", "label": "冷启动或半自动时人工确认计划", "type": "boolean"},
    {"key": "allow_cloud", "label": "允许使用云端模型（关闭则全部在私域推理）", "type": "boolean"},
    {"key": "experience_injection", "label": "注入经验与实体记忆", "type": "boolean"},
    {"key": "tool_blacklist", "label": "禁用工具", "type": "tags"},
    {"key": "budget", "label": "单次运行预算", "type": "object", "fields": [{"key": "cloud_cost", "label": "云端费用上限（元）", "type": "number", "min": 1}, {"key": "steps", "label": "步骤上限", "type": "number", "min": 20, "integer": True}, {"key": "wall_seconds", "label": "时长上限（秒）", "type": "number", "min": 60, "integer": True}]},
]


class ConfigVersionError(ValueError):
    pass


def base_config(agent_id: str) -> dict:
    p = get_profile(agent_id)
    return {"pass_score": p.pass_score, "max_reflection_rounds": p.max_reflection_rounds, "planner": p.planner, "plan_confirm": p.plan_confirm, "allow_cloud": True, "experience_injection": True, "tool_blacklist": [], "budget": {"cloud_cost": 200.0, "steps": 600, "wall_seconds": 4 * 3600}}


def version_label(cfg: AgentConfig | None, profile_version: str) -> str:
    return f"{profile_version}+{cfg.version}" if cfg else profile_version


def seed_configs() -> None:
    with session_scope() as s:
        have = {c.agent_id for c in s.scalars(select(AgentConfig)).all()}
        for aid in all_profiles():
            if aid not in have:
                s.add(AgentConfig(agent_id=aid, version="c1", status="active", config={**base_config(aid), "note": "初始配置（与智能体内置默认值一致）", "created_by": "系统初始化"}))


def config_dict(c: AgentConfig) -> dict:
    cfg = dict(c.config or {})
    return {"id": c.id, "agent_id": c.agent_id, "version": c.version, "status": c.status, "config": {k: cfg.get(k) for k in [f["key"] for f in FIELDS]}, "note": cfg.get("note", ""), "canary_ratio": cfg.get("canary_ratio"), "created_by": cfg.get("created_by"), "proposal_id": cfg.get("proposal_id"), "activated_at": cfg.get("activated_at"), "created_at": c.created_at.isoformat() + "Z"}


def list_versions(agent_id: str) -> list[dict]:
    with session_scope() as s:
        return [config_dict(c) for c in s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent_id).order_by(AgentConfig.id.desc())).all()]


def _validate(agent_id: str, cfg: dict) -> dict:
    merged = {**base_config(agent_id), **{k: v for k, v in cfg.items() if k in {f["key"] for f in FIELDS}}}
    if not 50 <= float(merged["pass_score"]) <= 95:
        raise ConfigVersionError("达标线需在 50–95 之间")
    if not 0 <= int(merged["max_reflection_rounds"]) <= 6:
        raise ConfigVersionError("反思上限需在 0–6 轮之间")
    if merged["planner"] not in ("auto", "procedure"):
        raise ConfigVersionError("规划方式无效")
    from ..harness.tools import registry

    p = get_profile(agent_id)
    unknown = [t for t in merged.get("tool_blacklist") or [] if t not in p.tool_whitelist() or (registry.has(t) and registry.get(t).platform)]
    if unknown:
        raise ConfigVersionError("只能禁用该智能体白名单中的业务工具：" + "、".join(unknown))
    merged["pass_score"] = float(merged["pass_score"])
    merged["max_reflection_rounds"] = int(merged["max_reflection_rounds"])
    return merged


def create_version(agent_id: str, cfg: dict, note: str, user_name: str, proposal_id: int | None = None) -> dict:
    merged = _validate(agent_id, cfg)
    with session_scope() as s:
        n = len(s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent_id)).all()) + 1
        c = AgentConfig(agent_id=agent_id, version=f"c{n}", status="draft", config={**merged, "note": note, "created_by": user_name, "proposal_id": proposal_id})
        s.add(c)
        s.flush()
        return config_dict(c)


def _get(s, version_id: int) -> AgentConfig:
    c = s.get(AgentConfig, version_id)
    if c is None:
        raise ConfigVersionError("配置版本不存在")
    return c


def start_canary(version_id: int, ratio: float) -> dict:
    if not 0.05 <= ratio <= 0.5:
        raise ConfigVersionError("灰度比例需在 5%–50% 之间")
    with session_scope() as s:
        c = _get(s, version_id)
        if c.status not in ("draft", "canary"):
            raise ConfigVersionError("只有草稿版本可以进入灰度")
        for other in s.scalars(select(AgentConfig).where(AgentConfig.agent_id == c.agent_id, AgentConfig.status == "canary", AgentConfig.id != c.id)).all():
            other.status = "draft"
        c.status = "canary"
        c.config = {**c.config, "canary_ratio": ratio, "canary_started_at": utcnow().isoformat() + "Z"}
        return config_dict(c)


def activate(version_id: int) -> dict:
    with session_scope() as s:
        c = _get(s, version_id)
        if c.status == "active":
            return config_dict(c)
        for other in s.scalars(select(AgentConfig).where(AgentConfig.agent_id == c.agent_id, AgentConfig.status == "active")).all():
            other.status = "retired"
            other.config = {**other.config, "retired_at": utcnow().isoformat() + "Z"}
        c.status = "active"
        c.config = {**c.config, "activated_at": utcnow().isoformat() + "Z", "canary_ratio": None}
        return config_dict(c)


def rollback(agent_id: str) -> dict:
    """回滚：当前生效版本退役，最近一个退役版本恢复生效；灰度中的版本退回草稿。"""
    with session_scope() as s:
        current = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent_id, AgentConfig.status == "active")).first()
        prev = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent_id, AgentConfig.status == "retired").order_by(AgentConfig.id.desc())).first()
        for c in s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent_id, AgentConfig.status == "canary")).all():
            c.status = "draft"
            c.config = {**c.config, "canary_ratio": None, "rolled_back_at": utcnow().isoformat() + "Z"}
        if prev is None:
            return {"rolled_back": None, "active": config_dict(current) if current else None}
        if current is not None:
            current.status = "rolled_back"
            current.config = {**current.config, "rolled_back_at": utcnow().isoformat() + "Z"}
        prev.status = "active"
        prev.config = {**prev.config, "activated_at": utcnow().isoformat() + "Z"}
        return {"rolled_back": current.version if current else None, "active": config_dict(prev)}


def stop_canary(version_id: int) -> dict:
    with session_scope() as s:
        c = _get(s, version_id)
        if c.status == "canary":
            c.status = "draft"
            c.config = {**c.config, "canary_ratio": None}
        return config_dict(c)


def choose_for_new_run(agent_id: str, seed: str) -> tuple[AgentConfig | None, dict]:
    """新运行使用的配置：灰度版本按比例分流（按运行种子哈希稳定分配），其余使用生效版本。"""
    with session_scope() as s:
        rows = s.scalars(select(AgentConfig).where(AgentConfig.agent_id == agent_id, AgentConfig.status.in_(["active", "canary"]))).all()
        for r in rows:
            s.expunge(r)
    active = next((r for r in rows if r.status == "active"), None)
    canary = next((r for r in rows if r.status == "canary"), None)
    if canary and (zlib.crc32(seed.encode()) % 1000) / 1000 < float((canary.config or {}).get("canary_ratio") or 0):
        return canary, dict(canary.config)
    return active, dict(active.config) if active else {}


def get_config(version_id: int) -> AgentConfig | None:
    with session_scope() as s:
        c = s.get(AgentConfig, version_id)
        if c is not None:
            s.expunge(c)
        return c


_CLAUSE_FLOW = {"planner": "procedure", "plan_confirm": False, "max_reflection_rounds": 1}


def apply(profile: AgentProfile, cfg: dict) -> AgentProfile:
    changes = {}
    if cfg:
        for key in ("pass_score", "max_reflection_rounds", "planner", "plan_confirm"):
            if key in cfg and cfg[key] is not None and getattr(profile, key) != cfg[key]:
                changes[key] = cfg[key]
    if profile.agent_id == "AG-CD":
        changes.update(_CLAUSE_FLOW)
    if not changes or all(getattr(profile, key) == value for key, value in changes.items()):
        return profile
    return dataclasses.replace(profile, **changes)
