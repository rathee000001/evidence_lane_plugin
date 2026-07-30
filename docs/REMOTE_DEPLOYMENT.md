# Personal HIL and future remote deployment contract

Git distribution and MCP execution are different boundaries.

- The private GitHub repository is the reviewed plugin and container source.
- Codex installs a versioned marketplace snapshot from that Git repository into
  its normal local cache. The cache is executable material, not source
  authority.
- ChatGPT never executes the Git repository. It must reach a running MCP
  process either through Secure MCP Tunnel in private developer mode or through
  a stable public HTTPS Streamable HTTP endpoint.

## Current private single-user candidate HIL

The selected HIL topology keeps one durable local runtime:

1. create one candidate branch from the reviewed base and commit only on that
   branch, never directly on `main`;
2. after the user's explicit candidate-distribution instruction, push only the
   exact feature branch commit without force;
3. install Codex from that exact Git commit and verify a fresh-task bootstrap;
4. run the same local Git build as the private MCP and attach OpenAI Secure MCP
   Tunnel;
5. create the personal ChatGPT plugin by selecting **Tunnel**, not by entering
   the Git URL or a Vercel URL;
6. verify tunnel health, MCP initialization, tool schemas, local durable-state
   reuse, and a fresh ChatGPT Work chat;
7. stop at the universal HIL before Fuse, pointer movement, `main` merge, or
   public release.

Candidate branch publication is installation evidence only. It does not accept
a PV or infer HIL success. Codex and ChatGPT both reach the same local engine;
the tunnel changes transport, not authority.

## Future public release topology

A public release requires a stable public HTTPS `/mcp` runtime. Vercel is one
possible future host, not a package installer and not part of the current
personal HIL. If selected later, the accepted topology is:

1. build a Vercel preview from an exact accepted branch;
2. verify endpoint, OAuth, external durability, tools, and logs;
3. promote that exact preview artifact to the stable production URL without a
   rebuild;
4. connect ChatGPT to the production `/mcp` URL and complete a separate public
   deployment HIL.

## Codex Git marketplace

Pin an exact reviewed ref or commit:

```text
codex plugin marketplace add rathee000001/evidence_lane_plugin --ref REVIEWED_REF
codex plugin add evidence-lane-plugin@evidence-lane-github --json
```

The plugin's first local MCP start builds a hash-locked `.venv` inside that
versioned cache. The plugin-local `pyproject.toml` makes the snapshot
self-contained; it does not import the F-drive checkout. Starting a new task is
still required after install or update because an existing task retains its
original plugin snapshot.

## Production MCP shape

The included OCI `Dockerfile` runs one long-lived Streamable HTTP service:

- public path: `/mcp`;
- unauthenticated liveness path: `/healthz`;
- container port: `8080`;
- durable volume: `/var/lib/evidence-lane`;
- one replica and one writer;
- HTTPS termination at the hosting ingress;
- no scale-to-zero or ephemeral serverless filesystem.

Build the reviewed Git checkout with:

```text
docker build --pull --tag evidence-lane-plugin:0.5.2 .
```

The host must preserve one replica and attach a durable volume at
`/var/lib/evidence-lane`; publishing the image without those two settings does
not satisfy the persistence contract.

### Vercel compatibility gate

Vercel can run Python ASGI and expose the required public HTTPS route, and its
Git integration can create a branch preview that is later promoted without a
rebuild. Its function instances, however, are dynamically scheduled and local
instance memory/filesystem cannot be the Evidence Lane authority. The current
SQLite-first service therefore must not be deployed to Vercel as if
`EVIDENCE_LANE_DATA_ROOT` were a durable mounted volume.

Before a Vercel deployment is valid, the queued governed storage work must
provide an external authoritative adapter for all mutable runtime state,
including session binding, backlog, ChatLineage head, prompt index, candidates,
accepted PVs, receipts, and generation-CAS pointer state. Blob/object storage
alone is insufficient for transactional pointer and single-writer law; the
adapter needs a transactional database or equivalent CAS authority plus
immutable object storage. Cold-start, concurrent-invocation, retry,
idempotency, and deployment-replacement tests must pass.

The account being single-user reduces traffic but does not remove cold starts,
instance replacement, concurrent retries, OAuth, duration limits, or durable
state requirements. Until those gates pass, Vercel is a future public target,
not an installer, demonstrated runtime, or superior path for this personal
HIL.

Required environment:

```text
EVIDENCE_LANE_MCP_BASE_URL=https://HOST
EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL=https://ISSUER/
EVIDENCE_LANE_MCP_OAUTH_JWKS_URL=https://ISSUER/.well-known/jwks.json
EVIDENCE_LANE_MCP_OAUTH_AUDIENCE=https://HOST
EVIDENCE_LANE_MCP_OAUTH_SCOPES=evidence-lane:read evidence-lane:write
EVIDENCE_LANE_MCP_OAUTH_ALGORITHMS=RS256
EVIDENCE_LANE_DATA_ROOT=/var/lib/evidence-lane
```

The OAuth issuer must be an established provider configured for the MCP
authorization contract, including discovery metadata and CIMD, DCR, or the
predefined client configured in ChatGPT. Evidence Lane validates JWT signature,
issuer, audience, expiry, subject, asymmetric algorithm, and required scopes.
It does not implement an authorization server.

`EVIDENCE_LANE_MCP_BEARER_TOKEN` remains a Codex-only private-client fallback.
ChatGPT cannot present a custom static API key, so a bearer-only deployment is
not ChatGPT-ready.

## ChatGPT public developer connection

After the accepted branch's production Vercel deployment is verified:

1. Open Plugins and select the plus button.
2. Name: `Evidence Lane Plugin`.
3. Description: `Persistent /evi-first Evidence Lane with fresh-window State Travel, automatic Refresh, exact-APPROVE Fuse, HIL, and rollback.`
4. Connection: **Server URL**.
5. URL: `https://HOST/mcp`.
6. Authentication: **OAuth**.
7. Review discovered tools and create the connection.

The final risk acknowledgement and Create action require the user's explicit
installation authorization. Secure MCP Tunnel is the selected personal
developer-HIL route; it depends on the local MCP and tunnel client remaining
healthy. It does not satisfy a later public submission. A Git repository URL
and a Vercel preview URL that has not passed OAuth/durability verification are
invalid Server URL inputs.

The Google Drive connector is a separate user-owned data connection. Its OAuth
token is not the MCP server's login credential and is not exposed to the
Evidence Lane process.

## Required proof before parity

Do not claim ChatGPT parity until the selected transport, all canonical tools,
write confirmations, persistence, new-chat resume, and accepted-PV handoff are
observed end to end. For the personal route, this includes tunnel health and
local-store durability; for a public route, it includes the public URL, OAuth
flow, and external durable readback. Deployment or installation never approves
a candidate PV or moves the accepted pointer.
