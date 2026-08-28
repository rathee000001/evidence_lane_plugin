from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evidence_lane_plugin import live_authority_query


class _Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def project_root(self, project_id: str) -> Path:
        assert project_id == "project-one"
        return self.root

    @staticmethod
    def config(project_id: str) -> SimpleNamespace:
        assert project_id == "project-one"
        return SimpleNamespace(repository_path=Path("repo"))

    @staticmethod
    def pointer(project_id: str) -> SimpleNamespace:
        assert project_id == "project-one"
        return SimpleNamespace(accepted_pv="PV12", generation=12)


class _Service:
    def __init__(self, root: Path) -> None:
        self.store = _Store(root)

    @staticmethod
    def agent_configuration_authority(
        project_id: str, *, session_id: str
    ) -> dict[str, Any]:
        assert project_id == "project-one"
        assert session_id == "session-one"
        return {
            "status": "PASS",
            "agent_configuration_authority_sha256": "A" * 64,
            "source_chain_sha256": "B" * 64,
        }

    @staticmethod
    def conversation_memory_authority(
        project_id: str, *, session_id: str
    ) -> dict[str, Any]:
        assert project_id == "project-one"
        assert session_id == "session-one"
        return {
            "status": "PASS",
            "conversation_memory_authority_sha256": "C" * 64,
            "source_chain_sha256": "D" * 64,
        }

    @staticmethod
    def connector_plugin_catalog(project_id: str) -> dict[str, Any]:
        assert project_id == "project-one"
        return {
            "status": "PASS",
            "active_count": 0,
            "routable_count": 0,
            "integrity": ["ok"],
            "foreign_key_errors": [],
            "secret_values_persisted": False,
        }


def _binding() -> SimpleNamespace:
    return SimpleNamespace(
        task_id="task-one",
        env_authority_sha256="E" * 64,
        uop_authority_sha256="F" * 64,
        derived_projection_sha256="1" * 64,
        flash_receipt_sha256="2" * 64,
    )


def _read_rows(*, retry: bool) -> list[dict[str, Any]]:
    return [
        {
            "authority": "agent_learning",
            "refresh_required": not retry,
            "data": {"hits": [], "result": "NO_HIT"},
        },
        {
            "authority": "project_memory",
            "refresh_required": not retry,
            "data": {"hits": [{"memory_id": "memory-one"}] if retry else []},
        },
        {
            "authority": "canon_input",
            "refresh_required": not retry,
            "data": {"hits": [{"node_id": "canon-one"}] if retry else []},
        },
        {
            "authority": "project_universe",
            "refresh_required": not retry,
            "data": {
                "hits": [{"node_id": "universe-one"}] if retry else [],
                "result": "HIT" if retry else "NO_HIT",
            },
        },
    ]


def test_live_query_keeps_six_authorities_and_never_opens_accepted(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project-one"
    accepted = project_root / "accepted"
    accepted.mkdir(parents=True)
    (accepted / "PV12.zip").write_bytes(b"must-not-be-opened")
    calls: list[str] = []

    def fake_read(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], Any]:
        pass_number = int(kwargs["pass_number"])
        calls.append(f"read-{pass_number}")
        return _read_rows(retry=pass_number == 2), _binding()

    def fake_refresh(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.extend(
            [
                "learning-refresh",
                "canon-refresh",
                "memory-refresh",
                "universe-refresh",
            ]
        )
        return [
            {"ordinal": 1, "authority": "agent_learning", "status": "PASS"},
            {"ordinal": 2, "authority": "canon_input", "status": "PASS"},
            {"ordinal": 3, "authority": "project_memory", "status": "PASS"},
            {"ordinal": 4, "authority": "project_universe", "status": "PASS"},
        ]

    monkeypatch.setattr(live_authority_query, "_read_arms", fake_read)
    monkeypatch.setattr(live_authority_query, "_refresh_arms", fake_refresh)
    monkeypatch.setattr(
        live_authority_query,
        "inspect_repository",
        lambda path: SimpleNamespace(branch="main", commit_sha="1" * 40),
    )
    monkeypatch.setattr(
        live_authority_query,
        "query_working_project_sectors",
        lambda *args, **kwargs: {
            "status": "PASS",
            "hits": [{"canonical_lane_id": "local_code", "path": "src/a.py"}],
            "query_mutated_project_authority": False,
            "query_rehashed_dirty_content": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
        },
    )

    result = live_authority_query.query_live_authorities(
        _Service(project_root),
        project_id="project-one",
        session_id="session-one",
        query="R266 live root",
        limit=4,
    )

    assert calls == [
        "read-1",
        "learning-refresh",
        "canon-refresh",
        "memory-refresh",
        "universe-refresh",
        "read-2",
    ]
    assert list(result["authorities"]) == [
        "sector_lanes",
        "agent_learning",
        "canon_graph",
        "project_memory",
        "project_universe",
        "connector_brain",
        "agent_configuration",
        "conversation_memory",
    ]
    assert result["authorities"]["agent_learning"]["data"]["result"] == "NO_HIT"
    assert result["authorities"]["sector_lanes"]["result"]["hits"]
    assert result["refresh_performed"] is True
    assert result["bounded_retry_performed"] is True
    assert result["accepted_archive_opened"] is False
    assert result["accepted_archive_queried"] is False
    assert (accepted / "PV12.zip").read_bytes() == b"must-not-be-opened"


def test_project_memory_wrong_active_task_hit_is_suppressed_and_refreshed() -> None:
    data, stale = live_authority_query._apply_active_task_freshness(
        "project_memory",
        {
            "status": "PASS",
            "result": "HIT",
            "hits": [
                {
                    "locator_id": "old-active",
                    "locator_kind": "ACTIVE_TASK",
                    "locator_value": "plan://task/R255",
                    "revision_sha256": "A" * 64,
                }
            ],
            "suppressed": [],
        },
        active_task_id="R265",
    )

    assert stale is True
    assert data["result"] == "NO_HIT"
    assert data["hits"] == []
    assert data["active_task_freshness"] == "STALE_HITS_SUPPRESSED"
    assert data["stale_active_task_hit_count"] == 1
    assert data["suppressed"][0]["reason"] == (
        "PROJECT_MEMORY_ACTIVE_TASK_MISMATCH"
    )
