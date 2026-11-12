"""平台领域知识的结构化部分（对应 K04 风险知识库、险种目录、因子字典、关键风险指标库、欺诈模式库、应急预案库）。"""

from __future__ import annotations

from .portfolio import CLAIM_MATERIALS, LINES, RISK_TAXONOMY

STAGES = {"rd": "研发", "transfer": "成果转化", "industrial": "产业化"}
SUB_PHASES = [
    {"code": "P1", "name": "立项", "stage": "rd", "trl": (1, 2), "signals": ["立项", "可行性研究", "概念验证", "预研"]},
    {"code": "P2", "name": "研发", "stage": "rd", "trl": (3, 4), "signals": ["实验室原型", "原理样机", "研发投入", "发明专利申请", "研发阶段"]},
    {"code": "P3", "name": "中试验证", "stage": "transfer", "trl": (5, 6), "signals": ["中试", "样机测试", "工程样机", "试产", "型式试验", "临床试验"]},
    {"code": "P4", "name": "转化示范", "stage": "transfer", "trl": (7, 7), "signals": ["首台套", "首批次", "示范应用", "技术许可", "技术转让", "注册证申报"]},
    {"code": "P5", "name": "量产", "stage": "industrial", "trl": (8, 8), "signals": ["量产", "批量交付", "生产线投产", "产能", "生产许可"]},
    {"code": "P6", "name": "市场推广", "stage": "industrial", "trl": (9, 9), "signals": ["市场份额", "营收增长", "渠道", "订单", "客户覆盖"]},
    {"code": "P7", "name": "迭代或退出", "stage": "industrial", "trl": (9, 9), "signals": ["新一代产品", "停产", "技术替代", "升级换代"]},
]

NEGATION_CUES = ["未发生", "没有发生", "未出现", "无重大", "尚未", "不存在", "未涉及", "暂无", "未曾"]
SEVERITY_CUES = {"重大": 1.0, "严重": 1.0, "全部": 0.6, "停产": 0.8, "起诉": 0.6, "召回": 0.8, "爆炸": 1.0, "火灾": 0.9, "勒索": 0.9, "宣告无效": 0.7, "亿元": 0.8}

INSURABILITY_LABEL = {"yes": "可保", "partial": "部分可保", "no": "不可保"}

LINE_SIMILARITY_WEIGHTS = {"subject": 0.25, "perils": 0.20, "loss_forms": 0.20, "stage": 0.15, "industry": 0.10, "trigger": 0.10}

