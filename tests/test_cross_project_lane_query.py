from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.lane_engine import build_lane_bundle
from evidence_lane_plugin.lane_reader import (
    DEFAULT_CROSS_PROJECT_RESULTS,
    DEFAULT_CROSS_PROJECT_TIMEOUT_MS,
    DEFAULT_CROSS_PROJECT_WORKERS,
    MAX_CROSS_PROJECT_RESULTS,
    MAX_CROSS_PROJECT_TIMEOUT_MS,
    MAX_CROSS_PROJECT_WORKERS,
    MAX_CROSS_PROJECTS,
    MIN_CROSS_PROJECTS,
    LaneReader,
)

from .conftest import build_and_approve_pv1, git

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "schemas"
    / "cross-project-query.v001.json"
)


def _grant(request: dict[str, Any]) -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.cross-project-read-grant.v1",
        "status": "PASS",
        "principal_id": request["principal_id"],
        "project_id": request["project_id"],
        "live_root_authority_ref": request["live_root_authority_ref"],
        "scope": "READ_LIVE_PROJECT_ROOT",
        "grant_id": (
            f"grant.{request['project_id']}."
            f"{request['live_root_authority_ref']}"
        ),
    }
    return {**body, "grant_sha256": sha256_bytes(canonical_json_bytes(body))}


class FakeProjectStore:
    def __init__(self) -> None:
        self.projects = {"project-alpha", "project-beta"}

    @staticmethod
    def validate_project_id(project_id: str) -> str:
        if project_id not in {"project-alpha", "project-beta"}:
            raise EvidenceLaneError(
                code="PROJECT_ID_INVALID",
                message="The fake project is unsupported.",
                status="BLOCKED",
            )
        return project_id

    @staticmethod
    def canonical_project_key(project_id: str) -> str:
        return project_id.casefold()

    @staticmethod
    def config(project_id: str) -> SimpleNamespace:
        return SimpleNamespace(project_id=project_id, enabled=True)

    @staticmethod
    def pointer(project_id: str) -> SimpleNamespace:
        suffix = "A" if project_id == "project-alpha" else "B"
        return SimpleNamespace(
            accepted_pv="PV12",
            accepted_manifest_sha256=suffix * 64,
            generation=12,
        )


def _hit(project_id: str, suffix: str) -> dict[str, Any]:
    lane = "docs"
    return {
        "chunk_id": len(suffix),
        "ref_id": f"lane:{lane}:chunk:{suffix}",
        "path": f"{project_id}/{suffix}.md",
        "locator": "line:1",
        "snippet": suffix,
        "source_sha256": suffix[0].upper() * 64,
        "chunk_sha256": suffix[-1].upper() * 64,
        "parser_state": "PARSED",
        "project_id": project_id,
        "live_root_authority_ref": f"{project_id}_WORKING",
        "canonical_lane_id": lane,
        "request_indexes": [0],
        "dedupe_key_sha256": "D" * 64,
    }


