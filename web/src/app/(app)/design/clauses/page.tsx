"use client";

import { ArtifactList } from "@/components/artifact-list";

export default function Page() {
  return <ArtifactList kind="clause_plan" base="/design/clauses" title="条款设计" description="条款设计智能体的条款方案：按阶段推荐险种与保障责任，给出零免赔产品规则、条款正文、合规校验与风险覆盖矩阵。" emptyHint="风险评估报告冻结后，条款设计会自动启动。" />;
}
