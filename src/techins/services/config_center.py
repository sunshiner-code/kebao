"""配置中心：核保规则、预警模板、合规规则库、数据分级与脱敏、出域策略、审批流、参数字典、通知、报告模板、安全、额度等平台配置。

每项配置有结构定义（供前端通用编辑器渲染）、默认值、校验规则与版本历史；修改即时生效并写配置审计。
运行时读取走进程内缓存：启动时加载，修改后刷新；缓存缺失且当前线程已有数据库事务时直接使用默认值，
避免 SQLite 下同线程嵌套事务。
"""

from __future__ import annotations

import copy
import re
import threading
from typing import Any, Callable

from ..db import session_scope, utcnow
from ..knowledge.catalog import INCIDENT_TYPES, KRI_LIBRARY, LINES

# ---------------------------------------------------------------- 结构定义辅助


def num(key: str, label: str, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "number", "help": help, **kw}


def pct(key: str, label: str, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "percent", "help": help, **kw}


def text(key: str, label: str, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "string", "help": help, **kw}


def para(key: str, label: str, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "text", "help": help, **kw}


def flag(key: str, label: str, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "boolean", "help": help, **kw}


def tags(key: str, label: str, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "tags", "help": help, **kw}


def select(key: str, label: str, options: list[tuple[str, str]], help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "select", "options": [{"value": v, "label": l} for v, l in options], "help": help, **kw}


def obj(key: str, label: str, fields: list[dict], help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "object", "fields": fields, "help": help, **kw}


def lst(key: str, label: str, item: dict, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "list", "item": item, "help": help, **kw}


def mapping(key: str, label: str, value: dict, key_options: list[tuple[str, str]] | None = None, help: str = "", **kw: Any) -> dict:
    return {"key": key, "label": label, "type": "map", "value": value, "key_options": [{"value": v, "label": l} for v, l in (key_options or [])], "help": help, **kw}


class ConfigError(ValueError):
    pass


LINE_OPTIONS = [(k, v["name"]) for k, v in LINES.items()]
ROLE_OPTIONS = [("designer", "产品设计人员"), ("service", "产品服务人员"), ("admin", "管理人员")]
RISK_PARTS = [("标的风险", "标的风险"), ("经营风险", "经营风险"), ("技术风险", "技术风险"), ("欺诈风险", "欺诈风险"), ("合规风险", "合规风险")]
CONDITION_TYPES = [("special", "特别约定"), ("deductible", "免赔调整"), ("loading", "费率加费"), ("exclusion", "除外责任")]
MASK_METHODS = [("token", "占位符替换（可回填）"), ("generalize", "泛化（地址到市级）"), ("bucket", "区间化（金额档位）"), ("mask", "部分遮盖"), ("drop", "删除")]
CHANNELS = [("inbox", "站内信"), ("email", "邮件"), ("sms", "短信"), ("im", "企业即时通讯")]
METHOD_OPTIONS = [(m, m) for m in ("DM1", "DM2", "DM3", "DM4", "DM5", "TP1", "TP2", "TP3", "TP4", "TP5")]
FREQ_OPTIONS = [("每分钟", "每分钟"), ("每小时", "每小时"), ("每日", "每日"), ("每周", "每周"), ("每月", "每月")]
SOURCE_OPTIONS = [("iot", "物联网"), ("log", "信息安全日志"), ("ops", "研发与运营数据")]
REPORT_TYPES = {
    "risk_report": "风险评估报告",
    "clause_explain": "条款通俗解读",
    "actuarial": "精算报告",
    "uw_opinion": "核保意见书",
    "risk_mgmt": "风险管理报告",
    "risk_notice": "风险提示函",
    "settlement": "理算书",
    "denial": "拒赔通知书",
    "policy": "电子保单",
    "endorsement": "批单",
    "invoice": "保费发票",
}


def _default_rw_templates() -> dict:
    out: dict[str, list[dict]] = {}
    freq = {"iot": "每小时", "log": "每小时", "ops": "每日"}
    for line, items in KRI_LIBRARY.items():
        out[line] = [{"key": k["key"], "name": k["name"], "source": k["source"], "unit": k["unit"], "threshold": k["threshold"], "direction": k["direction"], "methods": list(k["methods"]), "frequency": freq.get(k["source"], "每日"), "enabled": True} for k in items]
    return out


# ---------------------------------------------------------------- 配置项


