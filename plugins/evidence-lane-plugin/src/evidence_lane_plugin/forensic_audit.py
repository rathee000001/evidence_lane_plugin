"""Evidence-only forensic reports for a sealed eighteen-lane bundle."""

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
from .lane_engine import validate_lane_bundle
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

FORENSIC_AUDIT_SCHEMA = "evidence-lane.forensic-lane-audit.v1"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sqlite_audit(path: Path) -> dict[str, Any]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [
            dict(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        schema_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT name, type, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
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
        row_counts: dict[str, int | str] = {}
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
        "foreign_key_errors": foreign_keys,
        "user_version": user_version,
        "tables": table_names,
        "table_count": len(table_names),
        "row_counts": row_counts,
        "fts_tables": virtual_fts_tables,
        "fts_checks": fts_checks,
        "status": (
            "PASS"
            if integrity == ["ok"]
            and not foreign_keys
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
            "basis": "All sealed artifact, SQLite, FTS, topology, pointer, and manifest checks passed.",
            "evidence_that_would_change_verdict": (
                "Any hash mismatch, SQLite integrity/FK/FTS failure, missing required artifact, "
                "invalid MMD/DOT prefix, or pointer/manifest identity mismatch."
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
    lane_audits: list[dict[str, Any]] = []
    for lane_id in CANONICAL_LANE_IDS:
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
        sqlite_report = _sqlite_audit(lane_root / lane.sqlite_filename)
        mmd_report = _text_artifact_audit(
            lane_root / lane.mmd_filename,
            prefix="flowchart ",
        )
        dot_report = _text_artifact_audit(
            lane_root / lane.dot_filename,
            prefix="digraph ",
        )
        manifest_evidence = lane_manifest.get("evidence_artifacts")
        evidence_hashes = {
            name: row["sha256"]
            for name, row in artifact_hashes.items()
            if name != "lane_manifest.json"
        }
        manifest_hashes_match = (
            manifest_evidence == evidence_hashes
            if lane_manifest.get("schema") == "evidence-lane.lane-manifest.v2"
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
        checks_pass = (
            not missing
            and sqlite_report["status"] == "PASS"
            and mmd_report["status"] == "PASS"
            and dot_report["status"] == "PASS"
            and manifest_hashes_match
            and pointer_valid
            and lane_validation.get("valid") is True
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
            "sqlite": sqlite_report,
            "mermaid": mmd_report,
            "graphviz": dot_report,
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
        "status": "PASS" if overall_pass else "FAIL",
        **_lane_verdict(overall_pass),
    }
    result["audit_sha256"] = sha256_bytes(canonical_json_bytes(result))
    return result


def _lane_markdown(audit: dict[str, Any]) -> str:
    sqlite_report = audit["sqlite"]
    artifacts = "\n".join(
        f"- `{name}` — `{row['sha256']}` ({row['bytes']} bytes)"
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
- FTS tables: `{sqlite_report['fts_tables']}`
- FTS checks: `{sqlite_report['fts_checks']}`

### Row counts

{row_counts}

## Topology, pointer, and refresh

- Mermaid: `{audit['mermaid']['status']}` — `{audit['mermaid']['sha256']}`
- DOT: `{audit['graphviz']['status']}` — `{audit['graphviz']['sha256']}`
- Pointer valid: `{audit['pointer_valid']}`
- Manifest schema: `{audit['lane_manifest_schema']}`
- Manifest hashes match: `{audit['manifest_hashes_match']}`
- Refresh build mode: `{audit['refresh']['build_mode']}`
- Stable bytes reused: `{audit['refresh']['stable_artifacts_byte_reused']}`

## Verdict

**{audit['verdict']} — {audit['confidence_percent']}% confidence.**

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
    index = """# Evidence Lane forensic audit index

| # | Lane | Status | Verdict | Report |
|---:|---|---|---|---|
""" + "\n".join(index_rows) + f"""

Overall: **{audit['status']}**  
Verdict: **{audit['verdict']} — {audit['confidence_percent']}% confidence**  
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
