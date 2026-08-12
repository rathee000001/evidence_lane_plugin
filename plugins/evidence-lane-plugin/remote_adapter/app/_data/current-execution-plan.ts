import {
  websiteCurrentExecution,
  websiteCurrentExecutionBoundary,
} from "./website-current-execution";

export type ExecutionPlanStatus = "COMPLETED" | "IN_PROGRESS" | "PENDING";

export type ExecutionPlanRow = {
  number: number;
  status: ExecutionPlanStatus;
  step: string;
  boundary: "PRE_HIL" | "HIL";
};

export const currentExecutionPlan: readonly ExecutionPlanRow[] = websiteCurrentExecution.map((row) => ({
  number: row.order,
  status: row.status === "IN PROGRESS" ? "IN_PROGRESS" : row.status,
  step: row.summary,
  boundary: row.order === 196 ? "HIL" : "PRE_HIL",
}));

export const executionPlanBoundary = {
  authority: "SEALED_ORIGIN_PLUS_LIVE_LINEAR_PROJECTION_AS_PUBLIC_ROWS_081_196",
  sealedHistoricalDeltaRows: 80,
  liveProjectionRows: currentExecutionPlan.length,
  completedRows: currentExecutionPlan.filter((row) => row.status === "COMPLETED").length,
  activeRow: 184,
  activeTaskPosition: 104,
  activeReceiptPosition: 112,
  pendingRows: currentExecutionPlan.filter((row) => row.status === "PENDING").map((row) => row.number),
  exactlyOneActiveRow: currentExecutionPlan.filter((row) => row.status === "IN_PROGRESS").length === 1,
  persistentUntil: "ROW_196_FINAL_SIX_WAY_HIL_DECIDED_AND_DECISION_DEPENDENT_WORK_COMPLETE",
  lastExecutionStep: 195,
  physicallyLastStep: 196,
  sealedOriginReceiptPanelSha256: websiteCurrentExecutionBoundary.sealedOriginReceiptPanelSha256,
  sealedOriginPublicProjectionSha256: websiteCurrentExecutionBoundary.sealedOriginPublicProjectionSha256,
  livePanelSha256: websiteCurrentExecutionBoundary.livePanelSha256,
  canonicalPlanProjectionSha256: websiteCurrentExecutionBoundary.canonicalPlanProjectionSha256,
  panelReactivation: websiteCurrentExecutionBoundary.panelReactivation,
  executionWriterBoundary: websiteCurrentExecutionBoundary.executionWriterBoundary,
  goalContinuity: websiteCurrentExecutionBoundary.goalContinuity,
} as const;
