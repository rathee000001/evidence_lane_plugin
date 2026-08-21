import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Security",
  description: "Evidence Lane security, secret exclusion, remote-action, and disclosure boundaries.",
};

export default function SecurityPage() {
  return (
    <main className="legal shell repositoryDocument">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="kicker">Security boundary · website edition</span>
      <h1>Fail closed before evidence is trusted.</h1>
      <p>This page renders the public security contract inside the website. A source-controlled SECURITY.md at an exact released commit is supporting evidence; a missing file on <code>main</code> must never be the only public explanation.</p>
      <h2>Secret exclusion happens before indexing</h2>
      <p><code>.env</code> variants, private keys, credential paths, runtime caches, configured secret values, and recognized credential-shaped content are excluded before SQLite, CAS, FTS, graph, history, or PV insertion. Receipts record policy outcomes without copying secret bytes or secret values.</p>
      <h2>Remote actions require exact authority</h2>
      <p>Git pushes, deployment mutations, merges, installation, pointer movement, and Fuse are separate governed actions. A build, passing test, or deployment does not imply HIL approval. Prepared remote actions bind an exact repository, branch, commit, scope, and one-time confirmation.</p>
      <h2>Public MCP remains fail closed until durable</h2>
      <p>The purchased website and MCP domains are configured, but a public connector is not operational merely because DNS and Vercel routing exist. Readiness requires a Vercel-reachable durable HTTPS origin, exact release identity, authentication, bounded storage and queue behavior, and successful protocol verification. The local OpenAI tunnel is a separate stdio/control-plane path and is not substituted as that public origin.</p>
      <h2>Third-party boundary</h2>
      <p>Upstream projects, services, models, assets, and trademarks retain their own terms and rights. Evidence Lane records adopted contracts, bounded research roles, and refusals on the <Link href="/provenance#upstream-reference-ledger">Upstream provenance</Link> page.</p>
      <h2>Reporting</h2>
      <p>Do not place credentials, private source, or exploit payloads in public issues. Use the repository owner&apos;s private contact channel for sensitive disclosure and include the affected commit, surface, observed behavior, and a minimal reproducible case.</p>
    </main>
  );
}
