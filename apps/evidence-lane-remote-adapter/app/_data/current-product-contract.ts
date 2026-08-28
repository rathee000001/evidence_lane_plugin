export const hookEvents = [
  "SessionStart",
  "SubagentStart",
  "UserPromptSubmit",
  "PreToolUse",
  "PermissionRequest",
  "PostToolUse",
  "PreCompact",
  "PostCompact",
  "SubagentStop",
  "Stop",
  "SessionEnd",
] as const;

export const currentProductContract = {
  release: "3.0.0",
  nativeMcp: {
    server: "evidence-lane",
    totalActions: publicActionRegistry.tool_count,
    readActions: publicActionRegistry.read_tool_count,
    writeActions: publicActionRegistry.write_tool_count,
    sourceCatalogSha256: publicActionRegistry.source_catalog_sha256,
    projectionSha256: publicActionRegistry.projection_sha256,
  },
  governedSkillCount: publicActionRegistry.governed_skill_count,
  primaryControlCount: 6,
  hookEventCount: publicActionRegistry.hook_control.event_count,
  hookHandlerType: "command",
  hooksRequiredForExplicitActions: false,
  hooksTrustedAndEnabledAreSeparate: true,
  canonicalLaneCount: 18,
  ordinaryLiveAuthorityCount: publicActionRegistry.ordinary_live_authority_count,
  envUopGovernedSixWayArms: publicActionRegistry.env_uop_governed_six_way_arms,
  linkedOperationalAuthorities: publicActionRegistry.linked_operational_authorities,
  hilOnlyAuthorities: publicActionRegistry.hil_only_authorities,
  sourceContracts: {
    catalog: "skills/evi/references/mcp-tool-routing.v1.json",
    hooks: "hooks/hooks.json",
    release: "scripts/codex-release-channel.json",
    lanes: "src/evidence_lane_plugin/lane_engine.py",
    publicActions: "schemas/public-action-schemas.v001.json",
    remotePublicActions:
      "apps/evidence-lane-remote-adapter/app/_data/public-action-registry.json",
  },
} as const;

if (hookEvents.length !== currentProductContract.hookEventCount) {
  throw new Error("Remote hook projection drifted from the public action registry.");
}

export const authorityPlanes = [
  ["Source", "Authorized bytes, parser/tool identity, provenance, and exclusions."],
  ["Worktree", "Commit, tree, dirty-path identity, and preserved untracked bytes."],
  ["Project Truth", "Lane SQLite, graphs, pointers, receipts, and accepted evidence."],
  ["Plan", "Canonical row identity, dependencies, one active Delta, and HIL anchors."],
  ["ChatLineage", "Visible prompts, responses, tools, steers, and task boundaries."],
  ["Installed runtime", "Exact package, catalog, skills, hooks, tunnel, task, and host binding."],
  ["Candidate", "Immutable tested evidence that remains outside accepted truth."],
  ["Accepted", "The PV pointer moved only by the exact governed human decision."],
  ["Canon", "Typed task exchange and receiver-owned classification without Project HIL authority."],
  ["AI Learning", "Project-isolated lessons and a separate six-way decision lifecycle."],
  ["Project Memory", "Bounded cross-sector locators and typed links; never a prose replacement for authority."],
  ["Project Universe", "A read-optimized derived graph; never owner of lanes, Plan, candidate, HIL, or pointer."],
] as const;

export type HostCapabilityProfile = {
  id: string;
  label: string;
  interaction: string;
  storage: string;
  nativeRoute: string;
  toolGapRoute: string;
  setupFrequency: string;
  credentialBoundary: string;
  lifecycleBoundary: string;
};

