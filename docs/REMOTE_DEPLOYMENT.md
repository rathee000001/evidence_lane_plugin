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
privacy, terms, and support pages. Exact rewrites carry only `/healthz`, `/mcp`,
`/.well-known/oauth-protected-resource/mcp`, and the root metadata discovery
compatibility route, plus the exact OpenAI domain-challenge route into Python
using the reserved `__evi_path` query field.
The adapter restores the public path, removes the reserved field, and forwards
only the caller's remaining query string. There is no catch-all rewrite, so the
website and MCP transport cannot silently replace one another.

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

The Docker context intentionally excludes `.git`. Build the durable-origin
image from the exact reviewed commit and pass that lowercase 40-character SHA
as a build argument:

```powershell
docker build --build-arg EVIDENCE_LANE_RELEASE_SHA=<exact-40-character-sha> -t evidence-lane:<exact-40-character-sha> .
```

The image build fails if the argument is missing or malformed and seals it as
a root-owned, read-only marker inside the installed Python package. Runtime
identity uses direct Git first, then verified Codex-marketplace bytes, and only
then this container marker. The marker is not accepted from the Docker build
context and is never read from a mutable runtime environment variable. The
durable `/healthz` response therefore reports the exact image source commit even
though `.git` is absent, allowing the Vercel adapter to enforce the same SHA.

## OAuth resource and application policy

The durable origin is an OAuth 2.1 resource server, not an authorization
server. Use an established IdP that publishes authorization-server or OIDC
discovery, supports authorization code plus PKCE `S256`, and identifies the
OpenAI client through CIMD, DCR, or one predefined client. Evidence Lane
validates asymmetric JWT signatures, exact issuer and audience, `exp`, `nbf`,
`jti`, subject, exact client ID, deployment environment, application role, and
project grants. The base HTTP middleware requires only
`evidence-lane:read`; each listed MCP tool also declares a top-level OAuth
`securitySchemes` entry and a compatibility mirror.

If a service-level tool invocation is missing a bearer token or lacks a
required scope, the MCP result fails without mutation and includes
`_meta["mcp/www_authenticate"]` with the exact public protected-resource
metadata URL and required scopes. Role, deployment-environment, project-grant,
and owner-only denials remain hard authorization failures without a retry
challenge because reauthentication cannot expand those policy grants.

`EVIDENCE_LANE_MCP_OAUTH_AUDIENCE` must exactly equal the same externally
visible `/mcp` resource URL. Server construction fails if the configured JWT
audience and protected-resource identity differ.

Requiring `jti` gives the IdP and resource logs an exact token identifier; it
does not make a normal access token single-use. Short expiry, IdP revocation or
introspection where supported, key rotation, and incident response remain
deployment responsibilities and require live staging proof.

Set the durable origin's `EVIDENCE_LANE_MCP_BASE_URL` to the public resource
origin, such as `https://mcp.evidencelane.org`. Its protected-resource metadata
and `WWW-Authenticate` challenge must therefore identify the exact public
resource `https://mcp.evidencelane.org/mcp` and the path-specific discovery URL
`https://mcp.evidencelane.org/.well-known/oauth-protected-resource/mcp`. The
edge accepts the root compatibility route too, but normalizes it to that same
path-specific origin metadata; it never substitutes a private origin identity.

When the portal generates domain verification, place its one exact value only
in the server-side `EVIDENCE_LANE_OPENAI_APPS_CHALLENGE_TOKEN` variable. The
public `/.well-known/openai-apps-challenge` route returns that value alone as
plain text, exactly as the submission contract requires. It returns an empty
`404` if the value is absent or malformed. Never commit the token or return
JSON, multiple tokens, or a placeholder.

Executable read tools require `evidence-lane:read` and an exact project grant
when they are project-scoped. Full-lifecycle write tools require
`evidence-lane:write`. `remote_git_prepare_push` and
`remote_git_execute_push` additionally require `evidence-lane:remote-git` and
the `owner` role. All production lifecycle writes require the `owner` role.
`tester` identities can write only on a separately configured staging origin;
they receive exact staging project grants and never production secrets or a
production wildcard. Only an owner token may carry the `*` project grant.

Run staging and production with separate HTTPS origins, issuer/audience
configuration, allowed client IDs, data roots, credentials, and logs. The
required durable-origin settings are documented in `.env.example`; client IDs
and claims are identifiers and policy inputs, not substitutes for OAuth
secrets. The Vercel adapter never stores or decides these grants. The current
ChatGPT governed exposure profile still refuses all lifecycle actions before
service invocation, so its per-tool schemes request only the read scope. This
resource-server implementation does not prove a live IdP, a public durable
origin, reviewer login, revocation, or end-to-end ChatGPT linking; those remain
external staging and HIL gates.

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

A preview proves only the exact commit supplied at deployment. Any later
tracked-source correction supersedes that preview for final-release evidence,
even if the visible pages are unchanged. Rerun the source gate and deploy a new
preview bound to the corrected 40-character commit; never relabel the older
deployment as the final candidate preview.

## Codex installation remains Git-backed

Codex installs the exact Git snapshot through its marketplace route. Starting a
fresh task is required after update because an existing task retains its prior
plugin snapshot. Vercel is not involved in Codex installation.

## Evidence boundary

A Git push, marketplace install, durable-origin start, adapter preview, or
ChatGPT connection never approves a project-version candidate. Only exact
`APPROVE` through Fuse can move accepted truth. State Travel may preserve either
exact unfinished verified work or an explicitly requested accepted entry; it
requires a sealed handoff, and only an explicit user request or genuine context
exhaustion authorizes its use in a genuinely fresh destination task or chat.
