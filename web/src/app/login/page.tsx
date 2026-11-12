"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, Loader2, Radar, Scale, ShieldAlert, ShieldCheck, Sparkles, PenTool, LifeBuoy } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

const DEMO = [
  { username: "designer", name: "张明远", role: "产品设计人员", desc: "设计项目、风险评估、条款设计、精算定价" },
  { username: "service", name: "李若彤", role: "产品服务人员", desc: "承保风控、风险预警、事故响应、定损理赔" },
  { username: "senior", name: "周景行", role: "产品服务人员（二级审批）", desc: "核保决定与赔付审批" },
  { username: "admin", name: "王立衡", role: "系统管理员", desc: "全部功能、模型路由、隐私与审计" },
];

const PRODUCT_NAME = "科保";

const TOOL_GROUPS = [
  {
    label: "产品设计工具",
    items: [
      { icon: Radar, name: "风险评估" },
      { icon: PenTool, name: "条款设计" },
      { icon: Scale, name: "精算定价" },
    ],
  },
  {
    label: "产品服务工具",
    items: [
      { icon: ShieldCheck, name: "承保风控" },
      { icon: ShieldAlert, name: "风险预警" },
      { icon: LifeBuoy, name: "定损理赔" },
    ],
  },
];

export default function LoginPage() {
  const { login, user } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [showDemo, setShowDemo] = useState(false);

  useEffect(() => {
    if (user) router.replace("/workbench");
  }, [user, router]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/options")
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (cancelled || !body?.show_demo_accounts) return;
        setShowDemo(true);
        setUsername((current) => current || "designer");
        setPassword((current) => current || "demo1234");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async (u = username, p = password) => {
    setBusy(true);
    try {
      const me = await login(u, p);
      toast.success(`欢迎回来，${me.display_name}`);
      router.replace("/workbench");
    } catch (e: any) {
      toast.error(e.message || "登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden overflow-hidden bg-[radial-gradient(ellipse_at_top_left,oklch(0.35_0.12_265),oklch(0.18_0.05_265))] p-10 text-white lg:sticky lg:top-0 lg:flex lg:h-screen lg:flex-col">
        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(255,255,255,.04)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,.04)_1px,transparent_1px)] bg-[size:36px_36px]" />
        <div className="relative flex items-center gap-2.5">
          <div className="flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-sky-400 to-indigo-500">
            <Sparkles className="size-5" />
          </div>
          <div className="text-lg font-semibold tracking-wide">{PRODUCT_NAME}</div>
        </div>
        <div className="relative flex flex-1 items-center">
          <div className="grid w-full max-w-2xl grid-cols-2">
            {TOOL_GROUPS.map((group, index) => (
              <div key={group.label} className={index === 0 ? "pr-12" : "border-l border-white/15 pl-12"}>
                <div className="text-sm font-medium tracking-[0.16em] text-sky-200">{group.label}</div>
                <div className="mt-8 space-y-7">
                  {group.items.map((item) => (
                    <div key={item.name} className="flex items-center gap-3">
                      <item.icon className="size-6 shrink-0 text-sky-300" />
                      <div className="text-[1.65rem] leading-none font-semibold tracking-tight">{item.name}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          <div className="mb-6 lg:hidden">
            <div className="text-lg font-semibold">{PRODUCT_NAME}</div>
          </div>
          <h2 className="text-2xl font-semibold tracking-tight">登录</h2>
          {showDemo ? <p className="mt-1 text-sm text-muted-foreground">选择演示角色快速进入，或使用账号密码登录（演示密码 demo1234）。</p> : null}

          {showDemo ? <div className="mt-6 grid gap-2">
            {DEMO.map((d) => (
              <button
                key={d.username}
                disabled={busy}
                onClick={() => {
                  setUsername(d.username);
                  submit(d.username, "demo1234");
                }}
                className={cn("group flex items-center gap-3 rounded-xl border bg-card p-3 text-left transition-all hover:border-primary/40 hover:shadow-sm disabled:opacity-60")}
              >
                <div className="flex size-9 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-sky-500 text-sm font-semibold text-white">{d.name.slice(0, 1)}</div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {d.name} <span className="text-xs font-normal text-muted-foreground">· {d.role}</span>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{d.desc}</div>
                </div>
                <ArrowRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
              </button>
            ))}
          </div> : null}

          {showDemo ? <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
            <div className="h-px flex-1 bg-border" /> 账号登录 <div className="h-px flex-1 bg-border" />
          </div> : <div className="mt-6" />}

          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="u">用户名</Label>
              <Input id="u" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p">密码</Label>
              <Input id="p" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            </div>
            <Button type="submit" className="w-full" size="lg" disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />} 登录
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
