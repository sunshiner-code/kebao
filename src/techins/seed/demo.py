"""演示环境的全生命周期引导：两条产品设计流水线 → 产品发布 → 投保与核保 → 出单与监测方案 → 仿真监测产生预警
→ 红色预警启动事故响应 → 预报案 → 理赔材料提交 → 定损理赔。

所有环节都由智能体真实运行完成；本模块只负责准备投保与理赔材料（文本与照片）、推进仿真时钟，
并按"演示审批人"的规则处理人审卡片（部分卡片刻意保留给用户在界面上处理）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from ..db import session_scope, utcnow
from ..harness import hitl
from ..harness.executor import executor
from ..knowledge.catalog import LINES, has_family
from ..models import Application, ClaimCase, DesignProject, Enterprise, HitlCard, Incident, Material, MonitorPlan, Policy, Run, TelemetryPoint
from ..textutil import format_amount
from . import images

log = logging.getLogger("techins.demo")

TRACK_PROJECTS = [
    {"name": "工业机器人研发设备与中断保障", "track": "工业机器人", "members": ["星衡智能装备股份有限公司", "锐驰精密机器人科技有限公司", "擎川机器人技术有限公司"], "lines": ["RD08", "RD09"]},
    {"name": "人工智能网络安全与专利责任保障", "track": "人工智能软件", "members": ["深瞳智算科技有限公司", "明澜数据科技有限公司"], "lines": ["CY01", "IP06"]},
]

SIM_EVENTS = {
    "星衡智能装备股份有限公司": [
        {"offset_h": 30, "enterprise": "日本某精密减速器制造商", "source": "supplier", "title": "日本某精密减速器制造商主力工厂火灾停产", "content": "该厂商主力工厂发生火灾导致谐波减速器产线停产，预计恢复供货需六至八周，下游机器人企业交期普遍延长。"},
        {"offset_h": 44, "enterprise": "星衡智能装备股份有限公司", "source": "court", "title": "星衡智能装备收到专利侵权起诉二审开庭通知", "content": "外资企业就碰撞检测功能专利侵权起诉星衡智能装备，二审定于下月开庭，原告索赔金额上调至三千万元。"},
    ],
    "深瞳智算科技有限公司": [
        {"offset_h": 12, "enterprise": "深瞳智算科技有限公司", "source": "vuln", "title": "安全机构披露工业视觉平台常用组件高危漏洞", "content": "某开源模型服务组件被披露存在远程代码执行高危漏洞，已有攻击利用，影响多家人工智能企业的在线推理服务。"},
    ],
}


# ------------------------------------------------------------------ 演示审批人
class Driver:
    def __init__(self, reviewer_id: int, approver_id: int) -> None:
        self.reviewer_id = reviewer_id
        self.approver_id = approver_id
        self.handled: set[int] = set()

    def _decide(self, card: HitlCard) -> tuple[str, str] | None:
        ref = card.business_ref or {}
        if card.card_type == "decision" and "application_id" in ref and ref["application_id"] in self.hold_apps:
            return None
        if card.card_type == "claim_approval":
            return None
        keys = [o["key"] for o in card.options]
        if "approve" in keys:
            return "approve", ""
        if "accept" in keys:
            return "accept", ""
        return keys[0], ""

    hold_apps: set[int] = set()

    def step(self) -> int:
        with session_scope() as s:
            cards = s.scalars(select(HitlCard).where(HitlCard.status == "open")).all()
            for c in cards:
                s.expunge(c)
        n = 0
        for c in cards:
            if c.id in self.handled:
                continue
            d = self._decide(c)
            if d is None:
                continue
            self.handled.add(c.id)
            try:
                hitl.resolve(c.id, user_id=self.approver_id if c.card_type in ("decision", "claim_approval") else self.reviewer_id, action=d[0], comment=d[1] or "演示环境：按智能体建议处理")
                n += 1
            except hitl.HitlError:
                continue
        return n

    def until(self, pred, timeout: float, label: str) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.step()
            if pred():
                return True
            time.sleep(0.7)
        log.warning("等待超时：%s", label)
        return False


def _ent(name: str) -> Enterprise:
    with session_scope() as s:
        e = s.scalars(select(Enterprise).where(Enterprise.name == name)).first()
        s.expunge(e)
        return e


def _user_id(username: str) -> int:
    from ..models import User

    with session_scope() as s:
        return s.scalars(select(User).where(User.username == username)).first().id


def _runs_idle() -> bool:
    with session_scope() as s:
        return not s.scalars(select(Run).where(Run.status.in_(["queued", "planning", "running"]))).first()


# ------------------------------------------------------------------ 投保材料
def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def _add_material(owner_type: str, owner_id: int, ent_id: int, name: str, mtype: str, path: Path, text: str | None, level: int = 2) -> None:
    with session_scope() as s:
        s.add(Material(enterprise_id=ent_id, owner_type=owner_type, owner_id=owner_id, name=name, material_type=mtype, data_level=level, detected_level=level, file_path=str(path), mime="text/plain" if text is not None else "image/png", text=text, status="parsed" if text is not None else "uploaded", parsed={}))


def application_materials(app_id: int, ent: Enterprise, lines: list[str], *, variant: str = "normal") -> None:
    p = ent.profile
    f = p["financials"]
    folder = settings.materials_dir / f"application-{app_id}"
    today = utcnow().date()
    add = lambda name, mtype, text: _add_material("application", app_id, ent.id, name, mtype, _write(folder, name, text), text)  # noqa: E731
    name_on_license = ent.name if variant != "fraud" else ent.name.replace("有限公司", "科技有限公司")
    add("投保单.txt", "投保单", f"投保人：{ent.name}\n被保险人：{ent.name}\n统一社会信用代码：{ent.credit_code}\n联系地址：{p['address']}\n联系人：{p['legal_rep']}\n联系电话：{p['phone']}\n投保险种：{'、'.join(lines)}\n投保日期：{today:%Y年%m月%d日}\n")
    add("营业执照.txt", "营业执照", f"名称：{name_on_license}\n统一社会信用代码：{ent.credit_code}\n类型：有限责任公司\n法定代表人：{p['legal_rep']}\n住所：{p['address']}\n成立日期：{ent.founded_year}年03月15日\n")
    add("近两年财务报表.txt", "近两年财务报表或审计报告", f"资产负债表与利润表摘要（单位：元）\n营业收入：{f['revenue']:.0f}元\n净利润：{f['net_profit']:.0f}元\n经营活动现金流量净额：{f['operating_cash_flow']:.0f}元\n研发费用：{f['rd_expense']:.0f}元\n净资产：{f['net_assets']:.0f}元\n审计意见：{'标准无保留意见' if variant != 'fraud' else '保留意见'}\n")
    add("企业情况说明.txt", "企业情况说明", f"{ent.name}成立于{ent.founded_year}年，主营{ent.tech_field}，员工{ent.employees}人。" + "；".join(pr["description"] for pr in p.get("products", [])) + "\n")
    if has_family(lines, "equipment", "rd_cost"):
        eq = p.get("equipment", [])
        rows = "\n".join(f"{e['name']} | {e['model']} | {e['serial']} | {e['value']:.0f}元 | {e['purchase_date']}" for e in eq)
        add("设备清单.txt", "设备清单", f"关键研发设备清单\n名称 | 型号 | 序列号 | 原值 | 购置日期\n{rows}\n")
        inv = "\n".join(f"{e['serial']} | {e['name']} | {e['value'] * (1.0 if variant != 'fraud' else 1.35):.0f}元 | {e['purchase_date']}" for e in eq)
        add("设备购置发票.txt", "设备购置发票", f"增值税专用发票汇总\n序列号 | 名称 | 金额 | 开票日期\n{inv}\n")
        add("消防与安全检查记录.txt", "消防与安全检查记录", f"检查日期：{today - timedelta(days=40):%Y年%m月%d日}\n检查结论：消防设施完好，疏散通道畅通，危化品分区存放。\n")
        cap = (today - timedelta(days=12)).isoformat() + " 10:20"
        for i, e in enumerate(eq):
            path = folder / f"设备与铭牌照片-{e['serial']}.png"
            images.nameplate_photo(path, serial=e["serial"], model=e["model"], equipment=e["name"], captured_at=cap, seed=app_id * 10 + i, rust=0.03 if variant == "worn" and i == 0 else 0.0)
            if variant == "fraud" and i == 0:
                images.tampered(path, path, meta={"serial": e["serial"], "model": e["model"], "equipment": e["name"], "captured_at": (today + timedelta(days=3)).isoformat() + " 09:00"})
            _add_material("application", app_id, ent.id, path.name, "设备与铭牌照片", path, None)
        lab = folder / "实验室照片.png"
        images.lab_photo(lab, captured_at=cap, seed=app_id * 7)
        _add_material("application", app_id, ent.id, lab.name, "实验室照片或视频", lab, None)
    if has_family(lines, "rd_cost", "transfer"):
        ms = p.get("milestones") or [{"name": "核心产品工程化", "planned": "2027-06-30", "status": "进行中"}]
        add("研发项目立项书.txt", "研发项目立项书", f"项目名称：{ms[0]['name']}\n计划完成：{ms[0]['planned']}\n当前状态：{ms[0]['status']}\n")
        add("研发项目预算.txt", "研发项目预算", f"年度研发预算：{f['rd_expense']:.0f}元\n月度固定支出：{f['monthly_fixed_cost']:.0f}元\n")
        add("研发台账.txt", "研发台账", f"研发人员：{p['team']['core_size']}人\n日均持续费用：{f['rd_expense'] / 365:.0f}元\n")
        add("核心团队角色清单.txt", "核心团队角色清单", "\n".join(f"{r['role']}：{r['name']}" for r in p["team"].get("core_roles", [])) + "\n")
    if has_family(lines, "cyber"):
        sec = p.get("security", {})
        unpatched = 2 if variant == "vuln" else 0
        add("网络安全等级保护测评报告.txt", "网络安全等级保护测评报告", f"测评机构：安衡网络安全应急响应中心\n等保级别：{'三级' if sec.get('level', 2) >= 3 else '二级'}\n测评结论：基本符合\n高危漏洞：{unpatched}个未修补\n中危漏洞：7个\n")
        add("安全管理制度.txt", "安全管理制度", "已建立数据分级分类、访问控制、日志审计、应急响应制度，每季度开展一次应急演练。\n")
        add("安全事件记录.txt", "安全事件记录", f"近三年安全事件：{sec.get('incidents', 0)}起\n")
        add("资产清单.txt", "资产清单", "核心业务系统：在线推理服务、客户产线图像库、运维管理平台\n数据规模：客户图像约 4.2 亿张，客户联系人信息约 12 万条\n")
        add("渗透测试报告.txt", "渗透测试报告", f"测试日期：{today - timedelta(days=60):%Y年%m月%d日}\n结论：发现弱口令 1 处已整改，远程代码执行类漏洞 {unpatched} 处待修复\n")
    if any(LINES.get(line, {}).get("family") in ("ip", "guarantee") or str(line).startswith("IP") for line in lines):
        add("知识产权清单与证书.txt", "知识产权清单与证书", "发明专利 46 项，软件著作权 83 项。\n")
        add("知识产权价值评估.txt", "知识产权价值评估", "核心专利组合评估价值约 1.2 亿元。\n")
        add("诉讼与纠纷说明.txt", "诉讼与纠纷说明", "目前无未决知识产权诉讼。\n")


def create_app(ent_name: str, product_id: int, lines: list[str], sum_insured: float, *, variant: str = "normal", user_id: int | None = None, intermediary: str | None = None) -> int:
    from ..services import business

    ent = _ent(ent_name)
    p = ent.profile
    contacts = {"phone": p["phone"], "address": p["address"], "contact": p["legal_rep"]}
    statement = f"本企业主营{ent.tech_field}，" + ("设备运行与维保正常，近三年未发生保险事故。" if variant != "fraud" else "近期新购置关键设备，设备状况良好，无任何事故。")
    if variant == "fraud":
        contacts = {"phone": "13800001111", "address": "安徽省合肥市高新区望江西路800号创新产业园", "contact": "代理人周某"}
        intermediary = "合肥某保险代理有限公司"
    declared = {"statement": statement, "lines": lines, "authorizations": {"iot": True, "log": True, "ops": True}, "equipment_total": sum(e["value"] for e in p.get("equipment", []))}
    app_id = business.create_application(enterprise_id=ent.id, product_id=product_id, tier="standard", sum_insured=sum_insured, period_months=12, declared=declared, contacts=contacts, intermediary=intermediary, user_id=user_id)
    application_materials(app_id, ent, lines, variant=variant)
    return app_id


# ------------------------------------------------------------------ 理赔材料
def _failure_time(policy_id: int, serial: str) -> tuple[datetime | None, datetime | None]:
    with session_scope() as s:
        rows = s.execute(select(TelemetryPoint.ts, TelemetryPoint.metric, TelemetryPoint.value).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.asset == serial, TelemetryPoint.metric.in_(["motor_current", "vibration_rms"])).order_by(TelemetryPoint.ts)).all()
    cur = [(t, v) for t, m, v in rows if m == "motor_current"]
    stop = next((cur[i][0] for i in range(1, len(cur)) if cur[i][1] < 1 and cur[i - 1][1] > 5), None)
    vib = [(t, v) for t, m, v in rows if m == "vibration_rms"]
    base = sorted(v for _, v in vib[: len(vib) // 2]) or [3.0]
    thr = base[int(len(base) * 0.98)] + 0.4
    degrade = next((t for t, v in vib if v > thr), None)
    return stop, degrade


def equipment_claim_materials(claim_id: int, ent: Enterprise, policy_no: str, failure: datetime, degrade: datetime | None) -> None:
    folder = settings.materials_dir / f"claim-{claim_id}"
    occ = failure - timedelta(minutes=40)
    rep = occ + timedelta(hours=3)
    p = ent.profile
    eq = max(p["equipment"], key=lambda e: e["value"])
    daily = p["financials"]["rd_expense"] / 365
    add = lambda name, mtype, text: _add_material("claim", claim_id, ent.id, name, mtype, _write(folder, name, text), text)  # noqa: E731
    claim_total = 286000 + 4 * 19800 + 180 * 650 + 28000 + 68000 + 40 * round(daily, -2)
    add("索赔申请书.txt", "索赔申请书", f"被保险人：{ent.name}\n保单号：{policy_no}\n出险时间：{occ:%Y年%m月%d日 %H时%M分}\n出险地点：宁波中试车间\n索赔金额：{claim_total:.0f}元\n其他保险：无\n收款账户：6222 0212 0300 4567 891（户名：{ent.name}）\n")
    add("出险通知.txt", "出险通知", f"通知时间：{rep:%Y年%m月%d日 %H时%M分}\n通知方式：电话及平台报案\n")
    add("客户事故声明.txt", "客户事故声明", f"{occ:%Y年%m月%d日 %H时%M分}左右，我司宁波中试车间的{eq['name']}（序列号 {eq['serial']}）在加工RV减速器摆线轮时主轴突然发出异响，随后振动剧烈并自动停机，主轴前端冒烟。"
        f"此前一周设备振动略有升高，我司已联系德国厂商安排维保，但因备件交期延长尚未到场。事故发生后我司立即断电隔离设备并拍照保全现场，于{rep:%Y年%m月%d日 %H时%M分}向保险人报案，"
        f"并租赁同型号替代设备维持关键实验。初步估计设备修理与研发中断损失约{format_amount(claim_total)}。\n")
    rows = [
        ("主轴总成", "设备修理", "1", "286000", "286000"),
        ("精密轴承", "设备修理", "4", "19800", f"{4 * 19800}"),
        ("设备维修工时", "设备修理", "180", "650", f"{180 * 650}"),
        ("现场检测鉴定", "设备修理", "1", "28000", "28000"),
        ("设备租赁（替代设备）", "施救费用", "1", "68000", "68000"),
        ("研发中断持续费用", "研发中断持续费用", "40", f"{round(daily, -2):.0f}", f"{40 * round(daily, -2):.0f}"),
    ]
    add("损失清单.txt", "损失清单", "损失清单（单位：元）\n项目 | 类别 | 数量 | 单价 | 金额\n" + "\n".join(" | ".join(r) for r in rows) + "\n")
    add("维修报价.txt", "维修报价", "报价单位：国联精密设备原厂服务中心\n维修周期：25天\n维修方案：更换主轴总成与前后端精密轴承，重新动平衡与精度校准\n项目 | 数量 | 单价 | 金额\n主轴总成 | 1 | 286000 | 286000\n精密轴承 | 4 | 19800 | 79200\n设备维修工时 | 180 | 650 | 117000\n")
    add("购置发票.txt", "购置发票", f"名称：{eq['name']}\n序列号：{eq['serial']}\n金额：{eq['value']:.0f}元\n开票日期：{eq['purchase_date']}\n")
    deg = degrade or (occ - timedelta(hours=48))
    add("检测报告.txt", "检测报告", f"鉴定机构：精测工业设备检测鉴定有限公司\n鉴定对象：{eq['name']}（序列号 {eq['serial']}）\n鉴定结论：主轴前端轴承因润滑不良与疲劳磨损导致保持架突发断裂，进而引发主轴抱死与烧蚀，属突发机械故障；轴承批次未发现制造缺陷。\n振动异常开始时间：{deg:%Y年%m月%d日 %H时%M分}\n网关时钟偏差：+8分钟\n维保情况：维保记录显示计划维保推迟约2个月未执行。\n")
    add("维保记录.txt", "维保记录", f"维保服务商：德国某磨齿机厂商\n计划维保日期：{(occ - timedelta(days=70)):%Y年%m月%d日}\n执行情况：因备件交期延长推迟2个月，截至事故发生仍未执行\n合同约定：维保服务商因未按期维保造成损失的责任上限50万元\n")
    add("研发项目台账.txt", "研发项目台账", f"项目名称：RV减速器寿命验证\n日均持续费用：{round(daily, -2):.0f}元\n中断开始：{occ:%Y年%m月%d日}\n预计恢复：{(occ + timedelta(days=25)):%Y年%m月%d日}\n")
    add("中断原因证明.txt", "中断原因证明", f"因{eq['name']}主轴故障，RV减速器寿命验证试验自{occ:%Y年%m月%d日}起中断。\n")
    add("中断期间费用凭证.txt", "中断期间费用凭证", f"中断期间研发人员薪酬与场地费用按日均{round(daily, -2):.0f}元持续发生，附工资表与租金凭证。\n")
    for i, (part, burn, rust) in enumerate((("主轴总成", 0.36, 0.04), ("前端精密轴承", 0.14, 0.0))):
        path = folder / f"设备损坏照片-{part}.png"
        images.damage_photo(path, serial=eq["serial"], part=part, captured_at=(occ + timedelta(hours=1)).isoformat(sep=" ")[:16], seed=claim_id * 10 + i, burn=burn, rust=rust)
        _add_material("claim", claim_id, ent.id, path.name, "设备损坏照片", path, None)


def breach_claim_materials(claim_id: int, ent: Enterprise, policy_no: str, intrusion: datetime, exfil_end: datetime) -> None:
    folder = settings.materials_dir / f"claim-{claim_id}"
    add = lambda name, mtype, text: _add_material("claim", claim_id, ent.id, name, mtype, _write(folder, name, text), text)  # noqa: E731
    rev_daily = ent.profile["financials"]["revenue"] / 365
    bi = round(rev_daily * 1.5 * 0.42, -2)
    items = [("应急响应取证服务", "事件响应与法律费用", "12", "9800"), ("数据恢复服务", "事件响应与法律费用", "3", "12000"), ("律师服务", "事件响应与法律费用", "60", "2500"), ("受影响客户通知费用", "事件响应与法律费用", "1", "86000"), ("营业中断损失", "营业中断", "1", f"{bi:.0f}"), ("监管罚款", "罚款", "1", "300000")]
    total = sum(float(q) * float(u) for _, _, q, u in items)
    add("索赔申请书.txt", "索赔申请书", f"被保险人：{ent.name}\n保单号：{policy_no}\n出险时间：{intrusion:%Y年%m月%d日 %H时%M分}\n出险地点：杭州数据中心核心业务系统\n索赔金额：{total:.0f}元\n其他保险：无\n")
    add("出险通知.txt", "出险通知", f"通知时间：{(exfil_end + timedelta(hours=5)):%Y年%m月%d日 %H时%M分}\n")
    add("客户事故声明.txt", "客户事故声明", f"{intrusion:%Y年%m月%d日 %H时%M分}起，我司核心业务系统出现大量异常登录，随后运维账号被盗用并向境外地址外传数据。安全团队发现后立即隔离受影响系统、重置全部特权账号并启用多因素认证，"
        f"于{(exfil_end + timedelta(hours=5)):%Y年%m月%d日 %H时%M分}向保险人报案。事件导致在线推理服务中断约36小时，部分客户联系人信息泄露。\n")
    add("损失清单.txt", "损失清单", "损失清单（单位：元）\n项目 | 类别 | 数量 | 单价 | 金额\n" + "\n".join(f"{n} | {c} | {q} | {u} | {float(q) * float(u):.0f}" for n, c, q, u in items) + "\n")
    add("网络安全取证报告.txt", "网络安全取证报告", f"取证机构：安衡网络安全应急响应中心\n取证结论：攻击者利用模型服务组件未修补的高危漏洞并结合暴力破解获取运维账号，经云平台默认开放的管理端口进入内网，于{intrusion:%Y年%m月%d日 %H时}起登录失败激增，随后外传数据约9GB。\n受影响记录数：约120000条\n云平台安全组存在默认开放的管理端口（配置错误），未按服务合同约定进行基线加固。\n漏洞情况：该高危漏洞披露后45天内未修补。\n")
    add("事件响应费用凭证.txt", "事件响应费用凭证", "应急响应取证服务 12 人天，数据恢复 3TB，律师服务 60 小时，附发票。\n")
    add("业务中断证明.txt", "业务中断证明", f"中断时长：36小时\n营业收入减少：{rev_daily * 1.5:.0f}元\n毛利率：42%\n")
    add("监管通知.txt", "监管通知", "网信部门已受理数据安全事件报告，并作出罚款三十万元的行政处罚决定。\n")
    add("云服务合同.txt", "服务合同", "云服务商应按约定完成安全组基线加固；因云服务商原因造成的损失，责任上限100万元。\n")


# ------------------------------------------------------------------ 主流程
def bootstrap_demo(timeout: float = 1500) -> dict:
    from ..services import pipeline

    with session_scope() as s:
        if s.scalars(select(DesignProject)).first() is not None:
            return {"skipped": True}
    designer = _user_id("designer")
    executor.start()
    drv = Driver(reviewer_id=service, approver_id=senior)
    t0 = time.time()
    projects = []
    for spec in TRACK_PROJECTS:
        ids = [_ent(n).id for n in spec["members"]]
        pid = pipeline.create_project(name=spec["name"], project_type="track", track=spec["track"], enterprise_ids=ids, target_lines=spec["lines"], config={"mode": "auto", "auto_advance": True}, owner_id=designer)
        projects.append(pid)

    def pricing_done() -> bool:
        with session_scope() as s:
            done = 0
            for pid in projects:
                pr = s.get(DesignProject, pid)
                pipeline.refresh_project(pr, s)
                done += pr.pipeline["nodes"]["pricing"].get("status") == "done"
            return done == len(projects)

    drv.until(pricing_done, timeout, "产品设计流水线")
    if not pricing_done():
        raise RuntimeError("产品设计流水线未完成，不能继续组装产品包")
    log.info("产品设计完成 %.0fs", time.time() - t0)
    return continue_demo(projects, t0=t0)


def continue_demo(project_ids: list[int] | None = None, t0: float | None = None) -> dict:
    """从产品包组装继续演示引导。设计项目的条款与定价均已冻结时，可单独调用以接上中断的后半段。"""
    import techins.agents  # noqa: F401
    from ..models import Product
    from ..services import business, monitoring, products

    designer, actuary, service, senior, _admin = (_user_id(u) for u in ("designer", "actuary", "service", "senior", "admin"))
    if project_ids is None:
        with session_scope() as s:
            by_name = {p.name: p.id for p in s.scalars(select(DesignProject)).all()}
        missing = [spec["name"] for spec in TRACK_PROJECTS if spec["name"] not in by_name]
        if missing:
            raise RuntimeError(f"缺少设计项目：{'、'.join(missing)}")
        project_ids = [by_name[spec["name"]] for spec in TRACK_PROJECTS]
    executor.start()
    drv = Driver(reviewer_id=service, approver_id=senior)
    t0 = time.time() if t0 is None else t0
    product_ids = []
    for pid in project_ids:
        with session_scope() as s:
            project = s.get(DesignProject, pid)
            existing = ((project.pipeline or {}).get("nodes") or {}).get("package", {}).get("product_id")
            published = existing is not None and s.get(Product, existing) is not None and s.get(Product, existing).status == "published"
        if published:
            product_ids.append(existing)
            continue
        prod = products.assemble_package(pid, designer)
        products.approve_product(prod, actuary, "approve", "演示环境：审批发布")
        product_ids.append(prod)
    robot_product, ai_product = product_ids

    for name, events in SIM_EVENTS.items():
        with session_scope() as s:
            e = s.scalars(select(Enterprise).where(Enterprise.name == name)).first()
            e.profile = {**e.profile, "simulation": {"events": events}}

    app_specs = [
        ("锐驰", "锐驰精密机器人科技有限公司", robot_product, ["RD08", "RD09"], 2.2e7, "worn"),
        ("星衡", "星衡智能装备股份有限公司", robot_product, ["RD08", "RD09"], 2.5e7, "normal"),
        ("深瞳", "深瞳智算科技有限公司", ai_product, ["CY01", "IP06"], 3.0e7, "vuln"),
        ("华岩", "华岩先进材料有限公司", robot_product, ["RD08", "RD09"], 1.8e7, "fraud"),
        ("擎川", "擎川机器人技术有限公司", robot_product, ["RD08", "RD09"], 3.0e6, "normal"),
    ]
    apps = {}
    for key, ent_name, product_id, lines, amount, variant in app_specs:
        ent = _ent(ent_name)
        with session_scope() as s:
            existing = s.scalars(select(Application).where(Application.enterprise_id == ent.id, Application.product_id == product_id)).first()
            existing_id = existing.id if existing else None
        apps[key] = existing_id or create_app(ent_name, product_id, lines, amount, variant=variant, user_id=service)
    Driver.hold_apps = {apps["华岩"]}
    for key in ("锐驰", "星衡", "深瞳", "华岩"):
        with session_scope() as s:
            started = any((r.business_ref or {}).get("application_id") == apps[key] for r in s.scalars(select(Run).where(Run.agent_id == "AG-UW")).all())
        if not started:
            business.start_underwriting(apps[key], service)

    def policies_ready() -> bool:
        with session_scope() as s:
            n = len(s.scalars(select(MonitorPlan)).all())
            hold = s.get(Application, apps["华岩"])
            return n >= 3 and hold.status == "pending_decision"

    drv.until(policies_ready, 600, "核保与监测方案")
    log.info("核保与监测方案完成 %.0fs", time.time() - t0)
    with session_scope() as s:
        for pol in s.scalars(select(Policy)).all():
            pol.start_date = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=120)
            pol.end_date = pol.start_date + timedelta(days=365)
        plans = {s.get(Policy, p.policy_id).enterprise_id: p.id for p in s.scalars(select(MonitorPlan)).all()}
    ruichi, xingheng, shentong = (_ent(n) for n in ("锐驰精密机器人科技有限公司", "星衡智能装备股份有限公司", "深瞳智算科技有限公司"))
    for ent, hours in ((xingheng, 48), (shentong, 34), (ruichi, 64)):
        pid = plans[ent.id]
        for _ in range(hours):
            monitoring.tick_plan(pid, 1)
            drv.step()
        drv.until(_runs_idle, 300, f"{ent.name}预警研判")
    log.info("仿真监测完成 %.0fs", time.time() - t0)

    def claims_registered(n: int):
        return lambda: len(_claims()) >= n

    drv.until(claims_registered(2), 600, "事故响应与预报案")
    result = {"products": product_ids, "applications": apps}
    for case in _claims():
        with session_scope() as s:
            pol = s.get(Policy, case["policy_id"])
            policy_no, ent_id = pol.policy_no, pol.enterprise_id
        if ent_id == ruichi.id:
            failure, degrade = _failure_time(case["policy_id"], max(ruichi.profile["equipment"], key=lambda e: e["value"])["serial"])
            if failure:
                equipment_claim_materials(case["id"], ruichi, policy_no, failure, degrade)
                business.start_claim_run(case["id"], service)
                result["equipment_claim"] = case["id"]
        elif ent_id == shentong.id:
            with session_scope() as s:
                rows = s.execute(select(TelemetryPoint.ts, TelemetryPoint.metric, TelemetryPoint.value).where(TelemetryPoint.policy_id == case["policy_id"], TelemetryPoint.metric.in_(["login_failures", "egress_mb"])).order_by(TelemetryPoint.ts)).all()
            lf = [(t, v) for t, m, v in rows if m == "login_failures"]
            eg = [(t, v) for t, m, v in rows if m == "egress_mb"]
            intr = next((t for t, v in lf if v > 150), lf[-1][0])
            ex_end = max((t for t, v in eg if v > 1200), default=intr + timedelta(hours=10))
            breach_claim_materials(case["id"], shentong, policy_no, intr, ex_end)
            result["breach_claim"] = case["id"]
    drv.until(lambda: _claim_status(result.get("equipment_claim")) in ("pending_approval", "approved", "closed") or result.get("equipment_claim") is None, 600, "定损理赔")
    rw_report = executor.create_run(agent_id="AG-RW", inputs={"policy_id": _policy_of(xingheng.id), "period": "month"}, business_ref={"policy_id": _policy_of(xingheng.id)}, mode="auto", user_id=service, title=f"风险管理报告：{xingheng.name}")
    drv.until(lambda: _run_status(rw_report) in ("completed", "failed"), 300, "风险管理报告")
    drv.until(_runs_idle, 120, "收尾")
    result["elapsed_s"] = round(time.time() - t0, 1)
    return result


def _claims() -> list[dict]:
    with session_scope() as s:
        return [{"id": c.id, "policy_id": c.policy_id, "status": c.status} for c in s.scalars(select(ClaimCase)).all()]


def _claim_status(cid: int | None) -> str | None:
    if cid is None:
        return None
    with session_scope() as s:
        c = s.get(ClaimCase, cid)
        return c.status if c else None


def _run_status(rid: int) -> str:
    with session_scope() as s:
        return s.get(Run, rid).status


def _policy_of(ent_id: int) -> int:
    with session_scope() as s:
        return s.scalars(select(Policy).where(Policy.enterprise_id == ent_id)).first().id


_ = Incident
