export const publicSiteUrl = "https://evidencelane.org";
export const publicMcpOrigin = "https://mcp.evidencelane.org";
export const publicMcpUrl = "https://mcp.evidencelane.org/mcp";
export const publicMcpHealthUrl = "https://mcp.evidencelane.org/healthz";
export const repositoryUrl = "https://github.com/rathee000001/evidence_lane_plugin";

export const ownerSocialLinks = [
  { label: "LinkedIn", href: "https://www.linkedin.com/in/praveen-rathee-8b028030b/" },
  { label: "Devpost", href: "https://devpost.com/software/evidence-lane-plugins-codex-claude-code" },
  { label: "YouTube", href: "https://www.youtube.com/@praveenrathee8675" },
] as const;

export const primaryNavigation = [
  { href: "/", label: "Home" },
  { href: "/architecture", label: "Architecture" },
  { href: "/lanes", label: "Lanes" },
  { href: "/operators", label: "Operators" },
  { href: "/studio", label: "Prompt Studio" },
  { href: "/proof", label: "Proof" },
  { href: "/provenance", label: "Provenance" },
  { href: "/connect", label: "Connect" },
] as const;

export const promptSuggestions = [
  "What business problem does Evidence Lane solve?",
  "What happens from Source Intake to an accepted project version?",
  "What can each of the six everyday controls do?",
  "How does the human remain in control at HIL?",
  "How do Codex and ChatGPT use different storage boundaries?",
  "How can Adobe Express support release visuals without an account connection?",
] as const;

export const floatingStudioSuggestions = {
  architecture: [
    "How does Evidence Lane turn source material into accepted project truth?",
    "Why is a candidate different from an accepted version?",
    "Where does the human decision sit in the architecture?",
  ],
  lanes: [
    "Why does Evidence Lane separate work into 18 source lanes?",
    "What happens when Source Intake finds a new kind of material?",
    "What can a business reviewer inspect from each detected lane?",
  ],
  studio: [
    "How does Evidence AI Studio explain the whole plugin?",
    "Why does Studio refuse unsupported project claims?",
    "What is the difference between business guidance and the audit receipt?",
  ],
  default: [
    "What business problem does Evidence Lane solve?",
    "What do Build, Refresh, HIL, and Fuse each mean?",
    "What is active step 73 in the current seven-step plan?",
  ],
} as const;

export const controls = [
  {
    name: "Boot",
    detail: "Atomically verifies runtime, locked Flash, host class, and durable storage before a governed session opens.",
    result: "Verified session entry",
    guardrail: "No candidate and no pointer movement",
  },
  {
    name: "Rollback",
    detail: "Moves only an accepted pointer across immutable accepted versions; it does not rewrite evidence.",
    result: "Accepted pointer transition",
    guardrail: "Live source is never rewritten",
  },
  {
    name: "Build",
    detail: "Seals an unaccepted candidate, validates its evidence package, and stops at the six-way human gate.",
    result: "Immutable candidate package",
    guardrail: "Candidate remains unaccepted",
  },
  {
    name: "Refresh",
    detail: "Re-indexes changed sections while reusing unchanged content-addressed chunks and prior history.",
    result: "Exact source delta and next candidate",
    guardrail: "Complete final source is rechecked",
  },
  {
    name: "Mode",
    detail: "Applies ordered operating-mode intersections without changing source truth or lifecycle authority.",
    result: "Visible mode classification receipt",
    guardrail: "Lifecycle state is restored unchanged",
  },
  {
    name: "Source Intake",
    detail: "Auto-detects or explicitly routes governed sources across the canonical lane registry.",
    result: "Ordered lane-routing receipt",
    guardrail: "Chat Lineage is always included",
  },
] as const;

