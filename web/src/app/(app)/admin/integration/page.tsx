"use client";

import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { ErrorBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

function parseMap(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const i = line.indexOf("=");
    if (i <= 0) continue;
    const key = line.slice(0, i).trim();
    const value = line.slice(i + 1).trim();
    if (key && value) out[key] = value;
  }
  return out;
}

function formatMap(map?: Record<string, string>) {
  return Object.entries(map || {})
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function ConnectorCard({ c, onChanged }: { c: any; onChanged: () => void }) {
  const [mode, setMode] = useState(c.mode || "sim");
  const [direction, setDirection] = useState(c.direction || "push");
  const [host, setHost] = useState(c.host || "");
  const [port, setPort] = useState(String(c.port || 443));
  const [path, setPath] = useState(c.path || "");
  const [allow, setAllow] = useState((c.allow_ips || []).join("\n"));
  const [fields, setFields] = useState(formatMap(c.field_map));
  const [assets, setAssets] = useState(formatMap(c.asset_map));
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  const save = async () => {
    try {
      const body: any = {
        mode,
        direction,
        host,
        port: Number(port || 443),
        path,
        allow_ips: allow.split(/[\s,]+/).filter(Boolean),
        field_map: parseMap(fields),
        asset_map: parseMap(assets),
      };
      if (mode === "sim") body.direction = "push";
      if (secret && mode === "live") body.secret = secret;
      await api(`/admin/connectors/${c.id}`, { method: "PATCH", json: body });
      setSecret("");
      toast.success("已保存");
      onChanged();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const rotate = async () => {
    try {
      const out = await api<{ token: string }>(`/admin/connectors/${c.id}/token`, { method: "POST" });
      setToken(out.token);
      onChanged();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const outage = async (on: boolean) => {
    try {
      await api(`/admin/connectors/${c.id}/outage?outage=${on}`, { method: "POST" });
      onChanged();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const live = mode === "live";
  const address = live && direction === "push" ? c.ingest_url : c.endpoint;
  return (
    <Section
      title={c.name}
      actions={
        !live && (
          <Button size="sm" variant="outline" onClick={() => outage(c.status !== "offline")}>
            {c.status === "offline" ? "恢复接入" : "模拟中断"}
          </Button>
        )
      }
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Pill tone={c.status === "online" ? "green" : c.status === "degraded" ? "amber" : "red"}>{c.status}</Pill>
        <Pill>{live ? "实接" : "仿真"}</Pill>
        <span className="text-muted-foreground">{address}</span>
      </div>
      {c.quality && <div className="mt-1 text-xs text-muted-foreground">24 小时收到 {c.quality.received}/{c.quality.expected} · 缺失率 {(c.quality.missing_rate * 100).toFixed(0)}% · 延迟 {c.quality.lag_hours ?? "—"} 小时 · 异常值 {(c.quality.anomaly_rate * 100).toFixed(1)}%</div>}
      {c.last_error && <div className="mt-1 text-xs text-rose-600">{c.last_error}</div>}
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <label className="text-xs">接入方式
          <select className="mt-1 h-8 w-full rounded-lg border bg-transparent px-2 text-sm" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="sim">仿真</option>
            <option value="live">实接</option>
          </select>
        </label>
        {live && (
          <>
            <label className="text-xs">方向
              <select className="mt-1 h-8 w-full rounded-lg border bg-transparent px-2 text-sm" value={direction} onChange={(e) => setDirection(e.target.value)}>
                <option value="push">推送</option>
                <option value="pull">拉取（仅运营）</option>
              </select>
            </label>
            <Input className="h-8" placeholder="主机" value={host} onChange={(e) => setHost(e.target.value)} />
            <Input className="h-8" placeholder="端口" value={port} onChange={(e) => setPort(e.target.value)} />
            <Input className="h-8 md:col-span-2" placeholder="路径，例如 /ops" value={path} onChange={(e) => setPath(e.target.value)} />
            <Textarea rows={2} placeholder={"允许来源，每行一个地址"} value={allow} onChange={(e) => setAllow(e.target.value)} />
            <Textarea rows={2} placeholder={"字段映射，每行 对方指标=平台指标"} value={fields} onChange={(e) => setFields(e.target.value)} />
            <Textarea rows={2} className="md:col-span-2" placeholder={"设备映射，每行 对方编号=监测方案序列号"} value={assets} onChange={(e) => setAssets(e.target.value)} />
            <Input className="h-8 md:col-span-2" type="password" placeholder={c.has_secret ? "拉取凭证已保存，填写才会替换" : "拉取凭证（只在填写时提交）"} value={secret} onChange={(e) => setSecret(e.target.value)} />
          </>
        )}
      </div>
      {(live || mode !== (c.mode || "sim")) && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" onClick={save}>保存</Button>
          {live && direction === "push" && <Button size="sm" variant="outline" onClick={rotate}>轮换推送令牌</Button>}
        </div>
      )}
      {live && token && <div className="mt-2 rounded-md border bg-muted/40 p-2 text-xs">推送令牌只显示这一次：{token}<Button size="sm" variant="ghost" className="ml-2" onClick={() => setToken("")}>知道了</Button></div>}
    </Section>
  );
}

export default function IntegrationPage() {
  const sources = useApi<any[]>("/admin/data-sources");
  const cons = useApi<any[]>("/admin/connectors");
  const sourcePage = usePagedList(sources.data || [], "sources");
  const connectorPage = usePagedList(cons.data || [], "connectors");
  const toggle = async (code: string, enabled: boolean) => {
    try {
      await api(`/admin/data-sources/${code}`, { method: "PATCH", json: { enabled } });
      sources.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const probe = async () => {
    try {
      await api("/admin/data-sources/probe", { method: "POST" });
      toast.success("探测完成");
      sources.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  if (sources.error) return <ErrorBlock error={sources.error} />;
  return (
    <div>
      <PageHeader title="数据源与企业接入" description="外部数据源的额度、费用与健康探测会被风险评估采集直接使用。企业连接器可保持仿真，或配置实接推送与运营拉取；凭证不回显。" actions={<Button variant="outline" onClick={probe}>立即探测</Button>} />
      <Tabs defaultValue="sources">
        <TabsList><TabsTrigger value="sources">外部数据源</TabsTrigger><TabsTrigger value="connectors">企业连接器</TabsTrigger></TabsList>
        <TabsContent value="sources" className="mt-3">
          <div className="grid gap-3 md:grid-cols-2">
          {sourcePage.items.map((d) => (
            <Section key={d.code} title={d.name} actions={<Switch checked={d.enabled} onCheckedChange={(v) => toggle(d.code, v)} />}>
              <div className="flex flex-wrap gap-1.5 text-xs"><Pill>{d.category}</Pill><Pill tone={d.health === "healthy" ? "green" : d.health === "down" ? "red" : "gray"}>{d.health}</Pill><span className="text-muted-foreground">{d.provider}</span></div>
              <div className="mt-2 text-xs text-muted-foreground">今日 {d.used_today}/{d.daily_quota} 次 · 累计失败率 {(d.failure_rate * 100).toFixed(1)}% · 延迟 {d.latency_ms} ms · 单价 {d.unit_cost} 元</div>
              {d.last_error && <div className="mt-1 text-xs text-rose-600">{d.last_error}</div>}
              <div className="mt-1 text-[11px] text-muted-foreground">最近探测 {dateTime(d.last_checked_at)}</div>
            </Section>
          ))}
          </div>
          <PaginationBar page={sourcePage.page} pages={sourcePage.pages} total={sourcePage.total} onPage={sourcePage.setPage} />
        </TabsContent>
        <TabsContent value="connectors" className="mt-3 space-y-2">
          {(cons.data || []).length === 0 && <div className="text-sm text-muted-foreground">暂无连接器。保单监测方案发布后会按物联网、日志和运营数据自动登记。</div>}
          {connectorPage.items.map((c) => (
            <ConnectorCard key={c.id} c={c} onChanged={() => cons.mutate()} />
          ))}
          <PaginationBar page={connectorPage.page} pages={connectorPage.pages} total={connectorPage.total} onPage={connectorPage.setPage} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
