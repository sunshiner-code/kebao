"use client";

import { useParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { DataLevel, ErrorBlock, PageHeader, PageLoading, Pill, Section } from "@/components/common";

export default function DocPage() {
  const { id } = useParams<{ id: string }>();
  const STAGE: Record<string, string> = { rd: "研发", transfer: "成果转化", industrial: "产业化" };
  const STATUS: Record<string, string> = { effective: "生效", superseded: "已被新版本替代", retired: "已废止" };
  const { data, error } = useApi<any>(`/knowledge/docs/${id}`);
  const impact = useApi<any>(`/knowledge/docs/${id}/impact`);
  if (error) return <ErrorBlock error={error} />;
  if (!data) return <PageLoading />;
  return (
    <div>
      <PageHeader crumbs={[{ label: "知识中心", href: "/knowledge" }, { label: data.title }]} title={data.title} description={<span className="flex flex-wrap items-center gap-2"><Pill>{data.kb_name}</Pill><Pill>{data.doc_type}</Pill><DataLevel level={data.data_level} /><span>V{data.version} · 引用 {data.cite_count} · {STATUS[data.status] || data.status}</span></span>} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="正文" className="lg:col-span-2"><div className="whitespace-pre-wrap text-sm leading-6">{data.text}</div></Section>
        <div className="space-y-4">
          <Section title="元数据">
            <div className="space-y-1 text-xs text-muted-foreground">
              <div>来源：{data.source || "—"}</div>
              <div>状态：{STATUS[data.status] || data.status}</div>
              <div>适用险种：{(data.lines || []).join("、") || "—"}</div>
              <div>适用阶段：{(data.stages || []).map((s: string) => STAGE[s] || s).join("、") || "—"}</div>
              <div>风险类型：{(data.risk_types || []).join("、") || "—"}</div>
            </div>
          </Section>
          <Section title="切片">
            {(data.chunks || []).map((c: any) => <div key={c.seq} className="mb-2 text-xs"><div className="text-muted-foreground">{c.position}</div>{c.text}</div>)}
          </Section>
          <Section title="失效影响">
            {!impact.data ? "分析中…" : (
              <div className="space-y-1 text-xs">
                <div>引用运行 {impact.data.runs.length} 个</div>
                <div>相关在售或在途产品 {impact.data.products.length} 个</div>
                <div>相关产物 {impact.data.artifacts.length} 个</div>
                {impact.data.products.slice(0, 5).map((p: any) => <div key={p.id}>{p.name}（{p.status}）</div>)}
              </div>
            )}
          </Section>
          <Section title="版本">{(data.versions || []).map((v: any) => <div key={v.id} className="text-xs">{dateTime(v.created_at)} · V{v.version} · {v.status}</div>)}</Section>
        </div>
      </div>
    </div>
  );
}
