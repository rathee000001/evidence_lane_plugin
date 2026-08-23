from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import shutil
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


def test_exact_commit_builder_is_main_only_and_supports_fresh_package_identity() -> None:
    module = _load(BUILDER, "exact_commit_builder_main_only")
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
            "main",
            "--commit",
            "a" * 40,
            "--output-dir",
            str(ROOT / ".tmp-test-output"),
            "--expected-version",
            "3.0.0+codex.source",
            "--package-version",
            "3.0.0+codex.main.r266",
        ]
    )
    assert parsed.branch == "main"
    assert parsed.package_version == "3.0.0+codex.main.r266"
    with pytest.raises(module.ExactCommitPackageError, match="only from exact verified main"):
        module.build_exact_commit_package(
            repository=ROOT,
            plugin_path="plugins/evidence-lane-plugin",
            branch="agent/test",
            commit="a" * 40,
            output_dir=ROOT / ".tmp-test-output",
            expected_version="3.0.0+codex.source",
        )


def test_main_package_inventory_accepts_only_exact_codex_command_migration(
    tmp_path: Path,
) -> None:
    module = _load(INSTALLER, "main_generated_command_inventory")
    marketplace = tmp_path / "marketplace"
    installed = tmp_path / "installed"
    command = marketplace / "commands" / "evi-learning.md"
    command.parent.mkdir(parents=True)
    command.write_text(
        "---\ndescription: Query project learning\n---\n\n"
        "Use the bounded learning route.\n",
        encoding="utf-8",
    )
    shutil.copytree(marketplace, installed)
    expected = module._expected_codex_generated_command_skills(marketplace)
    assert len(expected) == 1
    for relative, content in expected.items():
        target = installed / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(content)

    package_inventory = module._source_inventory(marketplace)
    installed_inventory = module._source_inventory(installed)
    assert package_inventory["manifest_sha256"] == installed_inventory[
        "manifest_sha256"
    ]
    assert installed_inventory[
        "ignored_codex_generated_migration_artifact_count"
    ] == 1
    verified = module._verify_codex_generated_command_skills(
        installed_cache=installed,
        marketplace_plugin=marketplace,
    )
    assert verified["status"] == "PASS"
    assert verified["derivable_skill_count"] == 1
    assert verified["generated_skill_count"] == 1
    assert verified["host_selected_derivable_subset"] is True

    generated = next(
        (installed / module.CODEX_GENERATED_MIGRATED_COMMAND_ROOT).rglob(
            "SKILL.md"
        )
    )
    generated.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(module.InstallationError, match="exact derivation"):
        module._verify_codex_generated_command_skills(
            installed_cache=installed,
            marketplace_plugin=marketplace,
        )


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
        "package_version": "3.0.0+codex.main.r266",
        "local_rehearsal_receipt_sha256": module._sha256(package_receipt),
        "exact_commit_export": {
            "branch": "main",
            "source_ref": "refs/remotes/origin/main",
            "commit": "a" * 40,
            "tree": "b" * 40,
            "projection_clean": True,
            "working_checkout_bytes_used": False,
            "untracked_bytes_used": False,
            "stable_main_only": True,
            "local_main_attested": True,
            "origin_main_attested": True,
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
    module = _load(INSTALLER, "main_package_installer")
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


def test_two_slot_registry_is_exact_main_plus_versioned_local(tmp_path: Path) -> None:
    module = _load(INSTALLER, "main_local_two_slot_contract")
    stable = tmp_path / "stable"
    local = tmp_path / "local"
    stable.mkdir()
    local.mkdir()
    (stable / "marker.txt").write_text("stable\n", encoding="utf-8")
    (local / "marker.txt").write_text("local\n", encoding="utf-8")
    rows = []
    for role, selector in module.TWO_SLOT_SELECTORS.items():
        root = stable if role == "stable-git-main" else local
        rows.append(
            {
                "pluginId": selector,
                "version": "3.0.0+codex.20260821000000.main",
                "marketplaceName": selector.split("@", 1)[1],
                "source": {"path": str(root)},
                "marketplaceSource": {
                    "sourceType": "git" if role == "stable-git-main" else "local"
                },
                "enabled": role == "versioned-local-testing",
            }
        )
    receipt = module._materialize_two_slot_registry(
        plugin_list={"installed": rows},
        codex_home=tmp_path / "codex-home",
        data_root=tmp_path / "data",
        active_slot="versioned-local-testing",
        config_sha256="A" * 64,
    )
    assert receipt["schema"] == module.TWO_SLOT_REGISTRY_SCHEMA
    assert receipt["exact_live_slot_count"] == 2
    assert set(receipt["slots"]) == {
        "stable-git-main",
        "versioned-local-testing",
    }
    assert receipt["failure_target_slot"] == "stable-git-main"
    assert receipt["branch_recovery_install_allowed"] is False
    assert module.LOCAL_RECOVERY_SELECTOR in module.OBSOLETE_LIVE_SELECTORS
    assert module.LOCAL_RECOVERY_SELECTOR not in module.TWO_SLOT_SELECTORS.values()
    with pytest.raises(module.InstallationError, match="three-slot/branch-recovery"):
        module._materialize_three_slot_registry()


def test_branch_recovery_flags_are_permanently_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(INSTALLER, "retired_branch_checkpoint_route")
    monkeypatch.setattr(
        "sys.argv",
        ["install_codex_stable.py", "--materialize-branch-checkpoint"],
    )
    with pytest.raises(module.InstallationError, match="Branch/local recovery slot creation is retired"):
        module.main()
    source = inspect.getsource(module.main)
    assert "_materialize_local_recovery_copy(args)" not in source
