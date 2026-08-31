"""Verify installed-hook behavior in a fresh, disabled local projection.

This verifier exercises the exact Windows command recorded in ``hooks.json``
without installing or enabling a Codex plugin.  It projects only Git-tracked
and current untracked plugin bytes into a caller-owned temporary root, creates
the same content-addressed derived runtime used by the installed launcher, and
binds that projection to one sealed, explicitly disabled install receipt.

The proof boundary is intentionally narrow: this is an isolated installed-
runtime harness, not host activation evidence.  It never opens Codex config,
uses a live plugin selector, invokes the installer helper, or claims HIL/PV
movement.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import site
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import venv
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Final

RECEIPT_SCHEMA: Final = "evidence-lane.row210-isolated-hook-runtime.v1"
PROJECTION_SCHEMA: Final = "evidence-lane.row210-exact-byte-projection.v1"
INSTALL_SCHEMA: Final = "evidence-lane.row210-disabled-install-binding.v1"
INSTALLATION_ID: Final = "row210-isolated-hook-runtime"

EVENT_HANDLERS: Final = {
    "SessionStart": "session_start.py",
    "SubagentStart": "subagent_start.py",
    "UserPromptSubmit": "prompt_submit.py",
    "PreToolUse": "pre_tool_use.py",
    "PermissionRequest": "permission_request.py",
    "PostToolUse": "post_tool_use.py",
    "PreCompact": "lifecycle_boundary.py",
    "PostCompact": "lifecycle_boundary.py",
    "SubagentStop": "subagent_stop.py",
    "Stop": "stop_response.py",
    "SessionEnd": "lifecycle_boundary.py",
}
EVENT_ORDER: Final = tuple(EVENT_HANDLERS)
STAGE_HANDLERS: Final = (
    "subhook_validate.py",
    "subhook_seal.py",
    "subhook_transport.py",
    "subhook_emit.py",
)


class IsolatedRuntimeVerificationError(RuntimeError):
    """One bounded failure in the disabled installed-runtime harness."""


def _json_bytes(value: Any) -> bytes:
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_module(path: Path, role: str) -> ModuleType:
    module_name = f"row210_{role}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise IsolatedRuntimeVerificationError(f"{role.upper()}_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_fresh_isolated_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if resolved.exists():
        raise IsolatedRuntimeVerificationError("ISOLATED_ROOT_MUST_NOT_EXIST")
    if resolved == Path(resolved.anchor):
        raise IsolatedRuntimeVerificationError("ISOLATED_ROOT_TOO_BROAD")
    resolved.mkdir(parents=True)
    marker = {
        "schema": RECEIPT_SCHEMA,
        "role": "FRESH_CALLER_OWNED_ISOLATED_ROOT",
        "path_sha256": _sha256_bytes(os.path.normcase(str(resolved)).encode("utf-8")),
    }
    (resolved / ".row210-isolated-root.json").write_bytes(_json_bytes(marker))
    return resolved


def _git_projection_files(
    source_plugin_root: Path,
) -> tuple[Path, list[Path], list[Path]]:
    source = source_plugin_root.resolve()
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise IsolatedRuntimeVerificationError("SOURCE_GIT_ROOT_UNAVAILABLE")
    repo_root = Path(completed.stdout.strip()).resolve()
    try:
        plugin_relative = source.relative_to(repo_root)
    except ValueError as exc:
        raise IsolatedRuntimeVerificationError("PLUGIN_OUTSIDE_GIT_ROOT") from exc
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            plugin_relative.as_posix(),
        ],
        capture_output=True,
        check=False,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if listed.returncode != 0:
        raise IsolatedRuntimeVerificationError("PLUGIN_FILE_INVENTORY_UNAVAILABLE")
    paths: list[Path] = []
    deleted_paths: list[Path] = []
    for encoded in listed.stdout.split(b"\0"):
        if not encoded:
            continue
        try:
            repo_relative = Path(encoded.decode("utf-8"))
            source_path = (repo_root / repo_relative).resolve()
            plugin_path = source_path.relative_to(source)
        except (UnicodeDecodeError, ValueError) as exc:
            raise IsolatedRuntimeVerificationError(
                "PLUGIN_FILE_INVENTORY_PATH_INVALID"
            ) from exc
        if not source_path.exists():
            deleted_paths.append(plugin_path)
            continue
        if source_path.is_symlink() or not source_path.is_file():
            raise IsolatedRuntimeVerificationError(
                "PLUGIN_FILE_INVENTORY_REGULAR_FILE_REQUIRED"
            )
        paths.append(plugin_path)
    paths = sorted(set(paths), key=lambda value: value.as_posix())
    deleted_paths = sorted(
        set(deleted_paths), key=lambda value: value.as_posix()
    )
    if not paths:
        raise IsolatedRuntimeVerificationError("PLUGIN_FILE_INVENTORY_EMPTY")
    required = {
        Path("hooks/hooks.json"),
        Path("hooks/invoke_hook.ps1"),
        Path("hooks/invoke_hook.py"),
        Path("hooks/event_isolation.py"),
        Path("requirements.lock.txt"),
        Path("requirements.toolchain.lock.txt"),
        Path("scripts/runtime_contract.py"),
    }
    if not required.issubset(paths):
        raise IsolatedRuntimeVerificationError("PLUGIN_PROJECTION_REQUIRED_FILE_MISSING")
    return repo_root, paths, deleted_paths


def _project_exact_bytes(
    source_plugin_root: Path,
    destination_plugin_root: Path,
) -> dict[str, Any]:
    source = source_plugin_root.resolve()
    destination = destination_plugin_root.resolve()
    _, relative_paths, deleted_paths = _git_projection_files(source)
    records: list[dict[str, Any]] = []
    total_bytes = 0
    for relative in relative_paths:
        source_path = source / relative
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        source_sha256 = _sha256_file(source_path)
        destination_sha256 = _sha256_file(destination_path)
        if destination_sha256 != source_sha256:
            raise IsolatedRuntimeVerificationError("PLUGIN_PROJECTION_BYTE_MISMATCH")
        size = source_path.stat().st_size
        total_bytes += size
        records.append(
            {
                "path": relative.as_posix(),
                "size": size,
                "sha256": source_sha256,
            }
        )
    manifest = {
        "schema": PROJECTION_SCHEMA,
        "source_inventory": "GIT_TRACKED_PLUS_CURRENT_UNTRACKED_EXCLUDING_IGNORED",
        "file_count": len(records),
        "total_bytes": total_bytes,
        "tracked_worktree_deletion_count": len(deleted_paths),
        "tracked_worktree_deletions": [
            value.as_posix() for value in deleted_paths
        ],
        "records": records,
    }
    manifest_sha256 = _sha256_bytes(_json_bytes(manifest))
    manifest_path = destination.parent / "EXACT_BYTE_PROJECTION.json"
    manifest_path.write_bytes(
        _json_bytes({**manifest, "manifest_sha256": manifest_sha256})
    )
    return {
        "schema": PROJECTION_SCHEMA,
        "status": "PASS",
        "source_inventory": manifest["source_inventory"],
        "file_count": len(records),
        "total_bytes": total_bytes,
        "tracked_worktree_deletion_count": len(deleted_paths),
        "tracked_worktree_deletions_sha256": _sha256_bytes(
            _json_bytes([value.as_posix() for value in deleted_paths])
        ),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "destination_plugin_root": str(destination),
        "exact_bytes_verified": True,
        "ignored_build_artifacts_copied": False,
    }


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _dependency_site_packages(explicit: Path | None) -> Path:
    candidates = [explicit] if explicit is not None else [
        Path(value) for value in site.getsitepackages()
    ]
    for candidate in reversed(candidates):
        if candidate is not None and candidate.resolve().is_dir():
            return candidate.resolve()
    raise IsolatedRuntimeVerificationError("DEPENDENCY_SITE_PACKAGES_UNAVAILABLE")


def _create_derived_runtime(
    plugin_root: Path,
    data_root: Path,
    dependency_site_packages: Path,
) -> dict[str, Any]:
    contract = _load_module(plugin_root / "scripts/runtime_contract.py", "runtime")
    with _temporary_environment(
        {"EVIDENCE_LANE_RUNTIME_CONTROL_ROOT": str(data_root)}
    ):
        runtime_root = Path(contract.runtime_projection_root(plugin_root)).resolve()
        runtime_environment = Path(contract.runtime_environment(plugin_root)).resolve()
        venv.EnvBuilder(with_pip=False, clear=False, symlinks=False).create(
            runtime_environment
        )
        runtime_site = runtime_environment / "Lib/site-packages"
        if os.name != "nt":
            version = f"python{sys.version_info.major}.{sys.version_info.minor}"
            runtime_site = runtime_environment / "lib" / version / "site-packages"
        runtime_site.mkdir(parents=True, exist_ok=True)
        dependency_link = runtime_site / "row210-isolated-dependencies.pth"
        dependency_link.write_text(
            str(dependency_site_packages) + "\n",
            encoding="utf-8",
            newline="",
        )
        marker_path = Path(contract.runtime_marker(plugin_root)).resolve()
        marker = contract.write_marker(plugin_root, marker_path)
        if not contract.marker_is_valid(plugin_root, marker_path):
            raise IsolatedRuntimeVerificationError("DERIVED_RUNTIME_MARKER_INVALID")
    python = (
        runtime_environment / "Scripts/python.exe"
        if os.name == "nt"
        else runtime_environment / "bin/python"
    ).resolve()
    if not python.is_file():
        raise IsolatedRuntimeVerificationError("DERIVED_RUNTIME_PYTHON_MISSING")
    dependency_probe = subprocess.run(
        [
            str(python),
            "-c",
            "import cryptography, pydantic; print('ROW210_DEPENDENCIES_PASS')",
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if (
        dependency_probe.returncode != 0
        or dependency_probe.stdout.strip() != "ROW210_DEPENDENCIES_PASS"
    ):
        raise IsolatedRuntimeVerificationError("DERIVED_RUNTIME_DEPENDENCY_PROBE_FAILED")
    return {
        "schema": str(contract.RUNTIME_SCHEMA),
        "status": "PASS",
        "runtime_projection_root": str(runtime_root),
        "runtime_environment": str(runtime_environment),
        "runtime_python_sha256": _sha256_file(python),
        "runtime_marker": str(marker_path),
        "runtime_marker_sha256": _sha256_file(marker_path),
        "runtime_identity": marker["runtime_identity"],
        "dependency_mode": "READ_ONLY_CALLER_SITE_PACKAGES_FOR_ISOLATED_HARNESS",
        "dependency_site_packages_path_sha256": _sha256_bytes(
            os.path.normcase(str(dependency_site_packages)).encode("utf-8")
        ),
        "dependency_probe": "PASS",
        "live_install_equivalence_claimed": False,
    }


def _initialize_kill_switch(plugin_root: Path, data_root: Path) -> dict[str, Any]:
    event_isolation = _load_module(
        plugin_root / "hooks/event_isolation.py", "event_isolation"
    )
    receipt = event_isolation.initialize_inactive_kill_switch(
        data_root,
        installation_id=INSTALLATION_ID,
    )
    if receipt.get("status") != "PASS" or receipt.get("state") not in {
        "INACTIVE_KILL_SWITCH_INITIALIZED",
        "INACTIVE_KILL_SWITCH_REUSED",
    }:
        raise IsolatedRuntimeVerificationError("KILL_SWITCH_INITIALIZATION_FAILED")
    return dict(receipt)


def _write_install_binding(
    path: Path,
    *,
    plugin_root: Path,
    runtime: Mapping[str, Any],
    kill_switch: Mapping[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": INSTALL_SCHEMA,
        "status": "PASS",
        "installation_id": INSTALLATION_ID,
        "activation_mode": "ISOLATED_HARNESS_DISABLED",
        "plugin_enabled": False,
        "host_config_written": False,
        "installer_helper_invoked": False,
        "task_binding_used": False,
        "tunnel_invoked": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "activation": {
            "plugin_add": {
                "installedPath": str(plugin_root.resolve()),
                "installed": True,
                "enabled": False,
                "isolated_projection_only": True,
            },
            "runtime_prewarm": {
                "status": "PASS",
                "runtime_projection_root": runtime["runtime_projection_root"],
                "runtime_identity": runtime["runtime_identity"],
                "live_activation_ready": False,
            },
            "hook_event_isolation": {
                "status": "PASS",
                "verified_before_install_activation": True,
                "persistent_kill_switch": True,
                "kill_switch_receipt_path": kill_switch["path"],
                "kill_switch_receipt_sha256": kill_switch["file_sha256"],
                "policy_sha256": kill_switch["policy_sha256"],
            },
        },
    }
    body["receipt_sha256"] = _sha256_bytes(_json_bytes(body))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_json_bytes(body))
    os.replace(temporary, path)
    return body


def _activate_temporary_kill_switch(
    kill_switch_path: Path,
    event_isolation: ModuleType,
) -> dict[str, Any]:
    original = json.loads(kill_switch_path.read_text(encoding="utf-8"))
    payload = dict(original["payload"])
    payload["state"] = "ACTIVE"
    payload["generation"] = int(payload.get("generation") or 1) + 1
    payload["created_at_utc"] = _utc_now()
    body = {
        "schema": event_isolation.KILL_SWITCH_SCHEMA,
        "payload": payload,
        "payload_sha256": event_isolation._sha256(
            event_isolation._canonical_bytes(payload)
        ),
    }
    temporary = kill_switch_path.with_suffix(".active.tmp")
    temporary.write_bytes(event_isolation._canonical_bytes(body))
    os.replace(temporary, kill_switch_path)
    return {
        "path": str(kill_switch_path),
        "file_sha256": _sha256_file(kill_switch_path),
        "policy_sha256": event_isolation.POLICY_SHA256,
        "state": "ACTIVE",
    }


def _restore_temporary_kill_switch(
    kill_switch_path: Path,
    *,
    inactive_bytes: bytes,
    inactive_sha256: str,
    event_isolation: ModuleType,
) -> dict[str, Any]:
    """Restore only the harness-owned receipt after the active-state probe.

    Production initialization must fail closed on an ACTIVE kill switch.  The
    isolated verifier temporarily activates its own receipt to prove denial, so
    it must restore the exact pre-probe bytes instead of asking production code
    to overwrite that ACTIVE authority.
    """

    if _sha256_bytes(inactive_bytes) != inactive_sha256:
        raise IsolatedRuntimeVerificationError(
            "KILL_SWITCH_PRE_PROBE_BYTES_DRIFTED"
        )
    temporary = kill_switch_path.with_suffix(".inactive-restore.tmp")
    temporary.write_bytes(inactive_bytes)
    os.replace(temporary, kill_switch_path)
    _body, payload = event_isolation._read_kill_switch(kill_switch_path)
    if (
        str(payload.get("state") or "") != "INACTIVE"
        or _sha256_file(kill_switch_path) != inactive_sha256
    ):
        raise IsolatedRuntimeVerificationError(
            "KILL_SWITCH_EXACT_RESTORE_FAILED"
        )
    return {
        "path": str(kill_switch_path),
        "file_sha256": inactive_sha256,
        "policy_sha256": event_isolation.POLICY_SHA256,
        "state": "INACTIVE_KILL_SWITCH_RESTORED",
    }


def _base_child_environment(data_root: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("EVIDENCE_LANE_")
        and key.upper()
        not in {"CODEX_HOME", "PLUGIN_DATA", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(data_root.resolve())
    environment["EVIDENCE_LANE_ROW210_ISOLATED_HARNESS"] = "1"
    return environment


def _powershell_executable(environment: Mapping[str, str]) -> Path:
    system_root = str(environment.get("SystemRoot") or "C:\\Windows")
    executable = (
        Path(system_root)
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    ).resolve()
    if os.name != "nt" or not executable.is_file():
        raise IsolatedRuntimeVerificationError("WINDOWS_POWERSHELL_REQUIRED")
    return executable


def _is_failure_output(output: Mapping[str, Any]) -> bool:
    serialized = _json_bytes(dict(output)).decode("utf-8")
    return (
        "EVIDENCE_LANE_HOOK_LAUNCH_DIAGNOSTIC=" in serialized
        or "EVIDENCE_LANE_HOOK_EVENT_ISOLATION=" in serialized
        or '"status":"FAIL_CLOSED"' in serialized
    )


def _invoke_windows_hook(
    *,
    plugin_root: Path,
    workspace: Path,
    environment: Mapping[str, str],
    event_name: str,
    payload: Mapping[str, Any],
    ordinal: int,
    allow_failure_output: bool = False,
    handler_names: tuple[str, ...] = STAGE_HANDLERS,
) -> dict[str, Any]:
    powershell = _powershell_executable(environment)
    launcher = (plugin_root / "hooks/invoke_hook.ps1").resolve()
    started = time.monotonic()
    raw_payload = _json_bytes(dict(payload)).decode("utf-8")
    stage_records: list[dict[str, Any]] = []
    for handler in handler_names:
        command = [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(launcher),
            event_name,
            handler,
        ]
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            stdout, stderr = process.communicate(input=raw_payload, timeout=20)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise IsolatedRuntimeVerificationError("HOOK_COMMAND_TIMEOUT") from exc
        serialized = stdout.strip()
        if process.returncode != 0 or not serialized:
            raise IsolatedRuntimeVerificationError("HOOK_COMMAND_NONZERO_OR_EMPTY")
        try:
            stage_output = json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise IsolatedRuntimeVerificationError(
                "HOOK_COMMAND_OUTPUT_JSON_INVALID"
            ) from exc
        if not isinstance(stage_output, dict):
            raise IsolatedRuntimeVerificationError("HOOK_COMMAND_OUTPUT_OBJECT_REQUIRED")
        stage_records.append(
            {
                "handler": handler,
                "output": stage_output,
                "output_sha256": _sha256_bytes(_json_bytes(stage_output)),
                "returncode": int(process.returncode),
                "launcher_process_id": int(process.pid),
                "stderr_sha256": _sha256_bytes(stderr.encode("utf-8")),
                "command_sha256": _sha256_bytes(_json_bytes(command)),
            }
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    output = dict(stage_records[-1]["output"])
    failure = _is_failure_output(output)
    if failure and not allow_failure_output:
        raise IsolatedRuntimeVerificationError("HOOK_COMMAND_FAILED_CLOSED_UNEXPECTEDLY")
    if not failure and allow_failure_output:
        raise IsolatedRuntimeVerificationError("HOOK_COMMAND_EXPECTED_FAILURE_MISSING")
    if event_name in {"Stop", "SessionEnd"} and output != {}:
        raise IsolatedRuntimeVerificationError("TERMINAL_HOOK_OUTPUT_NOT_EMPTY")
    return {
        "ordinal": ordinal,
        "event_name": event_name,
        "handlers": list(handler_names),
        "handler_count": len(handler_names),
        "stage_output_sha256": {
            row["handler"]: row["output_sha256"] for row in stage_records
        },
        "output": output,
        "output_sha256": _sha256_bytes(_json_bytes(output)),
        "output_keys": sorted(output),
        "failure_output": failure,
        "returncode": 0,
        "launcher_process_ids": [
            row["launcher_process_id"] for row in stage_records
        ],
        "duration_ms": duration_ms,
        "stderr_sha256": _sha256_bytes(
            _json_bytes([row["stderr_sha256"] for row in stage_records])
        ),
        "command_sha256": _sha256_bytes(
            _json_bytes([row["command_sha256"] for row in stage_records])
        ),
        "windows_process_window_mode": "CREATE_NO_WINDOW_PLUS_HIDDEN",
    }


def _payload(
    event_name: str,
    workspace: Path,
    *,
    variant: str = "primary",
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "cwd": str(workspace.resolve()),
        "hook_event_name": event_name,
        "model": "gpt-5.6-sol",
        "permission_mode": "default",
        "session_id": "session-row210-isolated-runtime",
        "transcript_path": None,
    }
    suffix = variant.replace("_", "-")
    if event_name == "SessionStart":
        return {**common, "source": "resume" if variant == "restart" else "startup"}
    if event_name == "SubagentStart":
        return {
            **common,
            "agent_id": f"agent-row210-{suffix}",
            "agent_type": "worker",
            "turn_id": "turn-row210-primary",
        }
    if event_name == "UserPromptSubmit":
        return {
            **common,
            "prompt": "Row210 isolated visible prompt.",
            "turn_id": f"turn-row210-{suffix}",
        }
    if event_name == "PreToolUse":
        return {
            **common,
            "tool_input": {"path": "bounded.txt"},
            "tool_name": "Read",
            "tool_use_id": f"tool-row210-{suffix}",
            "turn_id": f"turn-row210-{suffix}",
        }
    if event_name == "PermissionRequest":
        return {
            **common,
            "tool_input": {"path": "bounded.txt"},
            "tool_name": "Read",
            "turn_id": "turn-row210-primary",
        }
    if event_name == "PostToolUse":
        return {
            **common,
            "tool_input": {"path": "bounded.txt"},
            "tool_name": "Read",
            "tool_response": {"status": "PASS"},
            "tool_use_id": "tool-row210-primary",
            "turn_id": "turn-row210-primary",
        }
    if event_name in {"PreCompact", "PostCompact"}:
        common.pop("permission_mode")
        return {
            **common,
            "trigger": "auto",
            "turn_id": "turn-row210-primary",
        }
    if event_name == "SubagentStop":
        return {
            **common,
            "agent_id": "agent-row210-primary",
            "agent_transcript_path": None,
            "agent_type": "worker",
            "last_assistant_message": "Row210 bounded subagent response.",
            "stop_hook_active": variant == "replay",
            "turn_id": "turn-row210-primary",
        }
    if event_name == "Stop":
        return {
            **common,
            "last_assistant_message": "Row210 isolated visible response.",
            "stop_hook_active": variant == "replay",
            "turn_id": "turn-row210-primary",
        }
    if event_name == "SessionEnd":
        return {
            "cwd": str(workspace.resolve()),
            "hook_event_name": "SessionEnd",
            "reason": "other",
            "session_id": "session-row210-isolated-runtime",
            "transcript_path": None,
        }
    raise IsolatedRuntimeVerificationError("HOOK_EVENT_UNSUPPORTED")


def _event_rows(database_path: Path) -> list[dict[str, Any]]:
    if not database_path.is_file():
        raise IsolatedRuntimeVerificationError("EVENT_RECEIPT_DATABASE_MISSING")
    connection = sqlite3.connect(database_path, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        meta = dict(connection.execute("SELECT key,value FROM isolation_meta"))
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT correlation_id,event_name,owner_sha256,
                       occurrence_input_sha256,policy_sha256,
                       kill_switch_sha256,output_sha256,status,failure_code
                FROM event_receipt ORDER BY created_at_utc,correlation_id
                """
            )
        ]
    finally:
        connection.close()
    if meta.get("schema") != "evidence-lane.codex-hook-event-isolation-sqlite.v1":
        raise IsolatedRuntimeVerificationError("EVENT_RECEIPT_DATABASE_SCHEMA_MISMATCH")
    return rows


