# Evidence Lane SDK

The public binding in `evidence_lane_sdk.py` exports the canonical protocol-v4 request, response, evidence and client types, plus the local and remote transports. `sdk-manifest.v4.json` identifies the current per-action bindings. Each binding points to its typed schema; SDK, MCP and packaged skill references derive from the engine action registry.

`EvidenceLaneClient.call` sends an action with its project, request ID, optional expected Plan revision and typed arguments through an already connected transport. The engine derives the authenticated client and checks project grants, locked ENV/UOP policy and the exact operation contract. Project work additionally requires its current Plan-bound Delta. The outer SDK contains no separate business executor.

Responses preserve the action and request ID and distinguish success, error and addressable queued work. A transport failure does not trigger automatic mutation retries. Reuse an explicit request ID only through the owning operation's documented status or reconciliation behavior; do not assume a timeout means the work stopped. Checkpoints and cancellation belong to the engine's job and steer workflows, which must confirm their actual outcomes.

Evidence stays attributed to its owning project and separate lane database/files. Engine-client identity, successful protocol calls and stored evidence do not establish installed-native task identity. Boot/Resume and locked Flash remain supported; Formula, accepted-PV Fuse/HIL and PV rollback are not SDK operations.
