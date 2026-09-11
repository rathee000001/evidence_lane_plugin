from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    register_source_batch,
    snapshot_source_authority_registry,
)
from evidence_lane_plugin.source_graph import (
    build_registered_source_graph,
    diff_source_graphs,
    source_graph_impact,
)
from evidence_lane_plugin.storage import ProjectStore


def _write_polyglot_project(root: Path, *, alpha_value: int = 1) -> None:
    root.mkdir(exist_ok=True)
    (root / "a.py").write_text(
        "def helper():\n"
        "    return 1\n\n"
        "def alpha():\n"
        f"    value = {alpha_value}\n"
        "    return helper() + external_call() + value\n",
        encoding="utf-8",
    )
    (root / "consumer.py").write_text(
        "from a import helper\n\n"
        "def consumer():\n"
        "    return helper()\n",
        encoding="utf-8",
    )
    (root / "dep.ts").write_text(
        "export function x() { return 1; }\n", encoding="utf-8"
    )
    (root / "app.ts").write_text(
        "import { x } from './dep';\n"
        "export function start() { return x(); }\n",
        encoding="utf-8",
    )
    (root / "main.go").write_text(
        'package main\nimport "fmt"\nfunc Run() { fmt.Println("ok") }\n',
        encoding="utf-8",
    )
    (root / "model.rs").write_text(
        "pub struct Model {}\npub fn load() {}\n", encoding="utf-8"
    )
    (root / "schema.sql").write_text(
        "CREATE TABLE evidence(id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    (root / "tool.ps1").write_text(
        "function Invoke-Evidence { Write-Output 'ok' }\n", encoding="utf-8"
    )
    (root / "package.json").write_text(
        '{"dependencies":{"zod":"^4.0.0"}}\n', encoding="utf-8"
    )
    (root / "README.md").write_text("# project\n", encoding="utf-8")


def _register(registry: Path, source: Path) -> str:
    receipt = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(source), 1, "local_code")],
    )
    return str(receipt["batch_id"])


def _rows(registry: Path, sql: str, parameters: tuple[object, ...]) -> list[dict]:
    with registry.connection(read_only=True) as connection:
        return [dict(row) for row in connection.execute(sql, parameters)]


