"use client";

import Image from "next/image";
import type {
  ButtonHTMLAttributes,
  CSSProperties,
  ReactNode,
} from "react";

export type BrainMotionState =
  | "idle"
  | "refresh-pulse"
  | "fuse-grow-pulse"
  | "success-flash";

export type GlassPillTone = "neutral" | "cyan" | "gold" | "green" | "rose" | "violet";

export type OfficialToolIconName =
  | "database"
  | "docker"
  | "git"
  | "media"
  | "node"
  | "package"
  | "pulse"
  | "python"
  | "terminal";

const officialToolIconAssets: Record<OfficialToolIconName, string> = {
  database: "/assets/tool-icons/database.svg",
  docker: "/assets/tool-icons/docker.svg",
  git: "/assets/tool-icons/git.svg",
  media: "/assets/tool-icons/media.svg",
  node: "/assets/tool-icons/node.svg",
  package: "/assets/tool-icons/package.svg",
  pulse: "/assets/tool-icons/pulse.svg",
  python: "/assets/tool-icons/python.svg",
  terminal: "/assets/tool-icons/terminal.svg",
};

type GlassIconOrbStyle = CSSProperties & {
  "--glass-orb-color"?: string;
  "--glass-orb-size"?: string;
};

export function GlassIconOrb({
  children,
  className = "",
  color = "#69d9f5",
  decorative = false,
  label,
  size = 36,
}: {
  children: ReactNode;
  className?: string;
  color?: string;
  decorative?: boolean;
  label?: string;
  size?: number | string;
}) {
  const resolvedSize = typeof size === "number" ? `${size}px` : size;
  const style: GlassIconOrbStyle = {
    "--glass-orb-color": color,
    "--glass-orb-size": resolvedSize,
  };

  return (
    <span
      className={`glass-icon-orb ${className}`.trim()}
      style={style}
      data-glass-orb-host="true"
      data-universal-orb-schema="T023_UNIVERSAL_GLASS_ORB_V001"
      data-glass-orb-content-origin="0,0"
      role={!decorative && label ? "img" : undefined}
      aria-label={!decorative ? label : undefined}
      aria-hidden={decorative || undefined}
    >
      <span className="glass-icon-orb__content">
        <span className="glass-icon-orb__payload">{children}</span>
      </span>
    </span>
  );
}

export function GlassPill({
  children,
  leading,
  trailing,
  active = false,
  tone = "neutral",
  className = "",
  type = "button",
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  children: ReactNode;
  leading?: ReactNode;
  trailing?: ReactNode;
  active?: boolean;
  tone?: GlassPillTone;
}) {
  return (
    <button
      {...props}
      type={type}
      className={`glass-pill universal-pill universal-pill--${tone}${active ? " is-active" : ""} ${className}`.trim()}
      data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"
      data-pill-content-mode="text"
      data-pill-geometry-schema="universal"
      data-pill-has-leading={leading ? "true" : "false"}
      data-pill-has-trailing={trailing ? "true" : "false"}
      data-pill-containment="no-overlap"
      data-pill-edge-inset-policy="symmetric-content-box"
      data-pill-inline-layout={leading && trailing ? "leading-label-trailing" : leading ? "leading-label" : trailing ? "label-trailing" : "label-only"}
    >
      <span className="universal-pill__content glass-pill__content">
        {leading ? <span className="universal-pill__slot universal-pill__slot--leading">{leading}</span> : null}
        <span className="universal-pill__label glass-pill__label">{children}</span>
        {trailing ? <span className="universal-pill__slot universal-pill__slot--trailing">{trailing}</span> : null}
      </span>
    </button>
  );
}

export function OfficialToolIcon({
  tool,
  size = 28,
  className = "",
  decorative = false,
}: {
  tool: OfficialToolIconName;
  size?: number;
  className?: string;
  decorative?: boolean;
}) {
  return (
    <Image
      className={`official-tool-icon ${className}`.trim()}
      src={officialToolIconAssets[tool]}
      alt={decorative ? "" : `${tool} tool`}
      width={size}
      height={size}
      aria-hidden={decorative || undefined}
    />
  );
}

function laneTool(lane: string): OfficialToolIconName {
  if (lane === "github_code" || lane === "local_code") return "git";
  if (lane === "data_excel" || lane === "brain_loader") return "database";
  if (lane === "pdf_ocr" || lane === "images_ocr" || lane === "ppt") return "media";
  if (lane === "artifacts" || lane === "project_engulf") return "package";
  if (lane === "research" || lane === "analysis") return "python";
  return "node";
}

