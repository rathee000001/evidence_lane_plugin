import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";
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
        title="Git for Codex. A governed action surface for ChatGPT."
        description="These are different host capabilities. ChatGPT executes accepted-evidence reads while lifecycle-write controls stay visible and fail closed; Codex keeps the full Git-backed lifecycle and accepted-state authority."
        aside={<HeroOrbit preset="connect" />}
      />
      <section className="section shell installCompare">
        <article className="installPath codexPath">
          <span className="pathNumber">01</span><span className="compactDepthPill"><GlassIconOrb color="#69d9f5" size={28} decorative><OfficialToolIcon tool="terminal" size={15} decorative /></GlassIconOrb><span>Codex</span></span>
          <h2>Install the plugin from an exact Git SHA.</h2>
          <p>Codex can load the plugin's skills, commands, hooks, and local MCP components from the governed Git marketplace route. The durable local host owns SQLite and pointer state.</p>
          <ol><li>Push and verify the governed Git commit.</li><li>Update the marketplace reference with a cachebuster.</li><li>Install and validate the exact SHA in a fresh Codex runtime.</li><li>Remove an older duplicate only after the replacement is proven.</li></ol>
          <strong className="pathBoundary">Vercel is not required for this path.</strong>
        </article>
        <article className="installPath chatgptPath">
          <span className="pathNumber">02</span><span className="compactDepthPill"><GlassIconOrb color="#efca72" size={28} decorative><OfficialToolIcon tool="database" size={15} decorative /></GlassIconOrb><span>ChatGPT</span></span>
          <h2>Install the full plugin, then connect its governed ChatGPT Pro profile.</h2>
          <p>The Evidence Lane package contributes all 15 governed skill entries to ChatGPT. Its registered MCP app exposes the complete 62-action catalog: 21 accepted-PV, ENV/UOP Flash, Entry/Exit, lane, search, diff, backlog, and panel operations execute as reads; 41 lifecycle-write actions remain visible but return a no-mutation fail-closed receipt before service invocation. ChatGPT&apos;s native project memory may continue its own append-only ENV/UOP and Project Mutation workflow, but the MCP does not perform or claim that write.</p>
          <ol><li>Use the contributor installer with one Runtime key and one Tunnel ID, or deploy the same Git SHA behind the owned durable HTTPS edge.</li><li>Register the MCP app once and map its technical ID into the full Evidence Lane plugin package.</li><li>Verify the stable Evidence Lane name, exact 1.5.0 metadata, branded icon, all 15 packaged skill entries, 62 visible actions, the 21-read/41-fail-closed split, and <code>/healthz</code>.</li><li>Prove accepted-PV readback and one no-mutation write refusal while Codex keeps its separate exact-Git full-lifecycle path.</li></ol>
          <strong className="pathBoundary">The Vercel filesystem is never accepted state authority.</strong>
        </article>
      </section>
      <section className="section shell provenanceBoundary">
        <span className="kicker">Two host universes, one lifecycle law</span>
        <h2>The same plugin skills meet different host authorities.</h2>
        <p>Codex may pair <code>/pl</code>, <code>/evi-plan</code>, its Goal, and its visible task panel with the canonical Plan Lane. The Codex 1.5.0 package exposes all 15 governed skills and 62 native actions. ChatGPT Pro can use the 21 read-safe Boot/Flash, accepted-PV, Entry/Exit, search, and panel actions; its 41 lifecycle writes remain visible only as explicit fail-closed capabilities and cannot invoke service mutation. The current registered ChatGPT page has not yet proven the 15-skill 1.5.0 presentation, so the site does not claim it has. ChatGPT can continue its native ENV/UOP package and Project Mutation sector without pretending Codex composer controls or Git lifecycle authority exist there.</p>
        <div className="hostUniverseMap" aria-label="Codex and ChatGPT host architecture">
          <article>
            <span>Codex universe</span>
            <h3>Full local lifecycle</h3>
            <p>Git source + local plugin runtime + project SQLite/PVs + native Plan/Goal/task panel.</p>
            <code>source → lanes → candidate → HIL → Fuse</code>
          </article>
          <div aria-hidden="true"><strong>same code</strong><span>same append-only law</span><small>separate storage</small></div>
          <article>
            <span>ChatGPT universe</span>
            <h3>Governed MCP action surface</h3>
            <p>Twenty-one operations read accepted evidence. Forty-one lifecycle writes remain visible and return an explicit no-mutation refusal. Native ENV/UOP and Project Mutation remain separately owned by ChatGPT.</p>
            <code>accepted PV → verified reads → ChatGPT guidance</code>
          </article>
        </div>
        <p className="hostBoundaryNote"><strong>No cross-host shortcut:</strong> neither host reads the other host&apos;s live SQLite, pointer, task panel, or unsealed work. State Travel carries verified resume evidence; it does not merge the two universes.</p>
      </section>
      <section className="section connectionBand">
        <div className="shell connectionGrid">
          <div><span className="kicker">Canonical public surfaces</span><h2>A browser page and an MCP endpoint are different surfaces.</h2><p>The website address is <a href={publicSiteUrl}>{publicSiteUrl}</a>. ChatGPT connects to <code>{publicMcpUrl}</code>. Opening that MCP route in a normal browser is not a valid protocol session; it expects an MCP client, authentication, exact release identity, and a configured durable origin.</p></div>
          <div className="endpointCards">
            <a href={publicSiteUrl} className="endpointCard endpointCardReady"><span>GET</span><code>{publicSiteUrl}</code><strong>Public website · live</strong><small>Open the human-readable canonical product and policy surface</small></a>
            <a href={publicMcpHealthUrl} className="endpointCard endpointCardBlocked"><span>GET</span><code>{publicMcpHealthUrl}</code><strong>Configuration health · fail-closed</strong><small>Currently reports the missing durable HTTPS origin and exact release identity; this is an honest blocker, not readiness</small></a>
            <a href={publicMcpUrl} className="endpointCard endpointCardProtocol"><span>MCP</span><code>{publicMcpUrl}</code><strong>ChatGPT protocol endpoint · client only</strong><small>Clickable for exact-address inspection, but a browser tab is not an authenticated MCP session</small></a>
          </div>
        </div>
      </section>
      <section className="section shell connectorReality">
        <span className="kicker">Current public connector truth</span>
        <h2>The domain is connected. The durable public MCP runtime is not closed yet.</h2>
        <p><code>evidencelane.org</code> and <code>mcp.evidencelane.org</code> resolve through Vercel. The MCP edge must remain fail-closed until the exact deployed release identity and durable origin pass protocol verification. The contributor OpenAI tunnel is a separate outbound transport for local testing and onboarding; both routes must expose the same 62-action <code>CHATGPT_PRO_GOVERNED</code> contract with 21 executing reads and 41 visible no-mutation write refusals.</p>
      </section>
      <section className="section shell connectorSlots" data-mcp-apps="SUPPORTED">
        <div className="sectionHead"><span className="kicker">Comparable ChatGPT presentation</span><h2>Rich metadata, complete tool contracts, verified links, and supported in-chat panels.</h2><p>Evidence Lane supplies its logo, descriptions, website and legal links, annotated MCP tools, structured results, and a portable MCP Apps resource. ChatGPT owns the surrounding listing and settings layout, so the product provides equivalent governed information without claiming control of the host UI.</p></div>
        <div className="slotGrid">
          <div><span>UI</span><strong>Governed console</strong><small><code>ui://evidence-lane/governed-console-v3.html</code></small></div>
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
