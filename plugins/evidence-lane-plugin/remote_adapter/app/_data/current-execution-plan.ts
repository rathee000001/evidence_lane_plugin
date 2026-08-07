export type ExecutionPlanStatus = "COMPLETED" | "IN_PROGRESS" | "PENDING";

export type ExecutionPlanRow = {
  number: number;
  status: ExecutionPlanStatus;
  step: string;
};

const steps = [
  "Resume and verify the preserved governed Evidence Lane session without mutating PV5",
  "Confirm accepted PV5 pointer, generation, branch, commit, tree, and next candidate boundary",
  "Preserve the old superseded PV5 HIL as read-only research context",
  "Publish the PV5 study branch without merging main",
  "Inventory all supplied repositories, extracted folders, SQLite brains, historical brains, and code packages",
  "Separate V5.9 app, app-generated brain, ChatGPT V3 brains, and historical builder versions by provenance",
  "Reconcile the all-source forensic inventory with the additive Delta ledger",
  "Implement safe ZIP, SQLite, code-repository, and historical-brain source intake contracts",
  "Implement schema-derived lane, mode, toolchain, and output contracts",
  "Implement Git history, commit, parent, patch, route-impact, and test-impact evidence surfaces",
  "Implement mode-selected ENV/UOP/PCM/MBA operator governance and lane-specific six-way HIL behavior",
  "Harden exact version identity, executable receipts, package seals, and candidate/Exit-Slip commit parity",
  "Install and verify the persistent Windows tunnel runtime and boot trigger",
  "Build the public Evidence Lane story site from the governed plugin evidence",
  "Replace source-credit showcase material with user-problem and product-mechanism storytelling",
  "Implement universal glass pills, colored icon orbs, click behavior, and app-aligned visual language",
  "Make the logo decorative and add a separate Home navigation pill",
  "Make lane and operator selectors two-row desktop grids with no horizontal scroll",
  "Move the complete additive Delta ledger to the final landing-page section before the footer",
  "Create separate canonical License, Copyright, Credits, Privacy, Terms, Security, and Support surfaces",
  "Add canonical human contributor roles and public LinkedIn links without private biographical details",
  "Add separate Repository and Contributors groups to every website footer",
  "Classify gh-aw-harness, gh-aw-mcpg, copilot-sdk, gh-aw-threat-detection, gh-aw, and Open WebUI through governed Source Intake",
  "Pin exact upstream commits, licenses, maturity, usable contracts, refusals, and distinct Evidence Lane role for every new reference",
  "Diagnose why GitHub Agents is empty while Actions runs exist, and audit the failing Actions runs",
  "Design the minimum real GitHub agent-session surface without confusing Actions workflows with Copilot sessions",
  "Implement evidence-backed agent harness, MCP gateway, event/session, safe-output, and threat-detection improvements",
  "Add public open-source provenance for GitHub Agentic Workflows, harness, MCP gateway, Copilot SDK, threat detection, Open WebUI, OpenAI docs, plugin guidance, and skills",
  "Update README, copyright, license, security, credits, contribution policy, and relevant repository files",
  "Rewrite the core story around re-explanation tax, repeated parsing, context drift, external memory, pointers, Exit Slips, and HIL authority",
  "Make the landing-page Delta ledger a collapsed-by-default glass table opened by an explicit pill",
  "Define the public-safe full-plugin retrieval corpus and secret/path exclusion boundary",
  "Build deterministic LlamaIndex chunks plus SQLite FTS5, BM25, TF-IDF, RRF, JSON projection, and hash manifest",
  "Wire Prompt Studio to committed local hybrid retrieval with citations, scores, and strict no-hit refusal",
  "Add and update conformance tests for layout, legal pages, contributors, collapsed ledger, retrieval, artifacts, agents, and security",
  "Run Python tests, TypeScript checks, production build, deterministic artifact regeneration, and upstream identity verification",
  "Run responsive browser verification for home, lanes, operators, architecture, Prompt Studio, legal/footer, and agent surfaces",
  "Audit the changed repository for stale versions, stale URLs, secrets, unsupported claims, license errors, and scope drift",
  "Update the existing Devpost project story, open-source credits, and contributor record from verified release evidence",
  "Commit the complete replacement PV6 implementation source on the governed feature branch",
  "Regenerate and commit the hash-bound Prompt Studio SQLite/JSON artifacts through the implementation commit",
  "Push the exact governed feature branch commits after the required remote-action gate",
  "Deploy and verify the exact approved Vercel preview URL against the release commit",
  "Build and seal PV6 manifest, package, Exit Slip, lane bundle, receipts, source intake, and exact commit identity",
  "Present the replacement PV6 exact six-way HIL and keep this task list visible until that gate",
  "Apply the already-recorded APPROVE_WITH_DELTA as the complete post-old-HIL, pre-replacement-HIL correction: bind AC12 identity; disposition every source; implement the distinct gh-aw execution harness; enforce conditional lane emission plus non-Git one-shot and real-Git tests; deliver local, Vercel, and ChatGPT metadata, final-domain/legal links, annotated tools, structured results, MCP Apps panels, and footer/social/legal corrections; publish this full persistent execution plan on Vercel and in Prompt Studio; implement unfinished-work State Travel with exact resume row, pointer/candidate context, additive Deltas, exact model/submodel/reasoning-speed profile verification, sole-writer law, entry-only read-only recovery agents, and explicit-user-only later subagents; make steer Deltas pre-HIL by default unless explicitly bounded otherwise, append linked Deltas to their existing step, append unrelated Deltas as new numbered steps, and preserve the visible panel until HIL; make Plan Lane the canonical post-planning goal/task list projected only to the Codex Goal and task panel, require a Codex-only /pl reminder when Plan mode is not active, and return a short copy/paste Goal prompt; keep ChatGPT as a separate host universe where the same plugin code reads and append-only writes that host's own PV in its mounted persistent storage under the same lane, ENV, and Exit-Slip laws, with no invented Codex Plan, Goal, or task-panel controls and no conflation with Codex storage or the public Vercel MCP/UI publication surface; after verified purchased-domain deployment update all relevant GitHub files/metadata and the existing Devpost project; use no sandboxes or additional paid usage; retest, deploy and verify, reseal, and stop at the next six-way HIL without replaying the prior decision, moving PV5, merging main, promoting production, or installing the candidate",
  "If approved, Fuse PV6 and verify accepted pointer generation and immutable receipts",
  "Merge the accepted release to main, push main, and deploy production Vercel",
  "Install and verify the accepted plugin in Codex and ChatGPT using the correct host storage boundary",
  "Run the full POC, one-shot dummy test across every non-Git lane, separate real Git test for Git, GitHub agent test, and forensic audit of every lane; prove that any source lane neither loaded nor detected leaves no PV folder or placeholder",
  "Present the post-release forensic evidence at a new six-way HIL and pause for the user",
] as const;

