# ChatGPT remote MCP deployment

Git distribution, durable MCP execution, and the Vercel adapter are three
separate boundaries.

## Accepted topology

```text
ChatGPT
  -> Vercel branch/preview adapter
      -> release-verified HTTPS durable MCP origin
          -> one writer + transactional local/durable state
```

The adapter lives in
`plugins/evidence-lane-plugin/remote_adapter`. It is ChatGPT-specific and may be
deployed to a branch preview. It is not the Evidence Lane engine, a general
router, a local-SQLite host, or an authority store.

The Vercel project serves a public multipage Next.js site at `/`,
`/architecture`, `/lanes`, `/proof`, `/provenance`, `/connect`, plus public
privacy, terms, and support pages. Exact rewrites carry only `/healthz`, `/mcp`, and
`/.well-known/oauth-protected-resource` into Python using the reserved
`__evi_path` query field. The adapter restores the public path, removes the
reserved field, and forwards only the caller's remaining query string. There is
no catch-all rewrite, so the website and MCP transport cannot silently replace
one another.

For every `/mcp` request it:

1. requires `EVIDENCE_LANE_DURABLE_MCP_ORIGIN` to be credential-free HTTPS;
2. requires `EVIDENCE_LANE_RELEASE_SHA` to be an exact 40-character Git SHA;
3. rejects a mismatching `VERCEL_GIT_COMMIT_SHA`;
4. verifies the origin `/healthz` reports the same release SHA;
5. forwards authorization and the request only after those checks;
6. stores no Evidence Lane state and returns fail-closed 5xx evidence when the
   origin or release identity is unavailable.

The durable origin runs the repository's Streamable HTTP MCP at `/mcp` with a
durable data root, one writer, and OAuth/JWT validation for ChatGPT. The Docker
deployment shape uses port 8080 and `/var/lib/evidence-lane`.

## Preview states

A branch preview may be deployed with no credentials to prove the landing page,
path recovery, and fail-closed behavior. In that state `/` renders the public
site and labels the ChatGPT edge fail-closed; `/healthz` and `/mcp` return a
bounded blocker. The website is **not** evidence of a working ChatGPT install.

A working ChatGPT preview is eligible only when all of these are configured:

- the exact implementation commit exists on the remote branch;
- Vercel CLI/project authentication is available without GUI control;
- the two adapter variables are configured for Preview;
- the durable origin is reachable and reports that exact commit;
- ChatGPT-compatible OAuth exists at the durable origin;
- the compromised OpenAI key has been revoked and is not in any environment;
- tests prove adapter fail-closed behavior and release identity.

If any condition is missing, record a manual HIL blocker and never describe the
fail-closed preview as a working ChatGPT connection. Never use Vercel as the
primary SQLite store or silently replace the durable origin with function-local
files.

From the adapter directory, an eligible branch preview can be created with the
normal Vercel CLI preview workflow. Verify the returned `/healthz` response
shows the expected release SHA and `local_state_authority: false`. Do not
promote to production in the implementation HIL.

## Codex installation remains Git-backed

Codex installs the exact Git snapshot through its marketplace route. Starting a
fresh task is required after update because an existing task retains its prior
plugin snapshot. Vercel is not involved in Codex installation.

## Evidence boundary

A Git push, marketplace install, durable-origin start, adapter preview, or
ChatGPT connection never approves a project-version candidate. Only exact
`APPROVE` through Fuse can move accepted truth. A later sealed handoff makes
State Travel eligible; only an explicit user request or genuine context
exhaustion authorizes its use in a genuinely fresh destination task or chat.