export const hostCapabilityProfiles: readonly HostCapabilityProfile[] = [
  {
    id: "desktop-durable",
    label: "Durable Codex desktop",
    interaction: "Interactive app on a local or persistent host",
    storage: "Durable project-scoped local SQLite",
    nativeRoute: "Use attested package-local MCP directly; no tunnel is required when the needed native capabilities are present.",
    toolGapRoute: "A version-bound support tunnel is eligible only for a measured host-tool gap.",
    setupFrequency: "At most once per persistent host and exact release when that gap is proven",
    credentialBoundary: "Any route credential is host-managed, masked, and separate from project evidence.",
    lifecycleBoundary: "Exact project, session, task, workspace, package, runtime, and capability receipts remain mandatory.",
  },
  {
    id: "cli-durable",
    label: "Durable local CLI",
    interaction: "Local CLI without the interactive desktop container",
    storage: "Durable project-scoped local SQLite",
    nativeRoute: "Use direct native MCP when the CLI exposes the required tool surface.",
    toolGapRoute: "Use the support tunnel only for an attested missing host capability, never from CLI identity alone.",
    setupFrequency: "At most once per persistent host and exact release for a proven gap",
    credentialBoundary: "Provider and transport credentials remain route-specific and secret-safe.",
    lifecycleBoundary: "The CLI cannot infer HIL, candidate acceptance, Fuse, or pointer movement from a successful command.",
  },
  {
    id: "headless-persistent",
    label: "Persistent headless API",
    interaction: "Direct API process on a durable machine or VM",
    storage: "Durable local PV store",
    nativeRoute: "The API layer is tunnel-independent and verifies ENV/UOP at each invocation entry.",
    toolGapRoute: "Only the concrete runtime may attest a separate host-tool gap; headless status alone proves none.",
    setupFrequency: "No interactive tunnel setup by default",
    credentialBoundary: "API billing and account tier do not select storage, transport, or lifecycle authority.",
    lifecycleBoundary: "Every invocation rebinds exact project, session, task, workspace, and execution profile.",
  },
  {
    id: "ephemeral-mounted",
    label: "Ephemeral VM with durable mount",
    interaction: "Perishable runner with a durable project mount",
    storage: "Mounted durable SQLite",
    nativeRoute: "The API layer uses its native route and restores from exact durable authority.",
    toolGapRoute: "An interactive host gap may select a VM-lifetime support tunnel; ephemerality alone does not.",
    setupFrequency: "Per VM only when an interactive tool gap is proven",
    credentialBoundary: "Credentials expire with the exact VM route and never enter public receipts.",
    lifecycleBoundary: "An expiring single-consumption host-entry envelope is required before writes.",
  },
  {
    id: "ephemeral-connector",
    label: "Ephemeral VM without a mount",
    interaction: "Perishable runner with no durable filesystem authority",
    storage: "Configured transactional durable connector or fail closed",
    nativeRoute: "The API layer remains tunnel-independent; storage must be proven separately.",
    toolGapRoute: "A tunnel cannot substitute for missing durable authority.",
    setupFrequency: "Connector and entry envelope are verified for every invocation",
    credentialBoundary: "Connector secrets stay outside prompts, process arguments, and receipts.",
    lifecycleBoundary: "No source write runs until the transactional authority and exact host-entry envelope pass.",
  },
  {
    id: "interactive-ephemeral",
    label: "Interactive Codex on an ephemeral VM",
    interaction: "Interactive app inside a VM-lifetime host",
    storage: "Durable mount or configured transactional connector",
    nativeRoute: "Use native MCP directly when the VM exposes the required capabilities.",
    toolGapRoute: "A version-matched support tunnel is eligible only for an attested interactive host-tool gap.",
    setupFrequency: "Once per exact VM instance when that gap is proven",
    credentialBoundary: "Tunnel credentials are bound to the current VM lifetime and kept secret-safe.",
    lifecycleBoundary: "Reconnect and restart must reject stale task, runtime, package, and VM identities.",
  },
  {
    id: "review-only",
    label: "Review-only client",
    interaction: "Website, artifact viewer, or other non-executing surface",
    storage: "Receipts and published artifacts only; no project runtime",
    nativeRoute: "No lifecycle execution route is exposed.",
    toolGapRoute: "A tunnel cannot turn a review surface into lifecycle authority.",
    setupFrequency: "None",
    credentialBoundary: "No lifecycle credential is requested or stored.",
    lifecycleBoundary: "The client may request a decision but cannot consume HIL, Fuse, rollback, or State Travel.",
  },
] as const;
import publicActionRegistry from "./public-action-registry.json" with { type: "json" };
