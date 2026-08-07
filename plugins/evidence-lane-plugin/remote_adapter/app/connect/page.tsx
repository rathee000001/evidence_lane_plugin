import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { PageHero } from "../_components/page-hero";
import { ReleaseStatus } from "../_components/release-status";
import { publicMcpHealthUrl, publicMcpUrl, publicSiteUrl } from "../_data/site";

export const metadata: Metadata = {
  title: "Connect",
  description: "Codex Git installation and ChatGPT durable MCP connection boundaries for Evidence Lane.",
};

export default function ConnectPage() {
  return (
    <main>
      <PageHero
        eyebrow="Installation and connection"
        title="Git for Codex. A durable MCP edge for ChatGPT."
        description="These are different host capabilities. Vercel is used only for the public website and ChatGPT's remotely reachable MCP edge—not as Evidence Lane's general router or local state authority."
        aside={<ReleaseStatus compact />}
      />
      <section className="section shell installCompare">
        <article className="installPath codexPath">
          <span className="pathNumber">01</span><span className="universal-pill statusGlassPill" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"><GlassIconOrb color="#69d9f5" size={28} decorative><OfficialToolIcon tool="terminal" size={15} decorative /></GlassIconOrb><span>Codex</span></span>
          <h2>Install the plugin from an exact Git SHA.</h2>
          <p>Codex can load the plugin's skills, commands, hooks, and local MCP components from the governed Git marketplace route. The durable local host owns SQLite and pointer state.</p>
          <ol><li>Push and verify the governed Git commit.</li><li>Update the marketplace reference with a cachebuster.</li><li>Install and validate the exact SHA in a fresh Codex runtime.</li><li>Remove an older duplicate only after the replacement is proven.</li></ol>
          <strong className="pathBoundary">Vercel is not required for this path.</strong>
        </article>
        <article className="installPath chatgptPath">
          <span className="pathNumber">02</span><span className="universal-pill statusGlassPill" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"><GlassIconOrb color="#efca72" size={28} decorative><OfficialToolIcon tool="database" size={15} decorative /></GlassIconOrb><span>ChatGPT</span></span>
          <h2>Use ChatGPT&apos;s own persistent plugin runtime or a durable HTTPS MCP origin.</h2>
          <p>When the ChatGPT host exposes mounted durable storage, the same plugin reads and append-writes its PV, lane, ENV, lineage, and Exit-Slip authorities there. A public connector instead uses the Vercel protocol edge and one separately configured durable service. Neither route borrows Codex&apos;s native Plan, Goal, or task-panel UI.</p>
          <ol><li>Deploy the same Git SHA to the Vercel project.</li><li>Configure durable auth, storage, queue, and HTTPS origin.</li><li>Verify <code>/healthz</code> and MCP protocol behavior.</li><li>Install or update the ChatGPT connector, then prove read/write/readback.</li></ol>
          <strong className="pathBoundary">The Vercel filesystem is never accepted state authority.</strong>
        </article>
      </section>
      <section className="section shell provenanceBoundary">
        <span className="kicker">Two host universes, one lifecycle law</span>
        <h2>Codex UI controls are not ChatGPT plugin capabilities.</h2>
        <p>Codex may pair <code>/pl</code>, <code>/evi-plan</code>, its Goal, and its visible task panel with the canonical Plan Lane. ChatGPT keeps the same append-only source lanes, persistent plugin state, ENV/UOP verification, PVs, Exit Slips, candidates, and HIL authority in its own storage boundary, but it does not pretend those Codex composer controls exist.</p>
      </section>
      <section className="section connectionBand">
        <div className="shell connectionGrid">
          <div><span className="kicker">Canonical public surfaces</span><h2>A browser page and an MCP endpoint are different surfaces.</h2><p>The website address is <a href={publicSiteUrl}>{publicSiteUrl}</a>. ChatGPT connects to <code>{publicMcpUrl}</code>. Opening that MCP route in a normal browser is not a valid protocol session; it expects an MCP client, authentication, exact release identity, and a configured durable origin.</p></div>
          <div className="endpointCards">
            <a href={publicSiteUrl} className="endpointCard"><span>GET</span><code>{publicSiteUrl}</code><strong>Public website</strong><small>Human-readable canonical product and policy surface</small></a>
            <a href={publicMcpHealthUrl} className="endpointCard"><span>GET</span><code>{publicMcpHealthUrl}</code><strong>Configuration health</strong><small>Ready or explicit fail-closed JSON</small></a>
            <div className="endpointCard"><span>MCP</span><code>{publicMcpUrl}</code><strong>ChatGPT protocol endpoint</strong><small>Use a compatible authenticated MCP client</small></div>
          </div>
        </div>
      </section>
      <section className="section shell connectorSlots" data-mcp-apps="SUPPORTED">
        <div className="sectionHead"><span className="kicker">Comparable ChatGPT presentation</span><h2>Rich metadata, complete tool contracts, verified links, and supported in-chat panels.</h2><p>Evidence Lane supplies its logo, descriptions, website and legal links, annotated MCP tools, structured results, and a portable MCP Apps resource. ChatGPT owns the surrounding listing and settings layout, so the product provides equivalent governed information without claiming control of the host UI.</p></div>
        <div className="slotGrid">
          <div><span>UI</span><strong>Governed console</strong><small><code>ui://evidence-lane/governed-console-v1.html</code></small></div>
          <div><span>01</span><strong>Runtime and lanes panel</strong><small><code>render_runtime_panel</code></small></div>
          <div><span>02</span><strong>Project and HIL panel</strong><small><code>render_project_panel</code></small></div>
          <div><span>DATA</span><strong>No-UI tools remain usable</strong><small>Rendering is decoupled from governed data operations</small></div>
        </div>
      </section>
      <section className="section shell connectorSlots">
        <div className="sectionHead"><span className="kicker">Bounded extension</span><h2>Up to eight persistent connector or AI-toolchain sidecars.</h2><p>Each slot records its host, purpose, role, lane/schema influence, capability boundary, and revocation receipt. A connector may supply evidence; it cannot change lifecycle state.</p></div>
        <div className="slotGrid">{Array.from({ length: 8 }, (_, index) => <div key={index}><span>{String(index + 1).padStart(2, "0")}</span><strong>Available governed slot</strong><small>Purpose and schema must be explicit</small></div>)}</div>
      </section>
    </main>
  );
}
