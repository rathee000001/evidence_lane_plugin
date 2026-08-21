"use client";

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";

import modeOperatorGuide from "../_data/mode-governance.json";
import {
  GlassIconOrb,
  OfficialToolIcon,
  PulsatingBrain,
  type OfficialToolIconName,
} from "./evidence-assets";
import { SourceLaneIcon } from "./source-lane-icon";

export type HeroOrbitPreset =
  | "home"
  | "architecture"
  | "operators"
  | "studio"
  | "proof"
  | "provenance"
  | "connect"
  | "hil";

type HeroOrbitNode = {
  label: string;
  color: string;
  lane?: string;
  tool?: OfficialToolIconName;
};

type HeroRingTag = {
  value: string;
  label: string;
  x: number;
  y: number;
  side: "left" | "top" | "bottom";
};

type HeroOrbitRing = {
  label: string;
  size: number;
  nodes: readonly HeroOrbitNode[];
  tag: HeroRingTag;
};

type RingStyle = CSSProperties & {
  "--hero-ring-delay": string;
  "--hero-ring-size": string;
  "--hero-ring-index": number;
  "--hero-ring-rest": string;
  "--hero-ring-rotation": string;
};

type NodeStyle = CSSProperties & {
  "--hero-node-angle": string;
};

type TagStyle = CSSProperties & {
  "--hero-tag-x": string;
  "--hero-tag-y": string;
  "--hero-tag-delay": string;
};

type MarkerLineStyle = CSSProperties & {
  "--hero-tag-delay": string;
};

type HeroOrbitStyle = CSSProperties & {
  "--studio-sequence-start"?: string;
  "--studio-brain-absorb-delay"?: string;
  "--studio-brain-absorb-duration"?: string;
  "--studio-output-reveal-delay"?: string;
};

type StudioInputStyle = CSSProperties & {
  "--studio-input-x": string;
  "--studio-input-y": string;
  "--studio-flight-delay": string;
};

type StudioOutputStyle = CSSProperties & {
  "--studio-output-x": string;
  "--studio-output-y": string;
  "--studio-output-delay": string;
};

type StudioOutput = {
  label: string;
  extension: string;
  tool: OfficialToolIconName;
  color: string;
  x: number;
  y: number;
  delay: number;
};

type StudioOriginRing =
  | "Exact six-way HIL"
  | "Governed controls"
  | "Plugin surfaces"
  | "Source lanes";

type StudioInput = HeroOrbitNode & {
  originRing: StudioOriginRing;
  delay: number;
};

const cyan = "#69d9f5";
const green = "#83ddb3";
const gold = "#efca72";
const violet = "#a99af7";
const rose = "#f2a1c5";
const lime = "#a8d878";
const accents = [cyan, green, gold, violet, rose, lime] as const;

