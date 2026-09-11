# Code

Code has separate Local Code and GitHub Code databases. Source registration
does not build a Code index. Read `code_current`, then use `code_index` through
an exact Delta contract for selected working-tree paths. `code_index_syntax`
requires verified shared native grammars. For GitHub Code, register Sources
Git history first and use `code_index_git` with that exact clean checkpoint;
the indexed bytes come from its Git blobs, preserving checkout conversions.
Use `code_query`, `code_read` and `code_impact` on an exact snapshot. Impact
describes bounded static import paths, with unresolved edges disclosed.
`code_query` supports literal `match_mode=all` or `any` and a parser `receipts`
collection. Use `pv_summary` for verified counts and changes of one exact Code
snapshot. `fetch` reads `file:<relative path>` or the exact `chunk:<digest>`
returned by a Code query. Text uses bounded line windows; binary uses base64
pages with `byte_offset` and `next_byte_offset`. Stored snapshot bytes do not
establish current source freshness. Use the existing source verification or
Code refresh action when the question requires current files.

`code_apply` parses one proposed UTF-8 Local Code replacement before its
before-hash journaled write. It automatically renews the bounded local Sources
observations and every affected current Local Code scope, retaining each
scope's paths, parser, limits and route selection. Shared parser results are
reused only for the same syntax choice. Already selected Code exports retain
their existing scope and file roles. Use the returned scope snapshots for
subsequent reads or edits; no separate
refresh command is needed for a successful edit. Source roots must fit the
Plan path grant and the bounded capture. Unavailable parsers or exporters stop
before writing. A failure after a confirmed write preserves its effect journal
and leaves the Delta incomplete; reconcile the recorded state without replay.
The automatic edit examines at most 128 current scopes and 16 MiB of their
metadata, refreshes at most 32 affected scopes, and captures at most 512 distinct
source files and 32 MiB across their source observations. A narrower Plan grant,
stale scope or insufficient parser/exporter budget stops before the file write.
GitHub Code uses `code_refresh_git` at a new selected Sources checkpoint.
Local Code refresh checks the complete selected membership and source hashes,
reuses verified unchanged file versions only under the same parser/runtime
identity, and reparses changed or added files. A changed parser identity forces
reparsing. The result reports measured reuse and parser calls. Other Code
scopes, historical snapshots and GitHub Code checkpoints remain separate.
Current content policy is checked before reuse. A refresh with the same index
contents preserves that snapshot and its view files; a separate receipt binds
the current observation to this job.
Optional `code_semantic_index` adds a bounded, pinned-model chunk page.
`code_semantic_query`, `code_semantic_query_vec` and `code_semantic_query_faiss`
rank that exact page with Python cosine, sqlite-vec or an ephemeral FAISS CPU
index respectively. Select a ready route; model and vector hashes must match.
FTS and exact source locators remain available. Missing assets block the model
route. Queries do not install, download, refresh or change the Plan.
Generate Code MMD/DOT/pointer exports only for a requested consumer through
`lane_view_preview` and `lane_view_refresh`; use scope.query for an exact Code
scope ID when the project has several indexed source selections.
