---
name: evi-additional-plugin
description: Add one bounded persistent connector or AI toolchain grant with purpose, actions, scope, and expiry.
---

# Add Additional Plugin

1. Inspect `connector_plugin_catalog`.
2. Require a lowercase plugin ID, connector/toolchain kind, visible purpose,
   allowed actions, canonical lanes, write scope, expiry, and configuration
   environment-variable names only.
3. Call `connector_plugin_register`. Never store secret values.
4. Verify the append-only registration receipt and active maximum of eight.
5. Return to the prior lifecycle position; registration is not acceptance.
