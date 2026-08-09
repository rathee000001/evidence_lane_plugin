from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin import database
from evidence_lane_plugin.connector_governance import (
    ConnectorGovernance,
    validate_connector_brain,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_history import index_git_history
from evidence_lane_plugin.ingest import ingest_repository, refresh_repository
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from evidence_lane_plugin.lineage import ChatLineage
from evidence_lane_plugin.operating_modes import classify_operating_modes
from evidence_lane_plugin.project_overlay import (
    build_project_overlay,
    validate_project_overlay,
)
from evidence_lane_plugin.service import EvidenceLaneService
from evidence_lane_plugin.source_intake import classify_source_intake


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _repository_row(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        INSERT INTO repositories(
            provider, repository_url, owner, name, branch, commit_sha,
            tree_sha, worktree_sha256, is_clean, submodules_json, lfs_state
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "local",
            "file:///chunk-cas",
            "test",
            "chunk-cas",
            "main",
            "0" * 40,
            "1" * 40,
            "2" * 64,
            1,
            "[]",
            "NOT_USED",
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def test_generalized_source_intake_supports_every_lane_in_order() -> None:
    sources = [f"source://{lane_id}" for lane_id in CANONICAL_LANE_IDS]
    overrides = {
        source: lane_id for source, lane_id in zip(sources, CANONICAL_LANE_IDS)
    }
    result = classify_source_intake(
        sources,
        code_mode="local_code",
        overrides=overrides,
    )
    assert result["status"] == "PASS"
    assert result["source_count"] == 18
    assert result["ordered_canonical_lanes"] == [
        "chat_lineage",
        *[lane_id for lane_id in CANONICAL_LANE_IDS if lane_id != "chat_lineage"],
    ]
    assert result["chat_lineage_included"] is True
    assert result["project_engulf_supported"] is True
    assert result["source_bytes_mutated"] is False
    assert result["pointer_moved"] is False
    auto = classify_source_intake(
        ["https://example.invalid/evidence/report.pdf"],
        code_mode="local_code",
    )
    assert auto["sources"][0]["canonical_lane_id"] == "pdf_ocr"
    assert auto["sources"][0]["classification_reason"] == ("remote_content_type_router")


def test_custom_mode_schema_preserves_order_and_chat_lineage() -> None:
    result = classify_operating_modes(
        request="Use Forensic Merge for this turn.",
        code_lane="local_code",
        explicit_modes=["Forensic Merge"],
        custom_modes=[
            {
                "name": "Forensic Merge",
                "brief": "Compare local code against cited research evidence.",
                "lanes": ["local_code", "research"],
                "dependency_policy": {
                    "requires": ["local_code", "research"],
                    "on_missing": "BLOCK",
                },
            }
        ],
    )
    assert result["canonical_lanes"] == [
        "mode",
        "chat_lineage",
        "local_code",
        "research",
    ]
    assert result["selected_modes"][0]["custom"] is True
    assert result["chat_lineage"]["private_reasoning_excluded"] is True


def test_lineage_hash_chain_redacts_secrets_and_blocks_private_reasoning(
    tmp_path: Path,
) -> None:
    lineage = ChatLineage(tmp_path / "lineage.jsonl")
    first = lineage.append(
        event_type="user.prompt",
        visible_payload={
            "prompt": "Inspect sk-abcdefghijklmnopqrstuvwxyz123456 and do not save it."
        },
        occurred_at="2026-07-31T00:00:00Z",
        session_id="session-test",
        actor_type="user",
    )
    second = lineage.append(
        event_type="assistant.response",
        visible_payload={
            "response": "The secret was redacted.",
            "tools": ["runtime_doctor"],
            "tests": ["lineage"],
            "output_links": ["artifact://lineage"],
        },
        occurred_at="2026-07-31T00:00:01Z",
        session_id="session-test",
        actor_type="assistant",
        model="gpt-test",
        submodel="test-submodel",
        token_metrics={"availability": "AVAILABLE", "output_tokens": 17},
    )
    assert first["visible_payload"]["prompt"].count("[REDACTED]") == 1
    assert second["previous_event_sha256"] == first["event_sha256"]
    assert second["model"] == "gpt-test"
    assert second["token_metrics"]["output_tokens"] == 17
    assert all(event["private_reasoning_stored"] is False for event in lineage.events())
    with pytest.raises(EvidenceLaneError) as blocked:
        lineage.append(
            event_type="assistant.response",
            visible_payload={"chain_of_thought": "must never persist"},
            occurred_at="2026-07-31T00:00:02Z",
            session_id="session-test",
        )
    assert blocked.value.code == "LINEAGE_PRIVATE_REASONING_FORBIDDEN"


def test_primary_chunk_cas_reuses_unchanged_sections(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    source = repository / "long.py"
    original_lines = [f"value_{index} = {index}" for index in range(1, 121)]
    source.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
    brain = tmp_path / "brain.sqlite"
    database.initialize(brain)
    with database.connect(brain) as connection:
        repository_id = _repository_row(connection)
        initial = ingest_repository(
            connection,
            repository_id=repository_id,
            repository_root=repository,
        )
        connection.commit()
    changed_lines = [*original_lines[:-1], "value_120 = 999"]
    source.write_text("\n".join(changed_lines) + "\n", encoding="utf-8")
    with database.connect(brain) as connection:
        refreshed = refresh_repository(
            connection,
            repository_id=repository_id,
            repository_root=repository,
            parent_pv="PV1",
        )
        connection.commit()
    assert initial.chunk_cas_created == 2
    assert refreshed.refresh["CHANGED_REBUILD"] == 1
    assert refreshed.changed_sections_reused == 1
    assert refreshed.changed_sections_reindexed == 1
    assert refreshed.chunk_cas_reused == 1
    report = database.integrity_report(brain)
    assert report["valid"] is True
    assert report["counts"]["chunk_content_cas"] == 3
    assert report["counts"]["chunk_history"] == 4


def test_git_history_brain_indexes_all_reachable_commits_and_reuses_cas(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "history"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "History Test")
    _git(repository, "config", "user.email", "history@example.invalid")
    source = repository / "story.txt"
    source.write_text("historic alpha\n", encoding="utf-8")
    _git(repository, "add", "story.txt")
    _git(repository, "commit", "-m", "historic alpha")
    source.write_text("historic alpha\nhistoric beta\n", encoding="utf-8")
    _git(repository, "add", "story.txt")
    _git(repository, "commit", "-m", "historic beta")

    connection = sqlite3.connect(tmp_path / "history.sqlite")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    first = index_git_history(connection, repository)
    connection.commit()
    second = index_git_history(connection, repository)
    connection.commit()
    assert first["full_reachable_history"] is True
    assert first["counts"]["commits"] == 2
    assert first["counts"]["file_changes"] == 2
    assert first["counts"]["blobs"] == 2
    assert second["new_commits"] == 0
    assert second["reused_commits"] == 2
    assert second["new_blobs"] == 0
    assert second["reused_blobs"] == 2
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM git_history_fts WHERE git_history_fts MATCH 'historic'"
        ).fetchone()[0]
        >= 2
    )
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_connector_governance_limits_active_plugins_and_preserves_drop_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connector_brain.sqlite"
    governance = ConnectorGovernance(path)
    for index in range(8):
        result = governance.register(
            plugin_id=f"tool-{index}",
            name=f"Tool {index}",
            plugin_kind="toolchain",
            description="A deterministic test toolchain connector.",
            config_env_keys=[f"TOOL_{index}_TOKEN"],
            capabilities=["semantic-index"],
            allowed_lanes=["research"],
            registered_by="test",
        )
        assert result["secret_values_persisted"] is False
    with pytest.raises(EvidenceLaneError) as limited:
        governance.register(
            plugin_id="tool-nine",
            name="Tool Nine",
            plugin_kind="connector",
            description="This registration must exceed the active limit.",
            config_env_keys=["TOOL_NINE_TOKEN"],
            capabilities=["semantic-index"],
            allowed_lanes=["research"],
            registered_by="test",
        )
    assert limited.value.code == "PLUGIN_PERSISTENT_LIMIT_REACHED"
    route = governance.route(capability="semantic-index", canonical_lane_id="research")
    assert route["selected_plugin_id"] is None
    assert route["decision"] == "AMBIGUOUS_FAIL_CLOSED"
    assert route["eligible_plugin_ids"] == [f"tool-{index}" for index in range(8)]
    preferred_route = governance.route(
        capability="semantic-index",
        canonical_lane_id="research",
        preferred_plugin_id="tool-0",
    )
    assert preferred_route["decision"] == "PERSISTENT_PLUGIN"
    assert preferred_route["selected_plugin_id"] == "tool-0"
    dropped = governance.drop(
        plugin_id="tool-0", confirmation="DROP:tool-0", dropped_by="test"
    )
    assert dropped["history_preserved"] is True
    catalog = governance.catalog()
    assert catalog["status"] == "PASS"
    assert catalog["active_count"] == 7
    assert len(catalog["registrations"]) == 8
    validation = validate_connector_brain(path)
    assert validation["valid"] is True
    assert validation["active_count"] == 7
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM plugin_event").fetchone()[0] == 9
    assert connection.execute("SELECT COUNT(*) FROM plugin_fts").fetchone()[0] == 8
    connection.close()


def test_project_overlay_is_candidate_only_and_fans_out_visible_lineage(
    tmp_path: Path,
) -> None:
    lineage_path = tmp_path / "lineage.jsonl"
    ChatLineage(lineage_path).append(
        event_type="tool.test.build",
        visible_payload={
            "command": "pytest",
            "files": ["src/app.py"],
            "tests": ["unit"],
            "build": "PASS",
            "output_links": ["artifact://test-report"],
        },
        occurred_at="2026-07-31T00:00:00Z",
        session_id="session-overlay",
        actor_type="tool",
        model="gpt-test",
        token_metrics={"availability": "UNAVAILABLE"},
    )
    lane_bundle = tmp_path / "lanes"
    lane_bundle.mkdir()
    active_sector_ids = ["local_code", "chat_lineage", "artifacts"]
    (lane_bundle / "manifest.json").write_text(
        json.dumps({"emitted_lane_ids": active_sector_ids}),
        encoding="utf-8",
    )
    for sector_id in active_sector_ids:
        (lane_bundle / sector_id).mkdir()
    output = tmp_path / "overlay"
    result = build_project_overlay(
        output,
        lane_bundle_path=lane_bundle,
        lineage_source=lineage_path,
        candidate_id="candidate-test",
        proposed_pv="PV1",
        parent_accepted_pv=None,
        pointer_generation=0,
        code_mode="local_code",
        created_at="2026-07-31T00:00:01Z",
    )
    assert result["status"] == "PASS"
    assert validate_project_overlay(output)["valid"] is True
    assert result["fanout_counts"]["chat_lineage"] == 1
    assert result["fanout_counts"]["local_code"] == 1
    assert result["fanout_counts"]["artifacts"] == 1
    expected_overlay_order = ["chat_lineage", "local_code", "artifacts"]
    assert result["sector_ids"] == expected_overlay_order
    connection = sqlite3.connect(output / "project_overlay.sqlite")
    assert [
        row[0]
        for row in connection.execute(
            "SELECT sector_id FROM project_sector_registry ORDER BY ordinal"
        )
    ] == expected_overlay_order
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM project_sector_registry WHERE sector_id='github_code'"
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM chat_lineage_event WHERE accepted_sector_truth<>0"
        ).fetchone()[0]
        == 0
    )
    assert connection.execute("SELECT state FROM fusion_receipt").fetchone()[0] == (
        "AWAITING_EXACT_APPROVE"
    )
    assert connection.execute("SELECT COUNT(*) FROM output_link").fetchone()[0] == 2
    connection.close()


