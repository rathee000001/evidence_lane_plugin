from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "plugins" / "evidence-lane-plugin" / "scripts" / "codex_release"
EXACT_BUILDER = RELEASE / "build_codex_exact_commit_package.py"
AUTHORITY_BUILDER = RELEASE / "seal_codex_git_ci_release_authority.py"
EXTERNAL_RECEIPTS = RELEASE / "seal_external_release_receipts.py"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _self_seal(value: dict[str, object]) -> dict[str, object]:
    value["receipt_sha256"] = hashlib.sha256(_json_bytes(value)).hexdigest().upper()
    return value


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_exact_commit_builder_exports_only_the_named_git_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module(EXACT_BUILDER, "exact_commit_builder")
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    _git(repository, "config", "user.email", "fixture@example.test")
    _git(repository, "config", "user.name", "Fixture")
    plugin = repository / "plugins" / "evidence-lane-plugin"
    plugin.mkdir(parents=True)
    (plugin / "marker.txt").write_text("committed\n", encoding="utf-8")
    _git(repository, "add", "plugins/evidence-lane-plugin/marker.txt")
    _git(repository, "commit", "-m", "fixture")
    _git(repository, "branch", "-M", "main")
    commit = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    _git(repository, "update-ref", "refs/remotes/origin/main", commit)
    (plugin / "marker.txt").write_text("dirty checkout\n", encoding="utf-8")
    (plugin / "untracked.txt").write_text("excluded\n", encoding="utf-8")
    fingerprint_refresh = tmp_path / "executable-fingerprint-refresh.v1.json"
    fingerprint_refresh.write_text("{}\n", encoding="utf-8")

    def fake_rehearsal(**arguments: object) -> dict[str, object]:
        exported = Path(str(arguments["plugin_root"]))
        assert (exported / "marker.txt").read_text("utf-8") == "committed\n"
        assert not (exported / "untracked.txt").exists()
        assert arguments["systemwide_route_audit_receipt"] is None
        assert arguments["executable_fingerprint_refresh_receipt"] == (
            fingerprint_refresh
        )
        output = Path(str(arguments["output_dir"]))
        output.mkdir(parents=True, exist_ok=True)
        archive = output / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("marker.txt", b"committed\n")
        receipt = {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.receipt"
            ),
            "boundary": "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL",
            "status": "PASS",
            "archive": {
                "filename": archive.name,
                "bytes": archive.stat().st_size,
                "sha256": _sha256(archive),
                "member_count": 1,
            },
            "base_anchor": {"commit": commit, "tree": tree},
            "working_source_manifest_sha256": "A" * 64,
            "source_member_count": 1,
            "skill_count": 15,
            "canonical_lane_count": 18,
            "governed_candidate_created": False,
            "git_invoked": False,
            "accepted_pointer_moved": False,
        }
        receipt_path = output / "LOCAL_PACKAGE_REHEARSAL.json"
        _write_json(receipt_path, receipt)
        return {**receipt, "receipt_path": str(receipt_path)}

    monkeypatch.setattr(module, "build_rehearsal", fake_rehearsal)
    result = module.build_exact_commit_package(
        repository=repository,
        plugin_path="plugins/evidence-lane-plugin",
        branch="main",
        commit=commit,
        output_dir=tmp_path / "output",
        expected_version="2.1.0+codex.fixture",
        executable_fingerprint_refresh_receipt=fingerprint_refresh,
    )

    assert result["status"] == "PASS"
    assert result["base_anchor"] == {"commit": commit, "tree": tree}
    assert result["exact_commit_export"]["projection_clean"] is True
    assert result["exact_commit_export"]["working_checkout_bytes_used"] is False
    assert result["exact_commit_export"]["untracked_bytes_used"] is False
    assert result["exact_commit_export"]["plugin_source_member_count"] == 1
    assert len(
        result["exact_commit_export"]["plugin_source_manifest_sha256"]
    ) == 64
    assert result["git_write_invoked"] is False
    assert len(result["receipt_sha256"]) == 64


