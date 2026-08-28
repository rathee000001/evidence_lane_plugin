<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Upstream reference provenance

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


This ledger records public upstream material inspected during the Evidence Lane
1.3.0 PV6 research cycle. It separates source identity, the contract studied,
the bounded local implementation, and material that was explicitly not copied.
Credit is not source authority, candidate acceptance, or proof of feature
parity.

## Intake and verification boundary

- Evidence Lane Source Intake batch:
  `intake_0db599bb979fc0561db6ed3e4346dec8`
- Batch SHA-256:
  `0DB599BB979FC0561DB6ED3E4346DEC8A2C120F1815605BEAD3BF012116A1CE3`
- Intake result: six ordered URL references plus Chat Lineage; remote URLs were
  classified as `POINTER_TEXT_ONLY`. Source Intake copied no remote payload,
  created no candidate, and moved no accepted pointer.
- Git identity verification: each repository was cloned separately into an
  ignored read-only audit directory. The commits and trees below are the
  independently observed Git identities for that audit, not identities proven
  by the pointer-only intake receipt.

## Exact public reference ledger

### GitHub Agentic Workflows (`github/gh-aw`)

- Repository: <https://github.com/github/gh-aw>
- Audited commit: `5107b7bd547ce124f69084dc623e45a539cd17f5`
- Audited tree: `1fa5cd684c6906a2f7bca8468097292fe4f6a40a`
- License observed at that identity: MIT
- Role: workflow compiler, least-authority Actions boundary, safe-output, and
  cost-control research reference.
- Local use: its contract informed the explicit separation of an Actions run,
  a Copilot coding-agent session, a remote Git action, a deployment, and an
  Evidence Lane PV. The local workflows remain project-authored and pinned.
- Implementation surfaces: `.github/workflows/`,
  `.github/copilot-instructions.md`, and
  `github_automation_governance.py`.
- Boundary: no upstream workflow file is presented as Evidence Lane code and
  no passing Action is treated as an agent session or HIL approval.
- Verdict: **pursue selective safe-workflow and output contracts**. Confidence:
  high. A license conflict, a materially different upstream contract, or a
  failed local receipt-equivalence test would change this verdict.

### GitHub Agentic Workflows MCP Gateway (`github/gh-aw-mcpg`)

- Repository: <https://github.com/github/gh-aw-mcpg>
- Audited commit: `0edcc73107c5a08b5bd8cfd489e68400b5ef95db`
- Audited tree: `58482ae9b60a14327f48f77e71ffd2d1930cf8ea`
- License observed at that identity: MIT
- Role: MCP gateway isolation, validation, bounded transport, timeout, audit,
  and payload-handling research reference.
- Local use: selective fail-closed origin validation, non-public literal-host
  rejection, exact release identity, bounded request bodies, and sanitized
  health output are implemented in `remote_adapter/api/index.py`.
- Boundary: the Evidence Lane adapter is not the upstream gateway, does not
  claim feature parity, and does not import its source.
- Verdict: **pursue selective gateway-safety patterns**. Confidence: high. A
  failing adversarial adapter test or evidence that a selected pattern weakens
  the durable-origin boundary would change this verdict.

### GitHub Copilot SDK (`github/copilot-sdk`)

- Repository: <https://github.com/github/copilot-sdk>
- Audited commit: `4e95deee58f2712a9e75e66a1ffba4460cc9a1d5`
- Audited tree: `3cb5e3b004cc224a538bc17078b651cbe5fa2509`
- License observed at that identity: MIT
- Role: agent-session identity, lifecycle hooks, tool authorization, audit,
  redaction, and bounded-session research reference.
- Local use: Evidence Lane now records Actions evidence and coding-agent
  evidence as different classes and redacts bounded agent output before it can
  enter a remote Git receipt.
- Implementation surfaces: `github_automation_governance.py`,
  `remote_git.py`, `.github/agents/evidence-lane.agent.md`, and their tests.
- Boundary: an Evidence Lane task is not claimed to be a GitHub Copilot agent
  session; only a real GitHub session identifier can prove that surface.
- Verdict: **pursue selective session and hook contracts**. Confidence: high.
  An incompatible GitHub session model or a failed real-agent receipt test
  would change this verdict.

### GitHub Agentic Workflows threat detection (`github/gh-aw-threat-detection`)

