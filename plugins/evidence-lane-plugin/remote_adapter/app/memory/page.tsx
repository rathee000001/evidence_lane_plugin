import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { GovernedStoryExplorer } from "../_components/governed-story-explorer";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Memory",
  description: "Evidence Lane durable SQLite memory, bounded retrieval, and host compaction continuity.",
};

const memoryLayers = [
  ["SQLite authority", "Project facts, source identities, task state, receipts, and visible lineage stay queryable without loading a whole conversation.", "database"],
  ["Bounded retrieval", "Exact IDs, FTS5, BM25, and typed links return the smallest project-isolated evidence slice needed for the current decision.", "node"],
  ["Host continuity", "Compaction continuity carries sealed locators and cursors; explicit reads continue to work while unverified hooks remain off.", "pulse"],
] as const;

const memoryStages = [
  {
    id: "locate",
    label: "Locate",
    summary: "Record content-addressed locators for exact project sectors and revisions.",
    outcome: "Typed project-sector URIs, revision hashes, labels, and provenance without copying an unrestricted memory payload.",
    proof: "SQLite locator rows, strict schemas, project IDs, and immutable locator identities.",
    boundary: "Raw host memory, secrets, private reasoning, and cross-project retrieval are excluded.",
    details: [
      "Bind each locator to one project, one sector, one revision identity, and one governed URI scheme.",
      "Reject secret-like or unbounded content before it reaches the searchable locator index.",
      "Keep the source record external; the locator proves where an attributable slice can be retrieved.",
    ],
    icon: "database",
    color: "#69d9f5",
  },
  {
    id: "link",
    label: "Link",
    summary: "Connect related sectors with typed, non-promoting graph edges.",
    outcome: "A project memory graph that can relate Plan, ChatLineage, Canon, Learning, source, and accepted-PV evidence.",
    proof: "Content-addressed source and target locators plus typed edge and evidence hashes.",
    boundary: "A graph edge is navigation evidence; it cannot change authority or approve either endpoint.",
    details: [
      "Verify both endpoints belong to the same governed project before recording the edge.",
      "Preserve corrected revisions as new locators rather than rewriting an earlier identity.",
      "Index the relationship for bounded traversal while retaining its exact evidence hash.",
    ],
    icon: "git",
    color: "#83ddb3",
  },
  {
    id: "query",
    label: "Query",
    summary: "Search the project graph with FTS5/BM25 and a strict result ceiling.",
    outcome: "A ranked, bounded result set with typed neighboring edges and exact retrieval provenance.",
    proof: "SQLite FTS5/BM25 receipts, project/sector filters, query hashes, and result counts.",
    boundary: "No hit is valid; the model must not fill the gap from scrollback, another project, or imagination.",
    details: [
      "Normalize the visible query and reject empty or non-indexable search terms.",
      "Filter by the exact project and optional sector before ranking results.",
      "Return at most twenty locators and only the bounded edge context required for the active Delta.",
    ],
    icon: "node",
    color: "#a99af7",
  },
  {
    id: "compact",
    label: "Compact continuity",
    summary: "Seal a small authority snapshot before compaction and verify it after re-entry.",
    outcome: "An at-most 8,192-byte context carrying pointer, Plan window, task-memory cursor, lineage cursor, and authority locators.",
    proof: "PreCompact and PostCompact hashes plus an idempotent re-entry receipt when those installed lifecycle surfaces are verified.",
    boundary: "The host owns its visible optimization message; Evidence Lane neither renames nor fabricates host UI state.",
    details: [
      "Seal only bounded cursors and locators before the host discards conversation context.",
      "On re-entry, recompute current authority and fail closed if the sealed identities drift.",
      "Keep explicit Memory queries usable when hooks are disabled; automation waits for independent hook proof.",
    ],
    icon: "pulse",
    color: "#f2a1c5",
  },
  {
    id: "reconcile",
    label: "Accepted-PV reconcile",
    summary: "After a real Project HIL accepts a PV, reconcile Memory against those exact accepted bytes.",
    outcome: "An automatic, pointer-bound Memory refresh that becomes the constitutional graph for later bounded queries.",
    proof: "Accepted PV, pointer generation, package hashes, graph counts, and non-promotion flags in one receipt chain.",
    boundary: "Memory reconciliation has no separate HIL and can never substitute for the Project HIL that accepted the PV.",
    details: [
      "Wait for the separate Project promotion route to prove the accepted pointer changed legitimately.",
      "Index only the newly accepted identities and preserve older revision provenance.",
      "Make the refreshed graph available to later Delta-entry snapshots without loading the whole ledger into context.",
    ],
    icon: "package",
    color: "#efca72",
  },
] as const;

export default function MemoryPage() {
  return (
    <main>
      <PageHero
        eyebrow="Project memory"
        title="Memory is a durable authority, not a longer prompt."
        description="Evidence Lane stores inspectable project state in SQLite and retrieves bounded evidence into each task. Hooks automate lifecycle timing only after installed-host verification; explicit reads continue to work while hooks remain off."
        aside={<HeroOrbit preset="home" />}
      />

      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">Current 3.0 contract</span><h2>Store once, query narrowly, preserve provenance.</h2><p>The host may display its own memory-optimization status. Evidence Lane proves only the SQLite, retrieval, graph, and lifecycle receipts it actually controls.</p></div>
        <div className="routeGrid">
          {memoryLayers.map(([title, detail, icon], index) => (
            <article className="routeCard" key={title}><span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#83ddb3", "#a99af7"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span><h3>{title}</h3><p>{detail}</p></article>
          ))}
        </div>
      </section>

      <section className="section governedStoryBand">
        <div className="shell">
          <GovernedStoryExplorer
            eyebrow="Inspectable Memory flow"
            title="Follow a fact from locator to accepted-PV reconciliation."
            description="Choose a stage to inspect what it produces, which evidence proves it, and where its authority stops."
            items={memoryStages}
          />
        </div>
      </section>

      <section className="section shell depthContractGrid">
        <article><span className="kicker">Works with hooks off</span><h2>Explicit bounded retrieval remains available.</h2><p>Memory locators, graph queries, Learning retrieval, Canon inbox reads, and Plan reads are native actions. Hook absence disables automatic timing; it does not erase the underlying SQLite authorities.</p></article>
        <article><span className="kicker">Compaction boundary</span><h2>Recover identities, not a hidden transcript.</h2><p>The compact snapshot carries hashes, cursors, the active Plan window, and authority locators. It excludes the full Plan, full ENV/UOP, raw memory, transcript scrollback, and private reasoning.</p></article>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Authority boundary</span><h2>Memory can recover state. It cannot approve it.</h2><p>Retrieved facts, host compaction, hook success, an automatic graph refresh, or a resumed Goal never infer HIL, Fuse, candidate acceptance, or pointer movement.</p></div></div></section>
    </main>
  );
}
