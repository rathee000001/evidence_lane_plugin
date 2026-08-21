# Google Drive persistence contract

Routing follows the MCP server's storage capability, not the UI brand.

- A durable local server uses the user-owned local SQLite store as primary
  authority.
- An ephemeral server requires a configured transactional durable runtime
  connector and fails closed without it.
- Google Drive is an optional verified mirror/fallback. It is never primary
  when durable local storage exists and the current object adapter is not a
  transactional replacement for the complete runtime state.

The host Google Drive app connection and the server-side object adapter are
separate OAuth boundaries. Host connector tokens never enter MCP payloads.
Server-side tokens exist in memory only and must come from an approved secret
store.

## Allowed mirror objects

- immutable accepted-PV archives;
- preserved unaccepted-candidate archives;
- bounded HIL and remote-action receipts;
- immutable generation-addressed pointer snapshots.

Repository clones, dependency caches, credentials, hidden model reasoning,
browser profiles, uncontrolled logs, and mutable function-local state are
forbidden.

Private or restricted archives require the existing AES-256-GCM envelope. The
object SHA-256 metadata rejects conflicting bytes at a governed path. Pointer
snapshots are generation-addressed and never overwritten in place.

These proofs must remain distinct: connector connected, governed folder
selected, object uploaded, bytes read back and hash verified, and complete
transactional runtime durability. None implies candidate approval or HIL
success.
