# ChatGPT edge and public site

This Vercel project has two deliberately separate surfaces:

- a public Next.js site at `/`, plus `/privacy`, `/terms`, and `/support`;
- a thin ChatGPT-facing reverse adapter at `/mcp`, `/healthz`, and
  `/.well-known/oauth-protected-resource`.

It stores no Evidence Lane state and is not the general router. Every `/mcp`
request is forwarded to one HTTPS durable MCP origin after `/healthz` proves
the origin's `release_sha` equals both `EVIDENCE_LANE_RELEASE_SHA` and, when
present, `VERCEL_GIT_COMMIT_SHA`.

Required preview variables:

- `EVIDENCE_LANE_DURABLE_MCP_ORIGIN`
- `EVIDENCE_LANE_RELEASE_SHA`

The incoming OAuth bearer is passed through to the durable origin, which owns
authentication, one-writer SQLite state, backlog, ChatLineage, queueing,
candidates, receipts, and pointer CAS. A missing origin, non-HTTPS origin, or
SHA mismatch returns `503 BLOCKED`. Vercel local files and memory are never
treated as durable state.

`vercel.json` rewrites only the three adapter routes into Python and preserves
their public path in the reserved `__evi_path` query value. All other routes
stay with Next.js. A successful landing-page render proves only the public site;
it does not prove MCP authentication, durable storage, queueing, or tool calls.
