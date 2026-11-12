"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { LifeBuoy } from "lucide-react";
import { useApi } from "@/lib/api";
import { amount, dateTime } from "@/lib/format";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, RunStatus, Section, StatCard } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";

const TONE: Record<string, any> = { registered: "gray", reported: "gray", assessing: "blue", pending_approval: "amber", approved: "violet", closed: "green" };

function Claims() {
  const q = useSearchParams().get("q") || "";
  const { data, error, mutate } = useApi<any[]>("/claims", { refreshInterval: 6000 });
  const rows = (data || []).filter((c) => !q || c.claim_no.includes(q));
  const page = usePagedList(rows, q);
  const sum = (k: string) => (data || []).reduce((s, c) => s + (c[k] || 0), 0);
  return (
    <div>
      <PageHeader title="定损理赔" description="定损理赔智能体：解析理赔材料与条款，多源数据融合确定损失范围，多方事故用因果推断划分责任比例，文本与图像两类定损后理算，赔付决定由具备权限的理赔人员作出。" actions={<Link href="/claims/new"><Button>报案登记</Button></Link>} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="在办案件" value={(data || []).filter((c) => c.status !== "closed").length} />
        <StatCard label="待审批" value={(data || []).filter((c) => c.status === "pending_approval").length} tone="warning" />
        <StatCard label="申报金额" value={amount(sum("claimed_amount"))} />
        <StatCard label="建议赔付" value={amount(sum("suggested_amount"))} tone="success" />
      </div>
      <Section contentClassName="p-0">
        {error ? (
          <div className="p-4">
            <ErrorBlock error={error} retry={() => mutate()} />
          </div>
        ) : !data ? (
          <div className="p-4">
            <LoadingBlock />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <EmptyState title="暂无理赔案件" description="事故响应确认预报案、或在保单页报案登记后生成案件" icon={LifeBuoy} />
          </div>
        ) : (
          <>
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[12%]">案件号</TableHead>
                <TableHead className="w-[16%]">被保险人</TableHead>
                <TableHead className="w-[12%]">事故类型</TableHead>
                <TableHead className="w-[12%] text-right">申报 / 预估</TableHead>
                <TableHead className="w-[10%] text-right">建议赔付</TableHead>
                <TableHead className="w-[10%] text-right">核定赔付</TableHead>
                <TableHead className="w-[10%]">状态</TableHead>
                <TableHead className="w-[10%]">定损运行</TableHead>
                <TableHead className="w-[8%]">登记时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell>
                    <Link href={`/claims/${c.id}`} className="font-mono text-xs text-primary hover:underline">
                      {c.claim_no}
                    </Link>
                  </TableCell>
                  <TableCell className="font-medium">{c.enterprise}</TableCell>
                  <TableCell>{c.incident_type}</TableCell>
                  <TableCell className="text-right tabular">{amount(c.claimed_amount)}</TableCell>
                  <TableCell className="text-right tabular">{amount(c.suggested_amount)}</TableCell>
                  <TableCell className="text-right tabular">{amount(c.approved_amount)}</TableCell>
                  <TableCell>
                    <Pill tone={TONE[c.status]}>{c.status_label}</Pill>
                  </TableCell>
                  <TableCell>{c.run_id ? <Link href={`/runs/${c.run_id}`}><RunStatus status={c.run_status} /></Link> : <span className="text-xs text-muted-foreground">材料 {c.materials} 份</span>}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{dateTime(c.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <PaginationBar className="border-t px-4 py-3" page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
          </>
        )}
      </Section>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <Claims />
    </Suspense>
  );
}
