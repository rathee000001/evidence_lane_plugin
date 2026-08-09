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
      <ol aria-label="Current 111-row Evidence Lane execution plan">
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
        This exact 111-row projection runs from public row 081 through 191 without rewriting the 80 sealed historical rows. Row 182 is the sole active row, row 190 is the final fresh POC/forensic/publication sweep, and row 191 is physically last as the final six-way HIL. The panel remains visible until that HIL is actually decided; a missing user token pauses its dependent work and never completes the Goal.
      </p>
    </div>
  );
}
