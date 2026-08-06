"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState, type CSSProperties, type KeyboardEvent } from "react";

import {
  EvidenceBrainAsset,
  OfficialToolIcon,
  type OfficialToolIconName,
} from "./evidence-assets";

type SourceBrain = {
  name: string;
  shortName: string;
  source: string;
  sourceUrl?: string;
  identity: string;
  verification: string;
  classification: string;
  binding: string;
  accent: string;
  tool: OfficialToolIconName;
  purpose: string;
  stats: readonly [string, string][];
  reused: string;
  refused: string;
  boundary: string;
  entities: readonly string[];
  visualLabel: string;
};

const sourceBrains: readonly SourceBrain[] = [
  {
    name: "SQLite Brain Builder V5.9",
    shortName: "V5.9 app",
    source: "Selected desktop generator application",
    identity: "SHA 3E7B6AC4CFE5",
    verification: "Exact selected executable hashed locally",
    classification: "GENERATOR APP",
    binding: "PACKAGE BINDING OPEN",
    accent: "#58d6ee",
    tool: "database",
    purpose: "The selected Windows application is the generator identity the user meant by V5.9. It is audited separately from Graphify, source-tree versions, and package schemas.",
    stats: [["Executable", "367 MB"], ["Selected bytes", "SHA-256"], ["Installed copy", "09 Jul 2026"], ["Source findings", "4 V5.9 / V5.3"]],
    reused: "The V5.9 application axis, exact executable hash, and the requirement for a hash-bound generator receipt.",
    refused: "Inferring that these exact installed bytes created a package, or rewriting the user's pre-July chronology as verified fact.",
    boundary: "V5.9 is the app/runtime identity. V5.3 is an internal stable-runtime or package-schema identity. They coexist. The user reports a pre-July build; the selected installed bytes are dated July 9, so the earlier build remains unproven by this executable.",
    entities: ["App", "Runtime", "Binary", "Source", "Package", "Hash", "Receipt"],
    visualLabel: "Generator-to-package provenance gate",
  },
  {
    name: "Graphify source snapshot",
    shortName: "Graphify",
    source: "Graphify-Labs/graphify",
    sourceUrl: "https://github.com/Graphify-Labs/graphify/tree/00efd6e7969837ae4a9f11d8d504dcd3b20b09df",
    identity: "00efd6e79698",
    verification: "Pinned public tree paired read-only",
    classification: "OFFICIAL SOURCE",
    binding: "776 / 776 TREE MATCH",
    accent: "#48cfe8",
    tool: "python",
    purpose: "A read-only code index used to test canonical entity identity, bounded parsing, and secret-safe configuration handling.",
    stats: [["Source files", "776"], ["Indexed bytes", "15.0 MB"], ["Tree match", "776 / 776"], ["Commit", "00efd6e"]],
    reused: "Canonical IDs, extracted-versus-inferred provenance, environment-name-only controls, and visible extraction failures.",
    refused: "Its UI, broad extractor set, agent installer, and any claim that Graphify has a V5.9 or V5.3 product version here.",
    boundary: "Graphify has its own pinned commit identity. V5.9 names the selected SQLite Brain Builder app; V5.3 names an internal EvidenceOS runtime/schema. Neither is Graphify's version.",
    entities: ["Repo", "Commit", "File", "Symbol", "Route", "Dependency", "Artifact"],
    visualLabel: "Seven-entity code-topology preview",
  },
  {
    name: "GitHub Agentic Workflows source",
    shortName: "GitHub AW",
    source: "github/gh-aw",
    sourceUrl: "https://github.com/github/gh-aw/tree/61bd1aa20cb68d77d2e0d4b4974b4480cb1b305c",
    identity: "61bd1aa20cb6",
    verification: "Pinned public tree paired read-only",
    classification: "OFFICIAL SOURCE",
    binding: "6,626 / 6,626 TREE MATCH",
    accent: "#e7b95e",
    tool: "git",
    purpose: "The official source used to isolate connector-access patterns that materially improve Evidence Lane routing without importing a second gateway.",
    stats: [["Source files", "6,626"], ["Indexed bytes", "436 MB"], ["Tree match", "6,626 / 6,626"], ["Commit", "61bd1aa"]],
    reused: "Conjunctive guards, first-failing-guard evidence, exact preferred-tool selection, and fail-safe ambiguity.",
    refused: "Its Docker gateway, firewall runtime, GitHub role model, and direct process orchestration.",
    boundary: "The extracted folder contains no .git directory. Its identity comes from a separately paired official ZIP and commit tree, not from inferred local Git metadata.",
    entities: ["Repo", "Commit", "File", "Guard", "Tool", "Route", "Receipt"],
    visualLabel: "Ordered route-guard topology",
  },
  {
    name: "Agentic Workflows brain package",
    shortName: "AW brain",
    source: "UEPC local_code sector package",
    identity: "ZIP 0CC50621A535",
    verification: "ZIP and SQLite bytes hashed read-only",
    classification: "GENERATED BRAIN",
    binding: "GENERATOR UNPROVEN",
    accent: "#f0c66d",
    tool: "database",
    purpose: "A generated brain of the Agentic Workflows source, inspected as a package rather than promoted as Git-history evidence.",
    stats: [["Source files", "6,626"], ["Code files", "3,623"], ["Chunks", "12,435"], ["Git history rows", "0"]],
    reused: "Queryable local-code evidence and the negative proof that folder ingestion did not invent branches, commits, remotes, or file changes.",
    refused: "Calling the package a Git lane or claiming the selected V5.9 executable generated it.",
    boundary: "The archive and embedded SQLite are exact-hashed and pass integrity checks. No manifest binds them to the selected V5.9 executable SHA-256.",
    entities: ["Package", "SQLite", "File", "Chunk", "Symbol", "Edge", "Receipt"],
    visualLabel: "Package topology with zero invented Git history",
  },
  {
    name: "GitHub MCP Server source",
    shortName: "GitHub MCP",
    source: "github/github-mcp-server",
    sourceUrl: "https://github.com/github/github-mcp-server/tree/3778a41476e31a072430cfee7c5d31c5f72def60",
    identity: "3778a41476e3",
    verification: "Pinned public tree paired read-only",
    classification: "OFFICIAL SOURCE",
    binding: "536 / 536 TREE MATCH",
    accent: "#6fd9c0",
    tool: "git",
    purpose: "The official MCP implementation used as a design reference for bounded, self-describing Git tools and sanitized output.",
    stats: [["Source files", "536"], ["Indexed bytes", "4.29 MB"], ["Tree match", "536 / 536"], ["Commit", "3778a41"]],
    reused: "Read-only annotations, static permission ceilings narrowed by each request, and sanitized tool results.",
    refused: "Copying the server, broadening connector grants, or treating tool availability as authorization.",
    boundary: "Evidence Lane keeps one governed connector surface. The official server is a read-only source reference, not a bundled second MCP runtime.",
    entities: ["Server", "Toolset", "Tool", "Request", "Grant", "Result", "Audit"],
    visualLabel: "Bounded MCP tool-surface preview",
  },
  {
    name: "GitHub CodeQL source",
    shortName: "CodeQL",
    source: "github/codeql",
    sourceUrl: "https://github.com/github/codeql/tree/74c8994c9fa3ca4551c01879ef9f74e3e09e791a",
    identity: "74c8994c9fa3",
    verification: "Pinned snapshot checked read-only",
    classification: "OFFICIAL SOURCE",
    binding: "PATH / SIZE + CRC SAMPLE",
    accent: "#8e9df7",
    tool: "terminal",
    purpose: "A large official source used only to strengthen negative-test and static-analysis thinking around the candidate.",
    stats: [["Source files", "58,970"], ["Indexed bytes", "334 MB"], ["CRC sample", "1,024 clean"], ["Commit", "74c8994"]],
    reused: "Negative fixtures, explicit query boundaries, and the habit of proving unsafe states fail.",
    refused: "Any claim that CodeQL CLI was installed, a CodeQL query ran, or this candidate passed a CodeQL scan.",
    boundary: "Paths and sizes match the paired source; a raw-blob sample requires CRLF normalization. That is source provenance evidence, not a security-scan result.",
    entities: ["Repo", "Pack", "Query", "Fixture", "Result", "Failure", "Boundary"],
    visualLabel: "Negative-test evidence model",
  },
  {
    name: "GitHub Branch Deploy source",
    shortName: "Branch Deploy",
    source: "github/branch-deploy",
    sourceUrl: "https://github.com/github/branch-deploy/tree/7ad5ec6a7e19e3e341846e4d33c4ed779b3e8036",
    identity: "7ad5ec6a7e19",
    verification: "Pinned public tree paired read-only",
    classification: "OFFICIAL SOURCE",
    binding: "208 / 208 TREE MATCH",
    accent: "#f08fb0",
    tool: "git",
    purpose: "The official action used to compare safe execution semantics for one-shot connector and release operations.",
    stats: [["Source files", "208"], ["Indexed bytes", "7.80 MB"], ["Tree match", "208 / 208"], ["Commit", "7ad5ec6"]],
    reused: "Dry-run and no-op semantics, commit-safety checks, locks, permission checks, and explicit reason codes.",
    refused: "Automatic deployment, branch mutation, or treating a green check as human release approval.",
    boundary: "The source informs failure semantics only. It does not authorize deploy, merge, Fuse, or pointer movement.",
    entities: ["Command", "Commit", "Permission", "Lock", "No-op", "Deploy", "Reason"],
    visualLabel: "Safe one-shot action topology",
  },
  {
    name: "GitHub Local Action source",
    shortName: "Local Action",
    source: "github/local-action",
    sourceUrl: "https://github.com/github/local-action/tree/b9351d8a8f1e6eed27646f4d892b49a3847ba180",
    identity: "b9351d8a8f1e",
    verification: "Pinned public tree paired read-only",
    classification: "OFFICIAL SOURCE",
    binding: "224 / 224 TREE MATCH",
    accent: "#79cd8c",
    tool: "terminal",
    purpose: "The official local-action source used to improve disposable fixtures and secret-safe one-shot execution tests.",
    stats: [["Source files", "224"], ["Indexed bytes", "1.13 MB"], ["Tree match", "224 / 224"], ["Commit", "b9351d8"]],
    reused: "Temporary fixture workspaces, registered-secret suppression, and log redaction.",
    refused: "Persisting a local runner, exposing credentials, or silently turning test fixtures into authority state.",
    boundary: "Only bounded test and redaction patterns were translated. No external source byte was copied into the plugin candidate.",
    entities: ["Action", "Fixture", "Input", "Secret", "Process", "Output", "Redaction"],
    visualLabel: "Disposable local-action test model",
  },
  {
    name: "Evidence Lane v0.9 brain",
    shortName: "EL v0.9",
    source: "Historical UEPC sector package",
    identity: "ZIP C51378BB05BB",
    verification: "Historical package inspected read-only",
    classification: "GENERATED BRAIN",
    binding: "NEGATIVE DELTA BASELINE",
    accent: "#879cf7",
    tool: "database",
    purpose: "A historical export used as a negative-delta baseline for the current connector and MCP candidate.",
    stats: [["Source files", "228"], ["Code files", "81"], ["Chunks", "1,367"], ["Foreign-key errors", "0"]],
    reused: "Regression comparison and historical source vocabulary only.",
    refused: "Version authority, current release identity, generator attribution, and implied feature advancement.",
    boundary: "The outer filename cannot make this package newer than the current v1.3 release candidate, and the archive does not bind the selected V5.9 binary.",
    entities: ["Package", "SQLite", "File", "Chunk", "Symbol", "Import", "Baseline"],
    visualLabel: "Historical negative-delta brain",
  },
  {
    name: "RIL historical mini brain",
    shortName: "RIL brain",
    source: "EvidenceOS V2 full-vertical mini brain",
    identity: "ZIP 53B2777588A9",
    verification: "June 16 package inspected read-only",
    classification: "GENERATED BRAIN",
    binding: "DESIGN EVIDENCE ONLY",
    accent: "#55cce7",
    tool: "database",
    purpose: "The historical Rathee Intelligence Lab brain preserves source-backed product-story and interaction evidence used to calibrate this site's full-width experience.",
    stats: [["Nodes", "47,274"], ["Edges", "101,260"], ["Source files", "127"], ["Read errors", "0"]],
    reused: "Full-width narrative hierarchy, floating pill navigation, active interaction clusters, and motion concepts.",
    refused: "Copying source bytes, calling the package current RIL authority, or claiming it was produced by the selected V5.9 executable.",
    boundary: "This June 16 mini brain is historical product evidence. Current UI concepts were separately inspected at a pinned RIL repository commit.",
    entities: ["Project", "Page", "Section", "Motion", "Asset", "Claim", "Evidence"],
    visualLabel: "Historical product-story brain",
  },
  {
    name: "Gold historical mini brain",
    shortName: "Gold brain",
    source: "EvidenceOS V2 full-vertical mini brain",
    identity: "ZIP D42EF938FC6F",
    verification: "June 16 package inspected read-only",
    classification: "GENERATED BRAIN",
    binding: "DESIGN EVIDENCE ONLY",
    accent: "#e8b95a",
    tool: "database",
    purpose: "The historical Gold Nexus Alpha brain preserves product, AI-studio, pill, and motion evidence used to calibrate the site's light institutional depth.",
    stats: [["Nodes", "143,998"], ["Edges", "296,484"], ["Source files", "444"], ["Read errors", "0"]],
    reused: "White, gold, cyan, and blue depth; large narrative type; institutional glass; and interactive AI-studio concepts.",
    refused: "Copying source bytes, calling the package current Gold authority, or claiming it was produced by the selected V5.9 executable.",
    boundary: "This June 16 mini brain is historical product evidence. Current UI concepts were separately inspected at a pinned Gold repository commit.",
    entities: ["Project", "Studio", "Prompt", "Pill", "Motion", "Claim", "Evidence"],
    visualLabel: "Historical product and studio brain",
  },
];

