"""产品管理：产品包完整性检查与发布配置、多级审批发布、版本快照与对比、产品表现追踪、停售条件与迭代。"""

from __future__ import annotations

import difflib
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..db import session_scope, utcnow
from ..knowledge.catalog import LINES
from ..models import Alert, Application, Artifact, ClaimCase, DesignProject, Notification, Policy, Product, ProductVersion, User
from . import approvals
from .config_center import cfg
from .notify import notify

STATUS_LABELS = {"draft": "待提交审批", "pending_approval": "审批中", "scheduled": "待上线", "published": "已发布", "suspended": "已停售", "rejected": "审批退回"}
STOP_METRICS = {"loss_ratio": "赔付率", "fraud_rate": "欺诈可疑率", "alert_rate": "单均预警数", "uw_pass_rate": "核保通过率", "policies": "累计件数", "premium": "累计保费（元）"}


class ProductAdminError(ValueError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def default_publish() -> dict:
    return {"online_at": (utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d"), "regions": list(cfg("params.dicts")["regions"]), "channels": ["直销", "经纪"], "stop_conditions": [{"metric": "loss_ratio", "op": ">=", "value": 0.85, "action": "suspend"}, {"metric": "fraud_rate", "op": ">=", "value": 0.3, "action": "review"}], "note": ""}


# ---------------------------------------------------------------- 完整性检查


def completeness(product_id: int) -> dict:
    from ..agents.clause_design.library import compliance_checks

    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise ProductAdminError("产品不存在")
        pkg = p.package or {}
        publish = p.publish or {}
        project = s.get(DesignProject, p.project_id) if p.project_id else None
        nodes = (project.pipeline or {}).get("nodes", {}) if project else {}
        clause_ref = nodes.get("clause", {})
        clause_art = s.scalars(select(Artifact).where(Artifact.artifact_key == clause_ref.get("artifact_key"), Artifact.version == clause_ref.get("version"))).first() if clause_ref.get("artifact_key") else None
    checks = []

    def add(key: str, name: str, ok: bool, detail: str, blocking: bool = True) -> None:
        checks.append({"key": key, "name": name, "passed": bool(ok), "detail": detail, "blocking": blocking})

    docs = pkg.get("docs", [])
    failed_rules = []
    for d in docs:
        try:
            failed_rules += [f"{d.get('name')} {c['code']} {c['name']}" for c in compliance_checks(d) if not c["passed"]]
        except (KeyError, TypeError):
            failed_rules.append(f"{d.get('name')} 条款结构不完整")
    add("clauses", "条款文本与强制合规规则", bool(docs) and not failed_rules, f"条款 {len(docs)} 份" + (f"；未通过：{'、'.join(failed_rules[:4])}" if failed_rules else "，C01–C16 全部通过"))
    issues = ((clause_art.data or {}).get("compliance") or {}).get("issues", []) if clause_art else []
    high = [i for i in issues if i.get("severity") == "high" and not i.get("resolved")]
    add("review", "合规审查严重问题", not high, f"严重问题 {len(high)} 项" if high else "无未整改的严重问题")
    comps = pkg.get("components", [])
    bad = [c.get("name") for c in comps if not c.get("gross_rate") or not c.get("band")]
    add("rates", "费率与报备区间", bool(comps) and not bad, f"费率方案 {len(comps)} 个" + (f"；缺少费率或区间：{'、'.join(bad)}" if bad else ""))
    covered_lines = {c.get("line") for c in comps}
    missing_lines = [LINES.get(l, {}).get("name", l) for l in p.lines if l not in covered_lines]
    add("line_rates", "每个险种都有费率", not missing_lines, "全部险种已定价" if not missing_lines else "未定价：" + "、".join(missing_lines))
    add("uw", "核保要求与材料清单", bool(pkg.get("uw_requirements")), f"核保要求 {len(pkg.get('uw_requirements', []))} 份")
    cm = pkg.get("claim_materials", {})
    add("claim_materials", "理赔材料清单", all(l in cm for l in p.lines) and "通用" in cm, f"覆盖 {len(cm)} 类")
    add("monitoring", "监测要点", bool(pkg.get("monitoring_points")), f"监测要点 {len(pkg.get('monitoring_points', []))} 项", blocking=False)
    add("actuarial", "精算报告结论", bool((pkg.get("actuarial") or {}).get("conclusion")), "已附精算报告" if (pkg.get("actuarial") or {}).get("conclusion") else "缺少精算结论")
    add("explanation", "条款通俗解读", bool(pkg.get("explanations")), f"{len(pkg.get('explanations', []))} 份", blocking=False)
    add("publish_time", "上线时间", bool(publish.get("online_at")), publish.get("online_at") or "未填写")
    add("regions", "销售区域", bool(publish.get("regions")), "、".join(publish.get("regions") or []) or "未填写")
    add("stop", "停售条件", bool(publish.get("stop_conditions")), "；".join(f"{STOP_METRICS.get(c['metric'], c['metric'])} {c['op']} {c['value']}" for c in publish.get("stop_conditions") or []) or "未设置")
    blocking = [c for c in checks if c["blocking"] and not c["passed"]]
    return {"product_id": product_id, "passed": not blocking, "checks": checks, "blocking": [c["name"] for c in blocking]}


def set_publish(product_id: int, publish: dict) -> dict:
    regions_all = set(cfg("params.dicts")["regions"])
    regions = [r for r in publish.get("regions") or [] if r in regions_all]
    if not regions:
        raise ProductAdminError("至少选择一个销售区域")
    try:
        datetime.fromisoformat(str(publish.get("online_at")))
    except ValueError as exc:
        raise ProductAdminError("上线时间格式应为 YYYY-MM-DD") from exc
    conds = []
    for c in publish.get("stop_conditions") or []:
        if c.get("metric") not in STOP_METRICS or c.get("op") not in (">=", "<="):
            raise ProductAdminError("停售条件的指标或比较方式无效")
        conds.append({"metric": c["metric"], "op": c["op"], "value": float(c["value"]), "action": c.get("action") if c.get("action") in ("suspend", "review") else "review"})
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise ProductAdminError("产品不存在")
        if p.status in ("pending_approval",):
            raise ProductAdminError("审批中的产品不能修改发布配置")
        p.publish = {"online_at": str(publish["online_at"])[:10], "regions": regions, "channels": publish.get("channels") or [], "stop_conditions": conds, "note": publish.get("note", "")}
        return p.publish


# ---------------------------------------------------------------- 版本


def snapshot(s, p: Product, status: str, note: str) -> None:
    v = s.scalars(select(ProductVersion).where(ProductVersion.product_id == p.id, ProductVersion.version == p.version)).first()
    if v is None:
        s.add(ProductVersion(product_id=p.id, version=p.version, name=p.name, lines=list(p.lines), package=p.package, publish=p.publish or {}, status=status, change_note=note))
    else:
        v.package, v.publish, v.status, v.name, v.lines = p.package, p.publish or {}, status, p.name, list(p.lines)
        if note:
            v.change_note = note


def versions(product_id: int) -> list[dict]:
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise ProductAdminError("产品不存在")
        if not s.scalars(select(ProductVersion).where(ProductVersion.product_id == product_id)).first():
            snapshot(s, p, p.status, "历史版本补登快照")
            s.flush()
        rows = s.scalars(select(ProductVersion).where(ProductVersion.product_id == product_id).order_by(ProductVersion.version.desc())).all()
        return [{"version": v.version, "name": v.name, "lines": v.lines, "status": v.status, "status_label": STATUS_LABELS.get(v.status, v.status), "change_note": v.change_note, "components": len((v.package or {}).get("components", [])), "docs": len((v.package or {}).get("docs", [])), "created_at": iso(v.created_at)} for v in rows]


def _articles(pkg: dict) -> dict[tuple, dict]:
    out = {}
    for d in pkg.get("docs", []):
        for ch in d.get("chapters", []):
            for a in ch.get("articles", []):
                out[(d.get("name"), ch.get("title"), a.get("title"))] = {"doc": d.get("name"), "chapter": ch.get("title"), "title": a.get("title"), "no": a.get("no"), "text": a.get("text", "")}
    return out


def compare(product_id: int, va: int, vb: int) -> dict:
    with session_scope() as s:
        a = s.scalars(select(ProductVersion).where(ProductVersion.product_id == product_id, ProductVersion.version == va)).first()
        b = s.scalars(select(ProductVersion).where(ProductVersion.product_id == product_id, ProductVersion.version == vb)).first()
        if a is None or b is None:
            raise ProductAdminError("版本不存在")
        pa, pb = a.package or {}, b.package or {}
        pub_a, pub_b = a.publish or {}, b.publish or {}
    arts_a, arts_b = _articles(pa), _articles(pb)
    clause_changes = []
    for k in sorted(set(arts_a) | set(arts_b), key=lambda x: tuple(str(i) for i in x)):
        x, y = arts_a.get(k), arts_b.get(k)
        if x and not y:
            clause_changes.append({"type": "removed", **x})
        elif y and not x:
            clause_changes.append({"type": "added", **y})
        elif x["text"] != y["text"]:
            ops = []
            sm = difflib.SequenceMatcher(None, x["text"], y["text"])
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                ops.append({"op": tag, "a": x["text"][i1:i2], "b": y["text"][j1:j2]})
            clause_changes.append({"type": "modified", **y, "old_text": x["text"], "diff": ops, "similarity": round(sm.ratio(), 3)})
    comps_a = {c.get("name"): c for c in pa.get("components", [])}
    comps_b = {c.get("name"): c for c in pb.get("components", [])}
    rate_changes = []
    for name in sorted(set(comps_a) | set(comps_b), key=str):
        x, y = comps_a.get(name), comps_b.get(name)
        if not x or not y:
            rate_changes.append({"name": name, "type": "added" if y else "removed", "gross_rate": (y or x).get("gross_rate")})
            continue
        fields = {}
        for f in ("gross_rate", "pure_rate", "deductible", "limit"):
            if x.get(f) != y.get(f):
                fields[f] = {"old": x.get(f), "new": y.get(f), "change": round((y.get(f) or 0) / x[f] - 1, 4) if x.get(f) else None}
        if x.get("band") != y.get("band"):
            fields["band"] = {"old": x.get("band"), "new": y.get("band")}
        rel_a = {(r.get("factor"), r.get("level")): r.get("relativity") for r in x.get("relativities", [])}
        rel_b = {(r.get("factor"), r.get("level")): r.get("relativity") for r in y.get("relativities", [])}
        rel = [{"factor": k[0], "level": k[1], "old": rel_a.get(k), "new": rel_b.get(k)} for k in sorted(set(rel_a) | set(rel_b), key=str) if rel_a.get(k) != rel_b.get(k)]
        if fields or rel:
            rate_changes.append({"name": name, "type": "modified", "fields": fields, "relativities": rel})
    publish_changes = {k: {"old": pub_a.get(k), "new": pub_b.get(k)} for k in sorted(set(pub_a) | set(pub_b)) if pub_a.get(k) != pub_b.get(k)}
    return {"a": va, "b": vb, "lines": {"added": [l for l in pb.get("lines", []) if l not in pa.get("lines", [])], "removed": [l for l in pa.get("lines", []) if l not in pb.get("lines", [])]}, "clauses": clause_changes, "rates": rate_changes, "publish": publish_changes, "summary": {"clauses_added": sum(1 for c in clause_changes if c["type"] == "added"), "clauses_removed": sum(1 for c in clause_changes if c["type"] == "removed"), "clauses_modified": sum(1 for c in clause_changes if c["type"] == "modified"), "rates_changed": len(rate_changes)}}


# ---------------------------------------------------------------- 审批发布


def submit(product_id: int, user: dict, note: str = "") -> dict:
    check = completeness(product_id)
    if not check["passed"]:
        raise ProductAdminError("产品包不完整，不能提交：" + "、".join(check["blocking"]))
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p.status not in ("draft", "rejected"):
            raise ProductAdminError(f"产品当前为「{STATUS_LABELS.get(p.status, p.status)}」，不能提交审批")
        snapshot(s, p, "pending_approval", note or "提交发布审批")
        title, pid, version, cold, lines = p.name, p.id, p.version, p.start_mode == "cold", list(p.lines)
        publish = dict(p.publish or {})
    req = approvals.start("product_publish", object_type="product", object_id=pid, title=f"产品发布：{title} V{version}", summary={"产品": title, "版本": f"V{version}", "险种": "、".join(LINES.get(l, {}).get("name", l) for l in lines), "启动方式": "冷启动" if cold else "热启动", "上线时间": publish.get("online_at"), "销售区域": "、".join(publish.get("regions") or []), "完整性检查": f"{sum(1 for c in check['checks'] if c['passed'])}/{len(check['checks'])} 项通过", "说明": note}, applicant=user, context={"cold_start": cold})
    with session_scope() as s:
        p = s.get(Product, product_id)
        p.status = "pending_approval"
        p.approval_id = req["id"]
        if p.project_id:
            _set_node(s, p.project_id, {"status": "waiting_approval", "approval_id": req["id"]}, f"产品包提交发布审批（{req['request_no']}）")
    return req


def _set_node(s, project_id: int, patch: dict, log_text: str) -> None:
    from .pipeline import _log

    project = s.get(DesignProject, project_id)
    if project is None:
        return
    pipeline = dict(project.pipeline)
    nodes = dict(pipeline["nodes"])
    nodes["approval"] = {**nodes.get("approval", {}), **patch}
    pipeline["nodes"] = nodes
    _log(pipeline, log_text)
    project.pipeline = pipeline
    if patch.get("status") == "done":
        project.status = "published"


def _approved(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        p = s.get(Product, int(req["object_id"]))
        if p is None or p.status != "pending_approval":
            return
        online = (p.publish or {}).get("online_at")
        go_live = datetime.fromisoformat(online) - timedelta(hours=8) if online else utcnow()
        p.approvals = list(p.approvals or []) + [{"by": d.get("user_id"), "name": d.get("name"), "stage": st["name"], "action": d["action"], "comment": d.get("comment"), "at": d.get("at")} for st in req["stages"] for d in st.get("decisions", [])]
        if go_live <= utcnow():
            p.status, p.published_at = "published", utcnow()
        else:
            p.status = "scheduled"
        snapshot(s, p, p.status, f"审批通过（{req['request_no']}）")
        if p.project_id:
            _set_node(s, p.project_id, {"status": "done", "by": actor["id"] if actor else None, "comment": "多级审批通过"}, f"产品审批通过：{'已发布' if p.status == 'published' else '待上线 ' + online}")
        notify(s, kind="product", title=f"产品{'已发布' if p.status == 'published' else '审批通过待上线'}：{p.name}", body=f"V{p.version}，{'、'.join((p.publish or {}).get('regions') or [])}", link=f"/products/{p.id}", role="service")
        s.add(Notification(role="designer", kind="product", title=f"产品审批通过：{p.name}", body=req["request_no"], link=f"/products/{p.id}"))


def _rejected(req: dict, actor: dict | None) -> None:
    with session_scope() as s:
        p = s.get(Product, int(req["object_id"]))
        if p is None or p.status != "pending_approval":
            return
        res = req.get("result") or {}
        p.status = "draft" if res.get("action") == "withdraw" else "rejected"
        p.approvals = list(p.approvals or []) + [{"name": res.get("by"), "stage": res.get("stage"), "action": res.get("action"), "comment": res.get("comment"), "at": iso(utcnow())}]
        snapshot(s, p, p.status, f"审批{'撤回' if p.status == 'draft' else '退回'}：{res.get('comment') or ''}")
        if p.project_id:
            _set_node(s, p.project_id, {"status": "rejected" if p.status == "rejected" else "waiting_submit", "comment": res.get("comment")}, f"产品审批{'撤回' if p.status == 'draft' else '退回'}：{res.get('comment') or ''}")


def go_live_due() -> int:
    n = 0
    with session_scope() as s:
        for p in s.scalars(select(Product).where(Product.status == "scheduled")).all():
            online = (p.publish or {}).get("online_at")
            if online and datetime.fromisoformat(online) - timedelta(hours=8) <= utcnow():
                p.status, p.published_at = "published", utcnow()
                snapshot(s, p, "published", "到达上线时间自动上线")
                notify(s, kind="product", title=f"产品已上线：{p.name}", body=online, link=f"/products/{p.id}", role="service")
                n += 1
    return n


# ---------------------------------------------------------------- 表现与停售


def performance(product_id: int) -> dict:
    now = utcnow()
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise ProductAdminError("产品不存在")
        apps = s.scalars(select(Application).where(Application.product_id == product_id)).all()
        pols = s.scalars(select(Policy).where(Policy.product_id == product_id)).all()
        pids = [x.id for x in pols]
        claims = s.scalars(select(ClaimCase).where(ClaimCase.policy_id.in_(pids))).all() if pids else []
        alerts = s.scalars(select(Alert).where(Alert.policy_id.in_(pids))).all() if pids else []
        reports = {a.ref_id: a for a in s.scalars(select(Artifact).where(Artifact.kind == "uw_report", Artifact.ref_type == "application", Artifact.ref_id.in_([a.id for a in apps]))).all()} if apps else {}
        premium = sum(x.premium for x in pols)
        earned = sum(x.premium * max(0, min((now - x.start_date).days, (x.end_date - x.start_date).days)) / max(1, (x.end_date - x.start_date).days) for x in pols)
        paid = sum(c.approved_amount or 0 for c in claims if c.status == "closed")
        outstanding = sum((c.suggested_amount or c.claimed_amount or 0) for c in claims if c.status not in ("closed",))
        decided = [a for a in apps if a.status in ("accepted", "rejected")]
        fraud_flags = 0
        for a in apps:
            rep = reports.get(a.id)
            prob = (((rep.data or {}).get("fraud") or {}).get("probability") if rep else None) or 0
            if prob >= cfg("uw.rules")["thresholds"]["manual_fraud"]:
                fraud_flags += 1
        months: dict[str, dict] = {}
        for x in pols:
            m = (x.created_at + timedelta(hours=8)).strftime("%Y-%m")
            row = months.setdefault(m, {"month": m, "premium": 0.0, "policies": 0, "claims": 0, "paid": 0.0})
            row["premium"] += x.premium
            row["policies"] += 1
        for c in claims:
            m = (c.created_at + timedelta(hours=8)).strftime("%Y-%m")
            row = months.setdefault(m, {"month": m, "premium": 0.0, "policies": 0, "claims": 0, "paid": 0.0})
            row["claims"] += 1
            row["paid"] += c.approved_amount or 0
        publish = dict(p.publish or {})
        name, status = p.name, p.status
    metrics = {
        "premium": round(premium, 2),
        "earned_premium": round(earned, 2),
        "policies": len(pols),
        "active_policies": sum(1 for x in pols if x.status == "active"),
        "applications": len(apps),
        "uw_pass_rate": round(sum(1 for a in decided if a.status == "accepted") / len(decided), 4) if decided else None,
        "claims": len(claims),
        "paid": round(paid, 2),
        "outstanding": round(outstanding, 2),
        "loss_ratio": round((paid + outstanding) / earned, 4) if earned else None,
        "alerts": len(alerts),
        "alert_rate": round(len(alerts) / len(pols), 3) if pols else None,
        "orange_plus": sum(1 for a in alerts if a.level in ("red", "orange")),
        "fraud_rate": round(fraud_flags / len(apps), 4) if apps else None,
    }
    triggered = []
    for c in publish.get("stop_conditions") or []:
        v = metrics.get(c["metric"])
        if v is None:
            continue
        hit = v >= c["value"] if c["op"] == ">=" else v <= c["value"]
        if hit:
            triggered.append({**c, "label": STOP_METRICS.get(c["metric"], c["metric"]), "current": v})
    return {"product_id": product_id, "name": name, "status": status, "metrics": metrics, "trend": sorted(months.values(), key=lambda r: r["month"]), "stop_conditions": publish.get("stop_conditions") or [], "triggered": triggered, "suggest_iteration": bool(triggered) or (metrics["loss_ratio"] or 0) >= 0.7}


def check_stop_conditions() -> list[dict]:
    """定时检查已发布产品的停售条件：动作为停售的自动停售并通知，动作为复核的提醒业务管理员。"""
    out = []
    with session_scope() as s:
        ids = [p.id for p in s.scalars(select(Product).where(Product.status == "published")).all()]
    for pid in ids:
        perf = performance(pid)
        if not perf["triggered"]:
            continue
        suspend = [t for t in perf["triggered"] if t["action"] == "suspend"]
        with session_scope() as s:
            p = s.get(Product, pid)
            text = "；".join(f"{t['label']} {t['current']} {t['op']} {t['value']}" for t in perf["triggered"])
            if suspend:
                p.status = "suspended"
                p.publish = {**(p.publish or {}), "suspended": {"at": iso(utcnow()), "reason": f"触发停售条件：{text}", "auto": True}}
                snapshot(s, p, "suspended", f"触发停售条件：{text}")
            notify(s, kind="product", title=f"产品{'已自动停售' if suspend else '触发复核条件'}：{p.name}", body=text, link=f"/products/{pid}", level="error" if suspend else "warning", role="admin")
        out.append({"product_id": pid, "triggered": perf["triggered"], "suspended": bool(suspend)})
    return out


def set_sale_status(product_id: int, action: str, reason: str) -> dict:
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise ProductAdminError("产品不存在")
        if action == "suspend":
            if p.status != "published":
                raise ProductAdminError("只有已发布产品可以停售")
            p.status = "suspended"
            p.publish = {**(p.publish or {}), "suspended": {"at": iso(utcnow()), "reason": reason, "auto": False}}
        elif action == "resume":
            if p.status != "suspended":
                raise ProductAdminError("只有已停售产品可以恢复销售")
            p.status = "published"
            p.publish = {**(p.publish or {}), "resumed": {"at": iso(utcnow()), "reason": reason}}
        else:
            raise ProductAdminError("无效的动作")
        snapshot(s, p, p.status, f"{'停售' if action == 'suspend' else '恢复销售'}：{reason}")
        notify(s, kind="product", title=f"产品{'停售' if action == 'suspend' else '恢复销售'}：{p.name}", body=reason, link=f"/products/{p.id}", level="warning", role="service")
        return {"status": p.status}


def start_iteration(product_id: int, reason: str, user_id: int) -> int:
    from .pipeline import create_project

    perf = performance(product_id)
    with session_scope() as s:
        p = s.get(Product, product_id)
        old = s.get(DesignProject, p.project_id) if p.project_id else None
        if old is None:
            raise ProductAdminError("产品没有关联的设计项目，无法发起迭代")
        args = dict(name=f"{old.name}（迭代 V{p.version + 1}）", project_type=old.project_type, track=old.track, enterprise_ids=list(old.enterprise_ids or []), target_lines=list(old.target_lines or []), config={**(old.config or {}), "iteration_of": {"product_id": p.id, "product": p.name, "version": p.version, "reason": reason, "metrics": perf["metrics"], "triggered": perf["triggered"]}})
    pid = create_project(**args, owner_id=user_id)
    return pid


def publish_direct(product_id: int, user_id: int, action: str = "approve", comment: str = "") -> dict:
    """演示与兼容入口：具备发布权限时一次性发布（跳过多级待办，审批记录仍留痕）。"""
    with session_scope() as s:
        p = s.get(Product, product_id)
        user = s.get(User, user_id)
        if p is None or user is None:
            raise ProductAdminError("产品或用户不存在")
        if p.status not in ("draft", "rejected", "pending_approval"):
            raise ProductAdminError("产品当前状态不能发布")
        record = {"by": user_id, "name": user.display_name, "level": user.approval_level, "action": action, "comment": comment, "at": iso(utcnow()), "direct": True}
        p.approvals = list(p.approvals or []) + [record]
        if action == "approve":
            if not p.publish:
                p.publish = default_publish()
            p.status = "published"
            p.published_at = utcnow()
        else:
            p.status = "rejected"
        snapshot(s, p, p.status, comment or ("直接发布" if action == "approve" else "退回"))
        project_id = p.project_id
        status = p.status
    if project_id:
        with session_scope() as s:
            _set_node(s, project_id, {"status": "done" if status == "published" else "rejected", "by": user_id, "comment": comment}, "产品已审批发布" if status == "published" else f"产品审批退回：{comment}")
    return {"status": status}


def register_handlers() -> None:
    approvals.register("product_publish", _approved, _rejected)


_ = func
