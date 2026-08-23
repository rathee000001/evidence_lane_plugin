from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs" / "CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.json"
OUTPUT_PATHS = {
    "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.json",
    "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.md",
}


def _tracked_paths() -> set[str]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return {item.decode("utf-8") for item in raw.split(b"\0") if item}


def _indexed_file_bytes() -> dict[str, bytes]:
    tree = subprocess.run(
        ["git", "write-tree"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    archive = subprocess.run(
        ["git", "archive", "--format=tar", tree],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        return {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile() and bundle.extractfile(member) is not None
        }


def test_current_route_refresh_receipt_covers_and_hashes_every_tracked_path() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert receipt["schema"] == "evidence-lane.current-route-file-refresh-receipt.v1"
    assert receipt["status"] == "PASS"
    rows = {row["path"]: row for row in receipt["entries"]}
    assert set(rows) - {"TASK6_ROW231_CONTRACT_REBIND_AUTHORITY.json"} == (
        _tracked_paths() - OUTPUT_PATHS
    )
    removed = rows["TASK6_ROW231_CONTRACT_REBIND_AUTHORITY.json"]
    assert removed["disposition"] == "REMOVED"
    assert not (ROOT / removed["path"]).exists()
    indexed = _indexed_file_bytes()
    for path, row in rows.items():
        if row["disposition"] == "REMOVED":
            continue
        source = ROOT / path
        assert source.is_file(), path
        assert hashlib.sha256(indexed[path]).hexdigest().upper() == row["sha256"]


def test_current_route_refresh_receipt_binds_current_public_contract() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    route = receipt["current_route"]
    assert route["runtime_catalog"]["tools"] == 88
    assert route["runtime_catalog"]["read"] == 27
    assert route["runtime_catalog"]["write"] == 61
    assert route["runtime_catalog"]["skills"] == 17
    assert len(route["hook_events"]) == 11
    assert route["direct_state_travel_fields"] == [
        "project_id",
        "session_id",
        "authoritative_source_task_id",
        "runtime_attachment_donor_task_id",
        "destination_task_id",
        "destination_task_title",
    ]
    assert route["github_app_commit_actor"] == "evidence-lane[bot]"
    assert route["main_live_work_allowed"] is False
