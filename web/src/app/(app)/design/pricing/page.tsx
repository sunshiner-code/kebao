"use client";

import { ArtifactList } from "@/components/artifact-list";

export default function Page() {
  return <ArtifactList kind="pricing_plan" base="/design/pricing" title="精算定价" description="精算定价智能体的定价方案：创新积分制定价法与分类费率厘定法并列测算，给出建议费率、示例测算与保险费率精算报告，选定后写入正式毛费率。" emptyHint="条款方案冻结后，精算定价会自动启动。" />;
}
