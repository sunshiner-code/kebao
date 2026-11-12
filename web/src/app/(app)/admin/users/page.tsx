"use client";

import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { ErrorBlock, LoadingBlock, PageHeader, Pill } from "@/components/common";
import { PaginationBar, usePagedList } from "@/components/pagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Label } from "@/components/ui/label";
import { createAccountErrors } from "@/lib/account";

const ROLES = [
  ["designer", "产品设计人员"],
  ["service", "产品服务人员"],
  ["admin", "管理人员"],
];
const SCOPES = [
  ["self", "本人"],
  ["customers", "所辖客户"],
  ["department", "本部门"],
  ["org", "本机构"],
  ["all", "全部"],
];
const KIND_ORDER = ["menu", "op", "agent", "tool", "model", "privacy", "admin"];
const KIND_LABEL: Record<string, string> = {
  menu: "菜单",
  op: "操作",
  agent: "智能体",
  tool: "工具",
  model: "模型",
  privacy: "隐私",
  admin: "管理",
};

const EMPTY_FORM = { username: "", display_name: "", password: "", password_confirm: "", role: "service", positions: [] as string[], org_id: "", approval_level: 1, data_scope: "department" };

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="mt-1 text-xs text-destructive">{message}</p>;
}

function permissionGroups(pack: any, catalog: any[]) {
  const groups = new Map<string, string[]>();
  for (const code of pack.permissions || []) {
    const meta = catalog.find((item) => item.code === code);
    const kind = meta?.kind && KIND_LABEL[meta.kind] ? meta.kind : "other";
    const name = meta?.name || code;
    groups.set(kind, [...(groups.get(kind) || []), name]);
  }
  const ordered = KIND_ORDER.filter((kind) => groups.has(kind)).map((kind) => ({ kind, label: KIND_LABEL[kind], names: groups.get(kind) || [] }));
  if (groups.has("other")) ordered.push({ kind: "other", label: "其他", names: groups.get("other") || [] });
  return ordered;
}