# 因子字典（K04）
FACTOR_DICTIONARY: list[dict] = [
    {"key": "trl", "name": "技术成熟度等级", "source": "llm", "kind": "ordinal", "direction": -1, "lines": ["RD01", "RD02", "RD04", "RD05", "TR01"], "definition": "科技产品技术成熟度（1–9），从技术资料与风险报告中提取"},
    {"key": "rd_intensity", "name": "研发投入强度", "source": "structured", "kind": "numeric", "direction": 1, "lines": ["RD01", "RD04", "RD05", "RD06", "RD09"], "definition": "研发投入占营业收入比例"},
    {"key": "team_stability", "name": "核心团队稳定性指数", "source": "llm", "kind": "numeric", "direction": -1, "lines": ["RD01", "RD02", "RD05", "RD06"], "definition": "由尽调与舆情中核心人员离职、变动信息提取，0–1，越高越稳定"},
    {"key": "patent_quality", "name": "专利质量指数", "source": "structured", "kind": "numeric", "direction": 1, "lines": ["IP01", "IP03", "IP04", "IP06"], "definition": "基于引用、同族与权利要求数的综合指数"},
    {"key": "litigation_exposure", "name": "专利诉讼暴露度", "source": "llm", "kind": "ordinal", "direction": 1, "lines": ["IP03", "IP04", "IP06", "IP07"], "definition": "所在领域诉讼密度与企业涉诉记录（0–3）"},
    {"key": "supplier_concentration", "name": "单一供应商依赖度", "source": "llm", "kind": "numeric", "direction": 1, "lines": ["RD03", "RD09", "TR02"], "definition": "第一大供应商或合作方依赖程度"},
    {"key": "export_control_exposure", "name": "出口管制暴露度", "source": "llm", "kind": "ordinal", "direction": 1, "lines": ["RD03", "RD09", "IP07"], "definition": "关键技术、设备或客户受出口管制与实体清单影响的程度（0–3）"},
    {"key": "compliance_maturity", "name": "数据与隐私合规成熟度", "source": "llm", "kind": "numeric", "direction": -1, "lines": ["CY01"], "definition": "个人信息保护、数据分类分级与跨境评估完备程度，0–1，越高越成熟"},
    {"key": "equipment_age", "name": "关键设备平均年龄", "source": "structured", "kind": "numeric", "direction": 1, "lines": ["RD08", "RD11", "RD12"], "definition": "关键设备平均使用年限（年）"},
    {"key": "equipment_concentration", "name": "关键设备集中度", "source": "structured", "kind": "numeric", "direction": 1, "lines": ["RD08", "RD09", "RD11"], "definition": "最高价值单台设备占设备总值比例"},
    {"key": "security_level", "name": "网络安全等级保护级别", "source": "structured", "kind": "ordinal", "direction": -1, "lines": ["CY01", "AI01", "AI02"], "definition": "等保级别（1–4）"},
    {"key": "data_scale", "name": "数据资产规模档位", "source": "llm", "kind": "ordinal", "direction": 1, "lines": ["CY01"], "definition": "从业务描述推断的数据记录规模档位（1–5）"},
    {"key": "security_incidents", "name": "历史安全事件数", "source": "llm", "kind": "numeric", "direction": 1, "lines": ["CY01", "AI01"], "definition": "从舆情与申报中提取的近三年安全事件数量"},
    {"key": "negative_sentiment", "name": "负面舆情指数", "source": "llm", "kind": "numeric", "direction": 1, "lines": ["RD08", "CY01", "AI01", "TR03", "IP06"], "definition": "对新闻事件分类与情感打分得到的指数（0–1）"},
    {"key": "cash_months", "name": "现金可支撑月数", "source": "structured", "kind": "numeric", "direction": -1, "lines": ["RD01", "RD04", "RD05", "TR01"], "definition": "现金储备 ÷ 月固定支出"},
    {"key": "quality_cert", "name": "质量体系认证", "source": "structured", "kind": "categorical", "direction": -1, "lines": ["TR03", "TR04", "TR05", "TR06", "AI01"], "definition": "是否通过质量管理体系认证"},
    {"key": "industry_risk", "name": "行业风险等级", "source": "knowledge", "kind": "ordinal", "direction": 1, "lines": ["RD01", "RD08", "TR01", "TR03", "CY01", "AI01", "IP04"], "definition": "风险知识库给出的行业风险等级（1–5）"},
    {"key": "maintenance_score", "name": "设备维保规范度", "source": "llm", "kind": "numeric", "direction": -1, "lines": ["RD08", "RD11", "RD12"], "definition": "从维保记录与核保材料提取的维保执行率（0–1）"},
]

