"""已维护的产品开发参数。缺来源或适用条件的参数不能进入测算。"""

from __future__ import annotations

import json
from copy import deepcopy

from ..config import settings

EVIDENCE_CLASSES = ("公开证据", "经验数据", "专家判断", "系统生成")


class ParameterError(ValueError):
    pass


def _blank() -> dict:
    return {
        "expense_ratio": {
            "value": "0.20",
            "base": "保费",
            "source": "公司费用假设",
            "version": "2026-01",
            "time": "2026-01-01",
            "applicability": "产品开发默认口径",
            "evidence_class": "专家判断",
        },
        "risk_load_rate": {
            "value": "0.001",
            "base": "计费保额",
            "source": "公司风险附加假设",
            "version": "2026-01",
            "time": "2026-01-01",
            "applicability": "产品开发默认口径",
            "evidence_class": "专家判断",
        },
        "classification": {
            "benchmark_condition": "基准风险条件",
            "benchmark_pure": "0.0025",
            "source": "分类费率基准假设",
            "version": "2026-01",
            "time": "2026-01-01",
            "applicability": "尚未积累足够损失数据的新产品",
            "evidence_class": "专家判断",
            "factors": [
                {"name": "风险暴露高于基准", "risk_key": "exposure", "condition": "暴露高于基准类别", "coefficient": "1.10"},
                {"name": "监测与应急优于基准", "risk_key": "monitor", "condition": "监测较基准完善", "coefficient": "0.95"},
            ],
        },
        "experience": [],
        "resources": [],
        "score_pricing": _score_pricing_seed(),
    }


def _score_pricing_seed() -> dict:
    return {
        "source": "创新积分制工作指引的企业评价维度，结合目标险种由精算与领域专家确认的权重和映射",
        "applicability": "尚未取得单家企业实际指标、需要为同类企业设计可复用费率规则的产品",
        "evidence_class": "专家判断",
        "version": "2026-01",
        "time": "2026-01-01",
        "formula": "综合得分等于各项指标得分乘以对应权重后相加",
        "example_score": "69",
        "mapping_basis": "各得分区间的纯风险费率来自已维护参数，计算时只按得分落入的区间取值，不根据得分高低自动调高或调低费率",
        "indicators": [
            {"name": "创新投入", "definition": "研发人员、研发投入和研发设施等投入强度的评分", "weight": "0.40", "source": "创新积分制投入类指标"},
            {"name": "创新产出", "definition": "知识产权、技术合同和科技成果转化等产出的评分", "weight": "0.35", "source": "创新积分制产出类指标"},
            {"name": "创新发展", "definition": "成长性和融资等发展情况的评分", "weight": "0.25", "source": "创新积分制发展类指标"},
        ],
        "bands": [
            {"label": "0–40", "min": "0", "max": "40", "pure": "0.0045", "reference": False},
            {"label": "40–70", "min": "40", "max": "70", "pure": "0.0032", "reference": False},
            {"label": "70–90", "min": "70", "max": "90", "pure": "0.0036", "reference": True},
            {"label": "90–100", "min": "90", "max": "100", "pure": "0.0055", "reference": False, "closed_max": True},
        ],
    }


def _path():
    return settings.data_dir / "development_parameters.json"


def load() -> dict:
    path = _path()
    if not path.exists():
        data = _blank()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    data = json.loads(path.read_text(encoding="utf-8"))
    if "score_pricing" not in data:
        data["score_pricing"] = _score_pricing_seed()
        save(data)
    return data


def save(data: dict) -> dict:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _check_provenance(item: dict) -> None:
    if not item.get("source") or not item.get("applicability"):
        raise ParameterError("该参数缺少可追溯信息")
    if item.get("evidence_class") not in EVIDENCE_CLASSES:
        raise ParameterError("证据类型必须是公开证据、经验数据、专家判断或系统生成")


def expense_ratio() -> dict:
    item = load()["expense_ratio"]
    _check_provenance(item)
    if item.get("base") != "保费":
        raise ParameterError("费用率必须以保费为基数")
    return item


