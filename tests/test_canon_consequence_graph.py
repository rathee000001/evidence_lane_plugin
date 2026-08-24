from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.canon_consequence_graph import (
    bootstrap_canon_consequence_graph,
    inspect_canon_consequence_graph,
)
from evidence_lane_plugin.canon_task_graph import inspect_canon_authority
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
)
from evidence_lane_plugin.internal_sdk import (
    SDKBinding,
    SDKCancellationToken,
    SDKInvocationContext,
    build_local_service_adapter,
)
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS

PROJECT_ID = "canon-consequence-fixture"
TASK_UUID = "01a00c90-f0d7-7b53-b94b-fa78c3665831"
ACTIVE_TASK = "EL-CODEX-CANON-CONSEQUENCE-ACTIVE-001"


def _hash(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _root(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / PROJECT_ID
    root.mkdir()
    accepted_manifest_sha256 = _hash("accepted-manifest")
    sectors = root / "sectors"
    for lane_id in CANONICAL_LANE_IDS:
        lane_root = sectors / lane_id
        lane_root.mkdir(parents=True)
        atomic_write_json(
            lane_root / "authority.ref.json",
            {
                "schema": "evidence-lane.project-sector-reference.v1",
                "lane_id": lane_id,
                "state": "SCHEMA_READY_UNPOPULATED",
                "accepted_pv": "PV12",
                "lane_schema_contract_sha256": _hash(f"lane:{lane_id}"),
                "artifact_contract_sha256": _hash(f"artifact:{lane_id}"),
                "empty_lane_payload_fabricated": False,
            },
        )
    layout_body = {
        "schema": "evidence-lane.project-authority-layout.v1",
        "project_id": PROJECT_ID,
        "ordered_lane_ids": list(CANONICAL_LANE_IDS),
        "accepted_pointer": {
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "moved": False,
        },
    }
    atomic_write_json(
        root / "project_authority.json",
        {
            **layout_body,
            "layout_sha256": sha256_bytes(canonical_json_bytes(layout_body)),
        },
    )
    _plan(root)
    _learning(root)
    inspect_canon_authority(root, project_id=PROJECT_ID)
    return root, accepted_manifest_sha256


def _plan(root: Path) -> None:
    connection = sqlite3.connect(root / "plan_runtime_projection.sqlite")
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE delta_task(task_id TEXT PRIMARY KEY);
        CREATE TABLE plan_execution_row(
            task_id TEXT PRIMARY KEY REFERENCES delta_task(task_id),
            plan_sequence INTEGER NOT NULL UNIQUE,
            projection_lane TEXT NOT NULL,
            row_number INTEGER UNIQUE,
            history_number INTEGER UNIQUE,
            lifecycle_status TEXT NOT NULL,
            panel_role TEXT NOT NULL,
            plan_group TEXT NOT NULL,
            commit_batch_id TEXT NOT NULL,
            dependencies_json TEXT NOT NULL,
            effective_for_execution INTEGER NOT NULL,
            task_contract_sha256 TEXT NOT NULL,
            linked_delta_ids_json TEXT NOT NULL
        );
        CREATE TABLE steer_delta(
            sequence INTEGER PRIMARY KEY,
            task_steer_sequence INTEGER NOT NULL,
            task_id TEXT NOT NULL REFERENCES delta_task(task_id),
            delta_id TEXT NOT NULL UNIQUE,
            delta_sha256 TEXT NOT NULL,
            boundary TEXT NOT NULL,
            classification TEXT NOT NULL,
            linked_task_id TEXT NOT NULL REFERENCES delta_task(task_id)
        );
        """
    )
    task_ids = ("EL-CODEX-CANON-CONSEQUENCE-DONE-001", ACTIVE_TASK)
    connection.executemany(
        "INSERT INTO delta_task(task_id) VALUES(?)", ((item,) for item in task_ids)
    )
    connection.executemany(
        """
        INSERT INTO plan_execution_row(
            task_id,plan_sequence,projection_lane,row_number,history_number,
            lifecycle_status,panel_role,plan_group,commit_batch_id,
            dependencies_json,effective_for_execution,task_contract_sha256,
            linked_delta_ids_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            (
                task_ids[0],
                1,
                "EXECUTABLE",
                240,
                None,
                "DONE",
                "DELTA",
                "CANON",
                "PV13",
                "[]",
                1,
                _hash("done-contract"),
                "[]",
            ),
            (
                task_ids[1],
                2,
                "EXECUTABLE",
                241,
                None,
                "ACTIVE",
                "DELTA",
                "CANON",
                "PV13",
                json.dumps([task_ids[0]]),
                1,
                _hash("active-contract"),
                json.dumps(["steer-canon-001"]),
            ),
        ),
    )
    connection.execute(
        """
        INSERT INTO steer_delta(
            sequence,task_steer_sequence,task_id,delta_id,delta_sha256,
            boundary,classification,linked_task_id
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            1,
            1,
            task_ids[1],
            "steer-canon-001",
            _hash("steer"),
            "CURRENT_DELTA",
            "IMPLEMENTATION_STEER",
            task_ids[1],
        ),
    )
    connection.commit()
    connection.close()


def _learning(root: Path) -> None:
    path = root / "ai_learning" / "agent-learning.sqlite"
    path.parent.mkdir()
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE learning_candidate(
            candidate_id TEXT PRIMARY KEY,
            candidate_sha256 TEXT NOT NULL UNIQUE,
            tier TEXT NOT NULL,
            lesson_type TEXT NOT NULL,
            candidate_json TEXT NOT NULL
        ) STRICT;
        CREATE TABLE learning_event(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL REFERENCES learning_candidate(candidate_id),
            lifecycle_state TEXT NOT NULL
        ) STRICT;
        """
    )
    candidate = {
        "scope": {"kind": "DELTA_TASK", "selectors": [ACTIVE_TASK]},
        "statement": "The Canon graph remains bounded by the active Plan task.",
    }
    connection.execute(
        "INSERT INTO learning_candidate VALUES(?,?,?,?,?)",
        (
            "learning-candidate-001",
            _hash("learning-candidate"),
            "PROJECT",
            "DELTA_LEARNING",
            canonical_json_bytes(candidate).decode("utf-8"),
        ),
    )
    connection.execute(
        "INSERT INTO learning_event(candidate_id,lifecycle_state) VALUES(?,?)",
        ("learning-candidate-001", "PENDING_LEARNING_HIL"),
    )
    connection.commit()
    connection.close()


def _bootstrap(root: Path, accepted_manifest_sha256: str) -> dict:
    return bootstrap_canon_consequence_graph(
        root,
        project_id=PROJECT_ID,
        accepted_pv="PV12",
        pointer_generation=12,
        accepted_manifest_sha256=accepted_manifest_sha256,
        host_task_uuid=TASK_UUID,
        host_task_deep_link=f"codex://threads/{TASK_UUID}",
        active_plan_task_id=ACTIVE_TASK,
        lineage_head_sha256=_hash("lineage-head"),
    )


class _Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def project_root(self, project_id: str) -> Path:
        assert project_id == PROJECT_ID
        return self.root


class _Service:
    def __init__(self, root: Path) -> None:
        self.store = _Store(root)


def _binding(accepted_manifest_sha256: str) -> SDKBinding:
    return SDKBinding.from_dict(
        {
            "project_id": PROJECT_ID,
            "session_id": "session-canon-consequence",
            "task_id": ACTIVE_TASK,
            "accepted_pv": "PV12",
            "pointer_generation": 12,
            "accepted_manifest_sha256": accepted_manifest_sha256,
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
            "write_scope": ["canon_input:bootstrap_consequence_graph"],
        }
    )


def _context() -> SDKInvocationContext:
    return SDKInvocationContext(
        request_id="canon-consequence-test",
        timeout_ms=5_000,
        started_monotonic=time.monotonic(),
        cancellation=SDKCancellationToken(),
    )


def test_consequence_graph_bootstrap_refreshes_one_live_folder_and_is_replay_safe(
    tmp_path: Path,
) -> None:
    root, accepted_manifest_sha256 = _root(tmp_path)

    created = _bootstrap(root, accepted_manifest_sha256)
    replay = _bootstrap(root, accepted_manifest_sha256)
    inspected = inspect_canon_consequence_graph(root, project_id=PROJECT_ID)

    assert created["state"] == "CONSEQUENCE_GRAPH_CREATED"
    assert created["authority_effects"]["canon_input"] == (
        "CONSEQUENCE_GRAPH_REFRESHED"
    )
    assert replay["state"] == "CONSEQUENCE_GRAPH_REUSED"
    assert replay["authority_effects"]["canon_input"] == "NONE"
    assert inspected["state"] == "CURRENT_CONSEQUENCE_GRAPH"
    assert inspected["authority_effects"]["canon_input"] == "NONE"
    assert created["receipt_sha256"] == replay["receipt_sha256"]
    assert created["graph_sha256"] == replay["graph_sha256"]
    assert created["node_kinds"]["PROJECT_SECTOR"] == 18
    assert created["node_kinds"]["LEARNING_CANDIDATE"] == 1
    assert created["edge_relations"]["HOST_TASK_EXECUTES_ACTIVE_PLAN_TASK"] == 1
    assert created["edge_relations"]["LEARNING_DERIVED_FROM_PLAN_TASK"] == 1
    assert created["edge_relations"]["DEPENDS_ON"] == 1
    graph_members = list((root / "canon" / "consequence-graphs").iterdir())
    assert {path.name for path in graph_members} == {
        "graph.sqlite",
        "graph.mmd",
        "graph.dot",
        "manifest.json",
        "receipt.json",
    }
    assert not any(path.is_dir() for path in graph_members)
    assert created["project_candidate_created"] is False
    assert created["project_hil_invoked"] is False
    assert created["canon_hil_invoked"] is False
    assert created["project_truth_pointer_moved"] is False
    assert created["ordinary_task_approval_inferred"] is False

    bundle = root / created["bundle_locator"].split(f"{PROJECT_ID}/", maxsplit=1)[1]
    connection = sqlite3.connect(
        f"file:{(bundle / 'graph.sqlite').as_posix()}?mode=ro", uri=True
    )
    relations = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT relation,COUNT(*) FROM consequence_edge GROUP BY relation"
        )
    }
    connection.close()
    assert relations["HAS_PROJECT_SECTOR"] == 18
    assert relations["BOUNDED_BY_ACCEPTED_PV"] >= 20


