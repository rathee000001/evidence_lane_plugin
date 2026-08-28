"""Ordered native subhook pipeline shared by all Codex hook classes.

Each host-visible Hook row executes one real stage.  Only TRANSPORT invokes the
event implementation, exactly once; EMIT verifies and returns that stored
result.  The chain stores no raw host payload or private reasoning.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Final

PIPELINE_SCHEMA: Final = "evidence-lane.codex-native-subhook-pipeline.v1"
STAGES: Final = ("VALIDATE", "SEAL", "TRANSPORT", "EMIT")
TERMINAL_OUTPUT_EVENTS: Final = {"Stop", "SessionEnd"}
PRIOR_STAGE_POLL_SECONDS: Final = 0.02


class SubhookPipelineError(RuntimeError):
    """Raised when an ordered native subhook stage cannot be proven."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _load_transport(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.hook_contract import build_hook_transport_envelope

    return build_hook_transport_envelope(event_name, payload)


def _control_root() -> Path:
    configured = os.environ.get("EVIDENCE_LANE_HOOK_PIPELINE_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    return (
        Path.home()
        / ".codex"
        / "plugins"
        / "runtime"
        / "evidence-lane-plugin"
        / "native-subhook-pipeline"
    ).resolve()


def _read_payload() -> tuple[dict[str, Any], str]:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return payload, raw


def _chain(event_name: str, transport: dict[str, Any]) -> tuple[Path, str]:
    receipt = str(transport.get("transport_receipt_sha256") or "")
    if len(receipt) != 64:
        raise SubhookPipelineError("SUBHOOK_TRANSPORT_RECEIPT_INVALID")
    chain_id = _sha256(f"{event_name}|{receipt}".encode())
    return _control_root() / event_name / chain_id, chain_id


def _write_sealed(path: Path, body: dict[str, Any]) -> dict[str, Any]:
    core = dict(body)
    core["receipt_sha256"] = _sha256(_canonical(core))
    encoded = _canonical(core) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise SubhookPipelineError("SUBHOOK_STAGE_REPLAY_COLLISION")
        return core
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return core


def _read_sealed(path: Path, *, stage: str, chain_id: str) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubhookPipelineError("SUBHOOK_PRIOR_STAGE_MISSING") from exc
    receipt = body.pop("receipt_sha256", None) if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("schema") != PIPELINE_SCHEMA
        or body.get("stage") != stage
        or body.get("chain_id") != chain_id
        or receipt != _sha256(_canonical(body))
    ):
        raise SubhookPipelineError("SUBHOOK_PRIOR_STAGE_RECEIPT_INVALID")
    body["receipt_sha256"] = receipt
    return body


