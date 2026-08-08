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
      <ol aria-label="Current seventeen-step Evidence Lane execution plan">
        {currentExecutionPlan.map((row) => (
          <li
            className={`executionPlanRow status${row.status}`}
            id={`execution-step-${row.number}`}
            key={row.number}
          >
            <span className="executionStepNumber">{String(row.number).padStart(2, "0")}</span>
            <div>
              <span className="executionStepStatus">{row.status.replace("_", " ")}</span>
              <small>{row.boundary.replace("_", "-")}</small>
              <p>{row.step}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="executionPlanLaw">
        This 17-row panel is the current Plan Lane, not the 80-row sealed historical Delta ledger. Later steers append through visible lineage and update this projection without rewriting sealed history. Step 67 retains the carried full POC and is the last execution/publication row; step 75 is physically last as the final six-way HIL stop. The panel remains visible with exactly one active row until that HIL is actually presented. A required user token pauses only its dependent row and never completes the Goal.
      </p>
    </div>
  );
}
