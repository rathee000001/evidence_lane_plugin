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

`working_authority_action=REFRESH_WORKING_SECTORS` is the only explicit action
that materializes or refreshes the live WORKING sector projection. Invoke it as
a separate call with the exact active session. A `turn_entry` call is an
immutable query over that materialized projection and must never migrate,
delete, relocate, or refresh project authority. If the projection is absent or
bound to another branch/HEAD, the query returns
`PROJECT_WORKING_QUERY_REFRESH_REQUIRED`; perform the explicit refresh and then
repeat the read as a new call.

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

## Authoritative live-root six-authority query workflow

`EVIDENCE_LANE_LIVE_ROOT_SIX_AUTHORITY_QUERY_V1` is the only ordinary query
workflow. ENV/UOP keeps these six authorities separate: all eighteen live-root
sector lanes plus Project Engulf; Agent Learning; the Canon consequence graph;
Project Memory; the resolved `AGENTS.md` chain; and host conversation
`MEMORY.md`. Accepted storage is an immutable HIL ZIP and is never opened,
queried, extracted, or treated as the current database. The accepted pointer is
baseline identity only.

1. Call `lane_catalog` once for canonical lane identity, then call `search` for
   the bounded six-authority result. `search` always queries all eighteen live
   sector SQLite/FTS5 projections with BM25 in addition to Learning, Canon, and
   Memory. Preserve the AGENTS.md and MEMORY.md source-chain hashes separately;
   never merge their authority roles.
2. A stale or no-hit Learning, Canon, or Memory arm triggers exactly one
   ordered refresh: Learning, Canon, then Memory. Retry those three bounded
   reads exactly once. A continuing no-hit is valid and the all-eighteen-sector
   slice remains the direct fallback; never widen to the accepted ZIP.
3. Use `lane_status`, `lane_search`, and `lane_fetch` only when the caller needs
   one exact lane result. Omit every candidate or accepted-archive selector:
   ordinary lane reads resolve only
   `<project-root>/sectors/<canonical_lane_id>/<sqlite_filename>`. Candidate and
   accepted-archive reads belong only to their HIL presentation routes and
   fail closed on this workflow.
4. Use `fetch` only with an exact `file:<path>` or `chunk:<id>` returned by the
   live-root query. Preserve project, live-root bundle, branch/HEAD, lane,
   locator, content hash, freshness, ENV/UOP, and instruction source-chain
   receipts. Never load a full SQLite/FTS corpus, raw Plan, chat scrollback,
   browser history, or private reasoning into model context.

The root-nested `sectors/<lane>/accepted_history/<PVn>` folders are immutable
historical support bytes inside the live root. They may be searched only by the
sealed live-sector fallback after current paths are suppressed. They are not
the accepted archive and never replace current lane authority.

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
