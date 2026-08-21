import type { Metadata } from "next";

import { LaneToolchainExplorer } from "../_components/evidence-console";
import { LaneOrbitAside } from "../_components/lane-orbit-aside";
import { PageHero } from "../_components/page-hero";
import { artifactContract } from "../_data/site";

export const metadata: Metadata = {
  title: "18 Lanes",
  description: "The 18 canonical Evidence Lane source classes and their inspectable artifact contract.",
};

export default function LanesPage() {
  return (
    <main>
      <PageHero
        eyebrow="Canonical source registry"
        title="Eighteen lanes. One visible lineage."
        description="Source Intake chooses a deterministic lane from detected evidence or an explicit user override. Mode remains a separate sidecar. Chat Lineage is always present."
        aside={<LaneOrbitAside />}
      />
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
