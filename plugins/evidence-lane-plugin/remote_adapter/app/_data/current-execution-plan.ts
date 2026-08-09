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
  boundary: row.order === 191 ? "HIL" : "PRE_HIL",
}));

export const executionPlanBoundary = {
  authority: "SEALED_STATE_TRAVEL_TASK_LIST_PROJECTED_AS_PUBLIC_ROWS_081_191",
  sealedHistoricalDeltaRows: 80,
  liveProjectionRows: currentExecutionPlan.length,
  completedRows: currentExecutionPlan.filter((row) => row.status === "COMPLETED").length,
  activeRow: 182,
  activeTaskPosition: 102,
  activeReceiptPosition: 110,
  pendingRows: currentExecutionPlan.filter((row) => row.status === "PENDING").map((row) => row.number),
  exactlyOneActiveRow: currentExecutionPlan.filter((row) => row.status === "IN_PROGRESS").length === 1,
  persistentUntil: "ROW_191_FINAL_SIX_WAY_HIL_DECIDED_AND_DECISION_DEPENDENT_WORK_COMPLETE",
  lastExecutionStep: 190,
  physicallyLastStep: 191,
  sealedPublicTaskListSha256: "B3E3C620F95DAA59B9E0206C69EBBF42AE4E1E2B142F3B60E81BBFB4E83B31D0",
  panelReactivation: websiteCurrentExecutionBoundary.panelReactivation,
  executionWriterBoundary: websiteCurrentExecutionBoundary.executionWriterBoundary,
  goalContinuity: websiteCurrentExecutionBoundary.goalContinuity,
} as const;
