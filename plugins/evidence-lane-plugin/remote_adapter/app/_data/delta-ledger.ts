export type DeltaPhase = "Foundation" | "V1.2 evolution" | "V1.3 hardening";

export type DeltaLedgerEntry = {
  order: number;
  id: string;
  phase: DeltaPhase;
  status: string;
  summary: string;
};

const foundationIds = [
  "EL-CODEX-PERSISTENT-LIFECYCLE-DELTA-003",
  "EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002",
  "EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001",
  "EL-RELEASE-DOCS-TEST-COMMIT-DELTA-004",
  "EL-GOVERNED-PUBLISH-REINSTALL-DEPLOY-DELTA-005",
  "EL-CHAT-LINEAGE-UNIVERSAL-RUNTIME-DELTA-006",
  "EL-ENV-UOP-VERIFIED-RUNTIME-PROJECTION-DELTA-007",
  "EL-MULTI-SOURCE-AUTOROUTE-DELTA-008",
  "EL-CHAT-LINEAGE-USAGE-METRICS-DELTA-009",
  "EL-EXIT-SLIP-NEXT-ACTION-DELTA-010",
  "EL-STATE-TRAVEL-FRESH-WINDOW-DELTA-011",
  "EL-MODE-INTERSECTION-SIDECAR-DELTA-012",
  "EL-LOCAL-DRIVE-CAPABILITY-ROUTING-DELTA-013",
  "EL-PERSISTENT-ATOMIC-BOOT-FLASH-DELTA-014",
  "EL-EVI-ORDERED-SURFACE-DELTA-015",
  "EL-CODEX-STEER-CHATLINEAGE-DELTA-016",
  "EL-STORAGE-CONNECTOR-POLICY-DELTA-017",
  "EL-GOVERNED-ADDITIONAL-PLUGINS-DELTA-018",
  "EL-CHATGPT-PUBLIC-CHROME-INSTALL-DELTA-019",
  "EL-PLAN-LANE-AUTOMATIC-LIFECYCLE-DELTA-020",
  "EL-GIT-HOST-PICKUP-BOOT-HIL-DELTA-021",
  "EL-STATE-TRAVEL-SUCCESSOR-HASH-BRIDGE-DELTA-022",
  "EL-SIX-CONTROL-AUTOROUTED-SURFACE-DELTA-023",
  "EL-ATOMIC-BOOT-HOST-STORAGE-CAPABILITY-DELTA-024",
  "EL-PROJECT-SECTOR-CHATLINEAGE-CANDIDATE-FANOUT-DELTA-025",
  "EL-DYNAMIC-MODE-SOURCE-PLUGIN-CLASSIFICATION-DELTA-026",
  "EL-ACTOR-MODEL-TOKEN-OUTPUT-TELEMETRY-DELTA-027",
  "EL-IMPLICIT-APPROVAL-BY-CONTINUATION-DELTA-028",
  "EL-FULL-GIT-HISTORY-BRAIN-DELTA-029",
  "EL-CHUNK-INCREMENTAL-REFRESH-HISTORY-DELTA-030",
  "EL-SEMANTIC-LANE-SCHEMA-MMD-TEST-DELTA-031",
  "EL-MINI-BRAIN-FUSION-PROJECT-ROUTING-DELTA-032",
  "EL-AI-TOOLCHAIN-CONNECTOR-BRAIN-DELTA-033",
  "EL-HOST-SPECIFIC-REFRESH-OUTPUT-HANDOFF-DELTA-034",
  "EL-CHATGPT-REMOTE-MCP-DURABLE-RUNTIME-DELTA-035",
  "EL-RELEASE-DOCS-INSTALL-HIL-DELTA-036",
  "EL-OPTIONAL-GIT-ARM-PROVENANCE-FALLBACK-DELTA-037",
  "EL-TOLERANT-HIL-INTENT-CLASSIFIER-DELTA-038",
  "EL-MIDTURN-STEER-CHATLINEAGE-DELTA-039",
  "EL-INDEPENDENT-RND-NAMING-POC-DELTA-040",
  "EL-VERCEL-ROUTE-RELEASE-IDENTITY-DELTA-041",
  "EL-DETACHABLE-BOOT-PLUGIN-SIDECAR-BRANDING-DELTA-042",
  "EL-DETERMINISTIC-PARALLEL-LANE-BUILD-REFRESH-DELTA-043",
  "EL-CONNECTOR-SETTINGS-ROLE-SCHEMA-POLYGLOT-DELTA-044",
  "EL-MCP-NATIVE-COLDSTART-LIFECYCLE-PERFORMANCE-DELTA-045",
  "EL-CHATGPT-PUBLIC-RUNTIME-INSTALL-CLEANUP-HIL-DELTA-046",
  "EL-SECRET-SAFE-TRACKED-SOURCE-BOUNDARY-DELTA-047",
  "EL-SQLITE-MMD-DOT-RECONCILIATION-DELTA-048",
  "EL-MULTIPAGE-ANIMATED-WEBSITE-DELTA-049",
  "EL-V070-BASELINE-FULL-POSTINSTALL-POC-DELTA-050",
  "EL-V110-EXACT-SHA-INSTALL-DEPLOY-AUDIT-HIL-DELTA-051",
] as const;

