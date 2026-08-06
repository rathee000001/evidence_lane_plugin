"""Exact, bounded acceptance-command execution for PV Refresh evidence."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any

from .git_adapter import calculate_worktree_sha256
from .hashing import sha256_file
from .redaction import contains_secret, redact_text

COMMAND_PREFIX = "cmd:"
COMMAND_MANIFEST_RELATIVE = Path("evidence/acceptance/commands.json")
COMMAND_MANIFEST_SCHEMA = "evidence-lane.acceptance-command-manifest.v1"
RUNTIME_PYTHON_TOKEN = "$RUNTIME_PYTHON"
MAX_CHECKS = 12
MAX_COMMAND_CHARS = 2000
MAX_OUTPUT_CHARS = 8000
MAX_ARGV_ITEMS = 64
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1800
POSTSEAL_PHASE = "POSTSEAL"
_SHELL_OPERATOR_TOKENS = frozenset(
    {"&", "&&", "|", "||", ";", "<", ">", ">>", "2>", "2>>"}
)
_SHELL_WRAPPER_SUFFIXES = frozenset({".bat", ".cmd", ".ps1", ".sh"})


def _strip_balanced_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_argv(repository: Path, command: str) -> tuple[list[str] | None, str | None]:
    if contains_secret(command):
        return None, "The cmd: declaration contains secret-shaped data."
    try:
        raw = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None, "The cmd: declaration has invalid quoting."
    argv = [_strip_balanced_quotes(item) for item in raw]
    if not argv or len(argv) > MAX_ARGV_ITEMS:
        return None, "The cmd: declaration has no argv or exceeds the argv bound."
    if any(item in _SHELL_OPERATOR_TOKENS for item in argv):
        return None, "Shell operators are forbidden; declare each check separately."

    requested = Path(argv[0])
    if requested.is_absolute():
        resolved = requested.resolve()
    elif requested.parent != Path("."):
        resolved = (repository / requested).resolve()
    else:
        found = shutil.which(argv[0])
        if not found:
            return (
                None,
                "The declared executable is not available on the governed PATH.",
            )
        resolved = Path(found).resolve()
    if not resolved.is_file():
        return None, "The declared executable does not resolve to a file."
    if resolved.suffix.lower() in _SHELL_WRAPPER_SUFFIXES:
        return None, "Shell and batch wrappers are forbidden acceptance executors."
    argv[0] = str(resolved)
    return argv, None


def _parse_manifest_argv(
    repository: Path,
    raw_argv: Any,
) -> tuple[list[str] | None, str | None]:
    if not isinstance(raw_argv, list) or not raw_argv:
        return None, "The acceptance manifest entry has no argv list."
    if len(raw_argv) > MAX_ARGV_ITEMS or not all(
        isinstance(item, str) and item for item in raw_argv
    ):
        return None, "The acceptance manifest argv is invalid or exceeds its bound."
    declared = list(raw_argv)
    if declared[0] == RUNTIME_PYTHON_TOKEN:
        declared[0] = sys.executable
    command = subprocess.list2cmdline(declared)
    if len(command) > MAX_COMMAND_CHARS or contains_secret(command):
        return None, "The acceptance manifest command is too long or secret-shaped."
    if any(item in _SHELL_OPERATOR_TOKENS for item in declared):
        return None, "Shell operators are forbidden in acceptance manifest argv."

    requested = Path(declared[0])
    if requested.is_absolute():
        resolved = requested.resolve()
    elif requested.parent != Path("."):
        resolved = (repository / requested).resolve()
    else:
        found = shutil.which(declared[0])
        if not found:
            return None, "The manifest executable is not available on the governed PATH."
        resolved = Path(found).resolve()
    if not resolved.is_file():
        return None, "The manifest executable does not resolve to a file."
    if resolved.suffix.lower() in _SHELL_WRAPPER_SUFFIXES:
        return None, "Shell and batch wrappers are forbidden acceptance executors."
    declared[0] = str(resolved)
    return declared, None


def _command_manifest(repository: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = (repository / COMMAND_MANIFEST_RELATIVE).resolve()
    if not path.is_file():
        return {}, {
            "path": str(COMMAND_MANIFEST_RELATIVE).replace("\\", "/"),
            "status": "NOT_PRESENT",
            "sha256": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, {
            "path": str(COMMAND_MANIFEST_RELATIVE).replace("\\", "/"),
            "status": "INVALID_JSON",
            "sha256": sha256_file(path),
        }
    commands = payload.get("commands") if isinstance(payload, dict) else None
    valid = (
        payload.get("schema") == COMMAND_MANIFEST_SCHEMA
        and isinstance(commands, dict)
        and len(commands) <= MAX_CHECKS
    )
    resolved_commands: dict[str, Any] = {}
    if valid and isinstance(commands, dict):
        resolved_commands = {str(key): value for key, value in commands.items()}
    return resolved_commands, {
        "path": str(COMMAND_MANIFEST_RELATIVE).replace("\\", "/"),
        "status": "PASS" if valid else "INVALID_SCHEMA",
        "sha256": sha256_file(path),
        "entry_count": len(commands) if isinstance(commands, dict) else 0,
    }


def declarations_for_phase(
    repository: str | Path,
    declarations: list[str],
    *,
    phase: str,
) -> list[str]:
    """Return only exact manifest-bound declarations for one execution phase."""

    manifest, _identity = _command_manifest(Path(repository).resolve())
    requested = phase.upper()
    return [
        declaration
        for declaration in declarations[:MAX_CHECKS]
        if isinstance(manifest.get(declaration.strip()), dict)
        and str(
            manifest[declaration.strip()].get("phase") or "PREBUILD"
        ).upper()
        == requested
    ]


def _run_one(
    repository: Path,
    declaration: str,
    *,
    timeout_seconds: int,
    manifest_entry: Any = None,
    phase: str = "PREBUILD",
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    value = declaration.strip()
    declared_via = "task_cmd"
    command: str
    if value.startswith(COMMAND_PREFIX):
        command = value[len(COMMAND_PREFIX) :].strip()
        if not command or len(command) > MAX_COMMAND_CHARS:
            return {
                "declaration": value,
                "command": None,
                "status": "BLOCKED_INVALID_COMMAND",
                "returncode": None,
                "duration_seconds": 0.0,
                "output_tail": "",
                "output_truncated": False,
                "reason": "The cmd: declaration is empty or exceeds the bounded length.",
            }
        argv, invalid_reason = _parse_argv(repository, command)
    elif isinstance(manifest_entry, dict):
        declared_via = "repository_manifest_exact_match"
        declared_phase = str(manifest_entry.get("phase") or "PREBUILD").upper()
        if declared_phase == POSTSEAL_PHASE and phase != POSTSEAL_PHASE:
            return {
                "declaration": value,
                "command": subprocess.list2cmdline(manifest_entry.get("argv", [])),
                "declared_via": declared_via,
                "phase": declared_phase,
                "status": "PENDING_POSTSEAL",
                "returncode": None,
                "duration_seconds": 0.0,
                "output_tail": "",
                "output_truncated": False,
                "reason": (
                    "This executable check requires the immutable candidate path and "
                    "therefore runs immediately after the candidate is sealed."
                ),
            }
        argv, invalid_reason = _parse_manifest_argv(
            repository,
            manifest_entry.get("argv"),
        )
        command = (
            subprocess.list2cmdline(manifest_entry.get("argv", []))
            if isinstance(manifest_entry.get("argv"), list)
            else ""
        )
        entry_timeout = manifest_entry.get("timeout_seconds", timeout_seconds)
        if isinstance(entry_timeout, int):
            timeout_seconds = max(1, min(entry_timeout, MAX_TIMEOUT_SECONDS))
    else:
        return {
            "declaration": value,
            "command": None,
            "status": "PENDING_HUMAN_REVIEW",
            "returncode": None,
            "duration_seconds": 0.0,
            "output_tail": "",
            "output_truncated": False,
            "reason": (
                "The check is prose, not an executable command. Prefix an exact "
                "authorized command with 'cmd:'; the plugin never guesses a runner."
            ),
        }
    if argv is None:
        return {
            "declaration": redact_text(value),
            "command": None,
            "status": "BLOCKED_INVALID_COMMAND",
            "returncode": None,
            "duration_seconds": 0.0,
            "output_tail": "",
            "output_truncated": False,
            "reason": invalid_reason,
        }
    started = time.monotonic()
    try:
        safe_environment = os.environ.copy()
        if environment:
            safe_environment.update(
                {str(key): str(item) for key, item in environment.items()}
            )
        completed = subprocess.run(  # nosec B603
            argv,
            cwd=str(repository),
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=safe_environment,
            close_fds=True,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        status = "PASS" if completed.returncode == 0 else "FAIL"
        returncode: int | None = completed.returncode
        reason = None
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + (
            (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        )
        status = "TIMEOUT"
        returncode = None
        reason = f"Command exceeded {timeout_seconds} seconds."
    except OSError as exc:
        output = ""
        status = "ERROR"
        returncode = None
        reason = f"Command execution failed with {type(exc).__name__}."
    safe_output = redact_text(output[-MAX_OUTPUT_CHARS:])
    return {
        "declaration": value,
        "command": redact_text(command),
        "declared_via": declared_via,
        "resolved_executable": argv[0],
        "status": status,
        "returncode": returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_tail": safe_output,
        "output_truncated": len(output) > MAX_OUTPUT_CHARS,
        "reason": reason,
    }


def run_acceptance_checks(
    repository: str | Path,
    declarations: list[str],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    phase: str = "PREBUILD",
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run only exact ``cmd:`` checks and prove they did not change source."""

    repo = Path(repository).resolve()
    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
    manifest, manifest_identity = _command_manifest(repo)
    before = calculate_worktree_sha256(repo)
    checks = [
        _run_one(
            repo,
            declaration,
            timeout_seconds=timeout,
            manifest_entry=manifest.get(declaration.strip()),
            phase=phase.upper(),
            environment=environment,
        )
        for declaration in declarations[:MAX_CHECKS]
        if declaration.strip()
    ]
    after = calculate_worktree_sha256(repo)
    source_unchanged = before == after
    counts = {
        state: sum(1 for row in checks if row["status"] == state)
        for state in (
            "PASS",
            "FAIL",
            "TIMEOUT",
            "ERROR",
            "PENDING_HUMAN_REVIEW",
            "BLOCKED_INVALID_COMMAND",
            "PENDING_POSTSEAL",
        )
    }
    if not checks:
        verdict = "NONE_DECLARED"
    elif not source_unchanged:
        verdict = "SOURCE_MUTATED_BY_CHECKS"
    elif counts["FAIL"] or counts["TIMEOUT"] or counts["ERROR"]:
        verdict = "FAILING_CHECKS_PRESENT"
    elif counts["BLOCKED_INVALID_COMMAND"]:
        verdict = "INVALID_CHECK_DECLARATION"
    elif counts["PENDING_POSTSEAL"]:
        verdict = "POSTSEAL_CHECKS_PENDING"
    elif counts["PENDING_HUMAN_REVIEW"]:
        verdict = "PARTIAL_PROSE_CHECKS_NOT_EXECUTED"
    else:
        verdict = "ALL_EXECUTABLE_CHECKS_PASS"
    prebuild_failure_count = sum(
        counts[state]
        for state in (
            "FAIL",
            "TIMEOUT",
            "ERROR",
            "PENDING_HUMAN_REVIEW",
            "BLOCKED_INVALID_COMMAND",
        )
    )
    prebuild_pass = (
        bool(checks)
        and source_unchanged
        and counts["PASS"] > 0
        and prebuild_failure_count == 0
        and counts["PASS"] + counts["PENDING_POSTSEAL"] == len(checks)
    )
    prebuild_verdict = verdict
    if prebuild_pass and counts["PENDING_POSTSEAL"]:
        prebuild_verdict = "ALL_PREBUILD_EXECUTABLE_CHECKS_PASS_POSTSEAL_PENDING"
    elif prebuild_pass:
        prebuild_verdict = "ALL_EXECUTABLE_CHECKS_PASS"
    return {
        "status": "PASS" if verdict == "ALL_EXECUTABLE_CHECKS_PASS" else "PARTIAL",
        "verdict": verdict,
        "declared": len(declarations),
        "bounded_to": MAX_CHECKS,
        "executed": sum(1 for row in checks if row["command"]),
        "counts": counts,
        "checks": checks,
        "source_worktree_sha256_before": before,
        "source_worktree_sha256_after": after,
        "source_unchanged": source_unchanged,
        "commands_inferred": False,
        "command_manifest": manifest_identity,
        "phase": phase.upper(),
        "prebuild_status": "PASS" if prebuild_pass else "PARTIAL",
        "prebuild_verdict": prebuild_verdict,
        "prebuild_executed": counts["PASS"],
        "postseal_pending": counts["PENDING_POSTSEAL"],
    }
