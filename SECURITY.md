# Security boundary

Report security issues privately to the repository owner. Never open a public
issue containing credentials, private source, PV packages, access tokens, or
private-engine material.

Project authorization, lifecycle transition authority, plugin installation,
candidate approval, connector registration, and remote Git writes are separate
gates. No one gate implies another.

Secrets must arrive through the host environment or an approved secret manager.
They are redacted from visible errors and prohibited from:

- plugin, MCP, Vercel, or lane manifests;
- SQLite brains, PV packages, project overlays, and receipts;
- Chat Lineage, including token telemetry and tool payloads;
- Google Drive metadata, Git remote receipts, screenshots, and ordinary logs.

Git source intake enumerates tracked index entries only. A shared pre-index
policy excludes `.env` variants, runtime/cache/build paths, credential and
private-key files, exact configured secret values, and recognized token or
assigned-secret content before any exact bytes reach SQLite, FTS, CAS, Git
history, topology, or a candidate package. Non-Git intake uses the same
deterministic path/content exclusions. Exclusion receipts record safe reason
codes and paths but never return secret values or secret environment names.

Hidden chain-of-thought, private model reasoning, and reasoning-content fields
are also prohibited from Chat Lineage and project-sector overlays.

## Compromised OpenAI key release blocker

An OpenAI API key pasted in the July 31 source conversation is compromised.
The repository deliberately contains neither that key nor a hash or excerpt of
it. No connected OpenAI Platform revocation capability is available to this
implementation runtime, so revocation is a hard manual release blocker:

1. revoke the exposed key in the OpenAI Platform;
2. create a replacement only if still needed;
3. place the replacement solely in an approved secret store;
4. verify repository, candidate packages, deployment configuration, and logs do
   not contain the old or replacement value.

Do not deploy any service that may still use the compromised key.

## Remote boundaries

Remote Git push stays disabled until a separate exact action request and one-use
confirmation are recorded. Vercel may host only the thin ChatGPT adapter. The
adapter requires HTTPS, an exact 40-character release SHA, a release-matched
durable MCP origin, and configured authentication at that origin. It stores no
runtime authority and fails closed if identity or origin health does not match.

The durable MCP origin accepts either one private static bearer for a bounded
Codex-only route or an established OAuth 2.1 IdP, never both. OAuth JWTs use an
allowlisted asymmetric algorithm and must bind issuer, audience, expiry,
not-before time, token ID, subject, exact client ID, deployment environment,
role, and project grants. Tool execution enforces read/write scopes and exact
project authorization. Production lifecycle writes are owner-only; remote Git
also requires its dedicated scope and owner role. Tester identities are valid
only against an isolated staging environment. A client ID, role, or successful
login does not imply project, lifecycle, Git, deployment, publication, or HIL
authority.

The configured OAuth audience must exactly equal the externally visible HTTPS
`/mcp` resource in protected-resource metadata. A private origin URL, parent
site URL, or different audience fails server construction rather than creating
a split identity.

The required JWT `jti` is an auditable token identifier, not a claim that access
tokens are one-use. Live deployments still require bounded expiry, issuer-side
revocation or introspection where available, signing-key rotation, and tested
credential-compromise response.

## Private CodeQL evidence

The personal canonical repository keeps CodeQL results as a private Actions
artifact and does not request hosted code-scanning upload. This preserves local
analysis evidence when GitHub Code Security is unavailable to that repository.

An optional self-only private organization mirror may run
`.github/workflows/evidence-lane-codeql-hosted.yml`. That workflow is manual,
requires `security-events: write` only inside its analysis job, and refuses to
run unless `github.repository_owner` is exactly `Evidence-Lane`. The mirror
must contain the exact reviewed release SHA and have organization Code Security
enabled. A mirror scan is security evidence; it is not source authority, a PV,
or HIL approval.
