"use client";

import Image from "next/image";
import {
  useEffect,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";

import { controls, laneToolchains } from "../_data/site";

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
  "--file-index"?: number;
  "--lane-accent"?: string;
};

function LaneGlyph({ laneId, className = "" }: { laneId: string; className?: string }) {
  const common = {
    className,
    viewBox: "0 0 48 48",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  if (laneId === "discussion" || laneId === "chat_lineage") {
    return <svg {...common}><path d="M8 10h25a6 6 0 0 1 6 6v12a6 6 0 0 1-6 6H21l-9 7v-7H8a6 6 0 0 1-6-6V16a6 6 0 0 1 6-6Z" /><path d="M11 20h19M11 26h13" /></svg>;
  }
  if (laneId === "analysis" || laneId === "research") {
    return <svg {...common}><circle cx="21" cy="21" r="13" /><path d="m31 31 11 11M14 25l5-6 5 4 6-8" /></svg>;
  }
  if (laneId === "plan" || laneId === "mode") {
    return <svg {...common}><rect x="8" y="6" width="32" height="36" rx="6" /><path d="m14 16 3 3 5-6M26 17h8M14 29l3 3 5-6M26 30h8" /></svg>;
  }
  if (laneId === "local_code" || laneId === "github_code") {
    return <svg {...common}><path d="m17 13-10 11 10 11M31 13l10 11-10 11M28 7l-8 34" /></svg>;
  }
  if (laneId === "data_excel") {
    return <svg {...common}><rect x="5" y="7" width="38" height="34" rx="5" /><path d="M5 18h38M17 7v34M29 18v23M5 30h38" /></svg>;
  }
  if (laneId === "ppt") {
    return <svg {...common}><rect x="6" y="7" width="36" height="28" rx="5" /><path d="M24 35v7M16 42h16M15 27V15h7a6 6 0 0 1 0 12h-7Z" /></svg>;
  }
  if (laneId === "images_ocr") {
    return <svg {...common}><rect x="5" y="7" width="38" height="34" rx="6" /><circle cx="17" cy="18" r="4" /><path d="m8 36 10-10 7 7 6-6 9 9" /></svg>;
  }
  if (laneId === "artifacts" || laneId === "custom") {
    return <svg {...common}><path d="m24 4 17 9v22l-17 9-17-9V13l17-9Z" /><path d="m7 13 17 9 17-9M24 22v22M17 9l17 9" /></svg>;
  }
  if (laneId === "project_engulf") {
    return <svg {...common}><path d="M5 13h15l5 6h18v21H5V13Z" /><path d="M16 29h16M24 23v12" /></svg>;
  }
  if (laneId === "brain_loader" || laneId === "sqlite_brain") {
    return <svg {...common}><ellipse cx="24" cy="11" rx="16" ry="7" /><path d="M8 11v13c0 4 7 7 16 7s16-3 16-7V11M8 24v13c0 4 7 7 16 7s16-3 16-7V24" /><path d="m20 19 8 5-8 5v-10Z" /></svg>;
  }
  return <svg {...common}><path d="M12 5h17l8 8v30H12V5Z" /><path d="M29 5v9h8M18 23h13M18 30h13M18 37h9" /></svg>;
}

function BrainMark() {
  return (
    <svg className="laneBrainMark" viewBox="0 0 120 96" fill="none" aria-hidden="true">
      <path d="M58 18C49 7 31 11 29 25 15 26 11 43 21 51c-8 12 2 29 17 26 4 12 20 12 22 0V20c0-5-1-7-2-2Z" />
      <path d="M62 18C71 7 89 11 91 25c14 1 18 18 8 26 8 12-2 29-17 26-4 12-20 12-22 0V20c0-5 1-7 2-2ZM31 31c9-2 15 4 15 12M25 54c9-3 18 2 19 12M89 31c-9-2-15 4-15 12M95 54c-9-3-18 2-19 12M46 24c4 4 5 9 3 14M74 24c-4 4-5 9-3 14" />
    </svg>
  );
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
              className={`deckControl deckControl${index}${index === activeIndex ? " active" : ""}`}
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
              <span>{String(index + 1).padStart(2, "0")}</span>
              {control.name}
            </button>
          ))}
        </div>
      </div>
      <article
        className="deckDetail"
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
            className={index === activeIndex ? "active" : ""}
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
            <span>{String(index + 1).padStart(2, "0")}</span>
            {lane.name}
          </button>
        ))}
      </div>

      <article
        className="laneToolchainDetail"
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
            <button type="button" onClick={() => setFlowRun((current) => current + 1)}>Replay flow</button>
            <button
              className={autoCycle ? "active" : ""}
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
          <div className="laneSourcePill">
            <span className="laneSourceIcon"><LaneGlyph laneId={active.id} /></span>
            <span><small>Selected lane</small><strong>{active.name}</strong></span>
          </div>
          <div className="laneFlightRail" aria-hidden="true" />
          <div className="laneBrainOrb" aria-hidden="true">
            <span className="laneBrainHalo" />
            <span className="laneBrainGlass" />
            <BrainMark />
            <span className="laneBrainRim" />
            <span className="laneBrainReflection" />
          </div>
          <div className="laneFlowAnimation" key={`${active.id}-${flowRun}`} aria-hidden="true">
            <span className="laneFlyingIcon"><LaneGlyph laneId={active.id} /></span>
            <span className="laneDigestedIcon"><LaneGlyph laneId={active.id} /></span>
            <span className="laneDigestPulse laneDigestPulseOne" />
            <span className="laneDigestPulse laneDigestPulseTwo" />
            <div className="laneArtifactEmitter">
              {outputFiles.map((file, index) => (
                <span
                  className="laneArtifactFile"
                  key={file}
                  style={{ "--file-index": index } as LaneFlowStyle}
                >
                  <b>{String(index + 1).padStart(2, "0")}</b>
                  <code>{file}</code>
                  <i>sealed</i>
                </span>
              ))}
            </div>
          </div>
          <span className="cinemaStatus" aria-hidden="true">parse · chunk · index · reconcile</span>
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
      </article>
    </div>
  );
}
