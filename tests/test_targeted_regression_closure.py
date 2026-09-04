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


def _deferral_contract(path: Path, selectors: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.regression-deferral-contract.v1",
                "status": "ACTIVE",
                "scope": "PV13_EXECUTABLE_PLUGIN_AND_GITHUB_APP",
                "publication_authorized": False,
                "documentation_generation_authorized": False,
                "deferred_batch": "POST_PV13_NORMAL_EVI_PLAN_PAGE_BY_PAGE_PUBLICATION",
                "exact_selector_set_required": True,
                "deferred_failure_selectors": selectors,
                "deferred_surfaces": [
                    "GITHUB_DOCUMENTATION",
                    "GITHUB_PAGES",
                    "VERCEL_PUBLICATION",
                ],
                "non_deferred_failures_must_close": True,
                "mixed_or_unknown_failure_policy": "FAIL_CLOSED",
            }
        ),
        encoding="utf-8",
    )


def test_targeted_closure_keeps_publication_failures_explicitly_deferred(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    full_receipt = tmp_path / "full.json"
    full_junit = tmp_path / "full.xml"
    targeted_junit = tmp_path / "targeted.xml"
    contract = tmp_path / "deferral.json"
    output = tmp_path / "closure.json"
    executable = [
        ("tests.test_runtime", "test_tool_order"),
        ("tests.test_plan", "test_normalization"),
    ]
    publication = [
        ("tests.test_docs", "test_readme_current"),
        ("tests.test_pages", "test_public_assets_current"),
    ]
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
    _junit(full_junit, [*executable, *publication], failing=True)
    _junit(targeted_junit, executable, failing=False)
    _deferral_contract(
        contract,
        [f"{classname}::{name}" for classname, name in publication],
    )
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
            "--deferred-contract",
            str(contract),
            "--output",
            str(output),
            "--root-cause",
            "EXECUTABLE_DEFECTS_CLOSED_PUBLICATION_BATCH_DEFERRED",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "EXECUTABLE_SCOPE_VALIDATED_PUBLICATION_DEFERRED"
    assert receipt["publication_authorized"] is False
    assert receipt["failed_selector_set_fully_dispositioned"] is True
    assert receipt["targeted_closure"]["failed_selector_set_fully_covered"] is False
    assert receipt["targeted_closure"]["non_deferred_failure_set_fully_covered"] is True
    assert receipt["deferred_publication"]["status"] == "DEFERRED_NOT_PASSED"
    assert receipt["deferred_publication"]["selector_count"] == 2


def test_targeted_closure_rejects_deferral_selector_not_in_full_failures(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    full_receipt = tmp_path / "full.json"
    full_junit = tmp_path / "full.xml"
    targeted_junit = tmp_path / "targeted.xml"
    contract = tmp_path / "deferral.json"
    output = tmp_path / "closure.json"
    case = [("tests.test_runtime", "test_tool_order")]
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
    _junit(full_junit, case, failing=True)
    _junit(targeted_junit, case, failing=False)
    _deferral_contract(contract, ["tests.test_docs::test_not_a_full_failure"])
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
            "--deferred-contract",
            str(contract),
            "--output",
            str(output),
            "--root-cause",
            "INVALID_DEFERRAL_MUST_FAIL",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode != 0
    assert "TARGETED_CLOSURE_EVIDENCE_INVALID" in completed.stderr
    assert not output.exists()


def test_task35_publication_deferral_contract_is_exact_and_fail_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    contract = json.loads(
        (root / "contracts" / "task35-publication-deferral.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["publication_authorized"] is False
    assert contract["documentation_generation_authorized"] is False
    assert contract["exact_selector_set_required"] is True
    assert contract["non_deferred_failures_must_close"] is True
    assert contract["mixed_or_unknown_failure_policy"] == "FAIL_CLOSED"
    assert set(contract["deferred_failure_selectors"]) == {
        "tests.test_github_docs_generation::test_repository_markdown_matches_current_renderers",
        "tests.test_github_docs_generation::test_root_readme_requires_current_linked_docs_and_is_reviewed_after_commit",
        "tests.test_public_dummy_lane_packages::test_all_canonical_lane_dummy_packages_are_exact_and_downloadable",
        "tests.test_publication_redesign_handoff::test_publication_handoff_is_route_complete_and_deferred",
        "tests.test_site_operator_export::test_checked_in_site_projection_is_fresh",
    }
