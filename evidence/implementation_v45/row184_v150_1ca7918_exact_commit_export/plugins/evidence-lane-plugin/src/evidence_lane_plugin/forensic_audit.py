"""Evidence-only forensic reports for a sealed eighteen-lane bundle."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .artifact_contract import validate_four_file_contract
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lane_engine import validate_lane_bundle
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from .schema_topology import physical_schema_projection
from .topology_reconciliation import reconciliation_markdown

FORENSIC_AUDIT_SCHEMA = "evidence-lane.forensic-lane-audit.v2"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sqlite_audit(path: Path, *, lane_id: str) -> dict[str, Any]:
    lane = LANE_REGISTRY[lane_id]
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_errors = [
            dict(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        schema_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT name, type, tbl_name, sql
                FROM sqlite_master
                ORDER BY type, name
                """
            )
        ]
        table_names = [
            str(row["name"]) for row in schema_rows if row["type"] == "table"
        ]
        virtual_fts_tables = [
            str(row["name"])
            for row in schema_rows
            if row["type"] == "table"
            and str(row.get("sql") or "").upper().startswith("CREATE VIRTUAL TABLE")
            and "USING FTS" in str(row.get("sql") or "").upper()
        ]
        public_projection = physical_schema_projection(connection, lane)
        contract_tables = set(lane.schema_contract)
        public_tables = {
            str(row["name"])
            for row in schema_rows
            if row["type"] == "table" and not str(row["name"]).startswith("sqlite_")
        }
        auxiliary_tables = set(public_projection["auxiliary_tables"])
        missing_contract_tables = sorted(contract_tables - public_tables)
        unexpected_public_tables = sorted(
            public_tables - contract_tables - auxiliary_tables
        )
        row_counts: dict[str, int | str] = {}
        table_details: list[dict[str, Any]] = []
        for name in table_names:
            quoted = name.replace('"', '""')
            try:
                row_counts[name] = int(
                    connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[
                        0
                    ]
                )
            except sqlite3.DatabaseError as exc:
                row_counts[name] = f"UNREADABLE:{type(exc).__name__}"
            columns = [
                {
                    "cid": int(row["cid"]),
                    "name": str(row["name"]),
                    "type": str(row["type"] or ""),
                    "notnull": bool(row["notnull"]),
                    "primary_key_ordinal": int(row["pk"]),
                    "hidden": int(row["hidden"]) if "hidden" in row else 0,
                }
                for row in connection.execute(
                    f'PRAGMA table_xinfo("{quoted}")'  # nosec B608
                )
            ]
            table_foreign_keys = [
                {
                    "id": int(row["id"]),
                    "sequence": int(row["seq"]),
                    "from_column": str(row["from"]),
                    "parent_table": str(row["table"]),
                    "parent_column": str(row["to"] or ""),
                    "on_update": str(row["on_update"]),
                    "on_delete": str(row["on_delete"]),
                }
                for row in connection.execute(
                    f'PRAGMA foreign_key_list("{quoted}")'  # nosec B608
                )
            ]
            indexes: list[dict[str, Any]] = []
            for index_row in connection.execute(
                f'PRAGMA index_list("{quoted}")'  # nosec B608
            ):
                index_name = str(index_row["name"])
                quoted_index = index_name.replace('"', '""')
                index_columns = [
                    {
                        "sequence": int(column["seqno"]),
                        "cid": int(column["cid"]),
                        "name": (
                            str(column["name"])
                            if column["name"] is not None
                            else None
                        ),
                    }
                    for column in connection.execute(
                        f'PRAGMA index_info("{quoted_index}")'  # nosec B608
                    )
                ]
                indexes.append(
                    {
                        "name": index_name,
                        "unique": bool(index_row["unique"]),
                        "origin": str(index_row["origin"]),
                        "partial": bool(index_row["partial"]),
                        "columns": index_columns,
                    }
                )
            schema_row = next(
                row
                for row in schema_rows
                if row["type"] == "table" and str(row["name"]) == name
            )
            if name in contract_tables:
                role = "declared_contract"
            elif name in auxiliary_tables:
                role = "sqlite_engine_auxiliary"
            elif name.startswith("sqlite_"):
                role = "sqlite_internal"
            else:
                role = "unexpected_public"
            detail_core = {
                "table": name,
                "role": role,
                "rows": row_counts[name],
                "definition_sha256": sha256_bytes(
                    str(schema_row.get("sql") or "").encode("utf-8")
                ),
                "columns": columns,
                "foreign_keys": table_foreign_keys,
                "indexes": indexes,
            }
            table_details.append(
                {
                    **detail_core,
                    "audit_sha256": sha256_bytes(canonical_json_bytes(detail_core)),
                }
            )
        fts_checks: dict[str, dict[str, Any]] = {}
        for name in virtual_fts_tables:
            quoted = name.replace('"', '""')
            try:
                connection.execute(
                    f'SELECT rowid FROM "{quoted}" ORDER BY rowid LIMIT 1'
                ).fetchall()
                fts_checks[name] = {
                    "status": "PASS",
                    "probe": "READ_ONLY_ROWID_QUERY",
                    "rows": row_counts.get(name),
                }
            except sqlite3.DatabaseError as exc:
                fts_checks[name] = {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                }
    return {
        "path": path.name,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "read_mode": "mode=ro&immutable=1",
        "integrity": integrity,
        "foreign_key_errors": foreign_key_errors,
        "user_version": user_version,
        "tables": table_names,
        "table_count": len(table_names),
        "audited_table_count": len(table_details),
        "every_table_audited": len(table_names) == len(table_details),
        "contract_tables": list(lane.schema_contract),
        "missing_contract_tables": missing_contract_tables,
        "auxiliary_tables": sorted(auxiliary_tables),
        "unexpected_public_tables": unexpected_public_tables,
        "public_schema_projection_sha256": public_projection[
            "projection_sha256"
        ],
        "row_counts": row_counts,
        "table_details": table_details,
        "schema_objects": [
            {
                "name": str(row["name"]),
                "type": str(row["type"]),
                "table": str(row["tbl_name"]),
                "definition_sha256": sha256_bytes(
                    str(row.get("sql") or "").encode("utf-8")
                ),
            }
            for row in schema_rows
        ],
        "fts_tables": virtual_fts_tables,
        "fts_checks": fts_checks,
        "status": (
            "PASS"
            if integrity == ["ok"]
            and not foreign_key_errors
            and len(table_names) == len(table_details)
            and not missing_contract_tables
            and not unexpected_public_tables
            and all(isinstance(value, int) for value in row_counts.values())
            and all(row["status"] == "PASS" for row in fts_checks.values())
            else "FAIL"
        ),
    }


