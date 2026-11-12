"""演示私有材料：尽调报告与访谈纪要（L2），其中包含个人信息与技术秘密（L3），用于验证私域处理与出域脱敏。"""

from __future__ import annotations

import random

from ..textutil import format_amount

_ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_CHECK = "10X98765432"

CUSTOM_NOTES: dict[str, list[str]] = {
    "星衡智能装备股份有限公司": [
        "谐波减速器主要采购自日本供应商，第一大供应商采购占比58%，交期由十二周延长至二十周，存在断供导致中试线停滞的可能。",
        "多轴疲劳寿命测试台为唯一一台，承担全部关节寿命验证任务，属于关键设备集中度较高的情况。",
        "公司已收到外资企业专利侵权起诉，涉及碰撞检测功能，法务评估败诉概率中等。",
        "力控关节模组目前处于工程样机测试，计划在中试线完成工艺放大，良率目标为92%。",
    ],
    "锐驰精密机器人科技有限公司": [
        "五轴精密磨齿机维保依赖德国厂商，最近一次计划维保因备件交期延长推迟了两个月，主轴振动值有上升趋势。",
        "近一年核心技术人员离职4人，均为工艺研发骨干，部分加入竞争对手，公司与其签订了竞业限制协议。",
        "RV减速器寿命验证里程碑延期，原计划2026年3月完成8000小时验证，目前进度滞后约两个月。",
        "公司经营现金流为负，现有现金储备可支撑约七个月研发投入，正在推进B轮融资。",
    ],
    "擎川机器人技术有限公司": [
        "动力电池包第一大供应商采购占比48%，已开发第二供应商。",
        "复合机器人已在半导体工厂批量部署，近两年未发生安全事故。",
    ],
    "康衡医疗器械有限公司": [
        "注册临床试验计划入组180例，目前入组112例，出现一例与器械相关的严重不良事件，受试者已康复。",
        "绝对值编码器依赖美国单一供应商，第一大供应商采购占比64%，存在出口管制风险。",
        "近一年核心技术人员离职5人，其中包括两名控制算法工程师。",
        "公司尚未盈利，现金储备可支撑约十六个月，若注册审批延迟将面临融资压力。",
    ],
    "瑞声微创医疗科技有限公司": [
        "聚乳酸原料采购自瑞士单一供应商，采购占比83%。",
        "2025年12月对一个批次输送系统主动召回，涉及产品约1200套，召回费用约380万元。",
    ],
    "碳源新材料科技股份有限公司": [
        "长沙中试基地高温真空烧结炉在2026年2月出现过热引发小型火情，热电偶老化是可能原因，已更换。",
        "化学气相渗透沉积炉为唯一一台，承担全部首批次产品制备，设备集中度高。",
        "核心工艺的烧结温度曲线为1650℃保温4小时后梯度降温，配方中碳化硅颗粒关键配比为37%，属于公司核心技术秘密。",
        "前员工涉嫌带走工艺资料引发商业秘密纠纷，案件尚在审理。",
        "首批次产品装车试验中出现一例刹车盘边缘崩缺，原因正在分析，可能导致客户更换退货。",
    ],
    "华岩先进材料有限公司": [
        "百吨级中试线建设进度滞后，工艺放大过程中批次一致性未达预期，中试良率仅为61%。",
        "惰性气氛手套箱产线与高能球磨机组均在2025年10月下旬集中购置，金额合计约1170万元。",
        "硫化锂原料单一供应商采购占比90%，硫化物材料遇水释放有毒气体，实验室危化品管理需加强。",
        "公司暂未缴纳社保，员工通过劳务派遣方式用工；新一轮融资尚未完成，现金储备可支撑约九个月。",
        "近一年核心技术人员离职3人。",
    ],
    "深瞳智算科技有限公司": [
        "2025年4月部分测试服务器遭遇勒索病毒攻击，入口为未启用多因素认证的远程登录账号，事后已整改。",
        "平台存储超过二十亿张客户产线图像及部分客户员工人脸信息，数据资产规模大。",
        "2026年3月平台批量误判导致客户停线约十小时，客户提出索赔，责任认定尚在协商。",
        "近一年核心技术人员离职6人，首席算法科学家团队有三名成员离职创业。",
        "渗透测试发现两个高危漏洞尚未修补，计划下季度完成。",
    ],
    "明澜数据科技有限公司": [
        "软件处理患者影像数据，涉及大量个人敏感信息，已完成个人信息保护影响评估。",
        "推理服务部署在单一托管机房，未建立异地容灾，机房故障可能导致服务中断。",
    ],
    "晶创半导体设备有限公司": [
        "射频电源第一大供应商为美国厂商，采购占比74%，出口管制收紧后交期延长至九个月以上，存在断供风险。",
        "首台刻蚀机在客户产线验证中，若验证失败，前期投入的工程样机与验证费用约1.2亿元难以回收。",
        "境外企业发起337调查申请，涉及刻蚀腔室结构专利，可能影响海外市场拓展。",
        "工艺验证洁净平台为唯一一台，价值约4800万元。",
    ],
    "芯测精密仪器科技有限公司": [
        "检测灵敏度里程碑延期，原计划2026年2月达到28纳米节点要求，目前仍差约15%。",
        "光学总工离职后部分研发任务暂停，核心技术人员离职5人。",
        "高数值孔径物镜依赖德国单一供应商，采购占比81%。",
    ],
    "拓微光电装备有限公司": [
        "准分子激光器为唯一一台，依赖德国厂商维保，采购占比92%。",
        "原理样机已完成实验室验证，工程样机设计尚未冻结，技术路线存在调整可能。",
        "公司处于天使轮阶段，现金储备可支撑约十一个月。",
    ],
}


