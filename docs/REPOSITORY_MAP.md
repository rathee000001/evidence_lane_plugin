<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Repository map

| Path | Role |
| --- | --- |
| `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` | Codex product identity and UI metadata |
| `plugins/evidence-lane-plugin/.mcp.json` | Package-local native MCP launch contract |
| `plugins/evidence-lane-plugin/src/evidence_lane_plugin/` | Lifecycle engine, lane authorities, SDK, and native server |
| `plugins/evidence-lane-plugin/skills/` | Twenty-six governed native workflow skills; no separate command layer |
| `plugins/evidence-lane-plugin/hooks/` | Eleven lifecycle event contracts and isolated handlers |
| `plugins/evidence-lane-plugin/schemas/` | Lane, Canon, Learning, query, and schema-evolution contracts |
| `plugins/evidence-lane-plugin/scripts/codex_release/` | Deterministic package, slot, helper, restart, and acceptance routes |
| `plugins/evidence-lane-plugin/scripts/windows_tunnel/` | Optional version-bound Windows support tunnel |
| `apps/evidence-lane-app/` | Public documentation/GitHub App source; not lifecycle authority. Clean builds do not require the deferred, untracked Prompt Studio RAG index |
| `docs/` | Architecture, public contracts, runbooks, matrices, licenses, and provenance |
| `github-pages/` | GitHub Pages layout and navigation source |
| `scripts/prepare_github_pages.py` | Exact-commit documentation projection builder |
| `.github/actions/evidence-lane-ci/` | Reusable clean-CI action |
| `.github/workflows/` | CI, CodeQL, preview, and GitHub Pages workflows |
| `.github/evidence-lane-repository-fingerprints.v1.json` | Per-file changed/unchanged refresh receipts bound to the exact feature commit and timestamp |
| `scripts/generate_repository_source_fingerprints.py` | Two-commit repository fingerprint receipt generator and CI verifier |
| `tests/` | Unit, integration, package, host, lifecycle, and contract verification |

Runtime project data is not stored in this source tree. The configured durable
Evidence Lane store owns each project's SQLite sectors, Plan, ChatLineage,
Canon, AI Learning, Memory, candidates, pointers, and receipts.
