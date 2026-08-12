# Prompt suggestion compatibility

Codex owns its composer UI. Evidence Lane returns portable
`next_action_contract` objects and a visible `suggested_next_prompt`; it does
not claim to write, persist, or auto-submit the prompt bar.

The root suggestion order is Boot, Rollback, Build, Refresh, Mode, and Source
Intake. A prepared exact-work handoff does not change that order by itself.
State Travel appears only after an explicit user request or genuine host-context
exhaustion and a matching sealed handoff. Unfinished-work travel suggests exact
continuation at the preserved Plan Lane row; explicit accepted entry waits.
If the user explicitly continues in the unchanged host after Fuse, the host may
route the exact same-host continuation contract instead; it preserves and
supersedes the handoff receipt without consuming it or moving the pointer.
After candidate construction the suggested control is `/evi-build` with all six
HIL choices. After verified accepted-entry State Travel the suggestion asks for
the next bounded task or a governed status inspection through Build.

Codex native Plan mode is host-owned. `/evi-plan` must return a `/pl` reminder
when Plan mode is not active. When planning finishes, it returns a short
`goal_start_prompt` that the user copies into the Codex Goal; MCP cannot change
Plan mode, the Goal, or model selectors.

Hosts may render these suggestions differently or not at all. The assistant
must still display the exact returned suggestion before a HIL or State Travel
stop. Composer text never constitutes HIL authority.
