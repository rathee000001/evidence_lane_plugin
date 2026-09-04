from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.plugin_build_identity import (
    PluginBuildIdentityError,
    resolve_plugin_build_identity,
)


def _write_manifest(root: Path, version: str) -> Path:
    path = root / ".codex-plugin" / "plugin.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": "evidence-lane-plugin", "version": version}) + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    payload = path.read_bytes()
    if b"\0" not in payload[:8000]:
        payload = payload.replace(b"\r\n", b"\n")
    return hashlib.sha256(payload).hexdigest().upper()


def test_base_only_manifest_is_not_an_exact_plugin_build(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "3.0.0")

    with pytest.raises(PluginBuildIdentityError, match="PLUGIN_EXACT_VERSION_INVALID"):
        resolve_plugin_build_identity(tmp_path, expected_base_release="3.0.0")


def test_double_cachebuster_manifest_is_rejected(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "3.0.0+codex.first+codex.second")

    with pytest.raises(PluginBuildIdentityError, match="PLUGIN_EXACT_VERSION_INVALID"):
        resolve_plugin_build_identity(tmp_path, expected_base_release="3.0.0")


def test_executable_surface_must_bind_exact_manifest_bytes(tmp_path: Path) -> None:
    version = "3.0.0+codex.fixture"
    manifest = _write_manifest(tmp_path, version)
    surface = {
        "schema": "evidence-lane.executable-package-surface-registry.v1",
        "status": "PASS",
        "plugin_version": version,
        "members": [
            {
                "path": ".codex-plugin/plugin.json",
                "bytes": len(manifest.read_bytes().replace(b"\r\n", b"\n")),
                "sha256": _sha256(manifest),
            }
        ],
        "member_count": 1,
    }
    surface["receipt_sha256"] = hashlib.sha256(
        (
            json.dumps(surface, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()
    surface_path = tmp_path / "manifests" / "executable-surface-registry.v1.json"
    surface_path.parent.mkdir(parents=True, exist_ok=True)
    surface_path.write_text(json.dumps(surface) + "\n", encoding="utf-8")

    identity = resolve_plugin_build_identity(
        tmp_path,
        expected_base_release="3.0.0",
        require_executable_surface_match=True,
    )
    assert identity["exact_version"] == version
    assert identity["executable_surface_identity_matches"] is True

    surface["plugin_version"] = "3.0.0+codex.other"
    surface["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in surface.items() if key != "receipt_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    ).hexdigest().upper()
    surface_path.write_text(json.dumps(surface) + "\n", encoding="utf-8")
    with pytest.raises(
        PluginBuildIdentityError,
        match="PLUGIN_EXECUTABLE_SURFACE_IDENTITY_MISMATCH",
    ):
        resolve_plugin_build_identity(
            tmp_path,
            expected_base_release="3.0.0",
            require_executable_surface_match=True,
        )


def test_executable_surface_rejects_any_stale_member_hash(tmp_path: Path) -> None:
    version = "3.0.0+codex.fixture"
    manifest = _write_manifest(tmp_path, version)
    member = tmp_path / "src" / "current.py"
    member.parent.mkdir(parents=True)
    member.write_text("VALUE = 1\n", encoding="utf-8")
    rows = [
        {
            "path": ".codex-plugin/plugin.json",
            "bytes": len(manifest.read_bytes().replace(b"\r\n", b"\n")),
            "sha256": _sha256(manifest),
        },
        {
            "path": "src/current.py",
            "bytes": len(member.read_bytes().replace(b"\r\n", b"\n")),
            "sha256": _sha256(member),
        },
    ]
    surface = {
        "schema": "evidence-lane.executable-package-surface-registry.v1",
        "status": "PASS",
        "plugin_version": version,
        "member_count": len(rows),
        "members": rows,
    }
    surface["receipt_sha256"] = hashlib.sha256(
        (
            json.dumps(surface, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()
    surface_path = tmp_path / "manifests" / "executable-surface-registry.v1.json"
    surface_path.parent.mkdir(parents=True)
    surface_path.write_text(json.dumps(surface) + "\n", encoding="utf-8")

    member.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(
        PluginBuildIdentityError,
        match="PLUGIN_EXECUTABLE_SURFACE_IDENTITY_MISMATCH",
    ):
        resolve_plugin_build_identity(
            tmp_path,
            expected_base_release="3.0.0",
            require_executable_surface_match=True,
        )


def test_installation_status_rejects_mismatched_exact_build(service) -> None:
    receipt = service.sessions.ensure_installation()
    path = service.store.root / "installation.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["version"] = "3.0.0+codex.other"
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceLaneError) as error:
        service.sessions.installation_status()

    assert error.value.code == "PLUGIN_INSTALLATION_RECEIPT_INVALID"
    assert receipt["version"] != tampered["version"]


def test_native_mcp_server_advertises_exact_loaded_plugin_build() -> None:
    from evidence_lane_plugin.mcp_server import create_mcp_server

    build = resolve_plugin_build_identity(expected_base_release="3.0.0")
    server = create_mcp_server()

    assert server._mcp_server.version == build["exact_version"]
    assert (
        server._evidence_lane_native_route_receipt["public_surface_registry"][
            "package_identity"
        ]["plugin_version"]
        == build["exact_version"]
    )
