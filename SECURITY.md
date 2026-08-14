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

## Codex, headless API, tunnel, and remote Git boundaries

The current pre-HIL 2.2.0 Codex source release uses the package-local native MCP route.
This source label is not an installed-host claim.
The disabled accepted PV11 fallback remains exact 2.0.0. Normal
Codex attachment does not depend on the public website, a Vercel adapter, a
ChatGPT plugin, or a remote OAuth service. Vercel hosts public documentation
only and receives no project truth, lifecycle authority, candidate state, or
accepted pointer.

The versioned Windows tunnel is a separate transport channel. Its installer
accepts the user's own Runtime key only through a masked prompt, stores only a
current-user DPAPI envelope, and binds the scheduled task to the pinned client
hash and configured tunnel ID. A live tunnel is not proof that the package-local
Codex MCP server, exact project/session, or accepted pointer is valid. Headless
API and direct CLI/API profiles do not require this tunnel.

An explicitly deployed headless/API Streamable HTTP service may use either one
private static bearer or an established OAuth 2.1 IdP, never both. OAuth JWTs
use an allowlisted asymmetric algorithm and bind issuer, audience, expiry,
not-before time, token ID, subject, exact client ID, deployment environment,
role, and project grants. Tool execution enforces read/write scopes and exact
project authorization. The configured audience must equal the externally
visible HTTPS `/mcp` resource in protected-resource metadata; a private origin,
parent site URL, or different audience fails closed. The required JWT `jti` is
an auditable identifier, not a claim that access tokens are one-use.

This optional service is not the post-HIL tester-entitlement or GitHub App
distribution design. A login, client ID, role, token, form, or license does not
imply repository access, plugin installation, project authority, lifecycle
writes, Git access, deployment, publication, candidate acceptance, pointer
movement, or HIL approval.

For the explicitly configured non-default test branch, an exact prepared
fast-forward push may use the project's standing authorization. Every push
receipt still binds the project, branch, commit, tree, remote, and action ID.
That policy never authorizes force-push, default/protected-branch mutation,
merge to `main`, publication, deployment, candidate acceptance, pointer
movement, or Fuse; each remains a separate governed action.

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
