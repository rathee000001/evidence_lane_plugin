export const primaryNavigation = [
  { href: "/architecture", label: "Architecture" },
  { href: "/lanes", label: "Lanes" },
  { href: "/proof", label: "Proof" },
  { href: "/provenance", label: "Provenance" },
  { href: "/connect", label: "Connect" },
] as const;
export const controls = [
  {
    name: "Boot",
    detail: "Atomically verifies runtime, locked Flash, host class, and durable storage before a governed session opens.",
  },
  {
    name: "Rollback",
    detail: "Moves only an accepted pointer across immutable accepted versions; it does not rewrite evidence.",
  },
  {
    name: "Build",
    detail: "Seals an unaccepted candidate, validates its evidence package, and stops at the six-way human gate.",
  },
  {
    name: "Refresh",
    detail: "Re-indexes changed sections while reusing unchanged content-addressed chunks and prior history.",
  },
  {
    name: "Mode",
    detail: "Applies ordered operating-mode intersections without changing source truth or lifecycle authority.",
  },
  {
    name: "Source Intake",
    detail: "Auto-detects or explicitly routes governed sources across the canonical lane registry.",
  },
] as const;

export const lanes = [
  ["Discussion", "Visible decisions, questions, and scoped conversation evidence."],
  ["Analysis", "Traceable findings, comparisons, assumptions, and unknowns."],
  ["Plan", "Ordered tasks, dependencies, gates, and completion states."],
  ["Mode", "Known operating intersections and explicit custom-mode schemas."],
  ["Local code", "Tracked local source, symbols, dependencies, tests, and builds."],
  ["GitHub code", "Repository files, full Git history, refs, authorship, and changes."],
  ["Documents", "Structured text, sections, tables, claims, and document lineage."],
  ["Data / Excel", "Tables, formulas, data quality, schemas, and workbook relationships."],
  ["Presentations", "Slides, narrative sequence, visual assets, and speaker evidence."],
  ["PDF / OCR", "Page-aware text, OCR output, layout observations, and citations."],
  ["Images / OCR", "Visual descriptions, extracted text, hashes, and source context."],
  ["Artifacts", "Build outputs, packages, reports, hashes, and verification receipts."],
  ["Custom", "User-defined evidence classes governed by an explicit lane schema."],
  ["Brain loader", "Existing SQLite brains and sealed packages treated as sources."],
  ["Research", "Source-backed findings with citations, dates, and uncertainty."],
  ["Project Engulf", "Project-wide inventory and routing without automatic acceptance."],
  ["SQLite brain", "Queryable sector facts, FTS, foreign keys, history, and manifests."],
  ["Chat Lineage", "Visible prompts, steers, assistant output, tools, files, tests, and hashes."],
] as const;

export const artifactContract = [
  ["SQLite", "Integrity, foreign keys, FTS, facts, history, and content-addressed chunks."],
  ["MMD", "Human-readable semantic topology reconciled back to the lane database."],
  ["DOT", "Machine-comparable graph topology with the same node and edge identity."],
  ["Pointer", "Entered-from, proposed version, lifecycle state, and exact hash evidence."],
  ["Receipt", "Source policy, refresh, actor, tool, build, test, and classification facts."],
] as const;

export const proofRules = [
  ["Tracked source only", "Git intake starts from the repository index, not arbitrary working-tree files."],
  ["Secret-safe before indexing", ".env, runtime state, credentials, private keys, and detected secret content are excluded before SQLite, CAS, FTS, or history insertion."],
  ["Graph reconciliation", "SQLite facts, Mermaid nodes and edges, and DOT nodes and edges must agree by identity and declared counts."],
  ["Candidate isolation", "Build and Refresh produce candidate overlays. Accepted sector truth changes only after an authorized Fuse."],
  ["Visible lineage only", "Prompts, steers, visible output, tools, files, tests, and available telemetry are retained; hidden chain-of-thought is never an input."],
] as const;

export const credits = [
  ["Praveen Rathee", "Independent product direction, architecture, funded hardware and services, source authority, testing, and human acceptance."],
  ["OpenAI ChatGPT", "Research partner, red-team and blue-team questioning, product critique, analysis, and decision support."],
  ["OpenAI Codex", "Implementation, debugging, test construction, forensic verification, documentation, and release evidence under human control."],
  ["Google Gemini", "Reasoning dialogue and comparative questioning used during research."],
  ["Anthropic Claude", "Independent Fable-plugin exploration and comparative implementation feedback."],
] as const;
