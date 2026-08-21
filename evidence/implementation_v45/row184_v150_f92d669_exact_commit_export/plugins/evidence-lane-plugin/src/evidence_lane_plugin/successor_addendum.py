"""Append-only successor State Travel hash-bridge package builder."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)

LEGACY_EXPECTED_STATE_TRAVEL_SHA256 = (
    "CC2B4C6F2DAAD4C7D0F3A722AB0AB240A7F4B922A17F585CF446A8E571CA591B"
)
SUCCESSOR_BRIDGE_SCHEMA = "evidence-lane.state-travel-successor-bridge.v1"
_GIT_SHA = re.compile(r"[0-9a-fA-F]{40}")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
    }


def _tree_sha256(inventory: dict[str, dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(inventory))


def _write_exact(path: Path, content: bytes) -> str:
    if path.is_file():
        require(
            path.read_bytes() == content,
            "SUCCESSOR_ADDENDUM_IMMUTABILITY_CONFLICT",
            "The exact release addendum already exists with different bytes.",
            status="BLOCKED",
            path=str(path),
        )
        return "REUSED"
    atomic_write_bytes(path, content)
    return "CREATED"


def build_successor_addendum(
    *,
    original_package: str | Path,
    addendum_root: str | Path,
    release_sha: str,
    candidate_id: str,
    candidate_manifest_sha256: str,
    candidate_package_sha256: str,
    built_by: str,
) -> dict[str, Any]:
    """Seal a release bridge without writing any byte under the original root."""

    original = Path(original_package).resolve()
    output = Path(addendum_root).resolve()
    exact_sha = release_sha.strip().lower()
    require(
        original.is_dir(),
        "SUCCESSOR_ORIGINAL_PACKAGE_MISSING",
        "The immutable original State Travel package directory is required.",
        status="BLOCKED",
        original_package=str(original),
    )
    require(
        bool(_GIT_SHA.fullmatch(exact_sha)),
        "SUCCESSOR_RELEASE_SHA_INVALID",
        "The successor bridge requires one exact 40-character Git SHA.",
        status="BLOCKED",
    )
    require(
        not _inside(output, original) and not _inside(original, output),
        "SUCCESSOR_PATH_OVERLAP_FORBIDDEN",
        "The successor addendum and immutable original package must not overlap.",
        status="BLOCKED",
    )
    for label, value in {
        "candidate_id": candidate_id,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "candidate_package_sha256": candidate_package_sha256,
        "built_by": built_by,
    }.items():
        require(
            bool(value.strip()),
            "SUCCESSOR_BINDING_REQUIRED",
            "Every successor release binding must be explicit.",
            status="BLOCKED",
            field=label,
        )
    before = _inventory(original)
    before_tree = _tree_sha256(before)
    release_root = output / "releases" / exact_sha
    bridge = {
        "schema": SUCCESSOR_BRIDGE_SCHEMA,
        "release_sha": exact_sha,
        "legacy_expected_sha256": LEGACY_EXPECTED_STATE_TRAVEL_SHA256,
        "original_package_path": str(original),
        "original_inventory_count": len(before),
        "original_tree_sha256": before_tree,
        "candidate_id": candidate_id,
        "candidate_manifest_sha256": candidate_manifest_sha256.upper(),
        "candidate_package_sha256": candidate_package_sha256.upper(),
        "built_by": built_by.strip(),
        "original_rewritten": False,
        "state_travel_performed": False,
        "pointer_moved": False,
        "candidate_accepted": False,
    }
    bridge["bridge_sha256"] = sha256_bytes(canonical_json_bytes(bridge))
    readme = (
        "# Evidence Lane successor State Travel addendum\n\n"
        f"Release: `{exact_sha}`\n\n"
        f"Legacy expected hash: `{LEGACY_EXPECTED_STATE_TRAVEL_SHA256}`\n\n"
        f"Original tree hash observed read-only: `{before_tree}`\n\n"
        f"Candidate: `{candidate_id}` (UNACCEPTED)\n\n"
        "This bridge does not rewrite the original package, accept the candidate, "
        "move a pointer, Fuse, or perform State Travel.\n"
    ).encode()
    files = {
        "HASH_BRIDGE.json": canonical_json_bytes(bridge),
        "ORIGINAL_INVENTORY.json": canonical_json_bytes(
            {
                "schema": "evidence-lane.original-package-inventory.v1",
                "root": str(original),
                "member_count": len(before),
                "tree_sha256": before_tree,
                "members": before,
            }
        ),
        "README.md": readme,
    }
    actions = {
        name: _write_exact(release_root / name, content)
        for name, content in files.items()
    }
    members = {
        name: {
            "bytes": len(content),
            "sha256": sha256_bytes(content),
        }
        for name, content in files.items()
    }
    release_manifest = {
        "schema": "evidence-lane.state-travel-successor-addendum.v1",
        "release_sha": exact_sha,
        "member_count": len(members),
        "members": members,
        "bridge_sha256": bridge["bridge_sha256"],
        "unaccepted": True,
    }
    actions["manifest.json"] = _write_exact(
        release_root / "manifest.json",
        canonical_json_bytes(release_manifest),
    )
    after = _inventory(original)
    after_tree = _tree_sha256(after)
    if before != after or before_tree != after_tree:
        raise EvidenceLaneError(
            "SUCCESSOR_ORIGINAL_PACKAGE_MUTATED",
            "The immutable original package changed during successor construction.",
            status="FAIL",
            details={"before": before_tree, "after": after_tree},
        )
    index_path = output / "SUCCESSOR_RELEASES.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index = {
            "schema": "evidence-lane.state-travel-successor-index.v1",
            "legacy_expected_sha256": LEGACY_EXPECTED_STATE_TRAVEL_SHA256,
            "releases": {},
        }
    entry = {
        "release_sha": exact_sha,
        "bridge_sha256": bridge["bridge_sha256"],
        "original_tree_sha256": before_tree,
        "manifest_sha256": sha256_file(release_root / "manifest.json"),
        "candidate_id": candidate_id,
        "candidate_accepted": False,
    }
    existing = index["releases"].get(exact_sha)
    require(
        existing is None or existing == entry,
        "SUCCESSOR_INDEX_CONFLICT",
        "The successor index already binds this release to different evidence.",
        status="BLOCKED",
    )
    index["releases"][exact_sha] = entry
    atomic_write_json(index_path, index)
    return {
        "status": "PASS",
        "release_root": str(release_root),
        "release_sha": exact_sha,
        "legacy_expected_sha256": LEGACY_EXPECTED_STATE_TRAVEL_SHA256,
        "original_tree_sha256_before": before_tree,
        "original_tree_sha256_after": after_tree,
        "original_unchanged": True,
        "bridge_sha256": bridge["bridge_sha256"],
        "manifest_sha256": entry["manifest_sha256"],
        "actions": actions,
        "candidate_accepted": False,
        "state_travel_performed": False,
        "pointer_moved": False,
    }
