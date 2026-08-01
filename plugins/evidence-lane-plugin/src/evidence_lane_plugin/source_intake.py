"""One generalized, ordered source-intake classifier for all canonical lanes."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY, resolve_lane_id, route_source

_PROJECT_MARKERS = {
    "cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "src/",
}


def _archive_lane(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name.replace("\\", "/").lower() for name in archive.namelist()]
    except (OSError, zipfile.BadZipFile):
        return "custom", "zip_unreadable_or_generic"
    sqlite_members = [
        name for name in names if Path(name).suffix in {".db", ".sqlite", ".sqlite3"}
    ]
    project_markers = [
        marker
        for marker in _PROJECT_MARKERS
        if any(name == marker or name.endswith("/" + marker) for name in names)
    ]
    if project_markers:
        return "project_engulf", "archive_project_markers"
    if sqlite_members:
        return "brain_loader", "archive_sqlite_brain_members"
    return "custom", "archive_generic"


def _classify_one(
    source: str, *, code_mode: str, override: str | None
) -> dict[str, Any]:
    exact = source.strip()
    if override:
        lane_id = resolve_lane_id(override, code_mode=code_mode)
        reason = "explicit_override"
    else:
        parsed = urlparse(exact)
        path = Path(exact).expanduser()
        remote_host = (parsed.hostname or "").lower()
        remote_path = parsed.path or exact
        if parsed.scheme in {"ssh", "git"} or (
            parsed.scheme in {"http", "https"}
            and (
                remote_host in {"github.com", "gitlab.com", "bitbucket.org"}
                or remote_path.lower().endswith(".git")
            )
        ):
            lane_id = "github_code"
            reason = "git_remote_url"
        elif parsed.scheme in {"http", "https"}:
            lane_id = route_source(remote_path, code_mode=code_mode)
            reason = "remote_content_type_router"
        elif path.exists() and path.is_dir():
            lane_id = code_mode if (path / ".git").is_dir() else "project_engulf"
            reason = (
                "local_git_directory" if lane_id == code_mode else "project_directory"
            )
        elif path.exists() and path.is_file() and path.suffix.lower() == ".zip":
            lane_id, reason = _archive_lane(path)
        else:
            lane_id = route_source(exact, code_mode=code_mode)
            reason = "canonical_path_and_content_type_router"
    path = Path(exact).expanduser()
    exists = path.exists()
    if exists and path.is_file():
        identity = {
            "kind": "file",
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    elif exists and path.is_dir():
        members = sorted(
            item.relative_to(path).as_posix()
            for item in path.rglob("*")
            if item.is_file() and ".git" not in item.relative_to(path).parts
        )
        identity = {
            "kind": "directory",
            "path": str(path.resolve()),
            "member_count": len(members),
            "member_path_sha256": sha256_bytes(canonical_json_bytes(members)),
        }
    else:
        identity = {
            "kind": "remote_or_declared",
            "pointer": exact,
            "pointer_sha256": sha256_bytes(exact.encode("utf-8")),
        }
    return {
        "source": exact,
        "source_identity": identity,
        "canonical_lane_id": lane_id,
        "display_label": LANE_REGISTRY[lane_id].display_label,
        "classification_reason": reason,
        "explicit_override": bool(override),
    }


def classify_source_intake(
    sources: list[str],
    *,
    code_mode: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Classify ordered inputs without copying, parsing, or mutating source bytes."""

    if code_mode not in {"github_code", "local_code"}:
        raise ValueError("code_mode must be github_code or local_code")
    exact_sources = [str(source).strip() for source in sources if str(source).strip()]
    if not exact_sources:
        raise ValueError("At least one non-empty source is required.")
    exact_overrides = overrides or {}
    unknown_overrides = sorted(set(exact_overrides) - set(exact_sources))
    if unknown_overrides:
        raise ValueError(
            "Every explicit source override must name one exact supplied source: "
            + ", ".join(unknown_overrides)
        )
    receipts = [
        _classify_one(
            source,
            code_mode=code_mode,
            override=exact_overrides.get(source),
        )
        for source in exact_sources
    ]
    ordered_lanes = ["chat_lineage"]
    for receipt in receipts:
        lane_id = str(receipt["canonical_lane_id"])
        if lane_id not in ordered_lanes:
            ordered_lanes.append(lane_id)
    return {
        "status": "PASS",
        "schema": "evidence-lane.source-intake-classification.v1",
        "sources": receipts,
        "source_count": len(receipts),
        "ordered_canonical_lanes": ordered_lanes,
        "chat_lineage_included": True,
        "all_canonical_lanes_supported": list(CANONICAL_LANE_IDS),
        "project_engulf_supported": True,
        "auto_detection": True,
        "explicit_overrides": bool(exact_overrides),
        "source_bytes_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
