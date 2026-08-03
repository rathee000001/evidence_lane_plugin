from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_optional import probe_git_arm
from evidence_lane_plugin.hil_intent import classify_hil_intent
from evidence_lane_plugin.lane_engine import build_lane_bundle
from evidence_lane_plugin.next_actions import boot_next_action
from evidence_lane_plugin.source_intake import classify_source_intake

from .conftest import boot_local


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_optional_git_arm_falls_back_for_plain_directory(tmp_path: Path) -> None:
    source = tmp_path / "plain-evidence"
    source.mkdir()
    (source / "notes.md").write_text("independent evidence\n", encoding="utf-8")
    receipt = probe_git_arm(source, requested_mode="AUTO")
    assert receipt["state"] == "NOT_A_GIT_WORKTREE_FALLBACK"
    assert receipt["history_index_enabled"] is False
    assert receipt["fallback_content_index_enabled"] is True
    result = classify_source_intake(
        [str(source)], code_mode="local_code", git_mode="AUTO"
    )
    assert result["sources"][0]["canonical_lane_id"] == "project_engulf"
    assert result["sources"][0]["git_optional_arm"]["state"] == (
        "NOT_A_GIT_WORKTREE_FALLBACK"
    )
    identity = result["sources"][0]["source_identity"]
    assert identity["identity_scope"] == "MEMBER_PATHS_AND_SIZES_ONLY"
    assert identity["content_bytes_hashed"] is False
    assert identity["content_identity_proven"] is False
    assert "member_path_size_sha256" in identity


def test_required_git_arm_rejects_plain_directory(tmp_path: Path) -> None:
    source = tmp_path / "plain-evidence"
    source.mkdir()
    with pytest.raises(EvidenceLaneError) as blocked:
        probe_git_arm(source, requested_mode="REQUIRED")
    assert blocked.value.code == "GIT_ARM_REQUIRED_WORKTREE_MISSING"


def test_optional_git_arm_detects_linked_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    linked = tmp_path / "linked"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Evidence Lane Test")
    _git(repository, "config", "user.email", "evidence-lane@example.invalid")
    (repository / "README.md").write_text("# Evidence Lane\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")
    _git(repository, "worktree", "add", "-b", "linked-test", str(linked))
    assert (linked / ".git").is_file()
    receipt = probe_git_arm(linked, requested_mode="AUTO")
    assert receipt["state"] == "ENABLED"
    assert receipt["history_index_enabled"] is True
    assert receipt["head_commit"] == _git(linked, "rev-parse", "HEAD")
    assert receipt["head_tree"] == _git(linked, "rev-parse", "HEAD^{tree}")
    assert receipt["branch"] == "linked-test"
    assert receipt["detached_head"] is False
    assert receipt["worktree_clean"] is True
    assert receipt["remote_identity_included"] is False


def test_directory_path_size_identity_never_claims_content_hash(
    tmp_path: Path,
) -> None:
    source = tmp_path / "extracted-source"
    source.mkdir()
    payload = source / "module.py"
    payload.write_text("alpha\n", encoding="utf-8")
    first = classify_source_intake([str(source)], code_mode="local_code")
    first_identity = first["sources"][0]["source_identity"]
    payload.write_text("bravo\n", encoding="utf-8")
    same_size = classify_source_intake([str(source)], code_mode="local_code")
    same_size_identity = same_size["sources"][0]["source_identity"]
    assert (
        same_size_identity["member_path_size_sha256"]
        == first_identity["member_path_size_sha256"]
    )
    assert same_size_identity["content_identity_proven"] is False
    payload.write_text("longer-content\n", encoding="utf-8")
    changed_size = classify_source_intake([str(source)], code_mode="local_code")
    assert (
        changed_size["sources"][0]["source_identity"]["member_path_size_sha256"]
        != first_identity["member_path_size_sha256"]
    )


