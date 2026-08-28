<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Repository map

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


| Path | Role |
| --- | --- |
| `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` | Codex product identity and UI metadata |
| `plugins/evidence-lane-plugin/.mcp.json` | Package-local native MCP launch contract |
| `plugins/evidence-lane-plugin/src/evidence_lane_plugin/` | Lifecycle engine, lane authorities, SDK, and native server |
| `plugins/evidence-lane-plugin/skills/` | Seventeen governed workflow skills |
| `plugins/evidence-lane-plugin/hooks/` | Eleven lifecycle event contracts and isolated handlers |
| `plugins/evidence-lane-plugin/schemas/` | Lane, Canon, Learning, query, and schema-evolution contracts |
| `plugins/evidence-lane-plugin/scripts/codex_release/` | Deterministic package, slot, helper, restart, and acceptance routes |
| `plugins/evidence-lane-plugin/scripts/windows_tunnel/` | Optional version-bound Windows support tunnel |
| `apps/evidence-lane-remote-adapter/` | Public documentation-site source; not lifecycle authority |
| `docs/` | Architecture, public contracts, runbooks, matrices, licenses, and provenance |
| `github-pages/` | GitHub Pages layout and navigation source |
| `scripts/prepare_github_pages.py` | Exact-commit documentation projection builder |
| `.github/actions/evidence-lane-ci/` | Reusable clean-CI action |
| `.github/workflows/` | CI, CodeQL, preview, and GitHub Pages workflows |
| `tests/` | Unit, integration, package, host, lifecycle, and contract verification |

Runtime project data is not stored in this source tree. The configured durable
Evidence Lane store owns each project's SQLite sectors, Plan, ChatLineage,
Canon, AI Learning, Memory, candidates, pointers, and receipts.