const sourceLanes = [
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

const laneNodes: readonly HeroOrbitNode[] = sourceLanes.map(([lane, label], index) => ({
  label,
  lane,
  color: accents[index % accents.length],
}));

const controlNodes: readonly HeroOrbitNode[] = [
  { label: "Boot", tool: "pulse", color: cyan },
  { label: "Rollback", tool: "database", color: violet },
  { label: "Build", tool: "package", color: green },
  { label: "Refresh", tool: "pulse", color: rose },
  { label: "Mode", tool: "terminal", color: gold },
  { label: "Source Intake", tool: "node", color: lime },
];

const hilDecisionNodes: readonly HeroOrbitNode[] = [
  { label: "APPROVE", tool: "pulse", color: green },
  { label: "APPROVE WITH DELTA", tool: "node", color: cyan },
  { label: "MORE RESEARCH", tool: "python", color: violet },
  { label: "ROLLBACK", tool: "database", color: gold },
  { label: "REJECT", tool: "terminal", color: rose },
  { label: "FAIL", tool: "media", color: "#d97e72" },
];

const pluginSurfaceNodes: readonly HeroOrbitNode[] = [
  ...controlNodes,
  { label: "State Travel", tool: "git", color: cyan },
  { label: "Storage", tool: "database", color: green },
  { label: "Storage connector", tool: "node", color: gold },
  { label: "Plugin", tool: "package", color: violet },
  { label: "Add plugin", tool: "package", color: rose },
  { label: "Drop plugin", tool: "package", color: lime },
  { label: "Exit Boot", tool: "terminal", color: cyan },
  { label: "Plan Lane", lane: "plan", color: violet },
  { label: "Lifecycle", tool: "pulse", color: gold },
];

const studioOutputs: readonly StudioOutput[] = [
  { label: "SQLite", extension: ".sqlite", tool: "database", color: cyan, x: 24, y: 40, delay: 0 },
  { label: "MMD", extension: ".mmd", tool: "node", color: green, x: 76, y: 40, delay: 0.12 },
  { label: "DOT", extension: ".dot", tool: "git", color: violet, x: 24, y: 60, delay: 0.24 },
  { label: "JSON", extension: ".json", tool: "package", color: gold, x: 76, y: 60, delay: 0.36 },
];

const studioInputs: readonly StudioInput[] = [
  { label: "Chat Lineage", originRing: "Source lanes", lane: "chat_lineage", color: rose, delay: 0 },
  { label: "Plugin package", originRing: "Plugin surfaces", tool: "package", color: violet, delay: 0.18 },
  { label: "Build", originRing: "Governed controls", tool: "terminal", color: green, delay: 0.36 },
  { label: "APPROVE WITH DELTA", originRing: "Exact six-way HIL", tool: "node", color: cyan, delay: 0.54 },
];

const studioOriginPositions: Record<StudioOriginRing, { x: number; y: number }> = {
  "Source lanes": { x: 82, y: 20 },
  "Plugin surfaces": { x: 16, y: 32 },
  "Governed controls": { x: 20, y: 78 },
  "Exact six-way HIL": { x: 80, y: 80 },
};

function studioInputOrigin(input: StudioInput): { x: number; y: number } {
  return studioOriginPositions[input.originRing];
}

const topTag = (value: string, label: string): HeroRingTag => ({ value, label, x: 50, y: 0, side: "top" });
const leftTag = (value: string, label: string, y: number): HeroRingTag => ({ value, label, x: 2, y, side: "left" });
const bottomTag = (value: string, label: string): HeroRingTag => ({ value, label, x: 50, y: 100, side: "bottom" });

const ringRestAngles = [5, -9, 14, -20] as const;
const studioInputFlightDuration = 0.88;
const studioLastInputComplete = Math.max(...studioInputs.map((input) => input.delay)) + studioInputFlightDuration;
const studioBrainAbsorbDelay = studioLastInputComplete + 0.12;
const studioBrainAbsorbDuration = 0.78;
const studioOutputRevealDelay = studioBrainAbsorbDelay + studioBrainAbsorbDuration + 0.12;

function markerTarget(ring: HeroOrbitRing): { x: number; y: number } {
  const radius = ring.size / 2;
  const clampToRadius = (value: number) => Math.max(-radius + 0.01, Math.min(radius - 0.01, value));

  if (ring.tag.side === "left") {
    const offsetY = clampToRadius(ring.tag.y - 50);
    return { x: 50 - Math.sqrt(radius ** 2 - offsetY ** 2), y: ring.tag.y };
  }

  const offsetX = clampToRadius(ring.tag.x - 50);
  const edgeY = Math.sqrt(radius ** 2 - offsetX ** 2);
  return { x: ring.tag.x, y: ring.tag.side === "top" ? 50 - edgeY : 50 + edgeY };
}

function ringIndexFromTarget(target: EventTarget | null): number | null {
  if (!(target instanceof Element)) return null;
  const node = target.closest<HTMLElement>(".heroOrbitNode[data-ring-index]");
  if (!node) return null;
  const ringIndex = Number(node.dataset.ringIndex);
  return Number.isInteger(ringIndex) ? ringIndex : null;
}

const homeGovernanceRings: readonly HeroOrbitRing[] = [
  {
    label: "Exact six-way HIL",
    size: 54,
    nodes: hilDecisionNodes,
    tag: bottomTag("6", "exact HIL decisions"),
  },
  { label: "Governed controls", size: 68, nodes: controlNodes, tag: leftTag("6", "governed controls", 70) },
  { label: "Plugin surfaces", size: 82, nodes: pluginSurfaceNodes, tag: leftTag("15", "plugin surfaces", 31) },
  { label: "Source lanes", size: 96, nodes: laneNodes, tag: topTag("18", "source lanes") },
];

const presetRings: Record<HeroOrbitPreset, readonly HeroOrbitRing[]> = {
  home: homeGovernanceRings,
  architecture: [
    {
      label: "Serial lifecycle",
      size: 48,
      nodes: [
        { label: "Candidate", tool: "package", color: gold },
        { label: "Human HIL", tool: "pulse", color: rose },
        { label: "Fuse", tool: "node", color: violet },
        { label: "Accepted pointer", tool: "database", color: green },
      ],
      tag: bottomTag("4", "serial authority gates"),
    },
    {
      label: "Evidence work",
      size: 66,
      nodes: [
        { label: "Parse", tool: "python", color: cyan },
        { label: "Index", tool: "database", color: green },
        { label: "Graph", tool: "node", color: violet },
        { label: "Test", tool: "terminal", color: gold },
      ],
      tag: leftTag("4", "evidence stages", 50),
    },
    {
      label: "Source families",
      size: 84,
      nodes: [
        { label: "Code", lane: "local_code", color: cyan },
        { label: "Documents", lane: "docs", color: green },
        { label: "Data", lane: "data_excel", color: gold },
        { label: "Lineage", lane: "chat_lineage", color: rose },
      ],
      tag: topTag("4", "source families"),
    },
  ],
  operators: [
    {
      label: "Ordered operator families",
      size: 66,
      nodes: [
        { label: "Physics", tool: "pulse", color: cyan },
        { label: "Chemistry", tool: "node", color: green },
        { label: "Maths", tool: "database", color: violet },
        { label: "MBA", tool: "package", color: gold },
        { label: "Supply", tool: "git", color: rose },
      ],
      tag: bottomTag("5", "operator families"),
    },
    {
      label: "Mode routes",
      size: 84,
      nodes: [
        { label: "Discussion", lane: "discussion", color: cyan },
        { label: "Plan", lane: "plan", color: gold },
        { label: "Code", lane: "local_code", color: violet },
        { label: "Validation", lane: "mode", color: green },
        { label: "Research", lane: "research", color: rose },
        { label: "Custom", lane: "custom", color: lime },
      ],
      tag: topTag("6", "mode routes"),
    },
  ],
  studio: homeGovernanceRings,
  proof: [
    { label: "Lane proofs", size: 66, nodes: laneNodes, tag: bottomTag("18", "lane proofs") },
    {
      label: "Governed files",
      size: 84,
      nodes: [
        { label: "SQLite", tool: "database", color: cyan },
        { label: "MMD", tool: "node", color: green },
        { label: "DOT", tool: "git", color: violet },
        { label: "JSON / receipt", tool: "package", color: gold },
      ],
      tag: topTag("4", "governed files"),
    },
  ],
  provenance: [
    {
      label: "Evidence identity",
      size: 66,
      nodes: [
        { label: "Origin", lane: "docs", color: cyan },
        { label: "Source", lane: "github_code", color: green },
        { label: "Hash", lane: "sqlite_brain", color: gold },
        { label: "Attribution", lane: "discussion", color: rose },
      ],
      tag: bottomTag("4", "identity checks"),
    },
    {
      label: "Provenance surfaces",
      size: 84,
      nodes: [
        { label: "Research", lane: "research", color: violet },
        { label: "Documents", lane: "docs", color: cyan },
        { label: "Git history", lane: "github_code", color: green },
        { label: "Chat Lineage", lane: "chat_lineage", color: rose },
      ],
      tag: topTag("4", "provenance surfaces"),
    },
  ],
  connect: [
    { label: "Governed controls", size: 66, nodes: controlNodes, tag: bottomTag("6", "governed controls") },
    {
      label: "Host routes",
      size: 84,
      nodes: [
        { label: "Codex Git", lane: "github_code", color: cyan },
        { label: "Codex local", lane: "local_code", color: green },
        { label: "Durable MCP", lane: "project_engulf", color: gold },
        { label: "ChatGPT read", lane: "chat_lineage", color: rose },
      ],
      tag: topTag("4", "host routes"),
    },
  ],
  hil: [
    {
      label: "Exact six-way decision",
      size: 66,
      nodes: hilDecisionNodes,
      tag: bottomTag("6", "exact HIL choices"),
    },
    {
      label: "Authority boundary",
      size: 84,
      nodes: [
        { label: "Candidate", tool: "package", color: cyan },
        { label: "Evidence", tool: "database", color: violet },
        { label: "Human decision", tool: "pulse", color: gold },
        { label: "Accepted pointer", tool: "git", color: green },
      ],
      tag: topTag("4", "authority states"),
    },
  ],
};

function HeroNodeIcon({ node }: { node: HeroOrbitNode }) {
  if (node.lane) return <SourceLaneIcon lane={node.lane} size="62%" decorative />;
  return <OfficialToolIcon tool={node.tool ?? "pulse"} size={28} decorative />;
}

function BrainCenter({ preset }: { preset: "home" | "architecture" }) {
  return (
    <div className={`heroOrbitCenter heroOrbitCenter--${preset}`} data-hero-center={`${preset}-brain`} aria-hidden="true">
      <PulsatingBrain size="100%" color={cyan} decorative />
    </div>
  );
}

function OperatorCenter() {
  const authority = modeOperatorGuide.modes[0].env_authority;
  return (
    <div className="heroOrbitCenter heroOrbitCenter--operators" data-hero-center="operator-authority">
      <div className="operatorAuthorityCard">
        <span>LIVE SOURCE PROJECTION</span>
        <strong>{modeOperatorGuide.mode_count} modes</strong>
        <p>No generic Code formula is copied into non-Code lanes.</p>
        <dl>
          <div><dt>ENV</dt><dd>{authority.env_sqlite_sha256.slice(0, 12)}</dd></div>
          <div><dt>UOP</dt><dd>{authority.uop_sqlite_sha256.slice(0, 12)}</dd></div>
          <div><dt>Export</dt><dd>{modeOperatorGuide.export_sha256.slice(0, 12)}</dd></div>
        </dl>
      </div>
    </div>
  );
}

function StudioCenter() {
  return (
    <div className="heroOrbitCenter heroOrbitCenter--studio" data-hero-center="studio-home-brain" aria-hidden="true">
      <div className="studioBuilder">
        <div className="studioBuilderBrain">
          <PulsatingBrain size="100%" color={cyan} decorative />
        </div>
        {studioInputs.map((input) => {
          const origin = studioInputOrigin(input);
          const style: StudioInputStyle = {
            "--studio-input-x": `${origin.x}%`,
            "--studio-input-y": `${origin.y}%`,
            "--studio-flight-delay": `${input.delay}s`,
          };
          return (
            <span
              className="studioBuilderInput"
              data-origin-orb={input.label}
              data-origin-ring={input.originRing}
              style={style}
              key={`${input.originRing}-${input.label}`}
            >
              <GlassIconOrb className="studioBuilderInputOrb" color={input.color} size="clamp(34px,3.6vw,54px)" decorative>
                <HeroNodeIcon node={input} />
              </GlassIconOrb>
              <small>{input.label}</small>
            </span>
          );
        })}
        {studioOutputs.map((output) => {
          const style: StudioOutputStyle = {
            "--studio-output-x": `${output.x}%`,
            "--studio-output-y": `${output.y}%`,
            "--studio-output-delay": `${output.delay}s`,
          };
          return (
            <span className="studioBuilderOutput" style={style} key={output.label}>
              <GlassIconOrb className="studioBuilderOutputOrb" color={output.color} size="28px" decorative>
                <OfficialToolIcon tool={output.tool} size={17} decorative />
              </GlassIconOrb>
              <strong>{output.label}</strong>
              <code>{output.extension}</code>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function ProofCenter() {
  return (
    <div className="heroOrbitCenter heroOrbitCenter--proof" data-hero-center="proof-lanes">
      <div className="numberAside heroOrbitLaneCore"><strong>18</strong><span>canonical lanes</span><small>one proof boundary</small></div>
    </div>
  );
}

function ProvenanceCenter() {
  return (
    <div className="heroOrbitCenter heroOrbitCenter--provenance" data-hero-center="provenance-r-and-d">
      <div className="provenanceStamp"><strong>R&amp;D</strong><span>Praveen Rathee</span><small>Human acceptance authority</small></div>
    </div>
  );
}

function ConnectCenter() {
  return (
    <div className="heroOrbitCenter heroOrbitCenter--connect" data-hero-center="host-connection">
      <div className="connectHeroSphere">
        <span>HOST BOUNDARY</span>
        <strong>2</strong>
        <b>governed hosts</b>
        <small>Codex full lifecycle<br />ChatGPT read-safe</small>
      </div>
    </div>
  );
}

function HilCenter() {
  return (
    <div className="heroOrbitCenter heroOrbitCenter--hil" data-hero-center="human-gate">
      <div className="hilHeroGate"><strong>1</strong><span>human gate</span><small>no implied approval</small></div>
    </div>
  );
}

function HeroCenter({ preset }: { preset: HeroOrbitPreset }) {
  if (preset === "home" || preset === "architecture") return <BrainCenter preset={preset} />;
  if (preset === "operators") return <OperatorCenter />;
  if (preset === "studio") return <StudioCenter />;
  if (preset === "proof") return <ProofCenter />;
  if (preset === "provenance") return <ProvenanceCenter />;
  if (preset === "connect") return <ConnectCenter />;
  return <HilCenter />;
}

export function HeroOrbit({ preset }: { preset: HeroOrbitPreset }) {
  const rings = presetRings[preset];
  const hasLeftRingTag = rings.some((ring) => ring.tag.side === "left");
  const [interactive, setInteractive] = useState(false);
  const [draggingRing, setDraggingRing] = useState<number | null>(null);
  const [ringRotations, setRingRotations] = useState<number[]>(() => rings.map(() => 0));
  const [activeOrb, setActiveOrb] = useState<string | null>(null);
  const drag = useRef<{
    pointerId: number;
    ringIndex: number;
    startX: number;
    startY: number;
    startRotation: number;
    moved: boolean;
  } | null>(null);
  const suppressClick = useRef(false);
  const ringOpeningStart = 0.85;
  const finalRingDelay = ringOpeningStart + Math.max(0, rings.length - 1) * 1.55;
  const openingCompleteAt = finalRingDelay + 1.95;
  const studioSequenceStart = openingCompleteAt + 0.25;
  const heroStyle: HeroOrbitStyle | undefined = preset === "studio"
    ? {
        "--studio-sequence-start": `${studioSequenceStart}s`,
        "--studio-brain-absorb-delay": `${studioBrainAbsorbDelay}s`,
        "--studio-brain-absorb-duration": `${studioBrainAbsorbDuration}s`,
        "--studio-output-reveal-delay": `${studioOutputRevealDelay}s`,
      }
    : undefined;

  useEffect(() => {
    setInteractive(false);
    setDraggingRing(null);
    setRingRotations(rings.map(() => 0));
    setActiveOrb(null);
    drag.current = null;
    suppressClick.current = false;
    if (preset === "studio") return undefined;
    const timer = window.setTimeout(() => setInteractive(true), openingCompleteAt * 1000);
    return () => window.clearTimeout(timer);
  }, [openingCompleteAt, preset, rings]);

  const clampRotation = (value: number) => Math.max(-720, Math.min(720, value));
  const handlePointerDown = (event: PointerEvent<HTMLElement>) => {
    if (!interactive || event.button !== 0) return;
    const ringIndex = ringIndexFromTarget(event.target);
    if (ringIndex === null) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      ringIndex,
      startX: event.clientX,
      startY: event.clientY,
      startRotation: ringRotations[ringIndex] ?? 0,
      moved: false,
    };
    setDraggingRing(ringIndex);
  };
  const handlePointerMove = (event: PointerEvent<HTMLElement>) => {
    if (!interactive || !drag.current || drag.current.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.current.startX;
    const deltaY = event.clientY - drag.current.startY;
    if (Math.abs(deltaX) + Math.abs(deltaY) > 5) drag.current.moved = true;
    const { ringIndex, startRotation } = drag.current;
    setRingRotations((current) => current.map((value, index) => (
      index === ringIndex ? clampRotation(startRotation + (deltaX - deltaY * 0.22) * 0.42) : value
    )));
  };
  const finishPointerDrag = (event: PointerEvent<HTMLElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    const moved = drag.current.moved;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    drag.current = null;
    setDraggingRing(null);
    suppressClick.current = moved;
    if (moved) window.setTimeout(() => { suppressClick.current = false; }, 0);
  };
  const handleWheel = (event: WheelEvent<HTMLElement>) => {
    if (!interactive) return;
    const ringIndex = ringIndexFromTarget(event.target);
    if (ringIndex === null) return;
    event.preventDefault();
    setRingRotations((current) => current.map((value, index) => (
      index === ringIndex ? clampRotation(value + (event.deltaY + event.deltaX) * 0.08) : value
    )));
  };
  const handleKeyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (!interactive || !["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
    const ringIndex = ringIndexFromTarget(event.target);
    if (ringIndex === null) return;
    event.preventDefault();
    setRingRotations((current) => current.map((value, index) => {
      if (index !== ringIndex) return value;
      if (event.key === "Home") return 0;
      return clampRotation(value + (event.key === "ArrowRight" ? 12 : -12));
    }));
  };
  const accessibleSummary = rings.map((ring) => {
    const nodes = ring.nodes.map((node) => node.label).join(", ");
    return `${ring.tag.value} ${ring.tag.label}${nodes ? `: ${nodes}` : ""}`;
  }).join(". ");
  const totalOrbCount = rings.reduce((total, ring) => total + ring.nodes.length, 0);
  const interactionSummary = preset === "studio"
    ? `After the shared opening completes, all ${totalOrbCount} actual ring orbs are engulfed outer-to-inner into the full-size Home brain; the rings and brain then clear so only the SQLite, MMD, DOT, and JSON file pills remain.`
    : "After the opening completes, hover or focus an orb for its name; drag or wheel that orb to rotate only its own ring, or use its arrow keys. Press Home on that orb to reset its ring.";

  return (
    <figure
      className={`heroOrbit heroOrbit--${preset}${hasLeftRingTag ? " heroOrbit--has-left-ring-tag" : ""}`}
      data-hero-preset={preset}
      data-orbit-interactive={interactive ? "true" : "false"}
      data-orbit-dragging={draggingRing === null ? "false" : "true"}
      data-dragging-ring={draggingRing ?? undefined}
      data-opening-complete-at={openingCompleteAt}
      data-studio-sequence-start={preset === "studio" ? studioSequenceStart : undefined}
      data-studio-orb-count={preset === "studio" ? totalOrbCount : undefined}
      data-studio-layer-order={preset === "studio" ? "Source lanes > Plugin surfaces > Governed controls > Exact six-way HIL" : undefined}
      style={heroStyle}
      aria-label={`Evidence Lane ${preset} hero. ${accessibleSummary}. ${interactionSummary}`}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerDrag}
      onPointerCancel={finishPointerDrag}
      onWheel={handleWheel}
      onKeyDown={handleKeyboard}
    >
      <div className="heroOrbitGlow" aria-hidden="true" />
      <HeroCenter preset={preset} />
      {rings.map((ring, ringIndex) => {
        const ringDelay = ringOpeningStart + ringIndex * 1.55;
        const restAngle = ringRestAngles[ringIndex] ?? ringIndex * 7;
        const ringStyle: RingStyle = {
          "--hero-ring-delay": `${ringDelay}s`,
          "--hero-ring-size": `${ring.size}%`,
          "--hero-ring-index": ringIndex,
          "--hero-ring-rest": `${restAngle}deg`,
          "--hero-ring-rotation": `${ringRotations[ringIndex] ?? 0}deg`,
        };
        const tagStyle: TagStyle = {
          "--hero-tag-x": `${ring.tag.x}%`,
          "--hero-tag-y": `${ring.tag.y}%`,
          "--hero-tag-delay": `${ringDelay + 1.32}s`,
        };
        const target = markerTarget(ring);
        const markerStyle: MarkerLineStyle = {
          "--hero-tag-delay": `${ringDelay + 1.2}s`,
        };
        return (
          <div className="heroOrbitLayer" key={ring.label}>
            <div className="heroOrbitRing" data-ring-index={ringIndex} data-ring-label={ring.label} style={ringStyle} role="group" aria-label={ring.label}>
              {ring.nodes.map((node, nodeIndex) => {
                const nodeStyle: NodeStyle = {
                  "--hero-node-angle": `${(nodeIndex * 360) / ring.nodes.length}deg`,
                };
                const orbId = `${preset}-${ringIndex}-${nodeIndex}`;
                return (
                  <button
                    className="heroOrbitNode"
                    data-active={activeOrb === orbId ? "true" : "false"}
                    data-ring-index={ringIndex}
                    data-ring-label={ring.label}
                    data-orb-label={node.label}
                    key={`${ring.label}-${node.label}`}
                    style={nodeStyle}
                    type="button"
                    aria-label={`${node.label}, ${ring.label}`}
                    aria-pressed={activeOrb === orbId}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (suppressClick.current) {
                        suppressClick.current = false;
                        return;
                      }
                      if (interactive) setActiveOrb((current) => current === orbId ? null : orbId);
                    }}
                  >
                    <GlassIconOrb className="heroOrbitGlassOrb" color={node.color} size="clamp(32px, 3.15vw, 50px)" decorative>
                      <HeroNodeIcon node={node} />
                    </GlassIconOrb>
                    <span className="heroOrbitNodeLabel" aria-hidden="true">{node.label}</span>
                  </button>
                );
              })}
            </div>
            <svg
              className="heroOrbitMarkerLine"
              data-ring-index={ringIndex}
              style={markerStyle}
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <line x1={ring.tag.x} y1={ring.tag.y} x2={target.x} y2={target.y} pathLength="1" />
              <circle cx={target.x} cy={target.y} r="0.72" />
            </svg>
            <span className="heroOrbitRingTag" data-ring-tag={ring.label} data-ring-index={ringIndex} data-tag-side={ring.tag.side} style={tagStyle}>
              <strong>{ring.tag.value}</strong><span>{ring.tag.label}</span>
            </span>
          </div>
        );
      })}
      <figcaption className="visuallyHidden">{accessibleSummary}</figcaption>
    </figure>
  );
}
