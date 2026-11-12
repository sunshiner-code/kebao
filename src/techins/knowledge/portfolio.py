"""示例科技保险产品目录（2026）。

本目录用于演示与测试：险种、保险责任为示例条款，责任免除、释义、赔偿处理和费率基准
是按保险责任整理的配套内容，不代表任何保险公司的正式备案文本。地区条款责任一致的产品
合并为一个险种，适用地区写在 regions。综合险与分项分开保留。没有保险责任正文的产品
放在 DEFERRED_PRODUCTS，暂不入库。正式使用前请替换为你自己经合规审核的条款与费率数据。
"""

from __future__ import annotations

from typing import Any

DEFERRED_PRODUCTS: list[dict[str, str]] = [
    {"name": "知识产权职业责任保险", "reason": "文档只有标题，没有保险责任正文"},
]

_RD_FAIL = """在保险期间内，由于以下原因造成的被保险人的科技项目经政府主管部门或保险人认可的鉴定机构认定，无法按照科技项目立项合同或销售合同中的时间约定按时完成，保险人按照本保险合同的约定负责赔偿被保险人由于未在约定时间节点完成而损失的研发费用：
（一）被保险人破产、兼并重组或发生重大经营困难使研发工作无法持续的；
（二）科技项目研发过程中核心研究人员因死亡、伤残、重大疾病等原因使研发工作无法正常开展的；
（三）研发过程中所使用的关键（核心）设备故障或损失导致研发工作无法正常开展的；
（四）被保险人在研发过程中与第三方产生知识产权纠纷导致研发工作无法正常进行的；
（五）经政府主管部门认定的导致科技项目无法按时完成的其他原因。"""

_TRANSFER_COST = """在保险期间内，科技成果转化项目经政府主管部门或保险人认可的鉴定机构认定失败的，且被保险人承诺不以同一项目继续开展此科技成果转化的，被保险人在成果转化过程中的以下费用损失，保险人按照本保险合同的约定负责赔偿：
（一）人员人工费用：参与成果转化活动的被保险人研发人员以及外聘研发人员的相关劳务费用；
（二）直接投入费用：直接消耗的材料、燃料和动力费用；用于中间试验和产品试制的模具、工艺装备开发及制造费，不构成固定资产的样品、样机及一般测试手段购置费，试制产品的检验费；用于成果转化活动的仪器、设备的运行维护、调整、检验、检测、维修费用；成果转化活动中研发成果的论证、评审、验收、评估费用；
（三）其他保险人认可且在保险单中载明的与成果转化相关的费用。"""

_RD_LIABILITY = """在保险期间或本保险合同载明的追溯期内，由于被保险研发成果存在设计缺陷造成意外事故，导致第三者的人身伤亡或直接财产损失，在保险期间内首次由受害人向被保险人提出索赔申请的，视为保险事故发生。保险事故发生后，依照中华人民共和国法律（不包括港、澳、台地区法律）应由被保险人承担的经济赔偿责任，保险人按照本保险合同的约定负责赔偿。
被保险研发成果为本保险合同载明的、经验收合格的研发成果。被保险研发成果的设计缺陷必须经政府主管部门或保险人认可的鉴定机构认定，或由仲裁机构、法院指定的鉴定机构鉴定并提供合法有效的书面鉴定结果。
保险事故发生后，被保险人因保险事故而被提起仲裁或者诉讼的，对应由被保险人支付的仲裁或诉讼费用以及事先经保险人书面同意支付的其他必要的、合理的法律费用，保险人按照本保险合同的约定也负责赔偿。"""

_EQ = """在保险期间内，由于下列原因造成保险标的的直接物质损坏或灭失，保险人按照本保险合同的约定负责赔偿：
（一）设计、制造或安装错误，铸造和原材料缺陷；
（二）工人或技术人员的操作错误、缺乏经验、技术不善、疏忽、过失、不当行为；
（三）离心力引起的断裂；
（四）超负荷、超电压、碰线、电弧、漏电、短路、大气放电、感应电及其他电气原因。
前款原因造成的保险事故发生时，为抢救保险标的或防止灾害蔓延，采取必要的、合理的措施而造成保险标的的损失，保险人按照本保险合同的约定也负责赔偿。
保险事故发生后，被保险人为防止或减少保险标的的损失所支付的必要的、合理的费用，保险人按照本保险合同的约定也负责赔偿。"""

_INTERRUPT = """在保险期间内，在保险合同载明的地址内由于下列原因造成被保险人在保险单列明的研发项目中使用的关键研发设备损坏、灭失、丧失使用功能以及存储于其中的科研资料丢失或丧失，导致研发项目的研发工作中断，保险人按照本保险合同的约定负责赔偿被保险人为将研发状态恢复到损失发生时的研发状态所支付的合理的、必要的研发费用：
（一）火灾、爆炸；
（二）雷击、暴雨、洪水、暴风、龙卷风、冰雹、台风、飓风、暴雪、冰凌、突发性滑坡、崩塌、泥石流；
（三）飞行物体及其他空中运行物体坠落。
研发费用包括研发活动直接消耗的材料、燃料和动力费用，在职研发人员的工资，中间试验和产品试制的模具、工艺装备、检验费、样品样机购置费，研发成果的知识产权申请费、注册费、代理费，以及外包、合作研发费用。固定资产建造购置租赁及维护、无形资产购置摊销、图书资料、会议差旅、培训咨询等费用不属于本保险的研发费用。
事先经保险人书面同意的审计费用，保险人也负责赔偿。"""

_INTERRUPT_SZ = """在保险期间内，由于下列原因造成保险单明细表中列明的关键研发设备损毁、灭失或丧失使用功能以及存储于其中的科研资料丢失，导致研发项目的研发工作中断，保险人依照本保险合同的约定，负责赔偿被保险人能够恢复到损失发生前研发状态所需要的合理必要的研发费用：
（一）火灾、爆炸；
（二）雷电、暴雨、洪水、暴风、龙卷风、冰雹、台风、飓风、暴雪、冰凌；
（三）突发性滑坡、崩塌、泥石流；
（四）飞行物体及其他空中运行物体坠落。
研发项目包括在国家各级主管机构申请立项的研发项目，以及被保险人根据国家各级主管机构或相关政策要求自主进行的研发项目。研发费用范围与不属于研发费用的项目，与高新技术企业研发中断费用补偿保险的口径一致，但不单列审计费用。"""

_PATENT_FEE = """在保险期间内，被保险人或其委托的本保险合同中载明的专利代理机构向国家知识产权行政管理部门提交本保险合同中载明的单项专利申请，由于专利申请不符合《中华人民共和国专利法》的相关规定，造成专利申请被国家知识产权行政管理部门驳回的，对于被保险人首次专利申请实际发生的下列费用损失，保险人按照本保险合同的约定负责赔偿：
（一）专利申请官费；
（二）专利申请代理费。"""

_SECRET_LOSS = """在保险期间内，被保险人的商业秘密遭受他人侵犯并经政府监督检查部门查处、仲裁机构仲裁裁决或人民法院判决认定该侵犯被保险人商业秘密的行为成立，被保险人在保险期间内向侵权人提出损害赔偿请求的，对于被保险人因前述侵犯商业秘密行为所遭受的直接经济损失，保险人按照本保险合同的约定负责赔偿。"""

_SECRET_COST = """在保险期间或本保险合同约定的追溯期内，被保险人发现其商业秘密涉嫌被侵犯，为获取证据自行或委托第三人进行调查取证，并在保险期间内通过向政府主管监督部门举报、向公安机关报案、向法院起诉或向仲裁机构申请仲裁等救济途径维权，经生效裁判文书或书面处理结果认定构成侵犯商业秘密的，保险人对立案或受理前实际支付的调查费用，以及维权过程中的律师费用、诉讼费用、仲裁费用、保全费用、案件处理费用、执行费用和事先经保险人书面同意的其他必要合理费用，按照本保险合同约定负责赔偿。
调查费用包括电子证据取证、侵权咨询、专家、侵权物品购买、公证、交通、住宿伙食、司法鉴定和损失评估等费用。"""

_LEGAL_FEE = "保险事故发生后，被保险人因保险事故而被提起仲裁或者诉讼的，对应由被保险人支付的仲裁或诉讼费用以及事先经保险人书面同意支付的其他必要的、合理的法律费用，保险人按照本保险合同的约定也负责赔偿。"


def _lia(title: str, text: str, covers: list[str]) -> dict[str, Any]:
    return {"title": title, "text": text.strip(), "covers": covers}


def _p(**kwargs: Any) -> dict[str, Any]:
    return kwargs


