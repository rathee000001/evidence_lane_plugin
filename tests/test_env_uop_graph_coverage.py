from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.graph_pipeline import semantic_graph_from_mermaid
from evidence_lane_plugin.hashing import sha256_file

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _connect(authority: str) -> sqlite3.Connection:
    database = PLUGIN / authority / f"{authority}_sqlite.sqlite"
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def test_clean_codex_env_uop_action_planes_cover_current_registries() -> None:
    coverage = json.loads(
        (PLUGIN / "toolchains" / "env-uop-row-to-graph-coverage.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert coverage["status"] == "PASS"
    assert coverage["codex_is_sole_agent"] is True
    assert coverage["chatgpt_surface_rows"] == 0
    assert coverage["foreign_absolute_paths"] == 0
    assert coverage["discussion_or_chatlineage_authority_rows"] == 0
    assert coverage["predecessor_database_copied"] is False

    expected = coverage["counts"]
    with _connect("env") as env:
        assert env.execute("PRAGMA user_version").fetchone()[0] == 17
        assert (
            env.execute("SELECT COUNT(*) FROM env_tool_registry_v17").fetchone()[0]
            == expected["tools"]
        )
        assert (
            env.execute("SELECT COUNT(*) FROM env_action_binding_v17").fetchone()[0]
            == expected["actions"]
        )
        assert (
            env.execute("SELECT COUNT(*) FROM env_lane_binding_v17").fetchone()[0]
            == expected["lanes"]
        )
        assert (
            env.execute("SELECT COUNT(*) FROM env_skill_binding_v17").fetchone()[0]
            == expected["skills"]
        )
        assert (
            env.execute("SELECT COUNT(*) FROM env_hook_binding_v17").fetchone()[0]
            == expected["hooks"]
        )
        assert (
            env.execute("SELECT COUNT(*) FROM codex_host_variant_v17").fetchone()[0]
            == expected["hosts"]
        )
        hosts = {
            str(row[0])
            for row in env.execute("SELECT host_id FROM codex_host_variant_v17")
        }
        assert {"CODEX_DESKTOP_STABLE", "CODEX_DESKTOP_BETA"} <= hosts
        assert not any("CHATGPT" in host for host in hosts)
        events = {
            str(row[0]): (int(row[1]), int(row[2]), int(row[3]))
            for row in env.execute(
                "SELECT event_id,entry_slip,delta_exit_append,exit_slip "
                "FROM env_workflow_event_v17"
            )
        }
        assert events["PROMPT_ENTRY"] == (1, 0, 0)
        assert events["STEER_ENTRY"] == (1, 0, 0)
        assert events["ADAPTIVE_DELTA_EXIT_APPEND"] == (0, 1, 0)
        assert events["GOAL_OPTION_2_EXIT"] == (0, 0, 1)
        assert events["STATE_TRAVEL_EXIT"] == (0, 0, 1)
        action_rows = {
            str(row[0]): (json.loads(str(row[1])), json.loads(str(row[2])), str(row[3]))
            for row in env.execute(
                "SELECT action_name,workflow_classes_json,ordered_tools_json,entry_event "
                "FROM env_action_binding_v17"
            )
        }
        assert action_rows["pv_fuse"][0] == ["GOVERNANCE"]
        assert action_rows["search"][0] == ["GOVERNANCE", "RETRIEVAL"]
        assert action_rows["adaptive_delta_exit"][2] == "ADAPTIVE_DELTA_EXIT_APPEND"
        assert action_rows["pv_plan_steer_delta"][2] == "STEER_ENTRY"
        assert "OpenAI_Agents_SDK" not in action_rows["pv_fuse"][1]
        forbidden_tables = {
            "active_source_stack",
            "artifact_raw_registry",
            "artifact_chunk_fts",
            "source_document",
            "delta_ledger",
            "env15_engulfed_record",
        }
        actual_tables = {
            str(row[0])
            for row in env.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not forbidden_tables & actual_tables

    with _connect("uop") as uop:
        assert uop.execute("PRAGMA user_version").fetchone()[0] == 17
        assert (
            uop.execute("SELECT COUNT(*) FROM uop_action_policy_v17").fetchone()[0]
            == expected["actions"]
        )
        assert (
            uop.execute("SELECT COUNT(*) FROM uop_tool_policy_v17").fetchone()[0]
            == expected["tools"]
        )
        assert (
            uop.execute("SELECT COUNT(*) FROM uop_host_policy_v17").fetchone()[0]
            == expected["hosts"]
        )
        assert (
            uop.execute("SELECT COUNT(*) FROM uop_governance_operator_v17").fetchone()[
                0
            ]
            >= 10
        )
        assert (
            uop.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='uop_source_registry'"
            ).fetchone()[0]
            == 0
        )

    for authority in ("env", "uop"):
        output = coverage["final_graphs"][authority]
        mmd_path = PLUGIN / authority / f"{authority}_mmd.mmd"
        dot_path = PLUGIN / authority / f"{authority}_mmd.dot"
        graph = semantic_graph_from_mermaid(
            mmd_path.read_text(encoding="utf-8"),
            name=f"verify_{authority}_codex_action_plane",
        )
        assert len(graph.nodes) == output["node_count"]
        assert len(graph.edges) == output["edge_count"]
        assert len(graph.groups) == output["group_count"]
        assert sha256_file(mmd_path) == output["mmd_sha256"]
        assert sha256_file(dot_path) == output["dot_sha256"]

    tools = coverage["graph_tool_execution_evidence"]
    assert tools["LangGraph_Mermaid_engine"]["state"] == "EXECUTED"
    assert tools["Python_Graphviz_DOT_engine"]["state"] == "EXECUTED"
    assert tools["rustworkx"]["state"] == "EXECUTED"
