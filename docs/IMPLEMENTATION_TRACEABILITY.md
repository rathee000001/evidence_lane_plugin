# Implementation traceability

| Requirement | Primary implementation | Verification |
| --- | --- | --- |
| Six public controls and conditional State Travel | `commands/`, `skills/`, `next_actions.py`, startup hook | exact surface inventory and stale-command scan |
| Atomic host/storage-aware Boot and persistent session | `service.py`, `session.py`, `persistence.py`, Flash authority | doctor/Flash/resume, ephemeral fail-closed, Exit Boot tests |
| Generalized Source Intake and separate custom-capable Mode | `source_intake.py`, `operating_modes.py` | all-18 override classification and ordered custom-mode tests |
| Eighteen lane brains and Project Engulf | `lanes.py`, `lane_engine.py` | real dummy sources for all lanes, SQLite/FK/FTS/MMD/DOT validation |
| Full Git-history brain and CAS reuse | `git_history.py`, lane schema | reachable-commit/change/blob/chunk/FTS and second-index reuse tests |
| Changed-section incremental refresh | `ingest.py`, lane CAS/history tables | retained chunk reuse plus changed-only reindex test |
| Candidate project-sector overlays | `project_overlay.py`, `pv_package.py` | candidate-only truth, fan-out, integrity/FK/FTS tests |
| Visible private-safe Chat Lineage | hooks, `lineage.py` | chain hashes, secret redaction, actor/model/token fields, private-reasoning rejection |
| Bounded connector/plugin governance | `connector_governance.py` | eight-active limit, deterministic route, exact drop, history/FTS tests |
| Host-specific output handoff | `next_actions.py`, Exit Slip | Codex-local versus user-mediated confirmation tests |
| Exact Fuse boundary | `service.py`, `mcp_server.py` | public HIL API rejects APPROVE; dedicated Fuse remains exact-case sensitive |
| ChatGPT-only Vercel adapter | `remote_adapter/` | missing origin/SHA fail closed; exact release/origin contract |
| Security release gate | `SECURITY.md`, `.env.example` | secret scan and manual compromised-key revocation blocker |
