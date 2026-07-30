"""Deterministic ENV15 mode intersections mapped to canonical Evidence Lanes."""

from __future__ import annotations

import re
from typing import Any

from .errors import EvidenceLaneError
from .lanes import LANE_REGISTRY

MODE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "D",
        "name": "discussion",
        "aliases": ("discussion", "meeting", "conversation"),
        "lanes": ("discussion",),
    },
    {
        "id": "AL",
        "name": "analysis",
        "aliases": ("analysis", "analyze", "forensic", "audit"),
        "lanes": ("analysis",),
    },
    {
        "id": "PL",
        "name": "planning",
        "aliases": ("planning", "plan", "roadmap"),
        "lanes": ("plan",),
    },
    {
        "id": "CD",
        "name": "code",
        "aliases": ("code", "implement", "implementation", "build", "fix"),
        "lanes": ("$CODE_LANE",),
    },
    {
        "id": "OP",
        "name": "output",
        "aliases": ("output", "deliverable", "render"),
        "lanes": ("artifacts",),
    },
    {
        "id": "VAL",
        "name": "validation",
        "aliases": ("validation", "validate", "verification", "verify", "test", "qa"),
        "lanes": ("analysis", "artifacts"),
    },
    {
        "id": "RS",
        "name": "research",
        "aliases": ("research", "prior art", "sources"),
        "lanes": ("research",),
    },
    {
        "id": "JD",
        "name": "job/JD",
        "aliases": ("job", "jd", "job description"),
        "lanes": ("docs", "custom"),
    },
    {
        "id": "XL",
        "name": "Excel",
        "aliases": ("excel", "spreadsheet", "csv", "workbook"),
        "lanes": ("data_excel",),
    },
    {
        "id": "PPT",
        "name": "presentation",
        "aliases": ("presentation", "ppt", "slides", "powerpoint"),
        "lanes": ("ppt",),
    },
    {
        "id": "DOC",
        "name": "document",
        "aliases": ("document", "docs", "docx"),
        "lanes": ("docs",),
    },
    {
        "id": "PB",
        "name": "project brain builder",
        "aliases": ("project brain", "brain builder", "pv candidate loader"),
        "lanes": ("brain_loader", "sqlite_brain"),
    },
    {
        "id": "ENG",
        "name": "project engulf",
        "aliases": ("project engulf", "engulf"),
        "lanes": ("project_engulf",),
    },
    {
        "id": "CE",
        "name": "clean exit",
        "aliases": ("clean exit", "exit"),
        "lanes": ("chat_lineage",),
    },
    {
        "id": "RCV",
        "name": "recovery",
        "aliases": ("recovery", "recover", "resume"),
        "lanes": ("chat_lineage", "artifacts"),
    },
    {
        "id": "X",
        "name": "custom user-defined mode",
        "aliases": ("custom", "user-defined", "user defined"),
        "lanes": ("custom",),
    },
)

_BY_ID = {definition["id"]: definition for definition in MODE_DEFINITIONS}
_EXPLICIT_ALIASES = {
    alias.lower(): definition["id"]
    for definition in MODE_DEFINITIONS
    for alias in (
        definition["id"],
        definition["name"],
        *definition["aliases"],
    )
}


