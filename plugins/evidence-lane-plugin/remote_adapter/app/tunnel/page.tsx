import type { Metadata } from "next";

import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "User Tunnel Guide",
  description: "Capability-gated Stable tunnel setup for eligible interactive ephemeral Codex hosts.",
};

export default function TunnelPage() {
  return (
    <main>
      <PageHero
        eyebrow="User tunnel"
        title="One hidden transport for the exact host that needs it."
        description="Durable local Codex and headless API profiles do not need the interactive tunnel. An eligible ephemeral host binds one versioned Stable tunnel without gaining lifecycle authority."
        aside={<HeroOrbit preset="connect" />}
      />
      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">Capability gate</span><h2>Transport, storage, and lifecycle remain separate.</h2><p>The tunnel is selected by measured host lifetime and interaction profile—not account tier, API billing, convenience, or brand.</p></div>
        <div className="compareGrid">
          <article><h3>Setup</h3><p>Bind the exact release, Stable slot, CODEX_APP_INTERACTIVE profile, Ephemeral lifetime, and real VM instance ID.</p></article>
          <article><h3>Secrets and windows</h3><p>Runtime credentials stay masked and DPAPI protected. Every helper and child process stays hidden/no-window.</p></article>
          <article><h3>Proof</h3><p>Status must pass, but only the package-local native MCP route can prove lifecycle readiness. A running process cannot approve anything.</p></article>
        </div>
      </section>
    </main>
  );
}
