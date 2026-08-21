from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes


def test_v41_top_level_receipt_self_seals_are_current() -> None:
    receipt_root = (
        Path(__file__).resolve().parents[1] / "evidence" / "implementation_v41"
    )
    sealed_receipts: list[str] = []
    for path in sorted(receipt_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or "receipt_sha256" not in payload:
            continue
        declared = str(payload.pop("receipt_sha256"))
        computed = sha256_bytes(canonical_json_bytes(payload))
        assert declared == computed, f"stale top-level receipt self-seal: {path.name}"
        sealed_receipts.append(path.name)

    assert {
        "DELTA079B_WINDOWS_TUNNEL_PERSISTENCE_RECEIPT.json",
        "DELTA079CD_HOST_STORAGE_ENV_MODE_CONTINUITY_RECEIPT.json",
        "DELTA080_PRE_PV6_RELEASE_GATE_RECEIPT.json",
    }.issubset(sealed_receipts)
    assert len(sealed_receipts) >= 11