def test_polyglot_graph_stable_ids_provenance_diff_and_impact(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    registry = _sources_registry(tmp_path)
    _write_polyglot_project(project, alpha_value=1)
    batch_v1 = _register(registry, project)

    graph_v1 = build_registered_source_graph(
        registry,
        batch_v1,
        occurrence_ordinals=[1],
    )
    reused = build_registered_source_graph(
        registry,
        batch_v1,
        occurrence_ordinals=[1],
    )

    assert graph_v1["status"] == "PASS"
    assert reused["append_status"] == "IDEMPOTENT_REUSE"
    assert reused["graph_root_sha256"] == graph_v1["graph_root_sha256"]
    assert graph_v1["coverage_states"] == {
        "FILE_ONLY_NON_CODE": 1,
        "PARSED_AST": 2,
        "PARSED_MANIFEST": 1,
        "PARSED_REGEX": 6,
    }
    assert graph_v1["edge_confidence_counts"]["EXTRACTED"] > 0
    assert graph_v1["edge_confidence_counts"]["INFERRED"] >= 2
    assert graph_v1["edge_confidence_counts"]["AMBIGUOUS"] >= 1

    languages = {
        row["language"]
        for row in _rows(
            registry,
            "SELECT DISTINCT language FROM source_graph_node WHERE graph_id=?",
            (graph_v1["graph_id"],),
        )
    }
    assert {"python", "typescript", "go", "rust", "sql", "powershell"} <= languages
    helper_node = _rows(
        registry,
        """SELECT node_id, node_sha256 FROM source_graph_node
        WHERE graph_id=? AND node_kind='SYMBOL' AND qualified_name='helper'""",
        (graph_v1["graph_id"],),
    )[0]
    impact = source_graph_impact(
        registry,
        graph_v1["graph_id"],
        [helper_node["node_id"]],
        relations=["CALLS"],
        direction="UPSTREAM",
        max_depth=2,
    )
    impacted_labels = {row["label"] for row in impact["nodes"] if not row["seed"]}
    assert {"alpha", "consumer"} <= impacted_labels
    assert impact["truncated"] is False

    _write_polyglot_project(project, alpha_value=2)
    batch_v2 = _register(registry, project)
    graph_v2 = build_registered_source_graph(registry, batch_v2)
    helper_v2 = _rows(
        registry,
        """SELECT node_id, node_sha256 FROM source_graph_node
        WHERE graph_id=? AND node_kind='SYMBOL' AND qualified_name='helper'""",
        (graph_v2["graph_id"],),
    )[0]
    assert helper_v2 == helper_node

    alpha_v1 = _rows(
        registry,
        """SELECT node_id, node_sha256 FROM source_graph_node
        WHERE graph_id=? AND node_kind='SYMBOL' AND qualified_name='alpha'""",
        (graph_v1["graph_id"],),
    )[0]
    alpha_v2 = _rows(
        registry,
        """SELECT node_id, node_sha256 FROM source_graph_node
        WHERE graph_id=? AND node_kind='SYMBOL' AND qualified_name='alpha'""",
        (graph_v2["graph_id"],),
    )[0]
    assert alpha_v2["node_id"] == alpha_v1["node_id"]
    assert alpha_v2["node_sha256"] != alpha_v1["node_sha256"]

    diff = diff_source_graphs(registry, graph_v1["graph_id"], graph_v2["graph_id"])
    assert diff["counts"]["added_nodes"] == 0
    assert diff["counts"]["removed_nodes"] == 0
    assert diff["counts"]["changed_nodes"] >= 2
    assert diff["counts"]["changed_edges"] >= 1
    snapshot = snapshot_source_authority_registry(registry)
    assert snapshot["graph_projection"]["snapshot_count"] == 2
    assert snapshot["graph_projection"]["diff_count"] == 1
    assert snapshot["graph_projection"]["impact_count"] == 1


def test_graph_skips_archive_only_with_exact_extracted_counterpart(
    tmp_path: Path,
) -> None:
    extracted = tmp_path / "project"
    extracted.mkdir()
    (extracted / "main.py").write_text("def exact():\n    return 1\n", encoding="utf-8")
    archive = tmp_path / "project.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(extracted / "main.py", "project/main.py")
    registry = _sources_registry(tmp_path)
    receipt = register_source_batch(
        registry,
        [
            SourceAuthoritySpec(str(extracted), 1, "local_code"),
            SourceAuthoritySpec(str(archive), 2, "local_code"),
        ],
    )

    graph = build_registered_source_graph(
        registry,
        str(receipt["batch_id"]),
        occurrence_ordinals=[1, 2],
    )

    assert graph["coverage_states"]["SKIPPED_EXACT_EXTRACTED_COUNTERPART"] == 1
    assert graph["coverage_states"]["PARSED_AST"] == 1
    archive_coverage = _rows(
        registry,
        """SELECT file_state, parser_id FROM source_graph_file_coverage
        WHERE graph_id=? AND member_path='<archive>'""",
        (graph["graph_id"],),
    )
    assert archive_coverage == [
        {
            "file_state": "SKIPPED_EXACT_EXTRACTED_COUNTERPART",
            "parser_id": "delta067a-counterpart-proof",
        }
    ]


def test_graph_bounds_fail_before_partial_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "bounded"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (project / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    registry = _sources_registry(tmp_path)
    batch_id = _register(registry, project)

    with pytest.raises(EvidenceLaneError) as blocked:
        build_registered_source_graph(registry, batch_id, max_files=1)

    assert blocked.value.code == "SOURCE_GRAPH_FILE_BOUND_EXCEEDED"
    assert _rows(registry, "SELECT graph_id FROM source_graph_snapshot", ()) == []


def test_graph_path_prefix_selection_is_explicit_and_bounded(tmp_path: Path) -> None:
    project = tmp_path / "selected"
    _write_polyglot_project(project)
    registry = _sources_registry(tmp_path)
    batch_id = _register(registry, project)

    graph = build_registered_source_graph(
        registry,
        batch_id,
        member_path_prefixes=["a.py"],
        max_files=1,
    )

    assert graph["status"] == "PASS_BOUNDED_SELECTION"
    assert graph["path_bounded_selection"] is True
    assert graph["member_path_prefixes"] == ["a.py"]
    assert graph["registered_member_count"] == 10
    assert graph["omitted_registered_member_count"] == 9
    assert graph["file_count"] == 1




def _sources_registry(tmp_path):
    source = tmp_path / 'selected-worktree'
    source.mkdir()
    return ProjectStore.create(tmp_path / 'separate-state', source).lane('sources')
