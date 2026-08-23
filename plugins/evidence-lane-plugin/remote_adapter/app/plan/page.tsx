import type { Metadata } from "next";

import { CurrentExecutionPlan } from "../_components/current-execution-plan";
import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { GovernedStoryExplorer } from "../_components/governed-story-explorer";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Plan and Changes",
  description: "Evidence Lane canonical Plan SQLite, Delta-entry hydration, active-window projection, Goal binding, and Changes continuity.",
};

const planSurfaces = [
  ["Canonical ledger", "One append-only SQLite Plan retains every executable row, history row, dependency, steer, HIL boundary, and stable task ID.", "database"],
  ["Step Task List", "A compact fixed header plus the active nine-row window is projected from SQLite; the full ledger stays outside model context.", "node"],
  ["Changes ownership", "The exact Task UUID and worktree keep the native Changes surface bound until human Goal completion or verified State Travel.", "git"],
] as const;

const planStages = [
  {
    id: "query",
    label: "Query active row",
    summary: "Resolve the exact canonical task and all linked steers before implementing a new Delta.",
    outcome: "A bounded active-row contract with task ID, dependencies, allowed paths/tools, checks, HIL boundaries, and steer receipt hashes.",
    proof: "Canonical/executable Plan hashes, runtime SQLite hash, active row, batch, stable task ID, and linked-Delta set.",
    boundary: "The full Plan, raw Delta text ledger, backlog, ChatLineage, and private reasoning stay outside model context.",
    details: [
      "Read the active row from canonical SQLite rather than reconstructing it from a prior chat or visible panel.",
      "Resolve supersede, add, drop, dependency, and reorder events before deciding what the row currently means.",
      "Fail closed if the row, Goal, host Task, worktree, accepted pointer, or execution profile does not agree.",
    ],
    icon: "database",
    color: "#69d9f5",
  },
  {
    id: "hydrate",
    label: "Hydrate authority",
    summary: "Query only the relevant Project/PV sectors, Memory, Learning, Canon, and ChatLineage for that Delta.",
    outcome: "One content-addressed row-entry snapshot plus ENV/UOP, operators, and prompt entry/exit slip bindings.",
    proof: "Bounded query receipts, no-hit states, result ceilings, authority locators, and one row-entry receipt SHA-256.",
    boundary: "No failed fallback, scrollback reconstruction, cross-project disclosure, or unaccepted candidate overlay fills a missing result.",
    details: [
      "Derive bounded query terms from the exact row and steers, not from an agent's recollection.",
      "Query each relevant authority independently and preserve NO_HIT as a valid result.",
      "Bind applicable operators and entry/exit slip identities before source mutation begins.",
    ],
    icon: "node",
    color: "#a99af7",
  },
  {
    id: "project",
    label: "Project 1+9 window",
    summary: "Render one compact header and the active nine-row batch directly from live Plan authority.",
    outcome: "A persistent Step Task List with exactly one active row, stable IDs, dependencies, and current next/final HIL anchors.",
    proof: "Host projection hash, active-row match, fixed header, row count, task order, and no-reconstruction flags.",
    boundary: "Plan acceptance activates work but is not HIL, candidate acceptance, Git authority, or pointer movement.",
    details: [
      "Project the compact header plus canonical metadata-rich rows byte-for-byte from the executable window.",
      "Update the projection automatically when a governed Plan mutation changes the active batch or order.",
      "Keep the full dynamic range in SQLite even though only the bounded window is visible.",
    ],
    icon: "package",
    color: "#83ddb3",
  },
  {
    id: "bind",
    label: "Bind Goal and Changes",
    summary: "Attach the same unfinished top Goal and Changes surface to the exact task, worktree, and active row.",
    outcome: "One carried Goal, one writer, and a persistent file-change projection that survives turns, pauses, stalls, and reattach.",
    proof: "Task UUID, Goal ID, worktree identity, active-row hash, Changes display hash, and host rehydration receipt.",
    boundary: "A human-paused Goal resumes only by the human; plugin stall recovery waits for its actual missing token or approval.",
    details: [
      "Reject competing Goals and any attempt to bind Changes to another task or provider group.",
      "Keep the panels alive during normal turn continuation and rebuild their bounded projection after app reattach.",
      "Distinguish human pause from a plugin-blocked/stalled gate in the lifecycle receipt.",
    ],
    icon: "git",
    color: "#f2a1c5",
  },
  {
    id: "advance",
    label: "Verify and advance",
    summary: "Complete local verification, log the Delta Learning observation, then atomically activate the next row.",
    outcome: "An idempotent Delta-completion receipt followed by a rehydrated Goal, Step list, and Changes projection for the new row.",
    proof: "Source, test, registry, projection, and row-declared install or local-preview receipts; Learning observation hash; prior/next row IDs; and transition hash.",
    boundary: "Row completion and transition never infer HIL or move the accepted PV pointer.",
    details: [
      "Prove the row's declared checks and selected Git/install stage against the exact source bytes; LOCAL_PREVIEW_ONLY rows neither install nor commit.",
      "Seal one project-isolated Learning observation attributable to the completed Delta.",
      "Activate the next canonical row and immediately repeat the bounded row-entry hydration law.",
    ],
    icon: "pulse",
    color: "#efca72",
  },
] as const;

