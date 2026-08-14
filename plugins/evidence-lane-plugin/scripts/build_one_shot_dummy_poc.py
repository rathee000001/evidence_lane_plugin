"""Build and audit one deterministic dummy package across all eighteen lanes.

The GitHub Code lane comes from a temporary synthetic Git repository with a
real three-commit parent chain. Every other lane receives generated,
public-safe fixture data. The command emits an initial PV, an unchanged
Refresh, forensic reports, and a sealed receipt without touching a governed
source repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from build_real_git_poc import FIXTURE_ROOT, _fixture_sources, _git
from evidence_lane_plugin.forensic_audit import (
    audit_lane_bundle,
    write_forensic_audit_reports,
)
from evidence_lane_plugin.hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.lane_engine import build_lane_bundle, validate_lane_bundle
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

SCHEMA = "evidence-lane.one-shot-dummy-poc.v1"
DUMMY_GIT_ROOT = "dummy-git-source"
DUMMY_GIT_LANE = "github_code"


def _commit(repository: Path, message: str, timestamp: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Evidence Lane Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@evidencelane.invalid",
        "GIT_COMMITTER_NAME": "Evidence Lane Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@evidencelane.invalid",
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_DATE": timestamp,
    }
    subprocess.run(
        ["git", "-C", str(repository), "add", "--all"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return _git(repository, "rev-parse", "HEAD")


def _build_synthetic_repository(repository: Path) -> dict[str, str]:
    repository.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(repository), "init", "-b", "main"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "config", "user.name", "Evidence Lane Fixture")
    _git(repository, "config", "user.email", "fixture@evidencelane.invalid")

    git_root = repository / DUMMY_GIT_ROOT
    git_root.mkdir()
    (git_root / "app.py").write_text(
        '"""Synthetic Git-lane fixture."""\n\nVALUE = 1\n',
        encoding="utf-8",
    )
    (git_root / "README.md").write_text(
        "# Synthetic Git history\n\nCommit one establishes the fixture.\n",
        encoding="utf-8",
    )
    _commit(repository, "fixture: establish source", "2026-01-01T00:00:00Z")

    (git_root / "app.py").write_text(
        '"""Synthetic Git-lane fixture."""\n\nVALUE = 2\n\ndef answer() -> int:\n    return VALUE * 21\n',
        encoding="utf-8",
    )
    (git_root / "README.md").write_text(
        "# Synthetic Git history\n\nCommit two adds a governed behavior change.\n",
        encoding="utf-8",
    )
    _commit(repository, "fixture: add governed behavior", "2026-01-02T00:00:00Z")

    (git_root / "test_app.py").write_text(
        "from app import answer\n\n\ndef test_answer() -> None:\n    assert answer() == 42\n",
        encoding="utf-8",
    )
    overrides = _fixture_sources(repository)
    _commit(repository, "fixture: add proof and all lane sources", "2026-01-03T00:00:00Z")

    overrides.update(
        {
            f"{DUMMY_GIT_ROOT}/app.py": DUMMY_GIT_LANE,
            f"{DUMMY_GIT_ROOT}/README.md": DUMMY_GIT_LANE,
            f"{DUMMY_GIT_ROOT}/test_app.py": DUMMY_GIT_LANE,
        }
    )
    if _git(repository, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Synthetic repository is unexpectedly dirty.")
    return overrides


def _route_policy(bundle: Path) -> dict[str, Any]:
    routes = json.loads((bundle / "routes.json").read_text(encoding="utf-8"))[
        "routes"
    ]
    by_lane = {
        lane: sorted(path for path, routed_lane in routes.items() if routed_lane == lane)
        for lane in CANONICAL_LANE_IDS
    }
    invalid_git = [
        path
        for path in by_lane[DUMMY_GIT_LANE]
        if not path.startswith(f"{DUMMY_GIT_ROOT}/")
    ]
    invalid_fixture = {
        lane: [
            path
            for path in by_lane[lane]
            if not path.startswith(f"{FIXTURE_ROOT}/")
        ]
        for lane in CANONICAL_LANE_IDS
        if lane != DUMMY_GIT_LANE
    }
    invalid_fixture = {lane: paths for lane, paths in invalid_fixture.items() if paths}
    empty_lanes = [lane for lane, paths in by_lane.items() if not paths]
    return {
        "status": (
            "PASS"
            if not invalid_git and not invalid_fixture and not empty_lanes
            else "FAIL"
        ),
        "dummy_git_lane": DUMMY_GIT_LANE,
        "dummy_git_sources": by_lane[DUMMY_GIT_LANE],
        "fixture_lane_count": len(CANONICAL_LANE_IDS) - 1,
        "sources_by_lane": by_lane,
        "invalid_dummy_git_sources": invalid_git,
        "invalid_fixture_sources": invalid_fixture,
        "empty_lanes": empty_lanes,
    }


def _git_history_proof(bundle: Path) -> dict[str, Any]:
    lane = LANE_REGISTRY[DUMMY_GIT_LANE]
    database = bundle / DUMMY_GIT_LANE / lane.sqlite_filename
    with sqlite3.connect(database) as connection:
        commits = connection.execute(
            "SELECT commit_sha, ordinal, message FROM git_commit_registry ORDER BY ordinal"
        ).fetchall()
        parents = connection.execute(
            "SELECT commit_sha, parent_sha, parent_ordinal FROM git_commit_parent "
            "ORDER BY commit_sha, parent_ordinal"
        ).fetchall()
        change_count = connection.execute(
            "SELECT COUNT(*) FROM git_file_change"
        ).fetchone()[0]
    parent_by_commit = {row[0]: row[1] for row in parents}
    ordered_shas = [row[0] for row in commits]
    chain_valid = (
        len(ordered_shas) == 3
        and len(parents) == 2
        and ordered_shas[0] not in parent_by_commit
        and parent_by_commit.get(ordered_shas[1]) == ordered_shas[0]
        and parent_by_commit.get(ordered_shas[2]) == ordered_shas[1]
    )
    return {
        "status": "PASS" if chain_valid and change_count >= 5 else "FAIL",
        "commit_count": len(commits),
        "parent_count": len(parents),
        "file_change_count": change_count,
        "chain_valid": chain_valid,
        "commits": [
            {"sha": row[0], "ordinal": row[1], "message": row[2]} for row in commits
        ],
    }


def _package_manifest(output: Path) -> dict[str, Any]:
    members = {
        path.relative_to(output).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "PACKAGE_MANIFEST.json"
    }
    manifest = {
        "schema": "evidence-lane.one-shot-dummy-poc-package.v1",
        "member_count": len(members),
        "members": members,
        "package_sha256": sha256_bytes(canonical_json_bytes(members)),
    }
    atomic_write_json(output / "PACKAGE_MANIFEST.json", manifest)
    return manifest


def build_one_shot_dummy_poc(output: Path, subject: str) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Output path already exists: {output}")

    with tempfile.TemporaryDirectory(prefix="evidence-lane-one-shot-dummy-") as temp:
        repository = Path(temp) / "source"
        overrides = _build_synthetic_repository(repository)
        head = _git(repository, "rev-parse", "HEAD")
        tree = _git(repository, "rev-parse", "HEAD^{tree}")
        output.mkdir(parents=True)
        initial_root = output / "PV1_INITIAL"
        refresh_root = output / "PV2_REFRESH"
        initial = build_lane_bundle(
            repository_root=repository,
            output_directory=initial_root,
            code_mode=DUMMY_GIT_LANE,
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV-DUMMY-1",
            pointer_generation=0,
            source_overrides=overrides,
        )
        refresh = build_lane_bundle(
            repository_root=repository,
            output_directory=refresh_root,
            code_mode=DUMMY_GIT_LANE,
            parent_lane_bundle=initial_root,
            parent_pv="PV-DUMMY-1",
            proposed_pv="PV-DUMMY-2",
            pointer_generation=1,
        )

    initial_validation = validate_lane_bundle(initial_root)
    refresh_validation = validate_lane_bundle(refresh_root)
    initial_policy = _route_policy(initial_root)
    refresh_policy = _route_policy(refresh_root)
    history = _git_history_proof(initial_root)
    initial_audit = audit_lane_bundle(initial_root, subject=f"{subject} - initial")
    refresh_audit = audit_lane_bundle(refresh_root, subject=f"{subject} - Refresh")
    initial_reports = write_forensic_audit_reports(
        initial_audit, output / "FORENSIC_INITIAL"
    )
    refresh_reports = write_forensic_audit_reports(
        refresh_audit, output / "FORENSIC_REFRESH"
    )
    expected_lanes = list(CANONICAL_LANE_IDS)
    all_reused = refresh["summary"]["byte_reused_lanes"] == expected_lanes
    lane_dirs = sorted(
        path.name for path in initial_root.iterdir() if path.is_dir()
    )
    passed = all(
        (
            initial_validation["valid"],
            refresh_validation["valid"],
            initial_policy["status"] == "PASS",
            refresh_policy["status"] == "PASS",
            history["status"] == "PASS",
            initial_audit["status"] == "PASS",
            refresh_audit["status"] == "PASS",
            initial["emitted_lane_ids"] == expected_lanes,
            initial["omitted_lane_ids"] == [],
            lane_dirs == sorted(expected_lanes),
            all_reused,
        )
    )
    receipt = {
        "schema": SCHEMA,
        "subject": subject,
        "status": "PASS" if passed else "FAIL",
        "synthetic_git": {
            "branch": "main",
            "head": head,
            "tree": tree,
            **history,
        },
        "lane_policy": {
            "emission": "LOADED_OR_DETECTED_ONLY",
            "loaded_lane_count": len(expected_lanes),
            "emitted_lane_ids": initial["emitted_lane_ids"],
            "omitted_lane_ids": initial["omitted_lane_ids"],
            "actual_lane_directory_ids": lane_dirs,
            "initial_route_policy": initial_policy,
            "refresh_route_policy": refresh_policy,
        },
        "initial": {
            "bundle_sha256": initial_validation["bundle_sha256"],
            "valid": initial_validation["valid"],
            "forensic_status": initial_audit["status"],
            "forensic_manifest_sha256": initial_reports["manifest_sha256"],
        },
        "refresh": {
            "bundle_sha256": refresh_validation["bundle_sha256"],
            "valid": refresh_validation["valid"],
            "all_eighteen_lanes_byte_reused": all_reused,
            "forensic_status": refresh_audit["status"],
            "forensic_manifest_sha256": refresh_reports["manifest_sha256"],
        },
        "verdict": "PURSUE" if passed else "FIX_THEN_PURSUE",
        "confidence_percent": 99,
        "evidence_that_would_change_verdict": (
            "Any invalid lane package, missing loaded lane, unexpected lane directory, "
            "broken Git parent chain, forensic failure, or unchanged Refresh byte mismatch."
        ),
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    atomic_write_json(output / "POC_RECEIPT.json", receipt)
    readme = f"""# Evidence Lane one-shot dummy PoC

- Status: **{receipt['status']}**
- Loaded and emitted lanes: **18**
- Synthetic Git commits: **{history['commit_count']}**
- Synthetic Git parents: **{history['parent_count']}**
- Unchanged Refresh byte reuse: **{all_reused}**

The GitHub Code lane is backed by a real temporary Git repository and commit
parent chain. The other seventeen lanes contain deterministic fixture evidence.
No governed source repository or accepted pointer is modified.

## Verdict

**{receipt['verdict']} - {receipt['confidence_percent']}% confidence.**
"""
    atomic_write_bytes(output / "README.md", readme.encode("utf-8"))
    package = _package_manifest(output)
    result = {
        "status": receipt["status"],
        "output": str(output),
        "synthetic_git_head": head,
        "synthetic_git_tree": tree,
        "receipt_sha256": receipt["receipt_sha256"],
        "package_sha256": package["package_sha256"],
        "member_count": package["member_count"],
    }
    if not passed:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument(
        "--subject",
        default="Evidence Lane one-shot dummy proof across all eighteen lanes",
    )
    args = parser.parse_args()
    result = build_one_shot_dummy_poc(args.output_directory, args.subject)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
