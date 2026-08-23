from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.intermediate-dirty-scope-manifest.v1"


def _git(repository: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _status_entries(repository: Path) -> list[dict[str, str]]:
    raw = _git(
        repository,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    records = raw.split(b"\0")
    result: list[dict[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        text = record.decode("utf-8", errors="surrogateescape")
        status = text[:2]
        path = text[3:]
        original_path: str | None = None
        if "R" in status or "C" in status:
            if index >= len(records) or not records[index]:
                raise RuntimeError(f"Missing rename/copy origin for {path!r}")
            original_path = records[index].decode(
                "utf-8", errors="surrogateescape"
            )
            index += 1
        entry = {"path": path.replace("\\", "/"), "status": status}
        if original_path is not None:
            entry["original_path"] = original_path.replace("\\", "/")
        result.append(entry)
    return result


def _matches(path: str, values: tuple[str, ...]) -> bool:
    normalized = path.rstrip("/")
    return any(
        normalized == value.rstrip("/")
        or normalized.startswith(value.rstrip("/") + "/")
        for value in values
    )


def build_manifest(
    *,
    repository: Path,
    output: Path,
    exclude_prefixes: tuple[str, ...],
    exclude_paths: tuple[str, ...],
    preserved_ignored_prefixes: tuple[str, ...] = (),
    require_fully_staged: bool = False,
    require_no_exclusions: bool = False,
) -> dict[str, Any]:
    repository = repository.resolve()
    output = output.resolve()
    try:
        output_relative = output.relative_to(repository).as_posix()
    except ValueError as exc:
        raise ValueError("Output must be inside the repository.") from exc

    status_records = _status_entries(repository)
    observed_paths = {record["path"] for record in status_records}
    for prefix in preserved_ignored_prefixes:
        root = repository / prefix
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(repository).as_posix()
            if relative not in observed_paths:
                status_records.append({"path": relative, "status": "!!"})
                observed_paths.add(relative)

    governed_status_records = [
        record
        for record in status_records
        if record["path"] != output_relative
    ]
    if require_fully_staged:
        unstaged_paths = [
            record["path"]
            for record in governed_status_records
            if record["status"] in {"??", "!!"}
            or len(record["status"]) < 2
            or record["status"][1] != " "
        ]
        if unstaged_paths:
            sample = ", ".join(unstaged_paths[:10])
            suffix = "" if len(unstaged_paths) <= 10 else ", ..."
            raise RuntimeError(
                "Current checkpoint scope is not fully staged; refusing PASS for: "
                f"{sample}{suffix}"
            )

    entries: list[dict[str, Any]] = []
    include_count = 0
    exclude_count = 0
    include_bytes = 0
    exclude_bytes = 0
    for record in status_records:
        relative = record["path"]
        if relative == output_relative:
            continue
        source = repository / Path(relative)
        if source.is_symlink():
            target = os.readlink(source)
            raw = target.encode("utf-8")
            size = len(raw)
            sha256 = hashlib.sha256(raw).hexdigest().upper()
            kind = "symlink"
        elif source.is_file():
            size = source.stat().st_size
            sha256 = _sha256(source)
            kind = "file"
        elif not source.exists() and "D" in record["status"]:
            size = 0
            sha256 = None
            kind = "deleted"
        else:
            raise RuntimeError(f"Dirty path is not a file: {relative}")

        excluded = _matches(relative, exclude_prefixes) or _matches(
            relative, exclude_paths
        )
        if excluded and require_no_exclusions:
            raise RuntimeError(
                "Current all-tracked checkpoint forbids exclusions; refusing PASS for: "
                f"{relative}"
            )
        decision = "EXCLUDE_PRESERVE_ON_DISK" if excluded else "INCLUDE"
        reason = (
            "EXPLICIT_NON_SOURCE_OR_HISTORICAL_GENERATED_SCOPE"
            if excluded
            else "CURRENT_V3_SOURCE_DOC_TEST_OR_GOVERNED_SMALL_RECEIPT"
        )
        entry: dict[str, Any] = {
            **record,
            "kind": kind,
            "size_bytes": size,
            "sha256": sha256,
            "decision": decision,
            "reason": reason,
        }
        entries.append(entry)
        if excluded:
            exclude_count += 1
            exclude_bytes += size
        else:
            include_count += 1
            include_bytes += size

    entries.sort(key=lambda item: item["path"])
    entry_digest = hashlib.sha256(
        b"".join(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
            for entry in entries
        )
    ).hexdigest().upper()
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "PASS",
        "generated_at": datetime.now(UTC).isoformat(),
        "repository": ".",
        "repository_directory_name": repository.name,
        "branch": _git(repository, "branch", "--show-current")
        .decode("utf-8")
        .strip(),
        "head_commit": _git(repository, "rev-parse", "HEAD")
        .decode("ascii")
        .strip(),
        "head_tree": _git(repository, "rev-parse", "HEAD^{tree}")
        .decode("ascii")
        .strip(),
        "output_path": output_relative,
        "output_self_reference_law": (
            "THE_MANIFEST_FILE_IS_COMMITTED_BUT_EXCLUDED_FROM_ITS_OWN_ENTRY_DIGEST"
        ),
        "exclusion_law": (
            "EVERY_EXCLUDED_BYTE_REMAINS_ON_DISK_AND_IS_IDENTIFIED_BY_PATH_SIZE_AND_SHA256"
        ),
        "exclude_prefixes": list(exclude_prefixes),
        "exclude_paths": list(exclude_paths),
        "preserved_ignored_prefixes": list(preserved_ignored_prefixes),
        "require_fully_staged": require_fully_staged,
        "require_no_exclusions": require_no_exclusions,
        "scope_mode": (
            "ALL_DIRTY_PATHS_INCLUDED"
            if require_no_exclusions
            else "EXPLICIT_INCLUDE_EXCLUDE"
        ),
        "summary": {
            "dirty_path_count": len(entries),
            "included_path_count": include_count,
            "included_bytes": include_bytes,
            "excluded_path_count": exclude_count,
            "excluded_bytes": exclude_bytes,
            "entry_digest_sha256": entry_digest,
        },
        "entries": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-prefix", action="append", default=[])
    parser.add_argument("--exclude-path", action="append", default=[])
    parser.add_argument(
        "--preserved-ignored-prefix", action="append", default=[]
    )
    parser.add_argument("--require-fully-staged", action="store_true")
    parser.add_argument("--require-no-exclusions", action="store_true")
    args = parser.parse_args()
    manifest = build_manifest(
        repository=args.repository,
        output=args.output,
        exclude_prefixes=tuple(args.exclude_prefix),
        exclude_paths=tuple(args.exclude_path),
        preserved_ignored_prefixes=tuple(args.preserved_ignored_prefix),
        require_fully_staged=args.require_fully_staged,
        require_no_exclusions=args.require_no_exclusions,
    )
    print(json.dumps(manifest["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
