"""Read one Codex ``hooks/list`` inventory without changing host state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.installed_hook_receipts import (
    InstalledHookReceiptError,
    build_installed_hook_diagnostic_receipt,
    validate_installed_hook_inventory,
)

PROBE_SCHEMA = "evidence-lane.codex-installed-hook-probe.v1"


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _failure(
    code: str,
    *,
    stderr_lines: list[str] | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stderr_text = "\n".join(stderr_lines or [])
    body: dict[str, Any] = {
        "schema": PROBE_SCHEMA,
        "status": "FAIL_CLOSED",
        "code": code,
        "read_only": True,
        "supported_codex_api": "hooks/list",
        "windows_process_window_mode": "CREATE_NO_WINDOW",
        "config_written": False,
        "hook_trust_written": False,
        "plugin_installed_or_removed": False,
        "raw_stderr_included": False,
        "installed_hook_diagnostic": diagnostic,
        "stderr_sha256": hashlib.sha256(stderr_text.encode("utf-8"))
        .hexdigest()
        .upper(),
    }
    body["probe_receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    return body


def query_hooks_list(
    *,
    executable: Path,
    codex_home: Path,
    workspace: Path,
    timeout_seconds: float = 20.0,
) -> tuple[dict[str, Any], list[str]]:
    """Return the raw readback and redaction-safe stderr collection."""

    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("CODEX_EXECUTABLE_ABSOLUTE_FILE_REQUIRED")
    if not codex_home.is_absolute() or not codex_home.is_dir():
        raise ValueError("CODEX_HOME_ABSOLUTE_DIRECTORY_REQUIRED")
    if not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError("HOOK_WORKSPACE_ABSOLUTE_DIRECTORY_REQUIRED")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    process = subprocess.Popen(
        [str(executable), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=environment,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("CODEX_APP_SERVER_STDIO_UNAVAILABLE")

    responses: queue.Queue[dict[str, Any]] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                responses.put(value)

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line.rstrip())

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(value: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(_json_bytes(value).decode("utf-8") + "\n")
        process.stdin.flush()

    def wait_for(request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                value = responses.get(timeout=min(0.5, deadline - time.monotonic()))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if value.get("id") != request_id:
                continue
            if value.get("error"):
                raise RuntimeError("CODEX_APP_SERVER_REQUEST_FAILED")
            return value
        raise RuntimeError("CODEX_APP_SERVER_RESPONSE_TIMEOUT")

    try:
        send(
            {
                "method": "initialize",
                "id": 1740,
                "params": {
                    "clientInfo": {
                        "name": "evidence_lane_hook_probe",
                        "title": "Evidence Lane Hook Probe",
                        "version": "1",
                    }
                },
            }
        )
        wait_for(1740)
        send({"method": "initialized", "params": {}})
        send(
            {
                "method": "hooks/list",
                "id": 1741,
                "params": {"cwds": [str(workspace)]},
            }
        )
        return wait_for(1741), stderr_lines
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-executable", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--plugin-selector", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    stderr_lines: list[str] = []
    reply: dict[str, Any] | None = None
    diagnostic: dict[str, Any] | None = None
    try:
        reply, stderr_lines = query_hooks_list(
            executable=args.codex_executable.resolve(),
            codex_home=args.codex_home.resolve(),
            workspace=args.workspace.resolve(),
        )
        diagnostic = build_installed_hook_diagnostic_receipt(
            reply,
            plugin_selector=str(args.plugin_selector),
            workspace=args.workspace.resolve(),
        )
        inventory = validate_installed_hook_inventory(
            reply,
            plugin_selector=str(args.plugin_selector),
            workspace=args.workspace.resolve(),
        )
        result = {
            "schema": PROBE_SCHEMA,
            "status": "PASS",
            "read_only": True,
            "supported_codex_api": "hooks/list",
            "windows_process_window_mode": "CREATE_NO_WINDOW",
            "inventory": inventory,
            "installed_hook_diagnostic": diagnostic,
            "config_written": False,
            "hook_trust_written": False,
            "plugin_installed_or_removed": False,
            "raw_stderr_included": False,
            "stderr_sha256": hashlib.sha256("\n".join(stderr_lines).encode("utf-8"))
            .hexdigest()
            .upper(),
        }
        result["probe_receipt_sha256"] = hashlib.sha256(
            _json_bytes(result)
        ).hexdigest().upper()
    except (InstalledHookReceiptError, OSError, RuntimeError, ValueError) as exc:
        if reply is not None and diagnostic is None:
            try:
                diagnostic = build_installed_hook_diagnostic_receipt(
                    reply,
                    plugin_selector=str(args.plugin_selector),
                    workspace=args.workspace.resolve(),
                )
            except InstalledHookReceiptError:
                diagnostic = None
        result = _failure(
            str(exc),
            stderr_lines=stderr_lines,
            diagnostic=diagnostic,
        )
    print(_json_bytes(result).decode("utf-8"))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
