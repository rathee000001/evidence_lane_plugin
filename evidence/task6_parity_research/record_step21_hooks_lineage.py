"""Record Step 21 hook, ChatLineage, lifecycle, compaction, and schema evidence.

This script writes only the separate Task6 parity-research database.  It never
opens the canonical Evidence Lane Plan store for mutation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
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
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"
SESSION_LINEAGE = PROJECT / "lineage" / f"{SESSION_ID}.sqlite"
TURN_CONTROL = PROJECT / "lineage" / "codex_turn_control.sqlite"
TASK3_MD = Path(r"C:\Users\rathe\Downloads\codex task 3.md")
TASK3_SQLITE = Path(
    r"D:\EvidenceLane\evidenmce_lane_plugin_task_3_chat_lenaghe_output"
    r"\project\sectors\chat_lineage\chat_lineage_sector_v001.sqlite"
)
LIFE_ARC_SQLITE = Path(
    r"C:\Users\rathe\Downloads"
    r"\LIFE_ARC_CHAT_LINEAGE_V153_THROUGH_ISLAND4288_20260814(1).sqlite"
)

OFFICIAL_DOCS = {
    "AUTH-OPENAI-HOOKS-STEP21": "https://learn.chatgpt.com/docs/hooks",
    "AUTH-OPENAI-MEMORIES-STEP21": "https://learn.chatgpt.com/docs/customization/memories",
    "AUTH-OPENAI-APP-SERVER-STEP21": "https://learn.chatgpt.com/docs/app-server",
    "AUTH-OPENAI-SKILLS-STEP21": "https://developers.openai.com/plugins/concepts/skills",
    "AUTH-OPENAI-MCP-STEP21": "https://developers.openai.com/plugins/concepts/mcp-server",
    "AUTH-OPENAI-PLUGIN-PACKAGE-STEP21": "https://developers.openai.com/plugins/build/plugins",
    "AUTH-OPENAI-PLUGIN-TEST-STEP21": "https://developers.openai.com/plugins/deploy/connect-chatgpt",
    "AUTH-OPENAI-PLUGIN-TROUBLESHOOT-STEP21": "https://developers.openai.com/plugins/deploy/troubleshooting",
    "AUTH-OPENAI-PLUGIN-GUIDELINES-STEP21": "https://developers.openai.com/plugins/app-guidelines",
    "AUTH-OPENAI-MCP-REVIEW-STEP21": "https://developers.openai.com/plugins/deploy/app-review",
    "AUTH-OPENAI-METADATA-STEP21": "https://developers.openai.com/plugins/guides/optimize-metadata",
}

VISIBLE_EVENT_TYPES = (
    "turn.visible_user_prompt",
    "turn.visible_user_steer",
    "turn.visible_user_goal",
    "turn.visible_assistant_response",
    "turn.control_prepared",
    "turn.control_committed",
    "turn.lifecycle.precompact",
    "turn.lifecycle.postcompact",
    "turn.lifecycle_exit_slip",
)

USER_SCHEMA_STEER = (
    "Per-lane SQLite schemas are initial scaffolds rather than permanent ceilings. "
    "For every lane except github_code and local_code, classified prompt and Entry Slip "
    "authority may require governed additive rows, columns, tables, relationships, foreign "
    "keys, FTS, and indexes while preserving history. The github_code and local_code schemas "
    "remain hardened and change only under explicit user authorization."
)

USER_HOOK_RECOVERY_STEER = (
    "The active hook runtime first showed four attempted hooks and four failures. "
    "After a later correction, hooks looped, mixed event responsibilities, and appeared "
    "to execute on their own. Recovery required removing the Goal, disabling the plugin, "
    "and restarting the app. This is a direct user-observed runtime sequence; it does not "
    "by itself prove which hook, Goal transition, host event, or wrapper caused the loop."
)


def hook_profile(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events: list[str] = []
    commands: list[str] = []
    windows_commands: list[str] = []
    timeouts: dict[str, list[int]] = {}
    for event, matchers in payload["hooks"].items():
        events.append(event)
        for matcher in matchers:
            for hook in matcher.get("hooks", []):
                commands.append(str(hook.get("command", "")))
                windows_commands.append(str(hook.get("commandWindows", "")))
                timeouts.setdefault(event, []).append(int(hook.get("timeout", 0)))
    return {
        "events": events,
        "commands": commands,
        "windows_commands": windows_commands,
        "timeouts": timeouts,
        "python_wrapper_all": all("invoke_hook.py" in value for value in commands),
        "windows_wrapper_all": all("invoke_hook.ps1" in value for value in windows_commands),
        "windows_system_powershell_all": all(
            "System32\\WindowsPowerShell" in value for value in windows_commands
        ),
        "windows_hidden_all": all("-WindowStyle Hidden" in value for value in windows_commands),
    }


def lineage_profile(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    total = int(connection.execute("SELECT COUNT(*) FROM lineage_event").fetchone()[0])
    counts = {
        event_type: int(
            connection.execute(
                "SELECT COUNT(*) FROM lineage_event WHERE event_type = ?", (event_type,)
            ).fetchone()[0]
        )
        for event_type in VISIBLE_EVENT_TYPES
    }
    task6_fts = int(
        connection.execute(
            "SELECT COUNT(*) FROM lineage_fts WHERE lineage_fts MATCH ?", ("Task6",)
        ).fetchone()[0]
    )
    quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    connection.close()
    return {
        "total_events": total,
        "target_event_counts": counts,
        "target_event_total": sum(counts.values()),
        "task6_fts_hits": task6_fts,
        "quick_check": quick_check,
        "codex_turn_control_present": TURN_CONTROL.exists(),
    }


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
    source_hooks = SOURCE / "hooks" / "hooks.json"
    installed_hooks = INSTALLED / "hooks" / "hooks.json"
    hook_contract = PACKAGE / "hook_contract.py"
    hook_runtime = PACKAGE / "hook_skill_runtime.py"
    turn_control = PACKAGE / "codex_turn_control.py"
    lineage_module = PACKAGE / "lineage.py"
    capture_routing = PACKAGE / "capture_routing.py"
    source_profile = hook_profile(source_hooks)
    installed_profile = hook_profile(installed_hooks)
    live_lineage = lineage_profile(SESSION_LINEAGE)

    source_hook_files = sorted(path.name for path in (SOURCE / "hooks").glob("*") if path.is_file())
    installed_hook_files = sorted(path.name for path in (INSTALLED / "hooks").glob("*") if path.is_file())
    production_python = list(PACKAGE.rglob("*.py"))
    exit_slip_references = sum(
        path.read_text(encoding="utf-8", errors="replace").count("seal_lifecycle_exit_slip(")
        for path in production_python
    )

    routes = [
        route(
            "HOOK-SOURCE-22-CONTRACT",
            "hooks",
            "eight-event deterministic source hook surface",
            "hook_contract.validate_hook_contract",
            "IMPLEMENTED_AND_SOURCE_TESTED",
            "plugins/evidence-lane-plugin/hooks/hooks.json;src/evidence_lane_plugin/hook_contract.py",
            skill_path="skills/evi/SKILL.md;skills/evidence-lane-code-lifecycle/SKILL.md",
            skill_status="INSTRUCTION_PRESENT",
            command_path="hooks/invoke_hook.py;hooks/invoke_hook.ps1",
            command_status="WRAPPED_SOURCE_COMMANDS",
            installed_status="NOT_ACTIVE_INSTALLED_IDENTITY",
            runtime_status="SOURCE_PROOF_ONLY",
            test_status="TASK3_REPORTED_ISOLATED_8_EVENT_PASS",
            test_locators="tests/test_hook_lifecycle_contract.py;Task3.md:2739-2825",
            gap_class="SOURCE_INSTALLED_PARITY_GAP",
            decision="INSTALL_AND_VERIFY_EXACT_SOURCE_BYTES_BEFORE_RUNTIME_CLAIM",
            proposed_delta_key="DELTA-HOOK-INSTALLED-RUNTIME",
            notes="The repaired source uses Python and hidden system-PowerShell wrappers; it is not the active 2.1 package.",
            now=now,
        ),
        route(
            "HOOK-INSTALLED-21-RUNTIME",
            "hooks",
            "active installed eight-event hook surface",
            None,
            "OLDER_DIRECT_HANDLER_PACKAGE",
            str(installed_hooks),
            skill_path="installed skills/evi and lifecycle",
            skill_status="OLDER_INSTALLED_INSTRUCTIONS",
            command_path="installed hooks direct python handlers",
            command_status="DIRECT_PATH_AND_INTERPRETER_AMBIGUITY",
            installed_status="ACTIVE_2_1_RUNTIME",
            runtime_status="TASK3_OBSERVED_FAILURE_THEN_USER_OBSERVED_LOOP_AND_EVENT_MIXING",
            test_status="HISTORICAL_LIVE_FAILURE_AND_MANUAL_RECOVERY_EXACT_CAUSE_UNPROVEN",
            test_locators="Task3.md:9219-9234;9271-9285",
            gap_class="INSTALLED_RUNTIME_FAILURE",
            decision="REPAIR_INSTALL_PATH_THEN_RUN_EXACT_INSTALLED_INVOCATION_MATRIX",
            proposed_delta_key="DELTA-HOOK-INSTALLED-RUNTIME",
            notes="The exact failing cause remains unknown. The later loop required Goal removal, plugin disablement, and app restart; those recovery actions are observed facts, not proof that Goal was the root cause.",
            now=now,
        ),
        route(
            "HOOK-EVENT-ISOLATION-AND-NO-LOOP",
            "hooks",
            "host-trigger-only event isolation with no autonomous hook loop",
            "hook_contract.HOOK_OWNER_CLAIMS",
            "SOURCE_CONTRACT_DECLARED_RUNTIME_BREACH_OBSERVED",
            "src/evidence_lane_plugin/hook_contract.py;hooks/*.py;official OpenAI hooks contract",
            skill_path="skills/evidence-lane-code-lifecycle/SKILL.md",
            skill_status="OWNERSHIP_LAW_DECLARED",
            command_path="hooks/invoke_hook.py;hooks/invoke_hook.ps1",
            command_status="SOURCE_WRAPPERS_PRESENT",
            installed_status="USER_DISABLED_AFTER_LOOP",
            runtime_status="USER_OBSERVED_EVENT_MIXING_SELF_EXECUTION_AND_LOOP",
            test_status="MANUAL_RECOVERY_OBSERVATION_REPRODUCTION_PENDING",
            test_locators="codex://thread/01a0036f-32fa-79b2-8846-9c716d4fe777/current-hook-correction;https://learn.chatgpt.com/docs/hooks",
            gap_class="HOST_EVENT_ISOLATION_AND_REENTRANCY_GAP",
            decision="ADD_EVENT_ID_CORRELATION_REENTRANCY_GUARD_NO_LOOP_OUTPUT_AND_KILL_SWITCH_TESTS",
            proposed_delta_key="DELTA-HOOK-EVENT-ISOLATION",
            notes="Each hook may respond only to its host event and transport bounded evidence. It must not trigger another lifecycle event, own Goal or Plan behavior, or emit Stop continuation unless explicitly governed and tested.",
            now=now,
        ),
        route(
            "HOOK-SKILL-BEHAVIOR-HANDOFF",
            "hooks",
            "automatic transition from hook PREPARE to model skill behavior",
            "hook_skill_runtime._behavior_query_handoff_receipt",
            "RECEIPT_ONLY",
            "src/evidence_lane_plugin/hook_skill_runtime.py",
            mcp_tool="pv_status,pv_task_backlog,pv_query",
            mcp_status="REQUIRED_AFTER_HOOK_BUT_NOT_INVOKED_BY_HOOK",
            skill_path="skills/evi/SKILL.md",
            skill_status="MODEL_INSTRUCTIONS_ONLY",
            installed_status="NO_AUTOMATIC_SKILL_TRIGGER",
            runtime_status="INSTRUCTION_DEPENDENT",
            test_locators="tests/test_hook_lifecycle_contract.py",
            gap_class="BEHAVIOR_ORCHESTRATION_GAP",
            decision="ADD_EXPLICIT_HOST_SKILL_HANDOFF_RECEIPT_AND_FAIL_CLOSED_VERIFICATION",
            proposed_delta_key="DELTA-HOOK-BEHAVIOR-ORCHESTRATION",
            notes="A Python module named skill runtime is not a model skill invocation; hooks cannot themselves trigger skills.",
            now=now,
        ),
        route(
            "CHATLINEAGE-AUTO-TURN-CAPTURE",
            "chat_lineage",
            "automatic visible prompt, steer, Goal, response, PREPARE, and COMMIT capture",
            "codex_turn_control.prepare_turn/commit_turn",
            "IMPLEMENTED_CORE",
            "src/evidence_lane_plugin/codex_turn_control.py;hook_skill_runtime.py",
            sdk_module="chat_lineage",
            sdk_operation="append",
            sdk_status="DECLARED_HANDLER_ABSENT",
            skill_path="skills/evidence-lane-code-lifecycle/SKILL.md",
            skill_status="CONTRACT_PRESENT",
            installed_status="NOT_ACTIVE_IN_CURRENT_2_1_RUNTIME",
            runtime_status="ZERO_TARGET_EVENTS_AND_NO_TURN_CONTROL_DB",
            test_status="LIVE_READ_ONLY_NEGATIVE_PROOF",
            test_locators=str(SESSION_LINEAGE),
            gap_class="IMPLEMENTED_CORE_BUT_UNROUTED",
            decision="ROUTE_EVERY_VISIBLE_TURN_THROUGH_TYPED_APPEND_AND_PROVE_TASK6_LOCATORS",
            proposed_delta_key="DELTA-CHATLINEAGE-AUTO-CAPTURE",
            notes="The session store is healthy and populated historically, but none of the new automatic event types or Task6 FTS locators exist.",
            now=now,
        ),
        route(
            "CHATLINEAGE-DURABLE-STORAGE",
            "chat_lineage",
            "append-preserving typed and scalable ChatLineage storage",
            "lineage.ChatLineage.append_event/_project_sqlite",
            "LOGICAL_APPEND_WITH_FULL_FILE_REWRITE_AND_SQLITE_REBUILD",
            "src/evidence_lane_plugin/lineage.py;capture_routing.py",
            sdk_module="chat_lineage",
            sdk_operation="append",
            sdk_status="DECLARED_HANDLER_ABSENT",
            installed_status="GENERIC_JSON_PAYLOAD_PROJECTION",
            runtime_status="LIVE_DB_PRESENT_BUT_AUTOMATIC_TASK6_CAPTURE_ABSENT",
            test_locators="tests/test_lineage.py;tests/test_codex_turn_control.py",
            gap_class="DURABILITY_SCHEMA_AND_SCALABILITY_GAP",
            decision="USE_TRANSACTIONAL_APPEND_MIGRATIONS_TYPED_RELATIONS_AND_INCREMENTAL_FTS",
            proposed_delta_key="DELTA-CHATLINEAGE-DURABLE-SCHEMA",
            notes="Logical hash-chain append semantics do not equal append-in-place storage; current JSONL and SQLite projections are rebuilt wholesale.",
            now=now,
        ),
        route(
            "ENTRY-EXIT-LIFECYCLE-SLIPS",
            "lifecycle",
            "Entry Slip on turn PREPARE and Exit Slip at genuine boundaries",
            "codex_turn_control.prepare_turn/seal_lifecycle_exit_slip",
            "ENTRY_CORE_PRESENT_EXIT_CORE_ONLY",
            "src/evidence_lane_plugin/codex_turn_control.py",
            skill_path="skills/evidence-lane-code-lifecycle/SKILL.md",
            skill_status="BOUNDARY_LAW_PRESENT",
            installed_status="NOT_LIVE_FOR_TASK6",
            runtime_status="EXIT_DEFINITION_WITHOUT_PRODUCTION_CALLER",
            test_status="UNIT_TESTS_PRESENT_NOT_EXECUTED_THIS_PHASE",
            test_locators="tests/test_codex_turn_control.py",
            gap_class="IMPLEMENTED_CORE_BUT_UNROUTED",
            decision="ADD_ONE_GENUINE_BOUNDARY_ROUTE_AND_EXACTLY_ONCE_RECEIPT",
            proposed_delta_key="DELTA-ENTRY-EXIT-LIFECYCLE-ROUTE",
            notes=f"Production source reference count for seal_lifecycle_exit_slip( is {exit_slip_references}; observed references are definition-only.",
            now=now,
        ),
        route(
            "COMPACTION-CONTINUITY",
            "compaction",
            "PreCompact seal and PostCompact deterministic rehydration",
            "hook_skill_runtime.capture_precompact/capture_postcompact",
            "SOURCE_CORE_PRESENT",
            "hooks/lifecycle_boundary.py;src/evidence_lane_plugin/hook_skill_runtime.py",
            skill_path="skills/evidence-lane-code-lifecycle/SKILL.md",
            skill_status="CONTRACT_PRESENT",
            command_path="hooks/invoke_hook.py;hooks/invoke_hook.ps1",
            command_status="SOURCE_WRAPPERS_PRESENT",
            installed_status="OLDER_INVALID_OUTPUT_SHAPE",
            runtime_status="NO_LIVE_PRE_OR_POST_COMPACT_EVENTS",
            test_status="OFFICIAL_CONTRACT_PLUS_SOURCE_TESTS_NOT_LIVE_PROOF",
            test_locators="tests/test_hook_lifecycle_contract.py;https://learn.chatgpt.com/docs/hooks;https://learn.chatgpt.com/docs/app-server",
            gap_class="INSTALLED_AND_APP_SERVER_CORRELATION_GAP",
            decision="CORRELATE_HOOK_RECEIPTS_WITH_CONTEXT_COMPACTION_ITEM_AND_REHYDRATE_PANEL",
            proposed_delta_key="DELTA-COMPACTION-CONTINUITY",
            notes="thread/compact/start and contextCompaction are current app-server contracts; thread/compacted is deprecated.",
            now=now,
        ),
        route(
            "HOST-MEMORY-BOUNDARY",
            "memory",
            "host memory versus governed Agent Learning authority",
            None,
            "SEPARATE_HOST_FEATURE",
            "https://learn.chatgpt.com/docs/customization/memories;skills/evi-learning/SKILL.md",
            sdk_module="agent_learning",
            sdk_status="SOURCE_OPERATIONS_PRESENT",
            skill_path="skills/evi-learning/SKILL.md",
            skill_status="SOURCE_ONLY",
            installed_status="HOST_MEMORY_OFF_BY_DEFAULT_AND_NOT_EVIDENCE_AUTHORITY",
            runtime_status="NO_GOVERNED_INTEGRATION_FOUND",
            test_status="OFFICIAL_CONTRACT_REVIEW",
            test_locators="https://learn.chatgpt.com/docs/customization/memories",
            gap_class="AUTHORITY_BOUNDARY_GAP",
            decision="KEEP_HOST_MEMORY_NONAUTHORITATIVE_AND_LINK_ONLY_EXPLICIT_PROVENANCE",
            proposed_delta_key="DELTA-HOST-MEMORY-BOUNDARY",
            notes="Host-generated memories under ~/.codex/memories must not be conflated with Evidence Lane Agent Learning candidates or accepted authority.",
            now=now,
        ),
        route(
            "GOVERNED-LANE-SCHEMA-EVOLUTION",
            "lane_schema",
            "Entry-Slip-classified additive lane schema evolution",
            None,
            "USER_BOUND_CONTRACT_RUNTIME_ABSENT",
            "codex://thread/01a0036f-32fa-79b2-8846-9c716d4fe777/current-schema-steer;lane_engine.py",
            mcp_tool="source_custom_schema_compile",
            mcp_status="EXTRACTION_SCHEMA_ONLY_NOT_PHYSICAL_MIGRATION",
            skill_path="skills/evi-source-intake/SKILL.md",
            skill_status="NO_PHYSICAL_LANE_MIGRATION_WORKFLOW",
            installed_status="GENERIC_FIXED_SCHEMA",
            runtime_status="NO_GOVERNED_ADDITIVE_EVOLUTION",
            test_status="USER_AUTHORITY_PLUS_SCHEMA_COMPARISON",
            test_locators=f"{TASK3_SQLITE};{LIFE_ARC_SQLITE};tests/test_custom_source_schema.py",
            gap_class="MISSING_GOVERNED_SCHEMA_EVOLUTION",
            decision="ADD_MIGRATION_LEDGER_DDL_HASH_FK_FTS_INTEGRITY_AND_EXPLICIT_HARDENED_LANE_GATE",
            proposed_delta_key="DELTA-GOVERNED-LANE-SCHEMA-EVOLUTION",
            notes="Sixteen lanes may evolve additively from classified Entry Slips; github_code and local_code remain hardened unless the user explicitly authorizes a schema change.",
            now=now,
        ),
    ]

    findings = [
        (
            "GAP-HOOK-INSTALLED-RUNTIME",
            "hooks",
            "CRITICAL",
            "SOURCE_FIXED_INSTALLED_STALE_RUNTIME_FAILED",
            "The 2.2 source has deterministic wrappers and corrected event outputs, while the active 2.1 package directly invokes handlers. Task3 first observed four attempted hooks and four exit-code-1 failures; after a later correction the user observed looping, event mixing, and apparent self-execution requiring Goal removal, plugin disablement, and app restart.",
            "AUTH-HOOKS-SOURCE-22;AUTH-HOOKS-INSTALLED-21;AUTH-TASK6-USER-HOOK-RECOVERY;Task3.md:9219-9234,9271-9285",
            "Source tests cannot establish active installed behavior, and the recovery sequence does not isolate one root cause.",
            "Install exact bytes only in an isolated harness; verify all eight host-triggered commands, event correlation, reentrancy denial, Stop no-loop output, kill switch, and restart recovery before enabling the plugin.",
            "DELTA-HOOK-INSTALLED-RUNTIME",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-HOOK-EVENT-ISOLATION-LOOP",
            "hooks",
            "CRITICAL",
            "USER_OBSERVED_EVENT_MIXING_AND_REENTRANCY",
            USER_HOOK_RECOVERY_STEER,
            "AUTH-TASK6-USER-HOOK-RECOVERY;official OpenAI hooks contract;source hook ownership claims",
            "A lifecycle transport that loops or crosses event ownership can mutate control state repeatedly and make Plan, Goal, and lineage receipts unreliable.",
            "Enforce host-event correlation IDs, per-event input/output schemas, exactly-once dedupe, a reentrancy lock, empty Stop output under no-loop policy, bounded SessionEnd, and a persistent kill switch verified before install activation.",
            "DELTA-HOOK-EVENT-ISOLATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-HOOK-SKILL-ORCHESTRATION",
            "hooks",
            "HIGH",
            "INSTRUCTION_ONLY_HANDOFF",
            "The PREPARE hook records required pv_status, pv_task_backlog, pv_query, and update_plan behavior but does not and cannot invoke a model skill. The post-hook behavior remains model-instruction dependent.",
            "hook_skill_runtime._behavior_query_handoff_receipt;official skills and hooks docs",
            "A successful hook does not prove native reads, bounded Plan rehydration, or Goal behavior occurred.",
            "Issue and consume an explicit behavior-handoff receipt with fail-closed verification after the owning skill executes.",
            "DELTA-HOOK-BEHAVIOR-ORCHESTRATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CHATLINEAGE-AUTO-CAPTURE",
            "chat_lineage",
            "CRITICAL",
            "IMPLEMENTED_CORE_BUT_UNROUTED",
            f"The live session lineage contains {live_lineage['total_events']} events but zero automatic visible prompt/steer/Goal/response, PREPARE/COMMIT, compaction, or Exit-Slip events; Task6 FTS hits are zero and codex_turn_control.sqlite is absent.",
            str(SESSION_LINEAGE),
            "Task6 work is not automatically appended into typed, queryable governed lineage.",
            "Route every visible turn through exactly-once typed PREPARE/COMMIT and prove Task6 locators, hashes, FTS, and restart recovery.",
            "DELTA-CHATLINEAGE-AUTO-CAPTURE",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CHATLINEAGE-DURABILITY",
            "chat_lineage",
            "HIGH",
            "LOGICAL_APPEND_PHYSICAL_REWRITE",
            "ChatLineage reads and rewrites the complete JSONL on append and deletes/rebuilds its SQLite projection when the authority hash changes. The projection is generic JSON payload rather than the rich typed Task3/Life Arc structure.",
            "lineage.py;capture_routing.py;Task3 and Life Arc SQLite profiles",
            "Large histories incur avoidable rewrite/rebuild cost and expose a larger interruption surface.",
            "Use transactional append-only events, incremental projection/FTS, typed versioned tables, migration receipts, and repairable cursors.",
            "DELTA-CHATLINEAGE-DURABLE-SCHEMA",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-EXIT-SLIP-PRODUCTION-ROUTE",
            "lifecycle",
            "HIGH",
            "IMPLEMENTED_CORE_BUT_UNROUTED",
            "The genuine-boundary Exit-Slip sealer exists and is tested, but no production MCP, service, SDK, hook, or command caller was found; SessionEnd is intentionally transport-only.",
            "codex_turn_control.seal_lifecycle_exit_slip;production reference scan",
            "Exit semantics cannot be claimed from core/test presence alone.",
            "Expose exactly one owning lifecycle route, idempotency key, allowed-reason gate, lineage append, and continuity receipt.",
            "DELTA-ENTRY-EXIT-LIFECYCLE-ROUTE",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-COMPACTION-CONTINUITY",
            "compaction",
            "HIGH",
            "SOURCE_CORE_NOT_ACTIVE_OR_CORRELATED",
            "The source contains corrected PreCompact/PostCompact handling, but the installed 2.1 outputs are stale, the live lineage has no compaction events, and no app-server contextCompaction correlation receipt was found.",
            "source/installed hooks;live lineage;official hooks/app-server docs",
            "Plan/panel/Goal continuity across compaction remains unproven in the active runtime.",
            "Bind hook receipts to thread/compact/start and contextCompaction item IDs, then prove bounded native rehydration without context dumping.",
            "DELTA-COMPACTION-CONTINUITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-HOST-MEMORY-BOUNDARY",
            "memory",
            "MEDIUM",
            "AUTHORITY_BOUNDARY_UNSPECIFIED",
            "Official host memories are background-generated, off by default, and stored outside the project. No explicit Evidence Lane contract prevents treating them as accepted Agent Learning evidence.",
            "official memories docs;Agent Learning source skill",
            "Convenience memory could be mistaken for governed or accepted project authority.",
            "Declare host memory nonauthoritative, require explicit provenance when imported, and keep Learning candidate/HIL semantics separate.",
            "DELTA-HOST-MEMORY-BOUNDARY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-GOVERNED-LANE-SCHEMA-EVOLUTION",
            "lane_schema",
            "CRITICAL",
            "USER_REQUIRED_CAPABILITY_ABSENT",
            USER_SCHEMA_STEER,
            "current Task6 user steer;lane_engine.py;Task3/Life Arc SQLite comparison",
            "The fixed generic schema cannot accumulate project-specific typed relationships without code edits or destructive reconstruction.",
            "Add governed additive migrations, schema ledger, DDL hashes, FKs, indexes, FTS rebuild proof, compatibility checks, and a hard explicit-user gate for github_code/local_code.",
            "DELTA-GOVERNED-LANE-SCHEMA-EVOLUTION",
            "OPEN",
            "HIGH",
            now,
        ),
    ]

    tests = [
        (
            "TEST-HOOK-SOURCE-22",
            "hooks",
            "tests/test_hook_lifecycle_contract.py;tests/test_codex_turn_control.py",
            "source 2.2 hook lifecycle and turn control",
            "UNIT_INTEGRATION_PLUS_TASK3_ISOLATED_REPORT",
            "PRESENT_TASK3_REPORTED_PASS_NOT_EXECUTED_THIS_PHASE",
            "The repaired source command wrappers and event-output contracts can pass in an isolated source test.",
            "Does not prove the active installed 2.1 runtime or Task6 automatic capture.",
            now,
        ),
        (
            "TEST-HOOK-INSTALLED-21-HISTORICAL",
            "hooks",
            str(TASK3_MD),
            "Task3 bounded locators 9219-9234 and 9271-9285",
            "HISTORICAL_HOST_RUNTIME",
            "FAIL_4_OF_4_EXIT_1_CAUSE_UNPROVEN",
            "Four attempted active-runtime hook invocations failed.",
            "Does not identify the exact failing line because stderr was unavailable.",
            now,
        ),
        (
            "TEST-HOOK-USER-RECOVERY-OBSERVATION",
            "hooks",
            "codex://thread/01a0036f-32fa-79b2-8846-9c716d4fe777/current-hook-correction",
            "user-observed post-correction loop and manual recovery sequence",
            "DIRECT_USER_OBSERVATION",
            "FAIL_EVENT_MIXING_LOOP_RECOVERY_REQUIRED",
            "The post-correction runtime was unsafe enough that the user removed the Goal, disabled the plugin, and restarted the app.",
            "Does not identify a single causal hook or prove that Goal was the root cause without correlated host/runtime logs.",
            now,
        ),
        (
            "TEST-CHATLINEAGE-LIVE-TASK6",
            "chat_lineage",
            str(SESSION_LINEAGE),
            "immutable event counts, Task6 FTS, quick_check, turn-control presence",
            "LIVE_READ_ONLY",
            "NEGATIVE_RUNTIME_PROOF",
            "The store is readable and healthy, yet automatic Task6 lifecycle event families are absent.",
            "Does not prove source core is defective; it proves current routing is inactive.",
            now,
        ),
        (
            "TEST-CHATLINEAGE-SCHEMA-BENCHMARK",
            "chat_lineage",
            f"{TASK3_SQLITE};{LIFE_ARC_SQLITE}",
            "typed table and relationship comparison",
            "READ_ONLY_BENCHMARK",
            "PASS_COMPARISON_RECORDED",
            "Typed prompt/response/receipt/recovery and additive-versioned structures are feasible.",
            "Does not prove every benchmark table has adequate FK or strictness guarantees.",
            now,
        ),
        (
            "TEST-OFFICIAL-HOOK-COMPACTION-MEMORY",
            "official_contract",
            ";".join(OFFICIAL_DOCS.values()),
            "current host hooks, skills, MCP, memory, and app-server contracts",
            "PRIMARY_DOCUMENTATION",
            "PASS_CONTRACT_REVIEW",
            "Current event shapes, trigger ownership, SessionEnd limits, memory boundary, and compaction item semantics.",
            "Does not prove Evidence Lane implementation parity.",
            now,
        ),
    ]

    authorities = [
        ("AUTH-HOOKS-SOURCE-22", "source_hook_config", source_hooks, "LIVE_DIRTY", "Source 2.2 eight-event hook configuration", "Deterministic cross-platform wrapper commands and 3-second SessionEnd."),
        ("AUTH-HOOK-CONTRACT-SOURCE-22", "source_module", hook_contract, "LIVE_DIRTY", "Source hook validation and event contract", "Validates exact inventory and wrapper forms."),
        ("AUTH-HOOK-RUNTIME-SOURCE-22", "source_module", hook_runtime, "LIVE_DIRTY", "Hook-to-behavior handoff implementation", "Records required skill-owned reads but cannot trigger a model skill."),
        ("AUTH-TURN-CONTROL-SOURCE-22", "source_module", turn_control, "LIVE_DIRTY", "Typed turn control and Entry/Exit core", "Core is present; current project control database is absent."),
        ("AUTH-LINEAGE-SOURCE-22", "source_module", lineage_module, "LIVE_DIRTY", "Hash-chain JSONL and SQLite projection", "Logical append with whole-file rewrite and projection rebuild."),
        ("AUTH-CAPTURE-ROUTING-SOURCE-22", "source_module", capture_routing, "LIVE_DIRTY", "Sparse/full capture classification", "Does not own physical lane schema evolution."),
        ("AUTH-HOOKS-INSTALLED-21", "installed_hook_config", installed_hooks, "ACTIVE_INSTALLED", "Active 2.1 hook configuration", "Direct handler commands, stale output contracts, and 10-second SessionEnd."),
        ("AUTH-TASK3-MD-STEP21", "chat_lineage_markdown", TASK3_MD, "USER_SUPPLIED_BOUNDED", "Task3 historical hook and source-test evidence", "Use only bounded locators; never load the document wholesale into model context."),
        ("AUTH-LIVE-LINEAGE-STEP21", "sqlite_live_read_only", SESSION_LINEAGE, "LIVE_READ_ONLY", "Current governed session lineage", json.dumps(live_lineage, sort_keys=True, separators=(",", ":"))),
        ("AUTH-TASK3-SQLITE-STEP21", "sqlite_benchmark", TASK3_SQLITE, "USER_SUPPLIED_READ_ONLY", "Task3 typed ChatLineage benchmark", "Populated typed turn, prompt, response, receipt, and relationship tables."),
        ("AUTH-LIFE-ARC-SQLITE-STEP21", "sqlite_benchmark", LIFE_ARC_SQLITE, "USER_SUPPLIED_READ_ONLY", "Life Arc rich ChatLineage benchmark", "Rich typed append, recovery, file-link, and additive-versioned structures; not every relationship is equally hardened."),
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
        for authority_id, url in OFFICIAL_DOCS.items():
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    authority_id,
                    "official_documentation",
                    url,
                    None,
                    None,
                    "LIVE_REVIEW_2026-08-15",
                    "Primary host/plugin contract",
                    "Used for Step21 contract comparison; implementation claims require separate local proof.",
                    now,
                ),
            )
        schema_steer_sha = hashlib.sha256(USER_SCHEMA_STEER.encode("utf-8")).hexdigest().upper()
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-TASK6-USER-SCHEMA-EVOLUTION",
                "direct_user_authority",
                "codex://thread/01a0036f-32fa-79b2-8846-9c716d4fe777/current-schema-steer",
                schema_steer_sha,
                len(USER_SCHEMA_STEER.encode("utf-8")),
                "CURRENT_BINDING_STEER",
                "Lane schema evolution and hardened-lane exception",
                USER_SCHEMA_STEER,
                now,
            ),
        )
        hook_recovery_sha = hashlib.sha256(USER_HOOK_RECOVERY_STEER.encode("utf-8")).hexdigest().upper()
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-TASK6-USER-HOOK-RECOVERY",
                "direct_user_observation",
                "codex://thread/01a0036f-32fa-79b2-8846-9c716d4fe777/current-hook-correction",
                hook_recovery_sha,
                len(USER_HOOK_RECOVERY_STEER.encode("utf-8")),
                "CURRENT_BINDING_CORRECTION",
                "Hook failure chronology and manual recovery",
                USER_HOOK_RECOVERY_STEER,
                now,
            ),
        )

        for lane_id, schema_status, notes in connection.execute(
            "SELECT lane_id,schema_asset_status,notes FROM lane_contract"
        ).fetchall():
            if lane_id in {"github_code", "local_code"}:
                suffix = "USER_HARDENED_EXPLICIT_AUTHORIZATION_ONLY"
                rule = " Schema is hardened; any physical schema change requires explicit user authorization."
            else:
                suffix = "GOVERNED_ADDITIVE_EVOLUTION_REQUIRED"
                rule = " Classified Entry Slips may authorize append/history-preserving additive schema evolution with migration receipts."
            merged_status = schema_status if suffix in schema_status else f"{schema_status}|{suffix}"
            merged_notes = notes if rule.strip() in notes else notes + rule
            connection.execute(
                "UPDATE lane_contract SET schema_asset_status=?,gap_class=?,notes=?,updated_at=? WHERE lane_id=?",
                (
                    merged_status,
                    "GOVERNED_SCHEMA_EVOLUTION_RUNTIME_GAP",
                    merged_notes,
                    now,
                    lane_id,
                ),
            )

        fts_rows = [
            (
                "FTS-STEP21-HOOK-INSTALLED",
                "research_finding",
                "Source-repaired versus installed-live hook boundary",
                f"source_events={source_profile['events']}; source_wrapped={source_profile['python_wrapper_all'] and source_profile['windows_wrapper_all']}; installed_events={installed_profile['events']}; installed_wrapped={installed_profile['python_wrapper_all'] or installed_profile['windows_wrapper_all']}; Task3 first observed 4 of 4 active hook attempts exit 1. A later user-observed correction loop mixed event responsibilities and required Goal removal, plugin disablement, and app restart; exact cause remains unknown.",
                "HOOK-SOURCE-22-CONTRACT;HOOK-INSTALLED-21-RUNTIME;HOOK-EVENT-ISOLATION-AND-NO-LOOP",
            ),
            (
                "FTS-STEP21-CHATLINEAGE-LIVE",
                "research_finding",
                "Live Task6 ChatLineage routing proof",
                json.dumps(live_lineage, sort_keys=True, separators=(",", ":")),
                "AUTH-LIVE-LINEAGE-STEP21;GAP-CHATLINEAGE-AUTO-CAPTURE",
            ),
            (
                "FTS-STEP21-ENTRY-EXIT-COMPACTION",
                "research_finding",
                "Entry Exit and compaction lifecycle routing",
                f"Exit-Slip core exists but production source reference count is {exit_slip_references} and no public caller was found. Live PREPARE COMMIT PreCompact PostCompact and Exit-Slip event counts are zero. Current app-server authority is thread/compact/start with contextCompaction items.",
                "ENTRY-EXIT-LIFECYCLE-SLIPS;COMPACTION-CONTINUITY",
            ),
            (
                "FTS-STEP21-SCHEMA-EVOLUTION",
                "direct_user_contract",
                "Governed additive lane schema evolution",
                USER_SCHEMA_STEER + " Required proof includes migration ledger, DDL hashes, FK/integrity checks, indexes, incremental FTS rebuild, compatibility, and rollback/rebuild receipts.",
                "AUTH-TASK6-USER-SCHEMA-EVOLUTION;GAP-GOVERNED-LANE-SCHEMA-EVOLUTION",
            ),
            (
                "FTS-STEP21-MEMORY-BOUNDARY",
                "research_finding",
                "Host memory is not Agent Learning authority",
                "Host memories are background-generated and off by default. Keep them outside accepted Evidence Lane authority unless explicitly imported with provenance; Agent Learning candidate and HIL laws remain separate.",
                "AUTH-OPENAI-MEMORIES-STEP21;HOST-MEMORY-BOUNDARY",
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
            step_no=21,
            to_status="completed",
            evidence_locator="FTS-STEP21-HOOK-INSTALLED;FTS-STEP21-CHATLINEAGE-LIVE;FTS-STEP21-SCHEMA-EVOLUTION",
            event_id="EVT-STEP21-HOOK-LINEAGE-COMPLETE",
            occurred_at=now,
        )
        transition_step(
            connection,
            step_no=22,
            to_status="in_progress",
            evidence_locator="Plan/Goal/FTS/panel continuity source audit",
            event_id="EVT-STEP22-PLAN-GOAL-PANEL-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEP21-HOOK-LINEAGE",
            event_type="RESEARCH_STEP_COMPLETED",
            payload={
                "step": 21,
                "source_hook_profile": source_profile,
                "installed_hook_profile": installed_profile,
                "source_hook_files": source_hook_files,
                "installed_hook_files": installed_hook_files,
                "live_lineage": live_lineage,
                "exit_slip_production_reference_count": exit_slip_references,
                "dynamic_schema_rule": {
                    "governed_additive_lanes": 16,
                    "explicit_authorization_only": ["github_code", "local_code"],
                },
                "user_observed_hook_recovery": USER_HOOK_RECOVERY_STEER,
                "canonical_plan_mutated": False,
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
                "completed_step": 21,
                "in_progress_step": 22,
                "source_hook_file_count": len(source_hook_files),
                "installed_hook_file_count": len(installed_hook_files),
                "source_wrappers": bool(
                    source_profile["python_wrapper_all"]
                    and source_profile["windows_wrapper_all"]
                    and source_profile["windows_system_powershell_all"]
                    and source_profile["windows_hidden_all"]
                ),
                "installed_wrappers": bool(
                    installed_profile["python_wrapper_all"]
                    or installed_profile["windows_wrapper_all"]
                ),
                "live_lineage": live_lineage,
                "exit_slip_production_reference_count": exit_slip_references,
                "governed_additive_schema_lanes": 16,
                "explicit_authorization_schema_lanes": ["github_code", "local_code"],
                "canonical_plan_mutated": False,
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
