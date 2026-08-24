from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.current-route-file-refresh-receipt.v2"
REFRESH_ID = "TASK16_CURRENT_ROUTE_REFRESH_20260823_003"
REMOVED_ROOT_AUTHORITY = "TASK6_ROW231_CONTRACT_REBIND_AUTHORITY.json"
REMOVED_ROOT_AUTHORITY_PRIOR_SHA256 = (
    "B544A8D58B80D65AD5663A7A4E0D1E2F135E67D2A23EEBB5AA83E26CEB31CAB3"
)
OUTPUT_PATHS = {
    "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.json",
    "docs/CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.md",
}
EXPECTED_DIRECT_STATE_TRAVEL_FIELDS = [
    "project_id",
    "session_id",
    "authoritative_source_task_id",
    "runtime_attachment_donor_task_id",
    "destination_task_id",
    "destination_task_title",
]
ROOT_FILES = [
    ".dockerignore",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".vercelignore",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "LICENSE.md",
    "README.md",
    "SECURITY.md",
    REMOVED_ROOT_AUTHORITY,
    "pyproject.toml",
    "requirements.in",
]


def _git(repository: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _git_paths(repository: Path, *args: str) -> set[str]:
    return {
        item.decode("utf-8").replace("\\", "/")
        for item in _git(repository, *args).split(b"\0")
        if item
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _direct_state_travel_fields(mcp_server: Path) -> list[str]:
    module = ast.parse(mcp_server.read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "pv_state_travel_direct_force_same_worktree"
        ):
            return [argument.arg for argument in node.args.args]
    raise RuntimeError("Direct same-worktree State Travel route is missing.")


def _changed_against_head(repository: Path, path: str) -> bool:
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "HEAD", "--", path],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"Unable to compare tracked path: {path}")
    return result.returncode == 1


def _record(repository: Path, path: str) -> dict[str, Any]:
    if path in OUTPUT_PATHS:
        return {
            "path": path,
            "scope": "docs",
            "disposition": "RECEIPT_SELF_BOUND_BY_FINAL_GIT_TREE",
            "sha256": None,
            "prior_sha256": None,
            "bytes": None,
            "route_refresh_verified": True,
        }
    source = repository / Path(path)
    if not source.exists():
        if path != REMOVED_ROOT_AUTHORITY:
            raise RuntimeError(f"Unexpected missing indexed path: {path}")
        previous_sha256 = REMOVED_ROOT_AUTHORITY_PRIOR_SHA256
        prior = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=repository,
            check=False,
            capture_output=True,
        )
        if prior.returncode == 0:
            previous_sha256 = _sha256_bytes(prior.stdout)
        return {
            "path": path,
            "scope": "repository-root",
            "disposition": "REMOVED",
            "sha256": None,
            "prior_sha256": previous_sha256,
            "bytes": 0,
            "route_refresh_verified": True,
        }
    indexed_bytes = _git(repository, "show", f":{path}")
    disposition = (
        "CHANGED" if _changed_against_head(repository, path) else ("UNCHANGED_VERIFIED")
    )
    return {
        "path": path,
        "scope": "repository-root" if "/" not in path else path.split("/", 1)[0],
        "disposition": disposition,
        "sha256": _sha256_bytes(indexed_bytes),
        "prior_sha256": None,
        "bytes": len(indexed_bytes),
        "route_refresh_verified": True,
    }


