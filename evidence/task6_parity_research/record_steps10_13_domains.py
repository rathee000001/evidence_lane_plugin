"""Record GitHub/CI, Canon, Learning, and ENV/UOP parity for Steps 10-13."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from research_db import (
    append_audit_event,
    connect,
    directory_fingerprint,
    sha256_file,
    transition_step,
    upsert_fts,
    utc_now,
)


WORKSPACE = Path(r"F:\test codex")
SOURCE = WORKSPACE / "plugins" / "evidence-lane-plugin"
PACKAGE = SOURCE / "src" / "evidence_lane_plugin"
INSTALLED = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github\evidence-lane-plugin"
    r"\2.1.0+codex.20260812193232"
)
INSTALLED_PACKAGE = INSTALLED / "src" / "evidence_lane_plugin"

CI_COMMIT = "53db7e0cbd599a71ec00d24f9499eee2a8207c61"
CI_TREE = "3eddd1ae1929ecdfa27d5b820b57f5d297655f7d"
CI_RUNS = [
    {
        "name": "Evidence Lane governed Python CI",
        "run_id": 31831674661,
        "url": "https://github.com/rathee000001/evidence_lane_plugin/actions/runs/31831674661",
        "conclusion": "success",
    },
    {
        "name": "Evidence Lane CodeQL",
        "run_id": 31831674581,
        "url": "https://github.com/rathee000001/evidence_lane_plugin/actions/runs/31831674581",
        "conclusion": "success",
    },
    {
        "name": "Evidence Lane preview build",
        "run_id": 31831674617,
        "url": "https://github.com/rathee000001/evidence_lane_plugin/actions/runs/31831674617",
        "conclusion": "success",
    },
]


def functions(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node.lineno
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    }


def classes(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node.lineno
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }


def line_of(path: Path, needle: str) -> int:
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if needle in line:
            return index
    return 1


def route_row(
    route_id: str,
    domain: str,
    capability: str,
    symbol: str,
    locator: str,
    *,
    mcp_tool: str | None = None,
    mcp_status: str = "NO_PUBLIC_MCP_ROUTE",
    sdk_module: str | None = None,
    sdk_operation: str | None = None,
    sdk_status: str = "NO_SDK_ROUTE",
    skill_path: str | None = None,
    skill_status: str = "NO_SKILL_ROUTE",
    installed_status: str = "SOURCE_ONLY",
    runtime_status: str = "NOT_PRODUCTION_REACHABLE",
    test_status: str = "TEST_PRESENT_NOT_EXECUTED_THIS_PHASE",
    tests: str = "",
    gap: str = "IMPLEMENTED_CORE_BUT_UNROUTED",
    decision: str = "ADD_SUPPORTED_ROUTE_OR_CLASSIFY_INTERNAL_ONLY",
    delta: str | None = None,
    notes: str = "",
    now: str,
) -> tuple[object, ...]:
    return (
        route_id,
        domain,
        capability,
        symbol,
        "IMPLEMENTED",
        locator,
        mcp_tool,
        mcp_status,
        sdk_module,
        sdk_operation,
        sdk_status,
        skill_path,
        skill_status,
        None,
        "NO_COMMAND_ROUTE",
        installed_status,
        runtime_status,
        test_status,
        tests,
        gap,
        decision,
        delta,
        notes,
        now,
    )


def main() -> None:
    now = utc_now()
    github_app = PACKAGE / "github_app_distribution.py"
    github_automation = PACKAGE / "github_automation_governance.py"
    canon_graph = PACKAGE / "canon_task_graph.py"
    canon_continuity = PACKAGE / "canon_runtime_continuity.py"
    learning = PACKAGE / "agent_learning.py"
    mode = PACKAGE / "mode_governance.py"
    flash = PACKAGE / "flash_authority.py"
    projection = PACKAGE / "flash_projection.py"
    internal_sdk = PACKAGE / "internal_sdk.py"
    mcp = PACKAGE / "mcp_server.py"

    workflow_paths = list((WORKSPACE / ".github" / "workflows").glob("*.yml"))
    workflow_sha, workflow_count, workflow_bytes = directory_fingerprint(
        workflow_paths, WORKSPACE / ".github" / "workflows"
    )
    uses_values: list[str] = []
    for path in workflow_paths:
        uses_values.extend(
            match.group(1).strip()
            for match in re.finditer(
                r"(?m)^\s*(?:-\s*)?uses:\s*([^\s#]+)", path.read_text(encoding="utf-8")
            )
        )
    external_uses = [value for value in uses_values if not value.startswith("./")]
    unpinned_external = [
        value
        for value in external_uses
        if not re.search(r"@[0-9a-fA-F]{40}$", value)
    ]

    app_classes = classes(github_app)
    app_functions = functions(github_app)
    automation_functions = functions(github_automation)
    canon_functions = functions(canon_graph)
    canon_continuity_functions = functions(canon_continuity)
    learning_functions = functions(learning)

    mcp_text = mcp.read_text(encoding="utf-8")
    service_text = (PACKAGE / "service.py").read_text(encoding="utf-8")
    sdk_text = internal_sdk.read_text(encoding="utf-8")
    skill_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (SOURCE / "skills").glob("*/SKILL.md")
    )
    production_route_text = "\n".join([mcp_text, service_text, sdk_text, skill_text])
    github_app_symbols = {
        "GitHubAppManifest",
        "InstallationTokenBroker",
        "WebhookVerifier",
        "ArtifactEntitlementStore",
        "map_check_run_receipt",
    }
    github_app_production_refs = sorted(
        symbol for symbol in github_app_symbols if symbol in production_route_text
    )

    source_schema_assets = sorted((PACKAGE / "schemas").glob("canon-*.schema.json"))
    canon_schema_ids = sorted(
        set(
            re.findall(
                r"^[A-Z][A-Z0-9_]+_SCHEMA\s*=\s*[\"']([^\"']+)",
                canon_graph.read_text(encoding="utf-8") + "\n" + canon_continuity.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
        )
    )
    learning_schema_ids = sorted(
        set(
            re.findall(
                r"^[A-Z][A-Z0-9_]+_SCHEMA\s*=\s*[\"']([^\"']+)",
                learning.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
        )
    )
    production_dispatcher_injected = bool(
        re.search(r"create_mcp_server\([^)]*canon_dispatcher\s*=", mcp_text[mcp_text.find("def run_server"):], re.DOTALL)
    )
    learning_public_mcp = sorted(re.findall(r'"(learning_[a-z_]+)"', mcp_text[: mcp_text.find("SDK_NATIVE_READ_TOOL_NAMES")]))
    learning_unrouted_core = sorted(
        name
        for name in learning_functions
        if name not in {
            "seal_learning_candidate",
            "decide_learning_candidate",
            "revoke_learning_candidate",
            "retrieve_accepted_learning",
            "inspect_learning_authority",
            "project_truth_pointer_sha256",
        }
    )

    sdk_missing_env = []
    for operation in ("compile_formula", "route_operator"):
        declared = f'"{operation}:WRITE_DERIVED_OPERATOR"' in sdk_text
        handler = bool(
            re.search(
                rf'\("env_uop_operator_runtime",\s*"{re.escape(operation)}"\)\s*:',
                sdk_text,
            )
        )
        if declared and not handler:
            sdk_missing_env.append(operation)
    env_public_tools = [
        tool for tool in ("session_flash_status", "mode_classify") if tool in mcp_text
    ]

    rows: list[tuple[object, ...]] = []
    for symbol in sorted(github_app_symbols):
        line = app_classes.get(symbol) or app_functions.get(symbol) or 1
        rows.append(
            route_row(
                f"CORE-GITHUB-APP-{symbol}",
                "github_app",
                symbol,
                f"github_app_distribution.{symbol}",
                f"plugins/evidence-lane-plugin/src/evidence_lane_plugin/github_app_distribution.py:{line}",
                installed_status="NOT_INSTALLED_2_1",
                runtime_status="TEST_ONLY_NO_SERVICE_MCP_SDK_SKILL_ROUTE",
                tests="tests/test_github_app_distribution.py",
                gap="IMPLEMENTED_CORE_BUT_UNROUTED",
                decision="ADD_GOVERNED_GITHUB_APP_SERVICE_AND_PUBLIC_ROUTE",
                delta="DELTA-GITHUB-APP-RUNTIME",
                notes="Least-privilege manifest/token/webhook/check/entitlement logic exists, but no production route imports it.",
                now=now,
            )
        )
    for symbol in ("run_agent_execution_harness", "evaluate_github_aw_access", "audit_workflow_action_pins"):
        rows.append(
            route_row(
                f"CORE-GITHUB-AUTOMATION-{symbol}",
                "github_automation",
                symbol,
                f"github_automation_governance.{symbol}",
                f"plugins/evidence-lane-plugin/src/evidence_lane_plugin/github_automation_governance.py:{automation_functions[symbol]}",
                installed_status="INSTALLED_CORE_FILE_PRESENT",
                runtime_status="TEST_OR_MCP_FILTER_HELPER_ONLY",
                tests="tests/test_github_automation_governance.py;tests/test_ci_workflow_adapters.py",
                gap="IMPLEMENTED_CORE_ROUTE_REVIEW",
                decision="BIND_GOVERNED_USER_WORKFLOW_OR_MARK_MAINTAINER_INTERNAL",
                delta="DELTA-GITHUB-AUTOMATION-ROUTE",
                notes="The MCP allowlist helper is used internally; harness, access evaluation, and workflow-pin audit are not exposed as one supported user workflow.",
                now=now,
            )
        )

    rows.extend(
        [
            route_row(
                "CORE-CANON-PRODUCTION-DISPATCHER",
                "canon",
                "production linked-task dispatcher",
                "create_mcp_server(canon_dispatcher)",
                "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py:658;run_server:2988",
                mcp_tool="canon_dispatch_linked_task",
                mcp_status="SOURCE_PUBLIC_SDK_NATIVE",
                sdk_module="canon_input",
                sdk_operation="dispatch_linked_task",
                sdk_status="HANDLER_REGISTERED",
                skill_path="skills/evi-canon/SKILL.md",
                skill_status="SOURCE_PRESENT",
                installed_status="NOT_INSTALLED_2_1",
                runtime_status="FAIL_CLOSED_NO_PRODUCTION_DISPATCHER_INJECTION",
                tests="tests/test_canon_task_graph.py;tests/test_internal_sdk.py",
                gap="IMPLEMENTED_CORE_BUT_PRODUCTION_HOST_SEAM_UNROUTED",
                decision="INJECT_SUPPORTED_CODEX_HOST_DISPATCHER_OR_RETAIN_EXPLICIT_FAIL_CLOSED_STATUS",
                delta="DELTA-CANON-HOST-DISPATCH",
                notes=f"Factory accepts injection; run_server supplies none. production_dispatcher_injected={production_dispatcher_injected}.",
                now=now,
            ),
            route_row(
                "CORE-CANON-SCHEMA-ASSETS",
                "canon",
                "Canon schema assets",
                "canon_task_graph embedded schema plus JSON schemas",
                "plugins/evidence-lane-plugin/src/evidence_lane_plugin/schemas;canon_task_graph.py:168",
                mcp_status="CANON_ACTIONS_SOURCE_PUBLIC",
                sdk_status="CANON_SDK_REGISTERED",
                skill_path="skills/evi-canon/SKILL.md",
                skill_status="SOURCE_PRESENT",
                installed_status="NOT_INSTALLED_2_1",
                runtime_status="THREE_JSON_SCHEMAS_PLUS_EMBEDDED_SQLITE_DDL",
                tests="tests/test_canon_task_graph.py",
                gap="SCHEMA_ASSET_PARITY_GAP",
                decision="EXTERNALIZE_VERSIONED_CANON_LEDGER_AND_RECEIPT_SCHEMAS_WITH_MIGRATION_TESTS",
                delta="DELTA-CANON-SCHEMA-PARITY",
                notes=f"schema_id_count={len(canon_schema_ids)}; first_class_json_schema_assets={len(source_schema_assets)}.",
                now=now,
            ),
            route_row(
                "CORE-LEARNING-EXPIRY",
                "agent_learning",
                "expire_learning_candidates",
                "agent_learning.expire_learning_candidates",
                f"plugins/evidence-lane-plugin/src/evidence_lane_plugin/agent_learning.py:{learning_functions['expire_learning_candidates']}",
                mcp_status="NO_PUBLIC_MCP_ROUTE",
                sdk_module="agent_learning",
                sdk_status="NO_EXPIRY_OPERATION_IN_ABI",
                skill_path="skills/evi-learning/SKILL.md",
                skill_status="NOT_NAMED",
                installed_status="NOT_INSTALLED_2_1",
                runtime_status="TEST_ONLY_NO_SCHEDULER_OR_PUBLIC_ROUTE",
                tests="tests/test_agent_learning.py",
                gap="IMPLEMENTED_CORE_BUT_UNROUTED",
                decision="ADD_BOUNDED_EXPLICIT_EXPIRY_ROUTE_OR_DOCUMENT_MAINTENANCE_OWNER",
                delta="DELTA-LEARNING-EXPIRY",
                notes="Expiry is append-only and Project Truth safe, but no supported invocation route is present.",
                now=now,
            ),
            route_row(
                "CORE-LEARNING-SCHEMA-ASSETS",
                "agent_learning",
                "Agent Learning schema assets",
                "agent_learning embedded SQLite and receipt schemas",
                "plugins/evidence-lane-plugin/src/evidence_lane_plugin/agent_learning.py:152",
                mcp_status="FIVE_SOURCE_PUBLIC_ACTIONS",
                sdk_module="agent_learning",
                sdk_status="FIVE_HANDLERS_REGISTERED",
                skill_path="skills/evi-learning/SKILL.md",
                skill_status="SOURCE_PRESENT",
                installed_status="NOT_INSTALLED_2_1",
                runtime_status="EMBEDDED_SQLITE_DDL_NO_FIRST_CLASS_SCHEMA_ASSETS",
                tests="tests/test_agent_learning.py",
                gap="SCHEMA_ASSET_PARITY_GAP",
                decision="EXTERNALIZE_VERSIONED_LEARNING_LEDGER_AND_RECEIPT_SCHEMAS_WITH_MIGRATION_TESTS",
                delta="DELTA-LEARNING-SCHEMA-PARITY",
                notes=f"schema_id_count={len(learning_schema_ids)}; first_class_schema_assets=0.",
                now=now,
            ),
            route_row(
                "CORE-ENV-UOP-OPERATOR-EXECUTION",
                "env_uop",
                "compile and route ENV/UOP operator formulas",
                "mode_governance.govern_mode_selection",
                "plugins/evidence-lane-plugin/src/evidence_lane_plugin/mode_governance.py:601;internal_sdk.py:199",
                mcp_tool="mode_classify",
                mcp_status="CLASSIFICATION_AND_RECEIPT_ONLY",
                sdk_module="env_uop_operator_runtime",
                sdk_operation="compile_formula,route_operator",
                sdk_status="DECLARED_HANDLERS_MISSING",
                skill_path="skills/evi-mode/SKILL.md",
                skill_status="SOURCE_AND_INSTALLED_PRESENT",
                installed_status="INSTALLED_2_1_PARTIAL",
                runtime_status="LOCKED_FLASH_AND_MODE_CONTRACT_PRESENT_EXECUTOR_ABSENT",
                tests="tests/test_env_uop_dual_identity.py;tests/test_operating_modes.py;tests/test_session_flash.py",
                gap="IMPLEMENTED_CONTRACT_BUT_UNROUTED_EXECUTION",
                decision="IMPLEMENT_EFFECT_BOUNDED_FORMULA_COMPILER_AND_OPERATOR_ROUTER_OR_NARROW_CLAIMS",
                delta="DELTA-ENV-UOP-EXECUTION",
                notes=f"public_tools={env_public_tools}; missing_sdk_handlers={sdk_missing_env}.",
                now=now,
            ),
        ]
    )

    test_rows = [
        (
            "TEST-GITHUB-APP",
            "github_app",
            "tests/test_github_app_distribution.py",
            "module",
            "UNIT",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Manifest, token, webhook, check-run, and entitlement core contracts.",
            "Does not prove service/MCP/SDK/skill wiring or real GitHub App installation.",
            now,
        ),
        (
            "TEST-GITHUB-CI",
            "github_ci",
            ".github/workflows/*.yml",
            CI_COMMIT,
            "LIVE_EXACT_COMMIT",
            "PASS_THREE_RUNS",
            "Python CI, CodeQL, and preview build passed for the exact immutable commit.",
            "Does not prove the newer dirty/untracked workspace or uninstalled source 2.2 changes.",
            now,
        ),
        (
            "TEST-CANON",
            "canon",
            "tests/test_canon_task_graph.py;tests/test_internal_sdk.py",
            "canon",
            "UNIT_INTEGRATION_WITH_INJECTED_DISPATCHER",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Canon graph, HIL separation, SDK handlers, and injected dispatcher fail-closed behavior.",
            "Does not prove production run_server dispatcher wiring or installed 2.1 exposure.",
            now,
        ),
        (
            "TEST-LEARNING",
            "agent_learning",
            "tests/test_agent_learning.py;tests/test_internal_sdk.py",
            "learning",
            "UNIT_INTEGRATION",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Project-isolated candidates, decisions, retrieval, revocation, expiry, and SDK handlers.",
            "Does not prove installed/public availability or production scheduling of expiry.",
            now,
        ),
        (
            "TEST-ENV-UOP",
            "env_uop",
            "tests/test_env_uop_dual_identity.py;tests/test_operating_modes.py;tests/test_session_flash.py",
            "env_uop",
            "UNIT_INTEGRATION",
            "PRESENT_NOT_EXECUTED_THIS_RESEARCH_PHASE",
            "Locked identity, derived projection, mode classification, and receipt validation.",
            "Does not prove SDK compile_formula/route_operator execution because those handlers are absent.",
            now,
        ),
    ]

    findings = [
        (
            "GAP-GITHUB-APP-PRODUCTION-ROUTE",
            "github_app",
            "HIGH",
            "IMPLEMENTED_CORE_BUT_UNROUTED",
            "GitHub App manifest, token, webhook, check-run, and tester entitlement code exists and is unit-tested, but no production service, MCP, SDK, or skill route imports it.",
            "CORE-GITHUB-APP-*;tests/test_github_app_distribution.py",
            "The product cannot expose the implemented GitHub App workflow through its supported runtime.",
            "Add a governed service and public route with least-privilege installation binding, secret isolation, webhook replay protection, and real-provider integration proof.",
            "DELTA-GITHUB-APP-RUNTIME",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-GITHUB-CI-CURRENT-DIRTY-BOUNDARY",
            "github_ci",
            "HIGH",
            "EVIDENCE_FRESHNESS_BOUNDARY",
            f"Three GitHub runs passed exact commit {CI_COMMIT}, but that immutable proof does not cover the current dirty/untracked source tree.",
            ";".join(run["url"] for run in CI_RUNS),
            "Treating the runs as current-source proof would be a false acceptance claim.",
            "Keep the commit receipt accepted for Row196 and rerun the governed exact-commit route only at the next authorized Git boundary.",
            "DELTA-GIT-EVIDENCE-FRESHNESS",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CANON-PRODUCTION-DISPATCHER",
            "canon",
            "HIGH",
            "IMPLEMENTED_CORE_BUT_UNROUTED_RUNTIME",
            "Canon linked-task dispatch is implemented and injectable in tests, but production run_server constructs the MCP server without a CanonTaskDispatcher.",
            "mcp_server.py:658,2988;internal_sdk.py:1070;tests/test_canon_task_graph.py",
            "The public action correctly fails closed instead of creating a linked host task.",
            "Provide an exact supported Codex host dispatcher seam with exact-once task UUID/deep-link receipts, or leave the capability explicitly unavailable.",
            "DELTA-CANON-HOST-DISPATCH",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-CANON-SCHEMA-ASSET-PARITY",
            "canon",
            "HIGH",
            "SCHEMA_ASSET_PARITY_GAP",
            f"Canon declares {len(canon_schema_ids)} schema identities but ships only {len(source_schema_assets)} first-class Canon JSON schema assets; its SQLite DDL remains embedded in canon_task_graph.py.",
            "canon_task_graph.py:168;canon_runtime_continuity.py:20;src/evidence_lane_plugin/schemas",
            "Schema evolution and external validation depend on implementation code rather than an independently versioned contract/migration surface.",
            "Ship first-class schemas for ledgers and receipts, explicit migration/version rules, and byte-parity tests against the builder.",
            "DELTA-CANON-SCHEMA-PARITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-LEARNING-INSTALLED-AND-EXPIRY-ROUTES",
            "agent_learning",
            "HIGH",
            "IMPLEMENTED_CORE_BUT_UNROUTED_INSTALLED",
            "Five Agent Learning actions are source-public but absent from installed 2.1; expiry is additionally core-only with no public or scheduled route.",
            "MCP-learning_*;CORE-LEARNING-EXPIRY;AUTH-INSTALLED-PLUGIN-MANIFEST",
            "The accepted installed runtime cannot use Learning, and candidates cannot be deterministically expired through a supported workflow.",
            "Complete schema/route/test parity, define explicit expiry ownership, then install only at the governed release boundary.",
            "DELTA-LEARNING-RUNTIME-PARITY",
            "OPEN",
            "HIGH",
            now,
        ),
        (
            "GAP-ENV-UOP-EXECUTION-ROUTE",
            "env_uop",
            "HIGH",
            "IMPLEMENTED_CONTRACT_BUT_UNROUTED_EXECUTION",
            "ENV/UOP locked bytes, projections, mode contracts, formula displays, and receipts exist, but SDK compile_formula and route_operator are declared without handlers and no equivalent MCP execution actions exist.",
            "mode_governance.py:601;internal_sdk.py:199;capability_route:SDK-env_uop_operator_runtime-*",
            "The runtime can classify and describe operators but cannot execute the advertised derived-operator write arms.",
            "Implement effect-bounded compiler/router handlers with explicit lane/tool budgets and tests, or narrow the public claims to classification only.",
            "DELTA-ENV-UOP-EXECUTION",
            "OPEN",
            "HIGH",
            now,
        ),
    ]

    with connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-GITHUB-WORKFLOWS",
                "github_workflow_catalog",
                str(WORKSPACE / ".github" / "workflows"),
                workflow_sha,
                workflow_bytes,
                "LIVE_DIRTY",
                "Current source workflow authority",
                f"{workflow_count} workflows; {len(external_uses)} external uses; unpinned={len(unpinned_external)}.",
                now,
            ),
        )
        for authority_id, kind, path, role, notes in [
            ("AUTH-GITHUB-APP-CORE", "source_module", github_app, "GitHub App distribution core", "Source-only, no production route."),
            ("AUTH-CANON-GRAPH", "source_module", canon_graph, "Canon graph and ledger core", "Source-only relative to installed 2.1."),
            ("AUTH-CANON-CONTINUITY", "source_module", canon_continuity, "Canon runtime continuity core", "Lifecycle-used source module."),
            ("AUTH-AGENT-LEARNING", "source_module", learning, "Agent Learning core", "Source-only relative to installed 2.1."),
            ("AUTH-ENV-UOP-MODE", "source_module", mode, "ENV/UOP mode/operator contract", "Classification and receipt projection."),
            ("AUTH-ENV-UOP-FLASH", "source_module", flash, "ENV/UOP locked Flash verifier", "Verifies immutable authority and derived projection."),
            ("AUTH-ENV-UOP-PROJECTION", "source_module", projection, "ENV/UOP runtime projection", "Derived content-addressed SQLite projection."),
        ]:
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (authority_id, kind, str(path), sha256_file(path), path.stat().st_size, "LIVE_DIRTY", role, notes, now),
            )
        for run in CI_RUNS:
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"AUTH-CI-RUN-{run['run_id']}",
                    "github_actions_run",
                    run["url"],
                    CI_COMMIT.upper(),
                    None,
                    "IMMUTABLE_EXACT_COMMIT_PROOF",
                    run["name"],
                    f"conclusion={run['conclusion']}; head_sha={CI_COMMIT}; tree={CI_TREE}",
                    now,
                ),
            )
        connection.executemany(
            "INSERT OR REPLACE INTO capability_route VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO test_evidence VALUES (?,?,?,?,?,?,?,?,?)",
            test_rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            findings,
        )

        upsert_fts(
            connection,
            doc_id="FTS-STEP10-GITHUB-CI",
            doc_type="research_finding",
            title="GitHub App, automation, Git, CI, and release parity",
            body=(
                f"workflow_count={workflow_count}; external_actions={len(external_uses)}; unpinned_external={len(unpinned_external)}; "
                f"exact_commit={CI_COMMIT}; passing_runs={len(CI_RUNS)}; github_app_production_refs={github_app_production_refs}. "
                "GitHub App core is source-implemented and unit-tested but has no service/MCP/SDK/skill production route. "
                "Exact-commit CI is immutable evidence and does not cover the newer dirty worktree."
            ),
            evidence_locator="AUTH-GITHUB-WORKFLOWS;AUTH-CI-RUN-*;CORE-GITHUB-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP11-CANON",
            doc_type="research_finding",
            title="Canon core, schema, SDK, skill, host seam, and installed parity",
            body=(
                f"canon_core_functions={len(canon_functions)}; continuity_functions={len(canon_continuity_functions)}; "
                f"schema_ids={len(canon_schema_ids)}; schema_assets={len(source_schema_assets)}; "
                f"production_dispatcher_injected={production_dispatcher_injected}. Sixteen Canon actions and the evi-canon skill are source-only."
            ),
            evidence_locator="MCP-canon_*;SDK-canon_input-*;CORE-CANON-*;GAP-CANON-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP12-LEARNING",
            doc_type="research_finding",
            title="Agent Learning storage, actions, isolation, expiry, and installed parity",
            body=(
                f"core_functions={len(learning_functions)}; source_public_actions={len(set(learning_public_mcp))}; "
                f"schema_ids={len(learning_schema_ids)}; first_class_schema_assets=0; unrouted_core={learning_unrouted_core}. "
                "Project Truth pointer guards and separate Learning pointer/history are implemented; installed 2.1 has no Learning surface."
            ),
            evidence_locator="MCP-learning_*;SDK-agent_learning-*;CORE-LEARNING-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP13-ENV-UOP",
            doc_type="research_finding",
            title="ENV/UOP authority, projection, mode, formula, and operator execution parity",
            body=(
                f"locked_flash=ENV15_UOP15_PUBLIC_LOCKED_20260807; public_tools={env_public_tools}; "
                f"missing_sdk_handlers={sdk_missing_env}. The source verifies immutable ENV/UOP, builds a derived SQLite projection, "
                "and returns formula/operator receipts, but lacks effectful compile_formula and route_operator handlers."
            ),
            evidence_locator="AUTH-ENV-UOP-*;CORE-ENV-UOP-OPERATOR-EXECUTION;SDK-env_uop_operator_runtime-*",
        )

        for step, locator, event in [
            (10, "FTS-STEP10-GITHUB-CI", "EVT-STEP10-GITHUB-CI-COMPLETE"),
            (11, "FTS-STEP11-CANON", "EVT-STEP11-CANON-COMPLETE"),
            (12, "FTS-STEP12-LEARNING", "EVT-STEP12-LEARNING-COMPLETE"),
            (13, "FTS-STEP13-ENV-UOP", "EVT-STEP13-ENV-UOP-COMPLETE"),
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
            step_no=14,
            to_status="in_progress",
            evidence_locator="lane_contract:18 canonical lanes",
            event_id="EVT-STEP14-LANE-REGISTRY-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEPS10-13-DOMAINS",
            event_type="RESEARCH_STEPS_COMPLETED",
            payload={
                "steps": [10, 11, 12, 13],
                "ci_commit": CI_COMMIT,
                "ci_tree": CI_TREE,
                "passing_ci_runs": [run["run_id"] for run in CI_RUNS],
                "workflow_unpinned_external_uses": unpinned_external,
                "github_app_production_refs": github_app_production_refs,
                "canon_schema_id_count": len(canon_schema_ids),
                "canon_schema_asset_count": len(source_schema_assets),
                "canon_production_dispatcher_injected": production_dispatcher_injected,
                "learning_schema_id_count": len(learning_schema_ids),
                "learning_unrouted_core": learning_unrouted_core,
                "env_uop_missing_sdk_handlers": sdk_missing_env,
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
                "completed_steps": [10, 11, 12, 13],
                "in_progress_step": 14,
                "ci_exact_commit": CI_COMMIT,
                "ci_pass_runs": len(CI_RUNS),
                "workflow_unpinned_external_uses": unpinned_external,
                "github_app_production_refs": github_app_production_refs,
                "canon_schema_ids": len(canon_schema_ids),
                "canon_schema_assets": len(source_schema_assets),
                "canon_production_dispatcher_injected": production_dispatcher_injected,
                "learning_schema_ids": len(learning_schema_ids),
                "learning_unrouted_core": learning_unrouted_core,
                "env_uop_missing_sdk_handlers": sdk_missing_env,
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
