---
name: evi-source-intake
description: Evidence Lane generalized ordered source intake with optional Git history, auto-detection, exact overrides, 18 lanes, Project Engulf, and Chat Lineage.
---

# Evidence Lane Source Intake

Before any tool call, read and apply
`../evidence-lane-code-lifecycle/SKILL.md`, including its Codex hook/skill
ownership contract. This skill owns behavior; hooks provide lifecycle receipts
only.

Call `source_intake_classify` for the user's ordered sources. Auto-detect Git,
local code, SQLite/PV brains, Chat Lineage, discussion, analysis, plan, Mode,
docs, data/Excel, PPT, PDF/OCR, images/OCR, artifacts, custom, research,
Project Engulf, and SQLite Brain; accept exact per-source overrides. Always
include Chat Lineage. Classification alone copies no source, creates no
candidate, and moves no pointer.

The Git history arm is optional for source intake. `AUTO` enriches a Git
worktree with history and otherwise falls back to deterministic content
indexing; `REQUIRED` fails closed without a readable Git worktree and HEAD;
`DISABLED` skips Git history explicitly. None of these modes authorizes a
remote Git write. Use existing internal enrollment and route tools only after
the visible classification is accepted.

For a Git worktree, enumerate current sources from tracked index entries only.
Before reading exact bytes into SQLite, FTS, CAS, history, topology, or a PV
package, apply the shared source policy and exclude `.env` variants,
`.runtime`/cache/build artifacts, credential or private-key paths, configured
secret values, and recognized credential-shaped content. Never place secret
bytes or secret environment-variable names in an exclusion receipt. Non-Git
sources use the same deterministic path/content policy but do not claim a
tracked-only boundary.

## Authoritative bounded lane-query workflow

`EVIDENCE_LANE_BOUNDED_LANE_QUERY_V1` is the only skill-owned workflow for
reading lane evidence. It queries immutable lane authority progressively; it
never loads a full PV package, Plan backlog, lane SQLite database, or raw FTS
corpus into model context.

1. Call `lane_catalog` once and resolve the supplied alias to its exact
   `canonical_lane_id`, `sqlite_filename`, FTS table, and mutation policy. Do
   not guess a lane name, filename, table, or filesystem location.
2. Call `lane_status` with the exact project_id, canonical lane, and optional
   pv_ref. Omit pv_ref only to select the current accepted pointer; name a
   candidate explicitly and continue to label it unaccepted. Bind subsequent
   reads to the returned project, PV, lane, bundle, pointer, SQLite/MMD/DOT
   hashes, and freshness evidence.
3. Call `lane_search` for one lane with the exact project/PV binding,
   `retrieval="hybrid"`, and `limit=20` unless the task contract requires a
   smaller value. The enforced range is 1 through 100 results and at most the
   first 12 lexical query terms. Use `bm25` or `tfidf` only when the user or
   task contract requires that ranking explicitly.
4. Call `lane_fetch` only with an exact `path` returned by search. Use
   `max_bytes=100000` unless a smaller task boundary applies; the enforced
   range is 1 through 1,000,000 bytes. Binary exact bytes remain inside SQLite.
5. Preserve, without relabelling, top-level project_id, pv_ref, canonical
   lane identity, lane/bundle/pointer hashes, authority state, and freshness.
   Preserve each hit's ref_id, path, locator, chunk_sha256, source_sha256, and
   parser_state; for a fetch also preserve sha256, size_bytes, truncated,
   structured facts, and freshness. `EMPTY` is a
   valid no-hit result. `STALE`, candidate, or dirty-live-source evidence stays
   visibly qualified and never becomes accepted truth by inference.

The exact diagnostic templates are:

- accepted lane SQLite:
  `<EVIDENCE_LANE_DATA_ROOT>/projects/<project_id>/accepted/<PVn>/lanes/<canonical_lane_id>/<sqlite_filename>`
- explicitly named candidate lane SQLite:
  `<EVIDENCE_LANE_DATA_ROOT>/projects/<project_id>/candidates/<candidate_id>/lanes/<canonical_lane_id>/<sqlite_filename>`

These templates verify returned provenance only. Do not use shell SQL, direct
filesystem discovery, arbitrary SQL, transcript search, browser history, or
scrollback as a substitute for `lane_catalog` -> `lane_status` ->
`lane_search` -> bounded `lane_fetch`. Fail closed on an invalid bundle,
project/PV/lane mismatch, missing exact path, invalid limit, or absent native
tool. Parallel, cross-lane, and cross-project reads require their separately
governed workflows; do not simulate them by widening this single-lane route.

## Schema-derived lane pills

When the user asks to add a new Source Intake pill, call
`source_intake_schema_configure` with `operation: ADD`, an exact visible
`pill_name`, a governed Source Intake `batch_id`, and a complete declarative
`schema_definition` whose title exactly matches the pill name and whose version
is `1`.

When the user asks to modify one, call the same tool with `operation: MODIFY`,
the next integer schema version, and `expected_previous_schema_sha256` bound to
the exact prior registered version. MODIFY is append-only: never edit or delete
the old definition, never mutate the canonical eighteen-lane registry, and
never treat schema configuration as source classification, a candidate build,
or HIL approval.

Suggested user forms:

- `/evi-source-intake ADD "<pill name>" --purpose "<need>" --schema <definition>`
- `/evi-source-intake MODIFY "<pill name>" --schema <next-version-definition> --previous-sha256 <exact-sha256>`

## MCP routing contract

Before the first MCP call, read `../evi/references/mcp-tool-routing.v1.json`
and use the ordered route for `evi-source-intake`. This route names the
schema, identity, SQLite, graph, Git-history, enrollment, and lane-query
primitives that are intentionally low-level. `MCP_ROUTING_FAIL_CLOSED`: if the
bundled `evidence-lane` dependency, an exact tool, or a required result is
missing or ambiguous, stop and report it; never rewrite prefixes, substitute a
tool, reorder a write, or infer success.
