"""Seal targeted closure against one preserved full-regression failure receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.targeted-regression-closure.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _junit(path: Path) -> tuple[dict[str, Any], list[str]]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {
        "suite_count": len(suites),
        "tests": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "failures": sum(int(suite.attrib.get("failures", 0)) for suite in suites),
        "errors": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
        "duration_seconds": round(
            sum(float(suite.attrib.get("time", 0.0)) for suite in suites), 3
        ),
    }
    selectors = [
        f"{case.attrib.get('classname')}::{case.attrib.get('name')}"
        for suite in suites
        for case in suite.findall("testcase")
        if case.find("failure") is not None or counts["failures"] == 0
    ]
    return counts, selectors


def _write(path: Path, value: dict[str, Any]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--full-receipt", type=Path, required=True)
    parser.add_argument("--full-junit", type=Path, required=True)
    parser.add_argument("--targeted-junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--purged-path", action="append", default=[])
    parser.add_argument("--root-cause", action="append", default=[])
    arguments = parser.parse_args()

    repository = arguments.repository.resolve()
    full_receipt_path = arguments.full_receipt.resolve()
    full_junit_path = arguments.full_junit.resolve()
    targeted_junit_path = arguments.targeted_junit.resolve()
    output = arguments.output.resolve()
    if output.exists():
        raise SystemExit("TARGETED_CLOSURE_OUTPUT_ALREADY_EXISTS")
    full = json.loads(full_receipt_path.read_text(encoding="utf-8"))
    full_counts, failed_selectors = _junit(full_junit_path)
    targeted_counts, targeted_selectors = _junit(targeted_junit_path)
    purged = sorted({str(value).replace("\\", "/") for value in arguments.purged_path})
    present = [path for path in purged if (repository / path).exists()]
    root_causes = sorted(
        {str(value).strip() for value in arguments.root_cause if str(value).strip()}
    )
    failed_selector_set = set(failed_selectors)
    targeted_selector_set = set(targeted_selectors)
    if not (
        full.get("status") == "FAIL_REQUIRES_TARGETED_CLOSURE"
        and full.get("full_run_count") == 1
        and full.get("full_suite_rerun") is False
        and full_counts["failures"] > 0
        and full_counts["errors"] == 0
        and len(failed_selectors) == full_counts["failures"]
        and targeted_counts["tests"] >= full_counts["failures"]
        and targeted_counts["failures"] == 0
        and targeted_counts["errors"] == 0
        and failed_selector_set.issubset(targeted_selector_set)
        and root_causes
        and not present
    ):
        raise SystemExit("TARGETED_CLOSURE_EVIDENCE_INVALID")
    core = {
        "schema": SCHEMA,
        "status": "PASS_WITH_TARGETED_FAILURE_CLOSURE",
        "source_head": full["source_head"],
        "full_regression": {
            "receipt_sha256": full["receipt_sha256"],
            "receipt_file_sha256": _sha256(full_receipt_path),
            "junit_sha256": _sha256(full_junit_path),
            "counts": full_counts,
            "failed_selectors": failed_selectors,
            "full_run_count": 1,
            "full_suite_rerun": False,
        },
        "root_causes": root_causes,
        "targeted_closure": {
            "junit_sha256": _sha256(targeted_junit_path),
            "counts": targeted_counts,
            "selectors": targeted_selectors,
            "purged_paths": purged,
            "purged_paths_present": present,
            "failed_selector_set_fully_covered": True,
        },
        "negative_proofs": {
            "full_suite_rerun": False,
            "git_index_mutated": False,
            "git_ref_mutated": False,
            "project_or_pv_mutated": False,
            "candidate_or_hil_inferred": False,
            "historical_evidence_tree_recreated": False,
        },
    }
    receipt = {
        **core,
        "receipt_sha256": hashlib.sha256(_canonical(core)).hexdigest().upper(),
    }
    _write(output, receipt)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "initial_failures": full_counts["failures"],
                "targeted_tests": targeted_counts["tests"],
                "purged_paths": len(purged),
                "receipt_sha256": receipt["receipt_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