SPECS: dict[str, dict] = {
    "uw.rules": {
        "category": "核保",
        "title": "核保规则与自动核保阈值",
        "page": "P-0306",
        "description": "决策矩阵阈值、核保权限分级、自动核保条件、附加条件模板与续保经验费率。修改后对新发起的核保运行生效。",
        "schema": [
            obj("thresholds", "决策矩阵阈值", [
                num("reject_total", "建议拒保：评分卡总分不低于", min=0, max=100),
                pct("reject_fraud", "建议拒保：欺诈概率不低于"),
                num("manual_total", "转人工：总分不低于", min=0, max=100),
                pct("manual_fraud", "转人工：欺诈概率不低于"),
                num("conditional_total", "附条件承保：总分不低于", min=0, max=100),
            ]),
            lst("authority", "核保权限分级（自上而下匹配，均不满足时需 3 级）", obj("", "", [num("max_sum_insured", "保额上限（元）", min=0), num("max_total", "总分上限", min=0, max=100), num("level", "所需审批等级", min=1, max=3, integer=True)])),
            num("reject_min_level", "拒保决定的最低审批等级", min=1, max=3, integer=True),
            obj("auto_uw", "自动核保", [flag("enabled", "启用自动核保"), num("max_sum_insured", "自动核保保额上限（元）", min=0), flag("warm_only", "仅热启动险种可自动核保")]),
            lst("condition_templates", "附加条件模板", obj("", "", [select("risk", "适用风险分项", RISK_PARTS), select("type", "条件类型", CONDITION_TYPES), para("text", "条件表述"), pct("loading", "费率调整", help="正数为加费，负数为减费")])),
            obj("renewal", "续保经验费率", [
                pct("loss_ratio_high", "高赔付率阈值"), pct("loading_high", "高赔付率加费"),
                pct("loss_ratio_mid", "中赔付率阈值"), pct("loading_mid", "中赔付率加费"),
                pct("ncd_discount", "无赔款优待（赔付率低于 10% 且无橙色以上预警）"),
                pct("alert_loading", "每条橙色及以上预警加费"), pct("alert_loading_cap", "预警加费上限"),
            ]),
        ],
        "default": {
            "thresholds": {"reject_total": 80, "reject_fraud": 0.5, "manual_total": 60, "manual_fraud": 0.2, "conditional_total": 40},
            "authority": [{"max_sum_insured": 5e6, "max_total": 60, "level": 1}, {"max_sum_insured": 3e7, "max_total": 79.9, "level": 2}],
            "reject_min_level": 2,
            "auto_uw": {"enabled": True, "max_sum_insured": 5e6, "warm_only": True},
            "condition_templates": [
                {"risk": "标的风险", "type": "special", "text": "被保险人应按设备厂商要求执行维保并保留记录，关键设备每季度提交运行状态报告", "loading": 0.0},
                {"risk": "标的风险", "type": "deductible", "text": "每次事故免赔额提高至原约定的 1.5 倍", "loading": -0.06},
                {"risk": "经营风险", "type": "loading", "text": "费率上浮 15%", "loading": 0.15},
                {"risk": "技术风险", "type": "exclusion", "text": "约定研发项目技术路线变更导致的损失除外", "loading": -0.03},
                {"risk": "欺诈风险", "type": "special", "text": "投保前 30 天内新购置设备设 90 天观察期，观察期内发生的事故不承担赔偿责任", "loading": 0.0},
                {"risk": "合规风险", "type": "special", "text": "被保险人应在保险期间内完成监管整改并书面告知保险人", "loading": 0.05},
            ],
            "renewal": {"loss_ratio_high": 0.7, "loading_high": 0.2, "loss_ratio_mid": 0.4, "loading_mid": 0.1, "ncd_discount": 0.05, "alert_loading": 0.05, "alert_loading_cap": 0.15},
        },
    },
    "rw.templates": {
        "category": "预警",
        "title": "预警规则与阈值模板",
        "page": "P-0312",
        "description": "按险种维护默认监测指标模板（阈值、检测方法、频率），以及静默期、预警风暴上限与各等级处置时限。对新生成的监测方案生效。",
        "schema": [
            mapping("lines", "险种监测模板", lst("", "", obj("", "", [text("key", "指标编码", readonly=True), text("name", "指标名称"), select("source", "数据来源", SOURCE_OPTIONS), text("unit", "单位"), num("threshold", "默认阈值"), select("direction", "方向", [("up", "越高越危险"), ("down", "越低越危险")]), {"key": "methods", "label": "检测方法", "type": "multiselect", "options": [{"value": v, "label": l} for v, l in METHOD_OPTIONS]}, select("frequency", "频率", FREQ_OPTIONS), flag("enabled", "启用")])), key_options=LINE_OPTIONS),
            num("silence_hours", "同类事件静默期（小时）", min=1, max=168, integer=True),
            num("storm_cap", "单次检测最多发起研判数（预警风暴上限）", min=1, max=50, integer=True),
            obj("sla_hours", "处置时限（小时）", [num("red", "红色"), num("orange", "橙色"), num("yellow", "黄色"), num("blue", "蓝色")]),
        ],
        "default": {"lines": _default_rw_templates(), "silence_hours": 24, "storm_cap": 5, "sla_hours": {"red": 1, "orange": 4, "yellow": 24, "blue": 168}},
    },
    "compliance.rules": {
        "category": "隐私与合规",
        "title": "合规规则库",
        "page": "P-1012",
        "description": "条款合规校验的禁用表述与模糊表述、费率报备浮动区间、生成内容合规规则。C01–C16 中依据保险法的规则为强制规则，不可关闭。",
        "schema": [
            tags("forbidden_words", "禁用表述（承诺性、误导性）", help="出现即判定 C13 不通过，自动整改时替换为“按照本保险合同约定赔偿”"),
            tags("ambiguous_words", "模糊表述（保险责任中出现时提示）"),
            obj("rate_bands", "费率报备浮动区间", [pct("warm", "热启动险种（±）"), pct("cold", "冷启动险种（±）")]),
            lst("generation_rules", "生成内容合规规则", obj("", "", [text("code", "编号"), text("name", "规则名称"), para("text", "规则内容"), flag("enabled", "启用")])),
            text("ai_mark", "生成内容标识文字"),
        ],
        "default": {
            "forbidden_words": ["全额赔付", "无条件赔付", "保证赔付", "百分之百赔付", "一律赔付"],
            "ambiguous_words": ["相关", "适当", "一定程度", "原则上", "视情况"],
            "rate_bands": {"warm": 0.25, "cold": 0.4},
            "generation_rules": [
                {"code": "G01", "name": "生成内容标识", "text": "智能体生成的报告、条款草稿、意见书在导出时必须带生成内容标识", "enabled": True},
                {"code": "G02", "name": "结论须有证据", "text": "报告中的风险结论、预警研判与理赔结论须引用已核验的证据编号，无证据的陈述在严格模式下删除", "enabled": True},
                {"code": "G03", "name": "不得承诺赔付结果", "text": "对客户的风险提示函、通俗解读不得出现承诺赔付或保证收益的表述", "enabled": True},
                {"code": "G04", "name": "私域信息不得出现在对外文书", "text": "对外文书不得包含其他客户的私域信息，出具前执行敏感信息扫描", "enabled": True},
            ],
            "ai_mark": "本文件由智能体生成、经人工审核",
        },
    },
    "privacy.rules": {
        "category": "隐私与合规",
        "title": "数据分级与脱敏规则",
        "page": "P-1010",
        "description": "敏感信息识别类型的级别、启用状态与脱敏方式；自定义识别规则；文档类型默认级别。个人信息与核心技术秘密为 L3 法定底线，不可下调或停用。",
        "schema": [
            mapping("kinds", "识别类型", obj("", "", [text("label", "名称", readonly=True), num("level", "数据级别", min=0, max=3, integer=True), flag("enabled", "启用识别"), select("method", "出域脱敏方式", MASK_METHODS)])),
            lst("custom_rules", "自定义识别规则", obj("", "", [text("name", "规则名称"), text("pattern", "正则表达式"), num("level", "数据级别", min=1, max=3, integer=True), select("method", "脱敏方式", MASK_METHODS), flag("enabled", "启用")])),
            mapping("doc_type_levels", "文档类型默认级别", num("", "", min=0, max=3, integer=True)),
        ],
        "default": {
            "kinds": {
                "id_card": {"label": "身份证号", "level": 3, "enabled": True, "method": "token"},
                "phone": {"label": "手机号", "level": 3, "enabled": True, "method": "token"},
                "landline": {"label": "固定电话", "level": 3, "enabled": True, "method": "token"},
                "bank_card": {"label": "银行账号", "level": 3, "enabled": True, "method": "token"},
                "email": {"label": "电子邮箱", "level": 3, "enabled": True, "method": "token"},
                "person": {"label": "人名", "level": 3, "enabled": True, "method": "token"},
                "tech_secret": {"label": "核心技术秘密", "level": 3, "enabled": True, "method": "token"},
                "address": {"label": "详细地址", "level": 2, "enabled": True, "method": "generalize"},
                "amount": {"label": "精确金额", "level": 2, "enabled": True, "method": "bucket"},
                "company": {"label": "企业名称", "level": 2, "enabled": True, "method": "token"},
            },
            "custom_rules": [
                {"name": "统一社会信用代码", "pattern": r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Z])", "level": 2, "method": "token", "enabled": True},
                {"name": "设备序列号", "pattern": r"(?<![A-Z0-9])[A-Z]{2,4}-\d{4}-[A-Z0-9]{3,6}(?![A-Z0-9])", "level": 2, "method": "mask", "enabled": False},
            ],
            "doc_type_levels": {"尽调报告": 2, "访谈纪要": 2, "财务报表": 2, "技术资料": 3, "营业执照": 1, "设备清单": 2, "安全测评报告": 2, "数据接入授权书": 2, "索赔申请书": 2, "其他材料": 2},
        },
    },
    "privacy.egress": {
        "category": "隐私与合规",
        "title": "出域策略",
        "page": "P-1011",
        "description": "私域数据（L2）出域到云端模型前的白名单、最小化与扫描策略。L3 数据一律不出域；出域二次扫描为强制要求。",
        "schema": [
            flag("whitelist_enabled", "启用任务级出域白名单", help="开启后只有白名单内的推理任务允许携带 L2 脱敏数据出域，其余任务自动回退到私域模型"),
            tags("task_whitelist", "出域白名单（推理任务名称）"),
            tags("minimize_fields", "最小化：出域前删除的字段名", help="载荷中键名命中的字段在脱敏前直接删除"),
            num("max_chars", "单次出域最大字符数", min=1000, integer=True),
            select("over_limit", "超过最大字符数时", [("flag", "标记并放行"), ("block", "阻断并回退私域")]),
            flag("outbound_scan", "出域二次扫描（强制）", readonly=True),
            flag("inbound_scan", "云端返回内容注入扫描"),
            num("spike_per_hour", "出域量突增告警阈值（次/小时）", min=1, integer=True),
        ],
        "default": {
            "whitelist_enabled": False,
            "task_whitelist": ["ra.analyze_public", "ra.classify_risks", "ra.write_report", "cd.compliance_review", "cd.explain", "uw.opinion", "cl.opinion", "rw.analyze_alert", "rw.compose_report", "harness.plan"],
            "minimize_fields": [],
            "max_chars": 60000,
            "over_limit": "flag",
            "outbound_scan": True,
            "inbound_scan": True,
            "spike_per_hour": 200,
        },
    },
    "approval.flows": {
        "category": "系统配置",
        "title": "审批流配置",
        "page": "P-1015",
        "description": "产品发布、批改、知识变更、自进化发布、临时授权、导出审批的节点与审批人规则；核保与理赔按金额分级的审批权限。",
        "schema": [
            mapping("flows", "审批流程", obj("", "", [
                text("name", "流程名称"),
                lst("stages", "审批节点", obj("", "", [text("key", "节点编码"), text("name", "节点名称"), select("role", "审批角色", ROLE_OPTIONS), text("permission", "所需权限点"), num("min_level", "最低审批等级", min=1, max=3, integer=True), select("mode", "会签方式", [("any", "或签（一人通过即可）"), ("all", "会签（全部通过）")]), num("deadline_hours", "超时（小时）", min=1, integer=True)])),
                lst("conditional_stages", "条件分支节点", obj("", "", [select("when", "触发条件", [("cold_start", "冷启动产品"), ("large_amount", "金额超过阈值"), ("fraud_suspect", "欺诈可疑"), ("high_risk", "高风险等级")]), num("amount", "金额阈值（元）", min=0), text("key", "节点编码"), text("name", "节点名称"), select("role", "审批角色", ROLE_OPTIONS), text("permission", "所需权限点"), num("min_level", "最低审批等级", min=1, max=3, integer=True), num("deadline_hours", "超时（小时）", min=1, integer=True)])),
                select("reject_to", "驳回目标", [("applicant", "退回申请人"), ("previous", "退回上一节点")]),
                select("timeout", "超时策略", [("escalate", "升级通知机构负责人"), ("remind", "仅提醒")]),
            ])),
            obj("claim_authority", "理赔审批权限", [num("level1_max", "一级权限赔付上限（元）", min=0), num("level2_max", "二级权限赔付上限（元）", min=0), num("reject_min_level", "部分或全部拒赔最低等级", min=1, max=3, integer=True), num("cold_min_level", "冷启动案件最低等级", min=1, max=3, integer=True), pct("width_threshold", "责任比例区间宽度超过时升级")]),
        ],
        "default": {
            "flows": {
                "product_publish": {
                    "name": "产品发布审批",
                    "stages": [
                        {"key": "review1", "name": "一审（条款与产品）", "role": "designer", "permission": "product.review1", "min_level": 1, "mode": "any", "deadline_hours": 48},
                        {"key": "review2", "name": "二审（精算与产品负责人）", "role": "designer", "permission": "product.review2", "min_level": 2, "mode": "any", "deadline_hours": 48},
                        {"key": "final", "name": "终审发布（业务管理）", "role": "admin", "permission": "product.publish", "min_level": 3, "mode": "any", "deadline_hours": 72},
                    ],
                    "conditional_stages": [{"when": "cold_start", "amount": 0, "key": "uw_review", "name": "冷启动核保复核", "role": "service", "permission": "uw.decide", "min_level": 2, "deadline_hours": 48}],
                    "reject_to": "applicant",
                    "timeout": "escalate",
                },
                "endorsement": {
                    "name": "批改审批",
                    "stages": [{"key": "review", "name": "批改复核", "role": "service", "permission": "policy.endorse.approve", "min_level": 1, "mode": "any", "deadline_hours": 24}],
                    "conditional_stages": [{"when": "large_amount", "amount": 50000, "key": "senior", "name": "大额批改复核", "role": "service", "permission": "policy.endorse.approve", "min_level": 2, "deadline_hours": 24}],
                    "reject_to": "applicant",
                    "timeout": "remind",
                },
                "knowledge_change": {
                    "name": "知识变更审批",
                    "stages": [{"key": "biz", "name": "业务审批", "role": "admin", "permission": "kb.proposal.approve", "min_level": 1, "mode": "any", "deadline_hours": 72}],
                    "conditional_stages": [],
                    "reject_to": "applicant",
                    "timeout": "remind",
                },
                "evolution_release": {
                    "name": "自进化发布审批",
                    "stages": [
                        {"key": "tech", "name": "技术评审", "role": "admin", "permission": "agent.config.edit", "min_level": 1, "mode": "any", "deadline_hours": 48},
                        {"key": "biz", "name": "业务确认", "role": "admin", "permission": "agent.release", "min_level": 1, "mode": "any", "deadline_hours": 48},
                    ],
                    "conditional_stages": [],
                    "reject_to": "applicant",
                    "timeout": "escalate",
                },
                "temp_grant": {
                    "name": "临时授权审批",
                    "stages": [{"key": "grant", "name": "授权审批", "role": "admin", "permission": "admin.users", "min_level": 1, "mode": "any", "deadline_hours": 4}],
                    "conditional_stages": [],
                    "reject_to": "applicant",
                    "timeout": "remind",
                },
                "export": {
                    "name": "导出审批",
                    "stages": [{"key": "audit", "name": "安全审计审批", "role": "admin", "permission": "admin.audit.export", "min_level": 1, "mode": "any", "deadline_hours": 24}],
                    "conditional_stages": [],
                    "reject_to": "applicant",
                    "timeout": "remind",
                },
            },
            "claim_authority": {"level1_max": 5e5, "level2_max": 3e6, "reject_min_level": 2, "cold_min_level": 2, "width_threshold": 0.2},
        },
    },
    "params.dicts": {
        "category": "系统配置",
        "title": "参数与字典",
        "page": "P-1014",
        "description": "业务字典、编号规则与系统参数。",
        "schema": [
            tags("industries", "行业分类"),
            tags("tech_fields", "技术领域"),
            tags("material_types", "材料类型"),
            mapping("incident_types", "事故类型", text("", "")),
            tags("regions", "销售区域"),
            mapping("numbering", "编号规则（前缀）", text("", ""), help="新生成单据按“前缀 + 日期 + 序号”编号"),
            obj("system", "系统参数", [pct("expense_ratio", "费用率（综合成本率口径）"), num("workspace_retention_days", "临时工作区保留天数", min=7, integer=True), num("renewal_notice_days", "续保提醒提前天数", min=7, integer=True), num("evidence_window_days", "事故数据窗口（事故前天数）", min=1, integer=True)]),
        ],
        "default": {
            "industries": ["智能制造", "新一代信息技术", "生物医药", "新材料", "新能源", "人工智能", "集成电路"],
            "tech_fields": ["工业机器人", "人工智能软件", "半导体设备", "创新药", "动力电池", "医疗器械", "工业软件"],
            "material_types": ["尽调报告", "访谈纪要", "财务报表", "技术资料", "营业执照", "设备清单", "设备铭牌照片", "设备发票", "安全测评报告", "数据接入授权书", "索赔申请书", "损失清单", "其他材料"],
            "incident_types": {"data_breach": "数据泄露或勒索攻击", "equipment_damage": "关键设备损坏", "lab_fire": "实验室火灾或水浸", "product_liability": "产品责任事故", "ip_infringement": "知识产权侵权纠纷"},
            "regions": ["华东", "华南", "华北", "华中", "西南", "西北", "东北"],
            "numbering": {"application": "TB", "policy": "PKJ", "alert": "YJ", "incident": "SG", "claim": "LP", "endorsement": "PG", "approval": "SP", "authorization": "SQ", "document": "DZ", "knowledge_proposal": "ZB", "evolution_proposal": "JH", "ingest_batch": "RK"},
            "system": {"expense_ratio": 0.32, "workspace_retention_days": 90, "renewal_notice_days": 60, "evidence_window_days": 30},
        },
    },
    "notify.config": {
        "category": "系统配置",
        "title": "通知配置",
        "page": "P-1016",
        "description": "通知渠道、事件订阅规则、免打扰与同类通知聚合。",
        "schema": [
            obj("channels", "可用渠道", [flag("inbox", "站内信", readonly=True), flag("email", "邮件"), flag("sms", "短信"), flag("im", "企业即时通讯")]),
            lst("subscriptions", "订阅规则", obj("", "", [text("kind", "通知类型"), text("name", "说明"), {"key": "roles", "label": "接收角色", "type": "multiselect", "options": [{"value": v, "label": l} for v, l in ROLE_OPTIONS]}, {"key": "channels", "label": "渠道", "type": "multiselect", "options": [{"value": v, "label": l} for v, l in CHANNELS]}, select("min_level", "最低级别", [("info", "全部"), ("warning", "警告及以上"), ("error", "仅严重")])])),
            obj("quiet_hours", "免打扰", [flag("enabled", "启用"), text("start", "开始（HH:MM）"), text("end", "结束（HH:MM）"), flag("allow_error", "严重通知不受免打扰限制")]),
            num("aggregate_minutes", "同类通知合并窗口（分钟）", min=0, max=120, integer=True),
        ],
        "default": {
            "channels": {"inbox": True, "email": False, "sms": False, "im": False},
            "subscriptions": [
                {"kind": "alert", "name": "预警发布", "roles": ["service"], "channels": ["inbox", "sms"], "min_level": "info"},
                {"kind": "hitl", "name": "人审待办", "roles": ["designer", "service", "admin"], "channels": ["inbox", "im"], "min_level": "info"},
                {"kind": "approval", "name": "审批待办", "roles": ["designer", "service", "admin"], "channels": ["inbox", "im"], "min_level": "info"},
                {"kind": "claim", "name": "理赔进展", "roles": ["service"], "channels": ["inbox"], "min_level": "info"},
                {"kind": "system", "name": "系统告警", "roles": ["admin"], "channels": ["inbox", "email"], "min_level": "warning"},
                {"kind": "renewal", "name": "续保提醒", "roles": ["service"], "channels": ["inbox", "email"], "min_level": "info"},
            ],
            "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00", "allow_error": True},
            "aggregate_minutes": 10,
        },
    },
    "report.templates": {
        "category": "系统配置",
        "title": "报告模板",
        "page": "P-1017",
        "description": "各类报告与单证的章节结构、页眉页脚与生成内容标识。电子保单、批单、发票、理算书等单证按模板生成。",
        "schema": [
            mapping("templates", "模板", obj("", "", [text("name", "模板名称"), tags("sections", "章节结构"), text("header", "页眉"), text("footer", "页脚"), flag("mark_ai", "带生成内容标识")]), key_options=list(REPORT_TYPES.items())),
        ],
        "default": {
            "templates": {
                "risk_report": {"name": "风险评估报告", "sections": ["评估概述", "风险清单", "三阶段归类", "风险热力矩阵", "可保性分析", "数据缺口与建议"], "header": "科技保险风险评估报告", "footer": "内部资料 · 注意保密", "mark_ai": True},
                "clause_explain": {"name": "条款通俗解读", "sections": ["保什么", "不保什么", "怎么赔", "需要注意"], "header": "条款通俗解读", "footer": "以正式条款为准", "mark_ai": True},
                "actuarial": {"name": "精算报告", "sections": ["定价概述", "数据与方法", "费率结果", "可解释性", "情景模拟", "A/B 实验", "结论"], "header": "精算定价说明书", "footer": "内部资料", "mark_ai": True},
                "uw_opinion": {"name": "核保意见书", "sections": ["投保概况", "风险评分", "欺诈核查", "决策建议", "附加条件"], "header": "核保意见书", "footer": "", "mark_ai": True},
                "risk_mgmt": {"name": "风险管理报告", "sections": ["概要", "指标趋势", "预警与处置", "事故", "生命周期变化", "风险管理建议", "合作事项"], "header": "风险管理报告", "footer": "", "mark_ai": True},
                "risk_notice": {"name": "风险提示函", "sections": ["风险事项", "可能影响", "建议措施", "联系方式"], "header": "风险提示函", "footer": "", "mark_ai": True},
                "settlement": {"name": "理算书", "sections": ["案件概况", "责任认定", "损失核定", "理算过程", "赔付建议"], "header": "理赔理算书", "footer": "", "mark_ai": True},
                "denial": {"name": "拒赔通知书", "sections": ["案件概况", "拒赔依据", "条款原文", "救济途径"], "header": "拒赔通知书", "footer": "", "mark_ai": True},
                "policy": {"name": "电子保单", "sections": ["保单信息", "投保人与被保险人", "承保险种与保额", "特别约定", "保险期间", "保费"], "header": "科技保险电子保单", "footer": "本保单经电子签章，与纸质保单具有同等效力", "mark_ai": False},
                "endorsement": {"name": "批单", "sections": ["批改事项", "变更前后", "保费调整", "生效日期"], "header": "批单", "footer": "", "mark_ai": False},
                "invoice": {"name": "保费发票", "sections": ["购买方", "项目", "金额与税额"], "header": "增值税电子普通发票（保费）", "footer": "", "mark_ai": False},
            }
        },
    },
    "security": {
        "category": "系统配置",
        "title": "安全设置",
        "page": "P-1020",
        "description": "登录策略、会话、地址白名单、导出审批、临时授权时长与异常访问告警。",
        "schema": [
            num("password_min_length", "密码最小长度", min=6, max=32, integer=True),
            flag("password_require_mixed", "密码须同时包含字母与数字"),
            num("lockout_threshold", "连续登录失败锁定次数", min=3, max=20, integer=True),
            num("lockout_minutes", "锁定时长（分钟）", min=1, integer=True),
            num("session_ttl_hours", "会话有效期（小时）", min=1, max=72, integer=True),
            flag("ip_whitelist_enabled", "启用地址白名单"),
            tags("ip_whitelist", "允许登录的地址前缀"),
            flag("export_approval", "数据导出需审批"),
            num("temp_grant_max_hours", "临时授权最长时长（小时）", min=1, max=24),
            num("l2_view_alert", "10 分钟内查看 L2 原文超过此次数告警", min=1, integer=True),
        ],
        "default": {"password_min_length": 8, "password_require_mixed": True, "lockout_threshold": 5, "lockout_minutes": 30, "session_ttl_hours": 12, "ip_whitelist_enabled": False, "ip_whitelist": ["127.0.0.1", "10.", "192.168."], "export_approval": True, "temp_grant_max_hours": 4, "l2_view_alert": 20},
    },
    "quota": {
        "category": "模型与路由",
        "title": "额度与成本",
        "page": "P-1007",
        "description": "按用户、部门、智能体设置的每日云端费用与 token 额度；超额策略为排队、降级到私域或拒绝。",
        "schema": [
            num("user_daily_cost", "用户每日云端费用额度（元）", min=0),
            num("user_daily_tokens", "用户每日云端 token 额度", min=0, integer=True),
            mapping("department_daily_cost", "部门每日费用额度（元）", num("", "", min=0)),
            mapping("agent_daily_cost", "智能体每日费用额度（元）", num("", "", min=0), key_options=[("AG-RA", "风险评估"), ("AG-CD", "条款设计"), ("AG-AP", "精算定价"), ("AG-UW", "承保风控"), ("AG-RW", "风险预警"), ("AG-CL", "定损理赔")]),
            select("over_limit", "超额策略", [("degrade", "降级到私域模型"), ("queue", "排队到次日"), ("reject", "拒绝发起新运行")]),
            pct("alert_ratio", "用量达到额度比例时提醒"),
            num("gpu_hour_cost", "私域 GPU 折算成本（元/小时）", min=0),
        ],
        "default": {"user_daily_cost": 50.0, "user_daily_tokens": 3_000_000, "department_daily_cost": {}, "agent_daily_cost": {"AG-RA": 120.0, "AG-CD": 80.0, "AG-AP": 80.0, "AG-UW": 60.0, "AG-RW": 60.0, "AG-CL": 80.0}, "over_limit": "degrade", "alert_ratio": 0.8, "gpu_hour_cost": 18.0},
    },
    "kb.ingest": {
        "category": "知识",
        "title": "知识入库与质检",
        "page": "P-0703",
        "description": "入库抽检比例与发布规则。",
        "schema": [pct("sample_ratio", "抽检比例"), num("min_samples", "每批最少抽检数", min=1, integer=True), pct("pass_ratio", "抽检合格率达到时允许发布")],
        "default": {"sample_ratio": 0.3, "min_samples": 1, "pass_ratio": 0.8},
    },
    "agents.tools": {
        "category": "智能体",
        "title": "工具上下线",
        "page": "P-0808",
        "description": "下线的工具在调用前被守卫拒绝，智能体按替代工具或降级处理。",
        "schema": [tags("disabled_tools", "已下线工具")],
        "default": {"disabled_tools": []},
    },
}