# 关键风险指标库（风险预警监测模板）
_EQUIPMENT_KRI = [
    {"key": "vibration_rms", "name": "设备振动有效值", "source": "iot", "unit": "mm/s", "methods": ["TP1", "DM2", "DM3"], "threshold": 7.1, "direction": "up", "liability": "设备物质损失"},
    {"key": "bearing_temp", "name": "轴承温度", "source": "iot", "unit": "℃", "methods": ["DM1", "DM2"], "threshold": 85, "direction": "up", "liability": "设备物质损失"},
    {"key": "motor_current", "name": "运行电流", "source": "iot", "unit": "A", "methods": ["DM2", "DM3"], "threshold": 42, "direction": "up", "liability": "设备物质损失"},
]
_RD_KRI = [
    {"key": "milestone_delay_days", "name": "里程碑延误天数", "source": "ops", "unit": "天", "methods": ["TP3", "DM1"], "threshold": 30, "direction": "up", "liability": "研发费用或中断费用"},
    {"key": "rd_spend_change", "name": "月度研发费用变化率", "source": "ops", "unit": "%", "methods": ["TP3", "DM3"], "threshold": -0.35, "direction": "down", "liability": "研发费用损失"},
]
_CYBER_KRI = [
    {"key": "login_failures", "name": "登录失败次数", "source": "log", "unit": "次/小时", "methods": ["TP2", "DM2"], "threshold": 120, "direction": "up", "liability": "网络安全损失"},
    {"key": "egress_mb", "name": "数据外传量", "source": "log", "unit": "MB/小时", "methods": ["TP2", "DM2", "DM3"], "threshold": 800, "direction": "up", "liability": "数据泄露"},
    {"key": "unpatched_critical", "name": "高危漏洞未修补数", "source": "log", "unit": "个", "methods": ["TP2", "DM1"], "threshold": 1, "direction": "up", "liability": "网络安全损失"},
    {"key": "cloud_availability", "name": "云服务可用率", "source": "ops", "unit": "%", "methods": ["DM1", "DM3"], "threshold": 0.995, "direction": "down", "liability": "云服务或算力中断"},
    {"key": "vuln_deps", "name": "高危开源依赖未修复数", "source": "log", "unit": "个", "methods": ["TP2", "DM1"], "threshold": 1, "direction": "up", "liability": "软件供应链漏洞"},
]
_SUPPLY_KRI = [
    {"key": "supplier_otif", "name": "关键供应商准时齐套率", "source": "ops", "unit": "%", "methods": ["DM1", "DM5"], "threshold": 0.85, "direction": "down", "liability": "供应链中断"},
    {"key": "critical_part_days", "name": "关键零部件安全库存天数", "source": "ops", "unit": "天", "methods": ["DM1", "DM3"], "threshold": 14, "direction": "down", "liability": "零部件短缺"},
]
_TEAM_KRI = [
    {"key": "core_turnover", "name": "核心研发人员年离职率", "source": "ops", "unit": "%", "methods": ["DM1", "DM3"], "threshold": 0.2, "direction": "up", "liability": "关键人员流失"},
]
KRI_LIBRARY: dict[str, list[dict]] = {
    "RD08": _EQUIPMENT_KRI,
    "RD11": _EQUIPMENT_KRI,
    "RD12": _EQUIPMENT_KRI,
    "RD09": _RD_KRI + _SUPPLY_KRI + [{"key": "vibration_rms", "name": "关键设备振动有效值", "source": "iot", "unit": "mm/s", "methods": ["TP1"], "threshold": 7.1, "direction": "up", "liability": "研发中断费用"}],
    "RD10": _RD_KRI,
    "RD01": _RD_KRI + _TEAM_KRI,
    "RD02": _RD_KRI + _TEAM_KRI,
    "RD04": _RD_KRI + _TEAM_KRI,
    "RD05": _RD_KRI + _TEAM_KRI,
    "RD06": _RD_KRI + _TEAM_KRI,
    "CY01": _CYBER_KRI,
    "AI01": [{"key": "complaints", "name": "产品事故与投诉量", "source": "ops", "unit": "件/日", "methods": ["TP5", "DM2"], "threshold": 8, "direction": "up", "liability": "人工智能产品责任"}],
    "AI02": [{"key": "complaints", "name": "控制系统事故信号", "source": "ops", "unit": "件/日", "methods": ["TP5", "DM2"], "threshold": 5, "direction": "up", "liability": "智能控制系统责任"}],
    "RB01": [{"key": "damage_events", "name": "机器人本体损失事件", "source": "ops", "unit": "件/日", "methods": ["TP1", "DM1"], "threshold": 2, "direction": "up", "liability": "机器人本体物质损失"}],
    "RB02": [{"key": "damage_events", "name": "机器人本体及网络损失事件", "source": "ops", "unit": "件/日", "methods": ["TP1", "TP2", "DM1"], "threshold": 2, "direction": "up", "liability": "机器人本体物质损失（含网络安全事故）"}],
    "LS03": [{"key": "ae_reports", "name": "受试者不良事件报告", "source": "ops", "unit": "件/日", "methods": ["TP5", "DM2"], "threshold": 3, "direction": "up", "liability": "临床试验受试者人身损害责任"}],
    "LS05": [{"key": "trial_stop", "name": "临床试验项目终止信号", "source": "ops", "unit": "件/日", "methods": ["TP3", "DM1"], "threshold": 1, "direction": "up", "liability": "药物临床试验费用损失"}],
    "TR03": [{"key": "complaints", "name": "首台套用户索赔", "source": "ops", "unit": "件/日", "methods": ["TP5", "DM2"], "threshold": 3, "direction": "up", "liability": "装备第三者责任"}],
    "TR04": [{"key": "complaints", "name": "修理更换退货请求", "source": "ops", "unit": "件/日", "methods": ["TP5", "DM2", "DM3"], "threshold": 3, "direction": "up", "liability": "装备质量保证"}],
    "RD03": [{"key": "pilot_yield", "name": "中试阶段达成率", "source": "ops", "unit": "%", "methods": ["DM1", "DM3"], "threshold": 0.7, "direction": "down", "liability": "中试费用损失"}] + _SUPPLY_KRI,
    "TR01": [{"key": "pilot_yield", "name": "成果转化节点达成率", "source": "ops", "unit": "%", "methods": ["DM1", "DM3"], "threshold": 0.7, "direction": "down", "liability": "转化费用损失"}],
    "TR02": _SUPPLY_KRI,
}

