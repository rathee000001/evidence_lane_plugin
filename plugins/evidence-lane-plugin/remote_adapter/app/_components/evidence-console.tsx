"use client";

import Image from "next/image";
import { useState, type KeyboardEvent } from "react";

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
  const active = laneToolchains[activeIndex];

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
            onClick={() => setActiveIndex(index)}
            onFocus={() => setActiveIndex(index)}
            onPointerEnter={() => setActiveIndex(index)}
            onKeyDown={(event) => {
              const next = nextTabIndex(event, index, laneToolchains.length);
              if (next === null) return;
              event.preventDefault();
              setActiveIndex(next);
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
        <header>
          <div>
            <span className="laneCode">{active.id}</span>
            <h3>{active.name}</h3>
            <p>{active.reason}</p>
          </div>
          <Image src="/evidence-lane-icon.png" alt="" width={1906} height={1906} />
        </header>
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
