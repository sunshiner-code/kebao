"""智能体配置包：六个业务智能体都是"统一运行时 + 各自配置包"。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .context import RunContext

PLATFORM_TOOLS = [
    "todo_write",
    "load_skill",
    "stage_complete",
    "request_human",
    "finish",
    "spawn_subagents",
    "start_background",
    "wait_background",
    "send_message",
    "check_mailbox",
    "read_workspace",
    "search_knowledge",
]


@dataclass
class Dimension:
    key: str
    name: str
    layer: str  # hard / metric / review / evidence
    fn: Callable[["RunContext", dict], tuple[Any, bool, str]]
    threshold: str
    weight: float = 1.0
    category: str = "推理错误"


@dataclass
class ReflectionRule:
    dims: list[str]
    category: str
    strategy: str
    steps: Callable[["RunContext", dict], list[dict]]


@dataclass
class AgentProfile:
    agent_id: str
    name: str
    version: str
    core_skill: str
    shared_skills: list[str]
    description: str
    initialize: Callable[["RunContext"], None]
    finalize: Callable[["RunContext"], dict]
    start_mode: Callable[[dict], dict]
    conditions: dict[str, Callable[["RunContext"], bool]] = field(default_factory=dict)
    stage_rubrics: dict[str, list[Dimension]] = field(default_factory=dict)
    final_rubric: list[Dimension] = field(default_factory=list)
    review_task: str | None = None
    review_inputs: Callable[["RunContext"], dict] | None = None
    reflection_rules: list[ReflectionRule] = field(default_factory=list)
    hitl_builders: dict[str, Callable[["RunContext"], dict]] = field(default_factory=dict)
    hitl_appliers: dict[str, Callable[["RunContext", dict], str]] = field(default_factory=dict)
    final_confirm: str | None = None
    on_confirm: Callable[["RunContext", dict], dict] | None = None
    entity_facts: Callable[["RunContext"], list[dict]] | None = None
    case_summary: Callable[["RunContext"], dict] | None = None
    priority: int = 3
    pass_score: float = 75.0
    max_reflection_rounds: int = 3
    extra_tools: list[str] = field(default_factory=list)
    resolvers: dict[str, Callable[["RunContext"], Any]] = field(default_factory=dict)
    procedure_name: str | None = None  # 使用技能中的子规程（用于子智能体或特定运行目的）
    purposes: dict[str, "AgentProfile"] = field(default_factory=dict)
    planner: str = "auto"  # auto：配置了大模型时由大模型规划；procedure：高频短运行固定按技能规程执行
    plan_confirm: bool = True  # 冷启动或半自动时是否需要人工确认计划（固定规程的高频短运行不需要）

    def skill_ids(self) -> list[str]:
        return [self.core_skill] + list(self.shared_skills)

    def tool_whitelist(self) -> set[str]:
        from .skills import skill_library

        names = set(PLATFORM_TOOLS) | set(self.extra_tools)
        for sid in self.skill_ids():
            try:
                names |= set(skill_library.get(sid).tools)
            except KeyError:
                continue
        return names


_PROFILES: dict[str, AgentProfile] = {}


def register_profile(profile: AgentProfile) -> AgentProfile:
    _PROFILES[profile.agent_id] = profile
    return profile


def get_profile(agent_id: str, purpose: str = "main") -> AgentProfile:
    base = _PROFILES[agent_id]
    if purpose and purpose != "main" and purpose in base.purposes:
        return base.purposes[purpose]
    return base


def all_profiles() -> dict[str, AgentProfile]:
    return dict(_PROFILES)
