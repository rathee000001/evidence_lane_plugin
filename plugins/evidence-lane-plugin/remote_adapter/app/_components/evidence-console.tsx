"use client";

import { motion } from "framer-motion";
import Image from "next/image";
import {
  useEffect,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";

import { controls, laneToolchains } from "../_data/site";
import {
  EvidenceBrainAsset,
  LaneAssetIcon,
  OfficialToolIcon,
  type OfficialToolIconName,
} from "./evidence-assets";

function focusIndexedControl(prefix: string, index: number) {
  document.getElementById(`${prefix}-${index}`)?.focus();
}

function nextTabIndex(
  event: KeyboardEvent<HTMLButtonElement>,
  current: number,
  total: number,
) {
  if (event.key === "Home") return 0;
  if (event.key === "End") return total - 1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    return (current + 1) % total;
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    return (current - 1 + total) % total;
  }
  return null;
}

const laneAccents = [
  "#63e6ff",
  "#f6c96d",
  "#8b9cff",
  "#57e7bc",
  "#ff8fb3",
  "#9ed368",
] as const;

type LaneFlowStyle = CSSProperties & {
  "--file-index"?: string;
  "--lane-accent"?: string;
  "--orbit-counter-end"?: string;
  "--orbit-counter-start"?: string;
  "--orbit-end"?: string;
  "--orbit-start"?: string;
  "--tool-index"?: string;
};

type LaneToolStep = {
  stage: string;
  label: string;
  tool: OfficialToolIconName;
};

function sourceToolForLane(laneId: string): OfficialToolIconName {
  if (laneId === "github_code" || laneId === "local_code") return "git";
  if (laneId === "data_excel" || laneId === "brain_loader") return "database";
  if (laneId === "pdf_ocr" || laneId === "images_ocr" || laneId === "ppt") return "media";
  if (laneId === "artifacts" || laneId === "project_engulf") return "package";
  return "node";
}

function laneToolSteps(lane: (typeof laneToolchains)[number]): LaneToolStep[] {
  return [
    { stage: "Intake", label: lane.source, tool: sourceToolForLane(lane.id) },
    { stage: "Parse", label: lane.parser, tool: "python" },
    { stage: "Index", label: lane.chunker, tool: "database" },
    { stage: "Topology", label: "Mermaid and DOT reconciliation", tool: "media" },
    { stage: "Retrieve", label: lane.retrieval, tool: "terminal" },
    { stage: "Seal", label: "Pointer, manifest, and receipt", tool: "package" },
  ];
}

export function UniversalCommandDeck() {
  const [activeIndex, setActiveIndex] = useState(0);
  const active = controls[activeIndex];

  return (
    <div className="commandDeckLayout">
      <div className="commandDeckScene">
        <span className="deckRing deckRingOuter" />
        <span className="deckRing deckRingInner" />
        <div className="deckCore" aria-hidden="true">
          <Image src="/evidence-lane-icon.png" alt="" width={1906} height={1906} />
          <span>Universal control plane</span>
        </div>
        <div className="deckControls" role="tablist" aria-label="Evidence Lane controls">
          {controls.map((control, index) => (
            <button
              className={`rilPill deckControl deckControl${index}${index === activeIndex ? " active" : ""}`}
              id={`deck-control-${index}`}
              key={control.name}
              type="button"
              role="tab"
              aria-selected={index === activeIndex}
              aria-controls="command-deck-detail"
              tabIndex={index === activeIndex ? 0 : -1}
              onClick={() => setActiveIndex(index)}
              onFocus={() => setActiveIndex(index)}
              onPointerEnter={() => setActiveIndex(index)}
              onKeyDown={(event) => {
                const next = nextTabIndex(event, index, controls.length);
                if (next === null) return;
                event.preventDefault();
                setActiveIndex(next);
                focusIndexedControl("deck-control", next);
              }}
            >
                <span className="rilIconBadge" aria-hidden="true">
                  <span className="deckControlIndex">{String(index + 1).padStart(2, "0")}</span>
                </span>
                <span>{control.name}</span>
            </button>
          ))}
        </div>
      </div>
      <article className="deckDetail rilSectionPanel" aria-label={`${active.name} universal control detail`}>
        <div
        id="command-deck-detail"
        role="tabpanel"
        aria-labelledby={`deck-control-${activeIndex}`}
        >
        <span className="deckDetailIndex">CONTROL {String(activeIndex + 1).padStart(2, "0")}</span>
        <h3>{active.name}</h3>
        <p>{active.detail}</p>
        <dl>
          <div><dt>Produces</dt><dd>{active.result}</dd></div>
          <div><dt>Boundary</dt><dd>{active.guardrail}</dd></div>
        </dl>
        <small>Hover, focus, or use arrow keys to inspect each control.</small>
        </div>
      </article>
    </div>
  );
}

