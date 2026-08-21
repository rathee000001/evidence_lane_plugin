# Delta 001-051 complete ordered task list

This document records the exact append-only governed order read from
`task_backlog.json` at the v1.1 correction intake. It does not replace or rewrite
the detailed evidence in
[`DELTA_001_043_TRACEABILITY.md`](DELTA_001_043_TRACEABILITY.md),
[`DELTA_001_045_TRACEABILITY.md`](DELTA_001_045_TRACEABILITY.md), or
[`DELTA_001_046_TRACEABILITY.md`](DELTA_001_046_TRACEABILITY.md).

`ACCEPTED` means the task was mapped through an earlier accepted PV. `DONE`
means implementation evidence exists but remains outside accepted truth.
`QUEUED` means the bounded v1.1 correction was authorized by
`APPROVE_WITH_DELTA` but had not yet received its atomic completion receipt at
intake. No status authorizes this release to Fuse, move the PV2 generation-2
pointer, merge main, or invoke State Travel.

| Order | Intake state | Exact governed Delta ID |
| ---: | --- | --- |
| 001 | ACCEPTED | `EL-CODEX-PERSISTENT-LIFECYCLE-DELTA-003` |
| 002 | ACCEPTED | `EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002` |
| 003 | ACCEPTED | `EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001` |
| 004 | ACCEPTED | `EL-RELEASE-DOCS-TEST-COMMIT-DELTA-004` |
| 005 | ACCEPTED | `EL-GOVERNED-PUBLISH-REINSTALL-DEPLOY-DELTA-005` |
| 006 | ACCEPTED | `EL-CHAT-LINEAGE-UNIVERSAL-RUNTIME-DELTA-006` |
| 007 | ACCEPTED | `EL-ENV-UOP-VERIFIED-RUNTIME-PROJECTION-DELTA-007` |
| 008 | ACCEPTED | `EL-MULTI-SOURCE-AUTOROUTE-DELTA-008` |
| 009 | ACCEPTED | `EL-CHAT-LINEAGE-USAGE-METRICS-DELTA-009` |
| 010 | ACCEPTED | `EL-EXIT-SLIP-NEXT-ACTION-DELTA-010` |
| 011 | ACCEPTED | `EL-STATE-TRAVEL-FRESH-WINDOW-DELTA-011` |
| 012 | ACCEPTED | `EL-MODE-INTERSECTION-SIDECAR-DELTA-012` |
| 013 | ACCEPTED | `EL-LOCAL-DRIVE-CAPABILITY-ROUTING-DELTA-013` |
| 014 | ACCEPTED | `EL-PERSISTENT-ATOMIC-BOOT-FLASH-DELTA-014` |
| 015 | ACCEPTED | `EL-EVI-ORDERED-SURFACE-DELTA-015` |
| 016 | ACCEPTED | `EL-CODEX-STEER-CHATLINEAGE-DELTA-016` |
| 017 | ACCEPTED | `EL-STORAGE-CONNECTOR-POLICY-DELTA-017` |
| 018 | ACCEPTED | `EL-GOVERNED-ADDITIONAL-PLUGINS-DELTA-018` |
| 019 | ACCEPTED | `EL-CHATGPT-PUBLIC-CHROME-INSTALL-DELTA-019` |
| 020 | ACCEPTED | `EL-PLAN-LANE-AUTOMATIC-LIFECYCLE-DELTA-020` |
| 021 | ACCEPTED | `EL-GIT-HOST-PICKUP-BOOT-HIL-DELTA-021` |
| 022 | ACCEPTED | `EL-STATE-TRAVEL-SUCCESSOR-HASH-BRIDGE-DELTA-022` |
| 023 | ACCEPTED | `EL-SIX-CONTROL-AUTOROUTED-SURFACE-DELTA-023` |
| 024 | ACCEPTED | `EL-ATOMIC-BOOT-HOST-STORAGE-CAPABILITY-DELTA-024` |
| 025 | ACCEPTED | `EL-PROJECT-SECTOR-CHATLINEAGE-CANDIDATE-FANOUT-DELTA-025` |
| 026 | ACCEPTED | `EL-DYNAMIC-MODE-SOURCE-PLUGIN-CLASSIFICATION-DELTA-026` |
| 027 | ACCEPTED | `EL-ACTOR-MODEL-TOKEN-OUTPUT-TELEMETRY-DELTA-027` |
| 028 | ACCEPTED | `EL-IMPLICIT-APPROVAL-BY-CONTINUATION-DELTA-028` |
| 029 | ACCEPTED | `EL-FULL-GIT-HISTORY-BRAIN-DELTA-029` |
| 030 | ACCEPTED | `EL-CHUNK-INCREMENTAL-REFRESH-HISTORY-DELTA-030` |
| 031 | ACCEPTED | `EL-SEMANTIC-LANE-SCHEMA-MMD-TEST-DELTA-031` |
| 032 | ACCEPTED | `EL-MINI-BRAIN-FUSION-PROJECT-ROUTING-DELTA-032` |
| 033 | ACCEPTED | `EL-AI-TOOLCHAIN-CONNECTOR-BRAIN-DELTA-033` |
| 034 | ACCEPTED | `EL-HOST-SPECIFIC-REFRESH-OUTPUT-HANDOFF-DELTA-034` |
| 035 | ACCEPTED | `EL-CHATGPT-REMOTE-MCP-DURABLE-RUNTIME-DELTA-035` |
| 036 | ACCEPTED | `EL-RELEASE-DOCS-INSTALL-HIL-DELTA-036` |
| 037 | ACCEPTED | `EL-OPTIONAL-GIT-ARM-PROVENANCE-FALLBACK-DELTA-037` |
| 038 | ACCEPTED | `EL-TOLERANT-HIL-INTENT-CLASSIFIER-DELTA-038` |
| 039 | ACCEPTED | `EL-MIDTURN-STEER-CHATLINEAGE-DELTA-039` |
| 040 | ACCEPTED | `EL-INDEPENDENT-RND-NAMING-POC-DELTA-040` |
| 041 | ACCEPTED | `EL-VERCEL-ROUTE-RELEASE-IDENTITY-DELTA-041` |
| 042 | ACCEPTED | `EL-DETACHABLE-BOOT-PLUGIN-SIDECAR-BRANDING-DELTA-042` |
| 043 | ACCEPTED | `EL-DETERMINISTIC-PARALLEL-LANE-BUILD-REFRESH-DELTA-043` |
| 044 | ACCEPTED | `EL-CONNECTOR-SETTINGS-ROLE-SCHEMA-POLYGLOT-DELTA-044` |
| 045 | ACCEPTED | `EL-MCP-NATIVE-COLDSTART-LIFECYCLE-PERFORMANCE-DELTA-045` |
| 046 | DONE | `EL-CHATGPT-PUBLIC-RUNTIME-INSTALL-CLEANUP-HIL-DELTA-046` |
| 047 | QUEUED | `EL-SECRET-SAFE-TRACKED-SOURCE-BOUNDARY-DELTA-047` |
| 048 | QUEUED | `EL-SQLITE-MMD-DOT-RECONCILIATION-DELTA-048` |
| 049 | QUEUED | `EL-MULTIPAGE-ANIMATED-WEBSITE-DELTA-049` |
| 050 | QUEUED | `EL-V070-BASELINE-FULL-POSTINSTALL-POC-DELTA-050` |
| 051 | QUEUED | `EL-V110-EXACT-SHA-INSTALL-DEPLOY-AUDIT-HIL-DELTA-051` |

