"""Record exact source/installed/runtime identity evidence for research Step 5."""

from __future__ import annotations

import json
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
INSTALLED = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github\evidence-lane-plugin"
    r"\2.1.0+codex.20260812193232"
)
TWO_SLOT = Path(
    r"C:\Users\rathe\EvidenceLanePV\installations\codex-v200\two-slot"
    r"\CODEX_TWO_SLOT_REGISTRY.json"
)
BINDING_ROOT = Path(r"C:\Users\rathe\EvidenceLanePV\installations\codex-v200\task-bindings")
TASK6 = "01a0036f-32fa-79b2-8846-9c716d4fe777"

OFFICIAL_DOCS = {
    "AUTH-OAI-PLUGIN-CONCEPT": (
        "https://developers.openai.com/plugins/concepts/plugins",
        "Official plugin composition and root-layout contract",
    ),
    "AUTH-OAI-SKILL-CONCEPT": (
        "https://developers.openai.com/plugins/concepts/skills",
        "Official skill role and progressive-disclosure contract",
    ),
    "AUTH-OAI-MCP-CONCEPT": (
        "https://developers.openai.com/plugins/concepts/mcp-server",
        "Official MCP live-data and controlled-action contract",
    ),
    "AUTH-OAI-BUILD-SKILLS": (
        "https://developers.openai.com/plugins/build/skills",
        "Official SKILL.md and dependency-declaration guidance",
    ),
    "AUTH-OAI-PACKAGE-PLUGINS": (
        "https://developers.openai.com/plugins/build/plugins",
        "Official plugin packaging and discovery contract",
    ),
    "AUTH-OAI-HOOKS": (
        "https://learn.chatgpt.com/docs/hooks",
        "Official hook events, payloads, stop semantics, and compaction contracts",
    ),
    "AUTH-OAI-MEMORIES": (
        "https://learn.chatgpt.com/docs/customization/memories",
        "Official Codex host-memory lifecycle and storage contract",
    ),
    "AUTH-OAI-APP-SERVER": (
        "https://learn.chatgpt.com/docs/app-server",
        "Official context-compaction item lifecycle and deprecated notification contract",
    ),
}


