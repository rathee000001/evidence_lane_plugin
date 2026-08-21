"""Record Step 22 Plan, Goal, FTS, and host-panel continuity evidence.

Only the separate Task6 research SQLite is written.  The canonical Plan JSON,
live Plan projection, accepted PV, Goal, and host Plan are read-only inputs.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
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
INSTALLED = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github"
    r"\evidence-lane-plugin\2.1.0+codex.20260812193232"
)
PROJECT = Path(
    r"C:\Users\rathe\EvidenceLanePV\projects\test-codex-evidence-lane-plugin"
)
TASK6_ID = "01a0036f-32fa-79b2-8846-9c716d4fe777"
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"

BACKLOG = PROJECT / "task_backlog.json"
LIVE_PLAN_SQLITE = PROJECT / "plan_runtime_projection.sqlite"
PV12_PLAN_ROOT = PROJECT / "accepted" / "PV12" / "lanes" / "plan"
PV12_PLAN_SQLITE = PV12_PLAN_ROOT / "plan_sector_v001.sqlite"
SESSION_JSON = PROJECT / "sessions" / f"{SESSION_ID}.json"
HOST_PLAN_RECEIPTS = PROJECT / "receipts" / "host-plan-rehydration"

SOURCE_PLAN_RUNTIME = PACKAGE / "plan_runtime.py"
SOURCE_HOST_PLAN = PACKAGE / "host_plan_rehydration.py"
SOURCE_GOAL_USAGE = PACKAGE / "goal_usage.py"
SOURCE_SERVICE = PACKAGE / "service.py"
SOURCE_STORE = PACKAGE / "store.py"
SOURCE_MCP = PACKAGE / "mcp_server.py"
SOURCE_ARTIFACT_CONTRACT = PACKAGE / "artifact_contract.py"
SOURCE_EVI_PLAN = SOURCE / "commands" / "evi-plan.md"
INSTALLED_MCP = INSTALLED / "src" / "evidence_lane_plugin" / "mcp_server.py"
INSTALLED_PLAN_RUNTIME = (
    INSTALLED / "src" / "evidence_lane_plugin" / "plan_runtime.py"
)
INSTALLED_EVI_PLAN_SKILL = (
    INSTALLED
    / ".codex-plugin"
    / "migrated-command-skills"
    / "source-command-evi-plan"
    / "SKILL.md"
)

CURRENT_GOAL_OBJECTIVE = (
    "Continue the governed Evidence Lane v2.2 implementation from canonical Row196 in Task6. "
    "Preserve accepted PV12/generation 12 and every dirty/untracked byte. Treat every linked "
    "Delta as an unapplied requirement rather than implementation proof. Query native Plan "
    "SQLite and FTS by stable task and Delta IDs without loading the full backlog into model "
    "context. Repair and implement the stable-v2.2 hook, lifecycle, public/runtime routing, "
    "context-continuity, Plan/Goal/panel, helper/slot-switching, local-install, and CI corrections "
    "governed by Row196. Verify each implemented Delta through its existing local testing contract "
    "and authorized local Stable-install/recovery route. Continue linearly until the fresh "
    "unaccepted PV13 six-way HIL at Row197, then stop without inferring approval. Do not wait for "
    "or launch the Task3 parity subagent until the user supplies the stronger full Task3 "
    "ChatLineage SQLite; at that time launch exactly one bounded read-only parity agent and keep "
    "implementation ownership in Task6."
)
CURRENT_GOAL_SNAPSHOT = {
    "thread_id": TASK6_ID,
    "status": "active",
    "tokens_used": 1_841_159,
    "time_used_seconds": 7_162,
    "objective_sha256": hashlib.sha256(
        CURRENT_GOAL_OBJECTIVE.encode("utf-8")
    ).hexdigest().upper(),
    "objective_length": len(CURRENT_GOAL_OBJECTIVE),
}

USER_PLAN_QUERY_STEER = (
    "The host Step Task List is one fixed progress header plus exactly nine Delta rows: the "
    "sole ACTIVE row and the next eight QUEUED rows, or fewer only at the physical end. After "
    "that nine-row window transitions, query the next active-anchored nine-row window. Query "
    "the live Plan SQLite by exact task ID or bounded FTS5/BM25; never send the SQLite, full "
    "backlog, full PV, or full lane payload into model context. Every classified/fired lane "
    "must expose bounded query and append/log receipts."
)

USER_SECTOR_AND_ENV_STEER = (
    "Each fired lane has four stable sector authorities: SQLite, MMD, DOT, and tools.json; "
    "control manifests, pointers, and refresh receipts are supporting evidence rather than "
    "extra model payload. Governed project schemas may evolve additively from Entry Slips, "
    "except github_code and local_code schema changes require explicit user authorization. "
    "ENV/UOP bytes and authority are locked. Any GitHub PAT is an external user-held secret "
    "and must never be copied into prompts, user data, ChatLineage, lane SQLite, receipts, "
    "tests, plugin assets, or model context."
)


def sqlite_profile(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    tables = [
        dict(row)
        for row in connection.execute(
            "SELECT name,type,ncol,strict FROM pragma_table_list "
            "WHERE schema='main' AND name NOT LIKE 'sqlite_%' AND type!='shadow' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for row in tables:
        if row["type"] == "virtual":
            continue
        quoted = str(row["name"]).replace('"', '""')
        try:
            counts[str(row["name"])] = int(
                connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
            )
        except sqlite3.Error:
            pass
    meta: dict[str, str] = {}
    if any(row["name"] == "projection_meta" for row in tables):
        meta = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM projection_meta")
        }
    result = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "quick_check": str(connection.execute("PRAGMA quick_check").fetchone()[0]),
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "table_count": len(tables),
        "strict_table_count": sum(int(row["strict"]) for row in tables),
        "fts_tables": sorted(
            str(row["name"]) for row in tables if row["type"] == "virtual"
        ),
        "counts": counts,
        "meta": meta,
    }
    connection.close()
    return result


def route(
    route_id: str,
    domain: str,
    capability: str,
    core_symbol: str | None,
    core_status: str,
    source_locator: str,
    *,
    mcp_tool: str | None = None,
    mcp_status: str = "ABSENT",
    sdk_module: str | None = None,
    sdk_operation: str | None = None,
    sdk_status: str = "ABSENT",
    skill_path: str | None = None,
    skill_status: str = "ABSENT",
    command_path: str | None = None,
    command_status: str = "ABSENT",
    installed_status: str = "ABSENT",
    runtime_status: str = "UNPROVEN",
    test_status: str = "NOT_EXECUTED_THIS_RESEARCH_PHASE",
    test_locators: str = "",
    gap_class: str = "NONE",
    decision: str = "RETAIN",
    proposed_delta_key: str | None = None,
    notes: str = "",
    now: str,
) -> tuple[object, ...]:
    return (
        route_id,
        domain,
        capability,
        core_symbol,
        core_status,
        source_locator,
        mcp_tool,
        mcp_status,
        sdk_module,
        sdk_operation,
        sdk_status,
        skill_path,
        skill_status,
        command_path,
        command_status,
        installed_status,
        runtime_status,
        test_status,
        test_locators,
        gap_class,
        decision,
        proposed_delta_key,
        notes,
        now,
    )


def main() -> None:
    now = utc_now()
    backlog_payload = json.loads(BACKLOG.read_text(encoding="utf-8"))
    backlog_profile = {
        "path": str(BACKLOG),
        "bytes": BACKLOG.stat().st_size,
        "sha256": sha256_file(BACKLOG),
        "schema": backlog_payload.get("schema"),
        "plan_count": len(backlog_payload.get("plans") or []),
        "task_count": len(backlog_payload.get("tasks") or []),
        "event_count": len(backlog_payload.get("events") or []),
        "planning_mode_event_count": len(
            backlog_payload.get("planning_mode_events") or []
        ),
        "goal_row_offset": backlog_payload.get("goal_row_offset"),
        "status_counts": dict(
            sorted(
                Counter(
                    str(row.get("status") or "UNKNOWN")
                    for row in backlog_payload.get("tasks") or []
                ).items()
            )
        ),
    }
    live_plan = sqlite_profile(LIVE_PLAN_SQLITE)
    accepted_plan = sqlite_profile(PV12_PLAN_SQLITE)
    session = json.loads(SESSION_JSON.read_text(encoding="utf-8"))
    metadata = dict(session.get("metadata") or {})
    session_profile = {
        "current_host_session_id": metadata.get("current_host_session_id"),
        "active_backlog_task_id": metadata.get("active_backlog_task_id"),
        "active_backlog_task_status": metadata.get("active_backlog_task_status"),
        "host_plan_window_present": "host_plan_window" in metadata,
        "last_host_plan_rehydration_receipt_sha256": metadata.get(
            "last_host_plan_rehydration_receipt_sha256"
        ),
    }
    receipt_count = (
        len(list(HOST_PLAN_RECEIPTS.rglob("*.json")))
        if HOST_PLAN_RECEIPTS.is_dir()
        else 0
    )
    source_mcp_text = SOURCE_MCP.read_text(encoding="utf-8", errors="replace")
    installed_mcp_text = INSTALLED_MCP.read_text(
        encoding="utf-8", errors="replace"
    )
    source_service_text = SOURCE_SERVICE.read_text(
        encoding="utf-8", errors="replace"
    )
    source_host_text = SOURCE_HOST_PLAN.read_text(
        encoding="utf-8", errors="replace"
    )
    source_plan_text = SOURCE_PLAN_RUNTIME.read_text(
        encoding="utf-8", errors="replace"
    )
    source_evi_text = SOURCE_EVI_PLAN.read_text(
        encoding="utf-8", errors="replace"
    )
    installed_evi_text = INSTALLED_EVI_PLAN_SKILL.read_text(
        encoding="utf-8", errors="replace"
    )
    installed_host_plan_present = (
        INSTALLED / "src" / "evidence_lane_plugin" / "host_plan_rehydration.py"
    ).is_file()
    four_stable_files = [
        "plan_sector_v001.sqlite",
        "plan.mmd",
        "plan.dot",
        "tools.json",
    ]
    support_files = ["lane_manifest.json", "lane_pointer.json", "refresh_receipt.json"]
    stable_four_present = all((PV12_PLAN_ROOT / name).is_file() for name in four_stable_files)
    support_three_present = all((PV12_PLAN_ROOT / name).is_file() for name in support_files)

    source_bounded_backlog = (
        "application.task_backlog_window" in source_mcp_text
        and "task_id: str | None = None" in source_mcp_text
        and "query: str | None = None" in source_mcp_text
    )
    installed_full_backlog = (
        "application.task_backlog, project_id" in installed_mcp_text
        and "def pv_task_backlog(project_id: str)" in installed_mcp_text
    )
    source_active_anchored_host_nine = (
        "_HOST_PLAN_WINDOW_SIZE = 9" in source_host_text
        and "window_start_index = active_index" in source_host_text
    )
    source_grid_window_ten = (
        "limit: int = 10" in source_service_text
        and "window_start = (active_index // window_size) * window_size"
        in source_service_text
    )
    source_plan_fts_bm25 = (
        "CREATE VIRTUAL TABLE plan_runtime_fts USING fts5" in source_plan_text
        and "bm25(plan_runtime_fts)" in source_plan_text
    )
    source_projection_replaced = (
        "os.replace(temporary, path)" in source_plan_text
        and "Atomically rebuild the derived SQLite projection" in source_plan_text
    )
    evi_complete_projection_claim = (
        "complete exact projection" in source_evi_text
        and "complete exact projection" in installed_evi_text
    )

    routes = [
        route(
            "PLAN-LIVE-AUTHORITY",
            "plan",
            "live append-preserving Plan authority",
            "ProjectStore._persist_backlog",
            "JSON_CANONICAL_SQLITE_DERIVED",
            "store.py:ProjectStore._persist_backlog;plan_runtime.py:write_plan_runtime_projection",
            mcp_tool="pv_plan_tasks,pv_plan_steer_delta,pv_task_transition",
            mcp_status="WRITES_JSON_THEN_REBUILDS_SQLITE",
            skill_path="source-command-evi-plan",
            skill_status="CLAIMS_PLAN_LANE_AUTHORITY",
            installed_status="LIVE_SQLITE_V1_DERIVED_NO_FTS",
            runtime_status="BACKLOG_JSON_185_TASKS_824_EVENTS",
            test_status="LIVE_READ_ONLY_PROFILE",
            test_locators=f"{BACKLOG};{LIVE_PLAN_SQLITE}",
            gap_class="CANONICAL_AUTHORITY_AND_MIGRATION_GAP",
            decision="MIGRATE_TO_TRANSACTIONAL_SQLITE_AUTHORITY_AND_RETAIN_JSON_AS_EXPORT_ONLY",
            proposed_delta_key="DELTA-PLAN-SQLITE-AUTHORITY",
            notes="Source v2 still rebuilds and replaces a derived SQLite from the full JSON ledger; it does not make SQLite the mutable canonical authority.",
            now=now,
        ),
        route(
            "PLAN-BOUNDED-WINDOW",
            "plan",
            "fixed header plus active-anchored nine-Delta query window",
            "EvidenceLaneService.task_backlog_window;host_plan_rehydration._exact_projection",
            "CONFLICTING_SOURCE_IMPLEMENTATIONS",
            "service.py:task_backlog_window;host_plan_rehydration.py:_exact_projection",
            mcp_tool="pv_task_backlog",
            mcp_status="SOURCE_BOUNDED_INSTALLED_FULL_DUMP",
            skill_path="source-command-evi-plan",
            skill_status="REQUESTS_COMPLETE_PROJECTION_NOT_FIXED_WINDOW",
            installed_status="FULL_BACKLOG_RETURN",
            runtime_status="CONTEXT_BLOAT_ROUTE_ACTIVE_IN_2_1",
            test_status="SOURCE_STATIC_PLUS_LIVE_SCHEMA_PROOF",
            test_locators="tests/test_host_plan_rehydration.py;tests/test_persistent_step_task_list_contract.py",
            gap_class="INSTALLED_FULL_DUMP_AND_WINDOW_CONTRACT_DRIFT",
            decision="UNIFY_ON_HEADER_PLUS_ACTIVE_AND_NEXT_EIGHT_SQLITE_QUERY",
            proposed_delta_key="DELTA-PLAN-BOUNDED-WINDOW",
            notes="The source MCP defaults to a grid-aligned ten-row window, while host rehydration uses active+8 and evi-plan asks for the complete projection. The user-bound law is header+9 Delta rows.",
            now=now,
        ),
        route(
            "PLAN-FTS-BM25-RUNTIME",
            "plan",
            "exact task lookup and bounded FTS5/BM25 Plan retrieval",
            "query_plan_runtime_projection",
            "IMPLEMENTED_SOURCE_V2",
            "plan_runtime.py:1789",
            mcp_tool="pv_task_backlog",
            mcp_status="SOURCE_OPTIONAL_TASK_OR_QUERY",
            skill_path="source-command-evi-plan",
            skill_status="BOUNDED_QUERY_REQUIRED_BUT_INSTALLED_COMMAND_STALE",
            installed_status="V1_PROJECTION_HAS_NO_FTS_TABLE",
            runtime_status="UNAVAILABLE_IN_ACTIVE_INSTALLED_RUNTIME",
            test_status="SOURCE_TESTS_PRESENT_NOT_EXECUTED_THIS_PHASE",
            test_locators="tests/test_backlog_enrollment.py;tests/test_plan_steers.py",
            gap_class="IMPLEMENTED_SOURCE_BUT_NOT_INSTALLED",
            decision="INSTALL_AFTER_PARITY_TEST_AND_QUERY_ONLY_SQLITE_AT_MODEL_BOUNDARY",
            proposed_delta_key="DELTA-PLAN-FTS-RUNTIME",
            notes="FTS5/BM25 source logic is useful, but it is a derived v2 index and active installed v1 cannot query it.",
            now=now,
        ),
        route(
            "HOST-PLAN-PANEL-CONTINUITY",
            "host_plan",
            "detect, rehydrate, and advance the native 1+9 Step Task List",
            "prepare_host_plan_rehydration",
            "IMPLEMENTED_SOURCE_CORE",
            "host_plan_rehydration.py;session.py",
            skill_path="skills/evidence-lane-code-lifecycle/SKILL.md",
            skill_status="MODEL_SKILL_MUST_CALL_UPDATE_PLAN",
            command_path="host update_plan",
            command_status="HOST_OWNED_NO_VISIBILITY_RECEIPT",
            installed_status="HOST_PLAN_MODULE_ABSENT",
            runtime_status="NO_TASK6_REHYDRATION_RECEIPT_OR_SESSION_WINDOW",
            test_status="UNIT_TESTS_PRESENT_NOT_LIVE_PROOF",
            test_locators="tests/test_host_plan_rehydration.py;tests/test_persistent_step_task_list_contract.py",
            gap_class="IMPLEMENTED_CORE_BUT_NOT_INSTALLED_OR_AUTOMATIC",
            decision="ROUTE_HOST_OBSERVATION_TO_EXACTLY_ONCE_SKILL_UPDATE_AND_VERIFY_ARTIFACT",
            proposed_delta_key="DELTA-HOST-PLAN-PANEL-CONTINUITY",
            notes="The source correctly refuses to equate an empty update_plan receipt with visibility. The plugin cannot guarantee host surface survival; it needs a verifiable observation and reactivation route.",
            now=now,
        ),
        route(
            "HOST-CHANGES-PANEL-CONTINUITY",
            "host_panel",
            "keep native Changes surface bound to Task6 and exact worktree",
            None,
            "DECLARATIVE_CLAIM_ONLY",
            "host_plan_rehydration.py:host_surface_persistence",
            installed_status="NO_HOST_PLAN_MODULE",
            runtime_status="NO_PUBLIC_CHANGES_SURFACE_OBSERVATION_OR_REHYDRATION_API",
            test_status="NO_LIVE_CONTINUITY_PROOF",
            test_locators="tests/test_persistent_step_task_list_contract.py",
            gap_class="HOST_CAPABILITY_AND_OBSERVATION_GAP",
            decision="KEEP_FAIL_CLOSED_AND_RECORD_HOST_OBSERVATION_WITHOUT_CLAIMING_CONTROL",
            proposed_delta_key="DELTA-HOST-PLAN-PANEL-CONTINUITY",
            notes="update_plan controls only the Plan artifact. Changes-panel persistence is host-owned and cannot be claimed from a source string.",
            now=now,
        ),
        route(
            "GOAL-NATIVE-LIFECYCLE-BINDING",
            "goal",
            "native Goal set/get/update/clear and Evidence Lane binding",
            "goal_usage.build_goal_completion_authorization",
            "ACCOUNTING_AND_HUMAN_COMPLETION_CORE_PRESENT",
            "goal_usage.py;codex_turn_control.py;official app-server Goal contract",
            mcp_status="EVIDENCE_LANE_CANNOT_MUTATE_NATIVE_GOAL",
            skill_path="source-command-evi-plan",
            skill_status="GOAL_PROMPT_HANDOFF_CONFLICTS_WITH_AUTOMATIC_STATE_TRAVEL_TEXT",
            command_path="thread/goal/set,get,clear;native create_goal/get_goal/update_goal",
            command_status="HOST_NATIVE_ROUTE",
            installed_status="GOAL_PREPARE_FAILS_CLOSED_NO_GOAL_HOOK",
            runtime_status="ACTIVE_GOAL_OBJECTIVE_STALE_AGAINST_CURRENT_RESEARCH_STEER",
            test_status="NATIVE_GET_GOAL_READ_ONLY_PLUS_OFFICIAL_CONTRACT",
            test_locators="https://learn.chatgpt.com/docs/app-server;tests/test_goal_usage.py",
            gap_class="NATIVE_GOAL_GENERATION_AND_STEER_DRIFT_GAP",
            decision="BIND_GOAL_GENERATIONS_VIA_NATIVE_EVENTS_NEVER_HOOKS_AND_REQUIRE_USER_AUTHORITY_FOR_OBJECTIVE_REPLACEMENT",
            proposed_delta_key="DELTA-GOAL-NATIVE-LIFECYCLE-BINDING",
            notes="Changing a Goal objective resets native usage. Preserve prior generation accounting in project lineage and do not replace the current objective automatically.",
            now=now,
        ),
        route(
            "PLAN-FOUR-FILE-LIVE-SECTOR",
            "plan",
            "four stable Plan lane authorities kept live together",
            "artifact_contract.build_four_file_contract",
            "FOUR_FILE_STATIC_PV_CONTRACT_IMPLEMENTED",
            "artifact_contract.py;accepted/PV12/lanes/plan",
            mcp_tool="lane_status,lane_search,lane_fetch",
            mcp_status="ACCEPTED_PV_READ_ONLY",
            installed_status="STATIC_ACCEPTED_SECTOR_PLUS_SEPARATE_LIVE_JSON",
            runtime_status="LIVE_DELTA_QUEUE_NOT_THE_ACCEPTED_PLAN_SQLITE",
            test_status="LIVE_FILESYSTEM_AND_SQLITE_PROFILE",
            test_locators=f"{PV12_PLAN_ROOT};tests/test_artifact_contract.py",
            gap_class="LIVE_PLAN_SECTOR_SPLIT_BRAIN",
            decision="MAKE_LIVE_PLAN_SQLITE_PRIMARY_AND_REGENERATE_MMD_DOT_TOOLS_POINTER_RECEIPTS_TRANSACTIONALLY",
            proposed_delta_key="DELTA-PLAN-SQLITE-AUTHORITY",
            notes="PV12 has the four stable authorities plus three support files, but plan_task is empty and the current 185-task queue lives outside the accepted Plan sector.",
            now=now,
        ),
        route(
            "ENV-UOP-AUTHORITY-SECRET-BOUNDARY",
            "env_uop",
            "locked ENV/UOP authority and external credential boundary",
            None,
            "USER_BOUND_ABSOLUTE_AUTHORITY",
            "codex://thread/01a0036f-32fa-79b2-8846-9c716d4fe777/current-plan-sector-env-steer",
            mcp_status="NO_SECRET_VALUE_ROUTE_ALLOWED",
            skill_path="skills/evi-boot/SKILL.md;skills/evi-mode/SKILL.md",
            skill_status="LOCKED_FLASH_AND_MODE_CONTRACT",
            installed_status="LOCKED_BYTES_PRESENT_EXECUTION_PARITY_INCOMPLETE",
            runtime_status="CREDENTIAL_MUST_REMAIN_EXTERNAL_REFERENCE_ONLY",
            test_status="USER_AUTHORITY_BOUNDARY_RECORDED",
            test_locators="Task6 direct steer;secret redaction tests",
            gap_class="ABSOLUTE_AUTHORITY_AND_SECRET_PROVENANCE_BOUNDARY",
            decision="ENFORCE_IMMUTABLE_ENV_UOP_HASHES_AND_NEVER_STORE_OR_REQUEST_PAT_IN_MODEL_DATA",
            proposed_delta_key="DELTA-ENV-UOP-AUTHORITY-BOUNDARY",
            notes="The PAT value is not evidence and was not supplied or recorded. Git/CI work may use a host credential provider only under an authorized route.",
            now=now,
        ),
    ]

    findings = [
        (
            "GAP-PLAN-SQLITE-CANONICAL-AUTHORITY",
            "plan",
            "CRITICAL",
            "JSON_AUTHORITY_SQLITE_DERIVED_REBUILD",
            f"The live canonical queue is {BACKLOG.name} ({backlog_profile['bytes']} bytes, {backlog_profile['task_count']} tasks, {backlog_profile['event_count']} events). The active SQLite declares projection_role=DERIVED_CONTROL_PLANE_INDEX and is version {live_plan['user_version']}; source v2 also rebuilds and replaces the whole derived database.",
            f"{BACKLOG};{LIVE_PLAN_SQLITE};store.py;plan_runtime.py",
            "The live system cannot use SQLite transactions, migrations, FTS, relationships, and append history as the sole Plan authority; JSON remains the split-brain source.",
            "Migrate all task, Delta, lifecycle, dependency, HIL, pointer-link, and Plan-generation records into a strict transactional SQLite authority; preserve hashes; make JSON a bounded export only; regenerate MMD/DOT/tools/pointer receipts from committed SQLite generations.",
            "DELTA-PLAN-SQLITE-AUTHORITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-INSTALLED-PV-TASK-BACKLOG-FULL-DUMP",
            "plan",
            "CRITICAL",
            "ACTIVE_INSTALLED_CONTEXT_BLOAT_ROUTE",
            "Installed 2.1 pv_task_backlog accepts only project_id and returns application.task_backlog, exposing the full ledger. Source 2.2 adds task_id/query/limit and a bounded window, but that implementation is not active.",
            f"{INSTALLED_MCP};{SOURCE_MCP}",
            "The model can receive the entire Plan/backlog, recreating the context drift and UI instability the bounded SQLite design was meant to prevent.",
            "Remove the unbounded installed response; return only the fixed header metadata plus active+8 rows, exact-task detail, or bounded FTS5/BM25 hits with hashes and truncation receipts.",
            "DELTA-PLAN-BOUNDED-WINDOW",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-PLAN-WINDOW-CONTRACT-DIVERGENCE",
            "plan",
            "HIGH",
            "THREE_CONFLICTING_WINDOW_LAWS",
            "Source task_backlog_window defaults to a grid-aligned ten-row slice, source host rehydration uses active+8 Delta rows plus a header, and source/installed evi-plan text requests the complete exact projection.",
            "service.py:task_backlog_window;host_plan_rehydration.py:_exact_projection;evi-plan.md;installed source-command-evi-plan",
            "Different entry routes can render different row sets or reintroduce the full Plan into model context.",
            "Adopt one law everywhere: Step1 fixed header; Steps2-10 active row plus next eight; on transition query the next active-anchored window; final window may be shorter; detail only by exact ID/FTS.",
            "DELTA-PLAN-BOUNDED-WINDOW",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-HOST-PLAN-REHYDRATION-LIVE",
            "host_plan",
            "HIGH",
            "SOURCE_CORE_ABSENT_INSTALLED_AND_UNROUTED_LIVE",
            f"host_plan_rehydration.py exists in source 2.2 but not installed 2.1. Task6 has {receipt_count} host-plan rehydration receipts and session host_plan_window_present={session_profile['host_plan_window_present']}.",
            f"{SOURCE_HOST_PLAN};{INSTALLED};{SESSION_JSON};{HOST_PLAN_RECEIPTS}",
            "A manually visible Plan can disappear on renderer reload with no verified exactly-once reactivation path.",
            "Install the module only after isolated testing; observe panel identity/fingerprint; run the owning skill update_plan action once when missing/stale/window-advanced; verify the resulting artifact separately from the preparation receipt.",
            "DELTA-HOST-PLAN-PANEL-CONTINUITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-HOST-CHANGES-SURFACE-CONTINUITY",
            "host_panel",
            "MEDIUM",
            "DECLARED_WITHOUT_CONTROLLABLE_HOST_ROUTE",
            "The source receipt declares a Task/worktree-bound Changes surface, but update_plan controls only Plan and no public host Changes observation/reactivation action was found.",
            "host_plan_rehydration.py:host_surface_persistence;available host tools",
            "The plugin may report continuity it cannot enforce.",
            "Treat Changes as host-owned; record observation when available; fail closed on mismatch; never claim persistence solely from plugin state.",
            "DELTA-HOST-PLAN-PANEL-CONTINUITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-GOAL-NATIVE-BINDING-DRIFT",
            "goal",
            "HIGH",
            "ACTIVE_NATIVE_GOAL_STALE_TO_CURRENT_USER_STEER",
            "The current native Goal is active but still commands immediate Row196 implementation, stops at the obsolete Row197 HIL location, and requests a later parity subagent. The user has superseded those instructions with root-only research first and no subagent.",
            "AUTH-NATIVE-GOAL-STEP22;current Task6 user steers;official app-server Goal contract",
            "If execution resumes from the Goal text, it can bypass the research-first boundary and use stale Plan/HIL/subagent positions.",
            "Never hook Goal. Observe thread/goal/updated or native get_goal, bind objective generation and Plan SHA, flag drift, preserve usage before replacement, and require explicit user authority because a new objective resets native Goal accounting.",
            "DELTA-GOAL-NATIVE-LIFECYCLE-BINDING",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-EVI-PLAN-SOURCE-INSTALLED-DRIFT",
            "plan",
            "HIGH",
            "SOURCE_COMMAND_AND_INSTALLED_SKILL_CONTRACT_DRIFT",
            "The installed migrated evi-plan skill and source command differ on State Travel/manual Plan activation and Goal handoff, while both still instruct complete host Plan projection rather than the fixed 1+9 window. The installed text also contains encoding corruption in visible labels.",
            f"{SOURCE_EVI_PLAN};{INSTALLED_EVI_PLAN_SKILL}",
            "The same command name can produce different Plan, acceptance, and Goal behavior depending on installed package identity.",
            "Generate the migrated skill from one canonical source, byte-test the installed artifact, enforce UTF-8, and make State Travel/ordinary branches explicit without full-Plan projection.",
            "DELTA-PLAN-GOAL-COMMAND-PARITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-LIVE-PLAN-FOUR-FILE-SPLIT-BRAIN",
            "plan",
            "HIGH",
            "STATIC_ACCEPTED_SECTOR_NOT_LIVE_DELTA_AUTHORITY",
            f"PV12 contains all four stable Plan files plus three support files, but its plan_task table has {accepted_plan['counts'].get('plan_task')} rows. The current 185-task queue instead lives in root JSON plus a separate derived SQLite without live MMD/DOT/tools binding.",
            f"{PV12_PLAN_ROOT};{BACKLOG};{LIVE_PLAN_SQLITE}",
            "The Plan lane artifacts do not represent the current mutable Plan authority between accepted PVs.",
            "Keep the four stable Plan authorities synchronized from the live transactional SQLite generation and store supporting manifest/pointer/refresh receipts without sending them into model context.",
            "DELTA-PLAN-SQLITE-AUTHORITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-ENV-UOP-ABSOLUTE-AUTHORITY-SECRET-BOUNDARY",
            "env_uop",
            "CRITICAL",
            "USER_BOUND_LOCK_AND_EXTERNAL_SECRET_REQUIREMENT",
            USER_SECTOR_AND_ENV_STEER,
            "AUTH-TASK6-USER-SECTOR-ENV;ENV/UOP source and redaction contracts",
            "An adaptable schema/runtime must not accidentally treat ENV/UOP or a PAT as project-mutable data or searchable evidence.",
            "Pin exact ENV/UOP hashes and ownership; allow only effect-bounded operator use; accept credentials only through an external host secret provider; log a redacted reference and outcome, never the value; reject prompt/SQLite/lineage/asset/test storage.",
            "DELTA-ENV-UOP-AUTHORITY-BOUNDARY",
            "OPEN",
            "HIGH",
            now,
        ),
    ]

    tests = [
        (
            "TEST-PLAN-LIVE-AUTHORITY-PROFILE",
            "plan",
            f"{BACKLOG};{LIVE_PLAN_SQLITE}",
            "bounded JSON/SQLite counts, schemas, hashes, quick_check, and user_version",
            "LIVE_READ_ONLY",
            "PASS_SPLIT_AUTHORITY_CONFIRMED",
            "The JSON is current canonical input and the active SQLite is a healthy derived v1 projection without FTS.",
            "Does not validate source v2 migration or make SQLite authoritative.",
            now,
        ),
        (
            "TEST-PLAN-WINDOW-SOURCE",
            "plan",
            "tests/test_host_plan_rehydration.py;tests/test_persistent_step_task_list_contract.py;tests/test_plan_steers.py",
            "fixed header/window, steer, and projection tests",
            "UNIT_INTEGRATION",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Source contracts have targeted tests.",
            "Does not prove installed 2.1, renderer survival, or one unified window law.",
            now,
        ),
        (
            "TEST-HOST-PLAN-TASK6-LIVE",
            "host_plan",
            f"{SESSION_JSON};{HOST_PLAN_RECEIPTS}",
            "Task6 session metadata and rehydration receipt presence",
            "LIVE_READ_ONLY",
            "NEGATIVE_RUNTIME_PROOF",
            "Task6 is the current host session, but no sealed host Plan window or rehydration receipt exists.",
            "Does not prove whether the host UI currently renders the manually supplied research Plan.",
            now,
        ),
        (
            "TEST-GOAL-NATIVE-READ",
            "goal",
            "native get_goal tool;https://learn.chatgpt.com/docs/app-server",
            "current Goal status/objective hash/usage and official lifecycle route",
            "LIVE_READ_ONLY_PLUS_PRIMARY_DOCUMENTATION",
            "PASS_ACTIVE_GOAL_DRIFT_DETECTED",
            "The Goal is native thread state and its objective is stale relative to current user authority.",
            "Does not authorize replacing, clearing, completing, or resetting its usage.",
            now,
        ),
        (
            "TEST-PLAN-FOUR-STABLE-AUTHORITIES",
            "plan",
            f"{PV12_PLAN_ROOT};{SOURCE_ARTIFACT_CONTRACT}",
            "SQLite/MMD/DOT/tools plus supporting control files",
            "LIVE_READ_ONLY",
            "PASS_STATIC_PV12_FILES_PRESENT",
            "The implemented four-file artifact contract matches the user's stable sector-file law.",
            "Does not prove those four artifacts are the live post-PV12 mutable Plan authority.",
            now,
        ),
    ]

    authorities = [
        ("AUTH-PLAN-RUNTIME-SOURCE-22", "source_module", SOURCE_PLAN_RUNTIME, "LIVE_DIRTY", "Source v2 derived Plan runtime and FTS query", "Still rebuilt from JSON authority."),
        ("AUTH-HOST-PLAN-SOURCE-22", "source_module", SOURCE_HOST_PLAN, "LIVE_DIRTY", "Source fixed header plus nine-Delta host projection", "Absent from active installed 2.1."),
        ("AUTH-GOAL-USAGE-SOURCE-22", "source_module", SOURCE_GOAL_USAGE, "LIVE_DIRTY", "Goal accounting and human-only completion laws", "Does not own native Goal creation/update events."),
        ("AUTH-PLAN-SERVICE-SOURCE-22", "source_module", SOURCE_SERVICE, "LIVE_DIRTY", "Source task backlog window service", "Grid-aligns a caller-selected window, default ten rows."),
        ("AUTH-PLAN-STORE-SOURCE-22", "source_module", SOURCE_STORE, "LIVE_DIRTY", "JSON backlog persistence and SQLite rebuild", "Confirms canonical split authority."),
        ("AUTH-PLAN-MCP-SOURCE-22", "source_module", SOURCE_MCP, "LIVE_DIRTY", "Source bounded pv_task_backlog signature", "Not active installed runtime."),
        ("AUTH-PLAN-MCP-INSTALLED-21", "installed_module", INSTALLED_MCP, "ACTIVE_INSTALLED", "Installed unbounded pv_task_backlog signature", "Returns the full application.task_backlog."),
        ("AUTH-PLAN-RUNTIME-INSTALLED-21", "installed_module", INSTALLED_PLAN_RUNTIME, "ACTIVE_INSTALLED", "Installed v1 Plan projection builder", "No FTS/execution-row query tables in live DB."),
        ("AUTH-EVI-PLAN-SOURCE-22", "source_command", SOURCE_EVI_PLAN, "LIVE_DIRTY", "Source evi-plan workflow", "Differs from installed migrated skill."),
        ("AUTH-EVI-PLAN-INSTALLED-21", "installed_skill", INSTALLED_EVI_PLAN_SKILL, "ACTIVE_INSTALLED", "Installed migrated evi-plan workflow", "Stale State Travel/Goal behavior and encoding corruption."),
        ("AUTH-LIVE-TASK-BACKLOG-STEP22", "live_plan_json", BACKLOG, "LIVE_READ_ONLY", "Current canonical task backlog", json.dumps(backlog_profile, sort_keys=True, separators=(",", ":"))),
        ("AUTH-LIVE-PLAN-SQLITE-STEP22", "live_plan_sqlite", LIVE_PLAN_SQLITE, "LIVE_READ_ONLY", "Current derived Plan projection", json.dumps({k:v for k,v in live_plan.items() if k not in {"path","sha256"}}, sort_keys=True, separators=(",", ":"))),
        ("AUTH-PV12-PLAN-SQLITE-STEP22", "accepted_plan_sqlite", PV12_PLAN_SQLITE, "ACCEPTED_IMMUTABLE", "Accepted PV12 Plan sector SQLite", json.dumps({k:v for k,v in accepted_plan.items() if k not in {"path","sha256"}}, sort_keys=True, separators=(",", ":"))),
        ("AUTH-SESSION-PLAN-STATE-STEP22", "session_state", SESSION_JSON, "LIVE_READ_ONLY", "Task6 host/session Plan metadata", json.dumps(session_profile, sort_keys=True, separators=(",", ":"))),
    ]

    with connect() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO capability_route VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            routes,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            findings,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO test_evidence VALUES (?,?,?,?,?,?,?,?,?)",
            tests,
        )
        for authority_id, kind, path, freshness, role, notes in authorities:
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    authority_id,
                    kind,
                    str(path),
                    sha256_file(path),
                    path.stat().st_size,
                    freshness,
                    role,
                    notes,
                    now,
                ),
            )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-NATIVE-GOAL-STEP22",
                "native_host_goal_read",
                f"codex://thread/{TASK6_ID}/goal",
                CURRENT_GOAL_SNAPSHOT["objective_sha256"],
                CURRENT_GOAL_SNAPSHOT["objective_length"],
                "LIVE_READ_ONLY_2026-08-15",
                "Current native Goal generation",
                json.dumps(CURRENT_GOAL_SNAPSHOT, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )
        for authority_id, path, body, role in [
            (
                "AUTH-TASK6-USER-PLAN-QUERY",
                f"codex://thread/{TASK6_ID}/current-plan-query-steer",
                USER_PLAN_QUERY_STEER,
                "Fixed 1+9 Plan query and no-context-offload law",
            ),
            (
                "AUTH-TASK6-USER-SECTOR-ENV",
                f"codex://thread/{TASK6_ID}/current-plan-sector-env-steer",
                USER_SECTOR_AND_ENV_STEER,
                "Four stable sector authorities and locked ENV/UOP secret boundary",
            ),
        ]:
            encoded = body.encode("utf-8")
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    authority_id,
                    "direct_user_authority",
                    path,
                    hashlib.sha256(encoded).hexdigest().upper(),
                    len(encoded),
                    "CURRENT_BINDING_STEER",
                    role,
                    body,
                    now,
                ),
            )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-OPENAI-APP-SERVER-GOAL-STEP22",
                "official_documentation",
                "https://learn.chatgpt.com/docs/app-server",
                None,
                None,
                "LIVE_REVIEW_2026-08-15",
                "Native thread Goal set/get/clear and updated/cleared events",
                "Goal is a distinct persisted host route; it is not UserPromptSubmit and must not be simulated by a hook.",
                now,
            ),
        )

        fts_rows = [
            (
                "FTS-STEP22-PLAN-AUTHORITY",
                "research_finding",
                "Live Plan authority is JSON with a derived SQLite",
                json.dumps(
                    {
                        "backlog": backlog_profile,
                        "live_sqlite": {
                            "bytes": live_plan["bytes"],
                            "sha256": live_plan["sha256"],
                            "user_version": live_plan["user_version"],
                            "fts_tables": live_plan["fts_tables"],
                            "meta": live_plan["meta"],
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "GAP-PLAN-SQLITE-CANONICAL-AUTHORITY",
            ),
            (
                "FTS-STEP22-PLAN-WINDOW",
                "direct_user_contract",
                "One fixed header plus active-anchored nine Delta rows",
                USER_PLAN_QUERY_STEER + f" source_bounded_backlog={source_bounded_backlog}; installed_full_backlog={installed_full_backlog}; source_host_active_plus_8={source_active_anchored_host_nine}; source_grid_window_default_10={source_grid_window_ten}; evi_complete_projection_claim={evi_complete_projection_claim}.",
                "GAP-INSTALLED-PV-TASK-BACKLOG-FULL-DUMP;GAP-PLAN-WINDOW-CONTRACT-DIVERGENCE",
            ),
            (
                "FTS-STEP22-PANEL-CONTINUITY",
                "research_finding",
                "Task6 host Plan and Changes continuity",
                f"installed_host_plan_module_present={installed_host_plan_present}; Task6_host_plan_receipts={receipt_count}; session_host_plan_window_present={session_profile['host_plan_window_present']}. Source can prepare a receipt but never invokes update_plan or proves host visibility; Changes is host-owned.",
                "GAP-HOST-PLAN-REHYDRATION-LIVE;GAP-HOST-CHANGES-SURFACE-CONTINUITY",
            ),
            (
                "FTS-STEP22-GOAL",
                "research_finding",
                "Native Goal route and stale Task6 objective",
                f"goal_status={CURRENT_GOAL_SNAPSHOT['status']}; goal_objective_sha256={CURRENT_GOAL_SNAPSHOT['objective_sha256']}; goal_tokens_used={CURRENT_GOAL_SNAPSHOT['tokens_used']}; stale_markers=immediate_Row196_implementation,obsolete_Row197_HIL,parity_subagent_wait. Official route is thread/goal/set,get,clear with goal updated/cleared events; no Goal hook exists.",
                "AUTH-NATIVE-GOAL-STEP22;GAP-GOAL-NATIVE-BINDING-DRIFT",
            ),
            (
                "FTS-STEP22-FOUR-FILE-ENV",
                "direct_user_contract",
                "Four stable lane authorities and locked ENV UOP boundary",
                USER_SECTOR_AND_ENV_STEER + f" pv12_plan_four_stable_present={stable_four_present}; support_three_present={support_three_present}; source_plan_fts_bm25={source_plan_fts_bm25}; source_projection_replaced={source_projection_replaced}.",
                "PLAN-FOUR-FILE-LIVE-SECTOR;ENV-UOP-AUTHORITY-SECRET-BOUNDARY",
            ),
        ]
        for doc_id, doc_type, title, body, locator in fts_rows:
            upsert_fts(
                connection,
                doc_id=doc_id,
                doc_type=doc_type,
                title=title,
                body=body,
                evidence_locator=locator,
            )

        transition_step(
            connection,
            step_no=22,
            to_status="completed",
            evidence_locator="FTS-STEP22-PLAN-AUTHORITY;FTS-STEP22-PLAN-WINDOW;FTS-STEP22-PANEL-CONTINUITY;FTS-STEP22-GOAL",
            event_id="EVT-STEP22-PLAN-GOAL-PANEL-COMPLETE",
            occurred_at=now,
        )
        transition_step(
            connection,
            step_no=23,
            to_status="in_progress",
            evidence_locator="Installed slots, helper, forced recovery, tunnel, and binding audit",
            event_id="EVT-STEP23-INSTALLED-RECOVERY-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEP22-PLAN-GOAL-PANEL",
            event_type="RESEARCH_STEP_COMPLETED",
            payload={
                "step": 22,
                "backlog_profile": backlog_profile,
                "live_plan_sqlite_profile": live_plan,
                "accepted_pv12_plan_profile": accepted_plan,
                "session_profile": session_profile,
                "host_plan_receipt_count": receipt_count,
                "source_bounded_backlog": source_bounded_backlog,
                "installed_full_backlog": installed_full_backlog,
                "source_active_anchored_host_nine": source_active_anchored_host_nine,
                "source_grid_window_ten": source_grid_window_ten,
                "source_plan_fts_bm25": source_plan_fts_bm25,
                "source_projection_replaced": source_projection_replaced,
                "installed_host_plan_module_present": installed_host_plan_present,
                "four_stable_files_present": stable_four_present,
                "support_three_files_present": support_three_present,
                "current_goal_snapshot": CURRENT_GOAL_SNAPSHOT,
                "credential_value_observed_or_stored": False,
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
            },
            occurred_at=now,
        )
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if check != "ok":
            raise RuntimeError(f"quick_check failed: {check}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "completed_step": 22,
                "in_progress_step": 23,
                "live_plan_authority": "task_backlog.json",
                "live_plan_tasks": backlog_profile["task_count"],
                "live_plan_events": backlog_profile["event_count"],
                "live_sqlite_schema": live_plan["meta"].get("schema"),
                "live_sqlite_role": live_plan["meta"].get("projection_role"),
                "live_sqlite_fts_tables": live_plan["fts_tables"],
                "installed_pv_task_backlog_full_dump": installed_full_backlog,
                "source_bounded_backlog": source_bounded_backlog,
                "source_host_active_plus_8": source_active_anchored_host_nine,
                "source_service_grid_window_default_10": source_grid_window_ten,
                "host_plan_receipt_count": receipt_count,
                "installed_host_plan_module_present": installed_host_plan_present,
                "current_goal_status": CURRENT_GOAL_SNAPSHOT["status"],
                "current_goal_drift": True,
                "four_stable_plan_files_present": stable_four_present,
                "credential_value_observed_or_stored": False,
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
