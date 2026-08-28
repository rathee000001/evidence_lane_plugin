from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.first_class_workflows import (
    BIGGER_UNIVERSE_WORKFLOW_SCHEMA,
    BRAIN_SCALING_WORKFLOW_SCHEMA,
    FORMULA_ENGINE_WORKFLOW_SCHEMA,
    FULL_AI_TOOLCHAIN_WORKFLOW_SCHEMA,
    PROJECT_RECIPE_WORKFLOW_SCHEMA,
    BiggerUniverseProjectRequest,
    BrainScalingRequest,
    BrainSlice,
    FormulaEngineRequest,
    FullAIToolchainRequest,
    ProjectRecipeRequest,
    compile_project_recipe,
    register_bigger_universe_project,
    run_brain_scaling,
    run_formula_engine,
    run_full_ai_toolchain,
)
from evidence_lane_plugin.mode_governance import ENV_UOP_EXECUTION_BUDGET_SCHEMA
from evidence_lane_plugin.operating_modes import classify_operating_modes


def test_brain_scaling_is_bounded_deterministic_and_not_training() -> None:
    request = BrainScalingRequest(
        authority_id="canon",
        slices=[
            BrainSlice(
                content_id="b",
                content_sha256="B" * 64,
                token_count=8,
                priority=1,
            ),
            BrainSlice(
                content_id="a",
                content_sha256="A" * 64,
                token_count=5,
                priority=2,
            ),
            BrainSlice(
                content_id="c",
                content_sha256="C" * 64,
                token_count=6,
                priority=0,
            ),
        ],
        token_budget=12,
        max_slices=2,
    )
    first = run_brain_scaling(request)
    second = run_brain_scaling(request)
    assert first == second
    assert first["schema"] == BRAIN_SCALING_WORKFLOW_SCHEMA
    assert [row["content_id"] for row in first["selected_slices"]] == ["a", "c"]
    assert first["used_tokens"] == 11
    assert first["model_training_performed"] is False
    assert first["authority_merged"] is False


@pytest.mark.parametrize(
    ("paths", "outcome", "expected"),
    [
        (["src/app.py"], "Implement the route", "CODE"),
        (["data/table.parquet"], "Validate the table", "DATA"),
        (["brief.docx"], "Refresh the document", "DOCUMENT"),
        (["sources.txt"], "Research prior art", "RESEARCH"),
        (["src/app.py", "data/table.csv"], "Build both", "MIXED"),
    ],
)
def test_project_recipe_is_first_class_and_keeps_chat_lineage(
    paths: list[str], outcome: str, expected: str
) -> None:
    receipt = compile_project_recipe(
        ProjectRecipeRequest(
            project_id="project-a",
            source_paths=paths,
            requested_outcome=outcome,
        )
    )
    assert receipt["schema"] == PROJECT_RECIPE_WORKFLOW_SCHEMA
    assert receipt["project_type"] == expected
    assert "chat_lineage" in receipt["canonical_lanes"]
    assert receipt["recipe_is_mode"] is False
    assert receipt["stored_lane_created"] is False


def test_full_ai_toolchain_is_conditional_and_codex_only() -> None:
    receipt = run_full_ai_toolchain(
        FullAIToolchainRequest(
            lane_id="local_code",
            host_profile="CODEX_DESKTOP",
            available_tools=["Git", "GitPython", "TreeSitter_LanguagePack"],
        )
    )
    assert receipt["schema"] == FULL_AI_TOOLCHAIN_WORKFLOW_SCHEMA
    assert receipt["conditional_dispatch"] is True
    assert receipt["run_everything"] is False
    assert receipt["chatgpt_plane_mixed"] is False
    with pytest.raises((EvidenceLaneError, ValueError)):
        run_full_ai_toolchain(
            FullAIToolchainRequest(
                lane_id="local_code",
                host_profile="CHATGPT_DESKTOP",
            )
        )


def test_bigger_universe_registers_hash_only_mini_brains(tmp_path: Path) -> None:
    receipt = register_bigger_universe_project(
        BiggerUniverseProjectRequest(
            database=tmp_path / "connector-brain.sqlite",
            project_id="project-a",
            project_root_identity_sha256="A" * 64,
            universe_head_sha256="B" * 64,
            pointer_generation=3,
            mini_brains=[
                {
                    "lane_id": "local_code",
                    "database_sha256": "C" * 64,
                    "mmd_sha256": "D" * 64,
                    "dot_sha256": "E" * 64,
                    "tools_sha256": "F" * 64,
                    "content_identity_sha256": "1" * 64,
                }
            ],
        )
    )
    assert receipt["schema"] == BIGGER_UNIVERSE_WORKFLOW_SCHEMA
    assert receipt["workflow_owner"] == "BIGGER_UNIVERSE_FEDERATION"
    assert receipt["per_project_universe_replaced"] is False
    assert receipt["project_truth_merged"] is False
    assert receipt["raw_payload_copied"] is False
    assert receipt["cross_project_edge_created"] is False


def test_formula_engine_compiles_and_routes_outside_mode_ownership() -> None:
    selection = classify_operating_modes(
        "Implement one bounded code correction.",
        explicit_modes=["CD"],
        code_lane="local_code",
    )["mode_governance"]
    contract = selection["contracts"][0]
    operator = contract["operators"][0]
    lanes = list(contract["canonical_lanes"])
    receipt = run_formula_engine(
        FormulaEngineRequest(
            mode_governance=selection,
            execution_budget={
                "schema": ENV_UOP_EXECUTION_BUDGET_SCHEMA,
                "lane_units": {lane: 2 for lane in lanes},
                "tool_invocations": {"repository_read": 2},
                "max_total_lane_units": len(lanes) * 2,
                "max_total_tool_invocations": 2,
            },
            sdk_binding_sha256="A" * 64,
            route={
                "mode_id": contract["mode_id"],
                "operator_id": operator["operator_id"],
                "requested_effect": operator["effect"],
                "lane_id": lanes[0],
                "tool_id": "repository_read",
                "lane_units": 1,
                "tool_invocations": 1,
            },
        )
    )
    assert receipt["schema"] == FORMULA_ENGINE_WORKFLOW_SCHEMA
    assert receipt["mode_is_owner"] is False
    assert receipt["operator_effect_executed"] is True
    assert receipt["route_receipt"]["status"] == "PASS"
    assert receipt["candidate_created"] is False
    assert receipt["hil_inferred"] is False
    assert receipt["pointer_moved"] is False
