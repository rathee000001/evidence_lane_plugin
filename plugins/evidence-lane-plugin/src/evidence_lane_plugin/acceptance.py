"""Exact, bounded acceptance-command execution for PV Refresh evidence."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

from .git_adapter import calculate_worktree_sha256
from .redaction import contains_secret, redact_text

COMMAND_PREFIX = "cmd:"
MAX_CHECKS = 8
MAX_COMMAND_CHARS = 2000
MAX_OUTPUT_CHARS = 8000
MAX_ARGV_ITEMS = 64
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1800
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


def _run_one(
    repository: Path,
    declaration: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    value = declaration.strip()
    if not value.startswith(COMMAND_PREFIX):
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
) -> dict[str, Any]:
    """Run only exact ``cmd:`` checks and prove they did not change source."""

    repo = Path(repository).resolve()
    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
    before = calculate_worktree_sha256(repo)
    checks = [
        _run_one(repo, declaration, timeout_seconds=timeout)
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
    elif counts["PENDING_HUMAN_REVIEW"]:
        verdict = "PARTIAL_PROSE_CHECKS_NOT_EXECUTED"
    else:
        verdict = "ALL_EXECUTABLE_CHECKS_PASS"
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
    }