def fake_id_number(rng: random.Random, region: str = "330206") -> str:
    year = rng.randint(1972, 1992)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    body = f"{region}{year}{month:02d}{day:02d}{rng.randint(100, 999)}"
    total = sum(int(d) * w for d, w in zip(body, _ID_WEIGHTS))
    return body + _ID_CHECK[total % 11]


def dd_report(ent: dict, seed: int) -> str:
    rng = random.Random(seed)
    f = ent["financials"]
    team = ent["team"]
    lines = [
        f"{ent['name']}尽职调查报告",
        "一、基本情况",
        f"{ent['name']}成立于{ent['founded_year']}年，注册地址为{ent['address']}，法定代表人{ent['legal_rep']}，联系电话{ent['phone']}。公司主营{ent['tech_field']}，员工{ent['employees']}人，参保人数{f['social_security_count']}人。",
        "二、财务状况",
        f"2025年营业收入{format_amount(f['revenue']).replace(' ', '')}，净利润{format_amount(f['net_profit']).replace(' ', '')}，净资产{format_amount(f['net_assets']).replace(' ', '')}，经营现金流{format_amount(f['operating_cash_flow']).replace(' ', '')}，现金储备{format_amount(f['cash_reserve']).replace(' ', '')}，月度固定支出约{format_amount(f['monthly_fixed_cost']).replace(' ', '')}，研发投入{format_amount(f['rd_expense']).replace(' ', '')}，研发投入强度{f['rd_expense'] / max(f['revenue'], 1) * 100:.1f}%。",
        "三、研发与产品",
    ]
    for p in ent["products"]:
        lines.append(f"{p['name']}：{p['description']}技术成熟度自评为TRL{p['trl']}。")
    for m in ent.get("milestones", []):
        lines.append(f"里程碑「{m['name']}」计划于{m['planned']}完成，当前状态：{m['status']}。")
    lines.append("四、核心团队")
    lines.append(f"核心研发团队{team['core_size']}人，近一年核心技术人员离职{team['left_last_year']}人。")
    for i, member in enumerate(team["core_roles"]):
        if i == 0:
            lines.append(f"{member['role']}{member['name']}（身份证号{fake_id_number(rng)}，手机{ent['phone'][:3]}{rng.randint(10000000, 99999999)}）负责整体技术路线。")
        else:
            lines.append(f"{member['role']}{member['name']}负责关键模块研发。")
    lines.append("五、关键设备")
    for eq in ent["equipment"]:
        lines.append(f"{eq['name']}（型号{eq['model']}，序列号{eq['serial']}），原值{format_amount(eq['value']).replace(' ', '')}，购置日期{eq['purchase_date']}，存放于{eq['location']}。")
    lines.append("六、供应链")
    for sp in ent["suppliers"]:
        lines.append(f"{sp['item']}由{sp['name']}供应，采购占比{sp['share'] * 100:.0f}%，供应商所在国家：{sp['country']}。")
    lines.append("七、信息安全")
    sec = ent["security"]
    lines.append(f"公司信息系统通过网络安全等级保护{sec['level']}级测评，数据资产规模档位为{sec['data_scale']}级，近三年发生安全事件{sec['incidents']}起。")
    lines.append("八、尽调发现的主要事项")
    lines.extend(CUSTOM_NOTES.get(ent["name"], []))
    return "\n".join(lines)


def interview_notes(ent: dict, seed: int) -> str:
    rng = random.Random(seed + 7)
    member = ent["team"]["core_roles"][0]
    notes = CUSTOM_NOTES.get(ent["name"], [])
    q_and_a = [
        f"访谈对象：{member['name']}（{member['role']}），联系电话{ent['phone'][:3]}{rng.randint(10000000, 99999999)}，访谈时间2026年3月。",
        "问：当前研发面临的最大不确定性是什么？",
        f"答：{notes[0] if notes else '主要是关键部件供应和人才稳定性。'}",
        "问：如果关键设备出现故障，对研发进度的影响有多大？",
        f"答：{'关键设备只有一台，一旦停机研发基本停滞，维修需要等海外备件。' if any('唯一一台' in n or '维保' in n for n in notes) else '有一定备份能力，影响可控。'}",
        "问：团队稳定性如何？",
        f"答：近一年核心技术人员离职{ent['team']['left_last_year']}人，{'对进度有影响。' if ent['team']['left_last_year'] >= 3 else '整体稳定。'}",
    ]
    if len(notes) > 2:
        q_and_a += ["问：还有哪些需要关注的风险？", f"答：{notes[2]}"]
    return "\n".join(q_and_a)
