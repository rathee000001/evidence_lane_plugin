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
  "Apply the already-recorded APPROVE_WITH_DELTA as the complete post-old-HIL, pre-replacement-HIL correction: bind AC12 identity; disposition every source; implement the distinct gh-aw execution harness; enforce conditional lane emission plus non-Git one-shot and real-Git tests; deliver local/Vercel/ChatGPT metadata, final-domain/legal links, annotated tools, structured results, MCP Apps panels, footer/social/legal corrections; publish the full persistent execution plan on Vercel and in Prompt Studio; implement unfinished-work State Travel with exact resume row, pointer/candidate context, additive Deltas, model/submodel/reasoning-speed profile verification, sole-writer law, entry-only read-only recovery agents, and explicit-user-only later subagents; make steer Deltas pre-HIL by default unless explicitly bounded otherwise, canonically append linked Deltas to their existing step, append unrelated Deltas as new numbered steps with an increased count, preserve the visible panel until HIL, and make the Plan Lane the canonical post-planning goal/task list projected to the Codex Goal and task panel, require a Codex-only /pl reminder when Plan mode is not active, and return a short copy/paste Goal prompt when planning finishes; keep ChatGPT as a separate host universe where the same plugin code reads and append-only writes that host's own PV in its mounted persistent storage under the same lane/ENV/Exit-Slip laws, with no invented Codex Plan/Goal/task-panel controls and no conflation with Codex storage or the public Vercel MCP/UI publication surface; deploy the exact verified candidate to the purchased Vercel domains before the replacement HIL, then update all relevant GitHub files/metadata and the existing Devpost project; restore the original Source Intake pill treatment with lane-specific glass-orb icons only in that surface; append the current 51 live execution rows after the existing 80 rows in the same 131-row public additive ledger container instead of a second ledger section; render Prompt Studio suggestions as readable rich-white cards with larger text; add a UI-aligned floating Prompt Studio backed by the same governed RAG projection and a strictly general-question, no-hit-only openrouter/free route that has no paid fallback and never supplies project evidence; apply the same frosted-glass background and universal icon-in-orb pill contract across Evidence AI Studio and audit the entire website for pill-schema exceptions; remove fixed universal-pill sizing where it creates dead space, make compact controls content-sized, and use high-depth glass treatment for the candidate-manifest, human-decision, and accepted-pointer flow; connect every Source Intake pill continuously to the candidate manifest; center every lane icon inside its glass orb and remove duplicate operator notation badges; keep footer README and documentation links inside canonical interactive website pages; label the exact use, boundary, and disposition of every reconciled upstream source including Graphify and OpenAI guidance and expose the relevant contribution links; make Connect and HIL non-duplicative, render the Codex full-lifecycle and ChatGPT mounted-PV append-only host architectures explicitly, fix dark-card contrast, and keep unresolved public MCP proof truthful; publish linked four-file dummy package outputs for every lane, add a one-shot non-Git plus dummy-local-Git demonstration command while retaining the separate real-Git proof, and implement governed Source Intake add/modify commands that create or change schema-derived lane pills without bypassing classification or HIL; keep README and Security full-width; move downloadable per-lane dummy proof artifacts out of Lane details into a Proof-page lane selector; publish each lane's exact SQLite, MMD, DOT, and refresh receipt plus derived 7680x4320 MMD PNG and lossless SVG inspection renders on the website, and require that the public MMD/DOT/PNG are the lane engine's actual full lane topology rather than a generic diagram connecting the four output files; use the product name Evidence Lane without appending Plugin; regenerate and bind the AI/RAG blobs only after the full correction is complete; make each exact-MMD 8K PNG and lossless SVG proof render open in a full-screen viewer with zoom controls, drag-to-pan hand cursor, Escape close, backdrop close, and a dedicated close button; make the dummy Git intake originate from a synthetic repository with real multi-commit history and prove its commit/parent/file-change chain in the four Git-lane artifacts, while retaining the separate real-Git acceptance test; use a normal hyphen-minus with visible spaces in the home hero between 'truth' and 'not'; remove connector readiness/fail-closed status cards from Home while keeping truthful endpoint status on Connect; make every public-surface endpoint card fully clickable, give website/health/MCP cards distinct high-contrast status treatments, and explain the current fail-closed health errors plus browser-versus-authenticated-MCP behavior without implying readiness; replace flat/shared Code-lane diagrams with Graphify-informed stable-identity evidence graphs derived end-to-end from each lane's actual SQLite package: GitHub Code must expose ref-to-commit parent history, file changes, blob/content identities, chunks, semantic code relationships, coverage, and impact, while Local Code must expose working-tree files, symbols, imports, routes, and dependencies without inventing Git history; keep non-code lane topology schema/relation/sample-derived, bind the two supplied MMD authorities by exact SHA-256, and prove GitHub Code and Local Code produce structurally distinct reconciled MMD/DOT/8K/vector renders; use no GitHub Sandbox, Vercel Sandbox, or additional paid usage; retest, deploy/verify, reseal, and stop at the next six-way HIL without replaying the prior decision, moving PV5 or any accepted pointer, merging main, Fusing, or installing the candidate; freeze feature intake now, accept no further improvements before this HIL except fixes required by existing acceptance checks; fix Proof-page 100% overflow and provide lossless deep zoom beyond 300%; place all 18 lane-specific glass-orb icons around the Lanes-page sphere, animate exactly one clockwise revolution on load, then stop with reduced-motion safety; sweep every relevant README, Markdown, legal, security, provenance, GitHub metadata, website, and existing Devpost surface against the final release; and keep this PC on",
  "If approved, Fuse PV6 and verify accepted pointer generation and immutable receipts",
  "Merge the accepted release to main, push main, and deploy production Vercel",
  "Install and verify accepted Evidence Lane in Codex and ChatGPT using each host's correct storage boundary; provide the richest comparable supported name, icon, description, verified final-domain and legal links, tool annotations, structured results, and interactive panels; verify equivalent governed content and behavior without claiming pixel-identical host-owned settings layouts",
  "Run the full post-acceptance POC and forensic audit using the row-46 one-shot lane proof as prior evidence; run the separate real Git test against the main Evidence Lane repository with its full reachable commit and parent history; run the GitHub agent proof; verify every lane and prove that any source lane neither loaded nor detected leaves no PV folder or placeholder; after acceptance and main publication, populate the Evidence-Lane organization security repository with the accepted full history, rerun GitHub security and Dependabot against the accepted manifests, deduplicate package/lockfile findings, and remediate genuine vulnerabilities as post-HIL work",
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
