"use client";

import Link from "next/link";
import { useState } from "react";
import { Bell, ClipboardCheck } from "lucide-react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { relative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { HitlCardView } from "@/components/hitl";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function InboxPage() {
  const [status, setStatus] = useState("open");
  const cards = useApi<any[]>(`/hitl/cards?status=${status}&scope=mine`, { refreshInterval: 5000 });
  const notes = useApi<any>("/notifications", { refreshInterval: 10000 });
  const approvals = useApi<any[]>("/approvals?scope=pending", { refreshInterval: 8000 });
  const mine = useApi<any[]>("/approvals?scope=mine");
  const cardPage = usePagedList(cards.data || [], status);
  const notePage = usePagedList(notes.data?.items || [], "");
  const approvalPage = usePagedList(approvals.data || [], "pending");
  const minePage = usePagedList(mine.data || [], "mine");
  const decide = async (id: number, action: string) => {
    const comment = action === "reject" ? window.prompt("驳回意见") || "" : "";
    if (action === "reject" && !comment.trim()) return;
    try {
      await api(`/approvals/${id}/act`, { method: "POST", json: { action, comment } });
      toast.success(action === "approve" ? "已通过" : "已驳回");
      approvals.mutate();
      mine.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader title="消息与待办" description="全平台人审节点、审批与通知的统一入口。处理结果会以信箱消息回到智能体运行，运行从等待人工恢复执行。" />
      <Tabs defaultValue="cards">
        <TabsList>
          <TabsTrigger value="cards">
            <ClipboardCheck className="size-3.5" /> 待办 {status === "open" && cards.data ? `(${cards.data.length})` : ""}
          </TabsTrigger>
          <TabsTrigger value="notes">
            <Bell className="size-3.5" /> 消息 {notes.data?.unread ? `(${notes.data.unread})` : ""}
          </TabsTrigger>
          <TabsTrigger value="approvals">审批 {approvals.data?.length ? `(${approvals.data.length})` : ""}</TabsTrigger>
          <TabsTrigger value="mine">我发起的</TabsTrigger>
        </TabsList>
        <TabsContent value="cards" className="mt-4">
          <div className="mb-3 flex gap-2">
            {[
              ["open", "待处理"],
              ["resolved", "已处理"],
              ["expired", "超时自动处理"],
            ].map(([k, l]) => (
              <button key={k} onClick={() => setStatus(k)} className={cn("rounded-full border px-3 py-1 text-xs", status === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
                {l}
              </button>
            ))}
          </div>
          {cards.error ? (
            <ErrorBlock error={cards.error} />
          ) : !cards.data ? (
            <LoadingBlock />
          ) : cards.data.length === 0 ? (
            <EmptyState title={status === "open" ? "没有待处理事项" : "暂无记录"} description="智能体需要人工确认、审批或补充信息时，卡片会出现在这里。" />
          ) : (
            <div>
            <div className="grid gap-3 lg:grid-cols-2">
              {cardPage.items.map((c) => (
                <HitlCardView key={c.id} card={c} onResolved={() => cards.mutate()} />
              ))}
            </div>
            <PaginationBar page={cardPage.page} pages={cardPage.pages} total={cardPage.total} onPage={cardPage.setPage} />
            </div>
          )}
        </TabsContent>
        <TabsContent value="notes" className="mt-4">
          {!notes.data ? (
            <LoadingBlock />
          ) : (
            <div>
            <div className="divide-y rounded-xl border bg-card">
              {notes.data.items.length === 0 && <EmptyState title="暂无消息" />}
              {notePage.items.map((n: any) => (
                <Link key={n.id} href={n.link || "#"} className="flex items-start gap-3 px-4 py-3 hover:bg-muted/50">
                  <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", n.level === "error" ? "bg-rose-500" : n.level === "warning" ? "bg-amber-500" : "bg-sky-500")} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">{n.title}</div>
                    <div className="text-xs text-muted-foreground">{n.body}</div>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{relative(n.ts)}</span>
                </Link>
              ))}
            </div>
            <PaginationBar page={notePage.page} pages={notePage.pages} total={notePage.total} onPage={notePage.setPage} />
            </div>
          )}
        </TabsContent>
        <TabsContent value="approvals" className="mt-4 space-y-3">
          {(approvals.data || []).length === 0 && <EmptyState title="没有待你审批的事项" />}
          {approvalPage.items.map((a) => (
            <div key={a.id} className="rounded-xl border bg-card p-4">
              <div className="text-sm font-medium">{a.title}</div>
              <div className="mt-1 text-xs text-muted-foreground">{a.flow_name} · {a.request_no} · 当前节点「{a.stages[a.current_stage]?.name}」· 申请人 {a.applicant_name}</div>
              <div className="mt-2 grid gap-1 text-xs sm:grid-cols-2">{Object.entries(a.summary || {}).map(([k, v]) => <div key={k}><span className="text-muted-foreground">{k}：</span>{typeof v === "string" ? v : JSON.stringify(v)}</div>)}</div>
              <div className="mt-3 flex gap-2">
                <button className="rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground" onClick={() => decide(a.id, "approve")}>通过</button>
                <button className="rounded-lg border px-3 py-1.5 text-xs" onClick={() => decide(a.id, "reject")}>驳回</button>
              </div>
            </div>
          ))}
          <PaginationBar page={approvalPage.page} pages={approvalPage.pages} total={approvalPage.total} onPage={approvalPage.setPage} />
        </TabsContent>
        <TabsContent value="mine" className="mt-4 space-y-2">
          {(mine.data || []).length === 0 && <EmptyState title="你还没有发起过审批" />}
          {minePage.items.map((a) => (
            <div key={a.id} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
              <span>{a.title}<span className="ml-2 text-xs text-muted-foreground">{a.request_no}</span></span>
              <span className="text-xs">{a.status === "pending" ? `审批中 · ${a.stages[a.current_stage]?.name}` : a.status}</span>
            </div>
          ))}
          <PaginationBar page={minePage.page} pages={minePage.pages} total={minePage.total} onPage={minePage.setPage} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
