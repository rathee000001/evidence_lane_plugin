from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SOURCE = PLUGIN / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from evidence_lane_plugin.constants import (
    GOVERNED_SKILL_COUNT,
    NATIVE_TOOL_COUNT,
)


def _json(relative: str) -> dict:
    value = json.loads((PLUGIN / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _canonical_package_bytes(path: Path) -> bytes:
    """Match the Git-archive text normalization used by the package registry."""

    payload = path.read_bytes()
    if b"\0" not in payload[:8000]:
        return payload.replace(b"\r\n", b"\n")
    return payload


def test_installed_package_has_complete_visible_surface() -> None:
    for name in (
        ".codex-plugin",
        "authorities",
        "env",
        "hooks",
        "manifests",
        "mcp",
        "schemas",
        "scripts",
        "sdk",
        "skills",
        "src",
        "tests",
        "toolchains",
        "uop",
    ):
        assert (PLUGIN / name).is_dir(), name
    assert not (PLUGIN / "src" / "evidence_lane_plugin" / "schemas").exists()
    assert not (PLUGIN / "src" / "evidence_lane_plugin" / "session_flash").exists()
    assert not list((PLUGIN / "scripts").glob("*poc*.py"))


def test_installed_mcp_sdk_skill_and_hook_counts_match_without_commands() -> None:
    from evidence_lane_plugin.mcp_server import create_mcp_server

    tools = asyncio.run(create_mcp_server().list_tools())
    runtime = _json("src/evidence_lane_plugin/runtime-public-catalog.v1.json")
    hooks = _json("hooks/hooks.json")
    logical = _json("hooks/logical-actions.json")
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hooks["hooks"].values()
        for group in groups
    )
    assert len(tools) == runtime["tools"] == NATIVE_TOOL_COUNT
    assert len(list((PLUGIN / "skills").glob("*/SKILL.md"))) == GOVERNED_SKILL_COUNT
    assert not (PLUGIN / "commands").exists()
    assert not (PLUGIN / ".codex-plugin" / "migrated-command-skills").exists()
    assert len(hooks["hooks"]) == 11
    assert handler_count == sum(map(len, logical["logicalActions"].values())) == 44


def test_installed_mcp_stdio_catalog_is_ready_without_eager_ocr(
    tmp_path: Path,
) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": "evidence_lane_installed_surface_smoke",
                    "version": "1.0.0",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["EVIDENCE_LANE_PLUGIN_ROOT"] = str(PLUGIN)
    environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(
        tmp_path / "runtime-control"
    )
    process = subprocess.run(
        [
            sys.executable,
            str(PLUGIN / "scripts" / "run_mcp.py"),
            "--transport",
            "stdio",
        ],
        input="".join(
            json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n"
            for message in messages
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=PLUGIN,
        env=environment,
        check=False,
        timeout=600,
    )
    assert process.returncode == 0, process.stderr[-2000:]
    responses = [
        json.loads(line) for line in process.stdout.splitlines() if line.strip()
    ]
    initialize = next(row for row in responses if row.get("id") == 1)
    tools = next(row for row in responses if row.get("id") == 2)
    listed = list(tools["result"]["tools"])
    assert initialize["result"]["serverInfo"] == {
        "name": "Evidence Lane",
        "version": "3.0.0",
        "icons": [
            {
                "src": "https://evidencelane.org/evidence-lane-icon.png",
                "mimeType": "image/png",
                "sizes": ["256x256"],
            }
        ],
        "websiteUrl": "https://evidencelane.org",
    }
    assert len(listed) == NATIVE_TOOL_COUNT
    assert listed[0]["name"] == "runtime_doctor"
    assert "RapidOCR" not in process.stderr


def test_installed_schema_surface_is_complete_and_source_bound() -> None:
    public = _json("schemas/public-action-schemas.v001.json")
    manifest = _json("schemas/schema-manifest.v1.json")
    action_files = sorted((PLUGIN / "schemas" / "actions").glob("*.json"))
    action_rows = [_json(path.relative_to(PLUGIN).as_posix()) for path in action_files]
    assert manifest["status"] == "PASS"
    assert manifest["generated_from_current_executable_source_only"] is True
    assert manifest["historical_schema_fallback_allowed"] is False
    assert manifest["canonical_public_catalog_sha256"] == _sha256(
        PLUGIN / "schemas" / "public-action-schemas.v001.json"
    )
    assert (
        manifest["public_action_schema_count"]
        == public["tool_count"]
        == NATIVE_TOOL_COUNT
    )
    assert manifest["project_sector_schema_surface_count"] == 18
    assert manifest["named_authority_schema_surface_count"] == 11
    assert len(action_rows) == NATIVE_TOOL_COUNT
    assert {row["name"] for row in action_rows} == {
        row["name"] for row in public["tools"]
    }
    assert manifest["schema_file_count"] >= 100
    for row in manifest["members"]:
        path = PLUGIN / row["path"]
        assert path.is_file(), row["path"]
        assert path.stat().st_size == row["bytes"]
        assert _sha256(path) == row["sha256"]


def test_installed_sdk_contains_internal_env_uop_and_outer_routing_planes() -> None:
    public = _json("schemas/public-action-schemas.v001.json")
    manifest = _json("sdk/sdk-manifest.v1.json")
    actions = _json("sdk/internal/public-action-registry.v1.json")
    env_uop = _json("sdk/env_uop/action-plane.v1.json")
    routing = _json("sdk/routing/mcp-action-routing.v1.json")
    module_registry = _json("src/evidence_lane_plugin/module-registry.v1.json")
    assert manifest["internal_and_outer_layers_distinct"] is True
    assert manifest["env_uop_plane_distinct_from_public_actions"] is True
    assert len(manifest["visible_surfaces"]) == manifest["visible_surface_count"]
    assert manifest["visible_surface_count"] >= 400
    assert manifest["workflow_projection_count"] == GOVERNED_SKILL_COUNT
    workflow_root = PLUGIN / "sdk" / "workflows"
    assert len(list(workflow_root.glob("*.workflow.v1.json"))) == GOVERNED_SKILL_COUNT
    assert (workflow_root / "skill-workflow-registry.v1.json").is_file()
    assert (workflow_root / "surface-workflow-registry.v1.json").is_file()
    for relative, sha256 in manifest["visible_surfaces"].items():
        assert _sha256(PLUGIN / relative) == sha256
    assert actions["action_count"] == len(actions["actions"]) == NATIVE_TOOL_COUNT
    assert actions["actions"] == public["tools"]
    assert env_uop["plane"]["plane_id"] == "ENV_UOP_AI_ACTION_PLANE"
    assert routing["action_count"] == len(routing["actions"]) == NATIVE_TOOL_COUNT
    assert manifest["internal_module_binding_count"] == module_registry["module_count"]
    assert manifest["outer_route_binding_count"] == NATIVE_TOOL_COUNT
    assert manifest["project_sector_authority_binding_count"] == 18
    assert manifest["named_authority_binding_count"] == 11
    assert manifest["hook_event_binding_count"] == 11
    support = public["sdk_planes"]["internal_support_bindings"]
    assert support["status"] == "PASS"
    assert {row["component"] for row in support["components"]} == {
        "env_uop_graph",
        "env_uop_tool_routing",
        "evaluation_toolchain",
        "github_toolchain",
        "live_root_normalization",
        "runtime_api",
    }


def test_installed_non_sector_authorities_are_complete_and_not_merged() -> None:
    registry = _json("authorities/authority-surface-registry.v1.json")
    assert registry["authority_count"] == 11
    assert registry["sqlite_authority_count"] == 10
    assert registry["non_sqlite_authority_count"] == 1
    assert registry["sector_lane_count"] == 18
    assert registry["authorities_are_outside_sector_count"] is True
    assert registry["sqlite_mmd_dot_tools_pointer_runtime_complete"] is True
    assert [row["authority_id"] for row in registry["authorities"]] == [
        "agent_learning",
        "canon_input",
        "project_memory",
        "project_overlay",
        "source_authority",
        "project_universe",
        "connector_brain",
        "project_authority",
        "receipt_ledger",
        "session_authority",
        "instructions",
    ]
    for row in registry["authorities"]:
        authority_id = row["authority_id"]
        root = PLUGIN / "authorities" / authority_id
        manifest = _json(f"authorities/{authority_id}/manifest.v1.json")
        assert manifest["authority_merged_with_sector_or_peer"] is False
        for member in manifest["members"]:
            path = PLUGIN / member["path"]
            assert path.is_file()
            assert path.stat().st_size == member["bytes"]
            assert _sha256(path) == member["sha256"]
        if row["persistent_sqlite_authority"]:
            database = root / manifest["database"]
            connection = sqlite3.connect(
                f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
                uri=True,
            )
            try:
                assert (
                    connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                )
                assert list(connection.execute("PRAGMA foreign_key_check")) == []
            finally:
                connection.close()
        else:
            assert manifest["persistent_sqlite_authority"] is False
            assert row["database_sha256"] is None


def test_installed_executable_tree_registry_hashes_every_declared_member() -> None:
    registry = _json("manifests/executable-surface-registry.v1.json")
    assert registry["status"] == "PASS"
    assert registry["installed_directory_count"] == 16
    assert registry["local_cache_or_output_included"] is False
    assert registry["historical_fallback_used"] is False
    assert registry["all_installed_members_hash_bound"] is True
    assert len(registry["members"]) == registry["member_count"]
    assert len({row["path"] for row in registry["members"]}) == registry["member_count"]
    for row in registry["members"]:
        path = PLUGIN / row["path"]
        assert path.is_file(), row["path"]
        payload = _canonical_package_bytes(path)
        assert len(payload) == row["bytes"]
        assert hashlib.sha256(payload).hexdigest().upper() == row["sha256"]
    assert "remote_adapter/" not in registry["repository_only_exclusions"]
    assert registry["repository_companion_surfaces"] == ["apps/evidence-lane-app/"]
    assert "tests/tools/" in registry["repository_only_exclusions"]


def test_installed_env_uop_are_single_executable_authorities() -> None:
    manifest = _json("env/SESSION_FLASH_MANIFEST.json")
    assert manifest["member_count"] == len(manifest["members"])
    assert not (PLUGIN / "env" / "authority").exists()
    assert not (PLUGIN / "uop" / "authority").exists()
    for relative, table in (
        ("env/env_sqlite.sqlite", "env_workflow_event_v17"),
        ("uop/uop_sqlite.sqlite", "uop_workflow_gate_v17"),
    ):
        connection = sqlite3.connect(
            f"file:{(PLUGIN / relative).resolve().as_posix()}?mode=ro", uri=True
        )
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() == (1,)
        finally:
            connection.close()


def test_installed_eighteen_lanes_are_separate_complete_bindings() -> None:
    from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

    registry = _json("authorities/project_sectors/lane-surface-registry.v1.json")
    assert registry["status"] == "PASS"
    assert registry["lane_count"] == len(CANONICAL_LANE_IDS) == 18
    assert [row["lane_id"] for row in registry["lanes"]] == list(CANONICAL_LANE_IDS)
    sqlite_hashes: set[str] = set()
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        lane_root = PLUGIN / "authorities" / "project_sectors" / lane_id
        expected = {
            "README.md",
            "builder-contract.schema.json",
            "builder.py",
            "dot-artifact.schema.json",
            "reader.py",
            "reader-contract.schema.json",
            "schema.sql",
            "lane-contract.v1.json",
            "lane-pointer.schema.json",
            "manifest.schema.json",
            "mmd-artifact.schema.json",
            "refresh-receipt.schema.json",
            "sqlite-artifact.schema.json",
            "tools.json",
            "tools.schema.json",
            "manifest.v1.json",
            "workflow.v1.json",
            "workflow.mmd",
            "workflow.dot",
            lane.sqlite_filename,
            lane.mmd_filename,
            lane.dot_filename,
        }
        assert {path.name for path in lane_root.iterdir() if path.is_file()} == expected
        database = lane_root / lane.sqlite_filename
        sqlite_hashes.add(_sha256(database))
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
        )
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                dict(connection.execute("SELECT key, value FROM lane_meta"))["lane_id"]
                == lane_id
            )
            assert (
                connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
                == 0
            )
        finally:
            connection.close()
    assert len(sqlite_hashes) == 18


