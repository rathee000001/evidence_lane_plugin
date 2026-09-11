#!/usr/bin/env python3
"""Generate the human-readable v4 toolchain execution matrix from current contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPOSITORY = PLUGIN.parents[1]
SOURCES = {
    "operation routes": PLUGIN / "toolchains/operation-toolchains.v4.json",
    "shared installation": PLUGIN / "toolchains/shared-toolchain.v4.json",
    "license index": PLUGIN / "toolchains/licenses/retained-install-license-index.v4.json",
    "first detection": PLUGIN / "provisioning/full-bundle-plan.v4.json",
}
OUTPUTS = (
    PLUGIN / "toolchains/TOOLCHAIN_EXECUTION_MATRIX.md",
    REPOSITORY / "docs/TOOLCHAIN_EXECUTION_MATRIX.md",
)


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected one JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cell(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value) or "—"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def code_list(values: list[object]) -> str:
    return ", ".join(f"`{cell(value)}`" for value in values) if values else "—"


def render() -> bytes:
    operations = read(SOURCES["operation routes"])
    shared = read(SOURCES["shared installation"])
    licenses = read(SOURCES["license index"])
    plan = read(SOURCES["first detection"])
    operation_rows = operations.get("operations")
    tool_rows = shared.get("entries")
    license_rows = licenses.get("records")
    if (
        not isinstance(operation_rows, list)
        or len(operation_rows) != operations.get("operation_count")
        or len(operation_rows) != 297
        or not isinstance(tool_rows, list)
        or len(tool_rows) != shared.get("retained_tool_count")
        or len(tool_rows) != 103
        or not isinstance(license_rows, list)
        or len(license_rows) != 103
    ):
        raise RuntimeError("The current operation, tool and license matrices do not reconcile.")
    if len({row["operation"] for row in operation_rows}) != len(operation_rows):
        raise RuntimeError("Operation identifiers must be unique.")
    if len({row["tool_id"] for row in tool_rows}) != len(tool_rows):
        raise RuntimeError("Tool identifiers must be unique.")
    license_by_tool = {row["tool_id"]: row for row in license_rows}
    if set(license_by_tool) != {row["tool_id"] for row in tool_rows}:
        raise RuntimeError("Every retained tool requires exactly one current license record.")

    lines = [
        "# Evidence Lane v4 toolchain execution matrix",
        "",
        (
            "This document is generated from the current executable registry and installation contracts. "
            "It is a readable projection for GitHub and local inspection; the JSON files listed below remain authoritative."
        ),
        "",
        f"- Registered operations: **{len(operation_rows)}**",
        f"- Retained tools and services: **{len(tool_rows)}**",
        f"- First-detection components: **{len(plan['components'])}**",
        f"- Provider environments: **{len(shared['provider_environments'])}**",
        f"- Windows shared root: `{shared['shared_root']}`",
        f"- Current qualification: `{shared['qualification']}`",
        f"- Full installed bundle verified: **{'yes' if shared['full_bundle_ready'] else 'no — installed qualification is pending'}**",
        "",
        (
            "Tools are selected by the registered operation and route before invocation. Catalog presence never means every tool runs, "
            "and a failed invoked adapter does not silently switch to another adapter. Installation membership and execution proof are separate."
        ),
        "",
        "## Source contracts",
        "",
        "| Role | Path | SHA-256 |",
        "|---|---|---|",
    ]
    for role, path in SOURCES.items():
        lines.append(
            f"| {cell(role)} | `{path.relative_to(REPOSITORY).as_posix()}` | `{sha256(path)}` |"
        )

    lines.extend(
        [
            "",
            "## First-detection component ownership",
            "",
            "| Component | Condition | Purpose | Tool count | Shared across projects |",
            "|---|---|---|---:|---|",
        ]
    )
    for row in plan["components"]:
        lines.append(
            f"| `{cell(row['component_id'])}` | `{cell(row['condition'])}` | {cell(row['purpose'])} | "
            f"{len(row.get('tool_ids', []))} | {cell(row.get('shared_once_across_projects'))} |"
        )

    lines.extend(
        [
            "",
            "## Provider environments",
            "",
            "| Provider | Python | Operation coverage | Install state | Hardware execution | Qualification requirement |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in shared["provider_environments"]:
        lines.append(
            f"| `{cell(row['runtime_id'])}` | `{cell(row['python_version'])}` | {code_list(row['operations'])} | "
            f"`{cell(row['installation_state'])}` | `{cell(row['hardware_execution'])}` | {cell(row['qualification_requires'])} |"
        )

    lines.extend(
        [
            "",
            "## Native executables and fixed launch inputs",
            "",
            "| Tool | Version | Component | Installed executable paths |",
            "|---|---|---|---|",
        ]
    )
    for row in plan["native_inputs"]:
        lines.append(
            f"| `{cell(row['tool_id'])}` | `{cell(row['version'])}` | `{cell(row['release_component'])}` | "
            f"{code_list(row['installed_executables'])} |"
        )

    lines.extend(
        [
            "",
            "## Retained tool installation and license matrix",
            "",
            "| Tool | Kind | Base requirement | Provisioning route | Bundle component | Windows bundle | External configuration | License gate | Install state | Adapter execution | License or terms |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in tool_rows:
        license_row = license_by_tool[row["tool_id"]]
        lines.append(
            f"| `{cell(row['tool_id'])}` | `{cell(row['kind'])}` | `{cell(row['base_requirement'])}` | "
            f"`{cell(row['provisioning_route'])}` | `{cell(license_row.get('studio_bundle_component'))}` | "
            f"{cell(row['installation_required_for_windows_bundle'])} | {cell(row['external_configuration_required'])} | "
            f"{cell(row['license_grant_required'])} | `{cell(row['installation_state'])}` | "
            f"`{cell(row['adapter_execution_state'])}` | {cell(license_row['license_expression_or_terms'])} |"
        )

    profiles = Counter(str(row["profile"]) for row in operation_rows)
    lines.extend(
        [
            "",
            "## Operation coverage by profile",
            "",
            "| Profile | Operation count |",
            "|---|---:|",
        ]
    )
    for profile, count in sorted(profiles.items()):
        lines.append(f"| `{cell(profile)}` | {count} |")

    lines.extend(
        [
            "",
            "## Registered operation execution matrix",
            "",
            "| Operation | Profile | Route IDs | Providers | Ordered route tools | Systems | Host profiles | Worker operations | Fallback after invocation | Installation implied |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in operation_rows:
        routes = row["routes"]
        route_ids = [route["route_id"] for route in routes]
        providers = list(dict.fromkeys(route["provider"] for route in routes))
        systems = list(dict.fromkeys(system for route in routes for system in route["systems"]))
        hosts = list(
            dict.fromkeys(host for route in routes for host in route.get("host_profiles", []))
        )
        route_tools = [
            route["route_id"] + ": " + (" → ".join(route["tool_ids"]) or "none")
            for route in routes
        ]
        lines.append(
            f"| `{cell(row['operation'])}` | `{cell(row['profile'])}` | {code_list(route_ids)} | "
            f"{code_list(providers)} | {cell('<br>'.join(route_tools))} | {code_list(systems)} | "
            f"{code_list(hosts)} | {code_list(row['worker_operations'])} | "
            f"{cell(row['fallback_after_invocation'])} | {cell(row['installation_implied'])} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "- `installation_state: not_verified` and `adapter_execution_state: not_verified` are truthful preinstall states.",
            "- CPU is always selected for its declared provider operation. CUDA, ROCm and DirectML require their exact compatible host probe and installed self-test.",
            "- External services ship as adapters and require explicit configuration; their services and credentials are not redistributed.",
            "- Ghostscript remains separately license-gated and is not selected by default.",
            "- The managed plugin source, Studio application, persistent engine and shared toolchains have separate owners and install locations.",
            "- Installed-native proof is recorded only after D088 installs and reads back the exact committed candidate.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def generate(*, check: bool = False) -> dict[str, object]:
    content = render()
    changed = []
    for path in OUTPUTS:
        if path.is_file() and path.read_bytes() == content:
            continue
        changed.append(path.relative_to(REPOSITORY).as_posix())
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if check and changed:
        raise RuntimeError("Toolchain execution matrix requires regeneration: " + ", ".join(changed))
    return {
        "status": "PASS",
        "operation_count": 297,
        "tool_count": 103,
        "outputs": [path.relative_to(REPOSITORY).as_posix() for path in OUTPUTS],
        "sha256": hashlib.sha256(content).hexdigest(),
        "changed": changed,
        "check": check,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(generate(check=args.check)))


if __name__ == "__main__":
    main()
