"use client";

import Image from "next/image";
import { useEffect, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { createPortal } from "react-dom";

import proofIndexJson from "../_data/dummy-lane-artifacts.json";
import { GlassIconOrb, GlassPill, OfficialToolIcon } from "./evidence-assets";
import { SourceLaneIcon } from "./source-lane-icon";

type ProofArtifact = {
  bytes: number;
  filename: string;
  kind: "sqlite" | "mmd" | "dot" | "receipt";
  label: string;
  sha256: string;
  url: string;
};

type LaneProof = {
  ordinal: number;
  lane_id: string;
  label: string;
  command: string;
  parser_id: string;
  chunker_version: string;
  fts_table: string;
  schema_table_count: number;
  schema_tables: string[];
  graph_profile: string;
  topology_generator_sha256: string;
  canonical_artifacts: ProofArtifact[];
  render: {
    bytes: number;
    filename: string;
    height: number;
    kind: "mmd_8k_png";
    label: string;
    sha256: string;
    source_mmd_sha256: string;
    url: string;
    width: number;
  };
  vector_render: {
    bytes: number;
    filename: string;
    kind: "mmd_vector_svg";
    label: string;
    sha256: string;
    source_mmd_sha256: string;
    url: string;
  };
  mmd_preview: string;
};

type ProofIndex = {
  schema: string;
  lane_count: number;
  canonical_file_count_per_lane: number;
  derived_render_count_per_lane: number;
  artifact_storage: string;
  fixture_boundary: string;
  lanes: LaneProof[];
};

const proofIndex = proofIndexJson as ProofIndex;
const accents = ["#62dff6", "#efc668", "#8c9df7", "#55dab3", "#ef8eb0", "#9fd16f"] as const;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 128;
const BUTTON_ZOOM_RATIO = 1.35;

function clampZoom(value: number) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function iconFor(kind: ProofArtifact["kind"]) {
  if (kind === "sqlite") return "database" as const;
  if (kind === "receipt") return "package" as const;
  return "media" as const;
}

export function LaneProofExplorer() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ pointerId: number; x: number; y: number; panX: number; panY: number } | null>(null);
  const closeButton = useRef<HTMLButtonElement | null>(null);
  const lane = proofIndex.lanes[activeIndex];
  const accent = accents[activeIndex % accents.length];
  const vectorSrc = `${lane.vector_render.url}?sha256=${lane.vector_render.sha256}`;

  const closeViewer = () => {
    setViewerOpen(false);
    setDragging(false);
    drag.current = null;
  };

  const resetViewer = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const openViewer = () => {
    resetViewer();
    setViewerOpen(true);
  };

  const multiplyZoom = (ratio: number) => setZoom((current) => clampZoom(current * ratio));

  useEffect(() => {
    if (!viewerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeViewer();
      if (event.key === "+" || event.key === "=") multiplyZoom(BUTTON_ZOOM_RATIO);
      if (event.key === "-") multiplyZoom(1 / BUTTON_ZOOM_RATIO);
    };
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    closeButton.current?.focus();
    return () => {
      document.body.style.overflow = priorOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [viewerOpen]);

  const startDrag = (event: PointerEvent<HTMLDivElement>) => {
    drag.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: pan.x,
      panY: pan.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };

  const moveDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    setPan({
      x: drag.current.panX + event.clientX - drag.current.x,
      y: drag.current.panY + event.clientY - drag.current.y,
    });
  };

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) {
      drag.current = null;
      setDragging(false);
    }
  };

  const zoomWithWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    const boundedDelta = Math.max(-240, Math.min(240, event.deltaY));
    multiplyZoom(Math.exp(-boundedDelta * 0.0025));
  };

  return (
    <div className="laneProofExplorer">
      <div className="proofLanePicker" role="tablist" aria-label="Dummy proof packages by Evidence Lane">
        {proofIndex.lanes.map((item, index) => {
          const laneAccent = accents[index % accents.length];
          return (
            <GlassPill
              id={`proof-lane-${item.lane_id}`}
              key={item.lane_id}
              className="proofLanePill"
              active={index === activeIndex}
              tone={index === activeIndex ? "cyan" : "neutral"}
              role="tab"
              aria-selected={index === activeIndex}
              aria-controls="lane-proof-panel"
              leading={
                <GlassIconOrb className="source-lane-orb" color={laneAccent} size={40} decorative>
                  <SourceLaneIcon lane={item.lane_id} size={23} decorative />
                </GlassIconOrb>
              }
              onClick={() => setActiveIndex(index)}
            >
              <span><b>{String(item.ordinal).padStart(2, "0")} · {item.label}</b><small>{item.schema_table_count} lane tables</small></span>
            </GlassPill>
          );
        })}
      </div>

      <article
        id="lane-proof-panel"
        className="laneProofPanel"
        role="tabpanel"
        aria-labelledby={`proof-lane-${lane.lane_id}`}
      >
        <header>
          <div className="laneProofIdentity">
            <GlassIconOrb className="source-lane-orb" color={accent} size={62} decorative>
              <SourceLaneIcon lane={lane.lane_id} size={34} decorative />
            </GlassIconOrb>
            <div><span className="laneCode">{lane.lane_id}</span><h3>{lane.label} dummy proof</h3><p>Public-safe synthetic evidence. It contains no project source, accepted PV bytes, secrets, or candidate data.</p></div>
          </div>
          <div className="laneProofRuntime"><span>Exact fixture route</span><code>{lane.command}</code><small>{lane.parser_id} · {lane.chunker_version}</small></div>
        </header>

        <div className="laneProofBody">
          <section className="laneProofDownloads" aria-label={`${lane.label} four-file dummy package`}>
            <span className="lanePanelLabel">Four canonical files · direct website downloads</span>
            <div>
              {lane.canonical_artifacts.map((artifact, index) => (
                <a href={artifact.url} download={artifact.filename} key={artifact.kind}>
                  <GlassIconOrb color={artifact.kind === "receipt" ? "#efc668" : index === 0 ? "#5bd8ae" : "#65d7f2"} size={43} decorative>
                    <OfficialToolIcon tool={iconFor(artifact.kind)} size={23} decorative />
                  </GlassIconOrb>
                  <span><strong>{String(index + 1).padStart(2, "0")} · {artifact.label}</strong><code>{artifact.filename}</code><small>{formatBytes(artifact.bytes)} · SHA-256 {artifact.sha256.slice(0, 16)}…</small></span>
                  <b>Download</b>
                </a>
              ))}
            </div>
          </section>

          <section className="laneProofRender">
            <div className="laneProofRenderHead"><span className="lanePanelLabel">Full generated lane MMD · 8K + vector</span><span><a href={lane.render.url} download={lane.render.filename}>Download 8K PNG</a><a href={lane.vector_render.url} download={lane.vector_render.filename}>Download SVG</a></span></div>
            <button type="button" onClick={openViewer} aria-label={`Open full-screen ${lane.label} exact-MMD vector render`}>
              <Image
                src={vectorSrc}
                alt={`${lane.label} public-safe dummy Mermaid topology vector preview`}
                width={lane.render.width}
                height={lane.render.height}
                unoptimized
              />
              <span>Open lossless full view · deep zoom and pan</span>
            </button>
            <small>{lane.render.width} × {lane.render.height} · {formatBytes(lane.render.bytes)} · PNG {lane.render.sha256.slice(0, 16)}… · vector {lane.vector_render.sha256.slice(0, 16)}… · source MMD {lane.render.source_mmd_sha256.slice(0, 16)}…</small>
          </section>
        </div>

        <div className="laneProofDetails">
          <section><span className="lanePanelLabel">Full lane Mermaid source preview</span><pre>{lane.mmd_preview}</pre></section>
          <section><span className="lanePanelLabel">SQLite proof boundary</span><dl><div><dt>Graph profile</dt><dd><code>{lane.graph_profile}</code></dd></div><div><dt>Schema tables</dt><dd>{lane.schema_table_count}</dd></div><div><dt>FTS surface</dt><dd><code>{lane.fts_table}</code></dd></div><div><dt>Topology identity</dt><dd><code>{lane.topology_generator_sha256.slice(0, 16)}...</code></dd></div><div><dt>Fixture</dt><dd>synthetic-only</dd></div><div><dt>Lifecycle</dt><dd>no candidate or pointer movement</dd></div></dl></section>
        </div>
      </article>

      {viewerOpen ? createPortal(
        <div className="proofLightbox" role="presentation" onPointerDown={(event) => { if (event.target === event.currentTarget) closeViewer(); }}>
          <div className="proofLightboxToolbar" role="toolbar" aria-label="Lossless Mermaid topology controls">
            <strong>{lane.label} · exact-MMD vector</strong>
            <button type="button" onClick={() => multiplyZoom(1 / BUTTON_ZOOM_RATIO)} aria-label="Zoom out">−</button>
            <span>{Math.round(zoom * 100)}%</span>
            <button type="button" onClick={() => multiplyZoom(BUTTON_ZOOM_RATIO)} aria-label="Zoom in">+</button>
            <button type="button" onClick={resetViewer}>Fit</button>
            <a href={lane.render.url} download={lane.render.filename}>8K PNG</a>
            <a href={lane.vector_render.url} download={lane.vector_render.filename}>SVG</a>
            <button ref={closeButton} type="button" className="proofLightboxClose" onClick={closeViewer} aria-label="Close full-screen render">Close ×</button>
          </div>
          <div
            className={`proofLightboxCanvas${dragging ? " isDragging" : ""}`}
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onWheel={zoomWithWheel}
            onDoubleClick={() => multiplyZoom(BUTTON_ZOOM_RATIO)}
          >
            <Image
              src={vectorSrc}
              alt={`${lane.label} full-screen lossless public-safe dummy Mermaid topology`}
              width={lane.render.width}
              height={lane.render.height}
              draggable={false}
              unoptimized
              style={{ transform: `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})` }}
            />
          </div>
          <p>Drag to pan · wheel, double-click, or +/− for lossless deep zoom · Escape, backdrop, or Close to exit</p>
        </div>,
        document.body,
      ) : null}
    </div>
  );
}
