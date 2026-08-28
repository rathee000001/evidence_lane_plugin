<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Codex hooks

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


Evidence Lane declares Codex hooks as optional lifecycle transports. Every
public skill, command, SDK route, MCP read/write, Source Intake, Refresh, Plan,
Canon, Learning, Memory, Universe, Git/CI, install, and lifecycle action must
remain explicitly usable while hooks are disabled. Hooks may observe or
automate a lifecycle boundary; they are never the sole authority for a public
action, approval, HIL decision, candidate, or accepted-pointer transition.

## Current host contract

The implementation is bound to the current [Codex Hooks guide](https://learn.chatgpt.com/docs/hooks),
read on 2026-08-22 with content SHA-256
`017D2A86BC8654FB5E566F968019E5BC23F65AB0BCA3B051B92EC74BC6DA130A`.
The current command-event registry contains eleven events in stable package
order:

1. `SessionStart`
2. `SubagentStart`
3. `UserPromptSubmit`
4. `PreToolUse`
5. `PermissionRequest`
6. `PostToolUse`
7. `PreCompact`
8. `PostCompact`
9. `SubagentStop`
10. `Stop`
11. `SessionEnd`

`SessionEnd` is a main-thread event and is not used for subagents. Matching
hooks from the active configuration layers and the plugin may all run; multiple
command handlers for one event may run concurrently. Only command handlers
execute under the current official contract. Prompt and agent handlers may be
parsed but are skipped, so prompt intent detection and public routing remain in
the plugin router rather than in a synthetic prompt hook.

Each of the eleven classes exposes four real native handler rows in the Codex
surface: validate/bound, seal/deduplicate, transport, and verify/emit. This is
44 native subhandlers, not one `Hook 1` wrapper containing 44 invisible logical
labels. Because the host may launch sibling handlers concurrently, each later
stage waits for and validates the prior stage's sealed receipt. Only transport
executes the original event implementation, exactly once; emit returns that
stored result. No duplicate or no-op row is used to manufacture the count.

## Control boundary

- `PermissionRequest` is observation-only. Evidence Lane emits no allow or deny
  decision, leaving the ordinary host permission flow authoritative.
- `SubagentStart` and `SubagentStop` verify the exact parent task/session
  binding and emit no continuation or subagent control.
- `Stop` emits the exact empty object and never requests another turn.
- `SessionEnd` is best-effort, output-inert, and bounded inside the host timeout.
- No hook imports cross-task state, private reasoning, raw credentials, or host
  memory as authority.

Trust and enablement are independent. A hook definition can be reviewed and
trusted by its exact installed hash while remaining disabled. The maintained
test installation keeps all hooks OFF until the designated installed-host
verification owner proves each event independently. The supported
`--progressive-all` installed-host route then leaves every passing event ON and
requires a final `hooks/list` readback showing all eleven trusted and enabled
before the matrix can pass. That all-ON state is the normal corrected release
state and is preserved across exact-task restart, reattachment, and upgrades.

A failing enabled hook is disabled alone through a compare-and-swap
`config/batchWrite`, followed by an exact `hooks/list` readback; unrelated
passing hook states remain ON and the active Goal is not paused. The failure
receipt names only the failed event, which is repaired and retested through the
same progressive route. It is re-enabled only after PASS. An upgrade preserves
the current verified enablement state; it neither blankets all hooks OFF nor
enables an unverified definition as a side effect.

## Evidence boundary

Package configuration and isolated-runtime tests prove declarations and local
behavior only. Installed-host invocation requires correlated native
`hook/started` and `hook/completed` notifications bound to the exact installed
selector, event key, definition hash, task, workspace, and host session. Missing
events remain pending; they are never relabeled as successful or unavailable.
