from __future__ import annotations

import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    load_source_batch,
    register_source_batch,
    register_source_crosswalk,
    snapshot_source_authority_registry,
    verify_source_batch_unchanged,
)
from evidence_lane_plugin.source_intake import classify_source_intake

from .conftest import boot_local


def _spec(
    source: Path, ordinal: int, lane: str = "project_engulf"
) -> SourceAuthoritySpec:
    return SourceAuthoritySpec(
        source=str(source),
        ordinal=ordinal,
        lane_id=lane,
    )


def test_registry_preserves_order_and_proves_zip_extracted_counterpart(
    tmp_path: Path,
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "README.md").write_text("# exact\n", encoding="utf-8")
    (extracted / "src").mkdir()
    (extracted / "src" / "main.py").write_text("print('exact')\n", encoding="utf-8")
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(extracted / "README.md", "source/README.md")
        handle.write(extracted / "src" / "main.py", "source/src/main.py")
    standalone = tmp_path / "brain.sqlite"
    standalone.write_bytes(b"sqlite-placeholder")
    registry = tmp_path / "authority.sqlite"

    receipt = register_source_batch(
        registry,
        [
            _spec(extracted, 1),
            _spec(archive, 2, "brain_loader"),
            _spec(standalone, 3, "sqlite_brain"),
        ],
    )

    assert receipt["source_count"] == 3
    assert receipt["directory_count"] == 1
    assert receipt["file_count"] == 2
    assert receipt["zip_count"] == 1
    assert receipt["exact_extracted_zip_relations"] == 1
    assert receipt["unique_zip_count"] == 0
    loaded = load_source_batch(registry, receipt["batch_id"])
    assert [row["ordinal"] for row in loaded["occurrences"]] == [1, 2, 3]
    assert loaded["relations"][0]["relation_type"] == ("EXACT_EXTRACTED_COUNTERPART")
    assert (
        verify_source_batch_unchanged(registry, receipt["batch_id"])["status"] == "PASS"
    )


def test_archive_skip_accepts_exact_nested_subtree_without_claiming_whole_dir(
    tmp_path: Path,
) -> None:
    supplied = tmp_path / "supplied"
    nested = supplied / "repo"
    nested.mkdir(parents=True)
    (nested / "README.md").write_text("# exact\n", encoding="utf-8")
    (nested / "src").mkdir()
    (nested / "src" / "main.py").write_text("print('exact')\n", encoding="utf-8")
    (supplied / "README.md").write_text("# exact\n", encoding="utf-8")
    (supplied / "src").mkdir()
    (supplied / "src" / "main.py").write_text("print('exact')\n", encoding="utf-8")
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(nested / "README.md", "repo/README.md")
        handle.write(nested / "src" / "main.py", "repo/src/main.py")
    registry = tmp_path / "authority.sqlite"

    receipt = register_source_batch(
        registry,
        [_spec(supplied, 1), _spec(archive, 2, "project_engulf")],
    )
    archive_intake = receipt["archive_intake"]

    assert receipt["exact_extracted_zip_relations"] == 1
    assert archive_intake["exact_extracted_zip_relations"] == 1
    assert archive_intake["skip_eligible_count"] == 1
    archive_receipt = archive_intake["receipts"][0]
    assert archive_receipt["skip_status"] == (
        "SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART"
    )
    assert archive_receipt["counterpart_scope"] == (
        "EXACT_NESTED_SUBTREE_POLICY_APPROVED_MEMBER_PATH_SIZE_SHA256"
    )
    assert archive_receipt["matched_prefix"] == "repo"
    assert archive_receipt["source_payloads_extracted"] is False


def test_unsafe_archive_member_blocks_skip_even_when_safe_subset_matches(
    tmp_path: Path,
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "safe.txt").write_text("safe\n", encoding="utf-8")
    archive = tmp_path / "unsafe.zip"
    symlink = zipfile.ZipInfo("link-to-outside")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("safe.txt", "safe\n")
        handle.writestr("../escape.txt", "escape\n")
        handle.writestr(symlink, "../outside")
    registry = tmp_path / "authority.sqlite"

    receipt = register_source_batch(
        registry,
        [_spec(extracted, 1), _spec(archive, 2, "project_engulf")],
    )
    classified = classify_source_intake(
        [str(archive)],
        code_mode="local_code",
        authority_mode="CLASSIFICATION_ONLY",
    )
    archive_receipt = receipt["archive_intake"]["receipts"][0]

    assert receipt["exact_extracted_zip_relations"] == 0
    assert archive_receipt["intake_status"] == "BLOCKED"
    assert archive_receipt["skip_status"] == "BLOCKED_UNSAFE_ARCHIVE"
    assert archive_receipt["unsafe_member_count"] == 2
    assert archive_receipt["unsafe_reasons"] == [
        "ARCHIVE_SYMLINK_NOT_FOLLOWED",
        "UNSAFE_ARCHIVE_PATH",
    ]
    profile = classified["sources"][0]["archive_profile"]
    assert profile["status"] == "BLOCKED"
    assert profile["archive_safety"]["unsafe_member_count"] == 2
    assert not (tmp_path / "escape.txt").exists()


