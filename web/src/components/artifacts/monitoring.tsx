"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { METHOD_NAMES, MATERIAL_SOURCE, num } from "@/lib/format";
import { AlertLevel, KV, Pill, Section, StatCard } from "@/components/common";
import { AreaTrend, TimeSeries } from "@/components/charts";

export function IndicatorTable({ indicators }: { indicators: any[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed text-xs">
        <colgroup>
          <col className="w-[18%]" />
          <col className="w-[8%]" />
          <col className="w-[18%]" />
          <col className="w-[8%]" />
          <col className="w-[14%]" />
          <col className="w-[12%]" />
          <col className="w-[12%]" />
          <col className="w-[10%]" />
        </colgroup>
        <thead className="border-b text-left text-muted-foreground">
          <tr>
            <th className="py-2">指标</th>
            <th>数据源</th>
            <th>检测方法</th>
            <th>频率</th>
            <th className="text-right">阈值</th>
            <th>等级映射</th>
            <th>关联责任</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {indicators.map((i) => (
            <tr key={i.id} className="border-b align-top last:border-0" title={i.rationale}>
              <td className="py-2 pr-2">
                <div className="font-medium leading-snug break-words">{i.name}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground break-words">{(i.asset_names || []).join("、")}</div>
              </td>
              <td className="py-2 whitespace-nowrap">{MATERIAL_SOURCE[i.source] || i.source}</td>
              <td className="py-2">
                <div className="flex flex-wrap gap-1">
                  {(i.methods || []).map((m: string) => (
                    <Pill key={m} tone={m.startsWith("TP") ? "violet" : "blue"}>
                      {METHOD_NAMES[m] || m}
                    </Pill>
                  ))}
                </div>
              </td>
              <td className="py-2 whitespace-nowrap">{i.frequency}</td>
              <td className="py-2 text-right tabular">
                {num(i.threshold, 2)} {i.unit !== "事件" && i.unit}
                <div className="text-[10px] leading-snug text-muted-foreground break-words text-left">{i.threshold_basis}</div>
              </td>
              <td className="py-2 text-[11px] leading-snug text-muted-foreground">
                {i.levels ? `黄 ${i.levels.yellow} / 橙 ${i.levels.orange} / 红 ${i.levels.red}` : "—"}
              </td>
              <td className="py-2 leading-snug break-words">{i.liability}</td>
              <td className="py-2">{i.status === "degraded" ? <Pill tone="amber" className="whitespace-normal break-words">降级：{i.fallback}</Pill> : <Pill tone="green">启用</Pill>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function MonitorPlanView({ data }: { data: any }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="监测指标" value={data.indicators?.length} />
        <StatCard label="降级指标" value={(data.indicators || []).filter((i: any) => i.status === "degraded").length} tone="warning" />
        <StatCard label="检测方法" value={new Set((data.indicators || []).flatMap((i: any) => i.methods)).size} hint="DM1–DM5、TP1–TP5" />
        <StatCard label="报告周期" value={data.report_cycle} />
      </div>
      <Section title="险种方法覆盖" description="每个险种至少 2 种检测方法，其中至少 1 种为科技产品专项检测">
        <div className="grid gap-2 md:grid-cols-3">
          {(data.method_coverage || []).map((c: any) => (
            <div key={c.line} className="flex items-start gap-2 rounded-lg border p-2 text-xs">
              {c.ok ? <CheckCircle2 className="mt-0.5 size-3.5 text-emerald-600" /> : <XCircle className="mt-0.5 size-3.5 text-rose-500" />}
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-muted-foreground">{c.methods.join("、")}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>
      <Section title="监测指标清单" contentClassName="p-0">
        <IndicatorTable indicators={data.indicators || []} />
      </Section>
      {(data.uw_points || []).length > 0 && (
        <Section title="承接的核保监测要点">
          {data.uw_points.map((p: any, i: number) => (
            <div key={i} className="text-xs text-muted-foreground">
              · {p.text}
            </div>
          ))}
        </Section>
      )}
    </div>
  );
}

export function RiskMgmtReportView({ data }: { data: any }) {
  const st = data.stats || {};
  const ri = st.risk_index || {};
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="风险指数" value={num(ri.end, 0)} hint={`期初 ${num(ri.start, 0)}，${ri.change >= 0 ? "+" : ""}${num(ri.change, 1)}`} tone={ri.end >= 60 ? "danger" : "warning"} />
        <StatCard label="风险等级" value={ri.level} />
        <StatCard label="预警" value={st.alerts?.total} hint={`红 ${st.alerts?.by_level?.red || 0} · 橙 ${st.alerts?.by_level?.orange || 0} · 黄 ${st.alerts?.by_level?.yellow || 0}`} />
        <StatCard label="时限处置率" value={`${Math.round((st.alerts?.sla_rate ?? 1) * 100)}%`} hint={`误报率 ${Math.round((st.alerts?.false_positive_rate || 0) * 100)}%`} />
        <StatCard label="事故" value={st.incidents ?? 0} tone={st.incidents ? "danger" : "success"} />
      </div>
      <Section title="风险指数趋势" description={`${data.scope?.period_label} · ${data.scope?.start?.slice(0, 10)} 至 ${data.scope?.end?.slice(0, 10)}`}>
        <AreaTrend data={(ri.series || []).map(([d, v]: any) => ({ d: d.slice(5), v }))} xKey="d" dataKey="v" domain={[0, 100]} />
      </Section>
      <div className="grid gap-4 lg:grid-cols-2">
        {(data.report?.sections || []).map((s: any) => (
          <Section key={s.key} title={s.title} description={(s.refs || []).length ? `依据：${s.refs.slice(0, 6).join("、")}` : undefined}>
            <div className="space-y-1.5 text-sm leading-relaxed">
              {s.paragraphs.map((p: string, i: number) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </Section>
        ))}
      </div>
      <Section title="关键风险指标" contentClassName="grid gap-4 md:grid-cols-2">
        {(st.indicators || [])
          .filter((i: any) => i.points)
          .slice(0, 6)
          .map((i: any) => (
            <div key={i.id + i.asset}>
              <div className="mb-1 text-xs font-medium">
                {i.name} · {i.asset}
              </div>
              <TimeSeries series={[{ name: i.name, points: i.series }]} threshold={i.threshold} height={150} />
            </div>
          ))}
      </Section>
      {(st.alerts?.items || []).length > 0 && (
        <Section title="期内预警">
          {st.alerts.items.map((a: any) => (
            <div key={a.alert_no} className="flex items-center gap-2 border-b py-1.5 text-xs last:border-0">
              <AlertLevel level={a.level} />
              <span className="font-mono">{a.alert_no}</span>
              <span className="flex-1">{a.title}</span>
              <span className="text-muted-foreground">{a.status_label}</span>
            </div>
          ))}
        </Section>
      )}
      <KV items={[{ label: "启动方式", value: data.start_mode === "warm" ? "热启动" : "冷启动" }]} />
    </div>
  );
}

export function DataWindowView({ data }: { data: any }) {
  const groups = Object.entries(data.series || {}).slice(0, 8);
  return (
    <div className="space-y-4">
      <Section title="事故前后数据窗口" description={`${data.window?.from?.slice(0, 16)} 至 ${data.window?.to?.slice(0, 16)}（私域保存）`}>
        <div className="grid gap-4 md:grid-cols-2">
          {groups.map(([k, pts]: any) => (
            <div key={k}>
              <div className="mb-1 text-xs font-medium">{k.split("|").slice(1).join(" · ")}</div>
              <TimeSeries series={[{ name: k, points: pts }]} height={150} />
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
