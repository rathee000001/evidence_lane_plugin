"""Stage and activate one sealed Evidence Lane 2.0 Codex marketplace package.

The script uses the supported Codex marketplace and plugin commands. It never
writes the generated plugin cache directly, never deletes an older release, and
never asks for or stores Git, OpenAI, OAuth, PAT, or tunnel credentials.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import uuid
import zipfile
from pathlib import Path
from typing import Any

BASE_RELEASE = "2.0.0"
MARKETPLACE_NAME = "evidence-lane-v200-github"
PLUGIN_NAME = "evidence-lane-plugin"
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
INSTALL_SCHEMA = "evidence-lane.codex-stable-installation.v2"


class InstallationError(RuntimeError):
    """Raised before restart when a stable-install invariant is not exact."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _surface_inventory(plugin_root: Path, *, version: str) -> dict[str, Any]:
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *sorted((plugin_root / "hooks").glob("*.py")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if not all(path.is_file() for path in hook_paths):
        raise InstallationError("The persistent hook inventory is incomplete.")
    hook_names = {path.name for path in hook_paths}
    if frozenset(hook_names) not in {
        frozenset(
            {
                "hooks.json",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "hooks.json",
                "post_tool_use.py",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
    }:
        raise InstallationError("The persistent hook inventory is not exact.")
    if len(skill_paths) != EXPECTED_CATALOG["skills"]:
        raise InstallationError("The governed skill inventory is not exact.")

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        rows = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        if len(rows) != len({row["name"] for row in rows}):
            raise InstallationError("A hook or skill inventory name is duplicated.")
        return {
            "count": len(rows),
            "records": rows,
            "inventory_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
        }

    hook_files = inventory(hook_paths, skill=False)
    hook_configuration = json.loads(
        (plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    hook_events = dict(hook_configuration.get("hooks") or {})
    registered_events = sorted(hook_events)
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hook_events.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    hook_inventory = {
        "count": len(registered_events),
        "count_semantics": "REGISTERED_EVENT_COUNT",
        "registered_event_count": len(registered_events),
        "registered_events": registered_events,
        "handler_count": handler_count,
        "hook_file_count": hook_files["count"],
        "records": hook_files["records"],
        "file_inventory_sha256": hook_files["inventory_sha256"],
        "event_inventory_sha256": hashlib.sha256(
            _json_bytes(registered_events)
        ).hexdigest().upper(),
    }
    hook_inventory["inventory_sha256"] = hashlib.sha256(
        _json_bytes(hook_inventory)
    ).hexdigest().upper()
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": version,
        "hooks": hook_inventory,
        "skills": inventory(skill_paths, skill=True),
        "catalog": dict(EXPECTED_CATALOG),
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
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
                raise InstallationError(
                    "Every native tool needs a literal name and annotation."
                )
            rows.append(
                {"name": name_node.value, "annotation": annotation_node.id}
            )
    rows.sort(key=lambda row: row["name"])
    read = sum(row["annotation"] == "_READ_ONLY" for row in rows)
    write = len(rows) - read
    if (
        len(rows) != EXPECTED_CATALOG["tools"]
        or len({row["name"] for row in rows}) != len(rows)
        or read != EXPECTED_CATALOG["read"]
        or write != EXPECTED_CATALOG["write"]
    ):
        raise InstallationError("The native 62/21/41 tool catalog drifted.")
    return {
        "tools": len(rows),
        "read": read,
        "write": write,
        "skills": EXPECTED_CATALOG["skills"],
        "tool_names_unique": True,
        "static_catalog_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
    }


def _surface_change_display(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    def changes(kind: str) -> dict[str, Any]:
        current_rows = {
            row["name"]: row["sha256"] for row in current[kind]["records"]
        }
        previous_rows = (
            {
                row["name"]: row["sha256"]
                for row in previous[kind]["records"]
            }
            if previous is not None
            else {}
        )
        added_files = sorted(current_rows.keys() - previous_rows.keys())
        changed_files = sorted(
            name
            for name in current_rows.keys() & previous_rows.keys()
            if current_rows[name] != previous_rows[name]
        )
        removed_files = sorted(previous_rows.keys() - current_rows.keys())
        result = {
            "count": current[kind]["count"],
            "count_semantics": current[kind].get(
                "count_semantics", "SURFACE_RECORD_COUNT"
            ),
            "added": sorted(current_rows.keys() - previous_rows.keys()),
            "changed": changed_files,
            "removed": removed_files,
            "added_files": added_files,
            "changed_files": changed_files,
            "removed_files": removed_files,
            "inventory_sha256": current[kind]["inventory_sha256"],
        }
        if kind == "hooks":
            current_events = set(current[kind]["registered_events"])
            previous_events = (
                set(previous[kind]["registered_events"])
                if previous is not None
                else set()
            )
            result.update(
                {
                    "registered_event_count": current[kind][
                        "registered_event_count"
                    ],
                    "registered_events": current[kind]["registered_events"],
                    "handler_count": current[kind]["handler_count"],
                    "hook_file_count": current[kind]["hook_file_count"],
                    "added_events": sorted(current_events - previous_events),
                    "removed_events": sorted(previous_events - current_events),
                    "file_inventory_sha256": current[kind][
                        "file_inventory_sha256"
                    ],
                    "event_inventory_sha256": current[kind][
                        "event_inventory_sha256"
                    ],
                }
            )
        return result

    core = {
        "schema": "evidence-lane.codex-installed-surface-change-display.v2",
        "state": "INITIAL_V2_BASELINE" if previous is None else "VERSIONED_UPDATE",
        "previous_plugin_version": (
            previous.get("plugin_version") if previous is not None else None
        ),
        "current_plugin_version": current["plugin_version"],
        "hooks": changes("hooks"),
        "skills": changes("skills"),
        "catalog": {
            **current["catalog"],
            "changed_from_previous": (
                previous is not None and previous.get("catalog") != current["catalog"]
            ),
        },
        "previous_surface_inventory_sha256": (
            previous.get("surface_inventory_sha256")
            if previous is not None
            else None
        ),
        "current_surface_inventory_sha256": current[
            "surface_inventory_sha256"
        ],
        "raw_paths_included": False,
        "private_research_question_included": False,
    }
    core["change_display_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    return core


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _load_receipt(receipt_path: Path, archive: Path) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    sealed = receipt.get("archive") or {}
    if (
        receipt.get("status") != "PASS"
        or sealed.get("filename") != archive.name
        or sealed.get("sha256") != _sha256(archive)
        or receipt.get("governed_candidate_created") is not False
        or receipt.get("accepted_pointer_moved") is not False
    ):
        raise InstallationError("The rehearsal receipt does not seal this archive.")
    return receipt


def _safe_extract(archive_path: Path, target: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise InstallationError("Archive members are not sorted and unique.")
        for info in archive.infolist():
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in info.filename:
                raise InstallationError(f"Unsafe archive member: {info.filename}")
            destination = (target / relative).resolve()
            if not _inside(destination, target):
                raise InstallationError(f"Archive member escaped extraction: {info.filename}")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _validate_plugin(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    contract = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text(
            encoding="utf-8"
        )
    )
    stable = contract.get("stable") or {}
    remote_git = contract.get("remote_git_policy") or {}
    promotion = contract.get("promotion_gate") or {}
    skill_count = len(list((plugin_root / "skills").glob("*/SKILL.md")))
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text("utf-8"))
    constants = (
        plugin_root / "src" / "evidence_lane_plugin" / "constants.py"
    ).read_text(encoding="utf-8")
    engine_match = re.search(
        r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
        constants,
        flags=re.MULTILINE,
    )
    catalog = _catalog(plugin_root)
    release_helpers = (
        plugin_root / "scripts" / "codex_release" / "install_codex_stable.py",
        plugin_root / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1",
        plugin_root / "scripts" / "codex_release" / "accept_codex_stable.py",
    )
    if (
        manifest.get("name") != PLUGIN_NAME
        or not version.startswith(f"{BASE_RELEASE}+codex.")
        or manifest.get("mcpServers") != "./.mcp.json"
        or "apps" in manifest
        or (plugin_root / ".app.json").exists()
        or contract.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != BASE_RELEASE
        or stable.get("codex_marketplace_slot") != "evidence-lane-v200-github"
        or stable.get("native_tool_count") != EXPECTED_CATALOG["tools"]
        or stable.get("native_read_tool_count") != EXPECTED_CATALOG["read"]
        or stable.get("native_write_tool_count") != EXPECTED_CATALOG["write"]
        or stable.get("skill_count") != EXPECTED_CATALOG["skills"]
        or skill_count != EXPECTED_CATALOG["skills"]
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or project.get("project", {}).get("version") != BASE_RELEASE
        or engine_match is None
        or engine_match.group("version") != BASE_RELEASE
        or contract.get("archive", {}).get("release") != "1.5.0"
        or contract.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or remote_git.get("effective_release") != BASE_RELEASE
        or remote_git.get("per_push_confirmation_token_required") is not False
        or remote_git.get("automatic_push_scope")
        != "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not False
        or remote_git.get("pull_request_acceptance_allowed") is not False
        or remote_git.get("force_push_allowed") is not False
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_six_way_hil_required") is not True
        or not all(path.is_file() for path in release_helpers)
    ):
        raise InstallationError("The extracted v2 plugin or release contract drifted.")
    native = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    if set(native.get("mcpServers") or {}) != {"evidence-lane"}:
        raise InstallationError("The package must contain one native evidence-lane MCP.")
    forbidden = (
        plugin_root / "chatgpt-app-connection.json",
        plugin_root / "chatgpt-app-submission.json",
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    if any(path.exists() for path in forbidden):
        raise InstallationError("A separate ChatGPT, website, or evidence surface leaked in.")
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_text("utf-8"))
    hook_events = dict(hooks.get("hooks") or {})
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hook_events.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    post_groups = hook_events.get("PostToolUse") or []
    post_matcher = str(post_groups[0].get("matcher") or "") if post_groups else ""
    if set(hook_events) != {
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUse",
        "Stop",
    } or handler_count != 4 or "pv_plan_steer_delta" not in post_matcher:
        raise InstallationError("The persistent hook event set drifted.")
    for name in (
        "session_start.py",
        "prompt_submit.py",
        "post_tool_use.py",
        "stop_response.py",
    ):
        source = (plugin_root / "hooks" / name).read_text(encoding="utf-8")
        if "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=" not in source:
            raise InstallationError(f"{name} does not emit the persistent change notice.")
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "catalog": catalog,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
    }


def _marketplace_bytes() -> bytes:
    return _json_bytes(
        {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "Evidence Lane 2.0 Stable"},
            "plugins": [
                {
                    "name": PLUGIN_NAME,
                    "source": {
                        "source": "local",
                        "path": "./plugins/evidence-lane-plugin",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Developer Tools",
                }
            ],
        }
    )


def _stage_marketplace(
    *,
    extracted: Path,
    marketplace_root: Path,
    data_root: Path,
    identity: dict[str, Any],
    archive_sha256: str,
) -> dict[str, Any]:
    codex_home = marketplace_root.parent.parent.resolve()
    expected_parent = codex_home / "local-marketplaces"
    if marketplace_root.parent.resolve() != expected_parent.resolve():
        raise InstallationError("Marketplace root is outside Codex local-marketplaces.")
    cache_root = codex_home / "plugins" / "cache"
    if _inside(marketplace_root, cache_root):
        raise InstallationError("Generated Codex plugin cache is immutable to this installer.")
    staging = marketplace_root.parent / (
        f".{marketplace_root.name}.staging-{uuid.uuid4().hex}"
    )
    staging_plugin = staging / "plugins" / PLUGIN_NAME
    staging_plugin.parent.mkdir(parents=True, exist_ok=True)
    prior_plugin = marketplace_root / "plugins" / PLUGIN_NAME
    previous_surface = None
    if prior_plugin.is_dir():
        prior_manifest = json.loads(
            (prior_plugin / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        previous_surface = _surface_inventory(
            prior_plugin,
            version=str(prior_manifest.get("version") or "UNVERIFIED"),
        )
    surface_change = _surface_change_display(
        previous=previous_surface,
        current=identity["surface_inventory"],
    )
    try:
        shutil.copytree(
            extracted,
            staging_plugin,
            ignore=shutil.ignore_patterns("_evidence_lane_rehearsal"),
        )
        marketplace_path = staging / ".agents" / "plugins" / "marketplace.json"
        _write_atomic(marketplace_path, _marketplace_bytes())
        prepared = {
            "schema": "evidence-lane.codex-marketplace-stage.v2",
            "state": "STAGED_NOT_HOST_ACTIVE",
            "marketplace": MARKETPLACE_NAME,
            "plugin": identity,
            "archive_sha256": archive_sha256,
            "generated_cache_written_directly": False,
            "prior_release_deleted": False,
        }
        _write_atomic(staging / "EVIDENCE_LANE_STAGE.json", _json_bytes(prepared))
        if marketplace_root.exists():
            current_stage = marketplace_root / "EVIDENCE_LANE_STAGE.json"
            if current_stage.is_file():
                current = json.loads(current_stage.read_text(encoding="utf-8"))
                if current.get("archive_sha256") == archive_sha256:
                    shutil.rmtree(staging)
                    return {
                        "state": "ALREADY_STAGED_EXACT",
                        "prior_marketplace_archived": False,
                        "surface_change_display": surface_change,
                    }
            archive_root = (
                data_root
                / "installations"
                / "codex-v200"
                / "marketplace-archives"
            )
            archive_root.mkdir(parents=True, exist_ok=True)
            prior_sha = _sha256(current_stage) if current_stage.is_file() else "UNSEALED"
            archive_target = archive_root / f"{marketplace_root.name}-{prior_sha[:16]}"
            if archive_target.exists():
                raise InstallationError("The exact prior marketplace archive already exists.")
            if not _inside(marketplace_root, expected_parent):
                raise InstallationError("Refusing to move an uncontained marketplace root.")
            os.replace(marketplace_root, archive_target)
            prior_archived = True
        else:
            prior_archived = False
        os.replace(staging, marketplace_root)
        return {
            "state": "STAGED",
            "prior_marketplace_archived": prior_archived,
            "surface_change_display": surface_change,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _run_codex(
    executable: Path,
    codex_home: Path,
    arguments: list[str],
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    completed = subprocess.run(
        [str(executable), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
    )
    if completed.returncode != 0:
        raise InstallationError(
            f"Codex command failed ({arguments[:3]}): {completed.stderr.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallationError("Codex did not return the requested JSON receipt.") from exc


def _set_exclusive_evidence_lane_channel(
    *,
    config_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    raw = config_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    plugins = parsed.get("plugins") or {}
    if PLUGIN_SELECTOR not in plugins:
        raise InstallationError("Codex did not persist the v2 plugin selector.")
    section = re.compile(r'^\[plugins\."(?P<selector>[^"]+)"(?P<tail>.*)\]$')
    enabled = re.compile(r"^(?P<indent>\s*)enabled\s*=\s*(?:true|false)\s*$")
    lines = raw.splitlines(keepends=True)
    current_selector: str | None = None
    changed: list[str] = []
    saw_v2_root = False
    for index, line in enumerate(lines):
        match = section.match(line.rstrip("\r\n"))
        if match:
            current_selector = match.group("selector")
            if current_selector == PLUGIN_SELECTOR and not match.group("tail"):
                saw_v2_root = True
            continue
        match_enabled = enabled.match(line.rstrip("\r\n"))
        if not match_enabled or not current_selector:
            continue
        if not current_selector.startswith(f"{PLUGIN_NAME}@"):
            continue
        desired = current_selector == PLUGIN_SELECTOR
        replacement = f'{match_enabled.group("indent")}enabled = {str(desired).lower()}'
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        if line.rstrip("\r\n") != replacement:
            lines[index] = replacement + newline
            changed.append(current_selector)
    if not saw_v2_root:
        raise InstallationError("The v2 plugin root section is absent from Codex config.")
    before_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    after = "".join(lines)
    config_archive = data_root / "installations" / "codex-v200" / "config-archives"
    config_archive.mkdir(parents=True, exist_ok=True)
    backup = config_archive / f"config-{before_sha}.toml"
    if not backup.exists():
        _write_atomic(backup, raw.encode("utf-8"))
    if changed:
        _write_atomic(config_path, after.encode("utf-8"))
    verified = tomllib.loads(config_path.read_text(encoding="utf-8"))
    for selector, settings in (verified.get("plugins") or {}).items():
        if selector.startswith(f"{PLUGIN_NAME}@"):
            expected = selector == PLUGIN_SELECTOR
            if bool(settings.get("enabled")) is not expected:
                raise InstallationError("Evidence Lane channel exclusivity did not persist.")
    return {
        "before_sha256": before_sha,
        "after_sha256": _sha256(config_path),
        "backup": str(backup),
        "changed_selectors": sorted(set(changed)),
        "previous_release_cache_deleted": False,
    }


def install(args: argparse.Namespace) -> dict[str, Any]:
    archive = args.archive.resolve()
    receipt_path = args.rehearsal_receipt.resolve()
    codex_home = args.codex_home.resolve()
    data_root = args.data_root.resolve()
    marketplace_root = codex_home / "local-marketplaces" / MARKETPLACE_NAME
    if _inside(archive, codex_home / "plugins" / "cache"):
        raise InstallationError("A generated cache package cannot be installation input.")
    _load_receipt(receipt_path, archive)
    with tempfile.TemporaryDirectory(prefix="evidence-lane-v200-install-") as raw:
        extracted = Path(raw) / "plugin"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        identity = _validate_plugin(extracted)
        stage = _stage_marketplace(
            extracted=extracted,
            marketplace_root=marketplace_root,
            data_root=data_root,
            identity=identity,
            archive_sha256=_sha256(archive),
        )
    activation: dict[str, Any] = {
        "state": "STAGED_RESTART_NOT_YET_REQUIRED",
        "plugin_add_invoked": False,
    }
    config_receipt: dict[str, Any] | None = None
    if args.activate:
        executable = args.codex_executable.resolve()
        if not executable.is_file():
            raise InstallationError("The exact Codex executable is unavailable.")
        listed = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "list", "--json"],
        )
        known = {row["name"]: Path(row["root"]).resolve() for row in listed["marketplaces"]}
        if MARKETPLACE_NAME in known:
            if known[MARKETPLACE_NAME] != marketplace_root.resolve():
                raise InstallationError("The v2 marketplace name is bound elsewhere.")
            marketplace_add = {"alreadyAdded": True, "marketplaceName": MARKETPLACE_NAME}
        else:
            marketplace_add = _run_codex(
                executable,
                codex_home,
                ["plugin", "marketplace", "add", str(marketplace_root), "--json"],
            )
        plugin_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "add", PLUGIN_SELECTOR, "--json"],
        )
        installed_path = Path(str(plugin_add.get("installedPath") or "")).resolve()
        expected_cache = codex_home / "plugins" / "cache" / MARKETPLACE_NAME / PLUGIN_NAME
        if (
            plugin_add.get("pluginId") != PLUGIN_SELECTOR
            or plugin_add.get("version") != identity["version"]
            or not _inside(installed_path, expected_cache)
        ):
            raise InstallationError("Codex installed a mismatched plugin cache identity.")
        config_receipt = _set_exclusive_evidence_lane_channel(
            config_path=codex_home / "config.toml",
            data_root=data_root,
        )
        activation = {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add_invoked": True,
            "marketplace_add": marketplace_add,
            "plugin_add": plugin_add,
            "exclusive_channel": config_receipt,
            "hot_reload_claimed": False,
        }
    body = {
        "schema": INSTALL_SCHEMA,
        "status": "PASS",
        "plugin": identity,
        "catalog_expected": dict(EXPECTED_CATALOG),
        "rehearsal_receipt_sha256": _sha256(receipt_path),
        "archive_sha256": _sha256(archive),
        "marketplace": {
            "name": MARKETPLACE_NAME,
            "root": str(marketplace_root),
            **stage,
        },
        "surface_change_display": stage["surface_change_display"],
        "activation": activation,
        "archive_release_retained": "1.5.0",
        "previous_release_cache_deleted": False,
        "generated_cache_written_directly": False,
        "credential_requested_or_stored": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_required": bool(args.activate),
    }
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    receipt_dir = data_root / "installations" / "codex-v200"
    install_receipt = receipt_dir / f"INSTALL_{body['archive_sha256'][:16]}.json"
    _write_atomic(install_receipt, _json_bytes(body))
    _write_atomic(receipt_dir / "CURRENT_INSTALLATION.json", _json_bytes(body))
    return {**body, "receipt_path": str(install_receipt)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--rehearsal-receipt", type=Path, required=True)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.environ.get("EVIDENCE_LANE_DATA_ROOT")
            or Path.home() / "EvidenceLanePV"
        ),
    )
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--activate", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.activate and args.codex_executable is None:
        raise InstallationError("--activate requires --codex-executable.")
    print(json.dumps(install(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
