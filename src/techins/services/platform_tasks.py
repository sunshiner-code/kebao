"""平台功能的推理任务：拜访纪要要点抽取、带引用的知识问答、协作面板回答、条款局部再生成。

与智能体推理任务一样经模型网关按"任务类型 × 数据级别"路由；内置实现是兜底链的最后一环。
"""

from __future__ import annotations

import re

from ..llm.tasks import reasoning_task

_SENT = re.compile(r"[^。！？；\n]+[。！？；]?")

TOPICS = {
    "经营": ["营收", "订单", "融资", "现金", "亏损", "扩产", "裁员", "客户", "回款", "利润"],
    "技术": ["研发", "中试", "良率", "技术路线", "里程碑", "延期", "专利", "样机", "验证"],
    "设备": ["设备", "振动", "维保", "停机", "故障", "采购", "产线", "轴承", "更换"],
    "人员": ["离职", "核心", "团队", "招聘", "骨干", "首席"],
    "信息安全": ["漏洞", "攻击", "等保", "数据", "勒索", "外传", "日志"],
    "合规": ["处罚", "整改", "诉讼", "监管", "起诉", "仲裁"],
}
NEGATIVE = ["延期", "下降", "离职", "故障", "处罚", "诉讼", "紧张", "风险", "暂停", "亏损", "漏洞", "投诉", "推迟", "短缺", "断供", "超支", "不及预期", "未完成", "停机"]
POSITIVE = ["增长", "突破", "顺利", "完成", "获批", "签约", "提升", "达成", "中标", "量产"]
COMMIT = ["将", "计划", "预计", "下周", "下月", "承诺", "拟", "安排", "准备"]
DUE = re.compile(r"(\d{1,2}月\d{1,2}日|\d{1,2}月底|下周[一二三四五六日]?|下月|本月底|季度末|年底|\d+\s*天内|\d+\s*周内)")


def sentences(text: str) -> list[str]:
    return [m.group(0).strip() for m in _SENT.finditer(text or "") if len(m.group(0).strip()) >= 4]


def bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return {t[i : i + 2] for i in range(len(t) - 1)}


def overlap(a: str, b: str) -> float:
    x, y = bigrams(a), bigrams(b)
    return len(x & y) / (len(x) ** 0.5 * len(y) ** 0.5) if x and y else 0.0


@reasoning_task(
    "cm.contact_summary",
    "TT-03",
    "从客户拜访纪要或访谈转写中抽取要点、风险信号、待跟进事项与总体倾向",
    '{"key_points": ["要点"], "risks": [{"text": "原文句子", "topic": "经营|技术|设备|人员|信息安全|合规"}], "followups": [{"text": "待办", "due": "时限或空"}], "sentiment": "positive|neutral|negative", "topics": ["涉及主题"]}',
    required_keys=("key_points", "risks", "followups", "sentiment"),
)
def contact_summary(inputs: dict) -> dict:
    sents = sentences(inputs.get("content", ""))
    points, risks, follow, topics = [], [], [], set()
    for s in sents:
        tps = [t for t, kws in TOPICS.items() if any(k in s for k in kws)]
        topics |= set(tps)
        neg = any(k in s for k in NEGATIVE)
        if neg:
            risks.append({"text": s, "topic": tps[0] if tps else "经营"})
        if any(s.startswith(k) or k in s[:12] for k in COMMIT) or DUE.search(s):
            due = DUE.search(s)
            follow.append({"text": s, "due": due.group(0) if due else ""})
        if tps and (neg or any(k in s for k in POSITIVE) or re.search(r"\d", s)):
            points.append(s)
    pos = sum(1 for s in sents if any(k in s for k in POSITIVE))
    sentiment = "negative" if len(risks) > pos + 1 else ("positive" if pos > len(risks) else "neutral")
    return {"key_points": points[:8] or sents[:3], "risks": risks[:8], "followups": follow[:6], "sentiment": sentiment, "topics": sorted(topics)}


def _post_contact(inputs: dict, out: dict) -> dict:
    content = inputs.get("content", "")
    risks = [r for r in out.get("risks", []) if isinstance(r, dict) and r.get("text") and (r["text"][:8] in content)]
    base = contact_summary(inputs)
    return {
        "key_points": [str(p) for p in out.get("key_points", [])][:8] or base["key_points"],
        "risks": risks or base["risks"],
        "followups": [f for f in out.get("followups", []) if isinstance(f, dict) and f.get("text")][:6] or base["followups"],
        "sentiment": out.get("sentiment") if out.get("sentiment") in ("positive", "neutral", "negative") else base["sentiment"],
        "topics": [t for t in out.get("topics", []) if t in TOPICS] or base["topics"],
    }


