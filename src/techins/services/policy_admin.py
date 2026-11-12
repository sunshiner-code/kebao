"""保单服务：批改（保额、期间、被保险人信息、特别约定）与审批、续保（到期提醒、经验数据、复用核保智能体增量评估、续保报价）、电子保单与单证。"""

from __future__ import annotations

import hashlib
import html
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..knowledge.catalog import LINES
from ..models import (
    Alert,
    Application,
    Artifact,
    ClaimCase,
    Endorsement,
    Enterprise,
    Incident,
    Policy,
    PolicyDocument,
    Product,
    Renewal,
)
from . import approvals
from .config_center import cfg
from .notify import notify

CHANGE_TYPES = {"sum_insured": "保险金额变更", "period": "保险期间延长", "insured_info": "被保险人信息变更", "special": "特别约定变更"}


class PolicyAdminError(ValueError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _no(s, model, kind: str, default: str) -> str:
    prefix = cfg("params.dicts")["numbering"].get(kind, default)
    n = (s.scalar(select(func.count(model.id))) or 0) + 1
    return f"{prefix}{utcnow():%Y%m%d}{n:04d}"


def local(dt: datetime | None, fmt: str = "%Y-%m-%d") -> str:
    return (dt + timedelta(hours=8)).strftime(fmt) if dt else "—"


# ---------------------------------------------------------------- 批改


def endorsement_dict(e: Endorsement) -> dict:
    return {"id": e.id, "endorse_no": e.endorse_no, "policy_id": e.policy_id, "change_type": e.change_type, "change_label": CHANGE_TYPES.get(e.change_type, e.change_type), "before": e.before, "after": e.after, "reason": e.reason, "premium_delta": e.premium_delta, "calc": e.calc, "status": e.status, "approval_id": e.approval_id, "created_at": iso(e.created_at), "effective_at": iso(e.effective_at)}


def calc_endorsement(policy: Policy, change_type: str, after: dict) -> tuple[dict, dict, float, dict]:
    now = utcnow()
    total_days = max(1, (policy.end_date - policy.start_date).days)
    remaining = max(0, (policy.end_date - max(now, policy.start_date)).days)
    ratio = remaining / total_days
    if change_type == "sum_insured":
        new = float(after.get("sum_insured") or 0)
        if new <= 0:
            raise PolicyAdminError("新保险金额必须大于 0")
        if new == policy.sum_insured:
            raise PolicyAdminError("新保险金额与原保险金额相同")
        delta = policy.premium * (new / policy.sum_insured - 1) * ratio
        calc = {"formula": "原保费 ×（新保额 ÷ 原保额 − 1）× 剩余期间比例", "premium": policy.premium, "old": policy.sum_insured, "new": new, "remaining_days": remaining, "total_days": total_days, "ratio": round(ratio, 4)}
        return {"sum_insured": policy.sum_insured}, {"sum_insured": new}, round(delta, 2), calc
    if change_type == "period":
        days = int(after.get("extend_days") or 0)
        if days <= 0 or days > 90:
            raise PolicyAdminError("期间延长需在 1–90 天之间")
        delta = policy.premium * days / 365
        new_end = policy.end_date + timedelta(days=days)
        calc = {"formula": "原保费 × 延长天数 ÷ 365", "premium": policy.premium, "extend_days": days}
        return {"end_date": iso(policy.end_date)}, {"end_date": iso(new_end), "extend_days": days}, round(delta, 2), calc
    if change_type == "insured_info":
        fields = {k: v for k, v in after.items() if k in ("contact_name", "contact_phone", "address", "email") and v}
        if not fields:
            raise PolicyAdminError("请至少填写一项变更信息")
        before = {k: ((policy.coverage or {}).get("insured") or {}).get(k) for k in fields}
        return before, fields, 0.0, {"formula": "信息变更不涉及保费"}
    if change_type == "special":
        text = str(after.get("text") or "").strip()
        op = after.get("op", "add")
        if not text:
            raise PolicyAdminError("请填写特别约定内容")
        conds = list((policy.coverage or {}).get("conditions") or [])
        if op == "remove" and not any(c.get("text") == text for c in conds):
            raise PolicyAdminError("保单中没有该特别约定")
        loading = float(after.get("loading") or 0)
        delta = policy.premium * loading * ratio
        return {"conditions": [c.get("text") for c in conds]}, {"op": op, "text": text, "loading": loading}, round(delta, 2), {"formula": "原保费 × 费率调整 × 剩余期间比例", "loading": loading, "ratio": round(ratio, 4)}
    raise PolicyAdminError("未知的批改类型")


def create_endorsement(policy_id: int, change_type: str, after: dict, reason: str, user: dict) -> dict:
    if not reason.strip():
        raise PolicyAdminError("请填写批改原因")
    with session_scope() as s:
        p = s.get(Policy, policy_id)
        if p is None:
            raise PolicyAdminError("保单不存在")
        if p.status != "active":
            raise PolicyAdminError("只有有效保单可以批改")
        if s.scalars(select(Endorsement).where(Endorsement.policy_id == policy_id, Endorsement.status == "pending")).first():
            raise PolicyAdminError("该保单有审批中的批改，请处理后再发起")
        before, after_v, delta, calc = calc_endorsement(p, change_type, after)
        e = Endorsement(endorse_no=_no(s, Endorsement, "endorsement", "PG"), policy_id=policy_id, change_type=change_type, before=before, after=after_v, reason=reason, premium_delta=delta, calc=calc, status="pending", created_by=user["id"])
        s.add(e)
        s.flush()
        eid, no, pno = e.id, e.endorse_no, p.policy_no
        ent = s.get(Enterprise, p.enterprise_id).name
    req = approvals.start("endorsement", object_type="endorsement", object_id=eid, title=f"批改 {no}：{pno} {CHANGE_TYPES[change_type]}", summary={"保单": pno, "被保险人": ent, "批改类型": CHANGE_TYPES[change_type], "变更前": before, "变更后": after_v, "保费调整（元）": delta, "原因": reason}, applicant=user, context={"amount": abs(delta)})
    with session_scope() as s:
        s.get(Endorsement, eid).approval_id = req["id"]
        return endorsement_dict(s.get(Endorsement, eid))


def _endorsement_approved(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        e = s.get(Endorsement, int(req["object_id"]))
        if e is None or e.status != "pending":
            return
        p = s.get(Policy, e.policy_id)
        cov = dict(p.coverage or {})
        if e.change_type == "sum_insured":
            p.sum_insured = float(e.after["sum_insured"])
        elif e.change_type == "period":
            p.end_date = datetime.fromisoformat(e.after["end_date"].rstrip("Z"))
        elif e.change_type == "insured_info":
            cov["insured"] = {**(cov.get("insured") or {}), **e.after}
        elif e.change_type == "special":
            conds = [c for c in (cov.get("conditions") or []) if c.get("text") != e.after["text"]]
            if e.after["op"] == "add":
                conds.append({"type": "special", "text": e.after["text"], "loading": e.after.get("loading", 0), "endorsement": e.endorse_no})
            cov["conditions"] = conds
        p.coverage = cov
        p.premium = round(p.premium + e.premium_delta, 2)
        e.status = "effective"
        e.effective_at = utcnow()
        notify(s, kind="system", title=f"批改生效：{e.endorse_no}", body=f"{CHANGE_TYPES[e.change_type]}，保费调整 {e.premium_delta:+.2f} 元", link=f"/policies/{p.id}", role="service")
        pid, eid = p.id, e.id
    generate_document(pid, "endorsement", ref_id=eid, user_id=actor["id"] if actor else None)


def _endorsement_rejected(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        e = s.get(Endorsement, int(req["object_id"]))
        if e is not None and e.status == "pending":
            e.status = "withdrawn" if (req.get("result") or {}).get("action") == "withdraw" else "rejected"


# ---------------------------------------------------------------- 续保


def experience(s, policy: Policy) -> dict:
    """续保经验数据：已决与未决赔款、赔付率、预警与事故、风险指数变化（来自最近一期风险管理报告）。"""
    claims = s.scalars(select(ClaimCase).where(ClaimCase.policy_id == policy.id)).all()
    paid = sum(c.approved_amount or 0 for c in claims if c.status == "closed")
    outstanding = sum((c.suggested_amount or c.claimed_amount or 0) for c in claims if c.status not in ("closed", "rejected"))
    elapsed = max(1, min((utcnow() - policy.start_date).days, (policy.end_date - policy.start_date).days))
    earned = policy.premium * elapsed / max(1, (policy.end_date - policy.start_date).days)
    alerts = s.scalars(select(Alert).where(Alert.policy_id == policy.id)).all()
    incidents = s.scalar(select(func.count(Incident.id)).where(Incident.policy_id == policy.id)) or 0
    report = s.scalars(select(Artifact).where(Artifact.kind == "risk_mgmt_report", Artifact.ref_id == policy.id).order_by(Artifact.id.desc())).first()
    ri = ((report.data or {}).get("stats") or {}).get("risk_index") if report else None
    fp = sum(1 for a in alerts if a.status == "false_positive")
    return {
        "premium": policy.premium,
        "earned_premium": round(earned, 2),
        "paid": round(paid, 2),
        "outstanding": round(outstanding, 2),
        "loss_ratio": round((paid + outstanding) / earned, 4) if earned else 0.0,
        "claims": len(claims),
        "alerts": {lv: sum(1 for a in alerts if a.level == lv and a.status != "false_positive") for lv in ("red", "orange", "yellow")},
        "false_positive_alerts": fp,
        "incidents": incidents,
        "risk_index": ({"start": ri.get("start"), "end": ri.get("end"), "change": ri.get("change"), "level": ri.get("level")} if ri else None),
        "report_key": report.artifact_key if report else None,
        "policy_no": policy.policy_no,
        "policy_id": policy.id,
    }


def renewal_dict(r: Renewal, s) -> dict:
    p = s.get(Policy, r.policy_id)
    ent = s.get(Enterprise, p.enterprise_id) if p else None
    app = s.get(Application, r.application_id) if r.application_id else None
    return {"id": r.id, "policy_id": r.policy_id, "policy_no": p.policy_no if p else None, "enterprise": ent.name if ent else None, "enterprise_id": ent.id if ent else None, "premium": p.premium if p else None, "status": r.status, "due_date": iso(r.due_date), "days_left": (r.due_date - utcnow()).days, "experience": r.experience, "application_id": r.application_id, "application_status": app.status if app else None, "uw_run_id": r.uw_run_id, "quote": r.quote or ((app.decision or {}) if app else {}), "new_policy_id": r.new_policy_id, "notes": r.notes, "created_at": iso(r.created_at)}


def scan_renewals(days: int | None = None) -> int:
    days = days or int(cfg("params.dicts")["system"]["renewal_notice_days"])
    horizon = utcnow() + timedelta(days=days)
    created = 0
    with session_scope() as s:
        for p in s.scalars(select(Policy).where(Policy.status == "active", Policy.end_date <= horizon)).all():
            if s.scalars(select(Renewal).where(Renewal.policy_id == p.id)).first():
                continue
            r = Renewal(policy_id=p.id, status="reminded", due_date=p.end_date, experience=experience(s, p), notes=[{"at": iso(utcnow()), "text": f"保单将于 {local(p.end_date)} 到期，已生成续保提醒"}])
            s.add(r)
            ent = s.get(Enterprise, p.enterprise_id)
            notify(s, kind="renewal", title=f"续保提醒：{p.policy_no} {ent.name}", body=f"到期日 {local(p.end_date)}，赔付率 {r.experience['loss_ratio']:.0%}", link="/policies/renewals", role="service")
            created += 1
        for p in s.scalars(select(Policy).where(Policy.status == "active", Policy.end_date < utcnow())).all():
            p.status = "expired"
    return created


def start_renewal_assessment(renewal_id: int, user: dict) -> dict:
    from .business import create_application, start_underwriting

    with session_scope() as s:
        r = s.get(Renewal, renewal_id)
        if r is None:
            raise PolicyAdminError("续保记录不存在")
        if r.status not in ("reminded", "declined"):
            raise PolicyAdminError("续保评估已发起")
        p = s.get(Policy, r.policy_id)
        old = s.get(Application, p.application_id) if p.application_id else None
        prod = s.get(Product, p.product_id)
        if prod.status != "published":
            raise PolicyAdminError("原产品已停售，请选择新产品重新投保")
        r.experience = experience(s, p)
        declared = dict((old.declared if old else {}) or {})
        declared.update({"lines": (p.coverage or {}).get("lines") or prod.lines, "renewal": r.experience, "renewal_of": p.policy_no, "renewal_of_policy_id": p.id, "authorizations": (p.coverage or {}).get("data_authorizations") or declared.get("authorizations") or {}})
        args = dict(enterprise_id=p.enterprise_id, product_id=p.product_id, tier=(p.coverage or {}).get("tier") or (old.tier if old else "standard"), sum_insured=p.sum_insured, period_months=12, declared=declared, contacts=(old.contacts if old else {}) or {}, intermediary=old.intermediary if old else None)
    app_id = create_application(**args, user_id=user["id"])
    from ..models import Material

    with session_scope() as s:
        p = s.get(Policy, s.get(Renewal, renewal_id).policy_id)
        if p.application_id:
            for m in s.scalars(select(Material).where(Material.owner_type == "application", Material.owner_id == p.application_id)).all():
                s.add(Material(enterprise_id=m.enterprise_id, owner_type="application", owner_id=app_id, name=m.name, material_type=m.material_type, data_level=m.data_level, detected_level=m.detected_level, file_path=m.file_path, mime=m.mime, text=m.text, parsed=m.parsed, status=m.status, uploaded_by=user["id"]))
    run_id = start_underwriting(app_id, user["id"])
    with session_scope() as s:
        r = s.get(Renewal, renewal_id)
        r.status = "assessing"
        r.application_id = app_id
        r.uw_run_id = run_id
        r.notes = list(r.notes or []) + [{"at": iso(utcnow()), "text": f"发起续保核保（增量评估），运行 #{run_id}", "by": user["display_name"]}]
        return renewal_dict(r, s)


def decline_renewal(renewal_id: int, reason: str, user: dict) -> dict:
    with session_scope() as s:
        r = s.get(Renewal, renewal_id)
        if r is None:
            raise PolicyAdminError("续保记录不存在")
        r.status = "declined"
        r.notes = list(r.notes or []) + [{"at": iso(utcnow()), "text": f"不续保：{reason}", "by": user["display_name"]}]
        return renewal_dict(r, s)


def on_renewal_policy_issued(old_policy_id: int, new_policy_id: int | None, decision: str) -> None:
    with session_scope() as s:
        r = s.scalars(select(Renewal).where(Renewal.policy_id == old_policy_id)).first()
        if r is None:
            return
        app = s.get(Application, r.application_id) if r.application_id else None
        r.quote = dict(app.decision or {}) if app else {}
        if new_policy_id:
            r.status, r.new_policy_id = "issued", new_policy_id
            r.notes = list(r.notes or []) + [{"at": iso(utcnow()), "text": f"续保出单，新保单 #{new_policy_id}"}]
        else:
            r.status = "declined"
            r.notes = list(r.notes or []) + [{"at": iso(utcnow()), "text": f"续保核保结论：{decision}"}]


def list_renewals() -> list[dict]:
    with session_scope() as s:
        return [renewal_dict(r, s) for r in s.scalars(select(Renewal).order_by(Renewal.due_date)).all()]


# ---------------------------------------------------------------- 电子保单与单证

DOC_TYPES = {"policy": "电子保单", "endorsement": "批单", "invoice": "保费发票", "settlement": "理算书", "renewal_notice": "续保通知书"}

CSS = """
body{font-family:'PingFang SC','Microsoft YaHei',sans-serif;color:#0f172a;margin:0;background:#f1f5f9}
.page{max-width:820px;margin:24px auto;background:#fff;padding:40px 48px;box-shadow:0 1px 3px rgba(0,0,0,.08);position:relative}
h1{font-size:22px;text-align:center;margin:0 0 4px} .sub{text-align:center;color:#64748b;font-size:12px;margin-bottom:20px}
h2{font-size:14px;border-left:3px solid #4f46e5;padding-left:8px;margin:22px 0 8px}
table{width:100%;border-collapse:collapse;font-size:13px} td,th{border:1px solid #e2e8f0;padding:6px 8px;text-align:left;vertical-align:top} th{background:#f8fafc;width:24%;font-weight:500;color:#475569}
.foot{margin-top:28px;font-size:11px;color:#64748b;border-top:1px dashed #cbd5e1;padding-top:10px}
.seal{position:absolute;right:56px;bottom:70px;width:110px;height:110px;border:3px solid rgba(220,38,38,.75);border-radius:50%;color:rgba(220,38,38,.85);display:flex;align-items:center;justify-content:center;text-align:center;font-size:12px;transform:rotate(-14deg)}
.ai{margin-top:10px;font-size:11px;color:#7c3aed}
.amount{font-weight:600}
"""


def _rows(pairs: list[tuple[str, str]]) -> str:
    return "<table>" + "".join(f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in pairs) + "</table>"


def _render(template_key: str, title: str, sub: str, sections: dict[str, str], doc_no: str, digest_src: str) -> tuple[str, str]:
    tpl = cfg("report.templates")["templates"].get(template_key) or {"sections": list(sections), "header": title, "footer": "", "mark_ai": False}
    order = [s for s in tpl.get("sections", []) if s in sections] + [s for s in sections if s not in tpl.get("sections", [])]
    digest = hashlib.sha256((digest_src + doc_no).encode()).hexdigest()
    body = "".join(f"<h2>{html.escape(name)}</h2>{sections[name]}" for name in order)
    mark = f'<div class="ai">{html.escape(cfg("compliance.rules")["ai_mark"])}</div>' if tpl.get("mark_ai") else ""
    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class="page">
<div class="sub">{html.escape(tpl.get('header') or '')}</div><h1>{html.escape(title)}</h1><div class="sub">{html.escape(sub)}</div>{body}
<div class="foot">单证编号：{doc_no} · 验真码：{digest[:16].upper()} · 生成时间：{local(utcnow(), '%Y-%m-%d %H:%M')}（北京时间）<br>{html.escape(tpl.get('footer') or '')}</div>{mark}
<div class="seal">科创财产保险<br>电子签章<br>{digest[:6].upper()}</div></div></body></html>"""
    return doc, digest


def generate_document(policy_id: int, doc_type: str, *, ref_id: int | None = None, user_id: int | None = None) -> dict:
    if doc_type not in DOC_TYPES:
        raise PolicyAdminError("未知的单证类型")
    with session_scope() as s:
        p = s.get(Policy, policy_id)
        if p is None:
            raise PolicyAdminError("保单不存在")
        ent = s.get(Enterprise, p.enterprise_id)
        prod = s.get(Product, p.product_id)
        cov = p.coverage or {}
        insured = {**{"contact_name": ent.profile.get("legal_rep", ""), "address": ent.profile.get("address", "")}, **(cov.get("insured") or {})}
        doc_no = _no(s, PolicyDocument, "document", "DZ")
        if doc_type == "policy":
            lines = cov.get("lines") or prod.lines
            line_rows = "".join(f"<tr><td>{html.escape(LINES.get(l, {}).get('name', l))}</td><td>{html.escape(next((d['name'] for d in cov.get('docs', []) if l in d.get('lines', [])), '—'))}</td></tr>" for l in lines)
            conds = "".join(f"<li>{html.escape(c.get('text', ''))}</li>" for c in cov.get("conditions") or []) or "<li>无</li>"
            sections = {
                "保单信息": _rows([("保单号", p.policy_no), ("产品", f"{html.escape(prod.name)}（{prod.code} V{prod.version}）"), ("方案档位", html.escape(str(cov.get("tier") or "standard")))]),
                "投保人与被保险人": _rows([("名称", html.escape(ent.name)), ("统一社会信用代码", ent.credit_code), ("联系人", html.escape(str(insured.get("contact_name") or ""))), ("地址", html.escape(str(insured.get("address") or "")))]),
                "承保险种与保额": "<table><tr><th>险种</th><th>适用条款</th></tr>" + line_rows + f"</table><p class='amount'>保险金额：{p.sum_insured:,.2f} 元</p>",
                "特别约定": f"<ul>{conds}</ul>",
                "保险期间": f"自 {local(p.start_date)} 零时起至 {local(p.end_date)} 二十四时止",
                "保费": f"<p class='amount'>保险费：人民币 {p.premium:,.2f} 元</p>",
            }
            title, sub, template = "科技保险电子保单", f"{prod.name} · {p.policy_no}", "policy"
        elif doc_type == "endorsement":
            e = s.get(Endorsement, ref_id) if ref_id else s.scalars(select(Endorsement).where(Endorsement.policy_id == policy_id, Endorsement.status == "effective").order_by(Endorsement.id.desc())).first()
            if e is None:
                raise PolicyAdminError("没有已生效的批改")
            sections = {
                "批改事项": _rows([("批单号", e.endorse_no), ("保单号", p.policy_no), ("批改类型", CHANGE_TYPES.get(e.change_type, e.change_type)), ("批改原因", html.escape(e.reason))]),
                "变更前后": _rows([("变更前", html.escape(str(e.before))), ("变更后", html.escape(str(e.after)))]),
                "保费调整": f"<p class='amount'>{'加收' if e.premium_delta >= 0 else '退还'}保险费 {abs(e.premium_delta):,.2f} 元</p><p>{html.escape(e.calc.get('formula', ''))}</p>",
                "生效日期": local(e.effective_at),
            }
            title, sub, template = "批单", f"{p.policy_no} · {e.endorse_no}", "endorsement"
        elif doc_type == "invoice":
            amount = p.premium
            excl = round(amount / 1.06, 2)
            sections = {
                "购买方": _rows([("名称", html.escape(ent.name)), ("纳税人识别号", ent.credit_code)]),
                "项目": _rows([("项目名称", f"*保险服务*{html.escape(prod.name)}"), ("保单号", p.policy_no)]),
                "金额与税额": _rows([("金额（不含税）", f"{excl:,.2f} 元"), ("税率", "6%"), ("税额", f"{amount - excl:,.2f} 元"), ("价税合计", f"<span class='amount'>{amount:,.2f} 元</span>")]),
            }
            title, sub, template = "增值税电子普通发票", f"{p.policy_no}", "invoice"
        elif doc_type == "settlement":
            c = s.get(ClaimCase, ref_id) if ref_id else s.scalars(select(ClaimCase).where(ClaimCase.policy_id == policy_id).order_by(ClaimCase.id.desc())).first()
            if c is None or not c.result_artifact_key:
                raise PolicyAdminError("没有可出具理算书的理赔案件")
            art = s.scalars(select(Artifact).where(Artifact.artifact_key == c.result_artifact_key).order_by(Artifact.version.desc())).first()
            data = art.data if art else {}
            adj = data.get("adjustment") or {}
            liab = data.get("liability") or {}
            steps = "".join(f"<tr><td>{html.escape(str(st.get('step', '')))}</td><td>{float(st.get('amount') or 0):,.2f}</td><td>{html.escape(str(st.get('basis', '')))}</td></tr>" for st in adj.get("steps", []))
            sections = {
                "案件概况": _rows([("案件号", c.claim_no), ("保单号", p.policy_no), ("被保险人", html.escape(ent.name)), ("申报金额", f"{c.claimed_amount:,.2f} 元")]),
                "责任认定": f"<p>近因：{html.escape(str(liab.get('proximate_cause', '')))}</p><p>是否属于保险事故：{'是' if liab.get('insured_event', True) else '否'}</p>",
                "损失核定": f"<p>核定损失 {float(adj.get('assessed') or 0):,.2f} 元</p>",
                "理算过程": f"<table><tr><th>步骤</th><th>金额（元）</th><th>依据</th></tr>{steps}</table>",
                "赔付建议": f"<p class='amount'>赔付金额 {float(c.approved_amount or adj.get('payout') or 0):,.2f} 元</p>",
            }
            title, sub, template = "理赔理算书", f"{c.claim_no}", "settlement"
            ref_id = c.id
        else:
            r = s.scalars(select(Renewal).where(Renewal.policy_id == policy_id)).first()
            ex = (r.experience if r else None) or experience(s, p)
            sections = {
                "续保事项": _rows([("保单号", p.policy_no), ("到期日", local(p.end_date)), ("被保险人", html.escape(ent.name))]),
                "保险期间经验": _rows([("已赚保费", f"{ex['earned_premium']:,.2f} 元"), ("已决与未决赔款", f"{ex['paid'] + ex['outstanding']:,.2f} 元"), ("赔付率", f"{ex['loss_ratio']:.1%}"), ("预警（红/橙/黄）", f"{ex['alerts']['red']}/{ex['alerts']['orange']}/{ex['alerts']['yellow']}")]),
                "续保报价": f"<p class='amount'>{('续保保费 ' + format(float((r.quote or {}).get('premium') or 0), ',.2f') + ' 元') if r and r.quote else '续保评估完成后提供报价'}</p>",
            }
            title, sub, template = "续保通知书", p.policy_no, "risk_notice"
        doc_html, digest = _render(template, title, sub, sections, doc_no, f"{policy_id}|{doc_type}|{ref_id}")
        d = PolicyDocument(doc_no=doc_no, policy_id=policy_id, doc_type=doc_type, ref_id=ref_id, title=f"{DOC_TYPES[doc_type]} {sub}", html=doc_html, digest=digest, created_by=user_id)
        s.add(d)
        s.flush()
        return {"id": d.id, "doc_no": d.doc_no, "doc_type": doc_type, "type_label": DOC_TYPES[doc_type], "title": d.title, "digest": digest[:16].upper(), "created_at": iso(d.created_at)}


def list_documents(policy_id: int) -> list[dict]:
    with session_scope() as s:
        return [{"id": d.id, "doc_no": d.doc_no, "doc_type": d.doc_type, "type_label": DOC_TYPES.get(d.doc_type, d.doc_type), "title": d.title, "digest": d.digest[:16].upper(), "created_at": iso(d.created_at)} for d in s.scalars(select(PolicyDocument).where(PolicyDocument.policy_id == policy_id).order_by(PolicyDocument.id.desc())).all()]


def register_handlers() -> None:
    approvals.register("endorsement", _endorsement_approved, _endorsement_rejected)