export const currentExecutionPlan: readonly ExecutionPlanRow[] = steps.map((step, index) => ({
  number: index + 1,
  status: index < 45 ? "COMPLETED" : index === 45 ? "IN_PROGRESS" : "PENDING",
  step,
}));

export const authoritativeCorrection =
  "Fix AC12 by binding the engine commit to the exact source and Exit-Slip commit 6020563094154ff780705cbed85f453425884914 and rerun the executable postseal gate. Give every supplied repository, brain, ZIP, and SQLite package an explicit adopted contract and test, bounded provenance role, or evidence-backed rejection; integrate gh-aw-harness as a testable execution harness distinct from the SQLite continuity harness. Update the verified final-domain plugin metadata, GitHub About, footer social links, single Contributors link, License and Copyright third-party rights boundary, and public HTTPS ChatGPT MCP metadata. Use no GitHub Sandbox, Vercel Sandbox, or additional paid usage. Retest, reseal, and stop at the next six-way HIL.";

export const executionPlanBoundary = {
  authority: "CURRENT_PLAN_LANE_NOT_HISTORICAL_ACCEPTED_DELTA_LEDGER",
  completedRows: 45,
  activeRow: 46,
  pendingRows: [47, 48, 49, 50, 51],
  priorHilDecisionAlreadyRecorded: "APPROVE_WITH_DELTA",
  hilDecisionMustNotBeReplayed: true,
  persistentUntil: "NEXT_SIX_WAY_HIL_PRESENTED",
  steerDefaultBoundary: "BEFORE_NEXT_HIL",
  linkedSteerPolicy: "APPEND_TO_EXISTING_STEP_WITHOUT_REPLACEMENT",
  unrelatedSteerPolicy: "APPEND_NEW_NUMBERED_STEP_AND_INCREASE_COUNT",
  historicalAcceptedDeltasRole: "READ_ONLY_EVIDENCE",
} as const;
