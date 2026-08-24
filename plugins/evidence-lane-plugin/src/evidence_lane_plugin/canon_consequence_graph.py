"""Content-addressed Canon consequence graph over live project authority.

The operational Canon ledger remains the sole owner of contracts, packets,
receiver decisions, task edges, results, and backfires.  This module builds a
separate immutable projection that makes their consequences traversable beside
the canonical Plan, project sectors, accepted pointer, and Agent Learning
locators.  Rebuilding the projection never accepts Canon input or Learning,
creates a Project candidate, invokes HIL, or moves the Project pointer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from .errors import EvidenceLaneError, require
from .hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lanes import lane_artifact_contract, lane_schema_asset
from .project_authority import resolved_plan_runtime_path
from .timeutil import utc_now

CONSEQUENCE_GRAPH_SCHEMA = "evidence-lane.canon-consequence-graph.v1"
CONSEQUENCE_GRAPH_BUNDLE_SCHEMA = "evidence-lane.canon-consequence-graph-bundle.v1"
CONSEQUENCE_GRAPH_RECEIPT_SCHEMA = (
    "evidence-lane.canon-consequence-graph-refresh-receipt.v1"
)
CONSEQUENCE_GRAPH_POINTER_SCHEMA = "evidence-lane.canon-consequence-graph-pointer.v1"

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_TASK_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_QUERY_TOKEN_RE = re.compile(r"[\w][\w./:@-]{1,127}", flags=re.UNICODE)
_ROOT_INVENTORY_EXCLUDED_TOP_LEVEL = frozenset(
    {"accepted", "ai_learning", "memory"}
)
_ROOT_INVENTORY_EXCLUDED_RELATIVE_PREFIXES = (
    "canon/consequence-graphs/",
    "canon/consequence-graph-current.json",
)


def _project_root(project_root: str | Path, *, project_id: str) -> Path:
    root = Path(project_root).resolve()
    require(
        root.name == project_id,
        "CANON_CONSEQUENCE_PROJECT_ROOT_MISMATCH",
        "The Canon consequence graph root does not match the exact project identity.",
        status="MISMATCH",
        project_id=project_id,
    )
    return root


def _json(path: Path, *, code: str) -> dict[str, Any]:
    require(path.is_file(), code, "A required Canon consequence input is missing.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        require(False, code, "A required Canon consequence input is invalid JSON.")
        raise AssertionError from exc
    require(
        isinstance(value, dict), code, "A Canon consequence input must be one object."
    )
    return cast(dict[str, Any], value)


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256_bytes(
        canonical_json_bytes({key: item for key, item in value.items() if key != field})
    )


def _sector_reference_matches(
    value: Mapping[str, Any],
    *,
    project_id: str,
    lane_id: str,
    accepted_pv: str,
    pointer_generation: int,
) -> bool:
    """Accept either an immutable PV reference or its exact live-working overlay."""

    if value.get("lane_id") != lane_id:
        return False
    if value.get("schema") == "evidence-lane.project-sector-reference.v1":
        return value.get("accepted_pv") == accepted_pv
    if value.get("schema") != "evidence-lane.working-sector-authority.v1":
        return False
    working_identity = str(value.get("working_identity_sha256") or "").upper()
    return bool(
        value.get("state") == "LIVE_WORKING"
        and value.get("project_id") == project_id
        and value.get("historical_parent_pv") == accepted_pv
        and value.get("pointer_generation") == pointer_generation
        and _SHA256_RE.fullmatch(working_identity)
        and value.get("candidate_directory_created") is False
        and value.get("pointer_moved") is False
    )


def _validate_sha(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "CANON_CONSEQUENCE_SHA256_INVALID",
        "A Canon consequence identity is not one exact SHA-256.",
        field=field,
    )
    return exact


def _schema_asset() -> tuple[Path, str]:
    candidates = (
        Path(__file__).resolve().parent
        / "schemas"
        / "canon"
        / "canon-consequence-graph.v1.sql",
        Path(__file__).resolve().parents[3]
        / "schemas"
        / "canon"
        / "canon-consequence-graph.v1.sql",
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    require(
        path is not None,
        "CANON_CONSEQUENCE_SCHEMA_ASSET_MISSING",
        "The first-class Canon consequence graph SQL asset is missing.",
    )
    exact_path = cast(Path, path)
    return exact_path, exact_path.read_text(encoding="utf-8")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    require(
        path.is_file(),
        "CANON_CONSEQUENCE_SQLITE_MISSING",
        "A required authority database is missing.",
    )
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    require(
        integrity == ["ok"] and not foreign_keys,
        "CANON_CONSEQUENCE_SOURCE_SQLITE_INVALID",
        "A Canon consequence source database failed integrity or foreign-key checks.",
        status="FAIL",
        path_role=path.name,
        integrity=integrity,
        foreign_key_error_count=len(foreign_keys),
    )
    return connection


def _node(
    node_kind: str, canonical_locator: str, attributes: Mapping[str, Any]
) -> dict[str, Any]:
    body = {
        "schema": CONSEQUENCE_GRAPH_SCHEMA,
        "record_type": "NODE",
        "node_kind": str(node_kind).strip().upper(),
        "canonical_locator": str(canonical_locator).strip(),
        "attributes": dict(attributes),
    }
    digest = sha256_bytes(canonical_json_bytes(body))
    return {
        **body,
        "node_id": f"cnode_{digest[:24].lower()}",
        "record_sha256": digest,
    }


def _edge(
    relation: str,
    source: Mapping[str, Any],
    destination: Mapping[str, Any],
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    exact_relation = str(relation).strip().upper()
    locator_body = {
        "relation": exact_relation,
        "source_node_id": source["node_id"],
        "destination_node_id": destination["node_id"],
        "attributes": dict(attributes or {}),
    }
    locator_digest = sha256_bytes(canonical_json_bytes(locator_body))
    canonical_locator = f"canon-consequence-edge://{locator_digest.lower()}"
    body = {
        "schema": CONSEQUENCE_GRAPH_SCHEMA,
        "record_type": "EDGE",
        **locator_body,
        "canonical_locator": canonical_locator,
    }
    digest = sha256_bytes(canonical_json_bytes(body))
    return {
        **body,
        "edge_id": f"cedge_{digest[:24].lower()}",
        "record_sha256": digest,
    }


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}

    def add_node(
        self, node_kind: str, canonical_locator: str, attributes: Mapping[str, Any]
    ) -> dict[str, Any]:
        existing = self.nodes.get(canonical_locator)
        candidate = _node(node_kind, canonical_locator, attributes)
        if existing is not None:
            require(
                existing["record_sha256"] == candidate["record_sha256"],
                "CANON_CONSEQUENCE_NODE_LOCATOR_CONFLICT",
                "One canonical Canon node locator maps to different bytes.",
                status="MISMATCH",
                canonical_locator=canonical_locator,
            )
            return existing
        self.nodes[canonical_locator] = candidate
        return candidate

    def add_edge(
        self,
        relation: str,
        source: Mapping[str, Any],
        destination: Mapping[str, Any],
        attributes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = _edge(relation, source, destination, attributes)
        locator = cast(str, candidate["canonical_locator"])
        existing = self.edges.get(locator)
        if existing is not None:
            require(
                existing["record_sha256"] == candidate["record_sha256"],
                "CANON_CONSEQUENCE_EDGE_LOCATOR_CONFLICT",
                "One canonical Canon edge locator maps to different bytes.",
                status="MISMATCH",
                canonical_locator=locator,
            )
            return existing
        self.edges[locator] = candidate
        return candidate


def _endpoint_node(graph: _Graph, endpoint: Mapping[str, Any]) -> dict[str, Any]:
    task_uuid = str(endpoint.get("task_uuid") or "").strip()
    deep_link = str(endpoint.get("task_deep_link") or "").strip()
    project_id = str(endpoint.get("project_id") or "").strip()
    require(
        bool(task_uuid and deep_link and project_id),
        "CANON_CONSEQUENCE_ENDPOINT_INVALID",
        "An operational Canon endpoint lacks exact project, task, or deep-link identity.",
        status="MISMATCH",
    )
    return graph.add_node(
        "HOST_TASK",
        deep_link,
        {
            "project_id": project_id,
            "task_uuid": task_uuid,
            "lane_id": endpoint.get("lane_id"),
            "delta_id": endpoint.get("delta_id"),
            "session_id": endpoint.get("session_id"),
        },
    )


def _live_root_inventory(root: Path) -> list[dict[str, Any]]:
    """Hash the live authority root without reading HIL archives or self-output."""

    members: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        top_level = relative.split("/", 1)[0]
        if top_level in _ROOT_INVENTORY_EXCLUDED_TOP_LEVEL:
            continue
        if any(
            relative == prefix.rstrip("/") or relative.startswith(prefix)
            for prefix in _ROOT_INVENTORY_EXCLUDED_RELATIVE_PREFIXES
        ):
            continue
        if path.is_dir():
            members.append(
                {
                    "path": relative,
                    "kind": "DIRECTORY",
                    "size_bytes": 0,
                    "sha256": sha256_bytes(relative.encode("utf-8")),
                }
            )
            continue
        if not path.is_file():
            continue
        members.append(
            {
                "path": relative,
                "kind": "FILE",
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return members


def _load_project_layout(
    root: Path,
    *,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    accepted_manifest_sha256: str,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], str]]]:
    layout_path = root / "project_authority.json"
    layout = _json(layout_path, code="CANON_CONSEQUENCE_PROJECT_LAYOUT_REQUIRED")
    require(
        layout.get("schema") == "evidence-lane.project-authority-layout.v1"
        and layout.get("project_id") == project_id
        and layout.get("layout_sha256") == _hash_without(layout, "layout_sha256"),
        "CANON_CONSEQUENCE_PROJECT_LAYOUT_MISMATCH",
        "The project authority layout identity is invalid.",
        status="MISMATCH",
    )
    pointer = dict(layout.get("accepted_pointer") or {})
    require(
        pointer.get("accepted_pv") == accepted_pv
        and pointer.get("generation") == pointer_generation
        and pointer.get("accepted_manifest_sha256") == accepted_manifest_sha256
        and pointer.get("moved") is False,
        "CANON_CONSEQUENCE_POINTER_BINDING_MISMATCH",
        "Project authority and the live accepted pointer do not agree.",
        status="MISMATCH",
    )
    lane_ids = [str(value) for value in layout.get("ordered_lane_ids") or []]
    require(
        len(lane_ids) == 18 and len(set(lane_ids)) == 18,
        "CANON_CONSEQUENCE_SECTOR_REGISTRY_MISMATCH",
        "The consequence graph requires exactly eighteen unique project sectors.",
        status="MISMATCH",
    )
    sectors: list[tuple[dict[str, Any], str]] = []
    for lane_id in lane_ids:
        path = root / "sectors" / lane_id / "authority.ref.json"
        value = _json(path, code="CANON_CONSEQUENCE_SECTOR_REFERENCE_REQUIRED")
        require(
            _sector_reference_matches(
                value,
                project_id=project_id,
                lane_id=lane_id,
                accepted_pv=accepted_pv,
                pointer_generation=pointer_generation,
            ),
            "CANON_CONSEQUENCE_SECTOR_REFERENCE_MISMATCH",
            "A project-sector reference does not match its canonical lane or pointer.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        normalized = dict(value)
        if value.get("schema") == "evidence-lane.working-sector-authority.v1":
            normalized["accepted_pv"] = accepted_pv
            normalized["lane_schema_contract_sha256"] = lane_schema_asset(lane_id)[
                "contract_sha256"
            ]
            normalized["artifact_contract_sha256"] = lane_artifact_contract(lane_id)[
                "contract_sha256"
            ]
        sectors.append((normalized, sha256_file(path)))
    return layout, sectors


def _plan_rows(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    path = resolved_plan_runtime_path(root)
    digest = sha256_file(path)
    connection = _connect_read_only(path)
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT task_id,plan_sequence,projection_lane,row_number,history_number,
                       lifecycle_status,panel_role,plan_group,commit_batch_id,
                       dependencies_json,effective_for_execution,task_contract_sha256,
                       linked_delta_ids_json
                FROM plan_execution_row ORDER BY plan_sequence
                """
            ).fetchall()
        ]
        steers = [
            dict(row)
            for row in connection.execute(
                """
                SELECT delta_id,task_id,linked_task_id,boundary,classification,
                       delta_sha256,task_steer_sequence
                FROM steer_delta ORDER BY sequence
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    require(
        bool(rows) and sum(row["lifecycle_status"] == "ACTIVE" for row in rows) == 1,
        "CANON_CONSEQUENCE_PLAN_INVARIANT_FAILED",
        "The Canon consequence graph requires one nonempty Plan with one active row.",
        status="MISMATCH",
    )
    return rows, steers, digest


def _learning_rows(root: Path) -> tuple[list[dict[str, Any]], str | None]:
    path = root / "ai_learning" / "agent-learning.sqlite"
    if not path.is_file():
        return [], None
    digest = sha256_file(path)
    connection = _connect_read_only(path)
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT candidate.candidate_id,candidate.candidate_sha256,
                       candidate.tier,candidate.lesson_type,candidate.candidate_json,
                       (
                         SELECT event.lifecycle_state FROM learning_event AS event
                         WHERE event.candidate_id=candidate.candidate_id
                         ORDER BY event.sequence DESC LIMIT 1
                       ) AS lifecycle_state
                FROM learning_candidate AS candidate
                ORDER BY candidate.candidate_id
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    return rows, digest


def _canon_operational_rows(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    path = root / "canon" / "canon-input.sqlite"
    digest = sha256_file(path)
    connection = _connect_read_only(path)
    try:
        edges = [
            cast(dict[str, Any], json.loads(str(row[0])))
            for row in connection.execute(
                "SELECT edge_json FROM canon_edge ORDER BY rowid"
            ).fetchall()
        ]
        packets = [
            {
                "canon_id": str(row["canon_id"]),
                "canon_type": str(row["canon_type"]),
                "current_state": str(row["current_state"]),
                "envelope": cast(dict[str, Any], json.loads(str(row["envelope_json"]))),
            }
            for row in connection.execute(
                """
                SELECT canon_id,canon_type,current_state,envelope_json
                FROM canon_packet
                WHERE canon_type IN ('TASK_RESULT','CANON_BACKFIRE')
                ORDER BY rowid
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    return edges, packets, digest


def _render_mermaid(
    nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]]
) -> str:
    lines = ["flowchart LR"]
    for node in nodes:
        label = f"{node['node_kind']}\\n{str(node['canonical_locator'])[:88]}"
        label = label.replace('"', "'")
        lines.append(f'  {node["node_id"]}["{label}"]')
    for edge in edges:
        lines.append(
            f"  {edge['source_node_id']} -->|{edge['relation']}| {edge['destination_node_id']}"
        )
    return "\n".join(lines) + "\n"


