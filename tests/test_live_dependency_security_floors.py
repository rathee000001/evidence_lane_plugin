from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

    adapter = ROOT / "apps" / "evidence-lane-app"
    adapter_package = json.loads(
        (adapter / "package.json").read_text(encoding="utf-8")
    )
    adapter_lock = (adapter / "pnpm-lock.yaml").read_text(encoding="utf-8")
    assert adapter_package["dependencies"]["next"] == "16.3.1"
    assert "next@16.3.1:" in adapter_lock
    assert "postcss@8.5.23:" in adapter_lock
    assert "nanoid@3.3.18:" in adapter_lock
    assert "sharp@0.35.3:" in adapter_lock
