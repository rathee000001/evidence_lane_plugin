import {
  currentExecutionPlan,
  executionPlanBoundary,
} from "../_data/current-execution-plan";

export function CurrentExecutionPlan() {
  return (
    <div
      className="currentExecutionPlan"
      data-plan-authority={executionPlanBoundary.authority}
      data-active-row={executionPlanBoundary.activeRow}
    >
      <div className="executionPlanSummary" aria-label="Current plan status">
        <span><strong>45</strong> completed</span>
        <span className="active"><strong>46</strong> in progress</span>
        <span><strong>5</strong> pending</span>
      </div>
      <ol aria-label="Current 51-step Evidence Lane execution plan">
        {currentExecutionPlan.map((row) => (
          <li
            className={`executionPlanRow status${row.status}`}
            id={`execution-step-${row.number}`}
            key={row.number}
          >
            <span className="executionStepNumber">{String(row.number).padStart(2, "0")}</span>
            <div>
              <span className="executionStepStatus">{row.status.replace("_", " ")}</span>
              <p>{row.step}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="executionPlanLaw">
        This panel is the current Plan Lane, not the historical accepted-Delta ledger. A linked steer appends to its existing row. An unrelated steer adds a numbered row. The panel remains visible until the next six-way HIL is actually presented.
      </p>
    </div>
  );
}
