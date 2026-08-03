"use client";

import Image from "next/image";
import type { CSSProperties } from "react";

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

export function EvidenceBrainAsset({
  color = "#43c7e8",
  className = "",
  label = "Evidence Lane brain inside a luminous glass orb",
}: {
  color?: string;
  className?: string;
  label?: string;
}) {
  return (
    <div
      className={`evidenceBrainAsset ${className}`.trim()}
      style={{ "--evidence-accent": color } as BrainAssetStyle}
      role={label ? "img" : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
    >
      <span className="evidenceBrainAssetRing evidenceBrainAssetRingA" aria-hidden="true" />
      <span className="evidenceBrainAssetRing evidenceBrainAssetRingB" aria-hidden="true" />
      <span className="evidenceBrainAssetGlow" aria-hidden="true" />
      <Image
        className="evidenceBrainAssetImage"
        src="/assets/evidence-static-brain.png"
        alt=""
        width={1142}
        height={1035}
        priority={false}
      />
      <span className="evidenceBrainAssetGlass" aria-hidden="true" />
    </div>
  );
}
