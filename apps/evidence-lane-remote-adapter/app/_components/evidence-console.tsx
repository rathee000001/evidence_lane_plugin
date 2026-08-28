"use client";

import { motion } from "framer-motion";
import { useCallback, useState, type KeyboardEvent } from "react";

import { laneRuntimeContracts, universalLaneSchema } from "../_data/lane-contracts";
import { laneToolchains } from "../_data/site";
import {
  GlassIconOrb,
  GlassPill,
  OfficialToolIcon,
  PulsatingBrain,
} from "./evidence-assets";
import { GovernedPopup } from "./governed-popup";
import { SourceLaneIcon } from "./source-lane-icon";

const laneAccents = [
  "#63e6ff",
  "#f6c96d",
  "#8b9cff",
  "#57e7bc",
  "#ff8fb3",
  "#9ed368",
] as const;

function nextTabIndex(
  event: KeyboardEvent<HTMLButtonElement>,
  current: number,
  total: number,
) {
  if (event.key === "Home") return 0;
  if (event.key === "End") return total - 1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") return (current + 1) % total;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") return (current - 1 + total) % total;
  return null;
}

export function LaneToolchainExplorer() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [popupOpen, setPopupOpen] = useState(false);
  const active = laneToolchains[activeIndex];
  const runtime = laneRuntimeContracts[active.id];
  const accent = laneAccents[activeIndex % laneAccents.length];
  const processSteps = [
    ...active.story,
    "Reconcile SQLite, Mermaid, and DOT identity",
    "Seal the inspectable lane contract with receipts",
  ];

  const activateLane = (index: number) => {
    setActiveIndex(index);
    setPopupOpen(true);
  };
  const closePopup = useCallback(() => setPopupOpen(false), []);

  return (
    <div className="laneExplorer">
      <div
        className="lanePicker"
        role="tablist"
        aria-label="Canonical Evidence Lane toolchains"
        data-universal-pill-cluster="website-source-intake"
      >
        {laneToolchains.map((lane, index) => {
          const laneColor = laneAccents[index % laneAccents.length];
          return (
            <GlassPill
              id={`lane-tool-${index}`}
              className="lanePickerPill"
              key={lane.id}
              active={index === activeIndex && popupOpen}
              tone={index === activeIndex && popupOpen ? "cyan" : "neutral"}
              role="tab"
              aria-selected={index === activeIndex && popupOpen}
              aria-expanded={index === activeIndex && popupOpen}
              aria-controls="lane-toolchain-detail"
              tabIndex={index === activeIndex ? 0 : -1}
              leading={
                <GlassIconOrb className="source-lane-orb" color={laneColor} size={38} decorative>
                  <SourceLaneIcon lane={lane.id} size={22} decorative />
                </GlassIconOrb>
              }
              onClick={() => activateLane(index)}
              onFocus={() => setActiveIndex(index)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  closePopup();
                  return;
                }
                const next = nextTabIndex(event, index, laneToolchains.length);
                if (next === null) return;
                event.preventDefault();
                setActiveIndex(next);
                document.getElementById(`lane-tool-${next}`)?.focus();
              }}
            >
              <span>
                <b>{String(index + 1).padStart(2, "0")} · {lane.name}</b>
                <small>{runtime.schemaAdditions.length} lane tables</small>
              </span>
            </GlassPill>
          );
        })}
      </div>

      <GovernedPopup
        labelledBy={`lane-tool-${activeIndex}`}
        onClose={closePopup}
        open={popupOpen}
        panelId="lane-toolchain-detail"
        size="wide"
      >
        <motion.article
          className="laneToolchainDetail"
          key={active.id}
          role="document"
          aria-label={`${active.name} Evidence Lane toolchain`}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.14 }}
        >
        <header className="laneToolchainHeader">
          <div className="laneIdentity">
            <GlassIconOrb className="source-lane-orb" color={accent} size={58} decorative>
              <SourceLaneIcon lane={active.id} size={31} decorative />
            </GlassIconOrb>
            <div>
              <span className="laneCode">{active.id}</span>
              <h3>{active.name}</h3>
              <p>{active.reason}</p>
            </div>
          </div>
          <div className="laneCommandBlock">
            <span>Exact route</span>
            <code>{runtime.command}</code>
          </div>
        </header>

        <section className="laneToolFlow" aria-label={`${active.name} current tool roster and brain flow`}>
          <div className="laneToolRoster">
            <span className="lanePanelLabel">Current tools · required and fail-visible optional</span>
            <div>
              {runtime.tools.map((tool) => (
                <article className="laneToolChip" key={`${active.id}-${tool.name}`}>
                  <GlassIconOrb color={tool.availability === "required" ? accent : "#b8c8d3"} size={36} decorative>
                    <OfficialToolIcon tool={tool.icon} size={20} decorative />
                  </GlassIconOrb>
                  <span><strong>{tool.name}</strong><small>{tool.role}</small></span>
                  <i>{tool.availability}</i>
                </article>
              ))}
            </div>
          </div>

          <div className="laneFlowConnector" aria-hidden="true"><i /><i /><i /></div>

          <div className="laneBrainStage">
            <PulsatingBrain size="min(340px, 70vw)" color={accent} decorative />
            <span>One governed brain</span>
            <small>parse · index · topology · retrieve · seal</small>
          </div>

        </section>

        <section className="laneWorkingSequence" aria-label={`${active.name} working sequence`}>
          <span className="lanePanelLabel">Working sequence</span>
          <div>
            {processSteps.map((step, index) => (
              <article key={step}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <p>{step}</p>
              </article>
            ))}
          </div>
        </section>

        <div className="laneInspectionGrid">
          <section className="laneSettingsPanel">
            <span className="lanePanelLabel">Lane settings</span>
            <dl>
              <div><dt>Source</dt><dd>{active.source}</dd></div>
              <div><dt>Parser</dt><dd><code>{active.parser}</code></dd></div>
              <div><dt>Chunker</dt><dd><code>{active.chunker}</code></dd></div>
              <div><dt>Retrieval</dt><dd><code>{active.retrieval}</code></dd></div>
              <div><dt>Mutation</dt><dd><code>{runtime.mutation}</code></dd></div>
              <div><dt>Table count</dt><dd>{universalLaneSchema.length + runtime.schemaAdditions.length} total</dd></div>
            </dl>
          </section>

          <section className="laneSchemaPanel">
            <header>
              <div><span className="lanePanelLabel">SQLite schema</span><h4>Universal + {active.name}</h4></div>
              <span>{universalLaneSchema.length} core · {runtime.schemaAdditions.length} lane</span>
            </header>
            <div className="laneSchemaGroup">
              <strong>Universal schema contract</strong>
              <div>{universalLaneSchema.map((table) => <code key={table}>{table}</code>)}</div>
            </div>
            <div className="laneSchemaGroup laneSchemaGroupSpecific">
              <strong>{active.name} additions</strong>
              <div>{runtime.schemaAdditions.map((table) => <code key={table}>{table}</code>)}</div>
            </div>
          </section>
        </div>
        </motion.article>
      </GovernedPopup>
      {!popupOpen ? <p className="lanePopupHint">Choose a lane pill to open its tools, settings, and schema. Downloadable dummy proofs live on the Proof page.</p> : null}
    </div>
  );
}
