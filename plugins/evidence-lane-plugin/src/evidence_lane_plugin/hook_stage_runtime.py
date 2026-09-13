"""Dependency-safe native Hook stages exposed as separate Codex Hook rows.

Codex invokes every configured Hook handler with the same bounded host payload,
but it does not make the plugin's internal stage graph visible.  This runtime
restores the selected v3 host presentation (validate, seal, transport, emit)
while executing the current v4 admission, classification, event-specific
handler, authenticated delivery, receipt, and bounded-output owners.

Only redacted identities, stage receipts, and the authenticated delivery result
are written below ``PLUGIN_DATA``.  Raw host input and private reasoning are
never persisted.  TRANSPORT is guarded by an exclusive occurrence lock so the
event reaches the engine at most once even if the host overlaps handler starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Final, TypedDict
from uuid import UUID

from .capture_routing import HookEnvelope
from .errors import LaneError
from .hook_admission import MAX_HOOK_INPUT_BYTES, admit_hook
from .hook_classification import classify_hook
from .hook_event_handlers import HandledHook, handler_class_for_event
from .hook_lifecycle_boundary import verify_capture_boundary
from .hook_output import project_hook_output
from .hook_receipts import seal_hook_receipt
from .hook_transport import deliver_hook
from .storage import json_text

HOST_STAGE_SCHEMA: Final = "evidence-lane.native-host-hook-stage.v4"


class HostHookStage(TypedDict):
    id: str
    status: str
    owners: list[str]


HOST_HOOK_STAGES: Final[tuple[HostHookStage, ...]] = (
    {
        "id": "VALIDATE",
        "status": "Validating and redacting Evidence Lane {event}",
        "owners": ["hook_admission"],
    },
    {
        "id": "SEAL",
        "status": "Classifying and sealing Evidence Lane {event}",
        "owners": ["hook_classification", "hook_lifecycle_boundary"],
    },
    {
        "id": "TRANSPORT",
        "status": "Handling and delivering Evidence Lane {event}",
        "owners": ["event_specific_handler", "hook_transport"],
    },
    {
        "id": "EMIT",
        "status": "Verifying and emitting Evidence Lane {event}",
        "owners": ["hook_receipts", "hook_output"],
    },
)
_STAGE_INDEX: Final = {stage["id"]: index for index, stage in enumerate(HOST_HOOK_STAGES, 1)}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _control_root() -> Path:
    configured = os.environ.get("EVIDENCE_LANE_HOOK_PIPELINE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    plugin_data = os.environ.get("PLUGIN_DATA", "").strip()
    if plugin_data:
        return (Path(plugin_data).expanduser().resolve() / "native-hook-pipeline-v4")
    studio = Path(os.environ.get("EVIDENCE_LANE_STUDIO_ROOT", r"C:\Apps\EvidenceLaneStudio"))
    return (studio / "engine" / "data" / "native-hook-pipeline-v4").resolve()


def _event_id(chain_id: str) -> str:
    return str(UUID(hex=chain_id[:32]))


def _admitted_occurrence(raw: bytes, event: str) -> tuple[Path, str, str, HookEnvelope]:
    admitted = admit_hook(raw, event)
    admitted_digest = _sha256(_canonical(admitted.event))
    chain_id = _sha256(f"{event}|{admitted_digest}".encode())
    envelope = HookEnvelope(event_id=_event_id(chain_id), event=admitted.event)
    return _control_root() / event / chain_id, chain_id, admitted_digest, envelope


def _stage_path(root: Path, stage: str) -> Path:
    return root / f"{_STAGE_INDEX[stage]:02d}-{stage.lower()}.json"


def _sealed_body(body: dict) -> dict:
    core = dict(body)
    core["receipt_sha256"] = _sha256(_canonical(core))
    return core


def _write_once(path: Path, body: dict) -> dict:
    sealed = _sealed_body(body)
    encoded = _canonical(sealed) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise LaneError("HOOK_STAGE_REPLAY_COLLISION", "A host Hook stage already has different sealed output.")
        return sealed
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(encoded)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return sealed


def _read_sealed(path: Path, *, stage: str, chain_id: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        receipt = value.pop("receipt_sha256")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LaneError("HOOK_PRIOR_STAGE_MISSING", "A prerequisite Host Hook stage has not completed.") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != HOST_STAGE_SCHEMA
        or value.get("stage") != stage
        or value.get("chain_id") != chain_id
        or receipt != _sha256(_canonical(value))
    ):
        raise LaneError("HOOK_STAGE_RECEIPT_INVALID", "A Host Hook stage receipt failed verification.")
    value["receipt_sha256"] = receipt
    return value


def _wait(path: Path, *, stage: str, chain_id: str, terminal: bool) -> dict:
    deadline = time.monotonic() + (2.0 if terminal else 8.0)
    while True:
        try:
            return _read_sealed(path, stage=stage, chain_id=chain_id)
        except LaneError as error:
            if error.code != "HOOK_PRIOR_STAGE_MISSING":
                raise
        if time.monotonic() >= deadline:
            raise LaneError("HOOK_PRIOR_STAGE_TIMEOUT", "A prerequisite Host Hook stage did not complete in time.")
        time.sleep(0.02)


def _base(event: str, stage: str, chain_id: str, admitted_digest: str, previous: str | None) -> dict:
    return {
        "schema": HOST_STAGE_SCHEMA,
        "event": event,
        "stage": stage,
        "stage_index": _STAGE_INDEX[stage],
        "chain_id": chain_id,
        "admitted_event_sha256": admitted_digest,
        "previous_receipt_sha256": previous,
        "raw_input_persisted": False,
        "private_reasoning_persisted": False,
        "automatic_retry": False,
        "lifecycle_control": False,
    }


def run_host_hook_stage(raw: bytes, event: str, stage: str) -> dict:
    if stage not in _STAGE_INDEX:
        raise LaneError("HOOK_STAGE_UNSUPPORTED", "Select a registered Host Hook stage.")
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise LaneError("HOOK_INPUT_BUDGET", "The native Hook payload exceeds its capture budget.")
    root, chain_id, admitted_digest, envelope = _admitted_occurrence(raw, event)
    terminal = event in {"Interrupt", "SessionEnd"}

    if stage == "VALIDATE":
        _write_once(
            _stage_path(root, stage),
            {
                **_base(event, stage, chain_id, admitted_digest, None),
                "event_id": envelope.event_id,
                "owners_executed": ["hook_admission"],
            },
        )
        return {}

    validated = _wait(_stage_path(root, "VALIDATE"), stage="VALIDATE", chain_id=chain_id, terminal=terminal)
    if validated.get("event_id") != envelope.event_id or validated.get("admitted_event_sha256") != _sha256(_canonical(envelope.event)):
        raise LaneError("HOOK_STAGE_EVENT_MISMATCH", "The admitted Host Hook event differs across stages.")
    classification = classify_hook(envelope, event)

    if stage == "SEAL":
        boundary = verify_capture_boundary(classification)
        _write_once(
            _stage_path(root, stage),
            {
                **_base(event, stage, chain_id, admitted_digest, validated["receipt_sha256"]),
                "event_id": envelope.event_id,
                "classification": asdict(classification),
                "boundary": boundary,
                "dedupe_identity_sha256": _sha256(f"{event}|{chain_id}|SEAL".encode()),
                "owners_executed": ["hook_classification", "hook_lifecycle_boundary"],
            },
        )
        return {}

    sealed = _wait(_stage_path(root, "SEAL"), stage="SEAL", chain_id=chain_id, terminal=terminal)
    if sealed.get("event_id") != envelope.event_id or sealed.get("classification") != asdict(classification):
        raise LaneError("HOOK_STAGE_EVENT_MISMATCH", "The classified Host Hook event differs across stages.")
    handler = handler_class_for_event(event)

    if stage == "TRANSPORT":
        handled = handler.handle(envelope, classification)
        target = _stage_path(root, stage)
        try:
            prior = _read_sealed(target, stage=stage, chain_id=chain_id)
        except LaneError as error:
            if error.code != "HOOK_PRIOR_STAGE_MISSING":
                raise
            lock = root / "03-transport.lock"
            try:
                handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                _wait(target, stage=stage, chain_id=chain_id, terminal=terminal)
                return {}
            try:
                os.close(handle)
                delivery = deliver_hook(handled)
                _write_once(
                    target,
                    {
                        **_base(event, stage, chain_id, admitted_digest, sealed["receipt_sha256"]),
                        "event_id": envelope.event_id,
                        "handler_id": handled.handler_id,
                        "delivery": delivery,
                        "delivery_sha256": _sha256(_canonical(delivery)),
                        "implementation_execution_count": 1,
                        "owners_executed": ["event_specific_handler", "hook_transport"],
                    },
                )
            finally:
                lock.unlink(missing_ok=True)
        else:
            if prior.get("handler_id") != handled.handler_id:
                raise LaneError("HOOK_STAGE_HANDLER_MISMATCH", "The Host Hook transport belongs to another handler.")
        return {}

    transported = _wait(_stage_path(root, "TRANSPORT"), stage="TRANSPORT", chain_id=chain_id, terminal=terminal)
    transported_delivery = transported.get("delivery")
    if (
        transported.get("event_id") != envelope.event_id
        or transported.get("handler_id") != handler.handler_id
        or not isinstance(transported_delivery, dict)
        or transported.get("delivery_sha256") != _sha256(_canonical(transported_delivery))
    ):
        raise LaneError("HOOK_STAGE_DELIVERY_MISMATCH", "The authenticated Hook delivery failed stage verification.")
    handled = HandledHook(
        envelope=envelope,
        classification=classification,
        handler_id=handler.handler_id,
    )
    receipt = seal_hook_receipt(handled, transported_delivery)
    output = project_hook_output(handled, receipt)
    _write_once(
        _stage_path(root, stage),
        {
            **_base(event, stage, chain_id, admitted_digest, transported["receipt_sha256"]),
            "event_id": envelope.event_id,
            "hook_receipt_sha256": receipt["receipt_sha256"],
            "output_sha256": _sha256(_canonical(output)),
            "implementation_execution_count": 0,
            "owners_executed": ["hook_receipts", "hook_output"],
        },
    )
    return output


def main(event: str, stage: str) -> int:
    import sys

    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
        print(json_text(run_host_hook_stage(raw, event, stage)))
        return 0
    except LaneError as error:
        print(json_text({"systemMessage": "Evidence Lane capture unavailable: " + error.code}))
        return 0


__all__ = ["HOST_HOOK_STAGES", "HOST_STAGE_SCHEMA", "main", "run_host_hook_stage"]