def _render_dot(
    nodes: Iterable[Mapping[str, Any]], edges: Iterable[Mapping[str, Any]]
) -> str:
    lines = ["digraph CanonConsequence {", "  rankdir=LR;"]
    for node in nodes:
        label = f"{node['node_kind']}\\n{str(node['canonical_locator'])[:88]}"
        label = label.replace('"', "'")
        lines.append(f'  {node["node_id"]} [label="{label}"];')
    for edge in edges:
        lines.append(
            f"  {edge['source_node_id']} -> {edge['destination_node_id']} "
            f'[label="{edge["relation"]}"];'
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _write_graph_sqlite(
    path: Path,
    *,
    source_snapshot: Mapping[str, Any],
    graph_fingerprint_sha256: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    _, sql = _schema_asset()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(sql)
        metadata = {
            "schema": CONSEQUENCE_GRAPH_SCHEMA,
            "graph_fingerprint_sha256": graph_fingerprint_sha256,
            "source_snapshot_json": canonical_json_bytes(source_snapshot).decode(
                "utf-8"
            ),
            "node_count": str(len(nodes)),
            "edge_count": str(len(edges)),
        }
        connection.executemany(
            "INSERT INTO graph_metadata(key,value) VALUES(?,?)",
            sorted(metadata.items()),
        )
        connection.executemany(
            """
            INSERT INTO consequence_node(
                node_id,node_kind,canonical_locator,record_json,record_sha256
            ) VALUES(?,?,?,?,?)
            """,
            [
                (
                    node["node_id"],
                    node["node_kind"],
                    node["canonical_locator"],
                    canonical_json_bytes(node).decode("utf-8"),
                    node["record_sha256"],
                )
                for node in nodes
            ],
        )
        connection.executemany(
            """
            INSERT INTO consequence_edge(
                edge_id,relation,source_node_id,destination_node_id,
                canonical_locator,record_json,record_sha256
            ) VALUES(?,?,?,?,?,?,?)
            """,
            [
                (
                    edge["edge_id"],
                    edge["relation"],
                    edge["source_node_id"],
                    edge["destination_node_id"],
                    edge["canonical_locator"],
                    canonical_json_bytes(edge).decode("utf-8"),
                    edge["record_sha256"],
                )
                for edge in edges
            ],
        )
        connection.executemany(
            """
            INSERT INTO consequence_graph_fts(
                record_id,record_type,record_kind,canonical_locator,searchable_text
            ) VALUES(?,?,?,?,?)
            """,
            [
                (
                    node["node_id"],
                    "NODE",
                    node["node_kind"],
                    node["canonical_locator"],
                    canonical_json_bytes(node).decode("utf-8"),
                )
                for node in nodes
            ]
            + [
                (
                    edge["edge_id"],
                    "EDGE",
                    edge["relation"],
                    edge["canonical_locator"],
                    canonical_json_bytes(edge).decode("utf-8"),
                )
                for edge in edges
            ],
        )
        connection.commit()
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        require(
            integrity == ["ok"] and not foreign_keys,
            "CANON_CONSEQUENCE_GRAPH_SQLITE_INVALID",
            "The generated Canon consequence graph failed integrity checks.",
            status="FAIL",
        )
    finally:
        connection.close()


def _verify_bundle(bundle_root: Path, manifest: Mapping[str, Any]) -> None:
    require(
        manifest.get("schema") == CONSEQUENCE_GRAPH_BUNDLE_SCHEMA,
        "CANON_CONSEQUENCE_BUNDLE_SCHEMA_INVALID",
        "The Canon consequence graph bundle schema is invalid.",
        status="MISMATCH",
    )
    for member in manifest.get("members") or []:
        require(
            isinstance(member, dict),
            "CANON_CONSEQUENCE_BUNDLE_MEMBER_INVALID",
            "A Canon consequence bundle member record is invalid.",
        )
        relative_path = str(member.get("path") or "")
        require(
            relative_path in {"graph.sqlite", "graph.mmd", "graph.dot"},
            "CANON_CONSEQUENCE_BUNDLE_MEMBER_INVALID",
            "A Canon consequence bundle contains an unknown member.",
        )
        path = bundle_root / relative_path
        require(
            path.is_file() and sha256_file(path) == member.get("sha256"),
            "CANON_CONSEQUENCE_BUNDLE_MEMBER_MISMATCH",
            "A Canon consequence bundle member failed its hash check.",
            status="MISMATCH",
            path=relative_path,
        )
    connection = _connect_read_only(bundle_root / "graph.sqlite")
    try:
        counts = {
            "node_count": int(
                connection.execute("SELECT COUNT(*) FROM consequence_node").fetchone()[
                    0
                ]
            ),
            "edge_count": int(
                connection.execute("SELECT COUNT(*) FROM consequence_edge").fetchone()[
                    0
                ]
            ),
        }
    finally:
        connection.close()
    require(
        counts == dict(manifest.get("counts") or {}),
        "CANON_CONSEQUENCE_BUNDLE_COUNT_MISMATCH",
        "The Canon consequence bundle counts do not match its immutable manifest.",
        status="MISMATCH",
    )


def _bundle_result(
    root: Path,
    *,
    bundle_root: Path,
    replay_state: str,
) -> dict[str, Any]:
    manifest = _json(
        bundle_root / "manifest.json", code="CANON_CONSEQUENCE_MANIFEST_REQUIRED"
    )
    receipt = _json(
        bundle_root / "receipt.json", code="CANON_CONSEQUENCE_RECEIPT_REQUIRED"
    )
    _verify_bundle(bundle_root, manifest)
    require(
        receipt.get("schema") == CONSEQUENCE_GRAPH_RECEIPT_SCHEMA
        and receipt.get("receipt_sha256") == _hash_without(receipt, "receipt_sha256")
        and receipt.get("manifest_sha256")
        == sha256_file(bundle_root / "manifest.json"),
        "CANON_CONSEQUENCE_RECEIPT_MISMATCH",
        "The Canon consequence graph receipt failed its immutable identity checks.",
        status="MISMATCH",
    )
    relative_bundle = bundle_root.relative_to(root).as_posix()
    authority_effects = dict(receipt["authority_effects"])
    if replay_state != "CONSEQUENCE_GRAPH_CREATED":
        authority_effects = {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "chat_lineage": "NONE",
            "host_entry_continuity": "NONE",
        }
    return {
        "status": "PASS",
        "state": replay_state,
        "project_id": manifest["project_id"],
        "graph_fingerprint_sha256": manifest["graph_fingerprint_sha256"],
        "graph_sha256": manifest["graph_sha256"],
        "counts": manifest["counts"],
        "node_kinds": manifest["node_kinds"],
        "edge_relations": manifest["edge_relations"],
        "source_snapshot": manifest["source_snapshot"],
        "bundle_locator": f"project-authority://{manifest['project_id']}/{relative_bundle}",
        "manifest_sha256": sha256_file(bundle_root / "manifest.json"),
        "receipt_sha256": receipt["receipt_sha256"],
        "authority_effects": authority_effects,
        "project_candidate_created": False,
        "project_hil_invoked": False,
        "canon_hil_invoked": False,
        "learning_hil_invoked": False,
        "project_truth_pointer_moved": False,
        "learning_pointer_moved": False,
        "ordinary_task_approval_inferred": False,
        "full_graph_loaded_into_model_context": False,
    }


def bootstrap_canon_consequence_graph(
    project_root: str | Path,
    *,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    accepted_manifest_sha256: str,
    host_task_uuid: str,
    host_task_deep_link: str,
    active_plan_task_id: str,
    lineage_head_sha256: str,
) -> dict[str, Any]:
    """Build or reuse one immutable consequence graph from exact live authority."""

    root = _project_root(project_root, project_id=project_id)
    exact_manifest = _validate_sha(
        accepted_manifest_sha256, field="accepted_manifest_sha256"
    )
    exact_lineage = _validate_sha(lineage_head_sha256, field="lineage_head_sha256")
    exact_task_uuid = str(host_task_uuid).strip().lower()
    exact_deep_link = str(host_task_deep_link).strip()
    require(
        bool(_TASK_UUID_RE.fullmatch(exact_task_uuid))
        and exact_deep_link == f"codex://threads/{exact_task_uuid}"
        and accepted_pv.startswith("PV")
        and isinstance(pointer_generation, int)
        and pointer_generation > 0
        and bool(str(active_plan_task_id).strip()),
        "CANON_CONSEQUENCE_LIVE_BINDING_INVALID",
        "The Canon consequence graph requires exact task, pointer, and active-row identities.",
        status="MISMATCH",
    )
    layout, sectors = _load_project_layout(
        root,
        project_id=project_id,
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
        accepted_manifest_sha256=exact_manifest,
    )
    plan_rows, steer_rows, plan_sha256 = _plan_rows(root)
    active_rows = [row for row in plan_rows if row["lifecycle_status"] == "ACTIVE"]
    require(
        active_rows[0]["task_id"] == active_plan_task_id,
        "CANON_CONSEQUENCE_ACTIVE_PLAN_TASK_MISMATCH",
        "The SDK-bound active task differs from canonical Plan authority.",
        status="MISMATCH",
    )
    learning_rows, learning_sha256 = _learning_rows(root)
    operational_edges, operational_packets, canon_sha256 = _canon_operational_rows(root)
    root_inventory = _live_root_inventory(root)
    root_inventory_sha256 = sha256_bytes(canonical_json_bytes(root_inventory))
    schema_path, _ = _schema_asset()
    source_snapshot = {
        "project_layout_sha256": sha256_file(root / "project_authority.json"),
        "project_layout_identity_sha256": layout["layout_sha256"],
        "sector_reference_sha256s": {
            value["lane_id"]: digest for value, digest in sectors
        },
        "plan_runtime_projection_sha256": plan_sha256,
        "canon_input_ledger_sha256": canon_sha256,
        "learning_ledger_sha256": learning_sha256,
        "live_root_inventory_sha256": root_inventory_sha256,
        "live_root_inventory_member_count": len(root_inventory),
        "live_root_inventory_excluded_top_level": sorted(
            _ROOT_INVENTORY_EXCLUDED_TOP_LEVEL
        ),
        "accepted_archive_opened": False,
        "accepted_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "accepted_manifest_sha256": exact_manifest,
        "host_task_uuid": exact_task_uuid,
        "host_task_deep_link": exact_deep_link,
        "active_plan_task_id": active_plan_task_id,
        "lineage_head_sha256": exact_lineage,
        "schema_asset_sha256": sha256_file(schema_path),
    }
    fingerprint = sha256_bytes(canonical_json_bytes(source_snapshot))
    canon_root = root / "canon"
    graphs_root = canon_root / "consequence-graphs"
    bundle_root = graphs_root
    current_manifest_path = bundle_root / "manifest.json"
    if current_manifest_path.is_file():
        current_manifest = _json(
            current_manifest_path,
            code="CANON_CONSEQUENCE_MANIFEST_REQUIRED",
        )
    else:
        current_manifest = {}
    if current_manifest.get("graph_fingerprint_sha256") == fingerprint:
        result = _bundle_result(
            root, bundle_root=bundle_root, replay_state="CONSEQUENCE_GRAPH_REUSED"
        )
        pointer_path = canon_root / "consequence-graph-current.json"
        pointer = (
            _json(pointer_path, code="CANON_CONSEQUENCE_POINTER_REQUIRED")
            if pointer_path.is_file()
            else {}
        )
        expected_relative = graphs_root.relative_to(root).as_posix()
        pointer_valid = bool(
            pointer.get("pointer_sha256")
            == _hash_without(pointer, "pointer_sha256")
            and pointer.get("graph_fingerprint_sha256") == fingerprint
            and pointer.get("receipt_sha256") == result["receipt_sha256"]
            and pointer.get("bundle_relative_path") == expected_relative
        )
        if not pointer_valid:
            pointer_body = {
                "schema": CONSEQUENCE_GRAPH_POINTER_SCHEMA,
                "project_id": project_id,
                "graph_fingerprint_sha256": fingerprint,
                "graph_sha256": result["graph_sha256"],
                "bundle_relative_path": expected_relative,
                "manifest_sha256": result["manifest_sha256"],
                "receipt_sha256": result["receipt_sha256"],
                "updated_at": utc_now(),
                "project_truth_pointer_moved": False,
            }
            atomic_write_json(
                pointer_path,
                {
                    **pointer_body,
                    "pointer_sha256": sha256_bytes(
                        canonical_json_bytes(pointer_body)
                    ),
                },
            )
            result["state"] = "CONSEQUENCE_GRAPH_REUSED_POINTER_REPAIRED"
        return result

    graph = _Graph()
    project = graph.add_node(
        "PROJECT",
        f"project://{project_id}",
        {"project_id": project_id, "layout_sha256": layout["layout_sha256"]},
    )
    pointer_node = graph.add_node(
        "ACCEPTED_PV",
        f"project-pv://{project_id}/{accepted_pv}/generation/{pointer_generation}",
        {
            "accepted_pv": accepted_pv,
            "pointer_generation": pointer_generation,
            "accepted_manifest_sha256": exact_manifest,
        },
    )
    graph.add_edge("HAS_ACCEPTED_POINTER", project, pointer_node)
    host_task = graph.add_node(
        "HOST_TASK",
        exact_deep_link,
        {"project_id": project_id, "task_uuid": exact_task_uuid},
    )
    graph.add_edge("GOVERNS_HOST_TASK", project, host_task)
    plan_authority = graph.add_node(
        "PLAN_AUTHORITY",
        f"plan-authority://{project_id}/{plan_sha256.lower()}",
        {"sqlite_sha256": plan_sha256, "task_count": len(plan_rows)},
    )
    graph.add_edge("HAS_PLAN_AUTHORITY", project, plan_authority)
    canon_authority = graph.add_node(
        "CANON_AUTHORITY",
        f"canon-authority://{project_id}/{canon_sha256.lower()}",
        {"sqlite_sha256": canon_sha256},
    )
    graph.add_edge("HAS_CANON_AUTHORITY", project, canon_authority)
    learning_authority = graph.add_node(
        "LEARNING_AUTHORITY",
        f"learning-authority://{project_id}/{(learning_sha256 or 'absent').lower()}",
        {"sqlite_sha256": learning_sha256, "candidate_count": len(learning_rows)},
    )
    graph.add_edge("HAS_LEARNING_AUTHORITY", project, learning_authority)

    root_authority = graph.add_node(
        "LIVE_PROJECT_ROOT",
        f"project-root://{project_id}/{root_inventory_sha256.lower()}",
        {
            "project_id": project_id,
            "member_count": len(root_inventory),
            "inventory_sha256": root_inventory_sha256,
            "accepted_archive_opened": False,
            "excluded_top_level": sorted(_ROOT_INVENTORY_EXCLUDED_TOP_LEVEL),
        },
    )
    graph.add_edge("HAS_LIVE_PROJECT_ROOT", project, root_authority)
    root_nodes: dict[str, dict[str, Any]] = {}
    for member in root_inventory:
        relative = str(member["path"])
        node = graph.add_node(
            f"ROOT_{member['kind']}",
            f"project-root-member://{project_id}/{relative}",
            member,
        )
        root_nodes[relative] = node
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        graph.add_edge(
            "CONTAINS_ROOT_AUTHORITY",
            root_nodes.get(parent, root_authority),
            node,
        )

    for sector, digest in sectors:
        node = graph.add_node(
            "PROJECT_SECTOR",
            f"sector://{project_id}/{sector['lane_id']}",
            {
                "lane_id": sector["lane_id"],
                "state": sector["state"],
                "accepted_pv": sector["accepted_pv"],
                "reference_sha256": digest,
                "lane_schema_contract_sha256": sector["lane_schema_contract_sha256"],
                "artifact_contract_sha256": sector["artifact_contract_sha256"],
            },
        )
        graph.add_edge("HAS_PROJECT_SECTOR", project, node)
        graph.add_edge("BOUNDED_BY_ACCEPTED_PV", node, pointer_node)

    plan_nodes: dict[str, dict[str, Any]] = {}
    for row in plan_rows:
        locator = f"plan-task://{project_id}/{row['task_id']}"
        node = graph.add_node(
            "PLAN_TASK",
            locator,
            {
                "task_id": row["task_id"],
                "plan_sequence": row["plan_sequence"],
                "projection_lane": row["projection_lane"],
                "row_number": row["row_number"],
                "history_number": row["history_number"],
                "lifecycle_status": row["lifecycle_status"],
                "panel_role": row["panel_role"],
                "plan_group": row["plan_group"],
                "commit_batch_id": row["commit_batch_id"],
                "effective_for_execution": bool(row["effective_for_execution"]),
                "task_contract_sha256": row["task_contract_sha256"],
            },
        )
        plan_nodes[str(row["task_id"])] = node
        graph.add_edge("PLAN_CONTAINS_TASK", plan_authority, node)
        graph.add_edge("BOUNDED_BY_ACCEPTED_PV", node, pointer_node)
        if row["task_id"] == active_plan_task_id:
            graph.add_edge("HOST_TASK_EXECUTES_ACTIVE_PLAN_TASK", host_task, node)

    for row in plan_rows:
        source = plan_nodes[str(row["task_id"])]
        dependencies = json.loads(str(row["dependencies_json"]))
        require(
            isinstance(dependencies, list),
            "CANON_CONSEQUENCE_PLAN_DEPENDENCIES_INVALID",
            "A canonical Plan dependency set is not one list.",
            status="MISMATCH",
            task_id=row["task_id"],
        )
        for dependency in dependencies:
            dependency_id = str(dependency).strip()
            destination = plan_nodes.get(dependency_id)
            if destination is None:
                destination = graph.add_node(
                    "EXTERNAL_PLAN_DEPENDENCY",
                    f"plan-external-dependency://{project_id}/{dependency_id}",
                    {"task_id": dependency_id},
                )
            graph.add_edge("DEPENDS_ON", source, destination)

    for steer in steer_rows:
        node = graph.add_node(
            "PLAN_STEER_DELTA",
            f"plan-steer://{project_id}/{steer['delta_id']}",
            {
                "delta_id": steer["delta_id"],
                "delta_sha256": steer["delta_sha256"],
                "boundary": steer["boundary"],
                "classification": steer["classification"],
                "task_steer_sequence": steer["task_steer_sequence"],
            },
        )
        for relation, task_id in (
            ("STEERS_TASK", steer["task_id"]),
            ("LINKS_PLAN_TASK", steer["linked_task_id"]),
        ):
            destination = plan_nodes.get(str(task_id))
            if destination is not None:
                graph.add_edge(relation, node, destination)

    for row in learning_rows:
        candidate = cast(dict[str, Any], json.loads(str(row["candidate_json"])))
        node = graph.add_node(
            "LEARNING_CANDIDATE",
            f"learning-candidate://{project_id}/{row['candidate_id']}",
            {
                "candidate_id": row["candidate_id"],
                "candidate_sha256": row["candidate_sha256"],
                "tier": row["tier"],
                "lesson_type": row["lesson_type"],
                "lifecycle_state": row["lifecycle_state"],
                "scope_kind": (candidate.get("scope") or {}).get("kind"),
            },
        )
        graph.add_edge("LEARNING_AUTHORITY_CONTAINS", learning_authority, node)
        selectors = (candidate.get("scope") or {}).get("selectors") or []
        for selector in selectors:
            task_node = plan_nodes.get(str(selector))
            if task_node is not None:
                graph.add_edge("LEARNING_DERIVED_FROM_PLAN_TASK", node, task_node)

    for operational in operational_edges:
        source = _endpoint_node(graph, cast(Mapping[str, Any], operational["source"]))
        destination = _endpoint_node(
            graph, cast(Mapping[str, Any], operational["destination"])
        )
        graph.add_edge(
            "CANON_HANDOFF",
            source,
            destination,
            {
                "edge_id": operational.get("edge_id"),
                "direction": operational.get("direction"),
                "execution_class": operational.get("execution_class"),
                "scope": operational.get("scope"),
                "expected_return_contract_sha256": operational.get(
                    "expected_return_contract_sha256"
                ),
            },
        )

    for packet in operational_packets:
        envelope = cast(Mapping[str, Any], packet["envelope"])
        source = _endpoint_node(graph, cast(Mapping[str, Any], envelope["source"]))
        destination = _endpoint_node(
            graph, cast(Mapping[str, Any], envelope["destination"])
        )
        packet_node = graph.add_node(
            "CANON_PACKET",
            f"canon-packet://{project_id}/{packet['canon_id']}",
            {
                "canon_id": packet["canon_id"],
                "canon_type": packet["canon_type"],
                "current_state": packet["current_state"],
                "canon_sha256": envelope.get("canon_sha256"),
                "payload_sha256": envelope.get("payload_sha256"),
            },
        )
        graph.add_edge("CANON_AUTHORITY_CONTAINS", canon_authority, packet_node)
        relation = (
            "BACKFIRES_TO_PARENT"
            if packet["canon_type"] == "CANON_BACKFIRE"
            else "RETURNS_RESULT_TO_PARENT"
        )
        graph.add_edge("ORIGINATES_FROM_TASK", packet_node, source)
        graph.add_edge(relation, packet_node, destination)

    nodes = sorted(graph.nodes.values(), key=lambda value: str(value["node_id"]))
    edges = sorted(graph.edges.values(), key=lambda value: str(value["edge_id"]))
    graph_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "schema": CONSEQUENCE_GRAPH_SCHEMA,
                "nodes": [node["record_sha256"] for node in nodes],
                "edges": [edge["record_sha256"] for edge in edges],
            }
        )
    )
    node_kinds = dict(sorted(Counter(str(node["node_kind"]) for node in nodes).items()))
    edge_relations = dict(
        sorted(Counter(str(edge["relation"]) for edge in edges).items())
    )
    canon_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".consequence-build-", dir=canon_root
    ) as temporary:
        temporary_root = Path(temporary)
        sqlite_path = temporary_root / "graph.sqlite"
        _write_graph_sqlite(
            sqlite_path,
            source_snapshot=source_snapshot,
            graph_fingerprint_sha256=fingerprint,
            nodes=nodes,
            edges=edges,
        )
        (temporary_root / "graph.mmd").write_text(
            _render_mermaid(nodes, edges), encoding="utf-8", newline="\n"
        )
        (temporary_root / "graph.dot").write_text(
            _render_dot(nodes, edges), encoding="utf-8", newline="\n"
        )
        members = [
            {
                "path": name,
                "bytes": (temporary_root / name).stat().st_size,
                "sha256": sha256_file(temporary_root / name),
            }
            for name in ("graph.sqlite", "graph.mmd", "graph.dot")
        ]
        manifest = {
            "schema": CONSEQUENCE_GRAPH_BUNDLE_SCHEMA,
            "project_id": project_id,
            "graph_fingerprint_sha256": fingerprint,
            "graph_sha256": graph_sha256,
            "source_snapshot": source_snapshot,
            "counts": {"node_count": len(nodes), "edge_count": len(edges)},
            "node_kinds": node_kinds,
            "edge_relations": edge_relations,
            "members": members,
            "raw_plan_or_learning_payload_returned": False,
        }
        atomic_write_json(temporary_root / "manifest.json", manifest)
        receipt_body = {
            "schema": CONSEQUENCE_GRAPH_RECEIPT_SCHEMA,
            "status": "PASS",
            "project_id": project_id,
            "graph_fingerprint_sha256": fingerprint,
            "graph_sha256": graph_sha256,
            "manifest_sha256": sha256_file(temporary_root / "manifest.json"),
            "counts": manifest["counts"],
            "active_plan_task_id": active_plan_task_id,
            "host_task_uuid": exact_task_uuid,
            "accepted_pv": accepted_pv,
            "pointer_generation": pointer_generation,
            "created_at": utc_now(),
            "authority_effects": {
                "project_truth": "NONE",
                "canon_input": "CONSEQUENCE_GRAPH_REFRESHED",
                "agent_learning": "NONE",
                "chat_lineage": "NONE",
                "host_entry_continuity": "NONE",
            },
            "project_candidate_created": False,
            "project_hil_invoked": False,
            "canon_hil_invoked": False,
            "learning_hil_invoked": False,
            "project_truth_pointer_moved": False,
            "learning_pointer_moved": False,
            "ordinary_task_approval_inferred": False,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        atomic_write_json(temporary_root / "receipt.json", receipt)
        graphs_root.mkdir(parents=True, exist_ok=True)
        allowed_files = {
            "graph.sqlite",
            "graph.mmd",
            "graph.dot",
            "manifest.json",
            "receipt.json",
        }
        unknown_files = sorted(
            path.name
            for path in graphs_root.iterdir()
            if path.is_file() and path.name not in allowed_files
        )
        require(
            not unknown_files,
            "CANON_CONSEQUENCE_LIVE_FOLDER_UNKNOWN_MEMBER",
            "The single live Canon consequence-graph folder contains an unknown file.",
            status="MISMATCH",
            unknown_files=unknown_files,
        )
        for name in sorted(allowed_files):
            os.replace(temporary_root / name, graphs_root / name)
        retired_directories: list[str] = []
        for path in sorted(graphs_root.iterdir(), key=lambda value: value.name):
            if not path.is_dir():
                continue
            require(
                bool(re.fullmatch(r"[0-9a-f]{64}", path.name)),
                "CANON_CONSEQUENCE_LIVE_FOLDER_UNKNOWN_DIRECTORY",
                "The single live Canon consequence-graph folder contains an unknown directory.",
                status="MISMATCH",
                directory=path.name,
            )
            shutil.rmtree(path)
            retired_directories.append(path.name)
        receipt_path = graphs_root / "receipt.json"
        refreshed_receipt = _json(
            receipt_path,
            code="CANON_CONSEQUENCE_RECEIPT_REQUIRED",
        )
        refreshed_body = {
            key: value
            for key, value in refreshed_receipt.items()
            if key != "receipt_sha256"
        }
        refreshed_body["single_live_folder"] = True
        refreshed_body["obsolete_generation_directories_retired"] = retired_directories
        atomic_write_json(
            receipt_path,
            {
                **refreshed_body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(refreshed_body)),
            },
        )
    result = _bundle_result(
        root, bundle_root=bundle_root, replay_state="CONSEQUENCE_GRAPH_CREATED"
    )
    pointer_body = {
        "schema": CONSEQUENCE_GRAPH_POINTER_SCHEMA,
        "project_id": project_id,
        "graph_fingerprint_sha256": fingerprint,
        "graph_sha256": result["graph_sha256"],
        "bundle_relative_path": graphs_root.relative_to(root).as_posix(),
        "manifest_sha256": result["manifest_sha256"],
        "receipt_sha256": result["receipt_sha256"],
        "updated_at": utc_now(),
        "project_truth_pointer_moved": False,
    }
    pointer = {
        **pointer_body,
        "pointer_sha256": sha256_bytes(canonical_json_bytes(pointer_body)),
    }
    atomic_write_json(canon_root / "consequence-graph-current.json", pointer)
    return result