FLOOR_KINDS = {"id_card", "phone", "landline", "bank_card", "email", "person", "tech_secret"}


# ---------------------------------------------------------------- 校验


def _validate_uw(v: dict) -> None:
    t = v["thresholds"]
    if not (t["conditional_total"] < t["manual_total"] < t["reject_total"] <= 100):
        raise ConfigError("决策阈值需满足：附条件 < 转人工 < 拒保 ≤ 100")
    if not (0 < t["manual_fraud"] < t["reject_fraud"] <= 1):
        raise ConfigError("欺诈概率阈值需满足：0 < 转人工 < 拒保 ≤ 1")
    levels = [a["level"] for a in v["authority"]]
    if levels != sorted(levels):
        raise ConfigError("核保权限分级需按审批等级从低到高排列")
    for c in v["condition_templates"]:
        if not c.get("text"):
            raise ConfigError("附加条件表述不能为空")
        if abs(float(c.get("loading", 0))) > 0.5:
            raise ConfigError("单个附加条件的费率调整不能超过 ±50%")


def _validate_rw(v: dict) -> None:
    for line, items in v["lines"].items():
        if line not in LINES:
            raise ConfigError(f"未知险种：{line}")
        for it in items:
            if it.get("enabled") and len(it.get("methods") or []) < 1:
                raise ConfigError(f"{LINES[line]['name']}·{it.get('name')} 至少需要一种检测方法")
    sla = v["sla_hours"]
    if not (sla["red"] <= sla["orange"] <= sla["yellow"] <= sla["blue"]):
        raise ConfigError("处置时限需满足：红色 ≤ 橙色 ≤ 黄色 ≤ 蓝色")