export default function UsersPage() {
  const users = useApi<any[]>("/admin/users");
  const orgs = useApi<any[]>("/admin/orgs");
  const packs = useApi<any>("/admin/permission-packs");
  const page = usePagedList(users.data || [], "");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [handoverUser, setHandoverUser] = useState<any>(null);
  const [handoverTo, setHandoverTo] = useState("");
  const reload = () => {
    users.mutate();
    orgs.mutate();
  };
  const openCreate = () => {
    setForm(EMPTY_FORM);
    setErrors({});
    setOpen(true);
  };
  const save = async () => {
    const next = createAccountErrors(form);
    setErrors(next);
    if (Object.keys(next).length) return;
    try {
      await api("/admin/users", { method: "POST", json: { ...form, username: form.username.trim(), display_name: form.display_name.trim(), org_id: form.org_id ? Number(form.org_id) : null, approval_level: Number(form.approval_level) } });
      toast.success("账号已创建");
      setOpen(false);
      setForm(EMPTY_FORM);
      setErrors({});
      reload();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const confirmHandover = async () => {
    if (!handoverUser) return;
    if (!handoverTo) {
      toast.error("请选择交接人，例如周景行（senior）");
      return;
    }
    try {
      await api(`/admin/users/${handoverUser.id}`, { method: "PATCH", json: { enabled: false, handover_to: Number(handoverTo) } });
      toast.success("账号已停用，未完成工作已交接");
      setHandoverUser(null);
      setHandoverTo("");
      reload();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const patch = async (id: number, body: any) => {
    try {
      await api(`/admin/users/${id}`, { method: "PATCH", json: body });
      toast.success("已更新");
      reload();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  if (users.error) return <ErrorBlock error={users.error} />;
  const catalog = packs.data?.permissions || [];
  const rolePacks = (role: string) => (packs.data?.packs || []).filter((p: any) => p.base_role === role || role === "admin");
  return (
    <div>
      <PageHeader title="用户与权限" description="账号创建、启停与交接、角色和岗位包、审批等级与数据范围。停用账号时其进行中的运行继续完成，未处理的待办转交给交接人。" actions={<Button onClick={openCreate}>新建账号</Button>} />
      {!users.data ? (
        <LoadingBlock />
      ) : users.data.length === 0 ? (
        <div className="text-sm text-muted-foreground">还没有账号</div>
      ) : (
        <div className="space-y-3">
          {page.items.map((u) => (
            <section key={u.id} className="rounded-xl border bg-card p-4">
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6 xl:items-end">
                <div className="min-w-0">
                  <div className="text-xs text-muted-foreground">账号</div>
                  <div className="truncate font-medium">{u.display_name}</div>
                  <div className="truncate text-xs text-muted-foreground">{u.username}</div>
                </div>
                <label className="text-xs text-muted-foreground">
                  基础角色
                  <select className="mt-1 h-8 w-full rounded-lg border bg-background px-2 text-xs text-foreground" value={u.role} onChange={(e) => e.target.value !== u.role && window.confirm(`确认把 ${u.display_name} 的基础角色改为「${ROLES.find((r) => r[0] === e.target.value)?.[1]}」？该操作会记入审计。`) && patch(u.id, { role: e.target.value, positions: [], confirm_role_change: true })}>
                    {ROLES.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-muted-foreground">
                  机构
                  <select className="mt-1 h-8 w-full rounded-lg border bg-background px-2 text-xs text-foreground" value={u.org_id || ""} onChange={(e) => patch(u.id, { org_id: Number(e.target.value) })}>
                    <option value="">未分配</option>
                    {(orgs.data || []).map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-muted-foreground">
                  审批等级
                  <select className="mt-1 h-8 w-full rounded-lg border bg-background px-2 text-xs text-foreground" value={u.approval_level} onChange={(e) => patch(u.id, { approval_level: Number(e.target.value) })}>
                    {[1, 2, 3].map((n) => (
                      <option key={n} value={n}>
                        {n} 级
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-muted-foreground">
                  数据范围
                  <select className="mt-1 h-8 w-full rounded-lg border bg-background px-2 text-xs text-foreground" value={u.data_scope} onChange={(e) => patch(u.id, { data_scope: e.target.value })}>
                    {SCOPES.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>
                <div>
                  <div className="mb-1">{u.enabled ? <Pill tone="green">启用</Pill> : <Pill tone="red">停用</Pill>}</div>
                  {u.enabled ? (
                    <Button size="sm" variant="outline" onClick={() => { setHandoverUser(u); setHandoverTo(""); }}>
                      停用并交接
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => patch(u.id, { enabled: true })}>
                      启用
                    </Button>
                  )}
                </div>
              </div>
              <div className="mt-4 border-t pt-3">
                <div className="mb-2 text-xs text-muted-foreground">岗位</div>
                <div className="flex flex-wrap gap-1.5">
                  {rolePacks(u.role).map((p: any) => {
                    const on = (u.positions || []).includes(p.name);
                    const groups = permissionGroups(p, catalog);
                    return (
                      <HoverCard key={p.name}>
                        <HoverCardTrigger
                          delay={0}
                          render={
                            <button
                              type="button"
                              className={`rounded-full border px-2.5 py-1 text-xs ${on ? "border-primary bg-primary/10 text-primary" : "bg-background"}`}
                              onClick={() => patch(u.id, { positions: on ? u.positions.filter((x: string) => x !== p.name) : [...u.positions, p.name] })}
                            >
                              {p.name}
                            </button>
                          }
                        />
                        <HoverCardContent className="w-96" align="start">
                          <div className="text-sm font-medium">{p.name}</div>
                          <div className="mt-1 text-xs text-muted-foreground">{p.description?.trim() ? p.description : "暂无职责说明"}</div>
                          <div className="mt-2 space-y-1.5">
                            {groups.map((group) => (
                              <div key={group.kind}>
                                <div className="text-[11px] text-muted-foreground">{group.label}</div>
                                <div className="text-xs">{group.names.join("、")}</div>
                              </div>
                            ))}
                          </div>
                        </HoverCardContent>
                      </HoverCard>
                    );
                  })}
                </div>
              </div>
            </section>
          ))}
          <PaginationBar page={page.page} pages={page.pages} total={page.total} onPage={page.setPage} />
        </div>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>新建账号</DialogTitle>
            <DialogDescription>带 * 的为必填。用户名示例：zhangsan。初始密码示例：Demo1234。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="new-username">用户名 *</Label>
              <Input id="new-username" className="mt-1" autoComplete="off" aria-invalid={!!errors.username} placeholder="例如 zhangsan" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
              <FieldError message={errors.username} />
            </div>
            <div>
              <Label htmlFor="new-name">姓名 *</Label>
              <Input id="new-name" className="mt-1" aria-invalid={!!errors.display_name} placeholder="例如 张三" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
              <FieldError message={errors.display_name} />
            </div>
            <div>
              <Label htmlFor="new-password">初始密码 *</Label>
              <Input id="new-password" className="mt-1" type="password" autoComplete="new-password" aria-invalid={!!errors.password} placeholder="例如 Demo1234" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
              <FieldError message={errors.password} />
            </div>
            <div>
              <Label htmlFor="new-password-confirm">确认密码 *</Label>
              <Input id="new-password-confirm" className="mt-1" type="password" autoComplete="new-password" aria-invalid={!!errors.password_confirm} placeholder="再输入一次 Demo1234" value={form.password_confirm} onChange={(e) => setForm({ ...form, password_confirm: e.target.value })} />
              <FieldError message={errors.password_confirm} />
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="new-role">基础角色 *</Label>
              <select id="new-role" className="mt-1 h-8 w-full rounded-lg border bg-background px-2 text-sm" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value, positions: [] })}>
                {ROLES.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
              <FieldError message={errors.role} />
            </div>
          </div>
          <div className="text-xs text-muted-foreground">密码不少于 8 位，且同时包含字母与数字，不能与用户名相同。岗位包可在创建后分配，保存时会做职责分离检查。</div>
          <DialogFooter>
            <Button onClick={save}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <HandoverDialog user={handoverUser} users={users.data || []} value={handoverTo} onChange={setHandoverTo} onClose={() => setHandoverUser(null)} onConfirm={confirmHandover} />
    </div>
  );
}

function HandoverDialog({ user, users, value, onChange, onClose, onConfirm }: { user: any; users: any[]; value: string; onChange: (v: string) => void; onClose: () => void; onConfirm: () => void }) {
  const candidates = users.filter((item) => item.enabled && item.id !== user?.id);
  const sample = candidates.find((item) => item.username === "senior") || candidates[0];
  return (
    <Dialog open={!!user} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>停用并交接{user ? ` · ${user.display_name}` : ""}</DialogTitle>
          <DialogDescription>停用后该账号不能再登录。请选择一位仍在启用的同事接收未完成工作。</DialogDescription>
        </DialogHeader>
        <div className="rounded-lg border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">
          <div className="font-medium text-foreground">这里填什么</div>
          <p className="mt-1">只填「交接人」，从下拉列表里选同事账号，不要手填数字编号，也不要写交接说明。</p>
          <p className="mt-1">例如：停用「李若彤（service）」时，交接人选择「周景行（senior）」。确认后，李若彤未处理的人审待办、在办投保单、未结理赔案件和负责客户会转给周景行；进行中的运行会继续完成。</p>
          {user && sample ? <p className="mt-1">这次停用「{user.display_name}（{user.username}）」，可以按同样方式选择「{sample.display_name}（{sample.username}）」。</p> : null}
        </div>
        <div>
          <Label htmlFor="handover-to">交接人 *</Label>
          <select id="handover-to" className="mt-1 h-8 w-full rounded-lg border bg-background px-2 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
            <option value="">{sample ? `请选择，例如 ${sample.display_name}（${sample.username}）` : "没有其他启用账号可交接"}</option>
            {candidates.map((item) => (
              <option key={item.id} value={item.id}>{item.display_name}（{item.username}）</option>
            ))}
          </select>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={!candidates.length} onClick={onConfirm}>确认停用</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
