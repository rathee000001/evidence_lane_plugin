"""Single Project Overlay builder for contained candidates and live PV HILs.

The contained-storage compatibility mode still emits one candidate snapshot.
External project authority uses the same public builder in progressive mode:
it copies the prior *live-root* overlay, appends one PV-HIL blast-radius
transition, and never reads an accepted archive.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .graph_pipeline import SemanticGraph
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from .lineage import ChatLineage
from .redaction import contains_secret
from .sqlite_indexing import rebuild_connection_authority_index

PROJECT_SECTORS: tuple[str, ...] = CANONICAL_LANE_IDS


def _active_project_sectors(lane_bundle: Path) -> tuple[str, ...]:
    """Return only sectors actually emitted by the current lane bundle.

    Historical overlays without lane emission metadata retain the legacy
    universal registry. New bundles never create null sector placeholders for
    lanes that were neither loaded nor detected.
    """

    manifest_path = lane_bundle / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared = manifest.get("emitted_lane_ids")
        if isinstance(declared, list):
            return tuple(
                sector_id for sector_id in PROJECT_SECTORS if sector_id in declared
            )
    discovered = tuple(
        sector_id
        for sector_id in PROJECT_SECTORS
        if (lane_bundle / sector_id).is_dir()
    )
    return discovered or PROJECT_SECTORS


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE overlay_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;
        CREATE TABLE project_sector_registry(
            sector_id TEXT PRIMARY KEY,
            display_label TEXT NOT NULL,
            ordinal INTEGER NOT NULL UNIQUE,
            mutation_policy TEXT NOT NULL
        ) STRICT;
        CREATE TABLE sector_candidate_snapshot(
            sector_id TEXT PRIMARY KEY REFERENCES project_sector_registry(sector_id),
            lane_database_sha256 TEXT,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            truth_state TEXT NOT NULL CHECK(truth_state='CANDIDATE_ONLY'),
            candidate_id TEXT NOT NULL,
            proposed_pv TEXT NOT NULL
        ) STRICT;
        CREATE TABLE chat_lineage_event(
            event_id TEXT PRIMARY KEY,
            lineage_index INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            model TEXT,
            submodel TEXT,
            token_metrics_json TEXT NOT NULL,
            visible_payload_json TEXT NOT NULL,
            visible_payload_sha256 TEXT NOT NULL,
            event_sha256 TEXT NOT NULL,
            previous_event_sha256 TEXT,
            accepted_sector_truth INTEGER NOT NULL CHECK(accepted_sector_truth=0)
        ) STRICT;
        CREATE TABLE chat_lineage_sector_fanout(
            event_id TEXT NOT NULL REFERENCES chat_lineage_event(event_id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL REFERENCES project_sector_registry(sector_id),
            routing_reason TEXT NOT NULL,
            candidate_only INTEGER NOT NULL CHECK(candidate_only=1),
            PRIMARY KEY(event_id, sector_id)
        ) STRICT;
        CREATE TABLE output_link(
            link_id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES chat_lineage_event(event_id) ON DELETE CASCADE,
            link TEXT NOT NULL,
            link_sha256 TEXT NOT NULL,
            UNIQUE(event_id, link_sha256)
        ) STRICT;
        CREATE TABLE fusion_receipt(
            receipt_id INTEGER PRIMARY KEY CHECK(receipt_id=1),
            state TEXT NOT NULL CHECK(state='AWAITING_EXACT_APPROVE'),
            parent_accepted_pv TEXT,
            pointer_generation INTEGER NOT NULL,
            candidate_id TEXT NOT NULL,
            accepted_truth_written INTEGER NOT NULL CHECK(accepted_truth_written=0),
            fallback TEXT NOT NULL,
            rollback_semantics TEXT NOT NULL
        ) STRICT;
        CREATE VIRTUAL TABLE overlay_fts USING fts5(
            event_id UNINDEXED,
            event_type,
            actor_type,
            visible_text,
            tokenize='unicode61'
        );
        """
    )


