import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Hooks",
  description: "Evidence Lane's eight installed Codex lifecycle hook events and transport boundary.",
};

const hookEvents = [
  ["SessionStart", "A governed task or session begins."],
  ["UserPromptSubmit", "The user's visible prompt enters the host lifecycle."],
  ["PreToolUse", "A tool call is about to execute."],
  ["PostToolUse", "A tool call returned a visible result."],
  ["PreCompact", "The host is about to compact task context."],
  ["PostCompact", "The host completed context compaction."],
  ["Stop", "The visible assistant turn is ending."],
  ["SessionEnd", "Best-effort host session closure."],
] as const;

export default function HooksPage() {
  return (
    <main>
      <PageHero
        eyebrow="Lifecycle hooks"
        title="Eight transport events. Governance stays in skills."
        description="Hooks carry bounded visible lifecycle facts into the package. They do not classify task authority, refresh Plan Lane, decide HIL, or move project truth."
        aside={<HeroOrbit preset="hooks" />}
      />

      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">Installed event matrix</span><h2>Every supported event must prove real host invocation.</h2><p>Package tests are necessary but not enough. The installed host must produce deterministic, secret-safe invocation receipts, or the capability remains explicitly unavailable.</p></div>
        <div className="routeGrid">
          {hookEvents.map(([name, detail], index) => (
            <article className="routeCard" key={name}>
              <span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#83ddb3", "#a99af7", "#efca72"][index % 4]} size={28} decorative><OfficialToolIcon tool="pulse" size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span>
              <h3>{name}</h3><p>{detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Windows behavior</span><h2>Background helpers stay hidden and observable through receipts.</h2><p>Python hooks and tunnel helpers must not flash transient console windows. Persistent services stay hidden; bounded processes use no-window launch flags and still return explicit health or failure evidence.</p></div></div></section>
    </main>
  );
}
