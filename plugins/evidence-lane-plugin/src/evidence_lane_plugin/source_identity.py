"""Append-only multi-axis identities for heterogeneous source generations."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .source_authority import (
    _connect,
    initialize_source_authority_registry,
    load_source_batch,
    snapshot_source_authority_registry,
    source_authority_write,
)
from .storage import LaneStore, ProjectStore
from .timeutil import utc_now

SOURCE_IDENTITY_MATRIX_SCHEMA = "evidence-lane.source-identity-matrix.v1"
SOURCE_IDENTITY_RECEIPT_SCHEMA = "evidence-lane.source-identity-receipt.v1"

_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_AUTHORITIES = frozenset(
    {
        "BYTE_OBSERVED",
        "SOURCE_CONTENT_OBSERVED",
        "SOURCE_NAME_OBSERVED",
        "SYSTEM_DERIVED",
        "USER_STATED",
        "RESEARCH_INFERENCE",
    }
)
_CLAIM_STATES = frozenset(
    {
        "CONFIRMED",
        "DISTINCT_IDENTITY_BOUNDARY",
        "NAME_MARKER_ONLY",
        "OBSERVED",
        "UNKNOWN_NOT_CLAIMED",
        "USER_STATED_UNVERIFIED",
    }
)
_RELATION_TYPES = frozenset(
    {
        "DISTINCT_FROM",
        "GENERATED_WITH",
        "NAME_SIGNALS_RELEASE",
        "PRODUCED_BY",
        "SCHEMA_LABEL_DISTINCT_FROM_APP_RELEASE",
        "UNBOUND_PENDING_PROOF",
        "USES_ARCHITECTURE",
    }
)
_FORBIDDEN_COLLAPSE_RELATIONS = frozenset({"ALIAS_OF", "EQUIVALENT_TO", "SAME_AS"})
_ENDPOINT_TYPES = frozenset({"ENTITY", "SOURCE"})





def _canonical_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8").strip()


def _require_exact_keys(
    raw: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    code: str,
    label: str,
) -> None:
    missing = sorted(required - set(raw))
    unknown = sorted(set(raw) - required - optional)
    require(
        not missing and not unknown,
        code,
        f"A source identity {label} has missing or unsupported keys.",
        status="BLOCKED",
        missing_keys=missing,
        unknown_keys=unknown,
    )


def _compile_entity(raw: Any) -> dict[str, Any]:
    require(
        isinstance(raw, dict),
        "SOURCE_IDENTITY_ENTITY_INVALID",
        "Every identity entity must be a JSON object.",
        status="BLOCKED",
    )
    _require_exact_keys(
        raw,
        required={"authority", "entity_id", "entity_kind", "evidence_ref", "label"},
        optional={"attributes", "version_label"},
        code="SOURCE_IDENTITY_ENTITY_KEYS_INVALID",
        label="entity",
    )
    entity_id = str(raw["entity_id"]).strip().casefold()
    entity_kind = str(raw["entity_kind"]).strip().casefold()
    label = str(raw["label"]).strip()
    version_label = str(raw.get("version_label") or "UNKNOWN_NOT_CLAIMED").strip()
    authority = str(raw["authority"]).strip().upper()
    evidence_ref = str(raw["evidence_ref"]).strip()
    attributes = raw.get("attributes") or {}
    require(
        bool(_TOKEN.fullmatch(entity_id)) and bool(_TOKEN.fullmatch(entity_kind)),
        "SOURCE_IDENTITY_ENTITY_TOKEN_INVALID",
        "Identity entity IDs and kinds require stable lowercase tokens.",
        status="BLOCKED",
        entity_id=entity_id,
        entity_kind=entity_kind,
    )
    require(
        1 <= len(label) <= 240
        and 1 <= len(version_label) <= 160
        and 1 <= len(evidence_ref) <= 1000
        and authority in _AUTHORITIES
        and isinstance(attributes, dict),
        "SOURCE_IDENTITY_ENTITY_VALUE_INVALID",
        "An identity entity contains an invalid bounded value or authority.",
        status="BLOCKED",
        entity_id=entity_id,
        authority=authority,
    )
    core = {
        "entity_id": entity_id,
        "entity_kind": entity_kind,
        "label": label,
        "version_label": version_label,
        "authority": authority,
        "evidence_ref": evidence_ref,
        "attributes": attributes,
    }
    return {**core, "entity_sha256": sha256_bytes(canonical_json_bytes(core))}


def _compile_assertion(
    raw: Any,
    *,
    batch_id: str,
    object_id: str,
) -> dict[str, Any]:
    require(
        isinstance(raw, dict),
        "SOURCE_IDENTITY_ASSERTION_INVALID",
        "Every source identity assertion must be a JSON object.",
        status="BLOCKED",
    )
    _require_exact_keys(
        raw,
        required={"authority", "axis", "claim_state", "evidence_ref", "value"},
        optional=set(),
        code="SOURCE_IDENTITY_ASSERTION_KEYS_INVALID",
        label="assertion",
    )
    axis = str(raw["axis"]).strip().casefold()
    authority = str(raw["authority"]).strip().upper()
    evidence_ref = str(raw["evidence_ref"]).strip()
    claim_state = str(raw["claim_state"]).strip().upper()
    require(
        bool(_TOKEN.fullmatch(axis))
        and authority in _AUTHORITIES
        and claim_state in _CLAIM_STATES
        and 1 <= len(evidence_ref) <= 1000,
        "SOURCE_IDENTITY_ASSERTION_VALUE_INVALID",
        "An identity assertion contains an invalid axis, authority, state, or evidence reference.",
        status="BLOCKED",
        axis=axis,
        authority=authority,
        claim_state=claim_state,
    )
    require(
        claim_state != "UNKNOWN_NOT_CLAIMED" or raw["value"] is None,
        "SOURCE_IDENTITY_UNKNOWN_VALUE_INVALID",
        "UNKNOWN_NOT_CLAIMED assertions must preserve a null value.",
        status="BLOCKED",
        axis=axis,
    )
    core = {
        "batch_id": batch_id,
        "object_id": object_id,
        "identity_axis": axis,
        "value": raw["value"],
        "authority": authority,
        "evidence_ref": evidence_ref,
        "claim_state": claim_state,
    }
    digest = sha256_bytes(canonical_json_bytes(core))
    return {
        **core,
        "assertion_id": f"identity_assertion_{digest[:32].lower()}",
        "assertion_sha256": digest,
    }


def _resolve_endpoint(
    endpoint_type: str,
    endpoint_ref: Any,
    *,
    occurrence_by_ordinal: Mapping[int, Mapping[str, Any]],
    entity_ids: set[str],
) -> str:
    require(
        endpoint_type in _ENDPOINT_TYPES,
        "SOURCE_IDENTITY_ENDPOINT_TYPE_INVALID",
        "Identity relations require SOURCE or ENTITY endpoints.",
        status="BLOCKED",
        endpoint_type=endpoint_type,
    )
    if endpoint_type == "SOURCE":
        require(
            isinstance(endpoint_ref, int)
            and not isinstance(endpoint_ref, bool)
            and endpoint_ref in occurrence_by_ordinal,
            "SOURCE_IDENTITY_SOURCE_ENDPOINT_INVALID",
            "A source relation endpoint must reference an exact registered ordinal.",
            status="BLOCKED",
            endpoint_ref=endpoint_ref,
        )
        return str(occurrence_by_ordinal[int(endpoint_ref)]["object_id"])
    entity_id = str(endpoint_ref).strip().casefold()
    require(
        entity_id in entity_ids,
        "SOURCE_IDENTITY_ENTITY_ENDPOINT_INVALID",
        "A relation references an unregistered identity entity.",
        status="BLOCKED",
        endpoint_ref=entity_id,
    )
    return entity_id


def _compile_relation(
    raw: Any,
    *,
    batch_id: str,
    occurrence_by_ordinal: Mapping[int, Mapping[str, Any]],
    entity_ids: set[str],
) -> dict[str, Any]:
    require(
        isinstance(raw, dict),
        "SOURCE_IDENTITY_RELATION_INVALID",
        "Every identity relation must be a JSON object.",
        status="BLOCKED",
    )
    _require_exact_keys(
        raw,
        required={
            "authority",
            "evidence_ref",
            "object_ref",
            "object_type",
            "relation_type",
            "subject_ref",
            "subject_type",
        },
        optional=set(),
        code="SOURCE_IDENTITY_RELATION_KEYS_INVALID",
        label="relation",
    )
    subject_type = str(raw["subject_type"]).strip().upper()
    object_type = str(raw["object_type"]).strip().upper()
    relation_type = str(raw["relation_type"]).strip().upper()
    authority = str(raw["authority"]).strip().upper()
    evidence_ref = str(raw["evidence_ref"]).strip()
    require(
        relation_type not in _FORBIDDEN_COLLAPSE_RELATIONS,
        "SOURCE_IDENTITY_COLLAPSE_FORBIDDEN",
        "Identity axes cannot be collapsed through alias or equivalence relations.",
        status="BLOCKED",
        relation_type=relation_type,
    )
    require(
        relation_type in _RELATION_TYPES
        and authority in _AUTHORITIES
        and 1 <= len(evidence_ref) <= 1000,
        "SOURCE_IDENTITY_RELATION_VALUE_INVALID",
        "An identity relation type, authority, or evidence reference is unsupported.",
        status="BLOCKED",
        relation_type=relation_type,
        authority=authority,
    )
    subject_id = _resolve_endpoint(
        subject_type,
        raw["subject_ref"],
        occurrence_by_ordinal=occurrence_by_ordinal,
        entity_ids=entity_ids,
    )
    object_id = _resolve_endpoint(
        object_type,
        raw["object_ref"],
        occurrence_by_ordinal=occurrence_by_ordinal,
        entity_ids=entity_ids,
    )
    require(
        (subject_type, subject_id) != (object_type, object_id),
        "SOURCE_IDENTITY_SELF_RELATION_INVALID",
        "An identity endpoint cannot relate to itself.",
        status="BLOCKED",
    )
    core = {
        "batch_id": batch_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "relation_type": relation_type,
        "object_type": object_type,
        "object_id": object_id,
        "authority": authority,
        "evidence_ref": evidence_ref,
    }
    digest = sha256_bytes(canonical_json_bytes(core))
    return {
        **core,
        "relation_id": f"identity_relation_{digest[:32].lower()}",
        "relation_sha256": digest,
    }


@source_authority_write
def register_source_identity_matrix(
    registry_path: ProjectStore | LaneStore,
    batch_id: str,
    *,
    entities: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate and append one complete, non-conflating identity matrix."""

    target = initialize_source_authority_registry(registry_path)
    batch = load_source_batch(target, batch_id)
    occurrence_by_ordinal = {int(row["ordinal"]): row for row in batch["occurrences"]}
    require(
        len(profiles) == batch["source_count"],
        "SOURCE_IDENTITY_PROFILE_COUNT_MISMATCH",
        "An identity matrix must account for every registered source occurrence.",
        status="MISMATCH",
        expected=batch["source_count"],
        actual=len(profiles),
    )
    compiled_entities = sorted(
        (_compile_entity(raw) for raw in entities), key=lambda row: row["entity_id"]
    )
    entity_ids = {str(row["entity_id"]) for row in compiled_entities}
    require(
        len(entity_ids) == len(compiled_entities),
        "SOURCE_IDENTITY_ENTITY_DUPLICATE",
        "Identity entity IDs must be unique.",
        status="BLOCKED",
    )

    compiled_assertions: list[dict[str, Any]] = []
    profile_projection: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    for expected_ordinal, profile in enumerate(profiles, start=1):
        require(
            isinstance(profile, dict)
            and set(profile) == {"assertions", "ordinal", "source_name"},
            "SOURCE_IDENTITY_PROFILE_INVALID",
            "Every identity profile requires exact ordinal, source_name, and assertions.",
            status="BLOCKED",
            expected_ordinal=expected_ordinal,
        )
        ordinal = profile["ordinal"]
        require(
            ordinal == expected_ordinal
            and ordinal in occurrence_by_ordinal
            and ordinal not in seen_ordinals,
            "SOURCE_IDENTITY_PROFILE_ORDINAL_MISMATCH",
            "Identity profiles must preserve the exact contiguous source order.",
            status="MISMATCH",
            expected_ordinal=expected_ordinal,
            actual_ordinal=ordinal,
        )
        occurrence = occurrence_by_ordinal[ordinal]
        source_name = Path(str(occurrence["supplied_pointer"])).name
        require(
            profile["source_name"] == source_name,
            "SOURCE_IDENTITY_PROFILE_NAME_MISMATCH",
            "An identity profile does not bind the exact registered source name.",
            status="MISMATCH",
            ordinal=ordinal,
            expected_name=source_name,
            actual_name=profile["source_name"],
        )
        raw_assertions = profile["assertions"]
        require(
            isinstance(raw_assertions, list) and bool(raw_assertions),
            "SOURCE_IDENTITY_ASSERTIONS_REQUIRED",
            "Every source profile needs at least one explicit identity assertion.",
            status="BLOCKED",
            ordinal=ordinal,
        )
        assertions = [
            _compile_assertion(
                raw,
                batch_id=batch_id,
                object_id=str(occurrence["object_id"]),
            )
            for raw in raw_assertions
        ]
        assertion_shas = [row["assertion_sha256"] for row in assertions]
        require(
            len(assertion_shas) == len(set(assertion_shas)),
            "SOURCE_IDENTITY_ASSERTION_DUPLICATE",
            "A source profile repeats an identical assertion.",
            status="BLOCKED",
            ordinal=ordinal,
        )
        require(
            any(
                row["identity_axis"] == "artifact.identity_sha256" for row in assertions
            ),
            "SOURCE_IDENTITY_BYTE_AXIS_REQUIRED",
            "Every source profile must retain its sealed artifact identity axis.",
            status="BLOCKED",
            ordinal=ordinal,
        )
        compiled_assertions.extend(assertions)
        profile_projection.append(
            {
                "ordinal": ordinal,
                "source_name": source_name,
                "object_id": occurrence["object_id"],
                "assertion_sha256s": assertion_shas,
            }
        )
        seen_ordinals.add(ordinal)

    compiled_relations = sorted(
        (
            _compile_relation(
                raw,
                batch_id=batch_id,
                occurrence_by_ordinal=occurrence_by_ordinal,
                entity_ids=entity_ids,
            )
            for raw in relations
        ),
        key=lambda row: row["relation_sha256"],
    )
    relation_shas = [row["relation_sha256"] for row in compiled_relations]
    require(
        len(relation_shas) == len(set(relation_shas)),
        "SOURCE_IDENTITY_RELATION_DUPLICATE",
        "The identity matrix repeats an identical relation.",
        status="BLOCKED",
    )
    matrix_core = {
        "schema": SOURCE_IDENTITY_MATRIX_SCHEMA,
        "batch_id": batch_id,
        "batch_sha256": batch["batch_sha256"],
        "entities": compiled_entities,
        "profiles": profile_projection,
        "assertions": compiled_assertions,
        "relations": compiled_relations,
        "identity_axes_collapsed": False,
        "unverified_producer_bindings_inferred": False,
    }
    matrix_sha256 = sha256_bytes(canonical_json_bytes(matrix_core))
    receipt_core = {
        "schema": SOURCE_IDENTITY_RECEIPT_SCHEMA,
        "batch_id": batch_id,
        "batch_sha256": batch["batch_sha256"],
        "matrix_sha256": matrix_sha256,
        "source_profile_count": len(profile_projection),
        "entity_count": len(compiled_entities),
        "assertion_count": len(compiled_assertions),
        "relation_count": len(compiled_relations),
        "identity_axes_collapsed": False,
        "unverified_producer_bindings_inferred": False,
        "source_payload_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
    receipt_id = f"source_identity_{receipt_sha256[:32].lower()}"

    with _connect(target, write=True) as connection:
        existing_receipt = connection.execute(
            "SELECT receipt_json FROM source_identity_receipt WHERE matrix_sha256=?",
            (matrix_sha256,),
        ).fetchone()
        if existing_receipt:
            require(
                json.loads(existing_receipt["receipt_json"]) == receipt_core,
                "SOURCE_IDENTITY_RECEIPT_COLLISION",
                "An immutable identity matrix resolved to different receipt content.",
                status="MISMATCH",
            )
            return {
                "status": "PASS",
                "append_status": "IDEMPOTENT_REUSE",
                **receipt_core,
                "receipt_id": receipt_id,
                "receipt_sha256": receipt_sha256,
                "registry_snapshot": snapshot_source_authority_registry(
                    target, batch_id
                ),
            }
        with target.transaction():
            for entity in compiled_entities:
                prior = connection.execute(
                    "SELECT entity_sha256 FROM source_identity_entity WHERE entity_id=?",
                    (entity["entity_id"],),
                ).fetchone()
                require(
                    prior is None or prior["entity_sha256"] == entity["entity_sha256"],
                    "SOURCE_IDENTITY_ENTITY_COLLISION",
                    "An identity entity cannot be silently redefined.",
                    status="MISMATCH",
                    entity_id=entity["entity_id"],
                )
                if prior is None:
                    connection.execute(
                        "INSERT INTO source_identity_entity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            entity["entity_id"],
                            entity["entity_kind"],
                            entity["label"],
                            entity["version_label"],
                            entity["authority"],
                            entity["evidence_ref"],
                            _canonical_text(entity),
                            entity["entity_sha256"],
                            utc_now(),
                        ),
                    )
            for assertion in compiled_assertions:
                conflict = connection.execute(
                    """SELECT value_json, assertion_sha256
                    FROM source_identity_assertion
                    WHERE batch_id=? AND object_id=? AND identity_axis=?
                    AND authority=? AND evidence_ref=?""",
                    (
                        batch_id,
                        assertion["object_id"],
                        assertion["identity_axis"],
                        assertion["authority"],
                        assertion["evidence_ref"],
                    ),
                ).fetchone()
                require(
                    conflict is None
                    or conflict["assertion_sha256"] == assertion["assertion_sha256"],
                    "SOURCE_IDENTITY_ASSERTION_CONFLICT",
                    "The same authority and evidence reference cannot silently change an identity assertion.",
                    status="MISMATCH",
                    object_id=assertion["object_id"],
                    identity_axis=assertion["identity_axis"],
                )
                if conflict is None:
                    connection.execute(
                        "INSERT INTO source_identity_assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            assertion["assertion_id"],
                            batch_id,
                            assertion["object_id"],
                            assertion["identity_axis"],
                            _canonical_text(assertion["value"]),
                            assertion["authority"],
                            assertion["evidence_ref"],
                            assertion["claim_state"],
                            assertion["assertion_sha256"],
                            utc_now(),
                        ),
                    )
            for relation in compiled_relations:
                prior = connection.execute(
                    "SELECT relation_sha256 FROM source_identity_relation WHERE relation_id=?",
                    (relation["relation_id"],),
                ).fetchone()
                require(
                    prior is None
                    or prior["relation_sha256"] == relation["relation_sha256"],
                    "SOURCE_IDENTITY_RELATION_COLLISION",
                    "An identity relation cannot be silently redefined.",
                    status="MISMATCH",
                    relation_id=relation["relation_id"],
                )
                if prior is None:
                    connection.execute(
                        "INSERT INTO source_identity_relation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            relation["relation_id"],
                            batch_id,
                            relation["subject_type"],
                            relation["subject_id"],
                            relation["relation_type"],
                            relation["object_type"],
                            relation["object_id"],
                            relation["authority"],
                            relation["evidence_ref"],
                            relation["relation_sha256"],
                            utc_now(),
                        ),
                    )
            connection.execute(
                "INSERT INTO source_identity_receipt VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    receipt_id,
                    batch_id,
                    matrix_sha256,
                    len(profile_projection),
                    len(compiled_entities),
                    len(compiled_assertions),
                    len(compiled_relations),
                    _canonical_text(receipt_core),
                    receipt_sha256,
                    utc_now(),
                ),
            )
            event_core = {
                "event_type": "source.identity_matrix.registered",
                "batch_id": batch_id,
                "matrix_sha256": matrix_sha256,
                "receipt_sha256": receipt_sha256,
                "source_profile_count": len(profile_projection),
                "identity_axes_collapsed": False,
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
        **receipt_core,
        "receipt_id": receipt_id,
        "receipt_sha256": receipt_sha256,
        "event_sha256": event_sha256,
        "registry_snapshot": snapshot_source_authority_registry(target, batch_id),
    }