def source_authority_row(
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


def main() -> None:
    now = utc_now()
    source_manifest = json.loads((SOURCE / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    installed_manifest = json.loads((INSTALLED / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    registry = json.loads(TWO_SLOT.read_text(encoding="utf-8-sig"))

    source_skill_paths = list((SOURCE / "skills").glob("*/SKILL.md"))
    installed_skill_paths = list((INSTALLED / "skills").glob("*/SKILL.md"))
    source_skill_sha, source_skill_count, source_skill_bytes = directory_fingerprint(
        source_skill_paths, SOURCE
    )
    installed_skill_sha, installed_skill_count, installed_skill_bytes = directory_fingerprint(
        installed_skill_paths, INSTALLED
    )
    binding_paths = list(BINDING_ROOT.glob("*.json"))
    binding_sha, binding_count, binding_bytes = directory_fingerprint(binding_paths, BINDING_ROOT)
    task6_binding = BINDING_ROOT / f"{TASK6}.json"

    source_version = str(source_manifest["version"])
    installed_version = str(installed_manifest["version"])
    source_tool_count = 83
    installed_tool_count = 62

    with connect() as connection:
        authority_rows = [
            source_authority_row(
                "AUTH-TWO-SLOT-REGISTRY",
                "installed_slot_registry",
                TWO_SLOT,
                "LIVE_INSTALLED",
                "Stable/fallback selector and activation authority",
                "PV12 stable enabled, fallback disabled, tunnel not required or started.",
                now,
            ),
            source_authority_row(
                "AUTH-SOURCE-MCP",
                "source_mcp_server",
                SOURCE / "src" / "evidence_lane_plugin" / "mcp_server.py",
                "LIVE_DIRTY",
                "Source 2.2 MCP action registration authority",
                "62 direct tools plus 21 SDK-native Canon/Learning actions.",
                now,
            ),
            source_authority_row(
                "AUTH-INSTALLED-MCP",
                "installed_mcp_server",
                INSTALLED / "src" / "evidence_lane_plugin" / "mcp_server.py",
                "LIVE_INSTALLED",
                "Installed 2.1 MCP action registration authority",
                "Installed/live catalog authority for 62 native actions.",
                now,
            ),
            source_authority_row(
                "AUTH-SOURCE-MCP-CONFIG",
                "source_mcp_config",
                SOURCE / ".mcp.json",
                "LIVE_DIRTY",
                "Source MCP process entrypoint",
                "Package-local stdio server definition.",
                now,
            ),
            source_authority_row(
                "AUTH-INSTALLED-MCP-CONFIG",
                "installed_mcp_config",
                INSTALLED / ".mcp.json",
                "LIVE_INSTALLED",
                "Installed MCP process entrypoint",
                "Byte comparison separates config parity from implementation parity.",
                now,
            ),
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO source_authority(authority_id,authority_kind,path,sha256,byte_count,freshness,authority_role,notes,recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            authority_rows,
        )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-SOURCE-SKILLS",
                "source_skill_catalog",
                str(SOURCE / "skills"),
                source_skill_sha,
                source_skill_bytes,
                "LIVE_DIRTY",
                "Source governed skill catalog",
                f"{source_skill_count} SKILL.md files.",
                now,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-INSTALLED-SKILLS",
                "installed_skill_catalog",
                str(INSTALLED / "skills"),
                installed_skill_sha,
                installed_skill_bytes,
                "LIVE_INSTALLED",
                "Installed governed skill catalog",
                f"{installed_skill_count} SKILL.md files.",
                now,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "AUTH-TASK-BINDINGS",
                "installed_task_binding_catalog",
                str(BINDING_ROOT),
                binding_sha,
                binding_bytes,
                "LIVE_INSTALLED",
                "Exact Codex task binding catalog",
                f"{binding_count} binding records; Task6 exact binding present={task6_binding.exists()}.",
                now,
            ),
        )
        schema_path = WORKSPACE / "evidence" / "task6_parity_research" / "TASK6_PARITY_RESEARCH_SCHEMA.sql"
        connection.execute(
            "UPDATE source_authority SET sha256=?, byte_count=?, recorded_at=? WHERE authority_id='AUTH-RESEARCH-SCHEMA'",
            (sha256_file(schema_path), schema_path.stat().st_size, now),
        )
        for authority_id, (url, role) in OFFICIAL_DOCS.items():
            connection.execute(
                "INSERT OR REPLACE INTO source_authority VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    authority_id,
                    "official_openai_documentation",
                    url,
                    None,
                    None,
                    "OFFICIAL_CURRENT_2026-08-15",
                    role,
                    "Bounded primary-source contract; no third-party interpretation.",
                    now,
                ),
            )

        route_rows = [
            (
                "ROUTE-PACKAGE-SOURCE-IDENTITY",
                "package_identity",
                "source_package",
                "plugin.json",
                "IMPLEMENTED",
                "plugins/evidence-lane-plugin/.codex-plugin/plugin.json",
                None,
                "N/A",
                None,
                None,
                "N/A",
                "plugins/evidence-lane-plugin/skills",
                f"PRESENT_{source_skill_count}",
                "plugins/evidence-lane-plugin/commands/evi-plan.md",
                "PRESENT",
                "NOT_INSTALLED",
                "SOURCE_ONLY_PRE_HIL",
                "DECLARATION_AND_STATIC_COUNTS",
                "tests/test_v140_version_consistency.py;tests/test_public_surface_parity_matrix.py",
                "SOURCE_INSTALLED_DRIFT",
                "RETAIN_AS_PRE_HIL_SOURCE",
                "DELTA-PACKAGE-INSTALL-PARITY",
                f"version={source_version}; actions={source_tool_count}; skills={source_skill_count}",
                now,
            ),
            (
                "ROUTE-PACKAGE-INSTALLED-IDENTITY",
                "package_identity",
                "installed_package",
                "plugin.json",
                "IMPLEMENTED_OLDER_RELEASE",
                str(INSTALLED / ".codex-plugin" / "plugin.json"),
                "evidence-lane native catalog",
                "PRESENT_62",
                None,
                None,
                "N/A",
                str(INSTALLED / "skills"),
                f"PRESENT_{installed_skill_count}",
                str(INSTALLED / "commands" / "evi-plan.md"),
                "PRESENT_OLDER_RELEASE",
                "INSTALLED_ACTIVE_STABLE",
                "LIVE_62_ACTION_CATALOG",
                "LIVE_CATALOG_PLUS_SLOT_RECEIPT",
                "AUTH-TWO-SLOT-REGISTRY;AUTH-INSTALLED-PLUGIN-MANIFEST",
                "INSTALLED_PUBLIC_RUNTIME_GAP",
                "UPGRADE_ONLY_AT_GOVERNED_GIT_INSTALL_BOUNDARY",
                "DELTA-PACKAGE-INSTALL-PARITY",
                f"version={installed_version}; actions={installed_tool_count}; skills={installed_skill_count}",
                now,
            ),
            (
                "ROUTE-PACKAGE-TASK6-BINDING",
                "host_identity",
                "exact_task6_binding",
                None,
                "ABSENT",
                str(task6_binding),
                "task_classify",
                "PUBLIC_BUT_ZERO_MATCH",
                None,
                None,
                "N/A",
                None,
                "N/A",
                None,
                "N/A",
                "ABSENT",
                "TASK6_UNBOUND",
                "LIVE_FAIL_CLOSED",
                "AUTH-TASK-BINDINGS;task_classify:CODEX_EXACT_BINDING_AMBIGUOUS_OR_MISSING",
                "HOST_SESSION_IDENTITY_GAP",
                "REQUIRE_EXPLICIT_SUPPORTED_BINDING_ROUTE_BEFORE_ANY_LIFECYCLE_CLAIM",
                "DELTA-TASK6-BINDING-ROUTE",
                "Task1-Task3 bindings exist; Task4/Task5 remain forbidden; no Task6 record exists.",
                now,
            ),
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO capability_route VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            route_rows,
        )

        findings = [
            (
                "GAP-PKG-SOURCE-INSTALLED-DRIFT",
                "package_identity",
                "HIGH",
                "INSTALLED_PUBLIC_RUNTIME_GAP",
                "Source 2.2 declares 83 actions and 17 skills while the enabled installed stable remains 2.1 with 62 actions and 15 skills.",
                "AUTH-SOURCE-PLUGIN-MANIFEST;AUTH-INSTALLED-PLUGIN-MANIFEST;AUTH-TWO-SLOT-REGISTRY",
                "Canon and Agent Learning are source-present but absent from the installed/live public surface.",
                "Preserve source/installed/live identities separately and upgrade only after governed exact-commit testing and install.",
                "DELTA-PACKAGE-INSTALL-PARITY",
                "OPEN",
                "HIGH",
                now,
            ),
            (
                "GAP-PKG-TASK6-BINDING",
                "host_identity",
                "HIGH",
                "HOST_SESSION_IDENTITY_GAP",
                "No exact binding file exists for Task6, and the live classifier returned zero matching bindings.",
                "AUTH-TASK-BINDINGS;Task6 task_classify receipt",
                "Lifecycle claims that require exact host-task binding must fail closed; Task3 identity cannot be reused.",
                "Provide a supported exact Task6-binding operation without State Travel replay or stale-task reuse.",
                "DELTA-TASK6-BINDING-ROUTE",
                "OPEN",
                "HIGH",
                now,
            ),
            (
                "GAP-PKG-INSTALLED-HOOK-WRAPPERS",
                "installed_hooks",
                "HIGH",
                "INSTALLED_RUNTIME_GAP",
                "Source hooks use sealed cross-platform invocation wrappers; installed 2.1 hooks directly invoke python and do not contain those wrappers.",
                "AUTH-SOURCE-HOOKS;AUTH-INSTALLED-HOOKS",
                "Source hook fixes do not prove current installed-host behavior.",
                "Verify source hook contracts, install exact tested bytes at the governed boundary, then probe every installed event.",
                "DELTA-HOOK-INSTALLED-PARITY",
                "OPEN",
                "HIGH",
                now,
            ),
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            findings,
        )

        upsert_fts(
            connection,
            doc_id="FTS-PACKAGE-IDENTITY-STEP05",
            doc_type="research_finding",
            title="Source, installed, slot, and Task6 binding identity",
            body=(
                f"source={source_version} actions={source_tool_count} skills={source_skill_count}; "
                f"installed={installed_version} actions={installed_tool_count} skills={installed_skill_count}; "
                f"active_slot={registry['active_slot']}; fallback_enabled={registry['slots']['fallback']['enabled']}; "
                f"tunnel_required={registry['tunnel_required']}; task6_binding_present={task6_binding.exists()}"
            ),
            evidence_locator="capability_route:ROUTE-PACKAGE-*;gap_finding:GAP-PKG-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-OFFICIAL-OPENAI-CONTRACT-INDEX",
            doc_type="official_contract_index",
            title="Official OpenAI plugin, skill, MCP, hook, memory, and compaction authorities",
            body=(
                "Plugins compose skills, optional MCP servers, and host-specific hooks. Skills carry workflow instructions; "
                "MCP carries live data and controlled actions. Host events trigger hooks. PreCompact and PostCompact are "
                "separate manual/automatic boundaries. SessionEnd is advisory. Codex memories are host-generated local state "
                "and must remain distinct from project Agent Learning. App-server compaction uses thread/compact/start and "
                "contextCompaction items; thread/compacted is legacy."
            ),
            evidence_locator="source_authority:AUTH-OAI-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-TASK3-OPENAI-ROUTING-LOCATORS",
            doc_type="task3_lineage_index",
            title="Task3 OpenAI routing and continuity instructions",
            body=(
                "Bounded Task3 locators: OpenAI lines 603,5957,6667; hook matrix line 639; installed invocation line 640; "
                "host proof and fixes lines 2742,2762,2784,2797,2817; failures lines 9221-9234 and 9271-9285; "
                "desired host-trigger/plugin-actionability architecture lines 9356-9368; memory lines 551,634; "
                "compaction lines 1544,2270,2742,2762,3724,9230."
            ),
            evidence_locator="AUTH-TASK3-MD:bounded-line-index",
        )

        transition_step(
            connection,
            step_no=5,
            to_status="completed",
            evidence_locator="FTS-PACKAGE-IDENTITY-STEP05;AUTH-TWO-SLOT-REGISTRY;AUTH-TASK-BINDINGS",
            event_id="EVT-STEP05-PACKAGE-IDENTITY-COMPLETE",
            occurred_at=now,
        )
        transition_step(
            connection,
            step_no=6,
            to_status="in_progress",
            evidence_locator="capability_route:skill-command inventory pending",
            event_id="EVT-STEP06-SKILL-COMMAND-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEP05-PACKAGE-IDENTITY",
            event_type="RESEARCH_STEP_COMPLETED",
            payload={
                "step": 5,
                "source_version": source_version,
                "source_actions": source_tool_count,
                "source_skills": source_skill_count,
                "installed_version": installed_version,
                "installed_actions": installed_tool_count,
                "installed_skills": installed_skill_count,
                "active_slot": registry["active_slot"],
                "fallback_enabled": registry["slots"]["fallback"]["enabled"],
                "task_binding_count": binding_count,
                "task6_binding_present": task6_binding.exists(),
                "canonical_plan_mutated": False,
            },
            occurred_at=now,
        )
        checks = connection.execute("PRAGMA quick_check").fetchall()
        if checks != [("ok",)]:
            raise RuntimeError(f"quick_check failed: {checks}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "step_completed": 5,
                "step_in_progress": 6,
                "source": {"version": source_version, "actions": source_tool_count, "skills": source_skill_count},
                "installed": {"version": installed_version, "actions": installed_tool_count, "skills": installed_skill_count},
                "active_slot": registry["active_slot"],
                "fallback_enabled": registry["slots"]["fallback"]["enabled"],
                "tunnel_required": registry["tunnel_required"],
                "task_binding_count": binding_count,
                "task6_binding_present": task6_binding.exists(),
                "official_openai_authorities": len(OFFICIAL_DOCS),
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