def _text_artifact_audit(path: Path, *, prefix: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return {
        "path": path.name,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "line_count": len(text.splitlines()),
        "required_prefix": prefix,
        "syntax_prefix_valid": text.startswith(prefix),
        "status": "PASS" if text.startswith(prefix) else "FAIL",
    }


def _lane_verdict(checks_pass: bool) -> dict[str, Any]:
    if checks_pass:
        return {
            "verdict": "PURSUE",
            "confidence_percent": 99,
            "basis": "All four-file, every-table SQLite, FTS, topology, pointer, and manifest checks passed.",
            "evidence_that_would_change_verdict": (
                "Any four-file hash mismatch, incomplete table audit, SQLite integrity/FK/FTS failure, missing required artifact, "
                "SQLite-to-MMD/DOT disagreement, or pointer/manifest identity mismatch."
            ),
        }
    return {
        "verdict": "FIX_THEN_PURSUE",
        "confidence_percent": 99,
        "basis": "At least one required forensic gate failed.",
        "evidence_that_would_change_verdict": (
            "A rebuilt sealed bundle in which every failed gate passes without bypass or waiver."
        ),
    }


def audit_lane_bundle(
    directory: str | Path,
    *,
    subject: str,
) -> dict[str, Any]:
    """Audit a lane bundle read-only and return deterministic structured evidence."""

    root = Path(directory).resolve()
    bundle_validation = validate_lane_bundle(root)
    bundle_manifest = _read_json(root / "manifest.json")
    routes = _read_json(root / "routes.json")
    topology_reconciliation = bundle_validation["topology_reconciliation"]
    topology_by_lane = {
        row["lane_id"]: row for row in topology_reconciliation["lanes"]
    }
    emitted_lane_ids = tuple(
        bundle_validation.get("emitted_lane_ids") or CANONICAL_LANE_IDS
    )
    lane_audits: list[dict[str, Any]] = []
    for lane_id in emitted_lane_ids:
        lane = LANE_REGISTRY[lane_id]
        lane_root = root / lane_id
        lane_manifest = _read_json(lane_root / "lane_manifest.json")
        lane_pointer = _read_json(lane_root / "lane_pointer.json")
        refresh_receipt = _read_json(lane_root / "refresh_receipt.json")
        required = list(
            lane_manifest.get(
                "required_artifacts",
                [
                    lane.sqlite_filename,
                    lane.mmd_filename,
                    lane.dot_filename,
                    "tools.json",
                    "lane_pointer.json",
                    "refresh_receipt.json",
                    "lane_manifest.json",
                ],
            )
        )
        artifact_hashes = {
            name: {
                "sha256": sha256_file(lane_root / name),
                "bytes": (lane_root / name).stat().st_size,
            }
            for name in required
            if (lane_root / name).is_file()
        }
        missing = sorted(name for name in required if not (lane_root / name).is_file())
        sqlite_report = _sqlite_audit(
            lane_root / lane.sqlite_filename,
            lane_id=lane_id,
        )
        mmd_report = _text_artifact_audit(
            lane_root / lane.mmd_filename,
            prefix="flowchart ",
        )
        dot_report = _text_artifact_audit(
            lane_root / lane.dot_filename,
            prefix="digraph ",
        )
        four_file_contract = validate_four_file_contract(
            lane_root,
            lane,
            (
                lane_manifest.get("four_file_contract")
                if lane_manifest.get("schema")
                == "evidence-lane.lane-manifest.v3"
                else None
            ),
        )
        manifest_evidence = lane_manifest.get("evidence_artifacts")
        evidence_hashes = {
            name: row["sha256"]
            for name, row in artifact_hashes.items()
            if name != "lane_manifest.json"
        }
        manifest_hashes_match = (
            manifest_evidence == evidence_hashes
            if lane_manifest.get("schema")
            in {
                "evidence-lane.lane-manifest.v2",
                "evidence-lane.lane-manifest.v3",
            }
            else lane_manifest.get("stable_artifacts")
            == {
                name: evidence_hashes[name]
                for name in (
                    lane.sqlite_filename,
                    lane.mmd_filename,
                    lane.dot_filename,
                    "tools.json",
                )
            }
        )
        pointer_valid = (
            lane_pointer.get("lane_id") == lane_id
            and lane_pointer.get("proposed_pv") == bundle_manifest.get("proposed_pv")
            and lane_pointer.get("pointer_generation")
            == bundle_manifest.get("pointer_generation")
            and lane_pointer.get("independent_authority") is False
        )
        lane_validation = bundle_validation.get("lanes", {}).get(lane_id, {})
        topology_report = topology_by_lane[lane_id]
        checks_pass = (
            not missing
            and sqlite_report["status"] == "PASS"
            and mmd_report["status"] == "PASS"
            and dot_report["status"] == "PASS"
            and manifest_hashes_match
            and pointer_valid
            and lane_validation.get("valid") is True
            and topology_report.get("status") == "PASS"
            and four_file_contract.get("valid") is True
        )
        audit = {
            "schema": FORENSIC_AUDIT_SCHEMA,
            "subject": subject,
            "bundle_root_sha256": bundle_validation.get("bundle_sha256"),
            "lane_id": lane_id,
            "display_label": lane.display_label,
            "command": lane.command,
            "build_mode": lane_manifest.get("build_mode"),
            "required_artifacts": required,
            "missing_artifacts": missing,
            "artifact_hashes": artifact_hashes,
            "four_file_contract": four_file_contract,
            "sqlite": sqlite_report,
            "mermaid": mmd_report,
            "graphviz": dot_report,
            "topology_reconciliation": topology_report,
            "lane_pointer": lane_pointer,
            "refresh": {
                "schema": refresh_receipt.get("schema"),
                "build_mode": refresh_receipt.get("build_mode"),
                "classification": refresh_receipt.get("classification"),
                "stable_artifacts_byte_reused": refresh_receipt.get(
                    "stable_artifacts_byte_reused"
                ),
                "git_history": refresh_receipt.get("git_history"),
            },
            "lane_manifest_schema": lane_manifest.get("schema"),
            "manifest_hashes_match": manifest_hashes_match,
            "pointer_valid": pointer_valid,
            "bundle_lane_validation": lane_validation,
            "status": "PASS" if checks_pass else "FAIL",
            **_lane_verdict(checks_pass),
        }
        audit["audit_sha256"] = sha256_bytes(canonical_json_bytes(audit))
        lane_audits.append(audit)
    overall_pass = bundle_validation.get("valid") is True and all(
        audit["status"] == "PASS" for audit in lane_audits
    )
    result = {
        "schema": "evidence-lane.forensic-bundle-audit.v1",
        "subject": subject,
        "bundle": str(root),
        "bundle_validation": bundle_validation,
        "source_count": bundle_manifest.get("source_count"),
        "route_count": len(routes.get("routes", {})),
        "lane_count": len(lane_audits),
        "lanes": lane_audits,
        "topology_reconciliation": topology_reconciliation,
        "status": "PASS" if overall_pass else "FAIL",
        **_lane_verdict(overall_pass),
    }
    result["audit_sha256"] = sha256_bytes(canonical_json_bytes(result))
    return result


def _lane_markdown(audit: dict[str, Any]) -> str:
    sqlite_report = audit["sqlite"]
    artifacts = "\n".join(
        f"- `{name}` - `{row['sha256']}` ({row['bytes']} bytes)"
        for name, row in audit["artifact_hashes"].items()
    )
    row_counts = "\n".join(
        f"- `{name}`: {count}"
        for name, count in sqlite_report["row_counts"].items()
    )
    return f"""# {audit['display_label']} forensic audit

- Subject: `{audit['subject']}`
- Canonical lane: `{audit['lane_id']}`
- Source control: `{audit['command']}`
- Build mode: `{audit['build_mode']}`
- Audit SHA-256: `{audit['audit_sha256']}`
- Status: **{audit['status']}**

## Required sealed artifacts

{artifacts}

Missing artifacts: `{audit['missing_artifacts']}`

## SQLite, foreign keys, and FTS

- Database: `{sqlite_report['path']}`
- Read mode: `{sqlite_report['read_mode']}`
- Integrity: `{sqlite_report['integrity']}`
- Foreign-key errors: `{len(sqlite_report['foreign_key_errors'])}`
- User version: `{sqlite_report['user_version']}`
- Tables: `{sqlite_report['table_count']}`
- Tables audited: `{sqlite_report['audited_table_count']}`
- Every table audited: `{sqlite_report['every_table_audited']}`
- Missing contract tables: `{sqlite_report['missing_contract_tables']}`
- SQLite engine auxiliaries: `{sqlite_report['auxiliary_tables']}`
- Unexpected public tables: `{sqlite_report['unexpected_public_tables']}`
- FTS tables: `{sqlite_report['fts_tables']}`
- FTS checks: `{sqlite_report['fts_checks']}`

### Row counts

{row_counts}

## Topology, pointer, and refresh

- Mermaid: `{audit['mermaid']['status']}` - `{audit['mermaid']['sha256']}`
- DOT: `{audit['graphviz']['status']}` - `{audit['graphviz']['sha256']}`
- SQLite/Mermaid/DOT reconciliation: `{audit['topology_reconciliation']['status']}`
- Reconciled claims: `{audit['topology_reconciliation']['claims_checked']}`
- Failed claims: `{audit['topology_reconciliation']['claims_failed']}`
- Pointer valid: `{audit['pointer_valid']}`
- Manifest schema: `{audit['lane_manifest_schema']}`
- Manifest hashes match: `{audit['manifest_hashes_match']}`
- Four-file contract: `{audit['four_file_contract']['status']}`
- Four-file contract SHA-256: `{audit['four_file_contract'].get('computed_contract_sha256')}`
- `tools.json` identity: `{audit['four_file_contract']['tools_json_valid']}`
- `tools.json` to SQLite/MMD/DOT binding: `{audit['four_file_contract']['tools_artifact_authority_valid']}`
- Refresh build mode: `{audit['refresh']['build_mode']}`
- Stable bytes reused: `{audit['refresh']['stable_artifacts_byte_reused']}`

## Verdict

**{audit['verdict']} - {audit['confidence_percent']}% confidence.**

Basis: {audit['basis']}

Evidence that would change the verdict: {audit['evidence_that_would_change_verdict']}
"""


def write_forensic_audit_reports(
    audit: dict[str, Any],
    output_directory: str | Path,
) -> dict[str, Any]:
    output = Path(output_directory).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Forensic audit output directory must be empty.")
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "forensic_audit.json", audit)
    report_files: list[dict[str, Any]] = []
    index_rows = []
    for position, lane in enumerate(audit["lanes"], start=1):
        name = f"{position:02d}-{lane['lane_id']}-forensic-audit.md"
        path = output / name
        atomic_write_bytes(path, _lane_markdown(lane).encode("utf-8"))
        report_files.append(
            {"path": name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
        )
        index_rows.append(
            f"| {position:02d} | `{lane['lane_id']}` | {lane['status']} | "
            f"{lane['verdict']} | [{name}]({name}) |"
        )
    topology_json = output / "topology_reconciliation.json"
    atomic_write_json(topology_json, audit["topology_reconciliation"])
    topology_markdown = output / "topology_reconciliation.md"
    atomic_write_bytes(
        topology_markdown,
        reconciliation_markdown(audit["topology_reconciliation"]).encode("utf-8"),
    )
    for path in (topology_json, topology_markdown):
        report_files.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    index = """# Evidence Lane forensic audit index

| # | Lane | Status | Verdict | Report |
|---:|---|---|---|---|
""" + "\n".join(index_rows) + f"""

Topology reconciliation: [topology_reconciliation.md](topology_reconciliation.md)

Overall: **{audit['status']}**

Verdict: **{audit['verdict']} - {audit['confidence_percent']}% confidence**

Audit SHA-256: `{audit['audit_sha256']}`

Evidence that would change the verdict: {audit['evidence_that_would_change_verdict']}
"""
    atomic_write_bytes(output / "README.md", index.encode("utf-8"))
    report_files.append(
        {
            "path": "README.md",
            "sha256": sha256_file(output / "README.md"),
            "bytes": (output / "README.md").stat().st_size,
        }
    )
    manifest = {
        "schema": "evidence-lane.forensic-report-package.v1",
        "subject": audit["subject"],
        "audit_sha256": audit["audit_sha256"],
        "lane_report_count": len(audit["lanes"]),
        "files": report_files,
        "status": audit["status"],
        "verdict": audit["verdict"],
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json_bytes(manifest))
    atomic_write_json(output / "report_manifest.json", manifest)
    return manifest
