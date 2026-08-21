import { repositoryUrl } from "./site";

export type RepositoryDocument = {
  path: string;
  label: string;
};

export const repositorySourceReference =
  process.env.NEXT_PUBLIC_EVIDENCE_LANE_SOURCE_REF
  ?? "agent/evi-v300-systemwide-release-hil-v3.0.0";

export const repositoryDocumentsByRoute = {
  "/": { path: "README.md", label: "Repository overview" },
  "/readme": { path: "README.md", label: "Repository overview" },
  "/memory": { path: "docs/MEMORY.md", label: "Memory and retrieval contract" },
  "/canon": {
    path: "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md",
    label: "Canon task graph and input decision contract",
  },
  "/ai-learning": { path: "docs/AI_LEARNING.md", label: "AI Learning contract" },
  "/skills": { path: "docs/SKILLS.md", label: "Skills contract" },
  "/mcp": { path: "docs/MCP.md", label: "Native MCP contract" },
  "/hooks": { path: "docs/HOOKS.md", label: "Lifecycle hook contract" },
  "/commands": { path: "docs/COMMANDS.md", label: "Command and control routes" },
  "/plan": { path: "docs/PLAN_AND_CHANGE_DISPLAY.md", label: "Plan and Changes contract" },
  "/git-ci": { path: "docs/GIT_AND_CI_CD.md", label: "Git and CI contract" },
  "/architecture": { path: "ARCHITECTURE.md", label: "System architecture" },
  "/lanes": { path: "docs/ARCHITECTURE.md", label: "Detailed lane architecture" },
  "/operators": {
    path: "docs/HOST_STORAGE_ENV_MODE_CONTINUITY.md",
    label: "Host, storage, ENV/UOP, and mode contract",
  },
  "/studio": { path: "README.md", label: "Repository overview" },
  "/proof": {
    path: "docs/IMPLEMENTATION_TRACEABILITY.md",
    label: "Implementation traceability",
  },
  "/provenance": {
    path: "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    label: "Upstream provenance",
  },
  "/release": {
    path: "docs/RELEASE_AND_COMPATIBILITY.md",
    label: "Release and compatibility contract",
  },
  "/connect": { path: "docs/HOST_CAPABILITY_MATRIX.md", label: "Host capability matrix" },
  "/hil": { path: "docs/FIRST_HIL_RUNBOOK.md", label: "Six-way HIL runbook" },
  "/privacy": { path: "SECURITY.md", label: "Security and privacy policy" },
  "/security": { path: "SECURITY.md", label: "Security policy" },
  "/terms": { path: "docs/TERMS_AND_CONDITIONS.md", label: "Terms and conditions" },
  "/license": { path: "LICENSE.md", label: "License terms" },
  "/copyright": { path: "docs/COPYRIGHT.md", label: "Copyright authority" },
  "/third-party": {
    path: "plugins/evidence-lane-plugin/THIRD_PARTY_NOTICES.md",
    label: "Third-party licenses and rights",
  },
  "/credits": {
    path: "docs/CREDITS_AND_CONTRIBUTIONS.md",
    label: "Credits and contribution policy",
  },
  "/support": { path: "README.md", label: "Repository overview" },
  "/helper": { path: "docs/USER_HELPER_GUIDE.md", label: "User helper guide" },
  "/tunnel": { path: "docs/USER_TUNNEL_GUIDE.md", label: "User tunnel guide" },
} as const satisfies Record<string, RepositoryDocument>;

export type RepositoryDocumentRoute = keyof typeof repositoryDocumentsByRoute;

export function repositoryDocumentForPath(pathname: string): RepositoryDocument {
  const normalized = pathname.length > 1 ? pathname.replace(/\/$/, "") : pathname;
  return repositoryDocumentsByRoute[normalized as RepositoryDocumentRoute]
    ?? repositoryDocumentsByRoute["/"];
}

export function repositoryDocumentUrl(document: RepositoryDocument): string {
  return `${repositoryUrl}/blob/${repositorySourceReference}/${document.path}`;
}
