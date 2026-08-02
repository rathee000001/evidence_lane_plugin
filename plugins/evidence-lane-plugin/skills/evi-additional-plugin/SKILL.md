---
name: evi-additional-plugin
description: Add one bounded host-specific connector or AI toolchain grant with purpose, role schema, actions, scope, runtime, and expiry.
---

# Add Additional Plugin

1. Inspect `connector_plugin_catalog`.
2. Require a lowercase plugin ID, connector/toolchain kind, one visible
   purpose/reason, role, typed role-field schema, CODEX/CHATGPT host profiles,
   allowed actions, canonical lanes, write scope, expiry, optional declared
   backend runtime, and configuration environment-variable names only.
3. Call `connector_plugin_register`. Never store secret values.
4. Verify the append-only registration receipt, role-schema hash/table, target
   host settings slots, and active maximum of eight.
5. Return to the prior lifecycle position; registration is not acceptance.