def build_receipt(repository: Path) -> dict[str, Any]:
    root = repository.resolve()
    plugin = root / "plugins" / "evidence-lane-plugin"
    indexed = _git_paths(root, "ls-files", "-z")
    head = _git_paths(root, "ls-tree", "-r", "-z", "--name-only", "HEAD")
    tracked_paths = indexed
    removed_paths = head - indexed
    paths = sorted(tracked_paths)
    entries = [_record(root, path) for path in paths]
    entry_paths = {entry["path"] for entry in entries}
    if entry_paths != tracked_paths or len(entries) != len(entry_paths):
        raise RuntimeError(
            "The refresh receipt must cover every Git-tracked path exactly once."
        )
    required_root_files = set(ROOT_FILES) - {REMOVED_ROOT_AUTHORITY}
    if not required_root_files.issubset(entry_paths):
        raise RuntimeError(
            "The root refresh receipt does not cover every tracked root file."
        )
    if (root / REMOVED_ROOT_AUTHORITY).exists():
        raise RuntimeError("The obsolete Task6 root authority still exists.")

    runtime_catalog = _json(
        plugin / "src" / "evidence_lane_plugin" / "runtime-public-catalog.v1.json"
    )
    hooks = _json(plugin / "hooks" / "hooks.json").get("hooks")
    plugin_manifest = _json(plugin / ".codex-plugin" / "plugin.json")
    marketplace = _json(root / ".agents" / "plugins" / "marketplace.json")
    direct_fields = _direct_state_travel_fields(
        plugin / "src" / "evidence_lane_plugin" / "mcp_server.py"
    )
    if runtime_catalog.get("schema") != "evidence-lane.runtime-public-catalog.v1":
        raise RuntimeError("The runtime public catalog is not current.")
    if [runtime_catalog.get(key) for key in ("tools", "read", "write", "skills")] != [
        88,
        27,
        61,
        17,
    ]:
        raise RuntimeError("The runtime public catalog counts drifted.")
    if not isinstance(hooks, dict) or len(hooks) != 11:
        raise RuntimeError("The registered hook-event set is not exactly eleven.")
    if direct_fields != EXPECTED_DIRECT_STATE_TRAVEL_FIELDS:
        raise RuntimeError(
            "The direct State Travel schema is not the safe six-field route."
        )
    if not str(plugin_manifest.get("version", "")).startswith("3.0.0+codex."):
        raise RuntimeError(
            "The plugin manifest is not the current 3.0.0 cache identity."
        )
    if marketplace != {
        "name": "evidence-lane-github",
        "interface": {"displayName": "Main Git Plugin Version"},
        "plugins": [
            {
                "name": "evidence-lane-plugin",
                "source": {
                    "source": "local",
                    "path": "./plugins/evidence-lane-plugin",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Developer Tools",
            }
        ],
    }:
        raise RuntimeError("The supported repository marketplace entry drifted.")

    digest_payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    removed_history = [
        {
            "path": path,
            "disposition": "OBSOLETE_REMOVED",
            "prior_sha256": _sha256_bytes(_git(root, "show", f"HEAD:{path}")),
        }
        for path in sorted(removed_paths)
    ]
    if REMOVED_ROOT_AUTHORITY not in {row["path"] for row in removed_history}:
        removed_history.append(
            {
                "path": REMOVED_ROOT_AUTHORITY,
                "disposition": "OBSOLETE_REMOVED",
                "prior_sha256": REMOVED_ROOT_AUTHORITY_PRIOR_SHA256,
            }
        )
    path_set_payload = "\n".join(sorted(entry_paths)).encode("utf-8")
    base_commit = _git(root, "rev-parse", "HEAD").decode().strip()
    base_committed_at = (
        _git(root, "show", "-s", "--format=%cI", "HEAD").decode().strip()
    )
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "refresh_id": REFRESH_ID,
        "generated_at": base_committed_at,
        "repository": "rathee000001/evidence_lane_plugin",
        "branch": _git(root, "branch", "--show-current").decode().strip(),
        "base_commit": base_commit,
        "current_route": {
            "plugin_id": plugin_manifest["name"],
            "plugin_version": plugin_manifest["version"],
            "runtime_catalog": runtime_catalog,
            "hook_events": list(hooks),
            "direct_state_travel_fields": direct_fields,
            "github_app_commit_route": "github_app_exact_commit_push_v1",
            "github_app_main_promotion_route": "github_app_main_fast_forward_v3",
            "github_app_commit_actor": "evidence-lane[bot]",
            "main_live_work_allowed": False,
        },
        "summary": {
            "path_count": len(entries),
            "dispositions": dict(
                sorted(Counter(row["disposition"] for row in entries).items())
            ),
            "entry_set_sha256": _sha256_bytes(digest_payload),
            "entry_path_set_sha256": _sha256_bytes(path_set_payload),
            "tracked_path_set_equality": True,
            "root_file_count": len(ROOT_FILES),
            "removed_root_authority": REMOVED_ROOT_AUTHORITY,
        },
        "removed_history": sorted(removed_history, key=lambda row: row["path"]),
        "output_self_reference_law": (
            "The JSON and Markdown receipts are enumerated exactly once with the "
            "RECEIPT_SELF_BOUND_BY_FINAL_GIT_TREE disposition. Their final bytes are "
            "bound by the App-authored commit/tree receipt; every other tracked path "
            "is verified directly by SHA-256."
        ),
        "entries": entries,
    }


def write_receipts(
    repository: Path, json_path: Path, markdown_path: Path
) -> dict[str, Any]:
    receipt = build_receipt(repository)
    content = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    json_bytes = content.encode("utf-8")
    if not json_path.exists() or json_path.read_bytes() != json_bytes:
        json_path.write_bytes(json_bytes)
    digest = _sha256_bytes(content.encode("utf-8"))
    by_path = {row["path"]: row for row in receipt["entries"]}
    removed_by_path = {row["path"]: row for row in receipt["removed_history"]}
    lines = [
        "# Current-route file refresh receipt — 2026-08-23",
        "",
        f"- Status: **{receipt['status']}**",
        f"- Refresh ID: `{receipt['refresh_id']}`",
        f"- Base commit: `{receipt['base_commit']}`",
        f"- JSON SHA-256: `{digest}`",
        f"- Audited paths: **{receipt['summary']['path_count']}**",
        "- Current plugin route: `evidence-lane-plugin` 3.0.0, 88 actions (27 read / 61 write), 17 skills, 11 hook events.",
        "- Git rule: App-authored feature-branch commits only; no live implementation on `main`.",
        "",
        "## Repository-root files",
        "",
        "| Path | Disposition | SHA-256 |",
        "| --- | --- | --- |",
    ]
    for path in ROOT_FILES:
        if path in by_path:
            row = by_path[path]
            sha = row["sha256"] or "BOUND BY FINAL GIT TREE"
        else:
            row = removed_by_path[path]
            sha = f"REMOVED (prior `{row['prior_sha256']}`)"
        lines.append(f"| `{path}` | `{row['disposition']}` | `{sha}` |")
    lines.extend(
        [
            "",
            "The JSON authority contains one content-addressed record for every audited tracked path, including every unchanged-but-verified file.",
        ]
    )
    markdown_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    if not markdown_path.exists() or markdown_path.read_bytes() != markdown_bytes:
        markdown_path.write_bytes(markdown_bytes)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    receipt = write_receipts(
        args.repository.resolve(),
        args.json.resolve(),
        args.markdown.resolve(),
    )
    print(
        json.dumps(
            {"status": receipt["status"], "summary": receipt["summary"]}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