def test_extreme_compression_ratio_is_blocked_without_extraction(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "compression-bomb.zip"
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as handle:
        handle.writestr("zeros.bin", b"\x00" * (2 * 1024 * 1024))

    classified = classify_source_intake(
        [str(archive)],
        code_mode="local_code",
        authority_mode="CLASSIFICATION_ONLY",
    )
    profile = classified["sources"][0]["archive_profile"]

    assert profile["status"] == "BLOCKED"
    assert profile["archive_safety"]["unsafe_reasons"] == [
        "SUSPICIOUS_COMPRESSION_RATIO"
    ]
    assert profile["archive_safety"]["source_payloads_extracted"] is False


def test_registry_is_idempotent_and_root_projection_is_reproducible(
    tmp_path: Path,
) -> None:
    source = tmp_path / "evidence.txt"
    source.write_text("same bytes\n", encoding="utf-8")
    registry = tmp_path / "authority.sqlite"
    first = register_source_batch(registry, [_spec(source, 1, "docs")])
    first_snapshot = snapshot_source_authority_registry(registry)
    second = register_source_batch(registry, [_spec(source, 1, "docs")])
    second_snapshot = snapshot_source_authority_registry(registry)

    assert first["append_status"] == "APPENDED"
    assert second["append_status"] == "IDEMPOTENT_REUSE"
    assert first["batch_sha256"] == second["batch_sha256"]
    assert (
        first_snapshot["registry_root_sha256"]
        == second_snapshot["registry_root_sha256"]
    )
    assert second_snapshot["batch_count"] == 1


def test_registry_detects_changed_source_after_classification(tmp_path: Path) -> None:
    source = tmp_path / "evidence.txt"
    source.write_text("before\n", encoding="utf-8")
    registry = tmp_path / "authority.sqlite"
    receipt = register_source_batch(registry, [_spec(source, 1, "docs")])
    source.write_text("after\n", encoding="utf-8")

    with pytest.raises(EvidenceLaneError) as stale:
        verify_source_batch_unchanged(registry, receipt["batch_id"])
    assert stale.value.code == "SOURCE_AUTHORITY_BATCH_CHANGED"
    assert stale.value.status == "STALE"


def test_secret_shaped_members_are_counted_but_not_content_hashed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "README.md").write_text("visible\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=do-not-capture\n", encoding="utf-8")
    registry = tmp_path / "authority.sqlite"
    receipt = register_source_batch(registry, [_spec(source, 1)])

    with sqlite3.connect(registry) as connection:
        connection.row_factory = sqlite3.Row
        secret = connection.execute(
            "SELECT * FROM source_member WHERE member_path='.env'"
        ).fetchone()
        assert secret is not None
        assert secret["policy_state"] == "EXCLUDED"
        assert secret["policy_reason"] == "SECRET_SHAPED_BASENAME"
        assert secret["sha256"] is None
        event_text = "\n".join(
            row[0]
            for row in connection.execute("SELECT event_json FROM registry_event")
        )
    assert "do-not-capture" not in event_text
    assert receipt["source_payloads_copied"] is False


def test_excluded_member_rename_changes_path_size_identity(tmp_path: Path) -> None:
    source = tmp_path / "project"
    source.mkdir()
    secret = source / ".env"
    secret.write_text("TOKEN=redacted\n", encoding="utf-8")
    registry = tmp_path / "authority.sqlite"
    receipt = register_source_batch(registry, [_spec(source, 1)])
    secret.rename(source / ".env.local")

    with pytest.raises(EvidenceLaneError) as stale:
        verify_source_batch_unchanged(registry, receipt["batch_id"])
    assert stale.value.code == "SOURCE_AUTHORITY_BATCH_CHANGED"


