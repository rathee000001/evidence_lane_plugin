"use client";

import { useState, type KeyboardEvent } from "react";

import {
  GlassIconOrb,
  OfficialToolIcon,
  type OfficialToolIconName,
} from "./evidence-assets";

type HilChoice = {
  token: string;
  lane_effect: string;
  requires: string;
};

type Operator = {
  operator_id: number;
  family: string;
  chapter: string;
  effect: string;
};

type ModeVariant = {
  selection_source: string;
  request_sha256: string;
  operator_receipt_sha256: string;
  formula_display: string;
  formula: { rule: string; authority: string; visible_in_response: boolean };
  recursive_loop: string;
  scan_order: string[];
  unit_of_work: string;
  validation_gate: string;
  exit_write_target: string;
  operator_law: string;
  operators: Operator[];
  operator_families: string[];
  ci_cd: {
    required: boolean;
    loop: string | null;
    controlled: boolean;
    autonomous_flash_fuse_deploy_allowed: boolean;
    authority: string;
  };
  hil: {
    accepted_object: string;
    authority: string;
    choices: HilChoice[];
    implicit_promotion_allowed: boolean;
    mode_selection_is_not_hil_approval: boolean;
  };
  lifecycle_effect: string;
  candidate_created: boolean;
  pointer_moved: boolean;
};

type ModeGuide = {
  id: string;
  runtime_mode_id: string;
  name: string;
  routed_lanes: string[];
  env_authority: {
    env_sqlite_sha256: string;
    uop_sqlite_sha256: string;
    mode_policy_projection_sha256: string;
    policy_row: string;
  };
  variants: { plugin: ModeVariant; prompt: ModeVariant };
};

type OperatorGuide = {
  export_sha256: string;
  mode_count: number;
  modes: ModeGuide[];
  six_way_token_vocabulary: string[];
  source_files: Record<string, string>;
  universal_boundary: string;
};

type SelectionOrigin = "plugin" | "prompt";

const modeIdentity: Record<string, { color: string; icon: OfficialToolIconName }> = {
  D: { color: "#69d9f5", icon: "node" },
  AL: { color: "#8b9cff", icon: "python" },
  PL: { color: "#efca72", icon: "package" },
  CD: { color: "#efca72", icon: "terminal" },
  OP: { color: "#83ddb3", icon: "pulse" },
  VAL: { color: "#9ed368", icon: "package" },
  RS: { color: "#8b9cff", icon: "python" },
  JD: { color: "#f2a1c5", icon: "media" },
  XL: { color: "#83ddb3", icon: "database" },
  PPT: { color: "#f2a1c5", icon: "media" },
  DOC: { color: "#69d9f5", icon: "media" },
  PB: { color: "#83ddb3", icon: "database" },
  ENG: { color: "#b6a0ff", icon: "package" },
  CE: { color: "#9ed368", icon: "git" },
  RCV: { color: "#f2a1c5", icon: "pulse" },
  X: { color: "#b6a0ff", icon: "node" },
};

function nextModeIndex(
  event: KeyboardEvent<HTMLButtonElement>,
  index: number,
  count: number,
) {
  if (event.key === "Home") return 0;
  if (event.key === "End") return count - 1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    return (index + 1) % count;
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    return (index - 1 + count) % count;
  }
  return null;
}

