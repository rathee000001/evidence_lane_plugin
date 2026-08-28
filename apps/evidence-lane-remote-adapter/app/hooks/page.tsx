import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "Hooks",
  description: "Evidence Lane's registry-derived Codex lifecycle hook events and transport boundary.",
};

const hookDetails: Record<string, string> = {
  SessionStart: "A governed task or session begins.",
  SubagentStart: "A bound subagent starts; Evidence Lane observes without controlling it.",
  UserPromptSubmit: "The user's visible prompt enters the host lifecycle.",
  PreToolUse: "A tool call is about to execute.",
  PermissionRequest: "The host requests permission; Evidence Lane never grants or denies it.",
  PostToolUse: "A tool call returned a visible result.",
  PreCompact: "The host is about to compact task context.",
  PostCompact: "The host completed context compaction.",
  SubagentStop: "A bound subagent stops; Evidence Lane observes without continuing it.",
  Stop: "The visible assistant turn is ending.",
  SessionEnd: "Best-effort main-thread session closure.",
};

type HookHandler = { command: string; type: string };
type HookGroup = { hooks: HookHandler[]; matcher?: string };
type HookManifest = { hooks: Record<string, HookGroup[]> };

const hookManifest = JSON.parse(
  readFileSync(
    resolve(
      process.cwd(),
      "../../plugins/evidence-lane-plugin/hooks/hooks.json",
    ),
    "utf8",
  ),
) as HookManifest;
const hookEvents = Object.entries(hookManifest.hooks).map(([name, groups]) => ({
  name,
  detail: hookDetails[name] ?? "A registry-declared Codex lifecycle event.",
  handlers: groups.flatMap((group) => group.hooks),
}));
const handlerCount = hookEvents.reduce((total, event) => total + event.handlers.length, 0);

function handlerIdentity(command: string, index: number) {
  return command.match(/--handler\s+([^\s"]+)/)?.[1] ?? `command-${index + 1}`;
}

export default function HooksPage() {
  return (
    <main>
      <PageHero
        eyebrow="Lifecycle hooks"
        title={`${hookEvents.length} registry events. Governance stays in skills.`}
        description="Hooks carry bounded visible lifecycle facts into the package. They do not classify task authority, refresh Plan Lane, decide HIL, or move project truth."
        aside={<HeroOrbit preset="hooks" />}
      />

      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">Declared event matrix</span><h2>{hookEvents.length} events and {handlerCount} command handlers, derived from the package registry.</h2><p>Declaration, trust, enablement, invocation, and usage are separate states. Package tests are necessary but not enough: installed-host receipts must prove the rest.</p></div>
        <div aria-label="Scrollable Evidence Lane hook registry" role="region" tabIndex={0} style={{ maxHeight: "42rem", overflowY: "auto", overscrollBehavior: "contain", paddingInlineEnd: "0.5rem" }}>
          <div className="routeGrid">
          {hookEvents.map(({ name, detail, handlers }, index) => (
            <article className="routeCard" key={name} id={`hook-event-${name.toLowerCase()}`}>
              <span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#83ddb3", "#a99af7", "#efca72"][index % 4]} size={28} decorative><OfficialToolIcon tool="pulse" size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span>
              <h3>{name}</h3><p>{detail}</p>
              <p><strong>{handlers.length} handler{handlers.length === 1 ? "" : "s"}</strong> · declared only · installed trust and enablement require a live receipt</p>
              <ul>
                {handlers.map((handler, handlerIndex) => (
                  <li key={`${name}-${handlerIdentity(handler.command, handlerIndex)}`}>
                    Handler {handlerIndex + 1}: <code>{handlerIdentity(handler.command, handlerIndex)}</code> · usage not attested
                  </li>
                ))}
              </ul>
            </article>
          ))}
          </div>
        </div>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Windows behavior</span><h2>Background helpers stay hidden and observable through receipts.</h2><p>Python hooks and tunnel helpers must not flash transient console windows. Persistent services stay hidden; bounded processes use no-window launch flags and still return explicit health or failure evidence.</p></div></div></section>

      <section className="section shell"><div className="sectionHead wideHead"><span className="kicker">Corrected steady state</span><h2>All eleven stay ON after the installed-host matrix passes.</h2><p>The progressive native route enables and proves every event, then reads the complete installed matrix back. If one event later fails, only that hook is turned off through compare-and-swap while the other passing hooks and active Goal continue. The failed event is repaired, retested, and re-enabled; upgrades preserve the verified state instead of silently resetting it.</p></div></section>
    </main>
  );
}