def test_source_intake_governed_registry_is_explicit_and_pointer_neutral(
    service, source_repository: Path
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    before = service.store.pointer("book-faires").as_dict()

    result = service.source_intake(
        "book-faires",
        [str(source_repository)],
        session_id=session_id,
        authority_mode="GOVERNED_CONTENT_REGISTRY",
        source_assertions={
            str(source_repository): {"reason_for_presence": "current code authority"}
        },
    )
    after = service.store.pointer("book-faires").as_dict()

    assert result["authority_mode"] == "GOVERNED_CONTENT_REGISTRY"
    assert result["source_authority"]["source_count"] == 1
    assert result["source_authority"]["source_bytes_mutated"] is False
    assert result["candidate_created"] is False
    assert result["pointer_moved"] is False
    assert before == after
    session = service.sessions.load("book-faires", session_id)
    classified = session.metadata["classified_source_authority"]
    assert classified["batch_id"] == result["source_authority"]["batch_id"]
    assert classified["armed_for_candidate"] is False


def test_reviewed_crosswalk_binds_every_occurrence_without_rehashing(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [_spec(first, 1, "docs"), _spec(second, 2, "research")],
    )
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text(
        """{
          "schema": "evidence-lane.all-source-authority-crosswalk.v1",
          "sources": [
            {
              "ordinal": 1,
              "name": "first.txt",
              "reason_for_presence": "Fixture one.",
              "planned_use": "Read only.",
              "rejected_use": "No mutation.",
              "license_state": "TEST"
            },
            {
              "ordinal": 2,
              "name": "second.txt",
              "reason_for_presence": "Fixture two.",
              "planned_use": "Read only.",
              "rejected_use": "No mutation.",
              "license_state": "TEST",
              "user_stated_provenance": "Fixture author statement."
            }
          ]
        }\n""",
        encoding="utf-8",
    )

    receipt = register_source_crosswalk(registry, batch["batch_id"], crosswalk)
    repeated = register_source_crosswalk(registry, batch["batch_id"], crosswalk)
    loaded = load_source_batch(registry, batch["batch_id"])

    assert receipt["append_status"] == "APPENDED"
    assert receipt["source_count"] == 2
    assert receipt["claim_count"] == 9
    assert repeated["append_status"] == "IDEMPOTENT_REUSE"
    assert len(loaded["assertion_sets"]) == 1
    assert {row["authority"] for row in loaded["provenance"]} == {
        "RESEARCH_ASSESSMENT",
        "USER_STATED",
    }


def test_classification_only_never_creates_registry(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("classification only\n", encoding="utf-8")
    registry = tmp_path / "must-not-exist.sqlite"
    result = classify_source_intake(
        [str(source)],
        code_mode="local_code",
        authority_mode="CLASSIFICATION_ONLY",
        authority_registry_path=registry,
    )
    assert result["source_authority"]["status"] == "NOT_REQUESTED"
    assert result["local_registry_mutated"] is False
    assert not registry.exists()


@pytest.mark.skipif(
    not Path("F:/DATA" + " MACHINE").is_dir(),
    reason="The opt-in real DATA " + "MACHINE corpus is unavailable.",
)
def test_data_machine_top_level_authority_contract_is_available_for_opt_in() -> None:
    """Cheap preflight only; additions do not rewrite the sealed 48-source registry."""

    sources = sorted(
        Path("F:/DATA" + " MACHINE").iterdir(),
        key=lambda row: row.name.casefold(),
    )
    assert len(sources) >= 48
    assert sum(row.is_dir() for row in sources) >= 28
    assert sum(row.is_file() for row in sources) >= 20
    assert (
        sum(row.is_file() and row.suffix.casefold() == ".zip" for row in sources) >= 9
    )


def test_repository_all_source_crosswalk_has_exact_48_ordered_rows() -> None:
    root = Path(__file__).resolve().parents[1]
    crosswalk_path = (
        root / "evidence" / "implementation_v41" / "ALL_SOURCE_AUTHORITY_CROSSWALK.json"
    )
    import json

    payload = json.loads(crosswalk_path.read_text(encoding="utf-8"))
    assert payload["expected_source_count"] == 48
    assert len(payload["sources"]) == 48
    assert [row["ordinal"] for row in payload["sources"]] == list(range(1, 49))
    assert len({row["name"] for row in payload["sources"]}) == 48
    assert all(row["reason_for_presence"] for row in payload["sources"])
    assert all(row["planned_use"] for row in payload["sources"])
    assert all(row["rejected_use"] for row in payload["sources"])
