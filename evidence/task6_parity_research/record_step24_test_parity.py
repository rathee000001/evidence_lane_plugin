"""Record Step 24 official/public/runtime conformance and test parity.

This is a static and read-only evidence pass over source, installed package
bytes, prior receipts, and the test inventory.  It does not execute the project
test suite, install a plugin, activate a selector, call lifecycle writes, or
mutate the canonical Plan/Goal.  Only the separate research SQLite is written.
"""

from __future__ import annotations

import ast
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
PLUGIN = WORKSPACE / "plugins" / "evidence-lane-plugin"
TESTS = WORKSPACE / "tests"
SOURCE_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
SOURCE_MCP_CONFIG = PLUGIN / ".mcp.json"
SOURCE_HOOKS = PLUGIN / "hooks" / "hooks.json"
SOURCE_STOP = PLUGIN / "hooks" / "stop_response.py"
SOURCE_PROMPT = PLUGIN / "hooks" / "prompt_submit.py"
SOURCE_BOUNDARY = PLUGIN / "hooks" / "lifecycle_boundary.py"
SOURCE_INVOKER = PLUGIN / "hooks" / "invoke_hook.py"
SOURCE_MCP = PLUGIN / "src" / "evidence_lane_plugin" / "mcp_server.py"

STABLE_ROOT = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github"
    r"\evidence-lane-plugin\2.1.0+codex.20260812193232"
)
TEST_ROOT = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-v220-testing-new"
    r"\evidence-lane-plugin\2.2.0+codex.20260814082900"
)
STABLE_MANIFEST = STABLE_ROOT / ".codex-plugin" / "plugin.json"
TEST_MANIFEST = TEST_ROOT / ".codex-plugin" / "plugin.json"
STABLE_STOP = STABLE_ROOT / "hooks" / "stop_response.py"
TEST_STOP = TEST_ROOT / "hooks" / "stop_response.py"

TASK6_ID = "01a0036f-32fa-79b2-8846-9c716d4fe777"

OFFICIAL_PLUGIN_PACKAGE = "https://developers.openai.com/plugins/build/plugins"
OFFICIAL_SKILLS = "https://developers.openai.com/plugins/build/skills"
OFFICIAL_MCP = "https://developers.openai.com/plugins/concepts/mcp-server"
OFFICIAL_PLUGIN_TEST = "https://developers.openai.com/plugins/deploy/connect-chatgpt"
OFFICIAL_HOOKS = "https://learn.chatgpt.com/docs/hooks"
OFFICIAL_APP_SERVER = "https://learn.chatgpt.com/docs/app-server"

DDL = """
CREATE TABLE IF NOT EXISTS official_contract (
    contract_id TEXT PRIMARY KEY,
    area TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    requirement TEXT NOT NULL,
    local_evidence TEXT NOT NULL,
    conformance_status TEXT NOT NULL,
    proposed_delta_key TEXT,
    recorded_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS conformance_case (
    case_id TEXT PRIMARY KEY,
    contract_id TEXT REFERENCES official_contract(contract_id),
    capability_route_id TEXT,
    test_layer TEXT NOT NULL,
    test_kind TEXT NOT NULL,
    target TEXT NOT NULL,
    selector_or_scenario TEXT NOT NULL,
    expected_result TEXT NOT NULL,
    current_coverage TEXT NOT NULL,
    execution_phase TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_locator TEXT NOT NULL,
    proposed_delta_key TEXT,
    recorded_at TEXT NOT NULL
) STRICT;
CREATE VIEW IF NOT EXISTS unresolved_conformance AS
SELECT * FROM conformance_case
WHERE status NOT IN ('PASS', 'NOT_APPLICABLE', 'REJECTED');
"""


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_inventory() -> tuple[list[dict[str, object]], int]:
    rows: list[dict[str, object]] = []
    total = 0
    for path in sorted(TESTS.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        functions = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ]
        rows.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "test_function_count": len(functions),
                "functions": functions,
            }
        )
        total += len(functions)
    return rows, total


def functions_containing(
    inventory: list[dict[str, object]], term: str
) -> list[str]:
    matches: list[str] = []
    for row in inventory:
        path = Path(str(row["path"]))
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            segment = "\n".join(
                lines[node.lineno - 1 : int(getattr(node, "end_lineno", node.lineno))]
            )
            if term in segment:
                matches.append(f"{path.name}:{node.name}")
    return sorted(matches)


def source_authority(
    authority_id: str,
    kind: str,
    path: Path,
    freshness: str,
    role: str,
    notes: str,
    now: str,
) -> tuple[object, ...]:
    return (
        authority_id,
        kind,
        str(path),
        sha256_file(path),
        path.stat().st_size,
        freshness,
        role,
        notes,
        now,
    )


def official_authority(
    authority_id: str,
    url: str,
    role: str,
    notes: str,
    now: str,
) -> tuple[object, ...]:
    return (
        authority_id,
        "official_openai_documentation",
        url,
        None,
        None,
        "LIVE_REVIEW_2026-08-15",
        role,
        notes,
        now,
    )


