"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { ErrorBlock, PageHeader, PageLoading, Pill } from "@/components/common";

export default function EvalJobPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error } = useApi<any>(`/eval-jobs/${id}`, { refreshInterval: 4000 });
  if (error) return <ErrorBlock error={error} />;
  if (!data) return <PageLoading />;
  return (
    <div>
      <PageHeader crumbs={[{ label: "评测与发布", href: "/agents/evals" }, { label: `#${data.id}` }]} title={data.label || `评测 #${data.id}`} description={`${data.agent_name} · ${data.config_version} · ${data.status}`} />
      <div className="overflow-x-auto rounded-xl border bg-card">
        <table className="w-full text-xs">
          <thead className="border-b text-left text-muted-foreground"><tr><th className="px-3 py-2">样本</th><th>状态</th><th>得分</th><th>与期望</th><th>反思</th><th>耗时</th><th>运行</th></tr></thead>
          <tbody>
            {(data.results || []).map((r: any) => (
              <tr key={r.sample_id} className="border-b last:border-0">
                <td className="px-3 py-1.5">{r.title || r.sample_id}<div className="text-muted-foreground">{r.tags?.start_mode} · {r.tags?.difficulty}</div></td>
                <td>{r.status === "completed" ? <Pill tone={r.passed ? "green" : "amber"}>{r.passed ? "达标" : "未达标"}</Pill> : <Pill tone="red">{r.status}</Pill>}</td>
                <td>{r.score ?? "—"}{r.score_delta != null ? ` (${r.score_delta > 0 ? "+" : ""}${r.score_delta})` : ""}</td>
                <td>{r.match == null ? "无期望" : r.match ? "一致" : "不一致"}<div className="max-w-64 text-muted-foreground">{r.match_detail}</div></td>
                <td>{r.reflection_rounds || 0}</td>
                <td>{r.duration_s}s</td>
                <td>{r.run_id && <Link className="text-primary hover:underline" href={`/runs/${r.run_id}`}>#{r.run_id}</Link>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
