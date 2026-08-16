"""Project-scoped ChatLineage capture-route authority.

The ENV-builder route intentionally records only governed authority units.  A
normal governed project records the complete secret-redacted visible lineage.
The route is bound before ingestion and every decision is append-only and
hash-chained outside the lineage directory so it cannot be mistaken for a
session JSONL stream.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
)

ENV_BUILDER_SPARSE = "ENV_BUILDER_SPARSE"
GOVERNED_PROJECT_FULL = "GOVERNED_PROJECT_FULL"
CAPTURE_ROUTES = (ENV_BUILDER_SPARSE, GOVERNED_PROJECT_FULL)

CAPTURE_ROUTE_BINDING_SCHEMA = "evidence-lane.capture-route-binding.v1"
CAPTURE_DECISION_SCHEMA = "evidence-lane.capture-decision.v1"

_SPARSE_INCLUDED_KINDS = {
    "ACCEPTED_DELTA",
    "HARD_GATE",
    "SCHEMA_DECISION",
    "RECEIPT",
    "GOVERNED_ARTIFACT",
    "EXIT_SLIP",
}
_VISIBLE_INPUT_KINDS = {
    "VISIBLE_PROMPT",
    "VISIBLE_GOAL",
    "VISIBLE_STEER",
    "VISIBLE_RESPONSE",
}
_ALLOWED_EXIT_REASONS = {
    "HIL_WAIT",
    "EXPLICIT_PAUSE",
    "GENUINE_BLOCK",
    "GOVERNED_ERROR",
    "EXIT_BOOT",
    "STATE_TRAVEL_HANDOFF",
    "STATELESS_EPHEMERAL_END",
}
_RECEIPT_EVENT_PREFIXES = (
    "session.",
    "task.",
    "plan.",
    "mode.",
    "lane.",
    "source.",
    "hook.",
    "turn.control_",
    "turn.lifecycle.",
)


def normalize_capture_route(value: str) -> str:
    route = value.strip().upper().replace("-", "_")
    require(
        route in CAPTURE_ROUTES,
        "CAPTURE_ROUTE_INVALID",
        "Capture route must select exactly one supported project policy.",
        status="BLOCKED",
        supplied_route=value,
        supported_routes=list(CAPTURE_ROUTES),
    )
    return route


def _normalized_kind(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _project_ids(value: Any) -> set[str]:
    """Collect active project_id claims, excluding provenance source IDs."""

    discovered: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized == "project_id" and isinstance(item, str) and item.strip():
                discovered.add(item.strip())
            elif normalized != "source_project_id":
                discovered.update(_project_ids(item))
    elif isinstance(value, list):
        for item in value:
            discovered.update(_project_ids(item))
    return discovered


def _accepted_delta(payload: dict[str, Any]) -> bool:
    statuses = {
        str(payload.get("status") or "").strip().upper(),
        str(payload.get("lifecycle_status") or "").strip().upper(),
        str(payload.get("delta_status") or "").strip().upper(),
    }
    return payload.get("accepted") is True or "ACCEPTED" in statuses


def _capture_kind(event_type: str, payload: dict[str, Any]) -> str:
    explicit = next(
        (
            _normalized_kind(payload.get(key))
            for key in ("capture_kind", "governed_capture_kind", "unit_kind")
            if _normalized_kind(payload.get(key)) is not None
        ),
        None,
    )
    if explicit is not None:
        return explicit

    lowered = event_type.strip().lower()
    if lowered == "turn.lifecycle_exit_slip":
        return "EXIT_SLIP"
    if "steer" in lowered:
        return "VISIBLE_STEER"
    if lowered in {"turn.visible_user_goal", "user.goal"}:
        return "VISIBLE_GOAL"
    if "prompt" in lowered or lowered in {"user.input", "user.message"}:
        return "VISIBLE_PROMPT"
    if "assistant" in lowered or lowered.endswith(".response"):
        return "VISIBLE_RESPONSE"
    if lowered.startswith("hil.") or ".hard_gate" in lowered:
        return "HARD_GATE"
    if "schema" in lowered and any(
        marker in lowered for marker in ("decision", "selected", "compiled", "accepted")
    ):
        return "SCHEMA_DECISION"
    if lowered.startswith("pv.candidate.") or "governed_artifact" in lowered:
        return "GOVERNED_ARTIFACT"
    if lowered.startswith("delta.") and _accepted_delta(payload):
        return "ACCEPTED_DELTA"
    if lowered.startswith(_RECEIPT_EVENT_PREFIXES) or lowered.endswith(".receipt"):
        return "RECEIPT"
    return "OPERATIONAL_EVENT"


class CaptureRouteAuthority:
    """Immutable route binding plus append-only per-visible-unit decisions."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.binding_path = self.project_root / "capture_route.json"
        self.decision_path = (
            self.project_root / "receipts" / "capture-route" / "decisions.jsonl"
        )

    @classmethod
    def for_lineage(cls, lineage_path: str | Path) -> CaptureRouteAuthority | None:
        path = Path(lineage_path).resolve()
        if path.parent.name != "lineage":
            return None
        project_root = path.parent.parent
        project_path = project_root / "project.json"
        binding_path = project_root / "capture_route.json"
        if not project_path.exists() and not binding_path.exists():
            return None
        require(
            project_path.is_file(),
            "CAPTURE_ROUTE_PROJECT_AUTHORITY_MISSING",
            "A project capture-route binding cannot exist without project authority.",
            status="BLOCKED",
            project_root=str(project_root),
        )
        authority = cls(project_root)
        authority.binding()
        return authority

    def bind(
        self,
        *,
        project_id: str,
        route: str,
        selected_by: str,
        reason: str,
    ) -> dict[str, Any]:
        exact_project_id = project_id.strip()
        exact_selected_by = selected_by.strip()
        exact_reason = reason.strip()
        require(
            bool(exact_project_id and exact_selected_by and exact_reason),
            "CAPTURE_ROUTE_BINDING_FIELDS_REQUIRED",
            "Capture-route binding requires project, selector, and reason authority.",
            status="BLOCKED",
        )
        normalized_route = normalize_capture_route(route)
        if self.binding_path.exists():
            existing = self.binding()
            require(
                existing["project_id"] == exact_project_id
                and existing["capture_route"] == normalized_route,
                "CAPTURE_ROUTE_BINDING_CONFLICT",
                "The project already has a different immutable capture-route binding.",
                status="BLOCKED",
                expected_project_id=exact_project_id,
                expected_route=normalized_route,
                actual_project_id=existing["project_id"],
                actual_route=existing["capture_route"],
            )
            return {"status": "PASS", "state": "BOUND_IDEMPOTENT_REUSE", **existing}

        core = {
            "schema": CAPTURE_ROUTE_BINDING_SCHEMA,
            "project_id": exact_project_id,
            "capture_route": normalized_route,
            "selected_by": exact_selected_by,
            "selection_reason": exact_reason,
            "selection_required_before_ingestion": True,
            "cross_project_fallback_allowed": False,
            "raw_secret_or_private_reasoning_stored": False,
        }
        payload = {
            **core,
            "binding_sha256": sha256_bytes(canonical_json_bytes(core)),
        }
        atomic_write_json(self.binding_path, payload)
        return {"status": "PASS", "state": "BOUND", **payload}

    def binding(self) -> dict[str, Any]:
        require(
            self.binding_path.is_file(),
            "CAPTURE_ROUTE_BINDING_MISSING",
            "The project capture route is ambiguous because no binding exists.",
            status="BLOCKED",
            project_root=str(self.project_root),
        )
        try:
            payload = json.loads(self.binding_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "CAPTURE_ROUTE_BINDING_INVALID",
                "The project capture-route binding is not valid JSON.",
                status="FAIL",
                details={"path": str(self.binding_path)},
            ) from exc
        require(
            payload.get("schema") == CAPTURE_ROUTE_BINDING_SCHEMA,
            "CAPTURE_ROUTE_BINDING_SCHEMA_MISMATCH",
            "The project capture-route binding schema is unsupported.",
            status="MISMATCH",
        )
        claimed = payload.get("binding_sha256")
        actual = sha256_bytes(
            canonical_json_bytes(
                {key: value for key, value in payload.items() if key != "binding_sha256"}
            )
        )
        require(
            claimed == actual,
            "CAPTURE_ROUTE_BINDING_HASH_MISMATCH",
            "The project capture-route binding hash does not match its content.",
            status="MISMATCH",
        )
        normalize_capture_route(str(payload.get("capture_route") or ""))
        project_path = self.project_root / "project.json"
        if project_path.is_file():
            project = json.loads(project_path.read_text(encoding="utf-8"))
            require(
                project.get("project_id") == payload.get("project_id"),
                "CAPTURE_ROUTE_PROJECT_ID_MISMATCH",
                "Capture-route authority does not match the registered project.",
                status="MISMATCH",
            )
            configured_route = project.get("capture_route")
            require(
                configured_route is None
                or normalize_capture_route(str(configured_route))
                == payload.get("capture_route"),
                "CAPTURE_ROUTE_PROJECT_CONFIG_MISMATCH",
                "Project configuration and capture-route binding disagree.",
                status="MISMATCH",
            )
        return payload

    def _decisions(self) -> list[dict[str, Any]]:
        if not self.decision_path.exists():
            return []
        decisions: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.decision_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                decision = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceLaneError(
                    "CAPTURE_DECISION_JSONL_INVALID",
                    "Capture decision history contains invalid JSON.",
                    status="FAIL",
                    details={"line_number": line_number},
                ) from exc
            claimed = decision.get("decision_sha256")
            actual = sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in decision.items()
                        if key != "decision_sha256"
                    }
                )
            )
            require(
                decision.get("schema") == CAPTURE_DECISION_SCHEMA
                and claimed == actual,
                "CAPTURE_DECISION_HASH_MISMATCH",
                "Capture decision history failed schema or hash validation.",
                status="MISMATCH",
                line_number=line_number,
            )
            require(
                decision.get("previous_decision_sha256")
                == (decisions[-1].get("decision_sha256") if decisions else None),
                "CAPTURE_DECISION_CHAIN_MISMATCH",
                "Capture decision history is not contiguous.",
                status="MISMATCH",
                line_number=line_number,
            )
            decisions.append(decision)
        return decisions

    def status(self) -> dict[str, Any]:
        binding = self.binding()
        decisions = self._decisions()
        return {
            "status": "PASS",
            "schema": CAPTURE_ROUTE_BINDING_SCHEMA,
            "project_id": binding["project_id"],
            "capture_route": binding["capture_route"],
            "binding_sha256": binding["binding_sha256"],
            "decision_count": len(decisions),
            "included_count": sum(
                1 for decision in decisions if decision.get("included") is True
            ),
            "excluded_count": sum(
                1 for decision in decisions if decision.get("included") is False
            ),
            "head_decision_sha256": (
                decisions[-1]["decision_sha256"] if decisions else None
            ),
            "decision_journal_path": str(self.decision_path),
            "decision_journal_outside_lineage_glob": True,
            "raw_excluded_visible_units_stored": False,
            "cross_project_fallback_allowed": False,
        }

    def classify_and_record(
        self,
        *,
        event_id: str,
        event_type: str,
        visible_payload: dict[str, Any],
        occurred_at: str,
        session_id: str,
        task_id: str | None,
        run_id: str | None,
    ) -> dict[str, Any]:
        binding = self.binding()
        project_id = str(binding["project_id"])
        claimed_projects = _project_ids(visible_payload)
        require(
            not claimed_projects or claimed_projects == {project_id},
            "CAPTURE_ROUTE_CROSS_PROJECT_BLOCKED",
            "A visible unit cannot cross the bound project capture authority.",
            status="BLOCKED",
            bound_project_id=project_id,
            claimed_project_ids=sorted(claimed_projects),
        )
        kind = _capture_kind(event_type, visible_payload)
        route = str(binding["capture_route"])
        if kind == "EXIT_SLIP":
            reason = str(visible_payload.get("reason") or "").strip().upper()
            require(
                reason in _ALLOWED_EXIT_REASONS,
                "CAPTURE_ROUTE_EXIT_SLIP_BOUNDARY_INVALID",
                "Exit Slip capture is allowed only at a governed lifecycle boundary.",
                status="BLOCKED",
                supplied_reason=reason or "UNAVAILABLE",
                allowed_reasons=sorted(_ALLOWED_EXIT_REASONS),
            )

        if route == GOVERNED_PROJECT_FULL:
            included = True
            reason_code = "FULL_ROUTE_SECRET_REDACTED_VISIBLE_PROJECT_EVIDENCE"
        elif kind == "ACCEPTED_DELTA" and not _accepted_delta(visible_payload):
            included = False
            reason_code = "SPARSE_ROUTE_DELTA_LACKS_ACCEPTED_STATUS"
        elif kind in _SPARSE_INCLUDED_KINDS:
            included = True
            reason_code = f"SPARSE_ROUTE_INCLUDED_{kind}"
        else:
            included = False
            reason_code = (
                f"SPARSE_ROUTE_EXCLUDED_{kind}"
                if kind in _VISIBLE_INPUT_KINDS or kind == "OPERATIONAL_EVENT"
                else "SPARSE_ROUTE_UNIT_NOT_AUTHORIZED"
            )

        visible_unit_sha256 = sha256_bytes(canonical_json_bytes(visible_payload))
        decision_id = "capture_" + sha256_bytes(
            canonical_json_bytes(
                {
                    "binding_sha256": binding["binding_sha256"],
                    "event_id": event_id,
                    "event_type": event_type,
                    "visible_unit_sha256": visible_unit_sha256,
                }
            )
        )[:26].lower()
        decisions = self._decisions()
        matches = [item for item in decisions if item.get("event_id") == event_id]
        previous = (
            matches[0].get("previous_decision_sha256")
            if matches
            else (decisions[-1].get("decision_sha256") if decisions else None)
        )
        core = {
            "schema": CAPTURE_DECISION_SCHEMA,
            "decision_id": decision_id,
            "event_id": event_id,
            "source_event_type": event_type,
            "project_id": project_id,
            "session_id": session_id,
            "task_id": task_id,
            "run_id": run_id,
            "capture_route": route,
            "capture_kind": kind,
            "included": included,
            "reason_code": reason_code,
            "visible_unit_sha256": visible_unit_sha256,
            "binding_sha256": binding["binding_sha256"],
            "previous_decision_sha256": previous,
            "raw_visible_unit_stored_in_decision_journal": False,
            "lineage_payload_policy": (
                "FULL_SECRET_REDACTED_VISIBLE_UNIT"
                if included
                else "MINIMAL_EXCLUSION_RECEIPT"
            ),
            "decided_at": occurred_at,
        }
        decision = {
            **core,
            "decision_sha256": sha256_bytes(canonical_json_bytes(core)),
        }
        if matches:
            require(
                matches[0] == decision,
                "CAPTURE_DECISION_EVENT_ID_CONFLICT",
                "The event ID already has a different capture decision.",
                status="BLOCKED",
                event_id=event_id,
            )
            return matches[0]
        decisions.append(decision)
        atomic_write_bytes(
            self.decision_path,
            b"".join(canonical_json_bytes(item) for item in decisions),
        )
        return decision

    @staticmethod
    def exclusion_payload(decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": CAPTURE_DECISION_SCHEMA,
            "decision_id": decision["decision_id"],
            "decision_sha256": decision["decision_sha256"],
            "project_id": decision["project_id"],
            "capture_route": decision["capture_route"],
            "capture_kind": decision["capture_kind"],
            "included": False,
            "reason_code": decision["reason_code"],
            "source_event_type": decision["source_event_type"],
            "visible_unit_sha256": decision["visible_unit_sha256"],
            "raw_visible_unit_stored": False,
            "private_reasoning_stored": False,
        }
