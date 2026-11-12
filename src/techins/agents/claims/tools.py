"""定损理赔智能体的工具。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select

from ...analytics import causal as C
from ...analytics import detection as D
from ...analytics import vision
from ...analytics.fraud import fraud_models
from ...db import session_scope
from ...harness.background import background_manager
from ...harness.tools import ToolError, ToolResult, registry
from ...knowledge.catalog import CAUSAL_TEMPLATES, CLAIM_FRAUD_PATTERNS, CLAIM_MATERIALS, FAULT_DEGREES, LINES, SERVICE_PROVIDERS, family_of, has_family
from ...llm.gateway import model_gateway
from ...models import Alert, Artifact, ClaimCase, Incident, Material, PriceItem, TelemetryPoint
from ...textutil import format_amount, parse_amounts
from ..common import load_policy_bundle, policy_doc_params
from .reasoning import EXCLUSION_RULES, KIND_LABELS, TRIGGER_RULES, find_time

INCIDENT_LINES = {"equipment_damage": ["RD08", "RD11", "RD09", "RD12", "RB01", "RB02"], "lab_fire": ["RD09", "RD10", "RD08"], "data_breach": ["CY01"], "product_liability": ["AI01", "AI02", "TR03", "TR04", "LS01", "LS02", "LS03"], "ip_infringement": ["IP04", "IP06", "IP08", "IP09", "IP03", "IP10"]}
RELEVANT_KEYWORDS = {"equipment_damage": ["设备", "机器", "物质损失", "损坏", "中断", "仪器"], "lab_fire": ["火灾", "物质损失", "设备", "中断"], "data_breach": ["数据", "网络", "信息", "系统", "隐私"], "product_liability": ["产品", "第三者", "缺陷"], "ip_infringement": ["知识产权", "专利", "侵权"]}
IRRELEVANT_TRIGGERS = {"equipment_damage": ("data_event",), "lab_fire": ("data_event",), "data_breach": ("physical_loss", "subject_listed"), "product_liability": ("data_event", "subject_listed"), "ip_infringement": ("data_event", "physical_loss", "subject_listed")}
PHYSICAL_CATEGORIES = ("机械部件", "电气部件", "真空与射频", "光学部件")
CATEGORY_FAMILY = {"设备修理": "equipment", "设备更换": "equipment", "数据与样品重建": "equipment", "研发中断持续费用": "rd_cost", "施救费用": "施救", "事件响应与法律费用": "cyber", "营业中断": "cyber", "第三者赔偿": "liability", "罚款": "除外", "赎金": "除外"}


def _line_for(category: str, policy_lines: list[str]) -> str:
    family = CATEGORY_FAMILY.get(category, "equipment")
    if family in ("除外", "施救"):
        return family
    matched = next((line for line in policy_lines if family_of(line) == family), None)
    if matched:
        return matched
    if family == "liability":
        matched = next((line for line in policy_lines if family_of(line) in ("quality", "life")), None)
        if matched:
            return matched
    return policy_lines[0] if policy_lines else family


def _amount(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    vals = parse_amounts(str(v or ""))
    if vals:
        return float(vals[0])
    m = re.search(r"[\d,.]+", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else 0.0


def _num(v) -> float:
    m = re.search(r"[\d.]+", str(v or ""))
    return float(m.group(0)) if m else 0.0


def _ws(ctx, key, default=None):
    return ctx.workspace.get(key, default)


def _docs(ctx) -> dict:
    return ctx.workspace["parsed"]["docs"]


def _incident_type(ctx) -> str:
    rep = ctx.workspace["claim"]["report"]
    if rep.get("incident_type") in INCIDENT_LINES:
        return rep["incident_type"]
    text = (rep.get("description") or "") + " ".join(d.get("text", "")[:200] for d in ctx.workspace.get("materials", []))
    if any(k in text for k in ("泄露", "勒索", "入侵", "攻击", "外传")):
        return "data_breach"
    if any(k in text for k in ("火灾", "水浸", "起火")):
        return "lab_fire"
    return "equipment_damage"


# ================================================================ 1 读取案件
@registry.tool("load_claim", "读取案件与保单", "读取理赔案件、保单与条款版本、预报案数据包、理赔材料与承保时的标的照片", permission="T0", zone="platform")
def load_claim(ctx, **_) -> ToolResult:
    with session_scope() as s:
        case = s.get(ClaimCase, ctx.inputs["claim_id"])
        if case is None:
            raise ToolError("理赔案件不存在", retryable=False)
        mats = s.scalars(select(Material).where(Material.owner_type == "claim", Material.owner_id == case.id)).all()
        inc = s.get(Incident, case.incident_id) if case.incident_id else None
        alert = s.get(Alert, inc.alert_id) if inc and inc.alert_id else None
        prior = s.scalars(select(ClaimCase).where(ClaimCase.policy_id == case.policy_id, ClaimCase.id != case.id, ClaimCase.status.in_(["approved", "closed", "paying"]))).all()
        ctx.workspace["claim"] = {"id": case.id, "claim_no": case.claim_no, "report": case.report or {}, "parties": case.parties or [], "claimed_amount": case.claimed_amount, "incident_id": case.incident_id, "created_at": case.created_at.isoformat(), "paid_before": sum(p.approved_amount or 0 for p in prior)}
        ctx.workspace["incident"] = {"id": inc.id, "incident_no": inc.incident_no, "occurred_at": inc.occurred_at.isoformat(), "response": inc.response or {}} if inc else None
        ctx.workspace["alert"] = {"id": alert.id, "alert_no": alert.alert_no, "level": alert.level, "title": alert.title, "sim_time": (alert.evidence or {}).get("sim_time"), "created_at": alert.created_at.isoformat(), "handling": alert.handling} if alert else None
        ctx.workspace["materials"] = [{"id": m.id, "name": m.name, "type": m.material_type, "path": m.file_path, "mime": m.mime, "text": m.text or "", "level": m.data_level, "created_at": m.created_at.isoformat()} for m in mats]
        if not ctx.eval_mode:
            case.status = "assessing"
        policy_id = case.policy_id
    b = load_policy_bundle(policy_id)
    ctx.workspace["bundle"] = b
    with session_scope() as s:
        app_id = (b.get("application") or {}).get("id")
        uw = s.scalars(select(Material).where(Material.owner_type == "application", Material.owner_id == app_id, Material.mime.like("image%"))).all() if app_id else []
        ctx.workspace["uw_photos"] = [{"id": m.id, "name": m.name, "path": m.file_path} for m in uw]
    itype = _incident_type(ctx)
    ctx.workspace["incident_type"] = itype
    ctx.workspace["lines"] = [l for l in INCIDENT_LINES[itype] if l in b["product"]["lines"]] or b["product"]["lines"][:1]
    dw = ctx.workspace["claim"]["report"].get("data_window")
    return ToolResult(summary=f"案件 {ctx.workspace['claim']['claim_no']}：保单 {b['policy']['policy_no']}（{b['product']['name']} V{b['product']['version']}），事故类型 {itype}，涉及险种 {'、'.join(LINES[l]['name'] for l in ctx.workspace['lines'])}；理赔材料 {len(mats)} 份" + ("；附预报案数据包" if dw else "") + (f"；承保照片 {len(ctx.workspace['uw_photos'])} 张" if ctx.workspace["uw_photos"] else ""), level=2)


@registry.tool("check_claim_materials", "理赔材料清单核对", "按险种理赔材料清单核对必要材料，缺失项生成补充材料建议", permission="T0", zone="private")
def check_claim_materials(ctx, **_) -> ToolResult:
    required = list(CLAIM_MATERIALS["通用"]["required"])
    optional = list(CLAIM_MATERIALS["通用"]["optional"])
    fallback = {"required": ["事故认定文件", "损失计算说明", "费用或损害凭证"], "optional": ["第三方鉴定报告", "合同或权属证明", "法律费用凭证"]}
    for l in ctx.workspace["lines"]:
        mats = CLAIM_MATERIALS.get(l) or fallback
        required += [m for m in mats.get("required", []) if m not in required]
        optional += [m for m in mats.get("optional", []) if m not in optional]
    mats = ctx.workspace["materials"]
    result = []
    for name in required + optional:
        key = name.replace("照片", "").replace("设备损坏", "损坏")
        match = next((m for m in mats if name in m["name"] or name in m["type"] or (key and key in m["name"]) or (name == "设备损坏照片" and m["mime"].startswith("image") and "损" in m["name"])), None)
        if name == "预报案数据包" and ctx.workspace["claim"]["report"].get("data_window"):
            match = {"name": "预报案数据包（风险预警移交）"}
        if name in ("物联网设备数据", "服务器日志") and ctx.workspace["claim"]["report"].get("data_window"):
            match = {"name": "数据窗口（私域）"}
        if name == "承保时设备照片" and ctx.workspace.get("uw_photos"):
            match = {"name": f"承保照片 {len(ctx.workspace['uw_photos'])} 张"}
        result.append({"material": name, "required": name in required, "status": "provided" if match else "missing", "file": match["name"] if match else None})
    missing = [r["material"] for r in result if r["required"] and r["status"] == "missing"]
    ctx.workspace["material_check"] = result
    ctx.workspace["missing_critical"] = missing
    return ToolResult(summary=f"必要材料 {len(required)} 项，已提供 {len(required) - len(missing)} 项" + (f"；缺失：{'、'.join(missing)}（生成补充材料建议）" if missing else "") + f"；可选材料与系统数据已提供 {sum(1 for r in result if not r['required'] and r['status'] == 'provided')} 项", data=result, level=1, metrics={"missing": len(missing)})


# ================================================================ 2 材料解析
@registry.tool("parse_claim_materials", "理赔材料解析", "私域多模态模型解析理赔文件、客户声明与照片：材料分类、关键要素与表格行抽取、声明要点、照片损伤识别", permission="T1", zone="private", max_level=3)
def parse_claim_materials(ctx, **_) -> ToolResult:
    docs: dict = {}
    photos = []
    statement = {}
    for m in ctx.workspace["materials"]:
        if m["mime"].startswith("image"):
            img = vision.load(m["path"])
            info = model_gateway.invoke("cl.image_damage", {"path": m["path"], "name": m["name"], "_images": [vision.to_data_url(m["path"])]}, ctx=ctx, data_level=3)
            meta = vision.metadata(img)
            photos.append({"material_id": m["id"], "name": m["name"], **info, "meta": {k: v for k, v in meta.items() if k in ("serial", "part", "captured_at", "equipment", "width", "height")}, "phash": vision.phash(img), "ela": vision.ela(img), "assessment": vision.assess_damage(img)})
        elif m["text"]:
            out = model_gateway.invoke("cl.extract_claim_doc", {"name": m["name"], "text": m["text"]}, ctx=ctx, data_level=2)
            out["material_id"] = m["id"]
            out["name"] = m["name"]
            out["label"] = KIND_LABELS.get(out["kind"], out["kind"])
            docs[out["kind"] if out["kind"] not in docs else f"{out['kind']}_{m['id']}"] = out
            if out["kind"] == "statement":
                statement = model_gateway.invoke("cl.statement_points", {"text": m["text"]}, ctx=ctx, data_level=2)
                statement["material_id"] = m["id"]
    ctx.workspace["parsed"] = {"docs": docs, "photos": photos, "statement": statement}
    confs = [d["confidence"] for d in docs.values()] + [p.get("confidence", 0.8) for p in photos]
    avg = sum(confs) / len(confs) if confs else 0
    low = [d["name"] for d in docs.values() if d["confidence"] < 0.9]
    ctx.workspace["parse_confidence"] = round(avg, 3)
    ctx.workspace["low_confidence"] = low
    return ToolResult(summary=f"解析文档 {len(docs)} 份（{'、'.join(sorted({d['label'] for d in docs.values()}))}）、照片 {len(photos)} 张；声明要点：出险时间 {statement.get('occurred_at') or '未识别'}" + (f"；低置信度材料 {len(low)} 份" if low else "") + f"；平均置信度 {avg:.2f}", data={"kinds": list(docs), "photos": len(photos)}, level=3, metrics={"confidence": round(avg, 3)})


# ================================================================ 3 条款结构化读取
@registry.tool("load_clause_elements", "条款要素读取", "从产品库按保单版本读取涉及险种的保险责任触发条件、责任免除、免赔与限额等要素", permission="T0", zone="platform")
def load_clause_elements(ctx, **_) -> ToolResult:
    b = ctx.workspace["bundle"]
    lines = ctx.workspace["lines"]
    params = {d["doc_id"]: d for d in policy_doc_params(b)}
    docs = []
    for d in b["package"].get("docs", []):
        if not set(d.get("lines", [])) & set(lines):
            continue
        liab = [a for ch in d.get("chapters", []) if ch["key"] == "liability" for a in ch["articles"]]
        excl = [a for ch in d.get("chapters", []) if ch["key"] == "exclusion" for a in ch["articles"]]
        itype = ctx.workspace["incident_type"]
        relevant = [a for a in liab if any(k in a["title"] + a["text"][:120] for k in RELEVANT_KEYWORDS[itype])] or liab
        triggers, seen = [], set()
        for a in relevant:
            for kw, kind, label in TRIGGER_RULES:
                if kind in IRRELEVANT_TRIGGERS.get(itype, ()):
                    continue
                if kw in a["text"] and kind not in seen:
                    seen.add(kind)
                    triggers.append({"kind": kind, "text": label, "article": f"{d['name']}{a['no']}", "quote": a["text"][:160]})
        exclusions, seen_ex = [], set()
        for a in excl:
            for kw, kind, label in EXCLUSION_RULES:
                if kw in a["text"] and kind not in seen_ex:
                    seen_ex.add(kind)
                    exclusions.append({"kind": kind, "title": label, "article": f"{d['name']}{a['no']}", "quote": a["text"][:160]})
        docs.append({"doc_id": d["doc_id"], "name": d["name"], "lines": d["lines"], "articles": [{"no": a["no"], "title": a["title"], "text": a["text"][:300]} for a in relevant], "triggers": triggers, "exclusions": exclusions, "params": params.get(d["doc_id"], {}).get("params", d.get("params", {})), "settlement": [a["text"][:200] for ch in d.get("chapters", []) if ch["key"] == "settlement" for a in ch["articles"]][:3]})
    if not docs:
        raise ToolError("保单条款中没有与事故类型对应的险种", retryable=False)
    ctx.workspace["clause"] = {"docs": docs}
    return ToolResult(summary="条款要素：" + "；".join(f"{d['name']}（触发条件 {len(d['triggers'])} 项、责任免除 {len(d['exclusions'])} 项、免赔 {format_amount(float(d['params'].get('deductible') or 0))}、限额 {format_amount(float(d['params'].get('limit') or 0))}）" for d in docs), level=1)


# ================================================================ 4 条款核查
def _build_facts(ctx) -> list[dict]:
    facts: list[dict] = []

    def add(text: str, source: str, tags: list[str], time: str | None = None, material_id: int | None = None) -> str:
        fid = f"F-{len(facts) + 1}"
        facts.append({"id": fid, "text": text, "source": source, "tags": tags, "time": time, "material_id": material_id})
        return fid

    st = ctx.workspace["parsed"]["statement"]
    docs = _docs(ctx)
    if st.get("occurred_at"):
        add(f"客户声明出险时间 {st['occurred_at']}，地点 {st.get('place') or '未说明'}", "客户事故声明", ["timeline", "occurred"], st["occurred_at"], st.get("material_id"))
    if st.get("process"):
        add(f"客户陈述经过：{st['process'][:160]}", "客户事故声明", ["statement"], None, st.get("material_id"))
    if st.get("objects"):
        add(f"客户声明的受损对象：{'、'.join(st['objects'])}", "客户事故声明", ["subject_listed"], None, st.get("material_id"))
    for m in st.get("mitigation", []):
        add(f"客户陈述施救措施：{m}", "客户事故声明", ["mitigation"], None, st.get("material_id"))
    for kind in ("inspection", "forensic", "certificate"):
        d = docs.get(kind)
        if d:
            concl = d["fields"].get("鉴定结论") or d["fields"].get("结论") or d["fields"].get("取证结论") or next(iter(d["fields"].values()), "")
            add(f"{d['label']}结论：{concl}", d["name"], ["third_party", "cause"] + (["subject_listed"] if kind == "forensic" else []), None, d["material_id"])
            for k, v in d["fields"].items():
                if any(x in k for x in ("时间", "始于", "开始")):
                    tm = find_time(v)
                    if tm:
                        add(f"{d['label']}记载 {k}：{v}", d["name"], ["timeline", "third_party"], tm, d["material_id"])
    mt = docs.get("maintenance")
    if mt:
        delay = any(k in json_text(mt) for k in ("推迟", "未执行", "延期"))
        add(f"维保记录：{'；'.join(f'{k}：{v}' for k, v in list(mt['fields'].items())[:4])}", mt["name"], ["maintenance"] + (["maintenance_delay"] if delay else []), None, mt["material_id"])
    inv = docs.get("invoice")
    if inv:
        add(f"购置发票：{'；'.join(f'{k}：{v}' for k, v in list(inv['fields'].items())[:5])}", inv["name"], ["invoice", "subject_listed"], None, inv["material_id"])
    for p in ctx.workspace["parsed"]["photos"]:
        if p.get("damages"):
            cap = p["meta"].get("captured_at")
            cap = (cap.replace(" ", "T") + ":00")[:19] if cap and len(cap) == 16 else (cap.replace(" ", "T") if cap else None)
            add(f"照片「{p['name']}」显示{p.get('part') or '受损部位'}：" + "、".join(f"{d['type']}（{d['grade']}）" for d in p["damages"]), "理赔照片", ["physical_loss", "image"], cap, p["material_id"])
    if ctx.workspace.get("alert"):
        a = ctx.workspace["alert"]
        add(f"风险预警 {a['alert_no']}（{a['level']}）：{a['title']}，发布于仿真时刻 {a.get('sim_time')}", "风险预警记录", ["timeline", "alert"], a.get("sim_time"))
    rl = docs.get("rd_ledger")
    if rl:
        add(f"研发项目台账：{'；'.join(f'{k}：{v}' for k, v in list(rl['fields'].items())[:5])}", rl["name"], ["interruption"], None, rl["material_id"])
    bi = docs.get("bi_proof")
    if bi:
        add(f"业务中断证明：{'；'.join(f'{k}：{v}' for k, v in list(bi['fields'].items())[:5])}", bi["name"], ["interruption"], None, bi["material_id"])
    return facts


def json_text(doc: dict) -> str:
    return " ".join(f"{k}{v}" for k, v in doc.get("fields", {}).items()) + " ".join(" ".join(str(x) for x in r.values()) for r in doc.get("rows", []))


def _flags(ctx) -> dict:
    docs = _docs(ctx)
    ent = ctx.workspace["bundle"]["enterprise"]
    serials = {e["serial"] for e in ent["profile"].get("equipment", [])}
    st = ctx.workspace["parsed"]["statement"]
    objs = set(st.get("objects", [])) | {p["meta"].get("serial") for p in ctx.workspace["parsed"]["photos"] if p["meta"].get("serial")}
    third = " ".join(json_text(docs[k]) for k in ("inspection", "forensic", "certificate") if k in docs)
    flags: dict = {}
    dev_objs = {o for o in objs if re.match(r"^[A-Z0-9]+-", o or "")}
    if ctx.workspace["incident_type"] in ("equipment_damage", "lab_fire"):
        flags["subject_listed"] = (bool(dev_objs & serials) if dev_objs else None)
    else:
        flags["subject_listed"] = True if any(o in ("核心业务系统", "数据库", "服务器", "客户数据") for o in objs) or "核心业务系统" in third else None
    if any(k in third for k in ("突发", "突然", "断裂", "抱死", "攻击", "入侵", "意外")):
        flags["accidental"] = True
    elif "故意" in third:
        flags["accidental"] = False
    else:
        flags["accidental"] = None
    flags["physical_loss"] = True if any(p.get("damages") for p in ctx.workspace["parsed"]["photos"]) or "损坏" in third else (None if ctx.workspace["incident_type"] != "data_breach" else False)
    flags["interruption"] = True if ("rd_ledger" in docs or "bi_proof" in docs) else None
    flags["data_event"] = True if "forensic" in docs else (None if ctx.workspace["incident_type"] == "data_breach" else False)
    wear_only = re.search(r"(自然磨损|正常磨损)[^。；]*?(所致|导致)", third) and not flags["accidental"]
    flags["ex_wear"] = True if wear_only else (None if "磨损" in third else False)
    mt = docs.get("maintenance")
    delay = bool(mt) and any(k in json_text(mt) for k in ("推迟", "未执行", "延期"))
    flags["maintenance_delay"] = delay
    flags["ex_maintenance"] = None if delay else False
    flags["ex_intent"] = False
    flags["ex_war"] = False
    items = " ".join(" ".join(str(v) for v in r.values()) for r in docs.get("loss_list", {}).get("rows", []))
    flags["ex_ransom"] = True if "赎金" in items else False
    flags["ex_fine"] = True if "罚款" in items or "罚金" in items else False
    flags["ex_indirect"] = True if "利润" in items and ctx.workspace["incident_type"] != "data_breach" else False
    return flags


@registry.tool("verify_clause_coverage", "条款核查", "私域抽取事实、脱敏后云域逐项推理：责任触发、责任免除、期间、标的一致性、义务履行、免赔与限额、比例赔付、其他保险", permission="T0", zone="follow", max_level=2)
def verify_clause_coverage(ctx, **_) -> ToolResult:
    facts = _build_facts(ctx)
    flags = _flags(ctx)
    ctx.workspace["facts"] = facts
    ctx.workspace["flags"] = flags
    b = ctx.workspace["bundle"]
    claim = ctx.workspace["claim"]
    st = ctx.workspace["parsed"]["statement"]
    checks = []
    per_doc = []
    for d in ctx.workspace["clause"]["docs"]:
        out = model_gateway.invoke("cl.clause_check", {"doc": d["name"], "trigger_conditions": d["triggers"], "exclusions": d["exclusions"], "facts": [{"id": f["id"], "text": f["text"], "tags": f["tags"]} for f in facts], "flags": flags}, ctx=ctx, data_level=2)
        per_doc.append({"doc_id": d["doc_id"], "name": d["name"], **out})
    trig_all = [t for p in per_doc for t in p["triggers"]]
    excl_all = [e for p in per_doc for e in p["exclusions"]]
    trig_concl = "不满足" if any(t["conclusion"] == "不满足" for t in trig_all) else ("待证实" if any(t["conclusion"] == "待证实" for t in trig_all) else "满足")
    applied = [e for e in excl_all if e["conclusion"] == "适用"]
    pending = [e for e in excl_all if e["conclusion"] == "待证实"]
    ex_concl = "适用" if applied else ("待证实" if pending else "不适用")
    checks.append({"item": "保险责任触发", "conclusion": trig_concl, "clause_ref": "；".join(f"{t['condition']}" for t in trig_all[:4]), "facts": sorted({f for t in trig_all for f in t["facts"]}), "note": "；".join(f"{t['condition']}：{t['conclusion']}" for t in trig_all)})
    checks.append({"item": "责任免除", "conclusion": ex_concl, "clause_ref": "；".join(e["title"] for e in excl_all[:6]), "facts": sorted({f for e in excl_all for f in e["facts"]}), "note": "；".join(f"{e['title']}：{e['conclusion']}（{e['reason']}）" for e in applied + pending) or "均不适用"})
    occurred = st.get("occurred_at") or claim["report"].get("occurred_at") or ""
    start, end = datetime.fromisoformat(b["policy"]["start_date"]), datetime.fromisoformat(b["policy"]["end_date"])
    try:
        occ = datetime.fromisoformat(occurred[:19])
        in_period = start <= occ <= end
        claims_made = any(LINES.get(l, {}).get("trigger") == "claims_made" for l in ctx.workspace["lines"])
        reported = datetime.fromisoformat(claim["created_at"][:19])
        note = f"出险 {occ:%Y-%m-%d %H:%M}，保险期间 {start:%Y-%m-%d} 至 {end:%Y-%m-%d}" + (f"；索赔发生制，索赔时间 {reported:%Y-%m-%d}" if claims_made else "")
        checks.append({"item": "保险期间", "conclusion": "在期内" if in_period and (not claims_made or start <= reported <= end + timedelta(days=60)) else "不在期内", "clause_ref": "保险期间条款", "facts": [f["id"] for f in facts if "occurred" in f["tags"]], "note": note})
    except ValueError:
        occ = None
        checks.append({"item": "保险期间", "conclusion": "待证实", "clause_ref": "保险期间条款", "facts": [], "note": "出险时间无法识别"})
    claimant = _docs(ctx).get("claim_form", {}).get("fields", {}).get("被保险人") or b["enterprise"]["name"]
    same = claimant.strip() == b["enterprise"]["name"]
    subj = flags.get("subject_listed")
    checks.append({"item": "标的与被保险人一致性", "conclusion": "一致" if same and subj else ("不一致" if (not same or subj is False) else "待证实"), "clause_ref": "保险标的条款", "facts": [f["id"] for f in facts if "subject_listed" in f["tags"]], "note": f"索赔人 {claimant}；受损对象{'为' if subj else ('不在' if subj is False else '待核实是否为')}保险单列明标的"})
    rep_at = st.get("reported_at") or claim["created_at"]
    timely = None
    if occ is not None and rep_at:
        try:
            gap_h = (datetime.fromisoformat(rep_at[:19]) - occ).total_seconds() / 3600
            timely = gap_h <= 48
        except ValueError:
            timely = None
    mitig = bool(st.get("mitigation")) or bool((ctx.workspace.get("incident") or {}).get("response", {}).get("response"))
    checks.append({"item": "义务履行", "conclusion": "已履行" if timely and mitig else ("未履行（影响说明）" if timely is False or not mitig else "待证实"), "clause_ref": "投保人、被保险人义务", "facts": [f["id"] for f in facts if "mitigation" in f["tags"]], "note": ("出险通知及时（48 小时内）" if timely else ("出险通知超过 48 小时，未及时通知导致无法确定的损失部分不承担" if timely is False else "通知时间待核实")) + ("；已采取施救措施" if mitig else "；未见施救措施记录")})
    nums = "；".join(f"{d['name']}：免赔 {format_amount(float(d['params'].get('deductible') or 0))}、限额 {format_amount(float(d['params'].get('limit') or 0))}" + (f"、自担 {float(d['params'].get('coinsurance') or 0):.0%}" if d["params"].get("coinsurance") else "") + (f"、等待期 {d['params'].get('waiting_days')} 天" if d["params"].get("waiting_days") else "") for d in ctx.workspace["clause"]["docs"])
    checks.append({"item": "免赔与限额", "conclusion": "已确定", "clause_ref": "保险金额与免赔额", "facts": [], "note": nums + f"；本保单已赔付 {format_amount(claim['paid_before'])}"})
    ratio = 1.0
    equipment_doc = next((d for d in ctx.workspace["clause"]["docs"] if has_family(d["lines"], "equipment")), None)
    if equipment_doc:
        value = sum(e["value"] for e in b["enterprise"]["profile"].get("equipment", []))
        limit = float(equipment_doc["params"].get("limit") or 0)
        ratio = min(1.0, limit / value) if value else 1.0
        checks.append({"item": "比例赔付", "conclusion": f"{ratio:.0%}", "clause_ref": "赔偿处理（不足额投保）", "facts": [], "note": f"保险金额 {format_amount(limit)}，列明设备重置价值合计 {format_amount(value)}，" + ("足额投保" if ratio >= 1 else f"不足额投保，按 {ratio:.0%} 比例赔偿")})
    else:
        checks.append({"item": "比例赔付", "conclusion": "不适用", "clause_ref": "赔偿处理", "facts": [], "note": "费用与责任类险种不适用不足额比例赔付"})
    other = _docs(ctx).get("claim_form", {}).get("fields", {}).get("其他保险", "无")
    checks.append({"item": "其他保险", "conclusion": "否" if other in ("无", "否", "") else "是", "clause_ref": "重复保险分摊", "facts": [], "note": f"索赔申请书申报其他保险：{other}"})
    ctx.workspace["checks"] = checks
    ctx.workspace["clause_detail"] = per_doc
    ctx.workspace["proportion"] = ratio
    pend = [c["item"] for c in checks if c["conclusion"] == "待证实"]
    return ToolResult(summary="条款核查：" + "；".join(f"{c['item']} {c['conclusion']}" for c in checks) + (f"；待证实 {len(pend)} 项" if pend else ""), data=checks, level=2, metrics={"pending": len(pend)})


# ================================================================ 5 证据分析
@registry.tool("analyze_evidence", "证据分析与时间线", "证据链完整性、真实性（篡改检测、外部核验）、一致性与时间线重建（时钟对齐）、矛盾点、理赔欺诈复核", permission="T0", zone="private", max_level=3)
def analyze_evidence(ctx, **_) -> ToolResult:
    docs = _docs(ctx)
    facts = ctx.workspace["facts"]
    b = ctx.workspace["bundle"]
    st = ctx.workspace["parsed"]["statement"]
    completeness = {"required": [r["material"] for r in ctx.workspace["material_check"] if r["required"]], "missing": ctx.workspace["missing_critical"]}
    authenticity = []
    for p in ctx.workspace["parsed"]["photos"]:
        authenticity.append({"object": p["name"], "check": "图像篡改检测（错误级别分析）", "result": "可疑" if p["ela"]["suspicious"] else "未见异常", "detail": f"得分 {p['ela']['score']}"})
    inv = docs.get("invoice")
    if inv:
        serial = inv["fields"].get("序列号") or next((v for k, v in inv["fields"].items() if "序列" in k), "")
        eq = next((e for e in b["enterprise"]["profile"].get("equipment", []) if e["serial"] == serial.strip()), None)
        amt = _amount(inv["fields"].get("金额") or inv["fields"].get("价税合计"))
        ok = bool(eq) and abs(amt - eq["value"]) <= 0.05 * eq["value"]
        authenticity.append({"object": inv["name"], "check": "发票外部核验（与承保设备清单比对）", "result": "一致" if ok else "不一致", "detail": f"序列号 {serial}，金额 {format_amount(amt)}" + (f"，承保登记价值 {format_amount(eq['value'])}" if eq else "，未在承保设备清单中")})
    for kind in ("inspection", "forensic"):
        d = docs.get(kind)
        if d:
            issuer = d["fields"].get("鉴定机构") or d["fields"].get("出具机构") or d["fields"].get("取证机构") or ""
            known = any(sp["name"] in issuer or issuer in sp["name"] for sp in SERVICE_PROVIDERS if issuer)
            authenticity.append({"object": d["name"], "check": "出具机构核验（约定服务商目录）", "result": "已核验" if known else "待核验", "detail": issuer or "未载明出具机构"})
    # 时间线：统一时钟后排序；物联网网关时钟偏差按鉴定报告记载或默认 0 校正
    skew_min = 0.0
    for d in docs.values():
        for k, v in d["fields"].items():
            if "时钟偏差" in k or "时钟" in k:
                m = re.search(r"([+-]?\d+)\s*分钟", v)
                if m:
                    skew_min = float(m.group(1))
    events = []
    for f in facts:
        if f.get("time"):
            events.append({"time": f["time"][:19], "event": f["text"], "source": f["source"], "fact": f["id"]})
    scope_pts = _iot_events(ctx, skew_min)
    for e in scope_pts:
        fid = f"F-{len(facts) + 1}"
        facts.append({"id": fid, "text": e["event"], "source": e["source"], "tags": ["timeline", "iot"], "time": e["time"], "material_id": None})
        events.append({**e, "fact": fid})
    for stp in ((ctx.workspace.get("incident") or {}).get("response", {}).get("response") or {}).get("steps", []):
        events.append({"time": stp["due"][:19], "event": f"预案步骤时限：{stp['title']}（{stp['status']}）", "source": "事故响应记录", "fact": None})
    events.sort(key=lambda e: e["time"])
    contradictions = []
    stated = st.get("occurred_at")
    failure = next((e for e in scope_pts if e.get("kind") == "failure"), None)
    if stated and failure:
        gap = abs((datetime.fromisoformat(stated[:19]) - datetime.fromisoformat(failure["time"])).total_seconds()) / 3600
        if gap > 2:
            contradictions.append({"item": "出险时间", "detail": f"客户声明 {stated[:16]} 与物联网数据识别的故障时刻 {failure['time'][:16]} 相差 {gap:.1f} 小时", "severity": "high" if gap > 12 else "medium"})
    degrade = next((e for e in scope_pts if e.get("kind") == "degradation"), None)
    if degrade and any(k in (st.get("process") or "") for k in ("运行正常", "无异常", "一直正常")):
        contradictions.append({"item": "事故前设备状态", "detail": f"客户声称事故前运行正常，但物联网数据显示 {degrade['time'][:16]} 起振动已持续上升", "severity": "medium"})
    for p in ctx.workspace["parsed"]["photos"]:
        cap = p["meta"].get("captured_at")
        if cap and stated and cap[:10] < stated[:10]:
            contradictions.append({"item": f"照片拍摄时间：{p['name']}", "detail": f"拍摄于 {cap}，早于声明出险时间 {stated[:10]}", "severity": "high"})
    fraud = _claim_fraud(ctx, contradictions, degrade)
    ctx.workspace["evidence_analysis"] = {"completeness": completeness, "authenticity": authenticity, "timeline": events, "clock_skew_minutes": skew_min, "contradictions": contradictions, "fraud": fraud}
    return ToolResult(summary=f"证据分析：必要材料缺失 {len(completeness['missing'])} 项；真实性核验 {len(authenticity)} 项（可疑或不一致 {sum(1 for a in authenticity if a['result'] in ('可疑', '不一致'))} 项）；时间线 {len(events)} 个事件（时钟校正 {skew_min:+.0f} 分钟）；矛盾点 {len(contradictions)} 个；理赔欺诈概率 {fraud['probability']:.1%}" + (f"，命中 {'、'.join(p['code'] for p in fraud['patterns'])}" if fraud["patterns"] else ""), data={"contradictions": contradictions, "fraud": fraud}, level=2, metrics={"contradictions": len(contradictions)})


def _window(ctx) -> tuple[datetime, datetime] | None:
    st = ctx.workspace["parsed"]["statement"]
    occ = st.get("occurred_at") or ctx.workspace["claim"]["report"].get("occurred_at")
    if not occ:
        return None
    o = datetime.fromisoformat(occ[:19])
    return o - timedelta(days=5), o + timedelta(days=3)


def _series(policy_id: int, metric: str, asset: str | None, start: datetime, end: datetime) -> tuple[list[datetime], np.ndarray]:
    with session_scope() as s:
        q = select(TelemetryPoint.ts, TelemetryPoint.value).where(TelemetryPoint.policy_id == policy_id, TelemetryPoint.metric == metric, TelemetryPoint.ts >= start, TelemetryPoint.ts <= end)
        if asset:
            q = q.where(TelemetryPoint.asset == asset)
        rows = s.execute(q.order_by(TelemetryPoint.ts)).all()
    return [r[0] for r in rows], np.array([r[1] for r in rows], float)


def _target_asset(ctx) -> str | None:
    st = ctx.workspace["parsed"]["statement"]
    serials = {e["serial"] for e in ctx.workspace["bundle"]["enterprise"]["profile"].get("equipment", [])}
    for o in st.get("objects", []):
        if o in serials:
            return o
    for p in ctx.workspace["parsed"]["photos"]:
        if p["meta"].get("serial") in serials:
            return p["meta"]["serial"]
    return None


def _iot_events(ctx, skew_min: float) -> list[dict]:
    win = _window(ctx)
    if win is None:
        return []
    pid = ctx.workspace["bundle"]["policy"]["id"]
    out = []
    if ctx.workspace["incident_type"] in ("equipment_damage", "lab_fire"):
        asset = _target_asset(ctx)
        if not asset:
            return []
        ts, vib = _series(pid, "vibration_rms", asset, *win)
        _, cur = _series(pid, "motor_current", asset, *win)
        if len(vib) < 30:
            return []
        shift = timedelta(minutes=skew_min)
        cs = D.cusum(vib)
        if cs.get("change") and cs.get("index") is not None:
            out.append({"time": (ts[cs["index"]] - shift).isoformat(), "event": f"物联网数据：{asset} 振动均值出现上升变点（累积和检测）", "source": "物联网数据（已校正时钟）", "kind": "degradation"})
        stop = next((i for i in range(1, len(cur)) if cur[i] < 1 and cur[i - 1] > 5), None)
        peak = int(np.argmax(vib))
        if stop is not None:
            out.append({"time": (ts[max(0, stop - 1)] - shift).isoformat(), "event": f"物联网数据：{asset} 振动峰值 {vib[peak]:.1f} mm/s 后运行电流归零，设备停机", "source": "物联网数据（已校正时钟）", "kind": "failure"})
    elif ctx.workspace["incident_type"] == "data_breach":
        ts, lf = _series(pid, "login_failures", "核心业务系统", *win)
        _, eg = _series(pid, "egress_mb", "核心业务系统", *win)
        if len(lf) < 30:
            return []
        z_lf = (lf - np.median(lf)) / (np.median(np.abs(lf - np.median(lf))) * 1.4826 or 1)
        z_eg = (eg - np.median(eg)) / (np.median(np.abs(eg - np.median(eg))) * 1.4826 or 1)
        i1 = next((i for i, z in enumerate(z_lf) if z > 6), None)
        i2 = next((i for i, z in enumerate(z_eg) if z > 6), None)
        if i1 is not None:
            out.append({"time": ts[i1].isoformat(), "event": f"服务器日志：登录失败次数激增至 {lf[i1]:.0f} 次/小时", "source": "服务器日志", "kind": "intrusion"})
        if i2 is not None:
            out.append({"time": ts[i2].isoformat(), "event": f"服务器日志：数据外传量骤增至 {eg[i2]:.0f} MB/小时", "source": "服务器日志", "kind": "failure"})
    return out


def _claim_fraud(ctx, contradictions: list[dict], degrade: dict | None) -> dict:
    b = ctx.workspace["bundle"]
    st = ctx.workspace["parsed"]["statement"]
    hits = []
    occ = st.get("occurred_at") or ctx.workspace["claim"]["report"].get("occurred_at")
    if occ:
        o = datetime.fromisoformat(occ[:19])
        s0, e0 = datetime.fromisoformat(b["policy"]["start_date"]), datetime.fromisoformat(b["policy"]["end_date"])
        if 0 <= (o - s0).days < 15 or 0 <= (e0 - o).days < 15:
            hits.append({"code": "C01", "evidence": f"出险时间 {o:%Y-%m-%d} 距保单起止日不足 15 天"})
    claimed = ctx.workspace["claim"]["claimed_amount"] or st.get("claimed_amount") or 0
    est = (ctx.workspace["claim"]["report"].get("loss_range") or [0, 0])[1]
    if est and claimed > 1.5 * est:
        hits.append({"code": "C02", "evidence": f"报损 {format_amount(claimed)} 超过预报案估损上限 {format_amount(est)} 的 1.5 倍"})
    with session_scope() as s:
        others = s.scalars(select(Material).where(Material.owner_type == "claim", Material.owner_id != ctx.inputs["claim_id"], Material.mime.like("image%"))).all()
        other_hash = [(o.owner_id, vision.phash(vision.load(o.file_path))) for o in others if o.file_path]
    for p in ctx.workspace["parsed"]["photos"]:
        if any(vision.hamming(p["phash"], h) <= 6 for _, h in other_hash):
            hits.append({"code": "C03", "evidence": f"照片「{p['name']}」与其他理赔案件照片高度相似"})
    old = _old_damage(ctx)
    for o in old:
        if o["result"]["old_damage"]:
            hits.append({"code": "C04", "evidence": f"照片「{o['photo']}」中的{'、'.join(o['result']['old_damage_types'])}在承保照片中已存在"})
    if degrade and ctx.workspace.get("alert") is None:
        hits.append({"code": "C05", "evidence": "事故前设备已出现退化趋势，但无预警处置与报告记录"})
    elif degrade and ctx.workspace.get("alert") and not any(h.get("action") not in ("发布", "升级", "启动事故响应") for h in ctx.workspace["alert"].get("handling") or []):
        hits.append({"code": "C05", "evidence": f"事故前已有预警 {ctx.workspace['alert']['alert_no']}，未见客户处置记录"})
    feats = {"si_to_value": 1.0, "recent_purchase_share": 0.0, "employees": b["enterprise"]["employees"], "social_security_ratio": (b["enterprise"]["profile"].get("financials", {}).get("social_security_count", 0) / max(1, b["enterprise"]["employees"])), "tax_to_revenue": b["enterprise"]["profile"].get("financials", {}).get("tax_paid", 0) / max(1, b["enterprise"]["profile"].get("financials", {}).get("revenue", 1)), "change_count_1y": 0, "prior_claims": 1 if ctx.workspace["claim"]["paid_before"] else 0, "industry_risk": 3}
    base = fraud_models.score(feats, {}, None, st.get("process", ""), b["enterprise"]["name"])
    prob = min(0.99, base["probability"] * 0.5 + 0.12 * len(hits) + 0.08 * sum(1 for c in contradictions if c["severity"] == "high"))
    patterns = [{"code": h["code"], "name": CLAIM_FRAUD_PATTERNS[h["code"]]["name"], "evidence": h["evidence"]} for h in hits]
    return {"probability": round(prob, 4), "base_model": base["probability"], "submodels": base["submodels"], "patterns": patterns, "old_damage": old}


def _old_damage(ctx) -> list[dict]:
    out = []
    uw = ctx.workspace.get("uw_photos", [])
    if not uw:
        return out
    uw_imgs = [(u, vision.load(u["path"])) for u in uw]
    for p in ctx.workspace["parsed"]["photos"]:
        serial = p["meta"].get("serial")
        img = vision.load(next(m["path"] for m in ctx.workspace["materials"] if m["id"] == p["material_id"]))
        match = None
        for u, uimg in uw_imgs:
            meta = vision.metadata(uimg)
            if serial and meta.get("serial") == serial:
                match = (u, uimg)
                break
        if match:
            out.append({"photo": p["name"], "uw_photo": match[0]["name"], "result": vision.compare_old_damage(match[1], img)})
    return out


# ================================================================ 6 多源融合定损范围
@registry.tool("determine_loss_scope", "多源融合定损范围", "时间对齐、事故窗口识别（变点检测）、受影响对象映射、停机时长与反事实基线量化损失、敏感性分析", permission="T0", zone="private", max_level=3)
def determine_loss_scope(ctx, **_) -> ToolResult:
    win = _window(ctx)
    pid = ctx.workspace["bundle"]["policy"]["id"]
    scope: dict = {"objects": [], "window": None, "metrics": {}, "confidence": 0.5, "sensitivity": [], "notes": []}
    if win is None:
        scope["notes"].append("出险时间无法识别，损失范围依赖声明与第三方报告")
        ctx.workspace["loss_scope"] = scope
        return ToolResult(summary="出险时间无法识别，损失范围依赖声明与第三方报告", level=1)
    if ctx.workspace["incident_type"] in ("equipment_damage", "lab_fire"):
        asset = _target_asset(ctx)
        ent_eq = {e["serial"]: e for e in ctx.workspace["bundle"]["enterprise"]["profile"].get("equipment", [])}
        if asset:
            scope["objects"].append({"object": asset, "name": ent_eq[asset]["name"], "mapped_to": "保险单列明设备", "value": ent_eq[asset]["value"]})
        ts, cur = _series(pid, "motor_current", asset, *win) if asset else ([], np.array([]))
        _, vib = _series(pid, "vibration_rms", asset, *win) if asset else ([], np.array([]))
        if len(cur) >= 48:
            stop = next((i for i in range(1, len(cur)) if cur[i] < 1 and cur[i - 1] > 5), None)
            if stop is not None:
                restart = next((i for i in range(stop + 1, len(cur)) if cur[i] > 5), None)
                end_i = restart if restart is not None else len(cur) - 1
                down_h = (ts[end_i] - ts[stop]).total_seconds() / 3600
                cf = C.counterfactual_baseline(cur, stop)
                scope["window"] = {"start": ts[stop].isoformat(), "end": ts[end_i].isoformat(), "ongoing": restart is None}
                scope["metrics"] = {"downtime_hours": round(down_h, 1), "capacity_loss_ratio": round(min(1.0, max(0.0, cf["relative_gap"])), 3), "peak_vibration": round(float(vib.max()), 2) if len(vib) else None, "baseline_current": round(float(np.mean(cur[:stop])), 2)}
                for shift in (-1, 1):
                    s2 = max(1, stop + shift)
                    scope["sensitivity"].append({"assumption": f"事故起点 {'提前' if shift < 0 else '推后'} 1 小时", "downtime_hours": round((ts[end_i] - ts[s2]).total_seconds() / 3600, 1)})
                scope["confidence"] = 0.9
            else:
                scope["notes"].append("物联网数据中未识别到停机点")
        else:
            scope["notes"].append("物联网数据缺失，损失范围依赖声明与第三方报告，置信度降低")
    elif ctx.workspace["incident_type"] == "data_breach":
        ts, eg = _series(pid, "egress_mb", "核心业务系统", *win)
        scope["objects"].append({"object": "核心业务系统", "name": "核心业务系统", "mapped_to": "资产清单：核心业务系统"})
        if len(eg) >= 48:
            med = float(np.median(eg))
            mad = float(np.median(np.abs(eg - med))) * 1.4826 or 1.0
            z = (eg - med) / mad
            idx = [i for i, v in enumerate(z) if v > 6]
            if idx:
                excess = float(sum(eg[i] - med for i in idx))
                scope["window"] = {"start": ts[idx[0]].isoformat(), "end": ts[idx[-1]].isoformat(), "ongoing": False}
                fx = _docs(ctx).get("forensic", {}).get("fields", {})
                records = _num(fx.get("受影响记录数") or fx.get("涉及记录") or "0")
                scope["metrics"] = {"excess_egress_mb": round(excess, 1), "exfil_hours": len(idx), "affected_records": records or None, "records_range": [round(records * 0.8), round(records * 1.1)] if records else None}
                for widen in (-1, 1):
                    sel = [i for i, v in enumerate(z) if v > 6 + widen]
                    scope["sensitivity"].append({"assumption": f"异常判定阈值 {'放宽' if widen < 0 else '收紧'}", "excess_egress_mb": round(float(sum(eg[i] - med for i in sel)), 1)})
                scope["confidence"] = 0.85
        bi = _docs(ctx).get("bi_proof", {}).get("fields", {})
        if bi:
            scope["metrics"]["declared_interruption_hours"] = _num(bi.get("中断时长") or bi.get("业务中断时长"))
    ctx.workspace["loss_scope"] = scope
    m = scope["metrics"]
    return ToolResult(summary="损失范围：" + ("；".join(f"{o['name']}（{o['mapped_to']}）" for o in scope["objects"]) or "未映射到标的") + (f"；事故窗口 {scope['window']['start'][:16]} 起" if scope.get("window") else "") + ("；" + "，".join(f"{k} {v}" for k, v in m.items() if v is not None) if m else "") + f"；置信度 {scope['confidence']:.0%}" + ("；" + "；".join(scope["notes"]) if scope["notes"] else ""), data=scope, level=2)


# ================================================================ 7 责任认定
@registry.tool("collect_party_evidence", "整理参与方行为证据", "在独立上下文中整理单个参与方的行为证据、偏离规范程度、过错程度与合同责任上限", permission="T0", zone="private", max_level=2)
def collect_party_evidence(ctx, **_) -> ToolResult:
    party = ctx.inputs["party"]
    with session_scope() as s:
        mats = s.scalars(select(Material).where(Material.owner_type == "claim", Material.owner_id == ctx.inputs["claim_id"])).all()
        texts = [(m.name, m.text or "") for m in mats if m.text]
    role = party["role"]
    keys = {"维保服务商": ["维保", "保养", "备件", "推迟"], "设备供应商": ["制造缺陷", "批次", "材质", "质保"], "被保险人": ["操作", "负载", "超载", "漏洞", "修补", "未修补", "运行"], "云服务商": ["安全组", "配置", "云平台", "端口"], "外部攻击者": ["攻击", "暴力破解", "入侵", "恶意"]}.get(role, [party["name"]])
    hits = []
    for name, text in texts:
        for sent in re.split(r"[。\n；]", text):
            if any(k in sent for k in keys) and len(sent.strip()) > 6:
                hits.append({"source": name, "text": sent.strip()[:160]})
    joined = " ".join(h["text"] for h in hits)
    deviation, fault = 0.0, "无过错"
    if role == "维保服务商":
        m = re.search(r"推迟[^。]*?(\d+)\s*个月", joined)
        months = float(m.group(1)) if m else (2.0 if "推迟" in joined or "未执行" in joined else 0.0)
        deviation = min(1.0, months / 3)
        fault = "一般过失" if months else "无过错"
        if "备件交期" in joined or "厂商原因" in joined:
            fault = "一般过失"
    elif role == "设备供应商":
        defect = any(k in joined for k in ("存在制造缺陷", "材质不合格", "批次缺陷")) and not any(k in joined for k in ("无制造缺陷", "未发现制造缺陷"))
        deviation = 0.7 if defect else 0.1
        fault = "一般过失" if defect else "无过错"
    elif role == "被保险人":
        if "未修补" in joined:
            m = re.search(r"(\d+)\s*天", joined)
            days = float(m.group(1)) if m else 30
            deviation = min(1.0, days / 60)
            fault = "一般过失"
        elif any(k in joined for k in ("超负荷", "超载", "违规操作")):
            deviation, fault = 0.6, "一般过失"
        else:
            deviation, fault = 0.15, "轻微过失"
    elif role == "云服务商":
        mis = any(k in joined for k in ("配置错误", "默认开放", "未按约定"))
        deviation = 0.6 if mis else 0.1
        fault = "一般过失" if mis else "无过错"
    elif role == "外部攻击者":
        deviation, fault = 1.0, "故意"
    cap = None
    m = re.search(r"责任上限[^。；]*?([\d.]+\s*万?元)", joined)
    if m:
        cap = _amount(m.group(1))
    ctx.workspace["party_result"] = {"name": party["name"], "role": role, "deviation": round(deviation, 3), "fault": fault, "cap": cap, "evidence": hits[:6]}
    return ToolResult(summary=f"{party['name']}（{role}）：行为证据 {len(hits)} 条，偏离规范程度 {deviation:.2f}，过错程度 {fault}" + (f"，合同责任上限 {format_amount(cap)}" if cap else ""), data=ctx.workspace["party_result"], level=2)


def _party_tasks(ctx) -> list[dict]:
    return [{"purpose": "party_evidence", "title": f"参与方证据：{p['name']}（{p['role']}）", "inputs": {"claim_id": ctx.inputs["claim_id"], "party": p}, "key": p["name"]} for p in ctx.workspace["claim"]["parties"]]


@registry.tool("analyze_associations", "关联分析", "参与方行为事件与损失指标的时序关联、滞后相关、格兰杰因果检验与事件关联规则挖掘", permission="T0", zone="private", max_level=2)
def analyze_associations(ctx, **_) -> ToolResult:
    outs = (ctx.workspace.get("subagent_outputs") or {}).get("parties", [])
    parties = []
    for o in outs:
        res = (o.get("output") or {}).get("party")
        if res:
            parties.append(res)
    if not parties:
        parties = [{"name": p["name"], "role": p["role"], "deviation": 0.0, "fault": "无过错", "cap": None, "evidence": []} for p in ctx.workspace["claim"]["parties"]]
    ctx.workspace["parties"] = parties
    tpl = CAUSAL_TEMPLATES.get(ctx.workspace["incident_type"]) or CAUSAL_TEMPLATES["equipment_damage"]
    win = _window(ctx)
    pid = ctx.workspace["bundle"]["policy"]["id"]
    data_edges = {}
    pc = None
    if win:
        start, end = win[0] - timedelta(days=9), win[1]
        metrics = {n["id"]: n["metric"] for n in tpl["nodes"] if n.get("metric")}
        asset = _target_asset(ctx) if ctx.workspace["incident_type"] != "data_breach" else "核心业务系统"
        series = {}
        for node, metric in metrics.items():
            _, vals = _series(pid, metric, asset, start, end)
            if len(vals) >= 48:
                series[node] = vals
        if len(series) >= 2:
            n = min(len(v) for v in series.values())
            series = {k: v[-n:] for k, v in series.items()}
            for e in tpl["edges"]:
                if e["from"] in series and e["to"] in series:
                    lc = C.lagged_correlation(series[e["from"]], series[e["to"]])
                    gr = C.granger(series[e["from"]], series[e["to"]])
                    data_edges[f"{e['from']}>{e['to']}"] = {"lag": lc["lag"], "corr": lc["corr"], "p": gr["p"], "f": gr["f"], "significant": gr["significant"] and abs(lc["corr"]) > 0.3}
            pc = C.pc_skeleton(series)
    events = []
    for f in ctx.workspace["facts"]:
        if f.get("time"):
            kind = "维保推迟" if "maintenance" in f["tags"] else ("预警" if "alert" in f["tags"] else ("设备退化" if "退化" in f["text"] or "变点" in f["text"] else ("故障" if "停机" in f["text"] or "故障" in f["text"] else "其他")))
            try:
                events.append((datetime.fromisoformat(f["time"][:19]).timestamp() / 3600, kind))
            except ValueError:
                continue
    for e in (ctx.workspace.get("evidence_analysis") or {}).get("timeline", []):
        if "变点" in e["event"]:
            events.append((datetime.fromisoformat(e["time"][:19]).timestamp() / 3600, "设备退化"))
    rules = C.association_rules(events, window=24 * 7, min_support=1) if len(events) >= 2 else []
    ctx.workspace["associations"] = {"data_edges": data_edges, "pc": pc, "rules": rules[:8], "template": tpl["name"]}
    sig = sum(1 for d in data_edges.values() if d["significant"])
    return ToolResult(summary=f"参与方 {len(parties)} 个；按模板「{tpl['name']}」检验数据边 {len(data_edges)} 条，格兰杰显著 {sig} 条" + (f"；条件独立性检验保留边 {len(pc['edges'])} 条" if pc else "") + f"；事件关联规则 {len(rules)} 条", data=ctx.workspace["associations"], level=2, metrics={"significant_edges": sig})


@registry.tool("build_causal_graph", "构建因果图", "以事故因果模板为骨架，结合数据驱动的因果发现结果，由大模型整合并标出关键边", permission="T0", zone="follow", max_level=1)
def build_causal_graph(ctx, **_) -> ToolResult:
    tpl = CAUSAL_TEMPLATES.get(ctx.workspace["incident_type"]) or CAUSAL_TEMPLATES["equipment_damage"]
    out = model_gateway.invoke("cl.causal_integrate", {"template_edges": tpl["edges"], "data_edges": ctx.workspace["associations"]["data_edges"], "behavior_nodes": [n["id"] for n in tpl["nodes"] if n["kind"] == "behavior"], "nodes": [{"id": n["id"], "label": n["label"], "kind": n["kind"]} for n in tpl["nodes"]]}, ctx=ctx, data_level=1)
    node_ids = {n["id"] for n in tpl["nodes"]}
    edges = [e for e in out["edges"] if e["from"] in node_ids and e["to"] in node_ids]
    try:
        C.SCM(tpl["nodes"], edges)
    except ValueError:
        edges = [{**e, "source": "template", "key": True, "note": "大模型整合结果存在环，退回模板"} for e in tpl["edges"]]
    ctx.workspace["causal_graph"] = {"nodes": tpl["nodes"], "edges": edges, "summary": out.get("summary", ""), "confirmed": False, "template": tpl["name"]}
    return ToolResult(summary=f"因果图：节点 {len(tpl['nodes'])} 个、边 {len(edges)} 条（数据支持 {sum(1 for e in edges if e.get('source') == 'both')} 条），关键边 {sum(1 for e in edges if e.get('key'))} 条待确认", data=ctx.workspace["causal_graph"], level=1)


def _party_nodes(graph: dict, parties: list[dict]) -> tuple[dict, dict]:
    behaviors, party_nodes = {}, {}
    for n in graph["nodes"]:
        if n["kind"] != "behavior":
            continue
        p = next((x for x in parties if x["role"] == n.get("party_role")), None)
        if p is None:
            behaviors[n["id"]] = 0.0
            continue
        behaviors[n["id"]] = p["deviation"]
        party_nodes.setdefault(p["name"], []).append(n["id"])
    return behaviors, party_nodes


@registry.tool("estimate_responsibility", "反事实效应与原因力分摊", "在结构因果模型上对各方行为做反事实干预，用 Shapley 值分摊原因力，按过错与合同调整，后台重抽样得到比例区间与敏感性", permission="T0", zone="private", max_level=2)
def estimate_responsibility(ctx, **_) -> ToolResult:
    g = ctx.workspace["causal_graph"]
    parties = ctx.workspace["parties"]
    loss = ctx.workspace["claim"]["claimed_amount"] or 1.0
    behaviors, party_nodes = _party_nodes(g, parties)
    if not party_nodes:
        ctx.workspace["responsibility"] = {"multi_party": False, "shares": {}, "note": "无可归责的参与方行为节点"}
        return ToolResult(summary="无可归责的参与方行为节点，按单方事故认定", level=1)
    scm = C.SCM(g["nodes"], g["edges"])
    faults = {p["name"]: FAULT_DEGREES.get(p["fault"], 0.6) for p in parties}
    caps = {p["name"]: p["cap"] for p in parties if p.get("cap")}

    def job(progress):
        base = C.responsibility(scm, behaviors, party_nodes, loss)
        adj = C.fault_adjust(base["shares"], faults, caps, loss)
        boot = C.bootstrap_shares(scm, behaviors, party_nodes, loss, faults, rounds=300, progress=progress, caps=caps)
        sens = C.sensitivity(scm, behaviors, party_nodes, loss, faults, caps=caps)
        return {"base": base, "adjusted": adj, "intervals": boot, "sensitivity": sens[:8], "summary": "原因力：" + "、".join(f"{k} {v:.0%}" for k, v in adj["shares"].items())}

    task_id = background_manager.submit(ctx, "因果效应重抽样与敏感性分析", job)
    res = background_manager.wait(task_id)
    widths = {p: round(v["p95"] - v["p05"], 4) for p, v in res["intervals"].items()}
    ctx.workspace["responsibility"] = {"multi_party": len(party_nodes) >= 2, "p_actual": res["base"]["p_actual"], "shapley": res["base"]["shapley"], "causation_shares": res["base"]["shares"], "counterfactual": res["base"]["counterfactual"], "faults": {p["name"]: p["fault"] for p in parties}, "shares": res["adjusted"]["shares"], "adjust_notes": res["adjusted"]["notes"], "intervals": res["intervals"], "interval_width": widths, "max_width": max(widths.values()) if widths else 0, "sensitivity": res["sensitivity"], "behaviors": behaviors}
    r = ctx.workspace["responsibility"]
    return ToolResult(summary="原因力（Shapley）：" + "、".join(f"{k} {v:.0%}" for k, v in r["causation_shares"].items()) + "；过错与合同调整后建议比例：" + "、".join(f"{k} {v:.0%}（{r['intervals'].get(k, {}).get('p05', 0):.0%}–{r['intervals'].get(k, {}).get('p95', 0):.0%}）" for k, v in r["shares"].items()) + f"；最大区间宽度 {r['max_width']:.0%}", data={k: r[k] for k in ("shares", "intervals", "causation_shares")}, level=2, metrics={"max_width": r["max_width"]})


@registry.tool("assess_liability", "责任认定", "单方事故按近因原则构建因果链；多方事故汇总关联分析、因果图、反事实与比例结果，大模型撰写认定说理（脱敏）", permission="T0", zone="follow", max_level=2)
def assess_liability(ctx, **_) -> ToolResult:
    tl = (ctx.workspace.get("evidence_analysis") or {}).get("timeline", [])
    itype = ctx.workspace["incident_type"]
    if itype in ("equipment_damage", "lab_fire"):
        chain = ["维保推迟" if ctx.workspace["flags"].get("maintenance_delay") else "部件劣化", "部件磨损与轴承过热", "设备突发机械故障", "设备损坏与研发中断"]
    elif itype == "data_breach":
        chain = ["高危漏洞未修补", "外部攻击者暴力破解入侵", "大规模数据外传", "数据泄露事件与业务中断"]
    else:
        chain = ["事故原因", "事故", "损失"]
    resp = ctx.workspace.get("responsibility") or {}
    out = model_gateway.invoke("cl.liability", {"chain": chain, "flags": ctx.workspace["flags"], "facts": [{"id": f["id"], "text": f["text"], "tags": f["tags"]} for f in ctx.workspace["facts"]], "shares": resp.get("shares"), "timeline": [{"time": e["time"], "event": e["event"]} for e in tl[:20]]}, ctx=ctx, data_level=2)
    ex_applied = [c for c in ctx.workspace["checks"] if c["item"] == "责任免除" and c["conclusion"] == "适用"]
    insured = out["insured_event"] and not (ex_applied and any(e["conclusion"] == "适用" and ("磨损" in e["title"] or "故意" in e["title"]) for p in ctx.workspace["clause_detail"] for e in p["exclusions"]))
    ctx.workspace["liability"] = {**out, "insured_event": insured, "multi_party": bool(resp.get("multi_party")), "shares": resp.get("shares", {}), "intervals": resp.get("intervals", {})}
    return ToolResult(summary=f"近因：{out['proximate_cause']}；{'属于' if insured else '不属于'}保险事故（置信度 {out['confidence']:.0%}）" + ("；多方建议比例：" + "、".join(f"{k} {v:.0%}" for k, v in resp.get("shares", {}).items()) if resp.get("multi_party") else ""), data=ctx.workspace["liability"], level=2)


# ================================================================ 8 文本定损
def _price_items() -> list[dict]:
    with session_scope() as s:
        return [{"name": p.name, "unit": p.unit, "price": p.reference_price, "life": p.useful_life_years, "keywords": p.keywords, "category": p.category} for p in s.scalars(select(PriceItem)).all()]


def _categorize(name: str) -> str:
    if any(k in name for k in ("中断", "持续费用", "研发人员")):
        return "研发中断持续费用"
    if "租赁" in name or "施救" in name:
        return "施救费用"
    if any(k in name for k in ("取证", "应急响应", "律师", "公关", "通知")):
        return "事件响应与法律费用"
    if "营业" in name or "收入" in name:
        return "营业中断"
    if "罚" in name:
        return "罚款"
    if "赎金" in name:
        return "赎金"
    if "数据恢复" in name or "样品" in name:
        return "数据与样品重建"
    if "整机" in name or "更换设备" in name:
        return "设备更换"
    return "设备修理"


@registry.tool("assess_text_loss", "文本定损", "损失清单与维修报价逐项核定：价格库比对、工时核对、折旧与残值、中断天数与日均费用、事件响应与法律费用凭证、除外项目剔除", permission="T0", zone="private", max_level=2)
def assess_text_loss(ctx, **_) -> ToolResult:
    docs = _docs(ctx)
    prices = _price_items()
    rows = docs.get("loss_list", {}).get("rows", []) or docs.get("repair_quote", {}).get("rows", [])
    if not rows:
        raise ToolError("损失清单缺失或无法解析，无法逐项定损", retryable=False)
    ent = ctx.workspace["bundle"]["enterprise"]
    asset = _target_asset(ctx)
    eq = next((e for e in ent["profile"].get("equipment", []) if e["serial"] == asset), None)
    params = {l: d["params"] for d in ctx.workspace["clause"]["docs"] for l in d["lines"]}
    scope = ctx.workspace.get("loss_scope", {}).get("metrics", {})
    items = []
    for r in rows:
        name = r.get("项目") or r.get("名称") or r.get("费用项目") or ""
        qty = _num(r.get("数量") or "1") or 1.0
        unit_price = _amount(r.get("单价") or "")
        declared = _amount(r.get("金额") or r.get("合计") or "") or unit_price * qty
        cat = r.get("类别") or _categorize(name)
        line = _line_for(cat, list(params))
        basis, assessed, status = [], declared, "核定"
        ref = next((p for p in prices if any(k in name for k in p["keywords"])), None)
        if cat in ("罚款", "赎金"):
            assessed, status = 0.0, "剔除"
            basis.append(f"{cat}属于责任免除（条款核查结论）")
        elif cat == "研发中断持续费用":
            ledger = docs.get("rd_ledger", {}).get("fields", {})
            daily = _amount(ledger.get("日均持续费用") or "") or (unit_price if unit_price else declared / max(qty, 1))
            waiting = next((item for line, item in params.items() if LINES.get(line, {}).get("param_style") == "waiting_days"), {})
            wait = float(waiting.get("waiting_days") or 0)
            down_days = scope.get("downtime_hours", 0) / 24 if scope.get("downtime_hours") else None
            repair_days = _num(docs.get("repair_quote", {}).get("fields", {}).get("维修周期") or "") or None
            supported = max(x for x in (down_days or 0, repair_days or 0)) if (down_days or repair_days) else qty
            days = min(qty, supported) if supported else qty
            pay_days = max(0.0, days - wait)
            max_days = float(waiting.get("indemnity_months") or 6) * 30
            pay_days = min(pay_days, max_days)
            assessed = round(daily * pay_days, 2)
            basis.append(f"台账日均持续费用 {format_amount(daily)} × 核定中断 {days:.0f} 天（申报 {qty:.0f} 天，维修周期与停机记录支持 {supported:.0f} 天）扣除等待期 {wait:.0f} 天 = {pay_days:.0f} 天")
            status = "调整" if abs(assessed - declared) > 1 else "核定"
        elif ref is not None:
            if ref["unit"] == "小时" and qty > 160:
                basis.append(f"申报工时 {qty:.0f} 小时超过同类维修合理工时上限 160 小时，按 160 小时核定")
                qty_ok = 160.0
            else:
                qty_ok = qty
            up = unit_price or declared / max(qty, 1)
            ok_price = min(up, ref["price"] * 1.1)
            if up > ref["price"] * 1.1:
                basis.append(f"申报单价 {format_amount(up)} 高于价格库参考价 {format_amount(ref['price'])} 的 110%，按参考价上浮 10% 核定")
            else:
                basis.append(f"单价与价格库参考价 {format_amount(ref['price'])}（{ref['name']}）比对合理")
            assessed = round(ok_price * qty_ok, 2)
            if cat == "设备更换" and eq:
                age = (datetime(2026, 11, 1) - datetime.fromisoformat(eq["purchase_date"])).days / 365
                dep = min(0.7, age / max(ref["life"], 1))
                salvage = 0.05
                assessed = round(assessed * (1 - dep) * (1 - salvage), 2)
                basis.append(f"使用 {age:.1f} 年、折旧年限 {ref['life']:.0f} 年，折旧率 {dep:.0%}，扣除残值 5%")
            status = "调整" if abs(assessed - declared) > 1 else "核定"
        else:
            basis.append("价格库无对应项目，凭证核定" + ("（建议第三方鉴定复核）" if ctx.state.get("start_mode") == "cold" else ""))
        items.append({"item": name, "category": cat, "line": line, "quantity": qty, "declared": round(declared, 2), "assessed": round(assessed, 2), "status": status, "basis": "；".join(basis), "reference": ref["name"] if ref else None, "material_id": docs.get("loss_list", docs.get("repair_quote", {})).get("material_id")})
    total_declared = sum(i["declared"] for i in items)
    total_assessed = sum(i["assessed"] for i in items)
    ctx.workspace["text_loss"] = {"items": items, "declared": round(total_declared, 2), "assessed": round(total_assessed, 2)}
    adj = [i for i in items if i["status"] != "核定"]
    return ToolResult(summary=f"文本定损 {len(items)} 项：申报 {format_amount(total_declared)}，核定 {format_amount(total_assessed)}" + (f"；调整或剔除 {len(adj)} 项（{'、'.join(i['item'] for i in adj[:4])}）" if adj else ""), data={"assessed": total_assessed}, level=2)


# ================================================================ 9 图像定损
def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _best_item(part: str, items: list[dict]) -> dict | None:
    if not part:
        return None
    scored = [(len(_bigrams(part) & _bigrams(i["item"])), i) for i in items if i["category"] in ("设备修理", "设备更换")]
    scored = [x for x in scored if x[0] > 0]
    return max(scored, key=lambda x: x[0])[1] if scored else None


@registry.tool("assess_image_loss", "图像定损", "私域多模态识别受损部位、损伤类型与程度，判定维修或更换，与承保照片比对新旧损，图片与损失清单条目对应（图片按 L3 仅私域）", permission="T0", zone="private", max_level=3)
def assess_image_loss(ctx, **_) -> ToolResult:
    photos = ctx.workspace["parsed"]["photos"]
    if not photos:
        ctx.workspace["image_loss"] = {"items": [], "unmatched_list": [], "unmatched_photos": [], "skipped": True}
        return ToolResult(summary="无图像材料，跳过图像定损", level=1)
    items_text = ctx.workspace.get("text_loss", {}).get("items", [])
    old = {o["photo"]: o for o in (ctx.workspace.get("evidence_analysis") or {}).get("fraud", {}).get("old_damage", [])}
    out = []
    for p in photos:
        a = p["assessment"]
        part = p.get("part") or p["meta"].get("part") or ""
        match = _best_item(part, items_text)
        ob = old.get(p["name"])
        out.append({"photo": p["name"], "material_id": p["material_id"], "part": part or "未识别部位", "damages": a["damages"], "grade": a["grade"], "level": a["level"], "action": a["action"], "regions": a["regions"], "old_damage": bool(ob and ob["result"]["old_damage"]), "old_types": (ob or {}).get("result", {}).get("old_damage_types", []), "list_item": match["item"] if match else None, "confidence": a["confidence"], "llm_view": {"damages": p.get("damages"), "action": p.get("action")}})
    prices = {p["name"]: p for p in _price_items()}
    listed_physical = [i["item"] for i in items_text if i["category"] in ("设备修理", "设备更换") and (prices.get(i.get("reference") or "") or {}).get("category") in PHYSICAL_CATEGORIES]
    unmatched_list = [n for n in listed_physical if not any(o["list_item"] == n for o in out)]
    unmatched_photos = [o["photo"] for o in out if not o["list_item"] and o["level"] >= 1]
    ctx.workspace["image_loss"] = {"items": out, "unmatched_list": unmatched_list, "unmatched_photos": unmatched_photos, "skipped": False}
    return ToolResult(summary=f"图像定损 {len(out)} 张：" + "；".join(f"{o['part']} {o['grade']}→{o['action']}" + ("（含旧损特征）" if o["old_damage"] else "") for o in out) + (f"；清单有图片无 {len(unmatched_list)} 项" if unmatched_list else "") + (f"；图片有清单无 {len(unmatched_photos)} 张" if unmatched_photos else ""), data=ctx.workspace["image_loss"], level=2)


# ================================================================ 10 交叉校验
@registry.tool("cross_validate", "交叉校验", "文本定损与图像定损比对（维修或更换、程度与金额），损失范围与定损金额比对", permission="T0", zone="private")
def cross_validate(ctx, **_) -> ToolResult:
    tl = ctx.workspace.get("text_loss", {})
    il = ctx.workspace.get("image_loss", {})
    ent = ctx.workspace["bundle"]["enterprise"]
    asset = _target_asset(ctx)
    eq = next((e for e in ent["profile"].get("equipment", []) if e["serial"] == asset), None)
    issues = []
    prices = {p["name"]: p for p in _price_items()}
    severity_factor = {0: 0.0, 1: 0.35, 2: 0.7, 3: 1.0, 4: 1.0}
    diff = None
    matched = {}
    for o in il.get("items", []):
        ti = next((i for i in tl.get("items", []) if i["item"] == o["list_item"]), None)
        ref = prices.get((ti or {}).get("reference") or "")
        if ti and ref:
            est = ref["price"] * ti["quantity"] * severity_factor[o["level"]]
            prev = matched.get(ti["item"])
            matched[ti["item"]] = (ti, max(est, prev[1]) if prev else est, o)
    if matched:
        text_amt = sum(ti["assessed"] for ti, _, _ in matched.values())
        img_amt = sum(est for _, est, _ in matched.values())
        diff = abs(text_amt - img_amt) / max(text_amt, img_amt, 1)
        detail = "；".join(f"{name}：文本 {format_amount(ti['assessed'])}，图像（{o['grade']}）{format_amount(est)}" for name, (ti, est, o) in matched.items())
        if diff > 0.15:
            issues.append({"type": "金额差异", "detail": f"有照片对应的部件文本核定 {format_amount(text_amt)}，按图像损伤程度与价格库估算 {format_amount(img_amt)}，差异 {diff:.0%}（{detail}）"})
    if eq is None and il.get("items"):
        issues.append({"type": "标的映射", "detail": "照片未能映射到保险单列明设备"})
        for o in il["items"]:
            ti = next((i for i in tl["items"] if i["item"] == o["list_item"]), None)
            if ti and o["action"] == "维修" and ti["category"] == "设备更换":
                issues.append({"type": "维修或更换不一致", "detail": f"{o['part']}图像判定可维修，清单按更换申报"})
            if o["old_damage"]:
                issues.append({"type": "新旧损", "detail": f"{o['photo']}中的{'、'.join(o['old_types'])}在承保照片中已存在，对应部分不予赔偿"})
    for n in il.get("unmatched_list", []):
        issues.append({"type": "清单有图片无", "detail": f"{n}缺少对应照片"})
    for n in il.get("unmatched_photos", []):
        issues.append({"type": "图片有清单无", "detail": f"{n}显示的损伤未出现在损失清单中"})
    scope = ctx.workspace.get("loss_scope", {}).get("metrics", {})
    interr = next((i for i in tl.get("items", []) if i["category"] == "研发中断持续费用"), None)
    window = ctx.workspace.get("loss_scope", {}).get("window") or {}
    if interr and scope.get("downtime_hours") and not window.get("ongoing"):
        down_days = scope["downtime_hours"] / 24
        if interr["quantity"] > down_days * 1.5 + 30:
            issues.append({"type": "损失范围与金额", "detail": f"申报中断 {interr['quantity']:.0f} 天，停机记录显示设备已于 {down_days:.1f} 天后恢复运行"})
    ctx.workspace["cross_check"] = {"issues": issues, "text_image_diff": round(diff, 4) if diff is not None else None}
    return ToolResult(summary=f"交叉校验：问题 {len(issues)} 个" + (f"；文本与图像差异 {diff:.0%}" if diff is not None else "") + ("；" + "；".join(i["detail"] for i in issues[:3]) if issues else ""), data=ctx.workspace["cross_check"], level=1, metrics={"diff": round(diff, 4) if diff is not None else 0})


@registry.tool("reconcile_losses", "差异修正", "逐项定位文本与图像定损差异来源（新旧损、维修或更换、价格偏差），修正核定金额", permission="T0", zone="private")
def reconcile_losses(ctx, **_) -> ToolResult:
    tl = ctx.workspace["text_loss"]
    il = ctx.workspace.get("image_loss", {})
    fixes = []
    for o in il.get("items", []):
        ti = next((i for i in tl["items"] if i["item"] == o["list_item"]), None)
        if not ti:
            continue
        if o["old_damage"] and ti["status"] != "剔除":
            share = 0.3
            ti["assessed"] = round(ti["assessed"] * (1 - share), 2)
            ti["basis"] += f"；图像比对存在旧损特征（{'、'.join(o['old_types'])}），扣减 30%"
            ti["status"] = "调整"
            fixes.append(f"{ti['item']}扣减旧损部分")
        if o["action"] == "维修" and ti["category"] == "设备更换":
            ti["category"] = "设备修理"
            ti["assessed"] = round(ti["assessed"] * 0.45, 2)
            ti["basis"] += "；图像判定可维修，由更换调整为修理（按修理费用约为更换的 45% 核定）"
            ti["status"] = "调整"
            fixes.append(f"{ti['item']}由更换改为修理")
    tl["assessed"] = round(sum(i["assessed"] for i in tl["items"]), 2)
    ctx.state["reconciled"] = True
    cross_validate(ctx)
    return ToolResult(summary=f"差异修正 {len(fixes)} 项" + (f"：{'；'.join(fixes)}" if fixes else "") + f"；修正后核定 {format_amount(tl['assessed'])}", level=1)


# ================================================================ 11 理算
@registry.tool("adjust_claim", "理算与赔付建议", "按核定损失→残值→不足额比例→免赔额→自担比例→分项限额→累计限额→施救费用的顺序逐步计算，给出赔付建议与代位求偿建议", permission="T0", zone="private", max_level=2)
def adjust_claim(ctx, **_) -> ToolResult:
    tl = ctx.workspace.get("text_loss")
    if not tl or not tl.get("items"):
        raise ToolError("尚未完成文本定损，无法理算", retryable=True)
    liab = ctx.workspace.get("liability") or {}
    claim = ctx.workspace["claim"]
    docs = ctx.workspace["clause"]["docs"]
    steps, by_doc = [], []
    payout_total = 0.0
    rescue = sum(i["assessed"] for i in tl["items"] if i["category"] == "施救费用")
    rejected = [{"item": i["item"], "reason": i["basis"]} for i in tl["items"] if i["status"] == "剔除"]
    if not liab.get("insured_event", True):
        steps.append({"step": "责任认定", "amount": 0.0, "basis": "近因不属于保险责任，建议拒赔"})
        ctx.workspace["adjustment"] = {"steps": steps, "by_doc": [], "assessed": tl["assessed"], "payout": 0.0, "subrogation": 0.0, "rejected": rejected + [{"item": "全部损失", "reason": "不属于保险事故"}], "approval_level": 2}
        return ToolResult(summary="近因不属于保险责任，建议拒赔（需二级以上审批）", level=1)
    paid_before = claim["paid_before"]
    for d in docs:
        lines = d["lines"]
        p = d["params"]
        its = [i for i in tl["items"] if i["line"] in lines and i["status"] != "剔除"]
        if not its:
            continue
        loss = sum(i["assessed"] for i in its)
        rows = [{"step": "核定损失", "amount": round(loss, 2), "basis": "、".join(i["item"] for i in its)}]
        salvage = round(sum(i["assessed"] for i in its if i["category"] in ("设备修理", "设备更换")) * 0.02, 2) if has_family(lines, "equipment") else 0.0
        if salvage:
            loss -= salvage
            rows.append({"step": "扣除残值", "amount": round(loss, 2), "basis": f"更换下的旧部件残值按修理更换金额的 2% 计 {format_amount(salvage)}"})
        ratio = ctx.workspace.get("proportion", 1.0) if has_family(lines, "equipment") else 1.0
        if ratio < 1:
            loss *= ratio
            rows.append({"step": "不足额投保比例", "amount": round(loss, 2), "basis": f"保险金额与保险价值之比 {ratio:.0%}"})
        ded = float(p.get("deductible") or 0)
        loss = max(0.0, loss - ded)
        rows.append({"step": "扣除免赔额", "amount": round(loss, 2), "basis": f"每次事故免赔额 {format_amount(ded)}"})
        co = float(p.get("coinsurance") or 0)
        if co:
            loss *= 1 - co
            rows.append({"step": "自担比例", "amount": round(loss, 2), "basis": f"被保险人自担 {co:.0%}"})
        sub = float(p.get("sublimit") or 0)
        if sub and loss > sub:
            loss = sub
            rows.append({"step": "分项限额封顶", "amount": round(loss, 2), "basis": f"分项限额 {format_amount(sub)}"})
        limit = float(p.get("limit") or 0)
        remaining = max(0.0, limit - paid_before) if limit else loss
        if loss > remaining:
            loss = remaining
            rows.append({"step": "累计限额封顶", "amount": round(loss, 2), "basis": f"累计限额 {format_amount(limit)}，已赔付 {format_amount(paid_before)}"})
        else:
            rows.append({"step": "累计限额校验", "amount": round(loss, 2), "basis": f"未超过剩余累计限额 {format_amount(remaining)}"})
        payout_total += loss
        by_doc.append({"doc": d["name"], "lines": lines, "payout": round(loss, 2), "steps": rows})
        steps += [{**r, "step": f"{d['name']}·{r['step']}"} for r in rows]
    si = ctx.workspace["bundle"]["policy"]["sum_insured"]
    if rescue:
        rescue_paid = min(rescue, si)
        payout_total += rescue_paid
        steps.append({"step": "加施救费用", "amount": round(payout_total, 2), "basis": f"必要合理的施救费用 {format_amount(rescue)}（另行计算，以保险金额为限）"})
    shares = liab.get("shares") or {}
    third = {k: v for k, v in shares.items() if k not in (ctx.workspace["bundle"]["enterprise"]["name"], "意外与不可归责因素") and not any(p["name"] == k and p["role"] == "外部攻击者" for p in ctx.workspace.get("parties", []))}
    subrogation = round(payout_total * sum(third.values()), 2)
    steps.append({"step": "建议赔付金额", "amount": round(payout_total, 2), "basis": "以上各险种理算结果合计"})
    if subrogation:
        steps.append({"step": "代位求偿建议", "amount": subrogation, "basis": "赔付金额 × 第三方责任比例（" + "、".join(f"{k} {v:.0%}" for k, v in third.items()) + "），以法律与合同允许为限"})
    from ...services.config_center import cfg

    auth = cfg("approval.flows")["claim_authority"]
    level = 1 if payout_total <= auth["level1_max"] else (2 if payout_total <= auth["level2_max"] else 3)
    if ctx.state.get("start_mode") == "cold":
        level = max(level, int(auth["cold_min_level"]))
    if (ctx.workspace.get("responsibility") or {}).get("max_width", 0) > float(auth["width_threshold"]):
        level = max(level, 2)
    if rejected:
        level = max(level, int(auth["reject_min_level"]))
    ctx.workspace["adjustment"] = {"steps": steps, "by_doc": by_doc, "assessed": tl["assessed"], "payout": round(payout_total, 2), "subrogation": subrogation, "subrogation_targets": third, "rejected": rejected, "approval_level": level, "payee": ctx.workspace["bundle"]["enterprise"]["name"], "method": "银行转账至被保险人账户"}
    return ToolResult(summary=f"理算：核定 {format_amount(tl['assessed'])} → 建议赔付 {format_amount(payout_total)}" + (f"；代位求偿建议 {format_amount(subrogation)}" if subrogation else "") + (f"；部分拒赔 {len(rejected)} 项" if rejected else "") + f"；需 {level} 级审批", data={"payout": round(payout_total, 2), "subrogation": subrogation}, level=2, metrics={"payout": round(payout_total, 2)})


@registry.tool("draft_claim_opinion", "撰写理赔意见", "撰写案件概述、条款核查、责任认定、定损与理算摘要、赔付建议、拒赔或部分拒赔说明", permission="T1", zone="cloud", max_level=1)
def draft_claim_opinion(ctx, **_) -> ToolResult:
    adj = ctx.workspace.get("adjustment")
    if not adj:
        raise ToolError("尚未完成理算，无法撰写理赔意见", retryable=True)
    liab = ctx.workspace.get("liability") or {}
    st = (ctx.workspace.get("parsed") or {}).get("statement") or {}
    tl = ctx.workspace.get("text_loss") or {"declared": 0}
    from ...privacy.narrative import finalize_narrative

    out = finalize_narrative(
        model_gateway.invoke(
            "cl.opinion",
            {"summary": f"{ctx.workspace['bundle']['enterprise']['name']}于 {st.get('occurred_at') or ctx.workspace['claim']['report'].get('occurred_at')} 发生{ctx.workspace['claim']['report'].get('incident_type_name') or ctx.workspace['incident_type']}，申报损失 {format_amount(tl.get('declared') or 0)}", "checks": ctx.workspace.get("checks") or [], "proximate_cause": liab.get("proximate_cause"), "insured_event": liab.get("insured_event"), "shares": liab.get("shares") if liab.get("multi_party") else {}, "claimed": tl.get("declared") or 0, "adjustment": {k: adj[k] for k in ("assessed", "payout", "subrogation", "steps")}, "approval_level": adj["approval_level"], "rejected_items": adj["rejected"]},
            ctx=ctx,
            data_level=2,
        ),
        ctx.vault,
    )
    ctx.workspace["opinion"] = out
    return ToolResult(summary=f"理赔意见已撰写（{len(out['paragraphs'])} 段）" + ("，含部分拒赔说明" if out.get("rejection") else ""), data=out, level=2)


_ = (Artifact,)
