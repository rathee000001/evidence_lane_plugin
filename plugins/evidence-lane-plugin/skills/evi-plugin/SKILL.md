---
name: evi-plugin
description: Govern persistent connector and AI-toolchain sidecars without changing Evidence Lane lifecycle state.
---

# Evidence Lane persistent plugin sidecar

This is an administrative sidecar, not a seventh primary Evidence Lane
control. It never changes the current lifecycle position, accepts a candidate,
moves a pointer, or invokes State Travel.

Use `/evi-storage` for the separate primary-storage inspection and selection
sidecar. Connector/plugin registration never silently changes storage authority.

- `LIST` calls `connector_plugin_catalog` and shows active and dropped history.
- `SETTINGS:CODEX` or `SETTINGS:CHATGPT` calls
  `connector_plugin_settings` and returns the eight structured slots for that
  host profile. The two profiles may contain different registrations. The host
  may render this structure in a settings UI, but Evidence Lane does not claim
  it can inject a new native settings panel into Codex or ChatGPT.
- `ADD:` calls `connector_plugin_register` only after the visible brief provides
  a lowercase ID, connector/toolchain kind, description, environment-variable
  **names** (never values), capabilities, canonical lanes, and actor. At most
  eight additional plugins may remain active. The grant also records a visible
  one-time purpose/reason, role, typed role-field schema, `CODEX`/`CHATGPT` host
  profiles, allowed actions, write scope, and an ISO expiry or `NO_EXPIRY`.
  `backend_runtime` may declare `python`, `java`, `kotlin`, `go`, `rust`, `cpp`,
  or `external_mcp`; the declaration is routing metadata and never authorizes
  execution by itself.
- `DROP:<plugin-id>` calls `connector_plugin_drop` with that exact case-sensitive
  confirmation and preserves the append-only registration/event history.
- `ROUTE:` calls `connector_plugin_route` for one visible capability, exact
  host profile, optional canonical lane, and optional exact preferred plugin
  ID. Active state, grant lifetime, capability/action, lane, and host guards
  are evaluated in that order. Zero matches, a denied preferred plugin, or
  multiple eligible plugins without a preferred ID remain fail-closed. The
  receipt returns candidate IDs, ordered guard traces, and the selected
  role-schema hash only when one route is actually selected.

Never persist credentials, access tokens, private model reasoning, or hidden
configuration values. Return to the exact prior lifecycle position after the
sidecar operation.
