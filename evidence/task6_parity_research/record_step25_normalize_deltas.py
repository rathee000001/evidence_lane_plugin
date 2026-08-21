"""Normalize Task6 parity findings into exact implementation Delta contracts.

This script writes only the separate Task6 research SQLite.  It never mutates
the canonical Evidence Lane Plan, Goal, project source, installed selectors,
Git, lifecycle, candidate, pointer, HIL, helper, tunnel, or host task panel.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from research_db import append_audit_event, connect, transition_step, upsert_fts, utc_now


WORKSPACE = Path(r"F:\test codex")
SCHEMA = WORKSPACE / "evidence" / "task6_parity_research" / "TASK6_PARITY_RESEARCH_SCHEMA.sql"
PROJECT_ID = "test-codex-evidence-lane-plugin"
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"
TASK6_ID = "01a0036f-32fa-79b2-8846-9c716d4fe777"
ACTIVE_TASK_ID = "EL-CODEX-GITHUB_ACTIONS_CLEAN_CI-PROPOSAL-31"
ROW197_TASK_ID = "EL-CODEX-DIRECT-FORCED-SAME-WORKTREE-STATE-TRAVEL-ROUTE-DELTA-002"
ROW198_TASK_ID = "EL-CODEX-STATE-TRAVEL-BOUNDED-HOST-ORCHESTRATION-DELTA-001"
ROW199_TASK_ID = "EL-CODEX-CHATLINEAGE-REVISION-CURSOR-ATOMIC-APPEND-003"
ROW200_TASK_ID = "EL-CODEX-PLAN-LANE-REUSABLE-SQLITE-DELTA-EXECUTION-LOG-004"
ROW201_HIL_TASK_ID = "EL-CODEX-V220-ROW196-IMMEDIATE-PV13-INSTALL-HIL-SUCCESSOR"
FINAL_HIL_TASK_ID = "EL-CODEX-NATIVE-FUSED-RELEASE-HIL-DELTA-141-NORMALIZED-SUCCESSOR"


DDL = """
CREATE TABLE IF NOT EXISTS delta_normalization_map (
    finding_id TEXT PRIMARY KEY REFERENCES gap_finding(finding_id),
    proposal_id TEXT NOT NULL REFERENCES delta_proposal(proposal_id),
    disposition TEXT NOT NULL CHECK (disposition IN ('APPEND_NEW_ROW', 'LINK_EXISTING_ROW')),
    linked_task_id TEXT,
    normalization_reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS native_append_preflight (
    preflight_id TEXT PRIMARY KEY,
    installed_plugin_identity TEXT NOT NULL,
    live_tool TEXT NOT NULL,
    required_operation TEXT NOT NULL,
    supported_operation TEXT NOT NULL,
    metadata_preserved_json TEXT NOT NULL,
    physical_final_preserved INTEGER NOT NULL CHECK (physical_final_preserved IN (0, 1)),
    atomic_batch_supported INTEGER NOT NULL CHECK (atomic_batch_supported IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('PASS', 'BLOCKED')),
    blocker TEXT NOT NULL,
    evidence_locator TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS delta_append_batch (
    batch_id TEXT PRIMARY KEY,
    proposal_count INTEGER NOT NULL,
    append_new_row_count INTEGER NOT NULL,
    link_existing_row_count INTEGER NOT NULL,
    proposal_sha256 TEXT NOT NULL,
    required_insertions_json TEXT NOT NULL,
    native_receipt_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('NORMALIZED', 'APPENDED', 'BLOCKED', 'FAILED')),
    recorded_at TEXT NOT NULL
) STRICT;
"""


LINK_EXISTING: dict[str, str] = {
    "DELTA-CHAT-LINEAGE-TYPED-RUNTIME": ROW199_TASK_ID,
    "DELTA-CHATLINEAGE-AUTO-CAPTURE": ROW199_TASK_ID,
    "DELTA-CHATLINEAGE-DURABLE-SCHEMA": ROW199_TASK_ID,
    "DELTA-COMPACTION-CONTINUITY": ROW198_TASK_ID,
    "DELTA-ENTRY-EXIT-LIFECYCLE-ROUTE": ROW199_TASK_ID,
    "DELTA-GOAL-NATIVE-LIFECYCLE-BINDING": ROW198_TASK_ID,
    "DELTA-HOST-PLAN-PANEL-CONTINUITY": ROW198_TASK_ID,
    "DELTA-PLAN-BOUNDED-WINDOW": ROW198_TASK_ID,
    "DELTA-PLAN-GOAL-COMMAND-PARITY": ROW198_TASK_ID,
    "DELTA-PLAN-SQLITE-AUTHORITY": ROW200_TASK_ID,
    "DELTA-STATE-TRAVEL-FORCED-SAME-WORKTREE": ROW197_TASK_ID,
    "DELTA-TASK-BINDING-EXACT-ATTACHMENT": ROW197_TASK_ID,
    "DELTA-TASK6-BINDING-ROUTE": ROW197_TASK_ID,
}


# New implementation rows are split around the four already-queued corrective
# rows.  PRE_ROW197 work executes immediately after the current active row;
# PRE_ROW201 release proof executes after Rows197-200 and before the PV13 HIL.
NEW_ORDER: list[tuple[str, str]] = [
    ("DELTA-PLAN-BATCH-PREHIL-INSERT-ROUTE", "PRE_ROW197"),
    ("DELTA-PER-DELTA-LOCAL-VERIFICATION", "PRE_ROW197"),
    ("DELTA-RELEASE-SLOT-REGISTRY-SEPARATION", "PRE_ROW197"),
    ("DELTA-FALLBACK-SELECTOR-AUTHORITY", "PRE_ROW197"),
    ("DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION", "PRE_ROW197"),
    ("DELTA-CANDIDATE-SELF-ROLLBACK", "PRE_ROW197"),
    ("DELTA-INSTALL-CORRECTION-RECEIPT", "PRE_ROW197"),
    ("DELTA-GOAL-RECOVERY-MULTI-BINDING", "PRE_ROW197"),
    ("DELTA-GOAL-RECOVERY-RETRY-ISOLATION", "PRE_ROW197"),
    ("DELTA-HOOK-EVENT-ISOLATION", "PRE_ROW197"),
    ("DELTA-HOOK-BEHAVIOR-ORCHESTRATION", "PRE_ROW197"),
    ("DELTA-HOOK-TEST-CONTRACT-UNIFICATION", "PRE_ROW197"),
    ("DELTA-HOOK-INSTALLED-RUNTIME", "PRE_ROW197"),
    ("DELTA-HOST-MEMORY-BOUNDARY", "PRE_ROW197"),
    ("DELTA-LANE-SCHEMA-SYSTEM", "PRE_ROW197"),
    ("DELTA-GOVERNED-LANE-SCHEMA-EVOLUTION", "PRE_ROW197"),
    ("DELTA-LANE-ARTIFACT-CONTRACT", "PRE_ROW197"),
    ("DELTA-LANE-QUERY-GUIDANCE", "PRE_ROW197"),
    ("DELTA-PARALLEL-LANE-QUERY", "PRE_ROW197"),
    ("DELTA-CROSS-LANE-QUERY", "PRE_ROW197"),
    ("DELTA-CROSS-PROJECT-QUERY", "PRE_ROW197"),
    ("DELTA-SDK-HANDLER-PARITY", "PRE_ROW197"),
    ("DELTA-SERVICE-ROUTE-REVIEW", "PRE_ROW197"),
    ("DELTA-SKILL-MCP-ROUTING", "PRE_ROW197"),
    ("DELTA-CANON-SCHEMA-PARITY", "PRE_ROW197"),
    ("DELTA-CANON-HOST-DISPATCH", "PRE_ROW197"),
    ("DELTA-LEARNING-RUNTIME-PARITY", "PRE_ROW197"),
    ("DELTA-ENV-UOP-AUTHORITY-BOUNDARY", "PRE_ROW197"),
    ("DELTA-ENV-UOP-EXECUTION", "PRE_ROW197"),
    ("DELTA-GITHUB-APP-RUNTIME", "PRE_ROW197"),
    ("DELTA-MCP-TOOL-EVAL-MATRIX", "PRE_ROW197"),
    ("DELTA-CONFORMANCE-RELEASE-GATE", "PRE_ROW197"),
    ("DELTA-GIT-EVIDENCE-FRESHNESS", "PRE_ROW201"),
    ("DELTA-PACKAGE-INSTALL-PARITY", "PRE_ROW201"),
    ("DELTA-MCP-INSTALLED-PARITY", "PRE_ROW201"),
    ("DELTA-HOOK-INSTALLED-PARITY", "PRE_ROW201"),
    ("DELTA-GOAL-RECOVERY-INSTALLED-PARITY", "PRE_ROW201"),
    ("DELTA-INSTALLED-E2E-EVALUATION", "PRE_ROW201"),
]


VERIFY_KEYS = {
    "DELTA-PER-DELTA-LOCAL-VERIFICATION",
    "DELTA-INSTALL-CORRECTION-RECEIPT",
    "DELTA-MCP-TOOL-EVAL-MATRIX",
    "DELTA-CONFORMANCE-RELEASE-GATE",
    "DELTA-GIT-EVIDENCE-FRESHNESS",
    "DELTA-PACKAGE-INSTALL-PARITY",
    "DELTA-MCP-INSTALLED-PARITY",
    "DELTA-HOOK-INSTALLED-PARITY",
    "DELTA-GOAL-RECOVERY-INSTALLED-PARITY",
    "DELTA-INSTALLED-E2E-EVALUATION",
}

FIX_KEYS = {
    "DELTA-PLAN-BATCH-PREHIL-INSERT-ROUTE",
    "DELTA-RELEASE-SLOT-REGISTRY-SEPARATION",
    "DELTA-FALLBACK-SELECTOR-AUTHORITY",
    "DELTA-LOCAL-TEST-TRANSACTIONAL-ACTIVATION",
    "DELTA-CANDIDATE-SELF-ROLLBACK",
    "DELTA-GOAL-RECOVERY-MULTI-BINDING",
    "DELTA-GOAL-RECOVERY-RETRY-ISOLATION",
    "DELTA-HOOK-EVENT-ISOLATION",
    "DELTA-HOOK-BEHAVIOR-ORCHESTRATION",
    "DELTA-HOOK-TEST-CONTRACT-UNIFICATION",
    "DELTA-HOOK-INSTALLED-RUNTIME",
    "DELTA-LANE-SCHEMA-SYSTEM",
    "DELTA-SDK-HANDLER-PARITY",
    "DELTA-SERVICE-ROUTE-REVIEW",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def title_for(key: str) -> str:
    words = key.removeprefix("DELTA-").split("-")
    acronyms = {"MCP", "SDK", "ENV", "UOP", "E2E", "CI", "FTS", "SQLITE"}
    rendered = [word if word in acronyms else word.title() for word in words]
    return " ".join(rendered)


def slug_for(key: str) -> str:
    value = re.sub(r"[^A-Z0-9-]+", "-", key.removeprefix("DELTA-").upper())
    return value[:48].strip("-")


def group_for(key: str) -> str:
    if any(token in key for token in ("INSTALL", "CANDIDATE", "SLOT", "FALLBACK", "PACKAGE")):
        return "INSTALL_RECOVERY"
    if "GOAL-RECOVERY" in key:
        return "GOAL_RECOVERY"
    if "HOOK" in key or "MEMORY" in key:
        return "HOST_LIFECYCLE"
    if "LANE" in key or "SCHEMA" in key or "QUERY" in key:
        return "LANE_DATA_SYSTEM"
    if any(token in key for token in ("MCP", "SDK", "SKILL", "SERVICE")):
        return "PUBLIC_SURFACE_PARITY"
    if any(token in key for token in ("CANON", "LEARNING", "ENV-UOP", "GITHUB-APP")):
        return "CAPABILITY_RUNTIME"
    if any(token in key for token in ("E2E", "CONFORMANCE", "VERIFICATION", "GIT-EVIDENCE")):
        return "PV13_RELEASE_GATES"
    if "PLAN" in key:
        return "PLAN_AUTHORITY"
    return "TASK6_PARITY"


def paths_for(key: str) -> list[str]:
    root = "plugins/evidence-lane-plugin"
    paths: list[str] = ["evidence/task6_delta_receipts/**"]
    if "PLAN" in key:
        paths += [
            f"{root}/src/evidence_lane_plugin/store.py",
            f"{root}/src/evidence_lane_plugin/plan_runtime.py",
            f"{root}/src/evidence_lane_plugin/host_plan_rehydration.py",
            f"{root}/src/evidence_lane_plugin/service.py",
            f"{root}/src/evidence_lane_plugin/mcp_server.py",
            "tests/test_plan_*.py",
            "tests/test_host_plan_rehydration.py",
        ]
    if any(token in key for token in ("INSTALL", "CANDIDATE", "SLOT", "FALLBACK", "PACKAGE")):
        paths += [
            f"{root}/scripts/**",
            f"{root}/.codex-plugin/plugin.json",
            f"{root}/hooks/hooks.json",
            "tests/test_local_install*.py",
            "tests/test_release_channels.py",
            "C:/Users/rathe/.codex/plugins/** (governed installer only)",
        ]
    if "GOAL-RECOVERY" in key:
        paths += [
            f"{root}/scripts/*goal*.*",
            f"{root}/scripts/*helper*.*",
            "tests/test_*goal*.py",
            "C:/Users/rathe/.codex/evidence-lane/** (task bindings and receipts only)",
        ]
    if "HOOK" in key:
        paths += [f"{root}/hooks/**", "tests/test_hook*.py", "tests/test_mcp_plugin.py"]
    if "MEMORY" in key:
        paths += [f"{root}/skills/**", f"{root}/hooks/**", f"{root}/src/evidence_lane_plugin/agent_learning.py"]
    if "LANE" in key or "QUERY" in key:
        paths += [
            f"{root}/src/evidence_lane_plugin/lanes.py",
            f"{root}/src/evidence_lane_plugin/lane_engine.py",
            f"{root}/src/evidence_lane_plugin/lane_reader.py",
            f"{root}/src/evidence_lane_plugin/schema_topology.py",
            f"{root}/schemas/**",
            f"{root}/skills/**",
            "tests/test_*lane*.py",
            "tests/test_reader_query.py",
        ]
    if "SCHEMA" in key and "CANON" not in key:
        paths += [f"{root}/schemas/**", "tests/test_*schema*.py"]
    if any(token in key for token in ("MCP", "SDK", "SKILL", "SERVICE")):
        paths += [
            f"{root}/src/evidence_lane_plugin/mcp_server.py",
            f"{root}/src/evidence_lane_plugin/service.py",
            f"{root}/src/evidence_lane_plugin/internal_sdk.py",
            f"{root}/skills/**",
            "tests/test_public_surface_parity_matrix.py",
            "tests/test_internal_sdk*.py",
        ]
    if "CANON" in key:
        paths += [
            f"{root}/src/evidence_lane_plugin/canon*.py",
            f"{root}/schemas/canon/**",
            f"{root}/skills/evi-canon/**",
            "tests/test_canon*.py",
        ]
    if "LEARNING" in key:
        paths += [f"{root}/src/evidence_lane_plugin/agent_learning.py", f"{root}/skills/evi-learning/**", "tests/test_agent_learning.py"]
    if "ENV-UOP" in key:
        paths += [
            f"{root}/src/evidence_lane_plugin/session_flash.py",
            f"{root}/src/evidence_lane_plugin/mode*.py",
            f"{root}/src/evidence_lane_plugin/internal_sdk.py",
            "tests/test_session_flash.py",
            "tests/test_*mode*.py",
        ]
    if "GITHUB-APP" in key or "GIT-EVIDENCE" in key:
        paths += [f"{root}/src/evidence_lane_plugin/github*.py", ".github/workflows/**", "tests/test_*github*.py"]
    if any(token in key for token in ("CONFORMANCE", "E2E", "VERIFICATION", "EVAL")):
        paths += ["tests/**", f"{root}/.codex-plugin/plugin.json", f"{root}/skills/**", f"{root}/hooks/**"]
    return list(dict.fromkeys(paths))


def tools_for(key: str) -> list[str]:
    tools = ["apply_patch", "rg", "shell_command:python", "shell_command:pytest-targeted"]
    if "GIT-EVIDENCE" in key:
        tools += ["git status", "git diff", "git add", "git commit", "git push", "GitHub Actions read"]
    if any(token in key for token in ("INSTALL", "PACKAGE", "MCP-INSTALLED", "HOOK-INSTALLED", "GOAL-RECOVERY-INSTALLED", "E2E")):
        tools += ["PowerShell governed installer", "native Evidence Lane diagnostics"]
    return tools


def task_class_for(key: str) -> str:
    if key in VERIFY_KEYS:
        return "verify_result"
    if key in FIX_KEYS:
        return "fix_bug"
    return "add_bounded_feature"


def add_plan_append_gap(connection: sqlite3.Connection, now: str) -> None:
    connection.execute(
        """
        INSERT INTO gap_finding(
            finding_id, domain, severity, classification, statement,
            evidence_locator, route_gap, required_contract,
            proposed_delta_key, status, confidence, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(finding_id) DO UPDATE SET
            statement=excluded.statement,
            evidence_locator=excluded.evidence_locator,
            route_gap=excluded.route_gap,
            required_contract=excluded.required_contract,
            proposed_delta_key=excluded.proposed_delta_key,
            status=excluded.status,
            confidence=excluded.confidence,
            recorded_at=excluded.recorded_at
        """,
        (
            "GAP-PLAN-BATCH-PREHIL-INSERT-API",
            "plan",
            "CRITICAL",
            "IMPLEMENTED_CORE_BUT_UNROUTED_AND_INSTALLED_METADATA_GAP",
            "The source and installed stores contain internal pre-HIL insertion support, but the public pv_plan_tasks MCP route exposes no insertion target. Installed 2.1 also drops plan_group, dependencies, commit_batch_id, and git_commit_stage. The alternative pv_plan_steer_delta route inserts only one row per call.",
            "source:service.py:1507-1571;source:store.py:1344-1711;installed:service.py:1356-1419;installed:store.py:563-852;native-catalog:pv_plan_tasks,pv_plan_steer_delta",
            "No live native operation can atomically insert a dependency-rich multi-row research batch before Row197 and Row201 while preserving the same physically-final HIL.",
            "Expose a bounded atomic batch insertion contract with exact insert-before task IDs, stable task IDs, dependencies, plan groups, commit/Git metadata, idempotent batch hash, physical-final-HIL preservation, and no Goal/HIL/pointer side effect; add installed/runtime tests.",
            "DELTA-PLAN-BATCH-PREHIL-INSERT-ROUTE",
            "OPEN",
            "HIGH",
            now,
        ),
    )


def main() -> None:
    connection = connect()
    connection.row_factory = sqlite3.Row
    now = utc_now()
    try:
        connection.executescript(DDL)
        add_plan_append_gap(connection, now)

        findings = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM gap_finding WHERE status IN ('OPEN', 'normalized') ORDER BY proposed_delta_key, finding_id"
            )
        ]
        by_key: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            key = str(finding.get("proposed_delta_key") or "").strip()
            if not key:
                raise RuntimeError(f"Unmapped finding: {finding['finding_id']}")
            by_key.setdefault(key, []).append(finding)

        expected = set(LINK_EXISTING) | {key for key, _ in NEW_ORDER}
        actual = set(by_key)
        if actual != expected:
            raise RuntimeError(
                canonical_json(
                    {
                        "missing_normalization_keys": sorted(actual - expected),
                        "missing_finding_keys": sorted(expected - actual),
                    }
                )
            )

        connection.execute("DELETE FROM delta_normalization_map")
        connection.execute("DELETE FROM delta_proposal")
        proposals: list[dict[str, Any]] = []
        prior_by_zone = {"PRE_ROW197": ACTIVE_TASK_ID, "PRE_ROW201": ROW200_TASK_ID}
        order_by_key = {key: index for index, (key, _) in enumerate(NEW_ORDER, start=1)}

        ordered_keys = [key for key, _ in NEW_ORDER] + sorted(LINK_EXISTING)
        for key in ordered_keys:
            rows = by_key[key]
            requirements = list(dict.fromkeys(str(row["required_contract"]) for row in rows))
            evidence = list(dict.fromkeys(str(row["evidence_locator"]) for row in rows))
            route_gaps = list(dict.fromkeys(str(row["route_gap"]) for row in rows))
            finding_ids = [str(row["finding_id"]) for row in rows]
            severities = list(dict.fromkeys(str(row["severity"]) for row in rows))
            title = title_for(key)

            if key in LINK_EXISTING:
                linked_task = LINK_EXISTING[key]
                contract: dict[str, Any] = {
                    "schema": "evidence-lane.task6-delta-proposal.v1",
                    "disposition": "LINK_EXISTING_ROW",
                    "linked_task_id": linked_task,
                    "do_not_add_duplicate_row": True,
                    "requirements": requirements,
                    "route_gaps": route_gaps,
                    "finding_ids": finding_ids,
                    "evidence_locators": evidence,
                    "research_db_locator": f"delta_proposal:{key}",
                }
                dependency_keys = f"EXISTING:{linked_task}"
                task_class = "fix_bug"
                group_name = "EXISTING_ROW_AMENDMENT"
                disposition = "LINK_EXISTING_ROW"
                reason = "The queued canonical row already owns this outcome; attach the evidence contract without adding a duplicate executable row."
            else:
                proposal_order = order_by_key[key]
                zone = next(zone for candidate, zone in NEW_ORDER if candidate == key)
                task_id = f"EL-CODEX-T6-PARITY-{proposal_order:03d}-{slug_for(key)}"
                dependency = prior_by_zone[zone]
                prior_by_zone[zone] = task_id
                task_class = task_class_for(key)
                git_boundary = "FINAL_GOVERNED_COMMIT_PUSH_AND_CLEAN_CI" if key == "DELTA-GIT-EVIDENCE-FRESHNESS" else "NONE"
                requested_outcome = (
                    f"{title}: implement the normalized Task6 parity correction and satisfy every evidence-linked requirement without broadening authority. "
                    + " ".join(requirements)
                )
                checks = list(requirements)
                checks += [
                    "Targeted unit and integration selectors for this exact Delta pass.",
                    "A per-Delta receipt binds changed paths, test selectors, outputs, and pre/post worktree identities.",
                    "No HIL decision, PV pointer movement, candidate acceptance, secret persistence, or unrelated dirty-byte change occurs.",
                ]
                if key == "DELTA-GIT-EVIDENCE-FRESHNESS":
                    checks += [
                        "Exactly one governed commit/push covers all verified pre-PV13 changes.",
                        "GitHub Actions clean-checkout CI passes for that exact immutable commit before installation or HIL.",
                    ]
                if key == "DELTA-CANDIDATE-SELF-ROLLBACK":
                    checks += [
                        "A failed local candidate disables itself and atomically restores the last verified stable/fallback selector without a restart loop."
                    ]
                if key == "DELTA-GOAL-RECOVERY-MULTI-BINDING":
                    checks += [
                        "One shared mutable manager handles multiple projects while every invocation creates or refreshes an exact task-scoped binding and receipt."
                    ]
                if key == "DELTA-PACKAGE-INSTALL-PARITY":
                    checks += [
                        "Install bytes are exported from and sealed to the exact CI-passing commit; Task6 reattaches without changing PV12."
                    ]
                contract = {
                    "schema": "evidence-lane.task6-delta-proposal.v1",
                    "disposition": "APPEND_NEW_ROW",
                    "proposal_order": proposal_order,
                    "insertion_zone": zone,
                    "insert_before_task_id": ROW197_TASK_ID if zone == "PRE_ROW197" else ROW201_HIL_TASK_ID,
                    "task_id": task_id,
                    "task_class": task_class,
                    "requested_outcome": requested_outcome,
                    "permitted_paths": paths_for(key),
                    "permitted_tools": tools_for(key),
                    "acceptance_checks": list(dict.fromkeys(checks))[:32],
                    "stop_condition": (
                        "Fail closed on authority, identity, dependency, selector, receipt, or test mismatch. "
                        + ("This is the sole Git mutation row; stop unless the exact commit/push and clean CI are provable. " if key == "DELTA-GIT-EVIDENCE-FRESHNESS" else "Do not stage, commit, push, install, invoke HIL, or move the pointer. ")
                        + "Complete only after the exact per-Delta local receipt passes."
                    ),
                    "plan_group": group_for(key),
                    "commit_batch_id": "PV13_TASK6_PARITY",
                    "dependencies": [dependency],
                    "git_commit_stage": git_boundary,
                    "panel_role": "STANDARD",
                    "requirements": requirements,
                    "route_gaps": route_gaps,
                    "finding_ids": finding_ids,
                    "evidence_locators": evidence,
                    "research_db_locator": f"delta_proposal:{key}",
                    "project_id": PROJECT_ID,
                    "session_id": SESSION_ID,
                    "task6_id": TASK6_ID,
                    "accepted_pv": "PV12",
                    "generation": 12,
                }
                dependency_keys = canonical_json([dependency])
                group_name = group_for(key)
                disposition = "APPEND_NEW_ROW"
                linked_task = None
                reason = "No queued canonical row fully owns this evidence-backed implementation contract."

            contract_json = canonical_json(contract)
            contract["contract_sha256"] = sha256_text(contract_json)
            contract_json = canonical_json(contract)
            git_boundary_value = str(contract.get("git_commit_stage") or "NONE")
            connection.execute(
                """
                INSERT INTO delta_proposal(
                    proposal_id, title, task_class, group_name, dependency_keys,
                    contract_json, git_boundary, status, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified', ?)
                """,
                (
                    key,
                    title,
                    task_class,
                    group_name,
                    dependency_keys,
                    contract_json,
                    git_boundary_value,
                    now,
                ),
            )
            for finding_id in finding_ids:
                connection.execute(
                    """
                    INSERT INTO delta_normalization_map(
                        finding_id, proposal_id, disposition, linked_task_id,
                        normalization_reason, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (finding_id, key, disposition, linked_task, reason, now),
                )
            connection.execute(
                "UPDATE gap_finding SET status='normalized' WHERE proposed_delta_key=?",
                (key,),
            )
            upsert_fts(
                connection,
                doc_id=f"DELTA-PROPOSAL-{key}",
                doc_type="delta_proposal",
                title=title,
                body=contract_json,
                evidence_locator=f"delta_proposal:{key}",
            )
            proposals.append(
                {
                    "proposal_id": key,
                    "disposition": disposition,
                    "linked_task_id": linked_task,
                    "contract": contract,
                    "severities": severities,
                }
            )

        proposal_payload = canonical_json(proposals)
        proposal_sha = sha256_text(proposal_payload)
        new_count = sum(row["disposition"] == "APPEND_NEW_ROW" for row in proposals)
        linked_count = len(proposals) - new_count
        pre197 = sum(
            row["disposition"] == "APPEND_NEW_ROW"
            and row["contract"].get("insertion_zone") == "PRE_ROW197"
            for row in proposals
        )
        pre201 = new_count - pre197

        preflight_id = "T6-NATIVE-PLAN-APPEND-PREFLIGHT-001"
        blocker = (
            "Installed Evidence Lane 2.1 has no live native operation that can perform the required dependency-rich atomic batch insertion. "
            "pv_plan_tasks batches only at the physical end and would place rows after the physically-final HIL; pv_plan_steer_delta inserts one row per call; "
            "installed plan_tasks drops plan_group, dependencies, commit_batch_id, and git_commit_stage."
        )
        connection.execute("DELETE FROM native_append_preflight")
        connection.execute(
            """
            INSERT INTO native_append_preflight(
                preflight_id, installed_plugin_identity, live_tool,
                required_operation, supported_operation, metadata_preserved_json,
                physical_final_preserved, atomic_batch_supported, status, blocker,
                evidence_locator, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'BLOCKED', ?, ?, ?)
            """,
            (
                preflight_id,
                "evidence-lane-plugin@evidence-lane-github 2.1.0+codex.20260812193232",
                "mcp__evidence_lane__pv_plan_tasks + mcp__evidence_lane__pv_plan_steer_delta",
                f"Atomically insert {pre197} rows before {ROW197_TASK_ID} and {pre201} rows before {ROW201_HIL_TASK_ID}; preserve structured metadata and {FINAL_HIL_TASK_ID} as physically final.",
                "pv_plan_tasks: batch append at physical end; pv_plan_steer_delta: single-row insertion before one target.",
                canonical_json(
                    {
                        "task_id": True,
                        "task_class": True,
                        "requested_outcome": True,
                        "paths_tools_checks_stop": True,
                        "panel_role": True,
                        "plan_group": False,
                        "dependencies": False,
                        "commit_batch_id": False,
                        "git_commit_stage": False,
                    }
                ),
                blocker,
                "native-catalog:pv_plan_tasks,pv_plan_steer_delta;installed:store.py:563-852;source:store.py:1344-1711",
                now,
            ),
        )
        batch_id = "T6-PARITY-NORMALIZATION-BATCH-001"
        connection.execute("DELETE FROM delta_append_batch")
        connection.execute(
            """
            INSERT INTO delta_append_batch(
                batch_id, proposal_count, append_new_row_count,
                link_existing_row_count, proposal_sha256,
                required_insertions_json, native_receipt_json, status, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'BLOCKED', ?)
            """,
            (
                batch_id,
                len(proposals),
                new_count,
                linked_count,
                proposal_sha,
                canonical_json(
                    {
                        "PRE_ROW197": pre197,
                        "PRE_ROW201": pre201,
                        "current_final_row": 210,
                        "proposed_final_row": 210 + new_count,
                        "physical_final_task_id": FINAL_HIL_TASK_ID,
                    }
                ),
                now,
            ),
        )
        transition_step(
            connection,
            step_no=25,
            to_status="in_progress",
            evidence_locator=f"delta_append_batch:{batch_id};native_append_preflight:{preflight_id}",
            event_id="T6-RESEARCH-STEP25-NORMALIZED-NATIVE-APPEND-BLOCKED-CORRECTED",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="T6-RESEARCH-STEP25-NORMALIZATION-002",
            event_type="DELTA_NORMALIZATION_COMPLETE_NATIVE_APPEND_BLOCKED",
            payload={
                "proposal_count": len(proposals),
                "append_new_row_count": new_count,
                "link_existing_row_count": linked_count,
                "pre_row197_count": pre197,
                "pre_row201_count": pre201,
                "proposal_sha256": proposal_sha,
                "proposed_final_row": 210 + new_count,
                "physical_final_task_id": FINAL_HIL_TASK_ID,
                "native_preflight_status": "BLOCKED",
                "blocker": blocker,
                "canonical_plan_mutated": False,
                "native_goal_mutated": False,
                "source_mutated": False,
                "tests_executed": False,
                "git_mutated": False,
            },
            occurred_at=now,
        )

        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        connection.commit()
        print(
            json.dumps(
                {
                    "status": "NORMALIZED_NATIVE_APPEND_BLOCKED",
                    "research_step": 25,
                    "proposal_count": len(proposals),
                    "append_new_row_count": new_count,
                    "link_existing_row_count": linked_count,
                    "insertion_counts": {"PRE_ROW197": pre197, "PRE_ROW201": pre201},
                    "proposal_sha256": proposal_sha,
                    "current_final_row": 210,
                    "proposed_final_row": 210 + new_count,
                    "physical_final_task_id": FINAL_HIL_TASK_ID,
                    "native_append_preflight": "BLOCKED",
                    "blocker": blocker,
                    "canonical_plan_mutated": False,
                    "native_goal_mutated": False,
                    "source_mutated": False,
                    "tests_executed": False,
                    "git_mutated": False,
                    "quick_check": quick,
                },
                indent=2,
            )
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
