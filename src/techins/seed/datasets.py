"""承保历史与价格库的生成。"""

from __future__ import annotations

import math
import random

import numpy as np

TRACKS = ["工业机器人", "医疗器械", "新材料", "人工智能软件", "半导体设备"]
TRACK_RISK = {"工业机器人": 3, "医疗器械": 4, "新材料": 3, "人工智能软件": 3, "半导体设备": 4}
REGIONS = ["江苏", "浙江", "广东", "上海", "北京", "湖北", "四川", "安徽", "湖南"]


def generate_all_experience() -> dict[str, str]:
    """不再生成模拟经验 CSV。定价成熟度改为读取保单和赔案。"""
    return {}


STATEMENT_TEMPLATES = [
    "本公司为科技型企业，主营{field}，经营状况良好，所投保设备均为正常使用的研发设备，无已知事故与纠纷。",
    "我司专注于{field}研发，设备维护规范，未发生过保险事故，承诺所提供材料真实有效。",
    "公司在{field}领域拥有多项专利，生产经营正常，本次投保设备购置于近期，用于新项目研发。",
]
FRAUD_TEMPLATE = "我司主营{field}，所有设备均为全新购置，价值以发票为准，经营正常无任何纠纷，请尽快承保。"


def generate_historical_applications(n: int = 2400, seed: int = 2026) -> list[dict]:
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    rings = [
        {"phone": "13600001111", "address": "某省某市某园区创业路1号", "contact": "赵某", "intermediary": "I07"},
        {"phone": "13700002222", "address": "某省某市某开发区科技路8号", "contact": "钱某", "intermediary": "I11"},
        {"phone": "13800003333", "address": "某省某市某新区产业大道66号", "contact": "孙某", "intermediary": "I07"},
    ]
    bad_intermediaries = {"I07", "I11"}
    rows = []
    for i in range(n):
        track = rng.choice(TRACKS)
        ring = rings[rng.randrange(3)] if rng.random() < 0.035 else None
        si_to_value = float(nrng.lognormal(0.0, 0.18)) * (1.35 if ring and rng.random() < 0.6 else 1.0)
        recent_purchase = float(nrng.beta(1.2, 6.0)) + (0.45 if ring and rng.random() < 0.5 else 0.0)
        employees = int(nrng.lognormal(4.6, 0.8))
        ss_ratio = min(1.0, float(nrng.beta(8, 1.5))) * (0.15 if ring and rng.random() < 0.5 else 1.0)
        tax_to_rev = float(nrng.beta(2, 30))
        changes = int(nrng.poisson(0.4 + (1.6 if ring else 0)))
        prior_claims = int(nrng.poisson(0.3))
        intermediary = ring["intermediary"] if ring else f"I{rng.randint(1, 12):02d}"
        logit = (
            -4.6
            + 2.1 * (si_to_value > 1.2)
            + 2.6 * recent_purchase
            + 1.9 * (ss_ratio < 0.3)
            + 0.45 * changes
            + 0.35 * prior_claims
            + 1.6 * (ring is not None)
            + 0.7 * (intermediary in bad_intermediaries)
            + 0.15 * (TRACK_RISK[track] - 3)
            + float(nrng.normal(0, 0.6))
        )
        fraud = int(rng.random() < 1 / (1 + math.exp(-logit)))
        pattern = None
        if fraud:
            causes = [("P02", si_to_value > 1.2), ("P01", recent_purchase > 0.4), ("P07", ss_ratio < 0.3), ("P08", changes >= 2), ("P06", ring is not None)]
            pattern = next((c for c, ok in causes if ok), "P03")
        field = {"工业机器人": "机器人核心部件", "医疗器械": "医疗器械", "新材料": "先进材料", "人工智能软件": "人工智能软件", "半导体设备": "半导体装备"}[track]
        statement = (FRAUD_TEMPLATE if fraud and rng.random() < 0.55 else rng.choice(STATEMENT_TEMPLATES)).format(field=field)
        contacts = ring or {
            "phone": f"1{rng.choice('3578')}{rng.randint(100000000, 999999999)}",
            "address": f"{rng.choice(REGIONS)}某市某区第{rng.randint(1, 300)}号",
            "contact": rng.choice("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨") + "某",
        }
        rows.append(
            {
                "enterprise_name": f"历史投保企业{i:04d}",
                "line": rng.choice(["RD08", "RD08", "RD09", "AI01", "CY01"]),
                "year": rng.choice([2022, 2023, 2024, 2025]),
                "features": {
                    "si_to_value": round(si_to_value, 3),
                    "recent_purchase_share": round(min(recent_purchase, 1.0), 3),
                    "employees": employees,
                    "social_security_ratio": round(ss_ratio, 3),
                    "tax_to_revenue": round(tax_to_rev, 4),
                    "change_count_1y": changes,
                    "prior_claims": prior_claims,
                    "industry_risk": TRACK_RISK[track],
                    "track": track,
                },
                "contacts": {"phone": contacts["phone"], "address": contacts["address"], "contact": contacts["contact"]},
                "intermediary": intermediary,
                "statement": statement,
                "fraud_label": fraud,
                "claimed": int(fraud or rng.random() < 0.08),
                "fraud_pattern": pattern,
            }
        )
    return rows


PRICE_ITEMS = [
    ("机械部件", "主轴总成", "套", 286000, 10, ["主轴", "主轴总成"]),
    ("机械部件", "精密轴承", "件", 18600, 5, ["轴承", "保持架"]),
    ("机械部件", "谐波减速器", "台", 9200, 8, ["谐波减速器", "减速器"]),
    ("电气部件", "伺服电机", "台", 32500, 8, ["伺服电机", "电机"]),
    ("电气部件", "绝对值编码器", "件", 8600, 8, ["编码器"]),
    ("电气部件", "运动控制板卡", "块", 42000, 6, ["控制板", "板卡", "控制器"]),
    ("电气部件", "高温热电偶", "支", 3200, 3, ["热电偶"]),
    ("真空与射频", "干式真空泵", "台", 268000, 8, ["真空泵"]),
    ("真空与射频", "射频电源", "台", 520000, 10, ["射频电源"]),
    ("光学部件", "高数值孔径物镜", "件", 380000, 10, ["物镜", "镜头"]),
    ("光学部件", "激光谐振腔组件", "套", 460000, 8, ["谐振腔", "激光器"]),
    ("服务", "设备维修工时", "小时", 650, 0, ["工时", "人工"]),
    ("服务", "现场检测鉴定", "次", 28000, 0, ["检测", "鉴定"]),
    ("服务", "数据恢复服务", "TB", 12000, 0, ["数据恢复"]),
    ("服务", "应急响应取证服务", "人天", 9800, 0, ["应急响应", "取证"]),
    ("服务", "律师服务", "小时", 2500, 0, ["律师"]),
    ("服务", "设备租赁（替代设备）", "台·月", 68000, 0, ["租赁"]),
]
