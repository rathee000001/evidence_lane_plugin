# Repository map

| Path | Role |
| --- | --- |
| `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` | Codex product identity and UI metadata |
| `plugins/evidence-lane-plugin/.mcp.json` | Package-local native MCP launch contract |
| `plugins/evidence-lane-plugin/src/evidence_lane_plugin/` | Lifecycle engine, lane authorities, SDK, and native server |
| `plugins/evidence-lane-plugin/skills/` | Seventeen governed workflow skills |
| `plugins/evidence-lane-plugin/hooks/` | Eight lifecycle event contracts and isolated handlers |
| `plugins/evidence-lane-plugin/schemas/` | Lane, Canon, Learning, query, and schema-evolution contracts |
| `plugins/evidence-lane-plugin/scripts/codex_release/` | Deterministic package, slot, helper, restart, and acceptance routes |
| `plugins/evidence-lane-plugin/scripts/windows_tunnel/` | Optional version-bound Windows support tunnel |
| `plugins/evidence-lane-plugin/remote_adapter/` | Public documentation-site source; not lifecycle authority |
| `docs/` | Architecture, public contracts, runbooks, matrices, licenses, and provenance |
| `github-pages/` | GitHub Pages layout and navigation source |
| `scripts/prepare_github_pages.py` | Exact-commit documentation projection builder |
| `.github/actions/evidence-lane-ci/` | Reusable clean-CI action |
| `.github/workflows/` | CI, CodeQL, preview, and GitHub Pages workflows |
| `tests/` | Unit, integration, package, host, lifecycle, and contract verification |

Runtime project data is not stored in this source tree. The configured durable
Evidence Lane store owns each project's SQLite sectors, Plan, ChatLineage,
Canon, AI Learning, Memory, candidates, pointers, and receipts.
