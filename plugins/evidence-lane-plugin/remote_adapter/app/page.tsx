import Link from "next/link";

import { DeltaLedgerExplorer } from "./_components/delta-ledger-explorer";
import { GlassIconOrb, OfficialToolIcon } from "./_components/evidence-assets";
import { HeroOrbit } from "./_components/hero-orbit";
import { MotionReveal } from "./_components/motion-reveal";
import { PluginSurfaceCatalog } from "./_components/plugin-surface-catalog";
import { artifactContract, painLedger, proofMetrics } from "./_data/site";
import { deltaLedgerBoundary } from "./_data/delta-ledger";

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
      <section className="homeHero orbitHeroFrame shell">
        <MotionReveal className="heroCopy">
          <span className="eyebrow"><i />Evidence Lane 2.0 for Codex</span>
          <h1>Resume from verified project truth - not another re-explanation.</h1>
          <p>
            The first governed PV parses and seals the bounded project. Later tasks query its
            SQLite evidence, reuse unchanged chunks, and Refresh only visible Deltas. Accepted
            pointers, PVs, Exit Slips, Chat Lineage, candidates, and HIL keep AI work moving from
            the last verified state while only the human can accept or redirect it.
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
          <HeroOrbit preset="home" />
        </MotionReveal>
      </section>

      <section className="principleBand">
        <div className="shell principleGrid">
          <div><strong>No re-explanation tax</strong><span>Resume from the accepted pointer, pending candidate, and exact open gate</span></div>
          <div><strong>Parse once, query again</strong><span>Search SQLite facts and reuse unchanged chunks instead of rebuilding context</span></div>
          <div><strong>Human / AI boundary</strong><span>Visible lineage and six-way HIL keep direction, output, and acceptance distinct</span></div>
          <div><strong>Directed operating modes</strong><span>Analysis, plan, code, and other modes bind their own ENV/UOP operators</span></div>
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
          <h2>AI work becomes expensive when every new task must reconstruct the project.</h2>
          <p>
            Re-explaining the goal, re-reading unchanged files, and re-parsing the same sources
            consume time while context drifts. A prose handoff cannot prove which bytes were
            accepted, what the human directed, what the AI produced, what changed afterward,
            which checks ran, or which decision remains unresolved.
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
          <span className="kicker">Codex capability profiles</span>
          <h2>The same governance law meets each Codex runtime at its real storage boundary.</h2>
          <p>
            The installed package exposes all 15 governed skills and the complete 62-action native
            catalog: 21 reads and 41 writes. Desktop and persistent profiles use durable local
            SQLite. Headless API entry reflashes ENV/UOP for each invocation. Ephemeral profiles
            require a durable mount or configured transactional connector. No profile can infer
            acceptance, Fuse, or pointer movement from installation or execution success.
          </p>
          <Link className="textLink" href="/connect">Inspect the verified host and connection boundaries <span aria-hidden="true">→</span></Link>
        </div>
        <div className="homeHostTruth" aria-label="Evidence Lane host boundaries">
          <article><span>Persistent Codex</span><strong>Full native lifecycle</strong><p>Source Intake, durable SQLite, tests, package seals, exact six-way HIL, and pointer-gated promotion.</p></article>
          <article><span>Headless or ephemeral Codex</span><strong>Same laws, explicit storage</strong><p>Per-entry ENV/UOP verification plus a durable mount or configured transactional runtime when local persistence is unavailable.</p></article>
        </div>
      </section>

      <section className="section deltaLedgerBand" id="delta-ledger">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">Historical Deltas + current Plan Lane</span>
            <h2>One additive ledger. {deltaLedgerBoundary.totalRows} governed public rows. No erased history.</h2>
            <p>
              Rows 1&ndash;80 preserve the sealed foundation, v1.2 evolution, and v1.3 hardening
              record byte-for-byte. Current execution begins only after row 80 and now runs consecutively
              from public row 081 through 196: {deltaLedgerBoundary.liveExecutionRows} full, unabridged rows,
              with {deltaLedgerBoundary.currentExecutionCompleted} completed,
              {` ${deltaLedgerBoundary.currentExecutionActive}`} active, and
              {` ${deltaLedgerBoundary.currentExecutionPending}`} pending. Public row
              {` ${deltaLedgerBoundary.activePublicOrder}`} / public task position
              {` ${deltaLedgerBoundary.activeTaskPosition}`} / governed receipt position
              {` ${deltaLedgerBoundary.activeReceiptPosition}`} is the sole active row; row
              {` ${deltaLedgerBoundary.finalSweepPublicOrder}`} is the final fresh sweep and row
              {` ${deltaLedgerBoundary.finalHilPublicOrder}`} is the physically final six-way HIL.
              Filters change only the view, never the text, order, or authority.
            </p>
          </div>
          <DeltaLedgerExplorer />
        </div>
      </section>
    </main>
  );
}
