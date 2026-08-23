import Link from "next/link";

import { DeltaLedgerExplorer } from "./_components/delta-ledger-explorer";
import { GlassIconOrb, OfficialToolIcon } from "./_components/evidence-assets";
import { HeroOrbit } from "./_components/hero-orbit";
import { HostCapabilityMatrix } from "./_components/host-capability-matrix";
import { MotionReveal } from "./_components/motion-reveal";
import { PluginSurfaceCatalog } from "./_components/plugin-surface-catalog";
import { authorityPlanes, currentProductContract } from "./_data/current-product-contract";
import { artifactContract, painLedger } from "./_data/site";
import { deltaLedgerBoundary } from "./_data/delta-ledger";

const routes = [
  ["Memory", "See durable SQLite memory, bounded retrieval, and compaction continuity.", "/memory", "01"],
  ["Canon", "Inspect bounded task exchange and receiver-owned Canon decisions.", "/canon", "02"],
  ["AI Learning", "See project-isolated learning, decisions, and revocation.", "/ai-learning", "03"],
  ["Skills", `Inspect all ${currentProductContract.governedSkillCount} governed skills and their authority boundaries.`, "/skills", "04"],
  ["Native MCP", `See the package-local server and its ${currentProductContract.nativeMcp.totalActions} governed action contracts.`, "/mcp", "05"],
  ["Hooks", `Inspect all ${currentProductContract.hookEventCount} lifecycle events and their transport-only boundary.`, "/hooks", "06"],
  ["Plan & Changes", "See the canonical ledger, active window, Goal, and worktree binding.", "/plan", "07"],
  ["Git & CI", "Bind source intake, GitHub checks, and Vercel preview to one commit.", "/git-ci", "08"],
  ["Architecture", "See how parallel lane computation meets serial lifecycle authority.", "/architecture", "09"],
  [`${currentProductContract.canonicalLaneCount} lanes`, "Open each lane's tools, settings, SQLite schema, process, and four files.", "/lanes", "10"],
  ["Operators", "Inspect mode formulas, ENV/UOP laws, operators, and lane-specific HIL effects.", "/operators", "11"],
  ["Prompt Studio", "Ask grounded product questions and see the evidence boundary in the answer.", "/studio", "12"],
  ["Proof", "Separate verified behavior, open blockers, and candidate claims.", "/proof", "13"],
  ["Provenance", "Audit source roles and credits without confusing them with runtime authority.", "/provenance", "14"],
  ["Release", "Inspect local testing, branch fallback, main release, and promotion gates.", "/release", "15"],
] as const;

const proofMetrics = [
  [String(currentProductContract.canonicalLaneCount), "canonical lanes", "Every routed source resolves to one inspectable lane contract."],
  [String(currentProductContract.governedSkillCount), "governed skills", `${currentProductContract.primaryControlCount} primary controls plus bounded routers and sidecars.`],
  [String(currentProductContract.nativeMcp.totalActions), "native actions", `${currentProductContract.nativeMcp.readActions} read-only and ${currentProductContract.nativeMcp.writeActions} write-capable routes, registry checked.`],
  [String(currentProductContract.hookEventCount), "optional hook events", "Lifecycle automation stays separate from the explicit public plugin surface."],
] as const;

export default function Home() {
  return (
    <main>
      <section className="homeHero orbitHeroFrame shell">
        <MotionReveal className="heroCopy">
          <span className="eyebrow"><i />Evidence Lane 3.0 for Codex</span>
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
              <span>Inspect all {currentProductContract.canonicalLaneCount} lanes</span>
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

      <section className="section shell authorityStory" id="authority-planes">
        <div className="sectionHead wideHead">
          <span className="kicker">{authorityPlanes.length} explicit authority planes</span>
          <h2>Evidence Lane preserves differences that a normal handoff collapses.</h2>
          <p>
            Each plane has a distinct owner and transition law. Memory and Project Universe
            connect the system for bounded retrieval; neither becomes a shortcut around source,
            Plan, candidate, Learning, Canon, or human acceptance.
          </p>
        </div>
        <div className="authorityPlaneGrid">
          {authorityPlanes.map(([name, detail], index) => (
            <article key={name}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h3>{name}</h3>
              <p>{detail}</p>
            </article>
          ))}
        </div>
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
            <h2>{currentProductContract.governedSkillCount} interactive surfaces. {currentProductContract.primaryControlCount} are lifecycle controls.</h2>
            <p>
              Choose any glass pill to inspect what it does, which setting governs it, what it
              produces, and what it cannot authorize. The remaining
              {` ${currentProductContract.governedSkillCount - currentProductContract.primaryControlCount}`} surfaces are explicit
              routers or sidecars—not hidden extra lifecycle commands. Every explicit skill,
              command, SDK, and MCP route remains usable while hooks are disabled; hooks only
              automate or observe supported host events.
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
        <span className="kicker">Current v3.0 provenance</span>
        <h2>Every public claim stays linked to current source and its evidence boundary.</h2>
        <p>
          The provenance page records the exact source identity, attributed contributors,
          licenses, generated artifacts, and limits that support this release. A reference,
          provider, preview, or generated page never becomes runtime or HIL authority.
        </p>
        <Link className="textLink" href="/provenance">Inspect source roles and credits <span aria-hidden="true">→</span></Link>
      </section>

      <section className="section shell releaseHome">
        <div>
          <span className="kicker">Codex capability profiles</span>
          <h2>The same governance law meets each Codex runtime at its real storage boundary.</h2>
          <p>
            The source registry projects {currentProductContract.governedSkillCount} governed
            skills and a {currentProductContract.nativeMcp.totalActions}-action native catalog:
            {` ${currentProductContract.nativeMcp.readActions}`} reads and
            {` ${currentProductContract.nativeMcp.writeActions}`} writes. Storage, interaction,
            VM lifetime, native capability, billing, tunnel transport, and credentials remain
            independent. No profile can infer acceptance, Fuse, or pointer movement from a
            successful install, route, build, test, or deployment.
          </p>
          <Link className="textLink" href="/connect">Inspect the verified host and connection boundaries <span aria-hidden="true">→</span></Link>
        </div>
        <HostCapabilityMatrix compact />
      </section>

      <section className="section deltaLedgerBand" id="delta-ledger">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">Historical Deltas + current Plan Lane</span>
            <h2>One additive ledger. {deltaLedgerBoundary.totalRows} governed public rows. No erased history.</h2>
            <p>
              Rows 1&ndash;80 preserve the sealed foundation, v1.2 evolution, and v1.3 hardening
              record byte-for-byte. Current execution begins only after row 80 and now runs consecutively
              from public row {deltaLedgerBoundary.rowStart} through {deltaLedgerBoundary.rowEnd}: {deltaLedgerBoundary.liveExecutionRows} full, unabridged rows,
              with {deltaLedgerBoundary.currentExecutionCompleted} completed,
              {` ${deltaLedgerBoundary.currentExecutionActive}`} active, and
              {` ${deltaLedgerBoundary.currentExecutionPending}`} pending. Public row
              {` ${deltaLedgerBoundary.activePublicOrder}`} / {deltaLedgerBoundary.activeTaskId} / public task position
              {` ${deltaLedgerBoundary.activeTaskPosition}`} is the sole active row; row
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
