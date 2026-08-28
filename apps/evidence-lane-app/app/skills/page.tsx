import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";
import { pluginSurfaces } from "../_data/plugin-surfaces";

export const metadata: Metadata = {
  title: "Skills",
  description: "The derived current Evidence Lane Codex skill inventory and its authority boundaries.",
};

const families = ["Router", "Lifecycle", "Session", "Mode", "Connector", "Storage", "Canon", "Learning"] as const;

export default function SkillsPage() {
  return (
    <main>
      <PageHero
        eyebrow="Codex skill surface"
        title="Seventeen skills. Six everyday controls. No hidden approval."
        description="Each installed skill has one visible purpose, setting, output, and authority boundary. Routers and sidecars remain distinct from the six primary lifecycle controls."
        aside={<HeroOrbit preset="skills" />}
      />

      <section className="section shell">
        <div className="sectionHead wideHead">
          <span className="kicker">Installed catalog</span>
          <h2>The public story follows the package contract.</h2>
          <p>The list below is projected from the same Git-tracked skill catalog that clean CI packages and verifies. A skill can prepare, inspect, classify, or route work only within its explicit boundary.</p>
        </div>
        <div className="compareGrid">
          {families.map((family) => {
            const surfaces = pluginSurfaces.filter((surface) => surface.family === family);
            return (
              <article key={family}>
                <span className="compactDepthPill">
                  <GlassIconOrb color="#a99af7" size={28} decorative><OfficialToolIcon tool="package" size={15} decorative /></GlassIconOrb>
                  <span>{family}</span>
                </span>
                <h3>{surfaces.length} {surfaces.length === 1 ? "skill" : "skills"}</h3>
                <p>{surfaces.map((surface) => surface.label).join(" · ")}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="section architectureDark">
        <div className="shell">
          <div className="sectionHead wideHead"><span className="kicker light">Authority boundary</span><h2>Discoverable does not mean self-authorizing.</h2><p>Tests, packages, previews, connectors, and continuing conversation cannot approve a candidate. Exact Project HIL and a separate Fuse path retain that authority.</p></div>
        </div>
      </section>
    </main>
  );
}
