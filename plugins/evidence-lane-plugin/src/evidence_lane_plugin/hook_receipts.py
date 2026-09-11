"""Verify and seal the engine receipt for one native Hook occurrence."""

from __future__ import annotations

import hashlib

from .errors import LaneError
from .hook_event_handlers import HandledHook
from .storage import json_text


def seal_hook_receipt(handled: HandledHook, delivery: dict) -> dict:
    if delivery.get("handler_id") != handled.handler_id:
        raise LaneError("HOOK_RECEIPT_HANDLER", "The Hook receipt belongs to another event handler.")
    captured = delivery.get("captured") is True
    if captured:
        result = delivery.get("result")
        if not isinstance(result, dict) or result.get("event_id") != handled.envelope.event_id:
            raise LaneError("HOOK_RECEIPT_EVENT", "The Hook receipt belongs to another event occurrence.")
    elif delivery.get("reason") != "project_session_not_bound":
        raise LaneError("HOOK_RECEIPT_STATE", "The Hook delivery returned an unsupported state.")
    body = {
        "schema": "evidence-lane.native-hook-receipt.v4",
        "event": handled.classification.event,
        "event_id": handled.envelope.event_id,
        "handler_id": handled.handler_id,
        "event_class": handled.classification.event_class,
        "captured": captured,
        "reason": delivery.get("reason"),
        "transport": delivery.get("transport"),
        "engine_instance_id": delivery.get("engine_instance_id"),
        "result": delivery.get("result") if captured else None,
        "automatic_retry": False,
        "raw_input_persisted": False,
        "lifecycle_control": False,
    }
    return {**body, "receipt_sha256": hashlib.sha256(json_text(body).encode()).hexdigest()}


__all__ = ["seal_hook_receipt"]