def _validate_compliance(v: dict) -> None:
    if not v["forbidden_words"]:
        raise ConfigError("禁用表述不能为空")
    b = v["rate_bands"]
    if not (0 < b["warm"] <= b["cold"] <= 0.6):
        raise ConfigError("费率浮动区间需满足：0 < 热启动 ≤ 冷启动 ≤ 60%")


def _validate_privacy(v: dict) -> None:
    for kind in FLOOR_KINDS:
        k = v["kinds"].get(kind)
        if k is None or int(k["level"]) < 3 or not k.get("enabled", True):
            raise ConfigError(f"{(k or {}).get('label', kind)} 属于个人信息或核心技术秘密，级别不得低于 L3 且不可停用")
        if k.get("method") not in ("token", "drop"):
            raise ConfigError(f"{k.get('label', kind)} 只能使用占位符替换或删除方式脱敏")
    for r in v["custom_rules"]:
        try:
            re.compile(r["pattern"])
        except re.error as exc:
            raise ConfigError(f"自定义规则「{r.get('name')}」的正则表达式无效：{exc}") from exc


def _validate_egress(v: dict) -> None:
    if not v.get("outbound_scan", True):
        raise ConfigError("出域二次扫描为强制要求，不可关闭")


def _validate_flows(v: dict) -> None:
    for code, f in v["flows"].items():
        if not f.get("stages"):
            raise ConfigError(f"审批流 {f.get('name', code)} 至少需要一个节点")
        keys = [s["key"] for s in f["stages"]] + [s["key"] for s in f.get("conditional_stages", [])]
        if len(keys) != len(set(keys)):
            raise ConfigError(f"审批流 {f.get('name', code)} 的节点编码重复")
    ca = v["claim_authority"]
    if not ca["level1_max"] < ca["level2_max"]:
        raise ConfigError("理赔一级权限上限需小于二级权限上限")


