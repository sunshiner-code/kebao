"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useApi } from "@/lib/api";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";

const GROUPS: [string, string][] = [
  ["enterprises", "企业"],
  ["products", "产品"],
  ["applications", "投保单"],
  ["policies", "保单"],
  ["claims", "理赔案件"],
  ["alerts", "预警"],
  ["runs", "运行"],
];

function ResultGroup({ label, items, query }: { label: string; items: any[]; query: string }) {
  const page = usePagedList(items, query);
  if (!items.length) return null;
  return (
    <Section title={label}>
      {page.items.map((it: any) => (
        <Link key={it.href} href={it.href} className="mb-1.5 flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/50">
          <span className="font-medium">{it.title}</span>
          <span className="text-xs text-muted-foreground">{it.hint}</span>
        </Link>
      ))}
      <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
    </Section>
  );
}

function Results() {
  const q = useSearchParams().get("q") || "";
  const { data, error } = useApi<any>(q ? `/search?q=${encodeURIComponent(q)}` : null);
  if (!q) return <EmptyState title="输入关键词开始搜索" description="支持企业名称、保单号、案件号、产品、预警号和运行编号。" />;
  if (error) return <ErrorBlock error={error} />;
  if (!data) return <LoadingBlock />;
  const total = GROUPS.reduce((n, [k]) => n + (data[k]?.length || 0), 0);
  return (
    <div className="space-y-4">
      <PageHeader title={`“${q}”的搜索结果`} description={`共 ${total} 条`} />
      {total === 0 && <EmptyState title="没有匹配结果" description="试试企业简称、完整单号，或运行编号。" />}
      {GROUPS.map(([k, label]) => (
        <ResultGroup key={k} label={label} items={data[k] || []} query={q} />
      ))}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <Results />
    </Suspense>
  );
}
