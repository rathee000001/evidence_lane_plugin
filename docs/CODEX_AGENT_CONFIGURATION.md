# Codex AGENTS.md integration

Evidence Lane treats `AGENTS.md` as first-class agent-configuration authority.
It is not documentation metadata and it is not a hook-only input.

## Bound official contracts

- Codex AGENTS.md guide: <https://learn.chatgpt.com/docs/agent-configuration/agents-md>
  - fetched for R255 on 2026-08-22
  - content SHA-256: `9D1F87A2D1CB55B4782B95ABE710692B35B9659789C2DB31A22C7074A3383E8E`
- Codex hooks guide: <https://learn.chatgpt.com/docs/hooks>
  - fetched for R255 on 2026-08-22
  - content SHA-256: `017D2A86BC8654FB5E566F968019E5BC23F65AB0BCA3B051B92EC74BC6DA130A`

The installed host remains the behavioral test target. A stored guide hash is
evidence of the contract reviewed for this Delta; it is not a substitute for
installed-host parity.

## Discovery and precedence

For the active Codex home, Evidence Lane selects the first non-empty file from
`AGENTS.override.md` and `AGENTS.md`. For the governed project, it walks from
the registered repository root to the request working directory. At each
directory it selects at most one non-empty source using this precedence:

1. `AGENTS.override.md`
2. `AGENTS.md`
3. configured `project_doc_fallback_filenames`, in configured order

The selected sources merge global first and then project root to working
directory, so nearer guidance has later precedence. Empty sources are skipped.
The active Codex `config.toml` controls configured fallback basenames and
`project_doc_max_bytes`; the default maximum is 32 KiB.

Invalid UTF-8, NUL content, path escape, an invalid fallback basename, malformed
TOML, or an individual/combined size violation fails closed. No alternate
project, parent directory, stale task, donor task, or fallback project root is
consulted.

## Runtime binding and public receipt

Resolution is cached once for an exact service run and exact tuple of project,
governed session, invoking Codex task/deep link, host session, workspace,
working directory, active stable Plan task ID, and execution profile. A new
service process, restart, reconnect, or helper attachment rebuilds the chain
under the invoking task identity.

Instruction text remains local. Existing MCP, SDK, command-routing, Plan,
Source Intake, Refresh, runtime, and State Travel seams expose only the bounded
source locators, byte counts, content hashes, source-chain hash, binding hash,
and AGENTS.md authority hash. Absolute source paths and private reasoning are
not returned.

AGENTS.md cannot merge tasks, widen tool or write permissions, infer approval or
HIL, move a PV pointer, create a candidate, promote Project Truth/Learning, or
cross project/task/worktree boundaries.

## Hooks remain optional

Core public Evidence Lane actions execute through their explicit service and
SDK routes with hooks off. Hooks may observe or automate lifecycle events, but
they are not the sole path for an authoritative action. Trust and enablement
remain separate.

The reviewed host contract names 11 lifecycle events: `SessionStart`,
`SubagentStart`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`,
`PostCompact`, `UserPromptSubmit`, `SubagentStop`, `Stop`, and `SessionEnd`.
Only command handlers execute under the reviewed contract; prompt and agent
handlers may be parsed but are not treated as executable routing paths.
