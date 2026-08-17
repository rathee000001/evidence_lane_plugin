import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Native MCP",
  description: "Evidence Lane's package-local native MCP server and eighty-seven governed actions.",
};

const actionClasses = [
  ["27", "read-only actions", "Inspect accepted pointers, status, Plan, receipts, sources, lanes, Canon, Learning, and governed evidence without lifecycle mutation.", "database"],
  ["60", "write-capable actions", "Mutate only through explicit project/session contracts, exact task identity, one-writer guards, and operation-specific receipts.", "terminal"],
  ["16", "Canon actions", "Three reads and thirteen writes govern linked-task exchange, dispatch, receiver-owned three-way decisions, bounded backfire, results, and continuity.", "git"],
  ["5", "Learning actions", "Two reads and three writes keep project-isolated AI Learning retrieval, candidates, decisions, and revocation separate from Project Truth.", "node"],
  ["1", "native server", "The package-local evidence-lane server is the Codex lifecycle route. Website and remote transport surfaces cannot substitute for it.", "package"],
] as const;

export default function McpPage() {
  return (
    <main>
      <PageHero
        eyebrow="Native MCP"
        title="One package-local server. Eighty-seven governed actions."
        description="Evidence Lane keeps read inspection, write capability, lifecycle gates, and failure receipts explicit. An action being visible never implies that its host or current state authorizes it."
        aside={<HeroOrbit preset="mcp" />}
      />

      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">Action contract</span><h2>Capability is measured at runtime and fails closed.</h2><p>The native catalog remains stable for compatibility, while each call proves its project, session, host, storage, pointer, task, and lifecycle preconditions.</p></div>
        <div className="compareGrid">
          {actionClasses.map(([count, title, detail, icon], index) => (
            <article key={title}>
              <span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#83ddb3", "#a99af7", "#f5b970", "#77c9a5"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{count}</span></span>
              <h3>{title}</h3><p>{detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Transport boundary</span><h2>The website explains the MCP. It never becomes the MCP.</h2><p>A successful page render, preview deployment, or public response cannot prove native plugin pickup, invoke lifecycle state, or move an accepted pointer.</p></div></div></section>
    </main>
  );
}
