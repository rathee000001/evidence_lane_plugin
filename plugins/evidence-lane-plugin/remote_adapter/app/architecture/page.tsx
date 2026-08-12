import type { Metadata } from "next";
import Link from "next/link";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";
import { SourceLaneIcon } from "../_components/source-lane-icon";
import { artifactContract } from "../_data/site";

export const metadata: Metadata = {
  title: "Architecture",
  description: "Evidence Lane source, sector, candidate, HIL, and accepted-pointer architecture.",
};

const flow = [
  ["Authorized sources", "Tracked Git, explicit files, durable connectors, visible chat lineage"],
  ["Policy boundary", "Classify, redact, exclude secrets, hash, and route before indexing"],
  ["Parallel lane workers", "Independent SQLite sectors, FTS, CAS, MMD, DOT, and receipts"],
  ["Candidate overlay", "Project-sector candidate facts remain outside accepted truth"],
  ["Lane-correct six-way HIL", "APPROVE, APPROVE_WITH_DELTA, MORE_RESEARCH, ROLLBACK, REJECT, or FAIL; each lane defines the exact governed effect"],
  ["Accepted pointer", "Only exact APPROVE may invoke Fuse and move governed truth"],
] as const;

const parallelSources = [
  { label: "Code", color: "#69d9f5", lane: "local_code" },
  { label: "Docs", color: "#83ddb3", lane: "docs" },
  { label: "Data", color: "#efca72", lane: "data_excel" },
  { label: "Lineage", color: "#f2a1c5", lane: "chat_lineage" },
] as const;

export default function ArchitecturePage() {
  return (
    <main>
      <PageHero
        eyebrow="System architecture"
        title="Parallel evidence work. Serial authority."
        description="Evidence Lane can calculate independent source sectors concurrently. Candidate sealing, human decision, Fuse, accepted pointers, rollback, and State Travel remain ordered and compare-and-swap governed."
        aside={<HeroOrbit preset="architecture" />}
      />

      <section className="section shell topologySection">
        <div className="sectionHead wideHead">
          <span className="kicker">End-to-end flow</span>
          <h2>Understanding is compiled into evidence, then held outside truth.</h2>
          <p>Mode selection first loads the exact ENV/UOP loop, formula, operators, validation gate, and lane-specific HIL meanings. It never substitutes Code governance for another lane.</p>
          <Link className="textLink" href="/operators">Inspect every mode contract <span aria-hidden="true">→</span></Link>
        </div>
        <div className="architectureFlow">
          {flow.map(([title, text], index) => (
            <article key={title} className={index === 4 ? "gateNode" : index === 5 ? "acceptedNode" : ""}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h3>{title}</h3><p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section architectureDark">
        <div className="shell parallelGrid">
          <div>
            <span className="kicker light">Execution model</span>
            <h2>Concurrency where it is safe. Linearity where authority changes.</h2>
            <p>Source lanes, chunking, FTS, topology rendering, and validation can run in bounded parallel workers. They converge into one deterministic candidate manifest. No worker can accept itself.</p>
          </div>
          <div className="parallelDiagram" aria-label="Parallel lane computation converging on a serial human gate">
            <span className="parallelDiagramLabel">Source Intake</span>
            <div className="parallelSources">
              {parallelSources.map((source) => (
                <span className="sourceIntakeDepthPill" key={source.label}>
                  <GlassIconOrb className="source-lane-orb" color={source.color} size={32} decorative>
                    <SourceLaneIcon lane={source.lane} size={19} decorative />
                  </GlassIconOrb>
                  <strong>{source.label}</strong>
                </span>
              ))}
            </div>
            <svg className="convergeLines" viewBox="0 0 800 120" preserveAspectRatio="none" aria-hidden="true">
              <path d="M 84 0 L 400 120" />
              <path d="M 276 0 L 400 120" />
              <path d="M 476 0 L 400 120" />
              <path d="M 704 0 L 400 120" />
            </svg>
            <div className="manifestNode flowDepthPill"><GlassIconOrb color="#69d9f5" size={34} decorative><OfficialToolIcon tool="package" size={18} decorative /></GlassIconOrb><span>Candidate manifest</span></div>
            <div className="authorityArrow" aria-hidden="true">↓</div>
            <div className="hilNode flowDepthPill"><GlassIconOrb color="#efca72" size={34} decorative><OfficialToolIcon tool="pulse" size={18} decorative /></GlassIconOrb><span>Human decision</span></div>
            <div className="authorityArrow" aria-hidden="true">↓</div>
            <div className="pointerNode flowDepthPill"><GlassIconOrb color="#83ddb3" size={34} decorative><OfficialToolIcon tool="database" size={18} decorative /></GlassIconOrb><span>Accepted pointer</span></div>
          </div>
        </div>
      </section>

      <section className="section shell sectorSection">
        <div className="sectorCopy">
          <span className="kicker">Sector contract</span>
          <h2>The graph must agree with the database.</h2>
          <p>Every topology renderer is derived from the same lane facts and then independently reconciled. Matching file counts are insufficient: node IDs, edge identities, subgraph identities, and declared table/kind counts must match.</p>
          <Link className="textLink" href="/proof">See the negative-test standard <span aria-hidden="true">→</span></Link>
        </div>
        <div className="artifactMatrix">
          {artifactContract.map(([title, text], index) => (
            <article key={title}><span>{index + 1}</span><h3>{title}</h3><p>{text}</p></article>
          ))}
        </div>
      </section>

      <section className="section shell boundaryCompare">
        <div className="sectionHead wideHead"><span className="kicker">Codex execution profiles</span><h2>One native lifecycle boundary across three storage realities.</h2></div>
        <div className="compareGrid">
          <article><span className="compactDepthPill"><GlassIconOrb color="#69d9f5" size={28} decorative><OfficialToolIcon tool="terminal" size={15} decorative /></GlassIconOrb><span>Persistent Codex</span></span><h3>Local-first and warm-attached</h3><p>The plugin is installed from the exact Git SHA. SQLite, Plan Lane, Agent Learning, and accepted-pointer authority remain on the durable user host through the package-local native MCP server.</p></article>
          <article><span className="compactDepthPill"><GlassIconOrb color="#efca72" size={28} decorative><OfficialToolIcon tool="database" size={15} decorative /></GlassIconOrb><span>Headless Codex API</span></span><h3>Native tools without a network transport dependency</h3><p>When the API process runs on a local or persistent machine, Evidence Lane follows that machine's local project storage. A perishable worker restores only from an exact sealed exit package and configured storage carrier.</p></article>
          <article><span className="compactDepthPill"><GlassIconOrb color="#f2a1c5" size={28} decorative><OfficialToolIcon tool="pulse" size={15} decorative /></GlassIconOrb><span>Failure boundary</span></span><h3>Closed, visible, and diagnosable</h3><p>Missing storage, package identity, execution-profile proof, native catalog parity, or task-panel continuity blocks lifecycle work. A successful website render proves none of those runtime conditions.</p></article>
        </div>
      </section>
    </main>
  );
}
