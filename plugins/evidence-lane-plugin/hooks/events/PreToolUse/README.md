# PreToolUse

This directory owns the packaged Evidence Lane handler for the documented
`PreToolUse` event. The handler validates and redacts the event's visible fields,
verifies the selected v4 engine build, submits one event to the explicitly bound
project session, verifies the returned event identity, and returns no control output.

The handler never selects a project from a title or path, starts lifecycle work,
controls a subagent, changes a Goal, retries an uncertain delivery, or reads a
host transcript. `event.schema.json` describes admitted input and
`pipeline.v4.json` binds every stage to the shared executable implementation.
