"use client";

import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

export default function JobsPage() {
  const { data, error, mutate } = useApi<any[]>("/admin/jobs", { refreshInterval: 8000 });
  const page = usePagedList(data || [], "");
  const toggle = async (id: number, enabled: boolean) => {
    try {
      await api(`/admin/jobs/${id}`, { method: "PATCH", json: { enabled } });
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const run = async (id: number) => {
    try {
      await api(`/admin/jobs/${id}/run`, { method: "POST" });
      toast.success("已触发，执行记录会在完成后出现");
      setTimeout(() => mutate(), 1500);
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  if (error) return <ErrorBlock error={error} />;
  return (
    <div>
      <PageHeader title="定时任务" description="周期报告、续保提醒、成熟度重算、评测、进化分析、灰度检查、数据回流、数据源探测与留存销毁。失败会按重试次数重试，并通知管理员。" />
      {!data ? <LoadingBlock /> : data.length === 0 ? <EmptyState title="没有定时任务" /> : (
        <div>
        <div className="space-y-3">
          {page.items.map((j) => (
            <Section key={j.id} title={j.name} actions={<div className="flex items-center gap-2"><Switch checked={j.enabled} onCheckedChange={(v) => toggle(j.id, v)} /><Button size="sm" variant="outline" onClick={() => run(j.id)}>立即执行</Button></div>}>
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                <Pill>{j.job_type}</Pill>
                <span>间隔 {j.interval_minutes} 分钟</span>
                <span>上次 {dateTime(j.last_run_at)} {j.last_status || ""}</span>
                <span>下次 {dateTime(j.next_run_at)}</span>
                {j.running && <Pill tone="blue">执行中</Pill>}
              </div>
              {(j.recent || []).slice(0, 3).map((r: any) => (
                <div key={r.id} className="mt-1 text-xs">
                  <span className={r.status === "failed" ? "text-rose-600" : "text-muted-foreground"}>{dateTime(r.started_at)} · {r.trigger} · {r.status}{r.attempt > 1 ? `（第 ${r.attempt} 次）` : ""}</span>
                  {r.error && <div className="text-rose-600">{r.error}</div>}
                  {r.output && <div className="truncate text-muted-foreground">{JSON.stringify(r.output).slice(0, 180)}</div>}
                </div>
              ))}
            </Section>
          ))}
        </div>
        <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
        </div>
      )}
    </div>
  );
}
