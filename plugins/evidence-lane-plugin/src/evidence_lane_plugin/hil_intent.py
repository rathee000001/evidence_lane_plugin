"""Conservative natural-language HIL intent classification without promotion."""

from __future__ import annotations

import re
from typing import Any

from .next_actions import HIL_SUGGESTED_PROMPT, PROJECT_HIL_DECISION_TOKENS

_CONTINUE_RE = re.compile(
    r"\b(?:pursue|purse|continue|proceed|resume|carry\s+on)\b.*"
    r"\b(?:same|this|current)?\s*(?:hil|hill)\b",
    re.IGNORECASE,
)
_APPROVAL_WORD_RE = re.compile(
    r"\b(?:approve|approved|accept|accepted|yes|looks\s+good|go\s+ahead)\b",
    re.IGNORECASE,
)


def _command_argument(utterance: str) -> tuple[str | None, str]:
    parts = utterance.split(maxsplit=2)
    if len(parts) >= 2 and parts[0] == "/evi-build":
        return parts[1], parts[2] if len(parts) == 3 else ""
    return None, ""


def classify_hil_intent(
    utterance: str,
    *,
    candidate_id: str | None,
    pending_hil: bool,
) -> dict[str, Any]:
    """Interpret visible intent while preserving exact-token promotion authority."""

    exact = str(utterance).strip()
    argument, continuation = _command_argument(exact)
    exact_approve = exact == "APPROVE" or argument == "APPROVE"
    if exact_approve:
        intent = "EXACT_APPROVE_READY" if pending_hil else "APPROVE_WITHOUT_PENDING_HIL"
        route = "pv_fuse" if pending_hil else None
        suggested = (
            "/evi-build APPROVE"
            if pending_hil
            else "Run /evi-refresh to build a new unaccepted candidate first."
        )
    elif argument and argument.startswith("APPROVE_WITH_DELTA"):
        intent = "EXACT_CORRECTION_DECISION"
        route = "hil_decide" if pending_hil else None
        suggested = HIL_SUGGESTED_PROMPT
    elif argument and any(
        argument.startswith(choice) for choice in PROJECT_HIL_DECISION_TOKENS[2:]
    ):
        intent = "EXACT_NON_PROMOTION_DECISION"
        route = "hil_decide" if pending_hil else None
        suggested = HIL_SUGGESTED_PROMPT
    elif _CONTINUE_RE.search(exact):
        intent = "CONTINUE_SAME_HIL"
        route = None
        suggested = HIL_SUGGESTED_PROMPT
    elif _APPROVAL_WORD_RE.search(exact):
        intent = "APPROVAL_INTENT_REQUIRES_EXACT_TOKEN"
        route = None
        suggested = "/evi-build APPROVE"
    else:
        intent = "UNCLASSIFIED_HIL_INTENT"
        route = None
        suggested = HIL_SUGGESTED_PROMPT
    return {
        "status": "PASS",
        "schema": "evidence-lane.hil-intent-classification.v1",
        "intent": intent,
        "candidate_id": candidate_id,
        "pending_hil": pending_hil,
        "exact_approval_token_present": exact_approve,
        "tool_route": route,
        "follow_on_instruction_present": bool(continuation),
        "suggested_next_prompt": suggested,
        "candidate_promoted": False,
        "pointer_moved": False,
        "automatic_acceptance": False,
        "classification_is_not_a_hil_decision": True,
    }
