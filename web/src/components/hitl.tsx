"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Clock, ExternalLink, Loader2, UserCheck } from "lucide-react";
import { api, useApi } from "@/lib/api";
import { ArtifactBody } from "@/components/artifacts/artifact-view";
import { useAuth } from "@/lib/auth";
import { amount, dateTime, num, pct, relative, LINE_NAMES } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Pill } from "@/components/common";

const TYPE_TONE: Record<string, "amber" | "blue" | "violet" | "red" | "teal" | "sky" | "orange" | "gray"> = {
  decision: "violet",
  claim_approval: "violet",
  incident_start: "red",
  provider_dispatch: "orange",
  causal_confirm: "teal",
  field_check: "sky",
  supplement_send: "orange",
  plan_confirm: "blue",
  result_confirm: "blue",
  option_select: "teal",
  action_confirm: "orange",
};

const ORIGIN: Record<string, { label: string; tone: "gray" | "blue" | "teal" | "violet" | "amber" | "orange" | "green" }> = {
  personalized: { label: "个性化", tone: "teal" },
  extension: { label: "扩展", tone: "blue" },
  innovation: { label: "创新", tone: "violet" },
  fix: { label: "合规整改", tone: "amber" },
  consistency: { label: "衔接", tone: "orange" },
  params: { label: "参数", tone: "green" },
  revision: { label: "修订", tone: "orange" },
};

