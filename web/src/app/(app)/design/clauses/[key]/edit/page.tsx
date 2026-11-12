"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { ErrorBlock, PageHeader, PageLoading, Pill, Section } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function ClauseEditorPage() {
  const { key } = useParams<{ key: string }>();
  const { data, error, mutate } = useApi<any>(`/clause-plans/${key}/editor`);
  const [doc, setDoc] = useState(0);
  const [chapter, setChapter] = useState("liability");
  const [index, setIndex] = useState(0);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [instruction, setInstruction] = useState("");
  const [candidate, setCandidate] = useState<any>(null);
  if (error) return <ErrorBlock error={error} />;
  if (!data) return <PageLoading />;
  const current = data.docs[doc];
  const ch = current?.chapters?.find((c: any) => c.key === chapter) || current?.chapters?.[0];
  const art = ch?.articles?.[index];
  const load = (d: number, ckey: string, i: number) => {
    const chapterRow = data.docs[d]?.chapters?.find((c: any) => c.key === ckey) || data.docs[d]?.chapters?.[0];
    const a = chapterRow?.articles?.[i];
    setDoc(d);
    setChapter(chapterRow?.key || ckey);
    setIndex(i);
    setTitle(a?.title || "");
    setText(a?.text || "");
    setCandidate(null);
  };
  const save = async (op: any) => {
    try {
      await api(`/clause-plans/${key}/edit`, { method: "POST", json: { base_version: data.version, op, reason } });
      toast.success("已生成新版本");
      setReason("");
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader crumbs={[{ label: "条款方案", href: `/design/clauses/${key}` }, { label: "编辑" }]} title={data.title || "条款编辑器"} description={<span>V{data.version} / 最新 V{data.latest_version} · {data.editable ? "可编辑" : "历史版本只读"}{data.recheck?.stale ? " · 合规结果已过期" : ""}</span>} actions={<Button variant="outline" onClick={async () => { try { const out = await api<any>(`/clause-plans/${key}/recheck`, { method: "POST" }); toast.success(`复检完成，强制规则未通过 ${out.rule_failures} 项`); mutate(); } catch (e: any) { toast.error(e.message); } }}>合规复检</Button>} />
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-2">
          {data.docs.map((d: any, di: number) => (
            <Section key={d.doc_id || di} title={d.name}>
              {d.chapters.map((c: any) => (
                <button key={c.key} className="block w-full truncate py-0.5 text-left text-xs hover:text-primary" onClick={() => load(di, c.key, 0)}>{c.title}（{c.articles.length}）</button>
              ))}
            </Section>
          ))}
        </div>
        <div className="space-y-3">
          <Section title={ch ? `${current?.name} · ${ch.title}` : "选择条款"}>
            <div className="mb-2 flex flex-wrap gap-1">
              {(ch?.articles || []).map((a: any, i: number) => (
                <button key={i} onClick={() => load(doc, ch.key, i)} className={`rounded-full border px-2 py-0.5 text-xs ${i === index ? "border-primary bg-primary/10" : ""}`}>{a.no || i + 1} {a.title}</button>
              ))}
            </div>
            <Input className="mb-2 h-8" value={title || art?.title || ""} onChange={(e) => setTitle(e.target.value)} />
            <Textarea rows={6} value={text || art?.text || ""} onChange={(e) => setText(e.target.value)} />
            <Input className="mt-2 h-8" placeholder="修订原因（必填）" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" disabled={!data.editable} onClick={() => save({ op: "update", doc_index: doc, chapter_key: ch.key, article_index: index, title, text })}>保存本条</Button>
              <Button size="sm" variant="outline" disabled={!data.editable} onClick={() => save({ op: "add", doc_index: doc, chapter_key: ch.key, title: title || "新增条款", text })}>在本章新增</Button>
              <Button size="sm" variant="ghost" disabled={!data.editable} onClick={() => save({ op: "remove", doc_index: doc, chapter_key: ch.key, article_index: index })}>删除本条</Button>
            </div>
          </Section>
          <Section title="局部再生成" description="只重写当前这一条，并对照合规规则给出变化。确认后仍需填写原因保存。">
            <Input className="h-8" placeholder="例如：收紧触发条件，去掉模糊表述" value={instruction} onChange={(e) => setInstruction(e.target.value)} />
            <Button className="mt-2" size="sm" variant="outline" onClick={async () => { try { setCandidate(await api(`/clause-plans/${key}/regenerate`, { method: "POST", json: { doc_index: doc, chapter_key: ch.key, article_index: index, instruction } })); } catch (e: any) { toast.error(e.message); } }}>生成候选</Button>
            {candidate && (
              <div className="mt-2 text-sm">
                <div className="text-xs text-muted-foreground">{candidate.candidate.rationale}</div>
                <div className="mt-1 whitespace-pre-wrap rounded-lg bg-muted p-2">{candidate.candidate.text}</div>
                {(candidate.compliance_changes || []).map((c: any) => <div key={c.code} className="text-xs">{c.code} {c.name}：{String(c.before)} → {String(c.after)}</div>)}
                <Button className="mt-2" size="sm" onClick={() => { setTitle(candidate.candidate.title); setText(candidate.candidate.text); }}>采用候选文本</Button>
              </div>
            )}
          </Section>
          <Section title="修订留痕">
            {(data.revisions || []).slice(0, 12).map((r: any, i: number) => (
              <div key={i} className="border-b py-1.5 text-xs last:border-0"><Pill>{r.op}</Pill> {r.by_name} · {r.reason} · {r.doc} {r.chapter}</div>
            ))}
          </Section>
        </div>
      </div>
    </div>
  );
}
