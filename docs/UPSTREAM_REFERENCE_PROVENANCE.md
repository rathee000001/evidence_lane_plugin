# Upstream reference provenance

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
- Role: research reference for a future repeatable agentic-workflow harness.
- Local use: no production code or behavioral contract adopted. The audited
  repository describes itself as intentionally minimal and does not define a
  sufficient production harness contract.
- Boundary: presence in the ledger is not implementation evidence.
- Verdict: **park production use; retain as a research/test reference**.
  Confidence: high. A versioned executable contract with reproducible fixtures
  and stable receipts would change this verdict.

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

## Documentation and service references

OpenAI Codex, ChatGPT, and their official documentation supported the host,
plugin, skill, and product-boundary research. GitHub documentation supported
the Actions, CodeQL, Copilot coding-agent, and custom-agent configuration
research. These are documentation and service credits, not claims that the
services or their documentation were copied as repository source.

- OpenAI Codex: <https://openai.com/index/introducing-codex/>
- OpenAI Codex plan guide:
  <https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan>
- GitHub Copilot coding-agent overview:
  <https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/overview>
- GitHub CodeQL configuration:
  <https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configure-code-scanning>

## Acceptance boundary

This ledger is a research and attribution record. It does not move the accepted
PV5 pointer, accept PV6, authorize a remote push, merge `main`, deploy Vercel,
install a plugin, or prove a GitHub coding-agent session. Those remain separate
receipted actions and human gates.
