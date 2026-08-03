import type { Metadata } from "next";
import Link from "next/link";

import { EvidenceBrainAsset } from "../_components/evidence-assets";
import { PageHero } from "../_components/page-hero";
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
  ["Six-way HIL", "APPROVE, correction, research, rollback, reject, or fail"],
  ["Accepted pointer", "Only exact APPROVE may invoke Fuse and move governed truth"],
] as const;

export default function ArchitecturePage() {
  return (
    <main>
      <PageHero
        eyebrow="System architecture"
        title="Parallel evidence work. Serial authority."
        description="Evidence Lane can calculate independent source sectors concurrently. Candidate sealing, human decision, Fuse, accepted pointers, rollback, and State Travel remain ordered and compare-and-swap governed."
        aside={
          <div className="routeBrainOrb" aria-label="Pulsing Evidence Lane glass brain">
            <EvidenceBrainAsset color="#37c7e7" label="" />
          </div>
        }
      />

      <section className="section shell topologySection">
        <div className="sectionHead wideHead">
          <span className="kicker">End-to-end flow</span>
          <h2>Understanding is compiled into evidence, then held outside truth.</h2>
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
            <div className="parallelSources">
              <span>Code</span><span>Docs</span><span>Data</span><span>Lineage</span>
            </div>
            <div className="convergeLines" aria-hidden="true"><i /><i /><i /><i /></div>
            <div className="manifestNode">Candidate manifest</div>
            <div className="authorityArrow" aria-hidden="true">↓</div>
            <div className="hilNode">Human decision</div>
            <div className="authorityArrow" aria-hidden="true">↓</div>
            <div className="pointerNode">Accepted pointer</div>
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
        <div className="sectionHead wideHead"><span className="kicker">Host boundary</span><h2>Two delivery paths, one release identity.</h2></div>
        <div className="compareGrid">
          <article><span className="pill blue">Codex</span><h3>Git-native and local-first</h3><p>The plugin is installed from the exact Git SHA. SQLite and accepted-pointer authority remain on the durable user host. Vercel is absent from this path.</p></article>
          <article><span className="pill gold">ChatGPT</span><h3>Durable remote MCP</h3><p>A thin Vercel edge publishes the protocol endpoint and public website, then forwards only to a configured durable HTTPS service with the same release SHA.</p></article>
          <article><span className="pill dark">Failure</span><h3>Closed, visible, and diagnosable</h3><p>Missing auth, storage, queue, durable origin, or exact identity blocks MCP. A successful website render never proves connector readiness.</p></article>
        </div>
      </section>
    </main>
  );
}
