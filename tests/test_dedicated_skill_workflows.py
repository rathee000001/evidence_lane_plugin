from __future__ import annotations

import json
import re
from pathlib import Path

from evidence_lane_plugin.graph_pipeline import semantic_graph_from_mermaid
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.lanes import LANE_REGISTRY

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_current_skill_has_one_dedicated_roundtrippable_workflow() -> None:
    skills = _json(PLUGIN / "skills" / "skill-surface-registry.v1.json")
    registry = _json(
        PLUGIN / "sdk" / "workflows" / "skill-workflow-registry.v1.json"
    )
    expected = {str(row["name"]) for row in skills["skills"]}  # type: ignore[index]
    root = PLUGIN / "sdk" / "workflows" / "skills"
    actual = {path.name for path in root.iterdir() if path.is_dir()}

    assert actual == expected
    assert registry["skill_count"] == len(expected)
    assert registry["skill_set_is_derived_from_current_registry"] is True
    assert registry["stale_skill_workflow_retained"] is False
    assert registry["counts_are_current_snapshot_not_ceiling"] is True

    workflow_count = 0
    action_step_count = 0
    for artifact in registry["artifacts"]:  # type: ignore[index]
        skill = str(artifact["skill"])
        workflow_json = PLUGIN / str(artifact["json"])
        workflow_mmd = PLUGIN / str(artifact["mmd"])
        workflow_dot = PLUGIN / str(artifact["dot"])
        model = _json(workflow_json)
        graph = semantic_graph_from_mermaid(
            workflow_mmd.read_text(encoding="utf-8"),
            name="verify_" + re.sub(r"[^A-Za-z0-9_]", "_", skill),
        )
        workflow_count += int(model["workflow_count"])
        action_step_count += int(model["action_step_count"])
        assert sha256_file(workflow_json) == artifact["json_sha256"]
        assert sha256_file(workflow_mmd) == artifact["mmd_sha256"]
        assert sha256_file(workflow_dot) == artifact["dot_sha256"]
        assert len(graph.nodes) == artifact["graph_receipt"]["node_count"]
        assert len(graph.edges) == artifact["graph_receipt"]["edge_count"]
        assert model["internal_sdk_is_execution_owner"] is True
        assert model["env_and_uop_are_distinct"] is True
        assert model["outer_sdk_and_mcp_are_transport_only"] is True
        assert model["every_condition_true_step_runs_or_fails_visible"] is True
        assert model["workflow_or_count_ceiling"] is False
        for step in model["steps"]:
            assert (PLUGIN / step["schema_path"]).is_file()
            assert (PLUGIN / step["sdk_binding"]).is_file()
            assert (PLUGIN / step["mcp_binding"]).is_file()
            assert step["current_route"]["status"] == "CURRENT_ROUTE"
            assert step["hook_contract"][
                "run_only_events_emitted_by_current_action"
            ] is True
            assert step["hook_contract"]["hooks_are_not_action_owners"] is True
            assert step["hil_inference_allowed"] is False

    assert registry["workflow_count"] == workflow_count
    assert registry["action_step_count"] == action_step_count


def test_source_intake_workflow_dispatches_every_registered_project_shape() -> None:
    path = (
        PLUGIN
        / "sdk"
        / "workflows"
        / "skills"
        / "evi-source-intake"
        / "workflow.v1.json"
    )
    model = _json(path)
    dispatch = model["lane_dispatch_contract"]
    lanes = {str(row["lane_id"]): row for row in dispatch["lanes"]}
    assert set(lanes) == set(LANE_REGISTRY)
    assert dispatch["lane_count"] == len(LANE_REGISTRY)
    assert dispatch["lane_count_is_behavior_ceiling"] is False
    assert dispatch["prompt_content_and_exact_overrides_drive_dispatch"] is True
    assert dispatch["every_registered_lane_is_conditionally_reachable"] is True
    assert dispatch["non_code_projects_use_same_recipe_mode_lane_dispatch"] is True
    assert dispatch[
        "github_code_materializes_to_local_code_only_when_authorized"
    ] is True
    assert dispatch["additional_code_sources_are_lane_scoped_study_brains"] is True
    assert dispatch[
        "study_brain_federation_requires_explicit_hash_only_bigger_universe_route"
    ] is True
    code_route = dispatch["code_source_route"]
    assert code_route["owner"] == "evi-source-intake"
    assert code_route["current_route"] == (
        "ONE_PRIMARY_CODE_PROJECT_PLUS_LANE_SCOPED_STUDY_BRAINS"
    )
    for lane_id, lane in LANE_REGISTRY.items():
        row = lanes[lane_id]
        assert row["parser_id"] == lane.parser_id
        assert row["chunker"] == lane.chunker_version
        assert row["fts_table"] == lane.fts_table
        assert row["schema_tables"] == list(lane.schema_contract)
        assert row["mutation_policy"] == lane.mutation_policy

    mmd = path.with_name("workflow.mmd").read_text(encoding="utf-8")
    for lane_id in LANE_REGISTRY:
        assert f"{lane_id}:" in mmd


