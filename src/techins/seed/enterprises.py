"""演示企业画像与外部公开数据集。

外部数据源（专利、新闻、财报、工商司法）在本地以演示数据集形式提供，接入真实数据源时替换
datasources 中的适配器即可，智能体与工具代码不需要改动。
"""

from __future__ import annotations

ENTERPRISES: list[dict] = [
    {
        "name": "星衡智能装备股份有限公司", "credit_code": "91320594MA1XH8826K", "industry": "智能制造装备", "track": "工业机器人",
        "tech_field": "协作机器人、力控关节", "region": "江苏省苏州市", "founded_year": 2014, "employees": 860, "listed": True,
        "legal_rep": "周启衡", "phone": "13900000001", "address": "江苏省苏州市工业园区星湖街218号5栋",
        "financials": {"revenue": 1.86e9, "net_profit": 1.42e8, "total_assets": 3.2e9, "net_assets": 2.05e9, "operating_cash_flow": 2.1e8, "cash_reserve": 6.8e8, "monthly_fixed_cost": 6.2e7, "rd_expense": 2.31e8, "social_security_count": 842, "tax_paid": 9.6e7},
        "products": [
            {"name": "XH-C7 协作机器人", "tech_field": "协作机器人", "trl": 9, "description": "七轴协作机器人，已批量交付汽车零部件与3C客户，年销量约4200台。"},
            {"name": "XH-R7 力控关节模组", "tech_field": "力控关节", "trl": 5, "description": "集成力矩传感器的一体化关节模组，处于工程样机测试阶段，计划建设中试线。"},
        ],
        "equipment": [
            {"name": "多轴疲劳寿命测试台", "model": "FT-6000", "serial": "FT6K-2021-0388", "value": 1.28e7, "purchase_date": "2021-03-18", "location": "苏州研发中心"},
            {"name": "激光跟踪仪", "model": "LT-920", "serial": "LT920-19-1172", "value": 3.6e6, "purchase_date": "2019-08-06", "location": "苏州研发中心"},
            {"name": "关节综合性能测试台", "model": "JT-300", "serial": "JT300-2023-0045", "value": 8.9e6, "purchase_date": "2023-05-22", "location": "中试车间"},
        ],
        "suppliers": [
            {"name": "日本某精密减速器制造商", "item": "谐波减速器", "share": 0.58, "country": "日本"},
            {"name": "苏州恒芯电子有限公司", "item": "伺服驱动芯片", "share": 0.22, "country": "中国"},
        ],
        "team": {"core_size": 46, "left_last_year": 2, "core_roles": [{"role": "首席技术官", "name": "沈嘉禾"}, {"role": "力控算法负责人", "name": "顾宁远"}]},
        "security": {"level": 3, "data_scale": 3, "incidents": 0},
        "milestones": [{"name": "力控关节中试线建成", "planned": "2026-06-30", "status": "进行中"}],
    },
    {
        "name": "锐驰精密机器人科技有限公司", "credit_code": "91330206MA2H9RC71X", "industry": "智能制造装备", "track": "工业机器人",
        "tech_field": "精密减速器、机器人关节", "region": "浙江省宁波市", "founded_year": 2018, "employees": 210, "listed": False,
        "legal_rep": "陈立川", "phone": "13900000002", "address": "浙江省宁波市北仑区新碶街道明州路366号3号楼",
        "financials": {"revenue": 1.62e8, "net_profit": -1.8e7, "total_assets": 4.1e8, "net_assets": 2.3e8, "operating_cash_flow": -2.6e7, "cash_reserve": 7.4e7, "monthly_fixed_cost": 9.8e6, "rd_expense": 4.1e7, "social_security_count": 204, "tax_paid": 6.1e6},
        "products": [{"name": "RC-HD 系列精密摆线减速器", "tech_field": "精密减速器", "trl": 7, "description": "面向六轴机器人的RV减速器，已进入两家整机厂示范应用，申报首台套。"}],
        "equipment": [
            {"name": "齿轮测量中心", "model": "GMC-450", "serial": "GMC450-2020-0217", "value": 6.4e6, "purchase_date": "2020-11-03", "location": "宁波研发楼"},
            {"name": "减速器寿命试验台", "model": "RLT-80", "serial": "RLT80-2022-0091", "value": 4.2e6, "purchase_date": "2022-04-15", "location": "宁波研发楼"},
            {"name": "五轴精密磨齿机", "model": "GX-5", "serial": "GX5-2019-0533", "value": 1.15e7, "purchase_date": "2019-06-28", "location": "宁波中试车间"},
        ],
        "suppliers": [
            {"name": "德国某磨齿机厂商", "item": "磨齿机备件与维保", "share": 0.71, "country": "德国"},
            {"name": "宁波甬锋特钢有限公司", "item": "齿轮钢材", "share": 0.35, "country": "中国"},
        ],
        "team": {"core_size": 24, "left_last_year": 4, "core_roles": [{"role": "首席科学家", "name": "林致远"}, {"role": "工艺总监", "name": "郑海波"}]},
        "security": {"level": 2, "data_scale": 2, "incidents": 0},
        "milestones": [{"name": "RV减速器寿命验证达到8000小时", "planned": "2026-03-31", "status": "延期"}],
    },
    {
        "name": "擎川机器人技术有限公司", "credit_code": "91440300MA5G3QC20M", "industry": "智能制造装备", "track": "工业机器人",
        "tech_field": "移动复合机器人", "region": "广东省深圳市", "founded_year": 2019, "employees": 150, "listed": False,
        "legal_rep": "何沛然", "phone": "13900000003", "address": "广东省深圳市南山区科技园南区高新南七道99号12层",
        "financials": {"revenue": 9.6e7, "net_profit": 4.2e6, "total_assets": 2.1e8, "net_assets": 1.3e8, "operating_cash_flow": 8.0e6, "cash_reserve": 5.2e7, "monthly_fixed_cost": 6.4e6, "rd_expense": 2.3e7, "social_security_count": 148, "tax_paid": 4.4e6},
        "products": [{"name": "QC-M2 复合移动机器人", "tech_field": "移动操作机器人", "trl": 8, "description": "AGV底盘与协作臂一体化，已在半导体工厂批量部署。"}],
        "equipment": [{"name": "整机耐久测试场", "model": "自建", "serial": "QC-TEST-01", "value": 3.1e6, "purchase_date": "2022-02-10", "location": "深圳测试中心"}],
        "suppliers": [{"name": "深圳鹏达电池有限公司", "item": "动力电池包", "share": 0.48, "country": "中国"}],
        "team": {"core_size": 18, "left_last_year": 1, "core_roles": [{"role": "技术总监", "name": "杨思齐"}]},
        "security": {"level": 2, "data_scale": 2, "incidents": 0},
        "milestones": [],
    },
    {
        "name": "康衡医疗器械有限公司", "credit_code": "91110108MA01KH5X3Y", "industry": "医疗器械", "track": "医疗器械",
        "tech_field": "腔镜手术机器人", "region": "北京市海淀区", "founded_year": 2016, "employees": 320, "listed": False,
        "legal_rep": "许文博", "phone": "13900000004", "address": "北京市海淀区中关村大街27号中关村大厦9层",
        "financials": {"revenue": 4.5e7, "net_profit": -1.26e8, "total_assets": 9.8e8, "net_assets": 7.6e8, "operating_cash_flow": -1.1e8, "cash_reserve": 3.9e8, "monthly_fixed_cost": 2.4e7, "rd_expense": 1.38e8, "social_security_count": 316, "tax_paid": 2.1e6},
        "products": [{"name": "KH-S1 腔镜手术机器人", "tech_field": "手术机器人", "trl": 6, "description": "三类医疗器械，正在开展多中心注册临床试验，计划次年申报注册证。"}],
        "equipment": [
            {"name": "动物实验手术平台", "model": "AES-200", "serial": "AES200-2021-0012", "value": 5.6e6, "purchase_date": "2021-09-01", "location": "北京研发中心"},
            {"name": "电磁兼容测试系统", "model": "EMC-9", "serial": "EMC9-2020-0331", "value": 7.8e6, "purchase_date": "2020-05-20", "location": "北京研发中心"},
        ],
        "suppliers": [{"name": "美国某高精度编码器厂商", "item": "绝对值编码器", "share": 0.64, "country": "美国"}],
        "team": {"core_size": 38, "left_last_year": 5, "core_roles": [{"role": "首席医学官", "name": "梁晓岚"}, {"role": "研发副总裁", "name": "韩之远"}]},
        "security": {"level": 3, "data_scale": 3, "incidents": 0},
        "milestones": [{"name": "注册临床试验入组完成", "planned": "2026-08-31", "status": "进行中"}],
    },
    {
        "name": "瑞声微创医疗科技有限公司", "credit_code": "91310115MA1K4RS99P", "industry": "医疗器械", "track": "医疗器械",
        "tech_field": "可吸收心血管支架", "region": "上海市浦东新区", "founded_year": 2015, "employees": 280, "listed": False,
        "legal_rep": "宋静怡", "phone": "13900000005", "address": "上海市浦东新区张江高科技园区蔡伦路88号2号楼",
        "financials": {"revenue": 2.3e8, "net_profit": 2.6e7, "total_assets": 6.2e8, "net_assets": 4.4e8, "operating_cash_flow": 3.4e7, "cash_reserve": 1.6e8, "monthly_fixed_cost": 1.4e7, "rd_expense": 5.2e7, "social_security_count": 275, "tax_paid": 1.9e7},
        "products": [{"name": "RS-Bio 可吸收支架", "tech_field": "可吸收支架", "trl": 8, "description": "已获注册证并在三十余家医院使用，二代产品处于研发阶段。"}],
        "equipment": [{"name": "飞秒激光切割机", "model": "FL-60", "serial": "FL60-2020-1189", "value": 9.2e6, "purchase_date": "2020-03-12", "location": "上海生产基地"}],
        "suppliers": [{"name": "瑞士某医用高分子材料供应商", "item": "聚乳酸原料", "share": 0.83, "country": "瑞士"}],
        "team": {"core_size": 31, "left_last_year": 1, "core_roles": [{"role": "研发总监", "name": "冯语桐"}]},
        "security": {"level": 2, "data_scale": 3, "incidents": 0},
        "milestones": [],
    },
    {
        "name": "碳源新材料科技股份有限公司", "credit_code": "91430100MA4L2TY66B", "industry": "新材料", "track": "新材料",
        "tech_field": "碳化硅陶瓷基复合材料", "region": "湖南省长沙市", "founded_year": 2012, "employees": 540, "listed": True,
        "legal_rep": "罗振宇", "phone": "13900000006", "address": "湖南省长沙市岳麓区麓谷大道628号新材料产业园",
        "financials": {"revenue": 7.4e8, "net_profit": 8.6e7, "total_assets": 1.6e9, "net_assets": 9.8e8, "operating_cash_flow": 1.1e8, "cash_reserve": 2.7e8, "monthly_fixed_cost": 3.1e7, "rd_expense": 6.8e7, "social_security_count": 531, "tax_paid": 4.2e7},
        "products": [{"name": "CY-SiC 刹车盘材料", "tech_field": "陶瓷基复合材料", "trl": 7, "description": "列入重点新材料首批次应用目录，正在轨道交通客户开展首批次应用。"}],
        "equipment": [
            {"name": "化学气相渗透沉积炉", "model": "CVI-1200", "serial": "CVI12-2018-0007", "value": 2.35e7, "purchase_date": "2018-10-09", "location": "长沙中试基地"},
            {"name": "高温真空烧结炉", "model": "VS-2200", "serial": "VS22-2021-0016", "value": 1.42e7, "purchase_date": "2021-12-01", "location": "长沙中试基地"},
        ],
        "suppliers": [{"name": "某进口碳纤维供应商", "item": "高模量碳纤维", "share": 0.52, "country": "日本"}],
        "team": {"core_size": 42, "left_last_year": 2, "core_roles": [{"role": "首席科学家", "name": "唐启明"}]},
        "security": {"level": 2, "data_scale": 2, "incidents": 0},
        "milestones": [{"name": "首批次装车试验", "planned": "2026-05-31", "status": "进行中"}],
    },
    {
        "name": "华岩先进材料有限公司", "credit_code": "91340100MA8NHY0425", "industry": "新材料", "track": "新材料",
        "tech_field": "硫化物固态电解质", "region": "安徽省合肥市", "founded_year": 2020, "employees": 95, "listed": False,
        "legal_rep": "曾一凡", "phone": "13900000007", "address": "安徽省合肥市高新区望江西路800号创新产业园C2栋",
        "financials": {"revenue": 1.2e7, "net_profit": -3.4e7, "total_assets": 1.9e8, "net_assets": 1.5e8, "operating_cash_flow": -3.1e7, "cash_reserve": 4.6e7, "monthly_fixed_cost": 5.1e6, "rd_expense": 2.9e7, "social_security_count": 0, "tax_paid": 0.2e6},
        "products": [{"name": "HY-SE 硫化物电解质粉体", "tech_field": "固态电池材料", "trl": 5, "description": "实验室公斤级制备已完成，百吨级中试线在建，空气稳定性与批次一致性是主要难点。"}],
        "equipment": [
            {"name": "惰性气氛手套箱产线", "model": "GB-12", "serial": "GB12-2025-0102", "value": 6.8e6, "purchase_date": "2025-10-21", "location": "合肥中试车间"},
            {"name": "高能球磨机组", "model": "BM-40", "serial": "BM40-2025-0118", "value": 4.9e6, "purchase_date": "2025-10-28", "location": "合肥中试车间"},
        ],
        "suppliers": [{"name": "合肥某硫化锂供应商", "item": "硫化锂原料", "share": 0.9, "country": "中国"}],
        "team": {"core_size": 15, "left_last_year": 3, "core_roles": [{"role": "创始人", "name": "曾一凡"}, {"role": "工艺负责人", "name": "钱沐阳"}]},
        "security": {"level": 1, "data_scale": 1, "incidents": 0},
        "milestones": [{"name": "百吨级中试线投产", "planned": "2026-04-30", "status": "延期"}],
    },
    {
        "name": "深瞳智算科技有限公司", "credit_code": "91330106MA27SZ4N8Q", "industry": "人工智能", "track": "人工智能软件",
        "tech_field": "工业视觉检测大模型", "region": "浙江省杭州市", "founded_year": 2017, "employees": 430, "listed": False,
        "legal_rep": "黄允之", "phone": "13900000008", "address": "浙江省杭州市西湖区文三路90号东部软件园科技大厦8层",
        "financials": {"revenue": 3.1e8, "net_profit": 1.9e7, "total_assets": 5.4e8, "net_assets": 3.6e8, "operating_cash_flow": 2.8e7, "cash_reserve": 1.9e8, "monthly_fixed_cost": 2.0e7, "rd_expense": 1.12e8, "social_security_count": 426, "tax_paid": 2.3e7},
        "products": [{"name": "深瞳视检平台", "tech_field": "工业视觉大模型", "trl": 9, "description": "为三百余家制造企业提供云端质检服务，存储客户产线图像数据与部分人员信息。"}],
        "equipment": [{"name": "GPU训练集群", "model": "自建 512 卡", "serial": "SZ-GPU-01", "value": 6.2e7, "purchase_date": "2024-03-01", "location": "杭州数据中心"}],
        "suppliers": [{"name": "某云服务商", "item": "公有云算力与对象存储", "share": 0.62, "country": "中国"}],
        "team": {"core_size": 52, "left_last_year": 6, "core_roles": [{"role": "首席算法科学家", "name": "苏景行"}]},
        "security": {"level": 3, "data_scale": 5, "incidents": 1},
        "milestones": [],
    },
    {
        "name": "明澜数据科技有限公司", "credit_code": "91510100MA6CML3T0D", "industry": "人工智能", "track": "人工智能软件",
        "tech_field": "医疗影像辅助诊断", "region": "四川省成都市", "founded_year": 2019, "employees": 160, "listed": False,
        "legal_rep": "邓若溪", "phone": "13900000009", "address": "四川省成都市高新区天府大道中段1268号天府软件园E区6栋",
        "financials": {"revenue": 6.8e7, "net_profit": -9.0e6, "total_assets": 1.7e8, "net_assets": 1.2e8, "operating_cash_flow": -6.5e6, "cash_reserve": 6.1e7, "monthly_fixed_cost": 6.9e6, "rd_expense": 3.4e7, "social_security_count": 158, "tax_paid": 3.2e6},
        "products": [{"name": "明澜肺结节辅助诊断软件", "tech_field": "医学影像AI", "trl": 8, "description": "已获二类医疗器械注册证，部署于六十余家医院，处理患者影像数据。"}],
        "equipment": [{"name": "推理服务器集群", "model": "自建 64 卡", "serial": "ML-INF-02", "value": 1.1e7, "purchase_date": "2023-07-15", "location": "成都机房"}],
        "suppliers": [{"name": "成都某机房托管服务商", "item": "机房托管", "share": 1.0, "country": "中国"}],
        "team": {"core_size": 22, "left_last_year": 3, "core_roles": [{"role": "技术总监", "name": "毛思远"}]},
        "security": {"level": 3, "data_scale": 4, "incidents": 0},
        "milestones": [],
    },
    {
        "name": "晶创半导体设备有限公司", "credit_code": "91310115MA1H7JC34W", "industry": "半导体设备", "track": "半导体设备",
        "tech_field": "等离子体刻蚀设备", "region": "上海市浦东新区", "founded_year": 2015, "employees": 620, "listed": False,
        "legal_rep": "孟庆远", "phone": "13900000010", "address": "上海市浦东新区临港新片区海洋一路333号1号楼",
        "financials": {"revenue": 5.9e8, "net_profit": 3.1e7, "total_assets": 2.4e9, "net_assets": 1.5e9, "operating_cash_flow": 4.2e7, "cash_reserve": 5.6e8, "monthly_fixed_cost": 4.6e7, "rd_expense": 2.05e8, "social_security_count": 612, "tax_paid": 2.8e7},
        "products": [{"name": "JC-E300 介质刻蚀机", "tech_field": "刻蚀设备", "trl": 7, "description": "首台套装备在客户12英寸产线验证中，验证通过后转入批量订单。"}],
        "equipment": [
            {"name": "工艺验证洁净平台", "model": "CR-100", "serial": "CR100-2022-0003", "value": 4.8e7, "purchase_date": "2022-09-30", "location": "临港研发中心"},
            {"name": "射频电源测试系统", "model": "RF-77", "serial": "RF77-2021-0219", "value": 6.3e6, "purchase_date": "2021-05-17", "location": "临港研发中心"},
        ],
        "suppliers": [
            {"name": "美国某射频电源厂商", "item": "射频电源", "share": 0.74, "country": "美国"},
            {"name": "日本某真空泵厂商", "item": "干式真空泵", "share": 0.66, "country": "日本"},
        ],
        "team": {"core_size": 64, "left_last_year": 4, "core_roles": [{"role": "首席技术官", "name": "白承泽"}]},
        "security": {"level": 3, "data_scale": 2, "incidents": 0},
        "milestones": [{"name": "客户产线验证通过", "planned": "2026-09-30", "status": "进行中"}],
    },
    {
        "name": "芯测精密仪器科技有限公司", "credit_code": "91320214MA20XC7F5L", "industry": "半导体设备", "track": "半导体设备",
        "tech_field": "晶圆缺陷检测设备", "region": "江苏省无锡市", "founded_year": 2018, "employees": 240, "listed": False,
        "legal_rep": "江念祖", "phone": "13900000011", "address": "江苏省无锡市新吴区菱湖大道200号中国传感网国际创新园F11",
        "financials": {"revenue": 1.4e8, "net_profit": -2.2e7, "total_assets": 4.6e8, "net_assets": 3.2e8, "operating_cash_flow": -1.5e7, "cash_reserve": 1.3e8, "monthly_fixed_cost": 1.2e7, "rd_expense": 6.6e7, "social_security_count": 236, "tax_paid": 5.5e6},
        "products": [{"name": "XC-D200 明场缺陷检测机", "tech_field": "光学检测", "trl": 6, "description": "工程样机在客户端试用，检测灵敏度尚未达到量产要求。"}],
        "equipment": [{"name": "超精密运动平台", "model": "UP-3", "serial": "UP3-2023-0007", "value": 1.38e7, "purchase_date": "2023-01-11", "location": "无锡研发中心"}],
        "suppliers": [{"name": "德国某光学镜头厂商", "item": "高数值孔径物镜", "share": 0.81, "country": "德国"}],
        "team": {"core_size": 29, "left_last_year": 5, "core_roles": [{"role": "光学总工", "name": "秦怀瑾"}]},
        "security": {"level": 2, "data_scale": 2, "incidents": 0},
        "milestones": [{"name": "检测灵敏度达到28纳米节点要求", "planned": "2026-02-28", "status": "延期"}],
    },
    {
        "name": "拓微光电装备有限公司", "credit_code": "91420100MA49TW8E1H", "industry": "半导体设备", "track": "半导体设备",
        "tech_field": "激光退火设备", "region": "湖北省武汉市", "founded_year": 2021, "employees": 70, "listed": False,
        "legal_rep": "毛振国", "phone": "13900000012", "address": "湖北省武汉市东湖新技术开发区光谷大道77号金融港B9栋",
        "financials": {"revenue": 8.0e6, "net_profit": -2.1e7, "total_assets": 1.1e8, "net_assets": 9.0e7, "operating_cash_flow": -1.9e7, "cash_reserve": 3.3e7, "monthly_fixed_cost": 3.0e6, "rd_expense": 1.7e7, "social_security_count": 66, "tax_paid": 0.4e6},
        "products": [{"name": "TW-LA 激光退火原理样机", "tech_field": "激光退火", "trl": 4, "description": "原理样机完成实验室验证，正在设计工程样机。"}],
        "equipment": [{"name": "准分子激光器", "model": "EX-308", "serial": "EX308-2022-0041", "value": 7.5e6, "purchase_date": "2022-06-06", "location": "武汉实验室"}],
        "suppliers": [{"name": "德国某准分子激光器厂商", "item": "激光器与维保", "share": 0.92, "country": "德国"}],
        "team": {"core_size": 12, "left_last_year": 2, "core_roles": [{"role": "首席科学家", "name": "龚致和"}]},
        "security": {"level": 1, "data_scale": 1, "incidents": 0},
        "milestones": [{"name": "工程样机设计冻结", "planned": "2026-07-31", "status": "进行中"}],
    },
]

