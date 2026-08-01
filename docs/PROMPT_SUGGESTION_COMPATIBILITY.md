# Prompt suggestion compatibility

Codex and ChatGPT own their composer UI. Evidence Lane returns portable
`next_action_contract` objects and a visible `suggested_next_prompt`; it does
not claim to write, persist, or auto-submit the prompt bar.

The root suggestion order is Boot, Rollback, Build, Refresh, Mode, and Source
Intake. State Travel appears first only for a prepared accepted-PV handoff.
After candidate construction the suggested control is `/evi-build` with all six
HIL choices. After verified State Travel the suggestion asks for the next
bounded task or a governed status inspection through Build.

Hosts may render these suggestions differently or not at all. The assistant
must still display the exact returned suggestion before a HIL or State Travel
stop. Composer text never constitutes HIL authority.