def _verify_primary_correlations(
    rows: list[dict[str, Any]],
    invocations: list[dict[str, Any]],
    policy_sha256: str,
) -> dict[str, Any]:
    expected_count = len(EVENT_ORDER) * len(STAGE_HANDLERS)
    if len(rows) != expected_count:
        raise IsolatedRuntimeVerificationError("PRIMARY_EVENT_RECEIPT_COUNT_MISMATCH")
    if {str(row["event_name"]) for row in rows} != set(EVENT_ORDER):
        raise IsolatedRuntimeVerificationError("PRIMARY_EVENT_RECEIPT_SET_MISMATCH")
    if any(
        sum(row["event_name"] == event_name for row in rows) != len(STAGE_HANDLERS)
        for event_name in EVENT_ORDER
    ):
        raise IsolatedRuntimeVerificationError("PRIMARY_EVENT_HANDLER_COUNT_MISMATCH")
    expected_output_hashes = sorted(
        str(value)
        for invocation in invocations
        for value in invocation["stage_output_sha256"].values()
    )
    if sorted(str(row["output_sha256"]) for row in rows) != expected_output_hashes:
        raise IsolatedRuntimeVerificationError("PRIMARY_EVENT_OUTPUT_CORRELATION_MISMATCH")
    if any(
        row["status"] != "COMPLETE"
        or row["failure_code"] is not None
        or row["policy_sha256"] != policy_sha256
        or not str(row["correlation_id"]).startswith("hook_")
        for row in rows
    ):
        raise IsolatedRuntimeVerificationError("PRIMARY_EVENT_RECEIPT_NOT_COMPLETE")
    owners = {str(row["owner_sha256"]) for row in rows}
    if len(owners) != 1:
        raise IsolatedRuntimeVerificationError("PRIMARY_EVENT_OWNER_MISMATCH")
    return {
        "status": "PASS",
        "event_count": len(rows),
        "events": list(EVENT_ORDER),
        "unique_correlation_count": len({row["correlation_id"] for row in rows}),
        "owner_sha256": next(iter(owners)),
        "policy_sha256": policy_sha256,
        "all_terminal_complete": True,
        "output_hashes_match_process_outputs": True,
        "raw_payloads_stored": False,
    }


