"use client";

import Link from "next/link";
import { useState } from "react";
import { Search } from "lucide-react";
import { toast } from "sonner";
import { api, getToken, useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Bar, EmptyState, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PAGE_SIZE } from "@/lib/pagination";

const KB: Record<string, string> = { K01: "法规监管", K02: "政策与理赔材料", K03: "条款模块", K04: "风险知识", K05: "理赔案例与因果模板", K06: "行业技术", K07: "精算数据", K08: "企业私有", K09: "欺诈模式", K10: "应急与减损" };
const STAGE: Record<string, string> = { rd: "研发", transfer: "成果转化", industrial: "产业化" };

function rate(v: number | undefined) {
  return `${Math.round((v || 0) * 100)}%`;
}

function IngestPanel() {
  const batches = useApi<any[]>("/knowledge/batches");
  const [kb, setKb] = useState("K04");
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<any | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = async (id: number) => {
    const next = await api<any>(`/knowledge/batches/${id}`);
    setBatch(next);
    batches.mutate();
  };
  const upload = async () => {
    if (!file || !source.trim()) {
      toast.error("请选择文件并填写来源");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("kb", kb);
      fd.append("source", source.trim());
      fd.append("doc_type", "知识文档");
      fd.append("files", file);
      const res = await fetch("/api/knowledge/ingest", { method: "POST", body: fd, headers: { Authorization: `Bearer ${getToken()}` } });
      const out = await res.json();
      if (!res.ok) throw new Error(out.error?.message || "上传失败");
      setBatch(out);
      setFile(null);
      batches.mutate();
      toast.success(`已生成批次 ${out.batch_no}`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  const qc = async (idx: number, verdict: "pass" | "fail") => {
    if (!batch) return;
    try {
      setBatch(await api(`/knowledge/batches/${batch.id}/items/${idx}/qc`, { method: "POST", json: { verdict, comment: verdict === "pass" ? "抽检通过" : "抽检退回" } }));
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const publish = async () => {
    if (!batch) return;
    try {
      setBatch(await api(`/knowledge/batches/${batch.id}/publish`, { method: "POST" }));
      toast.success("批次已发布");
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const sendBack = async () => {
    if (!batch || !reason.trim()) {
      toast.error("请填写退回原因");
      return;
    }
    try {
      setBatch(await api(`/knowledge/batches/${batch.id}/return`, { method: "POST", json: { reason: reason.trim() } }));
      toast.success("批次已退回");
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const samples = new Set<number>(batch?.sample_ids || []);
  const batchPage = usePagedList(batch?.items || [], String(batch?.id ?? ""));
  return (
    <div className="space-y-4">
      <Section title="上传入库" description="选择知识库和来源后上传文本、Markdown、PDF 或 Word。发布前需完成抽检。">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-muted-foreground">
            知识库
            <select value={kb} onChange={(e) => setKb(e.target.value)} className="mt-1 block h-8 rounded-md border bg-background px-2 text-sm">
              {Object.entries(KB).map(([code, name]) => (
                <option key={code} value={code}>
                  {code} {name}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-48 flex-1 text-xs text-muted-foreground">
            来源
            <Input value={source} onChange={(e) => setSource(e.target.value)} placeholder="例如：专家稿、内部制度" className="mt-1" />
          </label>
          <label className="text-xs text-muted-foreground">
            文件
            <Input type="file" accept=".txt,.md,.pdf,.docx,.csv" className="mt-1" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </label>
          <Button onClick={upload} disabled={busy}>
            上传
          </Button>
        </div>
      </Section>
      {batches.data && batches.data.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {batches.data.slice(0, 8).map((b) => (
            <Button key={b.id} size="sm" variant={batch?.id === b.id ? "default" : "outline"} onClick={() => refresh(b.id)}>
              {b.batch_no} · {b.status === "qc" ? "质检中" : b.status === "published" ? "已发布" : "已退回"}
            </Button>
          ))}
        </div>
      )}
      {batch && (
        <Section title={`${batch.batch_no} · ${batch.kb} ${batch.kb_name || KB[batch.kb] || ""}`} description={`来源 ${batch.source} · ${batch.docs} 篇`}>
          <div className="space-y-3">
            {batchPage.items.map((item: any) => (
              <div key={item.idx} className="rounded-lg border p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{item.title}</span>
                  {samples.has(item.idx) && <Pill tone="amber">抽检</Pill>}
                  {item.qc && <Pill tone={item.qc.verdict === "pass" ? "green" : "red"}>{item.qc.verdict === "pass" ? "通过" : "退回"}</Pill>}
                </div>
                <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{item.text}</div>
                {(item.issues || []).length > 0 && <div className="mt-1 text-xs text-orange-700">{item.issues.join("；")}</div>}
                {samples.has(item.idx) && batch.status === "qc" && (
                  <div className="mt-2 flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => qc(item.idx, "pass")}>
                      通过
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => qc(item.idx, "fail")}>
                      退回
                    </Button>
                  </div>
                )}
              </div>
            ))}
            <PaginationBar page={batchPage.page} pages={batchPage.pages} total={batchPage.total} onPage={batchPage.setPage} />
            {batch.status === "qc" && (
              <div className="flex flex-wrap items-end gap-2">
                <Button onClick={publish}>发布</Button>
                <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="退回原因" className="max-w-xs" />
                <Button variant="outline" onClick={sendBack}>
                  退回批次
                </Button>
              </div>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}

export default function KnowledgePage() {
  const { user } = useAuth();
  const canIngest = !!user?.permissions?.includes("kb.ingest");
  const stats = useApi<any>("/knowledge/stats");
  const maturity = useApi<any[]>("/knowledge/maturity");
  const catalog = useApi<any>("/catalog");
  const factors = useApi<any[]>("/knowledge/factors");
  const fraud = useApi<any[]>("/knowledge/fraud-patterns");
  const [kb, setKb] = useState("K04");
  const [docPage, setDocPage] = useState(1);
  const docs = useApi<{ items: any[]; total: number; page: number; pages: number }>(`/knowledge/docs?kb=${kb}&page=${docPage}&page_size=${PAGE_SIZE}`);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<any | null>(null);
  const [asking, setAsking] = useState(false);
  const selectKb = (code: string) => {
    setKb(code);
    setDocPage(1);
  };
  const factorPage = usePagedList(factors.data || [], "factors");
  const fraudPage = usePagedList(fraud.data || [], "fraud");
  const docItems = docs.data?.items || [];
  const docTotal = docs.data?.total ?? 0;
  const docPages = docs.data?.pages ?? 0;
  const docCurrent = docs.data?.page ?? docPage;
  const search = async () => {
    if (!q.trim()) return;
    setSearching(true);
    try {
      setHits(await api(`/knowledge/search?q=${encodeURIComponent(q)}`));
    } finally {
      setSearching(false);
    }
  };
  const ask = async () => {
    if (!question.trim()) return;
    setAsking(true);
    try {
      setAnswer(await api("/knowledge/answer", { method: "POST", json: { question, kbs: [] } }));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setAsking(false);
    }
  };
  const lineName = (code: string) => catalog.data?.lines?.[code]?.name || code;
  return (
    <div>
      <PageHeader title="知识中心" description="平台知识资产：十个知识库按混合检索（关键词 + 向量）供智能体引用；险种成熟度三维评分决定热启动或冷启动。" />
      <Tabs defaultValue="search">
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="search">知识检索</TabsTrigger>
          <TabsTrigger value="kbs">知识库</TabsTrigger>
          <TabsTrigger value="maturity">险种成熟度</TabsTrigger>
          <TabsTrigger value="taxonomy">风险分类体系</TabsTrigger>
          <TabsTrigger value="factors">因子字典</TabsTrigger>
          <TabsTrigger value="fraud">欺诈模式</TabsTrigger>
          {canIngest && <TabsTrigger value="ingest">入库与质检</TabsTrigger>}
        </TabsList>
        <TabsContent value="search" className="mt-3 space-y-4">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder="例如：研发设备损坏导致研发中断如何赔付、数据泄露应急预案、轴承振动" className="h-10 pl-9" />
            </div>
            <Button size="lg" onClick={search} disabled={searching}>
              检索
            </Button>
          </div>
          {hits === null ? (
            <EmptyState title="输入问题进行混合检索" description="检索结果与智能体引用的知识一致，带知识库与文档编号" icon={Search} />
          ) : hits.length === 0 ? (
            <EmptyState title="没有检索到相关内容" />
          ) : (
            <div className="space-y-2">
              {hits.map((h) => (
                <div key={h.chunk_id || h.doc_id + h.text?.slice(0, 10)} className="rounded-xl border bg-card p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone="blue">
                      {h.kb} {h.kb_name || KB[h.kb]}
                    </Pill>
                    <Link href={`/knowledge/docs/${h.doc_id}`} className="font-medium hover:text-primary">
                      {h.title}
                    </Link>
                    {h.position && <span className="text-xs text-muted-foreground">{h.position}</span>}
                    <span className="ml-auto text-xs text-muted-foreground tabular">综合 {Number(h.score).toFixed(1)} · 关键词 {h.bm25} · 向量 {h.vector}</span>
                  </div>
                  <div className="mt-1.5 text-sm text-muted-foreground">{h.text}</div>
                </div>
              ))}
            </div>
          )}
          <Section title="带引用的问答" description="回答只依据已入库知识，并链到原文。">
            <div className="flex gap-2">
              <Input value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} placeholder="例如：实验室火灾造成的研发设备损失能不能保" />
              <Button onClick={ask} disabled={asking}>
                提问
              </Button>
            </div>
            {answer && (
              <div className="mt-3 space-y-2 text-sm">
                <div>{answer.insufficient ? "没有检索到相关内容" : answer.answer}</div>
                {(answer.citations || []).length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {(answer.passages || [])
                      .filter((p: any) => (answer.citations || []).includes(p.n))
                      .map((p: any) => (
                        <Link key={p.n} href={`/knowledge/docs/${p.doc_id}`} className="text-xs text-primary hover:underline">
                          [{p.n}] {p.title}
                          {p.position ? ` · ${p.position}` : ""}
                        </Link>
                      ))}
                  </div>
                )}
              </div>
            )}
          </Section>
        </TabsContent>
        <TabsContent value="kbs" className="mt-3 space-y-4">
          {!stats.data ? (
            <LoadingBlock />
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {(stats.data.kbs || []).map((k: any) => (
                <button key={k.kb} onClick={() => selectKb(k.kb)} className={cn("rounded-xl border p-3 text-left", kb === k.kb ? "border-primary bg-primary/5" : "bg-card hover:bg-muted/50")}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">
                      {k.kb} {k.name || KB[k.kb]}
                    </span>
                    <Pill tone={k.domain === "private" ? "violet" : "blue"}>{k.domain_label}</Pill>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <span>文档 {k.docs} 篇</span>
                    <span>切片 {k.chunks}</span>
                    <span>更新 {k.updated_at ? dateTime(k.updated_at) : "暂无"}</span>
                    <span>近 30 天引用 {k.citations_30d}</span>
                    <span>解析成功率 {rate(k.parse_rate)}</span>
                    <span>标注完整率 {rate(k.annotation_rate)}</span>
                    <span>失效文档 {rate(k.inactive_rate)}</span>
                    <span>累计引用 {k.citations}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
          <Section title={`${kb} ${KB[kb] || ""}`} contentClassName="max-h-[640px] overflow-y-auto">
            {!docs.data ? (
              <LoadingBlock />
            ) : docTotal === 0 ? (
              <EmptyState title="这个知识库还没有文档" />
            ) : (
              docItems.map((d) => (
                <div key={d.id} className="border-b py-2.5 last:border-0">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <Link href={`/knowledge/docs/${d.id}`} className="font-medium hover:text-primary">
                      {d.title}
                    </Link>
                    <Pill>{d.doc_type}</Pill>
                    {d.quality === "checked" && <Pill tone="green">已审核</Pill>}
                    {d.cite_count > 0 && <span className="text-xs text-muted-foreground">被引用 {d.cite_count} 次</span>}
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{d.text}</div>
                </div>
              ))
            )}
          </Section>
          <PaginationBar page={docCurrent} pages={docPages} total={docTotal} onPage={setDocPage} />
        </TabsContent>
        <TabsContent value="maturity" className="mt-3 space-y-3">
          <Section title="险种成熟度三维评分" description="条款材料、定价数据、案例材料三个维度分别显示分数、阈值、热启动或冷启动，以及距离阈值还差的分数。">
            <div className="overflow-x-auto">
              <table className="w-full table-fixed text-sm">
                <colgroup>
                  <col className="w-[22%]" />
                  <col className="w-[22%]" />
                  <col className="w-[22%]" />
                  <col className="w-[22%]" />
                  <col className="w-[12%]" />
                </colgroup>
                <thead className="border-b text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="py-2">险种</th>
                    {(["clause", "pricing", "cases"] as const).map((k) => (
                      <th key={k} className="py-2">
                        {{ clause: "条款材料", pricing: "定价数据", cases: "案例材料" }[k]}
                      </th>
                    ))}
                    <th>综合</th>
                  </tr>
                </thead>
                <tbody>
                  {(maturity.data || []).map((m) => (
                    <tr key={m.line} className="border-b align-top last:border-0">
                      <td className="py-2 pr-2 font-medium leading-snug break-words">{m.name}</td>
                      {(["clause", "pricing", "cases"] as const).map((k) => (
                        <td key={k} className="py-2 pr-4">
                          <div className="mb-1 flex items-center gap-2">
                            <Bar value={m.scores[k]} max={100} tone={m.verdict[k] === "warm" ? "green" : "sky"} />
                            <span className="w-8 text-right text-xs tabular">{m.scores[k]}</span>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            阈值 {m.thresholds?.[k] ?? "—"} · {m.verdict[k] === "warm" ? "热启动" : "冷启动"} · 还差 {m.gaps?.[k] ?? 0}
                          </div>
                        </td>
                      ))}
                      <td className="py-2">
                        <Pill tone={m.overall === "warm" ? "green" : m.overall === "cold" ? "sky" : "amber"}>{m.overall === "warm" ? "热启动" : m.overall === "cold" ? "冷启动" : "混合"}</Pill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </TabsContent>
        <TabsContent value="taxonomy" className="mt-3 grid gap-3 md:grid-cols-2">
          {Object.entries(catalog.data?.risk_taxonomy || {}).map(([code, t]: any) => (
            <Section key={code} title={`${code} ${t.name}`}>
              <div className="space-y-3">
                {Object.entries(t.subtypes).map(([sc, s]: any) => (
                  <div key={sc} className="rounded-lg border p-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span>
                        <span className="mr-1 font-mono text-muted-foreground">{sc}</span>
                        {s.name}
                      </span>
                      <Pill tone={s.insurable === "yes" ? "green" : s.insurable === "partial" ? "amber" : "gray"}>{s.insurable === "yes" ? "可保" : s.insurable === "partial" ? "部分可保" : "不可保"}</Pill>
                    </div>
                    <div className="mt-1 text-muted-foreground">典型表现：{(s.patterns || []).join("、") || "—"}</div>
                    <div className="mt-1 text-muted-foreground">阶段：{(s.stages || []).map((x: string) => STAGE[x] || x).join("、") || "—"}</div>
                    <div className="mt-1 text-muted-foreground">险种：{(s.lines || []).map(lineName).join("、") || "无直接对应险种"}</div>
                    {s.consequence && <div className="mt-1 text-muted-foreground">后果：{s.consequence}</div>}
                  </div>
                ))}
              </div>
            </Section>
          ))}
        </TabsContent>
        <TabsContent value="factors" className="mt-3">
          <Section title="风险因子字典" description="定义、度量、方向和来源来自现有目录，使用次数统计定价产物。">
            {!factors.data ? (
              <LoadingBlock />
            ) : (
              <div className="space-y-3">
                {factorPage.items.map((f) => (
                  <div key={f.key} className="rounded-lg border p-3 text-sm">
                    <div className="font-medium">
                      {f.name} <span className="font-mono text-xs text-muted-foreground">{f.key}</span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">{f.definition}</div>
                    <div className="mt-2 flex flex-wrap gap-2 text-xs">
                      <Pill>{f.kind_label || f.kind}</Pill>
                      <Pill>{f.direction_label}</Pill>
                      <Pill>{f.source_label || f.source}</Pill>
                      <span className="text-muted-foreground">使用 {f.usage?.artifacts ?? 0} 次</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <PaginationBar page={factorPage.page} pages={factorPage.pages} total={factorPage.total} onPage={factorPage.setPage} />
          </Section>
        </TabsContent>
        <TabsContent value="fraud" className="mt-3">
          <Section title="欺诈模式库">
            {!fraud.data ? (
              <LoadingBlock />
            ) : (
              <div className="space-y-3">
                {fraudPage.items.map((p) => (
                  <div key={p.code} className="rounded-lg border p-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">
                        {p.code} {p.name}
                      </span>
                      <Pill>{p.stage}</Pill>
                      <span className="text-xs text-muted-foreground">案例 {p.history_cases} · 命中 {p.hits}</span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">{p.desc}</div>
                    <ul className="mt-2 list-disc pl-4 text-xs text-muted-foreground">
                      {(p.rules || []).map((r: string) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
            <PaginationBar page={fraudPage.page} pages={fraudPage.pages} total={fraudPage.total} onPage={fraudPage.setPage} />
          </Section>
        </TabsContent>
        {canIngest && (
          <TabsContent value="ingest" className="mt-3">
            <IngestPanel />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
