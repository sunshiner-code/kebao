import { AuthedShell } from "@/components/authed-shell";

// 业务页面在登录后由浏览器端按用户权限拉取数据，不参与静态外壳与即时导航校验
export const instant = false;

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AuthedShell>{children}</AuthedShell>;
}
