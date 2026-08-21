import type { Metadata } from "next";

import { HeroOrbit } from "../_components/hero-orbit";
import { LaneProofExplorer } from "../_components/lane-proof-explorer";
import { PageHero } from "../_components/page-hero";
import { proofRules } from "../_data/site";

export const metadata: Metadata = {
  title: "Proof Boundary",
  description: "Evidence Lane verification rules, negative tests, and current claim boundaries.",
};

export default function ProofPage() {
  return (
    <main>
      <PageHero
        eyebrow="Proof before claim"
        title="Tests can disprove a report. They cannot manufacture acceptance."
        description="Evidence Lane separates verified implementation behavior, historical design context, unaccepted candidate evidence, external deployment state, and human lifecycle authority."
        aside={<HeroOrbit preset="proof" />}
      />
      <section className="section shell dummyLaneProofs" id="dummy-lane-proofs">
        <div className="sectionHead wideHead">
          <span className="kicker">Inspectable dummy evidence</span>
          <h2>Choose a lane. Download its four files. Inspect its 8K and vector topology.</h2>
          <p>Every tab exposes a deterministic, public-safe synthetic package stored directly on this website. Its MMD and DOT are the complete lane-engine topology—source registry, lane schema, retrieval, lifecycle, outputs, and Git history where applicable—not a generic four-output overview. The 8K PNG and lossless SVG are derived from that exact full MMD and are not additional canonical lane files. Real-source Git remains a separate acceptance test.</p>
        </div>
        <LaneProofExplorer />
      </section>
      <section className="section shell proofRules">
        <div className="sectionHead wideHead"><span className="kicker">Current v1.5.0 candidate standard</span><h2>Five rules that must fail loudly.</h2></div>
        <div className="proofRuleGrid">
          {proofRules.map(([title, text], index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><h3>{title}</h3><p>{text}</p></article>)}
        </div>
      </section>
      <section className="section negativeBand">
        <div className="shell negativeGrid">
          <div><span className="kicker">Negative proof</span><h2>A diagram that renders can still be false.</h2><p>The v0.9 lesson is explicit: a tiny syntactically valid Mermaid file is not meaningful topology. Reconciliation now rejects shallow graphs, dangling endpoints, database count disagreement, and any Mermaid/DOT identity divergence.</p></div>
          <div className="negativeTerminal" aria-label="Topology reconciliation failure example">
            <span>topology_reconciliation.json</span>
            <code>status: FAIL</code>
            <code>structural_floor: false</code>
            <code>sqlite_counts_match: false</code>
            <code>mmd_dot_identity_match: false</code>
            <strong>CANDIDATE_BLOCKED</strong>
          </div>
        </div>
      </section>
      <section className="section shell claimTableSection">
        <div className="sectionHead"><span className="kicker">Claim boundary</span><h2>What the public site does and does not establish.</h2></div>
        <div className="claimTable">
          <div className="claimHead"><span>Observation</span><span>Permitted interpretation</span><span>Not proven</span></div>
          <div><span>Landing page renders</span><span>The public Next.js surface is deployed</span><span>ChatGPT MCP readiness</span></div>
          <div><span><code>/healthz</code> returns ready</span><span>Edge configuration and release identity passed its checks</span><span>End-user workflow quality</span></div>
          <div><span>18-lane dummy audit passes</span><span>Fixture contract and negative gates behave as tested</span><span>Every real project topology is correct</span></div>
          <div><span>Candidate package is sealed</span><span>Its bytes and evidence are ready for review</span><span>Acceptance, Fuse, or pointer movement</span></div>
          <div><span>Exact <code>APPROVE</code> is fused</span><span>Bound candidate became accepted under the governed rule</span><span>External market value or universal superiority</span></div>
        </div>
      </section>
      <section className="section shell killCriteria">
        <div><span className="kicker">Kill criteria</span><h2>Stop the direction if provenance cannot survive contact with reality.</h2></div>
        <ul>
          <li>Secrets or operational state reach SQLite, FTS, CAS, Git history, or a sealed package.</li>
          <li>MMD and DOT remain decorative outputs that cannot reconcile to SQLite.</li>
          <li>Durable connector identity cannot be tied to the exact Git release.</li>
          <li>Independent users cannot recover, inspect, and challenge the evidence without the original builder.</li>
          <li>The governance overhead outweighs the error and continuity cost it prevents.</li>
        </ul>
      </section>
    </main>
  );
}
