"""Deterministic Custom Source Schema compiler and authority mapper.

The compiler accepts declarative JSON only.  It never evaluates imported code,
executes SQL, reads source payload bytes, creates lane databases, builds a PV, or
moves an accepted pointer.  Mapping is limited to metadata already sealed in the
governed Source Intake authority registry.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, cast

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import SECTOR_LANE_IDS, is_named_custom_lane, resolve_lane_id
from .source_authority import (
    _connect,
    initialize_source_authority_registry,
    load_source_batch,
    snapshot_source_authority_registry,
    source_authority_write,
)
from .storage import LaneStore, ProjectStore
from .timeutil import utc_now

CUSTOM_SOURCE_SCHEMA = "evidence-lane.custom-source-schema.v1"
COMPILED_CUSTOM_SOURCE_SCHEMA = "evidence-lane.custom-source-schema.compiled.v1"
CUSTOM_SOURCE_MAPPING_RECEIPT = "evidence-lane.custom-source-schema-mapping.v1"
CUSTOM_SOURCE_SCHEMA_COMPILER_VERSION = "custom-source-schema-compiler-v1"

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SCHEMA_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
_KINDS = frozenset({"directory", "file", "zip"})
_FIELD_TYPES = frozenset(
    {"boolean", "integer", "json", "number", "path", "sha256", "string", "timestamp"}
)
_VALUE_SOURCES = frozenset(
    {
        "literal",
        "schema.id",
        "schema.version",
        "selector.id",
        "occurrence.ordinal",
        "occurrence.supplied_pointer",
        "source.object_id",
        "source.resolved_pointer",
        "source.kind",
        "source.lane_id",
        "source.identity_sha256",
        "source.byte_sha256",
        "source.size_bytes",
        "source.member_count",
        "member.path",
        "member.kind",
        "member.sha256",
        "member.size_bytes",
        "member.policy_state",
        "member.policy_reason",
    }
)
_TRANSFORMS = frozenset(
    {
        "basename",
        "boolean",
        "casefold",
        "dirname",
        "extension",
        "identity",
        "integer",
        "lower",
        "number",
        "posix_path",
        "sha256",
        "strip",
        "upper",
    }
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "acceptance",
        "dependency_policy",
        "description",
        "fields",
        "schema",
        "schema_id",
        "schema_version",
        "selectors",
        "target_lane",
        "title",
    }
)
_SELECTOR_KEYS = frozenset(
    {
        "extensions",
        "lane_ids",
        "member_path_globs",
        "scope",
        "selector_id",
        "source_kinds",
        "source_path_globs",
    }
)
_FIELD_KEYS = frozenset(
    {"default", "literal", "name", "required", "transforms", "type", "value_from"}
)





def _unknown_keys(raw: Mapping[str, Any], allowed: frozenset[str]) -> list[str]:
    return sorted(str(key) for key in raw if key not in allowed)


def _canonical_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8").strip()


def _validate_pattern(value: Any, *, field: str) -> str:
    pattern = str(value or "").strip().replace("\\", "/")
    parts = PurePosixPath(pattern).parts
    require(
        bool(pattern)
        and len(pattern) <= 512
        and "\x00" not in pattern
        and not pattern.startswith(("/", "//"))
        and not re.match(r"^[A-Za-z]:", pattern)
        and ".." not in parts,
        "CUSTOM_SOURCE_SCHEMA_PATTERN_INVALID",
        "Custom Source Schema selectors require bounded relative glob patterns.",
        status="BLOCKED",
        field=field,
        pattern=pattern,
    )
    return pattern


def _string_list(
    value: Any,
    *,
    field: str,
    maximum: int,
    normalizer: Any = str,
) -> list[str]:
    require(
        value is None or isinstance(value, list),
        "CUSTOM_SOURCE_SCHEMA_LIST_REQUIRED",
        "A Custom Source Schema list field is not an ordered JSON array.",
        status="BLOCKED",
        field=field,
    )
    result: list[str] = []
    for raw in value or []:
        normalized = str(normalizer(raw)).strip()
        require(
            bool(normalized),
            "CUSTOM_SOURCE_SCHEMA_LIST_VALUE_INVALID",
            "A Custom Source Schema list contains an empty value.",
            status="BLOCKED",
            field=field,
        )
        if normalized not in result:
            result.append(normalized)
    require(
        len(result) <= maximum,
        "CUSTOM_SOURCE_SCHEMA_LIST_LIMIT_EXCEEDED",
        "A Custom Source Schema list exceeds its governed item limit.",
        status="BLOCKED",
        field=field,
        maximum=maximum,
    )
    return result


def _compile_selector(raw: Any, ordinal: int) -> dict[str, Any]:
    require(
        isinstance(raw, dict),
        "CUSTOM_SOURCE_SCHEMA_SELECTOR_INVALID",
        "Every Custom Source Schema selector must be a JSON object.",
        status="BLOCKED",
        ordinal=ordinal,
    )
    unknown = _unknown_keys(raw, _SELECTOR_KEYS)
    require(
        not unknown,
        "CUSTOM_SOURCE_SCHEMA_SELECTOR_KEY_UNKNOWN",
        "A selector contains an unsupported key.",
        status="BLOCKED",
        ordinal=ordinal,
        unknown_keys=unknown,
    )
    selector_id = str(raw.get("selector_id") or "").strip().casefold()
    require(
        bool(_IDENTIFIER.fullmatch(selector_id)),
        "CUSTOM_SOURCE_SCHEMA_SELECTOR_ID_INVALID",
        "Every selector needs a stable lowercase identifier.",
        status="BLOCKED",
        ordinal=ordinal,
        selector_id=selector_id,
    )
    source_kinds = _string_list(
        raw.get("source_kinds"),
        field=f"selectors[{ordinal}].source_kinds",
        maximum=len(_KINDS),
        normalizer=lambda value: str(value).casefold(),
    )
    require(
        set(source_kinds) <= _KINDS,
        "CUSTOM_SOURCE_SCHEMA_SOURCE_KIND_INVALID",
        "A selector references an unsupported source kind.",
        status="BLOCKED",
        selector_id=selector_id,
        source_kinds=source_kinds,
        supported=sorted(_KINDS),
    )
    lane_ids: list[str] = []
    for alias in _string_list(
        raw.get("lane_ids"),
        field=f"selectors[{ordinal}].lane_ids",
        maximum=len(SECTOR_LANE_IDS),
    ):
        try:
            lane_id = resolve_lane_id(alias)
        except ValueError as error:
            raise EvidenceLaneError(
                "CUSTOM_SOURCE_SCHEMA_LANE_INVALID",
                "A selector references an unknown canonical lane.",
                status="BLOCKED",
                details={"selector_id": selector_id, "lane": alias},
            ) from error
        require(lane_id in SECTOR_LANE_IDS or is_named_custom_lane(lane_id), "CUSTOM_SOURCE_SCHEMA_LANE_INVALID",
                "A source selector must name a retained sector, not an authority.", status="BLOCKED")
        if lane_id not in lane_ids:
            lane_ids.append(lane_id)
    source_path_globs = [
        _validate_pattern(value, field=f"selectors[{ordinal}].source_path_globs")
        for value in _string_list(
            raw.get("source_path_globs"),
            field=f"selectors[{ordinal}].source_path_globs",
            maximum=64,
        )
    ]
    member_path_globs = [
        _validate_pattern(value, field=f"selectors[{ordinal}].member_path_globs")
        for value in _string_list(
            raw.get("member_path_globs"),
            field=f"selectors[{ordinal}].member_path_globs",
            maximum=64,
        )
    ]
    extensions = _string_list(
        raw.get("extensions"),
        field=f"selectors[{ordinal}].extensions",
        maximum=64,
        normalizer=lambda value: (
            str(value).casefold()
            if str(value).startswith(".")
            else f".{str(value).casefold()}"
        ),
    )
    require(
        all(re.fullmatch(r"\.[a-z0-9][a-z0-9_.+-]{0,31}", item) for item in extensions),
        "CUSTOM_SOURCE_SCHEMA_EXTENSION_INVALID",
        "A selector extension is not a bounded literal suffix.",
        status="BLOCKED",
        selector_id=selector_id,
        extensions=extensions,
    )
    scope = str(raw.get("scope") or "SOURCE").strip().upper()
    require(
        scope in {"MEMBER", "SOURCE"},
        "CUSTOM_SOURCE_SCHEMA_SELECTOR_SCOPE_INVALID",
        "A selector scope must be SOURCE or MEMBER.",
        status="BLOCKED",
        selector_id=selector_id,
        scope=scope,
    )
    return {
        "ordinal": ordinal,
        "selector_id": selector_id,
        "scope": scope,
        "source_kinds": source_kinds,
        "lane_ids": lane_ids,
        "source_path_globs": source_path_globs,
        "member_path_globs": member_path_globs,
        "extensions": extensions,
    }


def _compile_field(raw: Any, ordinal: int) -> dict[str, Any]:
    require(
        isinstance(raw, dict),
        "CUSTOM_SOURCE_SCHEMA_FIELD_INVALID",
        "Every Custom Source Schema field must be a JSON object.",
        status="BLOCKED",
        ordinal=ordinal,
    )
    unknown = _unknown_keys(raw, _FIELD_KEYS)
    require(
        not unknown,
        "CUSTOM_SOURCE_SCHEMA_FIELD_KEY_UNKNOWN",
        "A field contains an unsupported key.",
        status="BLOCKED",
        ordinal=ordinal,
        unknown_keys=unknown,
    )
    name = str(raw.get("name") or "").strip().casefold()
    field_type = str(raw.get("type") or "string").strip().casefold()
    value_from = str(raw.get("value_from") or "").strip()
    require(
        bool(_IDENTIFIER.fullmatch(name)),
        "CUSTOM_SOURCE_SCHEMA_FIELD_NAME_INVALID",
        "Every field needs a stable lowercase identifier.",
        status="BLOCKED",
        ordinal=ordinal,
        field_name=name,
    )
    require(
        field_type in _FIELD_TYPES,
        "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_INVALID",
        "A field type is unsupported.",
        status="BLOCKED",
        field_name=name,
        field_type=field_type,
        supported=sorted(_FIELD_TYPES),
    )
    require(
        value_from in _VALUE_SOURCES,
        "CUSTOM_SOURCE_SCHEMA_VALUE_SOURCE_INVALID",
        "A field value_from source is unsupported.",
        status="BLOCKED",
        field_name=name,
        value_from=value_from,
    )
    transforms = _string_list(
        raw.get("transforms") or ["identity"],
        field=f"fields[{ordinal}].transforms",
        maximum=8,
        normalizer=lambda value: str(value).casefold(),
    )
    require(
        set(transforms) <= _TRANSFORMS,
        "CUSTOM_SOURCE_SCHEMA_TRANSFORM_INVALID",
        "A field requests a transform outside the non-executable allow-list.",
        status="BLOCKED",
        field_name=name,
        transforms=transforms,
        supported=sorted(_TRANSFORMS),
    )
    require(
        value_from != "literal" or "literal" in raw,
        "CUSTOM_SOURCE_SCHEMA_LITERAL_REQUIRED",
        "A literal field must carry its exact literal value.",
        status="BLOCKED",
        field_name=name,
    )
    compiled = {
        "ordinal": ordinal,
        "name": name,
        "type": field_type,
        "required": bool(raw.get("required", False)),
        "value_from": value_from,
        "transforms": transforms,
        "has_default": "default" in raw,
        "default": raw.get("default"),
    }
    if value_from == "literal":
        compiled["literal"] = raw.get("literal")
    return compiled


def compile_custom_source_schema(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and deterministically compile one declarative source schema."""

    require(
        isinstance(definition, Mapping),
        "CUSTOM_SOURCE_SCHEMA_DEFINITION_INVALID",
        "Custom Source Schema input must be a JSON object.",
        status="BLOCKED",
    )
    unknown = _unknown_keys(definition, _TOP_LEVEL_KEYS)
    require(
        not unknown,
        "CUSTOM_SOURCE_SCHEMA_KEY_UNKNOWN",
        "The Custom Source Schema contains unsupported top-level keys.",
        status="BLOCKED",
        unknown_keys=unknown,
    )
    declared_schema = str(definition.get("schema") or CUSTOM_SOURCE_SCHEMA)
    require(
        declared_schema == CUSTOM_SOURCE_SCHEMA,
        "CUSTOM_SOURCE_SCHEMA_VERSION_UNSUPPORTED",
        "The requested Custom Source Schema contract is unsupported.",
        status="BLOCKED",
        declared_schema=declared_schema,
        supported_schema=CUSTOM_SOURCE_SCHEMA,
    )
    schema_id = str(definition.get("schema_id") or "").strip().casefold()
    schema_version = definition.get("schema_version")
    title = str(definition.get("title") or schema_id).strip()
    description = str(definition.get("description") or "").strip()
    require(
        bool(_SCHEMA_ID.fullmatch(schema_id)),
        "CUSTOM_SOURCE_SCHEMA_ID_INVALID",
        "A Custom Source Schema needs a stable lowercase schema_id.",
        status="BLOCKED",
        schema_id=schema_id,
    )
    require(
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and 1 <= schema_version <= 9999,
        "CUSTOM_SOURCE_SCHEMA_REVISION_INVALID",
        "schema_version must be an integer from 1 through 9999.",
        status="BLOCKED",
        schema_version=schema_version,
    )
    require(
        1 <= len(title) <= 160 and 12 <= len(description) <= 2000,
        "CUSTOM_SOURCE_SCHEMA_DESCRIPTION_INVALID",
        "The schema needs a bounded title and concrete description.",
        status="BLOCKED",
        title_length=len(title),
        description_length=len(description),
    )
    try:
        target_lane_id = resolve_lane_id(str(definition.get("target_lane") or "custom"))
    except ValueError as error:
        raise EvidenceLaneError(
            "CUSTOM_SOURCE_SCHEMA_TARGET_LANE_INVALID",
            "The schema target_lane is not a canonical lane.",
            status="BLOCKED",
            details={"target_lane": definition.get("target_lane")},
        ) from error
    require(target_lane_id in SECTOR_LANE_IDS or is_named_custom_lane(target_lane_id), "CUSTOM_SOURCE_SCHEMA_TARGET_LANE_INVALID",
            "Custom source schemas target retained sectors, not project authorities.", status="BLOCKED")
    raw_selectors = definition.get("selectors")
    raw_fields = definition.get("fields")
    require(
        isinstance(raw_selectors, list) and 1 <= len(raw_selectors) <= 64,
        "CUSTOM_SOURCE_SCHEMA_SELECTORS_REQUIRED",
        "The schema requires one through sixty-four ordered selectors.",
        status="BLOCKED",
    )
    require(
        isinstance(raw_fields, list) and 1 <= len(raw_fields) <= 128,
        "CUSTOM_SOURCE_SCHEMA_FIELDS_REQUIRED",
        "The schema requires one through one-hundred-twenty-eight ordered fields.",
        status="BLOCKED",
    )
    selector_input = cast(list[Any], raw_selectors)
    field_input = cast(list[Any], raw_fields)
    selectors = [
        _compile_selector(raw, ordinal)
        for ordinal, raw in enumerate(selector_input, start=1)
    ]
    fields = [
        _compile_field(raw, ordinal) for ordinal, raw in enumerate(field_input, start=1)
    ]
    selector_ids = [row["selector_id"] for row in selectors]
    field_names = [row["name"] for row in fields]
    require(
        len(selector_ids) == len(set(selector_ids)),
        "CUSTOM_SOURCE_SCHEMA_SELECTOR_DUPLICATE",
        "Selector identifiers must be unique within a schema.",
        status="BLOCKED",
        selector_ids=selector_ids,
    )
    require(
        len(field_names) == len(set(field_names)),
        "CUSTOM_SOURCE_SCHEMA_FIELD_DUPLICATE",
        "Field identifiers must be unique within a schema.",
        status="BLOCKED",
        field_names=field_names,
    )

    dependency_policy = definition.get("dependency_policy") or {}
    require(
        isinstance(dependency_policy, dict)
        and set(dependency_policy) <= {"on_missing", "requires"},
        "CUSTOM_SOURCE_SCHEMA_DEPENDENCY_POLICY_INVALID",
        "dependency_policy supports only exact requires pins and BLOCK on missing.",
        status="BLOCKED",
    )
    on_missing = str(dependency_policy.get("on_missing") or "BLOCK").upper()
    require(
        on_missing == "BLOCK",
        "CUSTOM_SOURCE_SCHEMA_DEPENDENCY_POLICY_UNSAFE",
        "Missing Custom Source Schema dependencies must fail closed.",
        status="BLOCKED",
        on_missing=on_missing,
    )
    dependencies: list[dict[str, Any]] = []
    raw_dependencies = dependency_policy.get("requires") or []
    require(
        isinstance(raw_dependencies, list) and len(raw_dependencies) <= 32,
        "CUSTOM_SOURCE_SCHEMA_DEPENDENCIES_INVALID",
        "Schema dependencies must be a bounded ordered array.",
        status="BLOCKED",
    )
    for ordinal, raw in enumerate(raw_dependencies, start=1):
        require(
            isinstance(raw, dict)
            and set(raw) == {"schema_id", "schema_sha256", "schema_version"},
            "CUSTOM_SOURCE_SCHEMA_DEPENDENCY_PIN_INVALID",
            "Every dependency must pin exact schema_id, version, and SHA-256.",
            status="BLOCKED",
            ordinal=ordinal,
        )
        dep_id = str(raw["schema_id"]).strip().casefold()
        dep_version = raw["schema_version"]
        dep_sha = str(raw["schema_sha256"]).strip().upper()
        require(
            bool(_SCHEMA_ID.fullmatch(dep_id))
            and isinstance(dep_version, int)
            and not isinstance(dep_version, bool)
            and dep_version > 0
            and bool(_SHA256.fullmatch(dep_sha)),
            "CUSTOM_SOURCE_SCHEMA_DEPENDENCY_PIN_INVALID",
            "A dependency pin is malformed.",
            status="BLOCKED",
            ordinal=ordinal,
        )
        dependencies.append(
            {
                "schema_id": dep_id,
                "schema_version": dep_version,
                "schema_sha256": dep_sha,
            }
        )

    acceptance = definition.get("acceptance") or {}
    require(
        isinstance(acceptance, dict)
        and set(acceptance)
        <= {
            "maximum_mappings",
            "minimum_mappings",
            "on_selector_overlap",
            "on_unmatched_occurrence",
            "require_all_selectors",
        },
        "CUSTOM_SOURCE_SCHEMA_ACCEPTANCE_INVALID",
        "The Custom Source Schema acceptance policy contains unsupported keys.",
        status="BLOCKED",
    )
    minimum = acceptance.get("minimum_mappings", 1)
    maximum = acceptance.get("maximum_mappings", 1_000_000)
    overlap = str(acceptance.get("on_selector_overlap") or "BLOCK").upper()
    unmatched = str(acceptance.get("on_unmatched_occurrence") or "EXCLUDE").upper()
    require(
        isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and isinstance(maximum, int)
        and not isinstance(maximum, bool)
        and 0 <= minimum <= maximum <= 1_000_000,
        "CUSTOM_SOURCE_SCHEMA_MAPPING_BOUNDS_INVALID",
        "Mapping acceptance bounds must satisfy 0 <= minimum <= maximum <= 1,000,000.",
        status="BLOCKED",
        minimum_mappings=minimum,
        maximum_mappings=maximum,
    )
    require(
        overlap in {"BLOCK", "FIRST_MATCH"} and unmatched in {"BLOCK", "EXCLUDE"},
        "CUSTOM_SOURCE_SCHEMA_ACCEPTANCE_STATE_INVALID",
        "Acceptance actions must use the governed BLOCK, EXCLUDE, or FIRST_MATCH states.",
        status="BLOCKED",
        on_selector_overlap=overlap,
        on_unmatched_occurrence=unmatched,
    )
    compiled_core = {
        "schema": COMPILED_CUSTOM_SOURCE_SCHEMA,
        "compiler_version": CUSTOM_SOURCE_SCHEMA_COMPILER_VERSION,
        "schema_id": schema_id,
        "schema_version": schema_version,
        "title": title,
        "description": description,
        "target_lane_id": target_lane_id,
        "selectors": selectors,
        "fields": fields,
        "dependency_policy": {
            "resolution": "EXACT_REGISTERED_SCHEMA_PIN_ONLY",
            "on_missing": "BLOCK",
            "requires": dependencies,
        },
        "acceptance": {
            "minimum_mappings": minimum,
            "maximum_mappings": maximum,
            "on_selector_overlap": overlap,
            "on_unmatched_occurrence": unmatched,
            "require_all_selectors": bool(
                acceptance.get("require_all_selectors", True)
            ),
        },
        "execution_contract": {
            "arbitrary_code_allowed": False,
            "imported_sql_allowed": False,
            "source_payload_read": False,
            "source_payload_copy": False,
            "mapping_authority": "SEALED_SOURCE_REGISTRY_METADATA_ONLY",
        },
    }
    return {
        **compiled_core,
        "schema_sha256": sha256_bytes(canonical_json_bytes(compiled_core)),
    }


