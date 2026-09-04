from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.agent_learning import (
    inspect_learning_authority,
)
from evidence_lane_plugin.agent_learning import (
    record_memory_link as record_legacy_memory_link,
)
from evidence_lane_plugin.codex_turn_control import (
    _rehydrate_compact_project_memory,
    _seal_compact_project_memory,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import atomic_write_json, sha256_bytes, sha256_file
from evidence_lane_plugin.internal_sdk import (
    InternalEvidenceLaneSDK,
    RegisteredSDKAdapter,
    SDKBinding,
)
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
from evidence_lane_plugin.project_memory import (
    bootstrap_project_memory,
    inspect_project_memory,
    query_memory_graph,
    record_memory_link,
    rehydrate_memory_checkpoint,
    seal_memory_checkpoint,
)

PROJECT_ID = "project-memory-fixture"
TASK_ID = "EL-CODEX-T8-MEMORY-BOUNDED-ROUTING-COMPACTION-DELTA-001"
TASK_UUID = "01a00c90-f0d7-7b53-b94b-fa78c3665831"
T0 = "2026-08-20T12:00:00+00:00"
T1 = "2026-08-20T13:00:00+00:00"
T2 = "2026-08-20T14:00:00+00:00"
T3 = "2026-08-20T15:00:00+00:00"


def _hash(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _locator(
    *,
    sector: str,
    kind: str,
    value: str,
    revision: str,
    label: str,
    terms: list[str],
) -> dict:
    return {
        "sector": sector,
        "locator_kind": kind,
        "locator_value": value,
        "revision_sha256": _hash(revision),
        "label": label,
        "search_terms": terms,
    }


def _root(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    atomic_write_json(
        root / "project.json",
        {
            "schema": "evidence-lane.project-registry.v1",
            "project_id": PROJECT_ID,
        },
    )
    accepted_manifest = _hash("accepted-manifest")
    layout = {
        "schema": "evidence-lane.project-authority-layout.v1",
        "project_id": PROJECT_ID,
        "ordered_lane_ids": list(CANONICAL_LANE_IDS),
        "accepted_pointer": {
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": accepted_manifest,
            "moved": False,
        },
    }
    atomic_write_json(root / "project_authority.json", layout)
    atomic_write_json(
        root / "active_pointer.json",
        {
            "schema": "evidence-lane.pointer.v1",
            "project_id": PROJECT_ID,
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": accepted_manifest,
        },
    )
    for lane_id in CANONICAL_LANE_IDS:
        lane_root = root / "sectors" / lane_id
        lane_root.mkdir(parents=True)
        atomic_write_json(
            lane_root / "authority.ref.json",
            {
                "schema": "evidence-lane.project-sector-reference.v1",
                "lane_id": lane_id,
                "state": "SCHEMA_READY_UNPOPULATED",
                "accepted_pv": "PV12",
            },
        )
    connection = sqlite3.connect(root / "plan_runtime_projection.sqlite")
    connection.executescript(
        """
        CREATE TABLE plan_execution_row(
            task_id TEXT PRIMARY KEY,
            row_number INTEGER NOT NULL,
            lifecycle_status TEXT NOT NULL,
            effective_for_execution INTEGER NOT NULL,
            task_contract_sha256 TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO plan_execution_row VALUES(?,?,?,?,?)",
        (TASK_ID, 242, "ACTIVE", 1, _hash("task-contract")),
    )
    connection.commit()
    connection.close()
    inspect_learning_authority(root, project_id=PROJECT_ID)
    return root, accepted_manifest


def _seed_legacy(root: Path) -> dict:
    return record_legacy_memory_link(
        root,
        project_id=PROJECT_ID,
        source=_locator(
            sector="CHAT_LINEAGE",
            kind="TURN",
            value="chat-lineage://task/task8/turn/corrected",
            revision="corrected-turn",
            label="Retry decision after correction",
            terms=["retry", "continuity", "corrected"],
        ),
        target=_locator(
            sector="CHAT_LINEAGE",
            kind="TURN",
            value="chat-lineage://task/task8/turn/original",
            revision="original-turn",
            label="Retry decision before correction",
            terms=["retry", "continuity", "original"],
        ),
        edge_type="SUPERSEDES",
        evidence_sha256=_hash("turn-supersession"),
        recorded_at=T0,
    )


def _bootstrap(root: Path, accepted_manifest: str) -> dict:
    return bootstrap_project_memory(
        root,
        project_id=PROJECT_ID,
        accepted_pv="PV12",
        pointer_generation=12,
        accepted_manifest_sha256=accepted_manifest,
        active_plan_task_id=TASK_ID,
        lineage_head_sha256=_hash("lineage-head"),
        recorded_at=T1,
    )


def _sdk_binding(accepted_manifest: str) -> SDKBinding:
    return SDKBinding.from_dict(
        {
            "project_id": PROJECT_ID,
            "session_id": "session-project-memory",
            "task_id": TASK_ID,
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "accepted_manifest_sha256": accepted_manifest,
            "lineage_head_sha256": _hash("lineage-head"),
            "env_authority_sha256": _hash("env"),
            "uop_authority_sha256": _hash("uop"),
            "derived_projection_sha256": _hash("projection"),
            "flash_receipt_sha256": _hash("flash"),
            "model": "gpt-5.6-sol",
            "submodel": "sol",
            "reasoning_effort": "xhigh",
            "reasoning_speed": "standard",
            "host_kind": "CODEX_DESKTOP",
            "host_session_id": TASK_UUID,
            "write_scope": [],
        }
    )


def test_memory_bootstrap_migrates_legacy_without_loss_or_duplication(
    tmp_path: Path,
) -> None:
    root, accepted_manifest = _root(tmp_path)
    pointer_before = (root / "active_pointer.json").read_bytes()
    legacy = _seed_legacy(root)

    created = _bootstrap(root, accepted_manifest)
    replay = _bootstrap(root, accepted_manifest)
    replay_later = bootstrap_project_memory(
        root,
        project_id=PROJECT_ID,
        accepted_pv="PV12",
        pointer_generation=12,
        accepted_manifest_sha256=accepted_manifest,
        active_plan_task_id=TASK_ID,
        lineage_head_sha256=_hash("lineage-head"),
        recorded_at=T2,
    )
    inspected = inspect_project_memory(root, project_id=PROJECT_ID)
    queried = query_memory_graph(
        root,
        project_id=PROJECT_ID,
        query="retry continuity",
        as_of=T2,
        sectors=["CHAT_LINEAGE"],
        limit=4,
    )

    assert created["state"] == "MEMORY_AUTHORITY_CREATED"
    assert replay["state"] == "MEMORY_AUTHORITY_REUSED"
    assert created["legacy_migration"]["locator_count"] == 2
    assert created["legacy_migration"]["edge_count"] == 1
    assert replay["legacy_migration"]["inserted_locator_count"] == 0
    assert replay["legacy_migration"]["inserted_edge_count"] == 0
    assert replay_later["inserted_locator_count"] == 0
    assert replay_later["inserted_edge_count"] == 0
    assert created["memory_head_sha256"] == replay["memory_head_sha256"]
    assert inspected["counts"]["locator_count"] >= 24
    assert inspected["counts"]["edge_count"] >= 22
    assert queried["result"] == "HIT"
    assert _hash("corrected-turn") in {
        hit["revision_sha256"] for hit in queried["hits"]
    }
    assert _hash("original-turn") not in {
        hit["revision_sha256"] for hit in queried["hits"]
    }
    assert queried["suppressed"] == [
        {
            "locator_id": legacy["target_locator_id"],
            "state": "SUPPRESSED_MEMORY_LOCATOR",
            "edge_types": ["SUPERSEDES"],
        }
    ]
    assert queried["full_memory_loaded_into_model_context"] is False
    assert queried["raw_database_or_markdown_returned"] is False
    assert (root / "active_pointer.json").read_bytes() == pointer_before
    for name in (
        "memory.sqlite",
        "memory.json",
        "memory.mmd",
        "memory.dot",
        "memory.tools.json",
        "memory.manifest.json",
        "head.json",
    ):
        assert (root / "memory" / name).is_file()


def test_new_memory_writes_do_not_change_legacy_learning_tables(tmp_path: Path) -> None:
    root, accepted_manifest = _root(tmp_path)
    _seed_legacy(root)
    _bootstrap(root, accepted_manifest)
    learning = root / "ai_learning" / "agent-learning.sqlite"
    connection = sqlite3.connect(learning)
    before = (
        connection.execute("SELECT COUNT(*) FROM memory_locator").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM memory_edge").fetchone()[0],
    )
    connection.close()

    result = record_memory_link(
        root,
        project_id=PROJECT_ID,
        source=_locator(
            sector="PLAN",
            kind="TASK",
            value=f"plan://task/{TASK_ID}",
            revision="plan-link-revision",
            label="Active Memory Delta",
            terms=["plan", "memory", "delta"],
        ),
        target=_locator(
            sector="CANON",
            kind="GRAPH",
            value=f"canon://graph/{_hash('canon-graph')}",
            revision="canon-graph",
            label="Canon graph linked by Memory",
            terms=["canon", "graph", "memory"],
        ),
        edge_type="MAPS_TO",
        evidence_sha256=_hash("memory-owned-link"),
        recorded_at=T2,
    )
    replay = record_memory_link(
        root,
        project_id=PROJECT_ID,
        source=_locator(
            sector="PLAN",
            kind="TASK",
            value=f"plan://task/{TASK_ID}",
            revision="plan-link-revision",
            label="Active Memory Delta",
            terms=["plan", "memory", "delta"],
        ),
        target=_locator(
            sector="CANON",
            kind="GRAPH",
            value=f"canon://graph/{_hash('canon-graph')}",
            revision="canon-graph",
            label="Canon graph linked by Memory",
            terms=["canon", "graph", "memory"],
        ),
        edge_type="MAPS_TO",
        evidence_sha256=_hash("memory-owned-link"),
        recorded_at=T3,
    )

    connection = sqlite3.connect(learning)
    after = (
        connection.execute("SELECT COUNT(*) FROM memory_locator").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM memory_edge").fetchone()[0],
    )
    connection.close()
    assert result["authority_effects"]["project_memory"] == "MEMORY_LINK_APPENDED"
    assert result["authority_effects"]["agent_learning"] == "NONE"
    assert replay["state"] == "MEMORY_LINK_REUSED"
    assert replay["inserted_locator_count"] == 0
    assert replay["idempotent_reuse"] is True
    assert after == before


def test_compaction_checkpoint_rehydrates_only_sealed_bounded_locators(
    tmp_path: Path,
) -> None:
    root, accepted_manifest = _root(tmp_path)
    _seed_legacy(root)
    _bootstrap(root, accepted_manifest)

    sealed = seal_memory_checkpoint(
        root,
        project_id=PROJECT_ID,
        host_task_uuid=TASK_UUID,
        host_task_deep_link=f"codex://threads/{TASK_UUID}",
        active_plan_task_id=TASK_ID,
        lineage_head_sha256=_hash("lineage-head"),
        query="active plan memory",
        limit=4,
        sealed_at=T2,
    )
    rehydrated = rehydrate_memory_checkpoint(
        root,
        project_id=PROJECT_ID,
        checkpoint_sha256=sealed["checkpoint_sha256"],
        host_task_uuid=TASK_UUID,
        host_task_deep_link=f"codex://threads/{TASK_UUID}",
        active_plan_task_id=TASK_ID,
        lineage_head_sha256=_hash("lineage-head"),
        rehydrated_at=T3,
    )

    assert sealed["bounded_locator_count"] <= 4
    assert [row["locator_id"] for row in rehydrated["locators"]] == [
        row["locator_id"] for row in sealed["bounded_locators"]
    ]
    assert rehydrated["full_transcript_replayed"] is False
    assert rehydrated["raw_source_payloads_returned"] is False
    assert rehydrated["controls_codex_host_wording"] is False
    with pytest.raises(EvidenceLaneError) as mismatch:
        rehydrate_memory_checkpoint(
            root,
            project_id=PROJECT_ID,
            checkpoint_sha256=sealed["checkpoint_sha256"],
            host_task_uuid=TASK_UUID,
            host_task_deep_link=f"codex://threads/{TASK_UUID}",
            active_plan_task_id=TASK_ID,
            lineage_head_sha256=_hash("different-lineage-head"),
            rehydrated_at=T3,
        )
    assert mismatch.value.code == "MEMORY_REHYDRATION_BINDING_MISMATCH"


def test_memory_manifest_and_schema_are_content_bound(tmp_path: Path) -> None:
    root, accepted_manifest = _root(tmp_path)
    _bootstrap(root, accepted_manifest)
    manifest_path = root / "memory" / "memory.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert (
        manifest["memory_head_sha256"]
        == inspect_project_memory(root, project_id=PROJECT_ID)["memory_head_sha256"]
    )
    assert {member["path"] for member in manifest["members"]} == {
        "memory.sqlite",
        "memory.json",
        "memory.mmd",
        "memory.dot",
        "memory.tools.json",
        "head.json",
    }
    for member in manifest["members"]:
        assert sha256_file(root / "memory" / member["path"]) == member["sha256"]
    connection = sqlite3.connect(root / "memory" / "memory.sqlite")
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_codex_compaction_adapter_uses_first_class_memory_checkpoint(
    tmp_path: Path,
) -> None:
    root, accepted_manifest = _root(tmp_path)
    _bootstrap(root, accepted_manifest)
    compact_context = {
        "project_id": PROJECT_ID,
        "active_authority": {"active_plan_task_id": TASK_ID},
    }

    sealed = _seal_compact_project_memory(
        root,
        host_session_id=TASK_UUID,
        compact_context=compact_context,
    )
    context_with_checkpoint = {
        **compact_context,
        "project_memory_checkpoint": sealed,
    }
    rehydrated = _rehydrate_compact_project_memory(
        root,
        host_session_id=TASK_UUID,
        compact_context=context_with_checkpoint,
    )

    assert sealed["state"] == "MEMORY_CHECKPOINT_SEALED"
    assert sealed["checkpoint_sealed"] is True
    assert sealed["bounded_locator_count"] <= 4
    assert rehydrated["state"] == "MEMORY_CHECKPOINT_REHYDRATED"
    assert rehydrated["checkpoint_sha256"] == sealed["checkpoint_sha256"]
    assert rehydrated["memory_head_sha256"] == sealed["memory_head_sha256"]
    assert rehydrated["controls_codex_host_wording"] is False


def test_internal_sdk_routes_memory_query_through_independent_arm(
    tmp_path: Path,
) -> None:
    root, accepted_manifest = _root(tmp_path)
    _bootstrap(root, accepted_manifest)
    binding = _sdk_binding(accepted_manifest)

    def memory_query(bound, payload, context):
        context.checkpoint()
        assert bound.project_id == PROJECT_ID
        return query_memory_graph(root, project_id=PROJECT_ID, **payload)

    adapter = RegisteredSDKAdapter(
        adapter_id="project-memory-fixture.v1",
        snapshot_provider=lambda supplied: supplied.as_dict(),
        handlers={("project_memory", "query"): memory_query},
    )
    sdk = InternalEvidenceLaneSDK(root, adapter)

    result = sdk.invoke(
        module_id="project_memory",
        operation="query",
        binding=binding,
        payload={"query": "active plan memory", "as_of": T2, "limit": 4},
        request_id="project-memory-query-001",
    )

    assert result["status"] == "PASS"
    assert result["module_id"] == "project_memory"
    assert result["authority"] == "PROJECT_MEMORY"
    assert result["authority_effects"]["project_memory"] == "NONE"
    assert result["data"]["result"] == "HIT"
    assert result["data"]["full_memory_loaded_into_model_context"] is False
