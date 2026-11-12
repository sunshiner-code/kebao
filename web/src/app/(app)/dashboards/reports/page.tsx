"use client";

import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { ErrorBlock, LoadingBlock, PageHeader, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Bars } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function ReportsPage() {
  const dict = useApi<any>("/dashboards/metrics");
  const saved = useApi<any[]>("/reports");
  const [name, setName] = useState("月度经营快报");
  const [metrics, setMetrics] = useState<string[]>(["premium", "loss_ratio"]);
  const [dimension, setDimension] = useState("month");
  const [days, setDays] = useState(180);
  const [preview, setPreview] = useState<any>(null);
  const toggle = (k: string) => setMetrics((xs) => (xs.includes(k) ? xs.filter((x) => x !== k) : [...xs, k]));
  const run = async (save: boolean) => {
    try {
      if (save) {
        await api("/reports", { method: "POST", json: { name, metrics, dimension, period_days: days, chart: "bar", schedule: "none", subscribers: [] } });
        toast.success("报表已保存");
        saved.mutate();
      }
      setPreview(await api("/reports/preview", { method: "POST", json: { name, metrics, dimension, period_days: days } }));
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const savedPage = usePagedList(saved.data || [], "");
  if (dict.error && saved.error) return <ErrorBlock error={dict.error || saved.error} />;
  const rows = preview?.rows || [];
  return (
    <div>
      <PageHeader title="自定义报表" description="自选指标和维度。口径与看板一致，可保存后按日、周、月订阅，到期由定时任务推送到站内信。" />
      <Section title="配置">
        <Input className="mb-2 h-8 max-w-sm" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="mb-2 flex flex-wrap gap-1">
          {(dict.data?.metrics || []).map((m: any) => (
            <button key={m.key} title={m.definition} onClick={() => toggle(m.key)} className={`rounded-full border px-2 py-0.5 text-[11px] ${metrics.includes(m.key) ? "border-primary bg-primary/10" : ""}`}>{m.name}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select className="h-8 rounded-lg border bg-background px-2" value={dimension} onChange={(e) => setDimension(e.target.value)}>
            {Object.entries(dict.data?.dimensions || {}).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}
          </select>
          <Input className="h-8 w-24" type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} />
          <span className="text-xs text-muted-foreground">天</span>
          <Button size="sm" variant="outline" onClick={() => run(false)}>预览</Button>
          <Button size="sm" onClick={() => run(true)}>保存</Button>
        </div>
      </Section>
      {preview && (
        <Section title="预览" className="mt-4">
          {rows.length === 0 ? "统计期内没有数据" : <Bars data={rows} xKey="key" bars={metrics.slice(0, 3).map((m, i) => ({ key: m, name: preview.metrics.find((x: any) => x.key === m)?.name || m, color: ["#6366f1", "#0ea5e9", "#14b8a6"][i] }))} />}
        </Section>
      )}
      <div className="mt-4 space-y-2">
        {!saved.data ? <LoadingBlock rows={1} /> : savedPage.items.map((r) => (
          <button key={r.id} className="block w-full rounded-lg border px-3 py-2 text-left text-sm hover:bg-muted/40" onClick={async () => setPreview((await api<any>(`/reports/${r.id}/run`)).data)}>
            {r.name}<span className="ml-2 text-xs text-muted-foreground">{r.metrics.join("、")} · 按{r.dimension} · {r.period_days} 天</span>
          </button>
        ))}
        <PaginationBar page={savedPage.page} pages={savedPage.pages} total={savedPage.total} onPage={savedPage.setPage} />
      </div>
    </div>
  );
}