# 每家企业的公开数据：专利、新闻、财报摘要（演示数据集）
PUBLIC_DATA: dict[str, dict] = {
    "星衡智能装备股份有限公司": {
        "patents": [
            {"no": "CN114xxxx221A", "title": "一种协作机器人关节力矩闭环控制方法", "type": "发明", "status": "有效", "citations": 18, "family": 4, "claims": 12, "events": []},
            {"no": "CN115xxxx083B", "title": "谐波减速器柔轮寿命预测装置", "type": "发明", "status": "有效", "citations": 6, "family": 2, "claims": 9, "events": []},
            {"no": "CN113xxxx560B", "title": "协作机器人碰撞检测与安全停机方法", "type": "发明", "status": "有效", "citations": 22, "family": 5, "claims": 15, "events": ["2025-07-11 某竞争对手提出无效宣告请求，复审委员会已受理"]},
        ],
        "news": [
            {"date": "2025-09-02", "title": "星衡智能协作机器人年出货突破四千台", "text": "星衡智能装备股份有限公司披露，协作机器人年出货量突破四千台，汽车与3C行业订单增长明显，但谐波减速器仍主要依赖进口，交期延长至二十周。"},
            {"date": "2025-11-18", "title": "星衡智能被诉专利侵权", "text": "一家外资机器人企业在上海知识产权法院起诉星衡智能，称其碰撞检测功能侵犯专利权，索赔金额约3000万元。公司表示将积极应诉。"},
            {"date": "2026-01-09", "title": "协作机器人夹伤事故引关注", "text": "某汽车零部件工厂发生协作机器人夹伤操作人员事故，涉事设备为星衡智能产品，事故原因正在调查，公司已派技术团队协助。"},
            {"date": "2026-03-21", "title": "星衡智能力控关节中试线开工", "text": "星衡智能力控关节模组中试线开工建设，计划年内完成工程样机验证并进入小批量试产。"},
        ],
        "financial_report": "公司2025年度营业收入18.6亿元，同比增长21%；研发投入2.31亿元，占营业收入12.4%。前五大供应商采购占比41%，其中谐波减速器单一进口供应商占比较高。应收账款周转天数有所上升。",
    },
    "锐驰精密机器人科技有限公司": {
        "patents": [
            {"no": "CN116xxxx417A", "title": "一种高刚度摆线针轮减速器", "type": "发明", "status": "审查中", "citations": 1, "family": 1, "claims": 8, "events": []},
            {"no": "CN217xxxx902U", "title": "减速器装配用定位工装", "type": "实用新型", "status": "有效", "citations": 0, "family": 1, "claims": 5, "events": []},
        ],
        "news": [
            {"date": "2025-08-14", "title": "锐驰精密RV减速器进入整机厂示范应用", "text": "锐驰精密机器人科技有限公司RV减速器进入两家国产机器人整机厂示范应用，公司正申报首台套重大技术装备保险补偿。"},
            {"date": "2025-12-03", "title": "锐驰精密研发骨干离职", "text": "据业内人士透露，锐驰精密两名工艺研发骨干年内离职加入竞争对手，公司表示核心工艺已形成文件化管理。"},
            {"date": "2026-02-26", "title": "精密磨齿机故障导致交付推迟", "text": "锐驰精密一台进口五轴精密磨齿机出现主轴故障停机，等待海外厂商备件，部分样件交付推迟约六周。"},
        ],
        "financial_report": "",
    },
    "擎川机器人技术有限公司": {
        "patents": [{"no": "CN115xxxx731A", "title": "复合机器人移动底盘与机械臂协同标定方法", "type": "发明", "status": "有效", "citations": 4, "family": 1, "claims": 10, "events": []}],
        "news": [{"date": "2025-10-10", "title": "擎川机器人复合机器人进入半导体工厂", "text": "擎川机器人技术有限公司复合移动机器人在半导体工厂批量部署，未发生重大安全事故。"}],
        "financial_report": "",
    },
    "康衡医疗器械有限公司": {
        "patents": [
            {"no": "CN114xxxx318A", "title": "手术机器人主从映射与震颤滤除方法", "type": "发明", "status": "有效", "citations": 9, "family": 3, "claims": 14, "events": []},
            {"no": "CN116xxxx044A", "title": "腔镜器械快换接口", "type": "发明", "status": "审查中", "citations": 0, "family": 1, "claims": 7, "events": []},
        ],
        "news": [
            {"date": "2025-07-30", "title": "康衡医疗启动手术机器人注册临床试验", "text": "康衡医疗器械有限公司腔镜手术机器人启动多中心注册临床试验，计划入组180例受试者。"},
            {"date": "2026-01-15", "title": "康衡医疗完成C轮融资", "text": "康衡医疗完成C轮融资，资金将用于临床试验与注册申报，公司仍处于亏损状态，现金流压力较大。"},
            {"date": "2026-03-02", "title": "手术机器人临床试验出现一例不良事件", "text": "康衡医疗披露临床试验中出现一例与器械相关的严重不良事件，受试者已康复，伦理委员会已审查。"},
        ],
        "financial_report": "",
    },
    "瑞声微创医疗科技有限公司": {
        "patents": [{"no": "CN112xxxx905B", "title": "聚乳酸可吸收支架及其制备方法", "type": "发明", "status": "有效", "citations": 31, "family": 6, "claims": 18, "events": []}],
        "news": [
            {"date": "2025-06-18", "title": "瑞声微创可吸收支架进入集采", "text": "瑞声微创可吸收支架纳入省际联盟集采，价格降幅约45%，销量预计增长。"},
            {"date": "2025-12-22", "title": "瑞声微创主动召回一个批次产品", "text": "瑞声微创对一个批次支架输送系统主动召回，原因为输送导管存在潜在断裂风险，未收到伤害报告。"},
        ],
        "financial_report": "",
    },
    "碳源新材料科技股份有限公司": {
        "patents": [
            {"no": "CN113xxxx772B", "title": "碳化硅陶瓷基复合材料制动盘制备工艺", "type": "发明", "status": "有效", "citations": 14, "family": 3, "claims": 11, "events": ["2025-10-08 被诉侵犯他人商业秘密，案件审理中"]},
        ],
        "news": [
            {"date": "2025-05-12", "title": "碳源新材料刹车盘入选首批次应用目录", "text": "碳源新材料科技股份有限公司碳陶刹车盘列入重点新材料首批次应用示范指导目录，正在轨道交通客户开展装车试验。"},
            {"date": "2025-10-08", "title": "碳源新材料涉商业秘密纠纷", "text": "某企业起诉碳源新材料前员工带走烧结工艺资料，要求碳源新材料承担连带责任，案件尚在审理。"},
            {"date": "2026-02-11", "title": "碳源新材料中试基地发生小型火情", "text": "碳源新材料长沙中试基地一台烧结炉附近发生小型火情，未造成人员伤亡，设备过热原因正在排查。"},
        ],
        "financial_report": "公司2025年营业收入7.4亿元，研发投入6800万元；高模量碳纤维主要依赖进口，单一供应商采购占比约52%。",
    },
    "华岩先进材料有限公司": {
        "patents": [{"no": "CN117xxxx628A", "title": "一种高空气稳定性硫化物固态电解质及制备方法", "type": "发明", "status": "审查中", "citations": 0, "family": 1, "claims": 9, "events": []}],
        "news": [
            {"date": "2025-11-05", "title": "华岩先进材料中试线建设推迟", "text": "华岩先进材料百吨级固态电解质中试线建设进度滞后，原定投产时间推迟，公司称工艺放大过程中批次一致性未达预期。"},
            {"date": "2026-01-28", "title": "固态电池材料企业融资遇冷", "text": "多家固态电池材料初创企业融资进度放缓，华岩先进材料新一轮融资尚未完成，资金链承压。"},
        ],
        "financial_report": "",
    },
    "深瞳智算科技有限公司": {
        "patents": [
            {"no": "CN115xxxx262A", "title": "基于小样本学习的工业缺陷检测方法", "type": "发明", "status": "有效", "citations": 27, "family": 2, "claims": 13, "events": []},
            {"no": "CN116xxxx847A", "title": "工业视觉大模型的增量训练方法", "type": "发明", "status": "有效", "citations": 5, "family": 1, "claims": 10, "events": ["2026-02-20 收到某竞争对手发出的侵权警告函"]},
        ],
        "news": [
            {"date": "2025-04-22", "title": "深瞳智算遭遇勒索病毒攻击", "text": "深瞳智算科技有限公司部分测试服务器遭遇勒索病毒攻击，公司称核心生产系统未受影响，已完成数据恢复并加固。"},
            {"date": "2025-12-09", "title": "深瞳智算核心算法团队变动", "text": "深瞳智算首席算法科学家团队中有三名成员离职创业，公司表示已完成人员补充。"},
            {"date": "2026-03-15", "title": "工业视觉平台误判导致客户停线", "text": "某汽车零部件客户反映深瞳视检平台出现批量误判，导致产线停线约十小时，双方正在协商责任。"},
        ],
        "financial_report": "",
    },
    "明澜数据科技有限公司": {
        "patents": [{"no": "CN114xxxx551A", "title": "肺结节良恶性辅助判别方法", "type": "发明", "status": "有效", "citations": 12, "family": 1, "claims": 11, "events": []}],
        "news": [{"date": "2025-09-26", "title": "医疗AI企业数据合规检查趋严", "text": "监管部门对医疗人工智能企业开展数据合规检查，明澜数据表示已完成个人信息保护影响评估。"}],
        "financial_report": "",
    },
    "晶创半导体设备有限公司": {
        "patents": [
            {"no": "CN113xxxx209B", "title": "电感耦合等离子体刻蚀腔室", "type": "发明", "status": "有效", "citations": 16, "family": 4, "claims": 16, "events": []},
            {"no": "CN115xxxx677A", "title": "刻蚀终点检测方法", "type": "发明", "status": "有效", "citations": 3, "family": 2, "claims": 9, "events": ["2026-01-19 境外企业发起337调查申请，涉及刻蚀腔室结构"]},
        ],
        "news": [
            {"date": "2025-10-28", "title": "晶创半导体刻蚀机进入客户产线验证", "text": "晶创半导体设备有限公司首台介质刻蚀机进入客户12英寸产线验证，属于首台套重大技术装备。"},
            {"date": "2026-01-19", "title": "射频电源出口管制收紧影响国产设备", "text": "境外射频电源出口管制收紧，晶创半导体等国产设备企业面临关键部件断供风险，交期延长至九个月以上。"},
        ],
        "financial_report": "",
    },
    "芯测精密仪器科技有限公司": {
        "patents": [{"no": "CN116xxxx139A", "title": "暗场散射缺陷检测光路", "type": "发明", "status": "审查中", "citations": 0, "family": 1, "claims": 8, "events": []}],
        "news": [
            {"date": "2025-11-30", "title": "芯测精密检测设备验证进度滞后", "text": "芯测精密晶圆检测设备客户端验证进度滞后，检测灵敏度尚未达到要求，研发里程碑延期。"},
            {"date": "2026-02-07", "title": "芯测精密光学总工离职", "text": "芯测精密光学团队负责人离职，公司正在招聘替代人员，部分研发任务暂停。"},
        ],
        "financial_report": "",
    },
    "拓微光电装备有限公司": {
        "patents": [{"no": "CN117xxxx006A", "title": "线光斑激光退火均匀化装置", "type": "发明", "status": "审查中", "citations": 0, "family": 1, "claims": 6, "events": []}],
        "news": [{"date": "2026-02-18", "title": "拓微光电完成天使轮融资", "text": "拓微光电装备有限公司完成天使轮融资，资金用于工程样机研制，准分子激光器依赖单一进口供应商。"}],
        "financial_report": "",
    },
}