def risk_load_rate() -> dict:
    item = load()["risk_load_rate"]
    _check_provenance(item)
    if item.get("base") != "计费保额":
        raise ParameterError("风险附加率必须以计费保额为基数")
    return item


def score_pricing() -> dict:
    item = deepcopy(load()["score_pricing"])
    _check_provenance(item)
    if not item.get("mapping_basis") or not item.get("indicators") or not item.get("bands"):
        raise ParameterError("创新积分制评分参数不完整")
    return item


def classification_assumptions() -> dict:
    item = deepcopy(load()["classification"])
    _check_provenance(item)
    return item


def save_classification_factor(factor: dict) -> dict:
    name = str(factor.get("name") or "")
    if "技术成熟度" in name or factor.get("risk_key") == "maturity":
        raise ParameterError("技术成熟度不能作为分类费率因子")
    _check_provenance(factor)
    data = load()
    data["classification"]["factors"].append(factor)
    save(data)
    return factor


def experience_rows() -> tuple[list[dict], list[dict]]:
    usable, rejected = [], []
    rows = list(load()["experience"])
    path = settings.datasets_dir / "experience_product_development.csv"
    if path.exists():
        import csv

        with path.open(encoding="utf-8", newline="") as handle:
            rows.extend(dict(row) for row in csv.DictReader(handle))
    for row in rows:
        if row.get("liability") and row.get("billing_amount") and row.get("period"):
            usable.append({**row, "usable": True})
        else:
            rejected.append({**row, "usable": False, "reason": "口径不完整"})
    return usable, rejected


def register_resource(resource: dict) -> dict:
    _check_provenance(resource)
    data = load()
    existing = next((item for item in data["resources"] if item["id"] == resource["id"]), None)
    if existing is None:
        resource = {**resource, "uses": []}
        data["resources"].append(resource)
        existing = resource
    save(data)
    return existing


def cite_resource(resource_id: str, use: str) -> dict:
    data = load()
    resource = next((item for item in data["resources"] if item["id"] == resource_id), None)
    if resource is None:
        raise ParameterError("资源不存在")
    resource.setdefault("uses", []).append(use)
    save(data)
    return resource


def report_sources() -> list[dict]:
    """报告第二章的资料与参数依据，逐条给出来源、证据类型和适用条件。"""
    data = load()
    expense = data["expense_ratio"]
    load_rate = data["risk_load_rate"]
    score = data["score_pricing"]
    classification = data["classification"]
    usable, rejected = experience_rows()
    rows = [
        {"name": "费用率", "source": expense["source"], "evidence_class": expense["evidence_class"], "applicability": expense["applicability"], "version": expense.get("version")},
        {"name": "风险附加率", "source": load_rate["source"], "evidence_class": load_rate["evidence_class"], "applicability": load_rate["applicability"], "version": load_rate.get("version")},
        {"name": "创新积分制指标、权重与得分映射", "source": score["source"], "evidence_class": score["evidence_class"], "applicability": score["applicability"], "version": score.get("version")},
        {"name": "分类费率基准与因子假设", "source": classification["source"], "evidence_class": classification["evidence_class"], "applicability": classification["applicability"], "version": classification.get("version")},
        {"name": "经验损失样本", "source": f"已维护样本与经验数据表，可用 {len(usable)} 条，口径不完整剔除 {len(rejected)} 条", "evidence_class": "经验数据", "applicability": "责任、计费保额、期间齐全的记录才计入", "version": None},
        {"name": "《创新积分制工作指引》", "source": "科技部火炬中心 2025 年 10 月发布（2.0 版）", "evidence_class": "公开证据", "applicability": "企业创新评价的指标体系参照", "version": "2.0"},
    ]
    for resource in data.get("resources", []):
        rows.append({"name": resource.get("name") or resource["id"], "source": resource.get("source"), "evidence_class": resource.get("evidence_class"), "applicability": resource.get("applicability"), "version": resource.get("version")})
    return rows
