# ChatGPT write-safe release and data-liability research

Research date: 2026-08-09
Scope: current official OpenAI, Vercel, GitHub, and regulator documentation plus the current Evidence Lane v1.5.0 candidate source tree
Change boundary: research plus local OAuth resource-server hardening; no ChatGPT write tool was enabled, no external permission changed, and nothing was promoted, published, or submitted

## Verdict

**FIX-THEN-PURSUE — high confidence (0.93).**

Evidence Lane can expose authenticated write tools in ChatGPT and can follow the normal public plugin route. OpenAI explicitly documents plugin write actions, OAuth 2.1 authorization, per-tool scope enforcement, host confirmation for destructive actions, review, and a universal Plugins Directory shared by ChatGPT and Codex. GitHub and Vercel likewise permit authorized writes; being a developer tool is not an exemption from authorization, least privilege, confirmation, auditability, or platform review.

The current package is not ready for that release. Its ChatGPT profile intentionally refuses lifecycle writes. The v1.5.0 candidate now enforces a read-only base transport gate, emits per-tool OAuth security schemes, and binds verified tokens to exact client, environment, role, and project claims; production lifecycle writes and remote Git are owner-gated. That is local resource-server proof, not a live OAuth deployment. A real registered read-safe Evidence Lane connection is now mapped in the package, but its underlying endpoint has not been proven against the candidate Git SHA. The public edge still has no working durable origin or established IdP proof; the portal has not scanned a production endpoint or verified the MCP domain; and the public privacy page does not yet disclose a complete data map, retention/deletion periods, processors, rights path, or incident contact. These are fixable release blockers, not reasons to abandon the normal route.

