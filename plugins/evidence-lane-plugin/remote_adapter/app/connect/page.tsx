import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";
import { publicSiteUrl, repositoryUrl } from "../_data/site";

export const metadata: Metadata = {
  title: "Connect",
  description: "Exact Git installation and native Codex activation boundaries for Evidence Lane.",
};

const installChecks = [
  "Verify the governed branch, full commit SHA, and clean release file set.",
  "Build the deterministic local package and prove the 87-action, 27-read, 60-write catalog.",
  "Install the exact 3.0.0 cache-busted package through the Codex Git marketplace route after the governed pre-HIL build route authorizes that test installation.",
  "Restart Codex only when the installer reports that a catalog refresh is required.",
  "Reopen the exact task, restore its full Plan Lane panel, and run post-restart native verification.",
] as const;

export default function ConnectPage() {
  return (
    <main>
      <PageHero
        eyebrow="Codex installation"
        title="One exact Git release. One native lifecycle route."
        description="Evidence Lane 3.0 installs into Codex from a verified Git identity. The local native MCP server, not a website or external transport, owns lifecycle execution."
        aside={<HeroOrbit preset="connect" />}
      />

      <section className="section shell installCompare">
        <article className="installPath codexPath">
          <span className="pathNumber">01</span>
          <span className="compactDepthPill">
            <GlassIconOrb color="#69d9f5" size={28} decorative>
              <OfficialToolIcon tool="terminal" size={15} decorative />
            </GlassIconOrb>
            <span>Codex stable</span>
          </span>
          <h2>Install from an exact governed commit.</h2>
          <p>
            The package contributes seventeen skills, eight registered hook events,
            the persistent task/change projection, and one native Evidence Lane
            MCP catalog. Older stable bytes remain recoverable until replacement
            verification succeeds.
          </p>
          <ol>
            {installChecks.map((check) => <li key={check}>{check}</li>)}
          </ol>
          <strong className="pathBoundary">No candidate, pointer, Fuse, or HIL result is inferred by installation.</strong>
        </article>

        <article className="installPath">
          <span className="pathNumber">02</span>
          <span className="compactDepthPill">
            <GlassIconOrb color="#83ddb3" size={28} decorative>
              <OfficialToolIcon tool="database" size={15} decorative />
            </GlassIconOrb>
            <span>Durable project runtime</span>
          </span>
          <h2>Storage and invocation stay separate.</h2>
          <p>
            Desktop and persistent Codex profiles use durable local SQLite.
            Ephemeral profiles require a durable mount or configured transactional
            connector. Headless API entry re-verifies ENV/UOP on every invocation.
          </p>
          <ul>
            <li>Native lifecycle calls stay package-local; the interactive Codex environment tunnel is version-bound, host-managed, and prewarmed separately.</li>
            <li>Headless/API profiles do not require that interactive tunnel.</li>
            <li>Google Drive may carry sealed artifacts but is never live runtime authority.</li>
            <li>Account tier and API billing do not select storage or lifecycle authority.</li>
          </ul>
          <strong className="pathBoundary">Every project-scoped action carries one exact project ID.</strong>
        </article>
      </section>

      <section className="section connectionBand">
        <div className="shell connectionGrid">
          <div>
            <span className="kicker">Canonical public surfaces</span>
            <h2>The website explains the product; it does not execute the lifecycle.</h2>
            <p>
              Read the public documentation at <a href={publicSiteUrl}>{publicSiteUrl}</a>
              {" "}and inspect the governed source at <a href={repositoryUrl}>{repositoryUrl}</a>.
              Installation proof still requires the exact package, local receipt,
              native catalog, restart boundary, tests, and CI run.
            </p>
          </div>
          <div className="endpointCards">
            <a href={publicSiteUrl} className="endpointCard endpointCardReady">
              <span>WEB</span><code>{publicSiteUrl}</code><strong>Public documentation</strong>
              <small>Product, lifecycle, architecture, proof, and legal boundaries</small>
            </a>
            <a href={repositoryUrl} className="endpointCard endpointCardProtocol">
              <span>GIT</span><code>agent/evi-v300-systemwide-release-hil-v3.0.0</code><strong>Governed pre-HIL test branch</strong>
              <small>Exact commit and CI identity are verified before installation</small>
            </a>
          </div>
        </div>
      </section>

      <section className="section shell connectorSlots">
        <div className="sectionHead">
          <span className="kicker">Bounded extension</span>
          <h2>Up to eight governed Codex connector or toolchain sidecars.</h2>
          <p>Each slot records purpose, role, schema, capability, lane, scope, expiry, and revocation history. A sidecar never changes lifecycle authority by registration alone.</p>
        </div>
        <div className="slotGrid">
          {Array.from({ length: 8 }, (_, index) => (
            <div key={index}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>Available governed slot</strong>
              <small>Purpose and schema must be explicit</small>
            </div>
          ))}
        </div>
      </section>

      <p className="section shell hostBoundaryNote">ChatGPT — Deferred</p>
    </main>
  );
}
