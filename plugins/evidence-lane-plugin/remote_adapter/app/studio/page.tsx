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
          <h1>Ask the product. See the evidence boundary.</h1>
          <p>
            Query the committed plugin, current 51-step Plan Lane, historical Delta ledger,
            policy, lane, mode, website, and Git-history corpus instead of re-explaining it. The
            local hybrid index returns extractive evidence and refuses unsupported project claims.
            Only a genuine general-question no-hit may cross the separately configured free-model
            boundary, and that answer is never labeled as Evidence Lane evidence.
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
        <aside className="studioHeroPanel" aria-label="Prompt Studio contract">
          <span>COMMITTED LOCAL RAG</span>
          <strong>LlamaIndex + hybrid projection · SQLite FTS5 authority</strong>
          <p>Project answers identify exact source paths and content hashes; external general answers identify their provider boundary.</p>
          <dl>
            <div><dt>Corpus</dt><dd>Public-safe committed evidence</dd></div>
            <div><dt>Ranking</dt><dd>Visible hybrid scores</dd></div>
            <div><dt>Project no hit</dt><dd>Visible refusal</dd></div>
            <div><dt>General no hit</dt><dd>Optional openrouter/free only</dd></div>
          </dl>
        </aside>
      </section>

      <section className="section studioPageBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker light">Interactive knowledge surface</span>
            <h2>Search parsed evidence instead of rebuilding project context.</h2>
            <p>
              LlamaIndex supplies deterministic chunks; the committed SQLite authority stores
              those chunks in FTS5 and materializes TF-IDF. Prompt Studio and the floating Studio
              share the same hash-bound JSON projection and deterministic BM25, TF-IDF, and
              reciprocal-rank fusion. Neither client executes SQLite or receives provider secrets.
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
        <article><span>01</span><h2>Parse once</h2><p>Versioned chunks and accepted lane facts remain reusable until exact source changes require Refresh.</p></article>
        <article><span>02</span><h2>Rank visibly</h2><p>Every supported response exposes source paths, chunk hashes, and hybrid retrieval scores.</p></article>
        <article><span>03</span><h2>Separate cleanly</h2><p>Unsupported project questions refuse. General no-hits may use only the zero-cost external route when explicitly configured, with no paid fallback and no project authority.</p></article>
      </section>
    </main>
  );
}
