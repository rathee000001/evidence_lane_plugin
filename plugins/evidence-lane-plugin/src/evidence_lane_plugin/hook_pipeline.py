"""Ordered native Hook pipeline composed from distinct stage owners."""

from __future__ import annotations

from pathlib import Path

from .hook_admission import admit_hook
from .hook_classification import classify_hook
from .hook_event_handlers import NativeHookHandler
from .hook_event_isolation import HookExecution
from .hook_lifecycle_boundary import verify_capture_boundary
from .hook_output import project_hook_output
from .hook_receipts import seal_hook_receipt
from .hook_transport import deliver_hook

HOOK_PIPELINE = (
    {"id": "validate_and_redact_input", "owner": "hook_admission", "implementation": "hook_admission.admit_hook"},
    {"id": "classify_event", "owner": "hook_classification", "implementation": "hook_classification.classify_hook"},
    {"id": "handle_event", "owner": "event_specific_handler", "implementation": "hook_event_handlers.<Event>Handler.handle"},
    {"id": "authenticate_and_deliver_once", "owner": "hook_transport", "implementation": "hook_transport.deliver_hook", "automatic_retry": False},
    {"id": "verify_and_seal_receipt", "owner": "hook_receipts", "implementation": "hook_receipts.seal_hook_receipt"},
    {"id": "project_bounded_output", "owner": "hook_output", "implementation": "hook_output.project_hook_output"},
)


def run_hook_pipeline(raw: bytes, handler: type[NativeHookHandler], selected_root: Path | None = None) -> tuple[dict, dict]:
    execution = HookExecution(handler.event_name)
    try:
        envelope = admit_hook(raw, handler.event_name)
        execution.advance("admitted")
        classification = classify_hook(envelope, handler.event_name)
        execution.advance("classified")
        verify_capture_boundary(classification)
        handled = handler.handle(envelope, classification)
        execution.advance("handled")
        delivery = deliver_hook(handled, selected_root)
        execution.advance("delivered")
        receipt = seal_hook_receipt(handled, delivery)
        execution.advance("sealed")
        output = project_hook_output(handled, receipt)
        execution.advance("projected")
        return output, {"execution": execution.receipt(), "capture": receipt}
    except Exception:
        execution.fail()
        raise


__all__ = ["HOOK_PIPELINE", "run_hook_pipeline"]
