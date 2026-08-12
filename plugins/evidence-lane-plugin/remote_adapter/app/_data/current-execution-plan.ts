import {
  websiteCurrentExecution,
  websiteCurrentExecutionBoundary,
} from "./website-current-execution.ts";

export type ExecutionPlanStatus = "COMPLETED" | "IN_PROGRESS" | "PENDING";

export type ExecutionPlanRow = {
  number: number;
  status: ExecutionPlanStatus;
  step: string;
  boundary: "PRE_HIL" | "HIL";
};

export const currentExecutionPlan: readonly ExecutionPlanRow[] = websiteCurrentExecution.map((row) => ({
  number: row.order,
  status: row.status,
  step: row.summary,
  boundary: row.panelRole === "PHYSICALLY_FINAL_HIL" ? "HIL" : "PRE_HIL",
}));

export const executionPlanBoundary = {
  authority: websiteCurrentExecutionBoundary.canonicalAuthority,
  sealedHistoricalDeltaRows: 80,
  liveProjectionRows: currentExecutionPlan.length,
  completedRows: currentExecutionPlan.filter((row) => row.status === "COMPLETED").length,
  activeRow: websiteCurrentExecutionBoundary.activePublicOrder,
  activeTaskId: websiteCurrentExecutionBoundary.activeTaskId,
  activeTaskPosition: websiteCurrentExecutionBoundary.activeTaskPosition,
  pendingRows: currentExecutionPlan.filter((row) => row.status === "PENDING").map((row) => row.number),
  exactlyOneActiveRow: currentExecutionPlan.filter((row) => row.status === "IN_PROGRESS").length === 1,
  persistentUntil: websiteCurrentExecutionBoundary.persistentUntil,
  lastExecutionStep: websiteCurrentExecutionBoundary.finalHilPublicOrder - 1,
  physicallyLastStep: websiteCurrentExecutionBoundary.finalHilPublicOrder,
  physicallyLastTaskId: websiteCurrentExecutionBoundary.finalHilTaskId,
  canonicalPlanSha256: websiteCurrentExecutionBoundary.canonicalPlanSha256,
  executableProjectionSha256: websiteCurrentExecutionBoundary.executableProjectionSha256,
  websitePlanSnapshotSha256: websiteCurrentExecutionBoundary.websitePlanSnapshotSha256,
} as const;
