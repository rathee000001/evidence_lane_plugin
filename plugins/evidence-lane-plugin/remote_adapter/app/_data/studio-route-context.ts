export type StudioRouteContext = {
  id: string;
  path: string;
  title: string;
  purpose: string;
  currentCapability: string;
  evidenceBoundary: string;
  artifactIds: readonly string[];
  suggestions: readonly string[];
};

const sharedSuggestions = [
  "Explain the whole Evidence Lane project in business language.",
  "What is proven today, and what remains an assumption or unknown?",
] as const;

export const studioRouteContexts: readonly StudioRouteContext[] = [
  {
    id: "home",
    path: "/",
    title: "Evidence Lane overview",
    purpose: "Explain the product problem, governed lifecycle, current release, and human authority boundary.",
    currentCapability: "The public site explains the current 2.2.0 pre-HIL Codex source, its 18 evidence lanes, 17 governed skills, six primary controls, native Codex catalog, and separate accepted-PV boundary.",
    evidenceBoundary: "The site does not prove adoption, customer value, production readiness, or human acceptance.",
    artifactIds: ["project-readme", "release-identity", "delta-ledger"],
    suggestions: [
      ...sharedSuggestions,
      "What business problem does Evidence Lane solve?",
      "How does a source become accepted project truth?",
      "Why can a successful build still remain unaccepted?",
      "Where does the human remain in control?",
    ],
  },
  {
    id: "skills",
    path: "/skills",
    title: "Seventeen governed Codex skills",
    purpose: "Explain every installed skill as a primary control, router, session operation, mode surface, connector sidecar, or storage sidecar.",
    currentCapability: "The 2.2.0 source packages seventeen named skills. Six are primary lifecycle controls; Canon and Agent Learning are separate governed sidecars; the remaining skills retain explicit non-overlapping authority boundaries.",
    evidenceBoundary: "A visible skill cannot infer approval, broaden its own scope, or substitute for a missing native host capability.",
    artifactIds: ["project-readme", "release-identity"],
    suggestions: [
      "What are the seventeen governed Evidence Lane skills?",
      "Which six skills are primary lifecycle controls?",
      "Why is State Travel conditional rather than a seventh control?",
      "What can a skill never approve by itself?",
      "How do Canon and Agent Learning keep separate authority?",
      "When does a Delta extend an existing skill instead of creating one?",
    ],
  },
  {
    id: "mcp",
    path: "/mcp",
    title: "Package-local native MCP",
    purpose: "Explain the 83 stable native action identities and their read, write, lifecycle, Canon, Learning, and host-capability boundaries.",
    currentCapability: "The Codex package declares one native evidence-lane server with 83 actions: 26 read-only and 57 write-capable.",
    evidenceBoundary: "The documentation website and any remote transport are not Codex lifecycle authority and cannot stand in for installed-host proof.",
    artifactIds: ["release-identity", "capability-matrix-csv"],
    suggestions: [
      "Why does Evidence Lane use one package-local native MCP server?",
      "What separates the 26 read actions from the 57 write actions?",
      "How does the native route fail closed?",
      "Can the website invoke a lifecycle action?",
      "Which native actions belong to Canon and Agent Learning?",
      "What proof is required before a later operation increases the action count?",
    ],
  },
  {
    id: "hooks",
    path: "/hooks",
    title: "Eight Codex lifecycle hooks",
    purpose: "Explain the installed lifecycle event matrix and the boundary between hook transport, skill governance, and host rendering.",
    currentCapability: "The package registers SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, PostCompact, Stop, and best-effort SessionEnd.",
    evidenceBoundary: "Source registration alone is not installed-host invocation proof; unavailable host events remain explicitly unavailable.",
    artifactIds: ["release-identity", "project-readme"],
    suggestions: [
      "Which eight lifecycle events does the package register?",
      "Why do skills, rather than hooks, own HIL behavior?",
      "How is installed-host invocation proven?",
      "How are Windows hook processes kept hidden?",
      "Why do Canon and Agent Learning reuse lifecycle transport instead of adding hooks?",
      "What happens when a host lifecycle event is unavailable?",
    ],
  },
  {
    id: "architecture",
    path: "/architecture",
    title: "Parallel evidence, serial authority",
    purpose: "Explain how source work can run in parallel while candidate sealing, HIL, Fuse, and accepted-pointer movement stay ordered.",
    currentCapability: "The architecture routes authorized sources through policy checks and independent lane evidence into one deterministic candidate manifest.",
    evidenceBoundary: "A candidate manifest, green test, or deployment is not an accepted project version and cannot move the pointer.",
    artifactIds: ["architecture-markdown", "sqlite-brain", "sqlite-topology-mmd", "sqlite-topology-dot"],
    suggestions: [
      "How does Evidence Lane turn source material into accepted project truth?",
      "Why is a candidate different from an accepted version?",
      "Where does the human decision sit in the architecture?",
      "How do parallel lanes converge on serial authority?",
      "What must agree between SQLite, MMD, and DOT?",
      "What can never move the accepted pointer by itself?",
    ],
  },
  {
    id: "lanes",
    path: "/lanes",
    title: "Eighteen source lanes",
    purpose: "Explain why unlike sources keep their own parsers, structure, evidence, and limitations inside one project view.",
    currentCapability: "The package declares 18 canonical lanes and produces inspectable public-safe dummy packages for every detected lane contract.",
    evidenceBoundary: "Lane detection and indexing do not make a source true, accepted, complete, or publication-ready.",
    artifactIds: ["lane-catalog-json", "sqlite-brain", "lane-receipt-json"],
    suggestions: [
      "Why does Evidence Lane separate work into 18 source lanes?",
      "What happens when Source Intake finds a new kind of material?",
      "What can a business reviewer inspect from each detected lane?",
      "How are code, documents, data, and chat lineage kept distinct?",
      "What does the SQLite Brain lane inspect?",
      "What does lane detection prove and not prove?",
    ],
  },
  {
    id: "operators",
    path: "/operators",
    title: "Modes and operators",
    purpose: "Explain how the selected work mode loads the relevant laws, operators, checks, and HIL meanings without changing lifecycle truth.",
    currentCapability: "Code mode binds the controlled entry, preflight, sandbox, patch, test, and exit loop to CI/CD evidence.",
    evidenceBoundary: "Selecting a mode classifies work; it does not approve an output or broaden source authority.",
    artifactIds: ["mode-contract-json", "project-readme"],
    suggestions: [
      "What does Code mode require before work starts?",
      "How do mode operators change the work without changing truth?",
      "Why is CI/CD controlled rather than automatic authority?",
      "Which operator families apply to Code mode?",
      "What happens when two mode laws intersect?",
      "Can selecting a mode approve a candidate?",
    ],
  },
  {
    id: "studio",
    path: "/studio",
    title: "Evidence AI Studio",
    purpose: "Provide whole-project and page-aware business explanations backed by the committed public-safe corpus.",
    currentCapability: "Deterministic reviewed synthesis, BM25 plus TF-IDF plus RRF retrieval, source chips, bounded history, route context, and inspectable artifact views are available without making a model the evidence authority.",
    evidenceBoundary: "OpenRouter is optional generation only. Unsupported project claims refuse, and optional live SQL or vector layers must report their real configuration state.",
    artifactIds: ["studio-rag-json", "sqlite-brain", "capability-matrix-csv"],
    suggestions: [
      ...sharedSuggestions,
      "How does Studio use the current page without treating it as truth?",
      "Which artifact formats can I inspect here?",
      "What happens when retrieval finds no supporting project evidence?",
      "What roles do OpenRouter, SQL, and vector retrieval play?",
    ],
  },
  {
    id: "proof",
    path: "/proof",
    title: "Verification and claim boundaries",
    purpose: "Separate tested mechanism evidence from product, market, deployment, and acceptance claims.",
    currentCapability: "The repository can test source policy, secret exclusion, SQLite integrity, graph reconciliation, package identity, negative cases, and native tool contracts.",
    evidenceBoundary: "Those checks do not prove customer demand, universal correctness, external publication, or HIL acceptance.",
    artifactIds: ["lane-receipt-json", "sqlite-brain", "sqlite-topology-mmd", "sqlite-topology-dot"],
    suggestions: [
      "What does the proof page actually establish?",
      "Which claims remain outside repository proof?",
      "How are negative cases tested?",
      "Why must SQLite, MMD, and DOT reconcile?",
      "Does a ready Vercel preview prove the MCP route?",
      "Does a passing package imply human acceptance?",
    ],
  },
  {
    id: "provenance",
    path: "/provenance",
    title: "Provenance and external references",
    purpose: "Show which outside sources informed design or testing and which bytes or claims were explicitly refused.",
    currentCapability: "Pinned source identities and historical brain references are recorded with reused, refused, and unknown boundaries.",
    evidenceBoundary: "A reference does not become Evidence Lane authority, prove generator attribution, or authorize copying unsupported claims.",
    artifactIds: ["project-readme", "delta-ledger"],
    suggestions: [
      "How does Evidence Lane use external references without copying authority?",
      "What was reused from the Gold reference and what was refused?",
      "Why is a historical brain not current release authority?",
      "How are source commit identities preserved?",
      "What provenance remains unknown?",
      "Can a comparison project prove Evidence Lane behavior?",
    ],
  },
  {
    id: "connect",
    path: "/connect",
    title: "Codex host, storage, and release boundaries",
    purpose: "Explain exact-Git Codex delivery, truthful host capability, stable versus fallback slots, local durability, and optional connector isolation.",
    currentCapability: "Codex 2.2.0 source uses the package-local native Evidence Lane server with 83 actions: 26 read-only and 57 write-capable. Current work governs only a positively proven Codex layer.",
    evidenceBoundary: "The accepted PV12 and installed 2.1 stable identity remain separate from 2.2 pre-HIL source. The disabled fallback is observed at 2.0 and remains untouched until its separately governed rotation step.",
    artifactIds: ["release-channels-json", "chatgpt-connection-json", "capability-matrix-csv"],
    suggestions: [
      "How do source, accepted PV, stable install, and fallback versions differ?",
      "Why does a local durable Codex host use the package-local native server?",
      "When may the disabled fallback slot be replaced?",
      "Why is a tunnel not Codex lifecycle authority?",
      "What is Google Drive allowed to do?",
      "Which install-page metadata must match before installation?",
    ],
  },
  {
    id: "hil",
    path: "/hil",
    title: "Exact six-way human decision",
    purpose: "Explain the explicit decision surface and why continued work, tests, or deployment never imply approval.",
    currentCapability: "The candidate can stop at APPROVE, APPROVE_WITH_DELTA, MORE_RESEARCH, ROLLBACK, REJECT, or FAIL with lane-correct effects.",
    evidenceBoundary: "Only exact case-sensitive APPROVE may authorize the separate Fuse operation; this page cannot infer or replay that token.",
    artifactIds: ["delta-ledger", "release-identity"],
    suggestions: [
      "What are the six exact HIL choices?",
      "What does APPROVE_WITH_DELTA preserve?",
      "Why does continued conversation not count as approval?",
      "What happens after exact APPROVE?",
      "Can a deployment move the accepted pointer?",
      "What is the difference between REJECT and FAIL?",
    ],
  },
] as const;

function normalizedPath(value: string) {
  const path = value.split(/[?#]/, 1)[0] || "/";
  if (path === "/") return path;
  return `/${path.replace(/^\/+|\/+$/g, "")}`;
}

export function studioRouteContextFor(pagePath: string | undefined) {
  const normalized = normalizedPath(pagePath || "/");
  return studioRouteContexts.find((context) => (
    context.path === "/" ? normalized === "/" : normalized.startsWith(context.path)
  )) ?? studioRouteContexts[0];
}

export function studioSuggestionsFor(pagePath: string | undefined, previousQuestion = "") {
  const context = studioRouteContextFor(pagePath);
  const normalizedQuestion = previousQuestion.toLowerCase();
  const suggestions = [...context.suggestions];
  if (normalizedQuestion.includes("whole") || normalizedQuestion.includes("overview")) {
    suggestions.push(
      "Which capability is current, which is planned, and which is blocked?",
      "Show the exact evidence and unknowns behind that explanation.",
    );
  } else if (previousQuestion.trim()) {
    suggestions.push(
      "What evidence supports that answer?",
      "Which risk or unknown should the operator inspect next?",
    );
  }
  return [...new Set(suggestions)].slice(0, 8);
}
