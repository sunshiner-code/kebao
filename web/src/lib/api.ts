"use client";

import useSWR, { type SWRConfiguration } from "swr";

const TOKEN_KEY = "techins.token";

export class ApiError extends Error {
  status: number;
  category: string;
  suggestion?: string;
  fields?: { field: string; message: string }[];
  object?: { id?: number; name?: string };
  constructor(status: number, body: { category?: string; message?: string; suggestion?: string; fields?: { field: string; message: string }[]; object?: { id?: number; name?: string } }) {
    super(body.message || `请求失败（${status}）`);
    this.status = status;
    this.category = body.category || "系统错误";
    this.suggestion = body.suggestion;
    this.fields = body.fields;
    this.object = body.object;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`/api${path}`, { ...init, headers, body });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/login")) {
    setToken(null);
    window.dispatchEvent(new Event("techins:unauthorized"));
  }
  if (!res.ok) {
    let payload: any = {};
    try {
      payload = await res.json();
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(res.status, payload.error || { message: res.statusText });
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export function fileUrl(path: string): string {
  const token = getToken();
  return `/api${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token || "")}`;
}

export function useApi<T = any>(path: string | null, config?: SWRConfiguration) {
  return useSWR<T>(path, (p: string) => api<T>(p), { revalidateOnFocus: false, ...config });
}
