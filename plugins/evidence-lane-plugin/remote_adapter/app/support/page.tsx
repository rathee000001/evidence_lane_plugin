import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Support" };

export default function SupportPage() {
  return (
    <main className="legal shell">
      <Link href="/">← Evidence Lane</Link>
      <span className="kicker">Operational support</span>
      <h1>Support and diagnostics</h1>
      <p>Start with the exact Git branch and commit, deterministic package receipt, installed version, native 62-tool catalog, and post-restart acceptance receipt.</p>
      <h2>Native plugin route</h2>
      <p>The supported lifecycle route is the installed package-local Evidence Lane MCP inside Codex. This website does not expose a lifecycle endpoint.</p>
      <h2>Website routing</h2>
      <p>The public multipage website is served by Next.js as documentation only. No route is rewritten to a lifecycle server.</p>
      <h2>Release reports</h2>
      <p>Every governed release should provide its Git SHA, package hashes, native install and restart evidence, test and CI evidence, remaining blockers, and an unaccepted HIL candidate.</p>
    </main>
  );
}
