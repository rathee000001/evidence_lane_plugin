import type { Metadata } from "next";

import { LaneToolchainExplorer } from "../_components/evidence-console";
import { LaneOrbitAside } from "../_components/lane-orbit-aside";
import { PageHero } from "../_components/page-hero";
import { currentProductContract } from "../_data/current-product-contract";
import { artifactContract, laneToolchains } from "../_data/site";

export const metadata: Metadata = {
  title: "18 Lanes",
  description: "The 18 canonical Evidence Lane source classes and their inspectable artifact contract.",
};

const laneCount = laneToolchains.length;
const intakeSequence = [
  ["Bind source", "Record exact project, task, source bytes, provenance, and exclusion policy before parsing."],
  ["Classify", "Prefer deterministic detection; keep an explicit user override visible and ordered."],
  ["Route", "Select only source classes that are present or explicitly requested. Chat Lineage remains included."],
  ["Materialize", "Write lane-specific SQLite facts, FTS, CAS, topology, tools, pointer, and receipts."],
  ["Reconcile", "Require SQLite, Mermaid, and DOT node/edge identities and declared counts to agree."],
  ["Refresh", "Reprocess changed sections, reuse stable content-addressed chunks, and preserve earlier evidence as history."],
] as const;

export default function LanesPage() {
  return (
    <main>
      <PageHero
        eyebrow="Canonical source registry"
        title={`${laneCount} lanes. One visible lineage.`}
        description="Source Intake chooses a deterministic lane from detected evidence or an explicit user override. Mode remains a separate sidecar. Chat Lineage is always present."
        aside={<LaneOrbitAside />}
      />
      <section className="section shell laneRoutingStory">
        <div className="sectionHead wideHead">
          <span className="kicker">Deterministic Source Intake</span>
          <h2>A lane is a typed evidence contract, not a decorative folder.</h2>
          <p>
            The current registry contains {laneCount} lane toolchains and must agree with the
            package contract of {currentProductContract.canonicalLaneCount}. Unsupported,
            malformed, secret-shaped, cross-project, or ambiguous input fails visibly before it
            can enter a lane database.
          </p>
        </div>
        <div className="laneSequenceGrid">
          {intakeSequence.map(([title, detail], index) => (
            <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><h3>{title}</h3><p>{detail}</p></article>
          ))}
        </div>
      </section>
      <section className="section laneToolchainPage">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">Lane pill · exact runtime contract</span>
            <h2>Select one lane. Inspect every layer.</h2>
            <p>
              Each glass pill opens that lane&apos;s current tools, ordered working sequence,
              parser and retrieval settings, and universal plus lane-specific SQLite schema.
              Downloadable four-file dummy packages, exact-MMD 8K PNGs, and lossless vector
              topology renders live on Proof.
            </p>
          </div>
          <LaneToolchainExplorer />
        </div>
      </section>
      <section className="section shell laneContract">
        <div className="sectionHead"><span className="kicker">Four-file contract</span><h2>No hidden proprietary viewer required.</h2><p>Each lane emits open formats that can be inspected independently and reconciled together. Open the Proof page for downloadable dummy packages.</p></div>
        <div className="contractCards">
          {artifactContract.map(([title, text], index) => <article key={title}><span>0{index + 1}</span><h3>{title}</h3><p>{text}</p></article>)}
        </div>
      </section>
      <section className="section shell laneDispositionStory">
        <div className="sectionHead wideHead"><span className="kicker">Refresh disposition</span><h2>Loaded, missing, deferred, and historical are different states.</h2><p>A refresh never manufactures empty lane packages to make a total look complete. Every canonical lane receives a hash-bound disposition while only real loaded lanes emit artifacts.</p></div>
        <div className="compareGrid">
          <article><h3>Loaded or partial</h3><p>Current authorized sources exist. Required tools, parser state, facts, topology, and limitations remain inspectable.</p></article>
          <article><h3>Missing</h3><p>No current source of that class exists, so no placeholder directory or database is emitted.</p></article>
          <article><h3>Deferred history</h3><p>A prior lane is no longer in the current source set. Earlier evidence remains immutable history without masquerading as current input.</p></article>
        </div>
      </section>
      <section className="section shell lineageFeature">
        <div>
          <span className="kicker">Chat Lineage</span>
          <h2>Steers are evidence, including corrections and “pursue same HIL.”</h2>
          <p>Visible user prompts and steers are appended as ordered, idempotent events alongside visible assistant output, tool calls, commands, files, tests, builds, actor type, model identity when available, token telemetry when available, hashes, and pointers.</p>
        </div>
        <div className="lineageRules">
          <div><strong>Included</strong><p>Visible conversation and observable work evidence.</p></div>
          <div><strong>Redacted</strong><p>Secrets and credential-shaped values before indexing.</p></div>
          <div><strong>Never stored</strong><p>Hidden chain-of-thought or private model reasoning.</p></div>
          <div><strong>Never inferred</strong><p>Acceptance from continued conversation or approximate approval language.</p></div>
        </div>
      </section>
    </main>
  );
}
