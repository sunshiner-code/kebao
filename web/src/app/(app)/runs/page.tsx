"use client";

import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { AGENTS, dateTime, pct, runDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AgentBadge, Bar, EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, RunStatus, ScoreBadge, Section, StartMode } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_FILTERS = [
  { key: "", label: "全部" },
  { key: "queued,planning,running", label: "运行中" },
  { key: "waiting_human", label: "等待人工" },
  { key: "completed", label: "已完成" },
  { key: "failed,cancelled", label: "失败与取消" },
];

export default function RunsPage() {
  const [agent, setAgent] = useState("");
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams({ limit: "200", ...(agent ? { agent_id: agent } : {}), ...(status ? { status } : {}) });
  const { data, error, isLoading, mutate } = useApi<any[]>(`/runs?${qs}`, { refreshInterval: 5000 });
  const page = usePagedList(data || [], `${agent}|${status}`);
  return (
    <div>
      <PageHeader title="运行记录" description="六个智能体的全部运行实例；点击进入运行台查看轨迹回放、评估与反思、模型路由与出域记录。" />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {[{ id: "", name: "全部智能体" }, ...Object.entries(AGENTS).map(([id, a]) => ({ id, name: a.name }))].map((a) => (
          <button key={a.id} onClick={() => setAgent(a.id)} className={cn("rounded-full border px-3 py-1 text-xs transition-colors", agent === a.id ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
            {a.name}
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-border" />
        {STATUS_FILTERS.map((s) => (
          <button key={s.key} onClick={() => setStatus(s.key)} className={cn("rounded-full border px-3 py-1 text-xs", status === s.key ? "border-foreground/30 bg-foreground/5 font-medium" : "bg-card hover:bg-muted")}>
            {s.label}
          </button>
        ))}
      </div>
      <Section contentClassName="p-0">
        {error ? (
          <div className="p-4">
            <ErrorBlock error={error} retry={() => mutate()} />
          </div>
        ) : isLoading ? (
          <div className="p-4">
            <LoadingBlock />
          </div>
        ) : !data?.length ? (
          <div className="p-4">
            <EmptyState title="没有符合条件的运行" />
          </div>
        ) : (
          <>
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[7%]">编号</TableHead>
                <TableHead className="w-[28%]">任务</TableHead>
                <TableHead className="w-[12%]">智能体</TableHead>
                <TableHead className="w-[10%]">状态</TableHead>
                <TableHead className="w-[14%]">进度</TableHead>
                <TableHead className="w-[8%]">评估</TableHead>
                <TableHead className="w-[8%]">启动方式</TableHead>
                <TableHead className="w-[8%]">开始时间</TableHead>
                <TableHead className="w-[5%]">用时</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((r) => (
                <TableRow key={r.id} className="cursor-pointer">
                  <TableCell className="font-mono text-xs">
                    <Link href={`/runs/${r.id}`} className="text-primary hover:underline">
                      #{r.id}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-[360px]">
                    <Link href={`/runs/${r.id}`} className="block truncate font-medium hover:text-primary">
                      {r.title}
                    </Link>
                    {r.summary && <div className="truncate text-xs text-muted-foreground">{r.summary}</div>}
                  </TableCell>
                  <TableCell>
                    <AgentBadge agentId={r.agent_id} />
                    {r.purpose !== "main" && <Pill className="ml-1">{r.purpose}</Pill>}
                  </TableCell>
                  <TableCell>
                    <RunStatus status={r.status} />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Bar value={r.progress} />
                      <span className="w-9 text-right text-xs tabular">{pct(r.progress)}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <ScoreBadge score={r.eval_score} passed={r.eval_passed} />
                  </TableCell>
                  <TableCell>
                    <StartMode mode={r.start_mode} degrade={r.degrade_level} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{dateTime(r.created_at)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{runDuration(r)}</TableCell>
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