def _validate_security(v: dict) -> None:
    if v["temp_grant_max_hours"] > 24:
        raise ConfigError("临时授权最长不超过 24 小时")
    if v["ip_whitelist_enabled"] and not v["ip_whitelist"]:
        raise ConfigError("启用地址白名单时至少需要一个地址前缀")


def _validate_quota(v: dict) -> None:
    if v["over_limit"] not in ("degrade", "queue", "reject"):
        raise ConfigError("超额策略无效")


VALIDATORS: dict[str, Callable[[dict], None]] = {
    "uw.rules": _validate_uw,
    "rw.templates": _validate_rw,
    "compliance.rules": _validate_compliance,
    "privacy.rules": _validate_privacy,
    "privacy.egress": _validate_egress,
    "approval.flows": _validate_flows,
    "security": _validate_security,
    "quota": _validate_quota,
}


# ---------------------------------------------------------------- 读写


def _merge(default: Any, value: Any) -> Any:
    """以默认值为骨架合并已保存的值：新增的配置字段自动带默认值，已保存的字段保持不变。"""
    if isinstance(default, dict) and isinstance(value, dict):
        out = {k: copy.deepcopy(v) for k, v in default.items()}
        for k, v in value.items():
            out[k] = _merge(default.get(k), v) if k in default else copy.deepcopy(v)
        return out
    return copy.deepcopy(value) if value is not None else copy.deepcopy(default)