def _glob_matches(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    normalized = path.replace("\\", "/")
    folded = normalized.casefold()
    basename = PurePosixPath(normalized).name.casefold()
    return any(
        fnmatch.fnmatchcase(folded, pattern.casefold())
        or fnmatch.fnmatchcase(basename, pattern.casefold())
        for pattern in patterns
    )


def _extension_matches(path: str, extensions: list[str]) -> bool:
    return not extensions or PurePosixPath(path).suffix.casefold() in extensions


def _source_matches(occurrence: Mapping[str, Any], selector: Mapping[str, Any]) -> bool:
    kinds = selector["source_kinds"]
    lanes = selector["lane_ids"]
    pointer = str(occurrence["supplied_pointer"])
    return (
        (not kinds or occurrence["kind"] in kinds)
        and (not lanes or occurrence["lane_id"] in lanes)
        and _glob_matches(pointer, selector["source_path_globs"])
        and (
            selector["scope"] != "SOURCE"
            or _extension_matches(pointer, selector["extensions"])
        )
    )


def _member_matches(member: Mapping[str, Any], selector: Mapping[str, Any]) -> bool:
    path = str(member["member_path"])
    return (
        member["policy_state"] == "INCLUDED"
        and _glob_matches(path, selector["member_path_globs"])
        and _extension_matches(path, selector["extensions"])
    )


def _value_from(
    source: str,
    *,
    field: Mapping[str, Any],
    compiled: Mapping[str, Any],
    selector: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    member: Mapping[str, Any] | None,
) -> Any:
    if source == "literal":
        return field.get("literal")
    values: dict[str, Any] = {
        "schema.id": compiled["schema_id"],
        "schema.version": compiled["schema_version"],
        "selector.id": selector["selector_id"],
        "occurrence.ordinal": occurrence["ordinal"],
        "occurrence.supplied_pointer": occurrence["supplied_pointer"],
        "source.object_id": occurrence["object_id"],
        "source.resolved_pointer": occurrence["resolved_pointer"],
        "source.kind": occurrence["kind"],
        "source.lane_id": occurrence["lane_id"],
        "source.identity_sha256": occurrence["identity_sha256"],
        "source.byte_sha256": occurrence["byte_sha256"],
        "source.size_bytes": occurrence["size_bytes"],
        "source.member_count": occurrence["member_count"],
        "member.path": member.get("member_path") if member else None,
        "member.kind": member.get("member_kind") if member else None,
        "member.sha256": member.get("sha256") if member else None,
        "member.size_bytes": member.get("size_bytes") if member else None,
        "member.policy_state": member.get("policy_state") if member else None,
        "member.policy_reason": member.get("policy_reason") if member else None,
    }
    return values[source]


def _transform(value: Any, transform: str) -> Any:
    if transform == "identity" or value is None:
        return value
    if transform == "integer":
        return int(value)
    if transform == "number":
        return float(value)
    if transform == "boolean":
        if isinstance(value, bool):
            return value
        folded = str(value).strip().casefold()
        require(
            folded in {"0", "1", "false", "no", "true", "yes"},
            "CUSTOM_SOURCE_SCHEMA_BOOLEAN_COERCION_FAILED",
            "A mapped value cannot be coerced to boolean deterministically.",
            status="MISMATCH",
            value=str(value),
        )
        return folded in {"1", "true", "yes"}
    text = str(value)
    if transform == "basename":
        return PurePosixPath(text.replace("\\", "/")).name
    if transform == "dirname":
        return PurePosixPath(text.replace("\\", "/")).parent.as_posix()
    if transform == "extension":
        return PurePosixPath(text.replace("\\", "/")).suffix.casefold()
    if transform == "lower":
        return text.lower()
    if transform == "upper":
        return text.upper()
    if transform == "casefold":
        return text.casefold()
    if transform == "posix_path":
        return text.replace("\\", "/")
    if transform == "sha256":
        return sha256_bytes(text.encode("utf-8"))
    if transform == "strip":
        return text.strip()
    raise AssertionError(f"unreachable transform: {transform}")


def _validate_type(value: Any, field: Mapping[str, Any]) -> Any:
    field_type = str(field["type"])
    if value is None:
        return None
    if field_type == "integer":
        require(
            isinstance(value, int) and not isinstance(value, bool),
            "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_MISMATCH",
            "A mapped field does not satisfy its compiled integer type.",
            status="MISMATCH",
            field_name=field["name"],
        )
    elif field_type == "number":
        require(
            isinstance(value, (int, float)) and not isinstance(value, bool),
            "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_MISMATCH",
            "A mapped field does not satisfy its compiled number type.",
            status="MISMATCH",
            field_name=field["name"],
        )
    elif field_type == "boolean":
        require(
            isinstance(value, bool),
            "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_MISMATCH",
            "A mapped field does not satisfy its compiled boolean type.",
            status="MISMATCH",
            field_name=field["name"],
        )
    elif field_type == "sha256":
        require(
            isinstance(value, str) and bool(_SHA256.fullmatch(value)),
            "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_MISMATCH",
            "A mapped field does not satisfy its compiled SHA-256 type.",
            status="MISMATCH",
            field_name=field["name"],
        )
        value = value.upper()
    elif field_type in {"path", "string", "timestamp"}:
        require(
            isinstance(value, str),
            "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_MISMATCH",
            "A mapped field does not satisfy its compiled text type.",
            status="MISMATCH",
            field_name=field["name"],
            field_type=field_type,
        )
    elif field_type == "json":
        try:
            canonical_json_bytes(value)
        except (TypeError, ValueError) as error:
            raise EvidenceLaneError(
                "CUSTOM_SOURCE_SCHEMA_FIELD_TYPE_MISMATCH",
                "A mapped field is not canonical-JSON serializable.",
                status="MISMATCH",
                details={"field_name": field["name"], "error": str(error)},
            ) from error
    return value


def _map_fields(
    *,
    compiled: Mapping[str, Any],
    selector: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    member: Mapping[str, Any] | None,
) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for field in compiled["fields"]:
        value = _value_from(
            str(field["value_from"]),
            field=field,
            compiled=compiled,
            selector=selector,
            occurrence=occurrence,
            member=member,
        )
        if value is None and field["has_default"]:
            value = field["default"]
        for transform in field["transforms"]:
            try:
                value = _transform(value, str(transform))
            except (TypeError, ValueError) as error:
                raise EvidenceLaneError(
                    "CUSTOM_SOURCE_SCHEMA_TRANSFORM_FAILED",
                    "An allow-listed field transform could not map the sealed metadata.",
                    status="MISMATCH",
                    details={
                        "field_name": field["name"],
                        "transform": transform,
                        "error": str(error),
                    },
                ) from error
        require(
            value is not None or not field["required"],
            "CUSTOM_SOURCE_SCHEMA_REQUIRED_FIELD_MISSING",
            "A required field is unavailable in the selected source scope.",
            status="MISMATCH",
            field_name=field["name"],
            selector_id=selector["selector_id"],
            occurrence_ordinal=occurrence["ordinal"],
            member_path=member.get("member_path") if member else "",
        )
        mapped[str(field["name"])] = _validate_type(value, field)
    return mapped


@source_authority_write
def compile_and_map_custom_source_schema(
    registry_path: ProjectStore | LaneStore,
    batch_id: str,
    definition: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a schema and append its deterministic registry mapping receipt."""

    target = initialize_source_authority_registry(registry_path)
    batch = load_source_batch(target, batch_id)
    compiled = compile_custom_source_schema(definition)
    schema_sha256 = str(compiled["schema_sha256"])

    with _connect(target, write=True) as connection:
        for dependency in compiled["dependency_policy"]["requires"]:
            row = connection.execute(
                """SELECT schema_sha256 FROM source_custom_schema
                WHERE schema_id=? AND schema_version=?""",
                (dependency["schema_id"], dependency["schema_version"]),
            ).fetchone()
            require(
                row is not None and row["schema_sha256"] == dependency["schema_sha256"],
                "CUSTOM_SOURCE_SCHEMA_DEPENDENCY_MISSING",
                "An exact Custom Source Schema dependency is not registered.",
                status="BLOCKED",
                dependency=dependency,
            )

        members_by_object: dict[str, list[dict[str, Any]]] = {}
        mappings: list[dict[str, Any]] = []
        matched_occurrences: set[int] = set()
        selector_counts: Counter[str] = Counter()
        target_owners: dict[tuple[int, str], str] = {}
        unmatched_occurrences: list[int] = []

        for occurrence in batch["occurrences"]:
            ordinal = int(occurrence["ordinal"])
            occurrence_match_count = 0
            for selector in compiled["selectors"]:
                if not _source_matches(occurrence, selector):
                    continue
                if selector["scope"] == "SOURCE":
                    targets: list[dict[str, Any] | None] = [None]
                else:
                    object_id = str(occurrence["object_id"])
                    if object_id not in members_by_object:
                        members_by_object[object_id] = [
                            dict(row)
                            for row in connection.execute(
                                """SELECT member_path, member_kind, size_bytes,
                                sha256, policy_state, policy_reason
                                FROM source_member WHERE object_id=?
                                ORDER BY member_path""",
                                (object_id,),
                            )
                        ]
                    targets = [
                        member
                        for member in members_by_object[object_id]
                        if _member_matches(member, selector)
                    ]
                for member in targets:
                    member_path = str(member["member_path"]) if member else ""
                    target_key = (ordinal, member_path)
                    prior_selector = target_owners.get(target_key)
                    if prior_selector:
                        if compiled["acceptance"]["on_selector_overlap"] == "BLOCK":
                            raise EvidenceLaneError(
                                "CUSTOM_SOURCE_SCHEMA_SELECTOR_OVERLAP",
                                "Two selectors map the same sealed source target.",
                                status="MISMATCH",
                                details={
                                    "occurrence_ordinal": ordinal,
                                    "member_path": member_path,
                                    "first_selector": prior_selector,
                                    "second_selector": selector["selector_id"],
                                },
                            )
                        continue
                    mapped_fields = _map_fields(
                        compiled=compiled,
                        selector=selector,
                        occurrence=occurrence,
                        member=member,
                    )
                    mapping_core = {
                        "schema_sha256": schema_sha256,
                        "batch_id": batch_id,
                        "occurrence_ordinal": ordinal,
                        "object_id": occurrence["object_id"],
                        "member_path": member_path,
                        "selector_id": selector["selector_id"],
                        "target_lane_id": compiled["target_lane_id"],
                        "mapped_fields": mapped_fields,
                        "provenance": {
                            "source_identity_sha256": occurrence["identity_sha256"],
                            "member_sha256": member.get("sha256") if member else None,
                            "authority": "SEALED_SOURCE_REGISTRY_METADATA_ONLY",
                        },
                    }
                    mapping_sha256 = sha256_bytes(canonical_json_bytes(mapping_core))
                    mappings.append(
                        {
                            **mapping_core,
                            "mapping_id": f"custom_mapping_{mapping_sha256[:32].lower()}",
                            "mapping_sha256": mapping_sha256,
                            "mapping_state": "COMPILED_METADATA_MAPPING",
                        }
                    )
                    target_owners[target_key] = str(selector["selector_id"])
                    selector_counts[str(selector["selector_id"])] += 1
                    occurrence_match_count += 1
                    matched_occurrences.add(ordinal)
            if occurrence_match_count == 0:
                unmatched_occurrences.append(ordinal)

        acceptance = compiled["acceptance"]
        require(
            acceptance["on_unmatched_occurrence"] != "BLOCK"
            or not unmatched_occurrences,
            "CUSTOM_SOURCE_SCHEMA_UNMATCHED_OCCURRENCE",
            "One or more registered source occurrences matched no schema selector.",
            status="MISMATCH",
            unmatched_occurrences=unmatched_occurrences,
        )
        missing_selectors = [
            selector["selector_id"]
            for selector in compiled["selectors"]
            if not selector_counts[selector["selector_id"]]
        ]
        require(
            not acceptance["require_all_selectors"] or not missing_selectors,
            "CUSTOM_SOURCE_SCHEMA_SELECTOR_UNMATCHED",
            "At least one required selector produced no mapping.",
            status="MISMATCH",
            missing_selectors=missing_selectors,
        )
        require(
            acceptance["minimum_mappings"]
            <= len(mappings)
            <= acceptance["maximum_mappings"],
            "CUSTOM_SOURCE_SCHEMA_MAPPING_COUNT_OUT_OF_BOUNDS",
            "The compiled mapping count violates the schema acceptance bounds.",
            status="MISMATCH",
            mapping_count=len(mappings),
            minimum_mappings=acceptance["minimum_mappings"],
            maximum_mappings=acceptance["maximum_mappings"],
        )
        mapping_projection = [
            {
                key: row[key]
                for key in (
                    "mapping_id",
                    "mapping_sha256",
                    "occurrence_ordinal",
                    "object_id",
                    "member_path",
                    "selector_id",
                    "target_lane_id",
                )
            }
            for row in mappings
        ]
        receipt_core = {
            "schema": CUSTOM_SOURCE_MAPPING_RECEIPT,
            "compiler_version": CUSTOM_SOURCE_SCHEMA_COMPILER_VERSION,
            "schema_id": compiled["schema_id"],
            "schema_version": compiled["schema_version"],
            "schema_sha256": schema_sha256,
            "batch_id": batch_id,
            "batch_sha256": batch["batch_sha256"],
            "source_occurrence_count": batch["source_count"],
            "matched_occurrence_count": len(matched_occurrences),
            "unmatched_occurrence_count": len(unmatched_occurrences),
            "mapping_count": len(mappings),
            "selector_counts": [
                {
                    "selector_id": selector["selector_id"],
                    "mapping_count": selector_counts[selector["selector_id"]],
                }
                for selector in compiled["selectors"]
            ],
            "mapping_set_sha256": sha256_bytes(
                canonical_json_bytes(mapping_projection)
            ),
            "source_payload_read": False,
            "source_payload_copy": False,
            "imported_code_executed": False,
            "imported_sql_executed": False,
            "candidate_created": False,
            "pointer_moved": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
        receipt_id = f"custom_schema_receipt_{receipt_sha256[:32].lower()}"
        existing_receipt = connection.execute(
            """SELECT receipt_json, receipt_sha256
            FROM source_custom_schema_receipt
            WHERE schema_sha256=? AND batch_id=?""",
            (schema_sha256, batch_id),
        ).fetchone()
        if existing_receipt:
            require(
                existing_receipt["receipt_sha256"] == receipt_sha256
                and json.loads(existing_receipt["receipt_json"]) == receipt_core,
                "CUSTOM_SOURCE_SCHEMA_RECEIPT_MISMATCH",
                "An immutable schema mapping receipt resolved to different content.",
                status="MISMATCH",
                schema_sha256=schema_sha256,
                batch_id=batch_id,
            )
            return {
                "status": "PASS",
                "append_status": "IDEMPOTENT_REUSE",
                "compiled_schema": compiled,
                **receipt_core,
                "receipt_id": receipt_id,
                "receipt_sha256": receipt_sha256,
                "mappings": mappings,
                "registry_snapshot": snapshot_source_authority_registry(
                    target, batch_id
                ),
            }

        existing_schema = connection.execute(
            """SELECT schema_sha256 FROM source_custom_schema
            WHERE schema_id=? AND schema_version=?""",
            (compiled["schema_id"], compiled["schema_version"]),
        ).fetchone()
        require(
            existing_schema is None
            or existing_schema["schema_sha256"] == schema_sha256,
            "CUSTOM_SOURCE_SCHEMA_REVISION_COLLISION",
            "A schema_id and version cannot be silently replaced with different content.",
            status="MISMATCH",
            schema_id=compiled["schema_id"],
            schema_version=compiled["schema_version"],
        )
        with target.transaction():
            if existing_schema is None:
                connection.execute(
                    "INSERT INTO source_custom_schema VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        compiled["schema_id"],
                        compiled["schema_version"],
                        schema_sha256,
                        compiled["target_lane_id"],
                        CUSTOM_SOURCE_SCHEMA_COMPILER_VERSION,
                        _canonical_text(dict(definition)),
                        _canonical_text(compiled),
                        utc_now(),
                    ),
                )
            for row in mappings:
                connection.execute(
                    """INSERT INTO source_custom_schema_mapping
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["mapping_id"],
                        schema_sha256,
                        batch_id,
                        row["occurrence_ordinal"],
                        row["object_id"],
                        row["member_path"],
                        row["selector_id"],
                        row["target_lane_id"],
                        _canonical_text(row),
                        row["mapping_sha256"],
                        row["mapping_state"],
                    ),
                )
            connection.execute(
                "INSERT INTO source_custom_schema_receipt VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    schema_sha256,
                    batch_id,
                    len(matched_occurrences),
                    len(mappings),
                    _canonical_text(receipt_core),
                    receipt_sha256,
                    utc_now(),
                ),
            )
            event_core = {
                "event_type": "source.custom_schema.compiled_and_mapped",
                "receipt_id": receipt_id,
                "receipt_sha256": receipt_sha256,
                "schema_sha256": schema_sha256,
                "batch_id": batch_id,
                "mapping_count": len(mappings),
            }
            event_sha256 = sha256_bytes(canonical_json_bytes(event_core))
            connection.execute(
                "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"registry_{event_sha256[:32].lower()}",
                    batch_id,
                    event_core["event_type"],
                    _canonical_text(event_core),
                    event_sha256,
                    utc_now(),
                ),
            )

    return {
        "status": "PASS",
        "append_status": "APPENDED",
        "compiled_schema": compiled,
        **receipt_core,
        "receipt_id": receipt_id,
        "receipt_sha256": receipt_sha256,
        "mappings": mappings,
        "event_sha256": event_sha256,
        "registry_snapshot": snapshot_source_authority_registry(target, batch_id),
    }


