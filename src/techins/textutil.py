from __future__ import annotations

import logging
import math
import re
from functools import lru_cache
from typing import Iterable

import jieba

jieba.setLogLevel(logging.WARNING)

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])|\n+")
_STOPWORDS = set(
    "的 了 和 是 在 与 及 或 等 对 为 以 将 被 由 从 其 该 并 也 而 之 于 中 上 下 个 这 那 有 无 不 已 可 会 能 就 都 但 还 又 把 让 向 至 所 因 此 如 若 即 则 所以 因为 以及 进行 相关 一个 我们 公司 企业 情况".split()
)

_AMOUNT = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(亿元|万元|千元|元|亿|万)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "")]
    return [p for p in parts if p and len(p) >= 4]


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in jieba.lcut(text or ""):
        tok = tok.strip().lower()
        if not tok or tok in _STOPWORDS:
            continue
        if re.fullmatch(r"[\W_]+", tok):
            continue
        tokens.append(tok)
    return tokens


def parse_amounts(text: str) -> list[float]:
    """把文本中的金额统一换算为元。"""
    values = []
    for num, unit in _AMOUNT.findall(text or ""):
        value = float(num.replace(",", ""))
        factor = {"亿元": 1e8, "亿": 1e8, "万元": 1e4, "万": 1e4, "千元": 1e3, "元": 1.0}[unit]
        values.append(value * factor)
    return values


def parse_percents(text: str) -> list[float]:
    return [float(v) / 100 for v in _PERCENT.findall(text or "")]


def first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return None


def format_amount(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f} 亿元"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f} 万元"
    return f"{value:.0f} 元"


def amount_bucket(value: float) -> str:
    bounds = [
        (1e5, "10万元以下"),
        (5e5, "10万至50万元"),
        (1e6, "50万至100万元"),
        (5e6, "100万至500万元"),
        (1e7, "500万至1000万元"),
        (5e7, "1000万至5000万元"),
        (1e8, "5000万至1亿元"),
        (5e8, "1亿至5亿元"),
        (1e9, "5亿至10亿元"),
    ]
    for bound, label in bounds:
        if value < bound:
            return label
    return "10亿元以上"


def char_ngrams(text: str, n_values: Iterable[int] = (2, 3)) -> list[str]:
    text = re.sub(r"\s+", "", text or "")
    grams = []
    for n in n_values:
        grams.extend(text[i : i + n] for i in range(max(0, len(text) - n + 1)))
    return grams


def jaccard(a: str, b: str) -> float:
    sa, sb = set(char_ngrams(a)), set(char_ngrams(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@lru_cache(maxsize=4096)
def _cached_tokens(text: str) -> tuple[str, ...]:
    return tuple(tokenize(text))


def cosine_tokens(a: str, b: str) -> float:
    ta, tb = _cached_tokens(a), _cached_tokens(b)
    if not ta or not tb:
        return 0.0
    from collections import Counter

    ca, cb = Counter(ta), Counter(tb)
    dot = sum(ca[k] * cb.get(k, 0) for k in ca)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


def estimate_tokens(text: str) -> int:
    """中英混合文本的 token 估算：中文约 1.5 字/token，其他约 4 字符/token。"""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4) + 1


def truncate(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
