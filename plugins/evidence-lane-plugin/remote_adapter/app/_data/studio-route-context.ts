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
    currentCapability: "The public site can explain the verified 1.5.0 package, its 18 evidence lanes, 15 governed skills, six primary controls, native Codex catalog, and read-safe ChatGPT boundary.",
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
    title: "Codex, ChatGPT, tunnel, and connector boundaries",
    purpose: "Explain separate host deliveries, truthful action availability, versioned tunnel channels, and optional connector isolation.",
    currentCapability: "Codex 1.5.0 uses the package-local native Evidence Lane server with 62 tools: 21 read-only and 41 write-capable. The registered ChatGPT Pro route exposes the catalog but only 21 reads may execute; 41 writes fail closed.",
    evidenceBoundary: "The frozen ChatGPT page showing version 1.0.0 is not the 1.5.0 identity and must not be installed. Google Drive is a separate connector and never lifecycle authority.",
    artifactIds: ["release-channels-json", "chatgpt-connection-json", "capability-matrix-csv"],
    suggestions: [
      "How do Codex and ChatGPT capabilities differ in release 1.5.0?",
      "Why are 41 ChatGPT Pro writes visible but unavailable?",
      "How do stable, future-test, and archive tunnel channels work?",
      "Why must the Codex native server never route through the tunnel?",
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
