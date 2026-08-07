import type { Metadata } from "next";
import Link from "next/link";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";

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
    <main className="legal shell hilStatusPage">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="compactDepthPill"><GlassIconOrb color="#efca72" size={30} decorative><OfficialToolIcon tool="package" size={16} decorative /></GlassIconOrb><span>Human decision surface</span></span>
      <h1>HIL decides candidate authority. It does not configure a connector.</h1>
      <p>This route is intentionally different from <Link href="/connect">Connect</Link>. Connect explains installation and host boundaries; HIL displays the vocabulary and state transition law for one exact candidate.</p>
      <div className="hilStateStrip"><article><span>Accepted truth</span><strong>PV5 · generation 5</strong></article><article><span>Active work</span><strong>Correction pending</strong></article><article><span>Pointer effect</span><strong>None</strong></article></div>
      <h2>Exact six-way vocabulary</h2>
      <div className="hilChoiceGrid">
        {choices.map(([token, meaning], index) => <article key={token}><span>{String(index + 1).padStart(2, "0")}</span><code>{token}</code><p>{meaning}</p></article>)}
      </div>
      <h2>Current public boundary</h2>
      <p>The prior <code>APPROVE_WITH_DELTA</code> decision is already recorded. The correction remains unfinished and no replacement HIL is being claimed on this page. PV5 stays accepted and unchanged until the governed correction is retested, resealed, and a new exact six-way gate is presented.</p>
    </main>
  );
}
