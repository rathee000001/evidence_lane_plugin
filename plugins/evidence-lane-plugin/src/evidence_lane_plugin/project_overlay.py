"""Candidate-only project-sector overlays and visible ChatLineage fan-out."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lanes import LANE_REGISTRY
from .lineage import ChatLineage
from .redaction import contains_secret

PROJECT_SECTORS: tuple[str, ...] = (
    "chat_lineage",
    "github_code",
    "local_code",
    "sqlite_brain",
    "docs",
    "data_excel",
    "pdf_ocr",
    "ppt",
    "images_ocr",
    "artifacts",
    "custom",
    "brain_loader",
    "research",
    "project_engulf",
)


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
) -> dict[str, Any]:
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=False)
    database_path = output / "project_overlay.sqlite"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    _schema(connection)
    metadata = {
        "schema": "evidence-lane.project-sector-overlay.v1",
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "truth_state": "CANDIDATE_ONLY",
        "accepted_sector_truth_written": "false",
        "created_at": created_at,
    }
    connection.executemany(
        "INSERT INTO overlay_meta(key,value) VALUES(?,?)", sorted(metadata.items())
    )
    lane_bundle = Path(lane_bundle_path).resolve()
    for ordinal, sector_id in enumerate(PROJECT_SECTORS):
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
                "CANDIDATE_ONLY",
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
            "READ_ACCEPTED_PARENT_OR_EXPLICIT_CANDIDATE_OVERLAY",
            "ROLLBACK_MOVES_ONLY_ACCEPTED_POINTER_AND_PRESERVES_OVERLAY_BYTES",
        ),
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
    mmd = ["flowchart LR", f'    C["{candidate_id}<br/>CANDIDATE ONLY"]']
    dot = [
        "digraph project_overlay {",
        '  rankdir="LR";',
        f'  candidate [label="{candidate_id}\\nCANDIDATE ONLY"];',
    ]
    for ordinal, sector_id in enumerate(PROJECT_SECTORS):
        label = LANE_REGISTRY[sector_id].display_label.replace('"', "'")
        count = fanout_counts.get(sector_id, 0)
        mmd.append(f'    C --> S{ordinal}["{label}<br/>lineage={count}"]')
        dot.append(f'  s{ordinal} [label="{label}\\nlineage={count}"];')
        dot.append(f"  candidate -> s{ordinal};")
    dot.append("}")
    atomic_write_bytes(
        output / "project_overlay.mmd", ("\n".join(mmd) + "\n").encode("utf-8")
    )
    atomic_write_bytes(
        output / "project_overlay.dot", ("\n".join(dot) + "\n").encode("utf-8")
    )
    validation = validate_project_overlay(output)
    manifest = {
        "schema": "evidence-lane.project-sector-overlay-manifest.v1",
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "truth_state": "CANDIDATE_ONLY",
        "sector_count": len(PROJECT_SECTORS),
        "lineage_event_count": len(events),
        "fanout_counts": fanout_counts,
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


def validate_project_overlay(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).resolve()
    database_path = root / "project_overlay.sqlite"
    connection = sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [
            dict(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        sector_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM project_sector_registry"
            ).fetchone()[0]
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
    finally:
        connection.close()
    valid = (
        integrity == ["ok"]
        and not foreign_keys
        and sector_count == 14
        and accepted_rows == 0
        and fts_count == event_count
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "sector_count": sector_count,
        "lineage_event_count": event_count,
        "fts_count": fts_count,
        "accepted_sector_truth_rows": accepted_rows,
    }
