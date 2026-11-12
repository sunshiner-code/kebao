"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Loader2, Plus, ShieldCheck } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CreateEnterpriseDialog } from "@/components/create-enterprise-dialog";
import { amount, dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, RunStatus, Section, StatCard } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_TONE: Record<string, any> = { draft: "gray", submitted: "sky", underwriting: "blue", pending_decision: "amber", accepted: "green", rejected: "red", returned: "orange" };
const DECISION: Record<string, string> = { standard: "标准承保", conditional: "附条件承保", reject: "拒保", manual: "转人工" };

function NewApplication({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { user } = useAuth();
  const ents = useApi<any[]>("/enterprises");
  const [creatingEnt, setCreatingEnt] = useState(false);
  const prods = useApi<any[]>("/products?status=published");
  const [ent, setEnt] = useState<number | null>(null);
  const [prod, setProd] = useState<number | null>(null);
  const [si, setSi] = useState("10000000");
  const [tier, setTier] = useState("standard");
  const [statement, setStatement] = useState("");
  const [auth, setAuth] = useState({ iot: true, log: true, ops: true });
  const [lines, setLines] = useState<string[]>([]);
  const product = (prods.data || []).find((p) => p.id === prod);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!ent || !prod) return toast.error("请选择企业与产品");
    setBusy(true);
    try {
      const out = await api<{ id: number }>("/applications", { method: "POST", json: { enterprise_id: ent, product_id: prod, sum_insured: Number(si), tier, statement, authorizations: auth, lines: lines.length ? lines : product?.lines || [] } });
      toast.success("投保单已创建，请上传投保材料后发起核保");
      onOpenChange(false);
      router.push(`/underwriting/${out.id}`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>新建投保单</DialogTitle>
          <DialogDescription>选择企业与已发布产品；数据接入授权决定承保后风险预警能接入哪些企业数据。</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <Label>投保企业</Label>
                {user?.role === "service" && (
                  <Button type="button" variant="outline" size="sm" onClick={() => setCreatingEnt(true)}>
                    <Plus className="size-3.5" /> 新建客户
                  </Button>
                )}
              </div>
              <select className="h-8 w-full rounded-lg border bg-background px-2 text-sm" value={ent ?? ""} onChange={(e) => setEnt(Number(e.target.value))}>
                <option value="">请选择</option>
                {(ents.data || []).map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label>保险产品</Label>
              <select
                className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
                value={prod ?? ""}
                onChange={(e) => {
                  setProd(Number(e.target.value));
                  setLines([]);
                }}
              >
                <option value="">请选择</option>
                {(prods.data || []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label>保险金额（元）</Label>
              <Input value={si} onChange={(e) => setSi(e.target.value.replace(/[^\d]/g, ""))} />
              <div className="text-xs text-muted-foreground">{amount(Number(si))}</div>
            </div>
            <div className="space-y-1.5">
              <Label>方案档次</Label>
              <div className="flex gap-1.5">
                {[
                  ["basic", "基础"],
                  ["standard", "标准"],
                  ["premium", "优选"],
                ].map(([k, l]) => (
                  <button key={k} onClick={() => setTier(k)} className={cn("flex-1 rounded-md border py-1 text-xs", tier === k ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {product && (
            <div className="space-y-1.5">
              <Label>投保险种（产品包内可选，未选择则全部投保）</Label>
              <div className="flex flex-wrap gap-1.5">
                {product.lines.map((l: string, i: number) => {
                  const on = lines.includes(l);
                  return (
                    <button key={l} onClick={() => setLines(on ? lines.filter((x) => x !== l) : [...lines, l])} className={cn("rounded-full border px-2.5 py-1 text-xs", on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
                      {product.line_names[i]}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div className="space-y-1.5">
            <Label>投保声明</Label>
            <Textarea value={statement} onChange={(e) => setStatement(e.target.value)} rows={2} placeholder="例如：企业主营业务、设备维保情况、近三年事故情况" />
          </div>
          <div className="space-y-1.5">
            <Label>数据接入授权</Label>
            <div className="flex gap-4 text-xs">
              {[
                ["iot", "物联网设备数据"],
                ["log", "信息安全日志"],
                ["ops", "研发与运营数据"],
              ].map(([k, l]) => (
                <label key={k} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={(auth as any)[k]} onChange={(e) => setAuth({ ...auth, [k]: e.target.checked })} className="accent-[var(--primary)]" /> {l}
                </label>
              ))}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy && <Loader2 className="size-3.5 animate-spin" />} 创建投保单
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <CreateEnterpriseDialog
      open={creatingEnt}
      onOpenChange={setCreatingEnt}
      onCreated={async (enterprise) => {
        await ents.mutate();
        setEnt(enterprise.id);
      }}
    />
    </>
  );
}

function Queue() {
  const params = useSearchParams();
  const [open, setOpen] = useState(params.get("new") === "1");
  const [filter, setFilter] = useState("");
  const { data, error } = useApi<any[]>("/applications", { refreshInterval: 5000 });
  const rows = (data || []).filter((a) => !filter || a.status === filter);
  const page = usePagedList(rows, filter);
  const count = (s: string) => (data || []).filter((a) => a.status === s).length;
  return (
    <div>
      <PageHeader
        title="承保风控"
        description="承保风控智能体：多模态材料要素抽取与交叉核验，五路反欺诈模型融合，风险评分卡与决策建议；最终核保决定由具备权限的核保人员作出。"
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus className="size-4" /> 新建投保
          </Button>
        }
      />
      <NewApplication open={open} onOpenChange={setOpen} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="待决定" value={count("pending_decision")} tone="warning" />
        <StatCard label="核保中" value={count("underwriting") + count("submitted")} tone="primary" />
        <StatCard label="草稿" value={count("draft")} />
        <StatCard label="已承保" value={count("accepted")} tone="success" />
        <StatCard label="已拒保" value={count("rejected")} tone="danger" />
      </div>
      <div className="mb-3 flex gap-2">
        {[
          ["", "全部"],
          ["pending_decision", "待决定"],
          ["underwriting", "核保中"],
          ["draft", "草稿"],
          ["accepted", "已承保"],
        ].map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)} className={cn("rounded-full border px-3 py-1 text-xs", filter === k ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
            {l}
          </button>
        ))}
      </div>
      <Section contentClassName="p-0">
        {error ? (
          <div className="p-4">
            <ErrorBlock error={error} />
          </div>
        ) : !data ? (
          <div className="p-4">
            <LoadingBlock />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-4">
            <EmptyState title="暂无投保单" icon={ShieldCheck} />
          </div>
        ) : (
          <>
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[14%]">投保单号</TableHead>
                <TableHead className="w-[18%]">企业</TableHead>
                <TableHead className="w-[20%]">产品</TableHead>
                <TableHead className="w-[12%] text-right">保险金额</TableHead>
                <TableHead className="w-[10%]">状态</TableHead>
                <TableHead className="w-[10%]">核保运行</TableHead>
                <TableHead className="w-[8%]">决定</TableHead>
                <TableHead className="w-[8%]">创建时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    <Link href={`/underwriting/${a.id}`} className="font-mono text-xs text-primary hover:underline">
                      {a.app_no}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <div className="font-medium">{a.enterprise}</div>
                    <div className="text-xs text-muted-foreground">{a.track}</div>
                  </TableCell>
                  <TableCell className="max-w-56 truncate">{a.product}</TableCell>
                  <TableCell className="text-right tabular">{amount(a.sum_insured)}</TableCell>
                  <TableCell>
                    <Pill tone={STATUS_TONE[a.status]}>{a.status_label}</Pill>
                  </TableCell>
                  <TableCell>{a.uw_run_id ? <Link href={`/runs/${a.uw_run_id}`}><RunStatus status={a.uw_run_status} /></Link> : <span className="text-xs text-muted-foreground">未发起</span>}</TableCell>
                  <TableCell className="text-xs">{a.decision?.decision ? DECISION[a.decision.decision] : "—"}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{dateTime(a.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <PaginationBar className="border-t px-4 py-3" page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
          </>
        )}
      </Section>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock />}>
      <Queue />
    </Suspense>
  );
}
