# ChatGPT edge and public site

Reviewed public website preview:
[`https://evidencelane.org`](https://evidencelane.org)

This Vercel project has two deliberately separate surfaces:

- a public multipage Next.js site at `/`, `/architecture`, `/lanes`, `/proof`,
  `/provenance`, `/connect`, plus `/privacy`, `/terms`, and `/support`;
- a thin ChatGPT-facing reverse adapter at `/mcp`, `/healthz`, and
  `/.well-known/oauth-protected-resource`.

The public site also exposes `/api/studio-query` for the full and floating
Prompt Studio. It first queries the same committed SQLite-derived BM25,
TF-IDF, and RRF projection used by the Studio page. Evidence Lane/project
no-hits refuse. A non-project, general-question no-hit may call OpenRouter only
when both of these variables are set:

- `EVIDENCE_LANE_GENERAL_AI_ENABLED=true`
- `OPENROUTER_API_KEY=<server-side key>`

That route hard-codes `openrouter/free`, sends no project context or chat
history, rejects credential-shaped input, and has no paid-model fallback. The
key remains server-side. When the owner elects to reuse the existing Gold
Nexus Alpha credential, keep one team-level Vercel Shared Sensitive Environment
Variable named `OPENROUTER_API_KEY` and link that same variable to this project
for the intended Preview/Production environments. Do not copy, pull, print,
log, or commit its value. Linking or changing that remote secret remains an
owner-only deployment action and is not performed by local tests.

Run `pnpm test:studio-query` for the zero-network provider contract. It injects
a dummy credential and a fake transport, then proves the fixed free model,
two-message general-only payload, no project context or chat history, no secret
in any result, and fail-closed provider, connection, and timeout behavior.

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
stay with Next.js, including `/api/studio-query`. A successful landing-page or
Studio render proves only the public site; it does not prove MCP authentication,
durable storage, queueing, tool calls, or external-provider availability.