def _progressive_schema(connection: sqlite3.Connection) -> None:
    """Add the append-only live-root HIL ledger beside legacy v1 tables.

    The legacy tables remain readable so an existing accepted overlay can
    become a truthful migration baseline.  Compressed history is never
    rewritten as invented earlier transitions.
    """

    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS overlay_history_baseline(
            baseline_id INTEGER PRIMARY KEY CHECK(baseline_id=1),
            baseline_pv TEXT,
            baseline_candidate_id TEXT,
            baseline_created_at TEXT,
            source_schema TEXT NOT NULL,
            source_database_sha256 TEXT,
            baseline_snapshot_sha256 TEXT NOT NULL,
            history_before_baseline TEXT NOT NULL,
            accepted_archive_opened INTEGER NOT NULL CHECK(accepted_archive_opened=0),
            accepted_archive_queried INTEGER NOT NULL CHECK(accepted_archive_queried=0)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS sector_hil_baseline_snapshot(
            sector_id TEXT PRIMARY KEY,
            lane_database_sha256 TEXT,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS pv_hil_transition(
            transition_id TEXT PRIMARY KEY,
            sequence INTEGER NOT NULL UNIQUE CHECK(sequence>0),
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            pointer_generation INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            truth_state TEXT NOT NULL CHECK(truth_state='HIL_PROPOSAL_ONLY'),
            prior_transition_sha256 TEXT,
            baseline_snapshot_sha256 TEXT NOT NULL,
            transition_payload_json TEXT NOT NULL,
            transition_sha256 TEXT NOT NULL UNIQUE,
            accepted_archive_opened INTEGER NOT NULL CHECK(accepted_archive_opened=0),
            accepted_archive_queried INTEGER NOT NULL CHECK(accepted_archive_queried=0)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS sector_hil_snapshot(
            transition_id TEXT NOT NULL REFERENCES pv_hil_transition(transition_id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            lane_database_sha256 TEXT,
            source_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            fact_count INTEGER NOT NULL,
            PRIMARY KEY(transition_id,sector_id),
            UNIQUE(transition_id,ordinal)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS sector_hil_delta(
            transition_id TEXT NOT NULL REFERENCES pv_hil_transition(transition_id) ON DELETE CASCADE,
            sector_id TEXT NOT NULL,
            change_kind TEXT NOT NULL CHECK(change_kind IN ('ADDED','MODIFIED','REMOVED','UNCHANGED')),
            before_json TEXT,
            after_json TEXT,
            PRIMARY KEY(transition_id,sector_id)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS hil_transition_receipt(
            transition_id TEXT PRIMARY KEY REFERENCES pv_hil_transition(transition_id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(state='AWAITING_EXACT_APPROVE'),
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            pointer_generation INTEGER NOT NULL,
            accepted_truth_written INTEGER NOT NULL CHECK(accepted_truth_written=0),
            baseline_authority TEXT NOT NULL,
            rollback_semantics TEXT NOT NULL
        ) STRICT;
        """
    )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _snapshot_sha256(snapshot: dict[str, dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(snapshot))


def _legacy_snapshot_index(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    if not _table_exists(connection, "sector_candidate_snapshot"):
        return {}
    rows = connection.execute(
        """
        SELECT sector_id,lane_database_sha256,source_count,chunk_count,fact_count
        FROM sector_candidate_snapshot ORDER BY sector_id
        """
    ).fetchall()
    return {
        str(row[0]): {
            "lane_database_sha256": row[1],
            "source_count": int(row[2]),
            "chunk_count": int(row[3]),
            "fact_count": int(row[4]),
        }
        for row in rows
    }


def _latest_progressive_snapshot(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    if not _table_exists(connection, "pv_hil_transition"):
        return {}
    latest = connection.execute(
        "SELECT transition_id FROM pv_hil_transition ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        return {}
    rows = connection.execute(
        """
        SELECT sector_id,lane_database_sha256,source_count,chunk_count,fact_count
        FROM sector_hil_snapshot WHERE transition_id=? ORDER BY sector_id
        """,
        (str(latest[0]),),
    ).fetchall()
    return {
        str(row[0]): {
            "lane_database_sha256": row[1],
            "source_count": int(row[2]),
            "chunk_count": int(row[3]),
            "fact_count": int(row[4]),
        }
        for row in rows
    }


def _overlay_delta(
    prior: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "evidence-lane.project-overlay-delta.v1",
        "added": [
            {"sector_id": sector_id, "after": current[sector_id]}
            for sector_id in sorted(current.keys() - prior.keys())
        ],
        "modified": [
            {
                "sector_id": sector_id,
                "before": prior[sector_id],
                "after": current[sector_id],
            }
            for sector_id in sorted(prior.keys() & current.keys())
            if prior[sector_id] != current[sector_id]
        ],
        "removed": [
            {"sector_id": sector_id, "before": prior[sector_id]}
            for sector_id in sorted(prior.keys() - current.keys())
        ],
        "unchanged": sorted(
            sector_id
            for sector_id in prior.keys() & current.keys()
            if prior[sector_id] == current[sector_id]
        ),
    }
    payload["delta_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def _lane_counts(lane_bundle: Path, sector_id: str) -> tuple[str | None, int, int, int]:
    lane = LANE_REGISTRY[sector_id]
    database_path = lane_bundle / sector_id / lane.sqlite_filename
    if not database_path.is_file():
        return None, 0, 0, 0
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        return (
            sha256_file(database_path),
            int(
                connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
            ),
            int(connection.execute("SELECT COUNT(*) FROM chunk_index").fetchone()[0]),
            int(
                connection.execute("SELECT COUNT(*) FROM structured_fact").fetchone()[0]
            ),
        )
    finally:
        connection.close()


def _links(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in {
                "output_link",
                "output_links",
                "file",
                "files",
                "path",
                "paths",
            }:
                candidates = item if isinstance(item, list) else [item]
                for candidate in candidates:
                    if (
                        isinstance(candidate, str)
                        and candidate.strip()
                        and candidate not in values
                    ):
                        values.append(candidate.strip())
            values.extend(item for item in _links(item) if item not in values)
    elif isinstance(value, list):
        for item in value:
            values.extend(
                candidate for candidate in _links(item) if candidate not in values
            )
    return values[:200]


def _fanout(event: dict[str, Any], code_mode: str) -> dict[str, str]:
    routes = {"chat_lineage": "all_visible_events"}
    event_type = str(event.get("event_type") or "").lower()
    payload = event.get("visible_payload") or {}
    if any(
        marker in event_type
        for marker in ("tool", "command", "file", "test", "build", "git")
    ):
        routes[code_mode] = "operational_code_activity"
        routes["artifacts"] = "operational_output_evidence"
    if any(marker in event_type for marker in ("artifact", "output", "receipt")):
        routes["artifacts"] = "artifact_or_output_event"
    if event_type == "source.intake.classified" and isinstance(payload, dict):
        for lane_id in payload.get("ordered_canonical_lanes", []):
            if lane_id in PROJECT_SECTORS:
                routes[str(lane_id)] = "source_intake_classification"
    if _links(payload):
        routes["artifacts"] = "visible_output_or_file_links"
    return routes


def _append_progressive_hil_transition(
    output: Path,
    *,
    prior_overlay_path: Path | None,
    lane_bundle: Path,
    lineage_source: str | Path | None,
    transition_id: str,
    proposed_pv: str,
    parent_accepted_pv: str | None,
    pointer_generation: int,
    code_mode: str,
    created_at: str,
    accepted_parent_access: str,
) -> dict[str, Any]:
    """Append one HIL transition through the existing Overlay builder route."""

    output.mkdir(parents=True, exist_ok=False)
    database_path = output / "project_overlay.sqlite"
    prior_database: Path | None = None
    prior_database_sha256: str | None = None
    if prior_overlay_path is not None:
        candidate = prior_overlay_path / "project_overlay.sqlite"
        if candidate.is_file():
            prior_database = candidate
            prior_database_sha256 = sha256_file(candidate)
            shutil.copy2(candidate, database_path)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    if prior_database is None:
        _schema(connection)
    _progressive_schema(connection)

    legacy_metadata = (
        {
            str(row[0]): str(row[1])
            for row in connection.execute(
                "SELECT key,value FROM overlay_meta ORDER BY key"
            )
        }
        if _table_exists(connection, "overlay_meta")
        else {}
    )
    legacy_snapshot = _legacy_snapshot_index(connection)
    latest_snapshot = _latest_progressive_snapshot(connection)
    baseline = connection.execute(
        "SELECT * FROM overlay_history_baseline WHERE baseline_id=1"
    ).fetchone()
    if baseline is None:
        baseline_snapshot = latest_snapshot or legacy_snapshot
        baseline_snapshot_sha256 = _snapshot_sha256(baseline_snapshot)
        baseline_pv = (
            legacy_metadata.get("proposed_pv")
            or parent_accepted_pv
        )
        connection.execute(
            """
            INSERT INTO overlay_history_baseline VALUES(1,?,?,?,?,?,?,?,0,0)
            """,
            (
                baseline_pv,
                legacy_metadata.get("candidate_id"),
                legacy_metadata.get("created_at"),
                legacy_metadata.get("schema", "EMPTY_LIVE_ROOT_OVERLAY"),
                prior_database_sha256,
                baseline_snapshot_sha256,
                (
                    "UNAVAILABLE_IN_LIVE_ROOT_DO_NOT_FABRICATE"
                    if baseline_snapshot
                    else "EMPTY_LIVE_ROOT_BASELINE"
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO sector_hil_baseline_snapshot VALUES(?,?,?,?,?)
            """,
            [
                (
                    sector_id,
                    row["lane_database_sha256"],
                    row["source_count"],
                    row["chunk_count"],
                    row["fact_count"],
                )
                for sector_id, row in sorted(baseline_snapshot.items())
            ],
        )
    else:
        baseline_snapshot_sha256 = str(baseline["baseline_snapshot_sha256"])
        baseline_snapshot = {
            str(row[0]): {
                "lane_database_sha256": row[1],
                "source_count": int(row[2]),
                "chunk_count": int(row[3]),
                "fact_count": int(row[4]),
            }
            for row in connection.execute(
                """
                SELECT sector_id,lane_database_sha256,source_count,chunk_count,fact_count
                FROM sector_hil_baseline_snapshot ORDER BY sector_id
                """
            )
        }

    prior_snapshot = latest_snapshot or baseline_snapshot
    active_sector_ids = _active_project_sectors(lane_bundle)
    current_snapshot: dict[str, dict[str, Any]] = {}
    # A legacy overlay may have compressed ordinals for the subset that existed
    # when it was built.  Rebase registry ordinals inside the copied
    # working database before adding the complete current eighteen-lane set.
    # The live baseline bytes remain untouched; this mutation occurs only in
    # the next live-root HIL overlay produced by this builder.
    connection.execute(
        "UPDATE project_sector_registry SET ordinal=ordinal+1000"
    )
    for ordinal, sector_id in enumerate(active_sector_ids):
        lane = LANE_REGISTRY[sector_id]
        connection.execute(
            """
            INSERT INTO project_sector_registry(sector_id,display_label,ordinal,mutation_policy)
            VALUES(?,?,?,?)
            ON CONFLICT(sector_id) DO UPDATE SET
                display_label=excluded.display_label,
                ordinal=excluded.ordinal,
                mutation_policy=excluded.mutation_policy
            """,
            (sector_id, lane.display_label, ordinal, lane.mutation_policy),
        )
        lane_hash, sources, chunks, facts = _lane_counts(lane_bundle, sector_id)
        current_snapshot[sector_id] = {
            "lane_database_sha256": lane_hash,
            "source_count": sources,
            "chunk_count": chunks,
            "fact_count": facts,
        }

    delta = _overlay_delta(prior_snapshot, current_snapshot)
    previous_transition = connection.execute(
        """
        SELECT sequence,transition_sha256 FROM pv_hil_transition
        ORDER BY sequence DESC LIMIT 1
        """
    ).fetchone()
    sequence = int(previous_transition["sequence"]) + 1 if previous_transition else 1
    prior_transition_sha256 = (
        str(previous_transition["transition_sha256"])
        if previous_transition is not None
        else None
    )
    transition_payload = {
        "schema": "evidence-lane.project-overlay-pv-hil-transition.v1",
        "transition_id": transition_id,
        "sequence": sequence,
        "parent_pv": parent_accepted_pv,
        "proposed_pv": proposed_pv,
        "pointer_generation": pointer_generation,
        "created_at": created_at,
        "truth_state": "HIL_PROPOSAL_ONLY",
        "prior_transition_sha256": prior_transition_sha256,
        "baseline_snapshot_sha256": baseline_snapshot_sha256,
        "sector_snapshot": current_snapshot,
        "project_overlay_delta": delta,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
    }
    transition_payload_json = canonical_json_bytes(transition_payload).decode("utf-8")
    transition_sha256 = sha256_bytes(transition_payload_json.encode("utf-8"))
    connection.execute(
        """
        INSERT INTO pv_hil_transition VALUES(?,?,?,?,?,?,?,?,?,?,?,0,0)
        """,
        (
            transition_id,
            sequence,
            parent_accepted_pv,
            proposed_pv,
            pointer_generation,
            created_at,
            "HIL_PROPOSAL_ONLY",
            prior_transition_sha256,
            baseline_snapshot_sha256,
            transition_payload_json,
            transition_sha256,
        ),
    )
    connection.executemany(
        """
        INSERT INTO sector_hil_snapshot VALUES(?,?,?,?,?,?,?)
        """,
        [
            (
                transition_id,
                sector_id,
                ordinal,
                row["lane_database_sha256"],
                row["source_count"],
                row["chunk_count"],
                row["fact_count"],
            )
            for ordinal, (sector_id, row) in enumerate(
                (sector_id, current_snapshot[sector_id])
                for sector_id in active_sector_ids
            )
        ],
    )
    delta_rows: list[tuple[str, str, str, str | None, str | None]] = []
    for row in delta["added"]:
        delta_rows.append(
            (
                transition_id,
                row["sector_id"],
                "ADDED",
                None,
                canonical_json_bytes(row["after"]).decode("utf-8"),
            )
        )
    for row in delta["modified"]:
        delta_rows.append(
            (
                transition_id,
                row["sector_id"],
                "MODIFIED",
                canonical_json_bytes(row["before"]).decode("utf-8"),
                canonical_json_bytes(row["after"]).decode("utf-8"),
            )
        )
    for row in delta["removed"]:
        delta_rows.append(
            (
                transition_id,
                row["sector_id"],
                "REMOVED",
                canonical_json_bytes(row["before"]).decode("utf-8"),
                None,
            )
        )
    for sector_id in delta["unchanged"]:
        value = canonical_json_bytes(current_snapshot[sector_id]).decode("utf-8")
        delta_rows.append(
            (transition_id, sector_id, "UNCHANGED", value, value)
        )
    sorted_delta_rows: list[tuple[str, str, str, str | None, str | None]] = (
        sorted(delta_rows, key=lambda delta_row: delta_row[1])
    )
    connection.executemany(
        "INSERT INTO sector_hil_delta VALUES(?,?,?,?,?)",
        sorted_delta_rows,
    )
    connection.execute(
        "INSERT INTO hil_transition_receipt VALUES(?,?,?,?,?,?,?,?)",
        (
            transition_id,
            "AWAITING_EXACT_APPROVE",
            parent_accepted_pv,
            proposed_pv,
            pointer_generation,
            0,
            accepted_parent_access,
            "ROLLBACK_MOVES_ONLY_ACCEPTED_POINTER_AND_PRESERVES_OVERLAY_HISTORY",
        ),
    )

    current_metadata = {
        "schema": "evidence-lane.project-sector-overlay.v2",
        "candidate_id": transition_id,
        "proposal_id": transition_id,
        "proposed_pv": proposed_pv,
        "truth_state": "HIL_PROPOSAL_ONLY",
        "accepted_sector_truth_written": "false",
        "accepted_archive_opened": "false",
        "accepted_archive_queried": "false",
        "created_at": created_at,
        "active_sector_ids_json": json.dumps(
            list(active_sector_ids), separators=(",", ":")
        ),
        "transition_sequence": str(sequence),
        "transition_sha256": transition_sha256,
        "progressive_history": "true",
    }
    connection.executemany(
        """
        INSERT INTO overlay_meta(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        sorted(current_metadata.items()),
    )

    events = ChatLineage(lineage_source).events() if lineage_source else []
    for index, event in enumerate(events, start=1):
        payload = event.get("visible_payload") or {}
        if contains_secret(payload):
            raise ValueError(
                "Secret-like content remained in visible ChatLineage payload."
            )
        existing = connection.execute(
            "SELECT event_sha256 FROM chat_lineage_event WHERE event_id=?",
            (event["event_id"],),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != str(event["event_sha256"]):
                raise ValueError("Project Overlay lineage event identity conflict.")
            continue
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        payload_sha256 = str(
            event.get("visible_payload_sha256")
            or sha256_bytes(canonical_json_bytes(payload))
        )
        connection.execute(
            """
            INSERT INTO chat_lineage_event(
                event_id,lineage_index,event_type,occurred_at,actor_type,
                model,submodel,token_metrics_json,visible_payload_json,
                visible_payload_sha256,event_sha256,previous_event_sha256,
                accepted_sector_truth
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                event["event_id"],
                int(event.get("lineage_index") or index),
                event["event_type"],
                event["occurred_at"],
                event.get("actor_type") or "system",
                event.get("model"),
                event.get("submodel"),
                json.dumps(
                    event.get("token_metrics") or {"availability": "UNAVAILABLE"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                payload_json,
                payload_sha256,
                event["event_sha256"],
                event.get("previous_event_sha256"),
            ),
        )
        connection.execute(
            "INSERT INTO overlay_fts(event_id,event_type,actor_type,visible_text) VALUES(?,?,?,?)",
            (
                event["event_id"],
                event["event_type"],
                event.get("actor_type") or "system",
                payload_json,
            ),
        )
        for sector_id, reason in _fanout(event, code_mode).items():
            if sector_id not in active_sector_ids:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO chat_lineage_sector_fanout VALUES(?,?,?,1)",
                (event["event_id"], sector_id, reason),
            )
        for link in _links(payload):
            connection.execute(
                "INSERT OR IGNORE INTO output_link(event_id,link,link_sha256) VALUES(?,?,?)",
                (event["event_id"], link, sha256_bytes(link.encode("utf-8"))),
            )
    llama_index_receipt = rebuild_connection_authority_index(
        connection,
        authority_id="project_overlay",
    )
    connection.commit()
    connection.execute("VACUUM")
    connection.close()

    check = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    check.row_factory = sqlite3.Row
    transitions = [
        dict(row)
        for row in check.execute(
            """
            SELECT sequence,parent_pv,proposed_pv,transition_id,transition_sha256
            FROM pv_hil_transition ORDER BY sequence
            """
        )
    ]
    fanout_counts = {
        str(row["sector_id"]): int(row["count"])
        for row in check.execute(
            """
            SELECT sector_id,COUNT(*) AS count
            FROM chat_lineage_sector_fanout GROUP BY sector_id ORDER BY sector_id
            """
        )
    }
    baseline_row = dict(
        check.execute(
            "SELECT * FROM overlay_history_baseline WHERE baseline_id=1"
        ).fetchone()
    )
    lineage_event_count = int(
        check.execute("SELECT COUNT(*) FROM chat_lineage_event").fetchone()[0]
    )
    check.close()

    baseline_label = str(baseline_row.get("baseline_pv") or "EMPTY")
    overlay_graph = SemanticGraph(
        "project_overlay",
        direction="LR",
        role="EXECUTABLE_WORKFLOW",
    )
    overlay_graph.add_node(
        "BASELINE",
        f"Baseline {baseline_label}\n{baseline_row['history_before_baseline']}",
        "root",
    )
    previous = "BASELINE"
    for row in transitions:
        node = f'T{int(row["sequence"])}'
        label = f'{row["parent_pv"] or "NONE"} to {row["proposed_pv"]}'
        overlay_graph.add_node(node, f"{label}\nPV HIL", "lifecycle")
        overlay_graph.add_edge(previous, node, "next HIL transition")
        previous = node
    for ordinal, sector_id in enumerate(active_sector_ids):
        label = LANE_REGISTRY[sector_id].display_label
        count = fanout_counts.get(sector_id, 0)
        node = f"S{ordinal}"
        overlay_graph.add_node(
            node,
            f"{label}\nlineage={count}",
            "semantic",
        )
        overlay_graph.add_edge(previous, node, "blast radius")
    overlay_mmd, overlay_dot, graph_pipeline_receipt = overlay_graph.render_pair()
    atomic_write_bytes(
        output / "project_overlay.mmd", overlay_mmd.encode("utf-8")
    )
    atomic_write_bytes(
        output / "project_overlay.dot", overlay_dot.encode("utf-8")
    )
    validation = validate_project_overlay(
        output, expected_sector_ids=active_sector_ids
    )
    manifest = {
        "schema": "evidence-lane.project-sector-overlay-manifest.v2",
        "proposal_id": transition_id,
        "candidate_id": transition_id,
        "proposed_pv": proposed_pv,
        "truth_state": "HIL_PROPOSAL_ONLY",
        "progressive_history": True,
        "history_baseline": baseline_row,
        "transition_count": len(transitions),
        "transition_head_sha256": transition_sha256,
        "project_overlay_delta": delta,
        "sector_count": len(active_sector_ids),
        "sector_ids": list(active_sector_ids),
        "lane_placeholder_policy": "ABSENT_WHEN_NOT_LOADED_OR_DETECTED",
        "lineage_event_count": lineage_event_count,
        "fanout_counts": fanout_counts,
        "llama_index_refresh_receipt": llama_index_receipt,
        "graph_pipeline_receipt": graph_pipeline_receipt,
        "accepted_sector_truth_written": False,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
        "full_candidate_package_built": False,
        "members": {
            name: sha256_file(output / name)
            for name in (
                "project_overlay.sqlite",
                "project_overlay.mmd",
                "project_overlay.dot",
            )
        },
        "validation": validation,
    }
    atomic_write_json(output / "manifest.json", manifest)
    return {
        **validation,
        "manifest_sha256": sha256_file(output / "manifest.json"),
        "fanout_counts": fanout_counts,
        "project_overlay_delta": delta,
        "transition_count": len(transitions),
        "transition_head_sha256": transition_sha256,
        "history_baseline": baseline_row,
        "full_candidate_package_built": False,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
    }


def build_project_overlay(
    output_directory: str | Path,
    *,
    lane_bundle_path: str | Path,
    lineage_source: str | Path | None,
    candidate_id: str,
    proposed_pv: str,
    parent_accepted_pv: str | None,
    pointer_generation: int,
    code_mode: str,
    created_at: str,
    truth_state: str = "CANDIDATE_ONLY",
    accepted_parent_access: str = (
        "READ_ACCEPTED_PARENT_OR_EXPLICIT_CANDIDATE_OVERLAY"
    ),
    prior_overlay_path: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_directory).resolve()
    lane_bundle = Path(lane_bundle_path).resolve()
    if truth_state == "HIL_PROPOSAL_ONLY":
        prior_overlay = (
            Path(prior_overlay_path).resolve()
            if prior_overlay_path is not None
            else None
        )
        return _append_progressive_hil_transition(
            output,
            prior_overlay_path=prior_overlay,
            lane_bundle=lane_bundle,
            lineage_source=lineage_source,
            transition_id=candidate_id,
            proposed_pv=proposed_pv,
            parent_accepted_pv=parent_accepted_pv,
            pointer_generation=pointer_generation,
            code_mode=code_mode,
            created_at=created_at,
            accepted_parent_access=accepted_parent_access,
        )
    if truth_state != "CANDIDATE_ONLY":
        raise ValueError(f"Unsupported Project Overlay truth state: {truth_state}")
    output.mkdir(parents=True, exist_ok=False)
    database_path = output / "project_overlay.sqlite"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    _schema(connection)
    metadata = {
        "schema": "evidence-lane.project-sector-overlay.v1",
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "truth_state": truth_state,
        "accepted_sector_truth_written": "false",
        "created_at": created_at,
    }
    connection.executemany(
        "INSERT INTO overlay_meta(key,value) VALUES(?,?)", sorted(metadata.items())
    )
    active_sector_ids = _active_project_sectors(lane_bundle)
    connection.execute(
        "INSERT INTO overlay_meta(key,value) VALUES(?,?)",
        (
            "active_sector_ids_json",
            json.dumps(list(active_sector_ids), separators=(",", ":")),
        ),
    )
    for ordinal, sector_id in enumerate(active_sector_ids):
        lane = LANE_REGISTRY[sector_id]
        connection.execute(
            "INSERT INTO project_sector_registry VALUES(?,?,?,?)",
            (sector_id, lane.display_label, ordinal, lane.mutation_policy),
        )
        if sector_id == "chat_lineage":
            lane_hash, sources, chunks, facts = _lane_counts(lane_bundle, sector_id)
        else:
            lane_hash, sources, chunks, facts = _lane_counts(lane_bundle, sector_id)
        connection.execute(
            """
            INSERT INTO sector_candidate_snapshot VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                sector_id,
                lane_hash,
                sources,
                chunks,
                facts,
                truth_state,
                candidate_id,
                proposed_pv,
            ),
        )
    events = ChatLineage(lineage_source).events() if lineage_source else []
    for index, event in enumerate(events, start=1):
        payload = event.get("visible_payload") or {}
        if contains_secret(payload):
            raise ValueError(
                "Secret-like content remained in visible ChatLineage payload."
            )
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        payload_sha256 = str(
            event.get("visible_payload_sha256")
            or sha256_bytes(canonical_json_bytes(payload))
        )
        connection.execute(
            """
            INSERT INTO chat_lineage_event(
                event_id, lineage_index, event_type, occurred_at, actor_type,
                model, submodel, token_metrics_json, visible_payload_json,
                visible_payload_sha256, event_sha256, previous_event_sha256,
                accepted_sector_truth
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                event["event_id"],
                int(event.get("lineage_index") or index),
                event["event_type"],
                event["occurred_at"],
                event.get("actor_type") or "system",
                event.get("model"),
                event.get("submodel"),
                json.dumps(
                    event.get("token_metrics") or {"availability": "UNAVAILABLE"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                payload_json,
                payload_sha256,
                event["event_sha256"],
                event.get("previous_event_sha256"),
            ),
        )
        connection.execute(
            "INSERT INTO overlay_fts(event_id,event_type,actor_type,visible_text) VALUES(?,?,?,?)",
            (
                event["event_id"],
                event["event_type"],
                event.get("actor_type") or "system",
                payload_json,
            ),
        )
        for sector_id, reason in _fanout(event, code_mode).items():
            if sector_id not in active_sector_ids:
                continue
            connection.execute(
                "INSERT INTO chat_lineage_sector_fanout VALUES(?,?,?,1)",
                (event["event_id"], sector_id, reason),
            )
        for link in _links(payload):
            connection.execute(
                "INSERT OR IGNORE INTO output_link(event_id,link,link_sha256) VALUES(?,?,?)",
                (event["event_id"], link, sha256_bytes(link.encode("utf-8"))),
            )
    connection.execute(
        "INSERT INTO fusion_receipt VALUES(1,?,?,?,?,?,?,?)",
        (
            "AWAITING_EXACT_APPROVE",
            parent_accepted_pv,
            pointer_generation,
            candidate_id,
            0,
            accepted_parent_access,
            "ROLLBACK_MOVES_ONLY_ACCEPTED_POINTER_AND_PRESERVES_OVERLAY_BYTES",
        ),
    )
    llama_index_receipt = rebuild_connection_authority_index(
        connection,
        authority_id="project_overlay",
    )
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    fanout_counts = {}
    check = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    check.row_factory = sqlite3.Row
    for row in check.execute(
        "SELECT sector_id,COUNT(*) AS count FROM chat_lineage_sector_fanout GROUP BY sector_id ORDER BY sector_id"
    ):
        fanout_counts[str(row["sector_id"])] = int(row["count"])
    check.close()
    display_truth_state = truth_state.replace("_", " ")
    overlay_graph = SemanticGraph(
        "project_overlay",
        direction="LR",
        role="EXECUTABLE_WORKFLOW",
    )
    overlay_graph.add_node(
        "CANDIDATE",
        f"{candidate_id}\n{display_truth_state}",
        "root",
    )
    for ordinal, sector_id in enumerate(active_sector_ids):
        label = LANE_REGISTRY[sector_id].display_label
        count = fanout_counts.get(sector_id, 0)
        node = f"S{ordinal}"
        overlay_graph.add_node(
            node,
            f"{label}\nlineage={count}",
            "semantic",
        )
        overlay_graph.add_edge("CANDIDATE", node, "blast radius")
    overlay_mmd, overlay_dot, graph_pipeline_receipt = overlay_graph.render_pair()
    atomic_write_bytes(
        output / "project_overlay.mmd", overlay_mmd.encode("utf-8")
    )
    atomic_write_bytes(
        output / "project_overlay.dot", overlay_dot.encode("utf-8")
    )
    validation = validate_project_overlay(
        output,
        expected_sector_ids=active_sector_ids,
    )
    manifest = {
        "schema": "evidence-lane.project-sector-overlay-manifest.v1",
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "truth_state": truth_state,
        "sector_count": len(active_sector_ids),
        "sector_ids": list(active_sector_ids),
        "lane_placeholder_policy": "ABSENT_WHEN_NOT_LOADED_OR_DETECTED",
        "lineage_event_count": len(events),
        "fanout_counts": fanout_counts,
        "llama_index_refresh_receipt": llama_index_receipt,
        "graph_pipeline_receipt": graph_pipeline_receipt,
        "accepted_sector_truth_written": False,
        "members": {
            name: sha256_file(output / name)
            for name in (
                "project_overlay.sqlite",
                "project_overlay.mmd",
                "project_overlay.dot",
            )
        },
        "validation": validation,
    }
    atomic_write_json(output / "manifest.json", manifest)
    return {
        **validation,
        "manifest_sha256": sha256_file(output / "manifest.json"),
        "fanout_counts": fanout_counts,
    }


def validate_project_overlay(
    directory: str | Path,
    *,
    expected_sector_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    root = Path(directory).resolve()
    if expected_sector_ids is None:
        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared = manifest.get("sector_ids")
            expected_sector_ids = (
                tuple(declared) if isinstance(declared, list) else PROJECT_SECTORS
            )
        else:
            expected_sector_ids = PROJECT_SECTORS
    expected_sector_ids = tuple(expected_sector_ids)
    database_path = root / "project_overlay.sqlite"
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        progressive = _table_exists(connection, "pv_hil_transition")
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute(
                "SELECT key,value FROM overlay_meta ORDER BY key"
            )
        }
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [
            dict(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        sector_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM project_sector_registry"
            ).fetchone()[0]
        )
        actual_sector_ids = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT sector_id FROM project_sector_registry ORDER BY ordinal"
            )
        )
        accepted_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM chat_lineage_event WHERE accepted_sector_truth<>0"
            ).fetchone()[0]
        )
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM chat_lineage_event").fetchone()[0]
        )
        fts_count = int(
            connection.execute("SELECT COUNT(*) FROM overlay_fts").fetchone()[0]
        )
        transition_count = 0
        transition_snapshot_count = 0
        transition_delta_count = 0
        transition_receipt_count = 0
        transition_chain_errors: list[dict[str, Any]] = []
        baseline_snapshot_sha256 = None
        baseline_snapshot_sha256_valid = None
        transition_head_sha256 = None
        if progressive:
            baseline = connection.execute(
                "SELECT * FROM overlay_history_baseline WHERE baseline_id=1"
            ).fetchone()
            if baseline is None:
                transition_chain_errors.append({"error": "BASELINE_MISSING"})
            else:
                baseline_snapshot = {
                    str(row["sector_id"]): {
                        "lane_database_sha256": row["lane_database_sha256"],
                        "source_count": int(row["source_count"]),
                        "chunk_count": int(row["chunk_count"]),
                        "fact_count": int(row["fact_count"]),
                    }
                    for row in connection.execute(
                        """
                        SELECT sector_id,lane_database_sha256,source_count,
                               chunk_count,fact_count
                        FROM sector_hil_baseline_snapshot ORDER BY sector_id
                        """
                    )
                }
                baseline_snapshot_sha256 = _snapshot_sha256(baseline_snapshot)
                baseline_snapshot_sha256_valid = (
                    baseline_snapshot_sha256
                    == str(baseline["baseline_snapshot_sha256"])
                )
                if not baseline_snapshot_sha256_valid:
                    transition_chain_errors.append(
                        {"error": "BASELINE_SNAPSHOT_SHA256_MISMATCH"}
                    )
                if int(baseline["accepted_archive_opened"]) != 0 or int(
                    baseline["accepted_archive_queried"]
                ) != 0:
                    transition_chain_errors.append(
                        {"error": "BASELINE_ACCEPTED_ARCHIVE_ACCESS_RECORDED"}
                    )

            transitions = list(
                connection.execute(
                    "SELECT * FROM pv_hil_transition ORDER BY sequence"
                )
            )
            transition_count = len(transitions)
            transition_snapshot_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sector_hil_snapshot"
                ).fetchone()[0]
            )
            transition_delta_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sector_hil_delta"
                ).fetchone()[0]
            )
            transition_receipt_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM hil_transition_receipt"
                ).fetchone()[0]
            )
            previous_sha256: str | None = None
            for expected_sequence, transition in enumerate(transitions, start=1):
                transition_id = str(transition["transition_id"])
                payload_json = str(transition["transition_payload_json"])
                stored_sha256 = str(transition["transition_sha256"])
                calculated_sha256 = sha256_bytes(payload_json.encode("utf-8"))
                if int(transition["sequence"]) != expected_sequence:
                    transition_chain_errors.append(
                        {
                            "transition_id": transition_id,
                            "error": "SEQUENCE_MISMATCH",
                        }
                    )
                if transition["prior_transition_sha256"] != previous_sha256:
                    transition_chain_errors.append(
                        {
                            "transition_id": transition_id,
                            "error": "PRIOR_TRANSITION_SHA256_MISMATCH",
                        }
                    )
                if calculated_sha256 != stored_sha256:
                    transition_chain_errors.append(
                        {
                            "transition_id": transition_id,
                            "error": "TRANSITION_SHA256_MISMATCH",
                        }
                    )
                if int(transition["accepted_archive_opened"]) != 0 or int(
                    transition["accepted_archive_queried"]
                ) != 0:
                    transition_chain_errors.append(
                        {
                            "transition_id": transition_id,
                            "error": "ACCEPTED_ARCHIVE_ACCESS_RECORDED",
                        }
                    )
                snapshot = {
                    str(row["sector_id"]): {
                        "lane_database_sha256": row["lane_database_sha256"],
                        "source_count": int(row["source_count"]),
                        "chunk_count": int(row["chunk_count"]),
                        "fact_count": int(row["fact_count"]),
                    }
                    for row in connection.execute(
                        """
                        SELECT sector_id,lane_database_sha256,source_count,
                               chunk_count,fact_count
                        FROM sector_hil_snapshot
                        WHERE transition_id=? ORDER BY ordinal
                        """,
                        (transition_id,),
                    )
                }
                delta_rows = list(
                    connection.execute(
                        """
                        SELECT sector_id,change_kind,before_json,after_json
                        FROM sector_hil_delta
                        WHERE transition_id=? ORDER BY sector_id
                        """,
                        (transition_id,),
                    )
                )
                persisted_delta: dict[str, Any] = {
                    "schema": "evidence-lane.project-overlay-delta.v1",
                    "added": [],
                    "modified": [],
                    "removed": [],
                    "unchanged": [],
                }
                for delta_row in delta_rows:
                    sector_id = str(delta_row["sector_id"])
                    change_kind = str(delta_row["change_kind"])
                    before = (
                        json.loads(str(delta_row["before_json"]))
                        if delta_row["before_json"] is not None
                        else None
                    )
                    after = (
                        json.loads(str(delta_row["after_json"]))
                        if delta_row["after_json"] is not None
                        else None
                    )
                    if change_kind == "ADDED":
                        persisted_delta["added"].append(
                            {"sector_id": sector_id, "after": after}
                        )
                    elif change_kind == "MODIFIED":
                        persisted_delta["modified"].append(
                            {
                                "sector_id": sector_id,
                                "before": before,
                                "after": after,
                            }
                        )
                    elif change_kind == "REMOVED":
                        persisted_delta["removed"].append(
                            {"sector_id": sector_id, "before": before}
                        )
                    else:
                        persisted_delta["unchanged"].append(sector_id)
                persisted_delta["delta_sha256"] = sha256_bytes(
                    canonical_json_bytes(persisted_delta)
                )
                try:
                    payload = json.loads(payload_json)
                except json.JSONDecodeError:
                    payload = None
                    transition_chain_errors.append(
                        {
                            "transition_id": transition_id,
                            "error": "TRANSITION_PAYLOAD_JSON_INVALID",
                        }
                    )
                if isinstance(payload, dict):
                    if payload.get("sector_snapshot") != snapshot:
                        transition_chain_errors.append(
                            {
                                "transition_id": transition_id,
                                "error": "TRANSITION_SNAPSHOT_MISMATCH",
                            }
                        )
                    if payload.get("project_overlay_delta") != persisted_delta:
                        transition_chain_errors.append(
                            {
                                "transition_id": transition_id,
                                "error": "TRANSITION_DELTA_MISMATCH",
                            }
                        )
                previous_sha256 = stored_sha256
            transition_head_sha256 = previous_sha256
            if metadata.get("transition_sha256") != transition_head_sha256:
                transition_chain_errors.append(
                    {"error": "OVERLAY_META_TRANSITION_HEAD_MISMATCH"}
                )
    finally:
        connection.close()
    progressive_valid = (
        not progressive
        or (
            metadata.get("truth_state") == "HIL_PROPOSAL_ONLY"
            and metadata.get("progressive_history") == "true"
            and transition_count >= 1
            and transition_snapshot_count
            == transition_count * len(expected_sector_ids)
            and transition_delta_count
            == transition_count * len(expected_sector_ids)
            and transition_receipt_count == transition_count
            and not transition_chain_errors
        )
    )
    valid = (
        integrity == ["ok"]
        and not foreign_keys
        and sector_count == len(expected_sector_ids)
        and actual_sector_ids == expected_sector_ids
        and accepted_rows == 0
        and fts_count == event_count
        and progressive_valid
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "sector_count": sector_count,
        "sector_ids": list(actual_sector_ids),
        "expected_sector_ids": list(expected_sector_ids),
        "lineage_event_count": event_count,
        "fts_count": fts_count,
        "accepted_sector_truth_rows": accepted_rows,
        "progressive_history": progressive,
        "progressive_history_valid": progressive_valid,
        "history_baseline_snapshot_sha256": baseline_snapshot_sha256,
        "history_baseline_snapshot_sha256_valid": baseline_snapshot_sha256_valid,
        "transition_count": transition_count,
        "transition_snapshot_count": transition_snapshot_count,
        "transition_delta_count": transition_delta_count,
        "transition_receipt_count": transition_receipt_count,
        "transition_head_sha256": transition_head_sha256,
        "transition_chain_errors": transition_chain_errors,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
    }