export const laneToolchains = [
  {
    id: "discussion",
    name: "Discussion",
    reason: "Visible decisions, questions, and scoped conversation evidence.",
    source: "Notes, meetings, and conversation exports",
    parser: "discussion_structured_v1",
    chunker: "semantic_blocks_v1",
    retrieval: "discussion_fts",
    story: ["Detect turns", "Extract decisions and deltas", "Index hard gates"],
  },
  {
    id: "analysis",
    name: "Analysis",
    reason: "Traceable findings, comparisons, assumptions, and unknowns.",
    source: "Audits, analyses, and decision records",
    parser: "analysis_structured_v1",
    chunker: "semantic_blocks_v1",
    retrieval: "analysis_fts",
    story: ["Separate claims", "Bind supporting evidence", "Expose risks and unknowns"],
  },
  {
    id: "plan",
    name: "Plan",
    reason: "Ordered tasks, dependencies, gates, and completion states.",
    source: "Project, implementation, and task plans",
    parser: "plan_structured_v1",
    chunker: "semantic_blocks_v1",
    retrieval: "plan_fts",
    story: ["Read phases", "Link dependencies", "Preserve acceptance criteria"],
  },
  {
    id: "mode",
    name: "Mode",
    reason: "Known operating intersections and explicit custom-mode schemas.",
    source: "Operating rules, policies, and control prompts",
    parser: "mode_contract_v1",
    chunker: "rule_blocks_v1",
    retrieval: "mode_fts",
    story: ["Classify in order", "Intersect allowed actions", "Restore lifecycle state"],
  },
  {
    id: "local_code",
    name: "Local code",
    reason: "Tracked local source, symbols, dependencies, tests, and builds.",
    source: "Authorized local folders and Git worktrees",
    parser: "local_code_snapshot_v2",
    chunker: "tiered_git_history_v2_incremental",
    retrieval: "code_chunk_fts",
    story: ["Hash tracked files", "Project seven logical entities", "Reconcile SQLite, MMD, and DOT"],
  },
  {
    id: "github_code",
    name: "GitHub code",
    reason: "Repository files, full Git history, refs, authorship, and changes.",
    source: "Authorized GitHub repositories and remotes",
    parser: "github_code_reverse_history_v2",
    chunker: "tiered_git_history_v2_incremental",
    retrieval: "code_chunk_fts",
    story: ["Walk reachable history", "Store content-addressed blobs", "Project seven logical entities"],
  },
  {
    id: "docs",
    name: "Documents",
    reason: "Structured text, sections, tables, claims, and document lineage.",
    source: "Documents, Markdown, HTML, and XML",
    parser: "document_structure_v1",
    chunker: "document_hierarchy_v1",
    retrieval: "doc_fts",
    story: ["Recover hierarchy", "Extract tables and images", "Index cited sections"],
  },
  {
    id: "data_excel",
    name: "Data / Excel",
    reason: "Tables, formulas, data quality, schemas, and workbook relationships.",
    source: "Workbooks, CSV, JSON, and Parquet",
    parser: "office_data_structural_v1",
    chunker: "table_structure_v1",
    retrieval: "data_fts",
    story: ["Inspect schema", "Trace formulas", "Sample rows with structure"],
  },
  {
    id: "ppt",
    name: "Presentations",
    reason: "Slides, narrative sequence, visual assets, and speaker evidence.",
    source: "PowerPoint and OpenDocument decks",
    parser: "presentation_structure_v1",
    chunker: "slide_structure_v1",
    retrieval: "ppt_fts",
    story: ["Read slide order", "Extract shapes and notes", "Bind visual references"],
  },
  {
    id: "pdf_ocr",
    name: "PDF / OCR",
    reason: "Page-aware text, OCR output, layout observations, and citations.",
    source: "Native and scanned PDF documents",
    parser: "pdf_ocr_structural_v2",
    chunker: "page_block_v1",
    retrieval: "pdf_fts",
    story: ["Prefer native text", "Run OCR when needed", "Preserve page locators"],
  },
  {
    id: "images_ocr",
    name: "Images / OCR",
    reason: "Visual descriptions, extracted text, hashes, and source context.",
    source: "Raster images and scanned evidence",
    parser: "image_ocr_structural_v2",
    chunker: "ocr_region_v1",
    retrieval: "image_ocr_fts",
    story: ["Hash exact pixels", "Detect OCR regions", "Flag review areas"],
  },
  {
    id: "artifacts",
    name: "Artifacts",
    reason: "Build outputs, packages, reports, hashes, and verification receipts.",
    source: "Reports, notebooks, manifests, and structured outputs",
    parser: "artifact_structure_v1",
    chunker: "artifact_blocks_v1",
    retrieval: "artifact_fts",
    story: ["Classify artifact", "Extract inspectable text", "Bind relations and review"],
  },
  {
    id: "custom",
    name: "Custom",
    reason: "User-defined evidence classes governed by an explicit lane schema.",
    source: "Named custom files and folders",
    parser: "custom_best_effort_v1",
    chunker: "bounded_text_v1",
    retrieval: "custom_fts",
    story: ["Apply explicit route", "Use bounded extraction", "Fail visibly on unsupported input"],
  },
  {
    id: "brain_loader",
    name: "Brain loader",
    reason: "Existing SQLite brains and sealed packages treated as sources.",
    source: "SQLite PV packages, folders, and databases",
    parser: "brain_package_loader_v1",
    chunker: "package_member_v1",
    retrieval: "brain_loader_fts",
    story: ["Verify package members", "Inspect manifests and pointers", "Load as candidate evidence"],
  },
  {
    id: "research",
    name: "Research",
    reason: "Source-backed findings with citations, dates, and uncertainty.",
    source: "Research documents, datasets, and notes",
    parser: "research_evidence_v1",
    chunker: "claim_evidence_v1",
    retrieval: "research_fts",
    story: ["Frame questions", "Bind evidence and citations", "Keep limitations visible"],
  },
  {
    id: "project_engulf",
    name: "Project Engulf",
    reason: "Project-wide inventory and routing without automatic acceptance.",
    source: "Authorized project folders and archives",
    parser: "project_engulf_v1",
    chunker: "project_structure_v1",
    retrieval: "project_engulf_fts",
    story: ["Inventory the project", "Map components and conflicts", "Route sectors without acceptance"],
  },
  {
    id: "sqlite_brain",
    name: "SQLite brain",
    reason: "Queryable sector facts, FTS, foreign keys, history, and manifests.",
    source: "SQLite databases and brain packages",
    parser: "sqlite_brain_inspector_v1",
    chunker: "sqlite_schema_row_v1",
    retrieval: "loaded_sqlite_brain_fts",
    story: ["Run integrity checks", "Map schema and foreign keys", "Expose compatibility evidence"],
  },
  {
    id: "chat_lineage",
    name: "Chat Lineage",
    reason: "Visible prompts, steers, assistant output, tools, files, tests, and hashes.",
    source: "Chat exports and append-only lineage packets",
    parser: "chat_lineage_state_travel_v57",
    chunker: "prepare_response_commit_v1",
    retrieval: "turn_fts",
    story: ["Prepare visible event", "Redact secrets", "Commit an idempotent hash-chain entry"],
  },
] as const;

