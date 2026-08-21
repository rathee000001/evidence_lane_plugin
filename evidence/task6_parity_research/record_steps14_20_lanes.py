"""Record lane registry, schema, artifact, and query parity for Steps 14-20."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from research_db import (
    append_audit_event,
    connect,
    sha256_file,
    transition_step,
    upsert_fts,
    utc_now,
)


WORKSPACE = Path(r"F:\test codex")
SOURCE = WORKSPACE / "plugins" / "evidence-lane-plugin"
PACKAGE = SOURCE / "src" / "evidence_lane_plugin"
PV12 = Path(
    r"C:\Users\rathe\EvidenceLanePV\projects\test-codex-evidence-lane-plugin"
    r"\accepted\PV12"
)
TASK3_SQLITE = Path(
    r"D:\EvidenceLane\evidenmce_lane_plugin_task_3_chat_lenaghe_output"
    r"\project\sectors\chat_lineage\chat_lineage_sector_v001.sqlite"
)
LIFE_ARC_SQLITE = Path(
    r"C:\Users\rathe\Downloads\LIFE_ARC_CHAT_LINEAGE_V153_THROUGH_ISLAND4288_20260814(1).sqlite"
)
LIFE_ARC_ZIP = Path(
    r"C:\Users\rathe\Downloads\Compressed"
    r"\LIFE_ARC_FULL_STATE_TRAVEL_DB_PACKAGE_V153_THROUGH_ISLAND4288_20260814(1).zip"
)


def sqlite_profile(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT name,type,ncol,strict FROM pragma_table_list "
            "WHERE schema='main' AND name NOT LIKE 'sqlite_%' AND type!='shadow' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for row in rows:
        try:
            quoted = str(row["name"]).replace('"', '""')
            counts[str(row["name"])] = int(
                connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
            )
        except sqlite3.Error:
            continue
    quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    connection.close()
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "quick_check": quick,
        "logical_table_count": len(rows),
        "strict_table_count": sum(int(row["strict"]) for row in rows),
        "virtual_table_count": sum(row["type"] == "virtual" for row in rows),
        "column_count": sum(int(row["ncol"]) for row in rows),
        "row_count_sum": sum(counts.values()),
        "nonempty_table_count": sum(value > 0 for value in counts.values()),
        "counts": counts,
    }


def main() -> None:
    now = utc_now()
    lane_engine = PACKAGE / "lane_engine.py"
    lanes_file = PACKAGE / "lanes.py"
    lane_reader = PACKAGE / "lane_reader.py"
    artifact_contract = PACKAGE / "artifact_contract.py"
    custom_schema = PACKAGE / "custom_source_schema.py"
    schema_assets = sorted((PACKAGE / "schemas").glob("*.json"))

    pv12_lane_root = PV12 / "lanes"
    accepted_lane_dirs = sorted(path for path in pv12_lane_root.iterdir() if path.is_dir())
    accepted_file_counts = {
        path.name: len([item for item in path.iterdir() if item.is_file()])
        for path in accepted_lane_dirs
    }
    accepted_file_name_sets = {
        path.name: sorted(item.name for item in path.iterdir() if item.is_file())
        for path in accepted_lane_dirs
    }
    dummy_root = SOURCE / "remote_adapter" / "public" / "dummy-lane-packages"
    dummy_lane_dirs = sorted(path for path in dummy_root.iterdir() if path.is_dir())
    dummy_file_counts = {
        path.name: len([item for item in path.iterdir() if item.is_file()])
        for path in dummy_lane_dirs
    }

    with zipfile.ZipFile(LIFE_ARC_ZIP) as archive:
        zip_names = [name for name in archive.namelist() if not name.endswith("/")]
        sector_counts: dict[str, int] = {}
        prefix = "project/sectors/"
        for name in zip_names:
            if not name.startswith(prefix):
                continue
            lane = name[len(prefix) :].split("/", 1)[0]
            sector_counts[lane] = sector_counts.get(lane, 0) + 1

    profiles = {
        "evidence_lane_pv12": sqlite_profile(
            PV12 / "lanes" / "chat_lineage" / "chat_lineage_sector_v001.sqlite"
        ),
        "task3_partial": sqlite_profile(TASK3_SQLITE),
        "life_arc": sqlite_profile(LIFE_ARC_SQLITE),
    }

    query_skill_hits: list[str] = []
    query_terms = ("lane_status", "lane_search", "lane_fetch")
    for skill in sorted((SOURCE / "skills").glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8", errors="replace")
        if any(term in text for term in query_terms):
            query_skill_hits.append(skill.relative_to(SOURCE).as_posix())

    lane_rows: list[tuple[object, ...]] = []
    with connect() as connection:
        connection.row_factory = sqlite3.Row
        current_lanes = [
            dict(row)
            for row in connection.execute("SELECT * FROM lane_contract ORDER BY lane_id")
        ]
    for row in current_lanes:
        lane_rows.append(
            (
                row["lane_id"],
                row["sqlite_filename"],
                row["fts_table"],
                row["mmd_filename"],
                row["dot_filename"],
                row["refresh_receipt_filename"],
                row["project_path_template"],
                row["schema_source_locator"],
                "EMBEDDED_SHARED_STRICT_DDL_PLUS_GENERIC_LANE_TABLES_NO_FIRST_CLASS_ASSET",
                "PUBLIC_SINGLE_LANE_STATUS_SEARCH_FETCH",
                "NO_SKILL_NAMES_LANE_STATUS_SEARCH_FETCH",
                "hybrid|bm25|tfidf",
                "limit=1..100; lexical_terms<=12; fetch<=1MiB; facts<=200",
                "ABSENT_PARALLEL_BUILD_ONLY_MAX8",
                "ABSENT_NO_MULTI_LANE_READ_CONTRACT",
                "ABSENT_NO_EXPLICIT_MULTI_PROJECT_READ_CONTRACT",
                "tests/test_lane_contract.py;tests/test_reader_query.py;tests/test_universal_lanes.py;tests/test_artifact_contract.py",
                "SCHEMA_QUERY_ORCHESTRATION_GAPS",
                "Accepted emitted lane has seven files: SQLite, MMD, DOT, tools.json, lane_pointer.json, refresh_receipt.json, lane_manifest.json. "
                "The registry resolves exact paths, but no owning skill teaches the model the path/query contract.",
                now,
            )
        )

    capability_rows = [
        (
            "LANE-REGISTRY-18",
            "lanes",
            "canonical 18-lane registry",
            "lanes.LANE_REGISTRY",
            "IMPLEMENTED",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/lanes.py:227",
            "lane_catalog",
            "SOURCE_AND_INSTALLED_PUBLIC",
            None,
            None,
            "NO_DEDICATED_SDK_MODULE",
            None,
            "NOT_NAMED_BY_SKILL",
            None,
            "NO_COMMAND_ROUTE",
            "INSTALLED_PUBLIC",
            "LIVE_READ_ONLY",
            "TEST_PRESENT_NOT_EXECUTED_THIS_PHASE",
            "tests/test_universal_lanes.py;tests/test_lane_contract.py",
            "MODEL_ROUTING_INSTRUCTION_GAP",
            "ADD_EXACT_PATH_AND_QUERY_WORKFLOW_TO_OWNING_SKILL",
            "DELTA-LANE-REGISTRY-ROUTING",
            "Registry is deterministic; accepted PV12 correctly emits only 15 active lanes rather than placeholders for all 18.",
            now,
        ),
        (
            "LANE-SCHEMA-BUILDER",
            "lane_schema",
            "per-lane SQLite schema builder",
            "lane_engine._create_lane_schema",
            "IMPLEMENTED_GENERIC_BUILDER",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/lane_engine.py:2998",
            None,
            "INDIRECT_BUILD_REFRESH_ONLY",
            None,
            None,
            "NO_SCHEMA_ADMIN_SDK",
            None,
            "NO_SKILL_SCHEMA_MIGRATION_ROUTE",
            None,
            "NO_COMMAND_ROUTE",
            "INSTALLED_OLDER_BUILDER",
            "EMBEDDED_DDL",
            "TEST_PRESENT_NOT_EXECUTED_THIS_PHASE",
            "tests/test_universal_lanes.py;tests/test_custom_source_schema.py",
            "FIRST_CLASS_SCHEMA_AND_MIGRATION_GAP",
            "EXTERNALIZE_VERSIONED_TYPED_SCHEMAS_AND_ADDITIVE_MIGRATIONS",
            "DELTA-LANE-SCHEMA-SYSTEM",
            "custom_source_schema versions input extraction contracts; it does not migrate or extend physical lane databases.",
            now,
        ),
        (
            "LANE-ARTIFACT-CONTRACT",
            "lane_artifacts",
            "seven-file emitted lane contract",
            "artifact_contract.build_four_file_contract + lane_engine evidence files",
            "IMPLEMENTED",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/artifact_contract.py:16;lane_engine.py:4687",
            "lane_status",
            "PUBLIC_READ",
            None,
            None,
            "NO_DEDICATED_SDK_MODULE",
            None,
            "NOT_EXPLAINED_BY_SKILL",
            None,
            "NO_COMMAND_ROUTE",
            "INSTALLED_PUBLIC",
            "LIVE_PV12_HAS_15_X_7",
            "TEST_PRESENT_NOT_EXECUTED_THIS_PHASE",
            "tests/test_artifact_contract.py;tests/test_public_dummy_lane_packages.py",
            "ARTIFACT_DISCOVERY_AND_EXTENSIBILITY_GAP",
            "VERSION_THE_COMPLETE_LANE_PACKAGE_CONTRACT_WITH_OPTIONAL_TYPED_EXTENSIONS",
            "DELTA-LANE-ARTIFACT-CONTRACT",
            "The evidence does not support a universal 43-files-per-lane invariant; observed counts vary by purpose.",
            now,
        ),
        (
            "LANE-SINGLE-QUERY",
            "lane_query",
            "single-lane bounded search/fetch",
            "lane_reader.LaneReader",
            "IMPLEMENTED",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/lane_reader.py:22",
            "lane_status,lane_search,lane_fetch",
            "SOURCE_AND_INSTALLED_PUBLIC",
            "source_lane_retrieval",
            "search,fetch,query",
            "READ_HANDLERS_REGISTERED",
            None,
            "NOT_NAMED_BY_SKILL",
            None,
            "NO_COMMAND_ROUTE",
            "INSTALLED_PUBLIC",
            "LIVE_SINGLE_PROJECT_SINGLE_LANE",
            "TEST_PRESENT_NOT_EXECUTED_THIS_PHASE",
            "tests/test_reader_query.py",
            "MODEL_QUERY_GUIDANCE_GAP",
            "ADD_BOUNDED_QUERY_PLAYBOOK_AND_COMPACT_STATUS_RECEIPT",
            "DELTA-LANE-QUERY-GUIDANCE",
            "Uses immutable mode=ro SQLite URI, validated registry FTS identifiers, BM25/TFIDF/RRF, 12 terms, limit<=100, fetch<=1MiB.",
            now,
        ),
        (
            "LANE-PARALLEL-QUERY",
            "lane_query",
            "bounded parallel lane reads",
            None,
            "ABSENT",
            "lane_engine.py:parallel build max8; lane_reader.py:no fan-out",
            None,
            "ABSENT",
            "source_lane_retrieval",
            None,
            "ABSENT",
            None,
            "ABSENT",
            None,
            "ABSENT",
            "ABSENT",
            "ABSENT",
            "NO_TEST",
            "",
            "PUBLIC_QUERY_ORCHESTRATION_GAP",
            "IMPLEMENT_EXPLICIT_BOUNDED_FAN_OUT_WITH_STABLE_ORDER_AND_PER_LANE_RECEIPTS",
            "DELTA-PARALLEL-LANE-QUERY",
            "Parallel build is implemented with up to eight workers; that is not evidence of parallel read/query support.",
            now,
        ),
        (
            "LANE-CROSS-LANE-QUERY",
            "lane_query",
            "cross-lane query",
            None,
            "ABSENT",
            "lane_reader.py accepts one lane_alias",
            None,
            "ABSENT",
            "source_lane_retrieval",
            None,
            "ABSENT",
            None,
            "ABSENT",
            None,
            "ABSENT",
            "ABSENT",
            "ABSENT",
            "NO_TEST",
            "",
            "PUBLIC_QUERY_ORCHESTRATION_GAP",
            "IMPLEMENT_EXPLICIT_MULTI_LANE_PLAN_WITH_SEPARATE_RANKING_AND_PROVENANCE",
            "DELTA-CROSS-LANE-QUERY",
            "No method accepts a lane set; no join/conflict/provenance synthesis contract exists.",
            now,
        ),
        (
            "LANE-CROSS-PROJECT-QUERY",
            "lane_query",
            "cross-project query",
            None,
            "ABSENT",
            "reader.py and lane_reader.py accept one exact project_id",
            None,
            "ABSENT",
            "source_lane_retrieval",
            None,
            "ABSENT",
            None,
            "ABSENT",
            None,
            "ABSENT",
            "ABSENT",
            "ABSENT",
            "NO_TEST",
            "",
            "PUBLIC_QUERY_ORCHESTRATION_GAP",
            "IMPLEMENT_OPT_IN_EXACT_PROJECT_SET_WITH_ISOLATED_RANKING_AND_POINTER_PROVENANCE",
            "DELTA-CROSS-PROJECT-QUERY",
            "Current single-project fail-closed routing is correct and must remain the default.",
            now,
        ),
    ]

    findings = [
        (
            "GAP-LANE-MODEL-ROUTING-INSTRUCTIONS",
            "lanes",
            "HIGH",
            "PUBLIC_ROUTE_NOT_BOUND_TO_SKILL",
            "The exact 18-lane registry and lane_status/search/fetch MCP actions exist, but no source skill names those actions or teaches exact project/lane/FTS/path usage.",
            "lane_contract:*;capability_route:MCP-lane_*;skill scan",
            "Models must rediscover paths and route semantics or fall back to broad backlog/filesystem reads.",
            "Add one authoritative bounded lane-query workflow to the root/source-intake skills with exact path templates, result limits, and provenance rules.",
            "DELTA-LANE-QUERY-GUIDANCE",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-LANE-FIRST-CLASS-TYPED-SCHEMAS",
            "lane_schema",
            "HIGH",
            "IMPLEMENTED_GENERIC_SCHEMA_BUT_MISSING_FIRST_CLASS_CONTRACTS",
            "All lane physical DDL is embedded in lane_engine.py; lane-specific schema_contract values create identical four-column payload tables. No first-class typed schema/migration asset exists for any of the 18 lanes.",
            "lane_engine.py:2998-3159;lanes.py:schema_contract;src/evidence_lane_plugin/schemas",
            "Lane meaning, schema evolution, and user extension cannot be independently validated or migrated without reading runtime implementation code.",
            "Create versioned per-lane schemas, additive migration ledgers, extension namespaces, schema hashes, and builder/schema byte-parity tests.",
            "DELTA-LANE-SCHEMA-SYSTEM",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CHAT-LINEAGE-SCHEMA-DEPTH",
            "chat_lineage",
            "HIGH",
            "SCHEMA_AND_RUNTIME_DEPTH_GAP",
            "PV12 ChatLineage carries generic lane tables and its typed turn/prompt/response/receipt tables are empty, while the supplied Task3 and Life Arc databases contain populated typed turn, prompt, response, receipt, FTS, file-link, and recovery structures.",
            "sqlite_profile:evidence_lane_pv12,task3_partial,life_arc",
            "Current project ChatLineage cannot provide the same append/query/recovery fidelity even though table names are declared.",
            "Implement typed append-at-PREPARE/COMMIT/true-Exit writes, source cursors, hashes, FTS, file links, compaction/recovery records, and additive schema evolution.",
            "DELTA-CHAT-LINEAGE-TYPED-RUNTIME",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-LANE-ARTIFACT-COUNT-CLAIM",
            "lane_artifacts",
            "MEDIUM",
            "UNVERIFIED_FIXED_COUNT_ASSUMPTION",
            "No supplied authority establishes a universal 43-files-per-lane contract. Accepted PV12 has seven files per emitted lane, public dummy lanes have six, and the Life Arc package has three files in most sectors but 181 in ChatLineage because it includes source history and attachments.",
            "PV12/lanes filesystem;public dummy packages;Life Arc ZIP central directory",
            "Hard-coding 43 would optimize for a number rather than an artifact/authority contract.",
            "Define required, conditional, and optional artifact roles per lane; let validated extensions add files without changing unrelated lanes.",
            "DELTA-LANE-ARTIFACT-CONTRACT",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-PARALLEL-LANE-QUERY",
            "lane_query",
            "HIGH",
            "MISSING_PUBLIC_CAPABILITY",
            "The source implements bounded parallel lane build with at most eight workers, but LaneReader performs only one-lane reads and has no parallel query orchestrator.",
            "lane_engine.py:5145-5327;lane_reader.py:107",
            "Parallel build evidence is currently being overextended into a read/query claim.",
            "Implement deterministic bounded fan-out, per-lane time/result budgets, cancellation, stable ordering, dedupe, and a sealed aggregate receipt.",
            "DELTA-PARALLEL-LANE-QUERY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CROSS-LANE-QUERY",
            "lane_query",
            "HIGH",
            "MISSING_PUBLIC_CAPABILITY",
            "No multi-lane query API, SDK operation, skill contract, ranking separation, join policy, conflict handling, or per-lane provenance response exists.",
            "lane_reader.py;internal_sdk.py:source_lane_retrieval",
            "The model cannot query related Plan, ChatLineage, Canon, Learning, source, and artifacts lanes in one bounded governed request.",
            "Add explicit lane sets, independent ranking, typed join relations, conflicts, bounded synthesis, and per-result authority provenance.",
            "DELTA-CROSS-LANE-QUERY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CROSS-PROJECT-QUERY",
            "lane_query",
            "HIGH",
            "MISSING_OPT_IN_CAPABILITY",
            "All readers require one exact project_id and intentionally fail closed; no opt-in cross-project query contract exists.",
            "reader.py;lane_reader.py;ProjectStore routing",
            "Legitimate portfolio or linked-Canon research requires repeated manual calls and lacks one aggregate provenance receipt.",
            "Preserve single-project default isolation and add an explicit exact project-ID set with per-project pointers, budgets, separate ranking, permissions, and provenance.",
            "DELTA-CROSS-PROJECT-QUERY",
            "OPEN",
            "HIGH",
            now,
        ),
    ]

    test_rows = [
        (
            "TEST-LANE-CONTRACT",
            "lanes",
            "tests/test_lane_contract.py;tests/test_universal_lanes.py;tests/test_artifact_contract.py",
            "lane contract",
            "UNIT_INTEGRATION",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Registry, seven-file accepted contract, database validation, and artifact hash binding.",
            "Does not prove first-class typed schema assets, migrations, or model-facing skill guidance.",
            now,
        ),
        (
            "TEST-LANE-QUERY",
            "lane_query",
            "tests/test_reader_query.py",
            "lane search/fetch",
            "UNIT_INTEGRATION",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Single-lane immutable query bounds and ranking behavior.",
            "Does not prove parallel, cross-lane, or cross-project query support.",
            now,
        ),
    ]

    with connect() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO lane_contract VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            lane_rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO capability_route VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            capability_rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            findings,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO test_evidence VALUES (?,?,?,?,?,?,?,?,?)",
            test_rows,
        )
        for authority_id, kind, path, freshness, role, notes in [
            ("AUTH-LANE-REGISTRY", "source_module", lanes_file, "LIVE_DIRTY", "Canonical 18-lane registry", "Exact filenames, FTS tables, aliases, parsers, and schema-contract names."),
            ("AUTH-LANE-ENGINE", "source_module", lane_engine, "LIVE_DIRTY", "Lane builder and topology runtime", "Embedded schema, incremental reuse, parallel build, seven-file artifacts."),
            ("AUTH-LANE-READER", "source_module", lane_reader, "LIVE_DIRTY", "Single-lane immutable reader", "One project and one lane per call."),
            ("AUTH-LANE-ARTIFACT-CONTRACT", "source_module", artifact_contract, "LIVE_DIRTY", "Four stable artifact hash contract", "Combined with three evidence files in emitted lane packages."),
            ("AUTH-CUSTOM-SOURCE-SCHEMA", "source_module", custom_schema, "LIVE_DIRTY", "Versioned custom source extraction schemas", "Not a physical lane-database migration or extension mechanism."),
        ]:
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (authority_id, kind, str(path), sha256_file(path), path.stat().st_size, freshness, role, notes, now),
            )
        for key, profile in profiles.items():
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"AUTH-SQLITE-PROFILE-{key.upper()}",
                    "sqlite_profile",
                    str(profile["path"]),
                    profile["sha256"],
                    profile["bytes"],
                    "READ_ONLY_PROFILE",
                    f"{key} bounded schema/row profile",
                    json.dumps(
                        {name: value for name, value in profile.items() if name not in {"path", "sha256", "counts"}},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )

        upsert_fts(
            connection,
            doc_id="FTS-STEP14-LANE-REGISTRY",
            doc_type="research_finding",
            title="Canonical lane registry and exact path authority",
            body=(
                f"canonical_lanes={len(lane_rows)}; accepted_pv12_emitted={len(accepted_lane_dirs)}; "
                f"query_skill_hits={query_skill_hits}. Exact package path is <PV>/lanes/<lane_id>/<sqlite_filename>; "
                "unloaded lanes remain absent rather than receiving placeholders."
            ),
            evidence_locator="lane_contract:*;AUTH-LANE-REGISTRY",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP15-LANE-SCHEMAS",
            doc_type="research_finding",
            title="Per-lane schema, migration, extension, and hardening parity",
            body=(
                f"canonical_lanes={len(lane_rows)}; first_class_lane_schema_assets=0; other_top_level_schema_assets={len(schema_assets)}. "
                "lane_engine embeds one shared STRICT schema and creates lane-specific table names with generic record_id/source_id/locator/payload_json columns. "
                "No additive physical lane migration or governed lane-schema extension contract was found."
            ),
            evidence_locator="AUTH-LANE-ENGINE;GAP-LANE-FIRST-CLASS-TYPED-SCHEMAS",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP16-LANE-ARTIFACTS",
            doc_type="research_finding",
            title="Lane artifact counts and authority roles",
            body=(
                f"accepted_pv12_lane_counts={sorted(set(accepted_file_counts.values()))}; accepted_lane_count={len(accepted_file_counts)}; "
                f"public_dummy_counts={sorted(set(dummy_file_counts.values()))}; life_arc_sector_counts={sector_counts}. "
                "Observed evidence rejects a universal fixed 43-file invariant; artifact roles must be required/conditional/optional."
            ),
            evidence_locator="AUTH-LANE-ARTIFACT-CONTRACT;GAP-LANE-ARTIFACT-COUNT-CLAIM",
        )
        upsert_fts(
            connection,
            doc_id="FTS-CHAT-LINEAGE-SQLITE-COMPARISON",
            doc_type="benchmark_comparison",
            title="Evidence Lane, Task3, and Life Arc ChatLineage SQLite comparison",
            body=json.dumps(
                {
                    key: {name: value for name, value in profile.items() if name not in {"path", "sha256", "counts"}}
                    for key, profile in profiles.items()
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            evidence_locator="AUTH-SQLITE-PROFILE-*;GAP-CHAT-LINEAGE-SCHEMA-DEPTH",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP17-LANE-QUERY",
            doc_type="research_finding",
            title="Exact single-lane query and fetch contract",
            body=(
                "LaneReader validates the PV and lane bundle, resolves one exact project and lane, opens SQLite with mode=ro&immutable=1, "
                "uses registry-owned FTS identifiers, caps lexical terms at 12, supports bm25/tfidf/hybrid, limits results to 100, "
                "fetches at most 1MiB and 200 facts. No skill exposes this playbook."
            ),
            evidence_locator="AUTH-LANE-READER;LANE-SINGLE-QUERY",
        )
        for step, title, body, locator in [
            (18, "Parallel query is absent", "Parallel lane build exists with at most eight workers. No parallel read fan-out, budget, ordering, cancellation, dedupe, or aggregate receipt exists.", "LANE-PARALLEL-QUERY"),
            (19, "Cross-lane query is absent", "LaneReader accepts one lane_alias. No lane set, independent ranking, join, conflict, or per-lane synthesis contract exists.", "LANE-CROSS-LANE-QUERY"),
            (20, "Cross-project query is absent", "Readers accept one exact project_id. This fail-closed default is correct, but no explicit multi-project set with per-project pointers and provenance exists.", "LANE-CROSS-PROJECT-QUERY"),
        ]:
            upsert_fts(
                connection,
                doc_id=f"FTS-STEP{step}-QUERY",
                doc_type="research_finding",
                title=title,
                body=body,
                evidence_locator=locator,
            )

        for step, locator, event in [
            (14, "FTS-STEP14-LANE-REGISTRY", "EVT-STEP14-LANE-REGISTRY-COMPLETE"),
            (15, "FTS-STEP15-LANE-SCHEMAS", "EVT-STEP15-LANE-SCHEMAS-COMPLETE"),
            (16, "FTS-STEP16-LANE-ARTIFACTS", "EVT-STEP16-LANE-ARTIFACTS-COMPLETE"),
            (17, "FTS-STEP17-LANE-QUERY", "EVT-STEP17-LANE-QUERY-COMPLETE"),
            (18, "FTS-STEP18-QUERY", "EVT-STEP18-PARALLEL-QUERY-COMPLETE"),
            (19, "FTS-STEP19-QUERY", "EVT-STEP19-CROSS-LANE-COMPLETE"),
            (20, "FTS-STEP20-QUERY", "EVT-STEP20-CROSS-PROJECT-COMPLETE"),
        ]:
            transition_step(
                connection,
                step_no=step,
                to_status="completed",
                evidence_locator=locator,
                event_id=event,
                occurred_at=now,
            )
        transition_step(
            connection,
            step_no=21,
            to_status="in_progress",
            evidence_locator="Official OpenAI hook/memory/compaction plus Task3 bounded locators",
            event_id="EVT-STEP21-HOOK-LINEAGE-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEPS14-20-LANES",
            event_type="RESEARCH_STEPS_COMPLETED",
            payload={
                "steps": [14, 15, 16, 17, 18, 19, 20],
                "canonical_lane_count": len(lane_rows),
                "accepted_pv12_emitted_lane_count": len(accepted_lane_dirs),
                "accepted_file_counts": accepted_file_counts,
                "accepted_file_name_sets": accepted_file_name_sets,
                "dummy_file_counts": dummy_file_counts,
                "life_arc_zip_file_count": len(zip_names),
                "life_arc_sector_counts": sector_counts,
                "sqlite_profiles": {
                    key: {name: value for name, value in profile.items() if name not in {"counts"}}
                    for key, profile in profiles.items()
                },
                "query_skill_hits": query_skill_hits,
                "parallel_query_present": False,
                "cross_lane_query_present": False,
                "cross_project_query_present": False,
                "canonical_plan_mutated": False,
            },
            occurred_at=now,
        )
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"quick_check failed: {check}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "completed_steps": [14, 15, 16, 17, 18, 19, 20],
                "in_progress_step": 21,
                "canonical_lanes": len(lane_rows),
                "accepted_pv12_emitted_lanes": len(accepted_lane_dirs),
                "accepted_lane_file_counts": sorted(set(accepted_file_counts.values())),
                "public_dummy_file_counts": sorted(set(dummy_file_counts.values())),
                "life_arc_sector_counts": sector_counts,
                "first_class_lane_schema_assets": 0,
                "query_skill_hits": query_skill_hits,
                "parallel_query": False,
                "cross_lane_query": False,
                "cross_project_query": False,
                "sqlite_profiles": {
                    key: {
                        "logical_tables": profile["logical_table_count"],
                        "strict_tables": profile["strict_table_count"],
                        "nonempty_tables": profile["nonempty_table_count"],
                        "rows": profile["row_count_sum"],
                    }
                    for key, profile in profiles.items()
                },
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
