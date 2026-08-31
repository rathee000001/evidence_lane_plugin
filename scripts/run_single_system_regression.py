#!/usr/bin/env python3
"""Run the one authorized full repository regression and preserve exact evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "evidence-lane.single-system-regression-receipt.v1"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git(root: Path, arguments: list[str], *, index_file: Path | None = None) -> str:
    environment = dict(os.environ)
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file)
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout.strip()


def _junit_counts(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        "suite_count": len(suites),
        "tests": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "failures": sum(int(suite.attrib.get("failures", 0)) for suite in suites),
        "errors": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
        "duration_seconds": round(
            sum(float(suite.attrib.get("time", 0.0)) for suite in suites), 3
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--index-file", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument(
        "--precondition",
        type=Path,
        action="append",
        default=[],
        help="Passing dedicated audit receipt required before the full launch.",
    )
    arguments = parser.parse_args()

    repository = arguments.repository.resolve()
    python = arguments.python.resolve()
    index_file = arguments.index_file.resolve()
    evidence = arguments.evidence_root.resolve()
    state_path = evidence / "full-regression-run-state.v1.json"
    receipt_path = evidence / "full-regression-receipt.v1.json"
    inventory_path = evidence / "selector-inventory.txt"
    inventory_error_path = evidence / "selector-inventory.stderr.txt"
    log_path = evidence / "full-regression.log"
    junit_path = evidence / "full-regression.junit.xml"

    if state_path.exists() or receipt_path.exists() or log_path.exists() or junit_path.exists():
        raise SystemExit("FULL_REGRESSION_EVIDENCE_ROOT_ALREADY_CONSUMED")
    if not repository.is_dir() or not (repository / ".git").exists():
        raise SystemExit("FULL_REGRESSION_REPOSITORY_INVALID")
    if not python.is_file() or not index_file.is_file():
        raise SystemExit("FULL_REGRESSION_RUNTIME_OR_INDEX_MISSING")
    preconditions: list[dict[str, Any]] = []
    for raw in arguments.precondition:
        path = raw.resolve()
        if not path.is_file():
            raise SystemExit(f"FULL_REGRESSION_PRECONDITION_MISSING:{path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("status") != "PASS":
            raise SystemExit(f"FULL_REGRESSION_PRECONDITION_NOT_PASSING:{path}")
        preconditions.append(
            {
                "path": str(path),
                "schema": value.get("schema"),
                "status": value.get("status"),
                "receipt_sha256": value.get("receipt_sha256"),
                "file_sha256": _sha256(path),
            }
        )
    if len(preconditions) < 5:
        raise SystemExit("FULL_REGRESSION_PRECONDITION_SET_INCOMPLETE")
    evidence.mkdir(parents=True, exist_ok=False)

    source_head = _git(repository, ["rev-parse", "HEAD"])
    alternate_tree = _git(repository, ["write-tree"], index_file=index_file)
    real_index_before = _sha256(repository / ".git" / "index")
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    collect_command = [
        str(python),
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    collected = subprocess.run(
        collect_command,
        cwd=repository,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    inventory_path.write_text(collected.stdout, encoding="utf-8", newline="\n")
    inventory_error_path.write_text(collected.stderr, encoding="utf-8", newline="\n")
    selectors = [
        line.strip()
        for line in collected.stdout.splitlines()
        if "::" in line and not line.startswith(("=", " "))
    ]
    if collected.returncode != 0 or not selectors:
        state = {
            "schema": "evidence-lane.single-system-regression-run-state.v1",
            "status": "COLLECTION_FAILED_FULL_RUN_NOT_LAUNCHED",
            "source_head": source_head,
            "alternate_tree": alternate_tree,
            "collector_exit_code": collected.returncode,
            "selector_count": len(selectors),
            "inventory_sha256": _sha256(inventory_path),
            "inventory_stderr_sha256": _sha256(inventory_error_path),
            "full_run_launched": False,
            "preconditions": preconditions,
        }
        _write_json(state_path, state)
        print(json.dumps(state, sort_keys=True), flush=True)
        return 2

    started_at = _now()
    state = {
        "schema": "evidence-lane.single-system-regression-run-state.v1",
        "status": "RUNNING",
        "source_head": source_head,
        "alternate_tree": alternate_tree,
        "started_at": started_at,
        "selector_count": len(selectors),
        "selector_inventory_sha256": _sha256(inventory_path),
        "full_run_launched": True,
        "full_run_count": 1,
        "preconditions": preconditions,
    }
    _write_json(state_path, state)

    command = [
        str(python),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit_path}",
    ]
    start = time.monotonic()
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        process = subprocess.Popen(
            command,
            cwd=repository,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        while process.poll() is None:
            elapsed = int(time.monotonic() - start)
            print(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "elapsed_seconds": elapsed,
                        "selector_count": len(selectors),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(30)
        exit_code = int(process.returncode or 0)
    completed_at = _now()
    if not junit_path.is_file():
        raise SystemExit("FULL_REGRESSION_JUNIT_MISSING")
    counts = _junit_counts(junit_path)
    real_index_after = _sha256(repository / ".git" / "index")
    status = (
        "PASS"
        if exit_code == 0 and counts["failures"] == 0 and counts["errors"] == 0
        else "FAIL_REQUIRES_TARGETED_CLOSURE"
    )
    core = {
        "schema": SCHEMA,
        "status": status,
        "source_head": source_head,
        "alternate_tree": alternate_tree,
        "started_at": started_at,
        "completed_at": completed_at,
        "wall_duration_seconds": round(time.monotonic() - start, 3),
        "selector_count": len(selectors),
        "selector_inventory_sha256": _sha256(inventory_path),
        "selector_inventory_stderr_sha256": _sha256(inventory_error_path),
        "command": command[1:],
        "exit_code": exit_code,
        "junit": counts,
        "junit_sha256": _sha256(junit_path),
        "full_log_sha256": _sha256(log_path),
        "full_log_bytes": log_path.stat().st_size,
        "full_run_count": 1,
        "preconditions": preconditions,
        "full_suite_rerun": False,
        "real_git_index_unchanged": real_index_before == real_index_after,
        "real_git_index_sha256": real_index_after,
        "git_ref_mutated": False,
        "project_or_pv_mutated": False,
        "candidate_or_hil_inferred": False,
    }
    receipt = {**core, "receipt_sha256": hashlib.sha256(_canonical_json_bytes(core)).hexdigest().upper()}
    _write_json(receipt_path, receipt)
    _write_json(
        state_path,
        {
            **state,
            "status": "COMPLETE",
            "completed_at": completed_at,
            "result": status,
            "receipt_sha256": receipt["receipt_sha256"],
        },
    )
    print(
        json.dumps(
            {
                "status": status,
                "selector_count": len(selectors),
                "junit": counts,
                "receipt_sha256": receipt["receipt_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
