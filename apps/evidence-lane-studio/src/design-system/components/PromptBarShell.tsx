import type { ReactNode } from "react";
import { GlassShell } from "./GlassShell";

export function PromptBarShell({ children, className = "", label = "Evidence Lane workflow bar" }: { children?: ReactNode; className?: string; label?: string }) {
  return <GlassShell as="nav" className={`prompt-bar-shell ${className}`.trim()} label={label} pillCluster="studio-workflows">{children}</GlassShell>;
}
