"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import { Loader2, Upload } from "lucide-react";
import { getToken } from "@/lib/api";
import { Button } from "@/components/ui/button";

export function UploadButton({ ownerType, ownerId, enterpriseId, materialTypes, onUploaded, label = "上传材料" }: { ownerType: string; ownerId: number; enterpriseId: number; materialTypes: string[]; onUploaded?: () => void; label?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const [type, setType] = useState(materialTypes[0] || "其他材料");
  const [busy, setBusy] = useState(false);
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("owner_type", ownerType);
        fd.append("owner_id", String(ownerId));
        fd.append("enterprise_id", String(enterpriseId));
        fd.append("material_type", type);
        const res = await fetch("/api/materials", { method: "POST", body: fd, headers: { Authorization: `Bearer ${getToken()}` } });
        const out = await res.json();
        if (!res.ok) throw new Error(out.error?.message || "上传失败");
        toast.success(`${out.name} 已上传，识别数据级别 L${out.data_level}`);
      }
      onUploaded?.();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = "";
    }
  };
  return (
    <div className="flex items-center gap-2">
      <select value={type} onChange={(e) => setType(e.target.value)} className="h-7 max-w-44 rounded-md border bg-background px-2 text-xs">
        {materialTypes.map((t) => (
          <option key={t}>{t}</option>
        ))}
      </select>
      <input ref={ref} type="file" multiple className="hidden" accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.csv" onChange={(e) => upload(e.target.files)} />
      <Button size="sm" variant="outline" onClick={() => ref.current?.click()} disabled={busy}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />} {label}
      </Button>
    </div>
  );
}