def _authority_inputs(tmp_path: Path) -> dict[str, Path | str]:
    archive = tmp_path / "package.zip"
    archive.write_bytes(b"exact package")
    commit = "1" * 40
    tree = "2" * 40
    branch = "main"
    package = _self_seal(
        {
            "schema": "evidence-lane.codex-exact-commit-package.v1.receipt",
            "boundary": "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED",
            "status": "PASS",
            "archive": {"filename": archive.name, "sha256": _sha256(archive)},
            "base_anchor": {"commit": commit, "tree": tree},
            "working_source_manifest_sha256": "A" * 64,
            "exact_commit_export": {
                "branch": branch,
                "commit": commit,
                "tree": tree,
                "source_ref": "refs/remotes/origin/main",
                "stable_main_only": True,
                "local_main_attested": True,
                "origin_main_attested": True,
                "plugin_path": "plugins/evidence-lane-plugin",
                "git_archive_sha256": "D" * 64,
                "git_archive_member_count": 1,
                "plugin_source_manifest_sha256": "E" * 64,
                "plugin_source_member_count": 1,
                "projection_clean": True,
                "working_checkout_bytes_used": False,
                "untracked_bytes_used": False,
            },
            "governed_candidate_created": False,
            "accepted_pointer_moved": False,
        }
    )
    package_path = tmp_path / "package.json"
    _write_json(package_path, package)
    remote = {
        "schema": "evidence-lane.github-app-main-merge.v1",
        "action_id": "remote_action_fixture",
        "route": "GITHUB_APP_SDK",
        "action": "MERGE_TO_MAIN",
        "status": "EXECUTED",
        "merge": {
            "source_branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
            "target_branch": "main",
            "merge_commit": commit,
            "merge_tree": tree,
        },
        "repository_identity": {
            "branch": branch,
            "commit_sha": commit,
            "tree_sha": tree,
        },
        "authorization": {
            "policy": "GOVERNED_FEATURE_TO_MAIN_MERGE",
            "direct_main_push_authorized": False,
            "merge_authorized": True,
        },
        "output_security": {
            "infrastructure_status": "PASS",
            "receipt_sha256": "B" * 64,
        },
    }
    remote_path = tmp_path / "remote.json"
    _write_json(remote_path, remote)
    checks = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "head_sha": commit,
        }
        for name in ("python-ci", "codeql")
    ]
    ci = _self_seal(
        {
            "schema": "evidence-lane.github-ci-exact-head.v1",
            "status": "PASS",
            "repository": "owner/repository",
            "branch": branch,
            "head_sha": commit,
            "clean_checkout": True,
            "required_check_names": ["python-ci", "codeql"],
            "checks": checks,
        }
    )
    ci_path = tmp_path / "ci.json"
    _write_json(ci_path, ci)
    preview = _self_seal(
        {
            "schema": "evidence-lane.vercel-git-preview-exact-head.v1",
            "status": "PASS",
            "project_id": "prj_fixture",
            "team_id": "team_fixture",
            "source": {
                "repository": "owner/repository",
                "branch": branch,
                "head_sha": commit,
            },
            "deployment": {
                "deployment_id": "dpl_fixture",
                "url": "fixture.vercel.app",
                "state": "READY",
                "target": "PREVIEW",
            },
            "git_integration": True,
            "manual_deploy": False,
            "production_deployment": False,
            "prior_successful_preview": {
                "deployment_id": "dpl_prior",
                "state": "READY",
            },
        }
    )
    preview_path = tmp_path / "preview.json"
    _write_json(preview_path, preview)
    return {
        "archive": archive,
        "package_receipt": package_path,
        "package_receipt_sha256": _sha256(package_path),
        "remote_git_receipt": remote_path,
        "remote_git_receipt_sha256": _sha256(remote_path),
        "github_ci_receipt": ci_path,
        "github_ci_receipt_sha256": _sha256(ci_path),
        "vercel_preview_receipt": preview_path,
        "vercel_preview_receipt_sha256": _sha256(preview_path),
        "output": tmp_path / "authority.json",
    }


def test_release_authority_joins_exact_package_native_push_and_clean_ci(
    tmp_path: Path,
) -> None:
    module = _module(AUTHORITY_BUILDER, "release_authority_builder")
    arguments = _authority_inputs(tmp_path)

    result = module.seal_release_authority(**arguments)

    assert result["status"] == "PASS"
    assert result["source"]["exact_commit_projection_clean"] is True
    assert result["source"]["working_checkout_clean_required"] is False
    assert result["remote_git"]["route"] == "GITHUB_APP_SDK"
    assert result["remote_git"]["target_branch"] == "main"
    assert result["remote_git"]["protected_branch"] is True
    assert result["github_ci"]["required_check_count"] == 2
    assert result["github_ci"]["successful_check_count"] == 2
    assert result["github_ci"]["failed_check_count"] == 0
    assert result["vercel_preview"]["state"] == "READY"
    assert result["vercel_preview"]["target"] == "PREVIEW"
    assert result["vercel_preview"]["production_deployment"] is False
    assert len(result["receipt_sha256"]) == 64


