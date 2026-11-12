"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { ConfigFields, type Field, useDraft } from "@/components/config-form";
import { ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export default function SettingsPage() {
  const list = useApi<any[]>("/admin/settings");
  const [key, setKey] = useState<string | null>(null);
  const active = key || list.data?.[0]?.key || null;
  const detail = useApi<any>(active ? `/admin/settings/${active}` : null);
  const [draft, setDraft] = useDraft<any>(detail.data?.value);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    for (const s of list.data || []) (g[s.category] ||= []).push(s);
    return g;
  }, [list.data]);
  const save = async () => {
    if (!active || !draft) return;
    setBusy(true);
    try {
      await api(`/admin/settings/${active}`, { method: "PUT", json: { value: draft, reason } });
      toast.success("配置已生效，并写入审计");
      setReason("");
      detail.mutate();
      list.mutate();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  const rollback = async (version: number) => {
    if (!active) return;
    const why = window.prompt("回滚原因");
    if (!why) return;
    try {
      await api(`/admin/settings/${active}/rollback`, { method: "POST", json: { version, reason: why } });
      toast.success(`已回滚到 V${version}`);
      detail.mutate();
    } catch (e: any) {
      toast.error(e.message);
    }
  };
  if (list.error) return <ErrorBlock error={list.error} />;
  return (
    <div>
      <PageHeader title="系统配置" description="核保规则、预警模板、合规与隐私、审批流、参数字典、通知、报告模板、安全与额度。每项修改校验后立即生效，并保留版本以便回滚。" />
      {!list.data ? (
        <LoadingBlock />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
          <div className="space-y-3">
            {Object.entries(groups).map(([cat, items]) => (
              <div key={cat}>
                <div className="px-2 pb-1 text-xs text-muted-foreground">{cat}</div>
                {items.map((s) => (
                  <button key={s.key} onClick={() => setKey(s.key)} className={`mb-1 block w-full rounded-lg px-2.5 py-1.5 text-left text-sm ${active === s.key ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>
                    <div>{s.title}</div>
                    <div className={`text-[10px] ${active === s.key ? "text-primary-foreground/70" : "text-muted-foreground"}`}>{s.page} · V{s.version}</div>
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div>
            {!detail.data || !draft ? (
              <LoadingBlock />
            ) : (
              <Section title={detail.data.title} description={detail.data.description} actions={<Pill>V{detail.data.version}</Pill>}>
                <div className="mb-3 text-xs text-muted-foreground">
                  {detail.data.updated_by ? `${detail.data.updated_by} 于 ${dateTime(detail.data.updated_at)} 更新` : "尚未人工修改"}
                </div>
                <ConfigFields fields={detail.data.schema as Field[]} value={draft} onChange={setDraft} />
                <div className="mt-4 space-y-2">
                  <div className="text-sm font-medium">变更原因</div>
                  <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="说明为什么调整，例如：橙色预警处置时限由 4 小时调整为 2 小时" rows={2} />
                  <Button disabled={busy || !reason.trim()} onClick={save}>
                    {busy ? "保存中…" : "保存并生效"}
                  </Button>
                </div>
                {detail.data.history?.length > 0 && (
                  <div className="mt-5">
                    <div className="mb-2 text-sm font-medium">版本历史</div>
                    {detail.data.history.slice(0, 8).map((h: any) => (
                      <div key={h.version} className="flex items-center justify-between border-t py-1.5 text-xs">
                        <span>
                          V{h.version} · {h.updated_by} · {dateTime(h.updated_at)} · {h.reason}
                        </span>
                        {h.version !== detail.data.version && (
                          <button className="text-primary hover:underline" onClick={() => rollback(h.version)}>
                            回滚到此版本
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </Section>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