def test_consequence_graph_accepts_exact_live_working_sector_references(
    tmp_path: Path,
) -> None:
    root, accepted_manifest_sha256 = _root(tmp_path)
    working_identity = _hash("working-identity")
    for lane_id in CANONICAL_LANE_IDS:
        atomic_write_json(
            root / "sectors" / lane_id / "authority.ref.json",
            {
                "schema": "evidence-lane.working-sector-authority.v1",
                "state": "LIVE_WORKING",
                "project_id": PROJECT_ID,
                "lane_id": lane_id,
                "historical_parent_pv": "PV12",
                "pointer_generation": 12,
                "working_identity_sha256": working_identity,
                "candidate_directory_created": False,
                "pointer_moved": False,
            },
        )

    result = _bootstrap(root, accepted_manifest_sha256)

    assert result["status"] == "PASS"
    assert result["node_kinds"]["PROJECT_SECTOR"] == 18
    assert result["project_candidate_created"] is False
    assert result["project_truth_pointer_moved"] is False


def test_consequence_graph_fails_closed_on_active_task_or_pointer_mismatch(
    tmp_path: Path,
) -> None:
    root, accepted_manifest_sha256 = _root(tmp_path)

    with pytest.raises(EvidenceLaneError) as wrong_task:
        bootstrap_canon_consequence_graph(
            root,
            project_id=PROJECT_ID,
            accepted_pv="PV12",
            pointer_generation=12,
            accepted_manifest_sha256=accepted_manifest_sha256,
            host_task_uuid=TASK_UUID,
            host_task_deep_link=f"codex://threads/{TASK_UUID}",
            active_plan_task_id="EL-CODEX-WRONG-TASK",
            lineage_head_sha256=_hash("lineage-head"),
        )
    assert wrong_task.value.code == "CANON_CONSEQUENCE_ACTIVE_PLAN_TASK_MISMATCH"

    with pytest.raises(EvidenceLaneError) as wrong_pointer:
        bootstrap_canon_consequence_graph(
            root,
            project_id=PROJECT_ID,
            accepted_pv="PV12",
            pointer_generation=13,
            accepted_manifest_sha256=accepted_manifest_sha256,
            host_task_uuid=TASK_UUID,
            host_task_deep_link=f"codex://threads/{TASK_UUID}",
            active_plan_task_id=ACTIVE_TASK,
            lineage_head_sha256=_hash("lineage-head"),
        )
    assert wrong_pointer.value.code == "CANON_CONSEQUENCE_POINTER_BINDING_MISMATCH"


def test_private_sdk_bootstrap_and_graph_read_keep_canon_ownership(
    tmp_path: Path,
) -> None:
    root, accepted_manifest_sha256 = _root(tmp_path)
    binding = _binding(accepted_manifest_sha256)
    adapter = build_local_service_adapter(
        _Service(root), runtime_binding=binding.as_dict()
    )

    created = adapter.invoke(
        "canon_input",
        "bootstrap_consequence_graph",
        binding,
        {},
        _context(),
    )
    graph = adapter.invoke("canon_input", "graph", binding, {}, _context())

    assert created["status"] == "PASS"
    assert created["authority_effects"] == {
        "project_truth": "NONE",
        "canon_input": "CONSEQUENCE_GRAPH_REFRESHED",
        "agent_learning": "NONE",
        "chat_lineage": "NONE",
        "host_entry_continuity": "NONE",
    }
    assert graph["authority_effects"]["canon_input"] == "NONE"
    assert graph["consequence_graph"]["state"] == "CURRENT_CONSEQUENCE_GRAPH"
    assert graph["consequence_graph"]["full_graph_loaded_into_model_context"] is False