export function LaneToolchainExplorer() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [flowRun, setFlowRun] = useState(0);
  const [autoCycle, setAutoCycle] = useState(false);
  const active = laneToolchains[activeIndex];
  const accent = laneAccents[activeIndex % laneAccents.length];
  const toolSteps = laneToolSteps(active);
  const outputFiles = [
    `${active.id}_sector_v001.sqlite`,
    `${active.id}.mmd`,
    `${active.id}.dot`,
    "refresh_receipt.json",
  ];

  useEffect(() => {
    if (!autoCycle) return undefined;
    const interval = window.setInterval(() => {
      setActiveIndex((current) => (current + 1) % laneToolchains.length);
      setFlowRun((current) => current + 1);
    }, 6200);
    return () => window.clearInterval(interval);
  }, [autoCycle]);

  const activateLane = (index: number) => {
    if (index !== activeIndex) setFlowRun((current) => current + 1);
    setActiveIndex(index);
  };

  return (
    <div className="laneExplorer">
      <div className="lanePicker" role="tablist" aria-label="Canonical Evidence Lane toolchains">
        {laneToolchains.map((lane, index) => (
          <button
            className={`rilPill${index === activeIndex ? " active" : ""}`}
            id={`lane-tool-${index}`}
            key={lane.id}
            type="button"
            role="tab"
            aria-selected={index === activeIndex}
            aria-controls="lane-toolchain-detail"
            tabIndex={index === activeIndex ? 0 : -1}
            onClick={() => activateLane(index)}
            onFocus={() => activateLane(index)}
            onPointerEnter={() => activateLane(index)}
            onKeyDown={(event) => {
              const next = nextTabIndex(event, index, laneToolchains.length);
              if (next === null) return;
              event.preventDefault();
                activateLane(next);
                focusIndexedControl("lane-tool", next);
            }}
          >
            <span className="rilIconBadge" aria-hidden="true">
              <LaneAssetIcon lane={lane.id} size={21} decorative />
            </span>
            <span className="lanePickerIndex">{String(index + 1).padStart(2, "0")}</span>
            <span className="lanePickerLabel">{lane.name}</span>
          </button>
        ))}
      </div>

      <article className="laneToolchainDetail rilSectionPanel" aria-label={`${active.name} Evidence Lane toolchain`}>
        <div
        id="lane-toolchain-detail"
        role="tabpanel"
        aria-labelledby={`lane-tool-${activeIndex}`}
        >
        <header className="laneToolchainHeader">
          <div>
            <span className="laneCode">{active.id}</span>
            <h3>{active.name}</h3>
            <p>{active.reason}</p>
          </div>
          <div className="laneFlowControls" role="group" aria-label="Toolchain animation controls">
            <button className="rilPill" type="button" onClick={() => setFlowRun((current) => current + 1)}>Replay flow</button>
            <button
              className={`rilPill${autoCycle ? " active" : ""}`}
              type="button"
              aria-pressed={autoCycle}
              onClick={() => setAutoCycle((current) => !current)}
            >Auto cycle</button>
          </div>
        </header>

        <div
          className="toolchainCinema"
          style={{ "--lane-accent": accent } as LaneFlowStyle}
          aria-label={`${active.name} icon enters the glass brain and emits four sealed files`}
        >
          <div className="cinemaTopline"><span>INPUT ICON</span><i /><span>BOUNDED PROCESS</span><i /><span>4 SEALED FILES</span></div>
          <div className="laneToolOrbit" aria-label={`${active.name} official toolchain`}>
            {toolSteps.map((step, index) => (
              <motion.div
                className="laneToolCard"
                initial={{ opacity: 0, y: -18, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ delay: index * 0.055, duration: 0.42 }}
                whileHover={{ y: -5, scale: 1.025 }}
                tabIndex={0}
                key={`${active.id}-${step.stage}`}
              >
                <span className="rilIconBadge" aria-hidden="true">
                  <OfficialToolIcon tool={step.tool} size={25} decorative />
                </span>
                <span><small>{step.stage}</small><strong>{step.label}</strong></span>
              </motion.div>
            ))}
          </div>
          <div className="laneSourcePill rilPill">
            <span className="rilIconBadge laneSourceIcon" aria-hidden="true">
              <LaneAssetIcon lane={active.id} size={26} decorative />
            </span>
            <span><small>Selected lane</small><strong>{active.name}</strong></span>
          </div>
          <div className="laneFlightRail" aria-hidden="true" />
          <div className="laneBrainOrb" aria-hidden="true">
            <EvidenceBrainAsset color={accent} label="" />
          </div>
          <div className="laneFlowAnimation" key={`${active.id}-${flowRun}`} aria-hidden="true">
            {toolSteps.map((step, index) => (
              <span
                className="laneFlyingIcon"
                key={`${active.id}-${step.stage}-flight`}
                style={{
                  "--tool-index": String(index),
                  "--orbit-start": `${index * 60}deg`,
                  "--orbit-end": `${index * 60 + 360}deg`,
                  "--orbit-counter-start": `${index * -60}deg`,
                  "--orbit-counter-end": `${(index * 60 + 360) * -1}deg`,
                } as LaneFlowStyle}
              >
                <OfficialToolIcon tool={step.tool} size={30} decorative />
              </span>
            ))}
            <span className="laneDigestedIcon"><OfficialToolIcon tool="pulse" size={31} decorative /></span>
            <span className="laneDigestPulse laneDigestPulseOne" />
            <span className="laneDigestPulse laneDigestPulseTwo" />
            <div className="laneArtifactEmitter">
              {outputFiles.map((file, index) => (
                <span
                  className="laneArtifactFile"
                  key={file}
                  style={{ "--file-index": String(index) } as LaneFlowStyle}
                >
                  <b>{String(index + 1).padStart(2, "0")}</b>
                  <code>{file}</code>
                  <i>sealed</i>
                </span>
              ))}
            </div>
          </div>
          <span className="cinemaStatus" aria-hidden="true">enter · orbit · digest · emit</span>
        </div>

        <div className="toolchainStory" aria-label={`${active.name} toolchain sequence`}>
          {active.story.map((step, index) => (
            <div key={step}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{step}</strong>
            </div>
          ))}
        </div>
        <dl className="toolchainSpecs">
          <div><dt>Source</dt><dd>{active.source}</dd></div>
          <div><dt>Parser</dt><dd><code>{active.parser}</code></dd></div>
          <div><dt>Chunker</dt><dd><code>{active.chunker}</code></dd></div>
          <div><dt>Retrieval</dt><dd><code>{active.retrieval}</code></dd></div>
        </dl>
        <footer>
          <span>SQLite</span><i />
          <span>MMD</span><i />
          <span>DOT</span><i />
          <span>Pointer</span><i />
          <span>Receipt</span>
        </footer>
        </div>
      </article>
    </div>
  );
}