@reasoning_task(
    "kb.answer",
    "TT-05",
    "依据检索到的知识片段回答问题，每个结论用 [编号] 标注引用；片段不足以回答时明确说明",
    '{"answer": "带 [1] 形式引用的回答", "citations": [1, 2], "insufficient": false, "confidence": 0.0}',
    required_keys=("answer", "citations"),
    guidance="只能使用给定片段中的事实；不要编造条款号、数字或机构名称",
)
def kb_answer(inputs: dict) -> dict:
    q = inputs.get("question", "")
    scored = []
    for p in inputs.get("passages", []):
        for s in sentences(p.get("text", "")):
            scored.append((overlap(q, s), p["n"], s))
    scored.sort(key=lambda x: -x[0])
    picked, used = [], []
    for score, n, s in scored:
        if score < 0.08 or len(picked) >= 4:
            break
        if any(overlap(s, x) > 0.6 for _, x in picked):
            continue
        picked.append((n, s))
        if n not in used:
            used.append(n)
    if not picked:
        return {"answer": "检索到的知识片段不足以回答该问题，建议换用更具体的关键词，或提交知识新增提案。", "citations": [], "insufficient": True, "confidence": 0.2}
    answer = "；".join(f"{s.rstrip('。；')}[{n}]" for n, s in picked) + "。"
    return {"answer": answer, "citations": used, "insufficient": False, "confidence": round(min(0.9, 0.4 + scored[0][0]), 2)}


def _post_answer(inputs: dict, out: dict) -> dict:
    valid = {p["n"] for p in inputs.get("passages", [])}
    answer = str(out.get("answer") or "")
    cited = {int(x) for x in re.findall(r"\[(\d+)\]", answer)}
    bad = cited - valid
    if bad:
        answer = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in valid else "", answer)
    cites = sorted((cited - bad) | {int(c) for c in out.get("citations", []) if str(c).isdigit() and int(c) in valid})
    if not cites and not out.get("insufficient"):
        raise ValueError("回答没有引用任何给定片段")
    return {"answer": answer, "citations": cites, "insufficient": bool(out.get("insufficient")), "confidence": float(out.get("confidence") or 0.6)}


INTENTS = [
    ("explain", ["为什么", "原因", "依据", "怎么得出", "理由", "解释"]),
    ("next", ["下一步", "建议", "怎么办", "如何处理", "应该", "需要做"]),
    ("risk", ["风险", "隐患", "问题", "关注"]),
    ("status", ["进展", "状态", "到哪", "完成了吗", "多久"]),
]


@reasoning_task(
    "copilot.answer",
    "TT-15",
    "作为业务协作助手，结合当前页面对象的摘要与知识片段回答用户问题，解释结果、给出下一步建议，并从可用动作中推荐需要用户确认后执行的动作",
    '{"answer": "回答（引用知识用 [编号]）", "citations": [1], "actions": [{"key": "动作键", "reason": "推荐理由"}]}',
    required_keys=("answer",),
    guidance="对象摘要中的数字与结论必须原样引用；推荐动作只能从 actions_available 中选择，不能编造动作",
)
def copilot_answer(inputs: dict) -> dict:
    q = inputs.get("question", "")
    obj = inputs.get("object") or {}
    intent = next((k for k, kws in INTENTS if any(w in q for w in kws)), "explain")
    parts: list[str] = []
    facts = obj.get("facts") or {}
    if intent == "explain":
        reasons = obj.get("reasons") or []
        if reasons:
            parts.append("主要依据：" + "；".join(str(r) for r in reasons[:5]))
        if facts:
            parts.append("关键数据：" + "，".join(f"{k} {v}" for k, v in list(facts.items())[:6]))
    elif intent == "next":
        steps = obj.get("next_steps") or []
        parts.append("建议下一步：" + ("；".join(f"{i + 1}. {s}" for i, s in enumerate(steps[:5])) if steps else "当前对象没有待处理事项"))
    elif intent == "risk":
        risks = obj.get("risks") or []
        parts.append("需要关注：" + ("；".join(str(r) for r in risks[:6]) if risks else "当前对象未记录中高风险"))
    else:
        parts.append(f"当前状态：{obj.get('status') or '未知'}" + (f"；{obj.get('progress')}" if obj.get("progress") else ""))
    passages = inputs.get("passages") or []
    cites = []
    if passages:
        kb = kb_answer({"question": q, "passages": passages})
        if not kb.get("insufficient"):
            parts.append("相关规定：" + kb["answer"])
            cites = kb["citations"]
    if not parts or all(not p for p in parts):
        parts.append(f"已读取{obj.get('kind_label', '当前页面')}「{obj.get('title', '')}」，可以问我结论的依据、下一步建议或需要关注的风险。")
    actions = []
    for a in inputs.get("actions_available") or []:
        kws = a.get("keywords") or []
        if any(k in q for k in kws) or (intent == "next" and a.get("suggest")):
            actions.append({"key": a["key"], "reason": a.get("reason") or "与问题相关"})
    return {"answer": "\n".join(parts), "citations": cites, "actions": actions[:3]}


