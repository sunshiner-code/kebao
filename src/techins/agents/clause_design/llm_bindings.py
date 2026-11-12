"""条款设计推理任务的大模型上下文与输出校验。"""

from __future__ import annotations

from ...knowledge.catalog import LINES
from ...knowledge.clause_library import INNOVATION_SKELETONS
from ...llm.tasks import attach
from .reasoning import innovate as builtin_innovate

FORBIDDEN = ("全额赔付", "无条件", "保证赔付", "百分之百")


def _clean(text: str) -> str:
    from .library import forbidden_words

    for w in sorted(set(FORBIDDEN) | set(forbidden_words()), key=len, reverse=True):
        text = text.replace(w, "按照本保险合同约定赔偿")
    return text.strip()


def _post_personalize(inputs: dict, out: dict) -> dict:
    arts = []
    for a in out.get("special_articles", []):
        title, text = str(a.get("title") or "").strip(), _clean(str(a.get("text") or ""))
        if title and len(text) >= 10:
            arts.append({"title": title[:40], "text": text})
    if not arts:
        raise ValueError("未生成有效的特别约定")
    return {"special_articles": arts[:8], "liability_notes": out.get("liability_notes", []), "rationale": str(out.get("rationale") or "")}


def _post_innovate(inputs: dict, out: dict) -> dict:
    skeleton = builtin_innovate(inputs)
    kinds = {"subject", "liability", "exclusion", "amount", "settlement", "definition"}
    llm_mods = {m.get("kind"): m for m in out.get("modules", []) if m.get("kind") in kinds and len(str(m.get("text") or "")) >= 20}
    if "liability" not in llm_mods:
        raise ValueError("创新条款缺少保险责任")
    merged = []
    for mod in skeleton["modules"]:
        candidate = llm_mods.get(mod["kind"])
        merged.append({"kind": mod["kind"], "title": str(candidate.get("title") or mod["title"]) if candidate else mod["title"], "text": _clean(str(candidate["text"])) if candidate else mod["text"]})
    if "definition" in llm_mods:
        merged.append({"kind": "definition", "title": str(llm_mods["definition"].get("title") or "创新条款释义"), "text": _clean(str(llm_mods["definition"]["text"]))})
    settlement = next(m for m in merged if m["kind"] == "settlement")
    if not any(x in settlement["text"] for x in ("=", "乘以", "×")):
        settlement["text"] += "\n" + next(m for m in skeleton["modules"] if m["kind"] == "settlement")["text"]
    exclusion = next(m for m in merged if m["kind"] == "exclusion")
    if "投保前" not in exclusion["text"]:
        exclusion["text"] += "\n" + next(m for m in skeleton["modules"] if m["kind"] == "exclusion")["text"]
    return {"modules": merged, "pending_points": [str(p) for p in out.get("pending_points", [])][:6] or skeleton["pending_points"], "parametric_trigger": str(out.get("parametric_trigger") or skeleton["parametric_trigger"])}


def _post_compliance(inputs: dict, out: dict) -> dict:
    issues = []
    for i in out.get("issues", []):
        if i.get("severity") not in ("high", "medium", "low"):
            continue
        issues.append({k: str(i.get(k) or "") for k in ("line", "article", "severity", "issue", "basis", "suggestion")})
    return {"issues": issues}


def _post_review(inputs: dict, out: dict) -> dict:
    scores = {}
    for key in ("personalization", "clarity", "fairness", "payability"):
        item = (out.get("scores") or {}).get(key)
        if not isinstance(item, dict) or "score" not in item:
            raise ValueError(f"评审缺少维度 {key}")
        scores[key] = {"score": max(1.0, min(5.0, float(item["score"]))), "reason": str(item.get("reason") or "")}
    return {"scores": scores}


def _post_explain(inputs: dict, out: dict) -> dict:
    lines = {d["doc_id"] for d in inputs.get("docs", [])}
    items = [e for e in out.get("explanations", []) if e.get("line") in lines]
    if len(items) < len(lines):
        raise ValueError("通俗解读缺少险种")
    return {"explanations": [{**e, **{k: _clean(str(e.get(k) or "")) for k in ("covers", "not_covered", "how_to_claim")}} for e in items]}


attach("cd.personalize", postprocess=_post_personalize, guidance="特别约定应列明标的、项目、人员岗位、供应商或系统等企业特定信息，并对高风险提出风险管理约定；不得出现承诺性表述，不得扩大保险责任")
attach("cd.innovate", llm_context=lambda i: {"条款骨架": INNOVATION_SKELETONS.get(i["spec"]["loss_form"]), "相近险种": {k: v["name"] for k, v in LINES.items()}}, postprocess=_post_innovate, guidance="modules 至少包含 subject、liability、exclusion、amount、settlement 五类；赔偿处理必须给出计算公式与认定依据；责任免除必须包含投保前已存在事由与经营决策导致的损失")
attach("cd.compliance_review", postprocess=_post_compliance, guidance="article 使用输入中的条号（如第十二条），line 使用险种名称；只输出确有依据的问题")
attach("cd.review", postprocess=_post_review)
attach("cd.explain", postprocess=_post_explain, guidance="line 使用输入中每份条款的 doc_id；语言通俗，不替代条款，不作承诺")
