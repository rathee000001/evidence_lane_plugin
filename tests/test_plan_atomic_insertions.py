from __future__ import annotations

import asyncio

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.mcp_server import create_mcp_server


def _task(task_id: str, outcome: str, **metadata: object) -> dict:
    return {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": outcome,
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Verify the bounded result."],
        "stop_condition": "Stop after the bounded result is verified.",
        **metadata,
    }


def _seed_plan(service) -> dict:
    return service.plan_tasks(
        "book-faires",
        tasks=[
            _task("seed-root", "Verify the seed root."),
            _task("seed-anchor", "Verify the insertion anchor."),
            _task(
                "seed-final-hil",
                "Present the physically final six-way HIL.",
                panel_role="PHYSICALLY_FINAL_HIL",
            ),
        ],
        planned_by="human-test",
        plan_id="seed-plan",
    )


def _atomic_contract(service, seed: dict, insertions: list[dict]) -> dict:
    return {
        "batch_id": "atomic-batch-001",
        "research_batch_sha256": "A" * 64,
        "expected_backlog_sha256": sha256_bytes(
            canonical_json_bytes(service.store._load_backlog("book-faires"))
        ),
        "expected_canonical_plan_sha256": seed[
            "canonical_plan_projection"
        ]["projection_sha256"],
        "expected_executable_projection_sha256": seed["goal_projection"][
            "projection_sha256"
        ],
        "expected_physical_final_task_id": "seed-final-hil",
        "insertions": insertions,
    }


def _valid_insertions() -> list[dict]:
    return [
        {
            "insert_before_task_id": "seed-anchor",
            "tasks": [
                _task(
                    "atomic-before-anchor",
                    "Verify the first inserted row.",
                    plan_group="parity-foundation",
                    commit_batch_id="pre-hil-batch",
                    dependencies=["seed-root"],
                )
            ],
        },
        {
            "insert_before_task_id": "seed-final-hil",
            "tasks": [
                _task(
                    "atomic-before-final",
                    "Verify the second inserted row.",
                    plan_group="parity-release",
                    commit_batch_id="pre-hil-batch",
                    dependencies=["seed-anchor", "atomic-before-anchor"],
                    git_commit_stage="NO_COMMIT",
                )
            ],
        },
    ]