export function LaneAssetIcon({
  lane,
  size = 22,
  className = "",
  decorative = false,
}: {
  lane: string;
  size?: number;
  className?: string;
  decorative?: boolean;
}) {
  return (
    <OfficialToolIcon
      tool={laneTool(lane)}
      size={size}
      className={className}
      decorative={decorative}
    />
  );
}

type BrainAssetStyle = CSSProperties & { "--evidence-accent": string };
type BrainSphereStyle = CSSProperties & { "--brain-orb-color"?: string };
type PulseStyle = CSSProperties & {
  "--brain-intensity": number;
  "--brain-orb-color": string;
};

export function BrainGlassSphere({
  size = 360,
  className = "",
  color = "#67d9f6",
  decorative = false,
}: {
  size?: number | string;
  className?: string;
  color?: string;
  decorative?: boolean;
}) {
  const style: BrainSphereStyle = {
    width: size,
    height: size,
    "--brain-orb-color": color,
  };

  return (
    <div
      className={`brain-sphere ${className}`.trim()}
      style={style}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : "Evidence Lane brain in a glass sphere"}
      aria-hidden={decorative || undefined}
      data-brain-orb-pulse-policy="COMMITTED_SELECTED_BRAIN_ONLY"
    >
      <span className="brain-sphere__rear" aria-hidden="true" />
      <Image
        className="brain-sphere__brain"
        src="/assets/evidence-static-brain.png"
        alt=""
        width={1142}
        height={1035}
        sizes="(max-width: 820px) 54vw, 320px"
      />
      <span className="brain-sphere__glass" aria-hidden="true" />
      <span className="brain-sphere__rim" aria-hidden="true" />
      <span className="brain-sphere__reflection" aria-hidden="true" />
    </div>
  );
}

export function PulsatingBrain({
  size = 380,
  active = true,
  intensity = 1,
  className = "",
  color = "#67d9f6",
  motionState = "idle",
  decorative = false,
}: {
  size?: number | string;
  active?: boolean;
  intensity?: number;
  className?: string;
  color?: string;
  motionState?: BrainMotionState;
  decorative?: boolean;
}) {
  const style: PulseStyle = {
    width: size,
    height: size,
    "--brain-intensity": Math.max(0.35, Math.min(intensity, 1.8)),
    "--brain-orb-color": color,
  };

  return (
    <div
      className={`pulsating-brain ${active || motionState !== "idle" ? "is-active" : ""} is-${motionState} ${className}`.trim()}
      data-brain-motion-state={motionState}
      style={style}
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : "Animated Evidence Lane brain"}
      aria-hidden={decorative || undefined}
    >
      <svg className="pulsating-brain__energy" viewBox="0 0 400 400" aria-hidden="true">
        <g className="pulsating-brain__arcs pulsating-brain__arcs--cool">
          <path d="M82 92a158 158 0 0 1 76-43" />
          <path d="M69 76a178 178 0 0 1 82-47" />
          <path d="M82 308a158 158 0 0 0 76 43" />
          <path d="M69 324a178 178 0 0 0 82 47" />
        </g>
        <g className="pulsating-brain__arcs pulsating-brain__arcs--warm">
          <path d="M242 49a158 158 0 0 1 76 43" />
          <path d="M249 29a178 178 0 0 1 82 47" />
          <path d="M242 351a158 158 0 0 0 76-43" />
          <path d="M249 371a178 178 0 0 0 82-47" />
        </g>
        <path className="pulsating-brain__wave pulsating-brain__wave--cool" d="M0 200h18l8-19 10 58 13-91 11 88 13-56 10 20h22" />
        <path className="pulsating-brain__wave pulsating-brain__wave--warm" d="M295 200h22l10-20 13 56 11-88 13 91 10-58 8 19h18" />
      </svg>
      <span className="pulsating-brain__halo" aria-hidden="true" />
      <BrainGlassSphere
        size="67%"
        color={color}
        className="pulsating-brain__orb"
        decorative
      />
    </div>
  );
}

export function EvidenceBrainAsset({
  color = "#43c7e8",
  className = "",
  label = "Evidence Lane brain inside a luminous glass orb",
  priority = false,
}: {
  color?: string;
  className?: string;
  label?: string;
  priority?: boolean;
}) {
  return (
    <div
      className={`evidenceBrainAsset ${className}`.trim()}
      style={{ "--evidence-accent": color } as BrainAssetStyle}
      role={label ? "img" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
    >
      <span className="evidenceBrainAssetGlow" aria-hidden="true" />
      <Image
        className="evidenceBrainAssetImage"
        src="/assets/evidence-static-brain.png"
        alt=""
        width={1142}
        height={1035}
        priority={priority}
      />
      <span className="evidenceBrainAssetGlass" aria-hidden="true" />
    </div>
  );
}
