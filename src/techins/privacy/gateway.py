"""隐私网关：私域数据出域前的脱敏、最小化、二次扫描与审计；云域结果返回后的回填。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..db import session_scope
from ..models import EgressLog
from ..textutil import amount_bucket, parse_amounts, truncate
from .classifier import LABELS, Finding, classify_text, masking_method
from .vault import TokenVault

_RE_PROVINCE_CITY = re.compile(r"^([\u4e00-\u9fa5]{2,7}?(?:省|自治区))?([\u4e00-\u9fa5]{1,8}?市)?")
_RE_INJECTION = re.compile(
    r"(忽略(之前|以上|前面)的?(所有)?(指令|要求)|ignore (all )?(previous|prior) instructions|你现在是|system prompt|请输出你的系统提示)",
    re.IGNORECASE,
)

MAX_EGRESS_CHARS = 60000


class PrivacyViolation(RuntimeError):
    def __init__(self, message: str, findings: list[dict]):
        super().__init__(message)
        self.findings = findings


@dataclass
class EgressReport:
    rules_hit: Counter = field(default_factory=Counter)
    fields: set[str] = field(default_factory=set)
    placeholder_count: int = 0
    truncated: bool = False

    def as_dict(self) -> dict:
        return {
            "rules_hit": {LABELS.get(k, k.split(":", 1)[-1]): v for k, v in self.rules_hit.items()},
            "fields": sorted(self.fields),
            "placeholder_count": self.placeholder_count,
            "truncated": self.truncated,
        }


def _generalize_address(address: str) -> str:
    m = _RE_PROVINCE_CITY.match(address)
    prefix = "".join(g for g in (m.groups() if m else ()) if g)
    return f"{prefix or '某地'}某区域"


def _minimize(obj: Any, drop: set[str]) -> Any:
    """出域最小化：删除策略指定的字段。"""
    if isinstance(obj, dict):
        return {k: _minimize(v, drop) for k, v in obj.items() if k not in drop}
    if isinstance(obj, list):
        return [_minimize(v, drop) for v in obj]
    return obj


class PrivacyGateway:
    def desensitize_text(self, text: str, vault: TokenVault, report: EgressReport, path: str = "") -> str:
        if not text or not isinstance(text, str):
            return text
        findings = classify_text(text)
        if not findings:
            return text
        pieces: list[str] = []
        cursor = 0
        for f in findings:
            pieces.append(text[cursor : f.start])
            pieces.append(self._replacement(f, vault, report))
            cursor = f.end
            if path:
                report.fields.add(path)
        pieces.append(text[cursor:])
        return "".join(pieces)

    def _replacement(self, f: Finding, vault: TokenVault, report: EgressReport) -> str:
        report.rules_hit[f.kind] += 1
        method = masking_method(f.kind)
        if method == "drop":
            return f"（{f.label}已删除）"
        if method == "mask":
            keep = max(1, len(f.text) // 4)
            return f.text[:keep] + "*" * max(2, len(f.text) - 2 * keep) + f.text[-keep:]
        if f.kind == "address" or method == "generalize":
            return _generalize_address(f.text) if f.kind == "address" else f"（{f.label}已泛化）"
        if f.kind == "amount" or method == "bucket":
            values = parse_amounts(f.text)
            return f"（金额区间：{amount_bucket(values[0])}）" if values else "（金额已泛化）"
        if f.kind == "tech_secret":
            token = vault.token_for("tech_secret", f.text)
            report.placeholder_count += 1
            return f"{token}（核心技术细节，原文保留在私域）"
        token = vault.token_for(f.kind, f.text)
        report.placeholder_count += 1
        return token

    def desensitize(self, obj: Any, vault: TokenVault, report: EgressReport | None = None, path: str = "$") -> Any:
        report = report or EgressReport()
        if isinstance(obj, str):
            return self.desensitize_text(obj, vault, report, path)
        if isinstance(obj, dict):
            # 键也可能是敏感实体（例如以参与方名称为键的责任比例），与值一样脱敏
            return {(self.desensitize_text(k, vault, report, f"{path}.<key>") if isinstance(k, str) else k): self.desensitize(v, vault, report, f"{path}.{k}") for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.desensitize(v, vault, report, f"{path}[]") for v in obj]
        return obj

    def rehydrate(self, obj: Any, vault: TokenVault) -> Any:
        if isinstance(obj, str):
            return vault.rehydrate_text(obj)
        if isinstance(obj, dict):
            return {(vault.rehydrate_text(k) if isinstance(k, str) else k): self.rehydrate(v, vault) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.rehydrate(v, vault) for v in obj]
        return obj

    def scan(self, obj: Any, vault: TokenVault | None = None) -> list[dict]:
        """出域二次扫描：任何 L3 命中或映射库中的原文残留都视为泄漏。"""
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        problems = []
        for f in classify_text(text, include_amounts=False, include_companies=False):
            if f.level >= 3 and not f.text.startswith("【"):
                problems.append({"kind": f.label, "text": f.text[:4] + "…"})
        if vault is not None:
            for original in vault.originals():
                if len(original) >= 2 and original in text:
                    problems.append({"kind": "映射库原文残留", "text": original[:2] + "…"})
        return problems

    def scan_inbound(self, text: str) -> list[str]:
        """云端返回内容扫描：拦截夹带的提示注入与越权指令。"""
        return [m.group(0) for m in _RE_INJECTION.finditer(text or "")]

    def egress(
        self,
        payload: Any,
        vault: TokenVault,
        *,
        run_id: int | None,
        step_id: int | None,
        agent_id: str | None,
        task: str,
        task_type: str,
        source_level: int,
        target_model: str,
    ) -> tuple[Any, int, dict]:
        from ..services.config_center import cfg

        policy = cfg("privacy.egress")
        report = EgressReport()
        # 图片原文禁止出域：纵深防御，即使路由误选云端也阻断
        if isinstance(payload, dict) and payload.get("_images"):
            raise PrivacyViolation("图片内容禁止出域到云端", [{"kind": "图像出域禁令", "text": task}])
        drop = set(policy.get("minimize_fields") or [])
        out = self.desensitize(_minimize(payload, drop) if drop else payload, vault, report)
        serialized = json.dumps(out, ensure_ascii=False)
        limit = int(policy.get("max_chars") or MAX_EGRESS_CHARS)
        over = len(serialized) > limit
        if over:
            report.truncated = True
        findings = self.scan(out, vault)
        original_serialized = json.dumps(payload, ensure_ascii=False)
        with session_scope() as s:
            log = EgressLog(
                run_id=run_id,
                step_id=step_id,
                agent_id=agent_id,
                task=task,
                task_type=task_type,
                source_level=source_level,
                rules_hit=report.as_dict()["rules_hit"],
                fields=report.as_dict()["fields"][:50],
                content_digest=hashlib.sha256(serialized.encode()).hexdigest(),
                content_preview=truncate(serialized, 2000),
                original_preview=truncate(original_serialized, 2000),
                placeholder_count=report.placeholder_count,
                scan_result="blocked" if findings else "pass",
                scan_findings=findings,
                target_model=target_model,
            )
            s.add(log)
            s.flush()
            egress_id = log.id
        if findings:
            raise PrivacyViolation("出域二次扫描发现敏感信息残留，已阻断", findings)
        if over and policy.get("over_limit") == "block":
            raise PrivacyViolation(f"出域内容 {len(serialized)} 字超过最小化上限 {limit} 字，已阻断", [{"kind": "超过出域上限", "text": str(len(serialized))}])
        return out, egress_id, report.as_dict()


privacy_gateway = PrivacyGateway()
