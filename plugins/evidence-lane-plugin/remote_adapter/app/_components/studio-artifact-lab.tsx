"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  studioArtifactCatalog,
  studioRetrievalServices,
  type StudioArtifact,
} from "../_data/studio-artifact-catalog";

const formatOrder: readonly StudioArtifact["format"][] = [
  "SQLite", "Markdown", "JSON", "CSV", "Chart", "Table", "MMD", "DOT",
];

const capabilityRows = [
  { label: "Codex native", executable: 87, failClosed: 0 },
] as const;

export function StudioArtifactLab() {
  const [activeFormat, setActiveFormat] = useState<StudioArtifact["format"] | "All">("All");
  const visibleArtifacts = useMemo(() => (
    activeFormat === "All"
      ? studioArtifactCatalog
      : studioArtifactCatalog.filter((artifact) => artifact.format === activeFormat)
  ), [activeFormat]);

  return (
    <section className="studioArtifactLab" id="artifact-lab" aria-label="Evidence AI Studio artifact and retrieval lab">
      <header>
        <span className="kicker light">Artifact and retrieval lab</span>
        <h2>Inspect the evidence shape before trusting the answer.</h2>
        <p>
          These are committed public-safe artifacts and derived views. Downloads preserve their
          stated identity; optional services report an honest unavailable state until configured.
        </p>
      </header>

      <div className="studioServiceStates" aria-label="Retrieval service state">
        <span><b>Lexical</b>{studioRetrievalServices.lexical}</span>
        <span><b>Read-only SQL</b>{studioRetrievalServices.sql}</span>
        <span><b>Vector</b>{studioRetrievalServices.vector}</span>
        <span><b>Generation</b>{studioRetrievalServices.generation}</span>
      </div>

      <div className="studioArtifactFilters" role="tablist" aria-label="Artifact formats">
        {(["All", ...formatOrder] as const).map((format) => (
          <button
            type="button"
            role="tab"
            aria-selected={activeFormat === format}
            className={activeFormat === format ? "active" : ""}
            key={format}
            onClick={() => setActiveFormat(format)}
          >
            {format}
          </button>
        ))}
      </div>

      <div className="studioArtifactGrid" role="tabpanel">
        {visibleArtifacts.map((artifact) => (
          <article key={artifact.id}>
            <div><span>{artifact.format}</span><small>{artifact.status.replaceAll("_", " ")}</small></div>
            <h3>{artifact.label}</h3>
            <p>{artifact.purpose}</p>
            <code>{artifact.identity}</code>
            <small><b>Boundary:</b> {artifact.boundary}</small>
            <Link href={artifact.href}>Open artifact or governed view →</Link>
          </article>
        ))}
      </div>

      <div className="studioCapabilityView" aria-label="Executable and fail-closed action chart and table">
        <div>
          <h3>Executable action chart</h3>
          {capabilityRows.map((row) => (
            <div className="studioCapabilityBar" key={row.label}>
              <span>{row.label}</span>
              <div>
                <i style={{ width: `${row.executable / 87 * 100}%` }} />
                <em style={{ width: `${row.failClosed / 87 * 100}%` }} />
              </div>
              <small>{row.executable} executable / {row.failClosed} fail closed / 87 visible</small>
            </div>
          ))}
        </div>
        <table>
          <caption>Host capability table</caption>
          <thead><tr><th>Host</th><th>Total</th><th>Read-only</th><th>Write-capable</th></tr></thead>
          <tbody>
            <tr><th>Codex native 3.0.0</th><td>87</td><td>27</td><td>60</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