PRODUCTS: list[dict[str, Any]] = [
    _p(
        code="RD01", name="科技研发转化综合保险", clause_title="科技研发转化综合保险（2026版）",
        category="科技研发", stage="rd", regions=["全国"], subject="费用损失",
        perils=["经营困难", "人员意外", "设备故障", "知识产权纠纷", "技术失败", "法律责任"],
        loss_forms=["费用损失", "第三者赔偿"], trigger="claims_made", industry="通用",
        benchmark_rate=0.036, loss_ratio=0.55, limit_basis="rd_expense", deductible_floor=10000, param_style="waiting_days",
        family="rd_cost", monitors=["ops"], experience="rd_cost", detect="TP3",
        source_note="综合险保留研发费用损失、成果转化费用损失和研发责任三个部分。分项产品另行建档。",
        liabilities=[
            _lia("科技项目研发费用损失", _RD_FAIL, ["R01-1", "R01-2", "R01-3", "R01-4", "R12-1"]),
            _lia("科技成果转化费用损失", _TRANSFER_COST, ["R03-1"]),
            _lia("科技项目研发责任", _RD_LIABILITY, ["R01-9"]),
        ],
        exclusions_extra=["研发技术路线本身未被鉴定为列明原因的失败", "被保险人主动终止项目", "投保前已经知道项目无法按期完成"],
        definitions=[("研发费用", "因未在约定时间节点完成而损失的、保险单载明范围内的研发费用"), ("设计缺陷", "经验收合格的研发成果中，经约定鉴定机构认定的设计缺陷"), ("首次索赔", "受害人在保险期间内第一次向被保险人提出索赔")],
    ),
    _p(
        code="RD02", name="概念验证与小试综合费用损失保险", clause_title="概念验证与小试综合费用损失保险（2026版）",
        category="科技研发", stage="rd", regions=["全国"], subject="费用损失",
        perils=["自然灾害", "突发公共卫生", "技术公开", "人员意外"], loss_forms=["费用损失"], trigger="indicator", industry="通用",
        benchmark_rate=0.045, loss_ratio=0.58, limit_basis="rd_expense", deductible_floor=10000, param_style="waiting_days",
        family="rd_cost", monitors=["ops"], detect="TP3",
        liabilities=[_lia("概念验证与小试费用损失", """在保险期间内，经投保人、保险人双方认可的鉴定机构或专家认定，由于以下原因造成保险项目小试样品未达到立项决议文件中设定的标准，导致项目终止无法结题，且被保险人承诺不继续开展此项目的，被保险人在该项目概念验证与小试阶段发生的、保险单中载明的费用的损失，保险人按照本保险合同约定负责赔偿：
（一）因自然灾害导致保险项目无法继续进行、阶段性研发成果严重破坏且无法恢复；
（二）发生突发公共卫生事件，致使保险项目无法继续进行；
（三）保险项目立项决议文件中计划实现的研发成果的核心技术指标，于项目正式获得立项批准之后，已在国内被公开披露，致使技术开发工作已无必要继续进行；
（四）核心研发人员死亡、伤病、违反竞业协议离职到竞争对手公司工作或者自己创业成为被保险人的竞争对手，致使保险项目无法继续进行的。""", ["R01-2", "R01-5", "R01-6", "R12-1", "R12-2"])],
        exclusions_extra=["被保险人继续开展同一项目", "立项前已经公开的技术", "未列入保险单的费用"],
        definitions=[("小试样品", "概念验证与小试阶段形成的、用于对照立项决议标准的样品"), ("核心技术指标", "立项决议文件中载明的核心技术指标")],
    ),
    _p(
        code="RD03", name="中试验证综合费用损失保险", clause_title="中试验证综合费用损失保险（2026版）",
        category="科技研发", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["自然灾害", "突发公共卫生", "政策调整", "合作方破产", "战争"], loss_forms=["费用损失"], trigger="indicator", industry="通用",
        benchmark_rate=0.042, loss_ratio=0.58, limit_basis="rd_expense", deductible_floor=20000, param_style="waiting_days",
        family="transfer", monitors=["ops"], detect="TP3",
        liabilities=[_lia("中试验证费用损失", """在保险期间内，对于被保险人已经通过小试、达到小批量生产水平的保险项目，在仿真或真实生产条件下进行中试验证的过程中，因下列原因造成中试验证活动终止，且被保险人承诺不继续开展此项目的，被保险人在该项目中试验证阶段发生的、保险单中载明的费用的损失，保险人按照本保险合同约定负责赔偿：
（一）因自然灾害导致保险项目无法继续进行、中试成果严重破坏且无法恢复的；
（二）发生突发公共卫生事件，导致保险项目无法继续进行；
（三）政府政策发生调整，致使保险项目产品不符合市场准入条件和有关规定；
（四）委托方或合作方破产，导致已无必要按照委托合作合同试制生产保险项目产品；
（五）因战争、武装冲突，导致保险项目产品不能向境外委托方或意向购买方合法转让。""", ["R01-6", "R01-7", "R13-1", "R14-1"])],
        exclusions_extra=["尚未通过小试的项目", "被保险人继续开展同一中试项目", "市场销量不足"],
        definitions=[("中试验证", "已通过小试并达到小批量生产水平的项目，在仿真或真实生产条件下的验证活动")],
    ),
    _p(
        code="RD04", name="研发费用损失保险（全国）", clause_title="科技研发转化综合保险（2026版）第一部分",
        category="科技研发", stage="rd", regions=["全国"], subject="费用损失",
        perils=["经营困难", "人员意外", "设备故障", "知识产权纠纷"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.03, loss_ratio=0.55, limit_basis="rd_expense", deductible_floor=10000, param_style="waiting_days",
        family="rd_cost", monitors=["ops"], detect="TP3",
        source_note="文档在本条目下粘贴了综合险全文。综合险已作为 RD01 保留三部分；本险种按产品名称只收录第一部分研发费用损失。",
        liabilities=[_lia("科技项目研发费用损失", _RD_FAIL, ["R01-1", "R01-2", "R01-3", "R01-4", "R12-1"])],
        exclusions_extra=["成果转化费用和研发成果设计缺陷致第三者损害不在本分项内", "被保险人主动终止项目"],
        definitions=[("研发费用", "因未在约定时间节点完成而损失的研发费用")],
    ),
    _p(
        code="RD05", name="陕西省科技项目研发费用损失保险", clause_title="陕西省科技项目研发费用损失保险（标准条款，2026版）",
        category="科技研发", stage="rd", regions=["陕西省"], subject="费用损失",
        perils=["经营困难", "人员意外", "设备故障", "知识产权纠纷", "技术失败"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.038, loss_ratio=0.58, limit_basis="rd_expense", deductible_floor=10000, param_style="waiting_days",
        family="rd_cost", monitors=["ops"], detect="TP3",
        liabilities=[_lia("研发失败费用", """在保险期间内，由于以下原因造成被保险人的科技项目无法按照立项书或销售合同中约定的时间按时完成而项目失败，保险人负责赔偿保险期间实际发生的研发费用：
（一）被保险人破产、倒闭、兼并重组或发生重大经营困难使研发工作无法持续的；
（二）核心研究人员因死亡、伤残（7级及以上）、重大疾病等原因使研发工作无法正常开展的；
（三）关键（核心）设备故障或损失导致研发工作无法正常开展的；
（四）与第三方产生知识产权纠纷导致研发工作无法正常开展的；
（五）研发生产的材料、设备、软件等成果未达到立项书或销售合同中约定的性能指标而项目失败；
（六）经国家主管部门认定的导致科技项目失败的其他原因。""", ["R01-1", "R01-2", "R01-3", "R01-4", "R01-5", "R12-1"]),
               _lia("研发延期超额费用", """在保险期间内，由于核心研究人员死亡、伤残（7级及以上）、重大疾病，关键设备故障或损失，知识产权纠纷，技术、工艺、操作不善，或国家主管部门认定的其他原因，造成科技项目无法按约定时间完成，被保险人选择延期继续研发的，可按约定延长保险期间。延长后研发成功的，保险人赔偿实际发生且超出立项书或销售合同约定的研发费用；延长后研发失败的，保险人赔偿保险期间实际发生的研发费用。""", ["R01-8", "R12-1"])],
        exclusions_extra=["未达到7级的伤残", "被保险人未选择延期时的超额费用"],
        definitions=[("研发失败", "无法按照立项书或销售合同约定时间完成且项目失败"), ("研发延期", "选择延期继续研发并延长保险期间")],
    ),
    _p(
        code="RD06", name="深圳市科技企业普惠型研发费用损失保险", clause_title="深圳市科技企业普惠型研发费用损失保险（2026版）",
        category="科技研发", stage="rd", regions=["深圳市"], subject="费用损失",
        perils=["经营困难", "人员意外", "设备故障", "知识产权纠纷"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.026, loss_ratio=0.52, limit_basis="rd_expense", deductible_floor=5000, param_style="waiting_days",
        family="rd_cost", monitors=["ops"], detect="TP3",
        source_note="普惠型产品的触发原因与全国研发费用损失分项接近，但产品名称、适用地区和费率基准不同，单独建档。",
        liabilities=[_lia("研发费用损失", _RD_FAIL, ["R01-1", "R01-2", "R01-3", "R01-4", "R12-1"])],
        exclusions_extra=["非深圳市科技企业普惠政策适用对象", "被保险人主动终止项目"],
        definitions=[("研发费用", "因未在约定时间节点完成而损失的研发费用")],
    ),
    _p(
        code="RD07", name="产品研发责任保险", clause_title="科技研发转化综合保险（2026版）第三部分",
        category="科技研发", stage="rd", regions=["全国"], subject="责任",
        perils=["法律责任", "技术失败"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="通用",
        benchmark_rate=0.008, loss_ratio=0.4, limit_basis="liability", deductible_floor=20000, param_style="standard",
        family="liability", monitors=[], detect="TP5",
        source_note="本险种对应综合险第三部分，只承保研发成果设计缺陷引起的第三者责任。",
        liabilities=[_lia("科技项目研发责任", _RD_LIABILITY, ["R01-9"])],
        exclusions_extra=["未经验收合格的研发成果", "被保险人故意造成的损害", "惩罚性赔偿"],
        definitions=[("设计缺陷", "经验收合格的研发成果中，经约定鉴定机构认定的设计缺陷"), ("意外事故", "不可预料、被保险人无法控制并造成人身伤亡或直接财产损失的突发性事件")],
    ),
    _p(
        code="RD08", name="关键研发设备保险", clause_title="高新技术企业关键研发设备保险（2025版）",
        category="科技研发", stage="rd", regions=["全国"], subject="财产",
        perils=["机器损坏", "电气事故", "操作错误"], loss_forms=["直接物质损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.004, loss_ratio=0.45, limit_basis="equipment", deductible_floor=10000, param_style="standard",
        family="equipment", monitors=["device"], experience="equipment", detect="TP1", bundle="rd_asset", bundle_name="关键研发设备与研发中断费用重组主险",
        source_note="文档中本产品与高新技术企业关键研发设备保险使用同一份条款。两者都保留，条款文本相同。",
        liabilities=[_lia("关键研发设备物质损失", _EQ, ["R02-1", "R02-2"])],
        exclusions_extra=["自然灾害造成的设备损失不在本险种列明原因内", "自然磨损、氧化、腐蚀", "保险责任列明原因以外的故障"],
        definitions=[("保险标的", "保险单列明的关键研发设备"), ("损失", "保险标的的直接物质损坏或灭失")],
    ),
    _p(
        code="RD09", name="研发中断研发费用补偿保险", clause_title="高新技术企业研发中断研发费用补偿保险（2026版）",
        category="科技研发", stage="rd", regions=["全国"], subject="费用损失",
        perils=["火灾", "爆炸", "自然灾害", "飞行物体坠落"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.018, loss_ratio=0.5, limit_basis="monthly_waiting", deductible_floor=10000, param_style="waiting_days",
        family="rd_cost", monitors=["device", "ops"], detect="TP3", bundle="rd_asset", bundle_name="关键研发设备与研发中断费用重组主险",
        attachable_to=["RD08", "RD11"],
        liabilities=[_lia("研发中断研发费用", _INTERRUPT, ["R02-3", "R01-3"])],
        exclusions_extra=["固定资产建造、购置、租赁和维护费用", "无形资产购置和摊销", "图书资料、会议、差旅、培训和咨询费用", "未经财政补贴书面批准的课题"],
        definitions=[("研发项目", "保险单列明的、向国家有关主管部门申请财政补贴并获书面批准的研究开发课题或项目"), ("研发中断", "关键研发设备或科研资料受损后，研发工作停止")],
    ),
    _p(
        code="RD10", name="深圳市科技企业普惠型研发中断费用保险", clause_title="深圳市科技企业普惠型研发中断费用保险（2026版）",
        category="科技研发", stage="rd", regions=["深圳市"], subject="费用损失",
        perils=["火灾", "爆炸", "自然灾害", "飞行物体坠落"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.016, loss_ratio=0.5, limit_basis="monthly_waiting", deductible_floor=5000, param_style="waiting_days",
        family="rd_cost", monitors=["device", "ops"], detect="TP3",
        source_note="与高新技术企业研发中断费用补偿保险接近，但项目范围包含按政策自主研发的项目，且不单列审计费用。",
        attachable_to=["RD06"],
        liabilities=[_lia("研发中断费用", _INTERRUPT_SZ, ["R02-3"])],
        exclusions_extra=["固定资产建造、购置、租赁和维护费用", "无形资产购置和摊销", "非列明关键研发设备受损"],
        definitions=[("研发项目", "国家各级主管机构立项的项目，或按相关政策要求自主进行的研发项目")],
    ),
    _p(
        code="RD11", name="高新技术企业关键研发设备保险", clause_title="高新技术企业关键研发设备保险（2025版）",
        category="科技研发", stage="rd", regions=["全国"], subject="财产",
        perils=["机器损坏", "电气事故", "操作错误"], loss_forms=["直接物质损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.0045, loss_ratio=0.45, limit_basis="equipment", deductible_floor=10000, param_style="standard",
        family="equipment", monitors=["device"], detect="TP1",
        source_note="与关键研发设备保险条款文本相同，因产品名称不同分别建档。基准费率略高，用于和关键研发设备保险区分优先级。",
        liabilities=[_lia("高新技术企业关键研发设备物质损失", _EQ, ["R02-1", "R02-2"])],
        exclusions_extra=["自然灾害造成的设备损失不在本险种列明原因内", "自然磨损、氧化、腐蚀"],
        definitions=[("保险标的", "保险单列明的高新技术企业关键研发设备")],
    ),
    _p(
        code="RD12", name="科研仪器开放共享损失补偿保险", clause_title="科研仪器开放共享损失补偿保险（2026版）",
        category="科技研发", stage="rd", regions=["全国"], subject="财产",
        perils=["机器损坏", "电气事故", "操作错误"], loss_forms=["直接物质损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.0055, loss_ratio=0.48, limit_basis="equipment", deductible_floor=10000, param_style="standard",
        family="equipment", monitors=["device"], detect="TP1",
        liabilities=[_lia("科研仪器物质损失", """在保险期间内，因下列原因造成保险标的的物质损坏或灭失，保险人按照本保险合同的约定负责赔偿：
（一）设计、制造或安装错误、铸造和原材料缺陷；
（二）被保险人的工作人员或被保险人许可授权使用的第三方工作人员操作错误、缺乏经验、技术不善、过失；
（三）离心力引起的断裂；
（四）超负荷、超电压、碰线、电弧、漏电、短路、大气放电、感应电及其他电气原因。
保险事故发生后，被保险人为防止或减少保险标的的损失所支付的必要的、合理的施救费用，保险人按照本保险合同的约定也负责赔偿。""", ["R02-1", "R02-2", "R02-4"])],
        exclusions_extra=["未经被保险人许可的第三方使用", "自然磨损"],
        definitions=[("开放共享", "被保险人许可授权第三方使用科研仪器"), ("施救费用", "为防止或减少保险标的损失所支付的必要、合理费用")],
    ),
    _p(
        code="TR01", name="科技成果转化费用损失保险", clause_title="科技研发转化综合保险（2026版）第二部分",
        category="成果转化", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["技术失败"], loss_forms=["费用损失"], trigger="indicator", industry="通用",
        benchmark_rate=0.032, loss_ratio=0.55, limit_basis="rd_expense", deductible_floor=20000, param_style="waiting_days",
        family="transfer", monitors=["ops"], detect="TP3",
        source_note="本险种对应综合险第二部分，只承保成果转化失败的费用损失。",
        liabilities=[_lia("科技成果转化费用损失", _TRANSFER_COST, ["R03-1"])],
        exclusions_extra=["被保险人以同一项目继续开展成果转化", "未列入保险单的费用", "市场销售利润"],
        definitions=[("科技成果转化项目", "保险单载明的、将已有科技成果应用于形成产品、工艺或服务的项目")],
    ),
    _p(
        code="TR02", name="科技成果先用后转履约保证保险", clause_title="科技成果先用后转履约保证保险（2026版）",
        category="成果转化", stage="transfer", regions=["广东省（不含深圳市）"], subject="责任",
        perils=["合同违约"], loss_forms=["第三者赔偿"], trigger="occurrence", industry="通用",
        benchmark_rate=0.025, loss_ratio=0.45, limit_basis="surety", deductible_floor=10000, param_style="standard",
        family="guarantee", monitors=[], detect="TP4",
        source_note="文档标明适用范围为广东省（不含深圳市）。投保人是成果被许可企业，被保险人是成果许可人。",
        liabilities=[_lia("先用后转履约保证", """在科技成果先用后转交易中，成果被许可企业为投保人，成果许可人即科技成果权利人、成果单位为被保险人。科技成果包括专利技术成果和非专利技术成果。先用后转包括先使用后付费和先使用后合作。
在保险期间内，投保人履行其与被保险人签订并在本保险合同中载明的《科技成果先用后转交易合同》过程中，发生以下任一情形的，视为保险事故发生：
（一）免费许可期限届满后，投保人在未办理完成许可协议续签或转让协议签约手续的情况下继续实施标的科技成果的；
（二）投保人作为意向买方参与有偿转让并在获得唯一买受人资格后，未在有偿转让公告确定的期限内与被保险人办理完成转让协议签约手续的。
被保险人向保险人提出索赔的，保险人按照本保险合同的约定负责赔偿。""", ["R03-2"])],
        exclusions_extra=["被保险人同意继续免费实施", "合同未在保险单中载明", "投保人与被保险人恶意串通"],
        definitions=[("先使用后付费", "免费使用一定期限后，再约定分期、延期、收益提成或里程碑支付并完成正式许可或转让"), ("先使用后合作", "免费使用一定期限后，再约定技术转让、许可、开发或作价投资"), ("唯一买受人资格", "有偿转让程序中取得的唯一买受人资格")],
    ),
    _p(
        code="TR08", name="首台（套）重大技术装备综合保险", clause_title="首台（套）重大技术装备保险（中央型，示范条款）（2022版）",
        category="成果转化", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["经营困难", "人员意外", "设备故障", "知识产权纠纷"], loss_forms=["费用损失"], trigger="occurrence", industry="装备",
        benchmark_rate=0.032, loss_ratio=0.55, limit_basis="rd_expense", deductible_floor=20000, param_style="waiting_days",
        family="rd_cost", monitors=["ops"], detect="TP3",
        source_note="保险责任按文档正文录入。正文条款标题为「首台（套）重大技术装备保险（中央型，示范条款）（2022版）」。",
        liabilities=[_lia("重大技术装备项目研发费用损失", _RD_FAIL, ["R01-1", "R01-2", "R01-3", "R01-4"])],
        exclusions_extra=["被保险人主动终止项目", "投保前已经知道项目无法按期完成"],
        definitions=[("研发费用", "因未在约定时间节点完成而损失的、保险单载明范围内的研发费用"), ("科技项目", "保险单载明的首台（套）重大技术装备相关科技项目")],
    ),
    _p(
        code="TR03", name="首台套重大技术装备责任保险", clause_title="首台（套）重大技术装备责任保险（中央型，示范条款）",
        category="成果转化", stage="transfer", regions=["全国"], subject="责任",
        perils=["质量缺陷", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="装备",
        benchmark_rate=0.012, loss_ratio=0.4, limit_basis="liability", deductible_floor=50000, param_style="standard",
        family="liability", monitors=[], experience="liability", detect="TP5",
        liabilities=[_lia("装备质量缺陷第三者责任", """在保险期间或保险单载明的追溯期内，被保险人制造销售的保险装备存在质量缺陷，导致其在被用户单位使用过程中发生意外事故，造成第三者人身伤亡或除保险装备以外的财产损失，由用户单位在保险期间内首次向被保险人书面提出损害赔偿请求的，依法应由被保险人承担的对第三者的经济赔偿责任，保险人按照本保险合同的约定负责赔偿。
""" + _LEGAL_FEE, ["R04-1"])],
        exclusions_extra=["保险装备本身的损失", "用户单位未在保险期间内首次书面索赔", "故意行为"],
        definitions=[("保险装备", "保险单载明的首台（套）重大技术装备"), ("质量缺陷", "造成意外事故的装备质量缺陷")],
    ),
    _p(
        code="TR04", name="首台套重大技术装备质量保证保险", clause_title="首台（套）重大技术装备质量保证保险（中央型，示范条款）",
        category="成果转化", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["质量缺陷"], loss_forms=["费用损失"], trigger="claims_made", industry="装备",
        benchmark_rate=0.015, loss_ratio=0.42, limit_basis="quality", deductible_floor=50000, param_style="standard",
        family="quality", monitors=[], detect="TP5",
        liabilities=[_lia("装备修理更换退货", """在保险期间或保险单载明的追溯期内，被保险人制造销售的保险装备存在质量缺陷，由于下列原因，导致用户单位在保险期间内首次向被保险人书面提出损害赔偿请求的，依法应由被保险人承担的修理、更换或退货责任，保险人按照本保险合同的约定负责赔偿：
（一）不具备保险装备及其组件、零部件应当具备的使用性能而事先未作说明的；
（二）不符合产品合同及说明约定的产品标准、技术参数的；
（三）不符合产品合同及说明约定的质量状况的。
由保险事故引起的必要、合理的鉴定费用、运输费用、交通费用、派出维修人员费用，以及仲裁或诉讼法律费用，保险人按照本保险合同的约定负责赔偿。""", ["R04-2"])],
        exclusions_extra=["事先已说明的性能限制", "用户单位使用不当", "第三者人身伤亡和装备以外财产损失不在本质量保证责任内"],
        attachable_to=["TR03"],
        definitions=[("修理、更换或退货责任", "依法应由被保险人承担的修理、更换或退货责任")],
    ),
    _p(
        code="TR05", name="重点新材料首批次应用综合保险", clause_title="首批次新材料责任保险（中央型，示范条款）",
        category="成果转化", stage="transfer", regions=["全国"], subject="责任",
        perils=["质量缺陷", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="新材料",
        benchmark_rate=0.014, loss_ratio=0.42, limit_basis="liability", deductible_floor=30000, param_style="standard",
        family="liability", monitors=[], detect="TP5",
        source_note="目录名称为综合保险，文档本节的条款标题和正文是首批次新材料责任保险，未包含质量保证部分。质量保证见首批次新材料质量保证保险。",
        liabilities=[_lia("新材料质量缺陷第三者责任", """在保险期间或保险单载明的追溯期内，被保险人制造销售的保险新材料存在质量缺陷，导致其在被用户单位使用过程中发生意外事故，造成第三者人身伤亡或除保险新材料以外的财产损失，由用户单位在保险期间内首次向被保险人书面提出损害赔偿请求的，依法应由被保险人承担的对第三者的经济赔偿责任，保险人按照本保险合同的约定负责赔偿。
""" + _LEGAL_FEE, ["R04-3"])],
        exclusions_extra=["保险新材料本身的更换退货责任不在本责任保险内", "故意行为"],
        definitions=[("保险新材料", "保险单载明的首批次新材料")],
    ),
    _p(
        code="TR06", name="首批次新材料质量保证保险", clause_title="首批次新材料质量保证保险（中央型，示范条款）",
        category="成果转化", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["质量缺陷"], loss_forms=["费用损失"], trigger="claims_made", industry="新材料",
        benchmark_rate=0.016, loss_ratio=0.42, limit_basis="quality", deductible_floor=30000, param_style="standard",
        family="quality", monitors=[], detect="TP5",
        liabilities=[_lia("新材料修理更换退货", """在保险期间或保险单载明的追溯期内，被保险人制造销售的保险新材料存在质量缺陷，由于下列原因，导致用户单位在保险期间内首次向被保险人书面提出损害赔偿请求的，依法应由被保险人承担的修理、更换或退货责任，保险人按照本保险合同的约定负责赔偿：
（一）不具备保险新材料应当具备的使用性能而事先未作说明的；
（二）不符合产品合同及说明约定的产品标准、技术参数的；
（三）不符合产品合同及说明约定的质量状况的。
鉴定、运输、交通、派出维修人员费用以及法律费用，保险人按照本保险合同的约定负责赔偿。""", ["R04-4"])],
        exclusions_extra=["事先已说明的性能限制", "第三者人身伤亡不在本质量保证责任内"],
        attachable_to=["TR05"],
        definitions=[("保险新材料", "保险单载明的首批次新材料")],
    ),
    _p(
        code="TR07", name="首版次软件综合保险", clause_title="软件首版次质量安全责任保险（2021版）",
        category="成果转化", stage="transfer", regions=["全国"], subject="责任",
        perils=["质量缺陷", "法律责任"], loss_forms=["第三者赔偿", "费用损失"], trigger="claims_made", industry="软件",
        benchmark_rate=0.018, loss_ratio=0.45, limit_basis="liability", deductible_floor=20000, param_style="standard",
        family="liability", monitors=[], detect="TP5",
        liabilities=[_lia("首版次软件质量责任与修复费用", """在保险期间或保险单载明的追溯期内，被保险人设计销售经被保险人或第三方检测机构测试通过的保险软件，在用户单位验收后因存在质量缺陷，导致用户单位在操作、使用过程中发生意外事故，造成用户单位的损失或责任，由用户单位在保险期间内首次向被保险人提出索赔的，依法应由被保险人承担的经济赔偿责任，保险人按照本保险合同的约定负责赔偿。
对保险软件进行修复所产生的费用，以及第三方软件检测机构对保险软件进行故障检测、功能检测等检测费用，保险人按照本保险合同约定负责赔偿。
""" + _LEGAL_FEE, ["R04-5"])],
        exclusions_extra=["未经测试通过的软件", "验收前已经存在并被用户书面确认接受的缺陷"],
        definitions=[("保险软件", "经被保险人或第三方检测机构测试通过并在保险单中载明的首版次软件")],
    ),
    _p(
        code="IP01", name="专利申请费用补偿保险", clause_title="专利申请费用补偿保险（2025版）",
        category="知识产权", stage="transfer", regions=["广东省", "浙江省（不含宁波市）", "宁波市"], subject="费用损失",
        perils=["专利申请驳回"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.22, loss_ratio=0.7, limit_basis="fee", deductible_floor=0, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        source_note="广东省、浙江省（不含宁波市）、宁波市三份条款的保险责任一致，合并为一个险种，适用地区以保险单载明为准。",
        liabilities=[_lia("专利申请官费与代理费", _PATENT_FEE, ["R05-1"])],
        exclusions_extra=["非首次专利申请", "撤回、视为撤回或主动放弃", "保险单未载明的专利"],
        definitions=[("首次专利申请", "被保险人就同一技术方案第一次提出的专利申请"), ("专利申请官费", "向国家知识产权行政管理部门缴纳的申请官费")],
    ),
    _p(
        code="IP02", name="宁波市PCT国际专利申请费用补偿保险", clause_title="宁波市PCT国际专利申请费用补偿保险（2025版）",
        category="知识产权", stage="transfer", regions=["宁波市"], subject="费用损失",
        perils=["国际专利未授权"], loss_forms=["费用损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.35, loss_ratio=0.7, limit_basis="fee", deductible_floor=0, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        liabilities=[_lia("PCT国际阶段与国家阶段费用", """在保险期间内，被保险人或其委托的专利代理机构根据PCT的规定，通过PCT途径向中国国家知识产权局提交本保险合同中载明的国际专利申请，由于该专利申请未被全部或部分申请国授予专利权的，对于被保险人首次PCT国际专利申请实际发生的下列费用损失，保险人按照本保险合同的约定负责赔偿：
（一）国际阶段费用；
（二）国家阶段费用。""", ["R05-2"])],
        exclusions_extra=["非首次PCT申请", "未通过PCT途径提交", "保险单未载明的申请国费用"],
        definitions=[("国际阶段费用", "PCT国际阶段实际发生的费用"), ("国家阶段费用", "进入国家阶段实际发生的费用")],
    ),
    _p(
        code="IP03", name="专利执行保险", clause_title="专利执行保险（2025版）",
        category="知识产权", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["专利被侵权"], loss_forms=["费用损失"], trigger="claims_made", industry="通用",
        benchmark_rate=0.04, loss_ratio=0.5, limit_basis="ip", deductible_floor=5000, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        liabilities=[_lia("专利维权调查与法律费用", """在保险期间或追溯期内，第三方未取得合法授权或许可而首次实施本保险单列明的专利，被保险人为获取证据在承保区域范围内进行调查，并在保险期间内向法院提起诉讼、向仲裁机构提出仲裁或向管理专利工作的部门提出处理请求且被立案或受理的，保险人对立案或受理前必要、合理的调查费、专家作证费用、公证费、交通费、住宿费、伙食补助费负责赔偿。
对应由被保险人支付的律师费、仲裁、诉讼费用或行政处理费以及事先经保险人书面同意的其他必要、合理费用，保险人按照本保险合同约定也负责赔偿。""", ["R05-3"])],
        exclusions_extra=["未被立案或受理的请求", "承保区域外的调查", "保险单未列明的专利"],
        definitions=[("调查费用", "立案或受理前为获取证据支出的调查费、专家作证费用、公证费和差旅费用"), ("法律费用", "律师费、仲裁诉讼费用或行政处理费以及经同意的其他必要合理费用")],
    ),
    _p(
        code="IP04", name="专利被侵权损失保险", clause_title="专利被侵权损失保险（2025版）",
        category="知识产权", stage="transfer", regions=["全国"], subject="费用损失",
        perils=["专利被侵权"], loss_forms=["费用损失"], trigger="claims_made", industry="通用",
        benchmark_rate=0.025, loss_ratio=0.5, limit_basis="ip", deductible_floor=20000, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        liabilities=[_lia("专利被侵权直接损失", """在保险期间或保险合同约定的追溯期内，第三方未取得合法授权或许可而首次实施本保险单列明的专利，被保险人在保险期间内就该侵权行为向法院提起诉讼，或向仲裁机构提出仲裁，或向管理专利工作的政府部门提出行政处理请求，且取得生效的法院判决、仲裁裁决、书面处理结果，或经保险人事先同意达成调解书或和解协议的，对于该侵权行为造成的被保险人的直接损失，保险人按照本保险合同约定负责赔偿。""", ["R05-4"])],
        exclusions_extra=["尚未取得生效裁判、处理结果或经同意的和解", "间接利润和商誉损失"],
        attachable_to=["IP03"],
        definitions=[("保险专利", "保险单列明的专利"), ("直接损失", "生效法律文书或经同意的和解确认的直接损失")],
    ),
    _p(
        code="IP05", name="深圳市科技企业普惠型专利被侵权损失保险", clause_title="深圳市科技企业普惠型专利被侵权损失保险（2026版）",
        category="知识产权", stage="transfer", regions=["深圳市"], subject="费用损失",
        perils=["专利被侵权"], loss_forms=["费用损失"], trigger="claims_made", industry="通用",
        benchmark_rate=0.03, loss_ratio=0.55, limit_basis="ip", deductible_floor=5000, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        source_note="触发不要求先取得生效裁判，与全国版专利被侵权损失保险不同，单独建档。",
        liabilities=[_lia("专利被侵权经济损失", """在保险期间或保险合同约定的追溯期内，第三方未经被保险人许可实施本保险单列明的专利，导致被保险人的经济损失，被保险人在保险期间内向保险人索赔的，保险人按照本保险合同约定负责赔偿。""", ["R05-4"])],
        exclusions_extra=["保险单未列明的专利", "被保险人许可的实施"],
        definitions=[("经济损失", "第三方未经许可实施保险专利导致的、保险单约定范围内的经济损失")],
    ),
    _p(
        code="IP06", name="侵犯专利权责任保险", clause_title="侵犯专利权责任保险（2026版）",
        category="知识产权", stage="industrial", regions=["全国"], subject="责任",
        perils=["过失侵权", "法律责任"], loss_forms=["第三者赔偿", "费用损失"], trigger="claims_made", industry="通用",
        benchmark_rate=0.015, loss_ratio=0.45, limit_basis="ip", deductible_floor=20000, param_style="standard",
        family="liability", monitors=[], detect="TP4",
        liabilities=[_lia("过失侵犯他人专利权", """在保险期间或保险单载明的追溯期内，被保险人在从事保险单载明产品的制造、使用、许诺销售、销售、进口过程中，因过失或疏忽侵犯其他专利权人依照中华人民共和国法律（不包括港、澳、台地区法律）取得的专利权，由该专利权人在保险期间内首次向被保险人提出侵犯专利权赔偿请求的，依法应由被保险人承担的经济赔偿责任，保险人按照本保险合同约定负责赔偿。
事先经保险人书面同意，被保险人提出专利无效宣告申请所支出的必要、合理费用，以及仲裁或诉讼法律费用，保险人按照本保险合同约定也负责赔偿。""", ["R05-5"])],
        exclusions_extra=["故意侵权", "保险单未载明的产品", "港、澳、台地区法律项下的专利权"],
        definitions=[("过失或疏忽", "非故意地实施侵犯专利权的行为")],
    ),
    _p(
        code="IP07", name="知识产权海外侵权责任保险", clause_title="知识产权海外侵权责任保险（2026版）",
        category="知识产权", stage="industrial", regions=["海外承保区域"], subject="责任",
        perils=["非故意侵权", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="通用",
        benchmark_rate=0.02, loss_ratio=0.5, limit_basis="ip", deductible_floor=50000, param_style="standard",
        family="liability", monitors=[], detect="TP4",
        liabilities=[_lia("海外知识产权侵权责任", """在保险期间或保险合同载明的追溯期内，被保险人或其下游企业实际或预期从事保险单载明产品的制造、使用、保管、进口、销售或许诺销售、持有、许可、分销或提供过程中，因非故意侵犯第三方合法的知识产权，由该第三方在保险期间内在承保区域内就该侵权行为首次向被保险人提出索赔请求的，依法应由被保险人承担的经济赔偿责任，由保险人按照本保险合同约定负责赔偿。
""" + _LEGAL_FEE, ["R05-6"])],
        exclusions_extra=["故意侵权", "承保区域外的索赔", "保险单未载明的产品"],
        definitions=[("承保区域", "保险单载明的海外区域"), ("下游企业", "保险合同约定范围内的下游企业")],
    ),
    _p(
        code="IP08", name="商业秘密被侵权损失保险", clause_title="商业秘密被侵权损失保险",
        category="知识产权", stage="industrial", regions=["全国", "宁波市", "上海市"], subject="费用损失",
        perils=["商业秘密被侵权"], loss_forms=["费用损失"], trigger="claims_made", industry="通用",
        benchmark_rate=0.02, loss_ratio=0.5, limit_basis="ip", deductible_floor=20000, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        source_note="通用2024版、宁波市2025版、上海市2024版的保险责任一致，合并为一个险种。版本和地区以保险单载明为准。",
        liabilities=[_lia("商业秘密被侵权直接经济损失", _SECRET_LOSS, ["R06-1"])],
        exclusions_extra=["未经查处、裁决或判决认定的行为", "间接损失和商誉损失"],
        definitions=[("商业秘密", "被保险人采取保密措施的技术信息或经营信息"), ("侵权行为", "经政府部门查处、仲裁裁决或法院判决认定成立的侵犯商业秘密行为")],
    ),
    _p(
        code="IP09", name="商业秘密被侵权维权费用补偿保险", clause_title="商业秘密被侵权维权费用补偿保险",
        category="知识产权", stage="industrial", regions=["全国", "上海市"], subject="费用损失",
        perils=["商业秘密被侵权"], loss_forms=["费用损失"], trigger="claims_made", industry="通用",
        benchmark_rate=0.03, loss_ratio=0.55, limit_basis="ip", deductible_floor=5000, param_style="standard",
        family="ip", monitors=[], detect="TP4",
        source_note="通用2024版与上海市2023版的保险责任一致，合并为一个险种。",
        attachable_to=["IP08"],
        liabilities=[_lia("商业秘密维权调查与法律费用", _SECRET_COST, ["R06-2"])],
        exclusions_extra=["未被认定构成侵犯商业秘密的维权", "立案或受理后新增的、不属于法律费用的开支"],
        definitions=[("救济途径维权", "举报并要求行政处罚、报案、起诉或申请仲裁"), ("调查费用", "立案或受理前为获取证据实际支付的调查、公证、鉴定和差旅等费用")],
    ),
    _p(
        code="CY01", name="网络安全保险", clause_title="网络安全损失保险（2025版）",
        category="网络安全", stage="industrial", regions=["全国"], subject="费用损失",
        perils=["恶意软件", "操作失误", "拒绝服务", "网络敲诈"], loss_forms=["费用损失", "收入损失"], trigger="occurrence", industry="通用",
        benchmark_rate=0.018, loss_ratio=0.6, limit_basis="cyber", deductible_floor=50000, param_style="waiting_hours",
        family="cyber", monitors=["logs"], experience="cyber", detect="TP2",
        liabilities=[
            _lia("数据恢复及其他费用", """在保险期间内，因计算机恶意行为、计算机恶意软件或人员操作失误造成被保险人计算机系统和电子介质中的数据损坏、丢失、被盗窃或泄露给未经授权的第三者，或计算机系统遭受拒绝服务攻击的，保险人对下列必要且合理的费用负责赔偿：数据恢复及重置费用，计算机系统恢复费用，为降低上述费用而发生的外部设备租赁、外包、加班和重启费用，事故调查费用，以及按照数据保护法律对第三者机密信息泄露履行通知义务的费用。""", ["R07-1", "R07-2", "R07-7", "R14-3"]),
            _lia("网络敲诈损失", """在保险期间内，因网络敲诈者的计算机恶意行为或其使用计算机恶意软件向被保险人发起网络敲诈，造成数据损坏、丢失、被盗或泄露，或系统遭受拒绝服务攻击的，保险人对事先书面同意的网络敲诈赎金、聘请专家处理网络敲诈的费用，以及为拘留及逮捕网络敲诈者向线索提供人员支付的赏金负责赔偿。""", ["R07-3"]),
            _lia("名誉损失补偿", """在保险期间内，因数据损坏、丢失、被盗、泄露或拒绝服务攻击造成媒体行业首次对被保险人进行不利报道，给被保险人造成负面顾客感知的，保险人对首次不利报道后连续14日内，为减轻品牌及声誉负面影响而采取的媒体危机管理或广告宣传活动所支付的必要且合理费用负责赔偿。""", ["R07-4"]),
            _lia("利润损失", """在保险期间内，因数据损坏、丢失、被盗、泄露或拒绝服务攻击造成被保险人业务活动受到干扰或中断的，保险人负责赔偿赔偿期间内的毛利润损失。毛利润损失按净利润损失、固定费用和额外费用计算，赔偿期间最长不超过最大赔偿期。净利润的保险金额不足时按比例赔偿。事先经要求发生的审计费用，保险人在保险金额内按实际数额赔偿。""", ["R07-5", "R07-6"]),
        ],
        exclusions_extra=["被保险人故意行为", "保险责任列明事故以外的系统中断", "其他部分已经赔偿的费用不在利润损失项下重复赔偿"],
        definitions=[("赔偿期间", "自业务受到干扰或中断之日起至计算机系统恢复至出险前状态之日止，最长不超过最大赔偿期"), ("毛利润损失", "净利润损失、持续支出的固定费用和为降低损失而支出的额外费用")],
    ),
    _p(
        code="AI01", name="人工智能产品责任保险（A款）", clause_title="人工智能产品责任保险A款（2026版）",
        category="机器人/人工智能", stage="industrial", regions=["全国"], subject="责任",
        perils=["设计缺陷", "运行故障", "网络攻击", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="人工智能",
        benchmark_rate=0.02, loss_ratio=0.45, limit_basis="liability", deductible_floor=30000, param_style="standard",
        family="liability", monitors=[], experience="liability", detect="TP5",
        source_note="",
        liabilities=[_lia("人工智能产品第三者责任", """在保险期间或保险单载明的追溯期内，保险产品在保险单载明的承保区域内使用过程中，因下列原因造成第三者人身伤亡或直接财产损失，由受害人或其他赔偿权利人在保险期间内首次向被保险人提出损害赔偿请求，依照中华人民共和国法律（不包括香港、澳门特别行政区和台湾地区法律）应由被保险人承担的经济赔偿责任，保险人按照本保险合同的约定负责赔偿：
（一）保险产品自身设计或运行缺陷故障（包括软件及硬件故障、软硬件不适配导致的系统异常）；
（二）保险产品的外部交互模块故障（包括通信网络信号缺失、突发断电、断网）；
（三）保险产品遭受黑客攻击。
""" + _LEGAL_FEE, ["R08-1", "R08-2", "R08-3"])],
        exclusions_extra=["承保区域外使用", "投保人或被保险人故意、重大过失或违法犯罪", "受害人故意或欺诈", "投保前已知道或可合理预见的产品缺陷", "精神损害、罚款罚金及惩罚性赔偿", "间接损失", "被保险人或其雇员人身伤亡及其所有或管理财产的损失"],
        definitions=[("保险产品", "经产品质量检验机构检验合格、并在保险单中载明的人工智能产品"), ("黑客攻击", "未经授权对保险产品实施的网络攻击")],
    ),
    _p(
        code="AI02", name="智能控制系统责任保险", clause_title="智能控制系统责任保险（2026版）",
        category="机器人/人工智能", stage="industrial", regions=["全国"], subject="责任",
        perils=["设计缺陷", "运行故障", "网络攻击", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="人工智能",
        benchmark_rate=0.022, loss_ratio=0.45, limit_basis="liability", deductible_floor=30000, param_style="standard",
        family="liability", monitors=["ops"], detect="TP5",
        source_note="",
        liabilities=[_lia("智能控制系统第三者责任", """在保险期间或本保险合同载明的追溯期内，在本保险合同载明的承保区域范围内，因下列原因（投保人可以选择投保，也可以同时投保，具体以保险单载明为准）导致搭载设备发生故障，进而引发意外事故造成使用或操作搭载设备的人员或其他第三者的人身伤亡或财产损失，由受害人或其代理人在保险期间内首次向被保险人提出损害赔偿请求，依照中华人民共和国法律（不包括港、澳、台地区法律）应由被保险人承担的经济赔偿责任，保险人按照本保险合同约定负责赔偿：
（一）智能控制系统自身功能设计或运行缺陷（包括软件及硬件故障、软硬件不适配导致的系统异常）；
（二）智能控制系统外部交互模块故障（包括但不限于定位系统被干扰、通信网络信号缺失、突发断电、断网）；
（三）智能控制系统遭受网络攻击。
""" + _LEGAL_FEE, ["R08-1", "R08-2", "R08-3"])],
        exclusions_extra=[
            "保险单未选择的承保原因",
            "未按安全操作说明进行的不规范操作",
            "智能控制系统未按产品说明书要求升级和维护",
            "搭载设备发生事故时智能控制系统功能未开启或已退出",
            "搭载设备改装或改变使用性质",
            "搭载设备上市销售前未经检验或经检验不合格仍允许销售",
            "智能控制系统本身的损失及收回、退换、修理或改进升级费用",
            "精神损害、罚款罚金及惩罚性赔偿",
            "间接损失",
            "被保险人或其雇员人身伤亡及其所有或管理财产的损失",
        ],
        definitions=[
            ("智能控制系统", "保险单载明的、用于控制搭载设备的系统"),
            ("搭载设备", "安装或使用该智能控制系统、并在保险单中载明的设备"),
            ("追溯期", "保险单载明的追溯期间；未载明的，以保险期间起始日为追溯日"),
            ("网络攻击", "未经授权对智能控制系统实施的侵入、干扰、控制或其他恶意行为"),
        ],
    ),
    _p(
        code="RB01", name="具身智能机器人本体损失保险（A款）", clause_title="具身智能机器人本体损失保险（A款）（2026版）",
        category="机器人/人工智能", stage="industrial", regions=["全国"], subject="财产",
        perils=["自然灾害", "火灾爆炸", "意外碰撞", "操作过失", "电气原因"], loss_forms=["直接物质损失", "费用损失"], trigger="occurrence", industry="机器人",
        benchmark_rate=0.012, loss_ratio=0.45, limit_basis="equipment", deductible_floor=10000, param_style="standard",
        family="equipment", monitors=["device", "ops"], detect="TP1",
        source_note="A款不承保网络安全事故造成的损失。",
        liabilities=[_lia("具身智能机器人本体物质损失（A款）", """在保险期间内，被保险人或被保险人授权的使用者停放或运行保险标的过程中，因下列原因导致保险标的损失，保险人按照本保险合同约定负责赔偿：
（一）自然灾害；
（二）火灾、爆炸；
（三）意外碰撞、倾覆、坠落；
（四）被保险人或使用者操作错误、过失；
（五）离心力引起的断裂；
（六）超负荷、超电压、碰线、电弧、漏电、短路、大气放电、感应电及其他电气原因。
保险事故发生后，被保险人为防止或减少保险标的的损失所支付的必要的、合理的施救费用，保险人按照本保险合同的约定也负责赔偿。""", ["R08-4"])],
        exclusions_extra=[
            "地震、海啸",
            "自然磨损、内在或潜在缺陷、自然损耗及渐变原因",
            "设计、制造或安装错误，铸造和原材料缺陷",
            "任何网络安全事故造成的损失和费用",
            "运输、安装、调试过程中的损坏",
            "数据本身的损失",
            "性能改进、优化、升级产生的额外费用",
            "使用者不具备产品说明书要求的操控资质",
            "保险标的被改装或未经许可更换部件",
        ],
        definitions=[
            ("具身智能机器人", "以物理实体为载体，与真实物理环境实时交互，具备感知、认知、决策和行动一体化能力，支持完成特定或通用复杂任务的智能体"),
            ("保险标的", "保险合同载明的、由被保险人所制造、拥有、管理或使用的具身智能机器人，包括机器人本体、配套设备及软件系统"),
            ("网络安全事故", "因恶意软件、黑客行为、拒绝服务攻击或非法使用访问等，导致机器人系统遭受网络攻击、侵入、干扰或非法使用并造成系统故障或数据损毁的事故"),
        ],
    ),
    _p(
        code="RB02", name="具身智能机器人本体损失保险（B款）", clause_title="具身智能机器人本体损失保险（B款）（2026版）",
        category="机器人/人工智能", stage="industrial", regions=["全国"], subject="财产",
        perils=["自然灾害", "火灾爆炸", "意外碰撞", "操作过失", "电气原因", "网络安全事故"], loss_forms=["直接物质损失", "费用损失"], trigger="occurrence", industry="机器人",
        benchmark_rate=0.015, loss_ratio=0.48, limit_basis="equipment", deductible_floor=10000, param_style="standard",
        family="equipment", monitors=["device", "ops", "logs"], detect="TP1",
        source_note="相对A款增加网络安全事故保险责任。",
        liabilities=[_lia("具身智能机器人本体物质损失（B款）", """在保险期间内，被保险人或被保险人授权的使用者停放或运行保险标的过程中，因下列原因导致保险标的损失，保险人按照本保险合同约定负责赔偿：
（一）自然灾害；
（二）火灾、爆炸；
（三）意外碰撞、倾覆、坠落；
（四）被保险人或使用者操作错误、过失；
（五）离心力引起的断裂；
（六）超负荷、超电压、碰线、电弧、漏电、短路、大气放电、感应电及其他电气原因；
（七）网络安全事故。
保险事故发生后，被保险人为防止或减少保险标的的损失所支付的必要的、合理的施救费用，保险人按照本保险合同的约定也负责赔偿。""", ["R08-4", "R08-3"])],
        exclusions_extra=[
            "地震、海啸",
            "自然磨损、内在或潜在缺陷、自然损耗及渐变原因",
            "设计、制造或安装错误，铸造和原材料缺陷",
            "运输、安装、调试过程中的损坏",
            "数据本身的损失",
            "性能改进、优化、升级产生的额外费用",
            "使用者不具备产品说明书要求的操控资质",
            "保险标的被改装或未经许可更换部件",
        ],
        definitions=[
            ("具身智能机器人", "以物理实体为载体，与真实物理环境实时交互，具备感知、认知、决策和行动一体化能力，支持完成特定或通用复杂任务的智能体"),
            ("保险标的", "保险合同载明的、由被保险人所制造、拥有、管理或使用的具身智能机器人，包括机器人本体、配套设备及软件系统"),
            ("网络安全事故", "因恶意软件、黑客行为、拒绝服务攻击或非法使用访问等，导致机器人系统遭受网络攻击、侵入、干扰或非法使用并造成系统故障或数据损毁的事故"),
        ],
    ),
    _p(
        code="LS03", name="临床试验责任保险", clause_title="生物医药人体临床试验责任保险（2026版）",
        category="生命科学", stage="rd", regions=["全国"], subject="责任",
        perils=["不良事件", "严重不良事件", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="生命科学",
        benchmark_rate=0.035, loss_ratio=0.5, limit_basis="liability", deductible_floor=20000, param_style="standard",
        family="life", monitors=["ops"], detect="TP5",
        source_note="原误录的费用损失责任已迁至 LS05。",
        liabilities=[_lia("临床试验受试者人身损害责任", """在保险单载明的追溯日之后至保险期间终止前，被保险人在中华人民共和国境内开展经政府有关部门批准的且在保险单中载明的生物医药人体临床试验活动中，临床试验受试者因接触或暴露于临床试验产品出现不良事件或严重不良事件导致遭受人身损害，由临床试验受试者或其代理人、监护人在保险期间或索赔宽限期内首次向被保险人（含附加被保险人）提出索赔，依照中华人民共和国法律（不包括香港、澳门特别行政区和台湾地区法律）应由被保险人承担的经济赔偿责任，保险人根据本保险合同的约定负责赔偿。
""" + _LEGAL_FEE, ["R09-3"])],
        exclusions_extra=[
            "临床试验未经批准或批准后被撤销",
            "临床试验机构或研究者不具备相应资质",
            "故意或重大过失违反药物/医疗器械临床试验质量管理规范强制性规定",
            "与临床试验无关的诊疗或其他行为",
            "临床试验产品未达预期效果",
            "因原患疾病或即使不参加试验仍会发生的人身损害或健康恶化",
            "精神损害赔偿",
            "财产损失与间接损失",
            "罚款、罚金及惩罚性赔偿",
        ],
        definitions=[
            ("临床试验产品", "试验用药物和试验用医疗器械"),
            ("临床试验受试者", "参加临床试验的自然人，以签署的知情同意书为参加证明"),
            ("不良事件", "临床试验相关规范性文件所述的不良事件"),
            ("严重不良事件", "临床试验相关规范性文件所述的严重不良事件"),
            ("索赔宽限期", "保险单载明的期限；未载明的，为保险期间终止后六十日"),
        ],
    ),
    _p(
        code="LS05", name="药物临床试验费用损失保险", clause_title="药物临床试验费用损失保险（2023版）",
        category="生命科学", stage="rd", regions=["全国"], subject="费用损失",
        perils=["技术失败", "严重不良反应"], loss_forms=["费用损失"], trigger="indicator", industry="生命科学",
        benchmark_rate=0.04, loss_ratio=0.55, limit_basis="rd_expense", deductible_floor=20000, param_style="standard",
        family="life", monitors=["ops"], detect="TP3",
        source_note="由原 LS03 误录内容迁出。",
        liabilities=[_lia("药物临床试验费用损失", """在保险期间内，由于临床试验过程中出现以下任一或多种情形导致被保险人的临床试验项目终止，对于被保险人于临床试验项目终止前所实际支出的属于保险单列明范围的费用，保险人按照本保险合同的约定负责赔偿：
（一）有证据证明试验药物未达临床试验方案中写明的主要试验目标；
（二）临床试验中出现大范围、非预期的严重不良反应，药品毒性、副作用等超预期或不能有效保证受试者安全。""", ["R09-4"])],
        exclusions_extra=["临床试验项目未终止", "保险单未载明的费用", "被保险人继续开展同一临床试验项目"],
        definitions=[("临床试验项目", "保险单载明的药物临床试验项目"), ("主要试验目标", "临床试验方案中写明的主要试验目标"), ("严重不良反应", "大范围、非预期，且药品毒性或副作用超预期、不能有效保证受试者安全的不良反应")],
    ),
    _p(
        code="IP10", name="知识产权侵权责任保险", clause_title="知识产权侵权责任保险（2026版）",
        category="知识产权", stage="industrial", regions=["全国"], subject="责任",
        perils=["疏忽侵权", "非故意侵权", "法律责任"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="通用",
        benchmark_rate=0.018, loss_ratio=0.45, limit_basis="liability", deductible_floor=30000, param_style="standard",
        family="liability", monitors=[], detect="TP5",
        source_note="原暂缓入库产品，现已整理入库。",
        liabilities=[_lia("知识产权侵权赔偿责任", """在保险期间及保险单载明的追溯期内，被保险人制造、使用、保管、进口、销售或许诺销售、持有、许可、分销或提供保险单载明的产品时，在保险单载明的承保区域内，因疏忽或者非故意地侵犯保险单载明类型的知识产权，直接导致第三方权利人在保险期间内首次对被保险人提出索赔的，依法应由被保险人承担的经济赔偿责任，保险人按照本保险合同的约定负责赔偿。保险人仅对被保险人侵犯保险单载明类型的知识产权导致的经济赔偿责任承担保险责任。
""" + _LEGAL_FEE, ["R05-7"])],
        exclusions_extra=["故意侵犯知识产权", "保险单未载明类型的知识产权", "惩罚性赔偿", "合同责任中超出法定责任的部分"],
        definitions=[("承保知识产权类型", "投保人与保险人协商确定并在保险单中载明的知识产权类型"), ("追溯期", "保险单载明的追溯期间")],
    ),
    _p(
        code="LS01", name="生命科学产品完工责任保险", clause_title="生命科学产品完工责任保险（2022版）",
        category="生命科学", stage="industrial", regions=["全国"], subject="责任",
        perils=["法律责任", "产品完工"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="生命科学",
        benchmark_rate=0.015, loss_ratio=0.45, limit_basis="liability", deductible_floor=50000, param_style="standard",
        family="life", monitors=[], detect="TP5",
        liabilities=[_lia("生命科学产品完工责任", """被保险人在保险单载明的承保区域内，依法从事保险单载明的生产经营活动时，发生意外事故导致产品完工责任承保范围内的第三者个人或机构人身伤害或财产损失，依法应由被保险人承担的经济赔偿责任，保险人按照本保险合同的约定负责赔偿。
保险人仅在下列条件均满足时承担责任：伤害或损失发生在保险期间或追溯期内；第三者首次提出损害赔偿请求的时间在保险期间或延长索赔通知期内；被保险人按通知条款首次通知保险人，且保险人在期间终止后30日内收到并记录书面通知。
人体临床试验仅在试验物质是生命科学产品、首次人体接触发生在保险期间或追溯日之后，且被保险人取得必要批准、未故意违反试验规则、并经保险人批单同意时，可以申请纳入承保范围。""", ["R09-1", "R09-3"])],
        exclusions_extra=["未取得必要批准的试验", "故意或恶意违反试验方案、法律或监管要求", "未经批单纳入的人体临床试验"],
        definitions=[("完工责任", "保险单约定的产品完工责任"), ("延长索赔通知期", "保险单或条款列明的延长索赔通知期")],
    ),
    _p(
        code="LS02", name="疫苗安全责任保险", clause_title="疫苗安全责任保险（2020版）",
        category="生命科学", stage="industrial", regions=["全国"], subject="责任",
        perils=["质量问题", "菌株管理", "接种异常反应"], loss_forms=["第三者赔偿"], trigger="claims_made", industry="生命科学",
        benchmark_rate=0.012, loss_ratio=0.4, limit_basis="liability", deductible_floor=20000, param_style="standard",
        family="life", monitors=[], detect="TP5",
        liabilities=[_lia("疫苗全生命周期人身伤亡责任", """在本保险合同列明的保险期间或追溯期内，被保险人在载明区域范围内依法从事疫苗全生命周期经营管理活动过程中，因疫苗质量问题、菌株管理不当或接种异常反应造成受种者、受试者或其他第三者的人身伤亡，由受害人或其代理人在保险期间内首次向被保险人提出损害赔偿请求的，依法应由被保险人承担的经济赔偿责任，保险人按照本保险合同约定负责赔偿。
""" + _LEGAL_FEE, ["R09-2"])],
        exclusions_extra=["非疫苗质量、菌株管理或接种异常反应引起的损害", "被保险人违法生产经营"],
        definitions=[("接种异常反应", "合格疫苗在规范接种过程中发生的、造成受种者损害的异常反应"), ("疫苗全生命周期", "保险单约定的研发、生产、流通和接种相关经营管理活动")],
    ),
    _p(
        code="AV01", name="卫星发射前保险", clause_title="卫星发射前保险条款（2018版）",
        category="航空航天/低空经济", stage="industrial", regions=["全国"], subject="财产",
        perils=["自然灾害", "意外事故", "人为误操作"], loss_forms=["直接物质损失", "费用损失"], trigger="occurrence", industry="航天",
        benchmark_rate=0.008, loss_ratio=0.4, limit_basis="aviation", deductible_floor=100000, param_style="standard",
        family="aviation", monitors=[], detect="TP1",
        liabilities=[_lia("发射前物理损失与额外测试费用", """在保险标的进行发射准备有关的安装、组合、测试、加注、包装、拆装、装载、卸载、仓储，以及在发射基地厂房间运输等活动中，自风险起期至风险终止期间，由于自然灾害、意外事故或人为误操作导致保险标的物质实体损失、损坏或损毁的，保险人就全部损失赔付保险金额，或就部分损失补偿修复或置换受损部件的必要、合理费用，包括材料成本、维修工时和重新投入使用前的测试费用，不包括利润附加。累计赔偿责任以保险金额为限。
保险事故发生后为避免或减少损失而发生的抢救、保护费用，以保险单载明的金额为限。保险标的发生物理损失后，为确保卫星适飞性而额外发生的必要、合理测试费用也负责补偿，不包括为查找事故原因而进行试验的费用。""", ["R10-1"])],
        exclusions_extra=["利润附加", "为查找事故原因而进行的试验费用", "风险起期前或风险终止后的损失"],
        definitions=[("风险起期至风险终止", "保险单声明载明的发射前风险期间"), ("部分损失", "修复或置换受损部件的必要合理费用")],
    ),
    _p(
        code="AV02", name="卫星在轨寿命保险", clause_title="卫星在轨寿命保险条款（2018版）",
        category="航空航天/低空经济", stage="industrial", regions=["全国"], subject="财产",
        perils=["卫星失效"], loss_forms=["直接物质损失"], trigger="occurrence", industry="航天",
        benchmark_rate=0.045, loss_ratio=0.55, limit_basis="aviation", deductible_floor=0, param_style="standard",
        family="aviation", monitors=[], detect="TP1",
        liabilities=[_lia("在轨全损、推定全损与部分损失", """在风险起期至风险终止期间，卫星发生全部损失的，按照保险金额赔偿；发生推定全损的，按照保险金额赔偿；发生部分损失的，按照部分损失金额赔偿。赔付总额不超过保险金额。
若通过增加地面设备、改变操作程序、改进软件或其他修正措施可以在合理时间内消除或弥补损失，保险人可选择按上述方式赔偿，或承担修正措施的必要费用。修正措施未达到效果的，保险人仍承担相应赔款，并承担修正措施成本。
损失、损坏或失效须由风险期间内的遥测数据、遥测数据缺失或其他地面措施证明。多种运行模式并存时，理赔计算采用使赔偿金额最小化的模式。先前已获赔偿的，后续赔偿应调整以避免重复。卫星性能在风险终止后继续退化且被证明由致损事件引起的，计算损失金额时考虑该退化。""", ["R10-2"])],
        exclusions_extra=["不能被遥测数据或地面措施证明的损失", "风险期间以外的失效", "重复赔偿"],
        definitions=[("全部损失", "卫星全部丧失预定功能"), ("推定全损", "按保险单约定构成推定全损的状态"), ("部分损失", "卫星部分丧失性能规范要求的功能")],
    ),
    _p(
        code="AV03", name="卫星发射及初始运行保险", clause_title="卫星发射及初始运行保险条款（2018版）",
        category="航空航天/低空经济", stage="industrial", regions=["全国"], subject="财产",
        perils=["发射失败", "初始运行失效"], loss_forms=["直接物质损失", "费用损失"], trigger="occurrence", industry="航天",
        benchmark_rate=0.06, loss_ratio=0.55, limit_basis="aviation", deductible_floor=0, param_style="standard",
        family="aviation", monitors=[], detect="TP1",
        liabilities=[_lia("发射及初始运行损失与发射终止费用", """在风险起期至风险终止期间，卫星的损失、损坏或失效导致全部损失的，按照保险金额赔偿；导致部分损失的，按照部分损失金额赔偿。发生发射终止的，补偿被保险人为使卫星恢复到适飞状态以继续下一次发射而承担的直接、合理及必需费用。
多种运行模式并存时，理赔计算采用使赔偿金额最小化的模式。修正措施可以消除或弥补损失的，保险人可选择赔偿损失或承担修正措施费用。除修正措施未达效果而需另行承担成本的情形外，赔付总额不超过保险金额。
索赔须有风险期间内的遥测数据、遥测数据缺失或其他地面措施证明，被保险人已提交出险通知和损失证明，并在转发器不能达到可用指标前用尽可用备份、冗余和余量。""", ["R10-3"])],
        exclusions_extra=["未用尽备份、冗余和余量", "未提交出险通知和损失证明", "非为恢复适飞状态发生的费用"],
        definitions=[("发射终止", "点火终止等导致当次发射未完成的情形"), ("适飞状态", "卫星可以继续进行下一次发射的状态")],
    ),
    _p(
        code="AV04", name="航空器机身一切险及责任保险", clause_title="航空器机身一切险及责任保险（2024版）",
        category="航空航天/低空经济", stage="industrial", regions=["全国"], subject="财产",
        perils=["意外事故"], loss_forms=["直接物质损失", "第三者赔偿"], trigger="occurrence", industry="航空",
        benchmark_rate=0.015, loss_ratio=0.45, limit_basis="aviation", deductible_floor=100000, param_style="standard",
        family="aviation", monitors=[], detect="TP1",
        liabilities=[
            _lia("航空器机身损失", """在保险期间内，本保险合同分项列明的被保险航空器因非除外责任导致的意外损失或毁坏，包括航空器起飞后15天内没有任何消息的失踪，保险人有权选择货币赔付、重置或修理，赔偿金额不超过保险单明细表列明的保险金额，并扣除约定免赔额。
投保了飞行风险的，保险人在保险金额之外另行支付航空器损坏或迫降时为即时安全所发生的必要且合理的应急救助费用，但不超过该航空器保险金额的10%。""", ["R10-4"]),
            _lia("航空器第三者责任", """在保险期间内，被保险人对于由被保险航空器或从被保险航空器上坠落的任何人或物对第三者（不包括航空器上旅客）造成的意外人身伤亡和财产损失，依照中华人民共和国法律（不包括港、澳、台地区法律）有责任支付的补偿性赔款，由保险人予以赔偿，不包括惩罚性赔款。
第三者就承保损害提起的索赔诉讼中，经保险人书面同意的法律费用另行赔付。""", ["R10-5"]),
        ],
        exclusions_extra=["航空器上旅客的伤亡", "惩罚性赔款", "条款除外的战争及相关风险"],
        definitions=[("失踪", "航空器起飞后15天内没有任何消息"), ("第三者", "不包括航空器上旅客")],
    ),
    _p(
        code="AV05", name="航空器机身战争及相关风险保险", clause_title="航空器机身战争及相关风险保险（2023版）",
        category="航空航天/低空经济", stage="industrial", regions=["全国"], subject="财产",
        perils=["战争", "罢工", "恐怖活动", "劫持", "没收"], loss_forms=["直接物质损失", "费用损失"], trigger="occurrence", industry="航空",
        benchmark_rate=0.012, loss_ratio=0.5, limit_basis="aviation", deductible_floor=100000, param_style="standard",
        family="aviation", monitors=[], detect="TP1",
        liabilities=[_lia("战争相关机身损失、赎金与劫持费用", """本保险合同负责赔偿被保险飞机由于下列原因造成的、在机身一切险中除外的毁灭或损坏：战争、入侵、外敌行为、敌对行为、内战、叛乱、革命、起义、戒严、军事政变或试图篡权；罢工、暴乱、民众骚乱或劳工骚动；以政治或恐怖主义为目的的行为；恶意行为或蓄意破坏；政府或公共权力机构没收、国有化、充公、管制、扣留、占用或征用；未经被保险人同意的劫持、非法扣押或不正当控制。飞机因上述风险脱离被保险人控制期间发生的、机身一切险除外的事故也负责赔偿。
对保险期间内对被保险飞机或其乘客、机组人员的威迫，以及因没收或劫持产生的必要额外费用，保险人在保单明细表限额内负责赔偿被保险人合理支出的90%。若本保障在某地区非法，则在该地区无效。""", ["R10-6"])],
        exclusions_extra=["机身一切险已经承保的风险", "在当地属于非法的保障", "被保险人同意的劫持或控制"],
        definitions=[("飞行中", "登机后所有外部舱门关闭时起，至舱门打开供人员离开或飞机处于运动状态时止；螺旋翼飞机指旋翼由动力、动量或自动旋转期间"), ("安全归还", "飞机在除外地区以外适合运行的机场停稳、引擎熄火且不处于胁迫状态")],
    ),
    _p(
        code="AV06", name="无人机保险", clause_title="无人机保险（2022版）A款",
        category="航空航天/低空经济", stage="industrial", regions=["全国"], subject="财产",
        perils=["自然灾害", "意外事故"], loss_forms=["直接物质损失"], trigger="occurrence", industry="低空",
        benchmark_rate=0.02, loss_ratio=0.48, limit_basis="aviation", deductible_floor=5000, param_style="standard",
        family="aviation", monitors=[], detect="TP1",
        liabilities=[_lia("无人机机身损失与施救费用", """在保险期间内，在法律允许的中华人民共和国境内飞行区域范围内，被保险人或其允许的有资质的自然人使用、操控保险标的的过程中，由于自然灾害或意外事故造成保险标的的直接物质损坏或灭失，保险人按照本保险合同的约定负责赔偿。
因上述事故为防止或减少保险标的损失所支付的必要、合理费用，保险人按照本保险合同的约定也负责赔偿。""", ["R10-7"])],
        exclusions_extra=["法律不允许的飞行区域", "无资质人员操控", "被保险人未允许的使用"],
        definitions=[("保险标的", "保险单载明的无人机"), ("有资质的自然人", "依法具备相应操控资质的自然人")],
    ),
]


_CN = "一二三四五六七八九十"


def _enum(items: list[str]) -> str:
    lines = []
    for i, text in enumerate(items, start=1):
        mark = _CN[i - 1] if i <= 10 else str(i)
        end = "。" if i == len(items) else "；"
        lines.append(f"（{mark}）{text}{end}")
    return "\n".join(lines)


def _family_defaults(product: dict[str, Any]) -> dict[str, Any]:
    family = product["family"]
    materials = {
        "equipment": ["设备清单", "设备购置发票", "设备与铭牌照片", "实验室或存放场所照片", "维保记录"],
        "rd_cost": ["研发项目立项书", "研发项目预算", "研发台账", "核心团队角色清单"],
        "transfer": ["成果转化或中试方案", "关键技术指标", "约定鉴定机构", "费用预算"],
        "ip": ["知识产权清单与证书", "申请或维权文件", "费用凭证"],
        "liability": ["产品或技术说明", "销售或使用合同", "质量或测试记录", "历史索赔说明"],
        "quality": ["产品合同与技术协议", "质量检验记录", "用户索赔资料"],
        "guarantee": ["先用后转交易合同", "成果权属证明", "许可或转让公告"],
        "cyber": ["网络安全等级保护测评报告", "资产与系统清单", "安全管理制度", "安全事件记录"],
        "life": ["生产或试验批准文件", "产品说明", "质量与不良反应记录"],
        "aviation": ["保险标的技术规格", "价值证明", "飞行或发射计划", "运营资质"],
    }
    claims = {
        "equipment": (["事故照片", "维修报价或重置报价", "购置发票"], ["维保记录", "鉴定报告"]),
        "rd_cost": (["项目失败或中断的认定文件", "研发费用凭证", "项目台账"], ["鉴定意见", "审计报告"]),
        "transfer": (["转化或中试终止证明", "费用凭证", "承诺不再继续的说明"], ["第三方检测报告"]),
        "ip": (["权利证书或申请文件", "驳回、裁判或受理证明", "费用凭证"], ["代理合同", "和解协议"]),
        "liability": (["索赔资料", "损害证明", "责任认定文件"], ["法律费用凭证", "鉴定报告"]),
        "quality": (["用户书面索赔", "质量缺陷证明", "修理更换退货费用凭证"], ["检测报告", "运输凭证"]),
        "guarantee": (["先用后转合同", "违约情形证明", "损失计算说明"], ["转让公告", "签约记录"]),
        "cyber": (["安全事件说明", "费用凭证", "系统或日志证据"], ["取证报告", "媒体报道", "审计报告"]),
        "life": (["损害赔偿请求", "医学或质量鉴定", "批准文件"], ["法律费用凭证"]),
        "aviation": (["事故报告", "遥测或飞行记录", "损失计算说明"], ["维修报价", "施救费用凭证"]),
    }
    required, optional = claims[family]
    return {"materials": materials[family], "claim_required": required, "claim_optional": optional}


def _subject_text(product: dict[str, Any]) -> str:
    regions = "、".join(product["regions"])
    note = product.get("source_note") or ""
    return (
        f"本保险产品名称为{product['name']}，条款名称为{product['clause_title']}。"
        f"适用地区：{regions}。保险标的按保险单载明的{product['subject']}确定。"
        + (f"\n{note}" if note else "")
    )


def _exclusion_text(product: dict[str, Any]) -> str:
    items = [
        "投保人、被保险人及其代表的故意行为",
        "保险责任已经列明承保的风险之外的战争、恐怖活动、核辐射、核污染",
        "罚款、罚金及惩罚性赔偿",
        "保险合同生效前已经发生，或投保时被保险人已经知道的事故、纠纷或损失",
        "保险责任未列明的原因",
    ] + list(product.get("exclusions_extra") or [])
    return "下列原因造成的损失、费用和责任，保险人不负责赔偿：\n" + _enum(items) + "\n本条根据该险种保险责任整理，正式备案文本以公司条款为准。"


def _definition_text(product: dict[str, Any]) -> str:
    items = list(product.get("definitions") or []) + [("免赔额", "保险人赔偿前由被保险人自行承担的金额，具体以保险单载明为准")]
    return "\n".join(f"【{name}】{text}" for name, text in items)


def _settlement_text(product: dict[str, Any]) -> str:
    family = product["family"]
    if family == "equipment" or product["subject"] == "财产" and "直接物质损失" in product["loss_forms"]:
        body = "全部损失在保险金额内赔偿，部分损失按修复或重置的合理费用赔偿，并扣除残值和免赔额。不足额投保时，按保险金额与保险价值的比例赔偿。施救费用在保险金额以外另行计算，最高不超过保险金额。"
    elif family in ("liability", "life", "guarantee"):
        body = "保险人依据生效判决、仲裁裁决或经书面同意的和解协议核定赔偿金额，并扣除免赔额。法律费用另按保险责任约定计算。累计赔偿不超过保险单载明的赔偿限额。"
    elif family == "quality":
        body = "保险人按被保险人依法承担的修理、更换或退货费用，以及保险责任列明的鉴定、运输和维修人员费用核定赔偿，扣除免赔额后以保险金额为限。"
    elif family == "cyber" and "收入损失" in product["loss_forms"]:
        body = "费用损失按实际必要合理费用核定。利润损失按条款约定的毛利润损失公式计算，赔偿期间不超过最大赔偿期，并扣除等待期对应的损失。各项赔偿合计不超过相应保险金额。"
    elif product["param_style"] == "waiting_days":
        body = "赔偿金额按核定的费用损失计算，扣除等待期对应的损失和免赔额，并以保险金额、赔偿期和保险责任列明的费用范围为限。项目恢复、结题或被保险人继续实施同一项目后，保险人停止相应计算。"
    else:
        body = "保险人按保险责任列明的费用或损失，凭约定证明材料核定，扣除免赔额后在保险金额内赔偿。"
    return body + "被保险人应提供与事故性质、原因和损失金额有关的证明资料。"


def _amount_text(product: dict[str, Any]) -> str:
    basis = {
        "equipment": "保险金额按保险标的的重置价值或保险单约定价值确定，不得超过保险价值。",
        "rd_expense": "保险金额按保险单列明项目的可保费用确定。",
        "monthly_waiting": "保险金额按恢复研发状态所需的约定研发费用确定，并约定等待期和赔偿期。",
        "liability": "本保险设每次事故赔偿限额、每人赔偿限额和累计赔偿限额，由投保人与保险人协商确定。",
        "quality": "保险金额按可能发生的修理、更换、退货及相关费用确定。",
        "ip": "保险金额按列明权利的费用、直接损失或赔偿责任分项确定。",
        "fee": "保险金额按保险单列明申请可能发生的官费、代理费或国际阶段、国家阶段费用确定。",
        "cyber": "数据恢复、敲诈、名誉和利润损失分项约定保险金额。利润损失约定最大赔偿期。",
        "surety": "保险金额按先用后转合同约定的履约赔偿限额确定。",
        "aviation": "保险金额按保险标的约定价值或保险单分项限额确定。",
    }[product["limit_basis"]]
    return basis + "免赔额、赔偿限额和等待期以保险单载明为准。基准费率仅供内部试算，不是已报备费率。"


def build_lines() -> dict[str, dict]:
    lines = {}
    for product in PRODUCTS:
        defaults = _family_defaults(product)
        lines[product["code"]] = {
            "name": product["name"],
            "clause_title": product["clause_title"],
            "category": product["category"],
            "stage": product["stage"],
            "regions": product["regions"],
            "subject": product["subject"],
            "perils": product["perils"],
            "loss_forms": product["loss_forms"],
            "trigger": product["trigger"],
            "industry": product["industry"],
            "benchmark_rate": product["benchmark_rate"],
            "loss_ratio": product["loss_ratio"],
            "limit_basis": product["limit_basis"],
            "deductible_floor": product["deductible_floor"],
            "param_style": product["param_style"],
            "family": product["family"],
            "monitors": product["monitors"],
            "materials": defaults["materials"],
            "claim_required": defaults["claim_required"],
            "claim_optional": defaults["claim_optional"],
            "experience": product.get("experience"),
            "detect": product["detect"],
            "bundle": product.get("bundle"),
            "bundle_name": product.get("bundle_name"),
            "role": product.get("role", "主险"),
            "attachable_to": list(product.get("attachable_to") or []),
            "source_note": product.get("source_note", ""),
        }
    return lines


def build_claim_materials() -> dict[str, dict]:
    materials = {"通用": {"required": ["索赔申请书", "出险通知", "损失清单", "保险单"], "optional": ["事故说明"]}}
    for product in PRODUCTS:
        defaults = _family_defaults(product)
        materials[product["code"]] = {"required": defaults["claim_required"], "optional": defaults["claim_optional"]}
    return materials


def build_line_modules() -> list[dict[str, Any]]:
    modules = []
    for product in PRODUCTS:
        code = product["code"]
        modules.append({"code": f"{code}-SUB-01", "line": code, "module_kind": "subject", "title": "保险标的与适用范围", "text": _subject_text(product), "meta": {"subject": product["subject"]}, "source": "根据保险责任整理", "filing_status": "整理稿"})
        attachable = list(product.get("attachable_to") or [])
        for index, item in enumerate(product["liabilities"], start=1):
            meta = {"covers": item["covers"], "perils": product["perils"], "loss_form": product["loss_forms"][0], "trigger": product["trigger"], "stage": product["stage"], "source_document": product["clause_title"]}
            if attachable:
                # 该责任与其他险种处于同一事故链，可作为附加责任依附对应主险；单独出单时自身责任正文已完整，故允许独立承保
                meta["depends_on"] = [f"{main}-LIA-01" for main in attachable]
                meta["standalone_ok"] = True
            modules.append({
                "code": f"{code}-LIA-{index:02d}",
                "line": code,
                "module_kind": "liability",
                "title": item["title"],
                "text": item["text"],
                "meta": meta,
                "source": "公司条款",
                "filing_status": "已备案",
            })
        modules.append({"code": f"{code}-EXC-01", "line": code, "module_kind": "exclusion", "title": "责任免除", "text": _exclusion_text(product), "meta": {"exclusion_type": "风险特定", "bold": True, "covers": []}, "source": "根据保险责任整理", "filing_status": "整理稿"})
        modules.append({"code": f"{code}-AMT-01", "line": code, "module_kind": "amount", "title": "保险金额", "text": _amount_text(product), "meta": {}, "source": "根据保险责任整理", "filing_status": "整理稿"})
        modules.append({"code": f"{code}-SET-01", "line": code, "module_kind": "settlement", "title": "赔偿处理", "text": _settlement_text(product), "meta": {}, "source": "根据保险责任整理", "filing_status": "整理稿"})
        modules.append({"code": f"{code}-DEF-01", "line": code, "module_kind": "definition", "title": "释义", "text": _definition_text(product), "meta": {}, "source": "根据保险责任整理", "filing_status": "整理稿"})
    return modules


LINES = build_lines()
CLAIM_MATERIALS = build_claim_materials()


def _sub(name: str, patterns: list[str], stages: list[str], insurable: str, lines: list[str], consequence: str) -> dict[str, Any]:
    missing = [line for line in lines if line not in LINES]
    if missing:
        raise ValueError(f"{name} 引用了未知险种 {missing}")
    return {"name": name, "patterns": patterns, "stages": stages, "insurable": insurable, "lines": lines, "consequence": consequence}


RISK_TAXONOMY: dict[str, dict] = {
    "R01": {
        "name": "研发项目失败与费用损失",
        "stage": "rd",
        "insurability": "partial",
        "subtypes": {
            "R01-1": _sub("经营困难导致研发无法持续", ["破产", "兼并重组", "重大经营困难", "倒闭"], ["rd", "transfer"], "yes", ["RD01", "RD04", "RD05", "RD06", "TR08"], "研发主体无法持续，已投入的研发费用难以回收"),
            "R01-2": _sub("研发进度延误", ["延期", "延误", "里程碑", "进度滞后", "滞后"], ["rd", "transfer"], "partial", ["RD01", "RD04", "RD05", "RD06", "RD09", "TR08"], "项目未按约定节点完成，持续费用和超额费用发生"),
            "R01-3": _sub("关键设备导致研发延误", ["关键研发设备", "关键（核心）设备", "设备故障"], ["rd", "transfer"], "yes", ["RD01", "RD04", "RD05", "RD06", "RD09", "TR08"], "关键设备故障或损失使研发工作不能正常开展"),
            "R01-4": _sub("知识产权纠纷导致研发受阻", ["知识产权纠纷"], ["rd", "transfer"], "yes", ["RD01", "RD04", "RD05", "RD06", "TR08"], "权属或侵权纠纷使研发工作不能继续"),
            "R01-5": _sub("技术指标未达标或技术失去必要性", ["性能指标", "核心技术指标", "公开披露", "小试样品"], ["rd"], "partial", ["RD02", "RD05"], "样品或成果未达约定标准，或者技术已经公开，项目失去继续价值"),
            "R01-6": _sub("自然灾害或公共卫生导致研发终止", ["突发公共卫生", "自然灾害导致"], ["rd", "transfer"], "yes", ["RD02", "RD03"], "外部灾害或公共卫生事件破坏阶段性成果并迫使项目终止"),
            "R01-7": _sub("政策、合作方或战争导致中试终止", ["政府政策", "合作方破产", "武装冲突", "中试验证"], ["transfer"], "partial", ["RD03"], "准入政策变化、合作方破产或战争使中试无法继续"),
            "R01-8": _sub("研发延期后的超额费用", ["研发延期", "延长保险期间", "操作不善"], ["rd"], "partial", ["RD05"], "延期继续研发后，实际费用超过原约定或最终仍然失败"),
            "R01-9": _sub("研发成果设计缺陷致第三者损害", ["设计缺陷", "研发成果"], ["rd", "transfer"], "yes", ["RD01", "RD07"], "验收合格的研发成果因设计缺陷造成第三者人身伤亡或财产损失"),
        },
    },
    "R02": {
        "name": "研发设备与科研仪器",
        "stage": "rd",
        "insurability": "yes",
        "subtypes": {
            "R02-1": _sub("设计制造或电气原因导致设备损坏", ["设计、制造或安装错误", "电气原因", "短路", "超负荷", "火灾", "爆炸"], ["rd", "transfer"], "yes", ["RD08", "RD11", "RD12", "RD09"], "设备或仪器发生直接物质损坏，火灾爆炸还可能引起研发费用"),
            "R02-2": _sub("操作错误或机器损坏", ["故障", "停机", "操作错误", "技术不善", "缺乏经验", "疏忽"], ["rd", "transfer"], "yes", ["RD08", "RD11", "RD12"], "操作或技术原因造成设备突然损坏"),
            "R02-3": _sub("自然灾害或空中坠物导致研发中断", ["雷击", "暴雨", "洪水", "飞行物体", "泥石流"], ["rd"], "yes", ["RD09", "RD10"], "列明自然灾害或坠物损坏关键设备及科研资料，研发工作中断"),
            "R02-4": _sub("开放共享使用中的仪器损失", ["科研仪器", "开放共享"], ["rd"], "yes", ["RD12"], "本单位或获许可的第三方使用科研仪器时造成物质损失"),
        },
    },
    "R03": {
        "name": "成果转化与技术交易履约",
        "stage": "transfer",
        "insurability": "partial",
        "subtypes": {
            "R03-1": _sub("科技成果转化失败", ["科技成果转化", "成果转化项目", "转化失败"], ["transfer"], "yes", ["RD01", "TR01"], "转化项目被认定失败后，人工和直接投入费用损失"),
            "R03-2": _sub("先用后转履约失败", ["先用后转", "免费许可", "唯一买受人"], ["transfer"], "yes", ["TR02"], "免费期届满后继续实施或未按期完成有偿转让签约"),
            "R03-3": _sub("技术许可或转让合同违约", ["技术许可", "技术转让", "许可费", "违约金", "独家许可"], ["transfer", "industrial"], "partial", ["TR01", "TR02"], "许可或转让合同未能按约定履行，产生费用损失或履约争议"),
        },
    },
    "R04": {
        "name": "首台套、首批次与首版次",
        "stage": "transfer",
        "insurability": "yes",
        "subtypes": {
            "R04-1": _sub("重大技术装备质量缺陷致第三者损害", ["首台套", "重大技术装备", "保险装备"], ["transfer"], "yes", ["TR03"], "用户使用首台套装备时发生意外，造成第三者人身伤亡或装备以外的财产损失"),
            "R04-2": _sub("重大技术装备修理更换退货", ["修理、更换或退货", "质量保证"], ["transfer"], "yes", ["TR04"], "装备不具备约定性能或不符合合同标准，产生修理、更换或退货费用"),
            "R04-3": _sub("新材料质量缺陷致第三者损害", ["新材料", "首批次"], ["transfer"], "yes", ["TR05"], "首批次新材料在用户使用中因质量缺陷造成第三者损害"),
            "R04-4": _sub("新材料修理更换退货", ["保险新材料"], ["transfer"], "yes", ["TR06"], "新材料不符合约定使用性能、标准或质量状况"),
            "R04-5": _sub("首版次软件质量缺陷", ["首版次", "保险软件", "软件缺陷"], ["transfer", "industrial"], "yes", ["TR07"], "验收后的软件质量缺陷造成用户损失，并产生修复和检测费用"),
        },
    },
    "R05": {
        "name": "专利与知识产权",
        "stage": "transfer",
        "insurability": "yes",
        "subtypes": {
            "R05-1": _sub("国内专利申请被驳回", ["专利申请被驳回", "专利申请官费", "专利申请代理费"], ["transfer"], "yes", ["IP01"], "首次国内申请被驳回，官费和代理费损失"),
            "R05-2": _sub("PCT国际专利申请未获授权", ["PCT", "国际专利申请", "国际阶段费用"], ["transfer"], "yes", ["IP02"], "国际申请未被全部或部分申请国授权，国际阶段和国家阶段费用损失"),
            "R05-3": _sub("专利维权调查与法律费用", ["专利执行", "调查费用", "获取证据"], ["transfer", "industrial"], "yes", ["IP03"], "为制止专利被侵权而支出调查费用和法律费用"),
            "R05-4": _sub("专利被侵权损失", ["专利被侵权", "未经许可实施"], ["transfer", "industrial"], "yes", ["IP04", "IP05"], "第三方实施保险专利造成直接损失或经济损失"),
            "R05-5": _sub("过失侵犯他人专利权", ["侵犯专利权", "专利无效宣告"], ["industrial"], "yes", ["IP06"], "制造、销售或进口过程中因过失侵犯他人专利权"),
            "R05-6": _sub("海外知识产权侵权责任", ["海外侵权", "非故意侵犯"], ["industrial"], "yes", ["IP07"], "在承保区域内非故意侵犯第三方知识产权"),
            "R05-7": _sub("疏忽侵犯载明类型知识产权", ["知识产权侵权", "疏忽侵权", "非故意侵权"], ["industrial"], "yes", ["IP10"], "制造销售等活动中疏忽或非故意侵犯保险单载明类型的知识产权"),
        },
    },
    "R06": {
        "name": "商业秘密",
        "stage": "industrial",
        "insurability": "yes",
        "subtypes": {
            "R06-1": _sub("商业秘密被侵权损失", ["商业秘密", "侵犯商业秘密", "直接经济损失"], ["industrial", "transfer"], "yes", ["IP08"], "侵权行为被认定后，权利人遭受直接经济损失"),
            "R06-2": _sub("商业秘密维权费用", ["维权费用", "调查取证", "商业秘密涉嫌"], ["industrial"], "yes", ["IP09"], "为证明商业秘密被侵权而支出调查费用和法律费用"),
        },
    },
    "R07": {
        "name": "网络安全",
        "stage": "industrial",
        "insurability": "yes",
        "subtypes": {
            "R07-1": _sub("数据损坏丢失或泄露", ["数据损坏", "数据泄露", "数据被盗窃", "恶意软件", "操作失误"], ["industrial"], "yes", ["CY01"], "数据恢复、系统恢复、调查和通知费用"),
            "R07-2": _sub("拒绝服务攻击", ["拒绝服务", "DOS"], ["industrial"], "yes", ["CY01"], "系统遭受拒绝服务攻击后的恢复费用和业务影响"),
            "R07-3": _sub("网络敲诈", ["网络敲诈", "勒索赎金"], ["industrial"], "yes", ["CY01"], "敲诈赎金、专家处置费用和线索赏金"),
            "R07-4": _sub("网络安全事件名誉损失", ["不利报道", "危机公关", "名誉损失"], ["industrial"], "yes", ["CY01"], "不利报道后的媒体危机管理和广告费用"),
            "R07-5": _sub("网络事件利润损失", ["毛利润损失", "业务活动受到干扰"], ["industrial"], "yes", ["CY01"], "网络事故造成业务中断期间的毛利润损失"),
            "R07-6": _sub("云服务或第三方算力中断", ["云服务中断", "算力中断", "SaaS不可用", "训练集群宕机"], ["rd", "industrial"], "partial", ["CY01"], "云平台、第三方算力或关键 SaaS 中断导致研发或业务停摆，产生恢复费用与利润损失"),
            "R07-7": _sub("开源组件或软件供应链漏洞", ["开源组件", "软件供应链", "依赖库漏洞", "第三方组件"], ["rd", "industrial"], "partial", ["CY01"], "第三方开源或闭源组件漏洞被利用，造成系统入侵、数据损失或业务中断"),
        },
    },
    "R08": {
        "name": "人工智能与智能控制",
        "stage": "industrial",
        "insurability": "yes",
        "subtypes": {
            "R08-1": _sub("智能产品设计或运行缺陷", ["人工智能", "设计或运行缺陷", "软硬件故障", "软硬件不适配"], ["industrial"], "yes", ["AI01", "AI02"], "产品或控制系统自身缺陷造成第三者人身伤亡或财产损失"),
            "R08-2": _sub("外部交互或通信故障", ["断网", "通信网络", "突发断电", "定位系统"], ["industrial"], "yes", ["AI01", "AI02"], "通信、供电或定位异常导致搭载设备或智能产品发生事故"),
            "R08-3": _sub("智能系统遭受网络攻击", ["黑客攻击", "网络攻击", "网络安全事故"], ["industrial"], "yes", ["AI01", "AI02", "RB02"], "黑客或网络攻击造成第三者损害，或造成机器人本体损失（B款）"),
            "R08-4": _sub("具身智能机器人本体物质损失", ["具身智能", "机器人本体", "碰撞倾覆", "电气原因"], ["industrial", "rd"], "yes", ["RB01", "RB02"], "停放或运行过程中因自然灾害、火灾爆炸、碰撞、操作过失或电气等原因造成机器人本体及配套系统损失"),
        },
    },
    "R09": {
        "name": "生命科学",
        "stage": "industrial",
        "insurability": "yes",
        "subtypes": {
            "R09-1": _sub("生命科学产品完工责任", ["完工责任", "生命科学产品"], ["industrial"], "yes", ["LS01"], "生产经营活动中的产品完工责任造成第三者伤害或财产损失"),
            "R09-2": _sub("疫苗质量或接种异常反应", ["疫苗", "接种异常", "菌株"], ["industrial"], "yes", ["LS02"], "疫苗质量、菌株管理或接种异常反应造成人身伤亡"),
            "R09-3": _sub("人体临床试验损害", ["受试者", "人体临床试验", "不良事件", "严重不良事件"], ["rd", "industrial"], "yes", ["LS03", "LS01"], "受试者因接触或暴露于临床试验产品发生不良事件导致人身损害；完工责任险仅在约定条件满足并经批单纳入时承保"),
            "R09-4": _sub("药物临床试验项目终止费用损失", ["主要试验目标", "严重不良反应", "试验药物", "临床试验项目终止"], ["rd"], "yes", ["LS05"], "试验药物未达主要目标，或出现大范围非预期严重不良反应，导致临床试验终止并损失已支出费用"),
        },
    },
    "R10": {
        "name": "航空航天与低空",
        "stage": "industrial",
        "insurability": "yes",
        "subtypes": {
            "R10-1": _sub("卫星发射前物理损失", ["发射前", "发射准备", "适飞"], ["industrial"], "yes", ["AV01"], "发射准备和场内转运中的物质损失、施救费用和额外测试费用"),
            "R10-2": _sub("卫星在轨寿命损失", ["在轨", "推定全损", "卫星性能"], ["industrial"], "yes", ["AV02"], "在轨期间全部损失、推定全损或部分损失"),
            "R10-3": _sub("卫星发射及初始运行损失", ["发射终止", "初始运行"], ["industrial"], "yes", ["AV03"], "发射和初始运行期间的全损、部分损失，以及恢复适飞状态的费用"),
            "R10-4": _sub("航空器机身意外损失", ["航空器", "机身", "航空器失踪"], ["industrial"], "yes", ["AV04"], "航空器意外损失、毁坏、失踪，以及应急救助费用"),
            "R10-5": _sub("航空器第三者责任", ["航空器第三者", "机上坠落"], ["industrial"], "yes", ["AV04"], "航空器或从航空器坠落的人或物造成第三者人身伤亡和财产损失"),
            "R10-6": _sub("航空器战争劫持与没收", ["劫持", "机身战争", "国有化"], ["industrial"], "yes", ["AV05"], "战争、罢工、恐怖、恶意破坏、没收或劫持造成的机身损失和相关费用"),
            "R10-7": _sub("无人机机身损失", ["无人机", "操控"], ["industrial"], "yes", ["AV06"], "合法空域内有资质人员操控时，自然灾害或意外事故造成的机身损失"),
        },
    },
    "R11": {
        "name": "通常不可保的经营风险",
        "stage": "industrial",
        "insurability": "no",
        "subtypes": {
            "R11-1": _sub("市场接受不足", ["市场接受", "需求不及预期", "价格战"], ["transfer", "industrial"], "no", [], "销售和价格变化不属于上述险种的保险责任"),
            "R11-2": _sub("单纯资金不足", ["融资", "资金链", "现金流"], ["rd", "transfer", "industrial"], "no", [], "未达到条款列明的破产、倒闭或重大经营困难时，资金压力本身不构成保险事故"),
        },
    },
    "R12": {
        "name": "关键人员与核心团队",
        "stage": "rd",
        "insurability": "partial",
        "subtypes": {
            "R12-1": _sub("核心研发人员死亡伤残或重大疾病", ["核心研究人员", "核心研发人员", "重大疾病", "伤残", "死亡"], ["rd", "transfer"], "yes", ["RD01", "RD02", "RD04", "RD05", "RD06"], "关键人员因死亡、伤残或重大疾病无法履职，研发工作不能正常开展并造成费用损失"),
            "R12-2": _sub("核心人员竞业离职或自立门户", ["违反竞业", "竞业协议", "跳槽", "自立门户", "核心团队流失"], ["rd", "transfer"], "partial", ["RD02"], "核心人员违反竞业到竞争对手任职或自行创业，导致项目无法继续"),
            "R12-3": _sub("关键岗位空缺导致研发中断", ["关键岗位空缺", "人才断层", "招聘困难", "骨干离职"], ["rd", "transfer"], "partial", ["RD01", "RD04", "RD05"], "关键岗位长期空缺使研发进度中断，仅在符合列明事故条件时可保化"),
        },
    },
    "R13": {
        "name": "供应链与关键外协",
        "stage": "transfer",
        "insurability": "partial",
        "subtypes": {
            "R13-1": _sub("关键供应商或合作方中断", ["供应商破产", "合作方破产", "委托方破产", "断供", "单一供应商"], ["rd", "transfer", "industrial"], "partial", ["RD03", "RD09"], "关键供应商、委托方或合作方破产停产，导致研发、中试或交付无法继续"),
            "R13-2": _sub("关键零部件或原材料短缺", ["关键零部件", "原材料短缺", "进口依赖", "交期延误"], ["rd", "transfer", "industrial"], "partial", ["RD03", "RD09"], "关键零部件或原材料短缺导致试验、中试或量产中断"),
            "R13-3": _sub("外包研发或委托加工失败", ["外包研发", "委托加工", "外协失败", "代工"], ["rd", "transfer"], "partial", ["RD01", "RD03", "TR01"], "外包或委托环节失败，已投入费用损失或项目被迫终止"),
            "R13-4": _sub("物流或仓储中断导致交付延误", ["物流中断", "仓储事故", "运输延误", "冷链中断"], ["transfer", "industrial"], "partial", ["RD09", "TR01"], "物流或仓储中断使样品、装备或材料无法按时交付，造成项目或订单延误"),
        },
    },
    "R14": {
        "name": "政策准入与合规监管",
        "stage": "industrial",
        "insurability": "partial",
        "subtypes": {
            "R14-1": _sub("市场准入或注册许可政策调整", ["市场准入", "注册证", "生产许可", "准入条件", "政府政策"], ["transfer", "industrial"], "partial", ["RD03"], "准入政策、注册审批或生产许可要求变化，致使项目不符合规定并终止"),
            "R14-2": _sub("出口管制与技术安全审查", ["出口管制", "技术安全审查", "实体清单", "禁运"], ["rd", "transfer", "industrial"], "partial", ["RD03"], "出口管制或技术安全审查限制关键技术、设备或产品跨境流转，造成中试或交付无法继续"),
            "R14-3": _sub("数据合规与个人信息保护处罚", ["个人信息", "数据合规", "隐私合规", "监管处罚", "数据出境"], ["industrial", "rd"], "partial", ["CY01"], "数据处理不合规引发监管处罚、通知与补救费用，部分费用可在网络安全责任范围内承保"),
            "R14-4": _sub("环保安全与行业监管处罚", ["环保处罚", "安全生产", "行业监管", "停产整顿"], ["industrial", "transfer"], "no", [], "环保、安全或行业监管处罚本身通常不可保，应作为风险管理与核保背景"),
        },
    },
}