def inspect_canon_consequence_graph(
    project_root: str | Path, *, project_id: str
) -> dict[str, Any]:
    """Read the current immutable consequence graph summary without mutation."""

    root = _project_root(project_root, project_id=project_id)
    pointer_path = root / "canon" / "consequence-graph-current.json"
    if not pointer_path.is_file():
        return {
            "status": "PASS",
            "state": "NO_CONSEQUENCE_GRAPH",
            "project_id": project_id,
            "counts": {"node_count": 0, "edge_count": 0},
            "project_truth_pointer_moved": False,
            "full_graph_loaded_into_model_context": False,
        }
    pointer = _json(pointer_path, code="CANON_CONSEQUENCE_POINTER_REQUIRED")
    require(
        pointer.get("schema") == CONSEQUENCE_GRAPH_POINTER_SCHEMA
        and pointer.get("project_id") == project_id
        and pointer.get("pointer_sha256") == _hash_without(pointer, "pointer_sha256"),
        "CANON_CONSEQUENCE_POINTER_MISMATCH",
        "The current Canon consequence graph pointer is invalid.",
        status="MISMATCH",
    )
    bundle_relative = str(pointer.get("bundle_relative_path") or "")
    bundle_root = (root / bundle_relative).resolve()
    require(
        root in bundle_root.parents,
        "CANON_CONSEQUENCE_POINTER_ESCAPE",
        "The Canon consequence pointer escapes project authority.",
        status="BLOCKED",
    )
    result = _bundle_result(
        root, bundle_root=bundle_root, replay_state="CURRENT_CONSEQUENCE_GRAPH"
    )
    require(
        result["graph_fingerprint_sha256"] == pointer["graph_fingerprint_sha256"]
        and result["graph_sha256"] == pointer["graph_sha256"]
        and result["manifest_sha256"] == pointer["manifest_sha256"]
        and result["receipt_sha256"] == pointer["receipt_sha256"],
        "CANON_CONSEQUENCE_POINTER_BUNDLE_MISMATCH",
        "The Canon consequence pointer and immutable bundle disagree.",
        status="MISMATCH",
    )
    return result


