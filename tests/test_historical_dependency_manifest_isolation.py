from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "evidence"
RECEIPT_PATH = (
    EVIDENCE_ROOT
    / "task9-r253-historical-dependency-manifest-isolation-receipt.json"
)

DEPENDENCY_MANIFEST_NAMES = frozenset(
    {
        "Cargo.lock",
        "Cargo.toml",
        "Gemfile",
        "Gemfile.lock",
        "Pipfile",
        "Pipfile.lock",
        "build.gradle",
        "bun.lock",
        "bun.lockb",
        "composer.json",
        "composer.lock",
        "go.mod",
        "go.sum",
        "mix.exs",
        "mix.lock",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pom.xml",
        "pyproject.toml",
        "requirements.in",
        "requirements.lock",
        "requirements.lock.txt",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
        "yarn.lock",
    }
)


def _historical_dependency_manifests() -> list[Path]:
    manifests: list[Path] = []
    for directory, _, filenames in os.walk(EVIDENCE_ROOT, followlinks=False):
        parent = Path(directory)
        manifests.extend(
            parent / filename
            for filename in filenames
            if filename in DEPENDENCY_MANIFEST_NAMES
        )
    return sorted(manifests)


def test_historical_dependency_manifests_are_vendor_isolated() -> None:
    manifests = _historical_dependency_manifests()

    assert manifests
    assert all("vendor" in path.relative_to(EVIDENCE_ROOT).parts for path in manifests)

    active_manifests = {
        ROOT / "pyproject.toml",
        ROOT / "plugins" / "evidence-lane-plugin" / "pyproject.toml",
        ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "requirements.lock.txt",
        ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "remote_adapter"
        / "package.json",
        ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "remote_adapter"
        / "pnpm-lock.yaml",
    }
    assert all(path.is_file() for path in active_manifests)
    assert not any(EVIDENCE_ROOT in path.parents for path in active_manifests)


def test_live_dependency_manifests_hold_current_security_floors() -> None:
    root_project = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    plugin_project = tomllib.loads(
        (ROOT / "plugins" / "evidence-lane-plugin" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]
    requirements = (ROOT / "requirements.in").read_text(encoding="utf-8")
    lock = (
        ROOT / "plugins" / "evidence-lane-plugin" / "requirements.lock.txt"
    ).read_text(encoding="utf-8")

    for project in (root_project, plugin_project):
        dependencies = set(project["dependencies"])
        assert "cryptography==50.0.0" in dependencies
        assert "pypdf==6.15.0" in dependencies
        assert "cryptography==48.0.1" not in dependencies
        assert "pypdf==6.14.2" not in dependencies
    for manifest in (requirements, lock):
        assert "cryptography==50.0.0" in manifest
        assert "pypdf==6.15.0" in manifest
        assert "cryptography==48.0.1" not in manifest
        assert "pypdf==6.14.2" not in manifest

    adapter = ROOT / "plugins" / "evidence-lane-plugin" / "remote_adapter"
    adapter_package = json.loads(
        (adapter / "package.json").read_text(encoding="utf-8")
    )
    adapter_lock = (adapter / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert adapter_package["dependencies"]["next"] == "16.3.1"
    assert "next@16.3.1:" in adapter_lock
    assert "postcss@8.5.23:" in adapter_lock
    assert "nanoid@3.3.18:" in adapter_lock
    assert "sharp@0.35.3:" in adapter_lock


def test_historical_dependency_manifest_locator_receipt_is_self_sealed() -> None:
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    declared = receipt.pop("receipt_sha256")

    assert declared == sha256_bytes(canonical_json_bytes(receipt))
    assert receipt["accepted_authority"] == {
        "accepted_pv": "PV12",
        "generation": 12,
    }
    assert receipt["identity"]["tracked_entry_count"] == 7045
    assert receipt["identity"]["historical_manifest_count"] == 126
    assert receipt["checks"] == {
        "all_historical_manifests_under_vendor": True,
        "candidate_created": False,
        "git_commit_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "source_directories_absent_after_move": True,
    }
    for move in receipt["moves"]:
        assert not (ROOT / move["old_path"]).exists()
        assert (ROOT / move["new_path"]).is_dir()
