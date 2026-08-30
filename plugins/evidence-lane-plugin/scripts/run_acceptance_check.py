"""Execute one exact Evidence Lane v1.5.0 acceptance check without mutating source."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.next_actions import PROJECT_HIL_DECISION_TOKENS
from evidence_lane_plugin.pv_package import validate_pv_package

EXPECTED_BRANCH = "agent/evi-v150-systemwide-release-hil-v1.5.0"
EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence" / "implementation_v41"
CORRECTION_EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence" / "implementation_v42"
RELEASE_EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence" / "implementation_v43"
RELEASE_RECEIPT = RELEASE_EVIDENCE_ROOT / "V140_RELEASE_IDENTITY_RECEIPT.json"
CURRENT_RELEASE_EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence" / "implementation_v44"
CURRENT_RELEASE_RECEIPT = (
    CURRENT_RELEASE_EVIDENCE_ROOT / "V140_POST_PV9_DELTA_BOUNDARY_RECEIPT.json"
)
CORRECTION_BASE_SOURCE_COMMIT = "6020563094154ff780705cbed85f453425884914"
CORRECTION_RECEIPT = (
    EVIDENCE_ROOT / "DELTA080A_V130_EXECUTABLE_GATE_VERSION_CORRECTION_RECEIPT.json"
)
SOURCE_DISPOSITION_RECEIPT = (
    CORRECTION_EVIDENCE_ROOT / "ALL_SOURCE_DISPOSITION_RECEIPT.json"
)
CURRENT_ACCEPTED_PV = "PV9"
CURRENT_POINTER_GENERATION = 9
CURRENT_ACCEPTED_MANIFEST_SHA256 = (
    "54A3FBEBE3DE904AFE694821E5D6ED03D0C271E1C5F319A8017E70DA52FE3C82"
)
CURRENT_ACCEPTED_PACKAGE_SHA256 = (
    "A42EF223B1F0FCB1A2FE0A4C1B4F4B463A48D9D2D917D2D34E05F39FF81682A6"
)
CURRENT_BASE_SOURCE_COMMIT = "1f16034fda58a46c313af826a9d1465fcf32db17"
CURRENT_BASE_SOURCE_TREE = "4b634e0fcf5643faa8216cf11fa6faaf90b7e0f3"
PRE_HIL_MAIN_COMMIT = "d919cbd0d73676c6e7c2a6b4189b614d1ec42144"
NEXT_PROPOSED_PV = "PV10"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load(path: Path) -> dict[str, Any]:
    _require(path.is_file(), f"Required evidence file is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), f"Evidence file is not an object: {path}")
    return payload


def _git(*arguments: str) -> str:
    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout.strip()


def _run(
    argv: list[str],
    *,
    timeout_seconds: int,
    cwd: Path = REPOSITORY_ROOT,
) -> dict[str, Any]:
    completed = subprocess.run(  # nosec B603
        argv,
        cwd=str(cwd),
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
    _require(completed.returncode == 0, "Command failed:\n" + output[-4000:])
    return {
        "argv": argv[1:],
        "executable": Path(argv[0]).name,
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes((completed.stdout or "").encode("utf-8")),
        "stderr_sha256": sha256_bytes((completed.stderr or "").encode("utf-8")),
        "output_tail": output[-1000:],
    }


def _pytest(*nodes: str, timeout_seconds: int = 1800) -> dict[str, Any]:
    return _run(
        [sys.executable, "-m", "pytest", "-q", *nodes],
        timeout_seconds=timeout_seconds,
    )


def _quality_python() -> str:
    configured = os.environ.get("EVIDENCE_LANE_QUALITY_PYTHON")
    suffix = Path("Scripts/python.exe" if os.name == "nt" else "bin/python")
    candidates = [
        Path(configured).resolve() if configured else None,
        (PLUGIN_ROOT / ".venv" / suffix).resolve(),
        (REPOSITORY_ROOT / ".venv" / suffix).resolve(),
        Path(sys.executable).resolve(),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return str(candidate)
    raise AssertionError("No governed Python runtime is available for Ruff/MyPy.")


def _sealed_receipt_path(path: Path) -> dict[str, Any]:
    payload = _load(path)
    declared = payload.get("receipt_sha256")
    _require(
        isinstance(declared, str) and len(declared) == 64,
        f"No seal: {path.name}",
    )
    canonical = dict(payload)
    canonical.pop("receipt_sha256", None)
    computed = sha256_bytes(canonical_json_bytes(canonical))
    _require(declared == computed, f"Stale receipt self-seal: {path.name}")
    return {
        "name": path.name,
        "receipt_sha256": computed,
        "file_sha256": sha256_file(path),
        "payload": payload,
    }


def _sealed_receipt(name: str) -> dict[str, Any]:
    return _sealed_receipt_path(EVIDENCE_ROOT / name)


def check_ac01() -> dict[str, Any]:
    return {"full_regression": _pytest()}


def check_ac02() -> dict[str, Any]:
    return {
        "ruff": _run(
            [_quality_python(), "-m", "ruff", "check", "."],
            timeout_seconds=600,
        )
    }


def check_ac03() -> dict[str, Any]:
    return {
        "mypy": _run(
            [_quality_python(), "-m", "mypy"],
            timeout_seconds=600,
        )
    }


def check_ac04() -> dict[str, Any]:
    pnpm = shutil.which("pnpm.cmd" if os.name == "nt" else "pnpm")
    _require(bool(pnpm), "pnpm is unavailable for the governed preview build.")
    return {
        "preview_build": _run(
            [
                str(pnpm),
                "--dir",
                "apps/evidence-lane-app",
                "build",
            ],
            timeout_seconds=900,
        )
    }


def check_ac05() -> dict[str, Any]:
    return {
        "version_consistency": _pytest(
            "tests/test_v140_version_consistency.py",
            timeout_seconds=180,
        )
    }


def check_ac06() -> dict[str, Any]:
    return {
        "gate_contract": _pytest(
            "tests/test_acceptance.py::test_prebuild_summary_passes_when_exact_prebuild_runs_before_postseal",
            "tests/test_operating_modes.py::test_selected_code_mode_binds_task_candidate_formula_and_lane_hil",
            timeout_seconds=300,
        )
    }


def check_ac07() -> dict[str, Any]:
    return {
        "receipt_seals": _pytest(
            "tests/test_v130_evidence_receipt_seals.py",
            timeout_seconds=180,
        )
    }


def check_ac08() -> dict[str, Any]:
    graph = _load(EVIDENCE_ROOT / "DELTA071_POLYGLOT_GRAPH_RECEIPT.json")
    history = _load(EVIDENCE_ROOT / "DELTA072_GIT_HISTORY_IMPACT_RECEIPT.json")
    _require(str(graph.get("status") or "").startswith("PASS"), "Graph receipt failed.")
    _require(
        str(history.get("status") or "").startswith("PASS"),
        "Git-history receipt failed.",
    )
    sealed = [
        _sealed_receipt(name)
        for name in (
            "DELTA073_SCHEMA_DERIVED_TOPOLOGY_RECEIPT.json",
            "DELTA074_DETERMINISTIC_RENDER_RECEIPT.json",
            "DELTA075_FOUR_FILE_EVERY_TABLE_FORENSIC_RECEIPT.json",
            "DELTA078_ALL_SOURCE_LANE_HISTORY_RECONCILIATION_RECEIPT.json",
        )
    ]
    topology = sealed[0]["payload"]
    reconciliation = sealed[-1]["payload"]
    _require(topology.get("all_pass") is True, "Eighteen-lane topology receipt failed.")
    _require(
        str(reconciliation.get("status") or "").startswith("PASS"),
        "All-source reconciliation receipt failed.",
    )
    return {
        "graph_status": graph["status"],
        "history_status": history["status"],
        "sealed_receipts": [row["receipt_sha256"] for row in sealed],
    }


def check_ac09() -> dict[str, Any]:
    pinned = _sealed_receipt("DELTA076A_PINNED_CI_MCP_GH_AW_RECEIPT.json")
    adapters = _sealed_receipt("DELTA076B_CI_ADAPTERS_RECEIPT.json")
    first = pinned["payload"]
    second = adapters["payload"]
    _require(
        (first.get("supplied_authority_validation") or {})
        .get("gh_aw_locked_workflows", {})
        .get("status")
        == "PASS",
        "Pinned GitHub workflow audit failed.",
    )
    _require(
        (second.get("ci_execution") or {}).get("aggregate_status") == "PASS",
        "Governed CI receipt failed.",
    )
    _require(
        (second.get("codeql") or {}).get("remote_execution_status")
        in {"RUN", "NOT_RUN_LOCAL"},
        "CodeQL receipt is neither RUN nor honest NOT_RUN_LOCAL.",
    )
    _require(
        (second.get("local_action_compatibility") or {}).get(
            "direct_entrypoint_execution"
        )
        == "PASS",
        "Local Action compatibility receipt failed.",
    )
    return {
        "pinned_receipt": pinned["receipt_sha256"],
        "adapter_receipt": adapters["receipt_sha256"],
        "codeql": (second.get("codeql") or {}).get("remote_execution_status"),
    }


def check_ac10() -> dict[str, Any]:
    tunnel = _sealed_receipt("DELTA079B_WINDOWS_TUNNEL_PERSISTENCE_RECEIPT.json")
    host = _sealed_receipt("DELTA079CD_HOST_STORAGE_ENV_MODE_CONTINUITY_RECEIPT.json")
    _require(tunnel["payload"].get("status") == "PASS", "Tunnel receipt failed.")
    _require(host["payload"].get("status") == "PASS", "Host receipt failed.")
    matrix = host["payload"].get("host_storage_matrix") or {}
    codex = matrix.get("codex_local_pc_laptop_or_stable_vm") or {}
    _require(
        codex.get("primary_runtime_authority") == "LOCAL_DURABLE_SQLITE",
        "Stable Codex primary runtime differs.",
    )
    return {
        "tunnel_receipt": tunnel["receipt_sha256"],
        "host_receipt": host["receipt_sha256"],
        "codex_runtime": codex,
    }


def check_ac11() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    status = _git("status", "--porcelain=v1")
    head = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    _require(branch == EXPECTED_BRANCH, "The governed study branch differs.")
    _require(not status, "The final study branch is not clean.")
    _require(ENGINE_VERSION == "1.5.0", "The engine is not v1.5.0.")
    release = _sealed_receipt_path(CURRENT_RELEASE_RECEIPT)
    safety = release["payload"].get("safety") or {}
    _require(safety.get("main_merged") is False, "The release claims a main merge.")
    _require(
        safety.get("production_deployed") is False,
        "The current pre-HIL contract claims a production deployment.",
    )
    _require(
        release["payload"].get("accepted_authority", {}).get("accepted_pv")
        == CURRENT_ACCEPTED_PV,
        "The current release boundary does not bind accepted PV9.",
    )
    _require(
        release["payload"].get("base_source_identity", {}).get("commit")
        == CURRENT_BASE_SOURCE_COMMIT,
        "The current release boundary does not bind the PV9 source commit.",
    )
    _git("merge-base", "--is-ancestor", CURRENT_BASE_SOURCE_COMMIT, head)
    return {
        "branch": branch,
        "commit": head,
        "tree": tree,
        "engine_version": ENGINE_VERSION,
        "release_receipt": release["receipt_sha256"],
    }


def _postseal_candidate_context() -> dict[str, Any]:
    candidate = Path(os.environ.get("EVIDENCE_LANE_CANDIDATE_PATH", "")).resolve()
    project_root = Path(os.environ.get("EVIDENCE_LANE_PROJECT_ROOT", "")).resolve()
    expected_candidate = os.environ.get("EVIDENCE_LANE_EXPECTED_CANDIDATE_ID")
    expected_pv = os.environ.get("EVIDENCE_LANE_EXPECTED_ACCEPTED_PV")
    expected_generation = int(
        os.environ.get("EVIDENCE_LANE_EXPECTED_POINTER_GENERATION", "-1")
    )
    expected_commit = os.environ.get("EVIDENCE_LANE_EXPECTED_COMMIT")
    _require(candidate.is_dir(), "The immutable candidate path is missing.")
    validation = validate_pv_package(candidate)
    manifest = _load(candidate / "manifest.json")
    project_identity = _load(candidate / "project_identity.json")
    exit_slip = _load(candidate / "exit_slip.json")
    pointer = _load(project_root / "active_pointer.json")
    release = _sealed_receipt_path(CURRENT_RELEASE_RECEIPT)
    source_commit = (project_identity.get("repository") or {}).get("commit_sha")
    engine_commit = (manifest.get("engine") or {}).get("commit")
    exit_commit = (exit_slip.get("repository_exit") or {}).get("commit_sha")
    _require(
        validation.get("candidate_id") == expected_candidate, "Candidate mismatch."
    )
    _require(validation.get("status") == "PASS", "Candidate validation failed.")
    _require(
        validation.get("proposed_pv") == NEXT_PROPOSED_PV,
        "The sealed candidate is not the expected PV10 successor.",
    )
    _require(
        str(expected_candidate).startswith(f"{NEXT_PROPOSED_PV}_CANDIDATE__"),
        "The candidate identity is not in the PV10 namespace.",
    )
    _require(
        expected_commit == _git("rev-parse", "HEAD"), "Live source commit mismatch."
    )
    _require(not _git("status", "--porcelain=v1"), "The post-seal source is not clean.")
    _require(
        _git("branch", "--show-current") == EXPECTED_BRANCH,
        "The governed source branch differs.",
    )
    _require(ENGINE_VERSION == "1.5.0", "The engine is not v1.5.0.")
    _require(
        (release["payload"].get("base_source_identity") or {}).get("commit")
        == CURRENT_BASE_SOURCE_COMMIT,
        "The current release base commit differs from accepted PV9 source.",
    )
    _git(
        "merge-base",
        "--is-ancestor",
        CURRENT_BASE_SOURCE_COMMIT,
        str(expected_commit),
    )
    _require(source_commit == expected_commit, "Candidate source commit mismatch.")
    _require(engine_commit == expected_commit, "Engine mismatch.")
    _require(
        exit_commit == expected_commit,
        "Exit commit mismatch.",
    )
    _require(
        pointer.get("accepted_pv") == expected_pv == CURRENT_ACCEPTED_PV,
        "The accepted PV9 pointer moved.",
    )
    _require(
        pointer.get("generation") == expected_generation == CURRENT_POINTER_GENERATION,
        "Pointer generation moved.",
    )
    _require(
        pointer.get("accepted_manifest_sha256") == CURRENT_ACCEPTED_MANIFEST_SHA256,
        "The accepted PV9 manifest pointer changed.",
    )
    accepted_authority = release["payload"].get("accepted_authority") or {}
    _require(
        accepted_authority
        == {
            "accepted_manifest_sha256": CURRENT_ACCEPTED_MANIFEST_SHA256,
            "accepted_package_sha256": CURRENT_ACCEPTED_PACKAGE_SHA256,
            "accepted_pv": CURRENT_ACCEPTED_PV,
            "generation": CURRENT_POINTER_GENERATION,
        },
        "The release boundary does not preserve exact PV9 authority.",
    )
    acceptance = exit_slip.get("acceptance_checks") or {}
    counts = acceptance.get("counts") or {}
    passing = int(counts.get("PASS") or 0)
    pending_postseal = int(counts.get("PENDING_POSTSEAL") or 0)
    declared = int(acceptance.get("declared") or 0)
    _require(acceptance.get("prebuild_status") == "PASS", "Prebuild gate failed.")
    _require(passing == 4, "The exact four prebuild checks did not pass.")
    _require(
        pending_postseal == 2,
        "The exact two immutable-candidate checks were not declared.",
    )
    _require(
        passing + pending_postseal == declared == 6,
        "The exact six-command acceptance accounting is incomplete.",
    )
    for state in ("FAIL", "TIMEOUT", "ERROR", "PENDING_HUMAN_REVIEW"):
        _require(
            int(counts.get(state) or 0) == 0,
            f"The candidate contains a non-passing acceptance state: {state}.",
        )
    ci_cd = (exit_slip.get("mode_execution") or {}).get("ci_cd") or {}
    _require(ci_cd.get("approve_gate") == "PASS", "Code-mode approve gate is open.")
    _require(ci_cd.get("executed") == passing, "Code-mode prebuild count differs.")
    _require(
        ci_cd.get("postseal_pending") == pending_postseal,
        "Code-mode postseal count differs.",
    )
    next_action = exit_slip.get("next_action") or {}
    _require(
        next_action.get("state") == "PRESENT_PROJECT_AUTHORITY_HIL",
        "HIL state differs.",
    )
    _require(
        next_action.get("choices") == list(PROJECT_HIL_DECISION_TOKENS),
        "HIL choices differ.",
    )
    _require(next_action.get("stop_and_wait") is True, "HIL is not stop-and-wait.")
    safety = release["payload"].get("safety") or {}
    _require(
        all(value is False for value in safety.values()),
        "The pre-HIL boundary claims a prohibited remote or pointer mutation.",
    )
    _require(_git("rev-parse", "main") == PRE_HIL_MAIN_COMMIT, "Local main moved.")
    _require(
        _git("rev-parse", "origin/main") == PRE_HIL_MAIN_COMMIT, "Remote main moved."
    )
    return {
        "candidate_id": expected_candidate,
        "manifest_sha256": validation.get("manifest_sha256"),
        "package_sha256": validation.get("package_sha256"),
        "base_accepted_commit": CURRENT_BASE_SOURCE_COMMIT,
        "release_source_commit": source_commit,
        "engine_commit": engine_commit,
        "exit_slip_commit": exit_commit,
        "commit_identity_parity": source_commit == engine_commit == exit_commit,
        "release_identity_receipt": release["receipt_sha256"],
        "accepted_pv_retained": expected_pv,
        "pointer_generation_retained": expected_generation,
        "prebuild_passed": passing,
        "postseal_pending": pending_postseal,
        "postseal_check": "PASS",
        "approve_gate": ci_cd["approve_gate"],
        "hil_choices": list(PROJECT_HIL_DECISION_TOKENS),
        "hil_state": next_action["state"],
        "hil_stop_and_wait": next_action["stop_and_wait"],
        "suggested_next_prompt": next_action.get("suggested_next_prompt"),
        "safety": safety,
    }


def check_ac12() -> dict[str, Any]:
    """Retain the historical AC12 entry as the current combined post-seal gate."""

    return _postseal_candidate_context()


def check_ac13() -> dict[str, Any]:
    """Prove the business-language Studio and release-facing website contract."""

    return {
        "business_and_site": _pytest(
            "tests/test_full_app_ui_conformance.py::test_evidence_ai_studio_is_a_business_guide_for_the_whole_plugin",
            "tests/test_full_app_ui_conformance.py::test_home_story_collapsed_delta_and_canonical_legal_footer_are_explicit",
            "tests/test_full_app_ui_conformance.py::test_connect_endpoint_cards_are_linked_readable_and_truthful",
            timeout_seconds=300,
        )
    }


def check_ac14() -> dict[str, Any]:
    """Prove full-plugin host metadata, skills, reads, and no-Meshy boundary."""

    return {
        "host_metadata_and_settings": _pytest(
            "tests/test_mcp_plugin.py::test_mcp_tool_inventory_and_annotations",
            "tests/test_mcp_plugin.py::test_v2_server_rejects_removed_chatgpt_exposure_profiles",
            "tests/test_mcp_plugin.py::test_modern_discovery_probe_receives_exact_legacy_fallback",
            "tests/test_mcp_plugin.py::test_plugin_manifest_has_evidence_lane_identity_only",
            "tests/test_mcp_plugin.py::test_real_stdio_transport_lists_tools_and_calls_doctor",
            "tests/test_full_app_ui_conformance.py::test_native_threejs_motion_remains_without_retired_3d_or_adobe_links",
            "tests/test_full_app_ui_conformance.py::test_public_plugin_metadata_and_third_party_rights_are_canonical",
            "tests/test_windows_tunnel_persistence.py",
            timeout_seconds=600,
        )
    }


def check_ac15() -> dict[str, Any]:
    """Run the complete regression from the exact final source."""

    return {"full_regression": _pytest()}


def check_ac16() -> dict[str, Any]:
    """Prove clean source identity and the governed publication boundary."""

    branch = _git("branch", "--show-current")
    status = _git("status", "--porcelain=v1")
    head = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    release = _sealed_receipt_path(CURRENT_RELEASE_RECEIPT)
    payload = release["payload"]
    safety = payload.get("safety") or {}
    publication = payload.get("publication_boundary") or {}
    _require(branch == EXPECTED_BRANCH, "The governed release branch differs.")
    _require(not status, "The exact release source is not clean.")
    _git("merge-base", "--is-ancestor", CURRENT_BASE_SOURCE_COMMIT, head)
    _require(ENGINE_VERSION == "1.5.0", "The engine is not v1.5.0.")
    _require(
        payload.get("status") == "BOUNDED_PRE_HIL_CONTRACT",
        "The post-PV9 release boundary status differs.",
    )
    _require(
        publication.get("existing_devpost_project") == "1348634/evidence_os"
        and publication.get("separate_devpost_publication_lane") is True
        and publication.get("main_merge_only_after_fresh_acceptance") is True,
        "The governed existing-Devpost publication correction differs.",
    )
    _require(
        all(value is False for value in safety.values()),
        "A prohibited pre-HIL lifecycle or publication action is claimed.",
    )
    _require(_git("rev-parse", "main") == PRE_HIL_MAIN_COMMIT, "Local main moved.")
    _require(
        _git("rev-parse", "origin/main") == PRE_HIL_MAIN_COMMIT, "Remote main moved."
    )
    release_surfaces = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            REPOSITORY_ROOT / "README.md",
            REPOSITORY_ROOT
            / "apps"
            / "evidence-lane-app"
            / "app"
            / "_data"
            / "business-guidance.ts",
            REPOSITORY_ROOT
            / "apps"
            / "evidence-lane-app"
            / "app"
            / "_data"
            / "current-execution-plan.ts",
        )
    )
    _require(
        "1348634/evidence_os" in release_surfaces,
        "The existing Devpost project is absent from release surfaces.",
    )
    _require(
        "Vercel" in release_surfaces and "Delta" in release_surfaces,
        "The systemwide propagation boundary is incomplete.",
    )
    return {
        "branch": branch,
        "commit": head,
        "tree": tree,
        "base_accepted_commit": CURRENT_BASE_SOURCE_COMMIT,
        "release_receipt": release["receipt_sha256"],
        "existing_devpost_project": publication["existing_devpost_project"],
        "main_retained": PRE_HIL_MAIN_COMMIT,
        "safety": safety,
    }


def check_ac17() -> dict[str, Any]:
    """Validate the exact immutable PV10 candidate and retained PV9 pointer."""

    evidence = _postseal_candidate_context()
    return {
        key: evidence[key]
        for key in (
            "candidate_id",
            "manifest_sha256",
            "package_sha256",
            "release_source_commit",
            "engine_commit",
            "exit_slip_commit",
            "commit_identity_parity",
            "accepted_pv_retained",
            "pointer_generation_retained",
            "prebuild_passed",
            "postseal_pending",
            "approve_gate",
        )
    }


def check_ac18() -> dict[str, Any]:
    """Prove the exact governed HIL stop and zero prohibited mutation."""

    evidence = _postseal_candidate_context()
    return {
        key: evidence[key]
        for key in (
            "candidate_id",
            "hil_state",
            "hil_choices",
            "hil_stop_and_wait",
            "suggested_next_prompt",
            "safety",
        )
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
    "AC13": check_ac13,
    "AC14": check_ac14,
    "AC15": check_ac15,
    "AC16": check_ac16,
    "AC17": check_ac17,
    "AC18": check_ac18,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=sorted(CHECKS))
    args = parser.parse_args()
    evidence = CHECKS[args.check]()
    print(
        json.dumps(
            {"check": args.check, "status": "PASS", "evidence": evidence},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
