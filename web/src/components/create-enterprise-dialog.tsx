"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type CreatedEnterprise = { id: number; name: string; existed: boolean };

export function CreateEnterpriseDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: (enterprise: CreatedEnterprise) => void }) {
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [track, setTrack] = useState("");
  const [tech, setTech] = useState("");
  const [region, setRegion] = useState("");
  const [busy, setBusy] = useState(false);

  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setName("");
      setCode("");
      setTrack("");
      setTech("");
      setRegion("");
    }
  }

  const submit = async () => {
    setBusy(true);
    try {
      const out = await api<{ id: number; name: string }>("/enterprises", { method: "POST", json: { name, credit_code: code, track, tech_field: tech, region } });
      toast.success("企业客户已创建");
      onOpenChange(false);
      onCreated({ id: out.id, name: out.name, existed: false });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.object?.id) {
        toast.info("该统一社会信用代码已建档，已选中已有客户");
        onOpenChange(false);
        onCreated({ id: err.object.id, name: err.object.name || "", existed: true });
        return;
      }
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新建企业客户</DialogTitle>
          <DialogDescription>以统一社会信用代码识别客户。代码已存在时将直接使用已有档案，不会重复建档。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label>企业名称</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="营业执照上的名称" />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>统一社会信用代码</Label>
            <Input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="18 位数字或大写字母" maxLength={18} className="font-mono" />
          </div>
          <div className="space-y-1.5">
            <Label>赛道</Label>
            <Input value={track} onChange={(e) => setTrack(e.target.value)} placeholder="可选" />
          </div>
          <div className="space-y-1.5">
            <Label>技术领域</Label>
            <Input value={tech} onChange={(e) => setTech(e.target.value)} placeholder="可选" />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>地区</Label>
            <Input value={region} onChange={(e) => setRegion(e.target.value)} placeholder="可选" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy && <Loader2 className="size-3.5 animate-spin" />} 保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
