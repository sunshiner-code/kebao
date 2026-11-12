"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import {
  Activity,
  Bell,
  Bot,
  BookOpen,
  Boxes,
  Briefcase,
  ClipboardCheck,
  FileSearch,
  FileText,
  Gauge,
  Inbox,
  LayoutDashboard,
  LifeBuoy,
  LogOut,
  Menu,
  PenTool,
  Radar,
  Scale,
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Siren,
  Sparkles,
  Users,
  Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth, type Role } from "@/lib/auth";
import { useApi } from "@/lib/api";
import { relative } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { RunStatus } from "@/components/common";
import { Input } from "@/components/ui/input";

type NavItem = { label: string; href: string; icon: React.ComponentType<{ className?: string }>; roles?: Role[]; match?: string[] };
type NavGroup = { label: string; roles?: Role[]; items: NavItem[] };

const NAV: NavGroup[] = [
  { label: "工作台", items: [{ label: "我的工作台", href: "/workbench", icon: LayoutDashboard }, { label: "消息与待办", href: "/inbox", icon: Inbox }, { label: "数据看板", href: "/dashboards", icon: Gauge }] },
  {
    label: "产品设计",
    roles: ["designer"],
    items: [
      { label: "设计项目", href: "/design", icon: Workflow, match: ["/design"] },
      { label: "风险评估", href: "/design/risk", icon: Radar },
      { label: "条款设计", href: "/design/clauses", icon: PenTool },
      { label: "精算定价", href: "/design/pricing", icon: Scale },
    ],
  },
  {
    label: "产品服务",
    roles: ["service"],
    items: [
      { label: "承保风控", href: "/underwriting", icon: ShieldCheck },
      { label: "风险预警", href: "/monitoring", icon: ShieldAlert, match: ["/monitoring/alerts", "/monitoring/reports"] },
      { label: "事故响应", href: "/monitoring/incidents", icon: Siren },
      { label: "定损理赔", href: "/claims", icon: LifeBuoy },
    ],
  },
  { label: "产品与保单", items: [{ label: "产品库", href: "/products", icon: Boxes }, { label: "保单与监测", href: "/policies", icon: FileText, roles: ["service"] }] },
  { label: "客户与知识", items: [{ label: "企业客户", href: "/customers", icon: Users }, { label: "知识中心", href: "/knowledge", icon: BookOpen }] },
  { label: "智能体中心", items: [{ label: "运行记录", href: "/runs", icon: Activity }] },
  { label: "系统管理", roles: ["admin"], items: [{ label: "云私计算与路由", href: "/admin", icon: Gauge }, { label: "用户与权限", href: "/admin/users", icon: Users }, { label: "系统配置", href: "/admin/settings", icon: Settings }, { label: "数据源与接入", href: "/admin/integration", icon: Workflow }, { label: "系统监控", href: "/admin/monitor", icon: Activity }, { label: "定时任务", href: "/admin/jobs", icon: Briefcase }, { label: "隐私与审计", href: "/admin/audit", icon: FileSearch }] },
];

function visible(role: Role | undefined, roles?: Role[]) {
  if (!roles) return true;
  return role === "admin" || (role ? roles.includes(role) : false);
}

