---
name: inspect-project-evidence-map
description: "Inspect and maintain one project's explicitly linked evidence and project references with attributed read-only queries. Use for integrity checks and selected project links."
---

# Inspect project evidence map

Read [shared boundaries](../run-project-lifecycle/references/shared-boundaries.md) before using the workflow.
Use [owned action references](references/actions.json) and the live MCP schemas for exact arguments.

Use `project_evidence_map_inspect` to verify the selected project's coherent
project evidence head coordinator, initialized lane databases, schema files, registered content and any
current natural graph artifacts within the selected budgets. `project_evidence_map_query`
reads a bounded attributed graph of the actual lanes, Plan tasks, Sources and
project links. Follow disclosed offsets or reduce selected graph categories.
Read `project_evidence_links_read`; use `project_evidence_link` and `project_evidence_unlink` for explicit
links under current project grants. `project_evidence_links_verify` checks their bounded
history without claiming current target access. `linked_project_evidence_query` reads
separately authorized owner queries in place. A link does not grant access.
For a topology export, discover `universe.topology` through `lane_view_catalog`,
preview its current source binding and use `lane_view_refresh` for only the
selected Mermaid/DOT formats. Exact project evidence head coordinator coordinates remain in the live
inspection; topology exports do not include their own publication effects.
Readback reports stale or historical files without automatically refreshing.
Keep per-project Universe, federation, and connector grants separate.
