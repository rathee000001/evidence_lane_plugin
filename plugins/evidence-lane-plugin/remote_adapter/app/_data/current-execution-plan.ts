export type ExecutionPlanStatus = "COMPLETED" | "IN_PROGRESS" | "PENDING";

export type ExecutionPlanRow = {
  number: number;
  status: ExecutionPlanStatus;
  step: string;
  boundary: "PRE_HIL" | "HIL" | "POST_HIL";
};

export const currentExecutionPlan: readonly ExecutionPlanRow[] = [
  {
    number: 73,
    status: "IN_PROGRESS",
    boundary: "PRE_HIL",
    step: "Establish the Evidence Lane 1.4.0 identity across the plugin, runtime, packages, documentation, website, tests, and release evidence; implement all carried Pre-HIL Deltas; create the governed feature commit; and seal PV8.",
  },
  {
    number: 66,
    status: "PENDING",
    boundary: "POST_HIL",
    step: "After acceptance, install and verify the newly accepted Evidence Lane 1.4.0 in Codex and ChatGPT through each host's correct storage boundary.",
  },
  {
    number: 70,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Render the Home concentric visualization as 18 source lanes, 15 plugin surfaces, and one human HIL.",
  },
  {
    number: 71,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Add concentric motion to Architecture and Proof, and ordered PHYSICS, CHEMISTRY, MATHS, MBA, and SUPPLY rings to Operators.",
  },
  {
    number: 72,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Make Evidence AI Studio a business-language guide for the whole plugin at Gold AI Studio quality, use Adobe Express as the creative route, and include no 3D model workflow or account linkage.",
  },
  {
    number: 68,
    status: "PENDING",
    boundary: "HIL",
    step: "Present the fresh exact six-way PV8 HIL and wait for a new user decision without inferring or replaying any superseded approval utterance.",
  },
  {
    number: 67,
    status: "PENDING",
    boundary: "POST_HIL",
    step: "LAST and post-HIL only: merge the accepted 1.4.0 commit to main; update and publish GitHub and the website; update the existing Devpost entry through its separate lane; and complete the final audit.",
  },
] as const;

export const executionPlanBoundary = {
  authority: "CURRENT_PLAN_LANE_NOT_HISTORICAL_ACCEPTED_DELTA_LEDGER",
  completedRows: 0,
  activeRow: 73,
  pendingRows: [66, 70, 71, 72, 68, 67],
  supersededApprovalMustNotBeReplayed: true,
  persistentUntil: "NEXT_SIX_WAY_HIL_PRESENTED",
  steerDefaultBoundary: "BEFORE_NEXT_HIL",
  linkedSteerPolicy: "APPEND_TO_VISIBLE_LINEAGE_WITHOUT_REWRITING_CANONICAL_HISTORY",
  unrelatedSteerPolicy: "APPEND_TO_VISIBLE_LINEAGE_AND_UPDATE_NATIVE_PROJECTION",
  historicalAcceptedDeltasRole: "READ_ONLY_EVIDENCE",
  lastStep: 67,
} as const;
