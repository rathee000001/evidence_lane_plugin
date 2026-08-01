# Delta 001-042 implementation traceability

This matrix follows the immutable backlog order. `IMPLEMENTED` means the
bounded source behavior and its local verification exist in this branch. It
does not mean a candidate is accepted, a pointer moved, a remote host is
healthy, a compromised key was revoked, or State Travel occurred. Those remain
separate release/HIL evidence.

| Order | Delta | Implementation evidence | Verification boundary |
| ---: | --- | --- | --- |
| 001 | `EL-CODEX-PERSISTENT-LIFECYCLE-DELTA-003` | persistent `SessionManager`, `RuntimeActivation`, next-action/HIL contracts | lifecycle, restart, detach/reattach tests |
| 002 | `EVIDENCE-LANE-PLUGIN-READONLY-COMPARISON-FORENSIC-DELTA-002` | `REFERENCE_RECONCILIATION.md` and host-owned composer boundary | read-only comparison; no foreign source copied |
| 003 | `EVIDENCE-LANE-PLUGIN-CODEX-COMMANDS-CHATGPT-PARITY-DELTA-001` | command/skill inventory, stable MCP tools, host matrix | plugin validator and MCP inventory tests |
| 004 | `EL-RELEASE-DOCS-TEST-COMMIT-DELTA-004` | README, architecture, runbooks, traceability, schemas, tests | final commit/push and HIL evidence supplied after source freeze |
| 005 | `EL-GOVERNED-PUBLISH-REINSTALL-DEPLOY-DELTA-005` | governed remote Git action, exact-SHA identity, Git marketplace and ChatGPT-only adapter contracts | live push/install/preview evidence is post-commit; acceptance is not inferred |
| 006 | `EL-CHAT-LINEAGE-UNIVERSAL-RUNTIME-DELTA-006` | session JSONL chain, sibling SQLite/FTS, project-wide state-hash head, prompt/pointer/file/source fields | integrity/FK/FTS and restart tests; no arbitrary-model read claim |
| 007 | `EL-ENV-UOP-VERIFIED-RUNTIME-PROJECTION-DELTA-007` | `FlashRuntimeProjection`, digest/schema/host-ABI key, immutable source verification | locked hashes/SQLite tests; mutable bundles rebuild, installed caches verify before reuse |
| 008 | `EL-MULTI-SOURCE-AUTOROUTE-DELTA-008` | deterministic ordered multi-source routing and explicit overrides | all-18 routing/ambiguity/idempotency tests |
| 009 | `EL-CHAT-LINEAGE-USAGE-METRICS-DELTA-009` | host-supplied model/submodel/token fields or explicit `UNAVAILABLE` | hook and lineage tests; no fabricated telemetry |
| 010 | `EL-EXIT-SLIP-NEXT-ACTION-DELTA-010` | state-derived `/evi` suggestion in Exit Slip/HIL | host-specific output tests; no composer mutation/submit claim |
| 011 | `EL-STATE-TRAVEL-FRESH-WINDOW-DELTA-011` | sealed handoff and fresh-host verification APIs | guarded tests only; this release performs no State Travel |
| 012 | `EL-MODE-INTERSECTION-SIDECAR-DELTA-012` | one ordered/custom Mode classifier preserving prior state | operating-mode and Chat Lineage tests |
| 013 | `EL-LOCAL-DRIVE-CAPABILITY-ROUTING-DELTA-013` | capability-based local/durable routing; Drive sealed-artifact mirror | host matrix and fail-closed persistence tests |
| 014 | `EL-PERSISTENT-ATOMIC-BOOT-FLASH-DELTA-014` | doctor + Flash + storage + boot/resume; no separate Flash command | boot/Flash/session/restart tests |
| 015 | `EL-EVI-ORDERED-SURFACE-DELTA-015` | `/evi` namespace with conditional State Travel and exact lifecycle order | stale-command and exact inventory tests |
| 016 | `EL-CODEX-STEER-CHATLINEAGE-DELTA-016` | host steer detection, ordered/idempotent redacted event append | hook tests; unavailable host events are not fabricated |
| 017 | `EL-STORAGE-CONNECTOR-POLICY-DELTA-017` | `/evi-storage` plus `/evi-change-storage-connector`, append-only exact-token selection | local/ephemeral failure tests; credentials never stored |
| 018 | `EL-GOVERNED-ADDITIONAL-PLUGINS-DELTA-018` | `/evi-plugin`, add/drop compatibility sidecars, max-eight grant brain | purpose/action/scope/expiry, route, exact drop, FTS tests |
| 019 | `EL-CHATGPT-PUBLIC-CHROME-INSTALL-DELTA-019` | public `/mcp` adapter and signed-in Chrome connection procedure | working install requires durable origin + OAuth + exact SHA; otherwise explicit blocker |
| 020 | `EL-PLAN-LANE-AUTOMATIC-LIFECYCLE-DELTA-020` | append-only universal Delta event law and Plan SQLite projection | transition, supersession, projection, batch tests |
| 021 | `EL-GIT-HOST-PICKUP-BOOT-HIL-DELTA-021` | installed-root engine identity, atomic startup, stable Git/local intake | cachebuster, process restart, stdio and lifecycle tests |
| 022 | `EL-STATE-TRAVEL-SUCCESSOR-HASH-BRIDGE-DELTA-022` | append-only successor builder with legacy expected hash and original before/after tree proof | idempotency/path-overlap/original-immutability tests; actual addendum built after exact SHA/candidate |
| 023 | `EL-SIX-CONTROL-AUTOROUTED-SURFACE-DELTA-023` | exactly Boot, Rollback, Build, Refresh, Mode, Source Intake | command ordering/inventory tests; admin sidecars are outside the six |
| 024 | `EL-ATOMIC-BOOT-HOST-STORAGE-CAPABILITY-DELTA-024` | atomic host kind, durability, Flash, session and selected-route flow | local, remote/ephemeral and missing-connector gates |
| 025 | `EL-PROJECT-SECTOR-CHATLINEAGE-CANDIDATE-FANOUT-DELTA-025` | candidate-only overlay with visible prompt/output/tool/file/test/build fan-out | overlay FKs/FTS and `accepted_sector_truth=0` tests |
| 026 | `EL-DYNAMIC-MODE-SOURCE-PLUGIN-CLASSIFICATION-DELTA-026` | generalized Source Intake, Mode/custom intersections, connector lane grants | all 18 lanes + Project Engulf + plugin routing tests |
| 027 | `EL-ACTOR-MODEL-TOKEN-OUTPUT-TELEMETRY-DELTA-027` | privacy-minimized actor/model/usage/output/hash/pointer fields | lineage schema/FTS/redaction tests |
| 028 | `EL-IMPLICIT-APPROVAL-BY-CONTINUATION-DELTA-028` | tolerant intent classifier returns safe next action but never Fuse | typo/continuation tests and exact-case `APPROVE` gate |
| 029 | `EL-FULL-GIT-HISTORY-BRAIN-DELTA-029` | reachable commits/refs/changes/blobs/history FTS, no remote write | two-commit and reuse/history query tests |
| 030 | `EL-CHUNK-INCREMENTAL-REFRESH-HISTORY-DELTA-030` | content/chunk CAS, occurrence history, changed-section refresh, fallback receipts | multi-refresh reuse/change/tombstone tests |
| 031 | `EL-SEMANTIC-LANE-SCHEMA-MMD-TEST-DELTA-031` | lane-specific SQLite/FTS plus required MMD/DOT/pointer/refresh/manifest artifacts | 18-lane dummy matrix and forensic reports |
| 032 | `EL-MINI-BRAIN-FUSION-PROJECT-ROUTING-DELTA-032` | deterministic candidate overlay/fusion routes and explicit fallback | candidate isolation and pointer-only rollback tests |
| 033 | `EL-AI-TOOLCHAIN-CONNECTOR-BRAIN-DELTA-033` | append-only grant/event/route brain, max eight, purpose/scope/expiry | SQLite/FK/FTS, deterministic route and exact-drop tests |
| 034 | `EL-HOST-SPECIFIC-REFRESH-OUTPUT-HANDOFF-DELTA-034` | Codex direct and user-mediated output confirmation receipts | host-output and no-auto-submit tests |
| 035 | `EL-CHATGPT-REMOTE-MCP-DURABLE-RUNTIME-DELTA-035` | Vercel adapter only; durable origin owns OAuth, one-writer state and queue | missing durable origin fails closed; Codex stays local/Git-backed |
| 036 | `EL-RELEASE-DOCS-INSTALL-HIL-DELTA-036` | manifests, docs, hooks, package validation, forensic/audit/addendum tooling | exact-SHA push/install/preview/candidate evidence completed after commit, then six-way HIL |
| 037 | `EL-OPTIONAL-GIT-ARM-PROVENANCE-FALLBACK-DELTA-037` | `AUTO`/`REQUIRED`/`DISABLED` Git arm with explicit fallback | history-present, no-Git fallback and required-failure tests |
| 038 | `EL-TOLERANT-HIL-INTENT-CLASSIFIER-DELTA-038` | natural continuation classification including “pursue same HIL” | intent tests; classifier cannot promote |
| 039 | `EL-MIDTURN-STEER-CHATLINEAGE-DELTA-039` | distinct prompt-indexed steer events and redaction | ordering/idempotency/private-reasoning tests |
| 040 | `EL-INDEPENDENT-RND-NAMING-POC-DELTA-040` | independent Evidence Lane R&D method/report generator boundary | no third-party identity or borrowed questions; real audit report built after install |
| 041 | `EL-VERCEL-ROUTE-RELEASE-IDENTITY-DELTA-041` | public path recovery, root descriptor, exact Git/expected/origin SHA checks | adapter route/health/fail-closed tests and final preview probes |
| 042 | `EL-DETACHABLE-BOOT-PLUGIN-SIDECAR-BRANDING-DELTA-042` | runtime detach/reattach, admin sidecars, supplied logo/icon, proprietary rights and accurate credits | plugin validation, runtime hook tests, asset hashes, README/license review |

## Decision boundary

The batch implementation receipt may move these tasks only to `DONE`. It does
not map them to `ACCEPTED`, does not accept the new PV candidate, and does not
move the accepted pointer. The six-way HIL remains mandatory; only a later exact
case-sensitive `APPROVE` may authorize Fuse.
