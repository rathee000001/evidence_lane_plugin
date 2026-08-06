import Link from "next/link";

import { DeltaLedgerExplorer } from "./_components/delta-ledger-explorer";
import { GlassIconOrb, OfficialToolIcon, PulsatingBrain } from "./_components/evidence-assets";
import { MotionReveal } from "./_components/motion-reveal";
import { PluginSurfaceCatalog } from "./_components/plugin-surface-catalog";
import { ReleaseStatus } from "./_components/release-status";
import { artifactContract, painLedger, proofMetrics } from "./_data/site";

const routes = [
  ["Architecture", "See how parallel lane computation meets serial lifecycle authority.", "/architecture", "01"],
  ["18 lanes", "Open each lane's tools, settings, SQLite schema, process, and four files.", "/lanes", "02"],
  ["Operators", "Inspect mode formulas, ENV/UOP laws, operators, and lane-specific HIL effects.", "/operators", "03"],
  ["Prompt Studio", "Ask grounded product questions and see the evidence boundary in the answer.", "/studio", "04"],
  ["Proof", "Separate verified behavior, historical evidence, open blockers, and candidate claims.", "/proof", "05"],
  ["Provenance", "Audit source roles and credits without confusing them with the product itself.", "/provenance", "06"],
] as const;

export default function Home() {
  return (
    <main>
      <section className="homeHero shell">
        <MotionReveal className="heroCopy">
          <span className="eyebrow"><i />Evidence Lane for Codex + ChatGPT</span>
          <h1>Resume AI work from evidence—not reconstructed memory.</h1>
          <p>
            Evidence Lane carries the exact source, Git identity, searchable lane databases,
            visible corrections, accepted pointer, candidate, receipts, and unresolved human
            decision across task windows. The model can keep working; only the human can accept.
          </p>
          <div className="actions">
            <Link className="primary universal-pill actionGlassPill" href="#delta-ledger" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001">
              <GlassIconOrb color="#69d9f5" size={30} decorative><OfficialToolIcon tool="pulse" size={16} decorative /></GlassIconOrb>
              <span>Read the complete Delta story</span>
            </Link>
            <Link className="secondary universal-pill actionGlassPill" href="/lanes" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001">
              <GlassIconOrb color="#83ddb3" size={30} decorative><OfficialToolIcon tool="database" size={16} decorative /></GlassIconOrb>
              <span>Inspect all 18 lanes</span>
            </Link>
          </div>
        </MotionReveal>
        <MotionReveal className="heroVisual" delay={0.12}>
          <div className="heroBrainStage">
            <PulsatingBrain size="min(610px, 88vw)" color="#69d9f5" />
            <span>Exact state in · governed evidence out</span>
          </div>
          <div className="visualBadge badgeA"><span>18</span> source lanes</div>
          <div className="visualBadge badgeB"><span>15</span> plugin surfaces</div>
          <div className="visualBadge badgeC"><span>1</span> human gate</div>
        </MotionReveal>
      </section>

      <section className="principleBand">
        <div className="shell principleGrid">
          <div><strong>Exact continuity</strong><span>Accepted pointer, source hashes, candidate, and open decision</span></div>
          <div><strong>Inspectable memory</strong><span>SQLite, Mermaid, DOT, and receipt per lane</span></div>
          <div><strong>Human authority</strong><span>Six-way HIL; tests never become approval</span></div>
          <div><strong>Host-aware runtime</strong><span>Durable local or mounted MCP authority by host class</span></div>
        </div>
      </section>

      <section className="metricReveal shell" aria-label="Verified contract counts">
        {proofMetrics.map(([value, label, detail]) => (
          <article key={label}>
            <strong>{value}</strong>
            <div><span>{label}</span><p>{detail}</p></div>
          </article>
        ))}
      </section>

      <section className="section shell splitIntro" id="problem">
        <div className="sectionHead stickyCopy">
          <span className="kicker">The product problem</span>
          <h2>AI can produce useful work and still lose the state that makes it trustworthy.</h2>
          <p>
            Long-running work crosses models, tools, task windows, machines, and deployments.
            A prose handoff cannot prove which bytes were accepted, what changed afterward,
            which checks actually ran, or which human decision is still unresolved.
          </p>
          <Link className="textLink" href="/architecture">See the authority model <span aria-hidden="true">→</span></Link>
        </div>
        <div className="problemStack">
          {painLedger.map((item, index) => (
            <article className="problemCard" key={item.title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h3>{item.title}</h3>
                <p>{item.observation}</p>
                <dl>
                  <div><dt>Failure</dt><dd>{item.failure}</dd></div>
                  <div><dt>Evidence Lane</dt><dd>{item.response}</dd></div>
                </dl>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="section deltaLedgerBand" id="delta-ledger">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">The complete build story</span>
            <h2>Eighty additive Deltas. No erased history.</h2>
            <p>
              The ledger is the product narrative: foundation, v1.2 evolution, and v1.3
              hardening in exact governed order. Filters change the view, never the underlying rows.
            </p>
          </div>
          <DeltaLedgerExplorer />
        </div>
      </section>

      <section className="section solutionBand" id="plugin-surfaces">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">Codex plugin settings</span>
            <h2>Fifteen interactive surfaces. Six are lifecycle controls.</h2>
            <p>
              Choose any glass pill to inspect what it does, which setting governs it, what it
              produces, and what it cannot authorize. The remaining nine surfaces are explicit
              routers or sidecars—not hidden extra lifecycle commands.
            </p>
          </div>
          <PluginSurfaceCatalog />
        </div>
      </section>

      <section className="section shell evidenceContract">
        <div>
          <span className="kicker">Openable by design</span>
          <h2>Every lane emits four files you can inspect without a proprietary viewer.</h2>
          <p className="sectionLead">
            The SQLite facts must reconcile with both topology formats. The receipt binds source,
            parser, refresh, actor, tool, test, hash, pointer, and candidate evidence.
          </p>
          <div className="fourFileRail" aria-label="Four inspectable files">
            {artifactContract.map(([title, text], index) => (
              <article key={title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{title}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
          <Link className="textLink" href="/lanes">Open the lane toolchain, settings, and schemas <span aria-hidden="true">→</span></Link>
        </div>
      </section>

      <section className="section routeSection shell">
        <div className="sectionHead wideHead">
          <span className="kicker">Inspect the system</span>
          <h2>Follow the evidence, not a feature collage.</h2>
        </div>
        <div className="routeGrid">
          {routes.map(([title, text, href, number]) => (
            <Link className="routeCard" href={href} key={href}>
              <span>{number}</span><h3>{title}</h3><p>{text}</p><b aria-hidden="true">↗</b>
            </Link>
          ))}
        </div>
      </section>

      <section className="section shell provenanceBoundary">
        <span className="kicker">Provenance, not product hierarchy</span>
        <h2>Historical brains and reference repositories remain credited evidence.</h2>
        <p>
          Their audited ideas, refusals, licenses, and identity boundaries remain available on
          the provenance page and in the POC. They do not replace Evidence Lane, dominate this
          product story, or become current runtime authority by inclusion.
        </p>
        <Link className="textLink" href="/provenance">Inspect source roles and credits <span aria-hidden="true">→</span></Link>
      </section>

      <section className="section shell releaseHome">
        <div>
          <span className="kicker">Live boundary</span>
          <h2>Website health and connector identity are different proofs.</h2>
          <p>
            Codex installs the governed Git plugin. ChatGPT reaches the same durable runtime through
            a mounted or local MCP path. Vercel can host this public story and a thin edge, but it is
            not the project brain and never substitutes for exact release readback.
          </p>
        </div>
        <ReleaseStatus />
      </section>
    </main>
  );
}
