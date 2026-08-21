"""Project-isolated graph and telemetry derived from live lane authority.

The Universe is a disposable, content-addressed projection. It reads the live
18-lane authority and canonical Plan SQLite, but it owns neither Project Truth,
Plan state, lane bytes, candidates, HIL, nor the accepted pointer.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from .project_authority import resolved_plan_runtime_path
from .timeutil import utc_now

UNIVERSE_SCHEMA = "evidence-lane.project-universe.v1"
UNIVERSE_BUNDLE_SCHEMA = "evidence-lane.project-universe-bundle.v1"
UNIVERSE_MANIFEST_SCHEMA = "evidence-lane.project-universe-manifest.v1"
UNIVERSE_POINTER_SCHEMA = "evidence-lane.project-universe-pointer.v1"
UNIVERSE_RECEIPT_SCHEMA = "evidence-lane.project-universe-refresh-receipt.v1"
UNIVERSE_QUERY_SCHEMA = "evidence-lane.project-universe-query.v1"
UNIVERSE_STATUS_SCHEMA = "evidence-lane.project-universe-status.v1"
UNIVERSE_DIR = "universe"
UNIVERSE_DB = "project_universe.sqlite"
_MAX_QUERY_LIMIT = 100


def _root(project_root: str | Path, *, project_id: str) -> Path:
    root = Path(project_root).resolve()
    require(
        root.name == project_id,
        "PROJECT_UNIVERSE_ROOT_MISMATCH",
        "The Project Universe root does not match the exact project identity.",
        status="MISMATCH",
        project_id=project_id,
    )
    return root


def _json(path: Path, *, code: str) -> dict[str, Any]:
    require(path.is_file(), code, "A required Project Universe input is missing.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        require(False, code, "A required Project Universe input is invalid JSON.")
        raise AssertionError from exc
    require(isinstance(value, dict), code, "A Project Universe input must be one object.")
    return cast(dict[str, Any], value)


def _schema_sql() -> str:
    candidates = (
        Path(__file__).resolve().parent
        / "schemas"
        / "universe"
        / "project-universe.v1.sql",
        Path(__file__).resolve().parents[3]
        / "schemas"
        / "universe"
        / "project-universe.v1.sql",
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    require(
        path is not None,
        "PROJECT_UNIVERSE_SCHEMA_MISSING",
        "The Project Universe SQLite schema asset is missing.",
    )
    return cast(Path, path).read_text(encoding="utf-8")


def _connect_ro(path: Path) -> sqlite3.Connection:
    require(
        path.is_file(),
        "PROJECT_UNIVERSE_SOURCE_SQLITE_MISSING",
        "A required live sector SQLite authority is missing.",
        path=str(path),
    )
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    require(
        integrity == ["ok"] and not foreign_keys,
        "PROJECT_UNIVERSE_SOURCE_SQLITE_INVALID",
        "A live sector SQLite authority failed integrity validation.",
        status="FAIL",
        path=str(path),
        integrity=integrity,
        foreign_key_error_count=len(foreign_keys),
    )
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _count(connection: sqlite3.Connection, table: str, tables: set[str]) -> int:
    if table not in tables:
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _stable_id(prefix: str, locator: str) -> str:
    return f"{prefix}_{sha256_bytes(locator.encode('utf-8'))[:24].lower()}"


def _node(
    kind: str, locator: str, label: str, attributes: Mapping[str, Any]
) -> dict[str, Any]:
    body = {
        "schema": UNIVERSE_SCHEMA,
        "node_kind": str(kind).upper(),
        "canonical_locator": locator,
        "label": label,
        "attributes": dict(attributes),
    }
    return {
        **body,
        "node_id": _stable_id("unode", locator),
        "record_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _edge(
    relation: str,
    source: Mapping[str, Any],
    destination: Mapping[str, Any],
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "schema": UNIVERSE_SCHEMA,
        "relation": str(relation).upper(),
        "source_node_id": source["node_id"],
        "destination_node_id": destination["node_id"],
        "attributes": dict(attributes or {}),
    }
    locator = "universe-edge://" + sha256_bytes(canonical_json_bytes(body)).lower()
    return {
        **body,
        "canonical_locator": locator,
        "edge_id": _stable_id("uedge", locator),
        "record_sha256": sha256_bytes(canonical_json_bytes({**body, "canonical_locator": locator})),
    }


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}

    def add_node(
        self, kind: str, locator: str, label: str, attributes: Mapping[str, Any]
    ) -> dict[str, Any]:
        candidate = _node(kind, locator, label, attributes)
        prior = self.nodes.get(locator)
        require(
            prior is None or prior["record_sha256"] == candidate["record_sha256"],
            "PROJECT_UNIVERSE_NODE_CONFLICT",
            "One stable Universe locator maps to different bytes.",
            status="MISMATCH",
            locator=locator,
        )
        self.nodes[locator] = prior or candidate
        return self.nodes[locator]

    def add_edge(
        self,
        relation: str,
        source: Mapping[str, Any],
        destination: Mapping[str, Any],
        attributes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = _edge(relation, source, destination, attributes)
        locator = cast(str, candidate["canonical_locator"])
        prior = self.edges.get(locator)
        require(
            prior is None or prior["record_sha256"] == candidate["record_sha256"],
            "PROJECT_UNIVERSE_EDGE_CONFLICT",
            "One stable Universe edge maps to different bytes.",
            status="MISMATCH",
            locator=locator,
        )
        self.edges[locator] = prior or candidate
        return self.edges[locator]


def _lane_database(root: Path, lane_id: str) -> Path:
    if lane_id == "plan":
        return resolved_plan_runtime_path(root)
    return root / "sectors" / lane_id / LANE_REGISTRY[lane_id].sqlite_filename


def _lane_snapshot(root: Path, *, project_id: str, graph: _Graph) -> dict[str, Any]:
    sectors = root / "sectors"
    require(
        sectors.is_dir(),
        "PROJECT_UNIVERSE_SECTORS_MISSING",
        "The live project sectors are missing.",
    )
    present = {path.name for path in sectors.iterdir() if path.is_dir()}
    missing = sorted(set(CANONICAL_LANE_IDS) - present)
    require(
        not missing,
        "PROJECT_UNIVERSE_LANE_MISSING",
        "The Project Universe requires all canonical live sector roots.",
        status="MISMATCH",
        missing=missing,
    )
    project = graph.add_node(
        "PROJECT",
        f"project://{project_id}",
        project_id,
        {"project_id": project_id, "authority_root": str(root)},
    )
    lane_rows: list[dict[str, Any]] = []
    for lane_id in CANONICAL_LANE_IDS:
        lane_root = sectors / lane_id
        reference = _json(
            lane_root / "authority.ref.json",
            code="PROJECT_UNIVERSE_LANE_REFERENCE_MISSING",
        )
        require(
            reference.get("project_id") == project_id
            and reference.get("lane_id") == lane_id
            and reference.get("state") == "LIVE_WORKING"
            and reference.get("candidate_directory_created") is False
            and reference.get("pointer_moved") is False,
            "PROJECT_UNIVERSE_LANE_REFERENCE_MISMATCH",
            "A live sector is not bound to the exact project working authority.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        database = _lane_database(root, lane_id)
        connection = _connect_ro(database)
        try:
            tables = _tables(connection)
            counts = {
                "source_count": _count(connection, "source_registry", tables),
                "fact_count": _count(connection, "structured_fact", tables),
                "chunk_count": _count(connection, "chunk_index", tables),
                "refresh_count": _count(connection, "refresh_receipt", tables),
                "task_count": _count(connection, "delta_task", tables),
                "event_count": _count(connection, "delta_event", tables),
            }
            lane = graph.add_node(
                "LANE",
                f"project://{project_id}/lane/{lane_id}",
                LANE_REGISTRY[lane_id].display_label,
                {
                    "lane_id": lane_id,
                    "sqlite_relative_path": database.relative_to(root).as_posix(),
                    "sqlite_sha256": sha256_file(database),
                    "working_identity_sha256": reference.get("working_identity_sha256"),
                    "historical_parent_pv": reference.get("historical_parent_pv"),
                    "pointer_generation": reference.get("pointer_generation"),
                    **counts,
                },
            )
            graph.add_edge("CONTAINS_LANE", project, lane)
            if "source_registry" in tables:
                for row in connection.execute(
                    "SELECT source_id, path, size_bytes, sha256, parser_state "
                    "FROM source_registry ORDER BY source_id"
                ):
                    source_id = str(row["source_id"])
                    source = graph.add_node(
                        "SOURCE",
                        f"project://{project_id}/lane/{lane_id}/source/{source_id}",
                        Path(str(row["path"])).name or source_id,
                        {
                            "source_id": source_id,
                            "lane_id": lane_id,
                            "path": str(row["path"]),
                            "size_bytes": int(row["size_bytes"]),
                            "sha256": str(row["sha256"]),
                            "parser_state": str(row["parser_state"]),
                        },
                    )
                    graph.add_edge("INDEXES_SOURCE", lane, source)
            lane_rows.append({"lane_id": lane_id, **counts})
        finally:
            connection.close()
    return {"project_node": project, "lanes": lane_rows}


def _plan_snapshot(root: Path, *, project_id: str, graph: _Graph) -> dict[str, Any]:
    database = resolved_plan_runtime_path(root)
    connection = _connect_ro(database)
    try:
        rows = list(
            connection.execute(
                "SELECT task_id, row_number, lifecycle_status, host_status, "
                "task_classification, plan_group, commit_batch_id, dependencies_json, "
                "effective_for_execution, task_contract_sha256 "
                "FROM plan_execution_row ORDER BY plan_sequence"
            )
        )
        lane = graph.nodes[f"project://{project_id}/lane/plan"]
        by_task: dict[str, dict[str, Any]] = {}
        status_counts: Counter[str] = Counter()
        for row in rows:
            task_id = str(row["task_id"])
            status = str(row["lifecycle_status"])
            status_counts[status] += 1
            task = graph.add_node(
                "TASK",
                f"project://{project_id}/task/{task_id}",
                task_id,
                {
                    "task_id": task_id,
                    "row_number": row["row_number"],
                    "lifecycle_status": status,
                    "host_status": str(row["host_status"]),
                    "task_classification": str(row["task_classification"]),
                    "plan_group": str(row["plan_group"]),
                    "commit_batch_id": str(row["commit_batch_id"]),
                    "effective_for_execution": bool(row["effective_for_execution"]),
                    "task_contract_sha256": str(row["task_contract_sha256"]),
                },
            )
            graph.add_edge("GOVERNS_TASK", lane, task)
            by_task[task_id] = task
        unresolved_dependencies: list[dict[str, str]] = []
        for row in rows:
            task_id = str(row["task_id"])
            try:
                dependencies = json.loads(str(row["dependencies_json"] or "[]"))
            except json.JSONDecodeError:
                dependencies = []
            for dependency in dependencies if isinstance(dependencies, list) else []:
                dependency_id = str(dependency)
                if dependency_id in by_task:
                    graph.add_edge(
                        "DEPENDS_ON_TASK",
                        by_task[task_id],
                        by_task[dependency_id],
                    )
                else:
                    unresolved_dependencies.append(
                        {"task_id": task_id, "dependency_task_id": dependency_id}
                    )
        return {
            "task_count": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "unresolved_dependencies": unresolved_dependencies,
            "plan_sqlite_sha256": sha256_file(database),
        }
    finally:
        connection.close()


def _pointer_snapshot(root: Path, *, project_id: str, graph: _Graph) -> dict[str, Any]:
    pointer = _json(root / "active_pointer.json", code="PROJECT_UNIVERSE_POINTER_MISSING")
    require(
        pointer.get("project_id") == project_id,
        "PROJECT_UNIVERSE_POINTER_MISMATCH",
        "The accepted pointer does not match the exact Project Universe binding.",
        status="MISMATCH",
    )
    project = graph.nodes[f"project://{project_id}"]
    accepted = graph.add_node(
        "ACCEPTED_POINTER",
        f"project://{project_id}/accepted/{pointer.get('accepted_pv')}",
        str(pointer.get("accepted_pv")),
        {
            "accepted_pv": pointer.get("accepted_pv"),
            "generation": pointer.get("generation"),
            "accepted_manifest_sha256": pointer.get("accepted_manifest_sha256"),
        },
    )
    graph.add_edge("HAS_ACCEPTED_POINTER", project, accepted)
    for lane_id in CANONICAL_LANE_IDS:
        graph.add_edge(
            "BASELINES_LANE",
            accepted,
            graph.nodes[f"project://{project_id}/lane/{lane_id}"],
        )
    return pointer


def _source_fingerprint(
    lane_rows: list[dict[str, Any]], plan: Mapping[str, Any], pointer: Mapping[str, Any]
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "lanes": lane_rows,
                "plan_sqlite_sha256": plan["plan_sqlite_sha256"],
                "accepted_pv": pointer.get("accepted_pv"),
                "accepted_manifest_sha256": pointer.get("accepted_manifest_sha256"),
                "generation": pointer.get("generation"),
            }
        )
    )


def _write_database(
    path: Path,
    *,
    graph: _Graph,
    project_id: str,
    source_fingerprint_sha256: str,
    recorded_at: str,
) -> tuple[str, dict[str, int]]:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_schema_sql())
        nodes = sorted(graph.nodes.values(), key=lambda row: str(row["node_id"]))
        edges = sorted(graph.edges.values(), key=lambda row: str(row["edge_id"]))
        kinds = Counter(str(row["node_kind"]) for row in nodes)
        relations = Counter(str(row["relation"]) for row in edges)
        metrics = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "lane_count": kinds["LANE"],
            "source_count": kinds["SOURCE"],
            "task_count": kinds["TASK"],
            "active_task_count": sum(
                1
                for row in nodes
                if row["node_kind"] == "TASK"
                and row["attributes"].get("lifecycle_status") == "ACTIVE"
            ),
            "relation_kind_count": len(relations),
        }
        graph_sha256 = sha256_bytes(
            canonical_json_bytes({"nodes": nodes, "edges": edges, "metrics": metrics})
        )
        connection.executemany(
            "INSERT INTO universe_node VALUES(?,?,?,?,?,?)",
            [
                (
                    row["node_id"],
                    row["node_kind"],
                    row["canonical_locator"],
                    row["label"],
                    json.dumps(row["attributes"], sort_keys=True, separators=(",", ":")),
                    row["record_sha256"],
                )
                for row in nodes
            ],
        )
        connection.executemany(
            "INSERT INTO universe_edge VALUES(?,?,?,?,?,?)",
            [
                (
                    row["edge_id"],
                    row["relation"],
                    row["source_node_id"],
                    row["destination_node_id"],
                    json.dumps(row["attributes"], sort_keys=True, separators=(",", ":")),
                    row["record_sha256"],
                )
                for row in edges
            ],
        )
        connection.executemany(
            "INSERT INTO universe_metric VALUES(?,?)", sorted(metrics.items())
        )
        connection.executemany(
            "INSERT INTO universe_meta VALUES(?,?)",
            sorted(
                {
                    "schema": UNIVERSE_SCHEMA,
                    "project_id": project_id,
                    "source_fingerprint_sha256": source_fingerprint_sha256,
                    "graph_sha256": graph_sha256,
                }.items()
            ),
        )
        connection.execute(
            "INSERT INTO universe_refresh VALUES(?,?,?,?,?,?)",
            (
                "urefresh_" + graph_sha256[:24].lower(),
                source_fingerprint_sha256,
                graph_sha256,
                recorded_at,
                metrics["node_count"],
                metrics["edge_count"],
            ),
        )
        connection.executemany(
            "INSERT INTO universe_fts VALUES(?,?,?,?,?)",
            [
                (
                    row["node_id"],
                    row["node_kind"],
                    row["label"],
                    row["canonical_locator"],
                    json.dumps(row["attributes"], sort_keys=True),
                )
                for row in nodes
            ],
        )
        connection.commit()
        return graph_sha256, metrics
    finally:
        connection.close()


def _projections(project_id: str, lane_rows: list[dict[str, Any]], metrics: Mapping[str, int]) -> tuple[bytes, bytes]:
    mmd = ["graph TD", f'  P["{project_id}"]']
    dot = ["digraph project_universe {", f'  project [label="{project_id}"];']
    for row in lane_rows:
        lane_id = str(row["lane_id"])
        label = LANE_REGISTRY[lane_id].display_label.replace('"', "'")
        mmd.append(f'  P --> L_{lane_id}["{label}"]')
        dot.append(f'  lane_{lane_id} [label="{label}"];')
        dot.append(f"  project -> lane_{lane_id};")
    mmd.append(f'  P --> T["Tasks: {metrics["task_count"]}"]')
    mmd.append(f'  P --> S["Sources: {metrics["source_count"]}"]')
    dot.extend(["  project -> task_count;", "  project -> source_count;", "}"])
    return ("\n".join(mmd) + "\n").encode(), ("\n".join(dot) + "\n").encode()


def _validate_bundle(root: Path, *, project_id: str) -> dict[str, Any]:
    manifest = _json(root / "PROJECT_UNIVERSE_MANIFEST.json", code="PROJECT_UNIVERSE_MANIFEST_MISSING")
    require(
        manifest.get("schema") == UNIVERSE_MANIFEST_SCHEMA
        and manifest.get("project_id") == project_id,
        "PROJECT_UNIVERSE_MANIFEST_MISMATCH",
        "The Project Universe manifest is not bound to this project.",
        status="MISMATCH",
    )
    members = cast(list[dict[str, Any]], manifest.get("members") or [])
    for member in members:
        path = root / str(member.get("path"))
        require(
            path.is_file() and sha256_file(path) == member.get("sha256"),
            "PROJECT_UNIVERSE_MEMBER_MISMATCH",
            "A Project Universe artifact is missing or changed.",
            status="MISMATCH",
            path=str(member.get("path")),
        )
    connection = _connect_ro(root / UNIVERSE_DB)
    try:
        meta = dict(connection.execute("SELECT key, value FROM universe_meta"))
        metrics = {
            str(row[0]): int(row[1])
            for row in connection.execute("SELECT metric_key, metric_value FROM universe_metric")
        }
        actual_nodes = int(connection.execute("SELECT COUNT(*) FROM universe_node").fetchone()[0])
        actual_edges = int(connection.execute("SELECT COUNT(*) FROM universe_edge").fetchone()[0])
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    finally:
        connection.close()
    require(
        meta.get("project_id") == project_id
        and metrics.get("lane_count") == len(CANONICAL_LANE_IDS)
        and metrics.get("node_count") == actual_nodes
        and metrics.get("edge_count") == actual_edges
        and not foreign_keys,
        "PROJECT_UNIVERSE_DATABASE_MISMATCH",
        "The Project Universe database does not match its declared telemetry.",
        status="MISMATCH",
    )
    return {
        "status": "PASS",
        "project_id": project_id,
        "graph_sha256": meta["graph_sha256"],
        "source_fingerprint_sha256": meta["source_fingerprint_sha256"],
        "metrics": metrics,
        "member_count": len(members),
        "manifest_sha256": manifest.get("manifest_sha256"),
    }


def refresh_project_universe(
    project_root: str | Path,
    *,
    project_id: str,
    active_plan_task_id: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    root = _root(project_root, project_id=project_id)
    graph = _Graph()
    lanes = _lane_snapshot(root, project_id=project_id, graph=graph)
    plan = _plan_snapshot(root, project_id=project_id, graph=graph)
    pointer = _pointer_snapshot(root, project_id=project_id, graph=graph)
    source_fingerprint = _source_fingerprint(lanes["lanes"], plan, pointer)
    exact_time = recorded_at or utc_now()
    refresh_id = "urefresh_" + sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "active_plan_task_id": active_plan_task_id,
                "source_fingerprint_sha256": source_fingerprint,
            }
        )
    )[:24].lower()
    staging = root / f".{UNIVERSE_DIR}-{refresh_id}.staging"
    require(
        not staging.exists(),
        "PROJECT_UNIVERSE_STAGING_EXISTS",
        "A Project Universe staging directory already exists.",
        status="BLOCKED",
    )
    staging.mkdir(parents=False)
    try:
        graph_sha256, metrics = _write_database(
            staging / UNIVERSE_DB,
            graph=graph,
            project_id=project_id,
            source_fingerprint_sha256=source_fingerprint,
            recorded_at=exact_time,
        )
        mmd, dot = _projections(project_id, lanes["lanes"], metrics)
        atomic_write_bytes(staging / "project_universe.mmd", mmd)
        atomic_write_bytes(staging / "project_universe.dot", dot)
        summary = {
            "schema": UNIVERSE_BUNDLE_SCHEMA,
            "status": "PASS",
            "project_id": project_id,
            "source_fingerprint_sha256": source_fingerprint,
            "graph_sha256": graph_sha256,
            "metrics": metrics,
            "task_status_counts": plan["status_counts"],
            "unresolved_dependency_count": len(plan["unresolved_dependencies"]),
            "canonical_lane_ids": list(CANONICAL_LANE_IDS),
            "candidate_created": False,
            "hil_inferred": False,
            "pointer_moved": False,
        }
        atomic_write_json(staging / "project_universe.json", summary)
        atomic_write_json(
            staging / "project_universe.tools.json",
            {
                "schema": "evidence-lane.project-universe-tools.v1",
                "project_id": project_id,
                "read_routes": ["project_universe_status", "project_universe_query"],
                "write_routes": ["project_universe_refresh"],
                "max_query_limit": _MAX_QUERY_LIMIT,
                "hard_coded_capability_counts": False,
            },
        )
        atomic_write_json(
            staging / "project_universe_pointer.json",
            {
                "schema": UNIVERSE_POINTER_SCHEMA,
                "project_id": project_id,
                "source_fingerprint_sha256": source_fingerprint,
                "graph_sha256": graph_sha256,
                "accepted_pv": pointer.get("accepted_pv"),
                "pointer_generation": pointer.get("generation"),
                "active_plan_task_id": active_plan_task_id,
            },
        )
        atomic_write_json(
            staging / "project_universe_refresh_receipt.json",
            {
                "schema": UNIVERSE_RECEIPT_SCHEMA,
                "status": "PASS",
                "refresh_id": refresh_id,
                "project_id": project_id,
                "recorded_at": exact_time,
                "source_fingerprint_sha256": source_fingerprint,
                "graph_sha256": graph_sha256,
                "metrics": metrics,
                "candidate_created": False,
                "pending_hil": False,
                "pointer_moved": False,
            },
        )
        member_names = sorted(
            path.name for path in staging.iterdir() if path.is_file()
        )
        members = [
            {
                "path": name,
                "bytes": (staging / name).stat().st_size,
                "sha256": sha256_file(staging / name),
            }
            for name in member_names
        ]
        manifest_body = {
            "schema": UNIVERSE_MANIFEST_SCHEMA,
            "project_id": project_id,
            "source_fingerprint_sha256": source_fingerprint,
            "graph_sha256": graph_sha256,
            "members": members,
        }
        atomic_write_json(
            staging / "PROJECT_UNIVERSE_MANIFEST.json",
            {
                **manifest_body,
                "manifest_sha256": sha256_bytes(canonical_json_bytes(manifest_body)),
            },
        )
        staged = _validate_bundle(staging, project_id=project_id)
        destination = root / UNIVERSE_DIR
        backup = root / f".{UNIVERSE_DIR}-{refresh_id}.previous"
        require(
            not backup.exists(),
            "PROJECT_UNIVERSE_BACKUP_EXISTS",
            "A Project Universe backup directory already exists.",
            status="BLOCKED",
        )
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except Exception:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        final = _validate_bundle(destination, project_id=project_id)
        require(
            final == staged,
            "PROJECT_UNIVERSE_COMMIT_MISMATCH",
            "The committed Project Universe differs from its verified staging bundle.",
            status="MISMATCH",
        )
        return {"schema": UNIVERSE_RECEIPT_SCHEMA, **final, "refresh_id": refresh_id}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def inspect_project_universe(
    project_root: str | Path, *, project_id: str
) -> dict[str, Any]:
    root = _root(project_root, project_id=project_id) / UNIVERSE_DIR
    require(
        root.is_dir(),
        "PROJECT_UNIVERSE_NOT_MATERIALIZED",
        "The real Project Universe has not been materialized.",
        status="BLOCKED",
    )
    return {"schema": UNIVERSE_STATUS_SCHEMA, **_validate_bundle(root, project_id=project_id)}


def query_project_universe(
    project_root: str | Path,
    *,
    project_id: str,
    query: str = "",
    node_kind: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    status = inspect_project_universe(project_root, project_id=project_id)
    require(
        1 <= int(limit) <= _MAX_QUERY_LIMIT,
        "PROJECT_UNIVERSE_QUERY_LIMIT_INVALID",
        "A Project Universe query must use a bounded limit from 1 through 100.",
        status="BLOCKED",
        limit=limit,
    )
    exact_kind = str(node_kind or "").strip().upper()
    exact_query = str(query or "").strip()
    database = _root(project_root, project_id=project_id) / UNIVERSE_DIR / UNIVERSE_DB
    connection = _connect_ro(database)
    try:
        clauses: list[str] = []
        values: list[Any] = []
        if exact_kind:
            clauses.append("node_kind = ?")
            values.append(exact_kind)
        if exact_query:
            clauses.append(
                "(label LIKE ? ESCAPE '\\' OR canonical_locator LIKE ? ESCAPE '\\' "
                "OR attributes_json LIKE ? ESCAPE '\\')"
            )
            escaped = exact_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.extend([f"%{escaped}%"] * 3)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = list(
            connection.execute(
                "SELECT node_id, node_kind, canonical_locator, label, attributes_json, "
                "record_sha256 FROM universe_node"
                + where
                + " ORDER BY node_kind, canonical_locator LIMIT ?",
                [*values, int(limit) + 1],
            )
        )
    finally:
        connection.close()
    truncated = len(rows) > int(limit)
    return {
        "schema": UNIVERSE_QUERY_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "query_sha256": sha256_bytes(exact_query.encode("utf-8")),
        "node_kind": exact_kind or None,
        "limit": int(limit),
        "truncated": truncated,
        "hits": [
            {
                "node_id": row["node_id"],
                "node_kind": row["node_kind"],
                "canonical_locator": row["canonical_locator"],
                "label": row["label"],
                "attributes": json.loads(row["attributes_json"]),
                "record_sha256": row["record_sha256"],
            }
            for row in rows[: int(limit)]
        ],
        "graph_sha256": status["graph_sha256"],
        "full_graph_returned": False,
    }
