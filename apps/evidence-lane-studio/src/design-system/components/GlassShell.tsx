import type { CSSProperties, ReactNode } from "react";
import { studioUiSchemas } from "../contracts/StudioUiSchemas";
import { useUniversalPillCluster } from "./UniversalPillCluster";

export type GlassSurface = "default" | "transparent" | "frosted-popup";

export type GlassShellProps = {
  children?: ReactNode;
  className?: string;
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  as?: "section" | "footer" | "header" | "aside" | "main" | "div" | "nav";
  label?: string;
  surface?: GlassSurface;
  pillCluster?: string;
  "data-metric-state"?: string;
  "data-shared-rear-glass"?: string;
};

type ShellStyle = CSSProperties & { "--shell-radius"?: string };
const unit = (value: number | string | undefined) => typeof value === "number" ? `${value}px` : value;

const surfaceClass: Record<GlassSurface, string> = {
  default: "",
  transparent: "glass-shell--transparent",
  "frosted-popup": "glass-shell--frosted-popup",
};

export function GlassShell({ children, className = "", width, height, radius, as: Tag = "section", label, surface = "default", pillCluster, ...dataAttributes }: GlassShellProps) {
  const style: ShellStyle = { width, height, "--shell-radius": unit(radius) };
  const pillClusterRef = useUniversalPillCluster(pillCluster);
  return (
    <Tag
      {...dataAttributes}
      className={`glass-shell ${surfaceClass[surface]} ${className}`.trim()}
      style={style}
      aria-label={label}
      data-glass-surface={surface}
      data-universal-glass-schema={studioUiSchemas.universalGlassShell}
      data-glass-layering-schema={studioUiSchemas.glassLayeringNoBleed}
      data-popup-schema={surface === "frosted-popup" ? studioUiSchemas.universalFrostedPopup : undefined}
      data-popup-motion-schema={surface === "frosted-popup" ? studioUiSchemas.universalPopupFade : undefined}
    >
      <div ref={pillClusterRef} className="glass-shell__content" data-universal-pill-cluster={pillCluster} data-universal-prompt-bar={pillCluster === "studio-workflows" ? "distributed" : undefined} data-universal-prompt-distribution-law={pillCluster === "studio-workflows" ? "EVIDENCE_LANE_BOUNDED_WORKFLOW_PILLS_V4" : undefined}>{children}</div>
    </Tag>
  );
}