def query_canon_consequence_graph(
    project_root: str | Path,
    *,
    project_id: str,
    query: str,
    limit: int = 8,
) -> dict[str, Any]:
    """Query one bounded FTS5/BM25 slice from the single current graph."""

    root = _project_root(project_root, project_id=project_id)
    terms = [token.casefold() for token in _QUERY_TOKEN_RE.findall(str(query))][:12]
    require(
        bool(terms) and 1 <= int(limit) <= 20,
        "CANON_CONSEQUENCE_QUERY_BOUNDS_INVALID",
        "Canon graph query requires lexical text and a limit from one to twenty.",
        status="BLOCKED",
    )
    try:
        current = inspect_canon_consequence_graph(root, project_id=project_id)
    except EvidenceLaneError as error:
        if error.code not in {
            "CANON_CONSEQUENCE_MANIFEST_REQUIRED",
            "CANON_CONSEQUENCE_POINTER_REQUIRED",
            "CANON_CONSEQUENCE_POINTER_BUNDLE_MISMATCH",
        }:
            raise
        return {
            "status": "STALE",
            "state": "CONSEQUENCE_GRAPH_POINTER_REFRESH_REQUIRED",
            "project_id": project_id,
            "result": "NO_HIT",
            "hits": [],
            "query_terms": terms,
            "bounded_result_limit": int(limit),
            "search_engine": "SQLITE_FTS5_BM25",
            "accepted_archive_opened": False,
            "project_truth_pointer_moved": False,
            "full_graph_loaded_into_model_context": False,
        }
    if current.get("state") == "NO_CONSEQUENCE_GRAPH":
        return {
            **current,
            "result": "NO_HIT",
            "hits": [],
            "search_engine": "SQLITE_FTS5_BM25",
        }
    pointer = _json(
        root / "canon" / "consequence-graph-current.json",
        code="CANON_CONSEQUENCE_POINTER_REQUIRED",
    )
    bundle_root = (root / str(pointer["bundle_relative_path"])).resolve()
    match_query = " OR ".join(
        f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
    )
    connection = _connect_read_only(bundle_root / "graph.sqlite")
    try:
        fts_ready = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='consequence_graph_fts'"
        ).fetchone()
        if fts_ready is None:
            return {
                "status": "STALE",
                "state": "CONSEQUENCE_GRAPH_QUERY_INDEX_REFRESH_REQUIRED",
                "project_id": project_id,
                "result": "NO_HIT",
                "hits": [],
                "query_terms": terms,
                "bounded_result_limit": int(limit),
                "graph_fingerprint_sha256": current["graph_fingerprint_sha256"],
                "graph_sha256": current["graph_sha256"],
                "search_engine": "SQLITE_FTS5_BM25",
                "accepted_archive_opened": False,
                "project_truth_pointer_moved": False,
                "full_graph_loaded_into_model_context": False,
            }
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT record_id,record_type,record_kind,canonical_locator,
                       snippet(consequence_graph_fts,4,'[',']',' ... ',24) AS snippet,
                       bm25(consequence_graph_fts) AS rank
                FROM consequence_graph_fts
                WHERE consequence_graph_fts MATCH ?
                ORDER BY rank,record_type,record_id LIMIT ?
                """,
                (match_query, int(limit)),
            ).fetchall()
        ]
    finally:
        connection.close()
    return {
        "status": "PASS",
        "state": "CURRENT_CONSEQUENCE_GRAPH",
        "project_id": project_id,
        "result": "HIT" if rows else "NO_HIT",
        "hits": rows,
        "query_terms": terms,
        "bounded_result_limit": int(limit),
        "graph_fingerprint_sha256": current["graph_fingerprint_sha256"],
        "graph_sha256": current["graph_sha256"],
        "search_engine": "SQLITE_FTS5_BM25",
        "accepted_archive_opened": False,
        "project_truth_pointer_moved": False,
        "full_graph_loaded_into_model_context": False,
    }
