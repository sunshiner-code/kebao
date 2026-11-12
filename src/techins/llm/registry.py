"""模型注册与默认路由策略（对应设计文档 06 号第 1、3 节）。"""

from __future__ import annotations

from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import ModelRegistryEntry, RoutingRule

TASK_TYPES = {
    "TT-01": "整体规划",
    "TT-02": "公开信息分析",
    "TT-03": "私有材料抽取",
    "TT-04": "私域脱敏改写",
    "TT-05": "综合推理",
    "TT-06": "长文本生成",
    "TT-07": "结构化格式化",
    "TT-08": "评审",
    "TT-09": "反思诊断",
    "TT-10": "图像理解",
    "TT-11": "检索向量化与重排",
    "TT-12": "语音转写",
    "TT-13": "统计与机器学习计算",
    "TT-14": "上下文压缩",
    "TT-15": "对话解释",
}


def _models() -> list[dict]:
    enabled = not settings.llm_disabled
    cloud_ok = enabled and bool(settings.cloud_llm_base_url and settings.cloud_llm_model)
    private_ok = enabled and bool(settings.private_llm_base_url and settings.private_llm_model)
    return [
        dict(
            id="cloud-llm",
            name="云端旗舰大模型",
            zone="cloud",
            kind="llm",
            tier="M1",
            capabilities=["plan", "reason", "long_text", "review", "extract_public", "format", "dialog", "tool_calling"],
            max_level=1,
            base_url=settings.cloud_llm_base_url,
            model_name=settings.cloud_llm_model,
            context_window=1000000,
            price_per_1k=0.004,
            params=settings.cloud_llm_extra,
            configured=cloud_ok,
        ),
        dict(
            id="cloud-builtin",
            name="云域推理端点（内置引擎）",
            zone="cloud",
            kind="builtin",
            tier="M1",
            capabilities=["plan", "reason", "long_text", "review", "extract_public", "format", "dialog"],
            max_level=1,
            context_window=32000,
            price_per_1k=0.0,
            configured=True,
        ),
        dict(
            id="private-llm",
            name="私域大模型",
            zone="private",
            kind="llm",
            tier="M4",
            capabilities=["plan", "reason", "long_text", "review", "compact", "extract_private", "vision", "rewrite", "format", "dialog", "tool_calling"],
            max_level=3,
            base_url=settings.private_llm_base_url,
            model_name=settings.private_llm_model,
            context_window=1000000,
            params=settings.private_llm_extra,
            configured=private_ok,
        ),
        dict(
            id="private-builtin",
            name="私域推理引擎（内置）",
            zone="private",
            kind="builtin",
            tier="M4",
            capabilities=["plan", "reason", "long_text", "review", "compact", "extract_private", "rewrite", "format", "vision", "dialog", "extract_public"],
            max_level=3,
            context_window=32000,
            configured=True,
        ),
        dict(
            id="private-retrieval",
            name="私域检索模型（BM25 + 字符向量）",
            zone="private",
            kind="retrieval",
            tier="M5",
            capabilities=["retrieval"],
            max_level=3,
            configured=True,
        ),
        dict(
            id="private-asr",
            name="私域语音转写模型",
            zone="private",
            kind="asr",
            tier="M6",
            capabilities=["asr"],
            max_level=3,
            configured=False,
        ),
        dict(
            id="private-ml",
            name="专用算法模型服务",
            zone="private",
            kind="ml",
            tier="M7",
            capabilities=["ml"],
            max_level=3,
            configured=True,
        ),
    ]