def _validate_installed_manifest(plugin_root: Path) -> dict[str, Any]:
    value = json.loads((plugin_root / "hooks/hooks.json").read_text(encoding="utf-8"))
    hooks = value.get("hooks") if isinstance(value, dict) else None
    if not isinstance(hooks, dict) or tuple(hooks) != EVENT_ORDER:
        raise IsolatedRuntimeVerificationError("INSTALLED_HOOK_MANIFEST_ORDER_MISMATCH")
    records: list[dict[str, Any]] = []
    for event_name in EVENT_HANDLERS:
        groups = hooks.get(event_name)
        if not isinstance(groups, list) or len(groups) != 1:
            raise IsolatedRuntimeVerificationError("INSTALLED_HOOK_GROUP_COUNT_MISMATCH")
        commands = groups[0].get("hooks")
        if not isinstance(commands, list) or len(commands) != len(STAGE_HANDLERS):
            raise IsolatedRuntimeVerificationError("INSTALLED_HOOK_COMMAND_COUNT_MISMATCH")
        for handler, command in zip(STAGE_HANDLERS, commands, strict=True):
            windows = str(command.get("commandWindows") or "")
            required = ("hooks\\EvidenceLaneHookHost.exe", event_name, handler)
            if command.get("type") != "command" or any(
                token not in windows for token in required
            ):
                raise IsolatedRuntimeVerificationError(
                    "INSTALLED_HOOK_WINDOWS_COMMAND_MISMATCH"
                )
            if "powershell.exe" in windows.casefold() or "invoke_hook.ps1" in windows.casefold():
                raise IsolatedRuntimeVerificationError(
                    "INSTALLED_HOOK_WINDOWS_COMMAND_MISMATCH"
                )
            expected_timeout = 3 if event_name == "SessionEnd" else 10
            if command.get("timeout") != expected_timeout:
                raise IsolatedRuntimeVerificationError(
                    "SESSION_END_HOST_TIMEOUT_MISMATCH"
                )
            records.append(
                {
                    "event_name": event_name,
                    "handler": handler,
                    "timeout": command.get("timeout"),
                    "command_windows_sha256": _sha256_bytes(windows.encode("utf-8")),
                }
            )
    return {
        "status": "PASS",
        "event_count": len(EVENT_ORDER),
        "handler_count": len(records),
        "records": records,
        "exact_windows_command_present": True,
    }