def test_every_current_lane_and_named_authority_has_one_bound_workflow() -> None:
    registry = _json(
        PLUGIN / "sdk" / "workflows" / "surface-workflow-registry.v1.json"
    )
    lanes = _json(
        PLUGIN
        / "authorities"
        / "project_sectors"
        / "lane-surface-registry.v1.json"
    )
    authorities = _json(
        PLUGIN / "authorities" / "authority-surface-registry.v1.json"
    )
    assert registry["sector_count"] == len(lanes["lanes"])
    assert registry["authority_count"] == len(authorities["authorities"])
    assert len(registry["artifacts"]) == (
        registry["sector_count"] + registry["authority_count"]
    )
    assert registry["counts_are_current_snapshot_not_ceiling"] is True
    assert registry["stale_surface_workflow_retained"] is False

    sector_mmd_hashes: set[str] = set()
    sector_action_counts: set[int] = set()
    for artifact in registry["artifacts"]:
        surface_id = str(artifact["surface_id"])
        model_path = PLUGIN / str(artifact["json"])
        mmd_path = PLUGIN / str(artifact["mmd"])
        dot_path = PLUGIN / str(artifact["dot"])
        model = _json(model_path)
        graph = semantic_graph_from_mermaid(
            mmd_path.read_text(encoding="utf-8"),
            name=(
                "verify_"
                + re.sub(
                    r"[^A-Za-z0-9_]",
                    "_",
                    f"{artifact['surface_kind']}_{surface_id}",
                )
            ),
        )
        assert len(graph.nodes) == artifact["graph_receipt"]["node_count"]
        assert len(graph.edges) == artifact["graph_receipt"]["edge_count"]
        assert sha256_file(model_path) == artifact["json_sha256"]
        assert sha256_file(mmd_path) == artifact["mmd_sha256"]
        assert sha256_file(dot_path) == artifact["dot_sha256"]
        assert model[
            "sqlite_mmd_dot_json_tools_manifest_refresh_together"
        ] is True
        assert model["unchanged_content_addressed_atoms_reused"] is True
        assert model["superseded_route_retained"] is False
        assert model["hil_inference_allowed"] is False
        assert model["workflow_or_count_ceiling"] is False
        baseline = model["project_pv_baseline_connection"]
        assert baseline["connection_only"] is True
        assert baseline["project_registration_or_pv_creation_performed"] is False
        contract = model["surface_execution_contract"]
        phase_ids = {str(row["id"]) for row in contract["phases"]}
        assert {"RESULT_VALIDATE", "ATOMIC_REFRESH"} <= phase_ids

        if artifact["surface_kind"] == "PROJECT_SECTOR":
            sector_mmd_hashes.add(str(artifact["mmd_sha256"]))
            sector_action_counts.add(int(model["public_action_count"]))
            surface_root = (
                PLUGIN / "authorities" / "project_sectors" / surface_id
            )
            tools_paths = [surface_root / "tools.json"]
            lane = LANE_REGISTRY[surface_id]
            assert contract["contract_kind"] == "LANE_SPECIFIC_EXECUTION"
            assert contract["parser_id"] == lane.parser_id
            assert contract["chunker"] == lane.chunker_version
            assert contract["fts_table"] == lane.fts_table
            assert contract["mutation_policy"] == lane.mutation_policy
            assert contract["retrieval_strategy"] == (
                "CONTENTLESS_FTS5_BM25_THEN_BOUNDED_QUERY_TIME_TFIDF"
            )
            assert {
                "SOURCE_CLASSIFY",
                "EXACT_SOURCE_CAS",
                "LANE_PARSE",
                "LANE_CHUNK",
                "STRUCTURED_FACTS",
                "SQLITE_COMMIT",
                "RETRIEVAL_INDEX",
                "TOPOLOGY_GRAPH",
            } <= phase_ids
            assert int(model["public_action_count"]) < 91
            assert int(model["skill_workflow_count"]) < 79
        else:
            surface_root = PLUGIN / "authorities" / surface_id
            tools_paths = list(surface_root.glob("*.tools.json"))
            if not tools_paths and (surface_root / "tools.json").is_file():
                tools_paths = [surface_root / "tools.json"]
        assert tools_paths
        for tools_path in tools_paths:
            tools = _json(tools_path)
            workflow = tools["dedicated_workflow"]
            for key in ("json", "mmd", "dot"):
                assert sha256_file(PLUGIN / workflow[key]) == workflow[
                    f"{key}_sha256"
                ]
        manifest = _json(surface_root / "manifest.v1.json")
        members = {str(row["path"]) for row in manifest["members"]}
        assert {
            str(artifact["json"]),
            str(artifact["mmd"]),
            str(artifact["dot"]),
        }.issubset(members)

    assert len(sector_mmd_hashes) == len(LANE_REGISTRY)
    assert len(sector_action_counts) > 1
