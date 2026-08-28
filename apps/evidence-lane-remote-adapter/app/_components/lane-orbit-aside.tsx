"use client";

import {
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";

import { GlassIconOrb } from "./evidence-assets";
import { SourceLaneIcon } from "./source-lane-icon";

const lanes = [
  ["discussion", "Discussion"],
  ["analysis", "Analysis"],
  ["plan", "Plan"],
  ["mode", "Mode"],
  ["local_code", "Local Code"],
  ["github_code", "GitHub Code"],
  ["docs", "Documents"],
  ["data_excel", "Data / Excel"],
  ["ppt", "Presentations"],
  ["pdf_ocr", "PDF / OCR"],
  ["images_ocr", "Images / OCR"],
  ["artifacts", "Artifacts"],
  ["custom", "Custom"],
  ["brain_loader", "Brain Loader"],
  ["research", "Research"],
  ["project_engulf", "Project Engulf"],
  ["sqlite_brain", "SQLite Brain"],
  ["chat_lineage", "Chat Lineage"],
] as const;

const accents = ["#63daf3", "#efc766", "#8d9df7", "#58dcb1", "#ed8db0", "#9fd26c"] as const;

type OrbitStyle = CSSProperties & {
  "--lane-angle": string;
  "--lane-angle-negative": string;
};

type WheelStyle = CSSProperties & {
  "--lane-orbit-rotation": string;
  "--lane-orbit-counter-rotation": string;
};

export function LaneOrbitAside() {
  const [rotation, setRotation] = useState(0);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ pointerId: number; startX: number; startRotation: number } | null>(null);
  const boundedRotation = (value: number) => Math.max(-1080, Math.min(1080, value));
  const style: WheelStyle = {
    "--lane-orbit-rotation": `${rotation}deg`,
    "--lane-orbit-counter-rotation": `${-rotation}deg`,
  };
  const beginDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (
      event.button !== 0 ||
      (event.target instanceof Element && event.target.closest("button, a, input, select, textarea"))
    ) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerId: event.pointerId, startX: event.clientX, startRotation: rotation };
    setDragging(true);
  };
  const moveDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    setRotation(boundedRotation(drag.current.startRotation + (event.clientX - drag.current.startX) * 0.5));
  };
  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
    setDragging(false);
  };
  const rotateFromWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    setRotation((value) => boundedRotation(value + (event.deltaX + event.deltaY) * 0.09));
  };
  const rotateFromKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
    event.preventDefault();
    setRotation((value) => event.key === "Home" ? 0 : boundedRotation(value + (event.key === "ArrowRight" ? 12 : -12)));
  };

  return (
    <div
      className="laneOrbitAside"
      aria-label={`${lanes.length} canonical Evidence Lane source lanes. Drag or wheel the ring, use left and right arrows, or press Home to reset.`}
      data-dragging={dragging ? "true" : "false"}
      onKeyDown={rotateFromKeyboard}
      onPointerCancel={endDrag}
      onPointerDown={beginDrag}
      onPointerMove={moveDrag}
      onPointerUp={endDrag}
      onWheel={rotateFromWheel}
      role="group"
      tabIndex={0}
    >
      <div className="numberAside laneOrbitCore">
        <strong>{lanes.length}</strong>
        <span>canonical lanes</span>
        <small>drag · wheel · arrow keys</small>
        <button
          onClick={(event) => {
            event.stopPropagation();
            drag.current = null;
            setDragging(false);
            setRotation(0);
          }}
          onPointerDown={(event) => event.stopPropagation()}
          type="button"
        >
          Reset ring
        </button>
      </div>
      <div className="laneOrbitWheel" aria-hidden="true" style={style}>
        {lanes.map(([lane, label], index) => {
          const angle = index * (360 / lanes.length);
          const style: OrbitStyle = {
            "--lane-angle": `${angle}deg`,
            "--lane-angle-negative": `${-angle}deg`,
          };
          return (
            <span className="laneOrbitItem" style={style} title={label} key={lane}>
              <span className="laneOrbitGlyph">
                <GlassIconOrb className="source-lane-orb" color={accents[index % accents.length]} size="clamp(32px,3vw,43px)" decorative>
                  <SourceLaneIcon lane={lane} size="56%" decorative />
                </GlassIconOrb>
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
