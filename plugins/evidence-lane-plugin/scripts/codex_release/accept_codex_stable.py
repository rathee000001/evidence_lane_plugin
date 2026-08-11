"""Verify the installed Evidence Lane 2.0 Codex package before final HIL.

This checker is read-only except for its explicit receipt output. It compares the
supported local marketplace with Codex's generated installed cache, validates the
enabled selector, statically proves the 62/21/41 catalog and fifteen skills, and
optionally binds a post-restart native route receipt. It never calls lifecycle,
Git, tunnel, candidate, pointer, or HIL actions.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

BASE_RELEASE = "2.0.0"
PLUGIN_NAME = "evidence-lane-plugin"
MARKETPLACE_NAME = "evidence-lane-v200-github"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
EXPECTED_CATALOG = {"tools": 62, "read": 21, "write": 41, "skills": 15}
EXPECTED_HOST_STORAGE_TUNNEL_MATRIX = {
    "routing_axes_independent": True,
    "account_tier_affects_routing": False,
    "api_billing_affects_routing": False,
    "headless_api": {
        "local_or_persistent_pv_storage": "LOCAL_SQLITE_WHEN_DURABLE",
        "ephemeral_pv_storage": (
            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
        ),
        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
        "flash_frequency": "EVERY_INVOCATION_ENTRY",
    },
    "interactive_codex_app_local_or_persistent": {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
    },
    "interactive_codex_app_ephemeral_vm": {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
    },
}
SCHEMA = "evidence-lane.codex-installed-acceptance.v2"
_IGNORED_DIRECTORIES = frozenset({".venv", "__pycache__", ".pytest_cache"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".tmp"})


class AcceptanceError(RuntimeError):
    """Raised when installed stable identity is not exact."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sealed_json(path: Path, *, hash_field: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(value.get(hash_field) or "")
    body = {key: item for key, item in value.items() if key != hash_field}
    if not claimed or claimed != _sha256_bytes(_json_bytes(body)):
        raise AcceptanceError(f"{path.name} failed its {hash_field} seal.")
    return value


def _inventory(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if any(part in _IGNORED_DIRECTORIES for part in relative_path.parts):
            continue
        if path.suffix.casefold() in _IGNORED_SUFFIXES:
            continue
        relative = relative_path.as_posix()
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "file_count": len(rows),
        "manifest_sha256": _sha256_bytes(_json_bytes(rows)),
        "files": rows,
    }


