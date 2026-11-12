"""敏感信息识别：规则 + 校验算法 + 已知实体词表。识别结果决定数据级别（L0–L3）。"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK = "10X98765432"

_RE_ID = re.compile(r"(?<![0-9A-Za-z])([1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?![0-9A-Za-z])")
_RE_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_RE_LANDLINE = re.compile(r"(?<!\d)(0\d{2,3}-\d{7,8})(?!\d)")
_RE_BANK = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")
_RE_EMAIL = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_RE_ADDRESS = re.compile(
    r"([\u4e00-\u9fa5]{2,7}(?:省|自治区|市)[\u4e00-\u9fa5]{1,8}(?:市|区|县)(?:[\u4e00-\u9fa5]{1,8}(?:区|县|镇|街道))?"
    r"[\u4e00-\u9fa5\d]{1,16}(?:路|街|道|大道|巷)\d{1,5}号(?:[\u4e00-\u9fa5\d]{0,12}(?:栋|楼|室|层))?)"
)
_RE_AMOUNT = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:亿元|万元|千元|元))")
_RE_COMPANY = re.compile(
    r"([\u4e00-\u9fa5]{2,14}(?:科技|技术|智能|装备|材料|医疗|电子|半导体|生物|软件|信息|机器人|精密|制造|数据|网络|器械)"
    r"(?:股份)?有限(?:责任)?公司)"
)
_SURNAMES = (
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
)
_RE_PERSON_ROLE = re.compile(
    r"(?:姓名|法定代表人|法人代表|联系人|负责人|经办人|总经理|董事长|副总裁|总裁|首席科学家|首席技术官|技术总监|研发总监|项目负责人|实验室主任|创始人|股东|被访谈人|访谈对象|受访者)"
    r"[：:\s为是]{0,2}([" + _SURNAMES + r"][\u4e00-\u9fa5]{1,2})(?=[，,。；;、\s（(]|先生|女士|博士|教授|$)"
)
_RE_PERSON_SUFFIX = re.compile(r"([" + _SURNAMES + r"][\u4e00-\u9fa5]{1,2})(?=先生|女士|博士|教授|工程师|院士)")
_RE_SECRET = re.compile(r"[^。；\n]*(?:配方|工艺参数|源代码|核心算法参数|良率参数|关键配比|烧结温度曲线)[^。；\n]*[。；]?")

_LEVEL = {
    "id_card": 3,
    "phone": 3,
    "landline": 3,
    "bank_card": 3,
    "email": 3,
    "person": 3,
    "tech_secret": 3,
    "address": 2,
    "amount": 2,
    "company": 2,
}

LABELS = {
    "id_card": "身份证号",
    "phone": "手机号",
    "landline": "固定电话",
    "bank_card": "银行账号",
    "email": "电子邮箱",
    "person": "人名",
    "tech_secret": "核心技术秘密",
    "address": "详细地址",
    "amount": "精确金额",
    "company": "企业名称",
}


@dataclass(frozen=True)
class Finding:
    kind: str
    text: str
    start: int
    end: int
    custom_level: int = -1
    custom_label: str = ""

    @property
    def level(self) -> int:
        if self.custom_level >= 0:
            return self.custom_level
        return _rules()["levels"].get(self.kind, _LEVEL.get(self.kind, 2))

    @property
    def label(self) -> str:
        return self.custom_label or LABELS.get(self.kind, self.kind)


_RULE_CACHE: dict = {"version": None, "value": None}


def _rules() -> dict:
    """数据分级与脱敏规则（配置中心 privacy.rules）：识别类型级别与启用状态、自定义识别规则、脱敏方式。"""
    try:
        from ..services.config_center import config_center
    except ImportError:  # pragma: no cover - 初始化早期
        return {"levels": dict(_LEVEL), "disabled": set(), "custom": [], "methods": {}}
    version = config_center.version("privacy.rules")
    if _RULE_CACHE["version"] == version and _RULE_CACHE["value"] is not None:
        return _RULE_CACHE["value"]
    conf = config_center.get("privacy.rules")
    kinds = conf.get("kinds") or {}
    custom = []
    for r in conf.get("custom_rules") or []:
        if not r.get("enabled"):
            continue
        try:
            custom.append((re.compile(r["pattern"]), r["name"], int(r.get("level", 2)), r.get("method", "token")))
        except re.error:
            continue
    value = {
        "levels": {k: int(v.get("level", _LEVEL.get(k, 2))) for k, v in kinds.items()} or dict(_LEVEL),
        "disabled": {k for k, v in kinds.items() if not v.get("enabled", True)},
        "custom": custom,
        "methods": {k: v.get("method", "token") for k, v in kinds.items()} | {f"custom:{c[1]}": c[3] for c in custom},
    }
    _RULE_CACHE.update(version=version, value=value)
    return value


def masking_method(kind: str) -> str:
    return _rules()["methods"].get(kind, "token")


def _valid_id(number: str) -> bool:
    total = sum(int(d) * w for d, w in zip(number[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11] == number[17].upper()


def _luhn(number: str) -> bool:
    digits = [int(d) for d in number]
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


class KnownEntities:
    """平台已知的企业与人员实体词表（来自客户档案与企业实体记忆），用于补充规则识别。"""

    def __init__(self) -> None:
        self._companies: set[str] = set()
        self._persons: set[str] = set()
        self._lock = threading.Lock()

    def add_company(self, name: str) -> None:
        if name and len(name) >= 4:
            with self._lock:
                self._companies.add(name)

    def add_person(self, name: str) -> None:
        if name and 2 <= len(name) <= 4:
            with self._lock:
                self._persons.add(name)

    def snapshot(self) -> tuple[list[str], list[str]]:
        with self._lock:
            return sorted(self._companies, key=len, reverse=True), sorted(self._persons, key=len, reverse=True)


known_entities = KnownEntities()


def classify_text(text: str, *, include_amounts: bool = True, include_companies: bool = True) -> list[Finding]:
    if not text:
        return []
    findings: list[Finding] = []

    def add(kind: str, m: re.Match, group: int = 1) -> None:
        findings.append(Finding(kind, m.group(group), m.start(group), m.end(group)))

    for m in _RE_ID.finditer(text):
        if _valid_id(m.group(1)):
            add("id_card", m)
    for m in _RE_PHONE.finditer(text):
        add("phone", m)
    for m in _RE_LANDLINE.finditer(text):
        add("landline", m)
    for m in _RE_BANK.finditer(text):
        if _luhn(m.group(1)):
            add("bank_card", m)
    for m in _RE_EMAIL.finditer(text):
        add("email", m)
    for m in _RE_ADDRESS.finditer(text):
        add("address", m)
    for m in _RE_PERSON_ROLE.finditer(text):
        add("person", m)
    for m in _RE_PERSON_SUFFIX.finditer(text):
        add("person", m)
    for m in _RE_SECRET.finditer(text):
        if m.group(0).strip():
            findings.append(Finding("tech_secret", m.group(0), m.start(), m.end()))
    if include_amounts:
        for m in _RE_AMOUNT.finditer(text):
            add("amount", m)
    companies, persons = known_entities.snapshot()
    if include_companies:
        for m in _RE_COMPANY.finditer(text):
            add("company", m)
        for name in companies:
            for m in re.finditer(re.escape(name), text):
                findings.append(Finding("company", name, m.start(), m.end()))
    for name in persons:
        for m in re.finditer(re.escape(name), text):
            findings.append(Finding("person", name, m.start(), m.end()))
    rules = _rules()
    for pattern, name, level, _method in rules["custom"]:
        for m in pattern.finditer(text):
            if m.group(0):
                findings.append(Finding(f"custom:{name}", m.group(0), m.start(), m.end(), custom_level=level, custom_label=name))
    if rules["disabled"]:
        findings = [f for f in findings if f.kind not in rules["disabled"]]
    return _resolve_overlaps(findings)


def _resolve_overlaps(findings: list[Finding]) -> list[Finding]:
    """重叠时保留级别更高、跨度更长的命中。"""
    ordered = sorted(findings, key=lambda f: (-f.level, -(f.end - f.start), f.start))
    taken: list[Finding] = []
    for f in ordered:
        if any(not (f.end <= t.start or f.start >= t.end) for t in taken):
            continue
        taken.append(f)
    return sorted(taken, key=lambda f: f.start)


def detect_level(text: str, *, private_context: bool = False) -> int:
    """private_context=True 表示文本来自私有材料：金额、企业名称在私有语境中属于敏感商业信息。"""
    findings = classify_text(text, include_amounts=private_context, include_companies=private_context)
    level = max((f.level for f in findings), default=0)
    if private_context:
        level = max(level, 2)
    return level
