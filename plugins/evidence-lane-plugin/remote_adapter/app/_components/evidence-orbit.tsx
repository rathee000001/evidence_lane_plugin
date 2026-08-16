"use client";

import type { CSSProperties } from "react";

import { PulsatingBrain } from "./evidence-assets";

const sourceLanes = [
  "Discussion", "Analysis", "Plan", "Mode", "Local code", "GitHub code",
  "Documents", "Data", "Presentations", "PDF / OCR", "Images / OCR", "Artifacts",
  "Custom", "Brain loader", "Research", "Project Engulf", "SQLite brain", "Chat Lineage",
] as const;

const pluginSurfaces = [
  "Boot", "Rollback", "Build", "Refresh", "Mode", "Source Intake", "State Travel",
  "Storage", "Storage connector", "Plugin", "Add plugin", "Drop plugin", "Exit Boot",
  "Plan Lane", "Lifecycle", "Canon", "Agent Learning",
] as const;

function orbitStyle(index: number, total: number) {
  return { "--orbit-angle": `${(index * 360) / total}deg` } as CSSProperties;
}

export function EvidenceOrbit() {
  return (
    <figure
      className="evidenceOrbit"
      aria-label="Concentric Evidence Lane map: 18 governed source lanes flow through 17 plugin surfaces to one human HIL"
    >
      <div className="evidenceOrbitHalo" aria-hidden="true" />
      <div className="evidenceOrbitRing sourceLaneOrbit" aria-label="18 source lanes">
        {sourceLanes.map((lane, index) => (
          <span className="evidenceOrbitNode" key={lane} style={orbitStyle(index, sourceLanes.length)} title={lane}>
            <i>{index + 1}</i><b>{lane}</b>
          </span>
        ))}
      </div>
      <div className="evidenceOrbitRing pluginSurfaceOrbit" aria-label="17 plugin surfaces">
        {pluginSurfaces.map((surface, index) => (
          <span className="evidenceOrbitNode" key={surface} style={orbitStyle(index, pluginSurfaces.length)} title={surface}>
            <i>{index + 1}</i><b>{surface}</b>
          </span>
        ))}
      </div>
      <div className="evidenceOrbitCenter">
        <PulsatingBrain size="clamp(150px, 25vw, 230px)" color="#69d9f5" />
        <span>1</span>
        <strong>Human HIL</strong>
        <small>Only the human can accept</small>
      </div>
      <figcaption>
        <span><strong>18</strong> source lanes</span>
        <span><strong>17</strong> plugin surfaces</span>
        <span><strong>1</strong> human gate</span>
      </figcaption>
    </figure>
  );
}
