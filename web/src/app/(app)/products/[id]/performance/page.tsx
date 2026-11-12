"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { amount, pct } from "@/lib/format";
import { ErrorBlock, PageHeader, PageLoading, Section, StatCard } from "@/components/common";
import { Bars } from "@/components/charts";
import { Button } from "@/components/ui/button";

export default function PerformancePage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, mutate } = useApi<any>(`/products/${id}/performance`);
  const versions = useApi<any[]>(`/products/${id}/versions`);
  const [a, setA] = useState(1);
  const [b, setB] = useState(2);
  const [diff, setDiff] = useState<any>(null);
  if (error) return <ErrorBlock error={error} />;
  if (!data) return <PageLoading />;
  const m = data.metrics;
  const compare = async () => {
    try {
      setDiff(await api(`/products/${id}/compare?a=${a}&b=${b}`));
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const sale = async (action: string) => {
    const reason = window.prompt(action === "suspend" ? "停售原因" : "恢复原因");
    if (!reason) return;
    try {
      await api(`/products/${id}/sale`, { method: "POST", json: { action, reason } });
      toast.success(action === "suspend" ? "已停售" : "已恢复");
      mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader crumbs={[{ label: "产品", href: `/products/${id}` }, { label: "表现" }]} title={`${data.name} 的经营表现`} description="保费、件数、赔付率、预警率和欺诈可疑率按产品归集。触发停售条件时，动作为停售的自动停售，动作为复核的通知业务管理员。" actions={data.status === "published" ? <Button variant="outline" onClick={() => sale("suspend")}>停售</Button> : data.status === "suspended" ? <Button onClick={() => sale("resume")}>恢复销售</Button> : null} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="签单保费" value={amount(m.premium)} hint={`${m.policies} 件，在保 ${m.active_policies}`} />
        <StatCard label="赔付率" value={pct(m.loss_ratio)} hint={`已决 ${amount(m.paid)} · 未决 ${amount(m.outstanding)}`} />
        <StatCard label="核保通过率" value={pct(m.uw_pass_rate)} />
        <StatCard label="欺诈可疑率" value={pct(m.fraud_rate)} hint={`预警 ${m.alerts} 条`} />
      </div>
      {data.triggered?.length > 0 && <div className="mt-3 rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm">已触发：{data.triggered.map((t: any) => `${t.label} 当前 ${t.current} ${t.op} ${t.value}`).join("；")}</div>}
      <Section title="月度趋势" className="mt-4"><Bars data={data.trend.length ? data.trend : [{ month: "—", premium: 0 }]} xKey="month" bars={[{ key: "premium", name: "保费", color: "#6366f1" }, { key: "paid", name: "赔款", color: "#f43f5e" }]} /></Section>
      <Section title="版本对比" className="mt-4">
        <div className="mb-2 flex items-center gap-2 text-sm">
          <select className="h-8 rounded-lg border bg-background px-2" value={a} onChange={(e) => setA(Number(e.target.value))}>{(versions.data || []).map((v) => <option key={v.version} value={v.version}>V{v.version}</option>)}</select>
          <span>与</span>
          <select className="h-8 rounded-lg border bg-background px-2" value={b} onChange={(e) => setB(Number(e.target.value))}>{(versions.data || []).map((v) => <option key={v.version} value={v.version}>V{v.version}</option>)}</select>
          <Button size="sm" variant="outline" onClick={compare}>对比</Button>
        </div>
        {diff && <div className="text-sm">条款新增 {diff.summary.clauses_added}、删除 {diff.summary.clauses_removed}、修改 {diff.summary.clauses_modified}；费率变化 {diff.summary.rates_changed} 项。</div>}
      </Section>
    </div>
  );
}
