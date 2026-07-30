# Implementation traceability

| Required law | Implemented source | Verification |
| --- | --- | --- |
| One immutable 18-lane registry and alias law | `lanes.py` | count, duplicate-ID, ambiguous-alias, and command inventory tests |
| Every lane has SQLite, MMD, DOT, tools, pointer, manifest, and Refresh evidence | `lane_engine.py`, `pv_package.py` | all-18 artifact fixture, recursive lane-bundle validation, and tamper tests |
| One source routes to exactly one lane; named overrides re-lock | `lanes.py`, `lane_engine.py`, `session.py` | override, inheritance, and invalid-route tests |
| State Travel first; one atomic Boot/Flash; 17 source-intake commands; one separate Mode sidecar; then Build PV Entry | commands, matching native skills, lifecycle skill, `operating_modes.py`, `session.py` | exact-order, command/skill parity, mode-intersection, resume, and code-mode tests |
| Internal `brain_loader` is presented as SQLite PV Candidate Loader | `lanes.py`, `/evi-04-*` command and skill | display-label, alias-compatibility, and ordered-surface tests |
| Exact source bytes plus structured code facts | `ingest.py`, `lane_engine.py`, `database.py` | byte, symbol, import, route, and dependency tests |
| Full spreadsheet range/cell/formula/dependency/table/chart plus CSV, JSON/JSONL, and Parquet evidence | `lane_engine.py` | XLSX, CSV, JSON, and real PyArrow Parquet fixtures |
| PDF native text, scanned-page OCR, image OCR, and exact capability blockers | `lane_engine.py` | real PyMuPDF and RapidOCR/ONNX fixtures plus dependency-minimal blocker fixture |
| PPT slide/notes/shape/text/table/image/relationship evidence | `lane_engine.py` | OpenXML presentation fixture |
| FTS5/BM25 and materialized TF-IDF across lanes | `lane_engine.py`, `lane_reader.py` | repeated deterministic BM25/TF-IDF ordering and search tests |
| Read-only SQLite/brain and brain-package inspection | `lane_engine.py` | immutable schema/FK/FTS/integrity and archive fixtures |
| Chat prompt/response commits and deterministic state hash chain | `lane_engine.py` | two-turn chat-lineage fixture |
| Candidate queries never masquerade as accepted truth | `reader.py`, `lane_reader.py` | authority-label tests |
| PV1-only full build | `lane_engine.py`, `engine.py`, `session.py` | PV1 build-mode assertions |
| PV2+ byte reuse, changed-lane rebuild, and tombstones | `lane_engine.py` | PV2/PV3 incremental fixture |
| Recursive package membership and checksum authority | `pv_package.py`, `lane_engine.py`, `store.py` | exact-set, hash, bundle-seal, and tamper tests |
| Accepted PV loads directly in the next host task | `session.py`, session-start hook | recreated-session entry assertions |
| Exact APPROVE Fuse performs byte-preserving promotion and seals fresh-window State Travel | `service.py`, `session.py`, `store.py` | Fuse, no-remake, and prepared-handoff assertions |
| State Travel enters through a new Codex task or ChatGPT chat, verifies atomic Boot/Flash/pointer/seals, then waits | `/evi-00-*`, `service.py`, `session.py`, `mcp_server.py` | stale-pointer, same-host-window denial, fresh-window entry, and wait-state tests |
| Booted governed session persists across host tasks until explicit Exit Boot | `/evi-01-*`, `/evi-exit-boot`, `session.py`, startup hook | cross-host resume, bounded close, preserved installation/Flash/pointer, and post-close denial tests |
| One Mode sidecar classifies intersections without lifecycle mutation | `/evi-mode`, `operating_modes.py`, `service.py`, `session.py` | explicit/inferred intersection, recursive code guard, no-match failure, privacy-minimized lineage receipt, and pointer-preservation tests |
| Initial APPROVE_WITH_DELTA reseals PV1 without inventing an accepted parent | `state_law.py`, `session.py` | preserved-candidate, generation-zero, corrected-PV1 test |
| Prompt/turn rollback index stores no raw prompt | `prompt_index.py`, prompt hook | redaction, record-hash, and indexed rollback tests |
| Persistent user-owned store survives plugin cache replacement | `service.py`, `store.py`, hook | external-store and restart tests |
| Highest accepted history controls next ordinal after rollback | `store.py`, `session.py` | backward/forward/default rollback lifecycle test |
| One executable transition law | `state_law.py`, `session.py` | transition-catalog and lifecycle tests |
| One writer, one active task, ordered waiting backlog | `tasking.py`, `store.py`, `session.py` | backlog claim and conflict tests |
| Planning and classification use the same validator | `tasking.py`, `store.py` | unsupported-tool rejection before backlog persistence |
| Exact executable acceptance checks; prose remains human pending | `acceptance.py`, `session.py` | pass, pending, and source-mutation tests |
| Task completion automatically confirms source, Refreshes, seals exit PV, and stops at HIL | `service.py`, `mcp_server.py`, `session.py` | automatic completion/Refresh and entry/exit-slip tests |
| Neutral next action survives hosts that cannot render gray composer suggestions | `next_actions.py`, `engine.py`, `service.py`, `session.py`, SessionStart hook | Exit Slip, MCP result, State Travel, no-auto-submit, and command-remnant tests |
| Six exact HIL outcomes | `models.py`, `session.py` | parameterized lifecycle tests |
| Only exact `APPROVE` promotes candidate bytes | `session.py`, `store.py` | five non-promotion outcome assertions |
| Rollback moves only accepted pointer and preserves candidates/source/history | `session.py`, `store.py` | CAS, no-op, invalid-target, and preservation tests |
| Durable decisions, accepted PVs, and immutable pointer snapshots | `persistence.py`, `service.py` | encrypted fake-backend and pointer-seal tests |
| Automatic visible ChatLineage | `lineage.py`, `session.py` | lifecycle and redaction tests |
| Runtime/declarative version hygiene | `constants.py`, manifests | version-parity test |
| Local adoption and credential-free HTTPS enrollment refuse overwrite | `enrollment.py`, `service.py` | local enrollment and conflict tests |
| Selected Git sync permits only clean path-bounded fast-forward | `enrollment.py`, `service.py` | local-source fast-forward test |
| Host aliases and capability-axis persistence routing | `models.py`, `persistence.py`, `service.py` | alias, capability-matrix, ephemeral fail-closed, and durable-local no-Drive-call tests |
| Required Google Drive connector uses normal host OAuth boundary | `.app.json`, manifest, docs | app-manifest packaging test |
| Separate remote Git authority | `remote_git.py`, `git_adapter.py` | wrong-token denial test |
| Canonical MCP tools and root command files agree | `mcp_server.py`, `commands/` | inventory, annotation, command-discovery, and real STDIO tests |
| Exact locked ENV/UOP flash stays outside PV | `flash_authority.py`, `session_flash/` | hash, SQLite, tamper, and PV-exclusion tests |
| A Git marketplace snapshot installs without the authority checkout | plugin-local `pyproject.toml`, `bootstrap.py`, `run_mcp.py` | isolated no-venv bootstrap, non-editable import path, and marketplace identity tests |
| Remote MCP fails closed and exposes the OAuth discovery boundary | `auth.py`, `mcp_server.py`, `.env.example` | algorithm/scope/JWT tests plus health, protected-resource metadata, and 401 challenge smoke checks |
| One-replica durable container runtime is deployable by an authorized host | `Dockerfile`, `.dockerignore`, `REMOTE_DEPLOYMENT.md` | locked dependency build and local container health/MCP smoke check |

## Deliberately unproved or externally blocked

- a real project boot, PV candidate, acceptance, or pointer move;
- a governed Google Drive mirror upload, readback, and hash verification;
- a registered and exercised ChatGPT web Secure MCP Tunnel or public remote MCP
  connection;
- live public HTTPS hosting, mounted durable storage, and an external OAuth
  issuer round trip;
- a production signing key and release signature;
- a live governed application-source push through the plugin's separate
  remote-Git action;
- host-rendered gray Suggested prompts in a fresh Codex or ChatGPT window. The
  documented plugin/MCP/hook contracts do not expose composer mutation; the
  sealed Exit Slip and visible MCP `suggested_next_prompt` are the portable
  fallback.

These are not represented as completed.
