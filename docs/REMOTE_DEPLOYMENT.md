# Remote-source deployment contract

Git distribution and MCP execution are different boundaries.

- The private GitHub repository is the reviewed plugin and container source.
- Codex installs a versioned marketplace snapshot from that Git repository into
  its normal local cache. The cache is executable material, not source
  authority.
- ChatGPT never executes the Git repository. It connects to a running public
  HTTPS Streamable HTTP server at `https://HOST/mcp`.

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
docker build --pull --tag evidence-lane-plugin:0.4.0 .
```

The host must preserve one replica and attach a durable volume at
`/var/lib/evidence-lane`; publishing the image without those two settings does
not satisfy the persistence contract.

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

## ChatGPT developer connection

After the container is deployed and verified:

1. Open Plugins and select the plus button.
2. Name: `Evidence Lane Plugin`.
3. Description: `Persistent /EV-first Evidence Lane with Refresh, Fuse, HIL, and rollback.`
4. Connection: **Server URL**.
5. URL: `https://HOST/mcp`.
6. Authentication: **OAuth**.
7. Review discovered tools and create the connection.

The final risk acknowledgement and Create action are human-owned. A Secure MCP
Tunnel is useful only for local development; it still depends on a running PC
and is not the requested production result.

The Google Drive connector is a separate user-owned data connection. Its OAuth
token is not the MCP server's login credential and is not exposed to the
Evidence Lane process.

## Required proof before parity

Do not claim ChatGPT parity until the public URL, OAuth flow, all canonical
tools, write confirmations, one-replica persistence, new-chat resume, and
accepted-PV handoff are observed end to end. Deployment or installation never
approves a candidate PV or moves the accepted pointer.
