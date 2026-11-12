"""两种费率的数值计算。费率数字只来自这些函数。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


class RateError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _d(value: Decimal | float | str | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def convert_rate(
    *,
    pure_loss_ratio: Decimal | float | str,
    risk_load_rate: Decimal | float | str,
    expense_ratio: Decimal | float | str,
    billing_amount: Decimal | float | str,
    reference_pure_loss_ratio: Decimal | float | str,
    pure_correction: Decimal | float | str,
) -> dict:
    """建议费率 =（纯风险损失率 + 风险附加率）÷（1 − 费用率）。

    风险附加率以计费保额为基数，费用率以保费为基数。
    基准费率用参照纯风险损失率按同一公式计算。
    调整系数 = 建议费率 ÷ 基准费率，与纯风险修正系数分开保存。
    """
    pure = _d(pure_loss_ratio)
    load = _d(risk_load_rate)
    expense = _d(expense_ratio)
    billing = _d(billing_amount)
    reference = _d(reference_pure_loss_ratio)
    correction = _d(pure_correction)
    if expense >= 1 or expense < 0:
        raise RateError("费用率必须大于等于 0 且小于 1", code="bad_expense")
    if billing <= 0:
        raise RateError("计费保额必须大于 0", code="bad_billing")
    denominator = Decimal(1) - expense

    def suggested(loss_ratio: Decimal) -> Decimal:
        return (loss_ratio + load) / denominator

    suggested_rate = suggested(pure)
    benchmark_rate = suggested(reference)
    if benchmark_rate == 0:
        raise RateError("基准费率不能为 0", code="bad_benchmark")
    return {
        "pure_loss_ratio": pure,
        "risk_load_rate": load,
        "risk_load_base": "计费保额",
        "expense_ratio": expense,
        "expense_base": "保费",
        "billing_amount": billing,
        "suggested_rate": suggested_rate,
        "benchmark_rate": benchmark_rate,
        "adjustment": suggested_rate / benchmark_rate,
        "pure_correction": correction,
        "premium": _money(billing * suggested_rate),
    }


def _band_for(bands: list[dict], score: Decimal) -> dict:
    ordered = sorted(bands, key=lambda item: _d(item["min"]))
    for band in ordered:
        lower, upper = _d(band["min"]), _d(band["max"])
        if lower <= score < upper or (score == upper and band.get("closed_max")):
            return band
    raise RateError("示例得分不在已维护的得分区间内", code="score_out_of_band")


def weighted_score_method(*, parameters: dict, example_score: Decimal | float | str | None = None) -> dict:
    """按已维护的指标、权重和得分区间查纯风险费率。代码不根据得分高低改写费率。"""
    if not parameters.get("source") or not parameters.get("applicability") or not parameters.get("mapping_basis"):
        raise RateError("创新积分制评分参数缺少来源或映射依据", code="missing_score_parameters")
    indicators = parameters.get("indicators") or []
    if not indicators:
        raise RateError("没有已维护的评分指标", code="missing_indicators")
    weight = sum((_d(item["weight"]) for item in indicators), Decimal(0))
    if weight != Decimal(1):
        raise RateError("指标权重合计必须为 1", code="weight_sum")
    score = _d(parameters["example_score"] if example_score is None else example_score)
    band = _band_for(parameters["bands"], score)
    reference = next((item for item in parameters["bands"] if item.get("reference")), None)
    if reference is None:
        raise RateError("得分映射没有参照区间", code="missing_reference_band")
    pure = _d(band["pure"])
    reference_pure = _d(reference["pure"])
    if reference_pure == 0:
        raise RateError("参照区间的纯风险费率不能为 0", code="bad_benchmark")
    return {
        "method": "创新积分制定价法",
        "indicators": [{"name": item["name"], "definition": item["definition"], "weight": _d(item["weight"]), "source": item.get("source") or parameters["source"]} for item in indicators],
        "weight_sum": weight,
        "formula": parameters.get("formula") or "综合得分等于各项指标得分乘以权重后相加",
        "example_score": score,
        "example_is_assumption": True,
        "score_band": {"label": band.get("label"), "min": _d(band["min"]), "max": _d(band["max"]), "pure": pure},
        "bands": [
            {"label": item.get("label"), "min": _d(item["min"]), "max": _d(item["max"]), "pure": _d(item["pure"]), "reference": bool(item.get("reference")), "closed_max": bool(item.get("closed_max"))}
            for item in parameters["bands"]
        ],
        "pure_loss_ratio": pure,
        "reference_pure_loss_ratio": reference_pure,
        "reference_band": reference.get("label"),
        "pure_correction": pure / reference_pure,
        "mapping_basis": parameters["mapping_basis"],
        "direction_encoded": False,
    }


def _forbidden_factor(name: str, key: str) -> bool:
    return key in ("maturity", "score", "innovation_score") or "技术成熟度" in name or "综合评分" in name


def _same_risk(left: dict, right: dict) -> bool:
    return bool(left.get("risk_key")) and left.get("risk_key") == right.get("risk_key")


def classification_method(*, factors: list[dict], benchmark_pure: Decimal | float | str, benchmark_condition: str, samples: list[dict], assumptions: dict | None) -> dict:
    usable = [row for row in samples if row.get("usable")]
    cleaned: list[dict] = []
    for factor in factors:
        name = str(factor.get("name") or "")
        key = str(factor.get("risk_key") or "")
        if _forbidden_factor(name, key):
            raise RateError("综合评分指标和技术成熟度不能作为分类费率因子", code="maturity_factor")
        if any(_same_risk(factor, kept) for kept in cleaned):
            continue
        cleaned.append(factor)
    fitted = len(usable) >= 30
    if not fitted:
        if not assumptions or not assumptions.get("source") or not assumptions.get("applicability"):
            raise RateError("数据不足且没有可追溯的分类假设", code="missing_assumption")
        source_factors = factors or assumptions["factors"]
        cleaned = []
        for factor in source_factors:
            if _forbidden_factor(str(factor.get("name") or ""), str(factor.get("risk_key") or "")):
                raise RateError("综合评分指标和技术成熟度不能作为分类费率因子", code="maturity_factor")
            if any(_same_risk(factor, kept) for kept in cleaned):
                continue
            cleaned.append(factor)
    coefficient = Decimal(1)
    for factor in cleaned:
        coefficient *= _d(factor["coefficient"])
    pure = _d(benchmark_pure) * coefficient
    return {
        "method": "分类费率厘定法",
        "benchmark_condition": benchmark_condition,
        "benchmark_pure_loss_ratio": _d(benchmark_pure),
        "pure_loss_ratio": pure,
        "pure_correction": coefficient,
        "factors": [{"name": f["name"], "risk_key": f.get("risk_key"), "condition": f.get("condition"), "coefficient": _d(f["coefficient"])} for f in cleaned],
        "fitted": fitted,
        "provisional": not fitted,
        "sample_count": len(usable),
        "excluded_sample_count": len(samples) - len(usable),
    }


def price_named_methods(*, rules: dict, results: dict[str, dict], expense_ratio: Decimal | float | str, risk_load_rate: Decimal | float | str) -> dict:
    if _d(rules.get("deductible") or 0) > 0 or _d(rules.get("deductible_rate") or 0) > 0:
        raise RateError("必须先把条款和测算口径统一为无免赔", code="deductible")
    billing = rules.get("billing_amount")
    if not billing or not rules.get("period"):
        raise RateError("需要先确认计费基数和保险期间", code="missing_scope")
    if rules.get("product_category") == "责任保险" and not rules.get("billing_base"):
        raise RateError("必须指定一个计费基数", code="missing_billing_base")
    common = {
        "liability": rules.get("liability"),
        "billing_amount": _d(billing),
        "billing_base": rules.get("billing_base"),
        "period": rules.get("period"),
        "limits": rules.get("limits") or {},
        "deductible": Decimal(0),
    }
    schemes = {}
    for key, result in results.items():
        converted = convert_rate(
            pure_loss_ratio=result["pure_loss_ratio"],
            risk_load_rate=risk_load_rate,
            expense_ratio=expense_ratio,
            billing_amount=billing,
            reference_pure_loss_ratio=result.get("reference_pure_loss_ratio", result.get("benchmark_pure_loss_ratio")),
            pure_correction=result["pure_correction"],
        )
        schemes[key] = {**common, **result, **converted}
    amounts = {scheme["billing_amount"] for scheme in schemes.values()}
    if len(amounts) != 1:
        raise RateError("计算与产品规则不一致", code="scope_mismatch")
    refuse_inconsistent(rules, schemes)
    return schemes


def refuse_inconsistent(rules: dict, schemes: dict) -> None:
    """测算结果与产品规则不一致时，不交付该结果。"""
    for scheme in schemes.values():
        same = (
            scheme.get("liability") == rules.get("liability")
            and _d(scheme.get("billing_amount")) == _d(rules.get("billing_amount"))
            and scheme.get("period") == rules.get("period")
            and _d(scheme.get("deductible") or 0) == 0
        )
        if not same:
            raise RateError("计算与产品规则不一致", code="scope_mismatch")


def sensitivity(schemes: dict, *, expense_ratio: Decimal | float | str) -> dict:
    """另算一份敏感性，不替换当前方案。"""
    out = {}
    for key, scheme in schemes.items():
        converted = convert_rate(
            pure_loss_ratio=scheme["pure_loss_ratio"],
            risk_load_rate=scheme["risk_load_rate"],
            expense_ratio=expense_ratio,
            billing_amount=scheme["billing_amount"],
            reference_pure_loss_ratio=scheme.get("reference_pure_loss_ratio", scheme.get("benchmark_pure_loss_ratio")),
            pure_correction=scheme["pure_correction"],
        )
        out[key] = {"expense_ratio": _d(expense_ratio), "suggested_rate": converted["suggested_rate"], "premium": converted["premium"]}
    return out


def example_weighted(scheme: dict, score: Decimal | float | str) -> dict:
    """按已交付方案里维护的得分区间做示例测算，不改写正式建议费率。"""
    bands = scheme.get("bands")
    if not bands:
        raise RateError("方案没有已维护的得分区间，不能示例测算", code="missing_bands")
    value = _d(score)
    if value < 0 or value > 100:
        raise RateError("综合得分必须在 0 到 100 之间", code="score_out_of_band")
    band = _band_for(bands, value)
    reference = next((item for item in bands if item.get("reference")), None)
    if reference is None:
        raise RateError("得分映射没有参照区间", code="missing_reference_band")
    converted = convert_rate(
        pure_loss_ratio=band["pure"],
        risk_load_rate=scheme["risk_load_rate"],
        expense_ratio=scheme["expense_ratio"],
        billing_amount=scheme["billing_amount"],
        reference_pure_loss_ratio=reference["pure"],
        pure_correction=_d(band["pure"]) / _d(reference["pure"]),
    )
    return {
        "method": "weighted",
        "example_score": value,
        "score_band": {"label": band.get("label"), "min": _d(band["min"]), "max": _d(band["max"]), "pure": _d(band["pure"])},
        "reference_band": reference.get("label"),
        **converted,
    }


def example_classification(scheme: dict, coefficients: dict | None = None) -> dict:
    """按已交付方案的基准和因子做示例测算，可逐项替换因子系数。"""
    factors = scheme.get("factors") or []
    if not factors:
        raise RateError("方案没有已维护的费率因子，不能示例测算", code="missing_factors")
    coefficients = coefficients or {}
    applied: list[dict] = []
    combined = Decimal(1)
    for factor in factors:
        key = str(factor.get("risk_key") or factor["name"])
        value = _d(coefficients.get(key, factor["coefficient"]))
        if value <= 0:
            raise RateError("费率因子系数必须大于 0", code="bad_coefficient")
        combined *= value
        applied.append({"name": factor["name"], "risk_key": factor.get("risk_key"), "condition": factor.get("condition"), "coefficient": value})
    converted = convert_rate(
        pure_loss_ratio=_d(scheme["benchmark_pure_loss_ratio"]) * combined,
        risk_load_rate=scheme["risk_load_rate"],
        expense_ratio=scheme["expense_ratio"],
        billing_amount=scheme["billing_amount"],
        reference_pure_loss_ratio=scheme["benchmark_pure_loss_ratio"],
        pure_correction=combined,
    )
    return {"method": "classification", "factors": applied, "combined_coefficient": combined, **converted}


def _primary_scheme(schemes: dict) -> tuple[str, dict]:
    for key in ("weighted", "classification"):
        if key in schemes and schemes[key].get("suggested_rate") is not None:
            return key, schemes[key]
    raise RateError("没有已算出的定价方案", code="missing_scheme")


def compare_schemes(schemes: dict) -> dict:
    primary_key, primary = _primary_scheme(schemes)
    left = primary["suggested_rate"]
    right = schemes["classification"]["suggested_rate"]
    return {
        "primary_method": primary_key,
        "weighted_rate": left if primary_key == "weighted" else None,
        "classification_rate": right,
        "difference": right - left,
        "reasons": [
            "方法一依据已维护的评分指标、权重和得分映射",
            "方法二依据基准风险条件和费率因子",
        ],
        "recommendation": "方法一用于判断目标场景的整体风险水平，方法二用于不同风险条件下的分类收费。选择应同时看产品要覆盖的风险条件，而不是只比较费率高低。",
        "blended_rate": None,
    }


def select_scheme(schemes: dict, method: str) -> dict:
    if method not in schemes:
        raise RateError("必须选择已经算出的一种方法", code="unknown_method")
    chosen = schemes[method]["suggested_rate"]
    other = next(item["suggested_rate"] for key, item in schemes.items() if key != method)
    low, high = (chosen, other) if chosen <= other else (other, chosen)
    return {
        "selected_method": method,
        "gross_rate": chosen,
        "deductible": Decimal(0),
        "limit": schemes[method]["billing_amount"],
        "band": [low, high],
        "methods": [
            {"name": scheme["method"], "suggested_rate": scheme["suggested_rate"]}
            for key, scheme in schemes.items()
            if scheme.get("suggested_rate") is not None and key in ("weighted", "classification")
        ],
        "models": {},
        "scenarios": [],
        "experiments": [],
    }


def commit_report(schemes: dict, report: dict) -> dict:
    """报告数字必须与计算输出一致，否则拒绝保存。"""
    for key, scheme in schemes.items():
        copied = report["schemes"][key]
        if str(copied["suggested_rate"]) != str(scheme["suggested_rate"]) or str(copied["premium"]) != str(scheme["premium"]):
            raise RateError("报告数字与计算输出不一致，不保存报告", code="report_mismatch")
    return report


def _band_issues(bands: list[dict]) -> list[str]:
    issues: list[str] = []
    if not bands:
        return ["没有已维护的得分区间"]
    ordered = sorted(bands, key=lambda item: _d(item["min"]))
    if _d(ordered[0]["min"]) != 0:
        issues.append("得分区间未从 0 开始")
    for prev, nxt in zip(ordered, ordered[1:]):
        if _d(prev["max"]) != _d(nxt["min"]):
            issues.append(f"区间 {prev.get('label')} 与 {nxt.get('label')} 不连续")
    if _d(ordered[-1]["max"]) != 100:
        issues.append("得分区间未覆盖到 100")
    if not ordered[-1].get("closed_max"):
        issues.append("最后一个区间未包含上限得分")
    return issues


def _pct(value) -> str:
    return f"{_d(value) * 100:.4f}%"


def _amount_text(value) -> str:
    return f"{_d(value):,.2f} 元"


def _ratio(value) -> str:
    return str(_d(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _liability_text(value) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value)
    return str(value or "—")


def actuarial_report(schemes: dict, comparison: dict, sensitivity_result: dict, *, provisional: bool, rules: dict | None = None, sources: list[dict] | None = None) -> dict:
    """生成供精算人员审定的结构化报告。数字全部来自已算出的方案，不做二次推算。"""
    rules = rules or {}
    sources = sources or []
    _, primary = _primary_scheme(schemes)
    weighted = schemes.get("weighted")
    classification = schemes.get("classification")

    def dump(scheme: dict) -> dict:
        return {
            "suggested_rate": scheme["suggested_rate"],
            "benchmark_rate": scheme["benchmark_rate"],
            "adjustment": scheme["adjustment"],
            "premium": scheme["premium"],
            "pure_correction": scheme["pure_correction"],
        }

    scope_items = [
        {"label": "适用对象", "value": rules.get("applicable_scope") or "—"},
        {"label": "保险责任", "value": _liability_text(primary.get("liability"))},
        {"label": "产品类别", "value": rules.get("product_category") or "—"},
        {"label": "计费基数", "value": f"{primary.get('billing_base') or rules.get('billing_base') or '—'} · {_amount_text(primary['billing_amount'])}"},
        {"label": "保险期间", "value": primary.get("period") or "—"},
        {"label": "赔偿限额", "value": "；".join(f"{k} {v}" for k, v in (primary.get("limits") or {}).items()) or "以计费保额为限"},
        {"label": "免赔设置", "value": "免赔额 0，免赔率 0（测算口径为无免赔）"},
    ]
    sections = [
        {
            "id": "scope",
            "title": "一、产品责任与定价口径",
            "blocks": [
                {"type": "kv", "items": scope_items},
                {"type": "text", "text": "两种方法读取同一组产品规则。费用率以保费为基数，风险附加率以计费保额为基数。建议费率 =（纯风险损失率 + 风险附加率）÷（1 − 费用率）；建议保费 = 计费保额 × 建议费率。"},
            ],
        }
    ]

    source_rows = [[item.get("name"), item.get("source"), item.get("evidence_class"), item.get("applicability"), item.get("version") or "—"] for item in sources]
    sections.append(
        {
            "id": "sources",
            "title": "二、资料与参数依据",
            "blocks": [
                {"type": "table", "columns": ["参数 / 资料", "依据来源", "证据类型", "适用条件", "版本"], "rows": source_rows} if source_rows else {"type": "text", "text": "参数依据已在开发参数中维护，详见开发参数页面。"},
                {"type": "text", "text": "创新积分制评分的指标体系参照《创新积分制工作指引》。得分到费率的映射区间为暂定维护值，须经精算人员复核后方可用于正式费率。"},
            ],
        }
    )

    method_blocks: list[dict] = []
    if weighted:
        method_blocks.append({"type": "text", "text": "方法一 · 创新积分制定价法：参照《创新积分制工作指引》的指标与权重对科技企业打分得到综合得分，再把得分映射到调整比例、按得分区间查表得到纯风险损失率。代码不把得分高低自动换算成费率方向，映射关系以维护的区间为准。"})
        method_blocks.append(
            {
                "type": "table",
                "columns": ["指标", "定义", "权重", "来源"],
                "rows": [[item["name"], item["definition"], str(item["weight"]), item.get("source")] for item in weighted.get("indicators", [])],
            }
        )
        method_blocks.append({"type": "text", "text": f"得分公式：{weighted.get('formula')}。映射依据：{weighted.get('mapping_basis')}。"})
        bands = weighted.get("bands") or []
        if bands:
            method_blocks.append(
                {
                    "type": "table",
                    "columns": ["得分区间", "纯风险费率", "是否参照区间"],
                    "rows": [[f"{item.get('label') or ''}（{_d(item['min'])}–{_d(item['max'])}{'，含上限' if item.get('closed_max') else ''}）", _pct(item["pure"]), "参照" if item.get("reference") else ""] for item in bands],
                }
            )
        band = weighted.get("score_band") or {}
        method_blocks.append(
            {
                "type": "kv",
                "items": [
                    {"label": "假设示例得分", "value": str(weighted.get("example_score", "—"))},
                    {"label": "落入区间", "value": band.get("label") or "—"},
                    {"label": "区间纯风险费率", "value": _pct(band["pure"]) if band.get("pure") is not None else "—"},
                    {"label": "参照区间", "value": weighted.get("reference_band") or "—"},
                    {"label": "权重合计", "value": str(weighted.get("weight_sum", "—"))},
                ],
            }
        )
    if classification:
        fitted_note = "已用样本拟合" if classification.get("fitted") else f"可用样本 {classification.get('sample_count', 0)} 条，不足 30 条，使用已维护假设，未声称完成拟合"
        method_blocks.append({"type": "text", "text": f"方法二 · 分类费率厘定法：以基准风险条件的纯风险损失率为起点，乘以各风险因子的调整系数。{fitted_note}。"})
        method_blocks.append(
            {
                "type": "kv",
                "items": [
                    {"label": "基准风险条件", "value": classification.get("benchmark_condition") or "—"},
                    {"label": "基准纯风险损失率", "value": _pct(classification["benchmark_pure_loss_ratio"])},
                    {"label": "综合调整系数", "value": _ratio(classification["pure_correction"])},
                    {"label": "目标纯风险损失率", "value": _pct(classification["pure_loss_ratio"])},
                ],
            }
        )
        method_blocks.append(
            {
                "type": "table",
                "columns": ["因子", "取值条件", "系数"],
                "rows": [[item["name"], item.get("condition") or "—", _ratio(item["coefficient"])] for item in classification.get("factors", [])],
            }
        )
        method_blocks.append({"type": "text", "text": "纯风险损失率 = 基准纯风险损失率 × 各因子系数连乘。综合评分指标和技术成熟度不作为分类费率因子。"})
    method_blocks.append(
        {
            "type": "kv",
            "items": [
                {"label": "费用率", "value": f"{_pct(primary['expense_ratio'])}（基数：保费）"},
                {"label": "风险附加率", "value": f"{_pct(primary['risk_load_rate'])}（基数：计费保额）"},
                {"label": "计费保额", "value": _amount_text(primary["billing_amount"])},
            ],
        }
    )
    method_blocks.append(
        {
            "type": "table",
            "columns": ["方法", "纯风险费率", "建议费率", "基准费率", "相对基准调整", "示例保费"],
            "rows": [
                [scheme["method"], _pct(scheme["pure_loss_ratio"]), _pct(scheme["suggested_rate"]), _pct(scheme["benchmark_rate"]), _ratio(scheme["adjustment"]), _amount_text(scheme["premium"])]
                for scheme in schemes.values()
            ],
        }
    )
    sections.append({"id": "methods", "title": "三、定价方法与费率结果", "blocks": method_blocks})

    band_issues = _band_issues(weighted.get("bands") or []) if weighted else []
    weight_ok = weighted is None or _d(weighted.get("weight_sum", 0)) == 1
    checks = [
        {"item": "测算口径与产品规则一致（责任、计费保额、期间、无免赔）", "passed": True, "note": "不一致的结果不会进入报告"},
        {"item": "评分指标权重合计为 1", "passed": weight_ok, "note": "" if weight_ok else "权重合计不为 1"},
        {"item": "得分区间覆盖 0–100 且连续", "passed": not band_issues, "note": "；".join(band_issues)},
        {"item": "报告数字与计算输出一致", "passed": True, "note": "不一致时拒绝保存报告"},
    ]
    verify_blocks: list[dict] = [
        {"type": "checks", "items": checks},
        {
            "type": "kv",
            "items": [
                {"label": "方法一建议费率", "value": _pct(weighted["suggested_rate"]) if weighted else "—"},
                {"label": "方法二建议费率", "value": _pct(classification["suggested_rate"]) if classification else "—"},
                {"label": "差额", "value": _pct(comparison.get("difference", 0))},
                {"label": "合成费率", "value": "不合成，两种方法并列呈现"},
            ],
        },
        {"type": "list", "items": comparison.get("reasons", [])},
        {"type": "text", "text": comparison.get("recommendation", "")},
    ]
    sensitivity_rows = [
        [schemes[key]["method"], _pct(schemes[key]["suggested_rate"]), _pct(item["suggested_rate"]), _amount_text(item["premium"])]
        for key, item in sensitivity_result.items()
        if key in schemes
    ]
    if sensitivity_rows:
        expense_label = _pct(next(iter(sensitivity_result.values()))["expense_ratio"])
        verify_blocks.append({"type": "table", "columns": ["方法", "当前建议费率", f"费用率 {expense_label} 时", "对应保费"], "rows": sensitivity_rows})
        verify_blocks.append({"type": "text", "text": "敏感性只记录假设变化的影响，不替换当前建议费率。"})
    sections.append({"id": "verification", "title": "四、检验、比较与选择建议", "blocks": verify_blocks})

    assumption_items = [
        "示例得分为讲解用假设，不是企业实际得分；正式承保时应使用企业真实数据。",
        "得分区间到纯风险费率的映射为暂定维护值，方向与幅度须经精算人员复核。",
        "费用率与风险附加率为公司假设，须结合经营数据复核。",
        "测算口径为无免赔；条款若设置免赔，须重新统一口径后再测算。",
    ]
    if provisional:
        assumption_items.append("分类费率样本不足 30 条，因子系数为已维护假设，数值为暂定，未声称完成拟合。")
    sections.append(
        {
            "id": "assumptions",
            "title": "五、假设与后续复核",
            "blocks": [
                {"type": "list", "items": assumption_items},
                {
                    "type": "kv",
                    "items": [
                        {"label": "方法一复核要求", "value": "复核评分指标与权重是否符合《创新积分制工作指引》，复核得分区间映射的方向与幅度，确认后方可作为正式费率依据。"},
                        {"label": "方法二校准要求", "value": "后续应补充赔案、对应计费保额、保险期间和风险特征，样本达到 30 条后重新拟合因子系数。"},
                        {"label": "审定与报批", "value": "本报告供精算人员审定。审定签署前不得用于报批或备案；正式毛费率由精算人员在两种方法中选定。"},
                    ],
                },
            ],
        }
    )

    report = {
        "title": "保险费率精算报告",
        "status": "待精算人员审定",
        "filed": False,
        "signed": False,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_comment": None,
        "liability": primary["liability"],
        "billing_amount": primary["billing_amount"],
        "period": primary["period"],
        "schemes": {key: dump(value) for key, value in schemes.items()},
        "comparison": comparison,
        "sensitivity": sensitivity_result,
        "sources": sources,
        "assumptions": "费用率以保费为基数，风险附加率以计费保额为基数，免赔额为 0",
        "calibration": "后续应补充赔案、对应计费保额、保险期间和风险特征，用于校准两种方法" if provisional else "",
        "provisional": provisional,
        "sections": sections,
    }
    for key, scheme in schemes.items():
        copied = report["schemes"][key]
        if copied["suggested_rate"] != scheme["suggested_rate"] or copied["premium"] != scheme["premium"]:
            raise RateError("报告数字与计算输出不一致", code="report_mismatch")
    return report