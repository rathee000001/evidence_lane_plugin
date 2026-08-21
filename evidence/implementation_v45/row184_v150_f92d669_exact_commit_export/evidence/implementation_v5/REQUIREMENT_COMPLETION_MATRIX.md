# Evidence Lane objective completion matrix

Recorded: `2026-07-26T11:14:58Z`

This matrix audits the full user objective. A passing local test is not used as
proof for an unobserved Codex or ChatGPT host surface.

| ID | Requirement | Authoritative evidence | Disposition |
| --- | --- | --- | --- |
| R01 | Preserve F-drive as sole product authority and fuse the recorded V1, V3, and current-app inputs. | The 10 F-drive inputs in `13_ADDITIONAL_LINEAR_DELTAS/05_SOURCE_INTAKE_AUDIT.json` were re-hashed; 10/10 still match. | `PASS_CURRENT_SOURCE` |
| R02 | One immutable registry contains exactly the 18 canonical lanes. | Installed `lane_catalog` returns 18, `single_registry=true`; duplicate IDs and ambiguous aliases fail closed. | `PASS` |
| R03 | Every lane follows code-lane governance and emits SQLite, MMD, DOT, pointer, tool, receipt, and manifest artifacts. | `test_all_eighteen_lanes_emit_full_contract_and_fixture_facts` verifies all required members and recursively validates the bundle. | `PASS` |
| R04 | Preserve lane-specific payloads rather than forcing all evidence into code tables. | Each registry entry declares its own SQLite/FTS/schema contract; current counts range from 17 to 45 schema members. | `PASS` |
| R05 | `/pdf` provides native extraction, scanned-page OCR, FTS5/BM25, TF-IDF, provenance, and honest blockers. | Native PDF and scanned-PDF RapidOCR/ONNX fixtures pass; deterministic BM25/TF-IDF and parser-state assertions pass. | `PASS_LOCAL` |
| R06 | `/excel` implements workbook, sheet, range, cell, formula, cached value, dependency, table, chart, CSV/JSON/JSONL/Parquet behavior. | XLSX/CSV/JSON and real PyArrow Parquet fixtures pass; exact tool blockers are tested when optional engines are absent. | `PASS_LOCAL` |
| R07 | Docs, PPT, image OCR, brain loader, SQLite brain, archives, research, and remaining lanes preserve structure and exact bytes. | High-fidelity DOCX, PPTX, image OCR, read-only SQLite, safe archive, and parse-failure fixtures pass. | `PASS_LOCAL` |
| R08 | Root `/` commands live in the plugin command folder and resolve through the canonical registry. | 28 source and installed command files match; `/code` requires one mode, and `/excel`/`/pdf` call `lane_catalog` rather than carrying alias tables. | `PASS_INSTALLED_FILES` |
| R09 | PV1 is the only normal heavy build; PV2+ reuses unchanged bytes and rebuilds changed/new/tombstoned lanes. | Full-build, all-lane byte reuse, routed incremental rebuild, tombstone, and tamper tests pass. | `PASS` |
| R10 | Exact `APPROVE` is the only fusion/promotion action; no separate fuse exists. | Transition law, non-approve pointer tests, and MCP inventory prove only `APPROVE` calls promotion; no fuse tool exists. | `PASS` |
| R11 | A later prompt loads its accepted entry PV directly without remaking it. | A new Python process opens the same disposable store at PV1, returns `CLASSIFY_ONE_TASK`, retains PV1 bytes, and reports PV2 as the next candidate. | `PASS_CROSS_PROCESS` |
| R12 | Plugin/cache replacement must not erase the user-owned store; SessionStart is bounded and read-only. | Two staged cachebuster roots read the same accepted PV1, pointer generation, seal, history, and next PV2; complete store hashes are identical before and after both hooks. | `PASS_CACHE_REPLACEMENT` |
| R13 | Rollback can target any accepted PV; bare rollback uses the session entry PV; same/invalid/stale targets follow exact law. | Backward, forward, bare-entry, same-target, invalid-target, stale-generation, and next-ordinal assertions pass. | `PASS` |
| R14 | Rollback moves only the pointer and preserves accepted bytes, history, candidate evidence, and live source. | Recursive hashes of PV1 and PV2 are identical before/after travel; receipts show `history_preserved=true`, `live_source_rewritten=false`, and candidate preservation. | `PASS_BYTE_IMMUTABLE` |
| R15 | Rolled-back reads must distinguish immutable entry truth from dirty or stale live truth. | A dirty live worktree yields `status=STALE`, `live_truth_status=DIRTY_WORKING_TREE`, while the accepted PV remains current pointer truth. | `PASS` |
| R16 | One lifecycle transition law governs all tools and six visible HIL outcomes. | Installed transition-law probe passes with `single_authority=true`; command and skill text expose all six outcomes. | `PASS` |
| R17 | Learn from Claude reference commit 8 without importing code-only/full-rebuild/bare-previous rollback weaknesses. | Commit `f10a546...` remains the bounded reference in Delta 006. Remote `main` has advanced to `889c28...`; later commits were not inspected or imported. | `PASS_BOUNDED_REFERENCE` |
| R18 | Current cachebuster must be discovered in a genuinely fresh Codex task, including native commands and current MCP tools. | Installed cache and standalone STDIO prove 28 commands and 31 tools, but this task still exposes the older cached skill/server. | `EXTERNAL_UNPROVEN_FRESH_TASK_REQUIRED` |
| R19 | Match the visible smooth prompt/session experience. | The plugin-owned SessionStart/status envelope is proven across cache replacement; native prompt-bar chrome control has no proven host API and was not claimed. | `PARTIAL_HOST_BOUNDARY` |
| R20 | Preserve full ChatGPT write-capable parity if it remains part of the accepted product boundary. | No write-capable ChatGPT parity run or installation has occurred. | `EXTERNAL_UNPROVEN` |
| R21 | Preserve explicit human HIL authority. | Candidate is uncommitted and unpromoted; no pointer or real PV changed. | `HIL_PENDING` |

## Audit verdict

`FIX-THEN-PURSUE`, confidence `0.99`.

All locally implementable requirements now have direct current evidence. The
full objective is not complete until a fresh Codex task proves the installed
native command/current MCP pickup and the user supplies one explicit six-way
HIL decision. If cross-host parity remains mandatory, ChatGPT write parity is
an additional external proof.
