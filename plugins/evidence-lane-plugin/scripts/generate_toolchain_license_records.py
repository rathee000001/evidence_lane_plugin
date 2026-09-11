#!/usr/bin/env python3
"""Regenerate every retained tool license record from current delivery owners."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
TOOLCHAINS = PLUGIN / "toolchains"


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component(entry: dict) -> str | None:
    if entry["external_configuration_required"]:
        return None
    if entry["tool_id"] == "Ghostscript":
        return "ghostscript-runtime"
    if entry["tool_id"] in {"PowerBI_TOM", "PBIXRay"}:
        return "powerbi-runtime"
    if entry["provisioning_route"] in {"shared_native_manifest", "shared_portable_git"}:
        return "native-tools"
    if entry["provisioning_route"] == "shared_node_runtime_and_locked_npm_assets":
        return "node-runtime"
    return "core-engine-studio"


def delivery(entry: dict) -> tuple[str, str, str]:
    route = entry["provisioning_route"]
    if entry["external_configuration_required"]:
        return (
            "EXTERNAL_SERVICE_CONFIGURATION",
            "packaged_adapter_external_service_not_redistributed",
            "plugin_adapter_and_explicit_external_service_configuration",
        )
    if route == "packaged_engine_code_or_standard_library":
        return (
            "PLUGIN_ENGINE_COMPONENT",
            "packaged_plugin_engine_code",
            "managed_plugin_and_studio_engine_source",
        )
    if route == "windows_host_api_with_platform_verification":
        return (
            "WINDOWS_HOST_CAPABILITY",
            "windows_host_capability_probe",
            "windows_host",
        )
    return (
        "STUDIO_BUNDLE_DISTRIBUTION",
        "shipped_with_evidence_lane_studio",
        "evidence_lane_studio_shared_toolchain",
    )


ADDED_LICENSES = {
    "LibreOffice": ("MPL-2.0 AND LGPL-3.0-or-later", ["toolchains/native-tools.v4.json"]),
    "PowerBI_TOM": ("LicenseRef-Microsoft-AnalysisServices", ["toolchains/native-tools.v4.json", "toolchains/powerbi-adapter/packages.lock.json"]),
    "PBIXRay": ("LicenseRef-Bundled-Python-and-Package-Licenses", ["toolchains/native-tools.v4.json", "toolchains/powerbi-pbix-runtime.v4.json"]),
    "JSONSchema": ("MIT", ["toolchains/tool-definitions.v4.json", "toolchains/locks/tools.lock.txt"]),
    "ReportLab": ("BSD-3-Clause", ["toolchains/tool-definitions.v4.json", "toolchains/locks/tools.lock.txt"]),
}


def outputs():
    shared_path = TOOLCHAINS / "shared-toolchain.v4.json"
    catalog_path = TOOLCHAINS / "tool-catalog.v4.json"
    shared = json.loads(shared_path.read_text(encoding="utf-8"))
    entries = {row["tool_id"]: row for row in shared["entries"]}
    records = {}
    for path in sorted((TOOLCHAINS / "licenses/requirements").glob("*/LICENSE-RECORD.json")):
        previous = json.loads(path.read_text(encoding="utf-8"))
        tool = previous.get("tool")
        if tool not in entries or tool in records:
            raise RuntimeError(f"License record does not resolve one retained tool: {path}")
        records[tool] = (path, previous)
    for ordinal, entry in enumerate(shared["entries"], 1):
        tool = entry["tool_id"]
        if tool in records:
            path, previous = records[tool]
        else:
            legal = ADDED_LICENSES.get(tool)
            if legal is None:
                raise RuntimeError("Every retained tool requires one current license record: " + tool)
            slug = "".join(character.lower() if character.isalnum() else "-" for character in tool).strip("-")
            path = TOOLCHAINS / "licenses/requirements" / ("current-" + slug) / "LICENSE-RECORD.json"
            previous = {
                "schema": "evidence-lane.tool-requirement-license-record.v4",
                "status": "PASS",
                "ordinal": ordinal,
                "tool": tool,
                "requirement": entry["base_requirement"],
                "license_expression_or_terms": legal[0],
                "evidence_paths": legal[1],
                "redistributed_by_source_package": False,
                "source_package_copied_license_text_present": False,
                "source_package_copied_license_texts": [],
                "native_license_acquisition": None,
                "metadata_without_a_license_file_is_labeled_metadata_only": True,
                "mcp_inventory_separate": True,
                "historical_ordinal": None,
                "catalog_ordinal": ordinal,
                "source_definition": "toolchains/tool-catalog.v4.json",
                "source_definition_sha256": sha(catalog_path),
            }
        classification, install_mode, owner = delivery(entry)
        local_distribution = classification == "STUDIO_BUNDLE_DISTRIBUTION"
        body = {
            **{key: value for key, value in previous.items() if key != "receipt_sha256"},
            "classification": classification,
            "install_mode": install_mode,
            "delivery_owner": owner,
            "provisioning_route": entry["provisioning_route"],
            "studio_bundle_component": component(entry),
            "studio_bundle_member": bool(entry["installation_required_for_windows_bundle"]),
            "external_configuration_required": bool(entry["external_configuration_required"]),
            "shared_once_across_projects": True,
            "tunnel_dependency": False,
            "installed_by_tunnel": False,
            "runtime_distribution_license_required": local_distribution,
            "runtime_distribution_license_manifest": (
                "toolchains/licenses/retained-install-license-index.v4.json"
                if local_distribution
                else None
            ),
            "runtime_distribution_license_files_must_be_copied_before_use": local_distribution,
            "license_evidence": (
                "Exact packaged license/source evidence is bound to the Studio release component; installed verification remains pending."
                if local_distribution
                else "The adapter is packaged, but the external service and its terms remain external and explicitly configured."
                if entry["external_configuration_required"]
                else "Evidence Lane-owned engine code or a standard-library/Windows host capability; no tunnel distribution role."
            ),
            "current_toolchain_entry_sha256": hashlib.sha256(canonical(entry)).hexdigest(),
            "shared_toolchain_sha256": sha(shared_path),
            "tool_catalog_sha256": sha(catalog_path),
        }
        sealed = {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}
        yield path, (json.dumps(sealed, indent=2, ensure_ascii=False) + "\n").encode()


def generate(*, check: bool = False) -> dict:
    changed = []
    count = 0
    for path, content in outputs():
        count += 1
        if path.is_file() and path.read_bytes() == content:
            continue
        changed.append(path.relative_to(PLUGIN).as_posix())
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if check and changed:
        raise RuntimeError("Toolchain license records require regeneration: " + ", ".join(changed))
    return {"status": "PASS", "record_count": count, "changed": changed, "check": check, "tunnel_dependency": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(generate(check=args.check)))


if __name__ == "__main__":
    main()
