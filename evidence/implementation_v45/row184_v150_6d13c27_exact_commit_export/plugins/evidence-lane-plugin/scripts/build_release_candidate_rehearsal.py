"""Build a deterministic, non-lifecycle Evidence Lane package rehearsal.

This command deliberately does not call Git or the Evidence Lane lifecycle.  It
packages the current plugin bytes for local verification, records the sealed
base commit/tree as an anchor, and gives the working source its own manifest
identity.  The result is not a governed candidate and cannot be accepted or
promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.non-lifecycle-local-package-rehearsal.v1"
BOUNDARY = "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
EXPECTED_SKILL_COUNT = 15
EXPECTED_LANE_COUNT = 18

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".runtime",
        ".tox",
        ".venv",
        ".vercel",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
    }
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        ".coverage",
        "Thumbs.db",
    }
)
EXCLUDED_FILE_SUFFIXES = frozenset(
    {".log", ".map", ".pyc", ".pyo", ".tmp", ".tsbuildinfo"}
)
FORBIDDEN_3D_SUFFIXES = frozenset({".glb", ".gltf"})
DEPENDENCY_FILENAMES = frozenset(
    {
        ".mcp.json",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.lock.txt",
        "requirements.txt",
        "yarn.lock",
    }
)
REQUIRED_MEMBERS = frozenset(
    {
        ".app.json",
        ".codex-plugin/plugin.json",
        ".mcp.json",
        "COPYRIGHT.md",
        "LICENSE.md",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "assets/evidence-lane-icon.png",
        "chatgpt-app-submission.json",
        "evidence/prompt_studio/manifest.json",
        "evidence/prompt_studio/studio_search.sqlite",
        "remote_adapter/app/manifest.ts",
        "remote_adapter/package.json",
        "remote_adapter/pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.lock.txt",
    }
)
SYNTHETIC_ROOT = "_evidence_lane_rehearsal"

SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(rb"sk-[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("github_fine_grained_token", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}")),
    ("google_api_key", re.compile(rb"AIza[0-9A-Za-z_-]{30,}")),
    ("slack_token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}")),
)


class PackageBoundaryError(RuntimeError):
    """Raised when source bytes violate the local-package boundary."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _is_environment_file(name: str) -> bool:
    lowered = name.casefold()
    return lowered == ".env" or (
        lowered.startswith(".env.") and lowered != ".env.example"
    )


def _excluded_file(path: Path) -> bool:
    name = path.name
    lowered = name.casefold()
    return (
        name in EXCLUDED_FILE_NAMES
        or path.suffix.casefold() in EXCLUDED_FILE_SUFFIXES
        or _is_environment_file(name)
        or (lowered.startswith(".codex-") and lowered != ".codex-plugin")
        or lowered.endswith(".zip")
    )


def _release_paths(plugin_root: Path) -> list[Path]:
    paths: list[Path] = []
    for raw_root, raw_directories, raw_files in os.walk(plugin_root, topdown=True):
        root = Path(raw_root)
        allowed_directories: list[str] = []
        for name in sorted(raw_directories):
            if name in EXCLUDED_DIRECTORY_NAMES or name.casefold().endswith(".egg-info"):
                continue
            candidate = root / name
            if candidate.is_symlink():
                raise PackageBoundaryError(
                    f"Symlinked directories are forbidden in the package: {candidate}"
                )
            allowed_directories.append(name)
        raw_directories[:] = allowed_directories

        for name in sorted(raw_files):
            candidate = root / name
            if _excluded_file(candidate):
                continue
            if candidate.is_symlink():
                raise PackageBoundaryError(
                    f"Symlinked files are forbidden in the package: {candidate}"
                )
            if candidate.suffix.casefold() in FORBIDDEN_3D_SUFFIXES:
                raise PackageBoundaryError(
                    f"Generated 3D assets are forbidden in the package: {candidate}"
                )
            paths.append(candidate)
    return sorted(paths, key=lambda item: item.relative_to(plugin_root).as_posix())


def _scan_secret_bytes(relative: str, content: bytes) -> None:
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            raise PackageBoundaryError(
                f"High-confidence {label} material detected in {relative}."
            )


def _source_inventory(plugin_root: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    records: list[dict[str, Any]] = []
    sources: dict[str, Path] = {}
    for path in _release_paths(plugin_root):
        relative = path.relative_to(plugin_root).as_posix()
        content = path.read_bytes()
        _scan_secret_bytes(relative, content)
        if path.name.casefold() in DEPENDENCY_FILENAMES and re.search(
            rb"(?i)\bmeshy(?:[-_/][A-Za-z0-9_.-]+)?\b", content
        ):
            raise PackageBoundaryError(
                f"Meshy is present in dependency or MCP configuration: {relative}"
            )
        records.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": _sha256_bytes(content),
            }
        )
        sources[relative] = path
    return records, sources


def _validate_sha1(label: str, value: str) -> str:
    normalized = value.strip().casefold()
    if re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
        raise PackageBoundaryError(f"{label} must be an exact 40-character SHA-1.")
    return normalized