export function ModeOperatorExplorer({ data }: { data: OperatorGuide }) {
  const [activeId, setActiveId] = useState("CD");
  const [origin, setOrigin] = useState<SelectionOrigin>("plugin");
  const active = data.modes.find((mode) => mode.id === activeId) ?? data.modes[0];
  const contract = active.variants[origin];

  return (
    <div className="operatorExplorer">
      <header className="operatorExplorerHeader">
        <div>
          <span className="kicker">Executable ENV/UOP projection</span>
          <h2>{data.mode_count} modes. No generic substitute.</h2>
          <p>{data.universal_boundary}</p>
        </div>
        <dl className="operatorExportSeal">
          <div><dt>Export</dt><dd>{data.export_sha256.slice(0, 16)}</dd></div>
          <div><dt>Tokens</dt><dd>{data.six_way_token_vocabulary.length} exact</dd></div>
          <div><dt>Sources</dt><dd>{Object.keys(data.source_files).length} hashed</dd></div>
        </dl>
      </header>

      <div className="operatorModeTabs" role="tablist" aria-label="Operating modes">
        {data.modes.map((mode, index) => (
          (() => {
            const identity = modeIdentity[mode.id] ?? { color: "#69d9f5", icon: "pulse" as const };
            return (
          <button
            aria-controls="operator-mode-panel"
            aria-selected={mode.id === active.id}
            className={`rilPill${mode.id === active.id ? " active" : ""}`}
            id={`operator-mode-tab-${mode.id}`}
            key={mode.id}
            onClick={() => setActiveId(mode.id)}
            onKeyDown={(event) => {
              const target = nextModeIndex(event, index, data.modes.length);
              if (target === null) return;
              event.preventDefault();
              const targetMode = data.modes[target];
              setActiveId(targetMode.id);
              document.getElementById(`operator-mode-tab-${targetMode.id}`)?.focus();
            }}
            role="tab"
            tabIndex={mode.id === active.id ? 0 : -1}
            type="button"
          >
            <GlassIconOrb color={identity.color} size={32} decorative>
              <OfficialToolIcon tool={identity.icon} size={17} decorative />
            </GlassIconOrb>
            <span>{mode.name}</span>
          </button>
            );
          })()
        ))}
      </div>

      <section
        aria-labelledby={`operator-mode-tab-${active.id}`}
        className="operatorModePanel"
        id="operator-mode-panel"
        role="tabpanel"
      >
        <div className="operatorPanelTopline">
          <div>
            <span>{active.runtime_mode_id}</span>
            <h3>{active.name}</h3>
            <p>{contract.hil.accepted_object}</p>
          </div>
          <div className="selectionOrigin" role="group" aria-label="Mode selection origin">
            <button
              className={`universal-pill${origin === "plugin" ? " active" : ""}`}
              onClick={() => setOrigin("plugin")}
              type="button"
            ><GlassIconOrb color="#b6a0ff" size={28} decorative><OfficialToolIcon tool="package" size={15} decorative /></GlassIconOrb><span>Plugin / API</span></button>
            <button
              className={`universal-pill${origin === "prompt" ? " active" : ""}`}
              onClick={() => setOrigin("prompt")}
              type="button"
            ><GlassIconOrb color="#83ddb3" size={28} decorative><OfficialToolIcon tool="pulse" size={15} decorative /></GlassIconOrb><span>Prompt inferred</span></button>
          </div>
        </div>

        <div className="formulaConsole">
          <div><span />Source-backed response formula</div>
          <code>{contract.formula_display}</code>
          <dl>
            <div><dt>Selection</dt><dd>{contract.selection_source}</dd></div>
            <div><dt>Receipt</dt><dd>{contract.operator_receipt_sha256}</dd></div>
            <div><dt>Request</dt><dd>{contract.request_sha256}</dd></div>
          </dl>
        </div>

        <div className="operatorContractGrid">
          <article className="operatorLoopCard">
            <span>Recursive lane loop</span>
            <div className="operatorLoopSteps">
              {contract.recursive_loop.split(" -> ").map((step, index) => (
                <div key={`${step}-${index}`}>
                  <b>{String(index + 1).padStart(2, "0")}</b>
                  <strong>{step}</strong>
                </div>
              ))}
            </div>
          </article>
          <article className="operatorFactsCard">
            <span>Mode boundary</span>
            <dl>
              <div><dt>Unit</dt><dd>{contract.unit_of_work}</dd></div>
              <div><dt>Gate</dt><dd>{contract.validation_gate}</dd></div>
              <div><dt>Exit write</dt><dd>{contract.exit_write_target}</dd></div>
              <div><dt>CI/CD</dt><dd>{contract.ci_cd.required ? "Controlled and required" : "Mode-specific; not generic"}</dd></div>
              <div><dt>Pointer</dt><dd>{contract.pointer_moved ? "Moved" : "Unchanged"}</dd></div>
            </dl>
          </article>
        </div>

        <div className="operatorLaneStrip" aria-label="Routed Evidence Lanes">
          {active.routed_lanes.map((lane) => <span key={lane}>{lane}</span>)}
        </div>

        <section className="operatorRoster">
          <div className="operatorSectionHead">
            <div><span className="kicker">PCM / MBA / UOP operators</span><h3>Only this mode&apos;s operators load.</h3></div>
            <p>{contract.operator_law}</p>
          </div>
          {contract.operators.length ? (
            <div className="operatorRosterGrid">
              {contract.operators.map((operator) => (
                <article key={operator.operator_id}>
                  <span>{operator.family} · {operator.operator_id}</span>
                  <h4>{operator.chapter}</h4>
                  <p>{operator.effect}</p>
                </article>
              ))}
            </div>
          ) : (
            <div className="operatorEmpty">A user-defined mode must name its dependency policy before any operator route is allowed.</div>
          )}
        </section>

        <section className="modeHilSection">
          <div className="operatorSectionHead">
            <div><span className="kicker">Lane-correct six-way HIL</span><h3>Same tokens. Different governed meaning.</h3></div>
            <p>Mode selection is not approval, and no token implicitly promotes a candidate.</p>
          </div>
          <div className="modeHilGrid">
            {contract.hil.choices.map((choice, index) => (
              <article key={choice.token}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <code>{choice.token}</code>
                <p>{choice.lane_effect}</p>
                <small>Requires: {choice.requires}</small>
              </article>
            ))}
          </div>
        </section>

        <footer className="operatorAuthorityFooter">
          <div><span>ENV15</span><code>{active.env_authority.env_sqlite_sha256}</code></div>
          <div><span>UOP15</span><code>{active.env_authority.uop_sqlite_sha256}</code></div>
          <div><span>Policy row</span><code>{active.env_authority.policy_row}</code></div>
        </footer>
      </section>
    </div>
  );
}