def test_public_hil_api_cannot_promote_and_vercel_adapter_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EvidenceLaneService(data_root=tmp_path / "store")
    with pytest.raises(EvidenceLaneError) as blocked:
        service.record_hil_decision(
            "project-test",
            "session-test",
            decision="APPROVE",
            decided_by="human-test",
            decision_id="decision-test",
        )
    assert blocked.value.code == "APPROVE_REQUIRES_PV_FUSE"
    assert not (tmp_path / "store" / "projects" / "project-test").exists()

    root = Path(__file__).resolve().parents[1]
    adapter_path = (
        root
        / "plugins"
        / "evidence-lane-plugin"
        / "remote_adapter"
        / "api"
        / "index.py"
    )
    adapter_root = adapter_path.parents[1]
    vercel = json.loads((adapter_root / "vercel.json").read_text(encoding="utf-8"))
    assert {rewrite["source"] for rewrite in vercel["rewrites"]} == {
        "/mcp",
        "/healthz",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/openai-apps-challenge",
    }
    assert "/(.*)" not in {rewrite["source"] for rewrite in vercel["rewrites"]}
    landing = (adapter_root / "app" / "page.tsx").read_text(encoding="utf-8")
    release = (adapter_root / "app" / "_components" / "release-status.tsx").read_text(
        encoding="utf-8"
    )
    styles = (adapter_root / "app" / "globals.css").read_text(encoding="utf-8")
    site_data = (adapter_root / "app" / "_data" / "site.ts").read_text(encoding="utf-8")
    package = json.loads((adapter_root / "package.json").read_text(encoding="utf-8"))
    assert "Resume from verified project truth" in landing
    assert "DeltaLedgerExplorer" in landing
    assert "PluginSurfaceCatalog" in landing
    assert "HeroOrbit" in landing
    for retired in ("UniversalCommandDeck", "LaneToolchainExplorer", "SourceBrainLab"):
        assert retired not in landing
    lanes_page = (adapter_root / "app" / "lanes" / "page.tsx").read_text(encoding="utf-8")
    assert "LaneToolchainExplorer" in lanes_page
    assert "ChatGPT MCP edge fail-closed" in release
    assert "prefers-reduced-motion" in styles
    active_tsx = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (adapter_root / "app").rglob("*.tsx")
    )
    assert "<img" not in active_tsx
    active_brand_source = active_tsx + (adapter_root / "app" / "manifest.ts").read_text(
        encoding="utf-8"
    )
    home_orbit = (
        adapter_root / "app" / "_components" / "hero-orbit.tsx"
    ).read_text(encoding="utf-8")
    assert "/evidence-lane-full-logo.png" in active_brand_source
    assert "/evidence-lane-icon.png" in active_brand_source
    assert "evidence-root-fibers.png" not in active_brand_source
    assert "evidence-cube-icon.png" not in active_brand_source
    assert "evidence-executive-scanner.png" not in active_brand_source
    assert "evidence-glass-orb.png" not in active_brand_source
    assert "/assets/evidence-static-brain.png" in active_brand_source
    assert "homeGovernanceRings" in home_orbit
    assert "PulsatingBrain" in home_orbit
    assert "GlassIconOrb" in home_orbit
    assert ".heroOrbitRing" in styles
    assert "prefers-reduced-motion" in styles
    assert package["dependencies"]["three"] == "0.185.1"
    assert package["dependencies"]["framer-motion"] == "12.38.0"
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        assert f'id: "{lane_id}"' in site_data
        assert f'parser: "{lane.parser_id}"' in site_data
        assert f'chunker: "{lane.chunker_version}"' in site_data
        assert f'retrieval: "{lane.fts_table}"' in site_data
    for public_page in (
        "architecture",
        "lanes",
        "proof",
        "provenance",
        "connect",
        "privacy",
        "terms",
        "support",
    ):
        assert (adapter_root / "app" / public_page / "page.tsx").is_file()
    for asset in (
        "evidence-lane-full-logo.png",
        "evidence-static-brain.png",
    ):
        assert (adapter_root / "public" / asset).stat().st_size > 100_000
    assert (
        adapter_root / "public" / "evidence-lane-icon.png"
    ).stat().st_size <= 10 * 1024
    assert (
        hashlib.sha256(
            (adapter_root / "public" / "evidence-lane-full-logo.png").read_bytes()
        )
        .hexdigest()
        .upper()
        == "FB7356284760A9AF436B08B75158B9407E582F8107077FC585E4C8F716530933"
    )
    assert (
        hashlib.sha256(
            (adapter_root / "public" / "evidence-lane-icon.png").read_bytes()
        )
        .hexdigest()
        .upper()
        == "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3DEF87B8A129C4FA"
    )
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_remote_adapter", adapter_path
    )
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    monkeypatch.delenv("EVIDENCE_LANE_DURABLE_MCP_ORIGIN", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_RELEASE_SHA", raising=False)
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(
        adapter.app(
            {
                "type": "http",
                "path": "/healthz",
                "method": "GET",
                "headers": [],
                "query_string": b"",
            },
            receive,
            send,
        )
    )
    assert sent[0]["status"] == 503
    body = json.loads(bytes(sent[1]["body"]).decode("utf-8"))
    assert body["status"] == "BLOCKED"
    assert body["local_state_authority"] is False
    assert set(body["errors"]) == {
        "DURABLE_HTTPS_ORIGIN_REQUIRED",
        "EXACT_RELEASE_SHA_REQUIRED",
    }

    sent.clear()
    asyncio.run(
        adapter.app(
            {
                "type": "http",
                "path": "/api/index.py",
                "method": "GET",
                "headers": [],
                "query_string": b"__evi_path=healthz&probe=1",
            },
            receive,
            send,
        )
    )
    assert sent[0]["status"] == 503
    rewritten = json.loads(bytes(sent[1]["body"]).decode("utf-8"))
    assert rewritten["service"] == "evidence-lane-chatgpt-adapter"
    assert adapter._external_route(
        {
            "path": "/api/index.py",
            "query_string": b"__evi_path=mcp&session=visible",
        }
    ) == ("/mcp", "session=visible")
