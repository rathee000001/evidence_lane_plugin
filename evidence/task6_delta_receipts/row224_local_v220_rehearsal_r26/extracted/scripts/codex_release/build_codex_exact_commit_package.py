"""Build one unaccepted Codex package from an exact local Git commit export.

The builder uses read-only Git identity and archive operations. It never stages,
commits, pushes, installs, opens Codex, calls Evidence Lane lifecycle, creates a
candidate, moves a pointer, or infers HIL. Dirty and untracked checkout bytes
remain untouched and cannot enter the exported package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from build_release_candidate_rehearsal import build_rehearsal

SCHEMA = "evidence-lane.codex-exact-commit-package.v1.receipt"
BOUNDARY = "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^(?:agent|feature|fix|test|tests|chore)/[A-Za-z0-9._/-]+$")


class ExactCommitPackageError(RuntimeError):
    """Raised before output when the exact Git export is not deterministic."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _source_inventory(root: Path) -> dict[str, Any]:
    """Seal every exact Git-exported plugin file, not only package members."""

    rows = []
    for path in sorted(row for row in root.rglob("*") if row.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("_evidence_lane_rehearsal/"):
            continue
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "file_count": len(rows),
        "manifest_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
    }


def _git(repository: Path, arguments: list[str], *, timeout: int = 120) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise ExactCommitPackageError(
            f"Read-only Git command failed ({arguments[:2]}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _safe_extract(archive_path: Path, destination: Path) -> int:
    count = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise ExactCommitPackageError("Git archive member escaped its root.")
            target = (destination / Path(name)).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise ExactCommitPackageError(
                    "Git archive member escaped its extraction root."
                ) from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            count += 1
    return count


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ExactCommitPackageError(f"Refusing to overwrite {path}.")
        return
    path.write_bytes(content)


def build_exact_commit_package(
    *,
    repository: Path,
    plugin_path: str,
    branch: str,
    commit: str,
    output_dir: Path,
    expected_version: str,
) -> dict[str, Any]:
    repository = repository.resolve()
    output_dir = output_dir.resolve()
    normalized_commit = commit.strip().lower()
    normalized_branch = branch.strip()
    normalized_plugin_path = Path(plugin_path.replace("\\", "/")).as_posix().strip("/")
    if not repository.is_dir() or not (repository / ".git").exists():
        raise ExactCommitPackageError("Repository is not an exact local Git checkout.")
    if _SHA1.fullmatch(normalized_commit) is None:
        raise ExactCommitPackageError("Commit must be an exact 40-character SHA-1.")
    if _BRANCH.fullmatch(normalized_branch) is None:
        raise ExactCommitPackageError("Branch must be one governed non-protected branch.")
    if not normalized_plugin_path or ".." in Path(normalized_plugin_path).parts:
        raise ExactCommitPackageError("Plugin path must stay inside the repository.")

    resolved_commit = _git(
        repository,
        ["rev-parse", "--verify", f"{normalized_commit}^{{commit}}"],
    ).lower()
    resolved_tree = _git(
        repository,
        ["rev-parse", "--verify", f"{normalized_commit}^{{tree}}"],
    ).lower()
    branch_commit = _git(
        repository,
        ["rev-parse", "--verify", f"refs/heads/{normalized_branch}^{{commit}}"],
    ).lower()
    if resolved_commit != normalized_commit or branch_commit != normalized_commit:
        raise ExactCommitPackageError("The governed branch does not point to the commit.")
    if _SHA1.fullmatch(resolved_tree) is None:
        raise ExactCommitPackageError("Git did not return one exact tree SHA.")

    with tempfile.TemporaryDirectory(prefix="evidence-lane-exact-commit-") as raw:
        temporary = Path(raw)
        export_zip = temporary / "commit-export.zip"
        _git(
            repository,
            [
                "archive",
                "--format=zip",
                f"--output={export_zip}",
                normalized_commit,
                "--",
                normalized_plugin_path,
            ],
            timeout=300,
        )
        if not export_zip.is_file():
            raise ExactCommitPackageError("Git did not produce the exact export.")
        export_root = temporary / "export"
        export_root.mkdir()
        export_member_count = _safe_extract(export_zip, export_root)
        exported_plugin = export_root / normalized_plugin_path
        if not exported_plugin.is_dir():
            raise ExactCommitPackageError("The exact commit lacks the plugin path.")
        exported_plugin_inventory = _source_inventory(exported_plugin)
        if exported_plugin_inventory["file_count"] != export_member_count:
            raise ExactCommitPackageError(
                "The exact plugin export member count and source inventory diverged."
            )
        local_receipt = build_rehearsal(
            plugin_root=exported_plugin,
            output_dir=output_dir,
            base_commit=normalized_commit,
            base_tree=resolved_tree,
            expected_version=expected_version,
        )
        export_zip_sha256 = _sha256(export_zip)

    local_receipt_path = Path(str(local_receipt["receipt_path"])).resolve()
    archive_path = local_receipt_path.parent / str(local_receipt["archive"]["filename"])
    if not archive_path.is_file() or _sha256(archive_path) != local_receipt["archive"]["sha256"]:
        raise ExactCommitPackageError("The exact export package receipt drifted.")
    core = {
        "schema": SCHEMA,
        "boundary": BOUNDARY,
        "status": "PASS",
        "archive": dict(local_receipt["archive"]),
        "base_anchor": {"commit": normalized_commit, "tree": resolved_tree},
        "working_source_manifest_sha256": local_receipt[
            "working_source_manifest_sha256"
        ],
        "source_member_count": local_receipt["source_member_count"],
        "skill_count": local_receipt["skill_count"],
        "canonical_lane_count": local_receipt["canonical_lane_count"],
        "exact_commit_export": {
            "branch": normalized_branch,
            "commit": normalized_commit,
            "tree": resolved_tree,
            "plugin_path": normalized_plugin_path,
            "git_archive_sha256": export_zip_sha256,
            "git_archive_member_count": export_member_count,
            "plugin_source_manifest_sha256": exported_plugin_inventory[
                "manifest_sha256"
            ],
            "plugin_source_member_count": exported_plugin_inventory["file_count"],
            "projection_clean": True,
            "working_checkout_bytes_used": False,
            "untracked_bytes_used": False,
        },
        "local_rehearsal_receipt_sha256": _sha256(local_receipt_path),
        "git_invoked": True,
        "git_write_invoked": False,
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "hil_inferred": False,
    }
    core["receipt_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    receipt_path = output_dir / (
        f"EXACT_COMMIT_PACKAGE_{normalized_commit[:12]}_"
        f"{core['archive']['sha256'][:16]}.json"
    )
    _write_immutable(receipt_path, _json_bytes(core))
    return {**core, "receipt_path": str(receipt_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--plugin-path", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    return parser


def main() -> int:
    result = build_exact_commit_package(**vars(_parser().parse_args()))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
