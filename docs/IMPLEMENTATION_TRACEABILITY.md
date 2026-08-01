# Implementation traceability

| Requirement | Primary implementation | Verification |
| --- | --- | --- |
| Six public controls and user-timed State Travel | `commands/`, `skills/`, `next_actions.py`, `session.py`, startup hook | exact surface inventory, no-auto-travel contract, same-host receipt supersession, changed-host bypass rejection, and stale-command scan |
| Atomic host/storage-aware Boot and persistent session | `service.py`, `session.py`, `persistence.py`, Flash authority | doctor/Flash/resume, ephemeral fail-closed, Exit Boot tests |
| Generalized Source Intake, optional Git arm, and separate custom-capable Mode | `source_intake.py`, `git_optional.py`, `operating_modes.py` | AUTO fallback, REQUIRED/DISABLED, linked-worktree, all-18 override, and custom-mode tests |
| Eighteen lane brains and Project Engulf | `lanes.py`, `lane_engine.py` | real dummy sources for all lanes, SQLite/FK/FTS/MMD/DOT validation |
| Full Git-history brain and CAS reuse | `git_history.py`, lane schema | reachable-commit/change/blob/chunk/FTS and second-index reuse tests |
| Changed-section incremental refresh | `ingest.py`, lane CAS/history tables | retained chunk reuse plus changed-only reindex test |
| Candidate project-sector overlays | `project_overlay.py`, `pv_package.py` | candidate-only truth, fan-out, integrity/FK/FTS tests |
| Visible private-safe Chat Lineage | hooks, `lineage.py` | prompt plus multi-steer ordering/idempotency, chain hashes, secret redaction, actor/model/token fields, private-reasoning rejection |
| Bounded connector/plugin governance | `connector_governance.py` | eight-active limit, deterministic route, exact drop, history/FTS tests |
| Host-specific output handoff | `next_actions.py`, Exit Slip | Codex-local versus user-mediated confirmation tests |
| Tolerant HIL intent with exact Fuse boundary | `hil_intent.py`, `service.py`, `mcp_server.py` | typo/continuation classification without pointer movement; dedicated Fuse remains exact-case sensitive |
| ChatGPT-only Vercel adapter | `remote_adapter/` | rewritten public path recovery; missing origin/SHA fail closed; exact release/origin contract |
| Security release gate | `SECURITY.md`, `.env.example` | secret scan and manual compromised-key revocation blocker |
