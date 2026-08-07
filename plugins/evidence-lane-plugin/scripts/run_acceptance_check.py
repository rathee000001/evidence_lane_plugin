"""Execute one exact Evidence Lane v1.3 acceptance check without mutating source."""

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
from evidence_lane_plugin.next_actions import HIL_CHOICES
from evidence_lane_plugin.pv_package import validate_pv_package

EXPECTED_BRANCH = "agent/evi-v130-all-source-brain-workflow-hil-v1.3.0"
EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence" / "implementation_v41"
CORRECTION_EVIDENCE_ROOT = REPOSITORY_ROOT / "evidence" / "implementation_v42"
CORRECTION_BASE_SOURCE_COMMIT = "6020563094154ff780705cbed85f453425884914"
CORRECTION_RECEIPT = (
    EVIDENCE_ROOT / "DELTA080A_V130_EXECUTABLE_GATE_VERSION_CORRECTION_RECEIPT.json"
)
SOURCE_DISPOSITION_RECEIPT = (
    CORRECTION_EVIDENCE_ROOT / "ALL_SOURCE_DISPOSITION_RECEIPT.json"
)


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
                "plugins/evidence-lane-plugin/remote_adapter",
                "build",
            ],
            timeout_seconds=900,
        )
    }


def check_ac05() -> dict[str, Any]:
    return {
        "version_consistency": _pytest(
            "tests/test_v130_version_consistency.py",
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
    chatgpt = matrix.get("chatgpt_durable_mcp_host") or {}
    codex = matrix.get("codex_local_pc_laptop_or_stable_vm") or {}
    _require(
        chatgpt.get("primary_runtime_authority")
        == "MCP_SERVER_MOUNTED_OR_LOCAL_SQLITE",
        "ChatGPT primary runtime differs.",
    )
    _require(
        chatgpt.get("google_drive_policy") == "FORBIDDEN_FOR_CHATGPT_RUNTIME",
        "ChatGPT GDrive runtime boundary differs.",
    )
    _require(
        codex.get("primary_runtime_authority") == "LOCAL_DURABLE_SQLITE",
        "Stable Codex primary runtime differs.",
    )
    return {
        "tunnel_receipt": tunnel["receipt_sha256"],
        "host_receipt": host["receipt_sha256"],
        "chatgpt_runtime": chatgpt,
        "codex_runtime": codex,
    }


def check_ac11() -> dict[str, Any]:
    branch = _git("branch", "--show-current")
    status = _git("status", "--porcelain=v1")
    head = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    _require(branch == EXPECTED_BRANCH, "The governed study branch differs.")
    _require(not status, "The final study branch is not clean.")
    _require(ENGINE_VERSION == "1.3.0", "The engine is not v1.3.0.")
    correction = _sealed_receipt(CORRECTION_RECEIPT.name)
    safety = correction["payload"].get("safety") or {}
    _require(safety.get("main_merged") is False, "The correction claims a main merge.")
    _require(
        safety.get("production_deployed") is False,
        "The correction claims a production deployment.",
    )
    return {
        "branch": branch,
        "commit": head,
        "tree": tree,
        "engine_version": ENGINE_VERSION,
        "correction_receipt": correction["receipt_sha256"],
    }


def check_ac12() -> dict[str, Any]:
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
    source_disposition = _sealed_receipt_path(SOURCE_DISPOSITION_RECEIPT)
    source_commit = ((project_identity.get("repository") or {}).get("commit_sha"))
    engine_commit = (manifest.get("engine") or {}).get("commit")
    exit_commit = (exit_slip.get("repository_exit") or {}).get("commit_sha")
    _require(validation.get("candidate_id") == expected_candidate, "Candidate mismatch.")
    _require(validation.get("status") == "PASS", "Candidate validation failed.")
    _require(expected_commit == _git("rev-parse", "HEAD"), "Live source commit mismatch.")
    _require(
        source_disposition["payload"].get("correction_base_source_commit")
        == CORRECTION_BASE_SOURCE_COMMIT,
        "Correction-base source commit mismatch.",
    )
    _require(
        expected_commit != CORRECTION_BASE_SOURCE_COMMIT,
        "The corrected implementation was not committed after its preserved base.",
    )
    _git("merge-base", "--is-ancestor", CORRECTION_BASE_SOURCE_COMMIT, expected_commit)
    _require(source_commit == expected_commit, "Candidate source commit mismatch.")
    _require(engine_commit == expected_commit, "Engine mismatch.")
    _require(
        exit_commit == expected_commit,
        "Exit commit mismatch.",
    )
    _require(pointer.get("accepted_pv") == expected_pv == "PV5", "PV5 pointer moved.")
    _require(
        pointer.get("generation") == expected_generation == 5,
        "Pointer generation moved.",
    )
    acceptance = exit_slip.get("acceptance_checks") or {}
    counts = acceptance.get("counts") or {}
    _require(acceptance.get("prebuild_status") == "PASS", "Prebuild gate failed.")
    _require(counts.get("PASS") == 11, "Expected eleven passing prebuild checks.")
    _require(counts.get("PENDING_POSTSEAL") == 1, "Expected one postseal check.")
    ci_cd = (exit_slip.get("mode_execution") or {}).get("ci_cd") or {}
    _require(ci_cd.get("approve_gate") == "PASS", "Code-mode approve gate is open.")
    _require(ci_cd.get("executed") == 11, "Code-mode prebuild count differs.")
    _require(ci_cd.get("postseal_pending") == 1, "Code-mode postseal count differs.")
    next_action = exit_slip.get("next_action") or {}
    _require(next_action.get("state") == "PRESENT_SIX_WAY_HIL", "HIL state differs.")
    _require(next_action.get("choices") == list(HIL_CHOICES), "HIL choices differ.")
    _require(next_action.get("stop_and_wait") is True, "HIL is not stop-and-wait.")
    return {
        "candidate_id": expected_candidate,
        "manifest_sha256": validation.get("manifest_sha256"),
        "package_sha256": validation.get("package_sha256"),
        "correction_base_source_commit": CORRECTION_BASE_SOURCE_COMMIT,
        "corrected_source_commit": source_commit,
        "engine_commit": engine_commit,
        "exit_slip_commit": exit_commit,
        "commit_identity_parity": source_commit == engine_commit == exit_commit,
        "source_disposition_receipt": source_disposition["receipt_sha256"],
        "accepted_pv_retained": expected_pv,
        "pointer_generation_retained": expected_generation,
        "prebuild_passed": 11,
        "postseal_check": "PASS",
        "approve_gate": ci_cd["approve_gate"],
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
    print(
        json.dumps(
            {"check": args.check, "status": "PASS", "evidence": evidence},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
