import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { GovernedStoryExplorer } from "../_components/governed-story-explorer";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "AI Learning",
  description: "Evidence Lane project-isolated learning retrieval, Delta observations, candidates, decisions, and revocation.",
};

const learningControls = [
  ["Observe each Delta", "A completed Delta emits one attributable, project-isolated Learning observation before the next row activates.", "node"],
  ["Retrieve and propose", "Bounded retrieval can inform later work; a sealed Learning candidate remains unaccepted until its own final decision.", "database"],
  ["Decide or revoke", "The dedicated Learning HIL may accept the final parity candidate, while later revocation stays append-only and inspectable.", "pulse"],
] as const;

const learningStages = [
  {
    id: "observe",
    label: "Observe Delta",
    summary: "Record what a verified Delta changed, proved, failed, and corrected before advancing the Plan row.",
    outcome: "A secret-redacted observed-experience packet attributable to one exact task, Delta, source snapshot, and test set.",
    proof: "Active-row identity, Delta receipt, source/test hashes, classification, replay key, and no-authority-effect flags.",
    boundary: "Automatic observation is candidate evidence only; it accepts no Learning and changes no Project Truth.",
    details: [
      "Wait for the Delta's local code, test, and installation verification receipt.",
      "Classify the outcome and corrections without storing raw tool payloads or private reasoning.",
      "Seal one idempotent observation before the Goal and host panels transition to the next row.",
    ],
    icon: "node",
    color: "#a99af7",
  },
  {
    id: "retrieve",
    label: "Retrieve",
    summary: "Query accepted and candidate Learning with an exact project boundary and a bounded result limit.",
    outcome: "A ranked project-specific guidance slice with source Delta and decision provenance.",
    proof: "SQLite FTS5 query receipt, project ID, candidate state, evidence hashes, result count, and no-hit state.",
    boundary: "No hit is valid; another project's guidance and shared global telemetry are forbidden substitutes.",
    details: [
      "Normalize the current Delta question and restrict it to the active project authority.",
      "Separate accepted Learning from unaccepted candidate evidence in every result.",
      "Return only the relevant guidance and provenance needed for the current row.",
    ],
    icon: "database",
    color: "#69d9f5",
  },
  {
    id: "candidate",
    label: "Build final candidate",
    summary: "Reconcile completed Delta observations from the prior accepted PV through the current pre-HIL stage.",
    outcome: "One content-addressed Learning candidate describing parity, effective corrections, failures, and reusable project guidance.",
    proof: "Prior accepted PV, covered Delta receipt set, dedup key, evidence hashes, expiry, and candidate identity.",
    boundary: "Sealing the candidate does not accept it, approve the Project PV, or write another project's Learning.",
    details: [
      "Verify the covered Delta chain is complete and belongs to the same project and PV interval.",
      "Summarize only evidence-backed patterns; preserve unknowns and failed approaches as explicit boundaries.",
      "Deduplicate byte-identical proposals and reject a reused identity with different evidence.",
    ],
    icon: "package",
    color: "#83ddb3",
  },
  {
    id: "decide",
    label: "Learning HIL",
    summary: "Ask the human to decide the final Agent Learning candidate separately from the Project PV decision.",
    outcome: "An explicit Learning ACCEPT, REJECT, or MORE_RESEARCH receipt bound to the exact candidate.",
    proof: "Human token, candidate SHA-256, decision scope, actor, timestamp, and append-only prior state.",
    boundary: "Learning HIL governs reusable project guidance only; Project HIL independently governs the candidate PV.",
    details: [
      "Present the candidate's evidence interval and distinguish observations from interpretations.",
      "Require the exact Learning decision token; never reuse or infer the Project HIL token.",
      "Publish accepted Learning to the project-isolated index only after the decision receipt passes.",
    ],
    icon: "pulse",
    color: "#efca72",
  },
  {
    id: "revoke",
    label: "Revoke",
    summary: "Remove an accepted Learning item from future retrieval without erasing its history.",
    outcome: "A revocation record that changes retrieval eligibility while retaining the original candidate and decision lineage.",
    proof: "Accepted Learning identity, revocation reason, actor, timestamp, and prior decision receipt.",
    boundary: "Revocation cannot rewrite past Project versions, Canon results, or the evidence that justified earlier work.",
    details: [
      "Verify the target belongs to the same project and is currently retrievable.",
      "Append a revocation event rather than modifying the historical acceptance record.",
      "Rebuild bounded retrieval indexes and prove the revoked item no longer appears as active guidance.",
    ],
    icon: "git",
    color: "#f2a1c5",
  },
] as const;

export default function AiLearningPage() {
  return (
    <main>
      <PageHero eyebrow="Agent Learning" title="Learning improves future work without rewriting project truth." description="Evidence Lane keeps reusable project-specific guidance in a separate authority plane. Every verified Delta contributes attributable evidence; only the final Learning candidate reaches its own human decision." aside={<HeroOrbit preset="studio" />} />

      <section className="section shell"><div className="sectionHead wideHead"><span className="kicker">Isolated lifecycle</span><h2>Learn at Delta boundaries. Decide at the final gate.</h2><p>Automatic Delta observations reduce repeated mistakes without silently accepting them as guidance. The final Learning HIL evaluates the accumulated parity evidence from the prior accepted PV to the new project boundary.</p></div><div className="routeGrid">{learningControls.map(([title, detail, icon], index) => <article className="routeCard" key={title}><span className="compactDepthPill"><GlassIconOrb color={["#a99af7", "#69d9f5", "#efca72"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span><h3>{title}</h3><p>{detail}</p></article>)}</div></section>

      <section className="section governedStoryBand"><div className="shell"><GovernedStoryExplorer eyebrow="Inspectable Learning flow" title="See how Delta evidence becomes reversible guidance." description="Open a stage to inspect its evidence interval, decision boundary, and project-isolation contract." items={learningStages} /></div></section>

      <section className="section shell depthContractGrid">
        <article><span className="kicker">Between Project HILs</span><h2>Completed Deltas produce evidence, not silent training.</h2><p>Each verified row logs one replay-safe observation before the next row begins. Later retrieval can use it with provenance, but only accepted Learning is presented as approved guidance.</p></article>
        <article><span className="kicker">Final paired decisions</span><h2>Project PV HIL and Learning HIL remain distinct.</h2><p>The Project decision governs candidate truth and pointer promotion. The Learning decision governs reusable guidance derived from the Delta interval. Neither token authorizes the other.</p></article>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">No authority collapse</span><h2>Learning is neither Canon nor accepted Project Truth.</h2><p>A Delta observation or Learning candidate cannot approve a Project candidate, decide a Canon backfire, infer HIL, install a package, merge main, or move the accepted PV pointer.</p></div></div></section>
    </main>
  );
}
