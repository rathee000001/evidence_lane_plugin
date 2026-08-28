import planProjection from "./website-plan-projection.json" with { type: "json" };

export type WebsiteExecutionStatus = "COMPLETED" | "IN_PROGRESS" | "PENDING";

export type WebsiteExecutionRow = {
  order: number;
  id: string;
  status: WebsiteExecutionStatus;
  summary: string;
  taskPosition: number;
  linkedDeltaIds: readonly string[];
  panelRole?: string;
};

type PlanProjectionRow = {
  row: number;
  task_position: number;
  task_id: string;
  description: string;
  status: WebsiteExecutionStatus;
  linked_delta_ids: string[];
  panel_role?: string;
};

const rows = planProjection.rows as readonly PlanProjectionRow[];

// This view is intentionally thin. The committed JSON is generated from native
// pv_task_backlog PLAN_LANE authority; no row, description, status, or Delta body
// is duplicated here.
export const websiteCurrentExecution: readonly WebsiteExecutionRow[] = rows.map((row) => ({
  order: row.row,
  id: row.task_id,
  status: row.status,
  summary: `Row ${row.row} / ${row.task_id} — ${row.description}`,
  taskPosition: row.task_position,
  linkedDeltaIds: row.linked_delta_ids,
  panelRole: row.panel_role,
}));

const active = websiteCurrentExecution.filter((row) => row.status === "IN_PROGRESS");
const finalHil = websiteCurrentExecution.find((row) => row.panelRole === "PHYSICALLY_FINAL_HIL");

export const websiteCurrentExecutionBoundary = {
  canonicalAuthority: planProjection.canonical_authority,
  firstPublicOrder: planProjection.row_start,
  lastPublicOrder: planProjection.row_end,
  topLevelRows: planProjection.task_count,
  completedRows: planProjection.status_counts.completed,
  activeRows: planProjection.status_counts.in_progress,
  pendingRows: planProjection.status_counts.pending,
  activePublicOrder: planProjection.active_row,
  activeTaskId: planProjection.active_task_id,
  activeTaskPosition: planProjection.active_task_position,
  finalHilPublicOrder: planProjection.physically_final_hil_row,
  finalHilTaskId: planProjection.physically_final_hil_task_id,
  finalHilTaskPosition: planProjection.physically_final_hil_task_position,
  taskCount: planProjection.task_count,
  canonicalTaskCount: planProjection.canonical_task_count,
  historyTaskCount: planProjection.history_task_count,
  persistentUntil: planProjection.persistent_until,
  canonicalPlanSha256: planProjection.canonical_plan_sha256,
  historyProjectionSha256: planProjection.history_projection_sha256,
  executableProjectionSha256: planProjection.executable_projection_sha256,
  websitePlanSnapshotSha256: planProjection.snapshot_sha256,
  lifecycleEventCount: planProjection.lifecycle_event_count,
  lifecycleEventHeadSha256: planProjection.lifecycle_event_head_sha256,
  planningEventCount: planProjection.planning_event_count,
  planningEventHeadSha256: planProjection.planning_event_head_sha256,
  runtimeProjectionContentSha256: planProjection.runtime_projection_content_sha256,
  exactlyOneActiveRow:
    active.length === 1 && active[0]?.order === planProjection.active_row,
  physicallyFinalHilIsLast:
    finalHil?.order === planProjection.row_end &&
    websiteCurrentExecution.at(-1)?.order === finalHil?.order,
} as const;