def test_atomic_multi_target_plan_insertion_preserves_metadata_and_final_hil(
    service,
) -> None:
    seed = _seed_plan(service)
    atomic = _atomic_contract(service, seed, _valid_insertions())

    result = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="atomic-plan-001",
        atomic_insertion=atomic,
    )

    rows = result["goal_projection"]["rows"]
    assert [row["task_id"] for row in rows] == [
        "seed-root",
        "atomic-before-anchor",
        "seed-anchor",
        "atomic-before-final",
        "seed-final-hil",
    ]
    assert rows[1]["plan_group"] == "parity-foundation"
    assert rows[1]["commit_batch_id"] == "pre-hil-batch"
    assert rows[1]["dependencies"] == ["seed-root"]
    assert rows[3]["dependencies"] == ["seed-anchor", "atomic-before-anchor"]
    assert rows[3]["git_commit_stage"] == "NO_COMMIT"
    assert rows[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert result["tasks"][-1]["task_id"] == "seed-final-hil"
    receipt = result["atomic_insertion_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["group_count"] == 2
    assert receipt["task_count"] == 2
    assert receipt["physical_final_row"] == 5
    assert receipt["pointer_moved"] is False
    assert receipt["candidate_created"] is False
    assert receipt["hil_invoked"] is False
    assert receipt["goal_mutated"] is False
    assert receipt["git_executed"] is False

    replay = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="atomic-plan-001",
        atomic_insertion=atomic,
    )
    assert replay["atomic_insertion_receipt"]["idempotent_replay"] is True
    assert [
        row["task_id"]
        for row in replay["goal_projection"]["rows"]
    ].count("atomic-before-anchor") == 1


@pytest.mark.parametrize(
    "field",
    [
        "expected_backlog_sha256",
        "expected_canonical_plan_sha256",
        "expected_executable_projection_sha256",
    ],
)
def test_atomic_insertion_hash_mismatch_writes_nothing(service, field: str) -> None:
    seed = _seed_plan(service)
    atomic = _atomic_contract(service, seed, _valid_insertions())
    atomic[field] = "0" * 64

    with pytest.raises(EvidenceLaneError) as blocked:
        service.plan_tasks(
            "book-faires",
            tasks=[],
            planned_by="human-test",
            plan_id="atomic-hash-mismatch",
            atomic_insertion=atomic,
        )

    assert blocked.value.code == "PLAN_ATOMIC_INSERTION_AUTHORITY_HASH_MISMATCH"
    backlog = service.task_backlog("book-faires")
    assert [row["task_id"] for row in backlog["goal_projection"]["rows"]] == [
        "seed-root",
        "seed-anchor",
        "seed-final-hil",
    ]
    assert not (
        service.store.project_root("book-faires") / "plan_atomic_insertions"
    ).exists()


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda atomic: atomic["insertions"][0]["tasks"][0].update(
                dependencies=["atomic-before-final"]
            ),
            "PLAN_DEPENDENCY_NOT_EARLIER_EXECUTABLE_ROW",
        ),
        (
            lambda atomic: atomic.update(
                expected_physical_final_task_id="not-the-final-hil"
            ),
            "PLAN_PHYSICAL_FINAL_HIL_MISMATCH",
        ),
    ],
)
def test_atomic_insertion_fails_closed_before_any_write(
    service,
    mutate,
    expected_code: str,
) -> None:
    seed = _seed_plan(service)
    atomic = _atomic_contract(service, seed, _valid_insertions())
    mutate(atomic)

    with pytest.raises(EvidenceLaneError) as blocked:
        service.plan_tasks(
            "book-faires",
            tasks=[],
            planned_by="human-test",
            plan_id="atomic-invalid-plan",
            atomic_insertion=atomic,
        )

    assert blocked.value.code == expected_code
    assert [
        row["task_id"]
        for row in service.task_backlog("book-faires")["goal_projection"]["rows"]
    ] == ["seed-root", "seed-anchor", "seed-final-hil"]


def test_atomic_insertion_recovers_after_canonical_write_before_commit_journal(
    service,
    monkeypatch,
) -> None:
    seed = _seed_plan(service)
    atomic = _atomic_contract(service, seed, _valid_insertions())
    original_write = service.store._write_plan_atomic_insertion_journal
    crashed = False

    def interrupt_commit(path, payload):
        nonlocal crashed
        if payload.get("state") == "COMMITTED" and not crashed:
            crashed = True
            raise RuntimeError("simulated journal commit interruption")
        original_write(path, payload)

    monkeypatch.setattr(
        service.store,
        "_write_plan_atomic_insertion_journal",
        interrupt_commit,
    )
    with pytest.raises(RuntimeError, match="simulated journal commit interruption"):
        service.plan_tasks(
            "book-faires",
            tasks=[],
            planned_by="human-test",
            plan_id="atomic-crash-recovery",
            atomic_insertion=atomic,
        )
    monkeypatch.setattr(
        service.store,
        "_write_plan_atomic_insertion_journal",
        original_write,
    )

    recovered = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="atomic-crash-recovery",
        atomic_insertion=atomic,
    )
    receipt = recovered["atomic_insertion_receipt"]
    assert receipt["recovered_prepared_insertion"] is True
    assert receipt["idempotent_replay"] is False
    assert recovered["plan_runtime_projection"]["status"] == "PASS"
    assert [
        row["task_id"] for row in recovered["goal_projection"]["rows"]
    ].count("atomic-before-final") == 1


def test_atomic_insertion_extends_existing_mcp_tool_without_catalog_growth(
    service,
) -> None:
    tools = asyncio.run(create_mcp_server(service=service).list_tools())
    assert len(tools) == 83
    plan_tool = next(tool for tool in tools if tool.name == "pv_plan_tasks")
    assert "atomic_insertion" in plan_tool.inputSchema["properties"]
