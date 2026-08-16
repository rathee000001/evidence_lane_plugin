import type { Metadata } from "next";
import Link from "next/link";

import { artifactContract, controls, proofRules } from "../_data/site";

export const metadata: Metadata = {
  title: "README",
  description: "Human-readable Evidence Lane product, lifecycle, host, and evidence contract.",
};

export default function ReadmePage() {
  return (
    <main className="legal shell repositoryDocument">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="kicker">Repository guide · website edition</span>
      <h1>Evidence Lane 2.2.0</h1>
      <p>Evidence Lane is a local-first, Git-backed evidence lifecycle for Codex. It turns authorized sources and visible task lineage into inspectable lane packages while keeping candidate state separate from accepted truth.</p>
      <h2>The problem</h2>
      <p>Long AI-assisted work crosses task windows, models, hosts, repositories, and toolchains. Reconstructing the project from prose creates re-explanation tax and context drift. Evidence Lane resumes from exact pointers, lane facts, Exit Slips, Chat Lineage, pending work, and the human gate.</p>
      <h2>Six everyday controls</h2>
      <div className="repositoryDocGrid">
        {controls.map((control) => <article key={control.name}><h3>{control.name}</h3><p>{control.detail}</p><small>{control.guardrail}</small></article>)}
      </div>
      <h2>Four-file lane contract</h2>
      <div className="repositoryDocGrid">
        {artifactContract.map(([name, detail]) => <article key={name}><h3>{name}</h3><p>{detail}</p></article>)}
      </div>
      <h2>Governed Source Intake extensions</h2>
      <p>Schema-derived Source Intake pills are append-only. <code>/evi-source-intake ADD &quot;&lt;pill name&gt;&quot; --purpose &quot;&lt;need&gt;&quot; --schema &lt;definition&gt;</code> creates version 1. <code>/evi-source-intake MODIFY &quot;&lt;pill name&gt;&quot; --schema &lt;next-version-definition&gt; --previous-sha256 &lt;exact-sha256&gt;</code> appends the next version only when the prior hash matches. Neither command mutates the canonical 18-lane registry or bypasses classification, source policy, candidate isolation, or HIL.</p>
      <h2>One-shot dummy proof</h2>
      <p>The row-46 proof loads all 18 dummy lanes, gives the GitHub Code lane a real three-commit synthetic repository and parent chain, validates initial and unchanged-Refresh packages, and produces independent forensic reports. Each public MMD/DOT pair is the engine&apos;s complete lane topology; the 8K PNG and lossless SVG are rendered from that exact MMD rather than from a generic four-file overview. Inspect and download the published artifacts on <Link href="/proof#dummy-lane-proofs">Proof</Link>. The separate post-acceptance real-Git test remains bound to the main Evidence Lane repository&apos;s full reachable history.</p>
      <h2>Proof law</h2>
      <ul>{proofRules.map(([name, detail]) => <li key={name}><strong>{name}:</strong> {detail}</li>)}</ul>
      <h2>Codex boundary</h2>
      <p><strong>Codex</strong> installs from exact Git source, runs the complete local lifecycle, and may project the canonical Plan Lane into Codex Plan mode, Goal, and its native task panel. The installed package exposes all 17 skills and exactly 83 native actions: 26 reads and 57 writes. The public website is documentation only and cannot substitute for native package, catalog, restart, test, CI, or HIL proof.</p>
      <p>Continue with <Link href="/architecture">Architecture</Link>, inspect <Link href="/lanes">all lane contracts</Link>, download <Link href="/proof#dummy-lane-proofs">dummy lane proofs</Link>, read <Link href="/security">Security</Link>, or review <Link href="/provenance#upstream-reference-ledger">Upstream provenance</Link>.</p>
    </main>
  );
}
