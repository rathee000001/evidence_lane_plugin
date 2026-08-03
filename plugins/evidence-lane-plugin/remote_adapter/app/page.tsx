import Image from "next/image";
import Link from "next/link";

import { EvidenceOrbit } from "./_components/evidence-orbit";
import {
  LaneToolchainExplorer,
  UniversalCommandDeck,
} from "./_components/evidence-console";
import { ReleaseStatus } from "./_components/release-status";
import { artifactContract, painLedger, proofMetrics } from "./_data/site";

const routes = [
  ["Architecture", "See how parallel lane computation meets serial lifecycle authority.", "/architecture", "01"],
  ["18 lanes", "Inspect the canonical source registry and the artifact contract for every lane.", "/lanes", "02"],
  ["Proof boundary", "Separate verified behavior, historical context, open blockers, and candidate claims.", "/proof", "03"],
  ["Provenance", "Follow the independent R&D lineage, source boundaries, and toolchain credits.", "/provenance", "04"],
] as const;

export default function Home() {
  return (
    <main>
      <section className="homeHero shell">
        <div className="heroCopy">
          <span className="eyebrow"><i />Controlled AI code intelligence</span>
          <h1>Build an inspectable project brain. Keep acceptance human.</h1>
          <p>
            Evidence Lane turns authorized project sources into searchable SQLite sectors,
            reconciled Mermaid and DOT topology, exact pointers, visible lineage, and rollback
            evidence. It never treats a candidate as accepted truth by implication.
          </p>
          <div className="actions">
            <Link className="primary" href="/architecture">Explore the system</Link>
            <Link className="secondary" href="/proof">Inspect the proof boundary</Link>
          </div>
        </div>
        <div className="heroVisual" role="img" aria-label="Evidence Lane identity with governed project metrics">
          <EvidenceOrbit />
          <div className="visualHalo" />
          <Image
            className="brandHeroLogo"
            src="/evidence-lane-full-logo.png"
            alt="Evidence Lane full logo"
            width={2400}
            height={1792}
            priority
          />
          <div className="visualBadge badgeA"><span>18</span> canonical lanes</div>
          <div className="visualBadge badgeB"><span>1</span> human gate</div>
          <div className="visualBadge badgeC"><span>0</span> silent promotions</div>
        </div>
      </section>

      <section className="principleBand">
        <div className="shell principleGrid">
          <div><strong>Memory first</strong><span>Reusable, queryable, content-addressed evidence</span></div>
          <div><strong>Internet controlled</strong><span>Explicit sources and governed connectors</span></div>
          <div><strong>Human review</strong><span>Candidate truth stops at HIL before Fuse</span></div>
          <div><strong>Change aware</strong><span>Refresh only what changed; retain what did not</span></div>
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

      <section className="section shell splitIntro">
        <div className="sectionHead stickyCopy">
          <span className="kicker">The problem</span>
          <h2>Code exists. Reliable project understanding usually does not.</h2>
          <p>
            The recurring failure is not a lack of summaries. It is the loss of exact state across
            tools and task windows: files, hashes, accepted versions, corrections, and approval gates.
            Evidence Lane treats that gap as an evidence problem.
          </p>
          <Link className="textLink" href="/provenance">Read the original R&amp;D lineage <span aria-hidden="true">→</span></Link>
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
                  <div><dt>Response</dt><dd>{item.response}</dd></div>
                </dl>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="section solutionBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">The operating surface</span>
            <h2>A compact command deck over a strict authority boundary.</h2>
            <p>Hover, focus, click, or use arrow keys to inspect what each public control produces—and what it is forbidden to imply.</p>
          </div>
          <UniversalCommandDeck />
          <div className="conditionalEvent">
            <strong>State Travel</strong>
            <p>A top conditional recovery event used only for an accepted, sealed fresh-host handoff when the user requests it or context is exhausted.</p>
            <span>Not a seventh everyday control</span>
          </div>
        </div>
      </section>

      <section className="section toolchainBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker light">Lane-by-lane toolchain</span>
            <h2>Every source class has a named parser, chunker, retrieval surface, and evidence package.</h2>
            <p>Nothing here is a decorative capability label. The identifiers below come from the same immutable eighteen-lane registry used by the engine.</p>
          </div>
          <LaneToolchainExplorer />
        </div>
      </section>

      <section className="section shell evidenceContract">
        <div className="contractVisual" aria-label="Five-part inspectable lane package">
          <div className="contractBrand">
            <Image src="/evidence-lane-icon.png" alt="" width={1906} height={1906} />
            <span>One lane package</span>
          </div>
          {artifactContract.map(([title], index) => (
            <div className={`artifactPlane artifactPlane${index + 1}`} key={title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{title}</strong>
            </div>
          ))}
          <span className="orbit orbitOne" />
          <span className="orbit orbitTwo" />
        </div>
        <div>
          <span className="kicker">Openable by design</span>
          <h2>Every lane leaves a package you can inspect.</h2>
          <p className="sectionLead">The database is not the only truth surface. Its facts must reconcile with human-readable and machine-readable topology plus exact lifecycle evidence.</p>
          <div className="artifactList">
            {artifactContract.map(([title, text]) => (
              <div key={title}><strong>{title}</strong><p>{text}</p></div>
            ))}
          </div>
          <Link className="textLink" href="/lanes">Open the 18-lane contract <span aria-hidden="true">→</span></Link>
        </div>
      </section>

      <section className="section routeSection shell">
        <div className="sectionHead wideHead">
          <span className="kicker">Explore the evidence</span>
          <h2>Follow the system from architecture to claim boundary.</h2>
        </div>
        <div className="routeGrid">
          {routes.map(([title, text, href, number]) => (
            <Link className="routeCard" href={href} key={href}>
              <span>{number}</span><h3>{title}</h3><p>{text}</p><b aria-hidden="true">↗</b>
            </Link>
          ))}
        </div>
      </section>

      <section className="section shell releaseHome">
        <div>
          <span className="kicker">Live boundary</span>
          <h2>The website can be healthy while the connector correctly refuses traffic.</h2>
          <p>Codex installs natively from Git. Vercel hosts the public site and the thin ChatGPT MCP edge; it is not the general router or the local Evidence Lane brain.</p>
        </div>
        <ReleaseStatus />
      </section>
    </main>
  );
}