class FakeCrossProjectReader(LaneReader):
    def __init__(
        self,
        behaviors: dict[str, dict[str, Any]],
        *,
        authorizer: Any = _grant,
    ) -> None:
        super().__init__(
            FakeProjectStore(),
            cross_project_authorizer=authorizer,
        )
        self.behaviors = behaviors

    def _parallel_bundle(
        self,
        project_id: str,
        pv_ref: str | None,
    ) -> dict[str, Any]:
        assert pv_ref is None
        suffix = "A" if project_id == "project-alpha" else "B"
        return {
            "code_mode": "github_code",
            "bundle_sha256": suffix * 64,
            "_resolved_pv_ref": f"{project_id}_WORKING",
        }

    def search_parallel(
        self,
        project_id: str,
        lane_queries: list[dict[str, Any]],
        *,
        pv_ref: str | None = None,
        max_workers: int = 4,
        aggregate_limit: int = 100,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        behavior = self.behaviors[project_id]
        time.sleep(float(behavior.get("delay", 0)))
        if cancel_event is not None and cancel_event.is_set():
            status = "CANCELLED"
            results: list[dict[str, Any]] = []
        else:
            results = list(behavior.get("results") or [])[:aggregate_limit]
            status = "PASS" if results else "EMPTY"
        request = lane_queries[0]
        request_body = {
            "canonical_lane_id": request["lane"],
            "request_sha256": sha256_bytes(canonical_json_bytes(request)),
            "status": status,
            "within_time_budget": True,
            "result_count": len(results),
            "result_sha256": sha256_bytes(canonical_json_bytes(results)),
        }
        live_root_authority_ref = f"{project_id}_WORKING"
        receipt_body = {
            "schema": "evidence-lane.parallel-lane-query-receipt.v1",
            "status": status,
            "project_id": project_id,
            "live_root_authority_ref": live_root_authority_ref,
            "budgets": {"selected_aggregate_limit": aggregate_limit},
            "request_receipts": [request_body],
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        return {
            "schema": "evidence-lane.parallel-lane-query.v1",
            "status": status,
            "project_id": project_id,
            "live_root_authority_ref": live_root_authority_ref,
            "results": results,
            "receipt": receipt,
        }


def _project_queries() -> list[dict[str, Any]]:
    return [
        {
            "project_id": "project-alpha",
            "lane_queries": [
                {
                    "lane": "docs",
                    "query": "alpha evidence",
                    "limit": 5,
                    "timeout_ms": 500,
                }
            ],
            "project_timeout_ms": 1_000,
            "project_result_limit": 10,
            "lane_workers": 1,
        },
        {
            "project_id": "project-beta",
            "lane_queries": [
                {
                    "lane": "docs",
                    "query": "beta evidence",
                    "limit": 5,
                    "timeout_ms": 500,
                }
            ],
            "project_timeout_ms": 1_000,
            "project_result_limit": 10,
            "lane_workers": 1,
        },
    ]


def _behaviors(
    *, alpha_delay: float = 0, beta_delay: float = 0
) -> dict[str, dict[str, Any]]:
    return {
        "project-alpha": {
            "delay": alpha_delay,
            "results": [_hit("project-alpha", "alpha")],
        },
        "project-beta": {
            "delay": beta_delay,
            "results": [_hit("project-beta", "beta")],
        },
    }


def test_cross_project_query_is_completion_order_independent_and_separately_ranked() -> None:
    slow_alpha = FakeCrossProjectReader(
        _behaviors(alpha_delay=0.03)
    ).search_cross_project("principal-one", _project_queries())
    slow_beta = FakeCrossProjectReader(
        _behaviors(beta_delay=0.03)
    ).search_cross_project("principal-one", _project_queries())

    assert slow_alpha == slow_beta
    assert slow_alpha["status"] == "PASS"
    assert slow_alpha["project_set"] == ["project-alpha", "project-beta"]
    assert [result["project_id"] for result in slow_alpha["results"]] == [
        "project-alpha",
        "project-beta",
    ]
    assert [result["project_rank"] for result in slow_alpha["results"]] == [1, 1]
    assert all(
        result["cross_project_score"] is None for result in slow_alpha["results"]
    )
    assert len({result["rank_domain"] for result in slow_alpha["results"]}) == 2
    for result in slow_alpha["results"]:
        authority = result["cross_project_authority"]
        assert authority["project_id"] == result["project_id"]
        assert authority["live_root_authority_ref"] == (
            f"{result['project_id']}_WORKING"
        )
        assert result["cross_project_authority_sha256"] == sha256_bytes(
            canonical_json_bytes(authority)
        )
    receipt = slow_alpha["receipt"]
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    assert receipt["receipt_sha256"] == sha256_bytes(canonical_json_bytes(body))


def test_cross_project_query_requires_an_injected_exact_permission_provider() -> None:
    without_provider = FakeCrossProjectReader(_behaviors(), authorizer=None)
    with pytest.raises(EvidenceLaneError) as missing:
        without_provider.search_cross_project("principal-one", _project_queries())
    assert missing.value.code == "CROSS_PROJECT_PERMISSION_PROVIDER_REQUIRED"

    def wrong_project(request: dict[str, Any]) -> dict[str, Any]:
        grant = _grant(request)
        grant["project_id"] = "project-beta"
        return grant

    with pytest.raises(EvidenceLaneError) as invalid:
        FakeCrossProjectReader(
            _behaviors(), authorizer=wrong_project
        ).search_cross_project("principal-one", _project_queries())
    assert invalid.value.code == "CROSS_PROJECT_PERMISSION_GRANT_INVALID"


def test_cross_project_query_rejects_implicit_or_unbounded_authority() -> None:
    reader = FakeCrossProjectReader(_behaviors())

    duplicate = _project_queries()
    duplicate[1]["project_id"] = "project-alpha"
    with pytest.raises(EvidenceLaneError) as duplicate_error:
        reader.search_cross_project("principal-one", duplicate)
    assert duplicate_error.value.code == "CROSS_PROJECT_SET_DUPLICATE"

    archived = _project_queries()
    archived[0]["pv_ref"] = "PV12"
    with pytest.raises(EvidenceLaneError) as archived_error:
        reader.search_cross_project("principal-one", archived)
    assert archived_error.value.code == "CROSS_PROJECT_QUERY_FIELD_UNSUPPORTED"

    unknown = _project_queries()
    unknown[0]["discover_projects"] = True
    with pytest.raises(EvidenceLaneError) as unknown_error:
        reader.search_cross_project("principal-one", unknown)
    assert unknown_error.value.code == "CROSS_PROJECT_QUERY_FIELD_UNSUPPORTED"

    timeout = _project_queries()
    timeout[0]["project_timeout_ms"] = 100
    timeout[0]["lane_queries"][0]["timeout_ms"] = 101
    with pytest.raises(EvidenceLaneError) as timeout_error:
        reader.search_cross_project("principal-one", timeout)
    assert timeout_error.value.code == "CROSS_PROJECT_TIMEOUT_HIERARCHY_INVALID"

    underfunded_project = _project_queries()
    underfunded_project[0]["project_result_limit"] = 4
    with pytest.raises(EvidenceLaneError) as project_budget_error:
        reader.search_cross_project("principal-one", underfunded_project)
    assert (
        project_budget_error.value.code == "CROSS_PROJECT_RESULT_BUDGET_INVALID"
    )

    with pytest.raises(EvidenceLaneError) as overall_budget_error:
        reader.search_cross_project(
            "principal-one", _project_queries(), overall_result_limit=19
        )
    assert (
        overall_budget_error.value.code == "CROSS_PROJECT_OVERALL_BUDGET_INVALID"
    )

    incomplete = _behaviors()
    incomplete["project-beta"]["results"][0]["locator"] = ""
    with pytest.raises(EvidenceLaneError) as provenance_error:
        FakeCrossProjectReader(incomplete).search_cross_project(
            "principal-one", _project_queries()
        )
    assert (
        provenance_error.value.code == "CROSS_PROJECT_RESULT_AUTHORITY_INVALID"
    )


def test_cross_project_query_seals_timeout_and_preflight_cancellation() -> None:
    timed_queries = _project_queries()
    timed_queries[0]["project_timeout_ms"] = 2
    timed_queries[0]["lane_queries"][0]["timeout_ms"] = 1
    timed = FakeCrossProjectReader(
        _behaviors(alpha_delay=0.05)
    ).search_cross_project("principal-one", timed_queries)

    assert timed["status"] == "PARTIAL"
    assert [receipt["status"] for receipt in timed["project_receipts"]] == [
        "TIMEOUT",
        "PASS",
    ]
    assert timed["project_receipts"][0]["within_time_budget"] is False
    assert {result["project_id"] for result in timed["results"]} == {"project-beta"}

    cancellation = Event()
    cancellation.set()
    cancelled = FakeCrossProjectReader(_behaviors()).search_cross_project(
        "principal-one", _project_queries(), cancel_event=cancellation
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["results"] == []
    assert all(
        receipt["status"] == "CANCELLED"
        for receipt in cancelled["project_receipts"]
    )


def test_cross_project_schema_matches_runtime_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["schema"] == "evidence-lane.cross-project-query-contract.v1"
    assert contract["project_set"]["minimum_projects"] == MIN_CROSS_PROJECTS
    assert contract["project_set"]["maximum_projects"] == MAX_CROSS_PROJECTS
    budgets = contract["budgets"]
    assert budgets["default_project_workers"] == DEFAULT_CROSS_PROJECT_WORKERS
    assert budgets["maximum_project_workers"] == MAX_CROSS_PROJECT_WORKERS
    assert budgets["default_project_timeout_ms"] == DEFAULT_CROSS_PROJECT_TIMEOUT_MS
    assert budgets["maximum_project_timeout_ms"] == MAX_CROSS_PROJECT_TIMEOUT_MS
    assert budgets["default_overall_results"] == DEFAULT_CROSS_PROJECT_RESULTS
    assert budgets["maximum_overall_results"] == MAX_CROSS_PROJECT_RESULTS
    assert (
        budgets["project_result_budget_rule"]
        == "SELECTED_PROJECT_LIMIT_MUST_COVER_SUM_OF_EXPLICIT_LANE_QUERY_LIMITS"
    )
    assert (
        budgets["overall_result_budget_rule"]
        == "SELECTED_OVERALL_LIMIT_MUST_COVER_SUM_OF_EXPLICIT_PROJECT_LIMITS"
    )
    assert contract["default_behavior"]["permission_provider_required"] is True
    assert contract["default_behavior"]["implicit_project_discovery"] is False


def _second_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "evidence-mirror"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Evidence Lane Test")
    git(repository, "config", "user.email", "evidence-lane@example.invalid")
    git(
        repository,
        "remote",
        "add",
        "origin",
        "https://github.com/example/evidence-mirror.git",
    )
    (repository / "README.md").write_text("# Evidence Mirror\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Initial mirror fixture")
    return repository


def _materialize_live_root(service, project_id: str) -> dict[str, Any]:
    pointer = service.store.pointer(project_id)
    return build_lane_bundle(
        repository_root=service.store.config(project_id).repository_path,
        output_directory=service.store.project_root(project_id) / "sectors",
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=pointer.accepted_pv,
        proposed_pv=f"{pointer.accepted_pv}_WORKING",
        pointer_generation=pointer.generation,
        include_untracked=False,
        materialize_all_lanes=True,
        index_git_history=False,
    )


def test_cross_project_query_reads_two_real_live_project_roots(
    service, tmp_path: Path
) -> None:
    build_and_approve_pv1(service)
    book_manifest = _materialize_live_root(service, "book-faires")
    repository = _second_repository(tmp_path)
    registered = service.register_project(
        project_id="evidence-mirror",
        display_name="Evidence Mirror",
        repository_path=str(repository),
        expected_owner="example",
        expected_name="evidence-mirror",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    assert registered["status"] == "PASS"
    boot = service.boot_session(
        project_id="evidence-mirror",
        user_id="user-test",
        workspace_id="workspace-mirror",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-mirror",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="host-session-mirror",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    mirror_session = boot["session"]["session_id"]
    service.build_initial("evidence-mirror", mirror_session)
    mirror_decision = service.decide(
        "evidence-mirror",
        mirror_session,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_mirror_pv1",
    )
    assert mirror_decision["pointer"]["accepted_pv"] == "PV1"
    mirror_manifest = _materialize_live_root(service, "evidence-mirror")

    reader = LaneReader(
        service.store,
        cross_project_authorizer=_grant,
    )
    result = reader.search_cross_project(
        "principal-one",
        [
            {
                "project_id": "book-faires",
                "lane_queries": [
                    {
                        "lane": "docs",
                        "query": "Book Faires",
                        "limit": 3,
                        "timeout_ms": 10_000,
                    }
                ],
                "project_timeout_ms": 30_000,
                "project_result_limit": 10,
                "lane_workers": 1,
            },
            {
                "project_id": "evidence-mirror",
                "lane_queries": [
                    {
                        "lane": "docs",
                        "query": "Evidence Mirror",
                        "limit": 3,
                        "timeout_ms": 10_000,
                    }
                ],
                "project_timeout_ms": 30_000,
                "project_result_limit": 10,
                "lane_workers": 1,
            },
        ],
    )

    assert result["status"] == "PASS"
    assert result["project_set"] == ["book-faires", "evidence-mirror"]
    assert {item["project_id"] for item in result["results"]} == {
        "book-faires",
        "evidence-mirror",
    }
    authority_refs = {
        receipt["project_id"]: receipt["live_root_authority_ref"]
        for receipt in result["project_receipts"]
    }
    assert authority_refs == {
        "book-faires": book_manifest["proposed_pv"],
        "evidence-mirror": mirror_manifest["proposed_pv"],
    }
    assert all("pv_ref" not in item for item in result["results"])
    assert result["accepted_archive_opened"] is False
    assert result["accepted_archive_queried"] is False