## v1.1 correction implementation contracts

| Order | Required evidence | Fail-closed verification boundary |
| ---: | --- | --- |
| 047 | tracked-only Git inventory; deterministic non-Git exclusions; shared path/content policy applied before current-source and Git-history writes; safe exclusion receipts | exact configured secret has zero byte occurrences across the sealed candidate; `.env`, `.runtime`, ignored, untracked, credential, private-key, and detected secret material is absent from SQLite/CAS/FTS/history/topology/package |
| 048 | SQLite-to-Mermaid and SQLite-to-DOT claims; exact Mermaid/DOT subgraph, node, and edge parity; bundle and forensic reconciliation reports | all 18 lanes pass; the known v0.9 six-line stub, understated SQLite count, dangling endpoint, parse failure, and MMD/DOT divergence fail |
| 049 | responsive Home, Architecture, Lanes, Proof, Provenance, Connect, Privacy, Terms, and Support pages using approved local brand assets and original CSS motion | production Next build, browser navigation, desktop/mobile visual review, reduced-motion support, no catch-all rewrite; `/mcp`, `/healthz`, and OAuth discovery remain the only adapter routes |
| 050 | full independent post-install POC with the complete v0.7 assessment depth and Evidence Lane-only questions | rendered DOCX page review; no outside comparison identity/questions; verified facts, candidate claims, blockers, decisive tests, and kill criteria remain distinct |
| 051 | one governed v1.1 commit and non-force push; exact remote parity; Codex exact-SHA install; Vercel same-SHA deployment; supported ChatGPT install/readback or exact blocker; dummy and real-Git audits; fresh sealed PV3 | no obsolete install removed before replacement proof; no false MCP-ready claim; no Fuse, pointer movement, main merge, or State Travel; stop at fresh unaccepted six-way HIL |

## Required release order

1. Complete and validate Deltas 047-049 in source.
2. Run static checks, the full suite, plugin/package validation, and the negative gates.
3. Create one governed v1.1 commit, push without force, and prove local/remote SHA parity.
4. Install that exact SHA in Codex and deploy the same SHA to the Vercel public site/ChatGPT edge.
5. Attempt the supported ChatGPT installation only after the remote edge is verified; preserve the older app until replacement readback succeeds.
6. Run the separate real-Git audit plus all 18 dummy lane audits from the installed release.
7. Produce and visually verify the full v0.7-baseline independent POC.
8. Atomically record completion evidence for 047-051, seal a fresh **UNACCEPTED** PV3, and stop at HIL.

## Decision boundary

The v1.1 completion receipt may move 047-051 only to `DONE_PENDING_HIL` and
keep 046 unaccepted with them. `APPROVE_WITH_DELTA` authorizes correction, not
promotion. Only a later exact case-sensitive `APPROVE` bound to the fresh
displayed candidate may authorize Fuse.
