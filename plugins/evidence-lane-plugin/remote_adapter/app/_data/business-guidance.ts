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
    title: "Why Evidence Lane exists — end to end",
    keywords: ["business problem", "business language", "explain the whole", "whole project", "project continuity", "why evidence lane", "whole plugin", "overview", "end to end"],
    answer: "Evidence Lane is a governed memory and evidence system for long-running AI work. It addresses a specific operating problem: a new task, model, or host can otherwise force the user to re-explain the project while the AI reconstructs a plausible but unverifiable state. Evidence Lane keeps the exact accepted project version, source identities, visible user and AI lineage, unfinished work, later corrections, and the next human decision in an inspectable package.\n\nThe lifecycle is deliberate. Source Intake classifies authorized material into 18 lanes. Each lane preserves the structure needed for code, documents, data, images, research, plans, existing SQLite brains, or chat lineage. Build or Refresh can then create a sealed candidate with SQLite facts, search indexes, MMD and DOT topology, manifests, hashes, tests, and receipts. That candidate can be built, installed, or deployed and still remains outside accepted truth. A six-way HIL decision is the human gate, and only exact APPROVE may authorize the separate Fuse operation that moves the accepted pointer.\n\nRelease 1.5.0 packages 15 governed skills. Codex uses one package-local native MCP server with exactly 62 canonical tools: 21 read-only and 41 write-capable. ChatGPT is a separate remote delivery with a different capability ceiling; on ChatGPT Pro the 21 reads may execute while all 41 writes must remain visible but fail closed. Versioned tunnel channels preserve a proven stable route, isolate future testing, and retain the displaced stable route as archive/fallback. Google Drive, when separately connected, is a carrier only and never project truth.\n\nWhat is proven is the mechanism: source boundaries, exact identities, retrieval, graph reconciliation, candidate isolation, negative cases, and human-controlled promotion. Customer value, universal correctness, live production readiness, external publication, and human acceptance remain separate claims until their own evidence exists.",
    href: "/",
  },
  {
    id: "architecture-flow",
    title: "Parallel evidence work converges on serial authority",
    keywords: ["parallel lanes", "serial authority", "source material", "accepted project truth", "architecture", "candidate different", "human decision sit"],
    answer: "Evidence Lane lets independent evidence work run concurrently because parsing code, documents, data, and lineage does not need to move project truth. Those workers produce inspectable lane facts, indexes, graphs, and receipts. They converge into one deterministic candidate manifest. From that point onward the authority path is serial: seal the candidate, present the lane-correct six-way HIL, verify the exact approved candidate during Fuse, and then move the accepted pointer through a guarded comparison. No worker, test, candidate, website, deployment, or model can accept itself.\n\nCurrent evidence: the package defines the lane contracts, candidate isolation, SQLite/MMD/DOT reconciliation, HIL meanings, and pointer guards. Assumption: parallel execution improves elapsed time for a given corpus. Unknown: the real-world speed and cost benefit across customer projects; that needs measured workloads rather than architecture reasoning.",
    href: "/architecture",
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
    answer: "The six-way Human-in-the-Loop gate separates AI work from human authority. Its exact choices are APPROVE, APPROVE_WITH_DELTA, MORE_RESEARCH, ROLLBACK, REJECT, and FAIL. APPROVE authorizes a separate exact-candidate Fuse check; APPROVE_WITH_DELTA preserves the candidate and appends bounded correction work; MORE_RESEARCH preserves the candidate while adding evidence work; ROLLBACK requests movement only among immutable accepted versions; REJECT records non-acceptance; and FAIL records a failed gate. Continued conversation, successful tests, a polished website, or an earlier superseded statement never counts as approval.",
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
    keywords: ["plan lane", "chat lineage", "active row", "row 184", "124 positions", "task list", "later steer", "delta", "full poc", "row 196", "project panel"],
    answer: "Plan Lane shows the current ordered work and its decision boundaries. Chat Lineage records visible prompts, user steers, assistant output, tools, files, tests, and receipts. The sealed 119-position State Travel panel remains immutable historical evidence with Row 191 physically final in that sealed origin. The live linear projection has grown to 124 positions: Row 184 remains the sole active row, additive work occupies Rows 191 through 195, and the exact six-way HIL moved to Row 196 so it remains physically final. Row 195 corrects the native project panel so Overview remains unchanged, Lanes projects all 18 canonical accepted-PV lanes, and HIL always explains the exact six decisions without changing authority. Nothing was inserted into or renumbered inside the sealed origin receipt.",
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
    keywords: ["codex chatgpt", "codex and chatgpt capabilities differ", "host capability", "host boundary", "storage boundary", "mounted storage", "durable storage", "same plugin"],
    answer: "Codex and ChatGPT use one product identity but do not pretend they share one runtime or authority. Evidence Lane is the product identity; 1.5.0 is version metadata. Evidence Lane 1.5.0 on Codex installs 15 governed skills and one package-local native MCP server with exactly 62 canonical tools: 21 read-only and 41 write-capable. Codex lifecycle evidence must come from that native route, never from the remote tunnel.\n\nThe registered ChatGPT delivery is a separate remote MCP connection. On ChatGPT Pro, official host limits mean only the 21 read operations may execute. The 41 lifecycle-write actions may remain visible for capability discovery only if they return an explicit no-mutation refusal before service invocation; they must never be relabeled as supported writes. The current live ChatGPT install page still shows generic developer metadata and version 1.0.0, so it is not valid 1.5.0 installation proof and must not be installed. Google Drive is separately authorized, separately named, and never lifecycle proof or durable Evidence Lane authority.",
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
    title: "Every release keeps versioned stable, future-test, and archive tunnels",
    keywords: ["tunnel", "runtime api key", "tunnel id", "starts at boot", "chatgpt pro", "reconnect", "contributor setup", "stable future-test archive", "future test", "fallback"],
    answer: "Each Evidence Lane release receives its own runtime root, launcher, scheduled-task identity, tunnel ID, health file, public route receipt, and registry entry. The stable channel keeps serving the last proven release. A different future-test tunnel can start and complete health and host checks while stable remains untouched. Only an evidence-backed promotion may switch the stable channel; the displaced stable release becomes the disabled archive/fallback and can be reactivated without rebuilding it. If the future candidate fails, it is stopped and the existing stable process is never interrupted.\n\nThe Windows registry is append-only and hash-chained, contains no plaintext Runtime API key, and records channel changes. This remote transport serves the ChatGPT layer. It is never Codex lifecycle proof and never replaces the package-local native Evidence Lane server.",
    href: "/connect",
  },
  {
    id: "install-surface",
    title: "Installation metadata is part of release identity",
    keywords: ["install page", "installation metadata", "publisher", "app developer", "version 1.0.0", "action counts", "correct icon", "capability surface"],
    answer: "A valid 1.5.0 installation surface must show Evidence Lane, the correct icon, Praveen Rathee as the developer identity, exact version 1.5.0, a complete business description, the exact MCP identity and connection state, and truthful capability counts. Codex must show 15 skills plus 62 native actions classified as 21 read and 41 write. ChatGPT must show the host-specific reality: on Pro, 21 reads are executable and 41 writes are unavailable and fail closed. Stable, future-test, and archive identity must be visible, and Google Drive must appear only as a separate connector. A page showing generic App developer and version 1.0.0 is a hard mismatch; the safe action is not to install it.",
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
    id: "studio-artifacts",
    title: "Studio exposes artifacts without making them authority",
    keywords: ["artifact formats", "sqlite markdown json csv chart table mmd dot", "read only sql", "vector retrieval", "source chips", "retrieval evidence", "openrouter"],
    answer: "Evidence AI Studio can present the committed public-safe RAG index, Markdown references, JSON receipts, a CSV capability matrix, chart and table summaries, sealed demonstration SQLite packages, and their MMD and DOT topology. Artifact links and hashes remain visible. The current server uses deterministic reviewed synthesis over BM25, TF-IDF, and RRF evidence. Optional live SQL and vector retrieval report WIRED_NOT_CONFIGURED unless their bounded read-only services are actually enabled. OpenRouter may rewrite an already grounded answer for clarity, but it cannot select evidence, add project facts, move a pointer, or become HIL authority. When generation is unavailable, the deterministic grounded answer remains the honest fallback.",
    href: "/studio",
  },
  {
    id: "release",
    title: "Release and publication happen after acceptance",
    keywords: ["release", "publish", "github", "website", "devpost", "merge main", "version 1.5.0"],
    answer: "The 1.5.0 release first aligns the plugin, runtime, packages, documentation, website, tests, and sealed candidate identity. The carried full POC then reconciles the complete Delta ledger and proves real-Git history, GitHub-agent behavior, lane-absence cases, security, and source identity. Only after fresh acceptance can the exact commit merge to main and propagate through GitHub Markdown, every relevant website page and footer, the website Delta table, the complete Vercel production site, and the existing Devpost project 1348634/evidence_os through its separate publication lane.",
    href: "/proof",
  },
] as const;

function normalized(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9.]+/g, " ").trim();
}

export function businessGuideFor(question: string) {
  const query = normalized(question);
  if (/\b(?:explain|describe|summarize)\b.*\b(?:whole|entire|end to end)\b/.test(query)
    || /\bwhole (?:evidence lane )?(?:project|plugin|product)\b/.test(query)) {
    return businessGuide.find((entry) => entry.id === "overview") ?? null;
  }
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
