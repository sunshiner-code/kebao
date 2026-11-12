"""险种推荐资格过滤：按企业地区、行业等条件排除明显不适用的险种。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import LINES, LINE_SIMILARITY_WEIGHTS

# 地区标签 → 匹配企业地址时使用的正向/排除关键词
_REGION_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "全国": {"any": ()},  # 国内通用，单独处理
    "陕西省": {"any": ("陕西", "西安")},
    "深圳市": {"any": ("深圳",)},
    "宁波市": {"any": ("宁波",)},
    "上海市": {"any": ("上海",)},
    "广东省": {"any": ("广东", "广州", "深圳", "珠海", "东莞", "佛山", "惠州", "中山", "汕头")},
    "广东省（不含深圳市）": {"any": ("广东", "广州", "珠海", "东莞", "佛山", "惠州", "中山", "汕头"), "exclude": ("深圳",)},
    "浙江省（不含宁波市）": {"any": ("浙江", "杭州", "温州", "嘉兴", "绍兴", "金华", "台州"), "exclude": ("宁波",)},
    "海外承保区域": {"any": ("海外", "境外", "国外", "港澳", "台湾", "美国", "欧洲", "东盟")},
}

_INDUSTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "通用": (),
    "人工智能": ("人工智能", "人工智能软件", "大模型", "机器学习", "算法模型"),
    "装备": ("装备", "机器人", "智能制造", "半导体设备", "精密制造"),
    "新材料": ("新材料", "先进材料", "陶瓷基", "复合材料", "电解质", "材料科技"),
    "软件": ("软件", "人工智能软件", "数据科技", "算法"),
    "生命科学": ("生命科学", "医疗器械", "医药", "生物医药", "疫苗", "临床试验"),
    "航天": ("航天", "卫星", "宇航"),
    "航空": ("航空", "航空器", "通航"),
    "低空": ("低空", "无人机", "eVTOL", "飞行器"),
}


@dataclass
class LineCheck:
    line: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class LineFilterResult:
    eligible: list[str]
    rejected: list[LineCheck]
    replacements: dict[str, str]  # 原险种 → 替代险种
    notes: list[str]


def enterprise_context(ent: dict | None) -> dict[str, str]:
    ent = ent or {}
    profile = ent.get("profile") if isinstance(ent.get("profile"), dict) else {}
    return {
        "region": str(ent.get("region") or profile.get("address") or ""),
        "industry": str(ent.get("industry") or ""),
        "track": str(ent.get("track") or ""),
        "tech_field": str(ent.get("tech_field") or profile.get("tech_field") or ""),
    }


def _haystack(ctx: dict[str, str]) -> str:
    return f"{ctx.get('region', '')} {ctx.get('industry', '')} {ctx.get('track', '')} {ctx.get('tech_field', '')}"


def region_compatible(line_regions: list[str], enterprise_region: str) -> tuple[bool, str | None]:
    """判断险种适用地区是否覆盖企业所在地。"""
    regions = list(line_regions or [])
    if not regions:
        return True, None
    text = (enterprise_region or "").strip()
    if "全国" in regions:
        return True, None
    if all(r == "海外承保区域" for r in regions):
        if not text:
            return False, "企业地区未知，无法匹配海外承保区域险种"
        if any(k in text for k in _REGION_RULES["海外承保区域"]["any"]):
            return True, None
        return False, "企业不在海外承保区域"
    if not text:
        return False, "企业地区未知，无法匹配地区限定险种"

    for label in regions:
        rule = _REGION_RULES.get(label)
        if label == "全国":
            return True, None
        if not rule:
            # 未建模标签：子串兜底
            token = label.replace("（不含深圳市）", "").replace("（不含宁波市）", "").rstrip("省市")
            if token and token in text:
                return True, None
            continue
        excludes = rule.get("exclude") or ()
        if any(x in text for x in excludes):
            continue
        anys = rule.get("any") or ()
        if not anys or any(a in text for a in anys):
            return True, None
    return False, f"不适用企业所在地（险种限{'、'.join(regions)}）"


def industry_compatible(line_industry: str, ctx: dict[str, str]) -> tuple[bool, str | None]:
    industry = (line_industry or "通用").strip() or "通用"
    if industry == "通用":
        return True, None
    aliases = _INDUSTRY_ALIASES.get(industry, (industry,))
    blob = _haystack(ctx)
    if not blob.strip():
        return False, f"企业行业未知，无法匹配行业限定险种（{industry}）"
    if any(a and a in blob for a in aliases):
        return True, None
    return False, f"不适用企业行业/赛道（险种限{industry}）"


def check_line(line: str, ctx: dict[str, str] | None = None) -> LineCheck:
    ctx = ctx or {}
    if line not in LINES:
        # 创新编码等目录外险种不做地区/行业硬拦
        return LineCheck(line=line, eligible=True, reasons=[])
    spec = LINES[line]
    reasons: list[str] = []
    ok_r, reason_r = region_compatible(list(spec.get("regions") or []), ctx.get("region", ""))
    if not ok_r and reason_r:
        reasons.append(reason_r)
    ok_i, reason_i = industry_compatible(str(spec.get("industry") or "通用"), ctx)
    if not ok_i and reason_i:
        reasons.append(reason_i)
    return LineCheck(line=line, eligible=not reasons, reasons=reasons)


def line_similarity(a: str, b: str) -> float:
    if a not in LINES or b not in LINES:
        return 0.0
    la, lb = LINES[a], LINES[b]
    s = 0.0
    s += LINE_SIMILARITY_WEIGHTS["subject"] * (la["subject"] == lb["subject"])
    s += LINE_SIMILARITY_WEIGHTS["perils"] * (
        len(set(la["perils"]) & set(lb["perils"])) / max(1, len(set(la["perils"]) | set(lb["perils"])))
    )
    s += LINE_SIMILARITY_WEIGHTS["loss_forms"] * (
        len(set(la["loss_forms"]) & set(lb["loss_forms"])) / max(1, len(set(la["loss_forms"]) | set(lb["loss_forms"])))
    )
    s += LINE_SIMILARITY_WEIGHTS["stage"] * (la["stage"] == lb["stage"])
    s += LINE_SIMILARITY_WEIGHTS["industry"] * (la["industry"] == lb["industry"] or "通用" in (la["industry"], lb["industry"]))
    s += LINE_SIMILARITY_WEIGHTS["trigger"] * (la["trigger"] == lb["trigger"])
    family_bonus = 0.15 if la.get("family") and la.get("family") == lb.get("family") else 0.0
    return round(min(1.0, s + family_bonus), 3)


def find_replacement(line: str, ctx: dict[str, str], *, candidates: list[str] | None = None, used: set[str] | None = None) -> str | None:
    """为不合格险种寻找同类、符合条件的替代。优先同 family，再按相似度。"""
    used = used or set()
    if line not in LINES:
        return None
    family = LINES[line].get("family")
    pool = [c for c in (candidates or list(LINES)) if c in LINES and c != line and c not in used]
    ranked = sorted(
        pool,
        key=lambda c: (
            0 if LINES[c].get("family") == family else 1,
            -line_similarity(line, c),
            0 if "全国" in (LINES[c].get("regions") or []) else 1,
            c,
        ),
    )
    for cand in ranked:
        if check_line(cand, ctx).eligible and line_similarity(line, cand) >= 0.35:
            return cand
    for cand in ranked:
        if check_line(cand, ctx).eligible and LINES[cand].get("family") == family:
            return cand
    return None


def filter_lines(lines: list[str], ctx: dict[str, str] | None = None, *, replace: bool = True) -> LineFilterResult:
    """过滤不合格险种；可选地用同类合格险种替换。"""
    ctx = ctx or {}
    eligible: list[str] = []
    rejected: list[LineCheck] = []
    replacements: dict[str, str] = {}
    notes: list[str] = []
    used: set[str] = set()

    for line in lines:
        if not line:
            continue
        result = check_line(line, ctx)
        if result.eligible:
            if line not in eligible:
                eligible.append(line)
                used.add(line)
            continue
        rejected.append(result)
        if not replace:
            notes.append(f"{LINES.get(line, {}).get('name', line)}：{'；'.join(result.reasons)}")
            continue
        alt = find_replacement(line, ctx, candidates=lines + list(LINES), used=used)
        if alt:
            replacements[line] = alt
            if alt not in eligible:
                eligible.append(alt)
                used.add(alt)
            notes.append(
                f"{LINES.get(line, {}).get('name', line)}因{'；'.join(result.reasons)}不适用，已改荐{LINES.get(alt, {}).get('name', alt)}"
            )
        else:
            notes.append(f"{LINES.get(line, {}).get('name', line)}因{'；'.join(result.reasons)}不适用，暂无合格替代")
    return LineFilterResult(eligible=eligible, rejected=rejected, replacements=replacements, notes=notes)


def ineligible_line_codes(ctx: dict[str, str] | None = None) -> list[str]:
    ctx = ctx or {}
    return [code for code in LINES if not check_line(code, ctx).eligible]