def verify_isolated_installed_runtime(
    *,
    source_plugin_root: Path,
    isolated_root: Path,
    dependency_site_packages: Path | None = None,
) -> dict[str, Any]:
    """Run the complete disabled Row210 proof and return its sealed receipt."""

    started_at = _utc_now()
    root = _require_fresh_isolated_root(isolated_root)
    plugin_root = root / "installed/evidence-lane-plugin"
    data_root = (root / "data").resolve()
    workspace = (root / "workspace").resolve()
    workspace.mkdir(parents=True)
    projection = _project_exact_bytes(source_plugin_root, plugin_root)
    installed_manifest = _validate_installed_manifest(plugin_root)
    dependencies = _dependency_site_packages(dependency_site_packages)
    runtime = _create_derived_runtime(plugin_root, data_root, dependencies)
    kill_switch = _initialize_kill_switch(plugin_root, data_root)
    install_path = data_root / "installations/codex-v300/INSTALL_ROW210.json"
    install_binding = _write_install_binding(
        install_path,
        plugin_root=plugin_root,
        runtime=runtime,
        kill_switch=kill_switch,
    )
    environment = _base_child_environment(data_root)
    invocations: list[dict[str, Any]] = []
    for ordinal, event_name in enumerate(EVENT_ORDER, start=1):
        invocations.append(
            _invoke_windows_hook(
                plugin_root=plugin_root,
                workspace=workspace,
                environment=environment,
                event_name=event_name,
                payload=_payload(event_name, workspace),
                ordinal=ordinal,
            )
        )
    database_path = data_root / "hook-event-isolation/event_receipts.sqlite"
    primary_handler_count = len(EVENT_ORDER) * len(STAGE_HANDLERS)
    primary_rows = _event_rows(database_path)
    correlation = _verify_primary_correlations(
        primary_rows,
        invocations,
        str(kill_switch["policy_sha256"]),
    )

    replay = _invoke_windows_hook(
        plugin_root=plugin_root,
        workspace=workspace,
        environment=environment,
        event_name="Stop",
        payload=_payload("Stop", workspace, variant="replay"),
        ordinal=len(EVENT_ORDER) + 1,
    )
    if replay["output"] != {} or len(_event_rows(database_path)) != primary_handler_count:
        raise IsolatedRuntimeVerificationError("STOP_REPLAY_NOT_EXACTLY_ONCE")

    subagent_replay = _invoke_windows_hook(
        plugin_root=plugin_root,
        workspace=workspace,
        environment=environment,
        event_name="SubagentStop",
        payload=_payload("SubagentStop", workspace, variant="replay"),
        ordinal=len(EVENT_ORDER) + 2,
    )
    primary_subagent_output = next(
        row["output"] for row in invocations if row["event_name"] == "SubagentStop"
    )
    if (
        subagent_replay["output"] != primary_subagent_output
        or len(_event_rows(database_path)) != primary_handler_count
    ):
        raise IsolatedRuntimeVerificationError(
            "SUBAGENT_STOP_REPLAY_NOT_EXACTLY_ONCE"
        )

    event_isolation = _load_module(
        plugin_root / "hooks/event_isolation.py", "event_isolation_lock"
    )
    reentrant_payload = _payload("PreToolUse", workspace, variant="reentrant")
    reentrant_raw = _json_bytes(reentrant_payload).decode("utf-8")
    _, reentrant_input_sha256 = event_isolation.validate_input(
        "PreToolUse", reentrant_raw
    )
    _reentrant_owner, reentrant_correlation_id = event_isolation._identity(
        "PreToolUse",
        reentrant_payload,
        reentrant_input_sha256,
        "subhook_validate.py",
    )
    with (
        _temporary_environment(
            {"EVIDENCE_LANE_RUNTIME_CONTROL_ROOT": str(data_root)}
        ),
        event_isolation._OwnerLock(reentrant_correlation_id),
    ):
        reentrant = _invoke_windows_hook(
            plugin_root=plugin_root,
            workspace=workspace,
            environment=environment,
            event_name="PreToolUse",
            payload=reentrant_payload,
            ordinal=len(EVENT_ORDER) + 3,
            allow_failure_output=True,
            handler_names=("subhook_validate.py",),
        )
    if (
        "HOOK_EVENT_REENTRANCY_DENIED"
        not in _json_bytes(reentrant["output"]).decode("utf-8")
        or len(_event_rows(database_path)) != primary_handler_count
    ):
        raise IsolatedRuntimeVerificationError("REENTRANCY_DENIAL_NOT_PROVEN")

    kill_switch_path = Path(kill_switch["path"])
    inactive_kill_switch_bytes = kill_switch_path.read_bytes()
    inactive_kill_switch_sha256 = _sha256_bytes(inactive_kill_switch_bytes)
    if inactive_kill_switch_sha256 != str(kill_switch["file_sha256"]):
        raise IsolatedRuntimeVerificationError(
            "KILL_SWITCH_PRE_PROBE_RECEIPT_DRIFTED"
        )
    active_kill_switch = _activate_temporary_kill_switch(
        kill_switch_path, event_isolation
    )
    _write_install_binding(
        install_path,
        plugin_root=plugin_root,
        runtime=runtime,
        kill_switch=active_kill_switch,
    )
    kill_invocation = _invoke_windows_hook(
        plugin_root=plugin_root,
        workspace=workspace,
        environment=environment,
        event_name="UserPromptSubmit",
        payload=_payload("UserPromptSubmit", workspace, variant="kill-switch"),
        ordinal=len(EVENT_ORDER) + 4,
        allow_failure_output=True,
        handler_names=("subhook_validate.py",),
    )
    if (
        "HOOK_KILL_SWITCH_ACTIVE"
        not in _json_bytes(kill_invocation["output"]).decode("utf-8")
        or len(_event_rows(database_path)) != primary_handler_count
    ):
        raise IsolatedRuntimeVerificationError("ACTIVE_KILL_SWITCH_NOT_PROVEN")

    recovered_kill_switch = _restore_temporary_kill_switch(
        kill_switch_path,
        inactive_bytes=inactive_kill_switch_bytes,
        inactive_sha256=inactive_kill_switch_sha256,
        event_isolation=event_isolation,
    )
    install_binding = _write_install_binding(
        install_path,
        plugin_root=plugin_root,
        runtime=runtime,
        kill_switch=recovered_kill_switch,
    )
    restart = _invoke_windows_hook(
        plugin_root=plugin_root,
        workspace=workspace,
        environment=environment,
        event_name="SessionStart",
        payload=_payload("SessionStart", workspace, variant="restart"),
        ordinal=len(EVENT_ORDER) + 5,
    )
    restart_rows = _event_rows(database_path)
    if (
        len(restart_rows) != primary_handler_count + len(STAGE_HANDLERS)
        or sum(row["event_name"] == "SessionStart" for row in restart_rows)
        != 2 * len(STAGE_HANDLERS)
        or any(row["status"] != "COMPLETE" for row in restart_rows)
    ):
        raise IsolatedRuntimeVerificationError("RESTART_RECOVERY_NOT_PROVEN")
    runtime_contract = _load_module(
        plugin_root / "scripts/runtime_contract.py", "runtime_recovery"
    )
    with _temporary_environment(
        {"EVIDENCE_LANE_RUNTIME_CONTROL_ROOT": str(data_root)}
    ):
        marker_valid_after_restart = runtime_contract.marker_is_valid(
            plugin_root,
            Path(runtime["runtime_marker"]),
        )
    if not marker_valid_after_restart:
        raise IsolatedRuntimeVerificationError("RUNTIME_MARKER_STALE_AFTER_RESTART")

    all_invocations = [
        *invocations,
        replay,
        subagent_replay,
        reentrant,
        kill_invocation,
        restart,
    ]
    process_ids = {
        process_id
        for row in all_invocations
        for process_id in row["launcher_process_ids"]
    }
    expected_process_count = sum(row["handler_count"] for row in all_invocations)
    public_invocations = [
        {
            key: value
            for key, value in row.items()
            if key not in {"output", "launcher_process_ids"}
        }
        for row in all_invocations
    ]
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "started_at_utc": started_at,
        "completed_at_utc": _utc_now(),
        "mode": "ISOLATED_EXACT_BYTE_INSTALLED_RUNTIME_DISABLED",
        "source_plugin_root_sha256": _sha256_bytes(
            os.path.normcase(str(source_plugin_root.resolve())).encode("utf-8")
        ),
        "projection": projection,
        "installed_manifest": installed_manifest,
        "runtime": runtime,
        "install_binding": {
            "schema": install_binding["schema"],
            "status": install_binding["status"],
            "receipt_path": str(install_path),
            "receipt_file_sha256": _sha256_file(install_path),
            "plugin_enabled": False,
            "host_config_written": False,
        },
        "event_correlation": correlation,
        "stop_no_loop": {
            "status": "PASS",
            "first_output_sha256": next(
                row["output_sha256"] for row in invocations if row["event_name"] == "Stop"
            ),
            "replay_output_sha256": replay["output_sha256"],
            "first_and_replay_output": {},
            "handler_execution_count": 1,
            "database_row_count_after_replay": primary_handler_count,
            "continuation_requested": False,
        },
        "subagent_stop_no_loop": {
            "status": "PASS",
            "first_output_sha256": next(
                row["output_sha256"]
                for row in invocations
                if row["event_name"] == "SubagentStop"
            ),
            "replay_output_sha256": subagent_replay["output_sha256"],
            "handler_execution_count": 1,
            "database_row_count_after_replay": primary_handler_count,
            "continuation_control_emitted": False,
        },
        "reentrancy": {
            "status": "PASS",
            "denial_code": "HOOK_EVENT_REENTRANCY_DENIED",
            "database_row_created": False,
            "handler_executed": False,
        },
        "kill_switch": {
            "status": "PASS",
            "active_state_denial_code": "HOOK_KILL_SWITCH_ACTIVE",
            "database_row_created_while_active": False,
            "recovered_state": recovered_kill_switch["state"],
            "recovered_receipt_sha256": recovered_kill_switch["file_sha256"],
        },
        "restart_recovery": {
            "status": "PASS",
            "fresh_launcher_process": True,
            "persistent_store_reused": True,
            "receipt_count_before_restart": primary_handler_count,
            "receipt_count_after_restart": (
                primary_handler_count + len(STAGE_HANDLERS)
            ),
            "runtime_marker_valid": True,
            "restart_output_sha256": restart["output_sha256"],
        },
        "invocations": public_invocations,
        "launcher_process_count": expected_process_count,
        "unique_launcher_process_count": len(process_ids),
        "fresh_launcher_process_per_subhook": True,
        "pid_reuse_after_process_exit_allowed": True,
        "live_codex_home_opened": False,
        "live_codex_config_written": False,
        "live_plugin_slot_written": False,
        "plugin_enabled": False,
        "installer_helper_invoked": False,
        "task_or_goal_binding_mutated": False,
        "tunnel_invoked": False,
        "source_mutated": False,
        "git_mutated": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "raw_hook_payload_stored": False,
        "private_reasoning_stored": False,
    }
    receipt["receipt_sha256"] = _sha256_bytes(_json_bytes(receipt))
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-plugin-root", type=Path, required=True)
    parser.add_argument("--isolated-root", type=Path, required=True)
    parser.add_argument("--dependency-site-packages", type=Path)
    parser.add_argument("--receipt-path", type=Path)
    parser.add_argument("--cleanup-isolated-root", action="store_true")
    return parser


