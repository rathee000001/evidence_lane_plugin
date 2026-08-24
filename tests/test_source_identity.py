from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    load_source_batch,
    reconcile_legacy_source_authority_registry,
    register_source_batch,
    snapshot_source_authority_registry,
)
from evidence_lane_plugin.source_identity import register_source_identity_matrix


def _fixture(tmp_path: Path) -> tuple[Path, str, list[dict[str, object]]]:
    chatgpt = tmp_path / "ChatGPT_Full_V3_Brain"
    chatgpt.mkdir()
    (chatgpt / "brain.sqlite").write_bytes(b"chatgpt-v3-not-sqlite-fixture")
    hil = tmp_path / "evidence_lane_hil_app_output"
    hil.mkdir()
    (hil / "README.md").write_text("HIL app output\n", encoding="utf-8")
    registry = tmp_path / "source_authority.sqlite"
    receipt = register_source_batch(
        registry,
        [
            SourceAuthoritySpec(str(chatgpt), 1, "sqlite_brain"),
            SourceAuthoritySpec(str(hil), 2, "project_engulf"),
        ],
    )
    batch = load_source_batch(registry, str(receipt["batch_id"]))
    profiles: list[dict[str, object]] = []
    for row in batch["occurrences"]:
        profiles.append(
            {
                "ordinal": row["ordinal"],
                "source_name": Path(row["supplied_pointer"]).name,
                "assertions": [
                    {
                        "axis": "artifact.identity_sha256",
                        "value": row["identity_sha256"],
                        "authority": "BYTE_OBSERVED",
                        "evidence_ref": "source-authority-registry",
                        "claim_state": "CONFIRMED",
                    }
                ],
            }
        )
    return registry, str(receipt["batch_id"]), profiles


def _entities() -> list[dict[str, object]]:
    return [
        {
            "entity_id": "producer.chatgpt-5.5-pro",
            "entity_kind": "model_release",
            "label": "ChatGPT 5.5 Pro",
            "version_label": "5.5 Pro",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
        {
            "entity_id": "architecture.evidence-lane-full-v3",
            "entity_kind": "architecture_generation",
            "label": "Evidence Lane full V3 architecture",
            "version_label": "full V3",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
        {
            "entity_id": "producer.hil-application",
            "entity_kind": "application_lineage",
            "label": "Evidence Lane HIL application",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
        {
            "entity_id": "producer.sqlite-brain-builder-v5.9",
            "entity_kind": "application_release",
            "label": "SQLite Brain Builder V5.9",
            "version_label": "V5.9",
            "authority": "USER_STATED",
            "evidence_ref": "user-installed-application-observation",
        },
    ]


def _relations() -> list[dict[str, object]]:
    return [
        {
            "subject_type": "SOURCE",
            "subject_ref": 1,
            "relation_type": "GENERATED_WITH",
            "object_type": "ENTITY",
            "object_ref": "producer.chatgpt-5.5-pro",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
        {
            "subject_type": "SOURCE",
            "subject_ref": 1,
            "relation_type": "USES_ARCHITECTURE",
            "object_type": "ENTITY",
            "object_ref": "architecture.evidence-lane-full-v3",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
        {
            "subject_type": "SOURCE",
            "subject_ref": 2,
            "relation_type": "PRODUCED_BY",
            "object_type": "ENTITY",
            "object_ref": "producer.hil-application",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
        {
            "subject_type": "SOURCE",
            "subject_ref": 2,
            "relation_type": "DISTINCT_FROM",
            "object_type": "ENTITY",
            "object_ref": "producer.sqlite-brain-builder-v5.9",
            "authority": "USER_STATED",
            "evidence_ref": "user-provenance-correction",
        },
    ]


def test_identity_matrix_preserves_distinct_axes_and_is_idempotent(
    tmp_path: Path,
) -> None:
    registry, batch_id, profiles = _fixture(tmp_path)
    first = register_source_identity_matrix(
        registry,
        batch_id,
        entities=_entities(),
        profiles=profiles,
        relations=_relations(),
    )
    second = register_source_identity_matrix(
        registry,
        batch_id,
        entities=_entities(),
        profiles=profiles,
        relations=_relations(),
    )
    snapshot = snapshot_source_authority_registry(registry, batch_id)

    assert first["append_status"] == "APPENDED"
    assert second["append_status"] == "IDEMPOTENT_REUSE"
    assert first["matrix_sha256"] == second["matrix_sha256"]
    assert first["source_profile_count"] == 2
    assert first["entity_count"] == 4
    assert first["identity_axes_collapsed"] is False
    assert first["unverified_producer_bindings_inferred"] is False
    assert snapshot["identity_projection"]["entity_count"] == 4
    assert snapshot["identity_projection"]["assertion_count"] == 2
    assert snapshot["identity_projection"]["relation_count"] == 4
    assert snapshot["identity_projection"]["receipt_count"] == 1


def test_identity_matrix_forbids_same_as_collapse(tmp_path: Path) -> None:
    registry, batch_id, profiles = _fixture(tmp_path)
    relations = _relations()
    relations.append(
        {
            "subject_type": "ENTITY",
            "subject_ref": "producer.hil-application",
            "relation_type": "SAME_AS",
            "object_type": "ENTITY",
            "object_ref": "producer.sqlite-brain-builder-v5.9",
            "authority": "RESEARCH_INFERENCE",
            "evidence_ref": "unsupported-conflation",
        }
    )
    with pytest.raises(EvidenceLaneError) as blocked:
        register_source_identity_matrix(
            registry,
            batch_id,
            entities=_entities(),
            profiles=profiles,
            relations=relations,
        )
    assert blocked.value.code == "SOURCE_IDENTITY_COLLAPSE_FORBIDDEN"


def test_identity_matrix_requires_complete_ordered_source_coverage(
    tmp_path: Path,
) -> None:
    registry, batch_id, profiles = _fixture(tmp_path)
    with pytest.raises(EvidenceLaneError) as mismatch:
        register_source_identity_matrix(
            registry,
            batch_id,
            entities=_entities(),
            profiles=profiles[:1],
            relations=[],
        )
    assert mismatch.value.code == "SOURCE_IDENTITY_PROFILE_COUNT_MISMATCH"


def test_legacy_root_source_authority_merges_into_sources_once(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    canonical = project / "sources" / "source_authority.sqlite"
    legacy = project / "source_authority.sqlite"
    first_source = tmp_path / "first.md"
    second_source = tmp_path / "second.md"
    first_source.write_text("first source\n", encoding="utf-8")
    second_source.write_text("second source\n", encoding="utf-8")
    register_source_batch(
        canonical,
        [SourceAuthoritySpec(str(first_source), 1, "docs")],
    )
    register_source_batch(
        legacy,
        [SourceAuthoritySpec(str(second_source), 1, "docs")],
    )

    receipt = reconcile_legacy_source_authority_registry(project)
    replay = reconcile_legacy_source_authority_registry(project)
    snapshot = snapshot_source_authority_registry(canonical)

    assert receipt["state"] == "LEGACY_ROOT_REGISTRY_MERGED_INTO_SOURCES"
    assert receipt["legacy_path_present_after"] is False
    assert not legacy.exists()
    assert canonical.is_file()
    assert snapshot["batch_count"] == 2
    assert receipt["inserted_row_counts"]["intake_batch"] == 1
    assert receipt["inserted_row_counts"]["source_object"] == 1
    assert replay["state"] == "CANONICAL_SOURCE_AUTHORITY_ROUTE"
