"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { agentLabel, dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { DataLevel, EmptyState, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const CATS = [
  ["", "全部"],
  ["operation", "操作审计"],
  ["login", "登录审计"],
  ["data_access", "数据访问审计"],
  ["config", "配置变更"],
];

export default function AuditPage() {
  const egress = useApi<any[]>("/admin/egress");
  const [cat, setCat] = useState("");
  const audit = useApi<any[]>(`/admin/audit${cat ? `?category=${cat}` : ""}`);
  const egressPage = usePagedList(egress.data || [], "egress");
  const auditPage = usePagedList(audit.data || [], cat);
  return (
    <div>
      <PageHeader title="隐私与审计" description="每一次出域都记录命中的脱敏规则、字段路径、占位符数量与二次扫描结果，可对照脱敏前后内容；所有操作、登录、数据访问与配置变更均可审计。" />
      <Tabs defaultValue="egress">
        <TabsList>
          <TabsTrigger value="egress">出域审计</TabsTrigger>
          <TabsTrigger value="audit">操作与访问审计</TabsTrigger>
        </TabsList>
        <TabsContent value="egress" className="mt-3 space-y-3">
          {!egress.data ? (
            <LoadingBlock />
          ) : egress.data.length === 0 ? (
            <EmptyState title="没有出域记录" />
          ) : (
            <>
            {egressPage.items.map((e) => (
              <Section key={e.id} title={`#${e.id} ${e.task}`} description={`运行 #${e.run_id} · ${agentLabel(e.agent_id)} → ${e.target_model} · ${dateTime(e.ts)}`} actions={<span className="flex gap-1.5"><DataLevel level={e.source_level} /><Pill tone={e.scan_result === "pass" ? "green" : "red"}>二次扫描{e.scan_result === "pass" ? "通过" : "拦截"}</Pill></span>}>
                <div className="mb-2 flex flex-wrap gap-1.5 text-xs">
                  {Object.entries(e.rules_hit || {}).map(([k, v]: any) => (
                    <Pill key={k} tone="teal">
                      {k} × {v}
                    </Pill>
                  ))}
                  <span className="text-muted-foreground">占位符 {e.placeholder_count} 个 · 字段 {(e.fields || []).length} 个</span>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="mb-1 text-[11px] font-medium text-rose-700">脱敏前（私域原文）</div>
                    <div className="max-h-40 overflow-y-auto rounded-md bg-rose-500/5 p-2 font-mono text-[11px] leading-relaxed break-all">{e.original_preview}</div>
                  </div>
                  <div>
                    <div className="mb-1 text-[11px] font-medium text-teal-700">出域内容（发送云端）</div>
                    <div className="max-h-40 overflow-y-auto rounded-md bg-teal-500/5 p-2 font-mono text-[11px] leading-relaxed break-all">{e.content_preview}</div>
                  </div>
                </div>
              </Section>
            ))}
            <PaginationBar page={egressPage.page} pages={egressPage.pages} total={egressPage.total} onPage={egressPage.setPage} />
            </>
          )}
        </TabsContent>
        <TabsContent value="audit" className="mt-3">
          <div className="mb-3 flex gap-2">
            {CATS.map(([k, l]) => (
              <button key={k} onClick={() => setCat(k)} className={cn("rounded-full border px-3 py-1 text-xs", cat === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
                {l}
              </button>
            ))}
          </div>
          <Section contentClassName="p-0">
            {!audit.data ? (
              <div className="p-4"><LoadingBlock rows={2} /></div>
            ) : audit.data.length === 0 ? (
              <div className="p-4"><EmptyState title="没有审计记录" /></div>
            ) : (
            <>
            <table className="w-full text-xs">
              <thead className="border-b bg-muted/40 text-left text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">时间</th>
                  <th>用户</th>
                  <th>类别</th>
                  <th>动作</th>
                  <th>对象</th>
                  <th>详情</th>
                </tr>
              </thead>
              <tbody>
                {auditPage.items.map((a) => (
                  <tr key={a.id} className="border-b last:border-0">
                    <td className="px-3 py-1.5 text-muted-foreground">{dateTime(a.ts)}</td>
                    <td>{a.username || "系统"}</td>
                    <td>
                      <Pill>{CATS.find((c) => c[0] === a.category)?.[1] || a.category}</Pill>
                    </td>
                    <td>{a.action}</td>
                    <td className="font-mono">
                      {a.object_type} {a.object_id}
                    </td>
                    <td className="max-w-md truncate text-muted-foreground">{Object.keys(a.detail || {}).length ? JSON.stringify(a.detail) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PaginationBar className="border-t px-3 py-3" page={auditPage.page} pages={auditPage.pages} total={auditPage.total} onPage={auditPage.setPage} />
            </>
            )}
          </Section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
