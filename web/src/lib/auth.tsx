"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken } from "./api";

export type Role = "designer" | "service" | "admin";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: Role;
  role_label: string;
  positions: string[];
  org: string;
  approval_level: number;
  data_scope: string;
  permissions?: string[];
  org_id?: number | null;
}

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    let active = true;
    const pending = getToken() ? api<User>("/auth/me").then((u) => active && setUser(u)) : Promise.resolve();
    pending
      .catch(() => setToken(null))
      .finally(() => active && setLoading(false));
    const onUnauthorized = () => {
      setUser(null);
      router.replace("/login");
    };
    window.addEventListener("techins:unauthorized", onUnauthorized);
    return () => {
      active = false;
      window.removeEventListener("techins:unauthorized", onUnauthorized);
    };
  }, [router]);

  const login = useCallback(async (username: string, password: string) => {
    const out = await api<{ token: string; user: User }>("/auth/login", { method: "POST", json: { username, password } });
    setToken(out.token);
    setUser(out.user);
    return out.user;
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    router.replace("/login");
  }, [router]);

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function canAccess(role: Role | undefined, allowed: Role[]): boolean {
  if (!role) return false;
  return role === "admin" || allowed.includes(role);
}
