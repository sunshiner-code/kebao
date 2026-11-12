"use client";

import { useApi } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { TimeSeries } from "@/components/charts";

const NAMES: Record<string, { name: string; unit: string }> = {
  vibration_rms: { name: "振动有效值", unit: "mm/s" },
  bearing_temp: { name: "轴承温度", unit: "℃" },
  motor_current: { name: "运行电流", unit: "A" },
  water_leak: { name: "环境水浸", unit: "" },
  login_failures: { name: "登录失败次数", unit: "次/小时" },
  egress_mb: { name: "数据外传量", unit: "MB/小时" },
  unpatched_critical: { name: "未修补高危漏洞", unit: "个" },
  milestone_delay_days: { name: "里程碑延误天数", unit: "天" },
  rd_spend_index: { name: "研发费用指数", unit: "" },
};

export function TelemetryCard({ policyId, metric, asset, hours = 168, refresh }: { policyId: number; metric: string; asset: string; hours?: number; refresh?: number }) {
  const { data } = useApi<any>(`/policies/${policyId}/telemetry?metric=${metric}&asset=${encodeURIComponent(asset)}&hours=${hours}`, { refreshInterval: refresh || 0 });
  const info = NAMES[metric] || { name: metric, unit: "" };
  return (
    <div className="rounded-xl border bg-card p-3">
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium">
          {info.name}
          <span className="ml-1 text-muted-foreground">· {asset}</span>
        </span>
        {data?.points?.length ? <span className="tabular text-muted-foreground">最新 {data.points[data.points.length - 1][1]} {info.unit}</span> : null}
      </div>
      {!data ? <Skeleton className="h-[160px]" /> : <TimeSeries series={[{ name: info.name, points: data.points }]} threshold={data.threshold} unit={info.unit} height={160} />}
    </div>
  );
}