COMMON_EXTERNAL_MONITORS = [
    {"key": "news", "name": "负面舆情与重大事件", "source": "news", "methods": ["DM4"]},
    {"key": "court", "name": "诉讼与执行", "source": "court", "methods": ["DM4", "TP4"]},
    {"key": "supplier", "name": "关键供应商事件", "source": "supplier", "methods": ["DM5"]},
]

DETECTION_METHODS = {
    "DM1": "关键风险指标规则与偏离",
    "DM2": "时序异常检测",
    "DM3": "变点检测",
    "DM4": "事件抽取与舆情研判",
    "DM5": "风险传导图谱分析",
    "TP1": "设备健康检测",
    "TP2": "数据安全检测",
    "TP3": "研发进度偏离检测",
    "TP4": "知识产权状态检测",
    "TP5": "产品质量信号检测",
}

FRAUD_PATTERNS: dict[str, dict] = {
    "P01": {"name": "投保前突击购置或虚增标的", "desc": "设备购置日期集中在投保前 30 天内且金额占比高，或发票与铭牌信息不一致"},
    "P02": {"name": "超额投保", "desc": "保额显著高于账面价值或重置价值（超过 1.2 倍）"},
    "P03": {"name": "带病投保", "desc": "投保前已发生事故、已有纠纷或研发已中断的迹象"},
    "P04": {"name": "材料篡改或伪造", "desc": "文档元数据异常、图像篡改痕迹、证照信息无法核验"},
    "P05": {"name": "关联企业集中投保或轮换出险", "desc": "关系网络中多家关联企业短期内投保同类产品或轮换出险"},
    "P06": {"name": "共用联系信息", "desc": "同一联系人、电话、地址对应多个无关投保主体"},
    "P07": {"name": "空壳企业", "desc": "参保人数为零、无纳税记录或无实际经营场所"},
    "P08": {"name": "频繁变更", "desc": "短期内多次变更法定代表人、注册地址、经营范围"},
    "P09": {"name": "中介异常集中", "desc": "同一中介渠道的投保件出险率或可疑率显著高于平均"},
    "P10": {"name": "申报与公开信息矛盾", "desc": "申报的规模、技术成果、团队与公开信息明显不符"},
    "P11": {"name": "模板化材料", "desc": "材料文本或照片与其他投保主体高度相似"},
    "P12": {"name": "财务异常", "desc": "营收与纳税、社保规模不匹配，或审计意见为非标准意见"},
}