- Repository: <https://github.com/github/gh-aw-threat-detection>
- Audited commit: `09cc2eed706368655776af394eab7146629f905a`
- Audited tree: `88003d9a48ba64c31f67cea827fe5ff726c472cf`
- License observed at that identity: MIT
- Role: post-output secret, prompt-injection, destructive-command, and
  infrastructure-failure classification research reference.
- Local use: complete captured remote output is scanned; only a redacted,
  bounded projection is persisted, with threat and infrastructure status kept
  separate. The receipt says `advisory_only` and `post_execution`; it is never
  represented as prevention.
- Implementation surfaces: `github_automation_governance.py`, `remote_git.py`,
  and `tests/test_github_automation_governance.py`.
- Boundary: the local scanner is a narrow project-authored classifier, not the
  upstream product and not a security guarantee.
- Verdict: **fix-then-pursue as advisory defense in depth only**. Confidence:
  high. Independent red-team results with measured false-negative and
  false-positive rates would be required before strengthening the claim.

### GitHub Agentic Workflows harness (`github/gh-aw-harness`)

- Repository: <https://github.com/github/gh-aw-harness>
- Audited commit: `75ed171c12321e0cf4249a732c9860386bdc46a0`
- Audited tree: `c0161d9c6f8a0b50aa00a34c0162b8ac3fbeb01d`
- License observed at that identity: MIT
- Role: research reference for repeatable agent-workflow execution and testing.
- Local use: Evidence Lane adopts a narrow project-authored v1 execution
  contract: exact registered in-process handlers, bounded JSON fixtures,
  explicit PASS/FAIL/ERROR expectations, post-output inspection, deterministic
  case receipts, and a sealed run receipt. Shell, command, executable, working
  directory, and script inputs are rejected before a fixture runs.
- Implementation surfaces: `github_automation_governance.py` and
  `tests/test_github_automation_governance.py`.
- Boundary: no upstream code is imported. The execution harness neither reads
  nor writes the SQLite continuity/retrieval brain; it proves repeatable fixture
  execution, while SQLite separately preserves and retrieves governed history.
- Verdict: **pursue the bounded testable execution contract**. Confidence:
  high. A reproducible failure, non-deterministic receipt, command-input bypass,
  or evidence of hidden SQLite coupling would change this verdict.

### Open WebUI (`open-webui/open-webui`)

- Repository: <https://github.com/open-webui/open-webui>
- Audited commit: `01f4282f1ffe0d6212f58d3afbeae21fffd0c4be`
- Audited tree: `89b6c6e20fd32f8df36309d2ffffc7c9e6043522`
- License observed at that identity: Open WebUI License.
- Role: user-stated conceptual reference for inspectable AI application
  surfaces and human review flows.
- Local use: conceptual credit only. This audit did not prove that current
  Open WebUI bytes were copied into Evidence Lane, and no such code import is
  claimed.
- Boundary: Open WebUI's current license and branding conditions apply to its
  source. Any future byte-level reuse requires a file-level provenance record,
  license review, and a separate accepted Git Delta before implementation.
- Verdict: **fix-then-pursue conceptual credit only; do not import code without
  exact provenance and rights review**. Confidence: high. A verified historical
  commit-to-file derivation and compatible written rights basis would change
  this verdict.

### Graphify (`Graphify-Labs/graphify`)

- Repository: <https://github.com/Graphify-Labs/graphify>
- Audited commit: `00efd6e7969837ae4a9f11d8d504dcd3b20b09df`
- Audited tree: `d1512b0250570474ee45b4169ba2d3b1b35376fa`
- License notices observed at that identity: Apache-2.0 and MIT
- Role: stable graph identities, extracted-versus-inferred provenance, diff
  coverage, and affected-subgraph research.
- Local use: project-authored stable node/edge identities, graph diff coverage,
  and bounded impact traversal with conformance tests.
- Boundary: no Neo4j service, LLM pipeline, installer, UI, or wholesale source
  transplant was adopted. V5.9 names the SQLite Brain Builder identity and is
  not represented as a Graphify version.
- Verdict: **pursue bounded graph contracts**. Confidence: high. A failed
  identity-stability or impact-coverage test, or incompatible license evidence,
  would change this verdict.

### GitHub CodeQL source (`github/codeql`)

- Repository: <https://github.com/github/codeql>
- Audited commit: `74c8994c9fa3ca4551c01879ef9f74e3e09e791a`
- Audited tree: `06e9890166e4ad4bd015439016a137f8f4099ca8`
- License observed at that identity: source MIT; CLI separately licensed
- Role: negative-test and static-analysis research model.
- Local use: project-authored security negative cases and explicit
  `NOT_RUN_LOCAL` receipts when the CodeQL CLI was unavailable.
