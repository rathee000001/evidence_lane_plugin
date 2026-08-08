export type BusinessGuideEntry = {
  id: string;
  title: string;
  keywords: readonly string[];
  answer: string;
  href: string;
};

export const businessGuide: readonly BusinessGuideEntry[] = [
  {
    id: "overview",
    title: "Why Evidence Lane exists",
    keywords: ["business problem", "re-explanation", "project continuity", "why evidence lane", "whole plugin", "overview"],
    answer: "Evidence Lane reduces the cost and risk of restarting serious AI work. It turns approved project material into an inspectable evidence base, remembers the exact accepted version, records what changed, and brings the next task back to the unresolved decision instead of asking the user to reconstruct the project. The commercial value is continuity with accountability: less repeated reading, less context drift, and a visible human decision boundary.",
    href: "/",
  },
  {
    id: "source-lanes",
    title: "Eighteen source lanes, one project view",
    keywords: ["18 lanes", "source lanes", "documents", "code", "data", "chat lineage", "source types"],
    answer: "The 18 source lanes organize different kinds of project evidence without pretending they are interchangeable. Code, documents, data, images, research, plans, discussions, existing databases, and visible chat history each keep the structure needed for later inspection. Their results can be viewed together, but the original role, source, and limitations remain visible.",
    href: "/lanes",
  },
  {
    id: "surfaces",
    title: "Fifteen clear plugin surfaces",
    keywords: ["15 surfaces", "plugin surfaces", "six controls", "everyday controls", "boot build refresh", "what can each"],
    answer: "Evidence Lane exposes 15 named surfaces so users can see whether they are starting a session, bringing in evidence, building a candidate, refreshing changed material, recovering state, changing storage, or managing an optional connector. Six are everyday lifecycle controls: Boot, Rollback, Build, Refresh, Mode, and Source Intake. The other surfaces are explicit routers and sidecars; they do not create hidden promotion paths.",
    href: "/#plugin-surfaces",
  },
  {
    id: "source-intake",
    title: "Source Intake brings material under governance",
    keywords: ["source intake", "bring source", "add source", "classify source", "route source"],
    answer: "Source Intake identifies what the user supplied, checks the permitted boundary, removes material that must not be indexed, and routes each detected source to the correct lane. It produces a reviewable intake record. Intake means the material can be examined; it does not mean the material is trusted, accepted, or ready to publish.",
    href: "/lanes",
  },
  {
    id: "build",
    title: "Build creates a reviewable candidate",
    keywords: ["build", "candidate", "sealed package", "unaccepted candidate", "candidate manifest"],
    answer: "Build turns the current governed work into a sealed candidate for review. The candidate has an exact manifest, package identity, test evidence, and source identity. It remains outside accepted project truth until the human chooses the exact approval route and a separate Fuse operation succeeds.",
    href: "/architecture",
  },
  {
    id: "refresh",
    title: "Refresh updates only what changed",
    keywords: ["refresh", "changed sections", "reuse", "incremental", "rebuild only"],
    answer: "Refresh compares the current source with the last accepted evidence, reuses unchanged content, and rebuilds the affected sections. It then seals a new candidate with a visible change record. This saves time without weakening the final check: the complete source boundary and final package are still verified before review.",
    href: "/architecture",
  },
  {
    id: "hil",
    title: "HIL keeps the decision with the human",
    keywords: ["hil", "human control", "six-way", "approve", "human decision", "acceptance"],
    answer: "The six-way Human-in-the-Loop gate separates AI work from human authority. The user can approve the exact candidate, request a bounded correction, ask for more evidence, request an accepted-version rollback, reject it, or record a failed gate. Continued conversation, successful tests, a polished website, or an earlier superseded statement never counts as approval.",
    href: "/hil",
  },
  {
    id: "fuse-pointer",
    title: "Fuse and the accepted pointer",
    keywords: ["fuse", "accepted pointer", "accepted version", "promotion", "move pointer"],
    answer: "Fuse is the separate promotion operation that follows an exact approval. It verifies that the approved candidate is still the same sealed object, records the immutable accepted version, and moves the accepted pointer through a guarded comparison. A candidate, test pass, deployment, or HIL display cannot move that pointer by itself.",
    href: "/architecture",
  },
  {
    id: "rollback",
    title: "Rollback changes the pointer, not history",
    keywords: ["rollback", "previous version", "restore accepted", "pointer only"],
    answer: "Rollback selects another immutable accepted project version by moving the accepted pointer. It does not rewrite the old version, delete later evidence, or silently change live source files. That preserves an auditable history while giving the user a controlled recovery route.",
    href: "/architecture",
  },
  {
    id: "state-travel",
    title: "State Travel resumes unfinished work exactly",
    keywords: ["state travel", "fresh task", "resume unfinished", "handoff receipt", "recover state"],
    answer: "State Travel moves an unfinished governed boundary into a genuinely fresh task when the user requests it or the current host can no longer continue safely. A sealed handoff binds the accepted version, pending candidate, source identity, task projection, later Deltas, and execution profile. The destination verifies those facts before work continues and never treats an old approval statement as a new decision.",
    href: "/architecture",
  },
  {
    id: "plan-lineage",
    title: "Plan Lane and Chat Lineage preserve direction",
    keywords: ["plan lane", "chat lineage", "active step", "step 66", "17-step", "task list", "later steer", "delta", "full poc"],
    answer: "Plan Lane shows the current ordered work and its decision boundaries. Chat Lineage records visible prompts, user steers, assistant output, tools, files, tests, and receipts. The sealed 80-row Delta ledger remains immutable evidence; the current 17-row projection is appended separately. Step 66 is the sole active row. Step 67 retains the carried full POC, forensic checks, and last publication execution, while step 75 is physically last as the final six-way HIL stop.",
    href: "/#delta-ledger",
  },
  {
    id: "modes",
    title: "Modes apply the right operating law",
    keywords: ["mode", "operators", "physics", "chemistry", "maths", "mba", "supply", "formula"],
    answer: "A mode tells Evidence Lane what kind of work is being done and which rules, tools, checks, and outputs belong to it. Operator families are evaluated in the stable order PHYSICS, CHEMISTRY, MATHS, MBA, and SUPPLY, but only the families relevant to the selected mode load. Choosing a mode classifies the work; it does not approve an outcome.",
    href: "/operators",
  },
  {
    id: "hosts-storage",
    title: "Codex and ChatGPT keep separate storage realities",
    keywords: ["codex chatgpt", "host boundary", "storage boundary", "mounted storage", "durable storage", "same plugin"],
    answer: "Codex and ChatGPT install the same Evidence Lane plugin package without pretending they share one filesystem or authority. The stable product name is Evidence Lane; 1.4 is version metadata. Codex operates the full repository lifecycle from exact Git source. ChatGPT Pro shows all 15 packaged skill entries and uses a registered 21-tool read profile for accepted-PV facts, ENV/UOP status, Entry/Exit slips, evidence search, and governed panels. Boot can verify an already active runtime through read-safe Flash and status checks; any skill path that requires a lifecycle write fails closed. ChatGPT's native project memory can still append through its own ENV/UOP and Project Mutation law, but the MCP does not perform or claim that mutation.",
    href: "/connect",
  },
  {
    id: "persistent-goal",
    title: "A blocked token pauses one row, not the Goal",
    keywords: ["persistent panel", "goal paused", "user token", "task list disappear", "one active row", "goal complete"],
    answer: "The persistent task panel stays visible with exactly one active row until the final six-way HIL is actually presented. If one future step needs a user token or external credential, only that dependent row pauses. Independent rows continue, and the Goal is never marked complete merely because a token is pending or the desktop app restarted.",
    href: "/#delta-ledger",
  },
  {
    id: "code-mode-law",
    title: "Code mode has one exact controlled execution law",
    keywords: ["code mode", "controlled required", "ci cd", "pcm mba supply", "sandbox build", "receipt 5183"],
    answer: "Code mode follows one visible order: plan, bounded sandbox build, test, hash, and package. Its controlled loop is entry, preflight, sandbox, patch, test, and exit. CI/CD is CONTROLLED_REQUIRED and the accountable operator set is PCM, MBA, and SUPPLY. The exact receipt keeps that contract identical across source, Codex, ChatGPT guidance, tests, and the website.",
    href: "/operators",
  },
  {
    id: "chatgpt-read-tunnel",
    title: "Contributors can establish the ChatGPT read path once",
    keywords: ["tunnel", "runtime api key", "tunnel id", "starts at boot", "chatgpt pro", "reconnect", "contributor setup"],
    answer: "A contributor runs one secret-safe Windows installer, pastes the OpenAI Tunnel ID once, enters a Runtime API key once through a masked prompt, and registers the Evidence Lane MCP app once. Its technical ID is mapped into the full plugin package so ChatGPT shows all 15 packaged skill entries alongside the 21 read-only tools. Windows starts the verified tunnel after sign-in. Status, repair, and exact removal are built in, and the key is never logged or committed. Vercel and the owned HTTPS domain serve ChatGPT; Codex remains Git-only.",
    href: "/connect",
  },
  {
    id: "connectors",
    title: "Optional connectors remain bounded sidecars",
    keywords: ["connector", "plugin grant", "additional plugin", "storage connector", "account connection", "oauth"],
    answer: "An optional connector receives a named purpose, narrow role, and visible grant. It can help move or present permitted material, but it does not become project truth, change an accepted version, or inherit unrelated account access. Revocation preserves the historical record while removing future authority.",
    href: "/connect",
  },
  {
    id: "proof-security",
    title: "Proof is separated from claims",
    keywords: ["proof", "security", "secrets", "tests", "topology", "what is proven", "claim boundary"],
    answer: "Evidence Lane checks source boundaries, secret exclusion, database integrity, graph reconciliation, package identity, and negative cases that must fail loudly. Those checks can show that a candidate is internally consistent and reviewable. They do not prove customer value, universal correctness, deployment readiness, or human acceptance.",
    href: "/proof",
  },
  {
    id: "studio",
    title: "Evidence AI Studio is the business guide",
    keywords: ["ai studio", "business guide", "business language", "ask the product", "studio answer"],
    answer: "Evidence AI Studio explains the whole plugin in decision-ready business language. It answers from the committed public-safe corpus, links the supporting Evidence Lane pages, and keeps the detailed retrieval receipt available for audit without forcing implementation fragments into the main conversation. Unsupported project questions are refused rather than sent to a general model.",
    href: "/studio",
  },
  {
    id: "release",
    title: "Release and publication happen after acceptance",
    keywords: ["release", "publish", "github", "website", "devpost", "merge main", "version 1.4"],
    answer: "The 1.4 release first aligns the plugin, runtime, packages, documentation, website, tests, and sealed candidate identity. The carried full POC then reconciles the complete Delta ledger and proves real-Git history, GitHub-agent behavior, lane-absence cases, security, and source identity. Only after fresh acceptance can the exact commit merge to main and propagate through GitHub Markdown, every relevant website page and footer, the website Delta table, the complete Vercel production site, and the existing Devpost project 1348634/evidence_os through its separate publication lane.",
    href: "/proof",
  },
  {
    id: "creative",
    title: "Adobe Express is the bounded creative route",
    keywords: ["adobe express", "creative route", "release visual", "design graphic", "account connection", "meshy", "three js", "webgl"],
    answer: "Adobe Express is an optional official route for release graphics, social cards, and other two-dimensional campaign material. Evidence Lane does not request, store, or broker Adobe credentials and does not create an account connection. Meshy, Meshy account linkage, generated GLB product assets, and a 3D-production dependency are excluded. The native Three.js/WebGL website presentation remains part of the existing site and must not be removed merely because Meshy is excluded.",
    href: "https://www.adobe.com/express/",
  },
] as const;

function normalized(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9.]+/g, " ").trim();
}

export function businessGuideFor(question: string) {
  const query = normalized(question);
  const queryWords = new Set(query.split(/\s+/).filter((word) => word.length > 2));
  let best: { entry: BusinessGuideEntry; score: number } | null = null;
  for (const entry of businessGuide) {
    const score = entry.keywords.reduce((total, keyword) => {
      const phrase = normalized(keyword);
      if (query.includes(phrase)) return total + 5;
      const overlaps = phrase.split(/\s+/).filter((word) => queryWords.has(word)).length;
      return total + overlaps;
    }, 0);
    if (score > (best?.score ?? 0)) best = { entry, score };
  }
  return best && best.score >= 2 ? best.entry : null;
}