PLAYBOOKS: dict[str, dict] = {
    "data_breach": {
        "name": "数据泄露或勒索攻击",
        "lines": ["CY01"],
        "steps": [
            {"title": "隔离受影响系统，断开异常外联", "deadline_hours": 1, "owner": "客户安全团队"},
            {"title": "保全服务器日志、安全告警与系统镜像（只读复制）", "deadline_hours": 2, "owner": "客户安全团队"},
            {"title": "启动约定应急响应服务商进行取证", "deadline_hours": 4, "owner": "产品服务人员"},
            {"title": "评估是否涉及个人信息及影响范围，按规定时限向主管部门报告并通知受影响个人", "deadline_hours": 24, "owner": "客户法务"},
            {"title": "评估恢复方案与业务中断时长，记录恢复过程", "deadline_hours": 48, "owner": "客户技术团队"},
        ],
        "mitigation": [
            {"measure": "优先从离线备份恢复核心业务系统", "effect": "缩短业务中断时长约 40%–60%"},
            {"measure": "重置全部特权账号凭证并启用多因素认证", "effect": "阻断二次入侵"},
            {"measure": "对外发布统一口径的事件通报", "effect": "降低第三者索赔与舆情扩散"},
        ],
        "evidence": ["服务器日志与安全告警原始文件", "受影响系统镜像", "取证报告", "事件响应费用凭证", "业务中断期间的营收数据"],
        "providers": ["应急响应服务商", "数据恢复服务商", "律师事务所"],
    },
    "equipment_damage": {
        "name": "关键研发设备损坏",
        "lines": ["RD08", "RD09", "RD11"],
        "steps": [
            {"title": "立即断电隔离受损设备，防止二次损失", "deadline_hours": 1, "owner": "客户设备负责人"},
            {"title": "拍照录像保全现场，保留损坏部件，不擅自拆解", "deadline_hours": 2, "owner": "客户设备负责人"},
            {"title": "通知设备厂商或第三方检测机构到场检测", "deadline_hours": 24, "owner": "产品服务人员"},
            {"title": "评估替代设备租赁方案，安排研发任务转移", "deadline_hours": 48, "owner": "客户研发负责人"},
        ],
        "mitigation": [
            {"measure": "租赁同型号替代设备维持关键实验", "effect": "研发中断天数预计减少 50% 以上"},
            {"measure": "将可迁移的实验任务转至合作实验室", "effect": "降低中断期间持续费用"},
            {"measure": "对同批次设备开展预防性检查", "effect": "避免同类故障再次发生"},
        ],
        "evidence": ["设备事故前后物联网数据", "现场照片与视频", "厂商检测报告", "维修报价", "维保记录"],
        "providers": ["设备厂商服务中心", "第三方检测机构", "设备租赁服务商"],
    },
    "product_liability": {
        "name": "产品责任事故",
        "lines": ["AI01", "TR03", "TR04", "LS02"],
        "steps": [
            {"title": "暂停相关批次销售并封存样品", "deadline_hours": 4, "owner": "客户质量负责人"},
            {"title": "记录受害方信息与损害情况，保留沟通记录", "deadline_hours": 12, "owner": "客户服务团队"},
            {"title": "通知法务与保险人，未经同意不作赔偿承诺", "deadline_hours": 24, "owner": "客户法务"},
            {"title": "评估是否需要启动召回", "deadline_hours": 72, "owner": "客户质量负责人"},
        ],
        "mitigation": [
            {"measure": "第一时间提供医疗救助并留存凭证", "effect": "降低损害扩大与诉讼风险"},
            {"measure": "委托第三方对封存样品检测", "effect": "为责任认定提供依据"},
        ],
        "evidence": ["产品批次与销售记录", "封存样品与检测报告", "受害方损害证明"],
        "providers": ["检测鉴定机构", "律师事务所"],
    },
    "ip_infringement": {
        "name": "知识产权侵权",
        "lines": ["IP03", "IP04", "IP06", "IP09"],
        "steps": [
            {"title": "对侵权证据进行公证保全", "deadline_hours": 72, "owner": "客户法务"},
            {"title": "委托律师评估诉前禁令与证据保全申请", "deadline_hours": 120, "owner": "客户法务"},
            {"title": "测算损失计算依据（许可费、利润损失）", "deadline_hours": 240, "owner": "客户财务"},
        ],
        "mitigation": [{"measure": "申请诉前行为保全", "effect": "尽快制止侵权扩大"}],
        "evidence": ["公证书", "侵权产品购买凭证", "专利证书与法律状态"],
        "providers": ["知识产权律师事务所", "公证机构"],
    },
    "lab_fire": {
        "name": "实验室火灾或水浸",
        "lines": ["RD08", "RD09"],
        "steps": [
            {"title": "确认人员安全并配合消防处置", "deadline_hours": 1, "owner": "客户安全负责人"},
            {"title": "抢救关键样品与数据存储介质", "deadline_hours": 2, "owner": "客户研发负责人"},
            {"title": "保全现场并取得消防部门出具的证明", "deadline_hours": 72, "owner": "客户安全负责人"},
        ],
        "mitigation": [{"measure": "转移未受损设备至安全区域并做防潮处理", "effect": "避免损失扩大"}],
        "evidence": ["消防证明", "现场照片", "受损设备与样品清单"],
        "providers": ["专业清洗修复服务商", "第三方检测机构"],
    },
    "supply_disruption": {
        "name": "关键供应商或零部件中断",
        "lines": ["RD03", "RD09", "TR01"],
        "steps": [
            {"title": "确认断供范围、库存与在途物料，冻结受影响生产或中试计划", "deadline_hours": 4, "owner": "客户供应链负责人"},
            {"title": "通知保险人与关键客户，固定供应商停产、破产或管制文件", "deadline_hours": 24, "owner": "客户采购与法务"},
            {"title": "启动第二来源、替代件验证或外协转产评估", "deadline_hours": 72, "owner": "客户研发与采购"},
            {"title": "归集已发生中试或研发费用，评估项目是否继续", "deadline_hours": 120, "owner": "客户项目负责人"},
        ],
        "mitigation": [
            {"measure": "启用安全库存与替代供应商小批量验证", "effect": "缩短中断窗口，降低项目终止概率"},
            {"measure": "将可迁移工序转至合作工厂或实验室", "effect": "维持关键路径进度"},
        ],
        "evidence": ["供应商停产或破产证明", "采购合同与交期记录", "库存与在途清单", "替代方案评估纪要", "费用台账"],
        "providers": ["第三方检测机构", "律师事务所", "设备租赁服务商"],
    },
    "key_personnel": {
        "name": "核心研发人员事故或流失",
        "lines": ["RD01", "RD02", "RD04", "RD05"],
        "steps": [
            {"title": "确认人员状态与岗位覆盖，暂停依赖该人员的不可逆实验", "deadline_hours": 8, "owner": "客户研发负责人"},
            {"title": "保全健康证明、竞业协议、交接记录与项目节点材料", "deadline_hours": 48, "owner": "客户人事与法务"},
            {"title": "启动备份人员、外包或合作实验室方案，评估立项节点影响", "deadline_hours": 72, "owner": "客户研发负责人"},
        ],
        "mitigation": [
            {"measure": "关键知识双人备份与文档化交接", "effect": "降低单人依赖导致的中断天数"},
            {"measure": "临时引入外部专家或合作单位承接关键任务", "effect": "维持约定节点完成可能性"},
        ],
        "evidence": ["人员名册与岗位说明", "医疗或离职证明", "竞业与保密协议", "项目进度与费用台账"],
        "providers": ["律师事务所"],
    },
}

