# Prompt suggestion compatibility

## Verified distinction

The gray next-action text shown in the supplied Claude Code screenshot is a
host feature, not evidence that the Evidence Lane MCP wrote Claude's composer.
Claude Code documents **Prompt suggestions** as a background, host-generated
request based on the conversation and Git history. The user accepts it with
Tab or Right Arrow. The pinned read-only comparison at commit
`889c28a33f16469e8e74aa62ef65559e4a74379d` and tree
`ad81f88d8014fe3c68e6b206e86b574393e29ca5` contains no composer-prefill hook,
manifest field, or MCP response field.

OpenAI likewise documents **Suggested prompts** as a ChatGPT setting that uses
context-aware suggestions when a user starts or returns to ChatGPT. Current
Codex plugin, MCP, and hook documentation provides model context, tool results,
warnings, and continuation controls, but no supported field by which this
plugin can set gray composer text.

Manifest `interface.defaultPrompt` is retained for static starter prompts. It
does not change in response to lifecycle state and is not treated as the Exit
Slip channel.

Official references:

- [Claude Code interactive mode: Prompt suggestions](https://code.claude.com/docs/en/interactive-mode#prompt-suggestions)
- [OpenAI Settings: Suggested prompts](https://learn.chatgpt.com/docs/reference/settings#suggested-prompts)
- [OpenAI Hooks](https://learn.chatgpt.com/docs/hooks)
- [OpenAI Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp)

The OpenAI `Stop` hook is deliberately not used. A blocking `Stop` result
creates a new continuation prompt and keeps the agent running. That is not a
gray suggestion, and it would cross the human HIL boundary instead of stopping
and waiting.

## Portable Evidence Lane contract

Every newly sealed candidate carries an
`evidence-lane.next-action.v1` object in `exit_slip.json`. The same object is
returned by initial build, Refresh, and automatic task completion. It includes:

- the lifecycle state and `/evi-80-hil` command;
- a neutral `suggested_next_prompt` containing all six decisions without
  selecting one;
- candidate, proposed-PV, project, and session identity;
- `composer_authority: HOST_OWNED`;
- `documented_mcp_composer_mutation_supported: false`;
- `auto_submit: false` and `stop_and_wait: true`.

State Travel receipts use the same schema. Before opening a fresh task or chat,
the suggestion is `/evi-00-state-travel`. After Boot/Flash, accepted-pointer,
and seal verification, the visible suggestion asks for the next bounded task
or offers the read-only `/evi-40-status` command.

MCP instructions and the SessionStart hook require Codex or ChatGPT to render
the returned suggestion visibly before stopping. When the host independently
supports gray Suggested prompts, the host may surface an equivalent suggestion.
Evidence Lane never claims that host rendering as an MCP write and never
submits a decision for the user.

## Live HIL boundary

Code and artifact tests can prove the portable contract. Only a fresh installed
Codex task and a fresh connected ChatGPT chat can prove how a particular host
version renders its own suggestion UI. Absence of gray text does not invalidate
the sealed next action; it means the host used the visible Exit Slip/MCP
fallback. No rendering result implies candidate approval.