export const lanes = laneToolchains.map(({ name, reason }) => [name, reason] as const);

export const painLedger = [
  {
    title: "Every new task can charge a re-explanation tax",
    observation: "A new Codex or ChatGPT task does not inherit exact files, hashes, accepted versions, steers, and gates by implication.",
    failure: "The user repeats the project while the model reconstructs a plausible but potentially different state.",
    response: "Resume from the accepted pointer, PV, Exit Slip, Chat Lineage, pending candidate, and exact HIL.",
  },
  {
    title: "Unchanged sources are repeatedly read and re-parsed",
    observation: "The first complete PV may be heavy, but most later work needs only relevant accepted facts and the sections that changed.",
    failure: "Native restarts repeatedly spend effort rebuilding context and may interpret the same source differently.",
    response: "Parse once into content-addressed lane facts, query with FTS5/BM25/TF-IDF, and Refresh only changed sections while reusing stable chunks.",
  },
  {
    title: "Human direction and AI output blur together",
    observation: "Chats mix user instructions, model proposals, tests, corrections, and release actions in one conversational stream.",
    failure: "Continued work, green CI, or a polished answer is mistaken for human input or acceptance.",
    response: "Record visible actor lineage, keep candidate state separate from accepted truth, and render a lane-specific six-way HIL where only exact APPROVE can authorize Fuse.",
  },
  {
    title: "The model needs a bounded corpus and the user needs command authority",
    observation: "Analysis, planning, code, research, and other work require different tools, formulas, gates, and evidence responses.",
    failure: "A generic prompt expands scope, applies the wrong operator law, or produces an answer that cannot be checked against project truth.",
    response: "Bind the selected mode to its relevant lanes and ENV/UOP operators, query only the governed corpus, and return that mode's correct six-way decision surface.",
  },
  {
    title: "Hosts do not share one storage reality",
    observation: "A stable PC, durable VM, ephemeral Codex VM, and ChatGPT MCP host have different continuity boundaries.",
    failure: "A convenient connector becomes an unverified alternative source of truth.",
    response: "Select durable local or mounted runtime authority by host class and carry only sealed entry/exit evidence.",
  },
] as const;

export const proofMetrics = [
  ["18", "canonical lanes", "Every routed source resolves to one inspectable lane contract."],
  ["15", "plugin surfaces", "Six lifecycle controls plus nine explicit routers and sidecars."],
  ["6", "everyday controls", "Boot, Rollback, Build, Refresh, Mode, and Source Intake."],
  ["1", "promotion token", "Only exact, case-sensitive APPROVE can authorize Fuse."],
] as const;

export const artifactContract = [
  ["SQLite", "Integrity, foreign keys, FTS, facts, history, and content-addressed chunks."],
  ["MMD", "Human-readable semantic topology reconciled back to the lane database."],
  ["DOT", "Machine-comparable graph topology with the same node and edge identity."],
  ["Receipt", "Source policy, pointer, refresh, actor, tool, build, test, and classification facts."],
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