def test_archive_profile_separates_package_format_from_generator_identity(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "SQLite-Brain-Builder-V5.9-output.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "project/manifest.json",
            json.dumps(
                {
                    "package_type": (
                        "EvidenceOS_V2_FULL_VERTICAL_project_mini_brain"
                    )
                }
            ),
        )
        archive.writestr("project/sql/mini_brain_graph.sqlite", b"not-opened")
    result = classify_source_intake([str(archive_path)], code_mode="local_code")
    source = result["sources"][0]
    profile = source["archive_profile"]
    assert source["canonical_lane_id"] == "brain_loader"
    assert profile["package_format"] == (
        "EVIDENCEOS_V2_FULL_VERTICAL_MINI_BRAIN"
    )
    assert profile["generator_identity_status"] == "UNPROVEN_BY_ARCHIVE"
    assert profile["filename_used_as_generator_evidence"] is False
    assert profile["declared_generator_versions"] == []


def test_archive_profile_detects_uepc_sector_layout_without_version_inference(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "sector-package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("env/env_sqlite.sqlite", b"environment")
        archive.writestr(
            "project/sectors/local_code/local_code_sector_v001.sqlite", b"sector"
        )
        archive.writestr(
            "manifests/manifest.json", json.dumps({"schema": "uepc-sector.v1"})
        )
    result = classify_source_intake([str(archive_path)], code_mode="local_code")
    profile = result["sources"][0]["archive_profile"]
    assert profile["package_format"] == "UEPC_SECTOR_PACKAGE"
    assert profile["sqlite_member_count"] == 2
    assert profile["generator_identity_status"] == "UNPROVEN_BY_ARCHIVE"


def test_lane_bundle_obeys_explicit_optional_git_arm(
    tmp_path: Path, source_repository: Path
) -> None:
    disabled = build_lane_bundle(
        repository_root=source_repository,
        output_directory=tmp_path / "disabled-bundle",
        code_mode="github_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV1",
        pointer_generation=0,
        git_mode="DISABLED",
    )
    assert disabled["git_optional_arm"]["state"] == "DISABLED_BY_USER"
    assert disabled["summary"]["git_history"] is None
    enabled = build_lane_bundle(
        repository_root=source_repository,
        output_directory=tmp_path / "enabled-bundle",
        code_mode="github_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV1",
        pointer_generation=0,
        git_mode="AUTO",
    )
    assert enabled["git_optional_arm"]["state"] == "ENABLED"
    assert enabled["summary"]["git_history"]["status"] == "PASS"


@pytest.mark.parametrize(
    ("utterance", "intent", "route"),
    [
        ("purse same hill", "CONTINUE_SAME_HIL", None),
        ("I accepted it, continue", "APPROVAL_INTENT_REQUIRES_EXACT_TOKEN", None),
        ("APPROVE", "EXACT_APPROVE_READY", "pv_fuse"),
        ("/evi-build APPROVE and install it", "EXACT_APPROVE_READY", "pv_fuse"),
    ],
)
def test_hil_intent_is_tolerant_but_never_promotes(
    utterance: str, intent: str, route: str | None
) -> None:
    result = classify_hil_intent(
        utterance,
        candidate_id="PV2_CANDIDATE__RUN_TEST",
        pending_hil=True,
    )
    assert result["intent"] == intent
    assert result["tool_route"] == route
    assert result["candidate_promoted"] is False
    assert result["pointer_moved"] is False
    assert result["automatic_acceptance"] is False


def test_boot_contract_never_auto_selects_state_travel() -> None:
    contract = boot_next_action(entry_action="SESSION_RESUMED")
    assert contract["state_travel_conditional_first"] is None
    assert contract["state_travel_available_command"] == "/evi-state-travel"
    assert contract["state_travel_eligibility_is_not_invocation"] is True
    assert contract["state_travel_auto_selected"] is False
    assert contract["state_travel_allowed_triggers"] == [
        "EXPLICIT_USER_REQUEST",
        "GENUINE_HOST_CONTEXT_EXHAUSTION",
    ]


def test_explicit_same_host_continuation_preserves_and_supersedes_handoff(
    service,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    fused = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_v070_same_host",
    )
    handoff = fused["state_travel_handoff"]["state_travel"]
    pointer_before = service.store.pointer("book-faires").as_dict()

    with pytest.raises(EvidenceLaneError) as still_blocked:
        service.sessions.begin_next_turn("book-faires", session_id)
    assert still_blocked.value.code == "STATE_TRAVEL_RESUME_REQUIRED"

    entry = service.sessions.begin_next_turn(
        "book-faires",
        session_id,
        continue_same_host=True,
        continuation_reason="EXPLICIT_USER_CONTINUATION",
    )
    pointer_after = service.store.pointer("book-faires").as_dict()
    assert pointer_before == pointer_after
    assert entry["session"]["state"] == "PVN1_ENTRY"
    disposition = entry["state_travel_disposition"]
    assert disposition["status"] == "SUPERSEDED_BY_SAME_HOST_CONTINUATION"
    assert disposition["handoff_id"] == handoff["handoff_id"]
    assert disposition["handoff_sha256"] == handoff["handoff_sha256"]
    assert disposition["state_travel_consumed"] is False
    persisted = service.sessions.load("book-faires", session_id)
    assert persisted.metadata["state_travel_history"][-1] == handoff
    assert persisted.metadata["state_travel"]["status"] == (
        "SUPERSEDED_BY_SAME_HOST_CONTINUATION"
    )
    lineage_path = (
        service.store.project_root("book-faires")
        / "lineage"
        / f"{session_id}.jsonl"
    )
    events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        row["event_type"] == "pv.state_travel.superseded_same_host"
        for row in events
    )
    assert not any(row["event_type"] == "pv.state_travel.verified" for row in events)