def test_installed_hidden_runtime_contract_has_no_project_path_default() -> None:
    runtime = (PLUGIN / "scripts" / "runtime_contract.py").read_text(encoding="utf-8")
    restart = (
        PLUGIN / "scripts" / "codex_release" / "Prepare-EvidenceLaneCodexRestart.ps1"
    ).read_text(encoding="utf-8")
    assert ".codex" in runtime and "plugins" in runtime and "runtime" in runtime
    assert "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT" in runtime
    assert "EVIDENCE_LANE_DATA_ROOT" not in runtime
    assert "EvidenceLanePV" not in runtime
    assert "EvidenceLanePV" not in restart


def test_installed_toolchain_links_every_requirement_and_license_to_tunnel() -> None:
    matrix = _json("toolchains/tool-requirement-matrix.v1.json")
    tunnel = _json("toolchains/tunnel-runtime-toolchain.v1.json")
    licenses = _json("toolchains/tool-license-inventory.v1.json")
    tools = {row["tool"] for row in matrix["requirements"]}
    assert tunnel["requirement_count"] == len(tools)
    assert tunnel["full_matrix_requirement_count"] == len(tools)
    assert tunnel["all_tool_requirements_linked"] is True
    assert {row["tool"] for row in tunnel["requirements"]} == tools
    assert {row["tunnel_prewarm_mode"] for row in tunnel["requirements"]} == {
        "INSTALL_OR_PROBE_HIDDEN_RUNTIME",
        "REGISTER_AND_VERIFY_AT_APPLICABLE_GATE",
        "REGISTER_EXTERNAL_PROOF_GATE",
    }
    assert licenses["tool_requirement_count"] == len(tools)
    assert licenses["license_classification_count"] == len(tools)
    assert licenses["all_tool_requirements_classified"] is True
    assert licenses["mcp_inventory_separate"] is True
    assert {row["tool"] for row in licenses["rows"]} == tools
    assert all(row["license_expression_or_terms"] for row in licenses["rows"])
    assert all(row["license_evidence"] for row in licenses["rows"])
