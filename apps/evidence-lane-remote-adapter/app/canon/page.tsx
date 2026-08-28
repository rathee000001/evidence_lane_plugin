import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { GovernedStoryExplorer } from "../_components/governed-story-explorer";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Canon",
  description: "Evidence Lane bounded task graph, input contracts, and receiver-owned Canon decisions.",
};

const canonSequence = [
  ["Register", "Bind an exact sender, receiver, contract, task UUID, and dependency edge without granting project authority.", "git"],
  ["Receive", "Admit one bounded envelope into the destination inbox while preserving source identity and replay protection.", "package"],
  ["Decide", "The receiver records ACCEPT, REJECT, or MORE_RESEARCH for Canon input only; Project HIL remains separate.", "pulse"],
] as const;

const canonStages = [
  {
    id: "graph",
    label: "Refresh graph",
    summary: "Discover new same-project sectors and relationships without reconstructing the project map.",
    outcome: "An additive task/sector graph with stable nodes, dependency edges, continuation direction, and revision hashes.",
    proof: "Canonical SQLite graph rows, detected-sector identities, edge hashes, and replay-safe refresh receipts.",
    boundary: "Ordinary same-project graph refresh is automatic, non-promoting, and has no HIL.",
    details: [
      "Compare detected project sectors and task relationships with the existing graph authority.",
      "Append new nodes or edges and retain superseded relationships as history rather than deleting them.",
      "Expose only bounded locators to the active Delta-entry snapshot; never load the full graph into the prompt.",
    ],
    icon: "node",
    color: "#69d9f5",
  },
  {
    id: "register",
    label: "Register contract",
    summary: "Name the sender, receiver, allowed payload shape, and exact dependency before transport.",
    outcome: "A receiver-addressed contract with stable task IDs, scope, expiry, and authority-denial fields.",
    proof: "Contract identity, project IDs, task UUIDs, schema hash, and dependency receipt.",
    boundary: "Registration cannot authorize source mutation, Project HIL, Learning acceptance, or pointer movement.",
    details: [
      "Verify both task identities and their permitted cross-task or cross-project relationship.",
      "Bind the schema, purpose, expiry, and expected receiver before any envelope exists.",
      "Reject ambiguous receivers and contracts that collapse Canon into Project Truth.",
    ],
    icon: "git",
    color: "#83ddb3",
  },
  {
    id: "envelope",
    label: "Seal envelope",
    summary: "Package one bounded, attributable Canon input for the named receiver.",
    outcome: "A content-addressed envelope whose payload, source evidence, contract, and destination agree.",
    proof: "Envelope SHA-256, sender receipt, contract hash, receiver binding, expiry, and replay identity.",
    boundary: "No transcript dump, private reasoning, unbounded backlog, or implicit authority travels with the envelope.",
    details: [
      "Select only the evidence fields allowed by the registered contract.",
      "Seal exact source locators and hashes instead of copying unrelated project state.",
      "Fail closed if the same identity is replayed with different bytes or a different receiver.",
    ],
    icon: "package",
    color: "#efca72",
  },
  {
    id: "inbox",
    label: "Inspect inbox",
    summary: "Let the destination read its bounded pending Canon inputs without importing authority.",
    outcome: "A receiver-scoped list of pending, decided, expired, or backfired inputs with source provenance.",
    proof: "Bounded inbox query receipt, receiver identity, state counts, and envelope hashes.",
    boundary: "Reading an envelope cannot accept it, mutate the destination Plan, or complete a Goal.",
    details: [
      "Query by the exact destination project and task identity.",
      "Verify every envelope against its contract and immutable content hash before display.",
      "Return compact metadata and source locators; fetch deeper evidence only when the receiver asks.",
    ],
    icon: "database",
    color: "#a99af7",
  },
  {
    id: "backfire",
    label: "Backfire decision",
    summary: "Escalate the exceptional cross-project contradiction to a bounded human decision.",
    outcome: "An explicit accept, reject, or more-research result for the cross-project backfire only.",
    proof: "Human decision token, exact envelope/graph identity, conflict evidence, and append-only result receipt.",
    boundary: "This is Canon's human gate; it is not Project HIL and cannot move either project's accepted pointer.",
    details: [
      "Prove the contradiction crosses project authority and cannot be resolved by an ordinary additive graph refresh.",
      "Show the human the smallest evidence packet and the exact effect of each decision.",
      "Record the result without rewriting either project's accepted truth or silently training Agent Learning.",
    ],
    icon: "pulse",
    color: "#f2a1c5",
  },
] as const;

export default function CanonPage() {
  return (
    <main>
      <PageHero eyebrow="Canon input" title="Tasks exchange bounded evidence without merging authority." description="Canon carries explicit task-to-task inputs, graph edges, backfire requests, results, and continuity receipts. It never promotes Project Truth or Agent Learning." aside={<HeroOrbit preset="provenance" />} />

      <section className="section shell"><div className="sectionHead wideHead"><span className="kicker">Receiver-owned flow</span><h2>Contract first. Envelope second. Decision last.</h2><p>Same-project graph refresh is automatic and replay-safe. Human Canon involvement is reserved for a separately proven cross-project backfire, not ordinary discovery.</p></div><div className="routeGrid">{canonSequence.map(([title, detail, icon], index) => <article className="routeCard" key={title}><span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#efca72", "#83ddb3"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span><h3>{title}</h3><p>{detail}</p></article>)}</div></section>

      <section className="section governedStoryBand"><div className="shell"><GovernedStoryExplorer eyebrow="Inspectable Canon flow" title="Trace a governed relationship without collapsing authority." description="Open any stage for its payload contract, evidence, replay behavior, and human boundary." items={canonStages} /></div></section>

      <section className="section shell depthContractGrid">
        <article><span className="kicker">Automatic map maintenance</span><h2>New sectors append to the project graph.</h2><p>Source Intake may detect a new governed sector or relationship. Canon refresh verifies and appends that topology so later Delta-entry queries can navigate it without rebuilding older nodes.</p></article>
        <article><span className="kicker">Cross-project exception</span><h2>Backfire is explicit, bounded, and human-owned.</h2><p>Only a separately proven cross-project contradiction creates a Canon decision gate. Its result governs that envelope and graph relationship, never Project PV acceptance.</p></article>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Separation law</span><h2>A Canon decision is not a Project HIL.</h2><p>Canon cannot create or accept a Project candidate, move a PV pointer, approve Git, install a package, accept Agent Learning, or complete a Goal.</p></div></div></section>
    </main>
  );
}
