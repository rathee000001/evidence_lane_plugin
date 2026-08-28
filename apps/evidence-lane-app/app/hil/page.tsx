import type { Metadata } from "next";
import Link from "next/link";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "HIL status",
  description: "Evidence Lane candidate decision state and exact six-way human gate.",
};

const choices = [
  ["APPROVE", "Authorize the displayed candidate for the separate Fuse operation."],
  ["APPROVE_WITH_DELTA: <correction>", "Record bounded correction work without accepting the candidate."],
  ["MORE_RESEARCH: <question>", "Keep the candidate unaccepted and gather named evidence."],
  ["ROLLBACK: <accepted PV>", "Request a pointer-only move among immutable accepted versions."],
  ["REJECT: <reason>", "Reject the candidate while preserving its evidence."],
  ["FAIL: <reason>", "Record a failed gate or invalid candidate."],
] as const;

export default function HilPage() {
  return (
    <main>
      <PageHero
        eyebrow="Human authority"
        title="Six choices. One exact candidate. No implied approval."
        description="HIL decides candidate authority; it does not configure a connector. The accepted pointer remains unchanged until the displayed candidate receives an exact governed decision and a separate permitted Fuse operation."
        aside={<HeroOrbit preset="hil" />}
      />
      <section className="legal shell hilStatusPage">
        <Link href="/">&larr; Evidence Lane</Link>
        <span className="compactDepthPill"><GlassIconOrb color="#efca72" size={30} decorative><OfficialToolIcon tool="package" size={16} decorative /></GlassIconOrb><span>Human decision surface</span></span>
        <p>This route is intentionally different from <Link href="/connect">Connect</Link>. Connect explains installation and host boundaries; HIL displays the vocabulary and state transition law for one exact candidate.</p>
        <div className="hilStateStrip"><article><span>Accepted truth</span><strong>PV12 · generation 12</strong></article><article><span>Active work</span><strong>3.0.0 pre-HIL source · no candidate sealed</strong></article><article><span>Pointer effect</span><strong>None</strong></article></div>
        <h2>Exact six-way vocabulary</h2>
        <div className="hilChoiceGrid">
          {choices.map(([token, meaning], index) => <article key={token}><span>{String(index + 1).padStart(2, "0")}</span><code>{token}</code><p>{meaning}</p></article>)}
        </div>
        <h2>Current public boundary</h2>
        <p>PV10 remains accepted truth at generation 10. The current UI, ledger, fresh installed-repository proof, lane-native one-shot POC, topology comparison, and forensic rerun remain unaccepted correction work until they are committed, tested, refreshed, and presented at a new exact six-way gate. No earlier approval is replayed, and no accepted pointer moves because this page renders.</p>
      </section>
    </main>
  );
}
