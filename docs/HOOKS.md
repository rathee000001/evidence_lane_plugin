<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / 2026-08-23 -->

# Codex hooks

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
