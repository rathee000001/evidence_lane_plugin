"""Derive the exact public plugin surface from package-owned registries.

Counts are evidence, not configuration.  This module deliberately derives them
from the routing manifest, skill and command files, hook configuration, and MCP
provider manifest so installed receipts cannot silently inherit stale totals.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any

_PLUGIN_ROOT_ENV = "EVIDENCE_LANE_PLUGIN_ROOT"
PUBLIC_SURFACE_SINGLE_INSTALLED_ROOT_LAW = (
    "PUBLIC_SURFACE_SINGLE_INSTALLED_ROOT_LAW"
)

# Read/write is a semantic tool registry, not a hard-coded count.  The complete
# tool-name registry remains skills/evi/references/mcp-tool-routing.v1.json.
CODEX_READ_TOOL_NAMES = (
    "canon_graph",
    "canon_inbox",
    "canon_inspect",
    "connector_plugin_catalog",
    "connector_plugin_settings",
    "fetch",
    "lane_catalog",
    "lane_fetch",
    "lane_search",
    "lane_status",
    "learning_inspect",
    "learning_memory_query",
    "learning_retrieve",
    "lifecycle_transition_law",
    "prompt_index_status",
    "pv_diff",
    "pv_query",
    "pv_status",
    "pv_summary",
    "pv_task_backlog",
    "render_project_panel",
    "render_runtime_panel",
    "runtime_activation_status",
    "runtime_doctor",
    "search",
    "session_flash_status",
    "storage_connector_inspect",
)


class PublicSurfaceRegistryError(RuntimeError):
    """Raised when a package registry is malformed or internally inconsistent."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicSurfaceRegistryError(
                f"PUBLIC_SURFACE_DUPLICATE_JSON_KEY:{key}"
            )
        result[key] = value
    return result


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicSurfaceRegistryError(
            f"PUBLIC_SURFACE_REGISTRY_INVALID:{path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise PublicSurfaceRegistryError(
            f"PUBLIC_SURFACE_REGISTRY_OBJECT_REQUIRED:{path.name}"
        )
    return value


def resolve_public_surface_plugin_root() -> Path:
    """Resolve the package root owning the currently imported runtime."""

    configured = os.environ.get(_PLUGIN_ROOT_ENV, "").strip()
    runtime_root = Path(__file__).resolve().parents[2]
    root = Path(configured) if configured else runtime_root
    if configured and not root.is_absolute():
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_PLUGIN_ROOT_NOT_ABSOLUTE")
    resolved = root.resolve()
    if resolved != runtime_root:
        raise PublicSurfaceRegistryError(
            "PUBLIC_SURFACE_PLUGIN_ROOT_RUNTIME_MISMATCH"
        )
    required = (
        resolved / ".codex-plugin" / "plugin.json",
        resolved / ".mcp.json",
        resolved / "hooks" / "hooks.json",
        resolved / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json",
    )
    if not all(path.is_file() for path in required):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_PLUGIN_ROOT_INVALID")
    return resolved


def _file_records(paths: list[Path], *, parent_name: bool = False) -> list[dict[str, str]]:
    records = [
        {
            "name": path.parent.name if parent_name else path.name,
            "sha256": _sha256_file(path),
        }
        for path in sorted(paths, key=lambda item: str(item).casefold())
    ]
    names = [row["name"] for row in records]
    if len(names) != len(set(names)):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_DUPLICATE_FILE_IDENTITY")
    return records


