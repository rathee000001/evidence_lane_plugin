import Image from "next/image";
import Link from "next/link";

import { ReleaseStatus } from "./_components/release-status";
import { artifactContract, controls } from "./_data/site";

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
        <div className="heroVisual" role="img" aria-label="Evidence flowing into a governed project brain">
          <div className="visualHalo" />
          <Image
            className="rootFibers"
            src="/evidence-root-fibers.png"
            alt="A network of evidence fibers forming a governed Evidence Lane topology"
            width={1600}
            height={900}
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

      <section className="section shell splitIntro">
        <div className="sectionHead stickyCopy">
          <span className="kicker">The problem</span>
          <h2>Code exists. Reliable project understanding usually does not.</h2>
          <p>
            Knowledge stays in builders&apos; heads, documentation drifts, architecture summaries
            flatten uncertainty, and assistants can invent confidence from incomplete context.
            Evidence Lane treats understanding as a governed evidence system, not another chat transcript.
          </p>
          <Link className="textLink" href="/provenance">Read the original R&amp;D lineage <span aria-hidden="true">→</span></Link>
        </div>
        <div className="problemStack">
          {[
            ["01", "Fragmented context", "Source, history, decisions, tests, and outputs live in different tools with no common provenance."],
            ["02", "Stale explanations", "Static documentation describes yesterday while the repository and operating decisions keep changing."],
            ["03", "Unbounded AI memory", "Convenient summaries can silently mix trusted source, speculation, secrets, and obsolete state."],
            ["04", "Weak acceptance", "A successful build or continued chat is often mistaken for approval even when no human decision was recorded."],
          ].map(([number, title, text]) => (
            <article className="problemCard" key={number}>
              <span>{number}</span><div><h3>{title}</h3><p>{text}</p></div>
            </article>
          ))}
        </div>
      </section>

      <section className="section solutionBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">The operating surface</span>
            <h2>Six public controls. One conditional recovery event.</h2>
            <p>Simple user commands sit above stable internal APIs, exact lifecycle receipts, and a fail-closed authority boundary.</p>
          </div>
          <div className="controlGrid">
            {controls.map((control, index) => (
              <article className="controlCard" key={control.name}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{control.name}</h3>
                <p>{control.detail}</p>
              </article>
            ))}
          </div>
          <div className="conditionalEvent">
            <strong>State Travel</strong>
            <p>A top conditional recovery event used only for an accepted, sealed fresh-host handoff when the user requests it or context is exhausted.</p>
            <span>Not a seventh everyday control</span>
          </div>
        </div>
      </section>

      <section className="section shell evidenceContract">
        <div className="contractVisual">
          <Image src="/evidence-static-brain.png" alt="Static Evidence Lane project brain visualization" width={1200} height={900} />
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
