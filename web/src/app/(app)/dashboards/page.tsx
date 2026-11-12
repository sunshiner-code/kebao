"use client";

import Link from "next/link";
import { useApi } from "@/lib/api";
import { amount, pct } from "@/lib/format";
import { ErrorBlock, LoadingBlock, PageHeader, Section, StatCard } from "@/components/common";
import { Bars } from "@/components/charts";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";

export default function DashboardsPage() {
  const dict = useApi<any>("/dashboards/metrics");
  const biz = useApi<any>("/dashboards/business?days=365");
  const risk = useApi<any>("/dashboards/risk?days=180");
  const ag = useApi<any>("/dashboards/agents?days=90");
  const compute = useApi<any>("/dashboards/compute?days=30");
  const def = (key: string) => (dict.data?.metrics || []).find((m: any) => m.key === key);
  if (biz.error) return <ErrorBlock error={biz.error} />;
  const b = biz.data?.kpis;
  return (
    <div>
      <PageHeader title="数据看板" description="经营、风险、智能体效能与云私计算。指标口径以指标字典为准，悬浮「ⓘ」可查看定义与计算时点。" actions={<Link href="/dashboards/reports"><Button variant="outline">自定义报表</Button></Link>} />
      <Tabs defaultValue="business">
        <TabsList>
          <TabsTrigger value="business">经营</TabsTrigger>
          <TabsTrigger value="risk">风险</TabsTrigger>
          <TabsTrigger value="agents">智能体效能</TabsTrigger>
          <TabsTrigger value="compute">云私计算</TabsTrigger>
          <TabsTrigger value="dict">指标字典</TabsTrigger>
        </TabsList>
        <TabsContent value="business" className="mt-4 space-y-4">
          {!b ? <LoadingBlock /> : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard label="保费收入" value={amount(b.premium)} hint={`已赚 ${amount(b.earned_premium)}。${def("premium")?.definition || ""}`} />
                <StatCard label="新单 / 续保" value={`${b.policies_new} / ${b.renewals}`} hint={`续保率 ${pct(b.renewal_rate)}`} />
                <StatCard label="赔付率" value={pct(b.loss_ratio)} tone={(b.loss_ratio || 0) > 0.7 ? "danger" : "default"} hint={`综合成本率 ${pct(b.combined_ratio)}。${def("loss_ratio")?.definition || ""}`} />
                <StatCard label="核保通过率" value={pct(b.uw_pass_rate)} hint={`投保 ${b.applications} 件 · 在保 ${b.active_policies}`} />
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Section title="保费趋势"><Bars data={biz.data.trend} xKey="month" bars={[{ key: "premium", name: "签单保费", color: "#6366f1" }]} /></Section>
                <Section title="险种结构"><Bars data={biz.data.structure.line} xKey="name" horizontal bars={[{ key: "value", name: "保费", color: "#0ea5e9" }]} /></Section>
                <Section title="行业结构"><Bars data={biz.data.structure.industry} xKey="name" bars={[{ key: "value", name: "保费", color: "#14b8a6" }]} /></Section>
                <Section title="机构与渠道">
                  <div className="text-xs text-muted-foreground">渠道：{biz.data.structure.channel.map((c: any) => `${c.name} ${c.value} 件`).join("、") || "—"}</div>
                  <Bars data={biz.data.structure.org} xKey="name" bars={[{ key: "value", name: "保费", color: "#f59e0b" }]} />
                </Section>
              </div>
            </>
          )}
        </TabsContent>
        <TabsContent value="risk" className="mt-4 space-y-4">
          {!risk.data ? <LoadingBlock /> : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard label="预警" value={risk.data.kpis.alerts} hint={`红 ${risk.data.kpis.red} · 橙 ${risk.data.kpis.orange}`} tone={risk.data.kpis.red ? "danger" : "default"} />
                <StatCard label="预警精确率" value={pct(risk.data.kpis.alert_precision)} hint={def("alert_precision")?.definition} />
                <StatCard label="事故转化率" value={pct(risk.data.kpis.incident_conversion)} hint={`事故 ${risk.data.kpis.incidents}。${def("incident_conversion")?.definition || ""}`} />
                <StatCard label="欺诈识别" value={risk.data.kpis.fraud_flags} hint={`涉及 ${amount(risk.data.kpis.fraud_amount)}`} />
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Section title="在保企业风险等级"><Bars data={risk.data.risk_levels} xKey="name" bars={[{ key: "value", name: "企业数", color: "#f59e0b" }]} /></Section>
                <Section title="赔付驱动（事故类型）"><Bars data={risk.data.loss_drivers} xKey="name" horizontal bars={[{ key: "value", name: "金额", color: "#f43f5e" }]} /></Section>
                <Section title="检测方法命中"><Bars data={risk.data.methods} xKey="name" bars={[{ key: "value", name: "次数", color: "#6366f1" }]} /></Section>
                <Section title="欺诈模式命中"><Bars data={risk.data.fraud_patterns.length ? risk.data.fraud_patterns : [{ name: "暂无", value: 0 }]} xKey="name" horizontal bars={[{ key: "value", name: "次数", color: "#8b5cf6" }]} /></Section>
              </div>
            </>
          )}
        </TabsContent>
        <TabsContent value="agents" className="mt-4 space-y-4">
          {!ag.data ? <LoadingBlock /> : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard label="运行量" value={ag.data.kpis.runs} hint={`成功率 ${pct(ag.data.kpis.success_rate)}`} />
                <StatCard label="平均评估得分" value={ag.data.kpis.avg_score ?? "—"} hint={`达标率 ${pct(ag.data.kpis.pass_rate)}`} />
                <StatCard label="人审修改率" value={pct(ag.data.kpis.human_modification_rate)} hint={`反思触发率 ${pct(ag.data.kpis.reflection_rate)}`} />
                <StatCard label="降级占比（D2+）" value={pct(ag.data.kpis.degraded_share)} hint={`平均耗时 ${ag.data.kpis.avg_duration ?? "—"} 秒（已扣除等待人工）`} />
              </div>
              <Section title="分智能体" contentClassName="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="border-b text-left text-muted-foreground"><tr><th className="py-2">智能体</th><th>运行</th><th>成功率</th><th>均分</th><th>达标率</th><th>反思率</th><th>人审修改率</th><th>热/冷启动</th></tr></thead>
                  <tbody>
                    {ag.data.by_agent.map((a: any) => (
                      <tr key={a.agent_id} className="border-b last:border-0">
                        <td className="py-1.5">{a.name}</td><td>{a.runs}</td><td>{pct(a.success_rate)}</td><td>{a.avg_score ?? "—"}</td><td>{pct(a.pass_rate)}</td><td>{pct(a.reflection_rate)}</td><td>{pct(a.human_modification_rate)}</td><td>{a.warm}/{a.cold}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            </>
          )}
        </TabsContent>
        <TabsContent value="compute" className="mt-4">
          {compute.error ? <ErrorBlock error={compute.error} /> : !compute.data ? <LoadingBlock rows={2} /> : (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard label="云域调用" value={compute.data.kpis.cloud_calls} hint={`费用 ${compute.data.kpis.cloud_cost} 元`} />
                <StatCard label="私域调用" value={compute.data.kpis.private_calls} hint={`GPU ${compute.data.kpis.gpu_hours} 小时`} />
                <StatCard label="出域" value={compute.data.kpis.egress} hint={`拦截 ${compute.data.kpis.egress_blocked} · 占位符 ${compute.data.kpis.placeholders}`} />
                <StatCard label="兜底切换" value={compute.data.kpis.fallbacks} />
              </div>
              <Section title="按智能体的费用"><Bars data={compute.data.by_agent} xKey="agent" bars={[{ key: "cost", name: "云端费用", color: "#0ea5e9" }, { key: "gpu_cost", name: "GPU 折算", color: "#14b8a6" }]} /></Section>
            </div>
          )}
        </TabsContent>
        <TabsContent value="dict" className="mt-4">
          <Section contentClassName="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-b text-left text-muted-foreground"><tr><th className="py-2">指标</th><th>看板</th><th>口径</th><th>计算时点</th></tr></thead>
              <tbody>
                {(dict.data?.metrics || []).map((m: any) => (
                  <tr key={m.key} className="border-b align-top last:border-0"><td className="py-1.5 font-medium">{m.name}</td><td>{m.board}</td><td>{m.definition}</td><td>{m.timing}</td></tr>
                ))}
              </tbody>
            </table>
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
