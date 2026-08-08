import Link from "next/link";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { EvidencePromptStudio } from "../_components/evidence-prompt-studio";
import { studioCorpus } from "../_data/studio-retrieval";

export default function StudioPage() {
  return (
    <main>
      <section className="pageHero studioHero shell">
        <div>
          <span className="eyebrow"><i />Evidence AI Studio</span>
          <h1>Your business guide to the whole Evidence Lane plugin.</h1>
          <p>
            Ask what the product solves, how a source becomes governed evidence, what each control
            does, where the human decision sits, and what must happen before release. Answers are
            written for operators, founders, reviewers, and business stakeholders. Supporting
            evidence remains available without turning the main conversation into code discussion.
          </p>
          <div className="actions">
            <Link className="primary universal-pill actionGlassPill" href="/architecture" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001">
              <GlassIconOrb color="#69d9f5" size={30} decorative><OfficialToolIcon tool="node" size={16} decorative /></GlassIconOrb>
              <span>Open architecture</span>
            </Link>
            <Link className="secondary universal-pill actionGlassPill" href="/connect" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001">
              <GlassIconOrb color="#efca72" size={30} decorative><OfficialToolIcon tool="package" size={16} decorative /></GlassIconOrb>
              <span>Inspect host routes</span>
            </Link>
          </div>
        </div>
        <aside className="studioHeroPanel" aria-label="Evidence AI Studio business guide contract">
          <span>WHOLE-PLUGIN BUSINESS GUIDE</span>
          <strong>Plain-language guidance with an inspectable evidence receipt</strong>
          <p>The guide explains decisions and outcomes first. Source identities and ranking details remain available for reviewers who need to audit the answer.</p>
          <dl>
            <div><dt>Coverage</dt><dd>Lifecycle, lanes, hosts, proof, release</dd></div>
            <div><dt>Language</dt><dd>Business-first, implementation-second</dd></div>
            <div><dt>Project no hit</dt><dd>Visible refusal, never invention</dd></div>
            <div><dt>Audit</dt><dd>Expandable source and retrieval receipt</dd></div>
          </dl>
        </aside>
      </section>

      <section className="section studioPageBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker light">Interactive product guide</span>
            <h2>Understand the decision before opening the technical receipt.</h2>
            <p>
              Evidence AI Studio matches each supported question to reviewed business guidance
              and supporting public-safe sources. The detailed ranking record is still preserved,
              but it sits behind an audit disclosure so the main answer remains readable.
            </p>
          </div>
          <EvidencePromptStudio corpus={{
            sourceCount: studioCorpus.sourceCount,
            chunkCount: studioCorpus.chunkCount,
            historyThroughSha: studioCorpus.historyThroughSha,
          }} />
        </div>
      </section>

      <section className="section shell studioRules">
        <article><span>01</span><h2>Explain the business outcome</h2><p>Answers start with the problem, decision, responsibility, and operational consequence.</p></article>
        <article><span>02</span><h2>Keep proof inspectable</h2><p>Supporting sources and the detailed retrieval receipt remain available when a reviewer needs them.</p></article>
        <article><span>03</span><h2>Refuse unsupported claims</h2><p>An unknown project answer stays unknown. A general model never becomes project authority.</p></article>
      </section>

      <section className="section shell creativeRoute">
        <div>
          <span className="kicker">Bounded creative route</span>
          <h2>Adobe Express for release graphics, with no Evidence Lane account connection.</h2>
          <p>Use Adobe Express for social cards, campaign graphics, and other two-dimensional release material. Evidence Lane links to Adobe&apos;s official experience; it does not request, store, or broker Adobe credentials.</p>
        </div>
        <Link className="primary universal-pill actionGlassPill" href="https://www.adobe.com/express/">
          <GlassIconOrb color="#8b9cff" size={30} decorative><OfficialToolIcon tool="media" size={16} decorative /></GlassIconOrb>
          <span>Open official Adobe Express</span>
        </Link>
      </section>
    </main>
  );
}