export default function PlanPage() {
  return (
    <main>
      <PageHero eyebrow="Plan and Changes" title="One ledger drives the Goal, Step list, and worktree surface." description="Evidence Lane reprojects rather than reconstructs. Every active-row transition starts with a bounded authority query and ends with verified Delta evidence before the next row activates." aside={<HeroOrbit preset="architecture" />} />

      <section className="section shell"><div className="sectionHead wideHead"><span className="kicker">Persistent projection</span><h2>Task state survives ordinary turns, pauses, stalls, compaction, and app reattachment.</h2><p>The visible panel is a bounded executable window. Canonical SQLite retains the full dynamic range and every linked steer, while the same unfinished Goal remains attached to the exact active row.</p></div><div className="routeGrid">{planSurfaces.map(([title, detail, icon], index) => <article className="routeCard" key={title}><span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#a99af7", "#83ddb3"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span><h3>{title}</h3><p>{detail}</p></article>)}</div></section>

      <section className="section currentExecutionPlanBand">
        <div className="shell">
          <div className="sectionHead wideHead">
            <span className="kicker">Canonical executable projection</span>
            <h2>The public Plan view is generated from the same sealed SQLite authority.</h2>
            <p>It keeps every executable row contiguous and exposes the sole active row and physically final HIL without creating a second Plan, a fallback window, or a serialized chat copy.</p>
          </div>
          <CurrentExecutionPlan />
        </div>
      </section>

      <section className="section governedStoryBand"><div className="shell"><GovernedStoryExplorer eyebrow="Delta execution protocol" title="Inspect the complete row-entry, work, and transition loop." description="Each stage exposes the exact evidence it requires and the authority effects it is forbidden to infer." items={planStages} /></div></section>

      <section className="section shell depthContractGrid">
        <article><span className="kicker">Automatic projection</span><h2>SQLite changes and host panels move together.</h2><p>A governed add, drop, supersede, dependency, or reorder event changes canonical authority first. The bounded Step list, Changes display, and Goal attachment are then reprojected from the resulting hashes.</p></article>
        <article><span className="kicker">State Travel continuity</span><h2>A new task resumes one unfinished Goal.</h2><p>The destination is genuinely new, never “Continued from chat,” and proves exact dirty-work continuity before reactivation. The source may close only its task-boundary Goal after destination Phase 5 is active.</p></article>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Human boundary</span><h2>Plan acceptance is not HIL.</h2><p>A visible or accepted host Plan, a completed Delta, an installed local layer, or a reattached Goal cannot accept a candidate, approve Git promotion, merge main, move PV, or mark the carried implementation objective complete.</p></div></div></section>
    </main>
  );
}