def _post_copilot(inputs: dict, out: dict) -> dict:
    allowed = {a["key"] for a in inputs.get("actions_available") or []}
    valid = {p["n"] for p in inputs.get("passages") or []}
    actions = [a for a in out.get("actions", []) if isinstance(a, dict) and a.get("key") in allowed][:3]
    answer = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in valid else "", str(out.get("answer") or ""))
    return {"answer": answer, "citations": [int(c) for c in out.get("citations", []) if str(c).isdigit() and int(c) in valid], "actions": actions}


@reasoning_task(
    "cd.regenerate_article",
    "TT-06",
    "按修改要求局部重写一条保险条款：保持章节用途与已有编号体系，语言准确严谨，不得出现禁用表述",
    '{"title": "条款标题", "text": "条款正文", "rationale": "修改说明"}',
    required_keys=("title", "text"),
    guidance="保险责任须写明触发条件与损失范围；责任免除须列举具体情形；不得使用“全额赔付”“保证赔付”等承诺性表述，不得使用“相关”“适当”等模糊词",
)
def regenerate_article(inputs: dict) -> dict:
    art = inputs.get("article") or {}
    instr = inputs.get("instruction", "")
    text, title = art.get("text", ""), art.get("title", "")
    modules = sorted(inputs.get("related_modules") or [], key=lambda m: -overlap(m.get("text", ""), text + instr))
    notes = []
    if any(k in instr for k in ("通俗", "简化", "精简", "简洁")):
        sents = sentences(text)
        text = "".join(s if s.endswith(("。", "；")) else s + "。" for s in sents[: max(1, len(sents) - 1)])
        notes.append("精简表述，删除重复说明")
    if any(k in instr for k in ("示范", "参考", "替换", "重写", "规范")) and modules:
        m = modules[0]
        text = m["text"]
        title = title or m["title"]
        notes.append(f"参照条款模块 {m['code']}（{m['title']}）重写")
    if any(k in instr for k in ("扩展", "增加", "补充", "覆盖")) and modules:
        extra = next((m for m in modules if m["text"] not in text), None)
        if extra:
            text = text.rstrip("。") + "；" + sentences(extra["text"])[0].rstrip("。") + "。"
            notes.append(f"补充 {extra['code']} 的保障表述")
    if any(k in instr for k in ("收紧", "限制", "限定", "除外")):
        limit = re.search(r"(限于[^，。]+|不超过[^，。]+|除外[^，。]*)", instr)
        text = text.rstrip("。") + (f"，{limit.group(1)}" if limit else "，但以本保险合同约定的赔偿限额与免赔额为限") + "。"
        notes.append("增加限定条件")
    m = re.search(r"等待期(?:改为|调整为|为)?(\d+)日", instr)
    if m:
        text = re.sub(r"等待期为\d+日", f"等待期为{m.group(1)}日", text) if "等待期为" in text else text.rstrip("。") + f"，等待期为{m.group(1)}日。"
        notes.append(f"等待期调整为 {m.group(1)} 日")
    for w in inputs.get("forbidden_words") or []:
        if w in text:
            text = text.replace(w, "按照本保险合同约定赔偿")
            notes.append(f"替换禁用表述「{w}」")
    for w in inputs.get("ambiguous_words") or []:
        if inputs.get("chapter_key") == "liability" and w in text and w in ("适当", "视情况", "原则上"):
            text = text.replace(w, "")
            notes.append(f"删除模糊表述「{w}」")
    if not notes:
        text = text.rstrip("。") + "。"
        notes.append("按要求复核文字，未发现需要修改的内容")
    return {"title": title, "text": text, "rationale": "；".join(notes)}


def _post_regen(inputs: dict, out: dict) -> dict:
    text = str(out.get("text") or "").strip()
    if len(text) < 10:
        raise ValueError("再生成的条款过短")
    for w in inputs.get("forbidden_words") or []:
        if w in text:
            text = text.replace(w, "按照本保险合同约定赔偿")
    return {"title": str(out.get("title") or (inputs.get("article") or {}).get("title") or ""), "text": text, "rationale": str(out.get("rationale") or "")}


from ..llm.tasks import attach  # noqa: E402

attach("cm.contact_summary", postprocess=_post_contact)
attach("kb.answer", postprocess=_post_answer)
attach("copilot.answer", postprocess=_post_copilot)
attach("cd.regenerate_article", postprocess=_post_regen)
