"use client";

import { useApi } from "@/lib/api";
import { pct } from "@/lib/format";
import { ErrorBlock, LoadingBlock, PageHeader, Pill, Section, StatCard } from "@/components/common";

export default function MonitorPage() {
  const { data, error } = useApi<any>("/admin/monitor", { refreshInterval: 8000 });
  if (error) return <ErrorBlock error={error} />;
  if (!data) return <LoadingBlock />;
  return (
    <div>
      <PageHeader title="系统监控" description="接口延迟、运行队列、模型熔断、数据源、出域异常与存储。严重告警同时通知业务管理员。" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="近 15 分钟请求" value={data.api.requests} hint={`错误率 ${pct(data.api.error_rate)} · P95 ${data.api.p95_ms} ms`} />
        <StatCard label="执行器" value={`${data.executor.active}/${data.executor.workers}`} hint={`排队 ${data.executor.queue} · 24h 失败率 ${pct(data.executor.fail_rate_24h)}`} />
        <StatCard label="监测调度" value={data.stream.enabled ? "开启" : "关闭"} hint={data.stream.tick_lag_seconds != null ? `距上次 ${data.stream.tick_lag_seconds} 秒` : "尚无监测方案"} />
        <StatCard label="数据库" value={`${(data.storage.db_bytes / 1048576).toFixed(1)} MB`} />
      </div>
      {data.alerts.length > 0 && (
        <div className="mt-4 space-y-2">
          {data.alerts.map((a: any, i: number) => (
            <div key={i} className={`rounded-lg border px-3 py-2 text-sm ${a.level === "error" ? "border-rose-300 bg-rose-50" : "border-amber-300 bg-amber-50"}`}>{a.object}：{a.message}</div>
          ))}
        </div>
      )}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Section title="模型服务">
          {data.models.map((m: any) => (
            <div key={m.id} className="flex items-center justify-between border-b py-1.5 text-sm last:border-0">
              <span>{m.name}</span>
              <span className="flex items-center gap-2 text-xs text-muted-foreground">{m.calls_1h} 次/时 · {m.avg_latency_ms} ms <Pill tone={m.state === "healthy" ? "green" : m.state === "circuit_open" ? "red" : "gray"}>{m.state}</Pill></span>
            </div>
          ))}
        </Section>
        <Section title="外部数据源">
          {data.sources.map((d: any) => (
            <div key={d.code} className="flex items-center justify-between border-b py-1.5 text-sm last:border-0">
              <span>{d.name}</span>
              <Pill tone={!d.enabled ? "gray" : d.health === "healthy" ? "green" : "red"}>{d.enabled ? d.health : "停用"}</Pill>
            </div>
          ))}
        </Section>
      </div>
    </div>
  );
}