function readableId(id: string) {
  return id
    .replace(/^EVIDENCE-LANE-PLUGIN-/, "")
    .replace(/^EL-/, "")
    .replace(/-DELTA-\d+[A-Z]*$/, "")
    .replaceAll("-", " ")
    .toLowerCase()
    .replace(/^./, (character) => character.toUpperCase());
}

const foundation: DeltaLedgerEntry[] = foundationIds.map((id, index) => ({
  order: index + 1,
  id,
  phase: "Foundation",
  status: index === 45 ? "DONE OUTSIDE ACCEPTED AUTHORITY" : "ACCEPTED",
  summary: readableId(id),
}));

const evolution: DeltaLedgerEntry[] = [
  { order: 52, id: "EL-V120-EXACT-TOPOLOGY-BRAND-SITE-DELTA-052", phase: "V1.2 evolution", status: "IMPLEMENTED", summary: "Exact topology, brand roles, and responsive site." },
  { order: 53, id: "EL-V120-REAL-GIT-18-LANE-POC-FORENSIC-DELTA-053", phase: "V1.2 evolution", status: "IMPLEMENTED", summary: "Real-Git plus 18-lane POC and independent forensics." },
  { order: 54, id: "EL-V120-TUNNEL-FIRST-CHATGPT-CODEX-INSTALL-DELTA-054", phase: "V1.2 evolution", status: "PARTIAL / EXTERNAL GATES", summary: "Tunnel-first ChatGPT and Codex install path with exact blockers retained." },
  { order: 55, id: "EL-V120-PV4-HIL-AND-POSTAPPROVE-MAIN-ORDER-DELTA-055", phase: "V1.2 evolution", status: "HISTORICAL ORDER", summary: "PV4 HIL and post-approval publication order; no implicit promotion." },
  { order: 56, id: "EL-V120-FULL-APP-INTERACTION-PROMPT-STUDIO-DELTA-056", phase: "V1.2 evolution", status: "IMPLEMENTED", summary: "App-derived interaction model and governed Prompt Studio." },
  { order: 57, id: "EL-V120-EXTERNAL-CONNECTOR-BRAIN-INTAKE-DELTA-057", phase: "V1.2 evolution", status: "READ-ONLY COMPLETE", summary: "Separate generator, repository snapshot, and brain-package identities." },
  { order: 58, id: "EL-V120-GH-AW-MCP-ROUTE-CORRECTION-DELTA-058", phase: "V1.2 evolution", status: "IMPLEMENTED", summary: "Ordered connector guards and exact preferred-route selection." },
  { order: 59, id: "EL-V120-SECURE-TUNNEL-ID-AND-INSTALL-DELTA-059", phase: "V1.2 evolution", status: "RUNTIME ACTIVE", summary: "Secure tunnel identity, persistence, and exact-release readback boundary." },
  { order: 60, id: "EL-V120-CONNECTOR-SOURCE-USE-REPORT-DELTA-060", phase: "V1.2 evolution", status: "COMPLETE", summary: "Source-use, rejected-transplant, and copied-byte accounting." },
  { order: 61, id: "EL-V120-EXACT-FULL-APP-UI-ASSET-COMPONENT-CONFORMANCE-DELTA-061", phase: "V1.2 evolution", status: "ASSETS RETAINED / PRESENTATION SUPERSEDED", summary: "Exact logo, icon, static brain, and official tool assets." },
  { order: 62, id: "EL-V120-VIEWPORT-SQLITE-GLASS-WORKSPACE-DELTA-062", phase: "V1.2 evolution", status: "INTERACTIONS RETAINED / PRESENTATION SUPERSEDED", summary: "Glass-pill, lane, and four-file behavior without the retired fixed shell." },
  { order: 63, id: "EL-V120-SCROLL-SOURCE-BRAIN-FUSION-SITE-DELTA-063", phase: "V1.2 evolution", status: "CORRECTION ACTIVE", summary: "Scroll-led site retained; orbit and source-lab dominance are being removed." },
];