def main() -> None:
    now = utc_now()
    inventory, total_test_functions = test_inventory()
    test_file_count = len(inventory)
    source_manifest = load_json(SOURCE_MANIFEST)
    stable_manifest = load_json(STABLE_MANIFEST)
    test_manifest = load_json(TEST_MANIFEST)
    source_hooks = load_json(SOURCE_HOOKS)
    source_stop_text = SOURCE_STOP.read_text(encoding="utf-8")
    stable_stop_text = STABLE_STOP.read_text(encoding="utf-8")
    test_stop_text = TEST_STOP.read_text(encoding="utf-8")
    prompt_text = SOURCE_PROMPT.read_text(encoding="utf-8")
    boundary_text = SOURCE_BOUNDARY.read_text(encoding="utf-8")
    invoker_text = SOURCE_INVOKER.read_text(encoding="utf-8")

    skill_dirs = sorted(
        path for path in (PLUGIN / "skills").iterdir() if path.is_dir()
    )
    skill_count = len(skill_dirs)
    skill_md_count = sum((path / "SKILL.md").is_file() for path in skill_dirs)
    skill_dependency_files = [
        path / "agents" / "openai.yaml"
        for path in skill_dirs
        if (path / "agents" / "openai.yaml").is_file()
    ]
    dependency_skill_names = sorted(path.parents[1].name for path in skill_dependency_files)

    source_manifest_paths = {
        "skills": str(source_manifest.get("skills") or ""),
        "mcpServers": str(source_manifest.get("mcpServers") or ""),
        "hooks": str(source_manifest.get("hooks") or "DEFAULT_hooks/hooks.json"),
    }
    manifest_paths_valid = (
        source_manifest_paths["skills"] == "./skills/"
        and source_manifest_paths["mcpServers"] == "./.mcp.json"
        and SOURCE_MCP_CONFIG.is_file()
        and SOURCE_HOOKS.is_file()
        and skill_md_count == skill_count
    )

    required_hook_events = {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "Stop",
        "SessionEnd",
    }
    actual_hook_events = set((source_hooks.get("hooks") or {}).keys())
    hook_event_set_valid = actual_hook_events == required_hook_events
    source_stop_is_inert = (
        'print("{}")' in source_stop_text
        and '"decision"' not in source_stop_text
        and '"continue": False' not in source_stop_text
    )
    stable_stop_returns_continue_true = (
        'result: dict[str, Any] = {"continue": True}' in stable_stop_text
    )
    test_stop_requests_continuation = (
        '"decision": "block"' in test_stop_text
        or '"decision":"block"' in test_stop_text
    )
    source_has_stop_active_guard = "stop_hook_active" in (
        source_stop_text + invoker_text
    )
    source_compaction_shapes_valid = all(
        marker in boundary_text
        for marker in (
            "PreCompact/PostCompact accept only the universal command-output fields",
            '"systemMessage": _bounded_notice',
            'if event_name == "SessionEnd"',
        )
    )
    prompt_failure_exit_is_zero = "return 0" in prompt_text
    launcher_failure_exit_is_zero = "return 0" in invoker_text
    test_suite_old_stop_expectations = functions_containing(
        inventory, 'stop_payload["continue"]'
    ) + functions_containing(inventory, '"continue": True')
    test_suite_inert_stop_expectations = functions_containing(
        inventory, "json.loads(capsys.readouterr().out) == {}"
    )
    stop_test_contract_split = bool(
        test_suite_old_stop_expectations and test_suite_inert_stop_expectations
    )

    goal_recovery_static_tests = functions_containing(
        inventory, "RecoverAtLogon"
    )
    goal_get_tests = functions_containing(inventory, "thread/goal/get")
    goal_set_tests = functions_containing(inventory, "thread/goal/set")
    local_test_trust_tests = functions_containing(inventory, "USER_TRUST_PENDING")
    local_test_ready_tests = functions_containing(
        inventory, "runtime_ready_before_task_reopen"
    )
    forced_route_tests = functions_containing(inventory, "forced_same_worktree")
    fts_tests = functions_containing(inventory, "FTS5")
    bm25_tests = functions_containing(inventory, "BM25")

    with connect() as read_connection:
        route_counts = {
            "total": int(
                read_connection.execute("SELECT COUNT(*) FROM capability_route").fetchone()[0]
            ),
            "referenced_by_test": int(
                read_connection.execute(
                    "SELECT COUNT(*) FROM capability_route WHERE test_status='REFERENCED_BY_TEST'"
                ).fetchone()[0]
            ),
            "no_exact_test_reference": int(
                read_connection.execute(
                    "SELECT COUNT(*) FROM capability_route WHERE test_status IN ('NO_EXACT_TEST_NAME_REFERENCE','NO_EXACT_REFERENCE','NO_TEST')"
                ).fetchone()[0]
            ),
            "source_only_not_installed": int(
                read_connection.execute(
                    "SELECT COUNT(*) FROM capability_route WHERE installed_status IN ('SOURCE_ONLY_NOT_INSTALLED','NOT_INSTALLED_2_1')"
                ).fetchone()[0]
            ),
            "implemented_core_unrouted": int(
                read_connection.execute(
                    "SELECT COUNT(*) FROM capability_route WHERE gap_class LIKE 'IMPLEMENTED_CORE_BUT_UNROUTED%'"
                ).fetchone()[0]
            ),
            "declared_sdk_handler_missing": int(
                read_connection.execute(
                    "SELECT COUNT(*) FROM capability_route WHERE gap_class='DECLARED_SDK_OPERATION_HANDLER_MISSING'"
                ).fetchone()[0]
            ),
        }
        open_gap_count = int(
            read_connection.execute(
                "SELECT COUNT(*) FROM gap_finding WHERE status='OPEN'"
            ).fetchone()[0]
        )

    official_contracts = [
        (
            "OPENAI-PLUGIN-MANIFEST-PATHS",
            "plugin_package",
            OFFICIAL_PLUGIN_PACKAGE,
            "lines 1046-1076,1130-1170",
            "Every plugin has .codex-plugin/plugin.json; skills, hooks, .mcp.json, and assets live at plugin root; manifest paths are relative and ./-prefixed; default hooks/hooks.json is discovered automatically.",
            f"source_paths={json.dumps(source_manifest_paths, sort_keys=True)}; files_present={manifest_paths_valid}; source_version={source_manifest.get('version')}; stable_version={stable_manifest.get('version')}; test_version={test_manifest.get('version')}",
            "PASS_SOURCE_STRUCTURE_SOURCE_INSTALLED_VERSION_DRIFT_RECORDED",
            "DELTA-PACKAGE-INSTALL-PARITY",
            now,
        ),
        (
            "OPENAI-PLUGIN-HOOK-TRUST",
            "plugin_package",
            OFFICIAL_PLUGIN_PACKAGE,
            "lines 1159-1171",
            "Installing or enabling a plugin does not automatically trust bundled hooks; the user must review and trust the current hook definition.",
            "Step23 latest v2.2 receipt records USER_TRUST_PENDING while runtime_ready_before_task_reopen=true.",
            "FAIL",
            "DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
            now,
        ),
        (
            "OPENAI-SKILL-WORKFLOW-BOUNDARY",
            "skills",
            OFFICIAL_SKILLS,
            "lines 791-852",
            "Each skill requires SKILL.md with trigger, inputs, steps, outputs, non-inference, stop/question behavior, and supporting-resource guidance.",
            f"skill_dirs={skill_count}; SKILL.md={skill_md_count}; earlier Step6 route audit found named and unnamed workflow bridges.",
            "PARTIAL",
            "DELTA-SKILL-METADATA",
            now,
        ),
        (
            "OPENAI-SKILL-MCP-DEPENDENCY",
            "skills",
            OFFICIAL_SKILLS,
            "lines 840-852",
            "A skill that requires an MCP server declares the dependency in agents/openai.yaml and still names tool order and missing/ambiguous-result handling.",
            f"only {len(skill_dependency_files)} of {skill_count} source skills have agents/openai.yaml: {dependency_skill_names}; most Evidence Lane skills instruct native MCP reads/writes.",
            "FAIL",
            "DELTA-SKILL-MCP-ROUTING",
            now,
        ),
        (
            "OPENAI-MCP-METADATA-AND-RESULTS",
            "mcp",
            OFFICIAL_PLUGIN_TEST,
            "lines 779-797",
            "Tool names, descriptions, schemas, and annotations are present; each tool is exercised with representative, edge, missing-ID, empty, authorization, error, confirmation, and model-readable-result cases.",
            f"route_counts={json.dumps(route_counts, sort_keys=True)}; live installed server has 62 tools while source/test package advertises 83; no executed per-tool installed matrix exists for this turn.",
            "FAIL_INSTALLED_COMPLETE_MATRIX",
            "DELTA-MCP-TOOL-EVAL-MATRIX",
            now,
        ),
        (
            "OPENAI-PLUGIN-GOLDEN-PROMPTS",
            "plugin_testing",
            OFFICIAL_PLUGIN_TEST,
            "lines 818-845",
            "Use a new conversation and record direct, indirect, follow-up, authorized write, and unsupported prompts; rerun whenever tool metadata changes and retain results across releases.",
            "The repository has source unit/integration tests but no sealed installed-host golden prompt set covering all source 2.2 tools/skills/hooks in a new Task6 conversation.",
            "FAIL",
            "DELTA-INSTALLED-E2E-EVALUATION",
            now,
        ),
        (
            "OPENAI-HOOK-COMMON-WIRE",
            "hooks",
            OFFICIAL_HOOKS,
            "lines 1040-1094",
            "Hooks consume one JSON object with session_id, transcript_path, cwd, hook_event_name, model, and turn_id where scoped; outputs stay concise and never carry secrets.",
            f"source invoker and adapters use JSON stdin, redact/bound receipts, and return zero on launcher/adapter failures={prompt_failure_exit_is_zero and launcher_failure_exit_is_zero}; live prompt dispatch is still unproven.",
            "PASS_SOURCE_FAIL_LIVE_DISPATCH",
            "DELTA-HOOK-INSTALLED-RUNTIME",
            now,
        ),
        (
            "OPENAI-HOOK-STOP-REENTRANCY",
            "hooks",
            OFFICIAL_HOOKS,
            "lines 1433-1459",
            "Stop receives stop_hook_active. decision:block creates a continuation prompt; an already-continued turn must not be recursively continued. Plain text is invalid when exit is zero.",
            f"source Stop inert={source_stop_is_inert}; stable returns continue:true={stable_stop_returns_continue_true}; test candidate decision:block={test_stop_requests_continuation}; stop_hook_active guard={source_has_stop_active_guard}; test contract split={stop_test_contract_split}.",
            "PASS_CURRENT_SOURCE_NO_CONTINUATION_REQUEST_TESTS_SPLIT",
            "DELTA-HOOK-TEST-CONTRACT-UNIFICATION",
            now,
        ),
        (
            "OPENAI-HOOK-COMPACTION",
            "hooks",
            OFFICIAL_HOOKS,
            "lines 1353-1378",
            "PreCompact and PostCompact distinguish manual/auto trigger, use common JSON output, and can stop with continue:false; raw plain text is ignored.",
            f"source lifecycle boundary uses bounded systemMessage and no hookSpecificOutput for compaction={source_compaction_shapes_valid}; no installed host event proof exists for current Task6.",
            "PASS_SOURCE_FAIL_INSTALLED_TASK6_PROOF",
            "DELTA-COMPACTION-CONTINUITY",
            now,
        ),
        (
            "OPENAI-APP-SERVER-GOAL",
            "goal",
            OFFICIAL_APP_SERVER,
            "lines 1164-1192",
            "thread/goal/set,get,clear manage persisted Goal; a new objective replaces the Goal and resets usage, while same/omitted objective can preserve usage.",
            f"goal/get tests={goal_get_tests}; goal/set tests={goal_set_tests}; current Task6 Goal was intentionally not mutated during research; Step22 found objective drift.",
            "PARTIAL_GET_ONLY_NO_CONTINUATION_REBIND_TEST",
            "DELTA-GOAL-NATIVE-LIFECYCLE-BINDING",
            now,
        ),
        (
            "OPENAI-APP-SERVER-BOUNDED-THREAD-READ",
            "chat_lineage",
            OFFICIAL_APP_SERVER,
            "lines 968-977,1223-1252",
            "thread/read can return a summary without resuming; thread/turns/list and thread/items/list page stored history with bounded views.",
            "Goal helper uses thread/read; project ChatLineage capture still does not expose a complete bounded Task6 thread/turn/item ingestion route with typed Entry/Exit slips.",
            "PARTIAL",
            "DELTA-CHAT-LINEAGE-TYPED-RUNTIME",
            now,
        ),
        (
            "OPENAI-APP-SERVER-MCP-RELOAD",
            "installed_runtime",
            OFFICIAL_APP_SERVER,
            "lines 1026-1039",
            "config/mcpServer/reload reloads MCP config and queues refresh for loaded threads; mcpServerStatus/list provides paginated status, tools, resources, and auth.",
            f"source Goal manager statically calls reload/status; static tests={goal_recovery_static_tests}; active installed source/helper/catalog versions drift and Task6 lacks durable binding.",
            "PARTIAL_STATIC_ONLY",
            "DELTA-GOAL-RECOVERY-INSTALLED-PARITY",
            now,
        ),
    ]

    conformance_cases = [
        (
            "CASE-PLUGIN-SOURCE-MANIFEST",
            "OPENAI-PLUGIN-MANIFEST-PATHS",
            None,
            "source",
            "static_package",
            str(SOURCE_MANIFEST),
            "validate manifest component paths and file containment",
            "All declared/default components exist within the plugin root and source identity is explicit.",
            f"PASS static={manifest_paths_valid}",
            "RESEARCH_STATIC",
            "PASS" if manifest_paths_valid else "FAIL",
            f"{SOURCE_MANIFEST};{SOURCE_MCP_CONFIG};{SOURCE_HOOKS}",
            "DELTA-PACKAGE-INSTALL-PARITY",
            now,
        ),
        (
            "CASE-PLUGIN-SOURCE-STABLE-TEST-IDENTITY",
            "OPENAI-PLUGIN-MANIFEST-PATHS",
            "PACKAGE-IDENTITY",
            "installed",
            "identity",
            f"{SOURCE_MANIFEST};{STABLE_MANIFEST};{TEST_MANIFEST}",
            "source 2.2 vs enabled stable 2.1 vs disabled test 2.2",
            "The enabled package must match the implementation generation under test; test and release receipts bind exact manifest/package hashes.",
            "Known version split; no implementation execution allowed before normalized Plan append.",
            "PREHIL_INSTALL",
            "PENDING",
            "FTS-STEP23-SLOTS",
            "DELTA-PACKAGE-INSTALL-PARITY",
            now,
        ),
        (
            "CASE-HOOK-TRUST-BEFORE-READY",
            "OPENAI-PLUGIN-HOOK-TRUST",
            "INSTALL-HOOK-TRUST-READINESS",
            "installed",
            "negative_activation",
            "install_codex_stable local-test route",
            "USER_TRUST_PENDING plus runtime prewarm PASS",
            "Readiness remains false and stable remains recoverable until human trust and post-reload live proof pass.",
            f"Existing test intentionally accepts pending trust and exclusive activation; tests={local_test_trust_tests}; readiness tests={len(local_test_ready_tests)}.",
            "LOCAL_PER_DELTA",
            "FAIL",
            "GAP-LOCAL-TEST-FALSE-RUNTIME-READY",
            "DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
            now,
        ),
        (
            "CASE-SKILL-MCP-DEPENDENCIES",
            "OPENAI-SKILL-MCP-DEPENDENCY",
            None,
            "source_package",
            "metadata",
            str(PLUGIN / "skills"),
            "all skills that require Evidence Lane MCP tools",
            "Each declares agents/openai.yaml dependency and exact tool order/fail-closed behavior.",
            f"{len(skill_dependency_files)}/{skill_count} dependency files.",
            "LOCAL_PER_DELTA",
            "FAIL",
            str(PLUGIN / "skills"),
            "DELTA-SKILL-MCP-ROUTING",
            now,
        ),
        (
            "CASE-MCP-ALL-TOOLS-SCHEMAS",
            "OPENAI-MCP-METADATA-AND-RESULTS",
            "MCP-SURFACE",
            "source_and_installed",
            "schema_matrix",
            str(SOURCE_MCP),
            "each source 2.2 and enabled installed tool",
            "Unique names; valid schemas/descriptions/annotations; read/write/confirmation class; bounded model-readable result.",
            f"Route inventory total={route_counts['total']}; source-only-not-installed={route_counts['source_only_not_installed']}.",
            "LOCAL_PER_DELTA",
            "PENDING",
            "FTS-STEP07-MCP-SURFACE;FTS-STEP23-RUNTIME",
            "DELTA-MCP-TOOL-EVAL-MATRIX",
            now,
        ),
        (
            "CASE-MCP-ALL-TOOLS-BEHAVIOR",
            "OPENAI-MCP-METADATA-AND-RESULTS",
            "MCP-SURFACE",
            "installed_host",
            "behavior_matrix",
            "mcp://evidence-lane/*",
            "representative, edge, missing ID, empty, auth, confirmation, unsupported",
            "Each installed tool emits the expected bounded result/error and no unauthorized mutation.",
            "Only runtime_doctor and runtime_activation_status were read in Step23; full matrix is not executed in research.",
            "PREHIL_INSTALLED",
            "PENDING",
            "TEST-INSTALLED-RUNTIME-DOCTOR-STEP23;TEST-RUNTIME-ACTIVATION-STEP23",
            "DELTA-MCP-TOOL-EVAL-MATRIX",
            now,
        ),
        (
            "CASE-HOOK-STOP-OFFICIAL-WIRE",
            "OPENAI-HOOK-STOP-REENTRANCY",
            "HOOK-STOP",
            "source_and_installed",
            "wire_contract",
            f"{SOURCE_STOP};{STABLE_STOP};{TEST_STOP}",
            "first Stop and stop_hook_active=true replay",
            "No decision:block, exit2, continue:false, or new user prompt; append receipt at most once; output valid JSON.",
            f"Current source inert={source_stop_is_inert}; candidate does not request block={not test_stop_requests_continuation}; no replay guard because inert; tests split={stop_test_contract_split}.",
            "LOCAL_PER_DELTA",
            "PARTIAL",
            f"{SOURCE_STOP};{TESTS / 'test_hook_lifecycle_contract.py'};{TESTS / 'test_mcp_plugin.py'}",
            "DELTA-HOOK-TEST-CONTRACT-UNIFICATION",
            now,
        ),
        (
            "CASE-HOOK-COMPACTION-EXACTLY-ONCE",
            "OPENAI-HOOK-COMPACTION",
            "HOOK-COMPACTION",
            "installed_host",
            "event_replay",
            f"{SOURCE_BOUNDARY};mcp://evidence-lane/runtime_activation_status",
            "manual/auto PreCompact and PostCompact, duplicate and reordered delivery",
            "Bounded typed receipt, no raw transcript/PV/backlog, exactly-once rebind, fixed 1+9 panel continuity.",
            f"Source output shape passes static audit; Task6 validated invocations=0 and live event dispatch unproven.",
            "PREHIL_INSTALLED",
            "PENDING",
            "FTS-STEP21-HOOK-INSTALLED;FTS-STEP22-PANEL-CONTINUITY;FTS-STEP23-RUNTIME",
            "DELTA-COMPACTION-CONTINUITY",
            now,
        ),
        (
            "CASE-GOAL-SAME-OBJECTIVE-REATTACH",
            "OPENAI-APP-SERVER-GOAL",
            "GOAL-NATIVE",
            "host_app_server",
            "state_continuity",
            "thread/goal/get,set",
            "same active objective after Plan research transition",
            "Preserve usage when objective is unchanged; if objective must change, seal the reset and old usage history explicitly.",
            f"goal/get static coverage={goal_get_tests}; goal/set coverage={goal_set_tests}.",
            "AFTER_PLAN_APPEND",
            "PENDING",
            "FTS-STEP22-GOAL",
            "DELTA-GOAL-NATIVE-LIFECYCLE-BINDING",
            now,
        ),
        (
            "CASE-GOAL-MULTI-PROJECT-RECOVERY",
            "OPENAI-APP-SERVER-MCP-RELOAD",
            "RECOVERY-GOAL-MULTI-BINDING-MANAGER",
            "installed_windows",
            "multi_binding_recovery",
            "Manage-EvidenceLaneCodexGoalRecovery",
            "two active tasks in different projects plus one ended Goal",
            "Recover valid tasks once, tombstone ended binding, shared task exits success, no retry/open loop.",
            "Only static string assertions exist; no multi-project runtime fixture or terminal stale-binding case.",
            "LOCAL_PER_DELTA",
            "FAIL",
            f"{TESTS / 'test_codex_v200_installation.py'};FTS-STEP23-GOAL-RECOVERY",
            "DELTA-GOAL-RECOVERY-MULTI-BINDING",
            now,
        ),
        (
            "CASE-STATE-TRAVEL-FORCED-SAME-WORKTREE",
            None,
            "STATE-TRAVEL-FORCED-SAME-WORKTREE",
            "source_and_installed",
            "contract_matrix",
            "requested direct/forced route",
            "exact match, one mismatch per identity axis, replay, consumed route, dirty-byte drift",
            "Resume exactly once on full match; fail closed without pointer/HIL/task/byte mutation on every mismatch or replay.",
            f"Exact route tests={forced_route_tests}; public route absent.",
            "LOCAL_PER_DELTA",
            "FAIL",
            "FTS-STEP23-STATE-TRAVEL",
            "DELTA-STATE-TRAVEL-FORCED-SAME-WORKTREE",
            now,
        ),
        (
            "CASE-PLAN-FTS-BOUNDED-WINDOW",
            None,
            "PLAN-BOUNDED-WINDOW",
            "source_and_installed",
            "query_matrix",
            "Plan SQLite FTS5/BM25",
            "exact task, active+8, terminal short window, FTS hit, zero hit, pagination, concurrent append",
            "Never return full Plan/PV; stable hashes/locators; active Row and physical final HIL preserved.",
            f"FTS5 tests={fts_tests}; BM25 tests={bm25_tests}; installed 2.1 backlog remains full dump.",
            "LOCAL_PER_DELTA_AND_PREHIL_INSTALLED",
            "FAIL_INSTALLED",
            "FTS-STEP22-PLAN-WINDOW",
            "DELTA-PLAN-BOUNDED-WINDOW",
            now,
        ),
        (
            "CASE-PER-DELTA-LOCAL-RECEIPTS",
            None,
            None,
            "release",
            "delta_acceptance",
            "all normalized implementation Deltas",
            "one exact acceptance contract per Delta",
            "Each Delta has a local PASS receipt tied to code/test hashes; dependent integration checkpoints run once; Git occurs only at the governed pre-HIL boundary.",
            f"Capability routes referenced by tests={route_counts['referenced_by_test']}; open gaps before Step24={open_gap_count}; no normalized Delta set exists yet.",
            "EXECUTION_AFTER_STEP25",
            "PENDING",
            f"{TESTS};TASK6_PARITY_RESEARCH.sqlite",
            "DELTA-PER-DELTA-LOCAL-VERIFICATION",
            now,
        ),
        (
            "CASE-INSTALLED-GOLDEN-PROMPT-SET",
            "OPENAI-PLUGIN-GOLDEN-PROMPTS",
            None,
            "installed_host",
            "golden_prompt_regression",
            "exact committed PV13 package",
            "direct, indirect, follow-up, write/HIL, unsupported, compaction, restart, recovery",
            "Selected skills/tools, arguments, receipts, confirmation behavior, panel/Goal continuity, and no secret/context bloat match expected release evidence.",
            "No sealed complete installed-host Task6 evaluation set exists.",
            "PREHIL_INSTALLED",
            "PENDING",
            OFFICIAL_PLUGIN_TEST,
            "DELTA-INSTALLED-E2E-EVALUATION",
            now,
        ),
        (
            "CASE-PREHIL-EXACT-COMMIT-CI",
            None,
            "INSTALL-PREHIL-EXACT-COMMIT-CI",
            "release",
            "ordered_gate",
            "PV13 candidate",
            "local PASS -> final commit/push -> exact commit install -> Git CI PASS -> fresh HIL",
            "No HIL before all prior evidence; no inferred approval; no PC shutdown before the governed HIL boundary.",
            "User contract recorded; not executed in research.",
            "PREHIL_RELEASE",
            "PENDING",
            "FTS-STEP23-PREHIL",
            "DELTA-PV13-PREHIL-RELEASE-CHAIN",
            now,
        ),
    ]

    findings = [
        (
            "GAP-SKILL-MCP-DEPENDENCY-METADATA",
            "skills",
            "HIGH",
            "OFFICIAL_DEPENDENCY_DECLARATION_MISSING",
            f"Only {len(skill_dependency_files)} of {skill_count} source skills carry agents/openai.yaml, although most Evidence Lane workflows require native Evidence Lane MCP tools.",
            f"{PLUGIN / 'skills'};{OFFICIAL_SKILLS}",
            "Installed surfaces can select a workflow without a declared tool dependency or deterministic missing-tool behavior, recreating source/installed route drift.",
            "Add correct per-skill MCP dependency metadata and validate tool names/order/fail-closed behavior against the installed catalog during packaging.",
            "DELTA-SKILL-MCP-ROUTING",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-HOOK-TEST-CONTRACT-SPLIT",
            "hooks",
            "HIGH",
            "TESTS_ENFORCE_INCOMPATIBLE_STOP_OUTPUTS",
            f"Current hook tests expect inert {{}} output, while test_mcp_plugin still requires continue:true and indexes stop_payload['continue']; old={test_suite_old_stop_expectations}, inert={test_suite_inert_stop_expectations}.",
            f"{TESTS / 'test_hook_lifecycle_contract.py'};{TESTS / 'test_mcp_plugin.py'};{SOURCE_STOP}",
            "The suite can fail after a correct Stop-wire change or, worse, be selectively run so incompatible behavior appears green.",
            "Choose one official-wire contract: Stop never requests continuation; test first delivery and stop_hook_active replay; update every source, package, and installed fixture together.",
            "DELTA-HOOK-TEST-CONTRACT-UNIFICATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-INSTALLED-PUBLIC-E2E-EVALUATION",
            "plugin_testing",
            "CRITICAL",
            "SOURCE_TESTS_WITHOUT_COMPLETE_INSTALLED_HOST_EVAL",
            f"The repository has {test_file_count} test files and {total_test_functions} test functions, but the active installed runtime is 2.1/62 tools, source is 2.2/83, Task6 prompt dispatch is unproven, and no sealed complete new-conversation golden prompt set covers the installed plugin.",
            f"{TESTS};mcp://evidence-lane/runtime_activation_status;{OFFICIAL_PLUGIN_TEST}",
            "Source-local tests can pass while skills, hooks, MCP metadata, host dispatch, Goal, panel, install, and public/runtime behavior remain unrouted or stale.",
            "Generate a release-versioned installed-host evaluation matrix from capability routes and normalized Deltas; execute it after exact commit installation, retain prompts/results, and block HIL on any missing or failed case.",
            "DELTA-INSTALLED-E2E-EVALUATION",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-OFFICIAL-MCP-TOOL-EVAL-COVERAGE",
            "mcp",
            "CRITICAL",
            "NO_COMPLETE_PER_TOOL_PUBLIC_RUNTIME_MATRIX",
            f"Research identified {route_counts['total']} capability routes, {route_counts['source_only_not_installed']} source-only/not-installed routes, {route_counts['implemented_core_unrouted']} implemented-core-unrouted routes, and {route_counts['declared_sdk_handler_missing']} declared SDK handler gaps; existing test references are not an executed installed per-tool matrix.",
            f"TASK6_PARITY_RESEARCH.sqlite;{SOURCE_MCP};{OFFICIAL_PLUGIN_TEST}",
            "A large catalog can advertise names while edge cases, annotations, confirmation rules, bounded results, installed handlers, and skill routing are broken.",
            "For every public tool/capability, seal schema metadata and run representative/edge/missing/empty/auth/write-confirmation/unsupported cases against both source fixture and exact installed package; reject untested public tools.",
            "DELTA-MCP-TOOL-EVAL-MATRIX",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CONFORMANCE-MATRIX-NOT-RELEASE-GATE",
            "release",
            "CRITICAL",
            "RESEARCH_FINDINGS_NOT_YET_EXECUTABLE_ACCEPTANCE_AUTHORITY",
            "Before this Step24 migration there was no relational official_contract/conformance_case authority tying OpenAI wire requirements, each capability route, installed-host scenarios, execution phase, and normalized Delta acceptance receipt.",
            f"TASK6_PARITY_RESEARCH_SCHEMA.sql;{OFFICIAL_PLUGIN_PACKAGE};{OFFICIAL_HOOKS};{OFFICIAL_APP_SERVER}",
            "Tests can be selected ad hoc, gaps can be missed during normalization, and pre-HIL can pass without proving installed/public behavior.",
            "Generate the release gate from this matrix, require every applicable case PASS with immutable locators, and include the matrix hash in the exact commit/install/CI/HIL receipt chain.",
            "DELTA-CONFORMANCE-RELEASE-GATE",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-PER-DELTA-LOCAL-TEST-RECEIPTS",
            "release",
            "CRITICAL",
            "DELTA_COMPLETION_NOT_BOUND_TO_CODE_AND_TEST_EVIDENCE",
            "The current research crosswalk references tests but the implementation Deltas are not yet normalized and there is no enforced one-Delta acceptance receipt binding changed code, exact selectors, tests, results, and dependency generation.",
            f"TASK6_PARITY_RESEARCH.sqlite;{TESTS};codex://thread/{TASK6_ID}/per-delta-local-testing-law",
            "Rows can be marked complete from source presence or a broad suite pass even when the specific public/runtime gap remains unrouted.",
            "Each normalized Delta must define local tests and negative cases, record exact code/test hashes and PASS output, and transition only after its contract passes; run integration batches without repeating Git until pre-HIL.",
            "DELTA-PER-DELTA-LOCAL-VERIFICATION",
            "OPEN",
            "HIGH",
            now,
        ),
    ]

    tests = [
        (
            "TEST-STATIC-INVENTORY-STEP24",
            "test_parity",
            str(TESTS),
            "AST parse and count test_*.py functions without importing or executing them",
            "READ_ONLY_STATIC",
            "PASS",
            f"{test_file_count} files and {total_test_functions} test functions parsed; route/test gaps remain separately classified.",
            "Does not prove any test currently passes or that installed-host behavior matches source.",
            now,
        ),
        (
            "TEST-OFFICIAL-PLUGIN-STRUCTURE-STEP24",
            "plugin_package",
            f"{SOURCE_MANIFEST};{SOURCE_MCP_CONFIG};{SOURCE_HOOKS}",
            "official manifest path and component presence audit",
            "READ_ONLY_STATIC_PLUS_OFFICIAL_DOCS",
            "PASS",
            "Source manifest and default hook/MCP/skill paths are structurally valid.",
            "Does not resolve enabled stable 2.1 versus source/test 2.2 runtime parity.",
            now,
        ),
        (
            "TEST-SKILL-DEPENDENCY-METADATA-STEP24",
            "skills",
            str(PLUGIN / "skills"),
            "count SKILL.md and agents/openai.yaml per skill",
            "READ_ONLY_STATIC_PLUS_OFFICIAL_DOCS",
            "FAIL_GAP_CONFIRMED",
            f"All {skill_count} skills have SKILL.md but only {len(skill_dependency_files)} have OpenAI dependency metadata.",
            "Does not decide that every skill needs MCP; normalization must preserve instructions-only exceptions where real.",
            now,
        ),
        (
            "TEST-HOOK-OFFICIAL-WIRE-STEP24",
            "hooks",
            f"{SOURCE_HOOKS};{SOURCE_STOP};{SOURCE_BOUNDARY};{OFFICIAL_HOOKS}",
            "event set, Stop continuation fields, compaction output, failure exit, and test-contract comparison",
            "READ_ONLY_STATIC_PLUS_OFFICIAL_DOCS",
            "PASS_SOURCE_WITH_TEST_SPLIT",
            f"Required event set exact={hook_event_set_valid}; source Stop inert={source_stop_is_inert}; candidate requests block={test_stop_requests_continuation}; compaction shape={source_compaction_shapes_valid}.",
            "Live Task6 hook dispatch remains unproven and existing tests enforce incompatible Stop outputs.",
            now,
        ),
        (
            "TEST-MCP-CONFORMANCE-MATRIX-STEP24",
            "mcp",
            f"TASK6_PARITY_RESEARCH.sqlite;{SOURCE_MCP}",
            "capability-route coverage and installed/source gap counts against official per-tool evaluation law",
            "READ_ONLY_STATIC_PLUS_OFFICIAL_DOCS",
            "FAIL_COMPLETE_MATRIX_ABSENT",
            json.dumps(route_counts, sort_keys=True, separators=(",", ":")),
            "No project tests were executed and no write tool was called in research.",
            now,
        ),
        (
            "TEST-GOAL-APP-SERVER-STEP24",
            "goal",
            f"{OFFICIAL_APP_SERVER};{TESTS / 'test_codex_v200_installation.py'}",
            "compare get/set/clear and usage-reset semantics with helper/static test coverage",
            "READ_ONLY_STATIC_PLUS_OFFICIAL_DOCS",
            "PARTIAL",
            f"goal/get tests={goal_get_tests}; goal/set tests={goal_set_tests}; no Task6 Goal mutation was made.",
            "Does not prove multi-project recovery, same-objective usage preservation, or installed host continuation.",
            now,
        ),
        (
            "TEST-RESEARCH-SQLITE-STEP24",
            "research_authority",
            "TASK6_PARITY_RESEARCH.sqlite",
            "additive official_contract and conformance_case schema migration plus quick_check",
            "LOCAL_RESEARCH_ONLY",
            "PASS",
            "The research schema can now query official requirements, local evidence, test phases, gaps, and proposed Deltas without loading full source/PV/backlog into context.",
            "This does not mutate the canonical Plan and does not itself append implementation Deltas.",
            now,
        ),
    ]

    authorities = [
        source_authority("AUTH-SOURCE-MANIFEST-STEP24", "project_manifest", SOURCE_MANIFEST, "DIRTY_WORKTREE_READ_ONLY", "Source 2.2 plugin package entry", json.dumps(source_manifest_paths, sort_keys=True), now),
        source_authority("AUTH-STABLE-MANIFEST-STEP24", "installed_manifest", STABLE_MANIFEST, "LIVE_ENABLED", "Enabled stable 2.1 manifest", str(stable_manifest.get("version")), now),
        source_authority("AUTH-TEST-MANIFEST-STEP24", "installed_manifest", TEST_MANIFEST, "LIVE_DISABLED", "Disabled test 2.2 manifest", str(test_manifest.get("version")), now),
        source_authority("AUTH-SOURCE-HOOKS-STEP24", "project_hooks", SOURCE_HOOKS, "DIRTY_WORKTREE_READ_ONLY", "Current eight-event hook registration", json.dumps(sorted(actual_hook_events)), now),
        source_authority("AUTH-SOURCE-STOP-STEP24", "project_hook", SOURCE_STOP, "DIRTY_WORKTREE_READ_ONLY", "Current inert Stop adapter", f"inert={source_stop_is_inert}; stop_hook_active_guard={source_has_stop_active_guard}", now),
        source_authority("AUTH-STABLE-STOP-STEP24", "installed_hook", STABLE_STOP, "LIVE_ENABLED_OLDER", "Enabled stable Stop adapter", f"continue_true={stable_stop_returns_continue_true}", now),
        source_authority("AUTH-TEST-STOP-STEP24", "installed_hook", TEST_STOP, "LIVE_DISABLED", "Disabled test candidate Stop adapter", f"requests_decision_block={test_stop_requests_continuation}", now),
        source_authority("AUTH-TEST-HOOK-LIFECYCLE-STEP24", "test_source", TESTS / "test_hook_lifecycle_contract.py", "DIRTY_WORKTREE_READ_ONLY", "Inert Stop and official lifecycle shape tests", "Current contract", now),
        source_authority("AUTH-TEST-MCP-PLUGIN-STEP24", "test_source", TESTS / "test_mcp_plugin.py", "DIRTY_WORKTREE_READ_ONLY", "Older Stop continue:true integration expectations", "Conflicts with current inert adapter tests", now),
        source_authority("AUTH-TEST-CODEX-INSTALL-STEP24", "test_source", TESTS / "test_codex_v200_installation.py", "DIRTY_WORKTREE_READ_ONLY", "Installer, helper, hook trust, and runtime fixture tests", "Static/general-manager tests do not cover multi-project stale-binding failure", now),
        official_authority("AUTH-OPENAI-PLUGIN-PACKAGE-STEP24", OFFICIAL_PLUGIN_PACKAGE, "Plugin manifest, roots, bundled MCP, and hook trust authority", "Reviewed live on 2026-08-15", now),
        official_authority("AUTH-OPENAI-SKILLS-STEP24", OFFICIAL_SKILLS, "SKILL.md workflow and MCP dependency authority", "Reviewed live on 2026-08-15", now),
        official_authority("AUTH-OPENAI-MCP-STEP24", OFFICIAL_MCP, "MCP architecture authority", "Reviewed live on 2026-08-15", now),
        official_authority("AUTH-OPENAI-PLUGIN-TEST-STEP24", OFFICIAL_PLUGIN_TEST, "Per-tool and installed complete plugin evaluation authority", "Reviewed live on 2026-08-15", now),
        official_authority("AUTH-OPENAI-HOOKS-STEP24", OFFICIAL_HOOKS, "Current release hook wire and Stop/compaction behavior", "Reviewed live on 2026-08-15; release page takes precedence over main schemas", now),
        official_authority("AUTH-OPENAI-APP-SERVER-STEP24", OFFICIAL_APP_SERVER, "Thread, Goal, MCP reload/status, and bounded history authority", "Reviewed live on 2026-08-15", now),
    ]

    fts_rows = [
        (
            "FTS-STEP24-OFFICIAL-CONTRACTS",
            "official_conformance",
            "OpenAI plugin, skill, MCP, hook, and app-server requirements",
            json.dumps(
                {
                    "contract_count": len(official_contracts),
                    "urls": sorted(
                        {
                            OFFICIAL_PLUGIN_PACKAGE,
                            OFFICIAL_SKILLS,
                            OFFICIAL_MCP,
                            OFFICIAL_PLUGIN_TEST,
                            OFFICIAL_HOOKS,
                            OFFICIAL_APP_SERVER,
                        }
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "official_contract",
        ),
        (
            "FTS-STEP24-TEST-INVENTORY",
            "research_finding",
            "Source test inventory is broad but not installed-host proof",
            f"test_files={test_file_count}; test_functions={total_test_functions}; route_counts={json.dumps(route_counts, sort_keys=True, separators=(',', ':'))}; open_gaps_before_step24={open_gap_count}. No project test was executed.",
            "GAP-INSTALLED-PUBLIC-E2E-EVALUATION;GAP-OFFICIAL-MCP-TOOL-EVAL-COVERAGE",
        ),
        (
            "FTS-STEP24-HOOK-WIRE",
            "research_finding",
            "Current Stop is inert but the suite contains incompatible old expectations",
            f"official Stop decision:block creates a new continuation prompt and stop_hook_active marks replay. source_inert={source_stop_is_inert}; stable_continue_true={stable_stop_returns_continue_true}; candidate_decision_block={test_stop_requests_continuation}; source_stop_active_guard={source_has_stop_active_guard}; test_split={stop_test_contract_split}; old_tests={test_suite_old_stop_expectations}; inert_tests={test_suite_inert_stop_expectations}.",
            "GAP-HOOK-TEST-CONTRACT-SPLIT;GAP-RECOVERY-EVENT-CORRELATION",
        ),
        (
            "FTS-STEP24-SKILL-DEPENDENCIES",
            "research_finding",
            "Most MCP-dependent skills lack OpenAI dependency metadata",
            f"skill_count={skill_count}; skill_md_count={skill_md_count}; agents_openai_yaml_count={len(skill_dependency_files)}; dependency_skills={dependency_skill_names}.",
            "GAP-SKILL-MCP-DEPENDENCY-METADATA",
        ),
        (
            "FTS-STEP24-RELEASE-MATRIX",
            "research_contract",
            "Every normalized Delta and official case gates PV13 pre-HIL",
            f"conformance_cases={len(conformance_cases)}; each applicable case must bind exact code/test/package/result hashes. Per-Delta local tests run during execution; exact commit install and Git CI run only at pre-HIL; HIL is never inferred.",
            "GAP-CONFORMANCE-MATRIX-NOT-RELEASE-GATE;GAP-PER-DELTA-LOCAL-TEST-RECEIPTS",
        ),
    ]

    with connect() as connection:
        connection.executescript(DDL)
        connection.executemany(
            "INSERT OR REPLACE INTO official_contract VALUES (?,?,?,?,?,?,?,?,?)",
            official_contracts,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO conformance_case VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            conformance_cases,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            findings,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO test_evidence VALUES (?,?,?,?,?,?,?,?,?)",
            tests,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            authorities,
        )
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
            step_no=24,
            to_status="completed",
            evidence_locator="FTS-STEP24-OFFICIAL-CONTRACTS;FTS-STEP24-TEST-INVENTORY;FTS-STEP24-HOOK-WIRE;FTS-STEP24-SKILL-DEPENDENCIES;FTS-STEP24-RELEASE-MATRIX",
            event_id="EVT-STEP24-TEST-PARITY-COMPLETE",
            occurred_at=now,
        )
        transition_step(
            connection,
            step_no=25,
            to_status="in_progress",
            evidence_locator="Deduplicate gaps and produce exact dependency-ordered implementation Delta contracts",
            event_id="EVT-STEP25-NORMALIZE-DELTAS-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEP24-TEST-PARITY",
            event_type="RESEARCH_STEP_COMPLETED",
            payload={
                "step": 24,
                "test_file_count": test_file_count,
                "test_function_count": total_test_functions,
                "project_tests_executed": False,
                "manifest_paths_valid": manifest_paths_valid,
                "skill_count": skill_count,
                "skill_md_count": skill_md_count,
                "skill_dependency_file_count": len(skill_dependency_files),
                "required_hook_events_exact": hook_event_set_valid,
                "source_stop_is_inert": source_stop_is_inert,
                "stable_stop_returns_continue_true": stable_stop_returns_continue_true,
                "candidate_stop_requests_continuation": test_stop_requests_continuation,
                "source_stop_active_guard": source_has_stop_active_guard,
                "stop_test_contract_split": stop_test_contract_split,
                "source_compaction_shapes_valid": source_compaction_shapes_valid,
                "route_counts": route_counts,
                "official_contract_count": len(official_contracts),
                "conformance_case_count": len(conformance_cases),
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
                "plugin_or_slot_mutated": False,
                "credential_value_observed_or_stored": False,
            },
            occurred_at=now,
        )
        check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if check != "ok":
            raise RuntimeError(f"quick_check failed: {check}")
        unresolved_case_count = int(
            connection.execute("SELECT COUNT(*) FROM unresolved_conformance").fetchone()[0]
        )

    print(
        json.dumps(
            {
                "status": "PASS",
                "completed_step": 24,
                "in_progress_step": 25,
                "test_inventory": {
                    "files": test_file_count,
                    "functions": total_test_functions,
                    "executed": False,
                },
                "manifest_paths_valid": manifest_paths_valid,
                "skills": {
                    "total": skill_count,
                    "SKILL_md": skill_md_count,
                    "agents_openai_yaml": len(skill_dependency_files),
                },
                "hook_wire": {
                    "event_set_exact": hook_event_set_valid,
                    "source_stop_inert": source_stop_is_inert,
                    "candidate_requests_block": test_stop_requests_continuation,
                    "test_contract_split": stop_test_contract_split,
                },
                "route_counts": route_counts,
                "official_contracts": len(official_contracts),
                "conformance_cases": len(conformance_cases),
                "unresolved_conformance_cases": unresolved_case_count,
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
                "plugin_or_slot_mutated": False,
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
