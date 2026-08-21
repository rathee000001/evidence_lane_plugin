# Evidence Lane plugin 1.5.0

Evidence Lane is one governed plugin package for Codex and ChatGPT. It carries
15 packaged skills, the six primary controls—Boot, Rollback, Build, Refresh,
Mode, and Source Intake—and a host-specific MCP connection without pretending
the two hosts share a filesystem or write authority.

## Host boundary

- Codex runs the package-local stdio server declared by `.mcp.json` and may use
  the full lifecycle only inside an explicitly governed project session.
- ChatGPT connects to the public HTTPS MCP endpoint only after its health,
  OAuth 2.1 metadata, durable-state origin, and release identity all pass. The
  ChatGPT profile exposes the same 15 skills and complete action catalog, but
  its lifecycle-write actions fail closed before service invocation.
- A successful website render is not MCP proof. A blocked `/healthz`, missing
  OAuth metadata, or unavailable durable origin blocks ChatGPT connection and
  does not authorize a tunnel, deployment, or local-state fallback.

Candidate creation, installation, deployment, publication, pointer movement,
and Fuse remain separate governed actions. Natural-language approval intent is
never sufficient to move accepted truth; Fuse requires the exact pending HIL
contract and case-sensitive decision on a write-capable host.

## Package map

- `.codex-plugin/plugin.json` — product and host presentation metadata
- `.mcp.json` — package-local Codex stdio registration
- `skills/` — the 15 governed skill contracts
- `src/evidence_lane_plugin/` — runtime, storage, lifecycle, and MCP source
- `remote_adapter/` — public website and ChatGPT HTTPS adapter source
- `chatgpt-app-submission.json` — review-form metadata and 62-action annotation
  contract; it is a draft artifact, not proof of submission or approval

Public product, privacy, terms, security, support, and connection information is
available at [evidencelane.org](https://evidencelane.org). Package rights and
dependency obligations are recorded in [LICENSE.md](LICENSE.md),
[COPYRIGHT.md](COPYRIGHT.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Current release boundary

This package is internal release evidence. Local tests can prove deterministic
packaging, source boundaries, metadata consistency, database integrity, and
expected fail-closed behavior. They do not prove production deployment,
marketplace acceptance, unaided external-user success, customer value, or HIL
acceptance.
