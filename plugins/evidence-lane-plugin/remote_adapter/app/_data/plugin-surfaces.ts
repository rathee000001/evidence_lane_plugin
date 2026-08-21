export type PluginSurfaceFamily =
  | "Router"
  | "Lifecycle"
  | "Session"
  | "Mode"
  | "Connector"
  | "Storage"
  | "Canon"
  | "Learning";

export type PluginSurface = {
  id: string;
  label: string;
  command: string;
  family: PluginSurfaceFamily;
  description: string;
  setting: string;
  produces: string;
  boundary: string;
  primaryControl: boolean;
};

export const pluginSurfaces: readonly PluginSurface[] = [
  {
    id: "evi",
    label: "Evi",
    command: "/evi",
    family: "Router",
    description: "Root router for one persistent Evidence Lane project.",
    setting: "Routes to six primary lifecycle controls or a bounded sidecar.",
    produces: "A visible control choice with the current project boundary.",
    boundary: "State Travel is conditional; it is never an automatic seventh control.",
    primaryControl: false,
  },
  {
    id: "evi-additional-plugin",
    label: "Additional plugin",
    command: "/evi-additional-plugin",
    family: "Connector",
    description: "Adds one bounded host connector or AI toolchain grant.",
    setting: "Purpose, role schema, actions, scope, runtime, and expiry are required.",
    produces: "One auditable connector grant.",
    boundary: "The grant cannot change lifecycle state or widen its own scope.",
    primaryControl: false,
  },
  {
    id: "evi-boot",
    label: "Boot",
    command: "/evi-boot",
    family: "Lifecycle",
    description: "Verifies runtime, ENV/UOP Flash, host class, and durable storage.",
    setting: "Resume the existing session when its accepted authority is valid.",
    produces: "A verified session-entry receipt.",
    boundary: "No candidate, Fuse, or accepted-pointer movement.",
    primaryControl: true,
  },
  {
    id: "evi-build",
    label: "Build",
    command: "/evi-build",
    family: "Lifecycle",
    description: "Builds and seals an unaccepted candidate, then renders six-way HIL.",
    setting: "Code mode requires executable CI/CD evidence under PCM and MBA operators.",
    produces: "A candidate package, Exit Slip, receipts, and HIL prompt.",
    boundary: "Only exact APPROVE may invoke Fuse; tests are not approval.",
    primaryControl: true,
  },
  {
    id: "evi-canon",
    label: "Canon",
    command: "/evi-canon",
    family: "Canon",
    description: "Governs bounded task-to-task and task-to-subagent Canon exchange.",
    setting: "Bind the exact graph edge, task class, scope, direction, receiver, and expiry.",
    produces: "Sealed Canon envelopes, dispatch receipts, receiver decisions, results, or bounded backfire.",
    boundary: "Canon cannot promote Project Truth or Agent Learning, replay HIL, or move a PV pointer.",
    primaryControl: false,
  },
  {
    id: "evi-change-storage-connector",
    label: "Change storage connector",
    command: "/evi-change-storage-connector",
    family: "Storage",
    description: "Compatibility sidecar for project-scoped storage inspection and selection.",
    setting: "Host class and durable-runtime policy determine eligible routes.",
    produces: "A secret-free storage-selection receipt.",
    boundary: "ChatGPT runtime never uses Google Drive as live primary authority.",
    primaryControl: false,
  },
  {
    id: "evidence-lane-code-lifecycle",
    label: "Code lifecycle",
    command: "/evidence-lane-code-lifecycle",
    family: "Router",
    description: "End-to-end Code-mode lifecycle contract for one universal project.",
    setting: "Formula: plan → sandbox build → test → hash → package.",
    produces: "Linear work, executable gates, candidate evidence, and exact HIL.",
    boundary: "One writer; no main merge, pointer movement, or implicit promotion.",
    primaryControl: false,
  },
  {
    id: "evi-drop-additional-plugin",
    label: "Drop additional plugin",
    command: "/evi-drop-additional-plugin",
    family: "Connector",
    description: "Revokes one active connector or AI-toolchain grant.",
    setting: "Select the exact active grant identity.",
    produces: "An append-only revocation receipt.",
    boundary: "Prior connector history and evidence remain immutable.",
    primaryControl: false,
  },
  {
    id: "evi-exit-boot",
    label: "Exit Boot",
    command: "/evi-exit-boot",
    family: "Session",
    description: "Explicitly closes one persistent session and detaches live capture.",
    setting: "Close the exact active session only.",
    produces: "A sealed session-exit receipt.",
    boundary: "Installation and immutable project evidence are retained.",
    primaryControl: false,
  },
  {
    id: "evi-mode",
    label: "Mode",
    command: "/evi-mode",
    family: "Mode",
    description: "Loads ordered ENV/UOP laws for a known or explicit custom mode.",
    setting: "Each mode carries its own formula, operators, gate, accepted object, and rollback target.",
    produces: "A mode-binding receipt and visible formula.",
    boundary: "Mode selection does not create a candidate or move a pointer.",
    primaryControl: true,
  },
  {
    id: "evi-learning",
    label: "Agent Learning",
    command: "/evi-learning",
    family: "Learning",
    description: "Governs project-isolated AI Learning retrieval and candidate lifecycle.",
    setting: "Retrieve bounded accepted lessons or seal evidence-backed candidates under ENV/UOP.",
    produces: "Learning-only receipts, candidates, decisions, pointers, and revocations.",
    boundary: "Learning cannot overwrite Project Truth, consume Canon authority, or act as the Formula Engine.",
    primaryControl: false,
  },
  {
    id: "evi-plugin",
    label: "Plugin",
    command: "/evi-plugin",
    family: "Connector",
    description: "Inspects and governs persistent connector and AI-toolchain sidecars.",
    setting: "Host, capabilities, actions, scope, expiry, and grant state remain visible.",
    produces: "A connector inventory or bounded sidecar action.",
    boundary: "Connector administration is outside lifecycle promotion.",
    primaryControl: false,
  },
  {
    id: "evi-refresh",
    label: "Refresh",
    command: "/evi-refresh",
    family: "Lifecycle",
    description: "Rebuilds changed evidence while reusing unchanged content-addressed bytes.",
    setting: "The complete final source and prior accepted authority are rechecked.",
    produces: "An exact source delta and a newly sealed unaccepted candidate.",
    boundary: "Refresh stops at HIL and never implies Fuse.",
    primaryControl: true,
  },
  {
    id: "evi-rollback",
    label: "Rollback",
    command: "/evi-rollback",
    family: "Lifecycle",
    description: "Moves only the accepted pointer across immutable accepted versions.",
    setting: "Select an exact accepted project version.",
    produces: "A pointer-transition receipt.",
    boundary: "Source bytes and historical packages are never rewritten.",
    primaryControl: true,
  },
  {
    id: "evi-source-intake",
    label: "Source Intake",
    command: "/evi-source-intake",
    family: "Lifecycle",
    description: "Routes authorized sources across 18 canonical lanes and Project Engulf.",
    setting: "Auto-detection is ordered; exact user overrides remain visible.",
    produces: "A source registry and lane-routing receipt with Chat Lineage included.",
    boundary: "Intake classifies sources; it does not accept or promote them.",
    primaryControl: true,
  },
  {
    id: "evi-state-travel",
    label: "State Travel",
    command: "/evi-state-travel",
    family: "Session",
    description: "Preserves and resumes exact unfinished work, or enters accepted context when explicitly requested.",
    setting: "Only user request or genuine context exhaustion makes a verified fresh-host handoff eligible.",
    produces: "A hash-bound pointer, candidate, source, Plan Lane, Delta, resume-row, and host-profile handoff.",
    boundary: "Acceptance is not required for unfinished-work travel; the destination resumes the same verified row without consuming or substituting accepted history.",
    primaryControl: false,
  },
  {
    id: "evi-storage",
    label: "Storage",
    command: "/evi-storage",
    family: "Storage",
    description: "Inspects or selects Evidence Lane primary storage routing.",
    setting: "Local durable SQLite, durable mount, or configured transactional runtime by host class.",
    produces: "An append-only, secret-free storage receipt.",
    boundary: "A connector is a carrier or route, not an alternative source of truth.",
    primaryControl: false,
  },
] as const;

export const primaryLifecycleSurfaceIds = pluginSurfaces
  .filter((surface) => surface.primaryControl)
  .map((surface) => surface.id);
