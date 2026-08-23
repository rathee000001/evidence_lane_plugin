"""Run one Evidence Lane hook through the sealed derived Python runtime.

The Windows host reaches this module through ``invoke_hook.ps1`` so it never
depends on the caller's ``PATH`` selection for Python.  Other hosts may start
the module with ``python3``; this module immediately re-executes itself through
the content-addressed runtime before importing or running a hook handler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

HOOK_LAUNCH_DIAGNOSTIC_SCHEMA = (
    "evidence-lane.codex-hook-launch-diagnostic.v1"
)

_EVENT_HANDLERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "SessionStart": ("session_start.py", ()),
    "SubagentStart": ("subagent_start.py", ()),
    "UserPromptSubmit": ("prompt_submit.py", ()),
    "PreToolUse": ("pre_tool_use.py", ()),
    "PermissionRequest": ("permission_request.py", ()),
    "PostToolUse": ("post_tool_use.py", ()),
    "PreCompact": ("lifecycle_boundary.py", ("PreCompact",)),
    "PostCompact": ("lifecycle_boundary.py", ("PostCompact",)),
    "SubagentStop": ("subagent_stop.py", ()),
    "Stop": ("stop_response.py", ()),
    "SessionEnd": ("lifecycle_boundary.py", ("SessionEnd",)),
}


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest().upper()


def _failure(event_name: str, code: str, *, handler: str | None = None) -> int:
    body: dict[str, Any] = {
        "schema": HOOK_LAUNCH_DIAGNOSTIC_SCHEMA,
        "status": "FAIL_CLOSED",
        "event_name": event_name or "UNKNOWN",
        "handler": handler,
        "code": code,
        "process_window_mode": "HIDDEN_ON_WINDOWS",
        "interpreter_resolution": "SEALED_DERIVED_RUNTIME_ONLY",
        "plugin_root_resolution": "HANDLER_RELATIVE_TO_INVOKE_HOOK_MODULE",
        "host_control_fields_emitted": False,
        "source_mutation_authorized": False,
        "lifecycle_mutated": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "raw_payload_stored": False,
        "raw_secret_stored": False,
        "private_reasoning_stored": False,
    }
    body["diagnostic_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    reason = f"Evidence Lane {body['event_name']} hook failed closed: {code}."
    diagnostic = (
        "EVIDENCE_LANE_HOOK_LAUNCH_DIAGNOSTIC="
        + _json_bytes(body).decode("utf-8")
    )
    if body["event_name"] in {"SessionEnd", "Stop"}:
        # A launch failure at either terminal event must remain output-inert.
        # In particular, Stop may never block or request another model turn.
        result: dict[str, Any] = {}
    elif body["event_name"] == "PreToolUse":
        result = {
            "systemMessage": diagnostic,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }
    else:
        # Surface the bounded failure without controlling the host turn.  The
        # Evidence Lane action still failed closed, but a broken optional hook
        # must not make Codex itself unusable.
        result = {
            "systemMessage": diagnostic,
        }
    print(_json_bytes(result).decode("utf-8"))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--handler", required=True)
    return parser


def _runtime_contract(plugin_root: Path):
    scripts = plugin_root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from runtime_contract import (  # type: ignore[import-not-found]
        marker_is_valid,
        runtime_environment,
        runtime_marker,
    )

    return marker_is_valid, runtime_environment, runtime_marker


def _reexec_sealed_runtime(plugin_root: Path, argv: list[str]) -> None:
    marker_is_valid, runtime_environment, runtime_marker = _runtime_contract(
        plugin_root
    )
    environment = runtime_environment(plugin_root)
    marker = runtime_marker(plugin_root)
    python = (
        environment / "Scripts" / "python.exe"
        if os.name == "nt"
        else environment / "bin" / "python"
    ).resolve()
    if not python.is_file() or not marker_is_valid(plugin_root, marker):
        raise RuntimeError("SEALED_RUNTIME_UNAVAILABLE_OR_INVALID")
    bound_runtime_root = os.environ.get(
        "EVIDENCE_LANE_HOOK_BOUND_RUNTIME_ROOT", ""
    ).strip()
    if bound_runtime_root and marker.parent.resolve() != Path(
        bound_runtime_root
    ).resolve():
        raise RuntimeError("SEALED_RUNTIME_INSTALL_BINDING_MISMATCH")
    if Path(sys.executable).resolve() != python:
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *argv])
    if Path(sys.executable).resolve() != python:
        raise RuntimeError("SEALED_RUNTIME_INTERPRETER_MISMATCH")


def _execute_isolated_handler(
    event_name: str,
    handler: Path,
    handler_args: tuple[str, ...],
    raw_payload: str,
) -> dict[str, Any]:
    hooks_root = Path(__file__).resolve().parent
    if str(hooks_root) not in sys.path:
        sys.path.insert(0, str(hooks_root))
    from event_isolation import (  # type: ignore[import-not-found]
        execute_isolated_hook,
    )

    return execute_isolated_hook(event_name, handler, handler_args, raw_payload)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(raw_argv)
    except SystemExit:
        return _failure("UNKNOWN", "HOOK_LAUNCH_ARGUMENTS_INVALID")

    event_name = str(args.event)
    handler_name = str(args.handler)
    expected = _EVENT_HANDLERS.get(event_name)
    if expected is None:
        return _failure(event_name, "HOOK_EVENT_UNSUPPORTED", handler=handler_name)
    if handler_name != expected[0]:
        return _failure(
            event_name,
            "HOOK_EVENT_HANDLER_MAPPING_MISMATCH",
            handler=handler_name,
        )

    plugin_root = Path(__file__).resolve().parents[1]
    handler = (Path(__file__).resolve().parent / handler_name).resolve()
    try:
        handler.relative_to((plugin_root / "hooks").resolve())
    except ValueError:
        return _failure(
            event_name,
            "HOOK_HANDLER_OUTSIDE_PLUGIN_ROOT",
            handler=handler_name,
        )
    if not handler.is_file():
        return _failure(event_name, "HOOK_HANDLER_MISSING", handler=handler_name)

    try:
        _reexec_sealed_runtime(plugin_root, raw_argv)
    except (OSError, RuntimeError, ValueError):
        return _failure(
            event_name,
            "SEALED_RUNTIME_INTERPRETER_UNAVAILABLE",
            handler=handler_name,
        )

    os.environ["EVIDENCE_LANE_HOOK_PLUGIN_ROOT_SHA256"] = _path_sha256(
        plugin_root
    )
    os.environ["EVIDENCE_LANE_HOOK_INTERPRETER_SHA256"] = _path_sha256(
        Path(sys.executable).resolve()
    )
    try:
        raw_payload = sys.stdin.read()
        output = _execute_isolated_handler(
            event_name,
            handler,
            expected[1],
            raw_payload,
        )
    except RuntimeError as exc:
        code = str(exc)
        return _failure(
            event_name,
            code if code.startswith("HOOK_") else "HOOK_EVENT_ISOLATION_UNAVAILABLE",
            handler=handler_name,
        )
    except Exception:  # noqa: BLE001 - diagnostic must remain secret-safe
        return _failure(
            event_name,
            "HOOK_EVENT_ISOLATION_UNHANDLED_EXCEPTION",
            handler=handler_name,
        )
    print(_json_bytes(output).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
