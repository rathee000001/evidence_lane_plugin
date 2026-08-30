#!/usr/bin/env python3
"""Build an external, non-executable source-impact closure for current changes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.git_adapter import resolve_git_executable
from evidence_lane_plugin.graph_pipeline import SemanticGraph
from evidence_lane_plugin.hashing import (
    atomic_write_bytes,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)

SCHEMA = "evidence-lane.maintenance-source-impact-closure.v1"
EXCLUDED_CANDIDATE_PATHS = {
    "apps/evidence-lane-app/app/_data/studio-rag-index.json",
    "plugins/evidence-lane-plugin.zip",
}


def _git(repository: Path, *arguments: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        [resolve_git_executable(repository), *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=not binary,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.stdout


def _live_changed_paths(repository: Path) -> list[str]:
    tracked = {
        line.strip().replace("\\", "/")
        for line in str(
            _git(repository, "diff", "--no-renames", "--name-only", "HEAD")
        ).splitlines()
        if line.strip()
    }
    raw = bytes(
        _git(
            repository,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            binary=True,
        )
    )
    untracked = {
        row.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for row in raw.split(b"\0")
        if row
    }
    return sorted((tracked | untracked) - EXCLUDED_CANDIDATE_PATHS)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _member_groups(repository: Path, plugin_root: Path) -> dict[str, list[str]]:
    executable = _load(plugin_root / "manifests/executable-surface-registry.v1.json")
    package_members = [
        f"plugins/evidence-lane-plugin/{row['path']}" for row in executable["members"]
    ]
    tracked = {
        line.strip().replace("\\", "/")
        for line in str(_git(repository, "ls-files")).splitlines()
        if line.strip()
    }

    def package_where(predicate: Any) -> list[str]:
        return sorted(path for path in package_members if predicate(path))

    return {
        "AUTHORITY_SECTOR_SQLITE": package_where(
            lambda path: (
                path.endswith(".sqlite")
                and any(part in path for part in ("/authorities/", "/env/", "/uop/"))
            )
        ),
        "AUTHORITY_SECTOR_GRAPHS": package_where(
            lambda path: path.endswith((".mmd", ".dot"))
        ),
        "SCHEMAS": package_where(
            lambda path: (
                "/schemas/" in path or path.endswith(("schema.sql", ".schema.json"))
            )
        ),
        "SDK_MCP_BINDINGS": package_where(
            lambda path: (
                "/sdk/" in path or "/mcp/" in path or path.endswith("/.mcp.json")
            )
        ),
        "SKILLS": package_where(lambda path: "/skills/" in path),
        "HOOKS": package_where(lambda path: "/hooks/" in path),
        "MANIFESTS_AND_MAPS": package_where(
            lambda path: (
                "/manifests/" in path
                or "manifest" in Path(path).name.casefold()
                or Path(path).name
                in {
                    "module-registry.v1.json",
                    "runtime-public-catalog.v1.json",
                    "tool-execution-routing.v1.json",
                    "tool-requirement-matrix.v1.json",
                }
            )
        ),
        "LOCKS_AND_PROVENANCE": package_where(
            lambda path: (
                path.endswith((".lock.txt", "THIRD_PARTY_NOTICES.md"))
                or "/toolchains/licenses/" in path
            )
        ),
        "TESTS": sorted(
            path
            for path in tracked
            if path.startswith(("tests/", "plugins/evidence-lane-plugin/tests/"))
        ),
        "SOURCE_DERIVED_DOCUMENTS": sorted(
            path
            for path in tracked
            if path == "README.md"
            or path.startswith("docs/")
            or path == "plugins/evidence-lane-plugin/README.md"
            or path.startswith("plugins/evidence-lane-plugin/toolchains/")
            and path.endswith(".md")
        ),
    }


def _affected_groups(path: str) -> list[str]:
    plugin_source = path.startswith("plugins/evidence-lane-plugin/src/")
    generator_or_contract = path.startswith(
        "plugins/evidence-lane-plugin/scripts/"
    ) or path in {
        "requirements.in",
        "requirements.toolchain.in",
        "requirements.torch-cpu.in",
        "requirements.torch-nvidia.in",
        "requirements.onnx-directml.in",
        "plugins/evidence-lane-plugin/pyproject.toml",
    }
    if plugin_source or generator_or_contract or path.endswith(".lock.txt"):
        return [
            "AUTHORITY_SECTOR_SQLITE",
            "AUTHORITY_SECTOR_GRAPHS",
            "SCHEMAS",
            "SDK_MCP_BINDINGS",
            "SKILLS",
            "HOOKS",
            "MANIFESTS_AND_MAPS",
            "LOCKS_AND_PROVENANCE",
            "TESTS",
            "SOURCE_DERIVED_DOCUMENTS",
        ]
    if path.startswith("plugins/evidence-lane-plugin/authorities/"):
        return [
            "AUTHORITY_SECTOR_SQLITE",
            "AUTHORITY_SECTOR_GRAPHS",
            "SCHEMAS",
            "MANIFESTS_AND_MAPS",
            "TESTS",
            "SOURCE_DERIVED_DOCUMENTS",
        ]
    if "/sdk/" in path or "/mcp/" in path or "/schemas/" in path:
        return ["SCHEMAS", "SDK_MCP_BINDINGS", "MANIFESTS_AND_MAPS", "TESTS"]
    if "/skills/" in path:
        return ["SKILLS", "SDK_MCP_BINDINGS", "MANIFESTS_AND_MAPS", "TESTS"]
    if "/hooks/" in path:
        return ["HOOKS", "SDK_MCP_BINDINGS", "MANIFESTS_AND_MAPS", "TESTS"]
    if path.startswith(("README.md", "docs/", "github-pages/")) or path.endswith(".md"):
        return ["SOURCE_DERIVED_DOCUMENTS", "TESTS"]
    if path.startswith("apps/"):
        return [
            "SDK_MCP_BINDINGS",
            "MANIFESTS_AND_MAPS",
            "SOURCE_DERIVED_DOCUMENTS",
            "TESTS",
        ]
    return ["MANIFESTS_AND_MAPS", "TESTS"]


def _orphaned_generated_members(plugin_root: Path) -> list[str]:
    public = _load(plugin_root / "schemas/public-action-schemas.v001.json")
    action_names = {str(row["name"]) for row in public["tools"]}
    expected = {
        *(f"schemas/actions/{name}.schema.json" for name in action_names),
        *(f"sdk/actions/{name}.action.v1.json" for name in action_names),
        *(f"mcp/actions/{name}.binding.v1.json" for name in action_names),
    }
    actual = {
        path.relative_to(plugin_root).as_posix()
        for root, pattern in (
            (plugin_root / "schemas/actions", "*.schema.json"),
            (plugin_root / "sdk/actions", "*.action.v1.json"),
            (plugin_root / "mcp/actions", "*.binding.v1.json"),
        )
        for path in root.glob(pattern)
    }
    skill_names = {
        path.parent.name for path in (plugin_root / "skills").glob("*/SKILL.md")
    }
    actual_skill_directories = {
        path.name
        for path in (plugin_root / "sdk/workflows/skills").iterdir()
        if path.is_dir()
    }
    orphaned = sorted(actual - expected)
    orphaned.extend(
        f"sdk/workflows/skills/{name}"
        for name in sorted(actual_skill_directories - skill_names)
    )
    commands = plugin_root / "commands"
    if commands.exists():
        orphaned.append("commands/")
    return sorted(orphaned)


def _purge_receipts(repository: Path) -> list[dict[str, Any]]:
    replacements = (
        ("SDK_" + "NATIVE_ACTIONS", "SPECIALIZED_NATIVE_ACTIONS"),
        ("SDK_" + "NATIVE_READ_TOOL_NAMES", "SPECIALIZED_NATIVE_READ_ACTION_NAMES"),
        ("25 " + "current skills", "26 current skills"),
        ("ai_toolchain_sync_receipt_" + "v16", "env_action_plane_build_receipt"),
        (
            "semantic_graph_render_receipt_" + "v16",
            "semantic_graph_render_receipt_v17",
        ),
    )
    tracked = {
        line.strip().replace("\\", "/")
        for line in str(_git(repository, "ls-files")).splitlines()
        if line.strip()
    }
    untracked_raw = bytes(
        _git(
            repository,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            binary=True,
        )
    )
    untracked = {
        row.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for row in untracked_raw.split(b"\0")
        if row
    }
    searchable = [
        repository / relative
        for relative in sorted((tracked | untracked) - EXCLUDED_CANDIDATE_PATHS)
        if (repository / relative).is_file()
        and "_evidence_lane_rehearsal" not in Path(relative).parts
        and Path(relative).suffix.casefold()
        in {".py", ".md", ".json", ".toml", ".yml", ".yaml"}
        and Path(relative).name != "studio-rag-index.json"
    ]
    receipts = []
    for removed, replacement in replacements:
        hits = []
        for path in searchable:
            try:
                if removed in path.read_text(encoding="utf-8"):
                    hits.append(path.relative_to(repository).as_posix())
            except UnicodeError:
                continue
        receipts.append(
            {
                "removed_identity": removed,
                "replacement_identity": replacement,
                "remaining_reference_count": len(hits),
                "remaining_references": hits,
                "direct_purge": True,
                "compatibility_alias_retained": False,
                "status": "PASS" if not hits else "FAIL",
            }
        )
    return receipts


def build_source_impact_closure(
    *,
    repository_root: Path,
    plugin_root: Path,
    output_root: Path,
    changed_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    repository = repository_root.resolve()
    plugin = plugin_root.resolve()
    output = output_root.resolve()
    if output.exists():
        raise RuntimeError("SOURCE_IMPACT_OUTPUT_MUST_BE_FRESH")
    paths = sorted(
        dict.fromkeys(
            str(path).strip().replace("\\", "/")
            for path in (
                changed_paths
                if changed_paths is not None
                else _live_changed_paths(repository)
            )
            if str(path).strip()
        )
    )
    groups = _member_groups(repository, plugin)
    mappings = [
        {
            "source_path": path,
            "source_exists": (repository / path).is_file(),
            "source_sha256": sha256_file(repository / path)
            if (repository / path).is_file()
            else None,
            "affected_group_ids": _affected_groups(path),
            "affected_member_count": sum(
                len(groups[group]) for group in _affected_groups(path)
            ),
        }
        for path in paths
    ]
    orphaned = _orphaned_generated_members(plugin)
    purges = _purge_receipts(repository)
    core = {
        "schema": SCHEMA,
        "status": "PASS"
        if not orphaned
        and all(row["affected_group_ids"] for row in mappings)
        and all(row["status"] == "PASS" for row in purges)
        else "FAIL",
        "repository_root": repository.as_posix(),
        "plugin_root": plugin.as_posix(),
        "external_non_executable_receipt": True,
        "changed_path_count": len(paths),
        "changed_paths": paths,
        "impact_group_count": len(groups),
        "impact_groups": [
            {"group_id": group, "member_count": len(members), "members": members}
            for group, members in sorted(groups.items())
        ],
        "source_mappings": mappings,
        "all_changed_paths_mapped": all(row["affected_group_ids"] for row in mappings),
        "orphaned_generated_members": orphaned,
        "orphaned_generated_member_count": len(orphaned),
        "purge_receipts": purges,
        "all_replacements_directly_purged": all(
            row["status"] == "PASS" for row in purges
        ),
        "immutable_historical_evidence_is_non_executable": True,
        "project_or_pv_registered": False,
    }
    receipt = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    graph = SemanticGraph(
        "maintenance_source_impact", direction="TB", role="EVIDENCE_MAP"
    )
    graph.add_node("CHANGES", f"Changed source paths: {len(paths)}", "root")
    graph.add_node("VALIDATE", "Orphan + purge + hash validation", "warn")
    graph.add_node("RECEIPT", "External non-executable impact receipt", "output")
    for group, members in sorted(groups.items()):
        graph.add_node(f"GROUP_{group}", f"{group}: {len(members)} members", "semantic")
        graph.add_edge(f"GROUP_{group}", "VALIDATE")
    for index, row in enumerate(mappings, 1):
        node = f"SOURCE_{index:04d}"
        graph.add_node(node, row["source_path"], "source")
        graph.add_edge("CHANGES", node)
        for group in row["affected_group_ids"]:
            graph.add_edge(node, f"GROUP_{group}", "affects", conditional=True)
    graph.add_edge("VALIDATE", "RECEIPT")
    mmd, dot, graph_receipt = graph.render_pair()
    receipt["graph_receipt"] = graph_receipt
    receipt["receipt_sha256"] = sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
    )
    output.mkdir(parents=True)
    atomic_write_bytes(
        output / "source-impact-closure.v1.json", canonical_json_bytes(receipt) + b"\n"
    )
    atomic_write_bytes(output / "source-impact-closure.mmd", mmd.encode("utf-8"))
    atomic_write_bytes(output / "source-impact-closure.dot", dot.encode("utf-8"))
    return {**receipt, "output_root": output.as_posix()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build_source_impact_closure(
        repository_root=args.repository_root,
        plugin_root=args.plugin_root,
        output_root=args.output_root,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
