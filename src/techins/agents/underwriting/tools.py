"""承保风控智能体的工具。"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select

from ...analytics import vision
from ...analytics.fraud import fraud_models
from ...datasources.public import get_source
from ...db import session_scope
from ...harness.tools import ToolError, ToolResult, registry
from ...knowledge.catalog import FRAUD_PATTERNS, INDUSTRY_PROFILES, LINES, family_of, has_family
from ...llm.gateway import model_gateway
from ...models import Application, Artifact, ClaimCase, Enterprise, Material, Policy, Product
from ...textutil import format_amount, parse_amounts
from ..risk_assessment.tools import baseline
from .reasoning import amount_of

WEIGHTS = {
    "equipment": {"经营风险": 0.20, "技术风险": 0.10, "标的风险": 0.35, "欺诈风险": 0.25, "合规风险": 0.10},
    "rd_cost": {"经营风险": 0.25, "技术风险": 0.25, "标的风险": 0.15, "欺诈风险": 0.25, "合规风险": 0.10},
    "transfer": {"经营风险": 0.20, "技术风险": 0.30, "标的风险": 0.10, "欺诈风险": 0.30, "合规风险": 0.10},
    "ip": {"经营风险": 0.15, "技术风险": 0.20, "标的风险": 0.30, "欺诈风险": 0.25, "合规风险": 0.10},
    "liability": {"经营风险": 0.20, "技术风险": 0.10, "标的风险": 0.35, "欺诈风险": 0.20, "合规风险": 0.15},
    "quality": {"经营风险": 0.20, "技术风险": 0.15, "标的风险": 0.30, "欺诈风险": 0.20, "合规风险": 0.15},
    "cyber": {"经营风险": 0.15, "技术风险": 0.10, "标的风险": 0.40, "欺诈风险": 0.20, "合规风险": 0.15},
    "life": {"经营风险": 0.15, "技术风险": 0.15, "标的风险": 0.30, "欺诈风险": 0.20, "合规风险": 0.20},
    "aviation": {"经营风险": 0.15, "技术风险": 0.20, "标的风险": 0.35, "欺诈风险": 0.15, "合规风险": 0.15},
    "guarantee": {"经营风险": 0.25, "技术风险": 0.15, "标的风险": 0.20, "欺诈风险": 0.25, "合规风险": 0.15},
}
DEFAULT_WEIGHTS = {"经营风险": 0.2, "技术风险": 0.2, "标的风险": 0.25, "欺诈风险": 0.25, "合规风险": 0.1}
AUTO_UW_LIMIT = 5e6


def _app(ctx) -> dict:
    return ctx.workspace["application"]


@registry.tool("load_application", "读取投保单与产品", "读取投保单、产品核保要求与材料清单、企业档案，并复用已冻结的风险评估报告", permission="T0", zone="platform")
def load_application(ctx, **_) -> ToolResult:
    with session_scope() as s:
        app = s.get(Application, ctx.inputs["application_id"])
        if app is None:
            raise ToolError("投保单不存在", retryable=False)
        product = s.get(Product, app.product_id)
        ent = s.get(Enterprise, app.enterprise_id)
        mats = s.scalars(select(Material).where(Material.owner_type == "application", Material.owner_id == app.id)).all()
        report = s.scalars(select(Artifact).where(Artifact.kind == "risk_report", Artifact.ref_id == ent.id, Artifact.status == "frozen").order_by(Artifact.created_at.desc())).first()
        claims = s.scalars(select(ClaimCase).join(Policy, ClaimCase.policy_id == Policy.id).where(Policy.enterprise_id == ent.id)).all()
        ctx.workspace["application"] = {"id": app.id, "app_no": app.app_no, "tier": app.tier, "sum_insured": app.sum_insured, "period_months": app.period_months, "declared": app.declared, "contacts": app.contacts, "intermediary": app.intermediary, "created_at": app.created_at.date().isoformat()}
        selected = [l for l in (app.declared or {}).get("lines") or product.lines if l in product.lines] or list(product.lines)
        ctx.workspace["product"] = {"id": product.id, "name": product.name, "lines": selected, "product_lines": product.lines, "package": product.package, "version": product.version}
        ctx.workspace["enterprise"] = {"id": ent.id, "name": ent.name, "credit_code": ent.credit_code, "track": ent.track, "tech_field": ent.tech_field, "employees": ent.employees, "profile": ent.profile, "region": ent.region}
        ctx.workspace["materials"] = [{"id": m.id, "name": m.name, "type": m.material_type, "path": m.file_path, "mime": m.mime, "text": m.text or "", "level": m.data_level} for m in mats]
        ctx.workspace["risk_report_ref"] = {"artifact_key": report.artifact_key, "version": report.version, "data": report.data} if report else None
        ctx.workspace["prior_claims"] = len(claims)
    return ToolResult(summary=f"投保单 {ctx.workspace['application']['app_no']}：{ent.name} 投保{product.name}，保额 {format_amount(app.sum_insured)}；材料 {len(mats)} 份；" + ("复用已冻结的风险评估报告" if report else "无风险评估报告，将执行精简风险扫描"), level=2)


@registry.tool("check_materials", "材料完整性检查", "对照产品材料清单检查已提供、缺失与不合格的材料", permission="T0", zone="private")
def check_materials(ctx, **_) -> ToolResult:
    from ..clause_design.library import COMMON_MATERIALS, LINE_MATERIALS

    required = list(COMMON_MATERIALS)
    for line in ctx.workspace["product"]["lines"]:
        required += [m for m in LINE_MATERIALS.get(line, []) if m not in required]
    provided = ctx.workspace["materials"]
    result = []
    for name in required:
        match = next((m for m in provided if name in m["type"] or m["type"] in name or name[:4] in m["name"]), None)
        status = "provided" if match else "missing"
        if match and match["mime"].startswith("text") and len(match["text"]) < 20:
            status = "invalid"
        result.append({"material": name, "status": status, "file": match["name"] if match else None})
    ctx.workspace["material_check"] = result
    missing = [r["material"] for r in result if r["status"] != "provided"]
    return ToolResult(summary=f"材料清单 {len(required)} 项，已提供 {len(required) - len(missing)} 项" + (f"，缺失或不合格：{'、'.join(missing)}" if missing else ""), data=result, level=1, metrics={"required": len(required), "missing": len(missing)})


@registry.tool("extract_material_elements", "多模态要素抽取", "私域多模态模型解析文档、表格与照片：抽取键值要素、设备清单与发票行、铭牌信息、场所隐患、拍摄时间、感知哈希与篡改痕迹", permission="T1", zone="private", max_level=3)
def extract_material_elements(ctx, **_) -> ToolResult:
    elements: dict = {"docs": {}, "photos": []}
    for m in ctx.workspace["materials"]:
        if m["mime"].startswith("image"):
            img = vision.load(m["path"])
            payload = {"path": m["path"], "name": m["name"], "_images": [vision.to_data_url(m["path"])]}
            info = model_gateway.invoke("uw.image_elements", payload, ctx=ctx, data_level=3)
            meta = vision.metadata(img)
            info.setdefault("captured_at", meta.get("captured_at", ""))
            info["serial"] = info.get("serial") or meta.get("serial", "")
            elements["photos"].append({"material_id": m["id"], "name": m["name"], **info, "phash": vision.phash(img), "ela": vision.ela(img), "damage": vision.assess_damage(img)})
        elif m["text"]:
            out = model_gateway.invoke("uw.extract_elements", {"name": m["name"], "text": m["text"]}, ctx=ctx, data_level=2)
            doc = {"material_id": m["id"], "name": m["name"], "fields": out["fields"], "rows": out.get("rows", []), "confidence": out.get("confidence", 0.8)}
            prev = elements["docs"].get(out["kind"])
            if prev is None:
                elements["docs"][out["kind"]] = doc
            else:
                # 同类材料多份（如测评报告与安全制度）：要素合并，先出现的字段不被覆盖，表格行追加
                prev["fields"] = {**doc["fields"], **prev["fields"]}
                prev["rows"] = prev["rows"] + doc["rows"]
                prev["name"] = f"{prev['name']}、{doc['name']}"
                prev["confidence"] = min(prev["confidence"], doc["confidence"])
    ctx.workspace["elements"] = elements
    low = [d["name"] for d in elements["docs"].values() if d["confidence"] < 0.85]
    return ToolResult(summary=f"解析文档 {len(elements['docs'])} 份、照片 {len(elements['photos'])} 张" + (f"；低置信度：{'、'.join(low)}" if low else ""), data={"kinds": list(elements["docs"].keys()), "photos": len(elements["photos"])}, level=3, metrics={"docs": len(elements["docs"]), "photos": len(elements["photos"]), "low_confidence": len(low)})


def _date(s: str) -> date | None:
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


@registry.tool("verify_consistency", "真实性与一致性核验", "营业执照与工商登记、财报与纳税社保、设备清单与发票与铭牌三方比对、保额与价值、照片时间逻辑、照片重复与篡改检测", permission="T0", zone="private", max_level=3)
def verify_consistency(ctx, **_) -> ToolResult:
    el = ctx.workspace["elements"]
    ent = ctx.workspace["enterprise"]
    app = _app(ctx)
    checks = []

    def add(item: str, sources: dict, status: str, note: str = "") -> None:
        checks.append({"item": item, "sources": sources, "status": status, "note": note})

    lic = el["docs"].get("license", {}).get("fields", {})
    reg = get_source().registry(ent["name"]) or {}
    if lic:
        same = lic.get("统一社会信用代码", "").strip() == reg.get("credit_code", "") and lic.get("名称", "").strip() == reg.get("name", "")
        add("营业执照与工商登记", {"营业执照": f"{lic.get('名称')} / {lic.get('统一社会信用代码')}", "工商登记": f"{reg.get('name')} / {reg.get('credit_code')}"}, "一致" if same else "不一致", "" if same else "名称或统一社会信用代码与工商登记不符")
    else:
        add("营业执照与工商登记", {}, "无法核验", "未提供营业执照")
    fin = el["docs"].get("financial", {}).get("fields", {})
    tax = get_source().tax_social_security(ent["name"]) or {}
    revenue = amount_of(fin.get("营业收入", "")) if fin else None
    emp = ent["employees"]
    ss = tax.get("social_security_count")
    ssr = (ss / emp) if (ss is not None and emp) else 1.0
    if revenue and tax:
        ratio = tax["tax_paid"] / revenue if revenue else 0
        add("财务报表与纳税社保规模", {"申报营业收入": format_amount(revenue), "纳税额": format_amount(tax["tax_paid"]), "参保人数": f"{ss} 人 / 员工 {emp} 人"}, "一致" if ratio > 0.004 and ssr > 0.3 else "不一致", f"纳税与营收之比 {ratio:.3%}，参保比例 {ssr:.0%}")
    else:
        ratio = 0.05
        add("财务报表与纳税社保规模", {}, "无法核验", "缺少财务报表或纳税社保数据授权")
    eq_rows = el["docs"].get("equipment_list", {}).get("rows", [])
    inv_rows = el["docs"].get("invoices", {}).get("rows", [])
    photo_serials = {p.get("serial") for p in el["photos"] if p.get("serial")}
    book_value = 0.0
    recent_value = 0.0
    app_date = date.fromisoformat(app["created_at"])
    mismatches = []
    for row in eq_rows:
        serial = row.get("序列号", "")
        val = amount_of(row.get("原值", "")) or 0
        book_value += val
        pdate = _date(row.get("购置日期", ""))
        if pdate and 0 <= (app_date - pdate).days <= 30:
            recent_value += val
        inv = next((r for r in inv_rows if r.get("序列号") == serial), None)
        if inv is None:
            mismatches.append(f"{row.get('名称')}缺少对应发票")
        else:
            inv_val = amount_of(inv.get("金额", "")) or 0
            if abs(inv_val - val) > 0.05 * max(val, 1):
                mismatches.append(f"{row.get('名称')}发票金额 {format_amount(inv_val)} 与清单原值 {format_amount(val)} 不符")
        if photo_serials and serial not in photo_serials:
            mismatches.append(f"{row.get('名称')}（{serial}）无铭牌照片对应")
    if eq_rows:
        add("设备清单、发票、铭牌三方比对", {"设备清单": f"{len(eq_rows)} 台", "发票": f"{len(inv_rows)} 张", "铭牌照片": f"{len(photo_serials)} 张"}, "一致" if not mismatches else "不一致", "；".join(mismatches[:5]))
    si = app["sum_insured"]
    si_to_value = si / book_value if book_value else 1.0
    if book_value:
        add("保额与账面价值", {"保险金额": format_amount(si), "设备原值合计": format_amount(book_value)}, "一致" if si_to_value <= 1.2 else "不一致", f"保额为价值的 {si_to_value:.2f} 倍")
    for p in el["photos"]:
        cap = _date(p.get("captured_at") or "")
        if cap and cap > app_date:
            add(f"照片拍摄时间：{p['name']}", {"拍摄时间": str(cap), "投保日期": str(app_date)}, "不一致", "照片拍摄时间晚于投保日期")
        if p["ela"]["suspicious"]:
            add(f"图像篡改检测：{p['name']}", {"错误级别异常得分": p["ela"]["score"]}, "不一致", "局部压缩误差异常集中，可能经过编辑")
    with session_scope() as s:
        others = s.scalars(select(Material).where(Material.mime.like("image%"), Material.owner_type == "application", Material.owner_id != app["id"])).all()
        other_hashes = [(o.name, o.owner_id, vision.phash(vision.load(o.file_path))) for o in others if o.file_path]
    duplicates = []
    for p in el["photos"]:
        for name, owner, h in other_hashes:
            if vision.hamming(p["phash"], h) <= 4:
                duplicates.append(f"{p['name']} 与投保单 {owner} 的「{name}」高度相似")
    if duplicates:
        add("照片重复使用检测", {"相似照片": len(duplicates)}, "不一致", "；".join(duplicates))
    sec = el["docs"].get("security", {}).get("fields", {})
    if sec:
        unpatched = int(next(iter([int(x) for x in re.findall(r"(\d+)", sec.get("高危漏洞", "0"))]), 0))
        add("安全测评报告", {"等保级别": sec.get("等保级别"), "高危漏洞": sec.get("高危漏洞")}, "一致" if unpatched == 0 else "不一致", f"存在 {unpatched} 个未修补高危漏洞" if unpatched else "")
        ctx.workspace["unpatched"] = unpatched
    ctx.workspace["consistency"] = checks
    ctx.workspace["fraud_features"] = {"si_to_value": si_to_value, "recent_purchase_share": recent_value / book_value if book_value else 0.0, "employees": emp, "social_security_ratio": ssr, "tax_to_revenue": ratio, "change_count_1y": len([n for n in get_source().news(ent["name"]) if "变更" in n["text"]]), "prior_claims": ctx.workspace.get("prior_claims", 0), "industry_risk": INDUSTRY_PROFILES.get(ent["track"], {}).get("risk_level", 3)}
    bad = [c for c in checks if c["status"] == "不一致"]
    return ToolResult(summary=f"核验 {len(checks)} 项：一致 {sum(1 for c in checks if c['status'] == '一致')}、不一致 {len(bad)}、无法核验 {sum(1 for c in checks if c['status'] == '无法核验')}" + (f"；不一致：{'；'.join(c['item'] for c in bad)}" if bad else ""), data=checks, level=2, metrics={"checks": len(checks), "inconsistent": len(bad)})


@registry.tool("analyze_business_background", "经营与行业背景分析", "私域计算财务健康与经营稳定性指标，脱敏后由云端研判经营与行业背景", permission="T0", zone="follow", max_level=2)
def analyze_business_background(ctx, **_) -> ToolResult:
    ent = ctx.workspace["enterprise"]
    f = ent["profile"].get("financials", {})
    court = get_source().court(ent["name"])
    ind = {
        "revenue": f.get("revenue", 0),
        "net_profit": f.get("net_profit", 0),
        "operating_cash_flow": f.get("operating_cash_flow", 0),
        "cash_months": f.get("cash_reserve", 0) / max(f.get("monthly_fixed_cost", 1), 1),
        "rd_intensity": f.get("rd_expense", 0) / max(f.get("revenue", 1), 1),
        "litigation": len(court),
        "social_security_ratio": ctx.workspace["fraud_features"]["social_security_ratio"],
    }
    out = model_gateway.invoke("uw.background", {"name": ent["name"], "track": ent["track"], "indicators": ind}, ctx=ctx, data_level=2)
    ctx.workspace["background"] = {**out, "indicators": ind, "court": court}
    return ToolResult(summary=out["summary"], data=ctx.workspace["background"], level=2)


@registry.tool("assess_technical_risk", "技术风险研判", "复用已冻结的风险评估结论；没有报告时按指标规则做精简风险扫描", permission="T0", zone="private")
def assess_technical_risk(ctx, **_) -> ToolResult:
    ent = ctx.workspace["enterprise"]
    rep = ctx.workspace.get("risk_report_ref")
    if rep:
        risks = [r for r in rep["data"].get("risks", []) if r["type_code"] in ("R01", "R03", "R05")]
        score = min(100.0, sum(r.get("score", 0) for r in risks) / max(1, len(risks)) * 4) if risks else 30.0
        source = f"风险评估报告 V{rep['version']}"
        items = [{"title": r["title"], "level": r.get("level"), "score": r.get("score")} for r in risks]
    else:
        items = []
        for sub in ("R01-2", "R03-1", "R05-1"):
            lik, sev, note = baseline(sub, {"profile": ent["profile"]}, 1)
            items.append({"title": sub, "level": None, "score": lik * sev, "note": note})
        score = min(100.0, sum(i["score"] for i in items) / len(items) * 4)
        source = "精简风险扫描（指标规则）"
    ctx.workspace["technical"] = {"score": round(score, 1), "source": source, "items": items}
    return ToolResult(summary=f"技术风险得分 {score:.0f}（依据：{source}）", data=ctx.workspace["technical"], level=1)


@registry.tool("run_fraud_models", "反欺诈模型研判", "专家规则、监督模型、无监督异常、关系网络、文本线索五路子模型融合，标记可疑欺诈模式", permission="T0", zone="private", max_level=2)
def run_fraud_models(ctx, **_) -> ToolResult:
    app = _app(ctx)
    ent = ctx.workspace["enterprise"]
    statement = app["declared"].get("statement", "")
    out = fraud_models.score(ctx.workspace["fraud_features"], app["contacts"], app["intermediary"], statement, ent["name"])
    extra = []
    for c in ctx.workspace.get("consistency", []):
        if c["status"] != "不一致":
            continue
        if "篡改" in c["item"] or "营业执照" in c["item"]:
            extra.append({"pattern": "P04", "rule": c["item"], "value": c["note"]})
        elif "重复" in c["item"]:
            extra.append({"pattern": "P11", "rule": c["item"], "value": c["note"]})
        elif "三方比对" in c["item"]:
            extra.append({"pattern": "P01", "rule": c["item"], "value": c["note"]})
    for e in extra:
        if not any(h["pattern"] == e["pattern"] and h["rule"] == e["rule"] for h in out["rule_hits"]):
            out["rule_hits"].append(e)
    if extra:
        out["probability"] = round(min(0.99, out["probability"] + 0.08 * len(extra)), 4)
    patterns = {}
    for h in out["rule_hits"]:
        patterns.setdefault(h["pattern"], {"code": h["pattern"], "name": FRAUD_PATTERNS[h["pattern"]]["name"], "evidence": []})["evidence"].append(f"{h['rule']}：{h['value']}")
    for p in patterns.values():
        p["confidence"] = round(min(0.95, 0.5 + 0.15 * len(p["evidence"]) + out["probability"] * 0.3), 2)
    out["patterns"] = list(patterns.values())
    ctx.workspace["fraud"] = out
    return ToolResult(summary=f"融合欺诈概率 {out['probability']:.1%}；子模型：" + "、".join(f"{k} {v}" for k, v in out["submodels"].items()) + (f"；可疑模式：{'、'.join(p['code'] + p['name'] for p in out['patterns'])}" if out["patterns"] else ""), data=out, level=2, metrics={"probability": out["probability"], "patterns": len(out["patterns"])})


def _subject_score(ctx) -> tuple[float, list[str]]:
    lines = ctx.workspace["product"]["lines"]
    p = ctx.workspace["enterprise"]["profile"]
    factors, score = [], 30.0
    if has_family(lines, "equipment", "rd_cost", "aviation"):
        eq = p.get("equipment", [])
        if eq:
            ages = [(date(2026, 11, 3) - date.fromisoformat(e["purchase_date"])).days / 365 for e in eq]
            age = sum(ages) / len(ages)
            conc = max(e["value"] for e in eq) / sum(e["value"] for e in eq)
            score += min(20, age * 2.5) + conc * 20
            factors.append(f"设备平均年龄 {age:.1f} 年、单台集中度 {conc:.0%}")
        hazards = [h for ph in ctx.workspace["elements"]["photos"] for h in ph.get("hazards", [])]
        score += 8 * len(hazards)
        if hazards:
            factors.append("现场隐患：" + "；".join(hazards[:3]))
    if has_family(lines, "cyber"):
        sec = p.get("security", {})
        score += (3 - sec.get("level", 2)) * 10 + sec.get("data_scale", 2) * 6 + 12 * ctx.workspace.get("unpatched", 0)
        factors.append(f"等保 {sec.get('level')} 级、数据规模 {sec.get('data_scale')} 级、未修补高危漏洞 {ctx.workspace.get('unpatched', 0)} 个")
    if has_family(lines, "liability", "quality", "life"):
        neg = sum(1 for n in get_source().news(ctx.workspace["enterprise"]["name"]) if any(k in n["text"] for k in ("事故", "召回", "投诉", "误判")))
        score += 10 * neg
        factors.append(f"产品质量与责任相关事件 {neg} 起")
    return min(100.0, score), factors


@registry.tool("score_application", "风险评分卡", "经营、技术、标的、欺诈、合规五个分项评分并按险种权重汇总", permission="T0", zone="private")
def score_application(ctx, **_) -> ToolResult:
    bg = ctx.workspace["background"]["indicators"]
    op = 25 + (20 if bg["net_profit"] < 0 else 0) + (15 if bg["cash_months"] < 12 else 0) + 6 * min(5, bg["litigation"]) + (20 if bg["social_security_ratio"] < 0.3 else 0)
    subject, subject_factors = _subject_score(ctx)
    fraud = ctx.workspace["fraud"]["probability"] * 100
    compliance = 20 + 15 * sum(1 for n in get_source().news(ctx.workspace["enterprise"]["name"]) if any(k in n["text"] for k in ("处罚", "整改", "合规检查")))
    line = ctx.workspace["product"]["lines"][0]
    w = WEIGHTS.get(family_of(line), DEFAULT_WEIGHTS)
    subs = [
        {"name": "经营风险", "score": min(100, op), "factors": ctx.workspace["background"]["concerns"] or ["经营指标正常"]},
        {"name": "技术风险", "score": ctx.workspace["technical"]["score"], "factors": [f"依据{ctx.workspace['technical']['source']}"]},
        {"name": "标的风险", "score": subject, "factors": subject_factors or ["标的状况正常"]},
        {"name": "欺诈风险", "score": fraud, "factors": [p["name"] for p in ctx.workspace["fraud"]["patterns"]] or ["未命中可疑模式"]},
        {"name": "合规风险", "score": min(100, compliance), "factors": ["监管与处罚记录"]},
    ]
    for s in subs:
        s["weight"] = w[s["name"]]
    total = sum(s["score"] * s["weight"] for s in subs)
    level = "低" if total < 40 else ("中" if total < 60 else ("高" if total < 80 else "极高"))
    ctx.workspace["scorecard"] = {"total": round(total, 1), "level": level, "subs": subs, "weights_line": line}
    return ToolResult(summary=f"总分 {total:.1f}（{level}）：" + "、".join(f"{s['name']} {s['score']:.0f}×{s['weight']:.0%}" for s in subs), data=ctx.workspace["scorecard"], level=1, metrics={"total": round(total, 1)})


def quote_premium(product_package: dict, sum_insured: float, enterprise_profile: dict, risks: list[dict], lines: list[str] | None = None) -> dict:
    """按产品费率表与企业因子计算保费：毛费率 × 保额 × 企业相对系数（相对系数按信度收缩）；只计投保人选择的险种组件。"""
    from ..actuarial_pricing.tools import enterprise_factor_values

    comps = [c for c in product_package.get("components", []) if not lines or c.get("line") in lines]
    if not comps:
        rate = LINES.get((product_package.get("lines") or ["RD08"])[0], {}).get("benchmark_rate", 0.01)
        return {"premium": round(rate * sum_insured, 2), "rate": rate, "relativity": 1.0, "detail": []}
    values = enterprise_factor_values(enterprise_profile, risks)
    total_limit = sum(float(c.get("limit") or 0) for c in comps) or sum_insured
    premium, detail = 0.0, []
    for c in comps:
        share = float(c.get("limit") or total_limit) / total_limit
        rel = 1.0
        for row in c.get("relativities", []):
            if row.get("is_base"):
                continue
            f = row["factor"]
            v = values.get(f)
            if v is None:
                continue
            label = row["level"]
            try:
                if label.startswith("≤") and v <= float(label[1:]):
                    rel *= row["relativity"]
                elif label.startswith(">") and v > float(label[1:]):
                    rel *= row["relativity"]
                elif "–" in label:
                    lo, hi = (float(x) for x in label.split("–"))
                    if lo < v <= hi:
                        rel *= row["relativity"]
                elif label.isdigit() and int(round(v)) == int(label):
                    rel *= row["relativity"]
            except ValueError:
                continue
        lo, hi = c.get("band", [c["gross_rate"] * 0.75, c["gross_rate"] * 1.25])
        rate = min(max(c["gross_rate"] * rel, lo), hi)
        p = rate * sum_insured * share
        premium += p
        detail.append({"component": c["name"], "gross_rate": c["gross_rate"], "relativity": round(rel, 3), "applied_rate": rate, "premium": round(p, 2)})
    return {"premium": round(premium, 2), "relativity": round(premium / max(1e-9, sum(c["gross_rate"] * sum_insured * float(c.get("limit") or total_limit) / total_limit for c in comps)), 3), "detail": detail}


def condition_templates(rules: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in rules.get("condition_templates", []):
        out.setdefault(c["risk"], []).append({"type": c["type"], "text": c["text"], "loading": float(c.get("loading") or 0)})
    return out


def renewal_adjustment(exp: dict, rules: dict) -> tuple[float, list[str]]:
    """续保经验费率：按上一保险期间的赔付率与橙色以上预警调整，无赔款且无重大预警给予优待。"""
    r = rules["renewal"]
    lr = float(exp.get("loss_ratio") or 0)
    major = int((exp.get("alerts") or {}).get("red", 0)) + int((exp.get("alerts") or {}).get("orange", 0))
    notes, adj = [], 0.0
    if lr >= r["loss_ratio_high"]:
        adj += r["loading_high"]
        notes.append(f"上期赔付率 {lr:.0%} 达到高赔付阈值，加费 {r['loading_high']:.0%}")
    elif lr >= r["loss_ratio_mid"]:
        adj += r["loading_mid"]
        notes.append(f"上期赔付率 {lr:.0%}，加费 {r['loading_mid']:.0%}")
    if major:
        a = min(r["alert_loading_cap"], major * r["alert_loading"])
        adj += a
        notes.append(f"上期橙色及以上预警 {major} 条，加费 {a:.0%}")
    if lr < 0.1 and not major:
        adj -= r["ncd_discount"]
        notes.append(f"上期无重大赔款与预警，无赔款优待 {r['ncd_discount']:.0%}")
    ri = exp.get("risk_index") or {}
    if ri.get("change") is not None:
        notes.append(f"风险指数由 {ri.get('start')} 变化至 {ri.get('end')}（{ri.get('change'):+}）")
    return round(adj, 4), notes


@registry.tool("recommend_decision", "决策建议", "按决策矩阵与核保规则给出标准承保、附条件承保、转人工或建议拒保，匹配附加条件并试算保费", permission="T0", zone="private")
def recommend_decision(ctx, **_) -> ToolResult:
    from ...services.config_center import cfg

    rules = cfg("uw.rules")
    th = rules["thresholds"]
    templates = condition_templates(rules)
    sc = ctx.workspace["scorecard"]
    fraud = ctx.workspace["fraud"]
    app = _app(ctx)
    missing = [m["material"] for m in ctx.workspace.get("material_check", []) if m["status"] != "provided"]
    veto = [h for h in fraud["rule_hits"] if h["pattern"] == "P04" and "营业执照" in h["rule"]]
    risks = (ctx.workspace.get("risk_report_ref") or {}).get("data", {}).get("risks", [])
    base_quote = quote_premium(ctx.workspace["product"]["package"], app["sum_insured"], ctx.workspace["enterprise"]["profile"], risks, ctx.workspace["product"]["lines"])
    reasons, conditions = [], []
    if veto or (sc["total"] >= th["reject_total"]) or fraud["probability"] >= th["reject_fraud"]:
        decision, label = "reject", f"拒保（需{rules['reject_min_level']}级以上审批）"
        reasons.append("命中一票否决规则" if veto else (f"总分 {sc['total']:.0f} 达到极高风险" if sc["total"] >= th["reject_total"] else f"欺诈概率 {fraud['probability']:.0%} 超过 {th['reject_fraud']:.0%}"))
    elif sc["total"] >= th["manual_total"] or fraud["probability"] >= th["manual_fraud"] or missing:
        decision, label = "manual", "转人工核保"
        if sc["total"] >= th["manual_total"]:
            reasons.append(f"总分 {sc['total']:.0f} 属高风险")
        if fraud["probability"] >= th["manual_fraud"]:
            reasons.append(f"欺诈概率 {fraud['probability']:.0%}，命中{'、'.join(p['name'] for p in fraud['patterns']) or '可疑特征'}")
        if missing:
            reasons.append("材料缺失：" + "、".join(missing))
    elif sc["total"] >= th["conditional_total"]:
        decision, label = "conditional", "附条件承保"
        top = sorted(sc["subs"], key=lambda s: -s["score"] * s["weight"])[:2]
        for s in top:
            conditions += templates.get(s["name"], [])[:1]
        reasons.append("主要风险来源：" + "、".join(f"{s['name']}（{s['score']:.0f} 分）" for s in top))
    else:
        decision, label = "standard", "标准承保"
        reasons.append(f"总分 {sc['total']:.0f} 属低风险，欺诈概率 {fraud['probability']:.1%}")
    loading = sum(c["loading"] for c in conditions)
    renewal = (app.get("declared") or {}).get("renewal")
    renewal_notes: list[str] = []
    if renewal:
        exp_adj, renewal_notes = renewal_adjustment(renewal, rules)
        loading += exp_adj
        reasons.append("续保增量评估：" + ("；".join(renewal_notes) if renewal_notes else "上期经验正常，不调整"))
    premium = round(base_quote["premium"] * (1 + loading), 2)
    level = next((int(a["level"]) for a in rules["authority"] if app["sum_insured"] <= float(a["max_sum_insured"]) and sc["total"] <= float(a["max_total"])), 3)
    if decision == "reject":
        level = max(level, int(rules["reject_min_level"]))
    auto = rules["auto_uw"]
    auto_eligible = bool(auto["enabled"]) and decision == "standard" and app["sum_insured"] <= float(auto["max_sum_insured"]) and (not auto["warm_only"] or ctx.state.get("start_mode") == "warm")
    ctx.workspace["decision"] = {"decision": decision, "label": label, "reasons": reasons, "conditions": conditions, "premium": premium, "base_premium": base_quote["premium"], "quote": base_quote, "loading": round(loading, 4), "approval_level": level, "auto_eligible": auto_eligible, "supplements": missing, "renewal": {"experience": renewal, "notes": renewal_notes} if renewal else None, "rules_version": f"V{_rules_version()}"}
    return ToolResult(summary=f"建议{label}；保费 {format_amount(premium)}（基础 {format_amount(base_quote['premium'])}" + (f"，条件与经验调整 {loading:+.0%}" if loading else "") + f"）；需 {level} 级审批" + ("；续保增量评估已纳入" if renewal else ""), data=ctx.workspace["decision"], level=1)


def _rules_version() -> int:
    from ...services.config_center import config_center

    return config_center.version("uw.rules")


@registry.tool("draft_uw_opinion", "起草核保意见书", "按模板起草核保意见书", permission="T1", zone="cloud")
def draft_uw_opinion(ctx, **_) -> ToolResult:
    from ...privacy.narrative import finalize_narrative

    out = finalize_narrative(
        model_gateway.invoke(
            "uw.opinion",
            {"enterprise": ctx.workspace["enterprise"]["name"], "product": ctx.workspace["product"]["name"], "sum_insured": _app(ctx)["sum_insured"], "scorecard": ctx.workspace["scorecard"], "fraud": {"probability": ctx.workspace["fraud"]["probability"], "rule_hits": ctx.workspace["fraud"]["rule_hits"]}, "decision": ctx.workspace["decision"], "supplements": ctx.workspace["decision"]["supplements"]},
            ctx=ctx,
            data_level=2,
        ),
        ctx.vault,
    )
    ctx.workspace["opinion"] = out
    return ToolResult(summary=f"核保意见书已起草（{len(out['paragraphs'])} 段）", data=out, level=2)


_ = parse_amounts
