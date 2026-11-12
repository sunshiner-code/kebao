"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { toast } from "sonner";
import { Building2, Loader2, Plus, Route, Workflow } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CreateEnterpriseDialog } from "@/components/create-enterprise-dialog";
import { dateTime, LINE_NAMES } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NODE_LABEL, NODE_TONE, STAGES } from "@/lib/pipeline";
import { Label } from "@/components/ui/label";

function CreateDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { user } = useAuth();
  const ents = useApi<any[]>("/enterprises");
  const [creatingEnt, setCreatingEnt] = useState(false);
  const [type, setType] = useState<"need" | "track" | "enterprise">("need");
  const [background, setBackground] = useState("");
  const [customerNeed, setCustomerNeed] = useState("");
  const [track, setTrack] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [lines, setLines] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("auto");
  const [busy, setBusy] = useState(false);
  const tracks = useMemo(() => Array.from(new Set((ents.data || []).map((e) => e.track))), [ents.data]);
  const activeTrack = track || tracks[0] || "";
  const candidates = (ents.data || []).filter((e) => type === "enterprise" || e.track === activeTrack);
  const submit = async () => {
    if (type === "need") {
      if (!background.trim() && !customerNeed.trim()) return toast.error("请描述产品开发背景或客户希望解决的问题");
    } else if (!selected.length) return toast.error(type === "track" ? "请选择样本企业" : "请选择一家企业");
    setBusy(true);
    try {
      const ent = (ents.data || []).find((e) => e.id === selected[0]);
      const out = await api<{ id: number }>("/design-projects", {
        method: "POST",
        json:
          type === "need"
            ? { name: name || "产品开发项目", project_type: "need", background, customer_need: customerNeed, enterprise_ids: [], target_lines: [], mode }
            : { name: name || (type === "track" ? `${activeTrack}赛道保障方案` : `${ent?.name}定制保障方案`), project_type: type, track: type === "track" ? activeTrack : ent?.track, enterprise_ids: selected, target_lines: lines, mode },
      });
      toast.success("项目已创建，风险评估智能体开始运行");
      onOpenChange(false);
      router.push(`/design/${out.id}`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建设计项目</DialogTitle>
          <DialogDescription>创建后自动启动风险评估；评估报告确认后依次进入条款设计与精算定价。</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {[
              { k: "need", icon: Workflow, t: "需求描述", d: "从产品开发背景和客户希望解决的问题开始" },
              { k: "track", icon: Route, t: "赛道通用产品", d: "选取同赛道多家样本企业，识别共性风险" },
              { k: "enterprise", icon: Building2, t: "企业定制方案", d: "单家企业，可按信用代码新建客户" },
            ].map((o) => (
              <button
                key={o.k}
                onClick={() => {
                  setType(o.k as any);
                  setSelected([]);
                }}
                className={cn("rounded-xl border p-3 text-left transition-colors", type === o.k ? "border-primary bg-primary/5" : "hover:bg-muted/50")}
              >
                <o.icon className="size-4 text-primary" />
                <div className="mt-1 text-sm font-medium">{o.t}</div>
                <div className="text-xs text-muted-foreground">{o.d}</div>
              </button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>项目名称</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="留空则自动命名" />
            </div>
            {type === "track" && (
              <div className="space-y-1.5">
                <Label>赛道</Label>
                <select
                  value={activeTrack}
                  onChange={(e) => {
                    setTrack(e.target.value);
                    setSelected([]);
                  }}
                  className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
                >
                  {tracks.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
          {type === "need" && (
            <div className="grid gap-3">
              <div className="space-y-1.5">
                <Label>产品开发背景</Label>
                <textarea value={background} onChange={(e) => setBackground(e.target.value)} className="min-h-20 w-full rounded-lg border bg-background px-3 py-2 text-sm" placeholder="这项产品准备解决什么业务场景" />
              </div>
              <div className="space-y-1.5">
                <Label>客户希望解决的问题</Label>
                <textarea value={customerNeed} onChange={(e) => setCustomerNeed(e.target.value)} className="min-h-20 w-full rounded-lg border bg-background px-3 py-2 text-sm" placeholder="客户希望保险分担哪一类支出" />
              </div>
            </div>
          )}
          {type !== "need" && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <Label>{type === "track" ? "样本企业（可多选）" : "目标企业（单选）"}</Label>
              {type === "enterprise" && (user?.role === "designer" || user?.role === "service") && (
                <Button type="button" variant="outline" size="sm" onClick={() => setCreatingEnt(true)}>
                  <Plus className="size-3.5" /> 按信用代码新建
                </Button>
              )}
            </div>
            <div className="grid max-h-48 gap-1.5 overflow-y-auto sm:grid-cols-2">
              {ents.isLoading && <LoadingBlock rows={2} />}
              {candidates.map((e) => {
                const on = selected.includes(e.id);
                return (
                  <button key={e.id} onClick={() => setSelected(type === "enterprise" ? [e.id] : on ? selected.filter((x) => x !== e.id) : [...selected, e.id])} className={cn("rounded-lg border px-3 py-2 text-left text-xs", on ? "border-primary bg-primary/5" : "hover:bg-muted/50")}>
                    <div className="font-medium">{e.name}</div>
                    <div className="text-muted-foreground">
                      {e.track} · {e.tech_field}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
          )}
          {type !== "need" && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(LINE_NAMES).map(([k, v]) => (
                <button key={k} onClick={() => setLines(lines.includes(k) ? lines.filter((x) => x !== k) : [...lines, k])} className={cn("rounded-full border px-2.5 py-1 text-xs", lines.includes(k) ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
                  {v}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center gap-4 text-sm">
            <Label>运行模式</Label>
            {[
              ["auto", "自动（非强制人审自动通过）"],
              ["semi", "半自动（每阶段人工确认）"],
            ].map(([k, l]) => (
              <label key={k} className="flex items-center gap-1.5 text-xs">
                <input type="radio" checked={mode === k} onChange={() => setMode(k)} className="accent-[var(--primary)]" /> {l}
              </label>
            ))}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy && <Loader2 className="size-3.5 animate-spin" />} 创建并启动风险评估
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <CreateEnterpriseDialog
      open={creatingEnt}
      onOpenChange={setCreatingEnt}
      onCreated={async (enterprise) => {
        await ents.mutate();
        setSelected([enterprise.id]);
      }}
    />
    </>
  );
}

function DesignProjects() {
  const params = useSearchParams();
  const [open, setOpen] = useState(params.get("new") === "1");
  const { data, error, mutate } = useApi<any[]>("/design-projects", { refreshInterval: 6000 });
  const page = usePagedList(data || [], "");
  return (
    <div>
      <PageHeader
        title="设计项目"
        description="产品设计流水线：风险评估 → 风险偏好与承受能力 → 条款设计 → 精算定价 → 产品包 → 审批发布。上游版本冻结后自动推进下游。"
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="size-4" /> 新建项目
          </Button>
        }
      />
      <CreateDialog open={open} onOpenChange={setOpen} />
      {error ? (
        <ErrorBlock error={error} retry={() => mutate()} />
      ) : !data ? (
        <LoadingBlock />
      ) : data.length === 0 ? (
        <EmptyState title="还没有设计项目" description="新建一个赛道通用或企业定制项目，智能体将从风险评估开始工作。" icon={Workflow} action={<Button onClick={() => setOpen(true)}>新建项目</Button>} />
      ) : (
        <div>
          <div className="space-y-3">
          {page.items.map((p) => (
            <Link key={p.id} href={`/design/${p.id}`}>
              <Section className="transition-shadow hover:shadow-md">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
                  <div className="min-w-0 lg:w-72">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{p.name}</span>
                      <Pill tone={p.project_type === "track" ? "blue" : p.project_type === "need" ? "teal" : "violet"}>{p.project_type === "track" ? "赛道通用" : p.project_type === "need" ? "需求描述" : "企业定制"}</Pill>
                    </div>
                    <div className="mt-1 truncate text-xs text-muted-foreground">{p.enterprises.map((e: any) => e.name).join("、")}</div>
                    <div className="text-xs text-muted-foreground">{dateTime(p.created_at)}</div>
                  </div>
                  <div className="grid flex-1 grid-cols-3 gap-2 md:grid-cols-6">
                    {STAGES.map(([k, l]) => {
                      const st = p.pipeline.nodes[k]?.status || "pending";
                      return (
                        <div key={k} className="rounded-lg border bg-muted/30 px-2 py-1.5">
                          <div className="text-[11px] text-muted-foreground">{l}</div>
                          <Pill tone={NODE_TONE[st]} className="mt-0.5">
                            {NODE_LABEL[st] || st}
                          </Pill>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </Section>
            </Link>
          ))}
          </div>
          <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <DesignProjects />
    </Suspense>
  );
}
