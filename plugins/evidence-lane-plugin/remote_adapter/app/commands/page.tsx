import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Commands",
  description: "Evidence Lane primary controls, conditional routes, and command authority boundaries.",
};

const commandGroups = [
  ["6", "primary controls", "Boot, Rollback, Build, Refresh, Mode, and Source Intake are the ordered everyday control surface.", "pulse"],
  ["1", "Plan compatibility command", "The migrated evi-plan command verifies one host Plan projection without creating another Plan authority.", "node"],
  ["11", "routers and sidecars", "State Travel, Canon, Learning, storage, connector, plugin, and session routes keep separate authority boundaries.", "package"],
] as const;

export default function CommandsPage() {
  return (
    <main>
      <PageHero
        eyebrow="Command routes"
        title="Commands select governed workflows. They never manufacture authority."
        description="The public command inventory is derived from seventeen installed skills and one migrated Plan compatibility command. Native MCP actions remain a separate execution inventory."
        aside={<HeroOrbit preset="skills" />}
      />

      <section className="section shell">
        <div className="sectionHead wideHead">
          <span className="kicker">Discovery contract</span>
          <h2>One route per responsibility, with explicit composition.</h2>
          <p>New Delta behavior attaches to the correct existing command family or adds a separately testable route. It does not silently remain only in source code or duplicate an existing authority.</p>
        </div>
        <div className="compareGrid">
          {commandGroups.map(([count, title, detail, icon], index) => (
            <article key={title}>
              <span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#a99af7", "#83ddb3"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{count}</span></span>
              <h3>{title}</h3><p>{detail}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Fail-closed law</span><h2>No native route means no lifecycle claim.</h2><p>A visible command, skill, website page, test, or continued conversation cannot approve a candidate or substitute for a missing native action or host capability.</p></div></div></section>
    </main>
  );
}