def derive_public_surface_registry(
    plugin_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return one sealed registry-derived source or installed surface receipt."""

    root = (
        Path(plugin_root).resolve()
        if plugin_root is not None
        else resolve_public_surface_plugin_root()
    )
    routing_path = root / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    routing = _json(routing_path)
    owners = routing.get("tool_owners")
    if routing.get("schema") != "evidence-lane.skill-mcp-routing.v1" or not isinstance(
        owners, dict
    ):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_TOOL_REGISTRY_INVALID")
    tool_names = sorted(str(name) for name in owners)
    if any(not name.strip() for name in tool_names):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_TOOL_NAME_INVALID")

    read_names = sorted(CODEX_READ_TOOL_NAMES)
    missing_read_names = sorted(set(read_names) - set(tool_names))
    if missing_read_names:
        raise PublicSurfaceRegistryError(
            "PUBLIC_SURFACE_READ_REGISTRY_UNKNOWN_TOOL:"
            + ",".join(missing_read_names)
        )
    write_names = sorted(set(tool_names) - set(read_names))
    routing_catalog_contract = routing.get("catalog_contract")
    if not isinstance(routing_catalog_contract, dict):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_CATALOG_CONTRACT_INVALID")
    routing_catalog_matches_derived = (
        routing_catalog_contract.get("tool_count") == len(tool_names)
    )

    skill_records = _file_records(
        list((root / "skills").glob("*/SKILL.md")),
        parent_name=True,
    )
    command_records = _file_records(list((root / "commands").glob("*.md")))

    hook_path = root / "hooks" / "hooks.json"
    hook_configuration = _json(hook_path)
    hooks = hook_configuration.get("hooks")
    if not isinstance(hooks, dict):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_HOOK_REGISTRY_INVALID")
    hook_events = list(hooks)
    handler_count = 0
    for groups in hooks.values():
        if not isinstance(groups, list):
            raise PublicSurfaceRegistryError("PUBLIC_SURFACE_HOOK_GROUP_INVALID")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise PublicSurfaceRegistryError("PUBLIC_SURFACE_HOOK_HANDLER_INVALID")
            handler_count += len(group["hooks"])

    provider_path = root / ".mcp.json"
    provider_configuration = _json(provider_path)
    providers = provider_configuration.get("mcpServers")
    if not isinstance(providers, dict) or any(
        not str(name).strip() for name in providers
    ):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_PROVIDER_REGISTRY_INVALID")
    provider_names = sorted(str(name) for name in providers)

    plugin_manifest_path = root / ".codex-plugin" / "plugin.json"
    plugin_manifest = _json(plugin_manifest_path)
    plugin_id = str(plugin_manifest.get("name") or "").strip()
    plugin_version = str(plugin_manifest.get("version") or "").strip()
    try:
        project = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        project_name = str(project["project"]["name"]).strip()
        project_version = str(project["project"]["version"]).strip()
    except (OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise PublicSurfaceRegistryError(
            "PUBLIC_SURFACE_PACKAGE_IDENTITY_INVALID"
        ) from exc
    package_identity_matches = (
        plugin_id == project_name == "evidence-lane-plugin"
        and bool(plugin_version)
        and plugin_version.split("+", 1)[0] == project_version
    )

    release_path = root / "scripts" / "codex-release-channel.json"
    release = _json(release_path)
    stable = release.get("stable")
    if not isinstance(stable, dict):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_RELEASE_REGISTRY_INVALID")
    catalog = {
        "tools": len(tool_names),
        "read": len(read_names),
        "write": len(write_names),
        "skills": len(skill_records),
        "commands": len(command_records),
        "hook_events": len(hook_events),
        "hook_handlers": handler_count,
        "providers": len(provider_names),
    }
    release_claim = {
        "tools": stable.get("native_tool_count"),
        "read": stable.get("native_read_tool_count"),
        "write": stable.get("native_write_tool_count"),
        "skills": stable.get("skill_count"),
    }
    release_catalog_matches_derived = release_claim == {
        key: catalog[key] for key in ("tools", "read", "write", "skills")
    }

    core: dict[str, Any] = {
        "schema": "evidence-lane.public-surface-registry.v1",
        "status": (
            "PASS"
            if (
                release_catalog_matches_derived
                and routing_catalog_matches_derived
                and package_identity_matches
            )
            else "BLOCKED"
        ),
        "route_law": PUBLIC_SURFACE_SINGLE_INSTALLED_ROOT_LAW,
        "package_identity": {
            "plugin_id": plugin_id,
            "plugin_version": plugin_version,
            "project_version": project_version,
            "identity_matches": package_identity_matches,
            "manifest_sha256": _sha256_file(plugin_manifest_path),
            "mcp_configuration_sha256": _sha256_file(root / ".mcp.json"),
            "runtime_module_root_is_registry_root": (
                root == Path(__file__).resolve().parents[2]
            ),
            "caller_selected_cross_package_root_allowed": False,
        },
        "catalog": catalog,
        "release_claimed_catalog": release_claim,
        "release_catalog_matches_derived": release_catalog_matches_derived,
        "routing_catalog_matches_derived": routing_catalog_matches_derived,
        "tools": {
            "names": tool_names,
            "read_names": read_names,
            "write_names": write_names,
            "routing_sha256": _sha256_file(routing_path),
            "routing_claimed_tool_count": routing_catalog_contract.get("tool_count"),
        },
        "skills": {
            "records": skill_records,
            "inventory_sha256": _sha256_bytes(_canonical_bytes(skill_records)),
        },
        "commands": {
            "records": command_records,
            "inventory_sha256": _sha256_bytes(_canonical_bytes(command_records)),
        },
        "hooks": {
            "event_names": hook_events,
            "configuration_sha256": _sha256_file(hook_path),
        },
        "providers": {
            "names": provider_names,
            "configuration_sha256": _sha256_file(provider_path),
        },
        "raw_paths_included": False,
    }
    core["registry_sha256"] = _sha256_bytes(_canonical_bytes(core))
    return core


def derive_runtime_catalog_constants(
    plugin_root: str | Path | None = None,
) -> dict[str, int]:
    """Derive import-safe counts from copied source plus the release marker.

    Cachebuster and read-only hook tests intentionally stage only ``src`` and
    the release-channel file.  Parsing decorators keeps those runtime imports
    independent from optional package UI files while the full installed gate
    still uses :func:`derive_public_surface_registry`.
    """

    root = (
        Path(plugin_root).resolve()
        if plugin_root is not None
        else Path(__file__).resolve().parents[2]
    )
    mcp_source = root / "src" / "evidence_lane_plugin" / "mcp_server.py"
    release_path = root / "scripts" / "codex-release-channel.json"
    try:
        tree = ast.parse(mcp_source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise PublicSurfaceRegistryError(
            "PUBLIC_SURFACE_MCP_SOURCE_REGISTRY_INVALID"
        ) from exc
    tool_records: list[tuple[str, bool]] = []
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        if not any(
            isinstance(target, ast.Name) and target.id == "SDK_NATIVE_ACTIONS"
            for target in targets
        ):
            continue
        if statement.value is None:
            raise PublicSurfaceRegistryError(
                "PUBLIC_SURFACE_SDK_TOOL_REGISTRY_INVALID"
            )
        try:
            sdk_actions = ast.literal_eval(statement.value)
        except (TypeError, ValueError) as exc:
            raise PublicSurfaceRegistryError(
                "PUBLIC_SURFACE_SDK_TOOL_REGISTRY_INVALID"
            ) from exc
        if not isinstance(sdk_actions, tuple):
            raise PublicSurfaceRegistryError(
                "PUBLIC_SURFACE_SDK_TOOL_REGISTRY_INVALID"
            )
        for row in sdk_actions:
            if (
                not isinstance(row, tuple)
                or len(row) != 6
                or not isinstance(row[0], str)
                or not isinstance(row[5], bool)
            ):
                raise PublicSurfaceRegistryError(
                    "PUBLIC_SURFACE_SDK_TOOL_REGISTRY_INVALID"
                )
            tool_records.append((row[0], row[5]))
    for syntax_node in ast.walk(tree):
        if not isinstance(syntax_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in syntax_node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                continue
            keywords = {row.arg: row.value for row in decorator.keywords if row.arg}
            name_node = keywords.get("name")
            annotation_node = keywords.get("annotations")
            if not isinstance(name_node, ast.Constant) or not isinstance(
                name_node.value, str
            ):
                raise PublicSurfaceRegistryError(
                    "PUBLIC_SURFACE_MCP_TOOL_NAME_NOT_STATIC"
                )
            read_only = (
                isinstance(annotation_node, ast.Name)
                and annotation_node.id == "_READ_ONLY"
            )
            tool_records.append((name_node.value, read_only))
    tool_names = [row[0] for row in tool_records]
    if not tool_names or len(tool_names) != len(set(tool_names)):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_MCP_TOOL_REGISTRY_INVALID")
    ast_read_names = sorted(name for name, read_only in tool_records if read_only)
    if ast_read_names != sorted(CODEX_READ_TOOL_NAMES):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_MCP_READ_REGISTRY_MISMATCH")
    release = _json(release_path)
    stable = release.get("stable")
    if not isinstance(stable, dict) or not isinstance(stable.get("skill_count"), int):
        raise PublicSurfaceRegistryError("PUBLIC_SURFACE_RELEASE_REGISTRY_INVALID")
    return {
        "tools": len(tool_records),
        "read": len(ast_read_names),
        "write": len(tool_records) - len(ast_read_names),
        "skills": int(stable["skill_count"]),
    }
