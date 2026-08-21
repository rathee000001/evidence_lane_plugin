from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "plugins" / "evidence-lane-plugin" / "scripts" / "codex_release"
INSTALLER = RELEASE / "install_codex_stable.py"
BUILDER = RELEASE / "build_codex_exact_commit_package.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_commit_builder_supports_fresh_package_identity() -> None:
    module = _load(BUILDER, "exact_commit_builder_branch_checkpoint")
    assert "package_version" in inspect.signature(
        module.build_exact_commit_package
    ).parameters
    parsed = module._parser().parse_args(
        [
            "--repository",
            str(ROOT),
            "--plugin-path",
            "plugins/evidence-lane-plugin",
            "--branch",
            "agent/test",
            "--commit",
            "a" * 40,
            "--output-dir",
            str(ROOT / ".tmp-test-output"),
            "--expected-version",
            "3.0.0+codex.source",
            "--package-version",
            "3.0.0+codex.branch.r249",
        ]
    )
    assert parsed.package_version == "3.0.0+codex.branch.r249"


def _exact_receipt_fixture(module, tmp_path: Path) -> tuple[Path, Path, Path, str]:
    data_root = tmp_path / "data"
    authority = (
        data_root
        / "installations"
        / "codex-v300"
        / "exact-commit-packages"
        / "r249"
    )
    authority.mkdir(parents=True)
    archive = authority / "package.zip"
    archive.write_bytes(b"exact-package")
    archive_sha = module._sha256(archive)
    package_receipt = authority / "LOCAL_PACKAGE_REHEARSAL.json"
    package_receipt.write_text(
        json.dumps({"archive": {"sha256": archive_sha}}) + "\n",
        encoding="utf-8",
    )
    core = {
        "schema": "evidence-lane.codex-exact-commit-package.v1.receipt",
        "boundary": "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED",
        "status": "PASS",
        "archive": {"sha256": archive_sha},
        "package_version": "3.0.0+codex.branch.r249",
        "local_rehearsal_receipt_sha256": module._sha256(package_receipt),
        "exact_commit_export": {
            "branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
            "commit": "a" * 40,
            "tree": "b" * 40,
            "projection_clean": True,
            "working_checkout_bytes_used": False,
            "untracked_bytes_used": False,
        },
        "git_write_invoked": False,
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "hil_inferred": False,
    }
    receipt = authority / "EXACT_COMMIT_PACKAGE.json"
    receipt.write_text(
        json.dumps(
            {
                **core,
                "receipt_sha256": hashlib.sha256(
                    module._json_bytes(core)
                ).hexdigest().upper(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return data_root, archive, package_receipt, module._sha256(receipt)


def test_exact_commit_receipt_is_bounded_and_tamper_evident(tmp_path: Path) -> None:
    module = _load(INSTALLER, "branch_checkpoint_installer")
    data_root, archive, package_receipt, receipt_sha = _exact_receipt_fixture(
        module, tmp_path
    )
    receipt = next(
        (data_root / "installations" / "codex-v300").rglob(
            "EXACT_COMMIT_PACKAGE.json"
        )
    )
    verified = module._load_exact_commit_package_receipt(
        receipt_path=receipt,
        receipt_file_sha256=receipt_sha,
        archive=archive,
        package_receipt_path=package_receipt,
        data_root=data_root,
    )
    assert verified["status"] == "PASS"
    assert verified["exact_commit_export"]["commit"] == "a" * 40

    with pytest.raises(module.InstallationError):
        module._load_exact_commit_package_receipt(
            receipt_path=receipt,
            receipt_file_sha256="0" * 64,
            archive=archive,
            package_receipt_path=package_receipt,
            data_root=data_root,
        )


def test_branch_checkpoint_mode_preserves_active_and_main_slots() -> None:
    module = _load(INSTALLER, "branch_checkpoint_mode_contract")
    source = inspect.getsource(module._materialize_local_recovery_copy)
    assert "EXACT_GOVERNED_BRANCH_COMMIT" in source
    assert "recovery_convergence_authorized=not branch_checkpoint" in source
    assert '"exact_governed_branch_commit": branch_checkpoint' in source
    assert '"branch_commit_git_recovery_authority": branch_checkpoint' in source
    assert '"production_delivery_authority": False' in source
    assert "two_slot_after != two_slot_before" in source
