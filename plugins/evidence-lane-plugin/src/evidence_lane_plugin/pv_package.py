"""Canonical recursive universal PV construction, validation, and sealing."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from . import database
from .connector_governance import validate_connector_brain
from .constants import (
    ENV_UOP_FORBIDDEN_NAMES,
    ENV_UOP_FORBIDDEN_PATH_PARTS,
    POINTER_SCHEMA,
    PV_MANIFEST_SCHEMA,
    PV_OPTIONAL_FILES,
    PV_RECEIPT_SCHEMA,
    PV_REQUIRED_FILES,
)
from .errors import EvidenceLaneError, require
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_file,
)
from .lane_engine import validate_lane_bundle
from .lineage import ChatLineage
from .project_overlay import build_project_overlay, validate_project_overlay
from .redaction import contains_secret
from .topology import build_mermaid, render_mermaid


def _copy_exact(source: str | Path, destination: str | Path) -> None:
    source_path = Path(source)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, source_path.read_bytes())


def _member_record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _checksum_text(directory: Path, names: list[str]) -> bytes:
    return "".join(
        f"{sha256_file(directory / Path(name))} *{name}\n" for name in sorted(names)
    ).encode("utf-8")


def build_pv_package(
    output_directory: str | Path,
    *,
    database_path: str | Path,
    lineage_source: str | Path | None,
    project_identity: dict[str, Any],
    active_pointer: dict[str, Any],
    entry_slip: dict[str, Any],
    exit_slip: dict[str, Any],
    engine_identity: dict[str, Any],
    candidate_id: str,
    proposed_pv: str,
    parent_accepted_pv: str | None,
    parent_manifest_sha256: str | None,
    run_id: str,
    created_at: str,
    warnings: list[dict[str, Any]] | None = None,
    lane_bundle_path: str | Path | None = None,
    code_mode: str = "local_code",
    connector_brain_path: str | Path | None = None,
) -> dict[str, Any]:
    output = Path(output_directory).resolve()
    require(
        not output.exists() or not any(output.iterdir()),
        "PV_OUTPUT_NOT_EMPTY",
        "A project-version package must be built in an empty directory.",
        status="BLOCKED",
        output=str(output),
    )
    output.mkdir(parents=True, exist_ok=True)
    _copy_exact(database_path, output / "code.sqlite")
    db_report = database.validate(output / "code.sqlite")
    if lane_bundle_path:
        lanes_source = Path(lane_bundle_path).resolve()
        shutil.copytree(lanes_source, output / "lanes")
        lane_validation = validate_lane_bundle(output / "lanes")
        require(
            lane_validation["valid"],
            "LANE_BUNDLE_VALIDATION_FAILED",
            "The universal lane bundle failed validation.",
            status="FAIL",
            validation=lane_validation,
        )
        _copy_exact(
            output / "lanes" / "project_lane_topology.mmd",
            output / "project_master_topology.mmd",
        )
        _copy_exact(
            output / "lanes" / "project_lane_topology.dot",
            output / "project_master_topology.dot",
        )
        topology_receipt = {
            "status": "PASS",
            "source": "universal_lane_bundle",
            "lane_count": lane_validation["lane_count"],
            "authoritative_mmd": "project_master_topology.mmd",
            "authoritative_dot": "project_master_topology.dot",
        }
    else:
        with database.connect(output / "code.sqlite", readonly=True) as connection:
            topology_receipt = build_mermaid(
                connection, output / "project_master_topology.mmd"
            )
        atomic_write_bytes(
            output / "project_master_topology.dot",
            (
                b'digraph evidence_lane { root [label="Legacy code topology; '
                b'see project_master_topology.mmd"]; }\n'
            ),
        )
        lane_validation = None
    render_receipt = render_mermaid(
        output / "project_master_topology.mmd",
        svg_path=output / "project_master_topology.svg",
        png_path=output / "project_master_topology.png",
    )
    package_warnings = list(warnings or [])
    if render_receipt["status"] != "PASS":
        package_warnings.append(render_receipt)

    pointer_payload = {
        "schema": POINTER_SCHEMA,
        **active_pointer,
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
    }
    atomic_write_json(output / "active_pointer.json", pointer_payload)
    atomic_write_json(output / "project_identity.json", project_identity)
    atomic_write_json(output / "entry_slip.json", entry_slip)
    atomic_write_json(output / "exit_slip.json", exit_slip)
    lineage = ChatLineage(output / "chat_lineage.jsonl")
    if lineage_source:
        lineage.copy_from(lineage_source)
    else:
        atomic_write_bytes(output / "chat_lineage.jsonl", b"")
        lineage.projection_status()

    overlay_validation = None
    if lane_bundle_path:
        overlay_validation = build_project_overlay(
            output / "project_overlay",
            lane_bundle_path=output / "lanes",
            lineage_source=output / "chat_lineage.jsonl",
            candidate_id=candidate_id,
            proposed_pv=proposed_pv,
            parent_accepted_pv=parent_accepted_pv,
            pointer_generation=int(active_pointer.get("generation", 0)),
            code_mode=code_mode,
            created_at=created_at,
        )
    connector_validation = None
    if connector_brain_path and Path(connector_brain_path).is_file():
        _copy_exact(connector_brain_path, output / "connector_brain.sqlite")
        connector_validation = validate_connector_brain(
            output / "connector_brain.sqlite"
        )
        require(
            connector_validation["valid"],
            "PV_CONNECTOR_BRAIN_INVALID",
            "The governed connector brain failed package validation.",
            status="FAIL",
            validation=connector_validation,
        )

    payload_names = [
        "code.sqlite",
        "project_master_topology.mmd",
        "project_master_topology.dot",
        "active_pointer.json",
        "project_identity.json",
        "chat_lineage.jsonl",
        "chat_lineage.sqlite",
        "entry_slip.json",
        "exit_slip.json",
    ]
    for optional in PV_OPTIONAL_FILES:
        if (output / optional).is_file():
            payload_names.append(optional)
    if lane_bundle_path:
        payload_names.extend(
            path.relative_to(output).as_posix()
            for path in sorted((output / "lanes").rglob("*"))
            if path.is_file()
        )
        payload_names.extend(
            path.relative_to(output).as_posix()
            for path in sorted((output / "project_overlay").rglob("*"))
            if path.is_file()
        )
    if (output / "connector_brain.sqlite").is_file():
        payload_names.append("connector_brain.sqlite")
    manifest = {
        "schema": PV_MANIFEST_SCHEMA,
        "package_kind": "UNIVERSAL_EVIDENCE_LANE_PROJECT_VERSION",
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "parent_accepted_pv": parent_accepted_pv,
        "parent_manifest_sha256": parent_manifest_sha256,
        "run_id": run_id,
        "created_at": created_at,
        "engine": engine_identity,
        "members": [
            _member_record(output / Path(name), output)
            for name in sorted(set(payload_names))
        ],
        "authoritative_topology": "project_master_topology.mmd",
        "rendering": render_receipt,
        "universal_lanes": lane_validation,
        "project_sector_overlay": overlay_validation,
        "connector_brain": connector_validation,
        "warnings": package_warnings,
        "immutability_rule": "candidate bytes are preserved on acceptance",
    }
    atomic_write_json(output / "manifest.json", manifest)
    manifest_sha256 = sha256_file(output / "manifest.json")
    receipt = {
        "schema": PV_RECEIPT_SCHEMA,
        "status": "PASS",
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "manifest_sha256": manifest_sha256,
        "database_validation": db_report,
        "lane_bundle_validation": lane_validation,
        "project_sector_overlay_validation": overlay_validation,
        "connector_brain_validation": connector_validation,
        "topology": topology_receipt,
        "rendering": render_receipt,
        "warnings": package_warnings,
        "required_files": list(PV_REQUIRED_FILES),
        "environment_operator_data_in_pv": False,
        "created_at": created_at,
    }
    atomic_write_json(output / "pv_receipt.json", receipt)
    checksum_members = [
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    ]
    atomic_write_bytes(
        output / "SHA256SUMS.txt",
        _checksum_text(output, checksum_members),
    )
    validation = validate_pv_package(output)
    return {
        "candidate_id": candidate_id,
        "proposed_pv": proposed_pv,
        "path": str(output),
        "manifest_sha256": manifest_sha256,
        "package_sha256": validation["package_sha256"],
        "validation": validation,
        "warnings": package_warnings,
    }


def _parse_checksums(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        parts = line.split(" *", 1)
        require(
            len(parts) == 2 and len(parts[0]) == 64 and bool(parts[1]),
            "CHECKSUM_FORMAT_INVALID",
            "SHA256SUMS.txt contains an invalid line.",
            status="FAIL",
            line_number=line_number,
        )
        require(
            parts[1] not in values,
            "CHECKSUM_DUPLICATE_MEMBER",
            "SHA256SUMS.txt contains a duplicate member.",
            status="FAIL",
            member=parts[1],
        )
        values[parts[1]] = parts[0].upper()
    return values


def validate_pv_package(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).resolve()
    require(
        root.is_dir(),
        "PV_PACKAGE_NOT_FOUND",
        "The project-version package directory does not exist.",
        status="MISMATCH",
    )
    actual_files = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    )
    missing = sorted(set(PV_REQUIRED_FILES) - set(actual_files))
    require(
        not missing,
        "PV_REQUIRED_MEMBER_MISSING",
        "The project-version package is missing required members.",
        status="FAIL",
        missing=missing,
    )
    forbidden = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name.lower() in ENV_UOP_FORBIDDEN_NAMES
            or bool(
                {part.lower() for part in path.relative_to(root).parts[:-1]}
                & ENV_UOP_FORBIDDEN_PATH_PARTS
            )
        )
    ]
    require(
        not forbidden,
        "PV_FORBIDDEN_ENVIRONMENT_OPERATOR_MEMBER",
        "Environment or operator data is forbidden inside project versions.",
        status="FAIL",
        forbidden=forbidden,
    )
    checksums = _parse_checksums(root / "SHA256SUMS.txt")
    expected_checksum_members = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    require(
        sorted(checksums) == expected_checksum_members,
        "CHECKSUM_MEMBER_SET_MISMATCH",
        "SHA256SUMS.txt does not describe the exact package member set.",
        status="FAIL",
        expected=expected_checksum_members,
        actual=sorted(checksums),
    )
    mismatches = {
        name: {"expected": digest, "actual": sha256_file(root / Path(name))}
        for name, digest in checksums.items()
        if sha256_file(root / Path(name)) != digest
    }
    require(
        not mismatches,
        "PV_CHECKSUM_MISMATCH",
        "One or more project-version members failed SHA-256 validation.",
        status="MISMATCH",
        mismatches=mismatches,
    )
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        receipt = json.loads((root / "pv_receipt.json").read_text(encoding="utf-8"))
        pointer = json.loads((root / "active_pointer.json").read_text(encoding="utf-8"))
        project_identity = json.loads(
            (root / "project_identity.json").read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise EvidenceLaneError(
            "PV_JSON_INVALID",
            "A required project-version JSON member is invalid.",
            status="FAIL",
            details={"error": str(exc)},
        ) from exc
    require(
        manifest.get("schema") == PV_MANIFEST_SCHEMA,
        "PV_MANIFEST_SCHEMA_MISMATCH",
        "The PV manifest schema is not supported.",
        status="MISMATCH",
    )
    require(
        receipt.get("schema") == PV_RECEIPT_SCHEMA
        and receipt.get("manifest_sha256") == sha256_file(root / "manifest.json"),
        "PV_RECEIPT_MISMATCH",
        "The PV receipt does not bind the current manifest.",
        status="MISMATCH",
    )
    require(
        pointer.get("schema") == POINTER_SCHEMA,
        "PV_POINTER_SCHEMA_MISMATCH",
        "The embedded entry pointer schema is not supported.",
        status="MISMATCH",
    )
    manifest_members = {
        member["path"]: member["sha256"] for member in manifest.get("members", [])
    }
    manifest_mismatches = {
        name: {
            "expected": digest,
            "actual": (
                sha256_file(root / Path(name))
                if (root / Path(name)).is_file()
                else None
            ),
        }
        for name, digest in manifest_members.items()
        if not (root / Path(name)).is_file() or sha256_file(root / Path(name)) != digest
    }
    require(
        not manifest_mismatches,
        "PV_MANIFEST_MEMBER_MISMATCH",
        "The manifest member hashes do not match the package.",
        status="MISMATCH",
        mismatches=manifest_mismatches,
    )
    for name in (
        "manifest.json",
        "pv_receipt.json",
        "active_pointer.json",
        "project_identity.json",
        "entry_slip.json",
        "exit_slip.json",
    ):
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        require(
            not contains_secret(payload),
            "PV_METADATA_SECRET_DETECTED",
            "A secret-like value was detected in PV metadata.",
            status="BLOCKED",
            member=name,
        )
    for path in sorted((root / "lanes").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(
            not contains_secret(payload),
            "PV_METADATA_SECRET_DETECTED",
            "A secret-like value was detected in lane metadata.",
            status="BLOCKED",
            member=path.relative_to(root).as_posix(),
        )
    for path in sorted((root / "project_overlay").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(
            not contains_secret(payload),
            "PV_METADATA_SECRET_DETECTED",
            "A secret-like value was detected in project-overlay metadata.",
            status="BLOCKED",
            member=path.relative_to(root).as_posix(),
        )
    lineage_events = ChatLineage(root / "chat_lineage.jsonl").events()
    db_report = database.validate(root / "code.sqlite")
    lane_report = validate_lane_bundle(root / "lanes")
    require(
        lane_report["valid"],
        "PV_LANE_BUNDLE_INVALID",
        "The PV universal lane bundle failed validation.",
        status="FAIL",
        lane_report=lane_report,
    )
    overlay_report = None
    if (root / "project_overlay").is_dir():
        overlay_report = validate_project_overlay(root / "project_overlay")
        require(
            overlay_report["valid"],
            "PV_PROJECT_OVERLAY_INVALID",
            "The candidate-only project-sector overlay failed validation.",
            status="FAIL",
            overlay_report=overlay_report,
        )
    connector_report = None
    if (root / "connector_brain.sqlite").is_file():
        connector_report = validate_connector_brain(root / "connector_brain.sqlite")
        require(
            connector_report["valid"],
            "PV_CONNECTOR_BRAIN_INVALID",
            "The packaged connector brain failed validation.",
            status="FAIL",
            connector_report=connector_report,
        )
    package_sha256 = (
        hashlib.sha256(
            canonical_json_bytes(
                {name: sha256_file(root / name) for name in sorted(actual_files)}
            )
        )
        .hexdigest()
        .upper()
    )
    return {
        "status": "PASS",
        "candidate_id": manifest["candidate_id"],
        "proposed_pv": manifest["proposed_pv"],
        "manifest_sha256": sha256_file(root / "manifest.json"),
        "package_sha256": package_sha256,
        "members": len(actual_files),
        "database": db_report,
        "lanes": lane_report,
        "project_sector_overlay": overlay_report,
        "connector_brain": connector_report,
        "chat_lineage_event_count": len(lineage_events),
        "project_id": project_identity.get("project_id"),
        "rendering_status": manifest.get("rendering", {}).get("status"),
    }


def compare_package_bytes(left: str | Path, right: str | Path) -> dict[str, Any]:
    left_root = Path(left)
    right_root = Path(right)
    left_files = {
        path.relative_to(left_root).as_posix(): sha256_file(path)
        for path in left_root.rglob("*")
        if path.is_file()
    }
    right_files = {
        path.relative_to(right_root).as_posix(): sha256_file(path)
        for path in right_root.rglob("*")
        if path.is_file()
    }
    return {
        "identical": left_files == right_files,
        "left": left_files,
        "right": right_files,
    }
