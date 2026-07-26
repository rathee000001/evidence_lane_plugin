# Google Drive persistence contract

## Routing

Durable local Codex hosts use the user-owned local store. ChatGPT, public AI,
Codex VM, and explicitly ephemeral sessions fail closed unless Google Drive
persistence is configured.

## Allowed objects

Only these categories are accepted:

- immutable accepted PV archives;
- preserved candidate PV archives;
- bounded HIL or remote-action receipts.

Raw repository clones, dependency trees, caches, browser profiles, build
directories, credentials, private-engine source, and uncontrolled logs are
forbidden.

## User authority

The adapter requires an in-memory user OAuth access token and one explicit
parent folder ID. The token is neither serialized nor returned. The adapter
creates this governed subtree beneath the selected folder:

```text
EvidenceLanePV/
└── <project-id>/
    ├── accepted/
    ├── candidates/
    └── receipts/
```

The implementation uses object SHA-256 app properties to reject conflicting
bytes at an existing governed path.

## Encryption

`PRIVATE`, `RESTRICTED`, or `CONFIDENTIAL` project PVs fail closed without an
explicit user-owned AES-256-GCM key. The key must decode from URL-safe base64 to
exactly 32 bytes. Nonces are random; authenticated associated data binds the
package metadata.

## Current HIL state

The adapter, encryption envelope, routing rules, and fake-backend tests exist.
No user token, folder, public endpoint, or live Drive write is configured by
this repository. A live connector proof remains a separate user-authorized HIL.