function isActive(pathname: string, item: NavItem) {
  if (pathname === item.href) return true;
  const prefixes = item.match || [item.href];
  const exactOnly = ["/design", "/monitoring", "/admin"];
  return prefixes.some((p) => (exactOnly.includes(p) && !item.match ? false : pathname.startsWith(p + "/") || pathname === p));
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user } = useAuth();
  const pathname = usePathname();
  const positions = user?.positions || [];
  const hideCustomers = positions.includes("系统管理员") && !positions.includes("业务管理员");
  const designDeep = ["/design/risk", "/design/clauses", "/design/pricing"].some((p) => pathname.startsWith(p));
  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4">
        <div className="flex size-8 items-center justify-center rounded-lg bg-gradient-to-br from-sky-400 to-indigo-500 shadow-inner">
          <Sparkles className="size-4 text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-base font-semibold tracking-wide text-white">科保</div>
        </div>
      </div>
      <nav className="scrollbar-thin flex-1 overflow-y-auto px-2 py-3">
        {NAV.filter((g) => visible(user?.role, g.roles)).map((g) => (
          <div key={g.label} className="mb-3">
            <div className="px-2 pb-1 text-[11px] font-medium tracking-wide text-sidebar-foreground/45">{g.label}</div>
            {g.items
              .filter((it) => visible(user?.role, it.roles) && !(hideCustomers && it.href === "/customers"))
              .map((it) => {
                let active = isActive(pathname, it);
                if (it.href === "/design") active = pathname === "/design" || (/^\/design\/\d+/.test(pathname) && !designDeep);
                if (it.href === "/monitoring") active = pathname === "/monitoring" || pathname.startsWith("/monitoring/alerts") || pathname.startsWith("/monitoring/reports");
                if (it.href === "/admin") active = pathname === "/admin";
                const Icon = it.icon;
                return (
                  <Link key={it.href} href={it.href} onClick={onNavigate} className={cn("group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] transition-colors", active ? "bg-sidebar-primary text-sidebar-primary-foreground shadow-sm" : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-white")}>
                    <Icon className={cn("size-4", active ? "text-white" : "text-sidebar-foreground/60 group-hover:text-white")} />
                    {it.label}
                  </Link>
                );
              })}
          </div>
        ))}
      </nav>
      <div className="border-t border-sidebar-border p-3 text-[11px] text-sidebar-foreground/50">
        <div className="flex items-center gap-1.5">
          <Briefcase className="size-3" /> {user?.org} · {user?.positions?.join("、")}
        </div>
      </div>
    </div>
  );
}

