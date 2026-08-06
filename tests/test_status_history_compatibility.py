from __future__ import annotations

import copy
from pathlib import Path

import evidence_lane_plugin.service as service_module
import pytest
from evidence_lane_plugin.errors import EvidenceLaneError

from .conftest import build_and_approve_pv1


def _accept_pv2(service) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Seal one unchanged fixture as the current authority.",
        permitted_paths=[],
        permitted_tools=["repository_read", "test"],
        acceptance_checks=["source remains unchanged"],
        stop_condition="Stop at the fresh unaccepted HIL.",
    )
    service.complete_task_and_refresh(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_pv2_status_history",
    )
    assert decision["pointer"]["accepted_pv"] == "PV2"


def test_status_keeps_historical_evidence_without_requalifying_it(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_pv2(service)
    real_validate = service_module.validate_pv_package
    calls: list[tuple[str, bool]] = []

    def historical_contract_probe(
        directory: str | Path,
        *,
        require_promotable: bool = True,
    ) -> dict:
        pv_id = Path(directory).name
        calls.append((pv_id, require_promotable))
        if pv_id == "PV1" and require_promotable:
            raise EvidenceLaneError(
                "PV_LANE_BUNDLE_INVALID",
                "Historical topology must not be requalified as current.",
                status="FAIL",
            )
        result = real_validate(
            directory,
            require_promotable=require_promotable,
        )
        if pv_id == "PV1":
            result = copy.deepcopy(result)
            result["promotable"] = False
            result["lanes"]["status"] = "FAIL"
            result["lanes"]["valid"] = False
        return result

    monkeypatch.setattr(
        service_module,
        "validate_pv_package",
        historical_contract_probe,
    )
    status = service.status("book-faires")
    assert calls == [("PV1", False), ("PV2", False)]
    history = {row["pv_id"]: row for row in status["accepted_history"]}
    assert history["PV1"] == {
        "pv_id": "PV1",
        "manifest_sha256": history["PV1"]["manifest_sha256"],
        "package_sha256": history["PV1"]["package_sha256"],
        "current": False,
        "validation_scope": "HISTORICAL_EVIDENCE",
        "integrity_validated": True,
        "promotability_required": False,
        "promotability_enforced": False,
        "promotable": False,
        "promotable_under_current_rules": False,
        "lane_topology_status": "FAIL",
        "lane_topology_valid": False,
        "historical_compatibility_path": True,
        "successor_candidate_must_pass_current_rules": True,
    }
    assert history["PV2"]["current"] is True
    assert history["PV2"]["validation_scope"] == "ACCEPTED_IMMUTABLE_AUTHORITY"
    assert history["PV2"]["promotability_required"] is False
    assert history["PV2"]["promotability_enforced"] is False
    assert history["PV2"]["promotable"] is True
    assert history["PV2"]["promotable_under_current_rules"] is True
    assert history["PV2"]["lane_topology_valid"] is True
    assert history["PV2"]["historical_compatibility_path"] is False
    assert history["PV2"]["successor_candidate_must_pass_current_rules"] is True

    def preserve_integrity_valid_current_authority(
        directory: str | Path,
        *,
        require_promotable: bool = True,
    ) -> dict:
        result = real_validate(
            directory,
            require_promotable=require_promotable,
        )
        if Path(directory).name == "PV2":
            assert require_promotable is False
            result = copy.deepcopy(result)
            result["promotable"] = False
            result["lanes"]["status"] = "FAIL"
            result["lanes"]["valid"] = False
        return result

    monkeypatch.setattr(
        service_module,
        "validate_pv_package",
        preserve_integrity_valid_current_authority,
    )
    compatibility_status = service.status("book-faires")
    current = next(
        row for row in compatibility_status["accepted_history"] if row["current"]
    )
    assert current["integrity_validated"] is True
    assert current["promotable_under_current_rules"] is False
    assert current["historical_compatibility_path"] is True
    assert current["successor_candidate_must_pass_current_rules"] is True

    monkeypatch.setattr(
        service_module,
        "validate_pv_package",
        real_validate,
    )
    historical_entry = (
        service.store.accepted_path("book-faires", "PV1") / "entry_slip.json"
    )
    historical_entry.write_text(
        historical_entry.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvidenceLaneError) as tampered_history:
        service.status("book-faires")
    assert tampered_history.value.code == "PV_CHECKSUM_MISMATCH"