def _wait_for_sealed(
    path: Path,
    *,
    stage: str,
    chain_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for one prior native handler without assuming host launch order."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return _read_sealed(path, stage=stage, chain_id=chain_id)
        except SubhookPipelineError as exc:
            if str(exc) != "SUBHOOK_PRIOR_STAGE_MISSING":
                raise
        if time.monotonic() >= deadline:
            raise SubhookPipelineError("SUBHOOK_PRIOR_STAGE_TIMEOUT")
        time.sleep(PRIOR_STAGE_POLL_SECONDS)


def _run_implementation(
    implementation: Path,
    implementation_args: list[str],
    raw_payload: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [sys.executable, str(implementation), *implementation_args],
        input=raw_payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
        creationflags=flags,
    )
    if completed.returncode != 0:
        raise SubhookPipelineError("SUBHOOK_TRANSPORT_IMPLEMENTATION_FAILED")
    serialized = completed.stdout.strip()
    try:
        output = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise SubhookPipelineError("SUBHOOK_TRANSPORT_OUTPUT_INVALID") from exc
    if not isinstance(output, dict):
        raise SubhookPipelineError("SUBHOOK_TRANSPORT_OUTPUT_INVALID")
    return output


def run(stage: str) -> int:
    if stage not in STAGES or len(sys.argv) < 3:
        raise SubhookPipelineError("SUBHOOK_STAGE_ARGUMENTS_INVALID")
    event_name = str(sys.argv[1])
    implementation_name = str(sys.argv[2])
    implementation_args = [str(value) for value in sys.argv[3:]]
    payload, raw_payload = _read_payload()
    transport = _load_transport(event_name, payload)
    chain_root, chain_id = _chain(event_name, transport)
    common = {
        "schema": PIPELINE_SCHEMA,
        "event_name": event_name,
        "chain_id": chain_id,
        "transport_receipt_sha256": transport["transport_receipt_sha256"],
        "raw_payload_stored": False,
        "private_reasoning_stored": False,
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }

    if stage == "VALIDATE":
        _write_sealed(
            chain_root / "01-validate.json",
            {
                **common,
                "stage": stage,
                "action": "VALIDATE_REDACT_AND_BOUND_VISIBLE_SIGNAL",
                "transport_bytes": transport["transport_bytes"],
            },
        )
        print("{}")
        return 0

    prior_stage_timeout = 2.0 if event_name == "SessionEnd" else 8.0
    validated = _wait_for_sealed(
        chain_root / "01-validate.json",
        stage="VALIDATE",
        chain_id=chain_id,
        timeout_seconds=prior_stage_timeout,
    )
    if stage == "SEAL":
        _write_sealed(
            chain_root / "02-seal.json",
            {
                **common,
                "stage": stage,
                "action": "DEDUPE_AND_SEAL_EVENT",
                "prior_receipt_sha256": validated["receipt_sha256"],
                "dedupe_identity_sha256": _sha256(
                    f"{event_name}|{chain_id}|SEAL".encode()
                ),
            },
        )
        print("{}")
        return 0

    sealed = _wait_for_sealed(
        chain_root / "02-seal.json",
        stage="SEAL",
        chain_id=chain_id,
        timeout_seconds=prior_stage_timeout,
    )
    implementation = (Path(__file__).resolve().parent / implementation_name).resolve()
    if implementation.parent != Path(__file__).resolve().parent or not implementation.is_file():
        raise SubhookPipelineError("SUBHOOK_IMPLEMENTATION_INVALID")

    if stage == "TRANSPORT":
        output = _run_implementation(
            implementation,
            implementation_args,
            raw_payload,
            timeout_seconds=1.0 if event_name == "SessionEnd" else 5.0,
        )
        _write_sealed(
            chain_root / "03-transport.json",
            {
                **common,
                "stage": stage,
                "action": "EXECUTE_EVENT_TRANSPORT_ONCE",
                "prior_receipt_sha256": sealed["receipt_sha256"],
                "implementation_handler": implementation_name,
                "implementation_execution_count": 1,
                "output": output,
                "output_sha256": _sha256(_canonical(output)),
            },
        )
        print("{}")
        return 0

    transported = _wait_for_sealed(
        chain_root / "03-transport.json",
        stage="TRANSPORT",
        chain_id=chain_id,
        timeout_seconds=prior_stage_timeout,
    )
    output = transported.get("output")
    if not isinstance(output, dict) or transported.get("output_sha256") != _sha256(
        _canonical(output)
    ):
        raise SubhookPipelineError("SUBHOOK_TRANSPORT_OUTPUT_RECEIPT_INVALID")
    serialized = _canonical(output).decode("utf-8")
    _write_sealed(
        chain_root / "04-emit.json",
        {
            **common,
            "stage": stage,
            "action": "VERIFY_AND_EMIT_EVENT_RESULT",
            "prior_receipt_sha256": transported["receipt_sha256"],
            "implementation_execution_count": 0,
            "behavior_handoff_or_terminal_output_verified": (
                event_name in TERMINAL_OUTPUT_EVENTS or isinstance(output, dict)
            ),
            "output_sha256": transported["output_sha256"],
        },
    )
    print(serialized)
    return 0
