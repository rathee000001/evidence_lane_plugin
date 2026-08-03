import type { Metadata } from "next";

import { PageHero } from "../_components/page-hero";
import { credits } from "../_data/site";

export const metadata: Metadata = {
  title: "Provenance",
  description: "Evidence Lane independent R&D lineage, design evolution, and contribution boundaries.",
};

const timeline = [
  ["Before 10 June 2026", "Controlled AI code intelligence pitch", "The problem was framed as local-first, memory-first project understanding with authorized inputs, evidence-backed outputs, controlled cloud exposure, and human review."],
  ["June 2026", "V3 architecture brains", "Project brains for EvidenceOS, Rathee Intelligence Lab, and Gold Nexus explored detailed graph inventories, source facts, change impact, workflow topology, and management handoff."],
  ["July 2026", "Evidence Lane separation", "The lifecycle, provenance, candidate isolation, rollback, and State Travel mechanisms were separated into a universal plugin rather than copied from a full application frontend."],
  ["August 2026", "Universal brain HIL", "The plugin added 18 canonical lanes, content-addressed refresh, Chat Lineage, host-aware storage, connector governance, exact six-way HIL, and topology reconciliation."],
] as const;

export default function ProvenancePage() {
  return (
    <main>
      <PageHero
        eyebrow="Independent research and development"
        title="Evidence Lane followed its own route."
        description="The project was developed through Praveen Rathee’s own product direction, accounts, hardware, time, testing, and iterative work with multiple AI systems. Outside questions may be used as tests; they are not the project’s identity or source authority."
        aside={<div className="provenanceStamp"><strong>R&amp;D</strong><span>Praveen Rathee</span><small>Human acceptance authority</small></div>}
      />
      <section className="section shell timelineSection">
        <div className="sectionHead"><span className="kicker">Evolution</span><h2>The current plugin has history, not a borrowed origin story.</h2></div>
        <div className="timeline">
          {timeline.map(([date, title, text], index) => <article key={date}><span>{String(index + 1).padStart(2, "0")}</span><div><time>{date}</time><h3>{title}</h3><p>{text}</p></div></article>)}
        </div>
      </section>
      <section className="section sourceBoundaryBand">
        <div className="shell sourceBoundary">
          <div><span className="kicker">Reference law</span><h2>Historical brains calibrate depth. Current source and tests govern claims.</h2><p>The June SQLite brains, master-fact CSVs, plan HTML, and early pitch preserve problem framing and topology ambition. They are read-only design references. They do not overwrite current plugin truth, bypass source policy, or prove the v1.1 implementation.</p></div>
          <div className="sourceTypes">
            <article><strong>Historical evidence</strong><p>Intent, earlier architecture, presentation language, and prior experiments.</p></article>
            <article><strong>Current authority</strong><p>Exact Git source, governed ledger, tests, manifests, hashes, installs, and deployment receipts.</p></article>
            <article><strong>External reality</strong><p>Independent user outcomes, reliable connector operation, performance, and market evidence.</p></article>
          </div>
        </div>
      </section>
      <section className="section shell creditsSection">
        <div className="sectionHead wideHead"><span className="kicker">Credits</span><h2>Roles stated precisely, without transferring project authority.</h2></div>
        <div className="creditGrid">
          {credits.map(([name, role], index) => <article key={name}><span>{String(index + 1).padStart(2, "0")}</span><h3>{name}</h3><p>{role}</p></article>)}
        </div>
        <p className="creditNote">The implementation also depends on Python, SQLite, Git, Mermaid, Graphviz DOT, Next.js, React, Vercel, and the open-source libraries named in the repository. Their licenses and trademarks remain their own.</p>
      </section>
    </main>
  );
}
