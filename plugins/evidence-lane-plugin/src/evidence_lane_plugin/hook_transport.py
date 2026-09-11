"""Authenticated once-only transport for one handled native Hook event."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .errors import LaneError
from .hook_event_handlers import HandledHook
from .launcher import runtime_root, verify_engine_binding
from .local_transport import LocalTransport, owner_endpoint


def deliver_hook(handled: HandledHook, selected_root: Path | None = None) -> dict:
    remote_config = os.environ.get("EVIDENCE_LANE_REMOTE_CONFIG")
    if remote_config:
        from .remote_transport import RemoteClientConfig, submit_remote_hook

        result = submit_remote_hook(RemoteClientConfig.load(Path(remote_config)), handled.envelope)
        return {**result, "transport": "remote_authenticated_capture", "handler_id": handled.handler_id}

    root = runtime_root(selected_root)
    timeout = 0.75 if handled.classification.terminal_timeout else 3
    with LocalTransport(root, timeout=timeout) as transport:
        health = verify_engine_binding(transport)
    record, credential = owner_endpoint(root)
    if record["instance_id"] != health["instance_id"]:
        raise LaneError("ENGINE_INSTANCE_CHANGED", "The engine changed before Hook delivery.")
    payload = {"instance_id": record["instance_id"], "capture": handled.envelope.model_dump(mode="json")}
    try:
        with (
            httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client,
            client.stream(
                "POST",
                f"http://127.0.0.1:{record['port']}/v4/capture",
                headers={"Authorization": "Bearer " + credential},
                json=payload,
            ) as response,
        ):
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 65_536:
                    raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError
            if response.status_code == 409 and result.get("error") == "CAPTURE_BINDING_REQUIRED":
                return {
                    "captured": False,
                    "reason": "project_session_not_bound",
                    "transport": "local_authenticated_capture",
                    "handler_id": handled.handler_id,
                }
            if response.status_code != 200 or result.get("event_id") != handled.envelope.event_id:
                raise ValueError
            return {
                "captured": True,
                "result": result,
                "engine_instance_id": record["instance_id"],
                "transport": "local_authenticated_capture",
                "handler_id": handled.handler_id,
            }
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        raise LaneError(
            "HOOK_DELIVERY_UNCONFIRMED",
            "Native Hook capture was not confirmed; no automatic retry was performed.",
        ) from None


__all__ = ["deliver_hook"]
