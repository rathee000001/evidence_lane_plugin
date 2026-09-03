"""Resolve the exact package-local Codex plugin build identity.

The stable engine/release line and the installed Codex package build are
different identities.  ``3.0.0`` remains the engine and schema release while
the plugin manifest must carry one exact ``+codex.<cachebuster>`` suffix.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .package_root import resolve_plugin_root

PLUGIN_BUILD_IDENTITY_SCHEMA = "evidence-lane.plugin-build-identity.v1"
_EXACT_PLUGIN_VERSION_RE = re.compile(
    r"^(?P<base>\d+\.\d+\.\d+)\+codex\."
    r"(?P<cachebuster>[0-9A-Za-z](?:[0-9A-Za-z.-]*[0-9A-Za-z])?)$"
)


class PluginBuildIdentityError(RuntimeError):
    """Raised when package-local plugin identity cannot be proven exactly."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _canonical_package_member_bytes(payload: bytes) -> bytes:
    if b"\0" not in payload[:8000]:
        return payload.replace(b"\r\n", b"\n")
    return payload


def _surface_members_match(
    root: Path,
    surface: dict[str, Any],
) -> tuple[bool, int]:
    members = surface.get("members")
    if not isinstance(members, list) or surface.get("member_count") != len(members):
        return False, 0
    seen: set[str] = set()
    checked = 0
    for row in members:
        if not isinstance(row, dict):
            return False, checked
        relative = str(row.get("path") or "")
        candidate_relative = Path(relative)
        if (
            not relative
            or candidate_relative.is_absolute()
            or ".." in candidate_relative.parts
            or relative in seen
        ):
            return False, checked
        seen.add(relative)
        candidate = (root / candidate_relative).resolve()
        try:
            candidate.relative_to(root)
            payload = _canonical_package_member_bytes(candidate.read_bytes())
        except (OSError, ValueError):
            return False, checked
        if (
            row.get("bytes") != len(payload)
            or row.get("sha256") != _sha256_bytes(payload)
        ):
            return False, checked
        checked += 1
    return True, checked


def parse_exact_plugin_version(
    value: object,
    *,
    expected_base_release: str | None = None,
) -> tuple[str, str]:
    """Return ``(base_release, cachebuster)`` for one exact Codex version."""

    version = str(value or "").strip()
    match = _EXACT_PLUGIN_VERSION_RE.fullmatch(version)
    if match is None or version.count("+codex.") != 1:
        raise PluginBuildIdentityError("PLUGIN_EXACT_VERSION_INVALID")
    base_release = match.group("base")
    if expected_base_release is not None and base_release != expected_base_release:
        raise PluginBuildIdentityError("PLUGIN_BASE_RELEASE_MISMATCH")
    return base_release, match.group("cachebuster")


def _read_json_object(path: Path, *, error_code: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginBuildIdentityError(error_code) from exc
    if not isinstance(value, dict):
        raise PluginBuildIdentityError(error_code)
    return value, raw


def resolve_plugin_build_identity(
    plugin_root: str | Path | None = None,
    *,
    expected_base_release: str | None = None,
    require_executable_surface_match: bool = False,
) -> dict[str, Any]:
    """Resolve one exact manifest identity and optional executable-surface proof."""

    root = (
        Path(plugin_root).expanduser().resolve()
        if plugin_root is not None
        else resolve_plugin_root(__file__)
    )
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest, manifest_bytes = _read_json_object(
        manifest_path,
        error_code="PLUGIN_MANIFEST_INVALID",
    )
    plugin_id = str(manifest.get("name") or "").strip()
    exact_version = str(manifest.get("version") or "").strip()
    if plugin_id != "evidence-lane-plugin":
        raise PluginBuildIdentityError("PLUGIN_IDENTITY_INVALID")
    base_release, cachebuster = parse_exact_plugin_version(
        exact_version,
        expected_base_release=expected_base_release,
    )
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    manifest_package_sha256 = _sha256_bytes(
        _canonical_package_member_bytes(manifest_bytes)
    )

    surface_path = root / "manifests" / "executable-surface-registry.v1.json"
    surface_present = surface_path.is_file()
    surface_sha256: str | None = None
    surface_receipt_sha256: str | None = None
    surface_plugin_version: str | None = None
    surface_manifest_member_sha256: str | None = None
    surface_receipt_matches = False
    surface_member_hashes_match = False
    surface_member_hashes_checked = 0
    surface_matches = False
    if surface_present:
        surface, surface_bytes = _read_json_object(
            surface_path,
            error_code="PLUGIN_EXECUTABLE_SURFACE_INVALID",
        )
        surface_sha256 = _sha256_bytes(surface_bytes)
        surface_receipt_sha256 = str(surface.get("receipt_sha256") or "") or None
        surface_body = {
            key: value for key, value in surface.items() if key != "receipt_sha256"
        }
        surface_receipt_matches = (
            surface_receipt_sha256
            == _sha256_bytes(_canonical_json_bytes(surface_body))
        )
        surface_plugin_version = str(surface.get("plugin_version") or "") or None
        members = surface.get("members")
        if isinstance(members, list):
            manifest_member = next(
                (
                    row
                    for row in members
                    if isinstance(row, dict)
                    and row.get("path") == ".codex-plugin/plugin.json"
                ),
                None,
            )
            if manifest_member is not None:
                surface_manifest_member_sha256 = (
                    str(manifest_member.get("sha256") or "") or None
                )
        surface_member_hashes_match, surface_member_hashes_checked = (
            _surface_members_match(root, surface)
        )
        surface_matches = (
            surface.get("schema")
            == "evidence-lane.executable-package-surface-registry.v1"
            and surface.get("status") == "PASS"
            and surface_plugin_version == exact_version
            and surface_manifest_member_sha256 == manifest_package_sha256
            and surface_receipt_matches
            and surface_member_hashes_match
        )
    if require_executable_surface_match and not surface_matches:
        raise PluginBuildIdentityError("PLUGIN_EXECUTABLE_SURFACE_IDENTITY_MISMATCH")

    core = {
        "schema": PLUGIN_BUILD_IDENTITY_SCHEMA,
        "plugin_id": plugin_id,
        "exact_version": exact_version,
        "base_release": base_release,
        "cachebuster": cachebuster,
        "plugin_manifest_sha256": manifest_sha256,
        "plugin_manifest_package_sha256": manifest_package_sha256,
        "executable_surface_present": surface_present,
        "executable_surface_registry_sha256": surface_sha256,
        "executable_surface_receipt_sha256": surface_receipt_sha256,
        "executable_surface_plugin_version": surface_plugin_version,
        "executable_surface_manifest_member_sha256": (
            surface_manifest_member_sha256
        ),
        "executable_surface_receipt_matches": surface_receipt_matches,
        "executable_surface_member_hashes_match": surface_member_hashes_match,
        "executable_surface_member_hashes_checked": surface_member_hashes_checked,
        "executable_surface_identity_matches": surface_matches,
    }
    return {
        **core,
        "package_identity_sha256": _sha256_bytes(_canonical_json_bytes(core)),
    }


__all__ = [
    "PLUGIN_BUILD_IDENTITY_SCHEMA",
    "PluginBuildIdentityError",
    "parse_exact_plugin_version",
    "resolve_plugin_build_identity",
]