const guardOrder = ["ACTIVE", "GRANT LIVE", "CAPABILITY + ACTION", "LANE", "HOST"] as const;
type BrainLabStyle = CSSProperties & { "--source-accent"?: string };

function sourceTabTarget(event: KeyboardEvent<HTMLButtonElement>, index: number) {
  if (event.key === "Home") return 0;
  if (event.key === "End") return sourceBrains.length - 1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    return (index + 1) % sourceBrains.length;
  }
  if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    return (index - 1 + sourceBrains.length) % sourceBrains.length;
  }
  return null;
}

export function SourceBrainLab() {
  const [activeIndex, setActiveIndex] = useState(0);
  const active = sourceBrains[activeIndex];

  return (
    <div className="sourceBrainLab" style={{ "--source-accent": active.accent } as BrainLabStyle}>
      <div className="sourceBrainTabs" role="tablist" aria-label="Audited generator, source snapshots, and brain packages">
        {sourceBrains.map((brain, index) => (
          <button
            id={`source-brain-tab-${index}`}
            key={brain.shortName}
            type="button"
            role="tab"
            aria-selected={index === activeIndex}
            aria-controls="source-brain-panel"
            tabIndex={index === activeIndex ? 0 : -1}
            className={`rilPill sourceBrainTab${index === activeIndex ? " active" : ""}`}
            onClick={() => setActiveIndex(index)}
            onKeyDown={(event) => {
              const target = sourceTabTarget(event, index);
              if (target === null) return;
              event.preventDefault();
              setActiveIndex(target);
              document.getElementById(`source-brain-tab-${target}`)?.focus();
            }}
          >
            <span className="rilIconBadge" style={{ "--source-accent": brain.accent } as BrainLabStyle} aria-hidden="true">
              <OfficialToolIcon tool={brain.tool} size={20} decorative />
            </span>
            <span>{brain.shortName}</span>
          </button>
        ))}
      </div>

      <section className="sourceBrainPanel rilSectionPanel" aria-label={`${active.name} evidence`}>
        <AnimatePresence mode="wait">
          <motion.div
            className="sourceBrainDossier"
            id="source-brain-panel"
            role="tabpanel"
            aria-labelledby={`source-brain-tab-${activeIndex}`}
            key={active.shortName}
            initial={{ opacity: 0, x: -18 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 12 }}
            transition={{ duration: 0.38, ease: [0.22, 1, 0.36, 1] }}
          >
            <span className="verifiedSource"><i /> {active.verification}</span>
            <div className="sourceIdentityBar" aria-label="Source classification and binding status">
              <span>{active.classification}</span>
              <span>{active.binding}</span>
            </div>
            <h3>{active.name}</h3>
            {active.sourceUrl ? (
              <a className="sourceCommit" href={active.sourceUrl} target="_blank" rel="noreferrer">
                {active.source} <span>@ {active.identity}</span>
              </a>
            ) : (
              <span className="sourceCommit isHistorical">
                {active.source} <span>@ {active.identity}</span>
              </span>
            )}
            <p>{active.purpose}</p>
            <div className="sourceBrainStats">
              {active.stats.map(([label, value]) => (
                <div key={label}><strong>{value}</strong><span>{label}</span></div>
              ))}
            </div>
            <dl className="sourceUseBoundary">
              <div><dt>Reused</dt><dd>{active.reused}</dd></div>
              <div><dt>Refused</dt><dd>{active.refused}</dd></div>
            </dl>
            <p className="sourceBoundaryNote"><strong>Evidence boundary:</strong> {active.boundary}</p>
          </motion.div>
        </AnimatePresence>

        <div className="sourceBrainVisual" aria-label={`${active.visualLabel} surrounding one inspectable brain`}>
          <div className="sourceBrainCore">
            <EvidenceBrainAsset color={active.accent} />
          </div>
          {active.entities.map((entity, index) => (
            <motion.span
              className={`sourceEntity sourceEntity${index + 1}`}
              key={`${active.shortName}-${entity}`}
              initial={{ opacity: 0, scale: 0.82 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: index * 0.045 }}
            >
              {entity}
            </motion.span>
          ))}
          <span className="sourceBrainCaption">{active.visualLabel}</span>
        </div>
      </section>

      <section className="mcpGuardLab rilSectionPanel" aria-label="Agentic MCP route law">
        <div className="mcpGuardIntro">
          <span>Agentic Git integration</span>
          <h3>One eligible connector. Five ordered guards. Ambiguity stops.</h3>
          <p>
            Evidence Lane adopts the narrow route-safety pattern, not a second GitHub gateway.
            The first failing guard is written into the route receipt.
          </p>
          <div className="mcpReferenceLinks">
            <a href="https://github.com/github/gh-aw" target="_blank" rel="noreferrer">GitHub Agentic Workflows</a>
            <a href="https://github.com/github/github-mcp-server" target="_blank" rel="noreferrer">GitHub MCP Server</a>
          </div>
        </div>
        <div className="mcpGuardSequence" aria-label="Ordered connector guards">
          {guardOrder.map((guard, index) => (
            <div key={guard}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{guard}</strong>
              {index < guardOrder.length - 1 && <i aria-hidden="true" />}
            </div>
          ))}
        </div>
        <div className="mcpOutcomes">
          <span><i className="pass" /> one match: route</span>
          <span><i className="stop" /> zero matches: fail closed</span>
          <span><i className="stop" /> multiple matches: require exact preferred ID</span>
        </div>
      </section>
    </div>
  );
}
