# SubagentStart

This directory owns the packaged Evidence Lane handler for the documented
`SubagentStart` event. Codex displays four separate Host Hook rows for validate, seal,
transport and emit. Those rows run the current admission, classification,
event handling, authenticated transport, receipt sealing and bounded-output
owners through dependency-safe stage receipts, and the final row returns no control output.

The handler never selects a project from a title or path, starts lifecycle work,
controls a subagent, changes a Goal, retries an uncertain delivery, or reads a
host transcript. `event.schema.json` describes admitted input and
`pipeline.v4.json` and the numbered stage contracts bind every step to its
executable owner. Event isolation prevents stage reuse or automatic replay.
