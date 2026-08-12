import {
  currentExecutionPlan,
  executionPlanBoundary,
} from "../_data/current-execution-plan";

export function CurrentExecutionPlan() {
  const completed = currentExecutionPlan.filter((row) => row.status === "COMPLETED").length;
  const active = currentExecutionPlan.find((row) => row.status === "IN_PROGRESS");
  const pending = currentExecutionPlan.filter((row) => row.status === "PENDING").length;

  return (
    <div
      className="currentExecutionPlan"
      data-plan-authority={executionPlanBoundary.authority}
      data-active-row={executionPlanBoundary.activeRow}
    >
      <div className="executionPlanSummary" aria-label="Current plan status">
        <span><strong>{completed}</strong> completed</span>
        <span className="active"><strong>{active?.number}</strong> in progress</span>
        <span><strong>{pending}</strong> pending</span>
      </div>
      <ol aria-label={`Current ${executionPlanBoundary.liveProjectionRows}-row public Evidence Lane execution projection`}>
        {currentExecutionPlan.map((row) => (
          <li
            className={`executionPlanRow status${row.status}`}
            id={`execution-step-${row.number}`}
            key={row.number}
          >
            <span className="executionStepNumber">{String(row.number).padStart(3, "0")}</span>
            <div>
              <span className="executionStepStatus">{row.status.replace("_", " ")}</span>
              <small>{row.boundary.replace("_", "-")}</small>
              <p>{row.step}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="executionPlanLaw">
        This exact {executionPlanBoundary.liveProjectionRows}-row projection is generated from the
        canonical PLAN_LANE without rewriting the 80 sealed historical Delta rows. Row
        {` ${executionPlanBoundary.activeRow}`} / {executionPlanBoundary.activeTaskId} is solely active;
        Row {executionPlanBoundary.physicallyLastStep} / {executionPlanBoundary.physicallyLastTaskId}
        is physically final. The same panel persists until {executionPlanBoundary.persistentUntil}.
      </p>
    </div>
  );
}
