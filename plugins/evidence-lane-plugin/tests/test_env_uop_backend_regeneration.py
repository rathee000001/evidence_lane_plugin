from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.hashing import sha256_file

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_json(database: Path, table: str, column: str) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = connection.execute(
            f'SELECT "{column}" FROM "{table}" ORDER BY sequence DESC LIMIT 1'
        ).fetchone()
        assert row is not None
        return json.loads(str(row[0]))
    finally:
        connection.close()


def test_env_uop_action_planes_are_current_codex_authorities() -> None:
    env = PLUGIN_ROOT / "env" / "env_sqlite.sqlite"
    uop = PLUGIN_ROOT / "uop" / "uop_sqlite.sqlite"
    env_receipt = _latest_json(env, "env_action_plane_build_receipt", "receipt_json")
    uop_receipt = _latest_json(uop, "uop_action_plane_build_receipt", "receipt_json")
    matrix = _json(PLUGIN_ROOT / "toolchains" / "tool-requirement-matrix.v1.json")
    catalog = _json(PLUGIN_ROOT / "schemas" / "public-action-schemas.v001.json")
    env_domain = _json(PLUGIN_ROOT / "toolchains" / "env-domain-catalog.v2.json")
    uop_domain = _json(PLUGIN_ROOT / "toolchains" / "uop-domain-catalog.v2.json")
    lanes = _json(
        PLUGIN_ROOT
        / "schemas"
        / "authorities"
        / "project-sector-lane-surface-registry.v1.json"
    )
    expected_counts = {
        "tools": len(matrix["requirements"]),
        "actions": len(catalog["tools"]),
        "lanes": len(lanes["lanes"]),
    }
    for receipt in (env_receipt, uop_receipt):
        assert receipt["status"] == "PASS"
        assert receipt["direct_rebuild_from_current_codex_registries"] is True
        assert receipt["predecessor_database_copied"] is False
        assert receipt["foreign_surface_row_count"] == 0
        assert {
            key: receipt["counts"][key] for key in expected_counts
        } == expected_counts

    for database, authority in ((env, "env"), (uop, "uop")):
        indexed = _latest_json(
            database,
            "authority_index_refresh_receipt",
            "receipt_json",
        )
        assert indexed["authority_id"] == authority
        assert indexed["source_count"] > 0
        assert indexed["node_count"] > 0

    connection = sqlite3.connect(
        f"file:{env.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert connection.execute(
            "SELECT COUNT(*) FROM env_tool_registry_v17"
        ).fetchone()[0] == len(matrix["requirements"])
        assert connection.execute(
            "SELECT COUNT(*) FROM env_action_binding_v17"
        ).fetchone()[0] == len(catalog["tools"])
        assert connection.execute(
            "SELECT COUNT(*) FROM env_lane_binding_v17"
        ).fetchone()[0] == len(lanes["lanes"])
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_tool_registry_v17 "
                "WHERE tool_id = 'OpenAI_Agents_SDK' "
                "AND agent_authority = 0"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_action_binding_v17 "
                "WHERE lower(ordered_tools_json) LIKE '%openai%agent%sdk%'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT entry_slip, delta_exit_append, exit_slip "
            "FROM env_workflow_event_v17 WHERE event_id = 'STEER_ENTRY'"
        ).fetchone() == (1, 0, 0)
        assert connection.execute(
            "SELECT entry_slip, delta_exit_append, exit_slip "
            "FROM env_workflow_event_v17 WHERE event_id = 'ADAPTIVE_DELTA_EXIT_APPEND'"
        ).fetchone() == (0, 1, 0)
        assert connection.execute(
            "SELECT entry_slip, delta_exit_append, exit_slip "
            "FROM env_workflow_event_v17 WHERE event_id = 'STATE_TRAVEL_EXIT'"
        ).fetchone() == (0, 0, 1)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_behavior_subgraph_v18"
            ).fetchone()[0]
            == 25
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM env_behavior_node_v18").fetchone()[
                0
            ]
            == 275
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM env_behavior_edge_v18").fetchone()[
                0
            ]
            == 256
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_pcm_mba_operator_v18"
            ).fetchone()[0]
            == 110
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_formula_component_v18"
            ).fetchone()[0]
            == 6
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM env_matrix_axis_v18").fetchone()[0]
            == 5
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM env_matrix_cell_v18").fetchone()[0]
            == 5
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM env_mode_cluster_v18").fetchone()[
                0
            ]
            == 14
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_mode_namespace_v18"
            ).fetchone()[0]
            == 17
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_mode_combination_v18"
            ).fetchone()[0]
            == 4
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_source_lane_classification_v18"
            ).fetchone()[0]
            == 18
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM env_lane_formula_v18").fetchone()[
                0
            ]
            == 6
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_operator_registry_v17 "
                "WHERE operator_id GLOB '[0-9]*'"
            ).fetchone()[0]
            == 110
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM env_behavior_node_v18 "
                "WHERE canonical_owner = '' OR execution_disposition = ''"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()

    assert env_domain["schema"] == "evidence-lane.env-domain-catalog.v2"
    assert env_domain["status"] == "PASS_FULL_MMD_BEHAVIOR_BOUND"
    assert env_domain["behavior_source"] == {
        "packaged_path": "toolchains/references/env15_3-working-behavior.mmd",
        "subgraph_count": 25,
        "node_count": 275,
        "edge_count": 256,
        "dot_node_count": 280,
        "dot_edge_count": 278,
        "all_behaviors_require_codex_owner_or_exclusion": True,
    }
    assert env_domain["source_provenance"]["source_is_codex_authority"] is False
    assert env_domain["import_policy"]["chatgpt_host_identity_imported"] is False
    assert env_domain["import_policy"]["behavior_labels_are_commands"] is False

    connection = sqlite3.connect(
        f"file:{uop.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert connection.execute(
            "SELECT COUNT(*) FROM uop_tool_policy_v17"
        ).fetchone()[0] == len(matrix["requirements"])
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM uop_tool_policy_v17 WHERE agent_authority != 0"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM uop_behavior_subgraph_v18"
            ).fetchone()[0]
            == 8
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM uop_behavior_node_v18").fetchone()[
                0
            ]
            == 80
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM uop_behavior_edge_v18").fetchone()[
                0
            ]
            == 57
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM uop_source_record_v18").fetchone()[
                0
            ]
            == 57
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM uop_public_operator_v18"
            ).fetchone()[0]
            == 14
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM uop_route_formula_v18").fetchone()[
                0
            ]
            == 3
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM uop_hil_boundary_v18").fetchone()[
                0
            ]
            == 5
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM uop_delta_auto_admission_v18"
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM uop_delta_auto_admission_v18 "
                "WHERE individual_hil_required != 0 OR project_overlay_effect != 0 "
                "OR accepted_zip_effect != 0 OR reused_at_event != 'NEXT_DELTA_ENTRY'"
            ).fetchone()[0]
            == 0
        )
        assert {
            row[0]
            for row in connection.execute(
                "SELECT state_id FROM uop_delta_auto_admission_v18"
            )
        } == {
            "AUTO_ACCEPTED_DELTA_ROW_WORK",
            "AUTO_ACCEPTED_DELTA_LEARNING",
        }
    finally:
        connection.close()

    assert uop_domain["schema"] == "evidence-lane.uop-domain-catalog.v2"
    assert uop_domain["status"] == "PASS_FULL_MMD_BEHAVIOR_BOUND"
    assert uop_domain["behavior_source"] == {
        "packaged_path": "toolchains/references/uop15-working-behavior.mmd",
        "subgraph_count": 8,
        "node_count": 80,
        "edge_count": 57,
        "dot_node_count": 89,
        "dot_edge_count": 73,
        "all_behaviors_require_codex_owner_or_exclusion": True,
    }
    assert uop_domain["source_provenance"]["source_is_codex_authority"] is False
    assert uop_domain["import_policy"]["chatgpt_host_identity_imported"] is False
    assert (
        uop_domain["import_policy"]["per_delta_auto_admission_has_individual_hil"]
        is False
    )


