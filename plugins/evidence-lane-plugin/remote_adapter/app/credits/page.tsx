import type { Metadata } from "next";
import Link from "next/link";

import { humanContributions } from "../_data/contributors";
import { upstreamReferences } from "../_data/upstream-references";

export const metadata: Metadata = { title: "Credits and contributions" };

export default function CreditsPage() {
  return (
    <main className="legal shell creditsPage">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="kicker">Attributable review record</span>
      <h1>Credits and contributions</h1>
      <p>Evidence Lane is conceived, directed, funded, and owned by Praveen Rathee. The roles below record attributable review and evaluation; they do not imply source authorship, ownership transfer, candidate acceptance, or release authority.</p>
      <h2>Human contributions</h2>
      <ul className="creditList">
        {humanContributions.map((person) => (
          <li key={person.name}>
            <Link href={person.href}>{person.name}</Link>
            <span>{person.role}</span>
          </li>
        ))}
      </ul>
      <h2>AI and implementation toolchain</h2>
      <p>OpenAI ChatGPT supported adversarial analysis, research, red/blue-team critique, and product reasoning. OpenAI Codex and GPT-5.6 supported human-directed implementation, debugging, test execution, and evidence review. Google Gemini supported reasoning and exploratory discussion. Anthropic Claude supplied separate comparative context; no Claude/Fable source is incorporated by reference.</p>
      <p>GitHub and Git provide source history and reviewed distribution. Python, SQLite, MCP, Vercel, LlamaIndex where explicitly declared, and the versioned dependencies provide the implementation and retrieval toolchain. Each third-party project remains governed by its own license and trademarks.</p>
      <h2>Public upstream reference ledger</h2>
      <p>These exact Git identities were inspected read-only. Each card distinguishes the research role from the local implementation boundary; listing a repository is not a claim that its code was copied or that Evidence Lane has feature parity.</p>
      <div className="upstreamLedger">
        {upstreamReferences.map((source) => (
          <article key={source.repository}>
            <div className="upstreamLedgerHead">
              <div>
                <span>{source.category}</span>
                <h3>{source.name}</h3>
              </div>
              <Link href={source.repository}>Repository</Link>
            </div>
            <dl>
              <div><dt>Commit</dt><dd><code>{source.commit}</code></dd></div>
              <div><dt>Tree</dt><dd><code>{source.tree}</code></dd></div>
              <div><dt>License</dt><dd>{source.license}</dd></div>
              <div><dt>Role</dt><dd>{source.role}</dd></div>
              <div><dt>Boundary</dt><dd>{source.boundary}</dd></div>
              <div><dt>Verdict</dt><dd>{source.verdict}</dd></div>
            </dl>
          </article>
        ))}
      </div>
      <p>OpenAI and GitHub official documentation supplied host, plugin, skill, Actions, CodeQL, and coding-agent configuration research. Documentation and services are credited as references, not imported source. The repository keeps the complete intake and implementation mapping in its <Link href="https://github.com/rathee000001/evidence_lane_plugin/blob/main/docs/UPSTREAM_REFERENCE_PROVENANCE.md">upstream provenance ledger</Link>.</p>
      <h2>Governance boundary</h2>
      <p>Credit never grants Git push, plugin installation, pointer movement, Fuse, main-branch merge, State Travel, or HIL approval. Accepted source contributions still require an attributable record, a rights basis, a reviewable Git Delta, executable evidence, and the project&apos;s human acceptance gate.</p>
    </main>
  );
}
