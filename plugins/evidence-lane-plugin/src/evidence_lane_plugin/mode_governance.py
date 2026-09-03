"""ENV/UOP-backed operator contracts for selected operating modes.

The projection below is a package-local executable contract derived from sealed
ENV15 tables.  It intentionally distinguishes exact ENV rows from derived mode
semantics and never treats mode selection as HIL approval or lifecycle mutation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from .errors import require
from .flash_authority import ENV_MMD_SHA256, UOP_MMD_SHA256
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .next_actions import PROJECT_HIL_DECISION_TOKENS
from .package_root import resolve_plugin_root
from .redaction import contains_secret

ENV15_ENV_SQLITE_SHA256 = (
    "9E055B4B3029F17EA7448C9A38483466FD436139ACD2AE16FF732BCCFEAF15C4"
)
ENV15_UOP_SQLITE_SHA256 = (
    "A1E31FE96A4537C5AAB4ADE82D9D138CF360D4DD0B863A4D21E51ACD83B4D3CD"
)
ENV15_MODE_POLICY_PROJECTION_SHA256 = (
    "BD9799FCDB03CD8D949A42602B269AC9AEEC83697434E8D61D4D9EE61F73414D"
)
ENV_UOP_AUTHORITY_BOUNDARY_SCHEMA = "evidence-lane.env-uop-authority-boundary.v1"
ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA = (
    "evidence-lane.external-host-secret-reference.v1"
)
ENV_UOP_EXTERNAL_SECRET_RECEIPT_SCHEMA = "evidence-lane.external-host-secret-receipt.v1"
ENV_UOP_OPERATOR_EFFECT_RECEIPT_SCHEMA = (
    "evidence-lane.env-uop-operator-effect-receipt.v1"
)
ENV_UOP_EXECUTION_BUDGET_SCHEMA = "evidence-lane.env-uop-execution-budget.v1"
ENV_UOP_COMPILED_FORMULA_SCHEMA = "evidence-lane.env-uop-compiled-formula.v1"
ENV_UOP_OPERATOR_ROUTE_RECEIPT_SCHEMA = (
    "evidence-lane.env-uop-operator-route-receipt.v1"
)

_ENV_UOP_EXTERNAL_SECRET_TARGET = "EXTERNAL_HOST_SECRET_PROVIDER"
_ENV_UOP_FORBIDDEN_SECRET_STORES = frozenset(
    {"PROMPT", "SQLITE", "CHAT_LINEAGE", "LINEAGE", "ASSET", "TEST"}
)
_ENV_UOP_SECRET_OUTCOMES = frozenset(
    {"RESOLVED", "NOT_FOUND", "DENIED", "ERROR", "NOT_REQUIRED"}
)
_ENV_UOP_SAFE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$")
_ENV_UOP_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_ENV_UOP_SAFE_ROUTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ENV_UOP_MAX_LANES = 32
_ENV_UOP_MAX_TOOLS = 64
_ENV_UOP_MAX_UNITS_PER_ROUTE = 1_024
_ENV_UOP_MAX_TOTAL_UNITS = 8_192
_ENV_UOP_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|password|private[_-]?key|client[_-]?secret|"
    r"secret[_-]?value|credential[_-]?value)(?:$|[_-])"
)

_ENV_UOP_ASSET_ROOT = resolve_plugin_root(__file__)
_ENV_SQLITE_PATH = _ENV_UOP_ASSET_ROOT / "env" / "env_sqlite.sqlite"
_UOP_SQLITE_PATH = _ENV_UOP_ASSET_ROOT / "uop" / "uop_sqlite.sqlite"
_ENV_MMD_PATH = _ENV_UOP_ASSET_ROOT / "env" / "env_mmd.mmd"
_UOP_MMD_PATH = _ENV_UOP_ASSET_ROOT / "uop" / "uop_mmd.mmd"


def _read_locked_sqlite_rows(
    path: Path,
    *,
    tables: tuple[str, ...],
) -> dict[str, list[dict[str, Any]]]:
    """Read one immutable ENV/UOP authority without creating SQLite sidecars."""

    require(
        path.is_file(),
        "ENV_UOP_SQLITE_AUTHORITY_MISSING",
        "The locked ENV/UOP SQLite authority is missing.",
        status="MISMATCH",
        path=str(path),
    )
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        require(
            integrity == ["ok"],
            "ENV_UOP_SQLITE_INTEGRITY_FAILED",
            "The locked ENV/UOP SQLite authority failed integrity validation.",
            status="FAIL",
            path=str(path),
            integrity=integrity,
        )
        available = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = sorted(set(tables) - available)
        require(
            not missing,
            "ENV_UOP_RUNTIME_TABLE_MISSING",
            "The locked ENV/UOP runtime is missing an executable table.",
            status="MISMATCH",
            path=str(path),
            missing_tables=missing,
        )
        return {
            table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
            for table in tables
        }
    finally:
        connection.close()


_MODE_ALIAS_TO_CODEX_MODE = {
    "D": "ANALYSIS",
    "AL": "ANALYSIS",
    "PL": "PLAN",
    "CD": "CODE",
    "XL": "DATA",
    "PPT": "MEDIA",
    "DOC": "DOCUMENT",
    "JD": "ANALYSIS",
    "PB": "DATA",
    "RS": "RESEARCH",
    "X": "CUSTOM",
    "OP": "DOCUMENT",
    "VAL": "ANALYSIS",
    "ENG": "PROJECT_ENGULF",
    "CE": "RUNTIME",
    "RCV": "RUNTIME",
}


@lru_cache(maxsize=1)
def load_env_uop_runtime_authority() -> dict[str, Any]:
    """Load the clean Codex-native ENV/UOP action planes read-only."""

    env_sha256 = sha256_file(_ENV_SQLITE_PATH)
    uop_sha256 = sha256_file(_UOP_SQLITE_PATH)
    env_mmd_sha256 = sha256_file(_ENV_MMD_PATH)
    uop_mmd_sha256 = sha256_file(_UOP_MMD_PATH)
    require(
        env_sha256 == ENV15_ENV_SQLITE_SHA256
        and uop_sha256 == ENV15_UOP_SQLITE_SHA256
        and env_mmd_sha256 == ENV_MMD_SHA256
        and uop_mmd_sha256 == UOP_MMD_SHA256,
        "ENV_UOP_RUNTIME_AUTHORITY_HASH_MISMATCH",
        "The clean Codex ENV/UOP authority no longer matches its lock.",
        status="MISMATCH",
    )
    env = _read_locked_sqlite_rows(
        _ENV_SQLITE_PATH,
        tables=(
            "env_authority_meta",
            "codex_host_variant_v17",
            "env_workflow_event_v17",
            "env_mode_registry_v17",
            "env_project_class_policy_v17",
            "env_behavior_subgraph_v18",
            "env_behavior_node_v18",
            "env_behavior_edge_v18",
            "env_formula_component_v18",
            "env_matrix_axis_v18",
            "env_matrix_cell_v18",
            "env_mode_cluster_v18",
            "env_mode_namespace_v18",
            "env_mode_combination_v18",
            "env_source_lane_classification_v18",
            "env_lane_formula_v18",
            "env_pcm_mba_operator_v18",
            "env_formula_registry_v17",
            "env_operator_registry_v17",
            "env_tool_registry_v17",
            "env_action_binding_v17",
            "env_lane_binding_v17",
            "env_action_plane_build_receipt",
            "semantic_graph_render_receipt_v17",
        ),
    )
    uop = _read_locked_sqlite_rows(
        _UOP_SQLITE_PATH,
        tables=(
            "uop_authority_meta",
            "uop_behavior_subgraph_v18",
            "uop_behavior_node_v18",
            "uop_behavior_edge_v18",
            "uop_source_record_v18",
            "uop_public_operator_v18",
            "uop_route_formula_v18",
            "uop_hil_boundary_v18",
            "uop_delta_auto_admission_v18",
            "uop_governance_operator_v17",
            "uop_project_class_hil_policy_v17",
            "uop_workflow_gate_v17",
            "uop_action_policy_v17",
            "uop_tool_policy_v17",
            "uop_host_policy_v17",
            "uop_fallback_policy_v17",
            "uop_action_plane_build_receipt",
            "semantic_graph_render_receipt_v17",
        ),
    )
    env_meta = {str(row["key"]): str(row["value"]) for row in env["env_authority_meta"]}
    uop_meta = {str(row["key"]): str(row["value"]) for row in uop["uop_authority_meta"]}
    host_ids = {str(row["host_id"]) for row in env["codex_host_variant_v17"]}
    require(
        env_meta.get("host_plane") == "CODEX_ONLY"
        and env_meta.get("codex_is_sole_agent") == "true"
        and env_meta.get("foreign_surface_payload_allowed") == "false"
        and uop_meta.get("governance_only") == "true"
        and uop_meta.get("can_override_env") == "false"
        and uop_meta.get("can_override_project") == "false"
        and {"CODEX_DESKTOP_STABLE", "CODEX_DESKTOP_BETA"} <= host_ids
        and not any("CHATGPT" in value for value in host_ids),
        "ENV_UOP_EXECUTABLE_CONTROL_PLANE_INVALID",
        "The clean Codex action-plane rows violate host or authority boundaries.",
        status="MISMATCH",
    )
    clean_modes = {str(row["mode_id"]): row for row in env["env_mode_registry_v17"]}
    mode_namespace = {
        str(row["mode_prefix"]): {
            **row,
            "project_classes_json": clean_modes[str(row["codex_mode_id"])][
                "project_classes_json"
            ],
            "ci_applicable": clean_modes[str(row["codex_mode_id"])]["ci_applicable"],
            "hil_policy": clean_modes[str(row["codex_mode_id"])]["hil_policy"],
        }
        for row in env["env_mode_namespace_v18"]
    }
    require(
        all(
            str(row["codex_mode_id"]) in clean_modes for row in mode_namespace.values()
        ),
        "ENV_UOP_MODE_CROSSWALK_INVALID",
        "Every adapted ENV mode namespace must map to one current Codex mode.",
        status="MISMATCH",
    )
    formulas = {str(row["formula_id"]): row for row in env["env_formula_registry_v17"]}
    formula_for_alias = {
        "CD": "CODE_BOOLEAN_GATE",
        "PB": "CODE_BOOLEAN_GATE",
        "ENG": "SOURCE_INTEGRITY",
        "CE": "GOAL_OPTION_2_READY",
        "RCV": "STATE_TRAVEL_READY",
    }
    formula_registry = {
        str(row["lane_id"]): {
            "lane_id": row["lane_id"],
            "lane_name": row["lane_name"],
            "formula_rule": row["formula_rule"],
            "ci_cd_applicable": row["ci_cd_applicable"],
            "validation_loop": row["validation_loop"],
            "codex_mode_id": row["codex_mode_id"],
            "source": "ENV15_3_ADAPTED_LANE_FORMULA",
        }
        for row in env["env_lane_formula_v18"]
    }
    for alias in mode_namespace:
        lane_id = f"LANE_{alias}"
        adapted = dict(formula_registry.get(lane_id) or {})
        selected = formulas[formula_for_alias.get(alias, "DELTA_COMPLETION")]
        formula_registry[lane_id] = {
            **adapted,
            "lane_id": lane_id,
            "lane_name": mode_namespace[alias]["mode_name"],
            "formula_rule": selected["boolean_expression"],
            "source_route_formula": adapted.get("formula_rule"),
            "ci_cd_applicable": int(selected["formula_id"] == "CODE_BOOLEAN_GATE"),
            "validation_loop": selected["rerun_scope"],
            "formula_id": selected["formula_id"],
        }
    operators = {
        str(row["operator_id"]): {
            "operator_id": row["operator_id"],
            "engine_group": row["engine_group"],
            "class_layer": row["class_layer"],
            "chapter": row["chapter"],
            "route_function": row["route_function"],
            "fire_trigger": row["fire_trigger"],
            "output_effect": row["output_effect"],
            "activation_state": row["activation_state"],
            "source_row_sha256": row["source_row_sha256"],
        }
        for row in env["env_pcm_mba_operator_v18"]
    }
    operator_activation = {
        str(row["operator_id"]): {
            "operator_id": row["operator_id"],
            "task_trigger": row["task_trigger"],
            "formula_depth": row["formula_depth"],
            "activation_state": row["formula_activation_state"],
        }
        for row in env["env_pcm_mba_operator_v18"]
    }
    lane_bindings = {
        str(row["lane_id"]): {
            **row,
            "action_classes_json": "[]",
        }
        for row in env["env_lane_binding_v17"]
    }
    env_receipt = env["env_action_plane_build_receipt"][-1]
    uop_root = {
        "uop_id": "UOP15_CODEX_GOVERNANCE",
        "governance_only": 1,
        "private_payload_present": 0,
        "can_override_env": 0,
        "can_override_project": 0,
        "status": "ACTIVE",
    }
    runtime_core = {
        "schema": "evidence-lane.env-uop-runtime-authority.v2",
        "status": "PASS",
        "authority_mode": "READ_ONLY_CLEAN_CODEX_ACTION_PLANE",
        "env_sqlite_sha256": env_sha256,
        "uop_sqlite_sha256": uop_sha256,
        "env_mmd_sha256": env_mmd_sha256,
        "uop_mmd_sha256": uop_mmd_sha256,
        "env_root": {
            "authority": "ENV15",
            "env_write_lock": 1,
            "uop_required": 1,
            "env_default_open_mode": "mode=ro&immutable=1",
        },
        "uop_root": uop_root,
        "mode_namespace": mode_namespace,
        "lane_activation_rules": env["env_source_lane_classification_v18"],
        "recursive_policies": {},
        "formula_registry": formula_registry,
        "formula_components": {
            str(row["symbol"]): row for row in env["env_formula_component_v18"]
        },
        "lifecycle_formulas": formulas,
        "matrix_axes": env["env_matrix_axis_v18"],
        "matrix_cells": env["env_matrix_cell_v18"],
        "mode_clusters": env["env_mode_cluster_v18"],
        "mode_combinations": env["env_mode_combination_v18"],
        "operators": operators,
        "operator_activation": operator_activation,
        "complete_working_behavior": {
            "subgraphs": env["env_behavior_subgraph_v18"],
            "nodes": env["env_behavior_node_v18"],
            "edges": env["env_behavior_edge_v18"],
        },
        "no_autonomous_cicd_gate": {
            "rule": "NO_AUTONOMOUS_FLASH_FUSE_DEPLOY",
            "active": 1,
        },
        "uop_mode_routes": uop["uop_route_formula_v18"],
        "uop_public_operators": uop["uop_public_operator_v18"],
        "uop_governance_operators": uop["uop_governance_operator_v17"],
        "uop_spatial_operators": [
            json.loads(str(row["record_json"]))
            for row in uop["uop_source_record_v18"]
            if str(row["source_table"]) == "uop_spatial_operator"
        ],
        "uop_human_gates": uop["uop_hil_boundary_v18"],
        "uop_disclosure_boundaries": [
            json.loads(str(row["record_json"]))
            for row in uop["uop_source_record_v18"]
            if str(row["source_table"])
            in {"uop_disclosure_boundary_operator", "uop_public_boundary_v15"}
        ],
        "uop_delta_auto_admission": uop["uop_delta_auto_admission_v18"],
        "complete_uop_working_behavior": {
            "subgraphs": uop["uop_behavior_subgraph_v18"],
            "nodes": uop["uop_behavior_node_v18"],
            "edges": uop["uop_behavior_edge_v18"],
            "source_records": uop["uop_source_record_v18"],
        },
        "ai_toolchain_registry": {
            str(row["tool_id"]): row for row in env["env_tool_registry_v17"]
        },
        "ai_toolchain_action_bindings": {
            str(row["action_name"]): row for row in env["env_action_binding_v17"]
        },
        "ai_toolchain_lane_bindings": lane_bindings,
        "ai_toolchain_host_bindings": {
            str(row["host_id"]): row for row in env["codex_host_variant_v17"]
        },
        "ai_toolchain_sync_receipt": env_receipt,
        "uop_toolchain_policies": {
            str(row["workflow_class"]): row for row in uop["uop_fallback_policy_v17"]
        },
        "uop_toolchain_host_policies": {
            str(row["host_id"]): row for row in uop["uop_host_policy_v17"]
        },
        "chatgpt_toolchain_plane_mixed": False,
        "codex_is_sole_agent": True,
        "env_semantic_graph_receipt": env["semantic_graph_render_receipt_v17"][-1],
        "uop_semantic_graph_receipt": uop["semantic_graph_render_receipt_v17"][-1],
        "sqlite_runtime_behavior": True,
        "mmd_traversal_behavior": True,
        "python_projection_is_authority": False,
    }
    return {
        **runtime_core,
        "runtime_authority_sha256": sha256_bytes(canonical_json_bytes(runtime_core)),
    }


def env_uop_authority_boundary() -> dict[str, Any]:
    """Return the immutable ENV/UOP ownership and secret-handling contract."""

    core = {
        "schema": ENV_UOP_AUTHORITY_BOUNDARY_SCHEMA,
        "env_sqlite_sha256": ENV15_ENV_SQLITE_SHA256,
        "uop_sqlite_sha256": ENV15_UOP_SQLITE_SHA256,
        "mode_policy_projection_sha256": ENV15_MODE_POLICY_PROJECTION_SHA256,
        "ownership": {
            "source_authority": "LOCKED_ENV15_UOP15",
            "operator_authority": "ENV_UOP_OPERATOR_RUNTIME",
            "mutation_authority": "EXPLICIT_USER_OR_AUTHORIZED_MAINTAINER_ONLY",
            "project_truth_authority": "NONE",
        },
        "operator_effect_policy": {
            "scope": "DECLARED_EFFECT_ONLY",
            "cross_authority_mutation_allowed": False,
            "hil_effect": "NONE",
            "pointer_effect": "NONE",
        },
        "credential_policy": {
            "source": f"{_ENV_UOP_EXTERNAL_SECRET_TARGET}_ONLY",
            "transport": "REFERENCE_ONLY",
            "value_logging_allowed": False,
            "forbidden_storage": sorted(_ENV_UOP_FORBIDDEN_SECRET_STORES),
        },
    }
    return {**core, "boundary_sha256": sha256_bytes(canonical_json_bytes(core))}


def validate_env_uop_external_secret_reference(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a host-owned secret handle and return only a redacted receipt."""

    exact = dict(value)
    require(
        exact.get("schema") == ENV_UOP_EXTERNAL_SECRET_REFERENCE_SCHEMA,
        "ENV_UOP_SECRET_REFERENCE_SCHEMA_INVALID",
        "ENV/UOP credentials require the external host secret-reference schema.",
        status="BLOCKED",
    )
    forbidden_keys = sorted(
        str(key) for key in exact if _ENV_UOP_SECRET_KEY_RE.search(str(key))
    )
    require(
        not forbidden_keys and not contains_secret(exact),
        "ENV_UOP_SECRET_VALUE_FORBIDDEN",
        "Credential values cannot enter ENV/UOP prompts, receipts, or replay state.",
        status="BLOCKED",
        forbidden_keys=forbidden_keys,
    )
    storage_target = str(exact.get("storage_target") or "").strip().upper()
    require(
        storage_target not in _ENV_UOP_FORBIDDEN_SECRET_STORES
        and storage_target == _ENV_UOP_EXTERNAL_SECRET_TARGET,
        "ENV_UOP_SECRET_STORAGE_FORBIDDEN",
        "ENV/UOP credentials may be resolved only by an external host secret provider.",
        status="BLOCKED",
        storage_target=storage_target or None,
        forbidden_storage=sorted(_ENV_UOP_FORBIDDEN_SECRET_STORES),
    )
    provider_id = str(exact.get("provider_id") or "").strip()
    reference_id = str(exact.get("reference_id") or "").strip()
    outcome = str(exact.get("outcome") or "").strip().upper()
    require(
        bool(_ENV_UOP_SAFE_REFERENCE_RE.fullmatch(provider_id))
        and bool(_ENV_UOP_SAFE_REFERENCE_RE.fullmatch(reference_id)),
        "ENV_UOP_SECRET_REFERENCE_INVALID",
        "The external provider and reference identifiers must be bounded opaque handles.",
        status="BLOCKED",
    )
    require(
        outcome in _ENV_UOP_SECRET_OUTCOMES,
        "ENV_UOP_SECRET_OUTCOME_INVALID",
        "The external host secret-provider outcome is not recognized.",
        status="BLOCKED",
        outcome=outcome or None,
    )
    reference_sha256 = sha256_bytes(reference_id.encode("utf-8"))
    redacted_reference = (
        f"{reference_id[:2]}...{reference_id[-2:]}"
        if len(reference_id) > 4
        else "[REDACTED_REFERENCE]"
    )
    core = {
        "schema": ENV_UOP_EXTERNAL_SECRET_RECEIPT_SCHEMA,
        "status": "PASS",
        "provider_id": provider_id,
        "storage_target": _ENV_UOP_EXTERNAL_SECRET_TARGET,
        "reference_redacted": redacted_reference,
        "reference_sha256": reference_sha256,
        "outcome": outcome,
        "value_received": False,
        "value_logged": False,
        "prompt_storage": False,
        "sqlite_storage": False,
        "lineage_storage": False,
        "asset_storage": False,
        "test_storage": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def bind_env_uop_operator_effect(
    operator_id: int,
    *,
    requested_effect: str,
    credential_reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one operator to its declared effect without executing the effect."""

    runtime = load_env_uop_runtime_authority()
    operator = _OPERATORS.get(operator_id)
    sqlite_operator = cast(
        dict[str, Any] | None,
        cast(dict[str, Any], runtime["operators"]).get(str(operator_id)),
    )
    require(
        operator is not None
        and sqlite_operator is not None
        and str(sqlite_operator.get("engine_group"))
        == str(cast(dict[str, Any], operator).get("family"))
        and str(sqlite_operator.get("chapter"))
        == str(cast(dict[str, Any], operator).get("chapter"))
        and str(sqlite_operator.get("activation_state")) == "BASELINE_REGISTERED",
        "ENV_UOP_OPERATOR_UNKNOWN",
        "The requested ENV/UOP operator is absent or differs from locked SQLite.",
        status="BLOCKED",
        operator_id=operator_id,
    )
    sqlite_operator = cast(dict[str, Any], sqlite_operator)
    declared_effect = str(cast(dict[str, Any], operator)["effect"])
    require(
        requested_effect == declared_effect,
        "ENV_UOP_OPERATOR_EFFECT_MISMATCH",
        "An ENV/UOP operator may perform only its declared effect.",
        status="BLOCKED",
        operator_id=operator_id,
        requested_effect=requested_effect,
        declared_effect=declared_effect,
    )
    credential_receipt = (
        validate_env_uop_external_secret_reference(credential_reference)
        if credential_reference is not None
        else None
    )
    boundary = env_uop_authority_boundary()
    core = {
        "schema": ENV_UOP_OPERATOR_EFFECT_RECEIPT_SCHEMA,
        "status": "PASS",
        "operator_id": operator_id,
        "operator_family": cast(dict[str, Any], operator)["family"],
        "declared_effect": declared_effect,
        "declared_effect_sha256": sha256_bytes(declared_effect.encode("utf-8")),
        "sqlite_runtime": {
            "chapter": sqlite_operator["chapter"],
            "route_function": sqlite_operator["route_function"],
            "fire_trigger": sqlite_operator["fire_trigger"],
            "output_effect": sqlite_operator["output_effect"],
            "activation_state": sqlite_operator["activation_state"],
            "runtime_authority_sha256": runtime["runtime_authority_sha256"],
        },
        "authority_boundary_sha256": boundary["boundary_sha256"],
        "credential_receipt": credential_receipt,
        "effect_executed": False,
        "cross_authority_mutation": False,
        "hil_effect": "NONE",
        "pointer_effect": "NONE",
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _env_uop_budget_units(value: Any, *, field: str) -> int:
    require(
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= _ENV_UOP_MAX_UNITS_PER_ROUTE,
        "ENV_UOP_EXECUTION_BUDGET_INVALID",
        "Every ENV/UOP lane and tool budget must be a positive bounded integer.",
        status="BLOCKED",
        field=field,
        max_units=_ENV_UOP_MAX_UNITS_PER_ROUTE,
    )
    return int(value)


def _normalize_env_uop_execution_budget(
    value: Mapping[str, Any],
    *,
    canonical_lanes: list[str],
) -> dict[str, Any]:
    """Validate exact per-lane and per-tool budgets for one compiled formula."""

    exact = dict(value)
    require(
        set(exact)
        == {
            "schema",
            "lane_units",
            "tool_invocations",
            "max_total_lane_units",
            "max_total_tool_invocations",
        }
        and exact.get("schema") == ENV_UOP_EXECUTION_BUDGET_SCHEMA,
        "ENV_UOP_EXECUTION_BUDGET_SHAPE_INVALID",
        "An ENV/UOP execution budget requires the exact versioned fields.",
        status="BLOCKED",
    )
    require(
        not contains_secret(exact),
        "ENV_UOP_EXECUTION_BUDGET_SECRET_FORBIDDEN",
        "Secret material cannot enter ENV/UOP execution budgets.",
        status="BLOCKED",
    )
    raw_lanes = exact.get("lane_units")
    raw_tools = exact.get("tool_invocations")
    require(
        isinstance(raw_lanes, Mapping)
        and isinstance(raw_tools, Mapping)
        and bool(raw_tools),
        "ENV_UOP_EXECUTION_BUDGET_MAP_REQUIRED",
        "ENV/UOP execution requires explicit lane and tool budget maps.",
        status="BLOCKED",
    )
    raw_lanes = cast(Mapping[Any, Any], raw_lanes)
    raw_tools = cast(Mapping[Any, Any], raw_tools)
    lanes = [str(item) for item in canonical_lanes]
    require(
        bool(lanes)
        and len(lanes) == len(set(lanes))
        and len(lanes) <= _ENV_UOP_MAX_LANES
        and set(raw_lanes) == set(lanes),
        "ENV_UOP_LANE_BUDGET_MISMATCH",
        "Every selected canonical lane requires exactly one explicit budget.",
        status="BLOCKED",
        required_lanes=lanes,
        supplied_lanes=sorted(str(item) for item in raw_lanes),
    )
    require(
        len(raw_tools) <= _ENV_UOP_MAX_TOOLS,
        "ENV_UOP_TOOL_BUDGET_LIMIT_EXCEEDED",
        "The ENV/UOP tool budget exceeds the bounded tool-route count.",
        status="BLOCKED",
        max_tools=_ENV_UOP_MAX_TOOLS,
    )
    lane_units = {
        lane: _env_uop_budget_units(raw_lanes[lane], field=f"lane_units.{lane}")
        for lane in lanes
    }
    tool_invocations: dict[str, int] = {}
    for raw_tool, raw_units in sorted(raw_tools.items(), key=lambda item: str(item[0])):
        tool_id = str(raw_tool)
        require(
            bool(_ENV_UOP_SAFE_ROUTE_RE.fullmatch(tool_id)),
            "ENV_UOP_TOOL_ROUTE_INVALID",
            "ENV/UOP tool routes require bounded public-safe identifiers.",
            status="BLOCKED",
            tool_id=tool_id,
        )
        tool_invocations[tool_id] = _env_uop_budget_units(
            raw_units,
            field=f"tool_invocations.{tool_id}",
        )
    max_lane = _env_uop_budget_units(
        exact.get("max_total_lane_units"),
        field="max_total_lane_units",
    )
    max_tool = _env_uop_budget_units(
        exact.get("max_total_tool_invocations"),
        field="max_total_tool_invocations",
    )
    lane_total = sum(lane_units.values())
    tool_total = sum(tool_invocations.values())
    require(
        lane_total == max_lane
        and tool_total == max_tool
        and lane_total <= _ENV_UOP_MAX_TOTAL_UNITS
        and tool_total <= _ENV_UOP_MAX_TOTAL_UNITS,
        "ENV_UOP_EXECUTION_BUDGET_TOTAL_MISMATCH",
        "Aggregate ENV/UOP budgets must equal their explicit route allocations.",
        status="BLOCKED",
        lane_total=lane_total,
        max_total_lane_units=max_lane,
        tool_total=tool_total,
        max_total_tool_invocations=max_tool,
        max_total_units=_ENV_UOP_MAX_TOTAL_UNITS,
    )
    core = {
        "schema": ENV_UOP_EXECUTION_BUDGET_SCHEMA,
        "lane_units": lane_units,
        "tool_invocations": tool_invocations,
        "max_total_lane_units": max_lane,
        "max_total_tool_invocations": max_tool,
    }
    return {**core, "budget_sha256": sha256_bytes(canonical_json_bytes(core))}


def compile_env_uop_formula(
    mode_governance: Mapping[str, Any],
    execution_budget: Mapping[str, Any],
    *,
    sdk_binding_sha256: str,
) -> dict[str, Any]:
    """Compile selected ENV/UOP formulas into a bounded provider route plan."""

    require(
        bool(_ENV_UOP_SHA256_RE.fullmatch(str(sdk_binding_sha256))),
        "ENV_UOP_SDK_BINDING_INVALID",
        "ENV/UOP compilation requires the exact SDK binding hash.",
        status="MISMATCH",
    )
    selection = validate_mode_governance_selection(dict(mode_governance))
    canonical_lanes = list(
        dict.fromkeys(
            str(lane)
            for contract in cast(list[dict[str, Any]], selection["contracts"])
            for lane in cast(list[Any], contract.get("canonical_lanes") or [])
        )
    )
    budget = _normalize_env_uop_execution_budget(
        execution_budget,
        canonical_lanes=canonical_lanes,
    )
    compiled_contracts: list[dict[str, Any]] = []
    for contract in cast(list[dict[str, Any]], selection["contracts"]):
        compiled_operators = []
        for operator in cast(list[dict[str, Any]], contract["operators"]):
            effect_receipt = bind_env_uop_operator_effect(
                int(operator["operator_id"]),
                requested_effect=str(operator["effect"]),
            )
            compiled_operators.append(
                {
                    "operator_id": int(operator["operator_id"]),
                    "family": str(operator["family"]),
                    "chapter": str(operator["chapter"]),
                    "declared_effect": str(operator["effect"]),
                    "declared_effect_sha256": str(operator["declared_effect_sha256"]),
                    "sqlite_runtime": dict(operator["sqlite_runtime"]),
                    "effect_receipt_sha256": effect_receipt["receipt_sha256"],
                }
            )
        compiled_contracts.append(
            {
                "mode_id": str(contract["mode_id"]),
                "mode_name": str(contract["mode_name"]),
                "canonical_lanes": [str(item) for item in contract["canonical_lanes"]],
                "formula_rule": str(contract["formula"]["rule"]),
                "formula_authority": str(contract["formula"]["authority"]),
                "operator_receipt_sha256": str(contract["operator_receipt_sha256"]),
                "operators": compiled_operators,
            }
        )
    core = {
        "schema": ENV_UOP_COMPILED_FORMULA_SCHEMA,
        "status": "PASS",
        "sdk_binding_sha256": str(sdk_binding_sha256),
        "mode_selection_sha256": sha256_bytes(canonical_json_bytes(selection)),
        "combined_operator_receipt_sha256": selection[
            "combined_operator_receipt_sha256"
        ],
        "runtime_authority": selection["runtime_authority"],
        "env_uop_authority_boundary": env_uop_authority_boundary(),
        "canonical_lanes": canonical_lanes,
        "execution_budget": budget,
        "contracts": compiled_contracts,
        "compiler_effect": "DERIVED_OPERATOR_ROUTE_PLAN_ONLY",
        "operator_effect_executed": False,
        "cross_authority_mutation": False,
        "hil_effect": "NONE",
        "pointer_effect": "NONE",
    }
    return {
        **core,
        "compiled_formula_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def _validate_compiled_env_uop_formula(
    value: Mapping[str, Any],
    *,
    sdk_binding_sha256: str,
) -> dict[str, Any]:
    runtime = load_env_uop_runtime_authority()
    compiled = dict(value)
    expected = str(compiled.pop("compiled_formula_sha256", ""))
    require(
        compiled.get("schema") == ENV_UOP_COMPILED_FORMULA_SCHEMA
        and compiled.get("status") == "PASS"
        and compiled.get("sdk_binding_sha256") == sdk_binding_sha256
        and compiled.get("env_uop_authority_boundary") == env_uop_authority_boundary()
        and isinstance(compiled.get("runtime_authority"), dict)
        and compiled["runtime_authority"].get("runtime_authority_sha256")
        == runtime["runtime_authority_sha256"]
        and bool(_ENV_UOP_SHA256_RE.fullmatch(expected))
        and sha256_bytes(canonical_json_bytes(compiled)) == expected,
        "ENV_UOP_COMPILED_FORMULA_INVALID",
        "The compiled ENV/UOP formula or its SDK binding no longer matches.",
        status="MISMATCH",
    )
    return {**compiled, "compiled_formula_sha256": expected}


def route_env_uop_operator(
    compiled_formula: Mapping[str, Any],
    *,
    sdk_binding_sha256: str,
    mode_id: str,
    operator_id: int,
    requested_effect: str,
    lane_id: str,
    tool_id: str,
    lane_units: int,
    tool_invocations: int,
    credential_reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Route one declared operator inside the compiled lane/tool budget."""

    compiled = _validate_compiled_env_uop_formula(
        compiled_formula,
        sdk_binding_sha256=sdk_binding_sha256,
    )
    contract = next(
        (
            item
            for item in cast(list[dict[str, Any]], compiled["contracts"])
            if item["mode_id"] == mode_id
        ),
        None,
    )
    require(
        contract is not None,
        "ENV_UOP_ROUTE_MODE_UNKNOWN",
        "The requested mode is not in the compiled ENV/UOP formula.",
        status="BLOCKED",
        mode_id=mode_id,
    )
    exact_contract = cast(dict[str, Any], contract)
    operator = next(
        (
            item
            for item in cast(list[dict[str, Any]], exact_contract["operators"])
            if item["operator_id"] == operator_id
        ),
        None,
    )
    require(
        operator is not None,
        "ENV_UOP_ROUTE_OPERATOR_UNKNOWN",
        "The requested operator is not compiled for this mode.",
        status="BLOCKED",
        mode_id=mode_id,
        operator_id=operator_id,
    )
    exact_operator = cast(dict[str, Any], operator)
    require(
        lane_id in exact_contract["canonical_lanes"],
        "ENV_UOP_ROUTE_LANE_MISMATCH",
        "The operator may route only through a canonical lane selected for its mode.",
        status="BLOCKED",
        lane_id=lane_id,
        mode_id=mode_id,
    )
    budget = cast(dict[str, Any], compiled["execution_budget"])
    lane_budget = cast(dict[str, int], budget["lane_units"])
    tool_budget = cast(dict[str, int], budget["tool_invocations"])
    exact_lane_units = _env_uop_budget_units(lane_units, field="lane_units")
    exact_tool_invocations = _env_uop_budget_units(
        tool_invocations,
        field="tool_invocations",
    )
    require(
        lane_id in lane_budget
        and exact_lane_units <= lane_budget[lane_id]
        and tool_id in tool_budget
        and exact_tool_invocations <= tool_budget[tool_id],
        "ENV_UOP_ROUTE_BUDGET_EXCEEDED",
        "The requested ENV/UOP route exceeds its compiled lane or tool budget.",
        status="BLOCKED",
        lane_id=lane_id,
        lane_budget=lane_budget.get(lane_id),
        requested_lane_units=exact_lane_units,
        tool_id=tool_id,
        tool_budget=tool_budget.get(tool_id),
        requested_tool_invocations=exact_tool_invocations,
    )
    require(
        requested_effect == exact_operator["declared_effect"],
        "ENV_UOP_ROUTE_EFFECT_MISMATCH",
        "The routed effect must match the compiled operator effect exactly.",
        status="BLOCKED",
        requested_effect=requested_effect,
        declared_effect=exact_operator["declared_effect"],
    )
    effect_receipt = bind_env_uop_operator_effect(
        operator_id,
        requested_effect=requested_effect,
        credential_reference=credential_reference,
    )
    core = {
        "schema": ENV_UOP_OPERATOR_ROUTE_RECEIPT_SCHEMA,
        "status": "PASS",
        "sdk_binding_sha256": sdk_binding_sha256,
        "compiled_formula_sha256": compiled["compiled_formula_sha256"],
        "mode_id": mode_id,
        "operator_id": operator_id,
        "declared_effect": requested_effect,
        "lane_id": lane_id,
        "tool_id": tool_id,
        "budget_consumption": {
            "lane_units": exact_lane_units,
            "lane_budget": lane_budget[lane_id],
            "tool_invocations": exact_tool_invocations,
            "tool_budget": tool_budget[tool_id],
        },
        "effect_receipt": effect_receipt,
        "route_executed": True,
        "declared_effect_executed": False,
        "downstream_side_effect_authorized": False,
        "replay_owner": "INTERNAL_SDK_SQLITE_REQUEST_LEDGER",
        "cross_authority_mutation": False,
        "hil_effect": "NONE",
        "pointer_effect": "NONE",
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


_POLICIES: dict[str, dict[str, Any]] = {
    "D": {
        "policy_name": "Discussion",
        "authority": "lane_recursive_policy_v7:LANE_DISCUSSION",
        "scan_order": ["discussion_cluster", "accepted_deltas", "current_prompt"],
        "unit_of_work": "prompt topic",
        "recursive_loop": "entry -> discuss -> delta/log -> exit",
        "validation_gate": "no code/files unless user changes mode",
        "exit_write_target": "turn_event + visible_reasoning_lane",
        "operator_law": "Relations/Functions + Mathematical Reasoning",
        "formula_rule": "prompt -> package state -> UOP guidance -> answer -> receipt",
        "formula_authority": "lane_formula_execution_registry_v15:LANE_D",
        "ci_cd_required": False,
        "accepted_object": "visible discussion record and accepted delta/log",
        "rollback_target": "prior accepted discussion checkpoint",
    },
    "AL": {
        "policy_name": "Analysis",
        "authority": "lane_recursive_policy_v7:LANE_ANALYSIS",
        "scan_order": [
            "analysis_cluster",
            "source_inventory",
            "related_discussion",
            "accepted_deltas",
        ],
        "unit_of_work": "source/gap/failure unit",
        "recursive_loop": "entry -> inspect -> classify -> findings -> exit",
        "validation_gate": "source truth + no fake claims",
        "exit_write_target": "analysis cluster + gate_evaluation_run",
        "operator_law": "Sets + Statistics + Probability",
        "formula_rule": "prompt -> sources -> relational map -> findings -> receipt",
        "formula_authority": "lane_formula_execution_registry_v15:LANE_AL",
        "ci_cd_required": False,
        "accepted_object": "source-backed findings and gate evaluation",
        "rollback_target": "prior accepted analysis receipt",
    },
    "PL": {
        "policy_name": "Planning",
        "authority": "lane_recursive_policy_v7:LANE_PLANNING",
        "scan_order": [
            "planning_cluster",
            "discussion_route",
            "source_constraints",
            "open_deltas",
        ],
        "unit_of_work": "phase/schema unit",
        "recursive_loop": "entry -> plan -> simulate -> hold/open -> exit",
        "validation_gate": "no build until user says pass/code",
        "exit_write_target": "planning cluster + delta_ledger",
        "operator_law": "Linear Programming + Operations Strategy",
        "formula_rule": "entry -> plan -> simulate -> hold/open -> exit",
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "plan and simulation state without code mutation",
        "rollback_target": "prior accepted plan checkpoint",
    },
    "CD": {
        "policy_name": "Code",
        "authority": "lane_recursive_policy_v7:LANE_CODE",
        "scan_order": [
            "discussion_cluster",
            "planning_cluster",
            "code_cluster",
            "validation_cluster",
            "error_open_deltas",
            "source_files",
        ],
        "unit_of_work": "phase/subphase/code patch",
        "recursive_loop": "entry -> preflight -> sandbox -> patch -> test -> exit",
        "validation_gate": ("safe build + redox/organic reaction + handoff on fail"),
        "exit_write_target": "code cluster + artifacts + validation",
        "operator_law": ("Redox + Organic reaction pathways + Environmental chemistry"),
        "formula_rule": "plan -> sandbox build -> test -> hash -> package",
        "formula_authority": "lane_formula_execution_registry_v15:LANE_CD",
        "ci_cd_required": True,
        "accepted_object": "tested package hash and executable CI/CD receipts",
        "rollback_target": "exact prior accepted PV or code checkpoint",
    },
    "XL": {
        "policy_name": "Excel",
        "authority": "lane_recursive_policy_v7:LANE_EXCEL",
        "scan_order": [
            "discussion_cluster",
            "workbook_plan",
            "sheet_cluster",
            "formula_dependency_graph",
            "validation",
        ],
        "unit_of_work": "sheet/table/formula block",
        "recursive_loop": (
            "entry -> sheet plan -> formula build -> validate cells -> exit"
        ),
        "validation_gate": "formula lineage + no hidden sheet drift",
        "exit_write_target": "artifact_chunk + formula matrix",
        "operator_law": "Matrices + Relations + Accounting",
        "formula_rule": (
            "entry -> sheet plan -> formula build -> validate cells -> exit"
        ),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "workbook artifact, formula lineage, and cell validation",
        "rollback_target": "prior accepted workbook/formula checkpoint",
    },
    "PPT": {
        "policy_name": "PowerPoint",
        "authority": "lane_recursive_policy_v7:LANE_PPT",
        "scan_order": [
            "discussion_cluster",
            "story_plan",
            "slide_cluster",
            "visual_proof",
            "speaker_logic",
        ],
        "unit_of_work": "slide/story unit",
        "recursive_loop": (
            "entry -> slide outline -> visual/source validation -> exit"
        ),
        "validation_gate": "public/private + source proof",
        "exit_write_target": "artifact_registry + slide chunks",
        "operator_law": "Optics + Marketing/Branding + OB",
        "formula_rule": ("entry -> slide outline -> visual/source validation -> exit"),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "deck, slide/source proof, and public-safe validation",
        "rollback_target": "prior accepted deck checkpoint",
    },
    "DOC": {
        "policy_name": "Document",
        "authority": "lane_recursive_policy_v7:LANE_DOCX",
        "scan_order": [
            "discussion_cluster",
            "doc_purpose",
            "source_truth",
            "style_structure",
            "render_QA",
        ],
        "unit_of_work": "section/table/page",
        "recursive_loop": "entry -> section build -> render QA -> exit",
        "validation_gate": "render/QA and source-backed text",
        "exit_write_target": "doc artifact + render receipt",
        "operator_law": "Surface chemistry + Communication systems",
        "formula_rule": "entry -> section build -> render QA -> exit",
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "document artifact, source map, and render receipt",
        "rollback_target": "prior accepted document checkpoint",
    },
    "JD": {
        "policy_name": "JD Matching",
        "authority": "lane_recursive_policy_v7:LANE_JD",
        "scan_order": [
            "jd_parse_cluster",
            "project_evidence_cluster",
            "direct_adjacent_ramp",
            "public_safe_gate",
        ],
        "unit_of_work": "JD requirement row",
        "recursive_loop": (
            "entry -> parse -> match -> classify direct/adjacent/ramp -> exit"
        ),
        "validation_gate": "no fake direct proof",
        "exit_write_target": "jd_match_fact + proof cards",
        "operator_law": "Sets + Probability + Decision Support",
        "formula_rule": (
            "entry -> parse -> match -> classify direct/adjacent/ramp -> exit"
        ),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "requirement classifications and evidence proof cards",
        "rollback_target": "prior accepted JD match matrix",
    },
    "PB": {
        "policy_name": "Project Brain Builder",
        "authority": (
            "lane_formula_execution_registry_v15:LANE_PB + "
            "lane_recursive_policy_v7:LANE_SQLITE"
        ),
        "scan_order": ["root_packet", "schema", "migration", "hash_chain", "mmd_sync"],
        "unit_of_work": "schema/table/migration",
        "recursive_loop": (
            "entry -> schema preflight -> sandbox -> migrate -> hash -> exit"
        ),
        "validation_gate": "write receipt + mmd after sqlite",
        "exit_write_target": "env_sqlite_write_receipt + mmd_node_registry",
        "operator_law": "Matrices + Determinants + Database/ERP",
        "formula_rule": (
            "register source -> inventory -> index -> graph -> project sqlite -> project MMD"
        ),
        "formula_authority": "lane_formula_execution_registry_v15:LANE_PB",
        "ci_cd_required": True,
        "accepted_object": "project SQLite, project MMD, hashes, and build receipts",
        "rollback_target": "prior accepted brain/package checkpoint",
    },
    "RS": {
        "policy_name": "Research",
        "authority": "lane_recursive_policy_v7:LANE_RESEARCH",
        "scan_order": [
            "question",
            "source_stack",
            "internet_context_if_allowed",
            "source_truth_split",
        ],
        "unit_of_work": "research claim section",
        "recursive_loop": (
            "entry -> source rank -> external context -> conclusion -> exit"
        ),
        "validation_gate": "internet context-only unless asked",
        "exit_write_target": "research cluster + citations summary",
        "operator_law": "Statistics + Source precedence",
        "formula_rule": (
            "entry -> source rank -> external context -> conclusion -> exit"
        ),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "source-ranked conclusion and citation summary",
        "rollback_target": "prior accepted research checkpoint",
    },
    "X": {
        "policy_name": "Custom User Mode",
        "authority": "lane_recursive_policy_v7:LANE_CUSTOM",
        "scan_order": ["user_defined_mode", "cluster_policy", "gates", "exit_write"],
        "unit_of_work": "user-defined unit",
        "recursive_loop": "entry -> create lane -> apply route -> exit",
        "validation_gate": "must define dependency policy",
        "exit_write_target": "custom mode cluster",
        "operator_law": "User-defined operator trigger",
        "formula_rule": "entry -> create lane -> apply route -> exit",
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "user-defined deliverable under its dependency policy",
        "rollback_target": "prior accepted custom-mode checkpoint",
    },
}

_EXTRA_POLICIES: dict[str, dict[str, Any]] = {
    "OP": {
        "policy_name": "Output",
        "authority": "mode_cluster:output + mode_cluster_dependency:output",
        "scan_order": ["OUTPUT_CLUSTER"],
        "unit_of_work": "artifact response",
        "recursive_loop": "gates pass -> artifact response -> lineage receipt",
        "validation_gate": "artifact response only after gates pass",
        "exit_write_target": "artifact output lane",
        "operator_law": "Optics + Accounting",
        "formula_rule": "gates pass -> artifact response -> lineage receipt",
        "formula_authority": "DERIVED_FROM_ENV_MODE_CLUSTER_PURPOSE",
        "ci_cd_required": False,
        "accepted_object": "source-bound artifact and render lineage",
        "rollback_target": "prior accepted artifact checkpoint",
    },
    "VAL": {
        "policy_name": "Validation",
        "authority": "mode_cluster:validation + mode_cluster_dependency:validation",
        "scan_order": ["VALIDATION_CLUSTER"],
        "unit_of_work": "validation gate",
        "recursive_loop": "inspect -> classify PASS/WARN/OPEN/BLOCKED/FAIL -> receipt",
        "validation_gate": "every claimed gate needs executable evidence",
        "exit_write_target": "validation receipt set",
        "operator_law": "Mathematical Reasoning + Statistics + Accounting",
        "formula_rule": ("inspect -> classify PASS/WARN/OPEN/BLOCKED/FAIL -> receipt"),
        "formula_authority": "DERIVED_FROM_ENV_MODE_CLUSTER_PURPOSE",
        "ci_cd_required": False,
        "accepted_object": "executable validation receipt set",
        "rollback_target": "prior accepted validation checkpoint",
    },
    "ENG": {
        "policy_name": "Project Engulf",
        "authority": "env_mode_registry_v17:PROJECT_ENGULF + project_engulf sector law",
        "scan_order": ["project package", "source registry", "topology", "gates"],
        "unit_of_work": "project source package",
        "recursive_loop": "inspect -> explicit grant -> controlled fusion -> receipt/relock",
        "validation_gate": "explicit one-turn mutation grant and immediate relock",
        "exit_write_target": "project_engulf sector + mutation receipt",
        "operator_law": "Redox + Organic pathway + Management Information Systems",
        "formula_rule": (
            "inspect -> explicit grant -> controlled fusion -> receipt/relock"
        ),
        "formula_authority": "DERIVED_FROM_MODE_NAMESPACE_AND_SECTOR_LAW",
        "ci_cd_required": False,
        "accepted_object": "registered project package and topology-growth receipt",
        "rollback_target": "prior accepted project-engulf checkpoint",
    },
    "CE": {
        "policy_name": "Clean Exit",
        "authority": "env_workflow_event_v17:GOAL_OPTION_2_EXIT + STATE_TRAVEL_EXIT",
        "scan_order": ["exact prompt", "exact files", "visible response", "receipts"],
        "unit_of_work": "Entry/Exit Slip continuity packet",
        "recursive_loop": "PREPARE -> visible response -> COMMIT -> receipt",
        "validation_gate": "hidden reasoning excluded; append-only head monotonic",
        "exit_write_target": "chat_lineage + Exit Slip",
        "operator_law": "Communication Systems + Mathematical Reasoning + Human gates",
        "formula_rule": "PREPARE -> visible response -> COMMIT -> receipt",
        "formula_authority": "CHAT_LINEAGE_APPEND_ONLY_LAW",
        "ci_cd_required": False,
        "accepted_object": "exact Exit Slip and continuity packet",
        "rollback_target": "prior accepted continuity checkpoint",
    },
    "RCV": {
        "policy_name": "Recovery",
        "authority": "env_mode_registry_v17:RUNTIME + STATE_TRAVEL_READY",
        "scan_order": ["preserved state", "receipts", "pointers", "recovery artifacts"],
        "unit_of_work": "exact recovery boundary",
        "recursive_loop": "resume preserved state -> verify receipts -> return to boundary",
        "validation_gate": "no fabricated State Travel or pointer mutation",
        "exit_write_target": "recovery receipt + chat lineage",
        "operator_law": "Continuity + fallback paths + Risk and Crisis Management",
        "formula_rule": (
            "resume preserved state -> verify receipts -> return to boundary"
        ),
        "formula_authority": "DERIVED_FROM_ENV_RECOVERY_NAMESPACE",
        "ci_cd_required": False,
        "accepted_object": "verified exact-resume boundary and recovery receipts",
        "rollback_target": "exact prior accepted pointer named by the user",
    },
}

_OPERATORS: dict[int, dict[str, Any]] = {
    4: {"family": "PHYSICS", "chapter": "Laws of Motion", "effect": "no route drift"},
    6: {
        "family": "PHYSICS",
        "chapter": "System of Particles and Rotational Motion",
        "effect": "rollback pivots",
    },
    13: {
        "family": "PHYSICS",
        "chapter": "EMI and Alternating Current",
        "effect": "fallback paths",
    },
    15: {
        "family": "PHYSICS",
        "chapter": "Optics",
        "effect": "public/private visibility",
    },
    19: {
        "family": "PHYSICS",
        "chapter": "Communication Systems",
        "effect": "channel encoding and feedback",
    },
    27: {
        "family": "CHEMISTRY",
        "chapter": "Redox Reactions",
        "effect": "sandbox-first patch risk",
    },
    28: {
        "family": "CHEMISTRY",
        "chapter": "Organic Chemistry Principles",
        "effect": "phase patch pathway",
    },
    29: {
        "family": "CHEMISTRY",
        "chapter": "Environmental Chemistry",
        "effect": "dependency and leak safety",
    },
    34: {
        "family": "CHEMISTRY",
        "chapter": "Surface Chemistry",
        "effect": "rendered surface versus backend truth",
    },
    42: {"family": "MATHS", "chapter": "Sets", "effect": "source and claim sets"},
    43: {
        "family": "MATHS",
        "chapter": "Relations and Functions",
        "effect": "typed source-output mapping",
    },
    55: {
        "family": "MATHS",
        "chapter": "Mathematical Reasoning",
        "effect": "contradiction and proof gates",
    },
    56: {
        "family": "MATHS",
        "chapter": "Statistics",
        "effect": "coverage and error distribution",
    },
    57: {
        "family": "MATHS",
        "chapter": "Probability",
        "effect": "uncertainty and risk thresholds",
    },
    58: {
        "family": "MATHS",
        "chapter": "Relations and Functions",
        "effect": "reverse typed mappings",
    },
    60: {
        "family": "MATHS",
        "chapter": "Matrices",
        "effect": "source-gate-mode matrices",
    },
    61: {
        "family": "MATHS",
        "chapter": "Determinants",
        "effect": "critical gate viability",
    },
    62: {
        "family": "MATHS",
        "chapter": "Continuity and Differentiability",
        "effect": "smooth phase lineage",
    },
    69: {
        "family": "MATHS",
        "chapter": "Linear Programming",
        "effect": "proof/risk constraint optimization",
    },
    71: {
        "family": "MBA",
        "chapter": "Accounting",
        "effect": "audit and source ledgers",
    },
    74: {
        "family": "MBA",
        "chapter": "Management Information Systems",
        "effect": "system integration and information flow",
    },
    76: {
        "family": "MBA",
        "chapter": "Production and Operations Management",
        "effect": "process and bottleneck control",
    },
    80: {
        "family": "MBA",
        "chapter": "Organizational Behavior",
        "effect": "human gates",
    },
    81: {
        "family": "MBA",
        "chapter": "Strategic Marketing and Branding",
        "effect": "public-safe positioning",
    },
    82: {
        "family": "MBA",
        "chapter": "Operations and Supply Chain",
        "effect": "workflow stages and data logistics",
    },
    90: {
        "family": "MBA",
        "chapter": "Database Management and ERP",
        "effect": "database ledgers",
    },
    98: {
        "family": "MBA",
        "chapter": "Decision Support Systems",
        "effect": "evidence-backed decision output",
    },
    106: {
        "family": "SUPPLY",
        "chapter": "Risk and Crisis Management",
        "effect": "failure handoff and rollback",
    },
    110: {
        "family": "SUPPLY",
        "chapter": "Operations Strategy",
        "effect": "technical route alignment",
    },
}

_ENV_DOMAIN_CATALOG_PATH = (
    resolve_plugin_root(__file__) / "toolchains" / "env-domain-catalog.v2.json"
)
_ENV_DOMAIN_CATALOG = json.loads(_ENV_DOMAIN_CATALOG_PATH.read_text(encoding="utf-8"))
_ENV_DOMAIN_CATALOG_RECEIPT = str(_ENV_DOMAIN_CATALOG.pop("receipt_sha256", ""))
require(
    _ENV_DOMAIN_CATALOG.get("schema") == "evidence-lane.env-domain-catalog.v2"
    and _ENV_DOMAIN_CATALOG.get("status") == "PASS_FULL_MMD_BEHAVIOR_BOUND"
    and _ENV_DOMAIN_CATALOG_RECEIPT
    == sha256_bytes(canonical_json_bytes(_ENV_DOMAIN_CATALOG)),
    "ENV_DOMAIN_OPERATOR_CATALOG_INVALID",
    "The complete adapted PCM/MBA operator catalog is invalid.",
    status="MISMATCH",
)
_OPERATORS = {
    int(row["operator_id"]): {
        "family": str(row["engine_group"]),
        "chapter": str(row["chapter"]),
        "effect": str(row["output_effect"]),
    }
    for row in _ENV_DOMAIN_CATALOG["tables"]["pcm_mba_operator"]["rows"]
}
require(
    len(_OPERATORS) == 110,
    "ENV_DOMAIN_OPERATOR_CATALOG_INCOMPLETE",
    "The adapted PCM/MBA catalog must preserve all 110 supplied operators.",
    status="MISMATCH",
)

_MODE_OPERATORS: dict[str, tuple[int, ...]] = {
    "D": (43, 55),
    "AL": (42, 56, 57),
    "PL": (69, 110),
    "CD": (4, 6, 27, 28, 29, 55, 58, 61, 71, 74, 76, 80, 82, 106),
    "XL": (43, 60, 71),
    "PPT": (15, 80, 81),
    "DOC": (19, 34),
    "JD": (42, 57, 98),
    "PB": (58, 60, 61, 74, 90),
    "RS": (56, 71),
    "X": (),
    "OP": (15, 71),
    "VAL": (55, 56, 71),
    "ENG": (27, 28, 29, 74),
    "CE": (19, 55, 80),
    "RCV": (6, 13, 62, 106),
}

_MODE_RECURSIVE_POLICY_ROWS: dict[str, str] = {
    "D": "LANE_DISCUSSION",
    "AL": "LANE_ANALYSIS",
    "PL": "LANE_PLANNING",
    "CD": "LANE_CODE",
    "XL": "LANE_EXCEL",
    "PPT": "LANE_PPT",
    "DOC": "LANE_DOCX",
    "JD": "LANE_JD",
    "PB": "LANE_SQLITE",
    "RS": "LANE_RESEARCH",
    "X": "LANE_CUSTOM",
}


def _runtime_policy(
    mode_id: str,
    runtime: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay the compatibility projection with exact locked SQLite rows."""

    mode_namespace = cast(dict[str, Any], runtime["mode_namespace"])
    require(
        mode_id in mode_namespace,
        "ENV_UOP_MODE_NAMESPACE_MISSING",
        "The selected mode is absent from the locked ENV mode namespace.",
        status="MISMATCH",
        mode_id=mode_id,
    )
    policy = dict(_POLICIES.get(mode_id) or _EXTRA_POLICIES[mode_id])
    recursive_id = _MODE_RECURSIVE_POLICY_ROWS.get(mode_id)
    recursive = (
        cast(dict[str, Any], runtime["recursive_policies"]).get(recursive_id)
        if recursive_id
        else None
    )
    if isinstance(recursive, dict):
        policy.update(
            {
                "authority": f"lane_recursive_policy_v7:{recursive_id}",
                "scan_order": json.loads(str(recursive["scan_order_json"])),
                "unit_of_work": str(recursive["unit_of_work"]),
                "recursive_loop": str(recursive["recursive_loop"]),
                "validation_gate": str(recursive["validation_gate"]),
                "exit_write_target": str(recursive["exit_write_target"]),
                "operator_law": str(recursive["chemistry_or_math_law"]),
            }
        )
    formula = cast(dict[str, Any], runtime["formula_registry"]).get(f"LANE_{mode_id}")
    if isinstance(formula, dict):
        policy.update(
            {
                "formula_rule": str(formula["formula_rule"]),
                "source_route_formula": formula.get("source_route_formula"),
                "formula_authority": (
                    f"lane_formula_execution_registry_v15:LANE_{mode_id}"
                ),
                "ci_cd_required": bool(formula["ci_cd_applicable"]),
                "validation_loop": str(formula["validation_loop"]),
            }
        )
    policy["mode_namespace_row"] = mode_namespace[mode_id]
    policy["runtime_authority_sha256"] = runtime["runtime_authority_sha256"]
    return policy


def _hil_contract(mode_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    accepted_object = str(policy["accepted_object"])
    validation_gate = str(policy["validation_gate"])
    rollback_target = str(policy["rollback_target"])
    code_like = mode_id in {"CD", "PB"}
    choices = [
        {
            "token": "APPROVE",
            "lane_effect": (
                f"Accept {accepted_object}. "
                + (
                    "Candidate promotion remains an exact lifecycle HIL action."
                    if code_like
                    else "This does not authorize code, deployment, or pointer movement."
                )
            ),
            "requires": "all mode-specific validation gates PASS",
        },
        {
            "token": "APPROVE_WITH_DELTA",
            "lane_effect": (
                f"Open one bounded {policy['policy_name']} correction and rerun "
                f"{policy['recursive_loop']}."
            ),
            "requires": "one exact correction",
        },
        {
            "token": "MORE_RESEARCH",
            "lane_effect": (
                f"Hold {accepted_object}; answer one bounded source or evidence gap."
            ),
            "requires": "one exact research question",
        },
        {
            "token": "ROLLBACK",
            "lane_effect": f"Return only to {rollback_target}.",
            "requires": "one exact accepted target when the target is not implicit",
        },
        {
            "token": "REJECT",
            "lane_effect": f"Reject {accepted_object} without accepting its state.",
            "requires": "one visible reason",
        },
        {
            "token": "FAIL",
            "lane_effect": f"Record failure of gate: {validation_gate}.",
            "requires": "one exact failed gate or executable receipt",
        },
    ]
    require(
        [row["token"] for row in choices] == list(PROJECT_HIL_DECISION_TOKENS),
        "MODE_HIL_TOKEN_CONTRACT_MISMATCH",
        "Mode HIL semantics must preserve the current Project-authority policy.",
        status="FAIL",
    )
    return {
        "schema": "evidence-lane.mode-hil-semantics.v1",
        "authority": (
            "PROJECT_AUTHORITY_POLICY_PLUS_ENV_LANE_GATE_LOOP_AND_EXIT_TARGET_DERIVATION"
        ),
        "mode_id": mode_id,
        "accepted_object": accepted_object,
        "choices": choices,
        "mode_selection_is_not_hil_approval": True,
        "implicit_promotion_allowed": False,
    }


def _compile_custom_dependency_policy(
    selected_mode: Mapping[str, Any],
) -> dict[str, Any]:
    schema = selected_mode.get("schema")
    require(
        isinstance(schema, dict),
        "CUSTOM_MODE_SCHEMA_REQUIRED",
        "A selected custom mode needs its explicit session schema.",
        status="BLOCKED",
        mode_id=selected_mode.get("id"),
    )
    schema_dict = cast(dict[str, Any], schema)
    dependency_policy = schema_dict.get("dependency_policy")
    require(
        isinstance(dependency_policy, dict)
        and set(dependency_policy) <= {"on_missing", "requires"}
        and isinstance(dependency_policy.get("requires", []), list)
        and str(dependency_policy.get("on_missing") or "BLOCK").upper() == "BLOCK",
        "CUSTOM_MODE_DEPENDENCY_POLICY_REQUIRED",
        "ENV15 Custom mode requires an explicit dependency list and fail-closed BLOCK policy.",
        status="BLOCKED",
        mode_id=selected_mode.get("id"),
    )
    exact_policy = cast(dict[str, Any], dependency_policy)
    return {
        "requires": exact_policy.get("requires", []),
        "on_missing": "BLOCK",
    }


def govern_mode_selection(
    selected_modes: list[dict[str, Any]],
    *,
    request: str,
    selection_source: str,
) -> dict[str, Any]:
    """Return visible formulas, operator receipts, and lane-specific HIL semantics."""

    runtime = load_env_uop_runtime_authority()
    request_sha256 = sha256_bytes(request.encode("utf-8"))
    contracts: list[dict[str, Any]] = []
    for selected in selected_modes:
        full_mode_id = str(selected["id"])
        mode_id = "X" if full_mode_id.startswith("X:") else full_mode_id
        policy = _runtime_policy(mode_id, runtime)
        dependency_policy = None
        if mode_id == "X":
            dependency_policy = _compile_custom_dependency_policy(selected)
            policy["accepted_object"] = (
                f"custom deliverable '{selected['name']}' under its dependency policy"
            )
        runtime_operators = cast(dict[str, Any], runtime["operators"])
        runtime_activation = cast(dict[str, Any], runtime["operator_activation"])
        operators = []
        for operator_id in _MODE_OPERATORS[mode_id]:
            projected = _OPERATORS[operator_id]
            sqlite_operator = cast(dict[str, Any], runtime_operators[str(operator_id)])
            activation = cast(dict[str, Any], runtime_activation[str(operator_id)])
            require(
                str(sqlite_operator["engine_group"]) == str(projected["family"])
                and str(sqlite_operator["chapter"]) == str(projected["chapter"])
                and str(activation["activation_state"]) == "FORMULA_ACTIVE",
                "ENV_UOP_OPERATOR_PROJECTION_MISMATCH",
                "The selected operator differs from its locked SQLite runtime row.",
                status="MISMATCH",
                operator_id=operator_id,
                mode_id=mode_id,
            )
            operators.append(
                {
                    "operator_id": operator_id,
                    **projected,
                    "declared_effect_sha256": sha256_bytes(
                        str(projected["effect"]).encode("utf-8")
                    ),
                    "sqlite_runtime": {
                        "route_function": sqlite_operator["route_function"],
                        "fire_trigger": sqlite_operator["fire_trigger"],
                        "output_effect": sqlite_operator["output_effect"],
                        "formula_depth": activation["formula_depth"],
                        "activation_state": activation["activation_state"],
                    },
                }
            )
        operator_families = list(dict.fromkeys(str(row["family"]) for row in operators))
        operator_groups: list[str] = []
        if {"PHYSICS", "CHEMISTRY", "MATHS"} <= set(operator_families):
            operator_groups.append("PCM")
        else:
            operator_groups.extend(
                family
                for family in ("PHYSICS", "CHEMISTRY", "MATHS")
                if family in operator_families
            )
        operator_groups.extend(
            family
            for family in operator_families
            if family not in {"PHYSICS", "CHEMISTRY", "MATHS"}
        )
        ci_cd = {
            "required": bool(policy["ci_cd_required"]),
            "loop": (policy["formula_rule"] if policy["ci_cd_required"] else None),
            "controlled": bool(policy["ci_cd_required"]),
            "autonomous_flash_fuse_deploy_allowed": False,
            "authority": (
                "env_formula_registry_v17"
                if policy["ci_cd_required"]
                else "MODE_SPECIFIC_VALIDATION_NOT_GENERIC_CI_CD"
            ),
        }
        lane_toolchains: list[dict[str, Any]] = []
        runtime_lane_toolchains = cast(
            dict[str, Any], runtime["ai_toolchain_lane_bindings"]
        )
        for lane_id in selected["canonical_lanes"]:
            binding = cast(dict[str, Any], runtime_lane_toolchains[str(lane_id)])
            lane_toolchains.append(
                {
                    "lane_id": lane_id,
                    "action_classes": json.loads(binding["action_classes_json"]),
                    "ordered_tools": json.loads(binding["ordered_tools_json"]),
                    "binding_sha256": binding["binding_sha256"],
                    "selection": "RUN_ONLY_WHEN_ACTIVE_ACTION_REQUIRES_TOOL",
                }
            )
        contract_core = {
            "schema": "evidence-lane.mode-governance-contract.v1",
            "mode_id": full_mode_id,
            "mode_name": selected["name"],
            "selection_source": selection_source,
            "request_sha256": request_sha256,
            "canonical_lanes": selected["canonical_lanes"],
            "env_authority": {
                "env_sqlite_sha256": ENV15_ENV_SQLITE_SHA256,
                "uop_sqlite_sha256": ENV15_UOP_SQLITE_SHA256,
                "env_mmd_sha256": ENV_MMD_SHA256,
                "uop_mmd_sha256": UOP_MMD_SHA256,
                "mode_policy_projection_sha256": ENV15_MODE_POLICY_PROJECTION_SHA256,
                "policy_row": policy["authority"],
                "mode_namespace_row": policy["mode_namespace_row"],
                "runtime_authority_sha256": runtime["runtime_authority_sha256"],
            },
            "env_uop_authority_boundary": env_uop_authority_boundary(),
            "scan_order": policy["scan_order"],
            "unit_of_work": policy["unit_of_work"],
            "recursive_loop": policy["recursive_loop"],
            "validation_gate": policy["validation_gate"],
            "exit_write_target": policy["exit_write_target"],
            "operator_law": policy["operator_law"],
            "formula": {
                "rule": policy["formula_rule"],
                "source_route_formula": policy.get("source_route_formula"),
                "authority": policy["formula_authority"],
                "components": runtime["formula_components"],
                "visible_in_response": True,
            },
            "operators": operators,
            "operator_families": operator_families,
            "operator_groups": operator_groups,
            "ci_cd": ci_cd,
            "dependency_policy": dependency_policy,
            "conditional_toolchain": {
                "authority": "env_lane_binding_v17",
                "lane_bindings": lane_toolchains,
                "all_tools_run_each_turn": False,
                "missing_required_primary_behavior": (
                    "TRY_DECLARED_SAME_CLASS_FALLBACK_ELSE_FAIL_CLOSED"
                ),
                "cross_class_silent_fallback_allowed": False,
            },
            "uop_runtime": {
                "public_operator_count": len(runtime["uop_public_operators"]),
                "mode_routes": runtime["uop_mode_routes"],
                "no_override_env": runtime["uop_root"]["can_override_env"] == 0,
                "no_override_project": (
                    runtime["uop_root"]["can_override_project"] == 0
                ),
                "no_autonomous_cicd_gate": runtime["no_autonomous_cicd_gate"],
            },
            "hil": _hil_contract(mode_id, policy),
            "lifecycle_effect": "NONE",
            "candidate_created": False,
            "pointer_moved": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(contract_core))
        formula_display = (
            f"Mode={selected['name']} | ENV formula: {policy['formula_rule']} | "
            f"Loop: {policy['recursive_loop']} | CI/CD: "
            f"{'CONTROLLED_REQUIRED' if ci_cd['required'] else 'NOT_GENERIC_TO_THIS_MODE'} | "
            f"Operators: {' + '.join(operator_groups) if operator_groups else policy['operator_law']} | "
            f"Receipt={receipt_sha256}"
        )
        contracts.append(
            {
                **contract_core,
                "operator_receipt_sha256": receipt_sha256,
                "formula_display": formula_display,
            }
        )

    receipt_projection = [
        {
            "mode_id": row["mode_id"],
            "operator_receipt_sha256": row["operator_receipt_sha256"],
        }
        for row in contracts
    ]
    combined_receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_projection))
    return {
        "schema": "evidence-lane.mode-governance-selection.v1",
        "status": "PASS",
        "selection_source": selection_source,
        "request_sha256": request_sha256,
        "contracts": contracts,
        "visible_formula_response": [row["formula_display"] for row in contracts],
        "combined_operator_receipt_sha256": combined_receipt_sha256,
        "authority_hil_is_lane_specific": True,
        "authority_hil_token_vocabulary": list(PROJECT_HIL_DECISION_TOKENS),
        "decision_count_is_behavior_ceiling": False,
        "mode_selection_is_not_hil_approval": True,
        "selected_governance_binds_next_task_execution": True,
        "runtime_authority": {
            key: runtime[key]
            for key in (
                "authority_mode",
                "env_sqlite_sha256",
                "uop_sqlite_sha256",
                "env_mmd_sha256",
                "uop_mmd_sha256",
                "runtime_authority_sha256",
                "sqlite_runtime_behavior",
                "mmd_traversal_behavior",
                "python_projection_is_authority",
            )
        },
        "lifecycle_effect": "NONE",
        "candidate_created": False,
        "pointer_moved": False,
    }


def validate_mode_governance_selection(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one selected-mode contract before execution or HIL rendering."""

    runtime = load_env_uop_runtime_authority()
    require(
        value.get("schema") == "evidence-lane.mode-governance-selection.v1"
        and value.get("status") == "PASS",
        "MODE_GOVERNANCE_SELECTION_INVALID",
        "The selected mode governance envelope is not executable.",
        status="MISMATCH",
    )
    runtime_selection = value.get("runtime_authority")
    require(
        isinstance(runtime_selection, dict)
        and runtime_selection.get("runtime_authority_sha256")
        == runtime["runtime_authority_sha256"]
        and runtime_selection.get("env_sqlite_sha256") == runtime["env_sqlite_sha256"]
        and runtime_selection.get("uop_sqlite_sha256") == runtime["uop_sqlite_sha256"]
        and runtime_selection.get("env_mmd_sha256") == runtime["env_mmd_sha256"]
        and runtime_selection.get("uop_mmd_sha256") == runtime["uop_mmd_sha256"]
        and runtime_selection.get("sqlite_runtime_behavior") is True
        and runtime_selection.get("mmd_traversal_behavior") is True
        and runtime_selection.get("python_projection_is_authority") is False,
        "ENV_UOP_RUNTIME_SELECTION_MISMATCH",
        "Mode governance is not bound to the current locked SQLite/MMD runtime.",
        status="MISMATCH",
    )
    raw_contracts = value.get("contracts")
    require(
        isinstance(raw_contracts, list) and bool(raw_contracts),
        "MODE_GOVERNANCE_CONTRACTS_REQUIRED",
        "Selected mode governance requires at least one lane contract.",
        status="MISMATCH",
    )
    contracts = cast(list[Any], raw_contracts)
    receipt_projection: list[dict[str, str]] = []
    for raw_contract in contracts:
        require(
            isinstance(raw_contract, dict),
            "MODE_GOVERNANCE_CONTRACT_INVALID",
            "A selected mode contract is not an object.",
            status="MISMATCH",
        )
        contract = cast(dict[str, Any], raw_contract)
        env_authority = cast(dict[str, Any], contract.get("env_authority") or {})
        require(
            contract.get("env_uop_authority_boundary") == env_uop_authority_boundary(),
            "ENV_UOP_AUTHORITY_BOUNDARY_MISMATCH",
            "The selected mode does not retain the exact ENV/UOP ownership boundary.",
            status="MISMATCH",
            mode_id=contract.get("mode_id"),
        )
        require(
            env_authority.get("runtime_authority_sha256")
            == runtime["runtime_authority_sha256"]
            and env_authority.get("env_mmd_sha256") == runtime["env_mmd_sha256"]
            and env_authority.get("uop_mmd_sha256") == runtime["uop_mmd_sha256"],
            "ENV_UOP_RUNTIME_CONTRACT_MISMATCH",
            "A selected mode contract is detached from locked SQLite/MMD runtime.",
            status="MISMATCH",
            mode_id=contract.get("mode_id"),
        )
        raw_operators = contract.get("operators")
        require(
            isinstance(raw_operators, list),
            "ENV_UOP_OPERATOR_EFFECTS_MISSING",
            "The selected mode must bind every operator to its declared effect.",
            status="MISMATCH",
            mode_id=contract.get("mode_id"),
        )
        for raw_operator in cast(list[Any], raw_operators):
            require(
                isinstance(raw_operator, dict),
                "ENV_UOP_OPERATOR_EFFECT_INVALID",
                "A selected ENV/UOP operator effect receipt is not an object.",
                status="MISMATCH",
            )
            operator_row = cast(dict[str, Any], raw_operator)
            raw_operator_id = operator_row.get("operator_id")
            require(
                isinstance(raw_operator_id, int)
                and not isinstance(raw_operator_id, bool),
                "ENV_UOP_OPERATOR_ID_INVALID",
                "A selected ENV/UOP operator requires an integer identifier.",
                status="MISMATCH",
            )
            operator_id = cast(int, raw_operator_id)
            registered = _OPERATORS.get(operator_id)
            registered_row = registered if isinstance(registered, dict) else {}
            declared_effect = str(registered_row.get("effect") or "")
            require(
                bool(registered_row)
                and operator_row.get("family") == registered_row.get("family")
                and operator_row.get("chapter") == registered_row.get("chapter")
                and operator_row.get("effect") == declared_effect
                and operator_row.get("declared_effect_sha256")
                == sha256_bytes(declared_effect.encode("utf-8")),
                "ENV_UOP_OPERATOR_EFFECT_MISMATCH",
                "A selected operator no longer matches its declared ENV/UOP effect.",
                status="MISMATCH",
                operator_id=operator_id,
            )
        expected = str(contract.get("operator_receipt_sha256") or "")
        core = {
            key: item
            for key, item in contract.items()
            if key not in {"operator_receipt_sha256", "formula_display"}
        }
        actual = sha256_bytes(canonical_json_bytes(core))
        require(
            bool(expected) and expected == actual,
            "MODE_OPERATOR_RECEIPT_INVALID",
            "A selected mode operator receipt does not match its ENV/UOP contract.",
            status="MISMATCH",
            mode_id=contract.get("mode_id"),
            expected=expected or None,
            actual=actual,
        )
        hil = contract.get("hil")
        choices = hil.get("choices") if isinstance(hil, dict) else None
        require(
            isinstance(choices, list)
            and [row.get("token") for row in choices if isinstance(row, dict)]
            == list(PROJECT_HIL_DECISION_TOKENS),
            "MODE_HIL_TOKEN_CONTRACT_MISMATCH",
            "The selected mode must preserve the current Project-authority HIL policy.",
            status="MISMATCH",
            mode_id=contract.get("mode_id"),
        )
        mode_id = str(contract.get("mode_id") or "")
        if mode_id == "CD":
            require(
                contract.get("ci_cd", {}).get("required") is True
                and contract.get("ci_cd", {}).get("controlled") is True
                and {"PCM", "MBA"} <= set(contract.get("operator_groups") or []),
                "CODE_MODE_OPERATOR_CONTRACT_INVALID",
                "Code mode requires controlled CI/CD plus PCM and MBA operator groups.",
                status="FAIL",
            )
        receipt_projection.append(
            {"mode_id": mode_id, "operator_receipt_sha256": expected}
        )
    combined = sha256_bytes(canonical_json_bytes(receipt_projection))
    require(
        value.get("combined_operator_receipt_sha256") == combined,
        "MODE_GOVERNANCE_COMBINED_RECEIPT_INVALID",
        "The selected-mode receipt projection does not match its lane contracts.",
        status="MISMATCH",
    )
    require(
        value.get("authority_hil_is_lane_specific") is True
        and value.get("authority_hil_token_vocabulary")
        == list(PROJECT_HIL_DECISION_TOKENS)
        and value.get("decision_count_is_behavior_ceiling") is False
        and value.get("candidate_created") is False
        and value.get("pointer_moved") is False,
        "MODE_GOVERNANCE_BOUNDARY_INVALID",
        "Mode selection must remain pointer-neutral and lane-specific.",
        status="FAIL",
    )
    return value


def validate_mode_binding(
    value: dict[str, Any], *, expected_task_id: str | None = None
) -> dict[str, Any]:
    """Validate the immutable selected-mode snapshot used by one task/build."""

    require(
        value.get("schema")
        in {
            "evidence-lane.active-mode-binding.v1",
            "evidence-lane.task-mode-binding.v1",
        },
        "MODE_BINDING_SCHEMA_INVALID",
        "The selected-mode execution binding is not supported.",
        status="MISMATCH",
    )
    expected = str(value.get("binding_receipt_sha256") or "")
    core = {key: item for key, item in value.items() if key != "binding_receipt_sha256"}
    actual = sha256_bytes(canonical_json_bytes(core))
    require(
        bool(expected) and expected == actual,
        "MODE_BINDING_RECEIPT_INVALID",
        "The selected-mode execution binding was changed after selection.",
        status="MISMATCH",
        expected=expected or None,
        actual=actual,
    )
    governance = value.get("mode_governance")
    require(
        isinstance(governance, dict),
        "MODE_BINDING_GOVERNANCE_MISSING",
        "The mode binding has no executable governance selection.",
        status="MISMATCH",
    )
    validate_mode_governance_selection(cast(dict[str, Any], governance))
    if expected_task_id is not None:
        require(
            value.get("task_id") == expected_task_id,
            "MODE_BINDING_TASK_MISMATCH",
            "The selected-mode snapshot belongs to a different task.",
            status="MISMATCH",
            expected_task_id=expected_task_id,
            actual_task_id=value.get("task_id"),
        )
    return value
