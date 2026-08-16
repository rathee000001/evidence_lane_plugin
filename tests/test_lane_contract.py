from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_lane_plugin.lane_contract import (
    LANE_DISPOSITION_SCHEMA,
    validate_lane_disposition_projection,
)
from evidence_lane_plugin.lane_engine import build_lane_bundle, validate_lane_bundle
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY


def _row(projection: dict, lane_id: str) -> dict:
    return next(row for row in projection["rows"] if row["lane_id"] == lane_id)


def _build(
    repository: Path,
    output: Path,
    *,
    parent: Path | None = None,
    parent_pv: str | None = None,
    proposed_pv: str = "PV-ROW181",
    overrides: dict[str, str] | None = None,
) -> dict:
    return build_lane_bundle(
        repository_root=repository,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=parent,
        parent_pv=parent_pv,
        proposed_pv=proposed_pv,
        pointer_generation=0 if parent is None else 1,
        source_overrides=overrides,
    )


def test_all_eighteen_lanes_have_truthful_dispositions_without_placeholders(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "guide.md").write_text("# Governed guide\n", encoding="utf-8")
    output = tmp_path / "bundle"

    manifest = _build(repository, output, overrides={"guide.md": "docs"})
    projection = json.loads(
        (output / "lane_dispositions.json").read_text(encoding="utf-8")
    )
    validation = validate_lane_bundle(output)

    assert manifest["lane_disposition_contract"] == {
        "schema": LANE_DISPOSITION_SCHEMA,
        "path": "lane_dispositions.json",
        "projection_sha256": projection["projection_sha256"],
        "canonical_lane_count": 18,
    }
    assert projection["ordered_lane_ids"] == list(CANONICAL_LANE_IDS)
    assert len(projection["rows"]) == 18
    assert projection["unloaded_lane_artifacts_fabricated"] is False
    assert _row(projection, "docs")["disposition"] == "PRESERVED"
    assert _row(projection, "chat_lineage")["disposition"] == "PRESERVED"
    for lane_id in CANONICAL_LANE_IDS:
        row = _row(projection, lane_id)
        assert row["disposition"] in {"PRESERVED", "PARTIAL", "MISSING", "DEFERRED"}
        if row["state"] == "NOT_EMITTED":
            assert row["disposition"] == "MISSING"
            assert row["absence_contract"]["lane_directory_absent"] is True
            assert not (output / lane_id).exists()
    docs = _row(projection, "docs")
    assert docs["capability_contract"]["required_all_active"] is True
    assert {
        row["capability"]
        for row in docs["capability_contract"]["requirements"]["REQUIRED"]
    } == {
        "exact_source_bytes",
        "fts5_bm25",
        "tfidf",
        "mermaid_source",
        "dot_source",
    }
    assert docs["retrieval_contract"]["fts_matches_chunk_count"] is True
    assert docs["authority_contract"]["ordered_members"] == [
        LANE_REGISTRY["docs"].sqlite_filename,
        LANE_REGISTRY["docs"].mmd_filename,
        LANE_REGISTRY["docs"].dot_filename,
        "tools.json",
    ]
    assert validation["valid"] is True
    assert validation["lane_disposition_contract"]["enforced"] is True
    assert validation["lane_disposition_contract"]["status"] == "PASS"


def test_parser_failure_is_partial_not_false_preserved(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "broken.pdf").write_bytes(b"not a PDF")
    output = tmp_path / "bundle"

    _build(repository, output, overrides={"broken.pdf": "pdf_ocr"})
    projection = json.loads(
        (output / "lane_dispositions.json").read_text(encoding="utf-8")
    )
    pdf = _row(projection, "pdf_ocr")

    assert pdf["state"] == "EMITTED"
    assert pdf["disposition"] == "PARTIAL"
    assert pdf["parser"]["blocked_or_failed_state_counts"]
    assert pdf["authority_contract"]["all_present"] is True
    assert validate_lane_bundle(output)["valid"] is True


def test_removed_lane_is_deferred_history_without_current_artifacts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    guide = repository / "guide.md"
    guide.write_text("# Parent guide\n", encoding="utf-8")
    parent = tmp_path / "parent"
    _build(repository, parent, proposed_pv="PV1", overrides={"guide.md": "docs"})
    guide.unlink()
    child = tmp_path / "child"

    _build(repository, child, parent=parent, parent_pv="PV1", proposed_pv="PV2")
    projection = json.loads(
        (child / "lane_dispositions.json").read_text(encoding="utf-8")
    )
    docs = _row(projection, "docs")

    assert docs["state"] == "NOT_EMITTED"
    assert docs["disposition"] == "DEFERRED"
    assert docs["absence_contract"]["reason"] == (
        "REMOVED_FROM_CURRENT_SOURCE_SET_HISTORY_RETAINED"
    )
    assert docs["absence_contract"]["lane_directory_absent"] is True
    assert not (child / "docs").exists()
    assert validate_lane_bundle(child)["valid"] is True


@pytest.mark.parametrize(
    "schema",
    [
        "evidence-lane.universal-lane-bundle.v1",
        "evidence-lane.universal-lane-bundle.v2",
        "evidence-lane.lane-manifest.v3",
    ],
)
def test_historical_v1_v2_v3_without_additive_projection_remain_compatible(
    tmp_path: Path,
    schema: str,
) -> None:
    result = validate_lane_disposition_projection(tmp_path, {"schema": schema})

    assert result == {
        "status": "PASS",
        "valid": True,
        "enforced": False,
        "compatibility": "HISTORICAL_V1_V2_V3_CONTRACT_ABSENT",
    }


def test_malformed_disposition_rows_fail_closed_without_raising(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "guide.md").write_text("# Governed guide\n", encoding="utf-8")
    output = tmp_path / "bundle"
    manifest = _build(repository, output, overrides={"guide.md": "docs"})
    projection_path = output / "lane_dispositions.json"
    projection = json.loads(projection_path.read_text(encoding="utf-8"))
    projection["rows"][0] = "MALFORMED"
    projection_path.write_text(
        json.dumps(projection, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = validate_lane_disposition_projection(output, manifest)

    assert result["status"] == "FAIL"
    assert result["valid"] is False
    assert result["enforced"] is True