def _safe_cleanup(root: Path) -> None:
    resolved = root.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    marker = resolved / ".row210-isolated-root.json"
    try:
        resolved.relative_to(temporary_root)
    except ValueError as exc:
        raise IsolatedRuntimeVerificationError("CLEANUP_ROOT_OUTSIDE_SYSTEM_TEMP") from exc
    if (
        not resolved.name.startswith("evidence-lane-row210-")
        or not marker.is_file()
        or resolved == temporary_root
    ):
        raise IsolatedRuntimeVerificationError("CLEANUP_ROOT_IDENTITY_INVALID")
    shutil.rmtree(resolved)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    isolated_root = args.isolated_root.expanduser().resolve()
    receipt_path = (
        args.receipt_path.expanduser().resolve() if args.receipt_path else None
    )
    try:
        if receipt_path is not None:
            try:
                receipt_path.relative_to(isolated_root)
            except ValueError:
                pass
            else:
                if args.cleanup_isolated_root:
                    raise IsolatedRuntimeVerificationError(
                        "RECEIPT_PATH_MUST_SURVIVE_CLEANUP"
                    )
        receipt = verify_isolated_installed_runtime(
            source_plugin_root=args.source_plugin_root,
            isolated_root=isolated_root,
            dependency_site_packages=args.dependency_site_packages,
        )
        if receipt_path is not None:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_bytes(_json_bytes(receipt) + b"\n")
        if args.cleanup_isolated_root:
            _safe_cleanup(isolated_root)
            receipt["isolated_projection_retained"] = False
            receipt["isolated_projection_cleanup"] = "PASS"
            receipt.pop("receipt_sha256", None)
            receipt["receipt_sha256"] = _sha256_bytes(_json_bytes(receipt))
            if receipt_path is not None:
                receipt_path.write_bytes(_json_bytes(receipt) + b"\n")
    except (IsolatedRuntimeVerificationError, OSError, ValueError) as exc:
        failure = {
            "schema": RECEIPT_SCHEMA,
            "status": "FAIL_CLOSED",
            "code": str(exc),
            "live_codex_config_written": False,
            "live_plugin_slot_written": False,
            "plugin_enabled": False,
            "installer_helper_invoked": False,
            "task_or_goal_binding_mutated": False,
            "tunnel_invoked": False,
            "candidate_created": False,
            "hil_inferred": False,
            "pointer_moved": False,
            "raw_hook_payload_stored": False,
            "private_reasoning_stored": False,
        }
        failure["receipt_sha256"] = _sha256_bytes(_json_bytes(failure))
        print(_json_bytes(failure).decode("utf-8"))
        return 2
    print(_json_bytes(receipt).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