INDUSTRY_REPORTS: dict[str, str] = {
    "工业机器人": "2025年国内工业机器人产量同比增长约18%，协作机器人增速超过30%。精密减速器、伺服电机国产化率持续提升，但高端谐波减速器仍依赖进口，交期波动大。随着人机协作场景扩大，人身伤害类产品责任纠纷呈上升趋势，专利诉讼集中在碰撞检测、力控算法等领域。",
    "医疗器械": "三类医疗器械注册周期普遍在三至五年，临床试验阶段的不确定性最高。集采政策压缩了部分耗材利润空间，创新器械企业普遍处于亏损期，融资环境对研发进度影响显著。产品召回与临床试验不良事件是主要的责任风险来源。",
    "新材料": "新材料企业从实验室到量产的中试放大环节失败率较高，工艺参数窗口窄导致批次一致性问题突出。首批次应用保险补偿政策推动了新材料示范应用，但材料质量缺陷导致的更换退货风险仍需关注。实验室危化品与高温设备是财产损失的主要来源。",
    "人工智能软件": "人工智能企业面临的数据安全事件数量持续上升，勒索攻击与数据泄露是主要威胁。监管对个人信息处理与算法备案的要求不断细化。算法误判导致客户损失的纠纷开始出现，相关责任保险处于起步阶段。核心算法人才流动率高。",
    "半导体设备": "国产半导体设备在刻蚀、薄膜等环节取得突破，首台套设备在客户产线验证周期通常为一至两年。射频电源、真空泵、精密光学部件等关键零部件高度依赖进口，出口管制可能导致断供。设备验证失败将造成大额转化投入损失。",
}
