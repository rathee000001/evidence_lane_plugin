import type { Metadata } from "next";

import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "User Tunnel Guide",
  description: "Capability-gated Stable tunnel setup for an exact Codex host with a proven native tool gap.",
};

export default function TunnelPage() {
  return (
    <main>
      <PageHero
        eyebrow="User tunnel"
        title="One hidden transport only after a real tool gap is proven."
        description="A durable desktop, local CLI, persistent API host, or ephemeral VM uses direct native MCP whenever its exact capability receipt supports the required tools. A version-bound tunnel fills only the measured gap and never gains lifecycle authority."
        aside={<HeroOrbit preset="connect" />}
      />
      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">Capability gate</span><h2>Transport, storage, and lifecycle remain separate.</h2><p>The tunnel is selected by an exact missing-host-tool receipt plus host lifetime—not account tier, API billing, convenience, brand, or a generic desktop/headless label.</p></div>
        <div className="compareGrid">
          <article><h3>Setup</h3><p>A persistent host binds the exact release at most once per host/release; an interactive ephemeral host binds the exact VM instance for that VM lifetime only.</p></article>
          <article><h3>Secrets and windows</h3><p>Runtime credentials stay masked and DPAPI protected. Every helper and child process stays hidden/no-window.</p></article>
          <article><h3>Proof</h3><p>Tunnel status proves only transport. Package-local MCP, project/session/task/runtime binding, storage, and action receipts still prove lifecycle readiness. A running process cannot approve anything.</p></article>
        </div>
      </section>
    </main>
  );
}