def _skill_inventory(plugin_root: Path) -> dict[str, Any]:
    skill_files = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if len(skill_files) != EXPECTED_SKILL_COUNT:
        raise PackageBoundaryError(
            f"Expected {EXPECTED_SKILL_COUNT} skills, found {len(skill_files)}."
        )
    rows = []
    for path in skill_files:
        relative = path.relative_to(plugin_root).as_posix()
        rows.append(
            {
                "name": path.parent.name,
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "schema": f"{SCHEMA}.skill-inventory",
        "count": len(rows),
        "skills": rows,
    }


def _lane_inventory(plugin_root: Path) -> dict[str, Any]:
    lane_root = plugin_root / "remote_adapter" / "public" / "dummy-lane-packages"
    lanes = sorted(path for path in lane_root.iterdir() if path.is_dir())
    if len(lanes) != EXPECTED_LANE_COUNT:
        raise PackageBoundaryError(
            f"Expected {EXPECTED_LANE_COUNT} public lane proofs, found {len(lanes)}."
        )
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        files = sorted(path for path in lane.iterdir() if path.is_file())
        names = {path.name for path in files}
        required_shapes = {
            "dot": sum(name.endswith(".dot") for name in names),
            "mmd": sum(name.endswith(".mmd") for name in names),
            "png_8k": sum(name.endswith(".mmd.8k.png") for name in names),
            "receipt": sum(name == "refresh_receipt.json" for name in names),
            "sqlite": sum(name.endswith(".sqlite") for name in names),
            "svg_vector": sum(name.endswith(".mmd.vector.svg") for name in names),
        }
        if any(count != 1 for count in required_shapes.values()):
            raise PackageBoundaryError(
                f"Lane {lane.name} does not have one exact six-artifact proof set: "
                f"{required_shapes}"
            )
        rows.append(
            {
                "lane_id": lane.name,
                "artifact_count": len(files),
                "artifacts": [path.relative_to(plugin_root).as_posix() for path in files],
            }
        )
    return {
        "schema": f"{SCHEMA}.lane-bundle-inventory",
        "lane_count": len(rows),
        "lanes": rows,
    }


def _write_zip_member(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    info.flag_bits |= 0x800
    archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _verify_archive(path: Path, expected: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(expected):
            raise PackageBoundaryError("Archive members are not exact and lexically sorted.")
        if len(names) != len(set(names)):
            raise PackageBoundaryError("Archive contains duplicate members.")
        for info in infos:
            member = info.filename
            pure_parts = Path(member).parts
            if member.startswith(("/", "\\")) or ".." in pure_parts or "\\" in member:
                raise PackageBoundaryError(f"Unsafe archive member: {member}")
            if info.date_time != FIXED_ZIP_TIME:
                raise PackageBoundaryError(f"Non-deterministic timestamp on {member}.")
            if ((info.external_attr >> 16) & 0o777) != 0o600:
                raise PackageBoundaryError(f"Unsafe archive mode on {member}.")
            if _sha256_bytes(archive.read(info)) != expected[member]:
                raise PackageBoundaryError(f"Archive member hash mismatch: {member}")


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise PackageBoundaryError(f"Refusing to overwrite different evidence: {path}")
        return
    path.write_bytes(content)


def build_rehearsal(
    *,
    plugin_root: Path,
    output_dir: Path,
    base_commit: str,
    base_tree: str,
    expected_version: str,
) -> dict[str, Any]:
    plugin_root = plugin_root.resolve()
    output_dir = output_dir.resolve()
    base_commit = _validate_sha1("base_commit", base_commit)
    base_tree = _validate_sha1("base_tree", base_tree)
    if not plugin_root.is_dir():
        raise PackageBoundaryError(f"Plugin root does not exist: {plugin_root}")

    plugin_manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    plugin_manifest = json.loads(plugin_manifest_path.read_text(encoding="utf-8"))
    if plugin_manifest.get("version") != expected_version:
        raise PackageBoundaryError(
            "Plugin version mismatch: "
            f"expected {expected_version}, got {plugin_manifest.get('version')!r}."
        )

    source_records, source_paths = _source_inventory(plugin_root)
    source_names = {record["path"] for record in source_records}
    missing = sorted(REQUIRED_MEMBERS - source_names)
    if missing:
        raise PackageBoundaryError(f"Required package members are missing: {missing}")

    source_manifest_sha = _sha256_bytes(_json_bytes(source_records))
    skill_inventory = _skill_inventory(plugin_root)
    lane_inventory = _lane_inventory(plugin_root)
    source_manifest = {
        "schema": f"{SCHEMA}.source-manifest",
        "boundary": BOUNDARY,
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "git_invoked": False,
        "base_anchor": {"commit": base_commit, "tree": base_tree},
        "working_source_identity": {
            "algorithm": "SHA256_CANONICAL_MEMBER_RECORDS_V1",
            "sha256": source_manifest_sha,
            "member_count": len(source_records),
            "total_bytes": sum(record["bytes"] for record in source_records),
        },
        "exclusion_policy": {
            "directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
            "directory_suffixes": [".egg-info"],
            "file_suffixes": sorted(EXCLUDED_FILE_SUFFIXES),
            "environment_files": "ALL_EXCEPT_DOT_ENV_EXAMPLE",
            "local_codex_files": "EXCLUDED",
            "zip_files": "EXCLUDED",
        },
        "members": source_records,
    }
    synthetic: dict[str, bytes] = {
        f"{SYNTHETIC_ROOT}/lane-bundle-inventory.json": _json_bytes(lane_inventory),
        f"{SYNTHETIC_ROOT}/source-manifest.json": _json_bytes(source_manifest),
        f"{SYNTHETIC_ROOT}/skill-inventory.json": _json_bytes(skill_inventory),
    }
    synthetic_hashes = {
        name: _sha256_bytes(content) for name, content in sorted(synthetic.items())
    }
    exit_slip = {
        "schema": f"{SCHEMA}.exit-slip",
        "boundary": BOUNDARY,
        "status": "LOCAL_REHEARSAL_VERIFIED_NOT_A_GOVERNED_CANDIDATE",
        "version": expected_version,
        "base_anchor": {"commit": base_commit, "tree": base_tree},
        "working_source_manifest_sha256": source_manifest_sha,
        "skill_count": skill_inventory["count"],
        "lane_count": lane_inventory["lane_count"],
        "synthetic_metadata_sha256": synthetic_hashes,
        "negative_proofs": {
            "cache_or_runtime_members": 0,
            "generated_glb_or_gltf_members": 0,
            "governed_candidate_created": False,
            "git_invoked": False,
            "high_confidence_secrets": 0,
            "meshy_dependency_or_mcp_binding": 0,
            "pointer_movement": False,
        },
        "next_lifecycle_boundary": (
            "A later authorized governed Refresh must bind the then-final committed "
            "source; this rehearsal cannot be accepted or fused."
        ),
    }
    synthetic[f"{SYNTHETIC_ROOT}/exit-slip.json"] = _json_bytes(exit_slip)

    all_names = sorted([*source_paths, *synthetic])
    expected_hashes = {
        record["path"]: record["sha256"] for record in source_records
    }
    expected_hashes.update(
        {name: _sha256_bytes(content) for name, content in synthetic.items()}
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor = _sha256_bytes(
        _json_bytes(
            {
                "source_manifest_sha256": source_manifest_sha,
                "synthetic_member_sha256": {
                    name: expected_hashes[name] for name in sorted(synthetic)
                },
            }
        )
    )[:16]
    archive_name = f"evidence-lane-{expected_version}-local-rehearsal-{descriptor}.zip"
    final_archive = output_dir / archive_name
    handle, raw_temporary = tempfile.mkstemp(
        prefix=".row181-package-", suffix=".tmp", dir=output_dir
    )
    os.close(handle)
    temporary = Path(raw_temporary)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            for name in all_names:
                if name in synthetic:
                    content = synthetic[name]
                else:
                    content = source_paths[name].read_bytes()
                    if _sha256_bytes(content) != expected_hashes[name]:
                        raise PackageBoundaryError(
                            f"Source changed while the package was being built: {name}"
                        )
                _write_zip_member(archive, name, content)
        _verify_archive(temporary, expected_hashes)
        archive_sha = _sha256_file(temporary)
        if final_archive.exists():
            if _sha256_file(final_archive) != archive_sha:
                raise PackageBoundaryError(
                    f"Refusing to overwrite different archive: {final_archive}"
                )
        else:
            temporary.replace(final_archive)
    finally:
        temporary.unlink(missing_ok=True)

    receipt = {
        "schema": f"{SCHEMA}.receipt",
        "boundary": BOUNDARY,
        "status": "PASS",
        "archive": {
            "filename": archive_name,
            "bytes": final_archive.stat().st_size,
            "sha256": _sha256_file(final_archive),
            "member_count": len(all_names),
        },
        "base_anchor": {"commit": base_commit, "tree": base_tree},
        "working_source_manifest_sha256": source_manifest_sha,
        "source_member_count": len(source_records),
        "skill_count": skill_inventory["count"],
        "lane_count": lane_inventory["lane_count"],
        "governed_candidate_created": False,
        "git_invoked": False,
        "accepted_pointer_moved": False,
        "next_lifecycle_boundary": exit_slip["next_lifecycle_boundary"],
    }
    receipt_bytes = _json_bytes(receipt)
    receipt_name = f"LOCAL_PACKAGE_REHEARSAL_{receipt['archive']['sha256'][:16]}.json"
    receipt_path = output_dir / receipt_name
    _write_immutable(receipt_path, receipt_bytes)
    return {**receipt, "receipt_path": str(receipt_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--base-tree", required=True)
    parser.add_argument("--expected-version", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = build_rehearsal(
        plugin_root=args.plugin_root,
        output_dir=args.output_dir,
        base_commit=args.base_commit,
        base_tree=args.base_tree,
        expected_version=args.expected_version,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