class ConfigCenter:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, dict]] = {}
        self._lock = threading.Lock()

    def load_all(self) -> None:
        from ..models import PlatformSetting

        with session_scope() as s:
            rows = {r.key: r for r in s.query(PlatformSetting).all()}
            for key, spec in SPECS.items():
                if key not in rows:
                    s.add(PlatformSetting(key=key, category=spec["category"], value=copy.deepcopy(spec["default"]), version=1, history=[], updated_by="系统初始化"))
            s.flush()
            loaded = {r.key: (r.version, _merge(SPECS[r.key]["default"], r.value)) for r in s.query(PlatformSetting).all() if r.key in SPECS}
        with self._lock:
            self._cache.update(loaded)

    def get(self, key: str) -> dict:
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return copy.deepcopy(hit[1])
        from ..db import _local

        if getattr(_local, "depth", 0):
            return copy.deepcopy(SPECS[key]["default"])
        self.load_all()
        with self._lock:
            hit = self._cache.get(key)
        return copy.deepcopy(hit[1] if hit else SPECS[key]["default"])

    def version(self, key: str) -> int:
        with self._lock:
            return self._cache.get(key, (1, {}))[0]

    def describe(self, key: str) -> dict:
        from ..models import PlatformSetting

        spec = SPECS[key]
        with session_scope() as s:
            row = s.get(PlatformSetting, key)
            history = list(row.history or []) if row else []
            meta = {"version": row.version if row else 1, "updated_by": row.updated_by if row else None, "updated_at": row.updated_at.isoformat() + "Z" if row else None}
        return {"key": key, "category": spec["category"], "title": spec["title"], "page": spec["page"], "description": spec["description"], "schema": spec["schema"], "value": self.get(key), "default": spec["default"], **meta, "history": [{k: h.get(k) for k in ("version", "updated_by", "updated_at", "reason", "value")} for h in reversed(history)]}

    def list(self) -> list[dict]:
        return [{"key": k, "category": v["category"], "title": v["title"], "page": v["page"], "description": v["description"], "version": self.version(k)} for k, v in SPECS.items()]

    def update(self, key: str, value: dict, *, user: str, reason: str) -> dict:
        from ..models import PlatformSetting

        if key not in SPECS:
            raise ConfigError("配置项不存在")
        if not reason.strip():
            raise ConfigError("请填写变更原因")
        merged = _merge(SPECS[key]["default"], value)
        validator = VALIDATORS.get(key)
        if validator:
            try:
                validator(merged)
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ConfigError):
                    raise
                raise ConfigError(f"配置结构不完整：{exc}") from exc
        with session_scope() as s:
            row = s.get(PlatformSetting, key)
            if row is None:
                row = PlatformSetting(key=key, category=SPECS[key]["category"], value={}, version=0, history=[])
                s.add(row)
            history = list(row.history or [])
            if not history and row.version:
                history.append({"version": row.version, "value": row.value, "updated_by": row.updated_by, "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None, "reason": "初始版本"})
            row.value = merged
            row.version = (row.version or 0) + 1
            row.updated_by = user
            row.updated_at = utcnow()
            history.append({"version": row.version, "value": merged, "updated_by": user, "updated_at": row.updated_at.isoformat() + "Z", "reason": reason})
            row.history = history[-30:]
            version = row.version
        with self._lock:
            self._cache[key] = (version, merged)
        return {"version": version, "value": merged}

    def rollback(self, key: str, version: int, *, user: str, reason: str) -> dict:
        from ..models import PlatformSetting

        with session_scope() as s:
            row = s.get(PlatformSetting, key)
            target = next((h for h in (row.history or []) if h.get("version") == version), None) if row else None
            if target is None:
                raise ConfigError(f"找不到版本 V{version}")
            value = target["value"]
        return self.update(key, value, user=user, reason=f"回滚到 V{version}：{reason}")


config_center = ConfigCenter()


def cfg(key: str) -> dict:
    return config_center.get(key)


_ = INCIDENT_TYPES
