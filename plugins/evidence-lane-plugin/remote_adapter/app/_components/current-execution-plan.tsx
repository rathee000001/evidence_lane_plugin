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
      <ol aria-label="Current seven-step Evidence Lane execution plan">
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
        This seven-row panel is the current Plan Lane, not historical accepted-Delta evidence. Later steers append through visible lineage and update this projection without rewriting sealed history. Step 67 stays last and remains post-HIL only. The panel remains visible until the next six-way HIL is actually presented.
      </p>
    </div>
  );
}
