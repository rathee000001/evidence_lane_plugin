import type { Metadata } from "next";
import Link from "next/link";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { ModeOperatorExplorer } from "../_components/mode-operator-explorer";
import modeOperatorGuide from "../_data/mode-governance.json";

export const metadata: Metadata = {
  title: "Mode operators",
  description:
    "Interactive source-backed ENV/UOP formulas, operators, gates, and lane-specific six-way HIL semantics.",
};

const operatorRoute = [
  ["Detect intent", "A plugin selection or prompt classification produces the same bounded mode identity."],
  ["Load ENV/UOP", "The exact mode policy, formula, loop, operators, gate, accepted object, and rollback target are hash-bound."],
  ["Route lanes", "Only the lanes named by that mode are queried or mutated; Code policy is never copied into unrelated work."],
  ["Execute explicitly", "Skills, commands, SDK arms, and MCP calls work with hooks off; hooks may observe lifecycle events but never own the action."],
  ["Validate", "The mode-specific gate records evidence and returns the correct next authority surface."],
] as const;

export default function OperatorsPage() {
  return (
    <main>
      <section className="pageHero operatorHero orbitHeroFrame shell">
        <div>
          <span className="eyebrow"><i />Mode governance</span>
          <h1>Select a mode. Load its exact law.</h1>
          <p>
            Plugin-selected and prompt-inferred modes resolve to the same ENV/UOP
            identity. Code displays its controlled CI/CD, PCM, and MBA formula;
            every other mode keeps its own loop, gate, operators, accepted object,
            and six-way human decision semantics.
          </p>
          <div className="actions">
            <Link className="primary universal-pill actionGlassPill" href="#mode-explorer" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001">
              <GlassIconOrb color="#b6a0ff" size={30} decorative><OfficialToolIcon tool="terminal" size={16} decorative /></GlassIconOrb>
              <span>Open the explorer</span>
            </Link>
            <Link className="secondary universal-pill actionGlassPill" href="/architecture" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001">
              <GlassIconOrb color="#69d9f5" size={30} decorative><OfficialToolIcon tool="node" size={16} decorative /></GlassIconOrb>
              <span>Trace authority flow</span>
            </Link>
          </div>
        </div>
        <aside><HeroOrbit preset="operators" /></aside>
      </section>

      <section className="section operatorExplorerBand" id="mode-explorer">
        <div className="shell">
          <ModeOperatorExplorer data={modeOperatorGuide} />
        </div>
      </section>

      <section className="section shell operatorRouteStory">
        <div className="sectionHead wideHead">
          <span className="kicker">Formula Engine route</span>
          <h2>Classification chooses a law; execution still proves every boundary.</h2>
          <p>
            The current exported guide contains {modeOperatorGuide.mode_count} modes. Each mode
            supplies its own recursive loop and accepted object while the native lifecycle keeps
            Project Truth, Learning, Canon, Memory, and Project Universe separate.
          </p>
        </div>
        <div className="operatorRouteGrid">
          {operatorRoute.map(([title, detail], index) => (
            <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><h3>{title}</h3><p>{detail}</p></article>
          ))}
        </div>
      </section>

      <section className="section shell operatorBoundaries">
        <article><span>01</span><h2>Selection is classification</h2><p>Choosing a mode attaches its formula and operator receipt. It does not approve work, create a candidate, or move a pointer.</p></article>
        <article><span>02</span><h2>Code is explicitly controlled</h2><p>Code runs through the visible plan → sandbox build → test → hash → package formula with executable CI/CD receipts.</p></article>
        <article><span>03</span><h2>HIL meaning stays local</h2><p>The six tokens remain exact, while each lane supplies its accepted object, validation gate, rollback target, and required evidence.</p></article>
        <article><span>04</span><h2>Project and Learning decisions stay separate</h2><p>A Project candidate and an AI Learning candidate may each reach a six-way human gate. Neither gate consumes, aliases, or silently decides the other.</p></article>
      </section>
    </main>
  );
}
