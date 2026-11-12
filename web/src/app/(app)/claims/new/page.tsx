"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { PageHeader, Section } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export default function NewClaimPage() {
  const router = useRouter();
  const pols = useApi<any[]>("/policies");
  const [policy, setPolicy] = useState<number | "">("");
  const [occurred, setOccurred] = useState("");
  const [typeName, setTypeName] = useState("关键设备损坏");
  const [desc, setDesc] = useState("");
  const [amount, setAmount] = useState("500000");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!policy || !desc.trim()) return toast.error("请选择保单并填写事故经过");
    setBusy(true);
    try {
      const out = await api<{ claim_id: number }>(`/policies/${policy}/claims`, { method: "POST", json: { incident_type: "equipment_damage", description: `${typeName}。${desc}`, occurred_at: occurred ? new Date(occurred).toISOString() : new Date().toISOString(), claimed_amount: Number(amount), parties: [] } });
      toast.success("报案已登记");
      router.push(`/claims/${out.claim_id}`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <PageHeader crumbs={[{ label: "定损理赔", href: "/claims" }, { label: "报案登记" }]} title="报案登记" description="关联在保保单、记录出险经过和申报金额。预报案转来的案件已带数据窗口，可在案件页直接启动定损。" />
      <Section title="报案信息" className="max-w-2xl">
        <div className="space-y-3">
          <select className="h-9 w-full rounded-lg border bg-background px-2 text-sm" value={policy} onChange={(e) => setPolicy(Number(e.target.value))}>
            <option value="">选择保单</option>
            {(pols.data || []).map((p) => <option key={p.id} value={p.id}>{p.policy_no} · {p.enterprise} · {p.product}</option>)}
          </select>
          <Input placeholder="事故类型，如关键设备损坏" value={typeName} onChange={(e) => setTypeName(e.target.value)} />
          <Input type="datetime-local" value={occurred} onChange={(e) => setOccurred(e.target.value)} />
          <Textarea rows={4} placeholder="事故经过" value={desc} onChange={(e) => setDesc(e.target.value)} />
          <Input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
          <Button disabled={busy} onClick={submit}>{busy ? "提交中…" : "登记报案"}</Button>
        </div>
      </Section>
    </div>
  );
}
