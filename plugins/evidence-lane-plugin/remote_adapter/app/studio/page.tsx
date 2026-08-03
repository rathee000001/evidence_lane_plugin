import Link from "next/link";

import { EvidencePromptStudio } from "../_components/evidence-prompt-studio";

export default function StudioPage() {
  return (
    <main>
      <section className="pageHero studioHero shell">
        <div>
          <span className="eyebrow"><i />Evidence AI Studio</span>
          <h1>Ask the product. See the evidence boundary.</h1>
          <p>
            Explore Evidence Lane through an interactive, source-linked knowledge surface.
            This preview answers from explicit published product facts and refuses unsupported
            claims instead of disguising a scripted demo as a live model.
          </p>
          <div className="actions">
            <Link className="primary" href="/architecture">Open architecture</Link>
            <Link className="secondary" href="/connect">Inspect host routes</Link>
          </div>
        </div>
        <aside className="studioHeroPanel" aria-label="Prompt Studio contract">
          <span>LOCAL GROUNDING</span>
          <strong>No invented provider call</strong>
          <p>Every successful answer links back to a published Evidence Lane section.</p>
          <dl>
            <div><dt>Known</dt><dd>Product facts</dd></div>
            <div><dt>Unknown</dt><dd>Visible refusal</dd></div>
            <div><dt>Future</dt><dd>Receipt-bound AI route</dd></div>
          </dl>
        </aside>
      </section>

      <section className="section studioPageBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker light">Interactive knowledge surface</span>
            <h2>A useful studio now, an honest boundary always.</h2>
            <p>
              Use the suggested prompts or ask in your own words. The local matcher exposes
              exactly where it has evidence and where a separately configured AI provider would
              be required.
            </p>
          </div>
          <EvidencePromptStudio />
        </div>
      </section>

      <section className="section shell studioRules">
        <article><span>01</span><h2>Ground first</h2><p>Answers come from a versioned, reviewable knowledge map rather than hidden prompt context.</p></article>
        <article><span>02</span><h2>Cite the surface</h2><p>Every supported response names the site sections that carry the claim.</p></article>
        <article><span>03</span><h2>Refuse cleanly</h2><p>Unsupported questions stay unsupported until a real provider and evidence route are configured.</p></article>
      </section>
    </main>
  );
}
