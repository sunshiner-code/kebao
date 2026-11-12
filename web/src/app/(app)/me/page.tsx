"use client";

import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { dateTime, pct } from "@/lib/format";
import { ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { passwordIssue } from "@/lib/account";

export default function MePage() {
  const me = useApi<any>("/me");
  const catalog = useApi<any>("/me/permissions");
  const [email, setEmail] = useState("");
  const [perms, setPerms] = useState<string[]>([]);
  const [hours, setHours] = useState(2);
  const [reason, setReason] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordErrors, setPasswordErrors] = useState<Record<string, string>>({});
  if (me.error) return <ErrorBlock error={me.error} />;
  if (!me.data) return <LoadingBlock />;
  const u = me.data;
  const save = async () => {
    try {
      await api("/me", { method: "PATCH", json: { email: email || u.email, prefs: { ...(u.prefs || {}), home: "workbench" } } });
      toast.success("个人资料已保存");
      me.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const changePassword = async () => {
    const next: Record<string, string> = {};
    if (!currentPassword) next.current = "请填写当前密码";
    const issue = passwordIssue(newPassword, u.username);
    if (issue) next.next = issue === "请填写密码" ? "请填写新密码" : issue;
    else if (newPassword === currentPassword) next.next = "新密码不能与当前密码相同";
    if (!confirmPassword) next.confirm = "请再次输入新密码";
    else if (confirmPassword !== newPassword) next.confirm = "两次输入的新密码不一致";
    setPasswordErrors(next);
    if (Object.keys(next).length) return;
    try {
      await api("/me/password", { method: "POST", json: { current_password: currentPassword, new_password: newPassword, confirm_password: confirmPassword } });
      toast.success("密码已修改，下次请使用新密码登录");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPasswordErrors({});
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  const grant = async () => {
    try {
      const out = await api<any>("/me/grants", { method: "POST", json: { permissions: perms, hours, reason } });
      toast.success(`已提交审批 ${out.approval.request_no}`);
      setPerms([]);
      setReason("");
      me.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  return (
    <div>
      <PageHeader title="个人中心" description={`${u.display_name} · ${u.role_label} · ${u.org || ""}`} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="资料与通知">
          <div className="space-y-2 text-sm">
            <div>审批等级 {u.approval_level} · 数据范围 {u.effective_scope || u.data_scope}</div>
            <div>岗位：{(u.positions || []).join("、") || "—"}</div>
            <div>最近登录 {dateTime(u.last_login_at)}</div>
            <Input placeholder={u.email || "通知邮箱"} value={email} onChange={(e) => setEmail(e.target.value)} />
            <Button size="sm" onClick={save}>保存</Button>
          </div>
        </Section>
        <Section title="修改密码" description="不少于 8 位，且同时包含字母与数字。例如把当前的 demo1234 改为 Demo5678。修改成功后，下次登录使用新密码。">
          <div className="space-y-2 text-sm">
            <div>
              <Label htmlFor="current-password">当前密码</Label>
              <Input id="current-password" className="mt-1" type="password" autoComplete="current-password" aria-invalid={!!passwordErrors.current} value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
              {passwordErrors.current ? <p className="mt-1 text-xs text-destructive">{passwordErrors.current}</p> : null}
            </div>
            <div>
              <Label htmlFor="new-password">新密码</Label>
              <Input id="new-password" className="mt-1" type="password" autoComplete="new-password" placeholder="例如 Demo5678" aria-invalid={!!passwordErrors.next} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
              {passwordErrors.next ? <p className="mt-1 text-xs text-destructive">{passwordErrors.next}</p> : null}
            </div>
            <div>
              <Label htmlFor="confirm-password">确认新密码</Label>
              <Input id="confirm-password" className="mt-1" type="password" autoComplete="new-password" placeholder="再输入一次 Demo5678" aria-invalid={!!passwordErrors.confirm} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
              {passwordErrors.confirm ? <p className="mt-1 text-xs text-destructive">{passwordErrors.confirm}</p> : null}
            </div>
            <Button size="sm" onClick={changePassword}>修改密码</Button>
          </div>
        </Section>
        <Section title="我的额度（今日）">
          {(u.quota || []).map((q: any) => (
            <div key={q.scope} className="mb-2 text-sm">
              <div className="flex justify-between"><span>{q.scope} {q.name}</span><span>{q.used_cost.toFixed(2)} / {q.limit_cost} 元</span></div>
              <div className="mt-1 h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, q.ratio * 100)}%` }} /></div>
              <div className="text-xs text-muted-foreground">已用 {pct(q.ratio)}{q.exceeded ? " · 已超额" : ""}</div>
            </div>
          ))}
        </Section>
        <Section title="我的权限">
          <div className="flex flex-wrap gap-1">{(u.permissions || []).map((p: string) => <Pill key={p}>{p}</Pill>)}</div>
        </Section>
        <Section title="临时授权" description="最长时长由安全设置限制，需管理员审批，到期自动回收，期间操作会打标记审计。">
          <div className="mb-2 flex flex-wrap gap-1">
            {(catalog.data?.items || []).filter((p: any) => !(u.permissions || []).includes(p.code)).slice(0, 24).map((p: any) => (
              <button key={p.code} onClick={() => setPerms((xs) => xs.includes(p.code) ? xs.filter((x) => x !== p.code) : [...xs, p.code])} className={`rounded-full border px-2 py-0.5 text-[11px] ${perms.includes(p.code) ? "border-primary bg-primary/10" : ""}`}>{p.name}</button>
            ))}
          </div>
          <div className="flex items-center gap-2 text-sm">时长（小时）<Input className="h-8 w-20" type="number" value={hours} onChange={(e) => setHours(Number(e.target.value))} /></div>
          <Textarea className="mt-2" rows={2} placeholder="申请理由" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button className="mt-2" size="sm" disabled={!perms.length || !reason.trim()} onClick={grant}>提交审批</Button>
          <div className="mt-3 space-y-1 text-xs">
            {(u.grant_history || []).map((g: any) => (
              <div key={g.id} className="flex justify-between border-t py-1"><span>{g.status} · {(g.permissions || []).join("、")}</span><span className="text-muted-foreground">{g.expires_at ? dateTime(g.expires_at) : dateTime(g.created_at)}</span></div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}