def _normalize_explicit(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def _resolve_explicit(values: list[str]) -> list[str]:
    selected: list[str] = []
    for raw in values:
        normalized = _normalize_explicit(raw)
        if normalized in _EXPLICIT_ALIASES:
            mode_id = _EXPLICIT_ALIASES[normalized]
            if mode_id not in selected:
                selected.append(mode_id)
            continue
        parts = [
            _normalize_explicit(part)
            for part in re.split(r"\s*(?:,|\+|&|->|>)\s*", raw)
            if part.strip()
        ]
        for part in parts:
            try:
                mode_id = _EXPLICIT_ALIASES[part]
            except KeyError as exc:
                raise EvidenceLaneError(
                    "MODE_SELECTION_INVALID",
                    "An explicit operating mode is outside the locked ENV15 namespace.",
                    status="BLOCKED",
                    details={
                        "provided": raw,
                        "unresolved": part,
                        "supported": [
                            f"{item['id']} {item['name']}" for item in MODE_DEFINITIONS
                        ],
                    },
                ) from exc
            if mode_id not in selected:
                selected.append(mode_id)
    return selected


def _infer_from_request(request: str) -> list[str]:
    lowered = request.lower()
    matches: list[tuple[int, int, str]] = []
    for definition_index, definition in enumerate(MODE_DEFINITIONS):
        for alias in definition["aliases"]:
            match = re.search(
                rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])",
                lowered,
            )
            if match:
                matches.append((match.start(), definition_index, definition["id"]))
                break
    if re.search(r"(?<![a-z0-9])(forensic|audit)(?![a-z0-9])", lowered):
        audit_position = min(
            match.start()
            for match in re.finditer(
                r"(?<![a-z0-9])(forensic|audit)(?![a-z0-9])",
                lowered,
            )
        )
        matches.append((audit_position, len(MODE_DEFINITIONS), "VAL"))
    hil_match = re.search(r"(?<![a-z0-9])hil(?![a-z0-9])", lowered)
    if hil_match:
        matches.append((hil_match.start(), len(MODE_DEFINITIONS) + 1, "VAL"))
    selected: list[str] = []
    for _, _, mode_id in sorted(matches):
        if mode_id not in selected:
            selected.append(mode_id)
    return selected


def classify_operating_modes(
    request: str,
    *,
    explicit_modes: list[str] | None,
    code_lane: str,
) -> dict[str, Any]:
    """Classify one or more ordered mode namespaces without lifecycle mutation."""

    if code_lane not in {"github_code", "local_code"}:
        raise EvidenceLaneError(
            "MODE_CODE_LANE_INVALID",
            "Mode routing requires the project's exact Git or local code lane.",
            status="MISMATCH",
            details={"code_lane": code_lane},
        )
    exact_request = request.strip()
    selected = (
        _resolve_explicit(explicit_modes)
        if explicit_modes
        else _infer_from_request(exact_request)
    )
    if not selected:
        raise EvidenceLaneError(
            "MODE_SELECTION_REQUIRED",
            "No locked operating mode could be classified. Name one or more "
            "explicit modes; no default mode is inferred.",
            status="BLOCKED",
            details={
                "supported": [
                    {"id": item["id"], "name": item["name"]}
                    for item in MODE_DEFINITIONS
                ]
            },
        )

    lanes = ["mode", "chat_lineage"]
    selected_details: list[dict[str, Any]] = []
    for mode_id in selected:
        definition = _BY_ID[mode_id]
        mapped_lanes = [
            code_lane if lane == "$CODE_LANE" else lane for lane in definition["lanes"]
        ]
        for lane in mapped_lanes:
            if lane not in lanes:
                lanes.append(lane)
        selected_details.append(
            {
                "id": mode_id,
                "name": definition["name"],
                "canonical_lanes": mapped_lanes,
            }
        )

    lane_routes = [
        {
            "canonical_lane_id": lane_id,
            "display_label": LANE_REGISTRY[lane_id].display_label,
            "command": f"/{LANE_REGISTRY[lane_id].command}",
        }
        for lane_id in lanes
    ]
    return {
        "status": "PASS",
        "schema": "evidence-lane.mode-classification.v1",
        "request": exact_request,
        "mode_namespace_authority": "ENV15_LOCKED_READ_ONLY",
        "selected_modes": selected_details,
        "mode_intersection": "+".join(selected),
        "intersection": len(selected) > 1,
        "canonical_lanes": lanes,
        "lane_routes": lane_routes,
        "chat_lineage": {
            "canonical_lane_id": "chat_lineage",
            "included": True,
            "automatic_append_write_lane": True,
            "private_reasoning_excluded": True,
        },
        "code_recursive_policy": (["D", "PL", "CD", "VAL"] if "CD" in selected else []),
        "lifecycle_effect": "NONE",
        "pointer_moved": False,
        "candidate_created": False,
        "task_classified": False,
        "return_to_prior_lifecycle_position": True,
    }