@source_authority_write
def configure_source_intake_schema_pill(
    registry_path: ProjectStore | LaneStore,
    batch_id: str,
    *,
    operation: str,
    pill_name: str,
    definition: Mapping[str, Any],
    expected_previous_schema_sha256: str | None = None,
) -> dict[str, Any]:
    """Append or version one schema-derived Source Intake pill contract."""

    action = str(operation or "").strip().upper()
    label = str(pill_name or "").strip()
    require(
        action in {"ADD", "MODIFY"},
        "SOURCE_INTAKE_SCHEMA_OPERATION_INVALID",
        "Source Intake schema configuration supports only ADD or MODIFY.",
        status="BLOCKED",
        operation=action,
    )
    require(
        1 <= len(label) <= 80,
        "SOURCE_INTAKE_SCHEMA_PILL_NAME_INVALID",
        "A schema-derived Source Intake pill needs a bounded visible name.",
        status="BLOCKED",
        pill_name=label,
    )
    compiled = compile_custom_source_schema(definition)
    require(
        compiled["title"] == label,
        "SOURCE_INTAKE_SCHEMA_PILL_TITLE_MISMATCH",
        "pill_name must exactly match the declarative schema title.",
        status="BLOCKED",
        pill_name=label,
        schema_title=compiled["title"],
    )
    target = initialize_source_authority_registry(registry_path)
    with _connect(target, write=True) as connection:
        rows = connection.execute(
            """SELECT schema_version, schema_sha256 FROM source_custom_schema
            WHERE schema_id=? ORDER BY schema_version""",
            (compiled["schema_id"],),
        ).fetchall()
    history = [
        {
            "schema_version": int(row["schema_version"]),
            "schema_sha256": row["schema_sha256"],
        }
        for row in rows
    ]
    compiled_sha = str(compiled["schema_sha256"])
    requested_version = int(compiled["schema_version"])
    idempotent_retry = bool(
        history
        and history[-1]["schema_version"] == requested_version
        and history[-1]["schema_sha256"] == compiled_sha
    )
    previous: dict[str, Any] | None = None

    if action == "ADD":
        require(
            requested_version == 1,
            "SOURCE_INTAKE_SCHEMA_ADD_VERSION_INVALID",
            "ADD must create schema_version 1.",
            status="BLOCKED",
            schema_version=requested_version,
        )
        require(
            not history or (len(history) == 1 and idempotent_retry),
            "SOURCE_INTAKE_SCHEMA_ALREADY_EXISTS",
            "ADD cannot replace or append to an existing schema-derived pill.",
            status="BLOCKED",
            schema_id=compiled["schema_id"],
            existing_versions=[row["schema_version"] for row in history],
        )
    else:
        expected = str(expected_previous_schema_sha256 or "").strip().upper()
        require(
            bool(_SHA256.fullmatch(expected)),
            "SOURCE_INTAKE_SCHEMA_PREVIOUS_SHA_REQUIRED",
            "MODIFY requires the exact SHA-256 of the prior registered version.",
            status="BLOCKED",
        )
        if idempotent_retry:
            require(
                len(history) >= 2,
                "SOURCE_INTAKE_SCHEMA_MODIFY_HISTORY_MISSING",
                "A MODIFY retry needs both the prior and appended schema versions.",
                status="BLOCKED",
            )
            previous = history[-2]
        else:
            require(
                bool(history),
                "SOURCE_INTAKE_SCHEMA_MODIFY_TARGET_MISSING",
                "MODIFY requires an existing schema-derived pill.",
                status="BLOCKED",
                schema_id=compiled["schema_id"],
            )
            previous = history[-1]
        require(
            previous["schema_sha256"] == expected,
            "SOURCE_INTAKE_SCHEMA_PREVIOUS_SHA_MISMATCH",
            "The supplied prior SHA-256 does not match the exact registered version.",
            status="MISMATCH",
            expected_previous_schema_sha256=expected,
            actual_previous_schema_sha256=previous["schema_sha256"],
        )
        require(
            requested_version == int(previous["schema_version"]) + 1,
            "SOURCE_INTAKE_SCHEMA_VERSION_NOT_NEXT",
            "MODIFY must append exactly the next integer schema version.",
            status="BLOCKED",
            previous_schema_version=previous["schema_version"],
            requested_schema_version=requested_version,
        )

    result = compile_and_map_custom_source_schema(target, batch_id, definition)
    return {
        **result,
        "operation": action,
        "configuration_status": (
            "IDEMPOTENT_REUSE" if idempotent_retry else "APPEND_ONLY_VERSION_CREATED"
        ),
        "pill_projection": {
            "pill_id": f"schema:{compiled['schema_id']}",
            "pill_name": label,
            "schema_id": compiled["schema_id"],
            "schema_version": requested_version,
            "schema_sha256": compiled_sha,
            "target_lane_id": compiled["target_lane_id"],
            "previous_schema_sha256": (
                previous["schema_sha256"] if previous is not None else None
            ),
            "append_only": True,
            "canonical_lane_registry_mutated": False,
        },
    }