_DEFAULT_RULES = [
    ("RR-01", "整体规划", ["TT-01"], [0, 1, 2], ["cloud-llm", "cloud-builtin"], ["private-llm", "private-builtin"], 100),
    ("RR-02", "整体规划（高敏）", ["TT-01"], [3], ["private-llm"], ["private-builtin"], 90),
    ("RR-03", "公开信息分析", ["TT-02"], [0, 1], ["cloud-llm", "cloud-builtin"], ["private-llm", "private-builtin"], 100),
    ("RR-04", "私有材料抽取", ["TT-03"], [0, 1, 2, 3], ["private-llm"], ["private-builtin"], 100),
    # 图像理解：设计上等同 L3，只走私域多模态（Qwen / MiniCPM 等），不得出域
    ("RR-04b", "图像理解（私域多模态）", ["TT-10"], [0, 1, 2, 3], ["private-llm"], ["private-builtin"], 110),
    ("RR-05", "私域脱敏改写", ["TT-04"], [0, 1, 2, 3], ["private-llm"], ["private-builtin"], 100),
    ("RR-06", "综合推理与长文本", ["TT-05", "TT-06", "TT-15"], [0, 1, 2], ["cloud-llm", "cloud-builtin"], ["private-llm", "private-builtin"], 100),
    ("RR-07", "综合推理与长文本（高敏）", ["TT-05", "TT-06", "TT-15"], [3], ["private-llm"], ["private-builtin"], 90),
    ("RR-08", "结构化格式化", ["TT-07"], [0, 1], ["cloud-llm", "cloud-builtin"], ["private-builtin"], 100),
    ("RR-09", "结构化格式化（私域）", ["TT-07"], [2, 3], ["private-llm"], ["private-builtin"], 95),
    ("RR-10", "评审与反思", ["TT-08", "TT-09"], [0, 1, 2], ["cloud-llm", "cloud-builtin"], ["private-llm", "private-builtin"], 100),
    ("RR-11", "评审与反思（高敏）", ["TT-08", "TT-09"], [3], ["private-llm"], ["private-builtin"], 90),
    ("RR-12", "上下文压缩", ["TT-14"], [0, 1], ["cloud-llm", "cloud-builtin"], ["private-builtin"], 100),
    ("RR-13", "上下文压缩（私域）", ["TT-14"], [2, 3], ["private-llm"], ["private-builtin"], 95),
    ("RR-14", "检索与计算", ["TT-11", "TT-13"], [0, 1, 2, 3], ["private-retrieval", "private-ml"], ["private-builtin"], 100),
    ("RR-15", "语音转写", ["TT-12"], [0, 1, 2, 3], ["private-asr"], [], 100),
]


def seed_registry() -> None:
    with session_scope() as s:
        for spec in _models():
            entry = s.get(ModelRegistryEntry, spec["id"])
            if entry is None:
                entry = ModelRegistryEntry(**spec)
                s.add(entry)
            else:
                for key in ("name", "capabilities", "base_url", "model_name", "configured", "params", "context_window"):
                    if key in spec:
                        setattr(entry, key, spec.get(key))
            entry.health = "healthy" if entry.configured else "unconfigured"
        old_vl = s.get(ModelRegistryEntry, "private-vl")
        if old_vl is not None:
            s.delete(old_vl)
        def _fold(chain: list | None) -> list:
            folded = []
            for item in chain or []:
                item = "private-llm" if item == "private-vl" else item
                if item not in folded:
                    folded.append(item)
            return folded
        for rule in s.scalars(select(RoutingRule)).all():
            rule.preferred = _fold(rule.preferred)
            rule.fallback = _fold(rule.fallback)
        # 同步默认路由：补齐新规则，并刷新内置规则的任务类型/级别/模型链（管理员自定义 scope 规则不覆盖）
        for rid, name, tts, levels, preferred, fallback, priority in _DEFAULT_RULES:
            row = s.get(RoutingRule, rid)
            if row is None:
                s.add(
                    RoutingRule(
                        id=rid,
                        name=name,
                        task_types=tts,
                        data_levels=levels,
                        preferred=preferred,
                        fallback=fallback,
                        priority=priority,
                        scope="global",
                        enabled=True,
                    )
                )
                continue
            if (row.scope or "global") != "global":
                continue
            row.name = name
            row.task_types = tts
            row.data_levels = levels
            row.preferred = preferred
            row.fallback = fallback
            row.priority = priority
            row.enabled = True if row.enabled is None else row.enabled
        # 兼容旧库：若仍有规则把 TT-10 与云端绑在一起，剥离图像任务
        for rule in s.scalars(select(RoutingRule)).all():
            types = list(rule.task_types or [])
            if "TT-10" in types and rule.id != "RR-04b":
                preferred_zones = []
                models = {m.id: m for m in s.scalars(select(ModelRegistryEntry)).all()}
                for mid in list(rule.preferred or []) + list(rule.fallback or []):
                    m = models.get(mid)
                    if m:
                        preferred_zones.append(m.zone)
                if "cloud" in preferred_zones:
                    rule.task_types = [t for t in types if t != "TT-10"]


class RoutingConfigError(ValueError):
    pass


def validate_rule(rule: dict, models: dict[str, ModelRegistryEntry]) -> None:
    """配置校验：L3 不得指向云域；兜底链不得从私域退回云域。"""
    chain = list(rule.get("preferred", [])) + list(rule.get("fallback", []))
    zones = [models[m].zone for m in chain if m in models]
    unknown = [m for m in chain if m not in models]
    if unknown:
        raise RoutingConfigError(f"引用了未注册的模型：{', '.join(unknown)}")
    if 3 in rule.get("data_levels", []) and "cloud" in zones:
        raise RoutingConfigError("L3 高敏数据不得路由到云域模型")
    seen_private = False
    for zone in zones:
        if zone == "private":
            seen_private = True
        elif zone == "cloud" and seen_private:
            raise RoutingConfigError("兜底链不允许从私域退回云域")