def test_env_uop_graph_flash_and_packaged_manifests_share_current_bytes() -> None:
    flash = _json(PLUGIN_ROOT / "env" / "SESSION_FLASH_MANIFEST.json")
    matrix = _json(PLUGIN_ROOT / "toolchains" / "tool-requirement-matrix.v1.json")
    catalog = _json(PLUGIN_ROOT / "schemas" / "public-action-schemas.v001.json")
    lanes = _json(
        PLUGIN_ROOT
        / "schemas"
        / "authorities"
        / "project-sector-lane-surface-registry.v1.json"
    )
    assert flash["ai_toolchain"]["tool_count"] == len(matrix["requirements"])
    assert flash["ai_toolchain"]["action_count"] == len(catalog["tools"])
    assert flash["ai_toolchain"]["lane_count"] == len(lanes["lanes"])
    for authority in ("env", "uop"):
        database = PLUGIN_ROOT / authority / f"{authority}_sqlite.sqlite"
        graph = _latest_json(
            database,
            "semantic_graph_render_receipt_v17",
            "graph_pipeline_receipt_json",
        )
        assert graph["status"] == "PASS"
        assert graph["mermaid_exporter"] == "LANGGRAPH_STATEGRAPH"
        assert graph["dot_exporter"] == "PYTHON_GRAPHVIZ"
        assert graph["langgraph_version"] == "1.2.11"
        assert graph["python_graphviz_version"] == "0.21"
        assert graph["node_count"] > 0
        assert graph["edge_count"] > 0

        packaged = _json(PLUGIN_ROOT / authority / "authority-manifest.v1.json")
        members = {row["path"]: row for row in packaged["members"]}
        relative = f"{authority}/{authority}_sqlite.sqlite"
        assert members[relative]["sha256"] == sha256_file(database)
        assert packaged["duplicate_authority_copy_present"] is False
        assert flash["authorities"][authority]["predecessor_database_copied"] is False
        assert flash["authorities"][authority]["sqlite_user_version"] == 17
