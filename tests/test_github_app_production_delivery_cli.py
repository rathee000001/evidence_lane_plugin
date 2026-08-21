from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "seal_github_app_production_delivery.py"
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_app_delivery_sealer", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request() -> dict[str, object]:
    commit = "a" * 40
    return {
        "schema": "evidence-lane.github-app-production-delivery-request.v1",
        "manifest": {
            "schema": "evidence-lane.github-app-manifest.v1",
            "app_slug": "evidence-lane-test",
            "manifest_version": "1",
            "repository_selection": "selected",
            "repository_permissions": {
                "metadata": "read",
                "actions": "read",
                "checks": "write",
            },
            "events": ["check_run", "workflow_run"],
            "public": False,
        },
        "installation_binding": {
            "binding_id": "binding-1",
            "installation_id": "installation-7",
            "project_id": "project-a",
            "task_id": "task-a",
            "accepted_pv": "PV12",
            "repositories": ["owner/repo"],
            "permissions": {
                "metadata": "read",
                "actions": "read",
                "checks": "write",
            },
            "expires_at": "2026-08-21T10:00:00Z",
        },
        "delivery": {
            "delivery_id": "delivery-1",
            "repository": "owner/repo",
            "branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
            "commit_sha": commit,
            "tree_sha": "b" * 40,
            "actions_run_id": "run-42",
            "actions_status": "completed",
            "actions_conclusion": "success",
            "actions_head_sha": commit,
            "package_id": "package-42",
            "package_version": "3.0.0+codex.20260821090000.git.aaaaaaaaaaaa",
            "package_sha256": "C" * 64,
            "package_source_commit": commit,
            "mutable_local_slot": "evidence-lane-v300-testing-new",
            "branch_commit_slot": "evidence-lane-v300-branch-stable",
            "main_merge_fallback_slot": "evidence-lane-github",
            "installed_version": "3.0.0+codex.20260821090000.git.aaaaaaaaaaaa",
            "installed_package_sha256": "C" * 64,
            "installed_surface_sha256": "D" * 64,
            "main_merge_fallback_before_sha256": "E" * 64,
            "main_merge_fallback_after_sha256": "E" * 64,
        },
    }


def test_public_sealer_is_immutable_and_non_mutating(tmp_path: Path) -> None:
    module = _module()
    request = tmp_path / "request.json"
    output = tmp_path / "receipt.json"
    request.write_text(json.dumps(_request()), encoding="utf-8")

    first = module.seal_file(request_path=request, output_path=output)
    second = module.seal_file(request_path=request, output_path=output)
    receipt = json.loads(output.read_text(encoding="utf-8"))

    assert first == second
    assert receipt["status"] == "PASS"
    assert receipt["commit_created"] is False
    assert receipt["ref_pushed"] is False
    assert receipt["installation_performed_by_contract"] is False
    assert receipt["pointer_moved"] is False
    assert receipt["hil_inferred"] is False


def test_public_sealer_refuses_changed_replay(tmp_path: Path) -> None:
    module = _module()
    request = tmp_path / "request.json"
    output = tmp_path / "receipt.json"
    value = _request()
    request.write_text(json.dumps(value), encoding="utf-8")
    module.seal_file(request_path=request, output_path=output)

    value["delivery"]["installed_surface_sha256"] = "F" * 64  # type: ignore[index]
    request.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.ProductionDeliverySealError):
        module.seal_file(request_path=request, output_path=output)
