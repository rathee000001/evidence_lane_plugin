export type StudioArtifact = {
  id: string;
  label: string;
  format: "SQLite" | "Markdown" | "JSON" | "CSV" | "Chart" | "Table" | "MMD" | "DOT";
  href: string;
  identity: string;
  status: "SEALED_PUBLIC_DEMONSTRATION" | "COMMITTED_PUBLIC_SAFE" | "DERIVED_VIEW";
  purpose: string;
  boundary: string;
};

export const studioArtifactCatalog: readonly StudioArtifact[] = [
  {
    id: "project-readme",
    label: "Evidence Lane 2.2.0 project narrative",
    format: "Markdown",
    href: "/readme",
    identity: "release=2.2.0",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Whole-project mechanism, lifecycle, host, proof, and limitation narrative.",
    boundary: "Narrative is not acceptance or production proof.",
  },
  {
    id: "architecture-markdown",
    label: "Architecture contract",
    format: "Markdown",
    href: "/architecture",
    identity: "route=/architecture",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Parallel evidence and serial authority explanation.",
    boundary: "Public explanation; source and tests remain the implementation evidence.",
  },
  {
    id: "release-identity",
    label: "Release identity and host matrix",
    format: "Table",
    href: "/connect",
    identity: "release=2.2.0 actions=62 read=21 write=41 skills=15 hooks=8",
    status: "DERIVED_VIEW",
    purpose: "Truthful host capability comparison.",
    boundary: "A table cannot establish live connection health.",
  },
  {
    id: "delta-ledger",
    label: "Append-only Delta ledger",
    format: "Table",
    href: "/#delta-ledger",
    identity: `authority=${websiteCurrentExecutionBoundary.canonicalAuthority} live-projection=${websiteCurrentExecutionBoundary.taskCount}-rows final-row=${websiteCurrentExecutionBoundary.finalHilPublicOrder} active-row=${websiteCurrentExecutionBoundary.activePublicOrder} snapshot=${websiteCurrentExecutionBoundary.websitePlanSnapshotSha256}`,
    status: "DERIVED_VIEW",
    purpose: "Ordered corrections, supersessions, and live task projection.",
    boundary: "Historical sealed receipts remain immutable and distinct from the live projection.",
  },
  {
    id: "studio-rag-json",
    label: "Prompt Studio RAG projection",
    format: "JSON",
    href: "/api/studio-query",
    identity: "schema=evidence-lane.prompt-studio-rag-index.v1",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Corpus identity, source count, chunk count, and retrieval gate.",
    boundary: "The projection excludes private runtime paths and is not accepted-project storage.",
  },
  {
    id: "lane-catalog-json",
    label: "Eighteen-lane artifact catalog",
    format: "JSON",
    href: "/proof",
    identity: "lanes=18 canonical-files-per-lane=4",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Discover the demonstration SQLite, MMD, DOT, and receipt packages.",
    boundary: "Dummy lane packages prove contract shape, not customer data behavior.",
  },
  {
    id: "sqlite-brain",
    label: "SQLite Brain demonstration sector",
    format: "SQLite",
    href: "/dummy-lane-packages/sqlite_brain/sqlite_brain_sector_v001.sqlite",
    identity: "SHA256=77E8DE63AB997C01D5506EA63A3B2E52C359DCA2494FD9999F2D8729A5F3C5CA",
    status: "SEALED_PUBLIC_DEMONSTRATION",
    purpose: "Downloadable read-only demonstration database with integrity and schema evidence.",
    boundary: "No arbitrary SQL executes in the public site; live read-only SQL is optional and currently not configured.",
  },
  {
    id: "sqlite-topology-mmd",
    label: "SQLite Brain Mermaid topology",
    format: "MMD",
    href: "/dummy-lane-packages/sqlite_brain/sqlite_brain.mmd",
    identity: "SHA256=5DA38CFB32048654B5F43C6979151B6FFF832F6D135359CD4A4E25D3B635138C",
    status: "SEALED_PUBLIC_DEMONSTRATION",
    purpose: "Human-readable topology derived from the demonstration database.",
    boundary: "Must reconcile with SQLite and DOT identity.",
  },
  {
    id: "sqlite-topology-dot",
    label: "SQLite Brain DOT topology",
    format: "DOT",
    href: "/dummy-lane-packages/sqlite_brain/sqlite_brain.dot",
    identity: "SHA256=84CA0F5A6CBFCB617832A94873143EC3EC8B603F5E35EA275552124B1C215D41",
    status: "SEALED_PUBLIC_DEMONSTRATION",
    purpose: "Machine-comparable topology derived from the same lane facts.",
    boundary: "Matching filenames are insufficient; node and edge identities must reconcile.",
  },
  {
    id: "lane-receipt-json",
    label: "SQLite Brain refresh receipt",
    format: "JSON",
    href: "/dummy-lane-packages/sqlite_brain/refresh_receipt.json",
    identity: "SHA256=BE3BB4F1FF886F30D423DE74E376CA281FCEE248E332CD35633076057E4852A9",
    status: "SEALED_PUBLIC_DEMONSTRATION",
    purpose: "Inspect source, parser, refresh, graph, and proof facts.",
    boundary: "A refresh receipt is not HIL acceptance.",
  },
  {
    id: "capability-matrix-csv",
    label: "Evidence Lane 2.2.0 Codex capability matrix",
    format: "CSV",
    href: "/studio-artifacts/evidence-lane-capability-matrix-v220.csv",
    identity: "release=2.2.0 rows=3",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Portable comparison of durable, headless, and ephemeral Codex profiles.",
    boundary: "Declared capability must still be confirmed by live host receipts.",
  },
  {
    id: "capability-chart",
    label: "Executable capability chart",
    format: "Chart",
    href: "/studio#artifact-lab",
    identity: "Codex=62 reads=21 writes=41",
    status: "DERIVED_VIEW",
    purpose: "Visualize the exact native Codex catalog without hiding the denominator.",
    boundary: "The chart is derived from the release matrix and never substitutes for runtime proof.",
  },
  {
    id: "mode-contract-json",
    label: "Mode and operator contract",
    format: "JSON",
    href: "/operators",
    identity: "mode=code ci_cd=CONTROLLED_REQUIRED",
    status: "DERIVED_VIEW",
    purpose: "Show the selected operating law and accountable operators.",
    boundary: "Mode selection does not authorize lifecycle mutation.",
  },
  {
    id: "release-channels-json",
    label: "Stable, future-test, and archive release channels",
    format: "JSON",
    href: "/connect",
    identity: "schema=evidence-lane.release-channels.v1",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Explain atomic promotion and retained fallback boundaries.",
    boundary: "Promotion still requires matching receipts and explicit HIL.",
  },
  {
    id: "chatgpt-connection-json",
    label: "Registered ChatGPT connection contract",
    format: "JSON",
    href: "/connect",
    identity: "connection=plugin_asdk_app_6a7743d238e48191be8b69c87fb71d7f",
    status: "COMMITTED_PUBLIC_SAFE",
    purpose: "Record remote identity and host-specific action availability.",
    boundary: "The current live page remains mismatched until the registered metadata itself is refreshed.",
  },
] as const;

const artifactById = new Map(studioArtifactCatalog.map((artifact) => [artifact.id, artifact]));

export function studioArtifactsFor(ids: readonly string[]) {
  return ids.flatMap((id) => {
    const artifact = artifactById.get(id);
    return artifact ? [artifact] : [];
  });
}

export const studioRetrievalServices = {
  lexical: "READY_COMMITTED_BM25_TFIDF_RRF",
  sql: "WIRED_NOT_CONFIGURED_READ_ONLY_ONLY",
  vector: "WIRED_NOT_CONFIGURED_OPTIONAL",
  generation: "DETERMINISTIC_REVIEWED_FALLBACK_OPENROUTER_OPTIONAL",
} as const;
import { websiteCurrentExecutionBoundary } from "./website-current-execution.ts";
