import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Terms" };

export default function TermsPage() {
  return (
    <main className="legal shell">
      <Link href="/">← Evidence Lane</Link>
      <span className="kicker">Public policy</span>
      <h1>Terms</h1>
      <p>Evidence Lane is an evidence-management and human-review system. Generated candidates, classifications, summaries, and topologies may be incomplete or wrong and require user review.</p>
      <h2>Acceptance authority</h2>
      <p>No website visit, tool call, continued conversation, approximate approval phrase, test result, installation, or deployment constitutes acceptance. Only the governed decision bound to the displayed candidate may change lifecycle state; the current implementation reserves Fuse for the exact case-sensitive token <code>APPROVE</code>.</p>
      <h2>Use</h2>
      <p>Users remain responsible for source rights, secrets, downstream decisions, and compliance. The service must not be used to bypass access controls or represent unaccepted candidate material as accepted truth.</p>
      <h2>Limitations</h2>
      <p>Evidence Lane does not replace security review, SAST, DAST, legal advice, human architecture judgment, or operational monitoring. Its evidence can support those processes; it cannot silently complete them.</p>
    </main>
  );
}
