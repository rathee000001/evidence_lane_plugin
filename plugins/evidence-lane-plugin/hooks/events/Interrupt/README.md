# Interrupt

This directory owns the packaged Evidence Lane handler for the documented
`Interrupt` event. Its fixed handler class runs the distinct admission,
classification, event handling, authenticated transport, receipt sealing and
bounded-output owners, and returns no control output.

The handler never selects a project from a title or path, starts lifecycle work,
controls a subagent, changes a Goal, retries an uncertain delivery, or reads a
host transcript. `event.schema.json` describes admitted input and
`pipeline.v4.json` and the numbered stage contracts bind every step to its
executable owner. Event isolation prevents stage reuse or automatic replay.