INDUSTRY_PROFILES: dict[str, dict] = {
    "工业机器人": {"risk_level": 3, "high_incidence": ["R02-2", "R08-1", "R04-1", "R01-3", "R13-2", "R12-2"], "trl_typical": 7},
    "医疗器械": {"risk_level": 4, "high_incidence": ["R09-4", "R09-3", "R09-1", "R14-1", "R12-1"], "trl_typical": 6},
    "新材料": {"risk_level": 3, "high_incidence": ["R01-7", "R04-3", "R04-4", "R13-1", "R02-3"], "trl_typical": 6},
    "人工智能软件": {"risk_level": 3, "high_incidence": ["R07-1", "R07-3", "R07-7", "R14-3", "R12-2"], "trl_typical": 8},
    "半导体设备": {"risk_level": 4, "high_incidence": ["R01-3", "R02-2", "R13-2", "R14-2", "R05-6"], "trl_typical": 7},
}


def subtype_index() -> dict[str, dict]:
    index = {}
    for code, rt in RISK_TAXONOMY.items():
        for sub_code, sub in rt["subtypes"].items():
            index[sub_code] = {**sub, "type_code": code, "type_name": rt["name"], "code": sub_code}
    return index


SUBTYPES = subtype_index()


def subtype_record(code: str | None, risk: dict | None = None) -> dict:
    """取分类体系中的子类型。体系中没有的编码视为新创建风险，返回可继续计算的占位定义。"""
    found = SUBTYPES.get(code or "")
    if found:
        return found
    risk = risk or {}
    type_name = str(risk.get("type_name") or "新创建风险")
    return {
        "name": str(risk.get("title") or type_name),
        "patterns": [],
        "stages": ["rd", "transfer", "industrial"],
        "insurable": "partial",
        "lines": list(risk.get("lines") or []),
        "consequence": "损失形态需在确认该新创建风险后核定",
        "type_code": "NX",
        "type_name": type_name,
        "novel": True,
    }


