"""Content-addressed project-root PV storage for external project authority.

The external project root is the only live working overlay.  Candidate state is
represented by an immutable receipt over those live bytes, never by a copied
``candidates/`` tree.  At an exact later Project HIL, the root can be sealed as
one deterministic ZIP beneath ``accepted/``.  The ZIP stores each unique blob
once and maps project-relative paths to those blobs in a canonical manifest.

This module deliberately does not infer HIL or move the accepted pointer.  The
caller owns the lifecycle compare-and-swap and may invoke the destructive
single-retention swap only after the normal exact-approval gate has passed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file

PROJECT_PV_ARCHIVE_SCHEMA = "evidence-lane.project-pv-cas-archive.v1"
PROJECT_WORKING_OVERLAY_SCHEMA = "evidence-lane.project-working-overlay.v1"
PROJECT_CANDIDATE_OVERLAY_SCHEMA = "evidence-lane.project-candidate-overlay.v1"
PROJECT_ACCEPTED_SWAP_SCHEMA = "evidence-lane.project-accepted-swap.v1"

_MANIFEST_MEMBER = "PROJECT_PV_MANIFEST.json"
_OBJECT_PREFIX = "objects/sha256/"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# ``accepted`` is the output, never an input.  Runtime locks/build state and the
# active session are intentionally excluded from accepted Project Truth.  The
# candidate-overlay receipt is self-referential lifecycle metadata and is also
# excluded; every other receipt remains part of the accepted project state.
_EXCLUDED_TOP_LEVEL = frozenset(
    {
        "accepted",
        "candidates",
        ".build",
        ".accepted-view",
        ".accepted-staging",
        "direct_state_travel_entries",
        "internal_sources",
        "profiles",
        "runtime",
        "sessions",
        "lineage",
        "active_session.json",
        "active_pointer.json",
        ".store.lock",
        ".state-travel-resume.lock",
    }
)
_EXCLUDED_PREFIXES = (
    "receipts/candidate-overlays/",
    "receipts/accepted-swap-journals/",
    "receipts/capture-route/",
    "receipts/postseal_",
)


def _safe_relative(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    path = PurePosixPath(normalized)
    require(
        bool(normalized)
        and not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts),
        "PROJECT_PV_MEMBER_PATH_INVALID",
        "A project PV member escaped the project-relative archive boundary.",
        status="BLOCKED",
        member=value,
    )
    return path.as_posix()


def _excluded(relative: str) -> bool:
    exact = _safe_relative(relative)
    parts = PurePosixPath(exact).parts
    first = parts[0]
    nested_accepted_history = (
        len(parts) >= 3 and parts[0] == "sectors" and "accepted_history" in parts
    )
    return nested_accepted_history or first in _EXCLUDED_TOP_LEVEL or any(
        exact == prefix.rstrip("/") or exact.startswith(prefix)
        for prefix in _EXCLUDED_PREFIXES
    )


def _iter_working_files(root: Path) -> Iterator[tuple[str, Path]]:
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise EvidenceLaneError(
                "PROJECT_PV_SYMLINK_FORBIDDEN",
                "Project PV archives do not follow or store symbolic links.",
                status="BLOCKED",
                details={"path": str(path)},
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if not _excluded(relative):
            yield relative, path


def working_overlay_manifest(
    project_root: str | Path,
    *,
    project_id: str,
    overrides: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Hash the sole live working overlay without copying project bytes."""

    root = Path(project_root).resolve()
    require(
        root.is_dir(),
        "PROJECT_WORKING_ROOT_MISSING",
        "The registered external project authority root does not exist.",
        status="MISMATCH",
        project_root=str(root),
    )
    exact_overrides = {
        _safe_relative(path): bytes(value) for path, value in (overrides or {}).items()
    }
    rows: dict[str, dict[str, Any]] = {}
    for relative, path in _iter_working_files(root):
        if relative in exact_overrides:
            continue
        rows[relative] = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    for relative, data in exact_overrides.items():
        require(
            not _excluded(relative),
            "PROJECT_PV_OVERRIDE_EXCLUDED",
            "An archive override targeted transient or accepted storage.",
            status="BLOCKED",
            path=relative,
        )
        rows[relative] = {
            "path": relative,
            "bytes": len(data),
            "sha256": sha256_bytes(data),
        }
    members = [rows[name] for name in sorted(rows, key=lambda item: (item.casefold(), item))]
    identity = {
        "schema": PROJECT_WORKING_OVERLAY_SCHEMA,
        "project_id": project_id,
        "authority": "SOLE_LIVE_WORKING_OVERLAY",
        "excluded_top_level": sorted(_EXCLUDED_TOP_LEVEL),
        "excluded_prefixes": list(_EXCLUDED_PREFIXES),
        "member_count": len(members),
        "total_bytes": sum(int(row["bytes"]) for row in members),
        "unique_blob_count": len({str(row["sha256"]) for row in members}),
        "members": members,
    }
    identity["working_identity_sha256"] = sha256_bytes(canonical_json_bytes(identity))
    return identity


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(_safe_relative(name), date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _load_json_member(package: Path, name: str) -> dict[str, Any] | None:
    path = package / name
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def build_project_pv_archive(
    project_root: str | Path,
    output_archive: str | Path,
    *,
    project_id: str,
    pv_id: str,
    candidate_id: str,
    parent_accepted_pv: str | None,
    parent_accepted_manifest_sha256: str | None,
    pointer_override: dict[str, Any],
    candidate_package: str | Path | None = None,
    candidate_package_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one deterministic CAS ZIP and prove the live bytes stayed stable."""

    root = Path(project_root).resolve()
    output = Path(output_archive).resolve()
    require(
        output.parent != root / "accepted",
        "PROJECT_PV_ARCHIVE_MUST_STAGE_OUTSIDE_ACCEPTED",
        "A new accepted archive must be verified outside accepted storage.",
        status="BLOCKED",
    )
    # The mutable compare-and-swap pointer is external transaction metadata.
    # Embedding its manifest hash would create an impossible self-referential
    # digest, so the archive manifest carries bounded pointer facts while the
    # live ``active_pointer.json`` remains outside accepted content.
    overrides: dict[str, bytes] = {}
    before = working_overlay_manifest(
        root,
        project_id=project_id,
        overrides=overrides,
    )
    package = Path(candidate_package).resolve() if candidate_package else None
    exact_metadata = dict(candidate_package_metadata or {})
    package_manifest = exact_metadata.get("manifest") or (
        _load_json_member(package, "manifest.json")
        if package is not None and package.is_dir()
        else None
    )
    project_identity = exact_metadata.get("project_identity") or (
        _load_json_member(package, "project_identity.json")
        if package is not None and package.is_dir()
        else None
    )
    exit_slip = exact_metadata.get("exit_slip") or (
        _load_json_member(package, "exit_slip.json")
        if package is not None and package.is_dir()
        else None
    )
    rows = list(before["members"])
    archive_body = {
        "schema": PROJECT_PV_ARCHIVE_SCHEMA,
        "project_id": project_id,
        "pv_id": pv_id,
        "candidate_id": candidate_id,
        "parent_accepted_pv": parent_accepted_pv,
        "parent_accepted_manifest_sha256": parent_accepted_manifest_sha256,
        "working_identity_sha256": before["working_identity_sha256"],
        "member_count": before["member_count"],
        "total_bytes": before["total_bytes"],
        "unique_blob_count": before["unique_blob_count"],
        "members": rows,
        "pointer": pointer_override,
        "candidate_package_manifest": package_manifest,
        "project_identity": project_identity,
        "exit_slip": exit_slip,
        "retention": {
            "accepted_artifact_count": 1,
            "prior_accepted_identity_carried": bool(
                parent_accepted_pv and parent_accepted_manifest_sha256
            ),
            "accepted_lineage": (
                [
                    {
                        "pv_id": parent_accepted_pv,
                        "manifest_sha256": parent_accepted_manifest_sha256,
                    }
                ]
                if parent_accepted_pv and parent_accepted_manifest_sha256
                else []
            ),
            "prior_accepted_bytes_embedded_separately": False,
            "prior_accepted_bytes_in_live_history_inferred": False,
            "exact_prior_byte_rollback_available": False,
            "accepted_directory_embedded": False,
            "transient_runtime_embedded": False,
        },
    }
    archive_manifest_sha256 = sha256_bytes(canonical_json_bytes(archive_body))
    archive_manifest = {
        **archive_body,
        "archive_manifest_sha256": archive_manifest_sha256,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        source_by_path = {relative: path for relative, path in _iter_working_files(root)}
        written_blobs: set[str] = set()
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            archive.writestr(
                _zip_info(_MANIFEST_MEMBER),
                canonical_json_bytes(archive_manifest),
            )
            for row in rows:
                relative = str(row["path"])
                digest = str(row["sha256"])
                if digest in written_blobs:
                    continue
                data = (
                    overrides[relative]
                    if relative in overrides
                    else source_by_path[relative].read_bytes()
                )
                require(
                    sha256_bytes(data) == digest and len(data) == int(row["bytes"]),
                    "PROJECT_PV_SOURCE_CHANGED_DURING_ARCHIVE",
                    "A live working member changed while the accepted archive was built.",
                    status="STALE",
                    path=relative,
                )
                archive.writestr(_zip_info(f"{_OBJECT_PREFIX}{digest}"), data)
                written_blobs.add(digest)
        after = working_overlay_manifest(
            root,
            project_id=project_id,
            overrides=overrides,
        )
        require(
            after["working_identity_sha256"] == before["working_identity_sha256"],
            "PROJECT_PV_WORKING_OVERLAY_CHANGED",
            "The live working overlay changed before archive verification completed.",
            status="STALE",
            before=before["working_identity_sha256"],
            after=after["working_identity_sha256"],
        )
        validation = validate_project_pv_archive(temporary)
        require(
            validation["archive_manifest_sha256"] == archive_manifest_sha256,
            "PROJECT_PV_ARCHIVE_MANIFEST_MISMATCH",
            "The staged archive did not preserve its canonical manifest.",
            status="FAIL",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return validate_project_pv_archive(output)


def _read_archive_manifest(archive_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            value = json.loads(archive.read(_MANIFEST_MEMBER).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, zipfile.BadZipFile) as exc:
        raise EvidenceLaneError(
            "PROJECT_PV_ARCHIVE_INVALID",
            "The accepted project archive or its manifest is unreadable.",
            status="FAIL",
            details={"archive": str(archive_path)},
        ) from exc
    require(
        isinstance(value, dict) and value.get("schema") == PROJECT_PV_ARCHIVE_SCHEMA,
        "PROJECT_PV_ARCHIVE_SCHEMA_MISMATCH",
        "The accepted project archive schema is unsupported.",
        status="MISMATCH",
    )
    return value


def validate_project_pv_archive(archive_path: str | Path) -> dict[str, Any]:
    """Validate exact member names, CAS objects, hashes, and manifest identity."""

    path = Path(archive_path).resolve()
    require(
        path.is_file(),
        "PROJECT_PV_ARCHIVE_NOT_FOUND",
        "The accepted project archive does not exist.",
        status="MISMATCH",
        archive=str(path),
    )
    manifest = _read_archive_manifest(path)
    claimed = str(manifest.get("archive_manifest_sha256") or "")
    body = dict(manifest)
    body.pop("archive_manifest_sha256", None)
    actual = sha256_bytes(canonical_json_bytes(body))
    require(
        claimed == actual,
        "PROJECT_PV_ARCHIVE_MANIFEST_HASH_MISMATCH",
        "The accepted project archive manifest failed its content identity check.",
        status="MISMATCH",
        expected=claimed,
        actual=actual,
    )
    rows = manifest.get("members")
    require(
        isinstance(rows, list),
        "PROJECT_PV_ARCHIVE_MEMBER_INDEX_INVALID",
        "The accepted project archive has no canonical member index.",
        status="FAIL",
    )
    rows = cast(list[Any], rows)
    paths: set[str] = set()
    digests: set[str] = set()
    for row in rows:
        require(
            isinstance(row, dict),
            "PROJECT_PV_ARCHIVE_MEMBER_INVALID",
            "An accepted project archive member is not an object.",
            status="FAIL",
        )
        row = cast(dict[str, Any], row)
        relative = _safe_relative(str(row.get("path") or ""))
        digest = str(row.get("sha256") or "")
        require(
            relative not in paths
            and len(digest) == 64
            and all(character in "0123456789ABCDEF" for character in digest),
            "PROJECT_PV_ARCHIVE_MEMBER_IDENTITY_INVALID",
            "Accepted project archive member paths and hashes must be unique and canonical.",
            status="FAIL",
            path=relative,
        )
        paths.add(relative)
        digests.add(digest)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(
            len(names) == len(set(names))
            and _MANIFEST_MEMBER in names
            and set(names) == {
                _MANIFEST_MEMBER,
                *(f"{_OBJECT_PREFIX}{digest}" for digest in digests),
            },
            "PROJECT_PV_ARCHIVE_OBJECT_SET_MISMATCH",
            "The accepted archive object set does not exactly match its manifest.",
            status="FAIL",
        )
        for digest in sorted(digests):
            data = archive.read(f"{_OBJECT_PREFIX}{digest}")
            require(
                sha256_bytes(data) == digest,
                "PROJECT_PV_ARCHIVE_OBJECT_HASH_MISMATCH",
                "An accepted archive CAS object failed SHA-256 validation.",
                status="MISMATCH",
                object_sha256=digest,
            )
        by_path = {str(row["path"]): row for row in rows}
        for relative, row in by_path.items():
            data = archive.read(f"{_OBJECT_PREFIX}{row['sha256']}")
            require(
                len(data) == int(row["bytes"]),
                "PROJECT_PV_ARCHIVE_MEMBER_SIZE_MISMATCH",
                "An accepted archive member length differs from its manifest.",
                status="MISMATCH",
                path=relative,
            )
    package_manifest = manifest.get("candidate_package_manifest")
    universal = (
        package_manifest.get("universal_lanes")
        if isinstance(package_manifest, dict)
        else None
    )
    return {
        "status": "PASS",
        "storage_kind": "SINGLE_CONTENT_ADDRESSED_PROJECT_ARCHIVE",
        "archive": str(path),
        "archive_sha256": sha256_file(path),
        "archive_manifest_sha256": actual,
        "manifest_sha256": actual,
        "package_sha256": sha256_file(path),
        "project_id": manifest.get("project_id"),
        "candidate_id": manifest.get("candidate_id"),
        "proposed_pv": manifest.get("pv_id"),
        "pv_id": manifest.get("pv_id"),
        "member_count": len(rows),
        "members": len(rows),
        "unique_blob_count": len(digests),
        "deduplicated_member_count": len(rows) - len(digests),
        "working_identity_sha256": manifest.get("working_identity_sha256"),
        "project_identity": manifest.get("project_identity"),
        "candidate_package_manifest": package_manifest,
        "exit_slip": manifest.get("exit_slip"),
        "pointer": manifest.get("pointer"),
        "lanes": {
            "status": "PASS" if isinstance(universal, dict) else "UNAVAILABLE",
            "valid": isinstance(universal, dict),
            "lane_count": (
                universal.get("canonical_lane_count")
                if isinstance(universal, dict)
                else None
            ),
        },
        "promotability_required": False,
        "promotable": True,
        "retention": manifest.get("retention"),
    }


def archive_member_bytes(archive_path: str | Path, relative_path: str) -> bytes:
    path = Path(archive_path).resolve()
    relative = _safe_relative(relative_path)
    manifest = _read_archive_manifest(path)
    row = next(
        (
            item
            for item in manifest.get("members", [])
            if isinstance(item, dict) and item.get("path") == relative
        ),
        None,
    )
    require(
        isinstance(row, dict),
        "PROJECT_PV_ARCHIVE_MEMBER_NOT_FOUND",
        "The accepted archive does not contain the requested project member.",
        status="MISMATCH",
        path=relative,
    )
    row = cast(dict[str, Any], row)
    with zipfile.ZipFile(path) as archive:
        data = archive.read(f"{_OBJECT_PREFIX}{row['sha256']}")
    require(
        sha256_bytes(data) == row["sha256"],
        "PROJECT_PV_ARCHIVE_OBJECT_HASH_MISMATCH",
        "The requested accepted archive member failed SHA-256 validation.",
        status="MISMATCH",
        path=relative,
    )
    return data


@contextmanager
def materialized_project_pv_archive(
    archive_path: str | Path,
) -> Iterator[Path]:
    """Yield a temporary exact project-root view; persist no duplicate authority."""

    path = Path(archive_path).resolve()
    validation = validate_project_pv_archive(path)
    manifest = _read_archive_manifest(path)
    temporary = Path(tempfile.mkdtemp(prefix="evidence-lane-accepted-view-"))
    try:
        with zipfile.ZipFile(path) as archive:
            for row in manifest["members"]:
                relative = _safe_relative(str(row["path"]))
                target = (temporary / Path(relative)).resolve()
                target.relative_to(temporary)
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(f"{_OBJECT_PREFIX}{row['sha256']}")
                target.write_bytes(data)
        require(
            working_overlay_manifest(
                temporary,
                project_id=str(validation["project_id"]),
            )["member_count"]
            == validation["member_count"],
            "PROJECT_PV_ARCHIVE_MATERIALIZATION_MISMATCH",
            "The temporary accepted view did not reproduce the manifest member set.",
            status="FAIL",
        )
        yield temporary
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def accepted_storage_status(
    project_root: str | Path,
    *,
    project_id: str,
    accepted_pv: str | None,
    pointer_generation: int,
    accepted_manifest_sha256: str | None,
) -> dict[str, Any]:
    """Inspect legacy/current storage without sealing, deleting, or moving authority."""

    root = Path(project_root).resolve()
    accepted = root / "accepted"
    candidate_directory = root / "candidates"
    artifacts = sorted(accepted.iterdir(), key=lambda item: item.name) if accepted.is_dir() else []
    archive_rows: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.is_file() and artifact.suffix.lower() == ".zip":
            archive_rows.append(validate_project_pv_archive(artifact))
        elif artifact.is_dir() and artifact.name.startswith("PV"):
            legacy_rows.append(
                {
                    "pv_id": artifact.name,
                    "path": str(artifact),
                    "storage_kind": "LEGACY_UNPACKED_ACCEPTED_DIRECTORY",
                }
            )
        else:
            legacy_rows.append(
                {
                    "path": str(artifact),
                    "storage_kind": "UNRECOGNIZED_ACCEPTED_ARTIFACT",
                }
            )
    working = working_overlay_manifest(root, project_id=project_id)
    archive_pointer_matches = bool(
        len(archive_rows) == 1
        and archive_rows[0].get("pv_id") == accepted_pv
        and archive_rows[0].get("archive_manifest_sha256")
        == accepted_manifest_sha256
    )
    if len(archive_rows) == 1 and not legacy_rows and archive_pointer_matches:
        state = "SINGLE_ACCEPTED_ARCHIVE_ACTIVE"
    elif len(archive_rows) == 1 and not legacy_rows:
        state = "ACCEPTED_ARCHIVE_POINTER_MISMATCH"
    elif (
        len(legacy_rows) == 1
        and not archive_rows
        and legacy_rows[0]["storage_kind"]
        == "LEGACY_UNPACKED_ACCEPTED_DIRECTORY"
    ):
        state = "LEGACY_ACCEPTED_PRESERVED_UNTIL_EXACT_FUTURE_HIL"
    elif not artifacts:
        state = "NO_ACCEPTED_ARTIFACT"
    else:
        state = "ACCEPTED_STORAGE_REQUIRES_RECONCILIATION"
    return {
        "schema": "evidence-lane.project-pv-storage-status.v1",
        "status": (
            "PASS"
            if state
            not in {
                "ACCEPTED_STORAGE_REQUIRES_RECONCILIATION",
                "ACCEPTED_ARCHIVE_POINTER_MISMATCH",
            }
            and not candidate_directory.exists()
            else "MISMATCH"
        ),
        "state": state,
        "project_id": project_id,
        "project_root": str(root),
        "live_authority": "PROJECT_ROOT_WORKING_OVERLAY",
        "working_identity_sha256": working["working_identity_sha256"],
        "working_member_count": working["member_count"],
        "accepted_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "accepted_manifest_sha256": accepted_manifest_sha256,
        "accepted_artifact_count": len(artifacts),
        "accepted_archives": archive_rows,
        "accepted_archive_pointer_matches": archive_pointer_matches,
        "legacy_accepted_artifacts": legacy_rows,
        "candidate_directory_present": candidate_directory.exists(),
        "candidate_directory_authoritative": False,
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "destructive_retention_action_performed": False,
        "next_transition": (
            "AT_EXACT_FUTURE_PROJECT_HIL_BUILD_VERIFY_AND_ATOMICALLY_SWAP_ONE_ARCHIVE"
        ),
    }
