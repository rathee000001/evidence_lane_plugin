---
name: evi-exit-boot
description: Explicitly close one persistent Evidence Lane session and detach live Flash/capture without removing the installation or immutable evidence.
---

# Evidence Lane Exit Boot

Call `session_close` with a visible reason. Close only the active governed
session and require the returned runtime-activation receipt to show that exact
session detached. When no other governed sessions remain, the installation
runtime must be `DETACHED`: ENV/UOP Flash context and visible prompt/response
capture are off. Preserve the plugin installation, locked Flash verification
receipt, immutable store, lineage, backlog, candidates, accepted PVs, and
pointer history. A later `/evi-boot` re-verifies and reattaches them.

On ChatGPT Pro's governed action profile, do not claim or simulate a successful
`session_close`. Read
`runtime_activation_status`, `session_flash_status`, `pv_status`, and the
governed Exit Slip, then label the result
`CHATGPT_PRO_READ_EXIT_OBSERVED`. Explain that the skill has stopped using the
read connection for the current answer while the host runtime remains
unchanged. An actual detach requires the governed write-capable runtime
operator.