function ComplianceIssues({ issues }: { issues: any[] }) {
  if (!issues.length) return null;
  return (
    <div className="rounded-lg border">
      <div className="border-b px-3 py-2 text-xs font-medium">合规审查意见（{issues.length}）</div>
      <div className="max-h-56 space-y-2 overflow-y-auto p-3">
        {issues.map((issue, idx) => {
          const high = issue.severity === "high" || issue.severity === "critical";
          const resolved = Boolean(issue.resolved);
          return (
            <div key={idx} className={cn("rounded-md border px-2.5 py-2 text-xs", high && !resolved ? "border-rose-300 bg-rose-50/70" : "bg-muted/40")}>
              <div className="flex flex-wrap items-center gap-1.5">
                <Pill tone={resolved ? "green" : high ? "red" : issue.severity === "medium" ? "orange" : "gray"}>
                  {resolved ? "已整改" : high ? "严重" : issue.severity === "medium" ? "中等" : "一般"}
                </Pill>
                {issue.article && <span className="text-muted-foreground">{issue.article}</span>}
              </div>
              <div className="mt-1 font-medium text-sm">{issue.issue || issue.title || "合规问题"}</div>
              {issue.basis && <div className="mt-0.5 text-muted-foreground">依据：{issue.basis}</div>}
              {issue.suggestion && <div className="mt-0.5 text-sky-800">建议：{issue.suggestion}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ClauseReview({ clauses }: { clauses: any[] }) {
  const [docId, setDocId] = useState(clauses[0]?.doc_id);
  const doc = clauses.find((d) => d.doc_id === docId) || clauses[0];
  if (!doc) return null;
  const chapters = Array.isArray(doc.chapters) ? doc.chapters : [];
  const count = (d: any) => (Array.isArray(d.chapters) ? d.chapters : []).reduce((sum: number, ch: any) => sum + (ch.articles || []).length, 0);
  return (
    <div className="rounded-lg border">
      <div className="border-b px-3 py-2 text-xs font-medium">待复核条款</div>
      <div className="grid gap-3 p-3 lg:grid-cols-[220px_minmax(0,1fr)]">
        <div className="flex gap-1.5 overflow-x-auto lg:block lg:space-y-1.5 lg:overflow-visible">
          {clauses.map((d) => (
            <button key={d.doc_id} type="button" onClick={() => setDocId(d.doc_id)} className={cn("shrink-0 rounded-lg border px-3 py-2 text-left text-sm lg:w-full", d.doc_id === doc.doc_id ? "border-primary bg-primary/5" : "bg-card hover:bg-muted/50")}>
              <div className="font-medium">{d.name}</div>
              <div className="text-[11px] text-muted-foreground">
                {d.doc_id} · {count(d)} 条
                {d.innovations > 0 && " · 含创新条款"}
              </div>
            </button>
          ))}
        </div>
        <div className="max-h-96 space-y-4 overflow-y-auto pr-1">
          {chapters.length === 0 && <div className="text-xs text-muted-foreground">条款正文未附带章节内容，请打开完整产物页查看。</div>}
          {chapters.map((ch: any) => (
            <div key={ch.key || ch.title}>
              <div className="mb-1.5 text-center text-xs font-semibold">{ch.title}</div>
              <div className="space-y-2">
                {(ch.articles || []).map((a: any, i: number) => (
                  <div key={`${a.no}-${i}`} className={cn("rounded-md px-2 py-1.5 text-sm leading-relaxed", ORIGIN[a.origin] && "bg-amber-50")}>
                    <span className="font-semibold">{a.no}</span>
                    {a.title && <span className="font-medium">【{a.title}】</span>}
                    <span className={cn(a.bold && "font-semibold")}>{a.text}</span>
                    <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
                      {ORIGIN[a.origin] && <Pill tone={ORIGIN[a.origin].tone}>{ORIGIN[a.origin].label}</Pill>}
                      {(a.risk_ids || []).map((r: string) => (
                        <span key={r} className="font-mono text-[10px] text-rose-600">
                          {r}
                        </span>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConfirmArtifact({ artifactKey, version }: { artifactKey: string; version?: number }) {
  const [followLatest, setFollowLatest] = useState(false);
  const pinned = followLatest ? undefined : version;
  const { data, error } = useApi<any>(`/artifacts/${artifactKey}${pinned ? `?version=${pinned}` : ""}`);
  if (error) return <div className="text-xs text-rose-600">方案内容加载失败：{error.message}</div>;
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> 正在加载待确认内容
      </div>
    );
  }
  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="font-medium">待确认内容 · {data.title}</span>
        <Link href={`/artifacts/${artifactKey}`} className="text-primary hover:underline">
          打开完整页面
        </Link>
      </div>
      <ArtifactBody kind={data.kind} data={data.data} projectId={data.project_id} onChanged={() => setFollowLatest(true)} />
    </div>
  );
}

function RecommendationList({ items }: { items: any[] }) {
  const rich = items.some((it) => it.risks || it.rationale);
  if (!rich) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {items.map((it: any) => (
          <Pill key={it.line || it.name} tone="blue">
            {it.name || LINE_NAMES[it.line]}
          </Pill>
        ))}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {items.map((it: any) => (
        <div key={it.line || it.name} className="rounded-lg border p-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{it.name || LINE_NAMES[it.line]}</span>
            {it.role && <Pill>{it.role}</Pill>}
            {it.premium_estimate != null && <span className="text-muted-foreground">预估保费 {amount(it.premium_estimate)}</span>}
          </div>
          {it.rationale && <div className="mt-1 text-muted-foreground">{it.rationale}</div>}
          {Array.isArray(it.risks) && it.risks.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {it.risks.map((r: any) => (
                <Pill key={r.id} tone={r.level === "极高" || r.level === "高" ? "orange" : "gray"}>
                  {r.title}
                </Pill>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function FactorTable({ components }: { components: any[] }) {
  return (
    <div className="space-y-3">
      {components.map((c) => (
        <div key={c.id || c.name}>
          <div className="mb-1 text-xs font-medium">{c.name}</div>
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[640px] text-xs">
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="px-2 py-1.5">因子</th>
                  <th className="text-right">信息价值</th>
                  <th className="text-right">共线性</th>
                  <th className="text-right">稳定性</th>
                  <th>建议</th>
                  <th>本次入模</th>
                </tr>
              </thead>
              <tbody>
                {(c.screening || []).map((s: any) => {
                  const on = (c.selected || []).includes(s.key);
                  return (
                    <tr key={s.key} className="border-b last:border-0">
                      <td className="px-2 py-1.5">
                        <div className="font-medium">{s.name}</div>
                        {s.reason && <div className="text-[11px] text-muted-foreground">{s.reason}</div>}
                      </td>
                      <td className="text-right tabular">{s.iv == null ? "—" : num(s.iv, 3)}</td>
                      <td className="text-right tabular">{s.vif == null ? "—" : num(s.vif, 1)}</td>
                      <td className="text-right tabular">{s.psi == null ? "—" : num(s.psi, 3)}</td>
                      <td>{s.recommend ? <Pill tone="green">建议入模</Pill> : <Pill>不建议</Pill>}</td>
                      <td>{on ? <Pill tone="blue">入模</Pill> : <span className="text-muted-foreground">未入模</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function ExperimentView({ experiments }: { experiments: any[] }) {
  return (
    <div className="space-y-3">
      {experiments.map((e) => (
        <div key={`${e.component}-${e.round}`}>
          <div className="mb-1 text-xs font-medium">
            {e.name} · 第 {e.round} 轮{e.winner ? ` · 胜出 ${e.winner}` : " · 尚无胜出变体"}
          </div>
          {e.insight?.conclusion && <div className="mb-1.5 text-xs text-muted-foreground">{e.insight.conclusion}</div>}
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full min-w-[640px] text-xs">
              <thead className="border-b text-left text-muted-foreground">
                <tr>
                  <th className="px-2 py-1.5">变体</th>
                  <th className="text-right">投保率</th>
                  <th className="text-right">赔付率</th>
                  <th className="text-right">亏损概率</th>
                  <th className="text-right">期望利润</th>
                  <th>约束</th>
                </tr>
              </thead>
              <tbody>
                {(e.variants || []).map((v: any) => (
                  <tr key={v.key} className="border-b last:border-0">
                    <td className="px-2 py-1.5 font-medium">
                      {v.label}
                      {e.winner === v.key && <Pill tone="green" className="ml-1">胜出</Pill>}
                    </td>
                    <td className="text-right tabular">{pct(v.metrics?.take_rate, 1)}</td>
                    <td className="text-right tabular">{pct(v.metrics?.loss_ratio, 1)}</td>
                    <td className="text-right tabular">{pct(v.metrics?.loss_probability, 1)}</td>
                    <td className="text-right tabular">{amount(v.metrics?.expected_profit)}</td>
                    <td className="text-[11px] text-muted-foreground">{Object.entries(v.constraints || {}).filter(([, ok]) => !ok).map(([k]) => k).join("、") || "全部满足"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function ContextView({ card }: { card: any }) {
  const ctx = card.context || {};
  return (
    <div className="space-y-2">
      {Array.isArray(ctx.compliance_issues) && ctx.compliance_issues.length > 0 && <ComplianceIssues issues={ctx.compliance_issues} />}
      {Array.isArray(ctx.clauses) && ctx.clauses.length > 0 && <ClauseReview clauses={ctx.clauses} />}
      {Array.isArray(ctx.items) && ctx.items.length > 0 && <RecommendationList items={ctx.items} />}
      {ctx.providers && (
        <ul className="space-y-1 text-xs">
          {ctx.providers.map((p: any) => (
            <li key={p.id} className="flex items-center justify-between rounded-md bg-muted/60 px-2 py-1">
              <span>
                {p.name} <span className="text-muted-foreground">· {p.category}</span>
              </span>
              <span className="text-muted-foreground">{p.response_hours} 小时响应</span>
            </li>
          ))}
        </ul>
      )}
      {ctx.missing && (
        <div className="flex flex-wrap gap-1.5">
          {ctx.missing.map((m: string) => (
            <Pill key={m} tone="orange">
              {m}
            </Pill>
          ))}
        </div>
      )}
      {ctx.todos && (
        <ol className="max-h-40 space-y-0.5 overflow-y-auto text-xs text-muted-foreground">
          {ctx.todos.map((t: any, i: number) => (
            <li key={t.id}>
              {i + 1}. {t.content}
              {t.tool && <span className="text-muted-foreground/70"> · {t.tool}</span>}
            </li>
          ))}
        </ol>
      )}
      {Array.isArray(ctx.uncovered) && ctx.uncovered.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium">未覆盖风险</div>
          <div className="space-y-1">
            {ctx.uncovered.map((u: any) => (
              <div key={u.risk_id || u.title} className="flex flex-wrap items-center gap-2 text-xs">
                <Pill tone="orange">{u.level || "未覆盖"}</Pill>
                <span>{u.title || u.name}</span>
                {u.reason && <span className="text-muted-foreground">{u.reason}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
      {Array.isArray(ctx.components) && ctx.components.length > 0 && <FactorTable components={ctx.components} />}
      {Array.isArray(ctx.experiments) && ctx.experiments.length > 0 && <ExperimentView experiments={ctx.experiments} />}
      {Array.isArray(ctx.changes) && ctx.changes.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs font-medium">拟修订内容</div>
          {ctx.changes.map((c: any, i: number) => (
            <div key={i} className="rounded-md bg-muted/60 px-2 py-1.5 text-xs">
              <span className="font-medium">{LINE_NAMES[c.line] || c.line || c.doc_id}</span>
              {c.type === "deductible" && <span> 免赔额调整为 {amount(c.value)}</span>}
              {c.reason && <span className="text-muted-foreground"> · {c.reason}</span>}
            </div>
          ))}
        </div>
      )}
      {ctx.preview && (
        <div className="space-y-2 rounded-lg border p-3 text-xs">
          <div className="font-medium">预报案草稿 · {ctx.preview.incident_type_name}</div>
          {ctx.preview.description && <p className="leading-relaxed text-muted-foreground">{ctx.preview.description}</p>}
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>估损 {amount(ctx.preview.estimated_loss)}{Array.isArray(ctx.preview.loss_range) ? `（${amount(ctx.preview.loss_range[0])}–${amount(ctx.preview.loss_range[1])}）` : ""}</span>
            <span>责任 {(ctx.preview.liabilities || []).join("、") || "—"}</span>
          </div>
          {Array.isArray(ctx.preview.parties) && ctx.preview.parties.length > 0 && <div>参与方：{ctx.preview.parties.map((p: any) => `${p.name}（${p.role}）`).join("、")}</div>}
          {Array.isArray(ctx.preview.handling) && ctx.preview.handling.length > 0 && (
            <ol className="space-y-0.5 text-muted-foreground">
              {ctx.preview.handling.map((h: any, i: number) => (
                <li key={i}>{i + 1}. {h.step}{h.status === "done" ? "（已完成）" : ""}</li>
              ))}
            </ol>
          )}
          {Array.isArray(ctx.preview.providers) && ctx.preview.providers.length > 0 && <div>已通知服务商：{ctx.preview.providers.join("、")}</div>}
          {ctx.preview.data_window?.artifact_key && (
            <Link href={`/artifacts/${ctx.preview.data_window.artifact_key}`} className="text-primary hover:underline">
              事故数据窗口 {ctx.preview.data_window.series ?? 0} 条序列、{ctx.preview.data_window.points ?? 0} 个数据点
            </Link>
          )}
        </div>
      )}
      {ctx.analysis && (
        <div className="space-y-2 rounded-lg border p-3 text-xs">
          <div className="font-medium">研判依据</div>
          {ctx.analysis.explanation && <p className="leading-relaxed">{ctx.analysis.explanation}</p>}
          {Array.isArray(ctx.analysis.causes) && ctx.analysis.causes.length > 0 && (
            <ul className="space-y-0.5 text-muted-foreground">
              {ctx.analysis.causes.map((c: any, i: number) => (
                <li key={i}>· {c.text}</li>
              ))}
            </ul>
          )}
          {Array.isArray(ctx.analysis.actions) && ctx.analysis.actions.length > 0 && (
            <ul className="space-y-0.5">
              {ctx.analysis.actions.map((a: any, i: number) => (
                <li key={i}>建议：{a.action}{a.owner ? ` · ${a.owner}` : ""}{a.deadline_hours ? ` · ${a.deadline_hours} 小时内` : ""}</li>
              ))}
            </ul>
          )}
          <div className="text-muted-foreground">
            {Array.isArray(ctx.analysis.loss_range) && <span>潜在损失 {amount(ctx.analysis.loss_range[0])}–{amount(ctx.analysis.loss_range[1])} · </span>}
            {ctx.analysis.relation && <span>与保单责任：{ctx.analysis.relation}</span>}
            {Array.isArray(ctx.analysis.liabilities) && ctx.analysis.liabilities.length > 0 && <span> · {(ctx.analysis.liabilities || []).join("、")}</span>}
          </div>
        </div>
      )}
      {card.card_type === "field_check" && Array.isArray(ctx.docs) && (
        <div className="space-y-2">
          {ctx.docs.map((d: any) => (
            <div key={d.kind} className="rounded-lg border p-2 text-xs">
              <div className="mb-1 font-medium">{d.name} <span className="font-normal text-muted-foreground">置信度 {d.confidence}</span></div>
              <div className="grid gap-1 sm:grid-cols-2">
                {Object.entries(d.fields || {}).slice(0, 8).map(([k, v]) => (
                  <div key={k} className="flex gap-2">
                    <span className="w-24 shrink-0 truncate text-muted-foreground">{k}</span>
                    <span className="min-w-0 break-all">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {card.card_type === "causal_confirm" && ctx.graph && (
        <div className="space-y-1 text-xs">
          {(ctx.graph.edges || []).map((e: any) => {
            const labels: Record<string, string> = Object.fromEntries((ctx.graph.nodes || []).map((n: any) => [n.id, n.label]));
            return (
              <div key={`${e.from}-${e.to}`} className="flex flex-wrap items-center gap-2 rounded-md bg-muted/60 px-2 py-1">
                <span>{labels[e.from] || e.from} → {labels[e.to] || e.to}</span>
                <span className="tabular text-muted-foreground">强度 {Number(e.strength).toFixed(2)}</span>
                {e.note && <span className="text-muted-foreground">{e.note}</span>}
              </div>
            );
          })}
        </div>
      )}
      {ctx.tool && (
        <div className="rounded-lg border p-2 text-xs">
          <div className="font-medium">待确认操作 · {ctx.tool}</div>
          {ctx.args && Object.keys(ctx.args).length > 0 && <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-[11px] text-muted-foreground">{JSON.stringify(ctx.args, null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}

function EditsForm({ card, edits, setEdits }: { card: any; edits: any; setEdits: (e: any) => void }) {
  const ctx = card.context || {};
  if (card.card_type === "option_select" && ctx.items) {
    const removed: string[] = edits.remove_lines || [];
    return (
      <div className="space-y-2">
        <div className="text-xs text-muted-foreground">取消勾选的险种将从推荐方案中移除</div>
        {ctx.items.map((it: any) => (
          <label key={it.line} className="flex items-center gap-2 text-sm">
            <Checkbox checked={!removed.includes(it.line)} onCheckedChange={(v) => setEdits({ remove_lines: v ? removed.filter((x) => x !== it.line) : [...removed, it.line] })} />
            {it.name}
            <span className="text-xs text-muted-foreground">覆盖风险 {it.risks?.length || 0} 条</span>
          </label>
        ))}
      </div>
    );
  }
  if (card.card_type === "causal_confirm" && ctx.graph) {
    const labels: Record<string, string> = Object.fromEntries(ctx.graph.nodes.map((n: any) => [n.id, n.label]));
    const current: any[] = edits.edges || [];
    const val = (e: any) => current.find((x) => x.from === e.from && x.to === e.to)?.strength ?? e.strength;
    return (
      <div className="space-y-2">
        <div className="text-xs text-muted-foreground">可调整关键因果边的强度（0–1），确认后用于反事实干预与原因力分摊</div>
        {ctx.graph.edges.map((e: any) => (
          <div key={`${e.from}-${e.to}`} className="flex items-center gap-2 text-xs">
            <span className="w-48 truncate">
              {labels[e.from]} → {labels[e.to]}
            </span>
            <input type="range" min={0.02} max={0.98} step={0.01} value={val(e)} onChange={(ev) => setEdits({ edges: [...current.filter((x) => !(x.from === e.from && x.to === e.to)), { from: e.from, to: e.to, strength: Number(ev.target.value) }] })} className="flex-1 accent-[var(--primary)]" />
            <span className="w-10 text-right tabular">{Number(val(e)).toFixed(2)}</span>
          </div>
        ))}
      </div>
    );
  }
  if (card.card_type === "field_check" && ctx.docs) {
    const fields = edits.fields || {};
    return (
      <div className="max-h-72 space-y-3 overflow-y-auto">
        {ctx.docs.map((d: any) => (
          <div key={d.kind}>
            <div className="mb-1 text-xs font-medium">
              {d.name} <span className="text-muted-foreground">置信度 {d.confidence}</span>
            </div>
            <div className="grid gap-1.5">
              {Object.entries(d.fields).slice(0, 8).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2">
                  <span className="w-28 shrink-0 truncate text-xs text-muted-foreground">{k}</span>
                  <Input className="h-7 text-xs" defaultValue={String(v)} onChange={(e) => setEdits({ fields: { ...fields, [d.kind]: { ...(fields[d.kind] || {}), [k]: e.target.value } } })} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }
  return null;
}

export function HitlCardView({ card, onResolved, compact }: { card: any; onResolved?: () => void; compact?: boolean }) {
  const { user } = useAuth();
  const [pending, setPending] = useState<any>(null);
  const [comment, setComment] = useState("");
  const [edits, setEdits] = useState<any>({});
  const [busy, setBusy] = useState(false);
  const overdue = card.deadline && new Date(card.deadline) < new Date();
  const editable = ["option_select", "causal_confirm", "field_check"].includes(card.card_type);
  const canHandle = user?.role === "admin" || user?.role === card.assignee_role;
  const need = card.context?.approval_level || 1;

  const submit = async (action: string, text = comment) => {
    setBusy(true);
    try {
      await api(`/hitl/cards/${card.id}/resolve`, { method: "POST", json: { action, comment: text, edits: Object.keys(edits).length ? edits : null } });
      toast.success("已处理，智能体将继续执行");
      setPending(null);
      setComment("");
      onResolved?.();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setBusy(false);
    }
  };

  const click = (opt: any) => {
    if (opt.requires_comment || (editable && opt.key !== "reject") || opt.key === "option:adjust") setPending(opt);
    else submit(opt.key, "");
  };

  return (
    <div className={cn("rounded-xl border bg-card p-4 shadow-xs", card.status !== "open" && "opacity-75")}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={TYPE_TONE[card.card_type] || "gray"}>{card.type_label}</Pill>
        {card.mandatory === false && <Pill>非强制</Pill>}
        {need > 1 && <Pill tone="violet">需 {need} 级审批</Pill>}
        {card.status === "open" ? (
          <span className={cn("ml-auto flex items-center gap-1 text-xs", overdue ? "text-rose-600" : "text-muted-foreground")}>
            <Clock className="size-3" />
            {overdue ? "已超时" : `截止 ${relative(card.deadline)}`}
            {card.default_action && <span className="text-muted-foreground">· 超时默认{card.options.find((o: any) => o.key === card.default_action)?.label}</span>}
          </span>
        ) : (
          <span className="ml-auto flex items-center gap-1 text-xs text-emerald-700">
            <UserCheck className="size-3" /> {card.resolution?.label}
            {card.resolution?.system && "（超时自动）"} · {dateTime(card.resolved_at)}
          </span>
        )}
      </div>
      <div className="mt-2 text-sm font-medium">{card.title}</div>
      <div className="mt-1 text-sm text-muted-foreground">{card.reason}</div>
      {!compact && card.impact && <div className="mt-1 text-xs text-muted-foreground/80">影响：{card.impact}</div>}
      {!compact && (
        <div className="mt-2 space-y-3">
          <ContextView card={card} />
          {card.context?.artifact_key && <ConfirmArtifact artifactKey={card.context.artifact_key} version={card.context.version} />}
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {card.status === "open" &&
          card.options.map((o: any) => (
            <Button key={o.key} size="sm" variant={o.primary ? "default" : o.key === "reject" || o.key === "option:decline" ? "outline" : "secondary"} disabled={busy || !canHandle} onClick={() => click(o)}>
              {busy && o.primary && <Loader2 className="size-3.5 animate-spin" />}
              {o.label}
            </Button>
          ))}
        {card.status === "open" && !canHandle && <span className="text-xs text-muted-foreground">该卡片分派给{card.assignee_role === "designer" ? "产品设计" : "产品服务"}岗位</span>}
        <div className="ml-auto flex items-center gap-3 text-xs">
          {card.context?.link && (
            <Link href={card.context.link} className="flex items-center gap-1 text-primary hover:underline">
              查看对象 <ExternalLink className="size-3" />
            </Link>
          )}
          {card.run_id && (
            <Link href={`/runs/${card.run_id}`} className="text-muted-foreground hover:text-foreground">
              运行 #{card.run_id}
            </Link>
          )}
        </div>
      </div>

      <Dialog open={!!pending} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{pending?.label}</DialogTitle>
            <DialogDescription>{card.title}</DialogDescription>
          </DialogHeader>
          {editable && pending?.key !== "reject" && <EditsForm card={card} edits={edits} setEdits={setEdits} />}
          <Textarea value={comment} onChange={(e) => setComment(e.target.value)} placeholder={pending?.key === "option:adjust" ? "请填写调整后的金额与理由，例如：调整为 68 万元，扣除旧损部分" : pending?.requires_comment ? "请填写说明（必填），智能体将据此反思修正" : "补充说明（可选）"} rows={4} />
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)}>
              取消
            </Button>
            <Button disabled={busy || (pending?.requires_comment && !comment.trim())} onClick={() => submit(pending.key)}>
              {busy && <Loader2 className="size-3.5 animate-spin" />} 确认
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
