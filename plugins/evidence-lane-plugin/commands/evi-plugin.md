---
description: Govern up to eight persistent connector or AI-toolchain sidecars
argument-hint: <LIST | ADD: governed plugin brief | DROP:plugin-id | ROUTE: capability>
---

# /evi-plugin

This is an administrative sidecar, not a seventh primary Evidence Lane
control. It never changes the current lifecycle position, accepts a candidate,
moves a pointer, or invokes State Travel.

Use `/evi-storage` for the separate primary-storage inspection and selection
sidecar. Connector/plugin registration never silently changes storage authority.

- `LIST` calls `connector_plugin_catalog` and shows active and dropped history.
- `ADD:` calls `connector_plugin_register` only after the visible brief provides
  a lowercase ID, connector/toolchain kind, description, environment-variable
  **names** (never values), capabilities, canonical lanes, and actor. At most
  eight additional plugins may remain active. The grant also records a visible
  purpose, allowed actions, write scope, and an ISO expiry or `NO_EXPIRY`.
- `DROP:<plugin-id>` calls `connector_plugin_drop` with that exact case-sensitive
  confirmation and preserves the append-only registration/event history.
- `ROUTE:` calls `connector_plugin_route` for one visible capability and optional
  canonical lane; deterministic fallback remains fail-closed.

Never persist credentials, access tokens, private model reasoning, or hidden
configuration values. Return to the exact prior lifecycle position after the
sidecar operation.
