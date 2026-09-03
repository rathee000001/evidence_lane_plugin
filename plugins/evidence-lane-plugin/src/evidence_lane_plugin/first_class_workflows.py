"""First-class workflow owners built from the existing Evidence Lane engines.

These workflows keep Formula, Brain Scaling, Project Recipes, the full AI
toolchain, and Bigger Universe federation distinct from Mode.  They project or
route existing authorities; they do not create a PV candidate, infer HIL, move
a pointer, train a model, or merge project truth.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from .ai_toolchain import resolve_lane_toolchain
from .connector_governance import ConnectorGovernance
from .ecosystem_toolchain import resolve_ecosystem_adapters
from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CANONICAL_LANE_IDS
from .mode_governance import compile_env_uop_formula, route_env_uop_operator
from .operating_modes import classify_operating_modes
from .project_execution_profile import compile_project_execution_profile

FORMULA_ENGINE_WORKFLOW_SCHEMA = "evidence-lane.formula-engine-workflow.v1"
BRAIN_SCALING_WORKFLOW_SCHEMA = "evidence-lane.brain-scaling-workflow.v1"
PROJECT_RECIPE_WORKFLOW_SCHEMA = "evidence-lane.project-recipe-workflow.v1"
FULL_AI_TOOLCHAIN_WORKFLOW_SCHEMA = "evidence-lane.full-ai-toolchain-workflow.v1"
BIGGER_UNIVERSE_WORKFLOW_SCHEMA = "evidence-lane.bigger-universe-workflow.v1"

KnownProjectType = Literal["CODE", "DATA", "DOCUMENT", "RESEARCH", "MIXED"]
ProjectType = str


class FormulaEngineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode_governance: dict[str, Any]
    execution_budget: dict[str, Any]
    sdk_binding_sha256: str = Field(pattern=r"^[A-F0-9]{64}$")
    route: dict[str, Any] | None = None


class BrainSlice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_id: str = Field(min_length=1, max_length=256)
    content_sha256: str = Field(pattern=r"^[A-F0-9]{64}$")
    token_count: int = Field(ge=1, le=10_000_000)
    priority: int = Field(default=0, ge=-1_000, le=1_000)


class BrainScalingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_id: str = Field(min_length=1, max_length=128)
    slices: list[BrainSlice]
    token_budget: int = Field(ge=1, le=100_000_000)
    max_slices: int = Field(ge=1, le=100_000)


class ProjectRecipeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str = Field(min_length=1, max_length=128)
    source_paths: list[str]
    requested_outcome: str = Field(min_length=1, max_length=10_000)
    explicit_project_type: str | None = None
    mode_request: str | None = None
    explicit_modes: list[str] | None = None
    custom_modes: list[dict[str, Any]] | None = None
    code_lane: str = "local_code"


class FullAIToolchainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lane_id: str
    host_profile: str
    available_tools: list[str] | None = None
    capability: str | None = None
    granted_tools: list[str] = Field(default_factory=list)
    accelerator_profile: str = "cpu"
    enabled_accelerator_plugins: list[str] = Field(default_factory=list)
    accelerator_memory_budget_percent: int = Field(default=80, ge=1, le=95)
    accelerator_temperature_limit_c: int | None = Field(default=None, ge=30, le=110)
    project_id: str = Field(default="UNBOUND_PROJECT", min_length=1, max_length=128)
    task_id: str = Field(default="UNBOUND_TASK", min_length=1, max_length=192)


class BiggerUniverseProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    database: Path
    project_id: str = Field(min_length=1, max_length=128)
    project_root_identity_sha256: str = Field(pattern=r"^[A-F0-9]{64}$")
    universe_head_sha256: str = Field(pattern=r"^[A-F0-9]{64}$")
    pointer_generation: int = Field(ge=0)
    mini_brains: list[dict[str, Any]]


def run_formula_engine(request: FormulaEngineRequest) -> dict[str, Any]:
    """Compile, and optionally route, one bounded ENV/UOP formula."""

    compiled = compile_env_uop_formula(
        request.mode_governance,
        request.execution_budget,
        sdk_binding_sha256=request.sdk_binding_sha256,
    )
    routed: dict[str, Any] | None = None
    if request.route is not None:
        routed = route_env_uop_operator(
            compiled,
            sdk_binding_sha256=request.sdk_binding_sha256,
            **request.route,
        )
    core = {
        "schema": FORMULA_ENGINE_WORKFLOW_SCHEMA,
        "status": "PASS",
        "compiled_formula_sha256": compiled["compiled_formula_sha256"],
        "route_receipt": routed,
        "workflow_owner": "FORMULA_ENGINE",
        "mode_is_owner": False,
        "operator_effect_executed": routed is not None,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def run_brain_scaling(request: BrainScalingRequest) -> dict[str, Any]:
    """Select a deterministic bounded indexed slice; never train or merge."""

    ordered = sorted(
        request.slices,
        key=lambda row: (-row.priority, row.content_id, row.content_sha256),
    )
    selected: list[dict[str, Any]] = []
    used = 0
    for row in ordered:
        if len(selected) >= request.max_slices:
            break
        if used + row.token_count > request.token_budget:
            continue
        selected.append(row.model_dump(mode="json"))
        used += row.token_count
    core = {
        "schema": BRAIN_SCALING_WORKFLOW_SCHEMA,
        "status": "PASS",
        "authority_id": request.authority_id,
        "selected_slices": selected,
        "selected_count": len(selected),
        "available_count": len(request.slices),
        "used_tokens": used,
        "token_budget": request.token_budget,
        "bounded_indexed_slicing": True,
        "model_training_performed": False,
        "authority_merged": False,
        "raw_payload_copied": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


_RECIPE_LANES: dict[KnownProjectType, tuple[str, ...]] = {
    "CODE": (
        "local_code",
        "github_code",
        "analysis",
        "plan",
        "artifacts",
        "chat_lineage",
    ),
    "DATA": (
        "data_excel",
        "sqlite_brain",
        "analysis",
        "plan",
        "artifacts",
        "chat_lineage",
    ),
    "DOCUMENT": (
        "docs",
        "ppt",
        "pdf_ocr",
        "images_ocr",
        "artifacts",
        "chat_lineage",
    ),
    "RESEARCH": (
        "research",
        "brain_loader",
        "analysis",
        "docs",
        "plan",
        "chat_lineage",
    ),
    "MIXED": CANONICAL_LANE_IDS,
}


def _infer_project_type(request: ProjectRecipeRequest) -> ProjectType:
    if request.explicit_project_type is not None:
        exact = request.explicit_project_type.strip().upper()
        if exact in _RECIPE_LANES:
            return exact
        custom_value = exact.removeprefix("CUSTOM:")
        slug = "_".join(
            part for part in re.split(r"[^A-Z0-9]+", custom_value) if part
        )
        if not slug:
            raise ValueError("Explicit project type must contain a visible identifier.")
        return f"CUSTOM:{slug}"
    suffixes = {Path(value).suffix.casefold() for value in request.source_paths}
    text = request.requested_outcome.casefold()
    classes: set[KnownProjectType] = set()
    if suffixes & {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".cs"}:
        classes.add("CODE")
    if suffixes & {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".sqlite", ".db"}:
        classes.add("DATA")
    if suffixes & {".docx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg"}:
        classes.add("DOCUMENT")
    if any(value in text for value in ("research", "prior art", "study", "evidence")):
        classes.add("RESEARCH")
    if not classes:
        return "MIXED"
    return next(iter(classes)) if len(classes) == 1 else "MIXED"


def compile_project_recipe(request: ProjectRecipeRequest) -> dict[str, Any]:
    """Compile a project-type recipe without creating a stored lane or PV."""

    project_type = _infer_project_type(request)
    lanes = list(
        _RECIPE_LANES.get(
            cast(KnownProjectType, project_type),
            CANONICAL_LANE_IDS,
        )
    )
    require(
        set(lanes) <= set(CANONICAL_LANE_IDS) and "chat_lineage" in lanes,
        "PROJECT_RECIPE_LANE_SET_INVALID",
        "A project recipe must use only current lanes and include Chat Lineage.",
        status="MISMATCH",
    )
    recipe_core = {
        "schema": PROJECT_RECIPE_WORKFLOW_SCHEMA,
        "status": "PASS",
        "project_id": request.project_id,
        "project_type": project_type,
        "canonical_lanes": lanes,
        "stages": [
            "SOURCE_INTAKE",
            "ENV_UOP_CLASSIFY",
            "PROJECT_EXECUTION_PROFILE",
            "LANE_EXECUTION",
            "VALIDATION",
            "DELTA_APPEND",
        ],
        "recipe_is_mode": False,
        "stored_lane_created": False,
        "candidate_created": False,
        "hil_inferred": False,
    }
    recipe = {
        **recipe_core,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(recipe_core)),
    }
    mode_classification = None
    if request.mode_request or request.explicit_modes or request.custom_modes:
        mode_classification = classify_operating_modes(
            request.mode_request or request.requested_outcome,
            explicit_modes=request.explicit_modes,
            code_lane=request.code_lane,
            custom_modes=request.custom_modes,
        )
    execution_profile = compile_project_execution_profile(
        project_recipe=recipe,
        mode_classification=mode_classification,
    )
    final_core = {
        **recipe,
        "mode_classification": mode_classification,
        "project_execution_profile": execution_profile,
        "recipe_and_mode_synchronized": mode_classification is not None,
        "receipt_sha256": recipe["receipt_sha256"],
    }
    return {
        **final_core,
        "workflow_receipt_sha256": sha256_bytes(canonical_json_bytes(final_core)),
    }


def run_full_ai_toolchain(request: FullAIToolchainRequest) -> dict[str, Any]:
    """Resolve one conditional lane toolchain through the existing action plane."""

    resolved = resolve_lane_toolchain(
        lane_id=request.lane_id,
        host_profile=request.host_profile,
        available_tools=(
            set(request.available_tools)
            if request.available_tools is not None
            else None
        ),
        accelerator_profile=request.accelerator_profile,
        enabled_accelerator_plugins=request.enabled_accelerator_plugins,
        accelerator_memory_budget_percent=request.accelerator_memory_budget_percent,
        accelerator_temperature_limit_c=request.accelerator_temperature_limit_c,
    )
    ecosystem_resolution: dict[str, Any] | None = None
    if request.capability:
        for action_class in resolved["action_classes"]:
            candidate = resolve_ecosystem_adapters(
                capability=request.capability,
                action_class=action_class,
                lane_id=request.lane_id,
                project_id=request.project_id,
                task_id=request.task_id,
                granted_tools=request.granted_tools,
                available_tools=resolved["runnable_tools"],
            )
            if candidate["candidate_tools"]:
                ecosystem_resolution = candidate
                break
    core = {
        "schema": FULL_AI_TOOLCHAIN_WORKFLOW_SCHEMA,
        "status": (
            "PASS" if resolved.get("status") == "PASS" else "BLOCKED"
        ),
        "lane_id": request.lane_id,
        "host_profile": request.host_profile,
        "resolved_toolchain": resolved,
        "ecosystem_resolution": ecosystem_resolution,
        "project_id": request.project_id,
        "task_id": request.task_id,
        "conditional_dispatch": True,
        "runtime_route_ready": resolved.get("status") == "PASS",
        "run_everything": False,
        "one_ecosystem_adapter_maximum": True,
        "mode_is_owner": False,
        "chatgpt_plane_mixed": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def register_bigger_universe_project(
    request: BiggerUniverseProjectRequest,
) -> dict[str, Any]:
    """Register hash-only project mini-brains in the distinct federation owner."""

    governance = ConnectorGovernance(request.database)
    registered = governance.register_universe_project(
        project_id=request.project_id,
        project_root_identity_sha256=request.project_root_identity_sha256,
        universe_head_sha256=request.universe_head_sha256,
        pointer_generation=request.pointer_generation,
        mini_brains=request.mini_brains,
    )
    core = {
        "schema": BIGGER_UNIVERSE_WORKFLOW_SCHEMA,
        "status": "PASS",
        "project_registration": registered,
        "workflow_owner": "BIGGER_UNIVERSE_FEDERATION",
        "per_project_universe_replaced": False,
        "project_truth_merged": False,
        "raw_payload_copied": False,
        "cross_project_edge_created": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = [
    "BIGGER_UNIVERSE_WORKFLOW_SCHEMA",
    "BRAIN_SCALING_WORKFLOW_SCHEMA",
    "FORMULA_ENGINE_WORKFLOW_SCHEMA",
    "FULL_AI_TOOLCHAIN_WORKFLOW_SCHEMA",
    "PROJECT_RECIPE_WORKFLOW_SCHEMA",
    "BiggerUniverseProjectRequest",
    "BrainScalingRequest",
    "BrainSlice",
    "FormulaEngineRequest",
    "FullAIToolchainRequest",
    "ProjectRecipeRequest",
    "compile_project_recipe",
    "register_bigger_universe_project",
    "run_brain_scaling",
    "run_formula_engine",
    "run_full_ai_toolchain",
]
