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
      <div className="hilStateStrip"><article><span>Accepted truth</span><strong>PV7 · generation 7</strong></article><article><span>Active work</span><strong>1.4.0 candidate preparation</strong></article><article><span>Pointer effect</span><strong>None</strong></article></div>
      <h2>Exact six-way vocabulary</h2>
      <div className="hilChoiceGrid">
        {choices.map(([token, meaning], index) => <article key={token}><span>{String(index + 1).padStart(2, "0")}</span><code>{token}</code><p>{meaning}</p></article>)}
      </div>
      <h2>Current public boundary</h2>
      <p>PV7 remains the accepted truth at generation 7. Evidence Lane 1.4.0 and its carried Pre-HIL Deltas remain unfinished candidate work until the governed source is committed, tested, refreshed, and presented at a fresh exact six-way PV8 gate. No earlier or superseded approval utterance is replayed, and no accepted pointer moves merely because this page renders.</p>
    </main>
  );
}