# 约定服务商目录（应急响应、检测鉴定、修复、租赁、法律）
SERVICE_PROVIDERS: list[dict] = [
    {"id": "SP01", "name": "安衡网络安全应急响应中心", "category": "应急响应服务商", "regions": ["全国"], "response_hours": 2, "rating": 4.8, "capabilities": ["入侵取证", "勒索处置", "日志分析"]},
    {"id": "SP02", "name": "数安信数据恢复实验室", "category": "数据恢复服务商", "regions": ["华东", "华南", "华北"], "response_hours": 6, "rating": 4.6, "capabilities": ["数据库恢复", "存储介质修复"]},
    {"id": "SP03", "name": "正衡律师事务所（科技与数据合规团队）", "category": "律师事务所", "regions": ["全国"], "response_hours": 24, "rating": 4.7, "capabilities": ["数据合规", "知识产权诉讼", "产品责任"]},
    {"id": "SP04", "name": "精测工业设备检测鉴定有限公司", "category": "第三方检测机构", "regions": ["华东", "华中"], "response_hours": 12, "rating": 4.7, "capabilities": ["设备故障鉴定", "振动分析", "失效分析"]},
    {"id": "SP05", "name": "国联精密设备原厂服务中心", "category": "设备厂商服务中心", "regions": ["全国"], "response_hours": 24, "rating": 4.5, "capabilities": ["原厂维修", "备件供应"]},
    {"id": "SP06", "name": "科租通研发设备租赁", "category": "设备租赁服务商", "regions": ["华东", "华南", "华北", "华中"], "response_hours": 48, "rating": 4.4, "capabilities": ["测试台租赁", "精密仪器租赁"]},
    {"id": "SP07", "name": "洁瑞灾后清洗修复服务", "category": "专业清洗修复服务商", "regions": ["华东", "华南"], "response_hours": 8, "rating": 4.5, "capabilities": ["水浸烟熏清洗", "电路板修复"]},
    {"id": "SP08", "name": "华检产品质量检测鉴定院", "category": "检测鉴定机构", "regions": ["全国"], "response_hours": 48, "rating": 4.6, "capabilities": ["产品缺陷鉴定", "批次抽检"]},
    {"id": "SP09", "name": "知衡知识产权律师事务所", "category": "知识产权律师事务所", "regions": ["全国"], "response_hours": 24, "rating": 4.8, "capabilities": ["专利诉讼", "诉前禁令"]},
    {"id": "SP10", "name": "信证公证处", "category": "公证机构", "regions": ["全国"], "response_hours": 24, "rating": 4.6, "capabilities": ["证据保全公证"]},
]

REGION_OF_PROVINCE = {"苏州": "华东", "宁波": "华东", "上海": "华东", "杭州": "华东", "合肥": "华东", "无锡": "华东", "深圳": "华南", "广州": "华南", "北京": "华北", "长沙": "华中", "武汉": "华中", "成都": "西南"}

# 事故类型识别（事故初判）：关键词 → 预案
INCIDENT_TYPES: dict[str, dict] = {
    "data_breach": {"keywords": ["泄露", "勒索", "入侵", "外传", "攻击", "加密", "数据安全", "网络敲诈", "开源漏洞"], "lines": ["CY01"], "liabilities": ["数据恢复", "网络敲诈", "利润损失"], "urgency": "紧急"},
    "equipment_damage": {"keywords": ["设备", "故障", "停机", "轴承", "损坏", "振动", "过热", "短路"], "lines": ["RD08", "RD09", "RD11"], "liabilities": ["设备物质损失", "研发中断费用"], "urgency": "紧急"},
    "lab_fire": {"keywords": ["火灾", "起火", "水浸", "淹水", "爆炸"], "lines": ["RD09", "RD10", "RD08"], "liabilities": ["研发中断费用", "设备物质损失"], "urgency": "紧急"},
    "product_liability": {"keywords": ["伤害", "召回", "产品责任", "受害", "投诉"], "lines": ["AI01", "TR03", "TR04", "LS02"], "liabilities": ["第三者责任", "质量保证"], "urgency": "较急"},
    "ip_infringement": {"keywords": ["侵权", "专利", "起诉", "无效", "商业秘密"], "lines": ["IP04", "IP06", "IP08", "IP09"], "liabilities": ["专利被侵权损失", "侵犯专利权责任", "商业秘密损失"], "urgency": "一般"},
    "supply_disruption": {"keywords": ["断供", "供应商破产", "零部件短缺", "出口管制", "交期延误"], "lines": ["RD03", "RD09", "TR01"], "liabilities": ["中试费用损失", "研发中断费用"], "urgency": "较急"},
    "key_personnel": {"keywords": ["核心研究人员", "核心研发人员", "竞业", "重大疾病", "骨干离职"], "lines": ["RD01", "RD02", "RD04", "RD05"], "liabilities": ["研发费用损失"], "urgency": "较急"},
}


