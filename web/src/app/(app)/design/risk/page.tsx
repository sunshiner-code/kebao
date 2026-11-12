"use client";

import { ArtifactList } from "@/components/artifact-list";

export default function Page() {
  return <ArtifactList kind="risk_report" base="/design/risk" title="风险评估" description="风险评估智能体的评估报告：风险清单与等级评分、三阶段归类、一级风险类型覆盖、风险图谱与可保性分析。" emptyHint="在设计项目中启动风险评估后，报告会出现在这里。" />;
}
