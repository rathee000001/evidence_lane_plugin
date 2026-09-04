from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SCRIPTS = ROOT / "plugins" / "evidence-lane-plugin" / "scripts"
INSTALL_SCRIPT = PLUGIN_SCRIPTS / "codex_release" / "install_codex_stable.py"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from build_release_candidate_rehearsal import (
    PackageBoundaryError,
    _load_executable_fingerprint_refresh,
)

SCOPED_STATUS = "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"
VERSION = "3.0.0+codex.scope-test"
DEFERRED_SELECTOR_COUNT = 5


def _install_module():
    spec = importlib.util.spec_from_file_location(
        "install_codex_stable_scope_deferral_test",
        INSTALL_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fingerprint_receipt() -> dict:
    return {
        "schema": "evidence-lane.executable-fingerprint-refresh.v1",
        "status": "PASS",
        "plugin_version": VERSION,
        "receipt_sha256": "A" * 64,
        "full_regression": {
            "status": SCOPED_STATUS,
            "authorized_run_count": 1,
        },
        "targeted_closure": {
            "status": "PASS",
            "failed": 0,
            "full_suite_rerun": False,
        },
        "publication_scope": {
            "status": "DEFERRED_NOT_PASSED",
            "publication_authorized": False,
            "deferred_selector_count": DEFERRED_SELECTOR_COUNT,
            "deferral_contract_file_sha256": "B" * 64,
        },
        "executable_surface": {
            "status": "PASS",
            "member_count": 1,
            "receipt_sha256": "C" * 64,
            "all_hash_bound": True,
            "local_cache_or_output_included": False,
            "historical_fallback_used": False,
        },
        "repository_fingerprints": {
            "status": "PASS",
            "receipt_sha256": "D" * 64,
        },
        "source_impact": {
            "status": "PASS",
            "receipt_sha256": "E" * 64,
            "all_changed_paths_mapped": True,
            "all_replacements_directly_purged": True,
            "orphaned_generated_member_count": 0,
        },
        "negative_proofs": {
            "accepted_archive_queried": False,
            "candidate_created_or_cleared": False,
            "pointer_moved": False,
            "project_or_pv_mutated": False,
            "git_index_mutated": False,
            "git_ref_mutated": False,
        },
    }


def _write_receipt(path: Path, receipt: dict) -> Path:
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def test_local_package_fingerprint_loader_accepts_exact_deferred_scope(
    tmp_path: Path,
) -> None:
    path = _write_receipt(tmp_path / "fingerprint.json", _fingerprint_receipt())

    compact, content = _load_executable_fingerprint_refresh(
        path,
        expected_version=VERSION,
    )

    assert content == path.read_bytes()
    assert compact is not None
    assert compact["full_regression_status"] == SCOPED_STATUS
    assert compact["publication_scope_status"] == "DEFERRED_NOT_PASSED"
    assert compact["publication_authorized"] is False
    assert compact["deferred_publication_selector_count"] == DEFERRED_SELECTOR_COUNT
    assert compact["deferral_contract_file_sha256"] == "B" * 64


@pytest.mark.parametrize(
    "publication_scope",
    [
        None,
        {
            "status": "DEFERRED_NOT_PASSED",
            "publication_authorized": True,
            "deferred_selector_count": DEFERRED_SELECTOR_COUNT,
            "deferral_contract_file_sha256": "B" * 64,
        },
        {
            "status": "PASS",
            "publication_authorized": False,
            "deferred_selector_count": DEFERRED_SELECTOR_COUNT,
            "deferral_contract_file_sha256": "B" * 64,
        },
        {
            "status": "DEFERRED_NOT_PASSED",
            "publication_authorized": False,
            "deferred_selector_count": 0,
            "deferral_contract_file_sha256": "B" * 64,
        },
        {
            "status": "DEFERRED_NOT_PASSED",
            "publication_authorized": False,
            "deferred_selector_count": DEFERRED_SELECTOR_COUNT,
            "deferral_contract_file_sha256": "not-a-sha256",
        },
    ],
)
def test_local_package_fingerprint_loader_rejects_missing_or_false_scope(
    tmp_path: Path,
    publication_scope: dict | None,
) -> None:
    receipt = _fingerprint_receipt()
    if publication_scope is None:
        receipt.pop("publication_scope")
    else:
        receipt["publication_scope"] = publication_scope
    path = _write_receipt(tmp_path / "fingerprint.json", receipt)

    with pytest.raises(PackageBoundaryError):
        _load_executable_fingerprint_refresh(path, expected_version=VERSION)


def test_local_install_gate_accepts_scoped_compact_evidence_and_rejects_publication(
    tmp_path: Path,
) -> None:
    path = _write_receipt(tmp_path / "fingerprint.json", _fingerprint_receipt())
    compact, _ = _load_executable_fingerprint_refresh(
        path,
        expected_version=VERSION,
    )
    assert compact is not None
    module = _install_module()

    accepted = module._require_local_executable_fingerprint_refresh(
        {"executable_fingerprint_refresh": compact}
    )
    assert accepted["full_regression_status"] == SCOPED_STATUS
    assert accepted["publication_scope_status"] == "DEFERRED_NOT_PASSED"
    assert accepted["publication_authorized"] is False

    unauthorized = copy.deepcopy(compact)
    unauthorized["publication_authorized"] = True
    with pytest.raises(module.InstallationError):
        module._require_local_executable_fingerprint_refresh(
            {"executable_fingerprint_refresh": unauthorized}
        )
