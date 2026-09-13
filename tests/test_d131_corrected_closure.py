from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import yaml
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.hook_event_handlers import SessionStartHandler
from evidence_lane_plugin.hook_pipeline import run_hook_pipeline
from evidence_lane_plugin.operating_modes import MODE_DEFINITIONS
from evidence_lane_plugin.registry import WORKFLOWS

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"
DIALECT = "https://json-schema.org/draft/2020-12/schema"
OLD_WORKFLOWS = {
    "evi", "boot", "build", "plan", "source-intake", "canon", "memory",
    "learning", "instructions", "project-recipe", "mode", "brain-scaling",
    "refresh", "plugin", "additional-plugin", "drop-additional-plugin",
    "toolchain", "storage", "universe", "bigger-universe", "state-travel",
    "recover", "exit-boot", "lifecycle",
}
OLD_ACTIONS = {
    "pv_diff", "pv_history", "pv_summary", "project_recipe", "mode_classify",
    "brain_slice_select", "cross_project_query", "linked_projects_read",
    "project_link", "project_unlink", "universe_inspect", "universe_links_verify",
    "universe_query", *{f"bigger_universe_{suffix}" for suffix in (
        "create", "grant", "link", "read", "register", "revoke", "verify")},
    *{f"canon_{suffix}" for suffix in (
        "backfire", "classify", "decide", "expect", "graph", "inbox", "inspect",
        "join", "packet_classify", "read", "receive", "send", "supersede",
        "task_edge_bind", "task_edge_register", "task_result")},
}


def test_canonical_skill_action_and_sdk_closure(tmp_path: Path) -> None:
    with Engine(tmp_path / "engine") as engine:
        schemas = engine.registry.schemas()
        workflows = engine.registry.workflow_schemas()
    names = [workflow.name for workflow in WORKFLOWS]
    assert names == [workflow.skill for workflow in WORKFLOWS]
    assert len(names) == len(set(names)) == 24
    assert not OLD_WORKFLOWS.intersection(names)
    actions = {row["name"] for row in schemas}
    assert len(actions) == 297
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "typed engine registry exposes 297 operations" in architecture
    assert "typed engine registry exposes 294 operations" not in architecture
    assert not OLD_ACTIONS.intersection(actions)
    assert {row["name"] for row in workflows} == set(names)
    assert all(row["actions"] for row in workflows)
    assert len(list((PLUGIN / "sdk/internal/modules").glob("*.module.v4.json"))) >= 275
    assert len(list((PLUGIN / "sdk/routing/actions").glob("*.route.v4.json"))) == 297
    assert len([path for path in (PLUGIN / "sdk/workflows/skills").iterdir() if path.is_dir()]) == 24
    for old in OLD_ACTIONS:
        assert not (PLUGIN / f"sdk/actions/{old}.action.v4.json").exists()
        assert not (PLUGIN / f"mcp/actions/{old}.binding.v4.json").exists()
        assert not (PLUGIN / f"schemas/actions/{old}.v4.schema.json").exists()
    for old in OLD_WORKFLOWS:
        assert not (PLUGIN / f"sdk/workflows/{old}.workflow.v4.json").exists()


def test_hook_pipeline_uses_distinct_stage_owners(monkeypatch) -> None:
    import evidence_lane_plugin.hook_pipeline as pipeline

    monkeypatch.setattr(
        pipeline,
        "deliver_hook",
        lambda handled, selected_root=None: {
            "captured": False,
            "reason": "project_session_not_bound",
            "transport": "fixture_authenticated_capture",
            "handler_id": handled.handler_id,
        },
    )
    output, receipt = run_hook_pipeline(
        json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}).encode(),
        SessionStartHandler,
    )
    assert output == {}
    assert receipt["execution"]["stage"] == "projected"
    assert receipt["execution"]["visited"] == [
        "admitted", "classified", "handled", "delivered", "sealed", "projected"
    ]
    assert receipt["capture"]["handler_id"] == "session-start.capture"
    assert receipt["capture"]["automatic_retry"] is False


def test_env_uop_mode_policies_are_complete_and_nonempty() -> None:
    expected = {row["id"] for row in MODE_DEFINITIONS}
    observed = {}
    for role in ("env", "uop"):
        path = PLUGIN / role / f"{role}_sqlite.sqlite"
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
            assert not [name for name in tables if connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == 0]
            if role == "env":
                mode_rows = connection.execute("SELECT mode_id,policy_json FROM env_mode_policy_v4 ORDER BY mode_id").fetchall()
                work_ids = {row[0] for row in connection.execute("SELECT work_id FROM env_work_policy_v4")}
                assert {row[0] for row in mode_rows} == work_ids == expected
                assert all(json.loads(row[1])["mode_id"] == row[0] for row in mode_rows)
            else:
                observed[role] = {row[0] for row in connection.execute("SELECT work_id FROM uop_work_classification_policy_v4")}
    assert observed["uop"] == expected


def test_schema_names_and_dialects_are_unambiguous() -> None:
    named = list((PLUGIN / "schemas").rglob("*.schema.json"))
    assert len(named) == 331
    assert all(json.loads(path.read_text(encoding="utf-8"))["$schema"] == DIALECT for path in named)
    for path in (PLUGIN / "schemas").rglob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "$schema" in document:
            assert path.name.endswith(".schema.json")


