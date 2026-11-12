export const STAGES = [
  ["risk", "风险评估"],
  ["preference", "偏好与承受能力"],
  ["clause", "条款设计"],
  ["pricing", "精算定价"],
  ["package", "产品包"],
  ["approval", "审批发布"],
] as const;

export const NODE_LABEL: Record<string, string> = { pending: "未开始", running: "运行中", waiting_confirm: "待确认", waiting_input: "待填写", waiting_approval: "待审批", waiting_submit: "待提交", ready: "可组装", done: "已完成", failed: "失败", skipped: "跳过", stale: "需重跑", cancelled: "已取消", rejected: "已退回" };

export const NODE_TONE: Record<string, "gray" | "blue" | "amber" | "violet" | "teal" | "green" | "red" | "orange"> = { pending: "gray", running: "blue", waiting_confirm: "amber", waiting_input: "amber", waiting_approval: "violet", ready: "teal", done: "green", failed: "red", skipped: "gray", stale: "orange", cancelled: "gray", rejected: "red" };

/** 智能体阶段是否允许显示「开始/重跑」：前序未完成时不显示。 */
export function stageActionReady(nodes: Record<string, any> | undefined, stage: string, projectType?: string): boolean {
  const n = nodes || {};
  if (stage === "risk") return true;
  if (stage === "clause") {
    if (n.risk?.status !== "done") return false;
    if (projectType === "enterprise" && n.preference?.status !== "done") return false;
    return true;
  }
  if (stage === "pricing") return n.clause?.status === "done";
  return false;
}
