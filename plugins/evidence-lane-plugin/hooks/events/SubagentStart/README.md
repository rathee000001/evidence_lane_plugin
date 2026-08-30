# Hook 2: SubagentStart

This event has 4 ordered, separately hash-bound handlers. The event adapter and shared stage pipeline remain the single executable implementation. Its exact host timing, lifecycle consumer, skill action, workflow phases, and public-action boundary are bound by `workflow_contract` in `event.v1.json`.