const hardening: DeltaLedgerEntry[] = [
  { order: 64, id: "DELTA_064", phase: "V1.3 hardening", status: "PASS LOCAL PRECOMMIT", summary: "Compact Codex and ChatGPT plugin icon from the supplied cube authority." },
  { order: 65, id: "DELTA_065", phase: "V1.3 hardening", status: "ACCEPTED IN PV5", summary: "Historical accepted-PV status compatibility without retroactive requalification." },
  { order: 66, id: "DELTA_066", phase: "V1.3 hardening", status: "COMPLETE", summary: "All 48 source authorities registered with explicit use/reject crosswalks." },
  { order: 67, id: "EL-V130-SAFE-ARCHIVE-AND-SQLITE-FORENSIC-INTAKE-DELTA-067A-B", phase: "V1.3 hardening", status: "PASS", summary: "Safe ZIP equivalence and embedded SQLite forensic intake." },
  { order: 68, id: "EL-V130-REAL-PROJECT-CUSTOM-SOURCE-SCHEMA-DELTA-068", phase: "V1.3 hardening", status: "PASS", summary: "Custom Source Schema compiler and project mapper." },
  { order: 69, id: "EL-V130-VERSION-PROVENANCE-AXES-DELTA-069", phase: "V1.3 hardening", status: "PASS", summary: "Independent application, generator, architecture, package, plugin, and snapshot identities." },
  { order: 70, id: "EL-V130-ENV-UOP-MODE-OPERATORS-HIL-DELTA-070", phase: "V1.3 hardening", status: "PASS", summary: "Mode-specific ENV/UOP formulas, operators, gates, and six-way effects." },
  { order: 71, id: "EL-V130-BOUNDED-POLYGLOT-GRAPH-PROVENANCE-DIFF-IMPACT-DELTA-071", phase: "V1.3 hardening", status: "PASS", summary: "Bounded polyglot graph, provenance, semantic diff, and downstream impact." },
  { order: 72, id: "EL-V130-GIT-HISTORY-FORENSICS-DELTA-072", phase: "V1.3 hardening", status: "PASS", summary: "Refs, commits, parents, blobs, hunks, lines, renames, and impact forensics." },
  { order: 73, id: "EL-V130-SCHEMA-DERIVED-18-LANE-TOPOLOGY-DELTA-073", phase: "V1.3 hardening", status: "PASS", summary: "Schema-derived additive Mermaid and DOT topology for all 18 lanes." },
  { order: 74, id: "EL-V130-DETERMINISTIC-RENDER-STALE-RECEIPT-DELTA-074", phase: "V1.3 hardening", status: "PASS", summary: "Deterministic SVG/PNG rendering with stale-receipt rejection." },
  { order: 75, id: "EL-V130-FOUR-FILE-EVERY-TABLE-FORENSIC-DELTA-075", phase: "V1.3 hardening", status: "PASS", summary: "Four-file and every-table forensic audit." },
  { order: 76, id: "EL-V130-PINNED-CI-MCP-GH-AW-AND-CI-ADAPTERS-DELTA-076A-B", phase: "V1.3 hardening", status: "PASS", summary: "Pinned CI/MCP/GitHub Actions plus CodeQL, preview, and local-action adapters." },
  { order: 77, id: "EL-V130-INTERACTIVE-SOURCE-BACKED-OPERATOR-GUIDE-DELTA-077", phase: "V1.3 hardening", status: "PASS", summary: "Interactive source-backed formulas, operators, and mode-specific HIL guide." },
  { order: 78, id: "EL-V130-ALL-SOURCE-ALL-LANE-FULL-HISTORY-RECONCILIATION-DELTA-078", phase: "V1.3 hardening", status: "PASS", summary: "All-source, all-lane, full-history reconciliation." },
  { order: 79, id: "EL-V130-WINDOWS-TUNNEL-AND-HOST-STORAGE-CONTINUITY-DELTA-079B-CD", phase: "V1.3 hardening", status: "PASS", summary: "Persistent Windows tunnel plus Codex/ChatGPT host, storage, ENV, and mode continuity." },
  { order: 80, id: "EL-V130-ACCEPTED-AUTHORITY-SUCCESSOR-AND-RELEASE-GATE-DELTA-080", phase: "V1.3 hardening", status: "PV6 CORRECTION ACTIVE", summary: "Accepted PV5 compatibility, executable pre-PV6 gate, release evidence, and replacement PV6 HIL." },
];

export const deltaLedger: readonly DeltaLedgerEntry[] = [
  ...foundation,
  ...evolution,
  ...hardening,
];