def _surface_inventory(plugin_root: Path, *, version: str) -> dict[str, Any]:
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *sorted((plugin_root / "hooks").glob("*.py")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if not all(path.is_file() for path in hook_paths):
        raise AcceptanceError("The installed persistent hook inventory is incomplete.")
    if {path.name for path in hook_paths} != {
        "hooks.json",
        "session_start.py",
        "prompt_submit.py",
        "stop_response.py",
    }:
        raise AcceptanceError("The installed persistent hook inventory is not exact.")

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        rows = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        if len(rows) != len({row["name"] for row in rows}):
            raise AcceptanceError("An installed hook or skill name is duplicated.")
        return {
            "count": len(rows),
            "records": rows,
            "inventory_sha256": _sha256_bytes(_json_bytes(rows)),
        }

    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": version,
        "hooks": inventory(hook_paths, skill=False),
        "skills": inventory(skill_paths, skill=True),
        "catalog": dict(EXPECTED_CATALOG),
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = _sha256_bytes(_json_bytes(core))
    return core


def _catalog(plugin_root: Path) -> dict[str, Any]:
    path = plugin_root / "src" / "evidence_lane_plugin" / "mcp_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rows: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                continue
            keywords = {item.arg: item.value for item in decorator.keywords if item.arg}
            name_node = keywords.get("name")
            annotation_node = keywords.get("annotations")
            if not (
                isinstance(name_node, ast.Constant)
                and isinstance(name_node.value, str)
                and isinstance(annotation_node, ast.Name)
            ):
                raise AcceptanceError("Every native tool needs a literal name and annotation.")
            rows.append(
                {
                    "name": name_node.value,
                    "annotation": annotation_node.id,
                }
            )
    rows.sort(key=lambda row: row["name"])
    names = [row["name"] for row in rows]
    read = sum(row["annotation"] == "_READ_ONLY" for row in rows)
    write = len(rows) - read
    if (
        len(rows) != EXPECTED_CATALOG["tools"]
        or len(names) != len(set(names))
        or read != EXPECTED_CATALOG["read"]
        or write != EXPECTED_CATALOG["write"]
    ):
        raise AcceptanceError("The installed native 62/21/41 tool catalog drifted.")
    return {
        "tools": len(rows),
        "read": read,
        "write": write,
        "tool_names_unique": True,
        "static_catalog_sha256": _sha256_bytes(_json_bytes(rows)),
    }


def _engine_version(plugin_root: Path) -> str:
    path = plugin_root / "src" / "evidence_lane_plugin" / "constants.py"
    match = re.search(
        r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match is None:
        raise AcceptanceError("ENGINE_VERSION is unavailable.")
    return match.group("version")


def _validate_plugin(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text("utf-8"))
    release = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = dict(release.get("stable") or {})
    promotion = dict(release.get("promotion_gate") or {})
    remote_git = dict(release.get("remote_git_policy") or {})
    skills = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    native = json.loads((plugin_root / ".mcp.json").read_text("utf-8"))
    required_release_helpers = (
        plugin_root / "scripts" / "codex_release" / "install_codex_stable.py",
        plugin_root / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1",
        plugin_root / "scripts" / "codex_release" / "accept_codex_stable.py",
    )
    forbidden = (
        plugin_root / ".app.json",
        plugin_root / "chatgpt-app-connection.json",
        plugin_root / "chatgpt-app-submission.json",
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    if (
        manifest.get("name") != PLUGIN_NAME
        or not version.startswith(f"{BASE_RELEASE}+codex.")
        or manifest.get("mcpServers") != "./.mcp.json"
        or "apps" in manifest
        or project.get("project", {}).get("version") != BASE_RELEASE
        or _engine_version(plugin_root) != BASE_RELEASE
        or release.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != BASE_RELEASE
        or stable.get("codex_marketplace_slot") != MARKETPLACE_NAME
        or stable.get("native_tool_count") != EXPECTED_CATALOG["tools"]
        or stable.get("native_read_tool_count") != EXPECTED_CATALOG["read"]
        or stable.get("native_write_tool_count") != EXPECTED_CATALOG["write"]
        or stable.get("skill_count") != EXPECTED_CATALOG["skills"]
        or len(skills) != EXPECTED_CATALOG["skills"]
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or release.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_six_way_hil_required") is not True
        or remote_git.get("per_push_confirmation_token_required") is not False
        or remote_git.get("automatic_push_scope")
        != "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not False
        or set(native.get("mcpServers") or {}) != {"evidence-lane"}
        or not all(path.is_file() for path in required_release_helpers)
        or any(path.exists() for path in forbidden)
    ):
        raise AcceptanceError("The installed v2 package identity or boundary drifted.")
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_text("utf-8"))
    if set(hooks.get("hooks") or {}) != {"SessionStart", "UserPromptSubmit", "Stop"}:
        raise AcceptanceError("The installed persistent turn hooks drifted.")
    for name in ("session_start.py", "prompt_submit.py", "stop_response.py"):
        source = (plugin_root / "hooks" / name).read_text(encoding="utf-8")
        if "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=" not in source:
            raise AcceptanceError(f"{name} does not emit the persistent change notice.")
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "release_contract_sha256": _sha256(
            plugin_root / "scripts" / "codex-release-channel.json"
        ),
        "skills": len(skills),
        "catalog": _catalog(plugin_root),
        "hook_events": ["SessionStart", "Stop", "UserPromptSubmit"],
        "mode": "CODE",
        "ci_cd_law": "CONTROLLED_REQUIRED",
        "host_storage_tunnel_matrix": EXPECTED_HOST_STORAGE_TUNNEL_MATRIX,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
    }


def _native_receipt(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    route = payload.get("mcp_route_identity") or payload
    if (
        route.get("schema") != "evidence-lane.native-mcp-route-receipt.v1"
        or route.get("status") != "PASS"
        or route.get("server_identity") != "evidence-lane"
        or route.get("canonical_tool_namespace") != "mcp__evidence_lane__"
        or route.get("exposure_profile") != "FULL_LIFECYCLE"
        or route.get("tool_count") != EXPECTED_CATALOG["tools"]
        or route.get("tool_names_unique") is not True
        or route.get("project_route_argument_required") is not True
        or route.get("cross_project_fallback_allowed") is not False
        or not route.get("tool_catalog_sha256")
    ):
        raise AcceptanceError("The post-restart native route receipt drifted.")
    return {
        "status": "PASS",
        "server_identity": route["server_identity"],
        "canonical_tool_namespace": route["canonical_tool_namespace"],
        "tool_count": route["tool_count"],
        "tool_catalog_sha256": route["tool_catalog_sha256"],
        "receipt_file_sha256": _sha256(path),
    }


def accept(args: argparse.Namespace) -> dict[str, Any]:
    installed = args.installed_plugin.resolve()
    marketplace = args.marketplace_plugin.resolve()
    config = args.codex_config.resolve()
    archive = args.archive.resolve()
    rehearsal_path = args.rehearsal_receipt.resolve()
    install_path = args.installation_receipt.resolve()
    rehearsal = json.loads(rehearsal_path.read_text(encoding="utf-8"))
    installation = _sealed_json(install_path, hash_field="receipt_sha256")
    activation = dict(installation.get("activation") or {})
    plugin_add = dict(activation.get("plugin_add") or {})
    surface_change = dict(installation.get("surface_change_display") or {})
    if (
        rehearsal.get("status") != "PASS"
        or rehearsal.get("archive", {}).get("filename") != archive.name
        or rehearsal.get("archive", {}).get("sha256") != _sha256(archive)
        or installation.get("status") != "PASS"
        or installation.get("schema") != "evidence-lane.codex-stable-installation.v2"
        or installation.get("archive_sha256") != _sha256(archive)
        or activation.get("state") != "INSTALLED_RESTART_REQUIRED"
        or plugin_add.get("installedPath") is None
        or Path(plugin_add["installedPath"]).resolve() != installed
        or installation.get("generated_cache_written_directly") is not False
        or installation.get("previous_release_cache_deleted") is not False
        or installation.get("credential_requested_or_stored") is not False
        or surface_change.get("schema")
        != "evidence-lane.codex-installed-surface-change-display.v2"
        or surface_change.get("raw_paths_included") is not False
        or surface_change.get("private_research_question_included") is not False
    ):
        raise AcceptanceError("The archive or supported installation receipt drifted.")
    configured = tomllib.loads(config.read_text(encoding="utf-8"))
    enabled = sorted(
        selector
        for selector, value in (configured.get("plugins") or {}).items()
        if selector.startswith(f"{PLUGIN_NAME}@") and value.get("enabled") is True
    )
    if enabled != [PLUGIN_SELECTOR]:
        raise AcceptanceError("Exactly the v2 Evidence Lane selector must be enabled.")
    installed_identity = _validate_plugin(installed)
    marketplace_identity = _validate_plugin(marketplace)
    if (
        surface_change.get("current_plugin_version")
        != installed_identity["version"]
        or surface_change.get("current_surface_inventory_sha256")
        != installed_identity["surface_inventory"]["surface_inventory_sha256"]
        or surface_change.get("hooks", {}).get("count")
        != installed_identity["surface_inventory"]["hooks"]["count"]
        or surface_change.get("skills", {}).get("count")
        != EXPECTED_CATALOG["skills"]
        or surface_change.get("catalog", {}).get("tools")
        != EXPECTED_CATALOG["tools"]
    ):
        raise AcceptanceError("The installed surface-change display drifted.")
    installed_inventory = _inventory(installed)
    marketplace_inventory = _inventory(marketplace)
    if installed_inventory != marketplace_inventory:
        raise AcceptanceError("Codex cache bytes differ from the staged marketplace bytes.")
    native = _native_receipt(args.native_route_receipt)
    state = (
        "POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL"
        if native is not None
        else "PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED"
    )
    body = {
        "schema": SCHEMA,
        "status": "PASS",
        "state": state,
        "installed_plugin": installed_identity,
        "marketplace_plugin": marketplace_identity,
        "package_inventory": {
            "file_count": installed_inventory["file_count"],
            "manifest_sha256": installed_inventory["manifest_sha256"],
            "cache_matches_marketplace": True,
        },
        "catalog": dict(EXPECTED_CATALOG),
        "surface_change_display": surface_change,
        "enabled_selector": PLUGIN_SELECTOR,
        "archive_sha256": _sha256(archive),
        "rehearsal_receipt_sha256": _sha256(rehearsal_path),
        "installation_receipt_sha256": _sha256(install_path),
        "native_route": native,
        "restart_verified": native is not None,
        "installed_host_hil_required": True,
        "hil_inferred": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "git_invoked": False,
        "tunnel_invoked": False,
        "source_mutated": False,
    }
    body["receipt_sha256"] = _sha256_bytes(_json_bytes(body))
    _write_atomic(args.output.resolve(), body)
    return body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-plugin", type=Path, required=True)
    parser.add_argument("--marketplace-plugin", type=Path, required=True)
    parser.add_argument("--codex-config", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--rehearsal-receipt", type=Path, required=True)
    parser.add_argument("--installation-receipt", type=Path, required=True)
    parser.add_argument("--native-route-receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    print(json.dumps(accept(_parser().parse_args()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
