# Evidence Lane 2.2.0 hooks

The package registers exactly eight lifecycle events:

1. `SessionStart`
2. `UserPromptSubmit`
3. `PreToolUse`
4. `PostToolUse`
5. `PreCompact`
6. `PostCompact`
7. `Stop`
8. best-effort `SessionEnd`

Hooks transport visible lifecycle facts and receipts. They do not own task
classification, Plan mutation, candidate creation, HIL decisions, Fuse,
pointer movement, Git, installation, or deployment. Skills own the governed
procedure and the native MCP owns authority-backed state changes.

`PermissionRequest` remains unregistered unless the host capability is
positively proven. Subagent hook events are out of scope. Installed-host proof
must exercise the supported host surface; a source manifest alone cannot prove
that hooks loaded.

Windows helper processes launched by hook, prewarm, or tunnel support code must
either remain a deliberately visible persistent operator process or start with
the supported no-window flags. Repeated visible console flashes are a defect,
not an acceptable background-launch behavior.

See [detailed architecture](ARCHITECTURE.md) and
[host continuity](HOST_STORAGE_ENV_MODE_CONTINUITY.md).