The claim that no vendor-held payload persistence yields “nearly zero data liability” is false. Local-first and short retention reduce the amount and duration of retained data. They do not remove processing, transmission, access-control, security, breach-response, deletion, transparency, lawful-basis, subprocessor, or contractual obligations. OpenAI’s current [App Developer Terms](https://openai.com/policies/developer-apps-terms/) expressly place responsibility for the app, API, app requests, privacy, security, legal compliance, support, and notices on the developer and treat OpenAI and the developer as separate parties for their respective processing.

Evidence that would change this verdict:

- **To pursue:** passing production-like OAuth discovery and tool-call tests against the implemented policy; complete retention/deletion and incident-response proof; an accurate public privacy notice and terms reviewed by qualified counsel; a stable public HTTPS MCP origin; clean Scan Tools results; reviewer-safe fixtures; and OpenAI review approval.
- **To park or drop:** OpenAI rejecting the lifecycle/HIL interaction model after a complete compliant submission; inability to operate a durable origin without exposing cross-tenant state; inability to implement reliable deletion/export and breach response; or counsel finding that the intended data categories or jurisdictions create obligations the project cannot support.

This document is an engineering and release-risk analysis, not legal advice.

## Fact / assumption / unknown matrix

| Class | Statement | Evidence and disposition |
|---|---|---|
| Fact | One reviewed public plugin can be distributed through the directory shared by ChatGPT and Codex. | OpenAI [plugin packaging](https://developers.openai.com/plugins/build/plugins) and [submission](https://developers.openai.com/plugins/deploy/submission) documentation. This does not guarantee identical host-local capabilities. |
| Fact | ChatGPT plugins may contain write tools. | OpenAI’s [security and privacy guide](https://developers.openai.com/plugins/guides/security-privacy) expressly addresses write actions, scope enforcement, confirmation, and audit controls. |
| Fact | OAuth 2.1 authentication does not by itself authorize every tool. | OpenAI’s [authentication guide](https://developers.openai.com/plugins/build/auth) requires resource/audience and scope checks; the security guide says scopes must be enforced on every tool call. |
| Fact | The present Evidence Lane ChatGPT profile refuses lifecycle writes. | `plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py`, `CHATGPT_PRO_GOVERNED` exposure boundary. |
| Fact | The v1.5.0 resource server uses a read-only base transport scope, per-tool OAuth security schemes, exact client/environment/role/project claims, owner-only production lifecycle writes, and owner-only remote Git. | `plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py`, `auth.py`, and the OAuth policy tests. This is locally implemented policy, not proof of an established IdP, public origin, token revocation, or OpenAI end-to-end linking. |
| Fact | Evidence Lane intentionally retains durable local project/session/lineage data. | `store.py`, `session.py`, `lineage.py`, and the configured durable project root. Local-first is not storage-free. |
| Fact | The Vercel MCP edge processes the complete bounded request body and allowed headers in transit and forwards them to the origin. | `plugins/evidence-lane-plugin/remote_adapter/api/index.py`. Vercel’s [runtime log documentation](https://vercel.com/docs/logs/runtime) also identifies retained operational metadata. |
| Fact | Optional Google Drive sync creates a third-party retained copy. | `plugins/evidence-lane-plugin/src/evidence_lane_plugin/persistence.py` uploads sealed PV/candidate/receipt bytes to a user-selected Drive folder. |
| Fact (user-provided portal evidence) | The selected OpenAI organization showed individual verification as complete. | User-provided Platform screenshot. This does not prove the draft’s Apps Management role, MCP domain, endpoint, metadata scan, or review readiness. |
| Assumption — rejected | “No vendor-held payload persistence means nearly zero data liability.” | Rejected by actual transport/storage paths and OpenAI’s [App Developer Terms](https://openai.com/policies/developer-apps-terms/), which impose developer privacy, security, notice, authorization, and legal-compliance duties. |
| Assumption — qualified | “The same plugin will behave identically in ChatGPT and Codex.” | One directory package is viable, but host capabilities and local source/runtime authority differ. The shared MCP UI/metadata can be consistent; host-owned controls and local servers cannot be claimed identical. |
| Assumption — rejected | “A developer tool may write without the same consent rules as other apps.” | Rejected. OpenAI, [GitHub App authorization](https://docs.github.com/en/apps/using-github-apps/authorizing-github-apps), and Vercel integration permissions all make writes permission- and user-authorization-dependent. |
| Unknown | Which durable production host, region, backup system, access roles, and deletion controls will operate the public MCP origin? | Must be selected and evidenced before staging-to-production promotion. |
| Unknown | Which established IdP and MCP client-registration method will be used, and whether its discovery/resource behavior passes ChatGPT and Codex end to end? | Must be resolved by staging OAuth tests; do not infer from JWT unit tests. |
| Unknown | What OpenAI workspace/plan terms and retention settings will apply to intended users? | Must be disclosed conditionally; no universal zero-retention promise is supportable. |
| Unknown | What Vercel plan, log/observability configuration, IP visibility, drains, region, DPA, and subprocessors will apply? | Inventory and approve before public traffic. |
| Unknown | Which jurisdictions, user categories, source-data categories, and legal roles apply at launch? | Requires qualified privacy/security counsel and a bounded launch geography. |
| Unknown | Whether OpenAI reviewers will accept the final scope taxonomy, HIL interaction, UI snapshot, and write-tool set. | Only a compliant reviewed submission can settle this. |

## What the normal OpenAI route actually is

OpenAI’s current [plugin packaging documentation](https://developers.openai.com/plugins/build/plugins) says a plugin can package skills, an MCP connection, optional UI, assets, and hooks. A public plugin is published once to the universal Plugins Directory shared by ChatGPT and Codex. Local and repository marketplaces are separate authoring, testing, and team-distribution mechanisms whose surface availability may differ.

That creates one release identity but not one identical runtime:

| Layer | ChatGPT | Codex |
|---|---|---|
| Directory package | Installs the reviewed plugin snapshot | Installs the same reviewed plugin snapshot |
| Skills | Uses supported packaged or MCP-imported skills | Uses supported packaged skills |
| MCP | Calls the reviewed public HTTPS MCP connection | Can call the same public MCP; a local authoring package may also declare a local `.mcp.json` server |
| Plugin-owned UI | Rendered from reviewed MCP UI resources and metadata | Rendered where that MCP UI capability is supported; native Codex controls remain host-owned |
| Authentication | End user authorizes the connection through the OpenAI host | End user authorizes the same resource-server contract when using the public connection |
| Local source authority | Not present in the ChatGPT client | May exist in a local Codex checkout and local durable runtime |

The `.app.json` file is a package mapping to a **registered MCP connection**, not a substitute for one. A portal draft ID is not a connector ID. The v1.5 package now maps the real read-safe connection `plugin_asdk_app_6a7743d238e48191be8b69c87fb71d7f`; this proves connection identity only, not endpoint health, OAuth correctness, candidate-SHA pickup, or complete-package installation.

## Can ChatGPT plugins write?

Yes. The current [OpenAI plugin security and privacy guide](https://developers.openai.com/plugins/guides/security-privacy) says plugin tools can access third-party APIs and write actions, and that developer mode includes write tools. It requires least privilege, explicit consent, server-side input validation, per-tool scope enforcement, audit logs, and human confirmation for irreversible operations. The [submission guide](https://developers.openai.com/plugins/deploy/submission) defines annotations for tools that create, update, delete, send, enqueue, run jobs, start workflows, write logs, push code, publish, or submit forms.

Write safety is therefore an authorization architecture, not a product-category exemption:

1. The user authorizes a bounded OAuth scope.
2. The MCP resource server verifies the token and scope on every tool call.
3. The tool schema and annotations accurately describe the effect.
4. The server binds the caller to the exact tenant, project, object, and action.
5. The host obtains confirmation when the action is destructive or irreversible.
6. The server validates again, applies idempotency or a one-use receipt, and records attribution.
7. Recovery or rollback exists where the external system supports it.

Evidence Lane’s six-way HIL is a product-governance control in addition to OAuth. OAuth proves who may request a category of action; it does not prove that the user made the exact Evidence Lane acceptance decision. A previous HIL token, a suggested prompt, a model inference, or broad “approved until HIL” statement must not be replayed as the final case-sensitive decision.

## Vercel and GitHub comparison

| Platform | Official evidence | What makes a write permissible | What does not make it permissible |
|---|---|---|---|
| Vercel | Vercel’s [integration permissions reference](https://vercel.com/docs/integrations/install-an-integration/manage-integrations-reference) assigns explicit read/write permissions; deployment write can create, update, or delete deployments. Its [Marketplace API](https://vercel.com/docs/integrations/create-integration/marketplace-api) and [Vercel API integration](https://vercel.com/docs/integrations/create-integration/vercel-api-integrations) documentation use scoped OAuth/access tokens and user authorization. | Installation consent, the necessary scoped permission, token validation, tenant/team binding, action-level policy, and an auditable call. | Calling it a developer tool, holding an unrelated Vercel token, or having read access. |
| GitHub | GitHub documents that an authorized [GitHub App can make changes on a user’s behalf](https://docs.github.com/en/apps/using-github-apps/authorizing-github-apps). Its effective authority is the intersection of the user’s permission and the app’s permission; activity is attributed to the user plus app and appears in audit/security logs. GitHub’s [OAuth authorization](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps/authorizing-oauth-apps) and [scope reference](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) distinguish read from write and show requested scopes to the user. | A GitHub App installation limited to selected repositories, fine-grained app permissions, a user or installation token, endpoint-specific permission checks, and action attribution. | “It is GitHub,” a broad `repo` token, repository visibility, or a contributor role by itself. |

For Evidence Lane, a GitHub App is preferable to a classic OAuth App for future hosted Git writes because GitHub recommends its fine-grained permission model. Production push authority should remain owner-only. Private testers should receive a staging identity and repository-specific installation permission; they should never receive a production client secret, private key, refresh token, or owner token.

## Official OpenAI requirements: mandatory versus recommended

### Mandatory for a public MCP-backed plugin

The current [submission documentation](https://developers.openai.com/plugins/deploy/submission) requires:

- Apps Management **Write** access for the submitter; organization owners already have it, while other submitters need the assigned permission.
- A verified individual or business developer identity that matches the listing, website, support, privacy, and terms identity.
- A stable public production HTTPS MCP URL. A [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) may be used for private or developer-mode connections but does not support public plugin submission or distribution.
- Control of the MCP domain, proven with the exact portal token at `/.well-known/openai-apps-challenge` on the MCP host or allowed parent origin.
- Authentication details and reviewer credentials when sign-in is required. Reviewer credentials must work without MFA, email confirmation, SMS confirmation, private-network access, or an unavailable human step.
- Accurate tool names, descriptions, input/output schemas, and `readOnlyHint`, `openWorldHint`, and `destructiveHint` values for every tool.
- A content security policy containing the exact domains used by any MCP UI.
- Final skills that pass local packaging tests and match the latest Scan Tools snapshot.
- Realistic starter prompts.
- At least five positive and three negative reproducible test cases.
- Public website, support, privacy-policy, and terms URLs matching the publisher identity.
- Accurate release notes, countries/regions, and policy attestations.

The current [OAuth documentation](https://developers.openai.com/plugins/build/auth) expects an authenticated MCP server to implement the MCP OAuth 2.1 contract:

- protected-resource metadata at the well-known endpoint, or discoverable through `WWW-Authenticate`;
- authorization-server discovery metadata;
- an authorization-code flow with PKCE `S256`;
- CIMD, DCR, or an approved predefined client;
- propagation of the `resource` parameter and a matching token audience/resource;
- signature, issuer, audience/resource, expiration/not-before, replay, and scope validation;
- `401 Unauthorized` plus an appropriate `WWW-Authenticate` challenge on failure;
- enforcement of the advertised OAuth scope on each affected tool call.

The current [App Developer Terms](https://openai.com/policies/developer-apps-terms/) also impose material requirements:

- app requests may be collected, used, stored, transmitted, and processed only as necessary for the request or law;
- the developer must maintain reasonable organizational, administrative, physical, and technical security;
- personal data must be processed lawfully, as authorized by the user, and under an adequate notice presented before processing;
- the app may not collect more personal data than reasonably necessary;
- the app must not process protected health information under HIPAA or payment-card data regulated by PCI DSS;
- other sensitive personal data needs the expected use and express opt-in where applicable;
- submission information and the app’s actual behavior must be accurate and current;
- the developer remains responsible for the app, API, compliance, support, updates, and documentation.

### Strongly recommended or operationally necessary

- Use an established identity provider rather than writing an authorization server from scratch, as OpenAI recommends.
- Separate production and staging issuers, audiences, clients, secrets, databases, domains, and user groups.
- Use short-lived access tokens, rotating refresh tokens, immediate revocation, and key rotation.
- Start with read-only and non-destructive local-write tools; add remote/destructive tools only after their dedicated tests pass.
- Require one idempotency key for retry-safe writes and one-use confirmation receipts for non-idempotent actions.
- Put owner-only actions such as publishing, production deployment, secret rotation, accepted-pointer movement, and production Git push outside normal tester scopes.
- Apply per-user, per-project, per-tool, and per-IP rate limits; alert on repeated authentication failures, scope failures, confirmation mismatches, and replay attempts.
- Maintain data inventory, retention schedule, deletion/export procedures, incident-response plan, vulnerability contact, dependency/SBOM controls, backups, and recovery tests.
- Red-team prompt injection, cross-project routing, OAuth mix-up, confused-deputy, replay, duplicate submission, destructive confirmation, and log-redaction paths.

## Current Evidence Lane implementation: verified facts

| Area | Verified current behavior | Release implication |
|---|---|---|
| ChatGPT write exposure | `mcp_server.py` defines `CHATGPT_PRO_GOVERNED`; it registers the catalog but refuses lifecycle mutations before service invocation. | Safe current boundary, but it does not satisfy the requested universal authenticated write behavior. A new reviewed policy must replace the refusal only after enforcement exists. |
| OAuth token verification | `auth.py` verifies asymmetric JWT signature, issuer, audience, expiration, subject, and a configured scope set. Non-loopback HTTP fails without static bearer or OAuth and HTTPS base URL. | Useful resource-server base. Missing production discovery proof, `nbf`/replay handling, per-tool scope enforcement, and owner/tester policy binding. |
| OAuth scope model | `mcp_server.py` currently passes one global list (default `evidence-lane:read evidence-lane:write`) to the server auth layer. No tool-call code reads the authenticated subject or applies a tool-specific scope. | Public write enablement would currently over-authorize. Must be fixed before enabling any ChatGPT write tool. |
| Tool metadata | Read-only, local-write, HIL-write, and remote-write annotation classes exist. The native catalog receipt hashes schemas and annotations. | Strong base, but every individual mapping must be audited against actual behavior and Scan Tools output. |
| Project routing | Every project-scoped MCP tool requires `project_id`; the store uses exact project routes and rejects cross-project fallback/collisions. | Strong base. OAuth identity must still be authorized for that exact project; possession of a valid token cannot imply access to every registered project. |
| Durable state | The MCP service persists project/session state, source-derived SQLite databases, JSON receipts, append-only ChatLineage JSONL, and SQLite lineage projections under the configured durable root. | This is intentional retained user data. A published retention, export, access, and deletion contract is mandatory; “no storage” is inaccurate. |
| ChatLineage | `lineage.py` redacts recognized secret patterns, rejects hidden/private reasoning fields, and stores visible payloads plus hashes in JSONL and SQLite/FTS. | Redaction reduces secret leakage but is not a complete PII classifier. Raw visible prompts or source snippets can still contain personal/confidential data. |
| Public Vercel edge | `remote_adapter/api/index.py` forwards the request method, body, and nearly all incoming headers—including the OAuth bearer—to one pinned durable HTTPS origin after release-health verification. It sets `no-store` responses and does not explicitly log bodies. | Vercel processes full request bytes in transit even though it is not accepted-state authority. Platform request metadata and runtime logs still exist. Authorization headers must never enter application logs or error responses. |
| Vercel logs/telemetry | The repository has no explicit MCP-body logging or custom MCP telemetry. Vercel nonetheless records function/edge events and request metadata; its [runtime log documentation](https://vercel.com/docs/logs/runtime) lists request IDs, status, path/search params, region, user agent/IP matching, outgoing requests, and plan-based retention. | “No logs” is inaccurate. Inventory the actual Vercel plan/configuration, hide IPs where supported, keep sensitive values out of paths/query strings, disable unnecessary analytics, and document retention. |
| Public Prompt Studio | `app/api/studio-query/route.ts` handles public questions. Project hits use committed retrieval; optional non-project no-hits are sent to OpenRouter when explicitly enabled. | Separate data flow and subprocessor. It must not be silently merged into the MCP privacy claim. Either keep it disabled for release or disclose OpenRouter processing and policy. |
| Google Drive | `persistence.py` accepts a user-supplied OAuth token from the environment, holds it in process memory, and can upload encrypted/sealed accepted PVs, candidates, or receipts to one user-selected folder. Drive is not a transactional runtime authority. | Optional Drive is vendor-held persistence. It needs separate opt-in, narrow Drive scope, retention/deletion rules, error redaction, and disclosure. Do not call it during plugin review unless it is part of the reviewed flow. |
| Public privacy page | The current page says the edge is not state authority and that the durable service governs retention/deletion. | Insufficient public notice: no publisher/controller identity, contact, categories/purposes, exact retention periods, processors, international-transfer basis, rights/request path, deletion SLA, incident contact, or OpenRouter/Drive disclosure. |
| Public MCP availability | The Vercel adapter fails closed unless a durable public origin and exact release SHA are configured and healthy. The known endpoint currently returns a blocked/unavailable state. | A portal draft cannot pass Scan Tools or review until a real production-like origin exists. A Windows/local tunnel cannot substitute for the public submission endpoint. |

## Actual data-flow trace

### Core ChatGPT/Codex MCP flow

1. A user installs the directory plugin and separately authorizes the underlying MCP connection.
2. The OpenAI host holds the conversation under the user/workspace’s terms and sends an MCP request containing selected prompt/tool arguments and an OAuth bearer.
3. The request reaches the public HTTPS edge. The current Vercel adapter reads the request body into memory (maximum 2 MiB), forwards the body and allowed headers to the durable origin, and streams the response back.
4. Vercel creates edge/function/observability metadata. The application does not intentionally log the body, but the operational configuration and provider contract determine metadata retention and access.
5. The durable origin validates the OAuth token, routes the exact `project_id`, executes the tool, and may read or write user-owned durable files and SQLite databases.
6. Visible operational events can enter redacted append-only ChatLineage. Tool responses travel back through Vercel to OpenAI and become part of the user’s interaction under OpenAI’s applicable terms.
7. If a separately authorized Drive sync is invoked, sealed PV/candidate/receipt bytes are uploaded to Google Drive. If a separately authorized GitHub or Vercel write is invoked in a future version, the selected external platform receives that action and its payload/metadata.

### Data-location matrix

| Location/party | Payload or metadata processed | Persistence status | Control/unknown |
|---|---|---|---|
| User’s ChatGPT/Codex workspace | Prompts, selected files/context, MCP tool requests and responses, conversation metadata | Depends on user plan, workspace settings, and OpenAI terms; OpenAI’s app terms allow prior app responses to remain usable under its user terms | Must not promise zero retention. Confirm the intended plan and workspace policy before the listing claim. |
| Vercel edge/function | Full MCP request/response in transit; bearer in transit; request/function/outgoing-call metadata | No Evidence Lane accepted state; Vercel runtime/observability metadata has plan-based retention | Confirm plan, log drains, IP visibility, analytics, region, DPA, subprocessors, access roles, and deletion behavior. |
| Durable Evidence Lane origin | Project config, source-derived SQLite, session state, visible ChatLineage, receipts, packages, tokens in request context | Persistent by design under the configured data root | Define retention per artifact, deletion/export procedure, backups, encryption, access audit, and operator roles. |
| Google Drive, optional | Encrypted or plaintext sealed archive depending on sensitivity/configuration; receipt metadata; Drive object identifiers | Persistent until the user/app deletes it under Google policy | Current code uploads but does not implement deletion. Confirm scope, encryption requirement, key custody, and deletion workflow. |
| GitHub/Vercel APIs, future optional writes | Repository/deployment payloads and action metadata | Retained under those platforms’ terms and repository/team policy | Require separate scopes, installation selection, confirmation, attribution, and recovery. |
| OpenRouter, optional public Studio only | A general question and system instruction; no intended Evidence Lane project context | Subject to OpenRouter/provider routing policy | Keep disabled or separately disclose and contract it; add it to subprocessor inventory if enabled. |
| Local/test logs | Test output, errors, correlation IDs, possibly exception fragments | Depends on local runner and CI configuration | Prohibit secrets/raw personal data, set retention, and scan artifacts. |

## Why “no-retention” is not “no liability”

### What local-first/no-retention genuinely reduces

- the number of durable third-party copies;
- the duration in which a retained payload can be compromised;
- the volume of data subject to backup, discovery, or deletion work;
- dependence on a hosted multi-tenant database for accepted project authority;
- impact radius when projects and tester identities are strictly isolated.

### What remains

- **Processing:** data is still parsed, transformed, searched, and returned.
- **Transmission:** OpenAI, the HTTPS edge, the durable origin, and any optional provider handle bytes in transit.
- **Access:** identities, scopes, tenant/project routing, support access, logs, and infrastructure roles must be controlled.
- **Security:** encryption, patching, dependency integrity, input validation, secret handling, monitoring, and incident response remain required.
- **Breach duties:** applicable notification and remediation duties can arise from exposure during processing or transit even if the application retained no long-term copy. The FTC’s [data breach response guidance](https://www.ftc.gov/business-guidance/resources/data-breach-response-guide-business) emphasizes rapid containment, investigation, service-provider review, legal assessment, and applicable notices.
- **Deletion and rights:** each retained copy, index, backup, receipt, log, and external mirror needs an accurate response path. Hashes and immutable audit records need a documented approach to erasure requests and legal retention.
- **Lawful basis and transparency:** users need notice of purposes, categories, parties, retention, choices, and rights before personal-data processing. Jurisdiction and customer role determine the exact rule.
- **Subprocessors and contracts:** Vercel, an identity provider, optional Drive/OpenRouter, monitoring/log drains, email/support systems, and the durable host may require contract/DPA and subprocessor review.
- **Accuracy and disclosure:** the app listing, privacy notice, tool schemas, annotations, and actual behavior must remain aligned.
- **Developer responsibility:** the OpenAI App Developer Terms include developer security/privacy duties and an indemnity related to the app/API and violations of terms or law.

The FTC’s [business data-security guidance](https://www.ftc.gov/business-guidance/privacy-security/data-security) supports minimizing collection, protecting what remains, and disposing of it securely. These steps reduce risk; they do not erase responsibility.

## Minimum compliant read-write architecture

### 1. Identity and authority planes

- **Publisher plane:** only the owner’s verified OpenAI organization can edit/submit/publish the plugin. Do not grant Apps Management Write to contributors merely so they can test.
- **Production operations plane:** owner-only access to production Vercel project, durable origin, IdP tenant/admin, encryption keys, domain/DNS, GitHub App private key, and release signing.
- **Staging tester plane:** separate staging hostname, OAuth issuer/audience/client, database root, encryption keys, logs, GitHub App installation, and test fixtures. Testers get named user accounts and the minimum test scopes—not shared secrets.
- **End-user plane:** each user authenticates individually. A token must bind `sub`, client, audience/resource, scopes, tenant, and allowed Evidence Lane project(s).

### 2. Scope model

Do not use one global `read write` gate. Define and enforce narrow scopes, for example:

- `evidence-lane:read`
- `evidence-lane:session.write`
- `evidence-lane:candidate.build`
- `evidence-lane:storage.select`
- `evidence-lane:mirror.write`
- `evidence-lane:git.prepare`
- `evidence-lane:git.push` (owner-only by default)
- `evidence-lane:pointer.approve` (owner-only and still subject to fresh HIL)

Every tool declares its exact scope set. The server checks token scope, subject/role, tenant, project grant, object boundary, and action policy before reading arguments that could trigger side effects. Scope failure returns a safe authorization error and creates no lifecycle receipt pretending success.

### 3. Action safety classes

| Class | Examples | Required controls |
|---|---|---|
| Read only | status, search, render, diff | read scope, project binding, rate limit, output minimization |
| Reversible local write | start session, add bounded backlog event | write scope, idempotency key, pre/post hash, audit receipt |
| Governed state write | candidate build, storage selection | dedicated scope, explicit parameters, conflict/CAS checks, recovery path, audit receipt |
| Destructive/HIL write | Fuse/pointer move, rollback decision, revoke connector | owner role, fresh host confirmation, fresh exact HIL token, one-use nonce, no replay, full receipt |
| Open-world write | Git push, deployment, submission, publication, external message | separate prepare and execute tools, owner-only scope, selected target proof, host confirmation, expiry, idempotency/one-use token, external audit link, recovery plan |

`readOnlyHint`, `destructiveHint`, `openWorldHint`, and idempotency behavior must be assigned from actual effects, not desired marketing language. A tool that merely writes an audit log is not read-only under OpenAI’s current submission guidance.

### 4. OAuth and request security

- Established OAuth 2.1/OIDC provider with protected-resource and authorization-server discovery.
- CIMD preferred where supported; otherwise compliant DCR or approved predefined client.
- Authorization code + PKCE `S256`, exact redirect URI allowlist, `resource` propagation, audience binding, asymmetric signing, short expiry, `nbf`, key rotation, revocation, and replay protection.
- Token never stored in ChatLineage, tool output, component props, URL, query string, or application logs.
- Optional OpenAI mTLS client authentication may authenticate the host connection but does not replace user OAuth.
- Request-size limit, schema validation, content-type validation, timeouts, retry bounds, SSRF/egress allowlist, prompt-injection tests, and fail-closed origin/release verification.

### 5. Data protection and operations

- Publish a data inventory with purpose, category, location, processor, retention, deletion, and access role.
- Separate conversation/tool transport from durable Evidence Lane evidence. Store only user-selected visible content necessary for the governed purpose.
- Define exact retention windows for active sessions, lineage, candidates, accepted PVs, receipts, logs, support records, and backups. “Governed by the service” is not a retention period.
- Implement authenticated export and deletion, including Drive mirrors and eligible backups/logs; document immutable-receipt treatment with counsel.
- Encrypt in transit and at rest; keep encryption keys outside artifacts; rotate and revoke secrets; prohibit secrets in source/CI output.
- Maintain incident response, vulnerability disclosure, security contact, access reviews, backup/restore proof, dependency updates, abuse monitoring, and alerting.
- Do not accept PHI or PCI card data through the app under the current App Developer Terms.

## Staged release checklist

### Stage A — local Codex-only proof

- [ ] Keep current production/public authority untouched.
- [ ] Build the universal package from the final tree with POSIX-safe ZIP member paths.
- [ ] Prove every skill loads from the packaged root.
- [ ] Install the package locally in an isolated Codex test profile/cache.
- [ ] Test plugin-owned MCP UI resources, metadata, icons, prompts, and complete tool inventory locally.
- [ ] Run read, reversible-write, denial, cross-project, replay, duplicate, rollback, redaction, and recovery tests.
- [ ] Record exact package hash and test receipts. Do not treat a local install as ChatGPT proof.

### Stage B — isolated staging MCP

- [ ] Deploy only after a separate deployment HIL; use a staging origin/domain/IdP/data root.
- [ ] Configure protected-resource and authorization-server metadata.
- [ ] Create owner and least-privilege tester identities; no shared OAuth/client secrets.
- [ ] Enforce per-tool scopes and project grants.
- [ ] Verify domain, TLS, optional mTLS, rate limits, logs, alerts, deletion/export, backup/restore, and incident path.
- [ ] Test the stable public HTTPS path; a tunnel may test private connectivity but is not submission proof.

### Stage C — production-readiness gate

- [ ] Owner-only publisher and infrastructure roles confirmed.
- [ ] Production origin pinned to the reviewed release and isolated from staging.
- [ ] Privacy policy, terms, support, security, retention, subprocessor list, and deletion form live and counsel-reviewed.
- [ ] Five positive and at least three negative reviewer cases use synthetic data and a no-MFA reviewer account.
- [ ] Every tool’s schema, annotations, response minimization, and scope mapping audited.
- [ ] Prompt injection, confused deputy, cross-tenant, token replay, duplicate write, and destructive confirmation tests pass.
- [x] `.app.json` references the real registered connection; no portal/app ID is substituted.

### Stage D — portal review (manual owner action)

- [ ] Upload/enter only final reviewed metadata and assets.
- [ ] Enter the production MCP URL and exact domain challenge.
- [ ] Scan tools; reconcile every discovered tool and imported skill to the final package.
- [ ] Complete prompts, tests, availability, release notes, and attestations accurately.
- [ ] Submit for review only after explicit HIL. Submission does not publish.

### Stage E — publication (separate manual owner action)

- [ ] Review OpenAI’s approval and any conditions.
- [ ] Re-run production smoke, OAuth, deletion, and rollback tests against the approved snapshot.
- [ ] Publish only after a new explicit publication HIL.
- [ ] Monitor auth failures, anomalous writes, support, revocation, dependency/security notices, and metadata drift.

## Residual-risk register

| Risk | Current likelihood / impact | Required mitigation | Residual owner decision |
|---|---|---|---|
| IdP or deployment misconfiguration bypasses the locally tested scope/role/project contract | Medium / Critical | Exact environment/client allowlists, production owner role, per-tool scopes, isolated staging, and live adversarial tests | Accept only after production-like IdP and origin proof |
| Prompt injection induces valid but unwanted write | High / High | Server-side policy, host confirmation, two-step prepare/execute, target preview, one-use nonce | Define which writes remain unavailable in ChatGPT |
| Cross-project or cross-tenant access | Medium / Critical | OAuth project grants plus existing exact route law; adversarial concurrency tests | Decide tenant model before public beta |
| HIL token replay or model-inferred approval | Medium / Critical | Fresh case-sensitive token tied to action hash/session/generation/nonce/expiry; consume once | Preserve final human decision boundary |
| Vercel/log leakage of bearer or prompt | Medium / High | No body/header logging, redaction, sensitive query ban, access review, shortest retention, log-drain review | Select hosting/log configuration |
| Local ChatLineage retains personal/confidential text | High / High | Data minimization, explicit capture control, PII scanning, retention/deletion/export | Decide permissible source categories |
| Drive mirror creates undeleted third-party copy | Medium / High | Separate opt-in/scope, encryption, inventory, delete/verify operation | Decide whether Drive ships at all |
| OpenRouter becomes undisclosed processor | Low while disabled / High if enabled | Keep disabled for plugin release or disclose/contract/test separately | Decide whether public Studio fallback belongs in product |
| Reviewer credentials expose real data | Medium / High | Synthetic tenant, least privilege, no real repo/project, rotate after review | Owner provisions fixture account |
| Immutable receipts conflict with erasure request | Medium / High | Store minimal/pseudonymous identifiers; separate payload from receipt; counsel-reviewed retention | Counsel defines lawful treatment |
| Public endpoint abuse/denial of service | High / Medium | Rate limits, quotas, WAF, request caps, anomaly alerts, circuit breakers | Define free-tier limits and support SLA |
| Incorrect privacy/zero-retention claim | High / High | Evidence-backed data map and precise provider/workspace qualifications | Owner approves only counsel-reviewed language |
| OpenAI review or later policy change | Medium / Medium | Versioned compliance matrix, change monitoring, re-review workflow, rapid disable/revoke | Maintain release owner and response SLA |

## Questions for qualified privacy/security counsel

1. For each intended user region, is Evidence Lane the controller, processor/service provider, or another role for source material, prompts, tool arguments, lineage, and support data?
2. What lawful bases, notices, and consents are required for user-provided source material that contains third-party personal data?
3. Do OpenAI’s App Developer Terms’ separate-party language and the proposed Vercel/IdP/Drive arrangements require distinct DPAs, SCCs, or subprocessor notices?
4. What exact privacy-policy disclosures are required for OpenAI conversation retention, Vercel request metadata/logs, the durable origin, optional Drive, optional GitHub/Vercel actions, and disabled-or-enabled OpenRouter?
5. Which U.S. state privacy and breach-notification laws apply based on publisher location, user location, thresholds, and data categories? Are other national laws likely to apply at launch?
6. Can immutable content hashes and audit receipts be retained after a valid deletion request, and if so under what purpose, minimization, pseudonymization, access, and retention limits?
7. What retention periods are defensible for sessions, visible ChatLineage, candidates, accepted PVs, security logs, reviewer fixtures, support tickets, and backups?
8. What deletion/export SLA and identity-verification process should apply, including third-party mirrors and backups?
9. Are any planned source categories “sensitive personal data” requiring express opt-in, impact assessment, or exclusion? Confirm the current prohibition on PHI and PCI data for this app route.
10. What incident-response, regulator/customer notice, cyber-insurance, vendor-notification, and evidence-preservation obligations apply?
11. Are the proposed disclaimers and limitation language adequate without being misleading, and how should risk allocation be handled in user terms?
12. Does a free testing period change any privacy, consumer-protection, security, or contractual duty? The engineering assumption is no.
13. What accessibility, age, sanctions/export, copyright, and confidential-information restrictions should appear in the listing and terms?
14. Can production authority remain an individual verified publisher, or is a business entity, dedicated support address, and formal security contact advisable before public release?
15. What records of consent, tool confirmation, OAuth grant/revocation, and HIL decision may be kept, for how long, and in what pseudonymous form?

## Final conclusion

The normal universal plugin route is viable. The correct target is one reviewed plugin identity, one accurate metadata and UI contract, and one OAuth-protected public MCP resource usable from ChatGPT and Codex, with local Codex authoring/testing remaining a separate pre-release surface. The release must not flatten the two runtimes into a false claim of identical local authority.

Proceed with local build and isolated testing, then staging, compliance proof, review, and separate publication HILs. Do not enable public write authority merely because the individual publisher identity is verified or because the plugin is a developer tool.
