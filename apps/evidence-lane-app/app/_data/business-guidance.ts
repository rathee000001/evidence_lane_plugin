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
    answer: "Evidence Lane is a governed memory and evidence system for long-running AI work. It addresses a specific operating problem: a new task, model, or host can otherwise force the user to re-explain the project while the AI reconstructs a plausible but unverifiable state. Evidence Lane keeps the exact accepted project version, source identities, visible user and AI lineage, unfinished work, later corrections, and the next human decision in an inspectable package.\n\nThe lifecycle is deliberate. Source Intake classifies authorized material into 18 lanes for distinct source types. Each lane preserves the structure needed for code, documents, data, images, research, plans, existing SQLite brains, or chat lineage. Adaptive Delta exit refreshes changed live authorities; only a full-PV HIL also appends Project Overlay and presents the conjoined Project/Learning decision. A live proposal can be built, installed, or deployed and still remains outside accepted truth. Only the exact dual approval contract may authorize Fuse and accepted ZIP rotation.\n\nThe current 3.0.0 pre-HIL source packages 25 governed skills and one package-local native MCP server with exactly 91 actions: 30 read-only and 61 write-capable. Accepted Project Truth remains at PV12/generation 12 while the existing PV13 proposal remains unaccepted. Local testing and main-release identities remain separate measured facts. This release governs only a positively proven Codex layer; the public website documents it and never becomes lifecycle authority.\n\nWhat is proven is the mechanism: source boundaries, exact identities, retrieval, graph reconciliation, live-proposal isolation, negative cases, and human-controlled promotion. Customer value, universal correctness, live production readiness, external publication, and human acceptance remain separate claims until their own evidence exists.",
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
    title: "Seventeen clear plugin surfaces",
    keywords: ["17 surfaces", "plugin surfaces", "six controls", "everyday controls", "boot build refresh", "canon", "learning", "what can each"],
    answer: "Evidence Lane exposes 20 governed skill surfaces so users can see whether they are starting a session, bringing in evidence, building a live proposal, deciding a dual HIL through the separate Fuse owner, routing one of three refresh workflows, recovering state, changing storage, resolving AGENTS.md and host MEMORY.md, querying Project Memory and Universe/connector brain, launching bounded Canon work, managing project-isolated Agent Learning, or governing an optional connector. Six are everyday lifecycle controls: Boot, Rollback, Build, Refresh, Mode, and Source Intake. The remaining fourteen are explicit routers or sidecars with separate authority; none creates a hidden Project Truth or HIL promotion path.",
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
    keywords: ["plan lane", "chat lineage", "active row", `row ${websiteCurrentExecutionBoundary.activePublicOrder}`, "task list", "later steer", "delta", `row ${websiteCurrentExecutionBoundary.finalHilPublicOrder}`, "project panel"],
    answer: `Plan Lane shows the current ordered work and its decision boundaries. Chat Lineage records visible prompts, user steers, assistant output, tools, files, tests, and receipts. The live executable projection comes from canonical PLAN_LANE authority and contains ${websiteCurrentExecutionBoundary.taskCount} contiguous rows from Row ${websiteCurrentExecutionBoundary.firstPublicOrder} through Row ${websiteCurrentExecutionBoundary.lastPublicOrder}. Row ${websiteCurrentExecutionBoundary.activePublicOrder} / ${websiteCurrentExecutionBoundary.activeTaskId} is the sole active row. Row ${websiteCurrentExecutionBoundary.finalHilPublicOrder} / ${websiteCurrentExecutionBoundary.finalHilTaskId} remains PHYSICALLY_FINAL_HIL. Linked Deltas remain native references rather than duplicated JSON, and the projection persists until ${websiteCurrentExecutionBoundary.persistentUntil}.`,
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
    title: "Codex source, accepted truth, installed stable, and fallback stay separate",
    keywords: ["codex chatgpt", "codex and chatgpt capabilities differ", "host capability", "host boundary", "storage boundary", "mounted storage", "durable storage", "same plugin"],
    answer: "Evidence Lane 3.0.0 is the current pre-HIL Codex source line. Accepted Project Truth remains PV12/generation 12; source, installed testing, branch-recovery, and main-release identities are separate facts, and none may be inferred from another. Codex lifecycle evidence must come from the package-local native route.\n\nInstallation of an exact 3.0 branch commit may occur only through its governed pre-HIL route. Main promotion and release-slot replacement remain separately authorized lifecycle actions. Evidence Lane currently governs only a positively proven Codex layer, regardless of which desktop shell exposes that layer. Storage connectors are separate surfaces and never lifecycle proof.",
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
    title: "Tunnel and helper processes remain hidden and host-bounded",
    keywords: ["tunnel", "runtime api key", "tunnel id", "starts at boot", "chatgpt pro", "reconnect", "contributor setup", "stable future-test archive", "future test", "fallback"],
    answer: "A durable local Codex host uses the package-local native MCP and does not require an Evidence Lane network tunnel. Any separately classified interactive-ephemeral or future transport receives its own runtime root, launcher, health receipt, and channel identity without interrupting stable Codex.\n\nWindows hook, helper, and tunnel subprocesses must launch without visible console windows. A long-lived helper stays persistently hidden and health-checked; a bounded helper exits with an explicit receipt. Neither form becomes Codex lifecycle authority, and no secret is written into public metadata or process arguments.",
    href: "/connect",
  },
  {
    id: "install-surface",
    title: "Installation metadata is part of release identity",
    keywords: ["install page", "installation metadata", "publisher", "app developer", "version 1.0.0", "action counts", "correct icon", "capability surface"],
    answer: "A valid 3.0.0 Codex test installation surface must show Evidence Lane, the correct icon, Praveen Rathee as the developer identity, exact cachebusted version, a complete business description, the package-local MCP identity, the exact derived current inventory of 26 native skills with no separate command layer, 11 lifecycle hook events with 44 ordered native subhandlers, and 91 native actions classified as 30 read and 61 write. The source, package, installed, MCP, SDK, skill, hook, and remote-adapter fingerprints must match the governed local receipt.\n\nA pre-HIL local test install does not update accepted Project Truth, run Git, merge main, or rewrite the Main slot. The existing PV13 proposal remains an explicit dual Project and Learning HIL boundary; it is not inferred from an install. Any mismatched page or executable historical route is a hard stop.",
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
    keywords: ["release", "publish", "github", "website", "devpost", "merge main", "version 3.0.0"],
    answer: "The 3.0.0 pre-HIL route first aligns plugin source, runtime, package metadata, Git-tracked Markdown, every mapped website page, tests, and exact source identity. The governed branch commit then runs clean GitHub CI, CodeQL, dependency checks, exact-package verification, an authorized test install, and an automatic Vercel branch preview before the final six-way HIL.\n\nOnly after fresh exact approval may that same accepted commit enter a separately governed main-promotion and production-publication route. Preview success, CI success, installation, or continued conversation is never HIL approval.",
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
import { websiteCurrentExecutionBoundary } from "./website-current-execution.ts";
