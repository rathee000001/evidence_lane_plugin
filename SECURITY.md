<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Security boundary

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


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

The current pre-HIL 3.0.0 Codex source line uses the package-local native MCP route.
This source label is not an installed-host claim. Selector names never
override exact package and runtime readback, and a local test installation is
not an accepted release. Normal Codex attachment does not depend on the public
website, a Vercel adapter, a ChatGPT plugin, or a remote OAuth service. Vercel
hosts public documentation only and receives no project truth, lifecycle
authority, candidate state, or accepted pointer.

The versioned Windows tunnel is a separate transport channel. Its installer
accepts the user's own Runtime key only through a masked prompt, stores only a
current-user DPAPI envelope, and binds the scheduled task to the pinned client
hash and configured tunnel ID. A live tunnel is not proof that the package-local
Codex MCP server, exact project/session, or accepted pointer is valid. Local
Codex and local CLI profiles may require the tunnel when the detected host route
lacks direct MCP transport or required host tools. Headless API requests do not require the tunnel
merely because they use API billing.

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
