"""Retained deterministic mode intersections over current authority/sector owners."""

from __future__ import annotations

import re
from typing import Any, cast

from .errors import EvidenceLaneError
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import LANE_REGISTRY, resolve_lane_id
from .mode_governance import govern_mode_selection

MODE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "D",
        "name": "discussion",
        "aliases": ("discussion", "meeting", "conversation"),
        "lanes": ("chat_lineage",),
    },
    {
        "id": "AL",
        "name": "analysis",
        "aliases": ("analysis", "analyze", "forensic", "audit"),
        "lanes": ("sources", "receipts"),
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
        "lanes": ("receipts", "artifacts"),
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
        "name": "selected structured source",
        "aliases": ("selected sqlite", "sqlite source", "structured source"),
        "lanes": ("custom",),
    },
    {
        "id": "ENG",
        "name": "source intake",
        "aliases": ("source intake", "source registration"),
        "lanes": ("sources",),
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


def _resolve_explicit(
    values: list[str], custom_aliases: dict[str, str] | None = None
) -> list[str]:
    custom = custom_aliases or {}
    selected: list[str] = []
    for raw in values:
        normalized = _normalize_explicit(raw)
        if normalized in {**_EXPLICIT_ALIASES, **custom}:
            mode_id = {**_EXPLICIT_ALIASES, **custom}[normalized]
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
                mode_id = {**_EXPLICIT_ALIASES, **custom}[part]
            except KeyError as exc:
                raise EvidenceLaneError(
                    "MODE_SELECTION_INVALID",
                    "An unknown mode requires one explicit custom-mode brief and lane schema.",
                    status="BLOCKED",
                    details={
                        "provided": raw,
                        "unresolved": part,
                        "supported": [
                            f"{item['id']} {item['name']}" for item in MODE_DEFINITIONS
                        ],
                        "custom_mode_required_fields": [
                            "name",
                            "brief",
                            "lanes",
                            "dependency_policy",
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
    custom_modes: list[dict[str, Any]] | None = None,
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
    custom_definitions: dict[str, dict[str, Any]] = {}
    custom_aliases: dict[str, str] = {}
    custom_order: list[str] = []
    for raw in custom_modes or []:
        name = str(raw.get("name") or "").strip()
        brief = str(raw.get("brief") or "").strip()
        raw_lanes = raw.get("lanes") or ["custom"]
        dependency_policy = raw.get("dependency_policy")
        if not isinstance(raw_lanes, list):
            raise EvidenceLaneError(
                "CUSTOM_MODE_LANES_INVALID",
                "A custom mode lane schema must be an ordered list.",
                status="BLOCKED",
            )
        if not name or len(brief) < 12:
            raise EvidenceLaneError(
                "CUSTOM_MODE_BRIEF_REQUIRED",
                "A custom mode needs a name and a concrete brief of at least 12 characters.",
                status="BLOCKED",
                details={"name": name, "minimum_brief_characters": 12},
            )
        if not (
            isinstance(dependency_policy, dict)
            and set(dependency_policy) <= {"on_missing", "requires"}
            and isinstance(dependency_policy.get("requires", []), list)
            and str(dependency_policy.get("on_missing") or "BLOCK").upper() == "BLOCK"
        ):
            raise EvidenceLaneError(
                "CUSTOM_MODE_DEPENDENCY_POLICY_REQUIRED",
                "Custom project work requires an explicit dependency list and fail-closed BLOCK policy.",
                status="BLOCKED",
                details={"name": name},
            )
        normalized_name = _normalize_explicit(name)
        slug = re.sub(r"[^a-z0-9]+", "-", normalized_name).strip("-")[:40]
        mode_id = (
            "X:"
            + slug
            + ":"
            + sha256_bytes(
                canonical_json_bytes(
                    {
                        "name": name,
                        "brief": brief,
                        "lanes": raw_lanes,
                        "dependency_policy": dependency_policy,
                    }
                )
            )[:8]
        )
        lanes: list[str] = []
        for alias in raw_lanes:
            try:
                lane_id = resolve_lane_id(str(alias), code_mode=code_lane)
            except ValueError as exc:
                raise EvidenceLaneError(
                    "CUSTOM_MODE_LANE_INVALID",
                    "A custom mode references an unknown canonical lane.",
                    status="BLOCKED",
                    details={"mode": name, "lane": str(alias)},
                ) from exc
            if lane_id not in lanes:
                lanes.append(lane_id)
        definition = {
            "id": mode_id,
            "name": name,
            "aliases": (name, mode_id),
            "lanes": tuple(lanes),
            "brief": brief,
            "dependency_policy": dependency_policy,
            "custom": True,
        }
        custom_definitions[mode_id] = definition
        custom_aliases[normalized_name] = mode_id
        custom_aliases[_normalize_explicit(mode_id)] = mode_id
        custom_order.append(mode_id)
    selected = (
        _resolve_explicit(explicit_modes, custom_aliases)
        if explicit_modes
        else _infer_from_request(exact_request)
    )
    if not explicit_modes:
        for mode_id in custom_order:
            name = str(custom_definitions[mode_id]["name"])
            if (
                re.search(
                    rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])",
                    exact_request.lower(),
                )
                and mode_id not in selected
            ):
                selected.append(mode_id)
        if not selected and len(custom_order) == 1:
            selected.append(custom_order[0])
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

    lanes = ["chat_lineage"]
    selected_details: list[dict[str, Any]] = []
    for mode_id in selected:
        definition = cast(
            dict[str, Any], dict(_BY_ID.get(mode_id) or custom_definitions[mode_id])
        )
        definition_lanes = cast(tuple[str, ...] | list[str], definition["lanes"])
        mapped_lanes = [
            code_lane if lane == "$CODE_LANE" else lane for lane in definition_lanes
        ]
        for lane in mapped_lanes:
            if lane not in lanes:
                lanes.append(lane)
        detail = {
            "id": mode_id,
            "name": definition["name"],
            "canonical_lanes": mapped_lanes,
            "custom": bool(definition.get("custom")),
        }
        if definition.get("custom"):
            detail["schema"] = {
                "brief": definition["brief"],
                "brief_sha256": sha256_bytes(str(definition["brief"]).encode("utf-8")),
                "ordered_lanes": mapped_lanes,
                "dependency_policy": definition["dependency_policy"],
                "authority": "USER_EXPLICIT_SESSION_SIDECAR",
            }
        selected_details.append(detail)

    lane_routes = [
        {
            "canonical_lane_id": lane_id,
            "display_label": LANE_REGISTRY[lane_id].display_label,
            "command": f"/{LANE_REGISTRY[lane_id].command}",
        }
        for lane_id in lanes
    ]
    result = {
        "status": "PASS",
        "schema": "evidence-lane.mode-classification.v4",
        "request": exact_request,
        "mode_namespace_authority": (
            "CURRENT_LOCKED_ENV_PLUS_EXPLICIT_CUSTOM_SCHEMA"
            if custom_definitions
            else "CURRENT_LOCKED_ENV_READ_ONLY"
        ),
        "selected_modes": selected_details,
        "mode_intersection": "+".join(selected),
        "intersection": len(selected) > 1,
        "canonical_lanes": lanes,
        "lane_routes": lane_routes,
        "chat_lineage": {
            "canonical_lane_id": "chat_lineage",
            "included": True,
            "automatic_append_write_lane": False,
            "private_reasoning_excluded": True,
        },
        "custom_mode_schemas": [
            item["schema"] | {"id": item["id"], "name": item["name"]}
            for item in selected_details
            if item.get("custom")
        ],
        "code_recursive_policy": (["D", "PL", "CD", "VAL"] if "CD" in selected else []),
        "lifecycle_effect": "NONE",
        "pointer_moved": False,
        "candidate_created": False,
        "task_classified": False,
        "return_to_prior_lifecycle_position": True,
    }
    result["mode_governance"] = govern_mode_selection(
        selected_details,
        request=exact_request,
        selection_source=(
            "PLUGIN_OR_API_EXPLICIT_SELECTION" if explicit_modes else "PROMPT_INFERENCE"
        ),
    )
    return result