def test_hil_intent_appends_visible_lineage_without_pointer_move(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    before = service.store.pointer("book-faires").as_dict()
    result = service.classify_hil_intent(
        "book-faires",
        session_id,
        "pursue same HIL",
        event_id="evt_hil_intent_same_candidate",
    )
    after = service.store.pointer("book-faires").as_dict()
    assert result["intent"] == "CONTINUE_SAME_HIL"
    assert before == after
    lineage = service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    events = [json.loads(line) for line in lineage.read_text(encoding="utf-8").splitlines()]
    event = next(row for row in events if row["event_id"] == "evt_hil_intent_same_candidate")
    assert event["event_type"] == "hil.intent.classified"
    assert event["visible_payload"]["visible_utterance"] == "pursue same HIL"
    assert event["private_reasoning_stored"] is False


def test_mid_turn_steers_append_in_order_and_deduplicate(
    service, source_repository: Path
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "prompt_submit.py"
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)

    def submit(prompt: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps(
                {
                    "session_id": "host-session-test",
                    "turn_id": "turn-with-steers",
                    "cwd": str(source_repository),
                    "prompt": prompt,
                    "source": "mid_turn_steer",
                    "is_steer": True,
                }
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        payload = json.loads(completed.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        return json.loads(context.removeprefix("EVIDENCE_LANE_PROMPT_ENTRY="))

    first = submit("keep the same HIL")
    second = submit("add optional Git evidence")
    repeated = submit("add optional Git evidence")
    assert first["state"] == "INDEXED"
    assert second["state"] == "INDEXED"
    assert repeated["state"] == "INDEXED_IDEMPOTENT_REUSE"
    assert [first["prompt_index"], second["prompt_index"]] == [1, 2]
    records = list((service.store.root / "prompt-index").rglob("*.json"))
    assert len(records) == 2
    lineage_path = (
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    )
    events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    steers = [row for row in events if row["event_type"] == "turn.visible_user_steer"]
    assert len(steers) == 2
    assert [row["visible_payload"]["prompt_index"] for row in steers] == [1, 2]
    assert all(row["actor_type"] == "user" for row in steers)
    assert all(row["private_reasoning_stored"] is False for row in steers)
