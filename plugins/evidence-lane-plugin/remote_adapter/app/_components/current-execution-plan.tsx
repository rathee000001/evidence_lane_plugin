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
      <ol aria-label="Current 116-row public Evidence Lane execution projection">
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
        This exact 116-row public projection runs from Row 081 through Row 196 without rewriting the 80 sealed historical Delta rows or the immutable 119-position State Travel origin. Eight governed positions before Row 081 plus these 116 public rows form the live 124-position panel. Row 184 / position 112 is solely active; Rows 191 through 195 are additive work; and Row 196 / position 124 is physically last as the final six-way HIL. The panel remains visible until that HIL is actually decided; a missing user token pauses its dependent work and never completes the Goal.
      </p>
    </div>
  );
}
