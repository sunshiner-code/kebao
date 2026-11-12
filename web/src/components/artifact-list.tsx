"use client";

import Link from "next/link";
import { FileText } from "lucide-react";
import { useApi } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { EmptyState, ErrorBlock, LoadingBlock, PageHeader, Pill, Section } from "@/components/common";

export function ArtifactList({ kind, title, description, base, emptyHint }: { kind: string; title: string; description: string; base: string; emptyHint: string }) {
  const { data, error, mutate } = useApi<any[]>(`/artifacts?kind=${kind}`);
  const latest = Object.values(
    (data || []).reduce((acc: Record<string, any>, a: any) => {
      if (!acc[a.artifact_key] || acc[a.artifact_key].version < a.version) acc[a.artifact_key] = { ...a, versions: (acc[a.artifact_key]?.versions || 0) + 1 };
      else acc[a.artifact_key].versions += 1;
      return acc;
    }, {}),
  ) as any[];
  return (
    <div>
      <PageHeader title={title} description={description} />
      {error ? (
        <ErrorBlock error={error} retry={() => mutate()} />
      ) : !data ? (
        <LoadingBlock />
      ) : latest.length === 0 ? (
        <EmptyState title="暂无产物" description={emptyHint} icon={FileText} action={<Link href="/design" className="text-sm text-primary hover:underline">前往设计项目</Link>} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {latest.map((a) => (
            <Link key={a.artifact_key} href={`${base}/${a.artifact_key}`}>
              <Section className="h-full transition-shadow hover:shadow-md">
                <div className="flex items-start gap-3">
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <FileText className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{a.title}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      V{a.version} · {dateTime(a.created_at)}
                    </div>
                    <div className="mt-2 flex gap-1.5">
                      <Pill tone={a.status === "frozen" ? "green" : "amber"}>{a.status === "frozen" ? "已冻结" : "草稿"}</Pill>
                      {a.project_id && <Pill>项目 #{a.project_id}</Pill>}
                      {a.run_id && <Pill>运行 #{a.run_id}</Pill>}
                    </div>
                  </div>
                </div>
              </Section>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
