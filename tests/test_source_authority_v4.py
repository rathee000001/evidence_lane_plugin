"""Retained source-evidence regressions against the separate Sources authority."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError, LaneError
from evidence_lane_plugin.migrations import Migration, apply_migrations, read_compatibility
from evidence_lane_plugin.source_authority import (
    SOURCES_MIGRATIONS,
    SourceAuthoritySpec,
    initialize_source_authority_registry,
    load_source_batch,
    register_source_batch,
    register_source_crosswalk,
    snapshot_source_authority_registry,
    verify_source_batch_unchanged,
)
from evidence_lane_plugin.storage import LaneStore, ProjectStore
from evidence_lane_plugin.writers import WriterLease


def _registry(tmp_path):
    source = tmp_path / 'selected-worktree'
    source.mkdir()
    return ProjectStore.create(tmp_path / 'state', source).lane('sources')


def _spec(source, ordinal, lane='local_code'):
    return SourceAuthoritySpec(source=str(source), ordinal=ordinal, lane_id=lane)


def test_schema_and_registered_evidence_have_one_explicit_owner(tmp_path):
    sources = _registry(tmp_path)
    original = tmp_path / 'evidence.txt'
    original.write_text('Selected evidence')
    original_bytes = original.read_bytes()
    with WriterLease(sources.project, 'fixture') as writer:
        result = register_source_batch(sources, [_spec(original, 1, 'docs')], writer=writer)
    with sources.project.connection(read_only=True) as root:
        assert root.execute("SELECT 1 FROM sqlite_schema WHERE name='source_object'").fetchone() is None
    with sources.connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM source_object').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM schema_ownership WHERE owner="sources"').fetchone()[0] >= 43
        assert connection.execute('SELECT COUNT(*) FROM schema_history_files WHERE owner="sources"').fetchone()[0] == 2
    with sources.project.lane('receipts').connection(read_only=True) as connection:
        receipts = [json.loads(row[0]) for row in connection.execute("SELECT body_json FROM receipts WHERE kind='source_authority_operation'")]
    assert len(receipts) == 2
    registration = next(row for row in receipts if row['operation'] == 'register_source_batch')
    assert json.loads(sources.read_object(registration['content_digest'])) == result
    assert original.read_bytes() == original_bytes
    heads = {item['lane_id']: item['commit_id'] for item in sources.project.lane_catalog()}
    assert heads['sources'] == heads['receipts']
    assert read_compatibility(sources, SOURCES_MIGRATIONS)[0]['status'] == 'compatible'


def test_source_read_never_initializes_or_changes_bytes(tmp_path):
    sources = _registry(tmp_path)
    def hashes():
        return {str(path.relative_to(sources.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sources.root.rglob('*') if path.is_file()}
    before = hashes()
    assert snapshot_source_authority_registry(sources)['status'] == 'NOT_INITIALIZED'
    assert hashes() == before
    initialize_source_authority_registry(sources)
    before = hashes()
    readonly = ProjectStore(sources.root, read_only=True).lane('sources')
    assert snapshot_source_authority_registry(readonly)['batch_count'] == 0
    assert read_compatibility(readonly, SOURCES_MIGRATIONS)[0]['status'] == 'compatible'
    assert hashes() == before
    with pytest.raises(LaneError) as error:
        register_source_batch(readonly, [])
    assert error.value.code == 'READ_ONLY_PROJECT'


def test_new_batch_reusing_source_object_keeps_one_fts_row_per_member(tmp_path):
    sources = _registry(tmp_path)
    source = sources.source_root / 'module.py'
    source.write_bytes(b'def example(): return 1\n')
    specs = [SourceAuthoritySpec(str(sources.source_root), 1, 'local_code')]
    with WriterLease(sources.project, 'fixture') as writer:
        first = register_source_batch(sources, specs, writer=writer)
        second = register_source_batch(sources, [SourceAuthoritySpec(str(sources.source_root), 1, 'local_code',
            assertions={'label': 'Additional attributed context'})], writer=writer)
    assert first['batch_id'] != second['batch_id']
    with sources.connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM intake_batch').fetchone()[0] == 2
        assert connection.execute('SELECT COUNT(*) FROM source_object').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM source_member').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM source_authority_fts').fetchone()[0] == 1
    assert source.read_bytes() == b'def example(): return 1\n'


def test_source_and_receipts_rollback_if_final_evidence_publication_fails(tmp_path, monkeypatch):
    sources = _registry(tmp_path)
    initialize_source_authority_registry(sources)
    source = tmp_path / 'source.txt'
    source.write_text('Preserve me')
    original = LaneStore.append_receipt
    def fail(self, kind, body, **kwargs):
        receipt = original(self, kind, body, **kwargs)
        if kind == 'source_authority_operation' and body['operation'] == 'register_source_batch':
            raise OSError('Injected failure after Source and Receipt writes')
        return receipt
    before = sources.project.pv_head()
    monkeypatch.setattr(LaneStore, 'append_receipt', fail)
    with pytest.raises(OSError, match='Injected failure'):
        register_source_batch(sources, [_spec(source, 1, 'docs')])
    assert sources.project.pv_head() == before
    with sources.connection(read_only=True) as connection:
        for table in ('source_object', 'source_occurrence', 'intake_batch', 'registry_event', 'objects'):
            assert connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
    with sources.project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM receipts WHERE kind='source_authority_operation'").fetchone() is None
    assert source.read_text() == 'Preserve me'


@pytest.mark.parametrize('lane', ['plan', 'brain_loader', 'sqlite_brain', 'project_engulf', 'discussion'])
def test_source_registration_cannot_revive_removed_or_authority_sectors(tmp_path, lane):
    sources = _registry(tmp_path)
    source = tmp_path / 'source.txt'
    source.write_text('Source evidence')
    with pytest.raises(LaneError) as error:
        register_source_batch(sources, [_spec(source, 1, lane)])
    assert error.value.code == 'SOURCE_SECTOR_REQUIRED'
    assert snapshot_source_authority_registry(sources)['status'] == 'NOT_INITIALIZED'


def test_sources_prefix_contract_does_not_allow_other_owner_objects(tmp_path):
    sources = _registry(tmp_path)
    with pytest.raises(LaneError, match='migration failed'):
        apply_migrations(sources, [Migration('sources', 1, 'Forbidden cross-owner write', ('CREATE TABLE plan_forged(x)',))])
    with pytest.raises(LaneError, match='Sources lane'):
        initialize_source_authority_registry(sources.database)
    with pytest.raises(LaneError, match='Sources lane'):
        initialize_source_authority_registry(sources.project.lane('memory'))


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
    registry = _registry(tmp_path)

    receipt = register_source_batch(
        registry,
        [
            _spec(extracted, 1),
            _spec(archive, 2, "local_code"),
            _spec(standalone, 3, "custom"),
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
    registry = _registry(tmp_path)

    receipt = register_source_batch(
        registry,
        [_spec(supplied, 1), _spec(archive, 2, "local_code")],
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


def test_registry_is_idempotent_and_root_projection_is_reproducible(
    tmp_path: Path,
) -> None:
    source = tmp_path / "evidence.txt"
    source.write_text("same bytes\n", encoding="utf-8")
    registry = _registry(tmp_path)
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
    registry = _registry(tmp_path)
    receipt = register_source_batch(registry, [_spec(source, 1, "docs")])
    source.write_text("after\n", encoding="utf-8")

    with pytest.raises(EvidenceLaneError) as stale:
        verify_source_batch_unchanged(registry, receipt["batch_id"])
    assert stale.value.code == "SOURCE_AUTHORITY_BATCH_CHANGED"
    assert stale.value.status == "STALE"


def test_secret_shaped_members_are_aggregated_without_path_or_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    (source / "README.md").write_text("visible\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=do-not-capture\n", encoding="utf-8")
    registry = _registry(tmp_path)
    receipt = register_source_batch(registry, [_spec(source, 1)])

    with registry.connection(read_only=True) as connection:
        secret = connection.execute(
            "SELECT * FROM source_member WHERE member_path='.env'"
        ).fetchone()
        assert secret is None
        summary = connection.execute(
            "SELECT * FROM source_exclusion_summary "
            "WHERE policy_reason='SECRET_SHAPED_BASENAME'"
        ).fetchone()
        assert summary is not None
        assert summary["excluded_entry_count"] == 1
        assert summary["member_paths_stored"] == 0
        event_text = "\n".join(
            row[0]
            for row in connection.execute("SELECT event_json FROM registry_event")
        )
    assert "do-not-capture" not in event_text
    assert receipt["source_payloads_copied"] is False


def test_excluded_member_rename_with_same_policy_class_keeps_identity(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    source.mkdir()
    secret = source / ".env"
    secret.write_text("TOKEN=redacted\n", encoding="utf-8")
    registry = _registry(tmp_path)
    receipt = register_source_batch(registry, [_spec(source, 1)])
    secret.rename(source / ".env.local")

    assert verify_source_batch_unchanged(registry, receipt["batch_id"])["status"] == "PASS"


def test_runtime_directory_is_pruned_and_aggregated_without_member_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    runtime = source / ".venv" / "Lib" / "site-packages" / "example"
    runtime.mkdir(parents=True)
    for index in range(20):
        (runtime / f"module_{index}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "app.py").write_text("print('included')\n", encoding="utf-8")
    registry = _registry(tmp_path)

    receipt = register_source_batch(registry, [_spec(source, 1)])

    with registry.connection(read_only=True) as connection:
        paths = [row[0] for row in connection.execute("SELECT member_path FROM source_member")]
        summary = connection.execute(
            "SELECT excluded_entry_count,descendant_members_enumerated,member_paths_stored "
            "FROM source_exclusion_summary WHERE policy_reason='RUNTIME_DIRECTORY'"
        ).fetchone()
    assert paths == ["app.py"]
    assert tuple(summary) == (1, 0, 0)
    assert receipt["source_payloads_copied"] is False


def test_reviewed_crosswalk_binds_every_occurrence_without_rehashing(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    registry = _registry(tmp_path)
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
