from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.current-route-file-refresh-receipt.v1"
REFRESH_ID = "TASK16_CURRENT_ROUTE_REFRESH_20260823_001"
REMOVED_ROOT_AUTHORITY = "TASK6_ROW231_CONTRACT_REBIND_AUTHORITY.json"
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
    source = repository / Path(path)
    if not source.exists():
        if path != REMOVED_ROOT_AUTHORITY:
            raise RuntimeError(f"Unexpected missing indexed path: {path}")
        previous = _git(repository, "show", f"HEAD:{path}")
        return {
            "path": path,
            "scope": "repository-root",
            "disposition": "REMOVED",
            "sha256": None,
            "prior_sha256": _sha256_bytes(previous),
            "bytes": 0,
            "route_refresh_verified": True,
        }
    indexed_bytes = _git(repository, "show", f":{path}")
    disposition = "CHANGED" if _changed_against_head(repository, path) else (
        "UNCHANGED_VERIFIED"
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
    paths = sorted((indexed | head) - OUTPUT_PATHS)
    entries = [_record(root, path) for path in paths]
    entry_paths = {entry["path"] for entry in entries}
    if not set(ROOT_FILES).issubset(entry_paths):
        raise RuntimeError("The root refresh receipt does not cover every root file.")
    if (root / REMOVED_ROOT_AUTHORITY).exists():
        raise RuntimeError("The obsolete Task6 root authority still exists.")

    runtime_catalog = _json(
        plugin
        / "src"
        / "evidence_lane_plugin"
        / "runtime-public-catalog.v1.json"
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
        raise RuntimeError("The direct State Travel schema is not the safe six-field route.")
    if not str(plugin_manifest.get("version", "")).startswith("3.0.0+codex."):
        raise RuntimeError("The plugin manifest is not the current 3.0.0 cache identity.")
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
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "refresh_id": REFRESH_ID,
        "generated_at": datetime.now(UTC).isoformat(),
        "repository": "rathee000001/evidence_lane_plugin",
        "branch": _git(root, "branch", "--show-current").decode().strip(),
        "base_commit": _git(root, "rev-parse", "HEAD").decode().strip(),
        "audited_index_tree_before_receipt": _git(root, "write-tree").decode().strip(),
        "current_route": {
            "plugin_id": plugin_manifest["name"],
            "plugin_version": plugin_manifest["version"],
            "runtime_catalog": runtime_catalog,
            "hook_events": list(hooks),
            "direct_state_travel_fields": direct_fields,
            "github_app_commit_route": "github_app_exact_commit_push_v1",
            "github_app_commit_actor": "evidence-lane[bot]",
            "main_live_work_allowed": False,
        },
        "summary": {
            "path_count": len(entries),
            "dispositions": dict(sorted(Counter(row["disposition"] for row in entries).items())),
            "entry_set_sha256": _sha256_bytes(digest_payload),
            "root_file_count": len(ROOT_FILES),
            "removed_root_authority": REMOVED_ROOT_AUTHORITY,
        },
        "output_self_reference_law": (
            "The JSON and Markdown receipts are excluded from their own per-path digest; "
            "clean-checkout tests verify every other tracked path against these hashes."
        ),
        "entries": entries,
    }


def write_receipts(repository: Path, json_path: Path, markdown_path: Path) -> dict[str, Any]:
    receipt = build_receipt(repository)
    content = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    json_path.write_bytes(content.encode("utf-8"))
    digest = _sha256_bytes(content.encode("utf-8"))
    by_path = {row["path"]: row for row in receipt["entries"]}
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
        row = by_path[path]
        sha = row["sha256"] or f"REMOVED (prior `{row['prior_sha256']}`)"
        lines.append(f"| `{path}` | `{row['disposition']}` | `{sha}` |")
    lines.extend(
        [
            "",
            "The JSON authority contains one content-addressed record for every audited tracked path, including every unchanged-but-verified file.",
        ]
    )
    markdown_path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
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
    print(json.dumps({"status": receipt["status"], "summary": receipt["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
