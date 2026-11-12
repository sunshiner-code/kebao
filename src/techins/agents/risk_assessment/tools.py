"""风险评估智能体的工具。"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict

import networkx as nx

from ...datasources.public import get_source
from ...harness.tools import ToolError, ToolResult, registry
from ...knowledge.catalog import INDUSTRY_PROFILES, INSURABILITY_LABEL, LINES, RISK_TAXONOMY, STAGES, SUBTYPES, subtype_record
from ...knowledge.eligibility import enterprise_context, filter_lines
from .reasoning import STAGE_WORDS
from ...knowledge.index import knowledge_index, record_citations
from ...llm.gateway import ModelUnavailable, model_gateway
from ...privacy.gateway import EgressReport, privacy_gateway
from ...privacy.vault import TokenVault
from ...textutil import cosine_tokens, format_amount, truncate
from ..common import level_of_text, load_materials, new_card, verify_card

def _sanitize_risk_title(title: str | None, *, type_name: str | None = None) -> str:
    """避免把新闻/公告摘录直接当作风险名称。"""
    import re

    text = re.sub(r"\s+", "", (title or "").strip())
    fallback = (type_name or "").strip() or "新创建风险"
    if not text:
        return fallback
    bad = ("披露", "占比", "采购自", "从单一", "随着", "公司已收到", "近一年", "前五大", "第一大", "科技保险从", "某竞争")
    if len(text) > 30 or any(marker in text for marker in bad) or re.match(r"^\d{4}-\d{2}-\d{2}", text):
        match = re.search(r"([\u4e00-\u9fff]{4,20}(?:风险|责任|损失|中断|侵权|泄露|损坏|失效))", text)
        if match:
            return match.group(1)
        return fallback
    return text


ADVICE = {
    "R01-1": "监控现金流和持续经营能力，提前准备项目止损与费用台账",
    "R01-2": "建立里程碑偏差预警，关键路径任务配置备份资源",
    "R01-3": "对关键研发设备做状态监测和备机安排，保留故障与维修记录",
    "R01-4": "开展知识产权尽职调查，争议发生后及时固定证据",
    "R01-5": "对照立项指标设置阶段评审，技术公开后及时评估是否继续",
    "R01-6": "完善灾害和公共卫生应急预案，阶段性成果异地备份",
    "R01-7": "跟踪准入政策与合作方履约能力，中试合同设置终止安排",
    "R01-8": "延期前核定剩余预算，区分原定费用和超额费用",
    "R01-9": "研发成果验收时保留设计鉴定材料，限定交付范围",
    "R02-1": "保留设备设计、安装和电气保护记录，定期做预防性维护",
    "R02-2": "执行操作规程和维保计划，按期更换易损件",
    "R02-3": "完善消防与防灾措施，科研资料离线备份",
    "R02-4": "开放共享前核验使用人员资质，并保留使用登记",
    "R03-1": "转化项目设置阶段目标和失败认定机制，费用单独归集",
    "R03-2": "先用后转合同明确免费期限、续签和唯一买受人义务",
    "R04-1": "首台套交付前完成型式试验和用户培训，保留质量追溯",
    "R04-2": "合同中写明性能、标准和质量状况，出厂检验留档",
    "R04-3": "首批次应用前向用户披露已知性能边界",
    "R04-4": "新材料批次检验和用户索赔资料分开建档",
    "R04-5": "首版次软件保留测试报告、版本记录和验收文件",
    "R05-1": "申请前做可专利性检索，区分官费和代理费",
    "R05-2": "PCT申请前评估目标国授权可能性，分项记录国际和国家阶段费用",
    "R05-3": "发现被侵权后先做证据保全，再选择诉讼、仲裁或行政处理",
    "R05-4": "监测专利实施情况，索赔以生效法律文书或经同意的和解为依据",
    "R05-5": "产品上市前做自由实施分析，保留无效宣告和抗辩费用凭证",
    "R05-6": "按承保区域做海外知识产权检索，约束下游企业的使用范围",
    "R06-1": "商业秘密分级并保留保密措施证据",
    "R06-2": "维权前固定调查费用凭证，选择合适的救济途径",
    "R07-1": "数据分类分级、最小权限和离线备份",
    "R07-2": "对拒绝服务准备流量清洗和应急切换",
    "R07-3": "网络敲诈处置须事先取得保险人书面同意",
    "R07-4": "准备媒体危机口径，不利报道后14日内保留公关费用凭证",
    "R07-5": "明确最大赔偿期和毛利润计算口径",
    "R08-1": "保留软硬件版本、测试记录和适用场景说明",
    "R08-2": "对通信、供电和定位中断设置降级保护",
    "R08-3": "智能产品上线前完成安全测试，记录攻击处置过程",
    "R09-1": "完工责任范围和追溯期在保单中写清，试验纳入须单独批单",
    "R09-2": "疫苗质量、菌株和接种异常分别建立追溯",
    "R09-3": "人体试验取得全部批准后再申请批单，严格执行方案",
    "R09-4": "对照临床试验方案保留主要终点和严重不良反应记录，项目终止时归集已支出费用",
    "R10-1": "发射前测试、转运和仓储分段记录风险起止",
    "R10-2": "在轨运行保留遥测，理赔前用尽备份和余量",
    "R10-3": "发射终止后的适飞恢复费用单独归集",
    "R10-4": "航空器价值、适航和飞行记录保持可核验",
    "R10-5": "第三者责任与旅客责任分开管理",
    "R10-6": "战争和劫持风险单独安排保障，注意地区合法性",
    "R10-7": "只在合法空域由有资质人员操控，保留飞行记录",
    "R11-1": "市场变化不属于现有险种责任，应通过商业安排消化",
    "R11-2": "单纯资金压力不构成保险事故，需与条款列明的破产或重大经营困难区分",
    "R03-3": "技术许可与转让合同明确里程碑、验收和违约责任，履约争议及时固定证据",
    "R07-6": "关键云与算力服务设置多活或异地备份，约定可用性与故障切换时限",
    "R07-7": "建立开源与第三方组件清单，定期扫描漏洞并限制高危依赖上线",
    "R12-1": "核心岗位做双人备份与知识沉淀，投保前核验人员健康与岗位清单",
    "R12-2": "竞业与保密协议及时签署并保留证据，关键人员变动后评估项目可否继续",
    "R12-3": "关键岗位建立继任计划，空缺超过阈值时启动外包或合作实验室方案",
    "R13-1": "关键供应商做第二来源与财务健康监测，合同设置破产终止与费用结算安排",
    "R13-2": "关键零部件保持安全库存并跟踪交期，进口件评估替代方案",
    "R13-3": "外包与委托合同明确交付标准、验收和失败认定，费用单独归集",
    "R13-4": "关键样品与装备运输投保分段责任，冷链与仓储设置备份路径",
    "R14-1": "跟踪注册与准入政策窗口，政策变化后及时评估项目是否继续",
    "R14-2": "出口管制与实体清单做尽职调查，跨境交付预留合规审查周期",
    "R14-3": "个人信息与重要数据分类分级，跨境与委托处理前完成合规评估",
    "R14-4": "环保与安全生产整改闭环，监管处罚作为核保与风险管理输入",
}

TRANSMISSION = [
    ("R02", "R01-3"),
    ("R07", "R07-5"),
    ("R08", "R08-3"),
    ("R01", "R03-1"),
    ("R12", "R01-2"),
    ("R13", "R01-7"),
    ("R14", "R01-7"),
    ("R07", "R14-3"),
]


def _ent(ctx) -> dict:
    return ctx.workspace["enterprise"]


def _level_label(score: int) -> str:
    return "低" if score <= 4 else "中" if score <= 9 else "高" if score <= 16 else "极高"


def _sev_band(ratio: float) -> int:
    return 1 if ratio < 0.01 else 2 if ratio < 0.05 else 3 if ratio < 0.15 else 4 if ratio < 0.4 else 5


def _cards(ctx) -> dict[str, dict]:
    return ctx.workspace.setdefault("evidence", {})


# ---------------------------------------------------------------- 公开信息采集（子智能体执行）
@registry.tool("collect_public_source", "公开信息采集", "按指定信息源采集企业公开信息并生成证据卡片", permission="T0", zone="cloud", max_level=0, params={"source": "patent/news/financial/report/policy"})
def collect_public_source(ctx, source: str | None = None, **_) -> ToolResult:
    source = source or ctx.inputs.get("source")
    from ...services import ops

    try:
        ops.check_source(source)
    except ops.SourceDisabled as exc:
        raise ToolError(f"{exc}，该信息源的证据缺失将记入数据缺口", retryable=False) from exc
    started = time.perf_counter()
    try:
        result = _collect_public_source(ctx, source)
    except Exception as exc:
        ops.record_source_call(source, False, (time.perf_counter() - started) * 1000, str(exc))
        raise
    ops.record_source_call(source, True, (time.perf_counter() - started) * 1000)
    return result


def _collect_public_source(ctx, source: str) -> ToolResult:
    name = ctx.inputs.get("enterprise_name")
    track = ctx.inputs.get("track")
    ds = get_source()
    cards = []
    label = {"patent": "专利", "news": "新闻舆情", "financial": "财报", "report": "行业报告", "policy": "政策"}[source]
    if source == "patent":
        for p in ds.patents(name):
            text = f"专利《{p['title']}》（{p['type']}，{p['status']}），被引用 {p['citations']} 次，同族 {p['family']} 件，权利要求 {p['claims']} 项。" + "".join(p.get("events", []))
            cards.append(new_card(prefix="PAT", source_type="public_patent", source_name="专利数据库", source_ref=p["source_ref"], text=text, position=p["no"], level=0, extractor="规则抽取", enterprise=name))
            for ev in p.get("events", []):
                cards.append(new_card(prefix="PATEV", source_type="public_patent", source_name="专利法律状态", source_ref=p["source_ref"], text=ev, position=p["no"], date=ev[:10], level=0, extractor="规则抽取", enterprise=name))
    elif source == "news":
        years = int(ctx.inputs.get("years", 3))
        since = f"{2026 - years}-01-01"
        for n in ds.news(name, since):
            cards.append(new_card(prefix="NEWS", source_type="public_news", source_name=n.get("channel", "新闻"), source_ref=n["source_ref"], text=n["text"], position=n["title"], date=n["date"], level=0, extractor="事件抽取", enterprise=name))
        for c in ds.court(name):
            if not any(c["text"] in x["text"] for x in cards):
                cards.append(new_card(prefix="COURT", source_type="public_court", source_name="司法信息", source_ref=c["source_ref"], text=c["text"], position=c["title"], date=c["date"], level=0, extractor="事件抽取", enterprise=name))
    elif source == "financial":
        fr = ds.financial_report(name)
        if fr:
            for i, sent in enumerate(fr["text"].split("；")):
                cards.append(new_card(prefix="FIN", source_type="public_financial", source_name=f"年报（{fr['period']}）", source_ref=fr["source_ref"], text=sent.strip("。") + "。", position=f"第{i + 1}句", level=0, extractor="规则抽取", enterprise=name))
        reg = ds.registry(name)
        if reg:
            cards.append(new_card(prefix="REG", source_type="public_registry", source_name="工商登记", source_ref=reg["source_ref"], text=f"{reg['name']}成立于{reg['founded_year']}年，注册地{reg['region']}，所属行业{reg['industry']}。", position="登记信息", level=0, extractor="结构化读取", enterprise=name))
    elif source == "report":
        rep = ds.industry_report(track)
        if rep:
            for i, sent in enumerate(s for s in rep["text"].split("。") if s.strip()):
                cards.append(new_card(prefix="IND", source_type="public_report", source_name=rep["title"], source_ref=rep["source_ref"], text=sent + "。", position=f"第{i + 1}句", level=0, extractor="规则抽取", enterprise=name))
    elif source == "policy":
        for p in ds.policies(f"{track} 科技保险 {ctx.inputs.get('tech_field', '')}"):
            cards.append(new_card(prefix="POL", source_type="public_policy", source_name=p["title"], source_ref=p["source_ref"], text=truncate(p["text"], 220), position=p["title"], level=0, extractor="知识检索", enterprise=name))
            record_citations([p["doc_id"]])
    else:
        raise ToolError(f"未知信息源：{source}", retryable=False)
    ctx.workspace["cards"] = cards
    return ToolResult(summary=f"{label}：采集证据卡片 {len(cards)} 张", data={"cards": cards}, level=0, metrics={"cards": len(cards), "source": source})


@registry.tool("merge_public_evidence", "汇总公开证据", "汇总各公开信息子智能体回传的证据卡片进入证据池", permission="T1", zone="platform")
def merge_public_evidence(ctx, **_) -> ToolResult:
    outputs = ctx.workspace.get("subagent_outputs", {}).get("public", [])
    pool = _cards(ctx)
    per_source = Counter()
    failed = []
    for out in outputs:
        if out["status"] != "completed":
            failed.append(out["title"])
            continue
        for card in (out["output"] or {}).get("cards", []):
            pool[card["id"]] = card
            per_source[out["title"]] += 1
    if failed:
        ctx.state["degrade_notes"].append(f"公开信息源不可用：{'、'.join(failed)}")
    summary = "公开证据入池：" + "，".join(f"{k} {v} 张" for k, v in per_source.items()) + (f"；失败：{'、'.join(failed)}" if failed else "")
    return ToolResult(summary=summary, data={"per_source": dict(per_source), "failed": failed}, metrics={"public_cards": sum(per_source.values())})


# ---------------------------------------------------------------- 私有材料处理（私域）
def _profile_cards(ent: dict) -> list[dict]:
    p = ent["profile"]
    name = ent["name"]
    out = []
    f = p.get("financials", {})

    def card(text: str, field: str, conf: float = 0.95) -> None:
        c = new_card(prefix="BIZ", source_type="business_data", source_name="客户档案（结构化）", source_ref=f"profile:{name}:{field}", text=text, position=field, level=2, extractor="结构化读取", zone="private", enterprise=name, confidence=conf)
        c["derived"] = True
        out.append(c)

    suppliers = p.get("suppliers", [])
    if suppliers:
        top = max(suppliers, key=lambda s: s["share"])
        card(f"第一大供应商采购占比{top['share'] * 100:.0f}%（{top['item']}，供应商所在国家{top['country']}）" + ("，属于单一供应商依赖" if top["share"] >= 0.5 else ""), "suppliers")
        foreign = [s for s in suppliers if s["country"] != "中国" and s["share"] >= 0.5]
        if foreign:
            card(f"关键物料{foreign[0]['item']}依赖进口，存在出口管制与断供风险", "suppliers.foreign", 0.85)
    team = p.get("team", {})
    if team.get("core_size"):
        ratio = team["left_last_year"] / team["core_size"]
        card(f"近一年核心技术人员离职{team['left_last_year']}人，占核心团队{ratio * 100:.1f}%", "team")
    eq = p.get("equipment", [])
    if eq:
        total = sum(e["value"] for e in eq)
        top = max(eq, key=lambda e: e["value"])
        card(f"关键设备共{len(eq)}台，最高价值设备{top['name']}占设备总值{top['value'] / total * 100:.0f}%" + ("，关键设备集中度高" if top["value"] / total >= 0.5 else ""), "equipment")
        recent = [e for e in eq if e["purchase_date"] >= "2025-09-01"]
        if recent:
            card(f"{len(recent)}台设备于{recent[0]['purchase_date'][:7]}前后集中购置，金额合计{format_amount(sum(e['value'] for e in recent))}", "equipment.recent", 0.9)
    if f:
        months = f["cash_reserve"] / max(f["monthly_fixed_cost"], 1)
        card(f"现金储备可支撑约{months:.1f}个月固定支出" + ("，现金流压力较大" if months < 12 else ""), "financials.cash")
        if f["net_profit"] < 0:
            card(f"2025年净利润为负（{format_amount(f['net_profit'])}），经营仍处于亏损", "financials.profit")
    sec = p.get("security", {})
    if sec:
        card(f"网络安全等级保护{sec.get('level')}级，数据资产规模档位{sec.get('data_scale')}级，近三年安全事件{sec.get('incidents')}起", "security")
    for m in p.get("milestones", []):
        if m["status"] == "延期":
            card(f"研发里程碑「{m['name']}」已延期（原计划{m['planned']}）", "milestones")
    return out


@registry.tool("process_private_materials", "私有材料处理", "在私域解析尽调与访谈材料，逐句抽取风险事实生成证据卡片，并读取客户档案的结构化事实", permission="T1", zone="private", max_level=3)
def process_private_materials(ctx, **_) -> ToolResult:
    ent = _ent(ctx)
    pool = _cards(ctx)
    mats = load_materials(ctx.inputs.get("material_ids") or None, enterprise_id=None if ctx.inputs.get("material_ids") else ent["id"])
    processed = ctx.workspace.setdefault("processed_materials", [])
    added, l3 = 0, 0
    for m in mats:
        if m["id"] in processed or not m["text"]:
            continue
        out = model_gateway.invoke("ra.extract_private_facts", {"text": m["text"], "doc_name": m["name"], "limit": 60}, ctx=ctx, data_level=3)
        for fact in out["facts"]:
            level = max(2, level_of_text(fact["text"], private=True))
            l3 += level >= 3
            c = new_card(prefix="PRV", source_type="private_material", source_name=m["name"], source_ref=f"material:{m['id']}", text=fact["text"], position=fact["position"], level=level, extractor="私域推理（材料抽取）", zone="private", enterprise=ent["name"], confidence=min(0.95, 0.55 + fact["score"] / 8), tags=fact.get("subtypes", []))
            pool[c["id"]] = c
            added += 1
        processed.append(m["id"])
    for c in _profile_cards(ent):
        pool[c["id"]] = c
        added += 1
    ctx.workspace["private_material_count"] = len(processed)
    return ToolResult(summary=f"私域处理材料 {len(mats)} 份，生成私有证据卡片 {added} 张（其中含高敏信息 {l3} 张，原文仅在私域保存）", data={"materials": [m["name"] for m in mats], "added": added}, level=3, metrics={"private_cards": added, "l3_cards": l3, "materials": len(mats)})


@registry.tool("prepare_egress", "出域准备与最小化", "对拟送云端推理的私有证据做最小化与脱敏预览，统计将命中的脱敏规则", permission="T0", zone="private", max_level=3)
def prepare_egress(ctx, **_) -> ToolResult:
    cards = [c for c in _cards(ctx).values() if c["level"] == 2]
    preview_vault = TokenVault(f"preview-{ctx.run_id}")
    report = EgressReport()
    samples = []
    for c in cards:
        out = privacy_gateway.desensitize_text(c["text"], preview_vault, report, "$.cards[].text")
        if len(samples) < 6 and out != c["text"]:
            samples.append({"card": c["id"], "before": truncate(c["text"], 80), "after": truncate(out, 80)})
    l3 = sum(1 for c in _cards(ctx).values() if c["level"] >= 3)
    ctx.workspace["egress_plan"] = {"cloud_cards": len(cards), "private_only_cards": l3, "rules": dict(report.rules_hit), "samples": samples}
    return ToolResult(summary=f"拟出域证据 {len(cards)} 张（仅事实陈述字段），高敏证据 {l3} 张保留在私域单独处理；预计命中脱敏规则 {sum(report.rules_hit.values())} 次", data=ctx.workspace["egress_plan"], level=1, metrics={"cloud_cards": len(cards), "private_only": l3})


# ---------------------------------------------------------------- 风险抽取与归一化
def _strip(c: dict) -> dict:
    return {"id": c["id"], "text": c["text"], "source_type": c["source_type"], "date": c.get("date")}


def _ent_brief(ent: dict) -> dict:
    return {"name": ent["name"], "track": ent["track"], "tech_field": ent["tech_field"], "products": [{"name": p["name"], "trl": p["trl"]} for p in ent["profile"].get("products", [])]}


@registry.tool("extract_risks", "风险抽取", "按风险分类体系从证据池抽取候选风险；没有合适类型或证据未被覆盖时创建新风险。L0–L2 证据脱敏后送云端推理，L3 证据在私域单独抽取", permission="T1", zone="follow", max_level=3, params={"scope": "all/new"})
def extract_risks(ctx, scope: str = "all", **_) -> ToolResult:
    ent = _ent(ctx)
    pool = _cards(ctx)
    seen = set(ctx.workspace.get("extracted_cards", []))
    cards = [c for c in pool.values() if scope == "all" or c["id"] not in seen]
    cloud_cards = [_strip(c) for c in cards if c["level"] <= 2]
    private_cards = [_strip(c) for c in cards if c["level"] >= 3]
    focus = ctx.inputs.get("focus_types") or []
    candidates = []
    if cloud_cards:
        out = model_gateway.invoke("ra.extract_risks", {"enterprise": _ent_brief(ent), "cards": cloud_cards, "focus": focus}, ctx=ctx, data_level=max(c["level"] for c in cards if c["level"] <= 2))
        candidates += out["candidates"]
    if private_cards:
        out = model_gateway.invoke("ra.extract_risks", {"enterprise": _ent_brief(ent), "cards": private_cards, "focus": focus}, ctx=ctx, data_level=3)
        candidates += out["candidates"]
    existing = ctx.workspace.setdefault("candidates", [])
    existing += candidates
    ctx.workspace["extracted_cards"] = sorted(seen | {c["id"] for c in cards})
    types = {c["subtype"].split("-")[0] for c in existing if c.get("subtype") in SUBTYPES}
    novel_n = sum(1 for c in candidates if c.get("novel"))
    summary = f"从 {len(cards)} 张证据中抽取候选风险 {len(candidates)} 条（云端推理 {len(cloud_cards)} 张证据、私域推理 {len(private_cards)} 张），累计覆盖一级类型 {len(types)} 个"
    if novel_n:
        summary += f"，其中新创建 {novel_n} 条"
    return ToolResult(summary=summary, data={"candidates": candidates}, level=1, metrics={"candidates": len(candidates), "types": len(types), "novel": novel_n})


@registry.tool("normalize_risks", "归一化与去重", "映射到二级子类型并合并重复风险", permission="T1", zone="private")
def normalize_risks(ctx, **_) -> ToolResult:
    candidates = ctx.workspace.get("candidates", [])
    by_sub: dict[str, dict] = {}
    merges = []
    for cand in candidates:
        code = cand.get("subtype") or ""
        novel = bool(cand.get("novel")) or code not in SUBTYPES
        title = str(cand.get("title") or cand.get("type_name") or "").strip()
        key = f"NX:{title}" if novel else code
        if not key or (novel and not title):
            continue
        if key in by_sub:
            target = by_sub[key]
            target["evidence_ids"] = list(dict.fromkeys(target["evidence_ids"] + cand["evidence_ids"]))
            target["strength"] = max(target["strength"], cand.get("strength", 0))
            target["cues"] = sorted(set(target.get("cues", [])) | set(cand.get("cues", [])))
            merges.append({"from": cand["title"], "into": target["title"], "reason": "同一新创建风险" if novel else "同一子类型"})
        else:
            item = dict(cand)
            item["novel"] = novel
            by_sub[key] = item
    keep = list(by_sub.values())
    keep.sort(key=lambda c: -c.get("strength", 0))
    novel_titles = sorted({str(c.get("title") or "").strip() for c in keep if c.get("novel")})
    nx_map = {title: f"NX-{i:03d}" for i, title in enumerate(novel_titles, start=1)}
    for cand in keep:
        if cand.get("novel"):
            cand["subtype"] = nx_map[str(cand.get("title") or "").strip()]
            cand["type_code"] = "NX"
            cand["type_name"] = cand.get("type_name") or "新创建风险"
    final: list[dict] = []
    for cand in keep:
        parent = "NX" if cand.get("novel") else cand["subtype"].split("-")[0]
        dup = next((f for f in final if ("NX" if f.get("novel") else f["subtype"].split("-")[0]) == parent and set(f["evidence_ids"]) == set(cand["evidence_ids"]) and cosine_tokens(f["description"], cand["description"]) > 0.8), None)
        if dup:
            merges.append({"from": cand["title"], "into": dup["title"], "reason": "证据完全相同且描述高度相似"})
            continue
        final.append(cand)
    prev = {r["subtype"]: r for r in ctx.workspace.get("risks", [])}
    risks = []
    for i, cand in enumerate(final, start=1):
        sub = subtype_record(cand["subtype"], cand)
        base = prev.get(cand["subtype"], {})
        novel = bool(cand.get("novel"))
        type_name = (cand.get("type_name") or sub["type_name"]) if novel else sub["type_name"]
        title = _sanitize_risk_title(cand.get("title"), type_name=type_name)
        risks.append(
            {
                "id": base.get("id") or f"R-{i:03d}",
                "subtype": cand["subtype"],
                "type_code": "NX" if novel else sub["type_code"],
                "type_name": type_name,
                "title": title,
                "description": cand["description"],
                "evidence_ids": cand["evidence_ids"],
                "cues": cand.get("cues", []),
                "strength": cand.get("strength", 0),
                "inferred": bool(cand.get("inferred")),
                "novel": novel,
                "origin_label": "新创建" if novel else None,
                "basis": cand.get("basis"),
                "allowed_stages": sub["stages"],
            }
        )
    used = {r["id"] for r in risks}
    counter = 1
    for r in risks:
        if r["id"] in used and list(x["id"] for x in risks).count(r["id"]) > 1:
            while f"R-{counter:03d}" in used:
                counter += 1
            r["id"] = f"R-{counter:03d}"
            used.add(r["id"])
    ctx.workspace["risks"] = risks
    ctx.workspace["merges"] = merges
    return ToolResult(summary=f"归一化后风险 {len(risks)} 条，合并重复 {len(merges)} 次", data={"merges": merges}, level=1, metrics={"risks": len(risks), "merged": len(merges)})


@registry.tool("classify_stages", "三阶段归类", "规则初判 + 证据确认，归类到研发、成果转化、产业化三阶段", permission="T1", zone="cloud", params={"strict": "是否加严"})
def classify_stages(ctx, strict: str = "0", **_) -> ToolResult:
    ent = _ent(ctx)
    pool = _cards(ctx)
    products = ent["profile"].get("products", [])
    trl = products[-1]["trl"] if products else None
    if products:
        trl = min(p["trl"] for p in products)
    payload = []
    for r in ctx.workspace.get("risks", []):
        sub = subtype_record(r.get("subtype"), r)
        texts = [pool[e]["text"] for e in r["evidence_ids"] if e in pool and pool[e]["level"] <= 2]
        if r.get("novel") or r.get("subtype") not in SUBTYPES:
            counts = {k: sum(1 for t in texts for w in words if w in t) for k, words in STAGE_WORDS.items()}
            rule_stage = max(counts, key=lambda k: counts[k]) if any(counts.values()) else "rd"
        else:
            rule_stage = RISK_TAXONOMY[sub["type_code"]]["stage"] if RISK_TAXONOMY[sub["type_code"]]["stage"] in sub["stages"] else sub["stages"][0]
        payload.append({"id": r["id"], "rule_stage": rule_stage, "allowed": sub["stages"], "evidence_texts": texts[:6], "product_trl": trl})
    out = model_gateway.invoke("ra.stage_confirm", {"risks": payload, "strict": strict}, ctx=ctx, data_level=2)
    decided = {s["id"]: s for s in out["stages"]}
    agree = 0
    records = []
    for r, p in zip(ctx.workspace.get("risks", []), payload):
        d = decided.get(r["id"], {"stage": p["rule_stage"], "involved": [p["rule_stage"]], "rationale": "规则判定"})
        stage = d["stage"] if d["stage"] in p["allowed"] else p["rule_stage"]
        r.update(stage=stage, stage_label=STAGES[stage], involved=d.get("involved", [stage]), rule_stage=p["rule_stage"], stage_rationale=d["rationale"])
        agree += stage == p["rule_stage"]
        records.append({"id": r["id"], "rule": p["rule_stage"], "final": stage, "consistent": stage == p["rule_stage"], "rationale": d["rationale"]})
    ctx.workspace["stage_records"] = records
    counts = Counter(r["stage"] for r in ctx.workspace.get("risks", []))
    return ToolResult(summary=f"阶段归类完成：研发 {counts.get('rd', 0)}、成果转化 {counts.get('transfer', 0)}、产业化 {counts.get('industrial', 0)}；规则与证据判断一致 {agree}/{len(records)}", data={"records": records}, level=1, metrics={"agreement": agree / max(1, len(records))})


def baseline(subtype: str, ent: dict, evidence_count: int) -> tuple[int, int, str]:
    p = ent["profile"]
    f = p.get("financials", {})
    na = max(f.get("net_assets", 1e8), 1.0)
    eq = p.get("equipment", [])
    sec = p.get("security", {})
    tcode = subtype.split("-")[0]
    lik, loss, note = 2, na * 0.02, ""
    if tcode == "R01":
        delays = sum(1 for m in p.get("milestones", []) if m["status"] == "延期")
        min_trl = min((x["trl"] for x in p.get("products", [])), default=6)
        lik = 2 + delays + (1 if min_trl <= 5 else 0)
        loss = f.get("rd_expense", na * 0.05) * 0.5
        note = f"里程碑延期 {delays} 个，最低技术成熟度 TRL{min_trl}"
    elif tcode == "R02":
        total = sum(e["value"] for e in eq) or 1
        conc = max((e["value"] for e in eq), default=0) / total
        lik = 2 + (1 if conc >= 0.5 else 0) + (1 if evidence_count >= 2 else 0)
        loss = max((e["value"] for e in eq), default=na * 0.02) + f.get("monthly_fixed_cost", 0) * 1.5
        note = f"最高价值设备占比 {conc * 100:.0f}%"
    elif tcode == "R03":
        min_trl = min((x["trl"] for x in p.get("products", [])), default=8)
        lik = 3 if 5 <= min_trl <= 7 else 2
        lik += 1 if evidence_count >= 2 else 0
        loss = f.get("rd_expense", na * 0.05) * 0.8
        note = f"成果转化相关产品最低 TRL{min_trl}"
    elif tcode == "R04":
        mature = any(x["trl"] >= 7 for x in p.get("products", []))
        lik = 2 + (1 if mature else 0) + (1 if evidence_count >= 2 else 0)
        loss = f.get("revenue", na) * 0.04
        note = "已有示范或量产产品" if mature else "尚未进入首台套或首批次应用"
    elif tcode == "R05":
        lik = 2 + min(2, evidence_count // 2)
        loss = max([3e7] if evidence_count else [na * 0.03])
        note = f"专利与知识产权证据 {evidence_count} 条"
    elif tcode == "R06":
        lik = 2 + min(2, evidence_count // 2)
        loss = na * 0.04
        note = f"商业秘密相关证据 {evidence_count} 条"
    elif tcode == "R07":
        lik = 1 + (1 if sec.get("data_scale", 1) >= 3 else 0) + (1 if sec.get("data_scale", 1) >= 5 else 0) + (1 if sec.get("incidents", 0) else 0) + (1 if sec.get("level", 2) <= 2 else 0)
        loss = 1.5e6 * (1.6 ** sec.get("data_scale", 1))
        note = f"等保 {sec.get('level')} 级，数据规模 {sec.get('data_scale')} 级，历史事件 {sec.get('incidents')} 起"
    elif tcode == "R08":
        mature = any(x["trl"] >= 7 for x in p.get("products", []))
        lik = 2 + (1 if mature else 0) + (1 if sec.get("incidents", 0) else 0)
        loss = f.get("revenue", na) * 0.03
        note = "智能产品已进入应用" if mature else "智能产品仍在研发或试验"
    elif tcode == "R09":
        lik = 2 + (1 if evidence_count >= 2 else 0)
        loss = f.get("revenue", na) * 0.05
        note = "生命科学产品质量、试验或疫苗相关证据"
    elif tcode == "R10":
        lik = 2 + (1 if evidence_count >= 2 else 0)
        loss = max(f.get("revenue", na) * 0.08, na * 0.05)
        note = "航空航天或低空运行标的"
    elif tcode == "R11":
        months = f.get("cash_reserve", 0) / max(f.get("monthly_fixed_cost", 1), 1)
        lik = 1 + (1 if months < 18 else 0) + (1 if months < 12 else 0) + (1 if f.get("net_profit", 0) < 0 else 0)
        loss = f.get("monthly_fixed_cost", na * 0.01) * 6
        note = f"现金可支撑 {months:.1f} 个月，通常不构成保险事故"
    return max(1, min(5, lik)), _sev_band(loss / na), note


def matches_stated_need(risk: dict, customer_need: str) -> bool:
    subtype = SUBTYPES.get(risk.get("subtype") or "")
    patterns = (subtype or {}).get("patterns") or []
    text = customer_need or ""
    if any(pattern and pattern in text for pattern in patterns):
        return True
    title = risk.get("title") or ""
    return bool(title) and title in text


def context_fields(risk: dict, pool: dict) -> dict:
    """可控性、趋势、置信度与管理建议：两条评分路径共用的确定性规则。

    需求描述项目没有财务基线，但这四项不依赖基线，仍然可以稳定给出，
    避免报告展开区出现空字段。
    """
    evidence = [pool[item] for item in risk.get("evidence_ids", []) if item in pool]
    novel = bool(risk.get("novel"))
    type_code = risk.get("type_code") or (risk.get("subtype") or "").split("-")[0]
    if novel or type_code == "NX":
        controllability = "中"
    elif type_code in ("R07", "R09", "R10"):
        controllability = "低"
    elif type_code in ("R04", "R05", "R08"):
        controllability = "中"
    else:
        controllability = "高"
    trend = "上升" if any((item.get("date") or "") >= "2026-01-01" for item in evidence) else "稳定"
    confidence = round(
        min(0.95, 0.45 + 0.1 * len(evidence) + (0.1 if any(item.get("source_type") == "business_data" for item in evidence) else 0))
        * (0.7 if risk.get("inferred") else 1.0),
        2,
    )
    advice = ADVICE.get(
        risk.get("subtype") or "",
        "新创建风险尚未纳入现行分类体系，需确认是否单列保障" if novel else "制定针对性的风险管理措施",
    )
    return {"controllability": controllability, "trend": trend, "confidence": confidence, "advice": advice}


def _score_need_risks(ctx) -> ToolResult:
    """需求描述项目由模型直接判定 1–5 分，不使用财务或类型基线。"""
    customer_need = str(ctx.inputs.get("customer_need") or "")
    pool = _cards(ctx)
    risks = ctx.workspace.get("risks", [])
    scorable = []
    for risk in risks:
        evidence = [pool[item] for item in risk.get("evidence_ids", []) if item in pool]
        texts = [card.get("text") or "" for card in evidence if card.get("level", 0) <= 2]
        if texts or risk.get("description"):
            scorable.append((risk, texts[:6]))
        else:
            risk.update(likelihood=None, severity=None, score=None, level=None, adjust_reason="没有客户原话或证据，未判定分数", demand_status="待确认")
    if scorable:
        payload = [{"id": risk["id"], "subtype": risk.get("subtype"), "title": risk.get("title"), "description": risk.get("description"), "inferred": bool(risk.get("inferred")), "evidence_texts": texts} for risk, texts in scorable]
        try:
            judged = model_gateway.invoke("ra.direct_scores", {"customer_need": customer_need, "risks": payload}, ctx=ctx, data_level=1)
        except (ModelUnavailable, ValueError, TypeError, KeyError):
            judged = None
        by_id = {item["id"]: item for item in (judged or {}).get("scores", [])}
        for risk, _texts in scorable:
            item = by_id.get(risk["id"])
            if not item:
                risk.update(likelihood=None, severity=None, score=None, level=None, adjust_reason="模型未给出可接受的直接分数", demand_status="明确诉求" if matches_stated_need(risk, customer_need) else ("推断" if risk.get("inferred") else "系统识别"))
                continue
            likelihood, severity = int(item["likelihood"]), int(item["severity"])
            score = likelihood * severity
            risk.update(likelihood=likelihood, severity=severity, score=score, level=_level_label(score), adjust_reason=item.get("reason") or "模型直接判定", score_basis="排序分数，不是发生概率")
    for risk in risks:
        stated = matches_stated_need(risk, customer_need)
        risk["demand_status"] = "明确诉求" if stated else ("推断" if risk.get("inferred") else risk.get("demand_status") or "系统识别")
        risk["stated_by_user"] = stated
        names = [LINES[line]["name"] for line in (SUBTYPES.get(risk.get("subtype") or {}) or {}).get("lines") or [] if line in LINES]
        if names:
            risk["line_names"] = names
        risk.update(context_fields(risk, pool))
    ctx.workspace["score_records"] = [{"id": risk["id"], "likelihood": risk.get("likelihood"), "severity": risk.get("severity"), "demand_status": risk.get("demand_status")} for risk in risks]
    levels = Counter(risk.get("level") for risk in risks if risk.get("level"))
    return ToolResult(summary=f"需求描述项目完成直接评分：极高 {levels.get('极高', 0)}、高 {levels.get('高', 0)}、中 {levels.get('中', 0)}、低 {levels.get('低', 0)}；未判定 {sum(1 for risk in risks if risk.get('likelihood') is None)} 条", data={"records": ctx.workspace["score_records"]}, level=1, metrics=dict(levels))


@registry.tool("score_risks", "风险量化评分", "指标规则基线分（私域）+ 依据证据不超过 1 分的调整（脱敏后云端）；需求描述项目改为模型直接判定", permission="T1", zone="follow", params={"strict": "是否要求每次调整说明证据"})
def score_risks(ctx, strict: str = "0", **_) -> ToolResult:
    if ctx.inputs.get("mode") == "need":
        return _score_need_risks(ctx)
    ent = _ent(ctx)
    pool = _cards(ctx)
    risks = ctx.workspace.get("risks", [])
    payload = []
    for r in risks:
        ev = [pool[e] for e in r["evidence_ids"] if e in pool]
        lik, sev, note = baseline(r["subtype"], ent, len(ev))
        r.update(baseline_l=lik, baseline_s=sev, indicator_note=note)
        payload.append({"id": r["id"], "subtype": r["subtype"], "baseline_l": lik, "baseline_s": sev, "evidence_texts": [e["text"] for e in ev if e["level"] <= 2][:6], "evidence_dates": [e.get("date") for e in ev], "inferred": r.get("inferred", False)})
    out = model_gateway.invoke("ra.adjust_scores", {"risks": payload, "strict": strict}, ctx=ctx, data_level=2)
    adj = {a["id"]: a for a in out["adjustments"]}
    records = []
    for r in risks:
        a = adj.get(r["id"], {"dl": 0, "ds": 0, "reason": "未调整"})
        dl = max(-1, min(1, int(a.get("dl", 0))))
        ds = max(-1, min(1, int(a.get("ds", 0))))
        if strict == "1" and not r["evidence_ids"]:
            dl = min(dl, 0)
        lik = max(1, min(5, r["baseline_l"] + dl))
        sev = max(1, min(5, r["baseline_s"] + ds))
        score = lik * sev
        r.update(
            likelihood=lik,
            severity=sev,
            score=score,
            level=_level_label(score),
            adjust_reason=a.get("reason", ""),
            **context_fields(r, pool),
        )
        records.append({"id": r["id"], "baseline": [r["baseline_l"], r["baseline_s"]], "adjust": [dl, ds], "final": [lik, sev], "reason": r["adjust_reason"], "indicator": r["indicator_note"]})
    ctx.workspace["score_records"] = records
    levels = Counter(r["level"] for r in risks)
    return ToolResult(summary=f"完成 {len(risks)} 条风险量化：极高 {levels.get('极高', 0)}、高 {levels.get('高', 0)}、中 {levels.get('中', 0)}、低 {levels.get('低', 0)}", data={"records": records}, level=2, metrics=dict(levels))


@registry.tool("build_risk_graph", "构建企业风险图谱", "抽取实体与关系，计算风险传导路径与关键节点", permission="T1", zone="private", max_level=3)
def build_risk_graph(ctx, **_) -> ToolResult:
    ent = _ent(ctx)
    p = ent["profile"]
    pool = _cards(ctx)
    g = nx.DiGraph()
    eid = f"ENT:{ent['name']}"
    g.add_node(eid, type="企业", label=ent["name"], level=0)
    for st, label in STAGES.items():
        g.add_node(f"STAGE:{st}", type="生命周期阶段", label=label, level=0)
    for tech in ent["tech_field"].split("、"):
        g.add_node(f"TECH:{tech}", type="技术", label=tech, level=0)
        g.add_edge(eid, f"TECH:{tech}", rel="拥有")
    product_nodes = []
    for prod in p.get("products", []):
        pid = f"PROD:{prod['name']}"
        product_nodes.append(pid)
        stage = "rd" if prod["trl"] <= 4 else ("transfer" if prod["trl"] <= 7 else "industrial")
        g.add_node(pid, type="科技产品", label=prod["name"], trl=prod["trl"], level=1)
        g.add_edge(eid, pid, rel="拥有")
        g.add_edge(pid, f"STAGE:{stage}", rel="处于")
        for tech in ent["tech_field"].split("、"):
            g.add_edge(pid, f"TECH:{tech}", rel="依赖")
    for pat in get_source().patents(ent["name"]):
        nid = f"PAT:{pat['no']}"
        g.add_node(nid, type="专利", label=pat["title"], status=pat["status"], level=0)
        g.add_edge(eid, nid, rel="拥有")
    for e in p.get("equipment", []):
        nid = f"EQ:{e['serial']}"
        g.add_node(nid, type="关键设备", label=e["name"], value=e["value"], level=2)
        g.add_edge(eid, nid, rel="拥有")
        for pid in product_nodes:
            g.add_edge(pid, nid, rel="依赖")
    for s in p.get("suppliers", []):
        nid = f"SUP:{s['item']}"
        g.add_node(nid, type="供应商", label=f"{s['item']}供应商（{s['country']}）", share=s["share"], level=2)
        for pid in product_nodes:
            g.add_edge(pid, nid, rel="依赖", weight=s["share"])
    for m in p.get("team", {}).get("core_roles", []):
        nid = f"ROLE:{m['role']}"
        g.add_node(nid, type="人员角色", label=m["role"], level=1)
        for pid in product_nodes:
            g.add_edge(pid, nid, rel="依赖")
    risk_nodes = {}
    for r in ctx.workspace.get("risks", []):
        rid = f"RISK:{r['id']}"
        risk_nodes[r["subtype"]] = rid
        g.add_node(rid, type="风险事件", label=r["title"], level_label=r.get("level"), score=r.get("score"), risk_id=r["id"], level=1)
        g.add_node(f"TYPE:{r['type_code']}", type="风险类型", label=r["type_name"], level=0)
        g.add_edge(rid, f"TYPE:{r['type_code']}", rel="属于")
        g.add_edge(rid, f"STAGE:{r.get('stage', 'rd')}", rel="属于")
        targets = product_nodes or [eid]
        for pid in targets:
            g.add_edge(pid, rid, rel="暴露于")
        tc = r["type_code"]
        anchors = {"R02": "EQ:", "R05": "PAT:", "R06": "PAT:", "R01": "ROLE:"}
        if tc in anchors:
            for node in [n for n in g.nodes if n.startswith(anchors[tc])][:3]:
                g.add_edge(node, rid, rel="导致")
        r["graph_node"] = rid
    for src_type, dst_sub in TRANSMISSION:
        dst = risk_nodes.get(dst_sub)
        if not dst:
            continue
        for sub, node in risk_nodes.items():
            if sub.startswith(src_type) and node != dst:
                g.add_edge(node, dst, rel="导致")
    paths = []
    sources = [n for n in g.nodes if n.startswith(("SUP:", "EQ:", "ROLE:"))]
    sinks = [risk_nodes[s] for s in ("R01-3", "R07-5", "R08-3", "R10-2") if s in risk_nodes]
    for src in sources:
        for dst in sinks:
            for path in nx.all_simple_paths(g, src, dst, cutoff=4):
                paths.append({"nodes": path, "labels": [g.nodes[n]["label"] for n in path]})
                if len(paths) >= 12:
                    break
    centrality = nx.betweenness_centrality(g.to_undirected())
    key_nodes = sorted(((n, c) for n, c in centrality.items() if not n.startswith(("STAGE:", "TYPE:"))), key=lambda t: -t[1])[:6]
    graph = {
        "nodes": [{"id": n, **{k: v for k, v in d.items()}} for n, d in g.nodes(data=True)],
        "edges": [{"source": u, "target": v, **d} for u, v, d in g.edges(data=True)],
        "paths": paths,
        "key_nodes": [{"id": n, "label": g.nodes[n]["label"], "centrality": round(c, 3)} for n, c in key_nodes],
        "stats": {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(), "paths": len(paths)},
    }
    ctx.workspace["graph"] = graph
    return ToolResult(summary=f"风险图谱：节点 {graph['stats']['nodes']}、关系 {graph['stats']['edges']}、传导路径 {len(paths)} 条；关键节点：{'、'.join(k['label'] for k in graph['key_nodes'][:3])}", data=graph, level=2, metrics=graph["stats"])


@registry.tool("assess_lifecycle", "全生命周期评估", "判定科技产品当前子阶段并做前瞻风险评估", permission="T1", zone="cloud")
def assess_lifecycle(ctx, **_) -> ToolResult:
    ent = _ent(ctx)
    products = [{"name": p["name"], "trl": p["trl"], "description": p["description"]} for p in ent["profile"].get("products", [])]
    if ctx.inputs.get("products"):
        products += ctx.inputs["products"]
    risks = [{"id": r["id"], "subtype": r["subtype"], "stage": r.get("stage")} for r in ctx.workspace.get("risks", [])]
    out = model_gateway.invoke("ra.lifecycle", {"products": products, "risks": risks, "track": ent["track"]}, ctx=ctx, data_level=1)
    ctx.workspace["lifecycle"] = out["products"]
    return ToolResult(summary="；".join(f"{p['name']}当前处于「{p['current_phase_name']}」（{'；'.join(p['basis'])}）" for p in out["products"]), data=out, level=1, metrics={"products": len(out["products"])})


@registry.tool("analyze_insurability", "可保性分析与险种映射", "按可保性准则判断风险是否可保并映射建议险种与保障要点；按企业地区、行业等条件过滤不适用险种", permission="T1", zone="cloud")
def analyze_insurability(ctx, **_) -> ToolResult:
    groups = defaultdict(list)
    elig_ctx = enterprise_context(ctx.workspace.get("enterprise") or {})
    eligibility_notes: list[str] = []
    for r in ctx.workspace.get("risks", []):
        sub = subtype_record(r.get("subtype"), r)
        ins = sub["insurable"]
        reasons = []
        raw_lines = [] if r.get("novel") else list(sub["lines"])
        filtered = filter_lines(raw_lines, elig_ctx, replace=True)
        lines = filtered.eligible
        if r.get("novel"):
            ins = "partial"
            reasons.append("新创建风险尚未纳入现行分类体系，可保性与险种待确认")
        elif ins == "yes":
            reasons.append("偶然性与损失可度量均满足")
        elif ins == "partial":
            reasons.append("需通过列明原因、第三方认定或自担比例等方式可保化")
        else:
            reasons.append("属于经营或商业风险，不满足偶然性或可分散性")
        if r.get("level") in ("极高",) and ins != "no" and not r.get("novel"):
            reasons.append("风险值极高，承保时需设置较高免赔或分项限额")
        if filtered.notes:
            reasons.extend(filtered.notes)
            eligibility_notes.extend(filtered.notes)
        if raw_lines and not lines and ins != "no":
            reasons.append("现有映射险种均不符合企业地区或行业条件，险种待人工确认")
        r.update(
            insurability=ins,
            insurability_label=INSURABILITY_LABEL[ins],
            insurability_reason="；".join(reasons),
            lines=lines,
            line_names=[LINES[l]["name"] for l in lines if l in LINES],
            lines_rejected=[{"line": c.line, "name": LINES.get(c.line, {}).get("name", c.line), "reasons": c.reasons} for c in filtered.rejected],
            lines_replaced=filtered.replacements,
        )
        groups[ins].append(r["id"])
    need = defaultdict(list)
    for r in ctx.workspace.get("risks", []):
        for l in r.get("lines", []):
            need[l].append(r["id"])
    mapping = [{"line": l, "name": LINES[l]["name"], "stage": LINES[l]["stage"], "regions": LINES[l].get("regions", []), "industry": LINES[l].get("industry", "通用"), "risk_ids": ids, "max_score": max(next(x["score"] for x in ctx.workspace["risks"] if x["id"] == i) for i in ids)} for l, ids in need.items() if l in LINES]
    mapping.sort(key=lambda m: -m["max_score"])
    ctx.workspace["insurability"] = {
        "groups": dict(groups),
        "line_needs": mapping,
        "eligibility": {"context": elig_ctx, "notes": list(dict.fromkeys(eligibility_notes))},
    }
    note = f"；资格过滤 {len(eligibility_notes)} 项" if eligibility_notes else ""
    return ToolResult(summary=f"可保 {len(groups['yes'])} 条、部分可保 {len(groups['partial'])} 条、不可保 {len(groups['no'])} 条；建议险种：{'、'.join(m['name'] for m in mapping[:6])}{note}", data=ctx.workspace["insurability"], level=1, metrics={k: len(v) for k, v in groups.items()})


def _coverage(ctx) -> list[dict]:
    risks = ctx.workspace.get("risks", [])
    out = []
    for code, rt in RISK_TAXONOMY.items():
        items = [r for r in risks if r["type_code"] == code]
        if items:
            status = "identified"
            label = f"已识别 {len(items)} 条"
        elif code in ctx.workspace.get("supplement_requested", []):
            status = "need_material"
            label = "需补充材料"
        else:
            status = "no_evidence"
            label = "未发现证据"
        out.append({"code": code, "name": rt["name"], "status": status, "label": label, "count": len(items), "inferred": sum(1 for r in items if r.get("inferred"))})
    return out


@registry.tool("compose_risk_report", "生成风险评估报告", "汇总风险清单、类型覆盖、三阶段矩阵、热力矩阵、数据缺口并撰写报告摘要", permission="T1", zone="cloud")
def compose_risk_report(ctx, **_) -> ToolResult:
    ent = _ent(ctx)
    risks = sorted(ctx.workspace.get("risks", []), key=lambda r: -r.get("score", 0))
    coverage = _coverage(ctx)
    stage_counts = Counter(r.get("stage") for r in risks)
    matrix = {code: {st: {"count": 0, "max_level": None} for st in STAGES} for code in RISK_TAXONOMY}
    order = ["低", "中", "高", "极高"]
    for r in risks:
        if r.get("type_code") not in matrix:
            continue
        cell = matrix[r["type_code"]][r.get("stage", "rd")]
        cell["count"] += 1
        level = r.get("level")
        if level in order and (cell["max_level"] is None or order.index(level) > order.index(cell["max_level"])):
            cell["max_level"] = level
    heat = [[[] for _ in range(5)] for _ in range(5)]
    for r in risks:
        if not isinstance(r.get("likelihood"), int) or not isinstance(r.get("severity"), int):
            continue
        heat[r["likelihood"] - 1][r["severity"] - 1].append(r["id"])
    gaps = []
    if not ctx.workspace.get("private_material_count"):
        gaps.append("未提供私有尽调材料，私有维度风险以客户档案与推断为主")
    for c in coverage:
        if c["status"] != "identified":
            gaps.append(f"{c['name']}：{c['label']}，建议补充{('知识产权诉讼与许可资料' if c['code'] == 'R04' else '相关经营与技术资料')}")
    for note in ctx.state.get("degrade_notes", []):
        gaps.append(note)
    high = sum(1 for r in risks if r["level"] in ("高", "极高"))
    overall = "极高" if any(r["level"] == "极高" for r in risks) else ("高" if high >= 3 else ("中" if high else "低"))
    novel_count = sum(1 for r in risks if r.get("novel"))
    if novel_count:
        gaps.append(f"新创建风险 {novel_count} 条尚未纳入现行分类体系，需人工确认类型与可保性")
    stats = {"total": len(risks), "types_covered": sum(1 for c in coverage if c["status"] == "identified"), "high": high, "stage_counts": dict(stage_counts), "overall_level": overall, "inferred": sum(1 for r in risks if r.get("inferred")), "novel": novel_count}
    # 结论文本只传展示字段，避免模型把子类型/险种编码写进摘要
    top_for_report = [
        {
            "title": r.get("title"),
            "level": r.get("level"),
            "score": r.get("score"),
            "description": r.get("description"),
            "stage_label": r.get("stage_label") or STAGES.get(r.get("stage"), r.get("stage")),
            "insurability_label": r.get("insurability_label"),
            "line_names": r.get("line_names") or [],
            "advice": r.get("advice"),
            "type_name": r.get("type_name"),
        }
        for r in risks[:5]
    ]
    out = model_gateway.invoke(
        "ra.report",
        {"enterprise": {"name": ent["name"], "track": ent["track"], "tech_field": ent["tech_field"]}, "stats": stats, "top_risks": top_for_report, "gaps": gaps},
        ctx=ctx,
        data_level=2,
    )
    # 内置引擎不走 llm postprocess，这里统一清洗编码并回填脱敏真值
    from ...privacy.narrative import finalize_narrative

    out = finalize_narrative(out if isinstance(out, dict) else {}, ctx.vault)
    statements = {st: f"{label}阶段暂未识别到显著风险（依据：现有证据中未出现该阶段的风险信号，建议在进入该阶段前复评）" for st, label in STAGES.items() if not stage_counts.get(st)}
    report = {**out, "stats": stats, "coverage": coverage, "matrix": matrix, "heatmap": heat, "gaps": gaps, "stage_statements": statements}
    ctx.workspace["report"] = report
    return ToolResult(summary=out["summary"], data=report, level=2, metrics={"types_covered": stats["types_covered"], "total": stats["total"]})


# ---------------------------------------------------------------- 反思修正与冷启动工具
@registry.tool("targeted_retrieval", "定向补充检索", "针对未覆盖的风险类型扩大时间范围与信息源进行定向检索", permission="T0", zone="cloud", params={"types": "风险类型编号，逗号分隔"})
def targeted_retrieval(ctx, types: str = "", **_) -> ToolResult:
    ent = _ent(ctx)
    wanted = [t for t in (types or "").split(",") if t] or [c["code"] for c in _coverage(ctx) if c["status"] != "identified"]
    pool = _cards(ctx)
    ds = get_source()
    added = 0
    corpus = [(n["text"], n.get("date"), n["source_ref"], "public_news") for n in ds.news(ent["name"], "2018-01-01")]
    corpus += [(c["text"], c["date"], c["source_ref"], "public_court") for c in ds.court(ent["name"])]
    for code in wanted:
        patterns = [p for sub in RISK_TAXONOMY[code]["subtypes"].values() for p in sub["patterns"]]
        for text, date, ref, st in corpus:
            if any(p in text for p in patterns):
                c = new_card(prefix="TGT", source_type=st, source_name="定向检索", source_ref=ref, text=text, date=date, level=0, extractor="定向检索", enterprise=ent["name"])
                if c["id"] not in pool:
                    pool[c["id"]] = c
                    added += 1
        hits = knowledge_index.search(f"{ent['track']} {RISK_TAXONOMY[code]['name']}", kbs=["K05", "K06"], max_level=1, top_k=2)
        for h in hits:
            c = new_card(prefix="KB", source_type="knowledge", source_name=h["title"], source_ref=f"kb:{h['doc_id']}", text=truncate(h["text"], 200), level=0, extractor="知识检索", enterprise=ent["name"], confidence=0.6)
            c["kb_context"] = True
            ctx.workspace.setdefault("kb_context", {})[code] = h["text"]
    return ToolResult(summary=f"定向检索 {len(wanted)} 个风险类型，新增证据 {added} 张", data={"types": wanted, "added": added}, level=0, metrics={"added": added})


@registry.tool("infer_missing_risks", "第一性原理推断", "对仍无证据的风险类型按第一性原理分解推断潜在风险并标注为推断", permission="T1", zone="cloud", params={"min_types": "最少覆盖类型数"})
def infer_missing_risks(ctx, min_types: str = "5", **_) -> ToolResult:
    ent = _ent(ctx)
    coverage = _coverage(ctx)
    covered = [c for c in coverage if c["status"] == "identified"]
    need = max(0, int(min_types) - len(covered))
    prior = INDUSTRY_PROFILES.get(ent["track"], {}).get("high_incidence", [])
    target_lines = [l for l in (ctx.inputs.get("target_lines") or []) if l in LINES]
    target_type_codes = set()
    for _code, rt in RISK_TAXONOMY.items():
        for sub in rt["subtypes"].values():
            if any(line in sub.get("lines", []) for line in target_lines):
                target_type_codes.add(_code)
    candidates = [c["code"] for c in coverage if c["status"] != "identified"]
    candidates.sort(key=lambda code: (0 if code in target_type_codes else 1, not any(p.startswith(code) for p in prior), code))
    prefer = [code for code in candidates if code in target_type_codes]
    fallback_n = max(need, sum(1 for code in candidates if any(p.startswith(code) for p in prior)) if ctx.state.get("start_mode") == "cold" else need)
    targets = (prefer + [c for c in candidates if c not in prefer])[: max(fallback_n, len(prefer) or 0)]
    if not targets:
        return ToolResult(summary="风险类型覆盖已满足要求，无需推断", data={"inferred": []}, level=1)
    out = model_gateway.invoke("ra.infer_risks", {"track": ent["track"], "products": ent["profile"].get("products", []), "missing_types": targets, "knowledge": ctx.workspace.get("kb_context", {})}, ctx=ctx, data_level=1)
    pool = _cards(ctx)
    for item in out["risks"]:
        c = new_card(prefix="INF", source_type="model", source_name="第一性原理推断", source_ref=f"inferred:{item['subtype']}", text=item["basis"], level=0, extractor="推断", enterprise=ent["name"], confidence=0.4)
        pool[c["id"]] = c
        ctx.workspace.setdefault("candidates", []).append({"subtype": item["subtype"], "title": item["title"], "description": item["description"], "evidence_ids": [c["id"]], "cues": [], "strength": 0.5, "inferred": True, "basis": item["basis"]})
    return ToolResult(summary=f"为 {len(targets)} 个无证据风险类型生成推断风险：{'、'.join(i['title'] for i in out['risks'])}（均标注为推断）", data=out, level=1, metrics={"inferred": len(out["risks"])})


@registry.tool("recheck_evidence", "证据回检", "回到原始来源核验风险引用的证据，删除不实引用，无证据的风险改标为推断", permission="T1", zone="private")
def recheck_evidence(ctx, **_) -> ToolResult:
    pool = _cards(ctx)
    removed, marked = 0, 0
    for r in ctx.workspace.get("risks", []):
        valid = [e for e in r["evidence_ids"] if e in pool and verify_card(pool[e])]
        removed += len(r["evidence_ids"]) - len(valid)
        r["evidence_ids"] = valid
        if not any(pool[e]["source_type"] != "model" for e in valid):
            if not r.get("inferred"):
                marked += 1
            r["inferred"] = True
    return ToolResult(summary=f"证据回检：删除不实引用 {removed} 处，改标推断 {marked} 条", data={"removed": removed, "marked": marked}, level=1, metrics={"removed": removed, "marked": marked})


# ---------------------------------------------------------------- 赛道级汇总
@registry.tool("aggregate_track", "赛道风险汇总", "汇总样本企业评估结果：共性风险、差异风险与赛道风险画像", permission="T1", zone="cloud")
def aggregate_track(ctx, **_) -> ToolResult:
    outputs = ctx.workspace.get("subagent_outputs", {}).get("enterprises", [])
    done = [o for o in outputs if o["status"] == "completed" and o["output"]]
    if not done:
        raise ToolError("没有可汇总的样本企业评估结果", retryable=False)
    freq = Counter()
    by_sub = defaultdict(list)
    all_cards = {}
    for o in done:
        data = o["output"].get("data", {})
        for r in data.get("risks", []):
            freq[r["subtype"]] += 1
            by_sub[r["subtype"]].append({**r, "enterprise": data.get("enterprise", {}).get("name")})
        all_cards.update(data.get("evidence", {}))
    n = len(done)
    common, distinct = [], []
    risks = []
    for i, (sub, cnt) in enumerate(freq.most_common(), start=1):
        items = by_sub[sub]
        rep = max(items, key=lambda r: r.get("score", 0))
        rate = cnt / n
        merged = {**rep, "id": f"R-{i:03d}", "occurrence": round(rate, 2), "enterprises": sorted({r["enterprise"] for r in items}), "evidence_ids": list(dict.fromkeys(e for r in items for e in r["evidence_ids"]))[:8]}
        merged["likelihood"] = max(1, min(5, round(sum(r["likelihood"] for r in items) / cnt + (1 if rate >= 0.6 else 0))))
        merged["severity"] = max(1, min(5, round(sum(r["severity"] for r in items) / cnt)))
        merged["score"] = merged["likelihood"] * merged["severity"]
        merged["level"] = _level_label(merged["score"])
        merged["description"] = f"{n} 家样本企业中有 {cnt} 家存在该风险（出现率 {rate * 100:.0f}%）。代表性情景：{rep['description']}"
        risks.append(merged)
        (common if rate >= 0.6 else distinct).append(merged["id"])
    ctx.workspace["evidence"] = all_cards
    ctx.workspace["risks"] = risks
    ctx.workspace["track_summary"] = {"samples": [o["output"].get("data", {}).get("enterprise", {}).get("name") for o in done], "common": common, "distinct": distinct, "per_enterprise": [{"name": o["output"]["data"]["enterprise"]["name"], "risks": len(o["output"]["data"].get("risks", [])), "high": sum(1 for r in o["output"]["data"].get("risks", []) if r["level"] in ("高", "极高")), "overall": o["output"]["data"].get("report", {}).get("stats", {}).get("overall_level")} for o in done]}
    first = done[0]["output"]["data"]
    ctx.workspace["lifecycle"] = first.get("lifecycle", [])
    ctx.workspace["graph"] = first.get("graph", {})
    return ToolResult(summary=f"汇总 {n} 家样本企业：共性风险 {len(common)} 条、差异风险 {len(distinct)} 条", data=ctx.workspace["track_summary"], level=1, metrics={"samples": n, "common": len(common)})


def json_tasks(tasks: list[dict]) -> str:
    return json.dumps(tasks, ensure_ascii=False)