def family_of(line: str) -> str:
    return str(LINES.get(line, {}).get("family") or "")


def lines_in_family(lines: list[str] | set[str], *families: str) -> list[str]:
    wanted = set(families)
    return [line for line in lines if family_of(line) in wanted]


def has_family(lines: list[str] | set[str], *families: str) -> bool:
    return bool(lines_in_family(lines, *families))

# 理赔特有欺诈模式
CLAIM_FRAUD_PATTERNS: dict[str, dict] = {
    "C01": {"name": "出险时间临近保单起止日", "desc": "出险时间距保单生效或到期不足 15 天"},
    "C02": {"name": "报损金额异常", "desc": "报损金额显著高于价格库与同类案例水平"},
    "C03": {"name": "重复索赔", "desc": "同一标的或同一损失在其他案件中已索赔"},
    "C04": {"name": "旧损冒充新损", "desc": "受损照片中的损伤特征在承保照片中已存在"},
    "C05": {"name": "事故前隐瞒设备恶化", "desc": "事故前设备健康指数已明显恶化且有预警，但未采取措施或未报告"},
    "C06": {"name": "关联主体轮换出险", "desc": "关系网络中的关联企业短期内相继出险"},
}

# 事故因果模板（K05）：节点、边与默认强度；参与方行为变量用于反事实干预
CAUSAL_TEMPLATES: dict[str, dict] = {
    "equipment_damage": {
        "name": "设备故障致研发中断",
        "nodes": [
            {"id": "maintenance", "label": "维保执行", "kind": "behavior", "party_role": "维保服务商"},
            {"id": "component_quality", "label": "部件质量", "kind": "behavior", "party_role": "设备供应商"},
            {"id": "operation", "label": "操作与负载", "kind": "behavior", "party_role": "被保险人"},
            {"id": "wear", "label": "部件磨损", "kind": "state", "metric": "vibration_rms"},
            {"id": "overheat", "label": "轴承过热", "kind": "state", "metric": "bearing_temp"},
            {"id": "failure", "label": "设备故障", "kind": "event"},
            {"id": "loss", "label": "损失", "kind": "outcome"},
        ],
        "edges": [
            {"from": "maintenance", "to": "wear", "strength": 0.6},
            {"from": "component_quality", "to": "wear", "strength": 0.4},
            {"from": "operation", "to": "overheat", "strength": 0.3},
            {"from": "wear", "to": "overheat", "strength": 0.5},
            {"from": "wear", "to": "failure", "strength": 0.6},
            {"from": "overheat", "to": "failure", "strength": 0.5},
            {"from": "failure", "to": "loss", "strength": 1.0},
        ],
        "peril": "机器损坏",
        "excluded_causes": ["自然磨损", "未按规定维护", "故意行为"],
    },
    "data_breach": {
        "name": "漏洞未修补致数据泄露",
        "nodes": [
            {"id": "patching", "label": "漏洞修补", "kind": "behavior", "party_role": "被保险人"},
            {"id": "cloud_config", "label": "云平台安全配置", "kind": "behavior", "party_role": "云服务商"},
            {"id": "attack", "label": "外部攻击", "kind": "behavior", "party_role": "外部攻击者"},
            {"id": "intrusion", "label": "入侵成功", "kind": "state", "metric": "login_failures"},
            {"id": "exfiltration", "label": "数据外传", "kind": "state", "metric": "egress_mb"},
            {"id": "breach", "label": "数据泄露事件", "kind": "event"},
            {"id": "loss", "label": "损失", "kind": "outcome"},
        ],
        "edges": [
            {"from": "attack", "to": "intrusion", "strength": 0.7},
            {"from": "patching", "to": "intrusion", "strength": 0.5},
            {"from": "cloud_config", "to": "intrusion", "strength": 0.3},
            {"from": "intrusion", "to": "exfiltration", "strength": 0.8},
            {"from": "exfiltration", "to": "breach", "strength": 0.9},
            {"from": "breach", "to": "loss", "strength": 1.0},
        ],
        "peril": "人为侵害",
        "excluded_causes": ["故意行为", "战争与网络战"],
    },
}

FAULT_DEGREES = {"故意": 1.0, "重大过失": 0.85, "一般过失": 0.6, "轻微过失": 0.35, "无过错": 0.0}
