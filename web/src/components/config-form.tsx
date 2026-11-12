"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";

export type Field = {
  key: string;
  label: string;
  type: string;
  help?: string;
  min?: number;
  max?: number;
  integer?: boolean;
  readonly?: boolean;
  options?: { value: string; label: string }[];
  fields?: Field[];
  item?: Field;
  value?: Field;
  key_options?: { value: string; label: string }[];
};

const selectCls = "h-8 w-full rounded-lg border bg-background px-2 text-sm disabled:opacity-60";

function num(v: unknown) {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

export function ConfigFields({ fields, value, onChange }: { fields: Field[]; value: any; onChange: (v: any) => void }) {
  const data = value && typeof value === "object" ? value : {};
  const set = (key: string, v: any) => onChange({ ...data, [key]: v });
  return (
    <div className="space-y-4">
      {fields.map((f) => (
        <div key={f.key || f.label} className="space-y-1.5">
          {f.type !== "boolean" && f.label && <div className="text-sm font-medium">{f.label}</div>}
          <FieldEditor field={f} value={f.key ? data[f.key] : data} onChange={(v) => (f.key ? set(f.key, v) : onChange(v))} />
          {f.help && <div className="text-xs text-muted-foreground">{f.help}</div>}
        </div>
      ))}
    </div>
  );
}

function FieldEditor({ field, value, onChange }: { field: Field; value: any; onChange: (v: any) => void }) {
  if (field.type === "boolean") {
    return (
      <label className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
        <span className="text-sm">{field.label}</span>
        <Switch checked={!!value} disabled={field.readonly} onCheckedChange={(v) => onChange(!!v)} />
      </label>
    );
  }
  if (field.type === "number" || field.type === "percent") {
    const shown = field.type === "percent" ? (value ?? 0) * 100 : (value ?? 0);
    return (
      <div className="flex items-center gap-2">
        <Input
          type="number"
          className="h-8 max-w-48"
          disabled={field.readonly}
          value={Number.isFinite(Number(shown)) ? shown : 0}
          onChange={(e) => {
            let n = Number(e.target.value);
            if (field.integer) n = Math.round(n);
            if (field.type === "percent") n = n / 100;
            onChange(n);
          }}
        />
        {field.type === "percent" && <span className="text-xs text-muted-foreground">%</span>}
      </div>
    );
  }
  if (field.type === "text") return <Textarea disabled={field.readonly} value={value ?? ""} onChange={(e) => onChange(e.target.value)} rows={3} />;
  if (field.type === "string") return <Input className="h-8" disabled={field.readonly} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
  if (field.type === "select") {
    return (
      <select className={selectCls} disabled={field.readonly} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {(field.options || []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "multiselect") {
    const selected: string[] = Array.isArray(value) ? value : [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {(field.options || []).map((o) => {
          const on = selected.includes(o.value);
          return (
            <button key={o.value} type="button" disabled={field.readonly} onClick={() => onChange(on ? selected.filter((x) => x !== o.value) : [...selected, o.value])} className={`rounded-full border px-2.5 py-1 text-xs ${on ? "border-primary bg-primary text-primary-foreground" : "bg-card"}`}>
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }
  if (field.type === "tags") return <Tags value={Array.isArray(value) ? value : []} onChange={onChange} />;
  if (field.type === "object") return <div className="rounded-lg border p-3"><ConfigFields fields={field.fields || []} value={value || {}} onChange={onChange} /></div>;
  if (field.type === "list") return <ListField field={field} value={Array.isArray(value) ? value : []} onChange={onChange} />;
  if (field.type === "map") return <MapField field={field} value={value && typeof value === "object" ? value : {}} onChange={onChange} />;
  return <div className="text-xs text-muted-foreground">不支持的字段类型 {field.type}</div>;
}

function Tags({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState("");
  const add = () => {
    const v = text.trim();
    if (!v || value.includes(v)) return;
    onChange([...value, v]);
    setText("");
  };
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap gap-1.5">
        {value.map((t) => (
          <span key={t} className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs">
            {t}
            <button type="button" onClick={() => onChange(value.filter((x) => x !== t))} aria-label="删除">
              <Trash2 className="size-3" />
            </button>
          </span>
        ))}
        {value.length === 0 && <span className="text-xs text-muted-foreground">暂无</span>}
      </div>
      <div className="flex gap-2">
        <Input className="h-8" value={text} placeholder="输入后回车添加" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())} />
        <Button type="button" size="sm" variant="outline" onClick={add}>
          添加
        </Button>
      </div>
    </div>
  );
}

function ListField({ field, value, onChange }: { field: Field; value: any[]; onChange: (v: any[]) => void }) {
  const item = field.item || { key: "", label: "", type: "string" };
  const blank = item.type === "object" ? Object.fromEntries((item.fields || []).map((f) => [f.key, f.type === "boolean" ? false : f.type === "number" || f.type === "percent" ? 0 : f.type === "tags" || f.type === "multiselect" ? [] : ""])) : "";
  return (
    <div className="space-y-2">
      {value.map((row, i) => (
        <div key={i} className="flex items-start gap-2 rounded-lg border p-2">
          <div className="min-w-0 flex-1">
            <FieldEditor field={{ ...item, label: "" }} value={row} onChange={(v) => onChange(value.map((x, j) => (j === i ? v : x)))} />
          </div>
          <Button type="button" size="icon-sm" variant="ghost" onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="删除">
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button type="button" size="sm" variant="outline" onClick={() => onChange([...value, structuredClone(blank)])}>
        <Plus className="size-3.5" /> 添加一项
      </Button>
    </div>
  );
}

function MapField({ field, value, onChange }: { field: Field; value: Record<string, any>; onChange: (v: any) => void }) {
  const keys = field.key_options?.length ? field.key_options : Object.keys(value).map((k) => ({ value: k, label: k }));
  const valField = field.value || { key: "", label: "", type: "string" };
  return (
    <div className="space-y-2">
      {keys.map((k) => (
        <div key={k.value} className="grid items-start gap-2 rounded-lg border p-2 md:grid-cols-[160px_1fr]">
          <div className="pt-1 text-sm">{k.label}</div>
          <FieldEditor field={{ ...valField, label: "" }} value={value[k.value]} onChange={(v) => onChange({ ...value, [k.value]: v })} />
        </div>
      ))}
    </div>
  );
}

export function useDraft<T>(source: T | undefined) {
  const [draft, setDraft] = useState<T | undefined>(source);
  const [prev, setPrev] = useState<T | undefined>(source);
  if (source !== prev) {
    setPrev(source);
    setDraft(source);
  }
  return [draft, setDraft] as const;
}

void num;