def test_release_authority_rejects_a_failed_required_check(tmp_path: Path) -> None:
    module = _module(AUTHORITY_BUILDER, "release_authority_builder_failed_ci")
    arguments = _authority_inputs(tmp_path)
    ci_path = Path(str(arguments["github_ci_receipt"]))
    ci = json.loads(ci_path.read_text("utf-8"))
    ci.pop("receipt_sha256")
    ci["checks"][0]["conclusion"] = "failure"
    _self_seal(ci)
    _write_json(ci_path, ci)
    arguments["github_ci_receipt_sha256"] = _sha256(ci_path)

    with pytest.raises(module.ReleaseAuthorityError, match="do not join"):
        module.seal_release_authority(**arguments)


def test_release_authority_rejects_a_production_vercel_deployment(
    tmp_path: Path,
) -> None:
    module = _module(AUTHORITY_BUILDER, "release_authority_builder_prod_preview")
    arguments = _authority_inputs(tmp_path)
    preview_path = Path(str(arguments["vercel_preview_receipt"]))
    preview = json.loads(preview_path.read_text("utf-8"))
    preview.pop("receipt_sha256")
    preview["production_deployment"] = True
    _self_seal(preview)
    _write_json(preview_path, preview)
    arguments["vercel_preview_receipt_sha256"] = _sha256(preview_path)

    with pytest.raises(module.ReleaseAuthorityError, match="do not join"):
        module.seal_release_authority(**arguments)


def test_external_receipts_seal_exact_github_and_null_target_preview(
    tmp_path: Path,
) -> None:
    module = _module(EXTERNAL_RECEIPTS, "external_release_receipts")
    commit = "3" * 40
    branch = "agent/evi-v210-test"
    checks = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "head_sha": commit,
        }
        for name in ("python-ci", "codeql", "preview-build")
    ]
    github_snapshot = tmp_path / "github-snapshot.json"
    _write_json(
        github_snapshot,
        {
            "schema": "evidence-lane.github-check-run-snapshot.v1",
            "provider": "GITHUB_APP_READ_ONLY",
            "repository": "owner/repository",
            "branch": branch,
            "head_sha": commit,
            "clean_checkout": True,
            "required_check_names": [row["name"] for row in checks],
            "checks": checks,
        },
    )
    github = module.seal_github_ci_snapshot(
        snapshot=github_snapshot,
        output=tmp_path / "github-receipt.json",
    )
    assert github["status"] == "PASS"
    assert github["head_sha"] == commit

    vercel_snapshot = tmp_path / "vercel-snapshot.json"
    _write_json(
        vercel_snapshot,
        {
            "schema": "evidence-lane.vercel-deployment-snapshot.v1",
            "provider": "VERCEL_APP_READ_ONLY",
            "project_id": "prj_fixture",
            "team_id": "team_fixture",
            "source": {
                "repository": "owner/repository",
                "branch": branch,
                "head_sha": commit,
            },
            "deployment": {
                "deployment_id": "dpl_fixture",
                "url": "fixture.vercel.app",
                "state": "READY",
                "target": None,
            },
            "git_integration": True,
            "manual_deploy": False,
            "production_deployment": False,
        },
    )
    preview = module.seal_vercel_preview_snapshot(
        snapshot=vercel_snapshot,
        output=tmp_path / "vercel-receipt.json",
    )
    assert preview["status"] == "PASS"
    assert preview["deployment"]["target"] == "PREVIEW"
    assert preview["deployment"]["provider_target"] is None


def test_external_receipts_reject_mixed_github_head(tmp_path: Path) -> None:
    module = _module(EXTERNAL_RECEIPTS, "external_release_receipts_mixed_head")
    snapshot = tmp_path / "github-snapshot.json"
    _write_json(
        snapshot,
        {
            "schema": "evidence-lane.github-check-run-snapshot.v1",
            "provider": "GITHUB_APP_READ_ONLY",
            "repository": "owner/repository",
            "branch": "agent/evi-v210-test",
            "head_sha": "4" * 40,
            "clean_checkout": True,
            "required_check_names": ["python-ci"],
            "checks": [
                {
                    "name": "python-ci",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": "5" * 40,
                }
            ],
        },
    )
    with pytest.raises(module.ExternalReceiptError, match="exact head"):
        module.seal_github_ci_snapshot(
            snapshot=snapshot,
            output=tmp_path / "github-receipt.json",
        )
