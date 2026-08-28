<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Credits and contribution policy

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


## Ownership

Evidence Lane is conceived, directed, funded, and owned by Praveen Rathee.
Copyright © 2026 Praveen Rathee. All rights reserved. AI systems and software
tools assist work; they do not own the project, accept candidates, authorize
source changes, or receive copyright authorship.

## Credited AI and toolchain roles

- OpenAI Codex and GPT-5.6: human-directed source implementation, debugging,
  test execution, and evidence review.
- Google Gemini: reasoning and exploratory discussion.
- Anthropic Claude: a separate Fable-plugin experiment and comparative context;
  no Fable or Claude-generated source is incorporated by reference.
- GitHub/Git: source control, immutable history, and reviewed distribution.
- Python, SQLite, MCP, Vercel, and the dependencies declared in
  `pyproject.toml` and `requirements.in`: implementation and deployment
  toolchain.

Third-party names are used only to describe provenance. Their software,
services, licenses, and trademarks remain governed by their respective owners.
The [third-party licenses page](THIRD_PARTY_LICENSES.md) records the current
pinned dependency, packaged-binary, pypdfium2/PDFium, and upstream notice
boundaries that must remain current before publication.

## Public upstream references

The PV6 research cycle inspected exact commits of GitHub Agentic Workflows,
GitHub Agentic Workflows MCP Gateway, GitHub Copilot SDK, GitHub Agentic
Workflows threat detection, GitHub Agentic Workflows harness, Open WebUI,
Graphify, GitHub CodeQL source, GitHub MCP Server, GitHub Branch Deploy, and
GitHub Local Action. OpenAI public MCP and app guidance supplied metadata,
annotation, structured-result, interactive-panel, CSP, and publication context.
The [upstream reference provenance ledger](UPSTREAM_REFERENCE_PROVENANCE.md)
records every audited commit and tree, license, intended role, local
implementation surface, refusal boundary, and verdict. The website renders the
same complete ledger and links each repository to its pinned commit rather than
to an unverified moving branch.

That ledger is the attribution authority for these references. It does not
turn a URL into copied source, claim feature parity, treat Open WebUI's current
license as MIT, or convert an Actions run into a coding-agent session.

## Human contributions

- [Kapil Dhawan](https://www.linkedin.com/in/kdhawan23/): Enterprise
  Engineering and Product Communication Reviewer.
- [Steven Tock](https://www.linkedin.com/in/steventock/): Senior Strategic
  Reviewer and Controlled-AI Advisor.
- [Sumit Hooda](https://www.linkedin.com/in/sumit-hooda-378884192/): External
  Software Engineering Evaluator for bare-metal code review, adversarial plugin
  testing, AI-drift analysis, and implementation loophole discovery.

These records describe review and evaluation roles. Asking a question, giving
feedback, suggesting a direction, or testing a build does not automatically
create source authorship, ownership, or acceptance authority.

An accepted source contribution can be credited as authorship only when it has
an attributable record, an explicit license/rights basis, a reviewable Git
Delta, passing evidence, and human acceptance through the project HIL. Until
those conditions are met, the repository will not infer contribution or
ownership claims.

## Release boundary

Credits never grant candidate approval, Git push authority, plugin installation
authority, pointer movement, Fuse, main-branch merge, or State Travel. Those are
separate governed actions.