- Boundary: the CLI was not installed or run, so no CodeQL scan result is
  claimed and no CLI bytes are bundled.
- Verdict: **pursue negative-test guidance only**. Confidence: high. An exact
  executable CLI receipt could justify a separately labeled scan claim.

### GitHub MCP Server (`github/github-mcp-server`)

- Repository: <https://github.com/github/github-mcp-server>
- Audited commit: `3778a41476e31a072430cfee7c5d31c5f72def60`
- Audited tree: `ae97fb877726d54334bebcaaa640e58bab3ca84e`
- License observed at that identity: MIT
- Role: read-only toolset bounds, validation, and connector-routing research.
- Local use: ordered connector guards, capability checks, exact preferred-route
  selection, and ambiguity failure.
- Boundary: Evidence Lane does not embed or execute a second MCP server and
  does not inherit that server's authority.
- Verdict: **pursue connector-boundary patterns**. Confidence: high. A routing
  ambiguity or authority-bypass test failure would change this verdict.

### GitHub Branch Deploy (`github/branch-deploy`)

- Repository: <https://github.com/github/branch-deploy>
- Audited commit: `7ad5ec6a7e19e3e341846e4d33c4ed779b3e8036`
- Audited tree: `5a901697cd7671a61db70f7f27787c6ba257deb8`
- License observed at that identity: MIT
- Role: no-op, commit-safety, and explicit deployment-action research.
- Local use: exact-SHA remote-action preparation, no-op detection, and
  commit-bound deployment receipts.
- Boundary: no automatic merge, main push, production promotion, or implicit
  HIL approval was adopted.
- Verdict: **pursue guarded deployment patterns**. Confidence: high. A receipt
  that can act beyond its bound branch or commit would change this verdict.

### GitHub Local Action (`github/local-action`)

- Repository: <https://github.com/github/local-action>
- Audited commit: `b9351d8a8f1e6eed27646f4d892b49a3847ba180`
- Audited tree: `77093c3aeb8b01bcc35f24c7160596b725a7421d`
- License observed at that identity: MIT
- Role: disposable fixture and secret-safe one-shot execution research.
- Local use: temporary fixture repositories, registered-secret suppression,
  and redacted project-authored proof logs.
- Boundary: no arbitrary JavaScript execution, persistent local runner, or
  host-secret inheritance was adopted.
- Verdict: **pursue bounded fixture patterns**. Confidence: high. A retained
  fixture, unredacted secret, or non-deterministic proof would change it.

## Documentation and service references

OpenAI Codex, ChatGPT, and their official documentation supported the host,
plugin, skill, and product-boundary research. GitHub documentation supported
the Actions, CodeQL, Copilot coding-agent, and custom-agent configuration
research. OpenRouter documentation supplied the optional zero-cost general
question route contract. These are documentation and service credits, not
claims that the services or their documentation were copied as repository
source.

- OpenAI Codex: <https://openai.com/index/introducing-codex/>
- OpenAI plugin MCP server guidance:
  <https://developers.openai.com/plugins/build/mcp-server>
- OpenAI ChatGPT UI and MCP Apps guidance:
  <https://developers.openai.com/plugins/build/chatgpt-ui>
- OpenAI MCP plugin review requirements:
  <https://developers.openai.com/plugins/deploy/app-review>
- OpenAI Codex plan guide:
  <https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan>
- GitHub Copilot coding-agent overview:
  <https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/overview>
- GitHub CodeQL configuration:
  <https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configure-code-scanning>
- OpenRouter free-model router:
  <https://openrouter.ai/docs/guides/routing/routers/free-router>

The OpenRouter integration is not Evidence Lane project authority. It is
eligible only after the committed corpus returns no hit for a non-project
general question, uses the fixed `openrouter/free` model router, sends no
project context or prior chat history, and has no paid fallback. Its API key is
host-managed and never committed, copied from another project, or exposed to
the browser. The route remains visibly unavailable until that separate key and
enable flag are configured.

## Acceptance boundary

This ledger is a research and attribution record. It does not move the accepted
PV5 pointer, accept PV6, authorize a remote push, merge `main`, deploy Vercel,
install a plugin, or prove a GitHub coding-agent session. Those remain separate
receipted actions and human gates.
