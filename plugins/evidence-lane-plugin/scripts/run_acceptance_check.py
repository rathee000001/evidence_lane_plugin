"""Execute one exact Delta 063 acceptance check without mutating source."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.lanes import (
    CANONICAL_LANE_IDS,
    PRIMARY_CODE_LANES,
    SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256,
)
from evidence_lane_plugin.next_actions import HIL_CHOICES
from evidence_lane_plugin.pv_package import validate_pv_package


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _head() -> str:
    return _git("rev-parse", "HEAD")


def _poc_root() -> Path:
    override = os.environ.get("EVIDENCE_LANE_ACCEPTANCE_ROOT")
    if override:
        return Path(override).resolve()
    return (Path(f"{REPOSITORY_ROOT} POC") / f"evidence-lane-v1.2-delta063-{_head()[:8]}").resolve()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssertionError(f"Required evidence file is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Evidence file is not a JSON object: {path}")
    return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _pytest(*nodes: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *nodes],
        cwd=str(REPOSITORY_ROOT),
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _require(
        completed.returncode == 0,
        "Focused acceptance tests failed:\n" + completed.stdout + completed.stderr,
    )
    return {"returncode": 0, "output_tail": (completed.stdout + completed.stderr)[-2000:]}


def _topology(phase: str) -> dict[str, Any]:
    return _load(_poc_root() / phase / "topology_reconciliation.json")


def _lane(report: dict[str, Any], lane_id: str) -> dict[str, Any]:
    rows = [row for row in report.get("lanes", []) if row.get("lane_id") == lane_id]
    _require(len(rows) == 1, f"Expected one topology row for {lane_id}.")
    return rows[0]


def check_ac01() -> dict[str, Any]:
    return _pytest(
        "tests/test_lifecycle.py::test_approve_with_delta_records_an_integral_nonpromotable_candidate",
        "tests/test_lifecycle.py::test_pv_fuse_requires_exact_case_sensitive_approve",
        "tests/test_lifecycle.py::test_promotion_requires_matching_postseal_acceptance_receipt",
    )


def check_ac02() -> dict[str, Any]:
    authority_value = os.environ.get("EVIDENCE_LANE_MMD_AUTHORITY_PATH")
    _require(bool(authority_value), "The external MMD authority path was not bound.")
    authority_path = Path(str(authority_value)).resolve()
    _require(authority_path.is_file(), "The supplied MMD authority script is missing.")
    _require(
        sha256_file(authority_path) == SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256,
        "The supplied generate_lane_mmd.py authority fingerprint changed.",
    )
    receipt = _load(_poc_root() / "POC_RECEIPT.json")
    runtime = receipt.get("runtime") or {}
    _require(runtime.get("commit") == _head(), "PoC runtime commit differs from HEAD.")
    _require(runtime.get("worktree_clean") is True, "PoC runtime was not clean.")
    _require(
        runtime.get("runtime_matches_source_commit") is True,
        "PoC runtime was not the selected source commit.",
    )
    _require(
        runtime.get("sqlite_brain_builder_mmd_authority_sha256")
        == SQLITE_BRAIN_BUILDER_MMD_AUTHORITY_SHA256,
        "PoC authority fingerprint differs from the runtime contract.",
    )
    _require(
        runtime.get("both_code_lanes_match_exact_runtime") is True,
        "Both code lanes were not emitted by the exact runtime.",
    )
    return {"authority_sha256": sha256_file(authority_path), "runtime": runtime}


def _code_contract(lane_id: str) -> dict[str, Any]:
    phases = {}
    for phase in ("FORENSIC_INITIAL", "FORENSIC_REFRESH"):
        row = _lane(_topology(phase), lane_id)
        contract = row.get("logical_code_contract") or {}
        _require(contract.get("status") == "PASS", f"{lane_id} logical contract failed.")
        _require(
            all((contract.get(kind) or {}).get("status") == "PASS" for kind in ("mermaid", "dot")),
            f"{lane_id} MMD/DOT logical contract failed.",
        )
        _require(
            (row.get("rendering_parity") or {}).get("status") == "PASS",
            f"{lane_id} MMD/DOT identity parity failed.",
        )
        phases[phase] = {"claims_checked": row.get("claims_checked"), "status": row.get("status")}
    return phases


def check_ac03() -> dict[str, Any]:
    return {"github_code": _code_contract("github_code")}


def check_ac04() -> dict[str, Any]:
    return {"local_code": _code_contract("local_code")}


def check_ac05() -> dict[str, Any]:
    expected = [lane for lane in CANONICAL_LANE_IDS if lane not in PRIMARY_CODE_LANES]
    evidence: dict[str, Any] = {}
    for phase in ("FORENSIC_INITIAL", "FORENSIC_REFRESH"):
        report = _topology(phase)
        passed = []
        for lane_id in expected:
            row = _lane(report, lane_id)
            contract = row.get("schema_derived_contract") or {}
            _require(contract.get("status") == "PASS", f"{lane_id} schema contract failed.")
            _require(
                all((contract.get(kind) or {}).get("status") == "PASS" for kind in ("mermaid", "dot")),
                f"{lane_id} schema MMD/DOT contract failed.",
            )
            _require(
                (row.get("rendering_parity") or {}).get("status") == "PASS",
                f"{lane_id} schema MMD/DOT parity failed.",
            )
            passed.append(lane_id)
        _require(len(passed) == 16, "Expected exactly sixteen non-code lanes.")
        evidence[phase] = passed
    return evidence


def check_ac06() -> dict[str, Any]:
    return _pytest(
        "tests/test_universal_lanes.py::test_missing_topology_generator_fingerprint_forces_both_code_lanes"
    )


def check_ac07() -> dict[str, Any]:
    receipt = _load(_poc_root() / "POC_RECEIPT.json")
    policy = receipt.get("authority_policy") or {}
    _require(receipt.get("status") == "PASS", "The exact-Git PoC failed.")
    _require((receipt.get("source") or {}).get("commit") == _head(), "PoC commit mismatch.")
    _require(policy.get("real_git_lane") == "github_code", "Real lane is not github_code.")
    _require(len(policy.get("fixture_lanes") or []) == 17, "Expected seventeen fixture lanes.")
    for phase in ("initial", "refresh"):
        row = receipt.get(phase) or {}
        route = row.get("route_policy") or {}
        _require(row.get("valid") is True, f"PoC {phase} bundle is invalid.")
        _require(route.get("fixture_lane_count") == 17, f"PoC {phase} fixture count differs.")
        _require(not route.get("empty_lanes"), f"PoC {phase} has empty lanes.")
    return {"commit": _head(), "real_lane": "github_code", "fixture_lanes": 17}


def check_ac08() -> dict[str, Any]:
    result = {}
    for phase in ("FORENSIC_INITIAL", "FORENSIC_REFRESH"):
        root = _poc_root() / phase
        audit = _load(root / "forensic_audit.json")
        manifest = _load(root / "report_manifest.json")
        topology = _load(root / "topology_reconciliation.json")
        _require(audit.get("status") == "PASS", f"{phase} forensic audit failed.")
        _require(audit.get("lane_count") == 18, f"{phase} audit lane count differs.")
        _require(manifest.get("status") == "PASS", f"{phase} report package failed.")
        _require(manifest.get("lane_report_count") == 18, f"{phase} report count differs.")
        _require(topology.get("status") == "PASS", f"{phase} topology audit failed.")
        result[phase] = {"audit_sha256": audit.get("audit_sha256"), "manifest_sha256": manifest.get("manifest_sha256")}
    return result


def check_ac09() -> dict[str, Any]:
    source = _load(REPOSITORY_ROOT / "evidence" / "implementation_v39" / "TASK_LEDGER.json")
    external = _load(_poc_root() / "POST_COMMIT_TASK_LEDGER.json")
    tasks = source.get("tasks") or []
    _require(source.get("task_count") == 16 and len(tasks) == 16, "Task ledger is not 16 tasks.")
    _require([row.get("id") for row in tasks] == list(range(1, 17)), "Task IDs are not linear.")
    _require(all(row.get("current") is True for row in tasks), "A current task is not marked current.")
    _require(source.get("superseded_presented_as_current") is False, "A superseded task is current.")
    _require(source.get("current_delta_ids") == ["DELTA_063", "DELTA_064"], "Delta ledger differs.")
    _require(external.get("source_commit") == _head(), "Post-commit ledger commit mismatch.")
    _require(external.get("task_count") == 16, "Post-commit ledger count differs.")
    return {"task_count": 16, "deltas": source.get("current_delta_ids"), "external_status": external.get("status")}


def check_ac10() -> dict[str, Any]:
    receipt = _load(_poc_root() / "DOCX_QA_RECEIPT.json")
    docx = Path(str(receipt.get("docx_path") or ""))
    _require(receipt.get("status") == "PASS", "DOCX QA receipt failed.")
    _require(receipt.get("source_commit") == _head(), "DOCX commit mismatch.")
    _require(docx.is_file(), "The actual Word POC report is missing.")
    _require(sha256_file(docx) == receipt.get("docx_sha256"), "DOCX hash mismatch.")
    _require(zipfile.is_zipfile(docx), "The report is not a valid DOCX container.")
    with zipfile.ZipFile(docx) as archive:
        _require("word/document.xml" in archive.namelist(), "DOCX document.xml is missing.")
    pages = receipt.get("rendered_pages") or []
    _require(bool(pages), "No rendered DOCX pages were recorded.")
    for page in pages:
        path = Path(str(page.get("path") or ""))
        _require(path.is_file(), f"Rendered DOCX page is missing: {path}")
        _require(sha256_file(path) == page.get("sha256"), f"Rendered page hash mismatch: {path}")
    _require(receipt.get("visual_inspection") == "PASS", "DOCX was not visually inspected.")
    return {"docx": str(docx), "pages": len(pages), "visual_inspection": "PASS"}


def check_ac11() -> dict[str, Any]:
    receipt = _load(_poc_root() / "EXTERNAL_HIL_SURFACES.json")
    _require(receipt.get("status") == "PASS", "External surface verification failed.")
    _require(receipt.get("source_commit") == _head(), "External surface commit mismatch.")
    for surface in ("codex", "chatgpt", "vercel", "routing"):
        _require((receipt.get(surface) or {}).get("status") == "PASS", f"{surface} verification failed.")
    codex = receipt.get("codex") or {}
    chatgpt = receipt.get("chatgpt") or {}
    vercel = receipt.get("vercel") or {}
    routing = receipt.get("routing") or {}
    _require(codex.get("engine_commit") == _head(), "Codex installed commit mismatch.")
    _require(codex.get("version") == "1.2.0", "Codex plugin version is not 1.2.0.")
    _require(chatgpt.get("fresh_chat_boot") == "PASS", "Fresh ChatGPT boot did not pass.")
    _require(chatgpt.get("plugin_version") == "1.2.0", "ChatGPT plugin version is not 1.2.0.")
    _require(bool(chatgpt.get("models_tested")), "No fresh-chat model proof is recorded.")
    _require(vercel.get("production") is False, "The HIL deployment must remain a branch preview.")
    _require(str(vercel.get("deployment_url") or "").startswith("https://"), "Preview URL is invalid.")
    _require(routing.get("plain_visible_chat") == "chat_lineage", "Plain chat route differs.")
    _require(routing.get("sqlite_or_pv_brain") in {"sqlite_brain", "brain_loader"}, "Brain route differs.")
    _require(routing.get("project_archive") == "project_engulf", "Project route differs.")
    _require(routing.get("state_travel_auto_selected") is False, "State Travel was auto-selected.")
    return {"codex": codex, "chatgpt": chatgpt, "vercel": vercel, "routing": routing}


def check_ac12() -> dict[str, Any]:
    candidate = Path(os.environ.get("EVIDENCE_LANE_CANDIDATE_PATH", "")).resolve()
    project_root = Path(os.environ.get("EVIDENCE_LANE_PROJECT_ROOT", "")).resolve()
    expected_candidate = os.environ.get("EVIDENCE_LANE_EXPECTED_CANDIDATE_ID")
    expected_pv = os.environ.get("EVIDENCE_LANE_EXPECTED_ACCEPTED_PV")
    expected_generation = int(os.environ.get("EVIDENCE_LANE_EXPECTED_POINTER_GENERATION", "-1"))
    expected_commit = os.environ.get("EVIDENCE_LANE_EXPECTED_COMMIT")
    _require(candidate.is_dir(), "The immutable candidate path is missing.")
    validation = validate_pv_package(candidate)
    manifest = _load(candidate / "manifest.json")
    exit_slip = _load(candidate / "exit_slip.json")
    pointer = _load(project_root / "active_pointer.json")
    _require(validation.get("candidate_id") == expected_candidate, "Candidate ID mismatch.")
    _require((manifest.get("engine") or {}).get("commit") == expected_commit, "Engine commit mismatch.")
    _require((exit_slip.get("repository_exit") or {}).get("commit_sha") == expected_commit, "Exit commit mismatch.")
    _require(pointer.get("accepted_pv") == expected_pv, "Accepted PV moved during candidate build.")
    _require(pointer.get("generation") == expected_generation, "Pointer generation moved during candidate build.")
    next_action = exit_slip.get("next_action") or {}
    _require(next_action.get("state") == "PRESENT_SIX_WAY_HIL", "Exit did not stop at HIL.")
    _require(next_action.get("choices") == list(HIL_CHOICES), "Six-way HIL choices differ.")
    _require(next_action.get("stop_and_wait") is True, "HIL is not a stop-and-wait gate.")
    return {
        "candidate_id": expected_candidate,
        "manifest_sha256": validation.get("manifest_sha256"),
        "package_sha256": validation.get("package_sha256"),
        "engine_commit": expected_commit,
        "accepted_pv_retained": expected_pv,
        "pointer_generation_retained": expected_generation,
        "hil_choices": list(HIL_CHOICES),
    }


CHECKS = {
    "AC01": check_ac01,
    "AC02": check_ac02,
    "AC03": check_ac03,
    "AC04": check_ac04,
    "AC05": check_ac05,
    "AC06": check_ac06,
    "AC07": check_ac07,
    "AC08": check_ac08,
    "AC09": check_ac09,
    "AC10": check_ac10,
    "AC11": check_ac11,
    "AC12": check_ac12,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=sorted(CHECKS))
    args = parser.parse_args()
    evidence = CHECKS[args.check]()
    print(json.dumps({"check": args.check, "status": "PASS", "evidence": evidence}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
