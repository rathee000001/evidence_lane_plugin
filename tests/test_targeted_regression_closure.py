from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _junit(path: Path, cases: list[tuple[str, str]], *, failing: bool) -> None:
    rows = []
    for classname, name in cases:
        failure = '<failure message="failed">details</failure>' if failing else ""
        rows.append(
            f'<testcase classname="{classname}" name="{name}" time="0.01">'
            f"{failure}</testcase>"
        )
    failures = len(cases) if failing else 0
    path.write_text(
        f'<testsuites><testsuite tests="{len(cases)}" failures="{failures}" '
        f'errors="0" skipped="0" time="0.02">{"".join(rows)}</testsuite></testsuites>',
        encoding="utf-8",
    )


def test_targeted_closure_derives_counts_and_selector_coverage(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    full_receipt = tmp_path / "full.json"
    full_junit = tmp_path / "full.xml"
    targeted_junit = tmp_path / "targeted.xml"
    output = tmp_path / "closure.json"
    cases = [("tests.test_one", "test_a"), ("tests.test_two", "test_b")]
    full_receipt.write_text(
        json.dumps(
            {
                "status": "FAIL_REQUIRES_TARGETED_CLOSURE",
                "full_run_count": 1,
                "full_suite_rerun": False,
                "source_head": "a" * 40,
                "receipt_sha256": "B" * 64,
            }
        ),
        encoding="utf-8",
    )
    _junit(full_junit, cases, failing=True)
    _junit(targeted_junit, cases, failing=False)
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "seal_targeted_regression_closure.py"),
            "--repository",
            str(root),
            "--full-receipt",
            str(full_receipt),
            "--full-junit",
            str(full_junit),
            "--targeted-junit",
            str(targeted_junit),
            "--output",
            str(output),
            "--root-cause",
            "DOCS_LAST",
            "--root-cause",
            "EXPLICIT_BOOTSTRAP",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS_WITH_TARGETED_FAILURE_CLOSURE"
    assert receipt["root_causes"] == ["DOCS_LAST", "EXPLICIT_BOOTSTRAP"]
    assert receipt["full_regression"]["counts"]["failures"] == 2
    assert receipt["targeted_closure"]["counts"]["tests"] == 2
    assert receipt["targeted_closure"]["failed_selector_set_fully_covered"] is True