def test_skill_icons_and_mcp_bridge_are_deterministic() -> None:
    import base64

    from evidence_lane_plugin.mcp_adapter import server_identity

    plugin_icon = PLUGIN / "assets/evidence-lane-icon.png"
    assert plugin_icon.is_file() and hashlib.sha256(plugin_icon.read_bytes()).hexdigest()
    skill_dirs = [path for path in (PLUGIN / "skills").iterdir() if (path / "SKILL.md").is_file()]
    assert len(skill_dirs) == 24
    for folder in skill_dirs:
        metadata = yaml.safe_load((folder / "agents/openai.yaml").read_text(encoding="utf-8"))["interface"]
        assert "icon_small" not in metadata and "icon_large" not in metadata
        assert metadata["brand_color"] == "#18A9C8"
        if (folder / "assets").exists():
            assert not list((folder / "assets").glob("*"))
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert manifest["interface"]["displayName"] == "Evidence Lane"
    assert manifest["interface"]["logo"] == manifest["interface"]["composerIcon"] == "./assets/evidence-lane-icon.png"
    identity = server_identity()
    assert identity["name"] == "Evidence Lane" and identity["website_url"] == "https://evidencelane.org"
    assert len(identity["icons"]) == 1 and identity["icons"][0].mimeType == "image/png"
    assert base64.b64decode(identity["icons"][0].src.removeprefix("data:image/png;base64,")) == plugin_icon.read_bytes()
    config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["evidence-lane"]
    assert config["required"] is False and config["startup_timeout_sec"] == 120
    assert config["command"] == "node" and config["args"][0] == "./mcp/server.mjs"
    assert (PLUGIN / "scripts/launch_evidence_lane_mcp").read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert (PLUGIN / "scripts/launch_evidence_lane_mcp.cmd").is_file()
    subprocess.run(["node", "--check", str(PLUGIN / "mcp/server.mjs")], check=True)
    subprocess.run(["node", "--check", str(PLUGIN / "hooks/runner.mjs")], check=True)
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event, groups in hooks.items():
        assert len(groups) == 1 and len(groups[0]["hooks"]) == 4
        for stage, command in zip(("VALIDATE", "SEAL", "TRANSPORT", "EMIT"), groups[0]["hooks"], strict=True):
            assert command["command"].endswith(f'hooks/runner.mjs" {event} {stage}')
            assert command["commandWindows"].endswith(f'hooks\\runner.mjs" {event} {stage}')
            assert " python" not in command["command"].lower()
            assert " python" not in command["commandWindows"].lower()
    assert not list((PLUGIN / "mcp/actions").glob("*.sh"))


def test_stable_direct_application_layout_contract() -> None:
    plan = json.loads((PLUGIN / "provisioning/full-bundle-plan.v4.json").read_text(encoding="utf-8"))
    assert plan["entrypoints"] == {
        "runtime_python": "engine/venv/Scripts/python.exe",
        "runtime_pythonw": "engine/venv/Scripts/pythonw.exe",
        "plugin_root": "plugin",
        "studio_launcher_executable": "app/EvidenceLaneStudio.exe",
        "mcp_launcher": "plugin/scripts/run_mcp.py",
        "engine_launcher": "plugin/scripts/run_engine.py",
        "installation_self_test": "plugin/scripts/verify_installed_runtime.py",
        "installation_registration": "plugin/scripts/register_installed_runtime.py",
    }
    layout = json.loads((PLUGIN / "authorities/session_authority/installation-layout.v4.json").read_text(encoding="utf-8"))
    assert layout["folders"]["app"] == "app"
    assert layout["folders"]["persistent_engine"] == "engine"
    assert not any("releases/" in str(value) for value in layout["folders"].values())
    pointer_schema = json.loads((PLUGIN / "schemas/install/first-detection-installation.v4.schema.json").read_text(encoding="utf-8"))
    assert "active_release" not in pointer_schema["properties"]
    assert pointer_schema["properties"]["status"]["const"] == "ACTIVE_INSTALLATION"


def test_all_license_records_and_studio_linkage_resolve_current_owners() -> None:
    shared = json.loads((PLUGIN / "toolchains/shared-toolchain.v4.json").read_text(encoding="utf-8"))
    record_paths = list((PLUGIN / "toolchains/licenses/requirements").glob("*/LICENSE-RECORD.json"))
    assert len(record_paths) == len(shared["entries"]) == 103
    records = [json.loads(path.read_text(encoding="utf-8")) for path in record_paths]
    assert {row["tool"] for row in records} == {row["tool_id"] for row in shared["entries"]}
    assert all(row["tunnel_dependency"] is False and row["installed_by_tunnel"] is False for row in records)
    assert all(row["install_mode"] in {
        "shipped_with_evidence_lane_studio", "packaged_plugin_engine_code",
        "windows_host_capability_probe", "packaged_adapter_external_service_not_redistributed",
    } for row in records)
    index = json.loads((PLUGIN / "toolchains/licenses/retained-install-license-index.v4.json").read_text(encoding="utf-8"))
    assert index["retained_tool_count"] == len(index["records"]) == 103
    assert index["tunnel_dependency"] is False
    assert all(row["tunnel_dependency"] is False for row in index["records"])
    manifest = json.loads((PLUGIN / "studio/studio-linkage.v4.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETE_PREINSTALL_SOURCE_LINKAGE"
    assert manifest["parallel_installer"] is False and manifest["tunnel_dependency"] is False
    assert manifest["native_installation_verified"] is False
    for member in manifest["members"]:
        path = PLUGIN / member["path"]
        assert path.is_file()
        assert path.stat().st_size == member["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == member["sha256"]


def test_user_visible_skill_vocabulary_has_no_retired_names() -> None:
    forbidden = ("evi-", "bigger universe", "Root PV", "State Travel", "Project Recipe", "Brain Scaling")
    for root in (PLUGIN / "skills", ROOT / "apps/evidence-lane-studio/src"):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".ts", ".tsx"}:
                text = path.read_text(encoding="utf-8", errors="strict")
                assert not any(value in text for value in forbidden), (path, forbidden)
