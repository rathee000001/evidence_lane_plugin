#!/usr/bin/env python3
"""Seal the bounded pre-regression executable-lane harness."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file

SCHEMA = "evidence-lane.executable-lane-harness-receipt.v1"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git(repository: Path, *arguments: str, index: Path | None = None) -> str:
    environment = os.environ.copy()
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout.strip()


def junit_counts(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        "tests": sum(int(row.attrib.get("tests", "0")) for row in suites),
        "failures": sum(int(row.attrib.get("failures", "0")) for row in suites),
        "errors": sum(int(row.attrib.get("errors", "0")) for row in suites),
        "skipped": sum(int(row.attrib.get("skipped", "0")) for row in suites),
        "duration_seconds": round(
            sum(float(row.attrib.get("time", "0")) for row in suites), 3
        ),
        "suite_count": len(suites),
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--targeted-junit", type=Path, required=True)
    parser.add_argument("--alternate-index", type=Path, required=True)
    parser.add_argument("--initial-alternate-tree", required=True)
    parser.add_argument("--alternate-tree", required=True)
    parser.add_argument("--tool-parity", type=Path, required=True)
    parser.add_argument("--contract-parity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repository = args.repository_root.resolve()
    plugin = args.plugin_root.resolve()
    manifest = load(args.manifest.resolve())
    tool = load(args.tool_parity.resolve())
    contract = load(args.contract_parity.resolve())
    executable = load(plugin / "manifests/executable-surface-registry.v1.json")
    cross = load(plugin / "sdk/env_uop/cross-plane-contract.v1.json")
    counts = junit_counts(args.junit.resolve())
    targeted_counts = junit_counts(args.targeted_junit.resolve())
    alternate_tree = git(repository, "write-tree", index=args.alternate_index.resolve())
    alternate_worktree_diff = git(
        repository, "diff", "--name-only", index=args.alternate_index.resolve()
    )
    real_index_diff = git(repository, "diff", "--cached", "--name-only")
    targeted_changed_paths = git(
        repository,
        "diff",
        "--name-only",
        args.initial_alternate_tree,
        args.alternate_tree,
    ).splitlines()
    head = git(repository, "rev-parse", "HEAD")
    selectors = [
        selector
        for category in manifest["categories"]
        for selector in category["selectors"]
    ]
    coverage = dict(manifest["coverage_requirements"])
    checks = {
        "manifest_ready": manifest.get("status") == "READY",
        "selector_categories_complete": len(manifest["categories"]) == 6
        and len(selectors) == len(set(selectors))
        and len(selectors) >= 30,
        "junit_and_targeted_closure": counts["tests"] > 0
        and counts["skipped"] == 0
        and counts["failures"] + counts["errors"] == targeted_counts["tests"]
        and targeted_counts["tests"] > 0
        and targeted_counts["failures"] == 0
        and targeted_counts["errors"] == 0
        and targeted_counts["skipped"] == 0,
        "alternate_tree_exact": alternate_tree == args.alternate_tree,
        "alternate_index_matches_worktree": alternate_worktree_diff == "",
        "real_git_index_unchanged": real_index_diff == "",
        "tool_parity": tool.get("status") == "PASS"
        and tool.get("tool_count") == coverage["tool_requirements"]
        and tool.get("lane_count") == coverage["sector_lanes"]
        and tool.get("authority_count") == coverage["named_root_authorities"],
        "contract_parity": contract.get("status") == "PASS"
        and contract.get("action_count") == coverage["actions"]
        and contract.get("skill_count") == coverage["skills"]
        and contract.get("hook_event_count") == coverage["hook_events"]
        and contract.get("hook_handler_count") == coverage["hook_handlers"],
        "cross_plane": cross.get("status") == "PASS"
        and cross.get("action_count") == coverage["actions"]
        and cross.get("behavior_counts", {}).get("env_nodes")
        == coverage["env_behavior_nodes"]
        and cross.get("behavior_counts", {}).get("uop_nodes")
        == coverage["uop_behavior_nodes"],
        "executable_surface": executable.get("status") == "PASS"
        and executable.get("all_installed_members_hash_bound") is True
        and executable.get("local_cache_or_output_included") is False
        and executable.get("historical_fallback_used") is False,
    }
    core = {
        "schema": SCHEMA,
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "repository_head": head,
        "initial_alternate_tree": args.initial_alternate_tree,
        "alternate_tree": alternate_tree,
        "alternate_index_path_disclosed": False,
        "manifest_sha256": sha256_file(args.manifest.resolve()),
        "junit_sha256": sha256_file(args.junit.resolve()),
        "junit": counts,
        "targeted_junit_sha256": sha256_file(args.targeted_junit.resolve()),
        "targeted_junit": targeted_counts,
        "targeted_failure_closure": {
            "full_harness_rerun": False,
            "initial_failure_count": counts["failures"] + counts["errors"],
            "targeted_pass_count": targeted_counts["tests"],
            "changed_paths": targeted_changed_paths,
        },
        "selector_count": len(selectors),
        "selectors": selectors,
        "categories": manifest["categories"],
        "coverage_requirements": coverage,
        "checks": checks,
        "evidence": {
            "tool_parity_file_sha256": sha256_file(args.tool_parity.resolve()),
            "tool_parity_receipt_sha256": tool["receipt_sha256"],
            "contract_parity_file_sha256": sha256_file(args.contract_parity.resolve()),
            "contract_parity_receipt_sha256": contract["receipt_sha256"],
            "cross_plane_file_sha256": sha256_file(
                plugin / "sdk/env_uop/cross-plane-contract.v1.json"
            ),
            "executable_surface_file_sha256": sha256_file(
                plugin / "manifests/executable-surface-registry.v1.json"
            ),
            "executable_member_count": executable["member_count"],
        },
        "negative_proofs": {
            "full_system_regression_run": False,
            "real_git_index_mutated": False,
            "project_or_pv_mutated": False,
            "accepted_zip_queried": False,
            "candidate_created": False,
            "pointer_moved": False,
            "network_required": False,
            "credential_value_read": False,
        },
    }
    result = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    atomic_json(args.output.resolve(), result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "tests": counts["tests"],
                "duration_seconds": counts["duration_seconds"],
                "alternate_tree": alternate_tree,
                "receipt_sha256": result["receipt_sha256"],
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
