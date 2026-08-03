import Image from "next/image";
import Link from "next/link";

import { EvidenceOrbit } from "./_components/evidence-orbit";
import {
  LaneToolchainExplorer,
  UniversalCommandDeck,
} from "./_components/evidence-console";
import { EvidenceBrainAsset } from "./_components/evidence-assets";
import { MotionReveal } from "./_components/motion-reveal";
import { ReleaseStatus } from "./_components/release-status";
import { SourceBrainLab } from "./_components/source-brain-lab";
import { artifactContract, painLedger, proofMetrics } from "./_data/site";

const routes = [
  ["Architecture", "See how parallel lane computation meets serial lifecycle authority.", "/architecture", "01"],
  ["18 lanes", "Inspect the canonical source registry and the artifact contract for every lane.", "/lanes", "02"],
  ["Prompt Studio", "Ask grounded product questions and see the evidence boundary in the answer.", "/studio", "03"],
  ["Proof boundary", "Separate verified behavior, historical context, open blockers, and candidate claims.", "/proof", "04"],
  ["Provenance", "Follow the independent R&D lineage, source boundaries, and toolchain credits.", "/provenance", "05"],
] as const;

export default function Home() {
  return (
    <main>
      <section className="homeHero shell">
        <MotionReveal className="heroCopy">
          <span className="eyebrow"><i />Governed source-to-brain intelligence</span>
          <h1>Turn a repository into an inspectable brain. Keep truth human.</h1>
          <p>
            Evidence Lane lets tools move fast without letting claims move silently. Authorized
            sources become searchable SQLite sectors, reconciled topology, exact pointers, and
            receipts that a human can inspect before anything is accepted.
          </p>
          <div className="actions">
            <Link className="primary" href="#brains">Watch a source become evidence</Link>
            <Link className="secondary" href="/proof">Inspect the HIL boundary</Link>
          </div>
        </MotionReveal>
        <MotionReveal className="heroVisual" delay={0.12}>
          <EvidenceOrbit />
          <div className="visualHalo" />
          <div className="heroBrainOrb" role="img" aria-label="Evidence Lane brain inside a glass orb">
            <EvidenceBrainAsset color="#4bd3f2" label="" />
          </div>
          <div className="visualBadge badgeA"><span>18</span> source lanes</div>
          <div className="visualBadge badgeB"><span>7</span> code entities</div>
          <div className="visualBadge badgeC"><span>1</span> human gate</div>
        </MotionReveal>
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

      <section className="section sourceLabBand" id="brains">
        <div className="shell">
          <MotionReveal className="sectionHead wideHead">
            <span className="kicker">Verified source lab</span>
            <h2>Inspect the generator, six official source snapshots, and four read-only brains without collapsing their identities.</h2>
            <p>
              The selected V5.9 desktop app, Graphify, five official GitHub sources, an Agentic
              Workflows brain, the older Evidence Lane export, and the RIL and Gold mini brains
              are separate evidence objects. Each interactive view shows what was verified,
              what improved the candidate, and what the evidence cannot prove.
            </p>
          </MotionReveal>
          <SourceBrainLab />
        </div>
      </section>

      <section className="section shell splitIntro" id="problem">
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
            <p>Hover, focus, click, or use arrow keys to inspect what each public control produces and what it is forbidden to imply.</p>
          </div>
          <UniversalCommandDeck />
          <div className="conditionalEvent">
            <strong>State Travel</strong>
            <p>A top conditional recovery event used only for an accepted, sealed fresh-host handoff when the user requests it or context is exhausted.</p>
            <span>Not a seventh everyday control</span>
          </div>
        </div>
      </section>

      <section className="section toolchainBand" id="toolchains">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">Lane-by-lane toolchain</span>
            <h2>Select a lane. Watch its real tools enter one brain and emit four inspectable files.</h2>
            <p>The official app icons, parser IDs, chunkers, retrieval surfaces, and output names come from the same eighteen-lane contract used by the engine.</p>
          </div>
          <LaneToolchainExplorer />
          <div className="toolchainStudioLink">
            <span>Want to interrogate the system?</span>
            <Link href="/studio">Open Evidence AI Studio <b aria-hidden="true">→</b></Link>
          </div>
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
