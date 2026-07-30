# Google Drive persistence contract

## Routing

Routing follows the MCP server's actual filesystem capability, not the name of
its UI host.

- A durable local server—whether reached from Codex or ChatGPT desktop—uses the
  user-owned local store as authority.
- An explicitly ephemeral server, Codex VM without durable storage, or public
  remote runtime fails closed unless the direct server-side Google Drive
  persistence backend is configured.
- Google Drive is also a required plugin app dependency. Codex/ChatGPT prompts
  the user through the normal Google OAuth connector flow during installation
  or first use. That host connection is suitable for a bounded verified mirror;
  its OAuth token is never exposed to the Python MCP.

`runtime_doctor.google_drive` reports the connector dependency and direct
server backend separately. The legacy `google_drive_configured` field means
only the direct backend is configured.

## Allowed objects

Only these categories are accepted:

- immutable accepted PV archives;
- preserved candidate PV archives;
- bounded HIL or remote-action receipts;
- immutable, generation-addressed accepted-pointer snapshots.

Raw repository clones, dependency trees, caches, browser profiles, build
directories, credentials, private-engine source, and uncontrolled logs are
forbidden.

## Two distinct OAuth boundaries

### Host Google Drive connector

The plugin root `.app.json` requires:

```text
connector_5f3c8c41a1e54ad7a76272c89e2554fa
```

The host owns sign-in, consent, refresh tokens, and connector calls. `/evi`
performs a read-only connector check. Connector availability does not prove a
PV mirror was uploaded or read back.

### Direct server-side persistence

An ephemeral MCP server cannot rely on a model-mediated connector call as its
atomic persistence backend. The existing adapter therefore requires an
in-memory user OAuth access token, one explicit parent folder ID, and—for
private data—an encryption key. These are provided to the server environment,
never in a prompt or tool payload. The token is neither serialized nor
returned. The adapter creates this governed subtree:

```text
EvidenceLanePV/
`-- <project-id>/
    |-- accepted/
    |-- candidates/
    `-- receipts/
```

The implementation uses object SHA-256 app properties to reject conflicting
bytes at an existing governed path. Pointer state is never overwritten in
place: each move stores
`active-pointer-gen-<generation>-<accepted-pv>.json`, preserving rollback
history and making the highest generation independently verifiable.

## Encryption

`PRIVATE`, `RESTRICTED`, or `CONFIDENTIAL` project PVs fail closed without an
explicit user-owned AES-256-GCM key. The key must decode from URL-safe base64 to
exactly 32 bytes. Nonces are random; authenticated associated data binds the
package metadata.

## Current HIL state

The host connector dependency and normal OAuth onboarding are packaged. The
direct adapter, encryption envelope, routing rules, accepted-PV sealing,
decision receipts, pointer snapshots, and fake-backend tests also exist.

These are separate proofs:

1. connector connected and callable;
2. a specific governed Drive folder selected;
3. a sealed object uploaded;
4. bytes downloaded and hash-verified;
5. a direct ephemeral-server backend configured, when required.

Never collapse one proof into another or infer HIL approval from any of them.