function RunningIndicator() {
  const { data } = useApi<any[]>("/runs?status=queued,planning,running,waiting_human&limit=20", { refreshInterval: 5000 });
  const running = (data || []).filter((r) => r.status !== "waiting_human");
  const waiting = (data || []).filter((r) => r.status === "waiting_human");
  return (
    <Popover>
      <PopoverTrigger render={<Button variant="ghost" size="sm" className="gap-1.5" />}>
        <Activity className={cn("size-4", running.length ? "text-primary" : "text-muted-foreground")} />
        <span className="hidden text-xs sm:inline">运行中 {running.length}</span>
        {waiting.length > 0 && <span className="rounded-full bg-amber-500/15 px-1.5 text-[10px] font-semibold text-amber-700">待人工 {waiting.length}</span>}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="border-b px-3 py-2 text-sm font-medium">智能体运行</div>
        <div className="max-h-80 overflow-y-auto">
          {(data || []).length === 0 && <div className="px-3 py-6 text-center text-xs text-muted-foreground">当前没有运行中的任务</div>}
          {(data || []).map((r) => (
            <Link key={r.id} href={`/runs/${r.id}`} className="flex items-center justify-between gap-2 border-b px-3 py-2 last:border-0 hover:bg-muted/60">
              <div className="min-w-0">
                <div className="truncate text-xs font-medium">{r.title}</div>
                <div className="text-[11px] text-muted-foreground">
                  {r.agent_name} · #{r.id} · {relative(r.created_at)}
                </div>
              </div>
              <RunStatus status={r.status} />
            </Link>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Notifications() {
  const { data, mutate } = useApi<any>("/notifications", { refreshInterval: 10000 });
  const unread = data?.unread || 0;
  return (
    <Popover
      onOpenChange={(open) => {
        if (!open && unread) fetch("/api/notifications/read", { method: "POST", headers: { Authorization: `Bearer ${localStorage.getItem("techins.token")}` } }).then(() => mutate());
      }}
    >
      <PopoverTrigger render={<Button variant="ghost" size="icon-sm" className="relative" />}>
        <Bell className="size-4" />
        {unread > 0 && <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">{unread > 99 ? "99+" : unread}</span>}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-sm font-medium">消息通知</span>
          <Link href="/inbox" className="text-xs text-primary hover:underline">
            查看待办
          </Link>
        </div>
        <div className="max-h-96 overflow-y-auto">
          {(data?.items || []).length === 0 && <div className="px-3 py-6 text-center text-xs text-muted-foreground">暂无消息</div>}
          {(data?.items || []).slice(0, 30).map((n: any) => (
            <Link key={n.id} href={n.link || "#"} className={cn("block border-b px-3 py-2 last:border-0 hover:bg-muted/60", !n.read && "bg-primary/5")}>
              <div className="flex items-center gap-2">
                <span className={cn("size-1.5 rounded-full", n.level === "error" ? "bg-rose-500" : n.level === "warning" ? "bg-amber-500" : "bg-sky-500")} />
                <span className="truncate text-xs font-medium">{n.title}</span>
              </div>
              <div className="mt-0.5 line-clamp-2 pl-3.5 text-[11px] text-muted-foreground">{n.body}</div>
              <div className="pl-3.5 text-[10px] text-muted-foreground/70">{relative(n.ts)}</div>
            </Link>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function GlobalSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const go = () => {
    const v = q.trim();
    if (!v) return;
    if (/^\d+$/.test(v)) router.push(`/runs/${v}`);
    else if (/^LP/i.test(v)) router.push(`/claims?q=${encodeURIComponent(v)}`);
    else if (/^PKJ/i.test(v)) router.push(`/policies?q=${encodeURIComponent(v)}`);
    else if (/^YJ/i.test(v)) router.push(`/monitoring?q=${encodeURIComponent(v)}`);
    else router.push(`/search?q=${encodeURIComponent(v)}`);
    setQ("");
  };
  return (
    <div className="relative hidden w-72 md:block">
      <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && go()} placeholder="搜索企业、保单号、案件号、运行编号" className="h-8 bg-muted/60 pl-8 text-xs" />
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <div className="flex min-h-screen bg-background">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 lg:block">
        <Sidebar />
      </aside>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-64 p-0" showCloseButton={false}>
          <Sidebar onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur md:px-6">
          <Button variant="ghost" size="icon-sm" className="lg:hidden" onClick={() => setOpen(true)} aria-label="打开导航">
            <Menu className="size-4" />
          </Button>
          <GlobalSearch />
          <div className="ml-auto flex items-center gap-1.5">
            <RunningIndicator />
            <Link href="/inbox">
              <Button variant="ghost" size="icon-sm" aria-label="待办">
                <ClipboardCheck className="size-4" />
              </Button>
            </Link>
            <Notifications />
            <Popover>
              <PopoverTrigger render={<button className="ml-1 flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-muted" />}>
                <div className="flex size-7 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-sky-500 text-xs font-semibold text-white">{user?.display_name?.slice(0, 1)}</div>
                <div className="hidden text-left leading-tight sm:block">
                  <div className="text-xs font-medium">{user?.display_name}</div>
                  <div className="text-[10px] text-muted-foreground">{user?.role_label}</div>
                </div>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-60">
                <div className="space-y-1 text-xs">
                  <div className="font-medium">{user?.display_name}（{user?.username}）</div>
                  <div className="text-muted-foreground">{user?.role_label} · 审批等级 {user?.approval_level}</div>
                  <div className="text-muted-foreground">岗位：{user?.positions?.join("、")}</div>
                </div>
                <div className="mt-3 flex flex-col gap-1">
                  <Link href="/me" className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted">
                    <Settings className="size-3.5" /> 个人中心
                  </Link>
                  <Link href="/agents" className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-muted">
                    <Bot className="size-3.5" /> 智能体中心
                  </Link>
                  <button onClick={logout} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-rose-600 hover:bg-rose-50">
                    <LogOut className="size-3.5" /> 退出登录
                  </button>
                </div>
              </PopoverContent>
            </Popover>
          </div>
        </header>
        <main className="relative mx-auto w-full max-w-[1480px] flex-1 px-4 py-5 md:px-6 md:py-6">{children}</main>
      </div>
    </div>
  );
}
