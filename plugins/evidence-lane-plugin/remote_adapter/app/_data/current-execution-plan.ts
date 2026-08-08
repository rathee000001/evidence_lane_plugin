export type ExecutionPlanStatus = "COMPLETED" | "IN_PROGRESS" | "PENDING";

export type ExecutionPlanRow = {
  number: number;
  status: ExecutionPlanStatus;
  step: string;
  boundary: "PRE_HIL" | "HIL" | "POST_HIL";
};

export const codeModeLaw = "Mode=code | ENV formula: plan -> sandbox build -> test -> hash -> package | Loop: entry -> preflight -> sandbox -> patch -> test -> exit | CI/CD: CONTROLLED_REQUIRED | Operators: PCM + MBA + SUPPLY | Receipt=5183AB1AD17D570DA860858B7B45D90F67E996273EA3A642B5E2C2511BA553A6";

export const currentExecutionPlan: readonly ExecutionPlanRow[] = [
  {
    number: 73,
    status: "COMPLETED",
    boundary: "PRE_HIL",
    step: "Evidence Lane 1.4 systemwide source, exact feature commit, PV8 seal, and acceptance.",
  },
  {
    number: 74,
    status: "COMPLETED",
    boundary: "HIL",
    step: "PV9 APPROVE_WITH_DELTA correction: replace prose declarations with executable governed checks, reseal, and accept the corrected candidate.",
  },
  {
    number: 66,
    status: "IN_PROGRESS",
    boundary: "PRE_HIL",
    step: "Install and verify Evidence Lane 1.4 separately under the stable Evidence Lane product name: full Codex lifecycle from the exact Git commit; full ChatGPT plugin package with branded icon, exact version metadata, all 15 packaged skill entries visible (including read-safe Boot/ENV-UOP Flash and accepted-PV Entry/Exit/status/search/panels), and the registered 21-tool read profile; preserve 1.3 until replacement proof and remove obsolete apps only afterward.",
  },
  {
    number: 70,
    status: "COMPLETED",
    boundary: "PRE_HIL",
    step: "Home visualization: 18 source lanes, 15 plugin surfaces, and one human gate.",
  },
  {
    number: 71,
    status: "COMPLETED",
    boundary: "PRE_HIL",
    step: "Architecture, Proof, and Operators governed concentric maps.",
  },
  {
    number: 72,
    status: "COMPLETED",
    boundary: "PRE_HIL",
    step: "Business-language Evidence AI Studio; Adobe Express is an optional official 2D route; no Meshy plugin or account connection.",
  },
  {
    number: 68,
    status: "COMPLETED",
    boundary: "HIL",
    step: "Exact PV8 six-way HIL approved and fused without replaying a superseded decision.",
  },
  {
    number: 76,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Prove exact control-skill parity across governed source, Codex, ChatGPT, and the public website.",
  },
  {
    number: 77,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Enforce the persistent-panel law: exactly one active row remains visible; a required user token pauses only its dependent row and never completes the Goal.",
  },
  {
    number: 78,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: codeModeLaw,
  },
  {
    number: 79,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Ship the contributor ChatGPT read-tunnel bootstrap: accept the Runtime key and Tunnel ID once, link ChatGPT once, start at boot, prove health/recovery/removal, and never log or commit secrets.",
  },
  {
    number: 80,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Display Goal usage in readable K/M notation and include the earlier cumulative usage baseline.",
  },
  {
    number: 82,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Preserve the existing native Three.js/WebGL website; remove only Meshy plugin, API, account, generated-GLB, and 3D-production dependencies.",
  },
  {
    number: 83,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Enforce the ChatGPT Pro plugin law: all 15 packaged skill entries remain visible and read-safe workflows stay usable; Boot uses read-safe ENV/UOP Flash and active-runtime verification; accepted-PV Entry/Exit/status/search/fetch/panels remain readable; lifecycle-write requests fail closed; slips plus the host-owned Project Mutation sector remain visible.",
  },
  {
    number: 81,
    status: "PENDING",
    boundary: "PRE_HIL",
    step: "Use Vercel and the owned durable HTTPS domain for the ChatGPT read connection only; Codex remains Git-only.",
  },
  {
    number: 67,
    status: "PENDING",
    boundary: "POST_HIL",
    step: "LAST execution/publication row: run the carried full POC and reconcile the complete sealed Delta ledger plus live projection; prove the forensic audit, real-Git history, GitHub-agent behavior, lane-absence cases, and security review; only after branch-install proof and fresh HIL approval, merge and push the accepted commit to main; propagate the accepted release through GitHub Markdown, every relevant website page and footer, the website Delta table, the complete Vercel production site, and only existing Devpost project 1348634/evidence_os; finish with one cross-surface audit.",
  },
  {
    number: 75,
    status: "PENDING",
    boundary: "HIL",
    step: "FINAL and physically last: present the exact six-way HIL with the governed suggested next prompt and wait for a fresh user decision.",
  },
] as const;

export const executionPlanBoundary = {
  authority: "CURRENT_PLAN_LANE_NOT_HISTORICAL_ACCEPTED_DELTA_LEDGER",
  sealedHistoricalDeltaRows: 80,
  liveProjectionRows: 17,
  completedRows: 6,
  activeRow: 66,
  pendingRows: [76, 77, 78, 79, 80, 82, 83, 81, 67, 75],
  exactlyOneActiveRow: true,
  requiredUserTokenEffect: "PAUSE_DEPENDENT_ROW_ONLY_NEVER_COMPLETE_GOAL",
  supersededApprovalMustNotBeReplayed: true,
  persistentUntil: "FINAL_SIX_WAY_HIL_PRESENTED",
  steerDefaultBoundary: "BEFORE_NEXT_HIL",
  linkedSteerPolicy: "APPEND_TO_VISIBLE_LINEAGE_WITHOUT_REWRITING_CANONICAL_HISTORY",
  unrelatedSteerPolicy: "APPEND_TO_VISIBLE_LINEAGE_AND_UPDATE_NATIVE_PROJECTION",
  historicalAcceptedDeltasRole: "READ_ONLY_EVIDENCE",
  lastExecutionStep: 67,
  physicallyLastStep: 75,
} as const;
