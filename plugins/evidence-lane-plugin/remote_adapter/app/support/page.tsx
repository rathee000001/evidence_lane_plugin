import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Support" };

export default function SupportPage() {
  return (
    <main className="legal shell">
      <Link href="/">← Evidence Lane</Link>
      <span className="kicker">Operational support</span>
      <h1>Support and diagnostics</h1>
      <p>Start with <Link href="/healthz">/healthz</Link>. A fail-closed response is intentional when the durable HTTPS origin, authentication, storage/queue, or exact release SHA is absent or mismatched.</p>
      <h2>Connector endpoint</h2>
      <p>The ChatGPT connector endpoint is <code>/mcp</code>. Opening it as a normal browser page is not a functional MCP test; use an MCP client or inspector with the required authentication and protocol headers.</p>
      <h2>Website routing</h2>
      <p>The public multipage website is served by Next.js. Only <code>/mcp</code>, <code>/healthz</code>, and the OAuth protected-resource metadata route are rewritten to the Python adapter.</p>
      <h2>Release reports</h2>
      <p>Every governed release should provide its Git SHA, Vercel deployment identity when applicable, package hashes, install evidence, test evidence, remaining blockers, and an unaccepted HIL candidate.</p>
    </main>
  );
}
