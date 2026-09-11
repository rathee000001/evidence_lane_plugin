"""Compile the complete v4 first-detection installation plan without installing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

PLUGIN = Path(__file__).resolve().parents[1]
TOOLCHAINS = PLUGIN / "toolchains"
PROVISIONING = PLUGIN / "provisioning"
SHA256_LENGTH = 64
REMOVED_TOOL_IDS = (
    "Package_sealer",
    "OpenJDK",
    "Jackcess",
    "OneNote_Parser",
    "RapidFuzz",
    "Promptfoo",
    "TruLens",
    "DeepEval",
    "Helicone",
    "Docker",
    "Kubernetes",
    "AWS_Lambda",
    "Google_Cloud_Run",
    "AWS",
    "Azure",
    "Google_Cloud",
    "Vercel_Git_integration",
    "GitHub_Actions",
    "GitHub_MCP_Server",
    "Filesystem_MCP_Server",
    "PostgreSQL_MCP_Server",
    "Slack_MCP_Server",
    "psutil",
)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sealed(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}


def document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected an object: {path}")
    return value


def lock_packages(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(
        r"(?=^[A-Za-z0-9_.-]+==)",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    rows: list[dict[str, Any]] = []
    for block in blocks:
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)", block)
        if match is None:
            continue
        hashes = sorted(
            set(re.findall(r"--hash=sha256:([a-f0-9]{64})", block))
        )
        if not hashes:
            raise RuntimeError(f"A resolved runtime package has no hashes: {match[1]}")
        rows.append(
            {
                "name": match[1],
                "version": match[2],
                "hashes": hashes,
            }
        )
    if not rows or len({row["name"].casefold() for row in rows}) != len(rows):
        raise RuntimeError("The resolved runtime lock has duplicate or missing packages")
    return rows


ADDED_LICENSES: dict[str, dict[str, Any]] = {
    "LibreOffice": {
        "license_expression_or_terms": "MPL-2.0 AND LGPL-3.0-or-later",
        "evidence_paths": ["toolchains/native-tools.v4.json"],
        "redistributed_by_release_asset": True,
    },
    "PowerBI_TOM": {
        "license_expression_or_terms": "LicenseRef-Microsoft-AnalysisServices",
        "evidence_paths": [
            "toolchains/native-tools.v4.json",
            "toolchains/powerbi-adapter/packages.lock.json",
        ],
        "redistributed_by_release_asset": True,
    },
    "PBIXRay": {
        "license_expression_or_terms": (
            "LicenseRef-Bundled-Python-and-Package-Licenses"
        ),
        "evidence_paths": [
            "toolchains/native-tools.v4.json",
            "toolchains/powerbi-pbix-runtime.v4.json",
        ],
        "redistributed_by_release_asset": True,
    },
    "JSONSchema": {
        "license_expression_or_terms": "MIT",
        "evidence_paths": [
            "toolchains/tool-definitions.v4.json",
            "toolchains/locks/tools.lock.txt",
        ],
        "redistributed_by_release_asset": True,
    },
    "ReportLab": {
        "license_expression_or_terms": "BSD-3-Clause",
        "evidence_paths": [
            "toolchains/tool-definitions.v4.json",
            "toolchains/locks/tools.lock.txt",
        ],
        "redistributed_by_release_asset": True,
    },
}


def retained_license_index(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    old_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    license_root = TOOLCHAINS / "licenses/requirements"
    for path in sorted(license_root.glob("*/LICENSE-RECORD.json")):
        value = document(path)
        if (
            value.get("schema") != "evidence-lane.tool-requirement-license-record.v4"
            or value.get("status") != "PASS"
            or not isinstance(value.get("tool"), str)
        ):
            raise RuntimeError(f"Invalid inherited license evidence: {path}")
        record_body = {key: item for key, item in value.items() if key != "receipt_sha256"}
        if value.get("receipt_sha256") != hashlib.sha256(canonical(record_body)).hexdigest():
            raise RuntimeError(f"Unsealed inherited license evidence: {path}")
        if value["tool"] in old_records:
            raise RuntimeError(f"Duplicate inherited license evidence: {value['tool']}")
        old_records[value["tool"]] = (path, value)
    retained_ids = {entry["tool_id"] for entry in entries}
    unused_records = sorted(set(old_records) - retained_ids)
    if unused_records:
        raise RuntimeError(
            "Retired inherited license evidence remains: " + ", ".join(unused_records)
        )
    rows: list[dict[str, Any]] = []
    for entry in entries:
        tool_id = entry["tool_id"]
        if tool_id in old_records:
            path, value = old_records[tool_id]
            rows.append(
                {
                    "tool_id": tool_id,
                    "license_expression_or_terms": value[
                        "license_expression_or_terms"
                    ],
                    "evidence_paths": [path.relative_to(PLUGIN).as_posix()],
                    "evidence_sha256": sha256(path),
                    "delivery_owner": value["delivery_owner"],
                    "install_mode": value["install_mode"],
                    "provisioning_route": value["provisioning_route"],
                    "studio_bundle_component": value["studio_bundle_component"],
                    "studio_bundle_member": value["studio_bundle_member"],
                    "tunnel_dependency": value["tunnel_dependency"],
                    "redistributed_by_release_asset": bool(
                        entry["installation_required_for_windows_bundle"]
                        and not entry["external_configuration_required"]
                    ),
                    "configuration_only": bool(
                        entry["external_configuration_required"]
                    ),
                    "license_grant_required": bool(
                        entry["license_grant_required"]
                    ),
                }
            )
            continue
        added = ADDED_LICENSES.get(tool_id)
        if added is None:
            raise RuntimeError(f"No license evidence for retained tool: {tool_id}")
        paths = [PLUGIN / path for path in added["evidence_paths"]]
        if not all(path.is_file() for path in paths):
            raise RuntimeError(f"Missing added license evidence for {tool_id}")
        rows.append(
            {
                "tool_id": tool_id,
                "license_expression_or_terms": added[
                    "license_expression_or_terms"
                ],
                "evidence_paths": added["evidence_paths"],
                "evidence_sha256": hashlib.sha256(
                    canonical(
                        [
                            {
                                "path": path.relative_to(PLUGIN).as_posix(),
                                "sha256": sha256(path),
                            }
                            for path in paths
                        ]
                    )
                ).hexdigest(),
                "redistributed_by_release_asset": added[
                    "redistributed_by_release_asset"
                ],
                "configuration_only": False,
                "license_grant_required": bool(
                    entry["license_grant_required"]
                ),
            }
        )
    if len(rows) != len(entries) or len({row["tool_id"] for row in rows}) != len(rows):
        raise RuntimeError("Retained license evidence does not reconcile")
    body = {
        "schema": "evidence-lane.retained-install-license-index.v4",
        "status": "COMPLETE_PREINSTALL_SOURCE_EVIDENCE",
        "retained_tool_count": len(entries),
        "removed_office_tools_absent": not {
            "OpenJDK",
            "Jackcess",
            "OneNote_Parser",
        }.intersection(row["tool_id"] for row in rows),
        "records": rows,
        "installed_license_receipt_required": True,
        "external_service_terms_remain_external": True,
        "studio_bundle_owns_retained_local_toolchain": True,
        "tunnel_dependency": False,
    }
    return sealed(body)


def primary_component(entry: dict[str, Any]) -> str:
    tool_id = entry["tool_id"]
    route = entry["provisioning_route"]
    if tool_id == "Ghostscript":
        return "ghostscript-runtime"
    if tool_id in {"PowerBI_TOM", "PBIXRay"}:
        return "powerbi-runtime"
    if route in {"shared_native_manifest", "shared_portable_git"}:
        return "native-tools"
    if route == "shared_node_runtime_and_locked_npm_assets":
        return "node-runtime"
    return "core-engine-studio"


def native_installed_executables(tool: dict[str, Any]) -> list[str]:
    tool_id = tool["tool_id"]
    if tool_id == "ffmpeg":
        return ["ffmpeg/ffmpeg.exe"]
    if tool_id == "powerbi_tom":
        return ["powerbi_tom/evidence-lane-powerbi.exe"]
    if tool_id == "powerbi_pbix":
        return ["powerbi_pbix/python.exe"]
    values = tool.get("installed_executables")
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"Missing native executable paths for {tool_id}")
    return values


def components(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assigned: dict[str, list[str]] = {}
    for entry in entries:
        assigned.setdefault(primary_component(entry), []).append(entry["tool_id"])
    definitions = [
        (
            "core-engine-studio",
            "always",
            "Engine Python, locked Python libraries, immutable plugin copy, Studio and service launchers",
        ),
        (
            "native-tools",
            "always",
            "Portable Git and retained native command tools with copied license/source records",
        ),
        (
            "node-runtime",
            "always",
            "Pinned Node runtime and locked JavaScript tool assets",
        ),
        (
            "model-assets",
            "always",
            "Pinned embedding, grammar, OCR, Tesseract and Docling model bytes",
        ),
        (
            "powerbi-runtime",
            "always",
            "Pinned TOM and PBIXRay helper runtimes with source and license inventories",
        ),
        (
            "provider-cpu",
            "always",
            "CPU provider environment and self-test inputs",
        ),
        (
            "provider-cuda",
            "nvidia_cuda_compatible",
            "CUDA provider environment selected only after measured compatible NVIDIA hardware",
        ),
        (
            "provider-directml",
            "directml_compatible",
            "DirectML OCR environment selected only for a measured compatible Windows DirectX 12 device",
        ),
        (
            "provider-rocm",
            "amd_rocm_compatible",
            "ROCm environment selected only after its exact Windows AMD compatibility contract",
        ),
        (
            "ghostscript-runtime",
            "explicit_ghostscript_license",
            "Optional Ghostscript runtime selected only with an explicit accepted license reference",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for component_id, condition, purpose in definitions:
        rows.append(
            {
                "component_id": component_id,
                "condition": condition,
                "release_asset_required": True,
                "maximum_part_bytes": 2_100_000_000,
                "purpose": purpose,
                "tool_ids": sorted(assigned.get(component_id, [])),
                "shared_once_across_projects": True,
            }
        )
    if sorted(tool for row in rows for tool in row["tool_ids"]) != sorted(
        entry["tool_id"] for entry in entries
    ):
        raise RuntimeError("Every retained tool needs exactly one primary component")
    return rows


def materialization_steps(
    provider_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [
        {
            "operation": "create_venv",
            "component_id": "core-engine-studio",
            "interpreter": "toolchains/python/base/cp314/python.exe",
            "target": "engine/venv",
        },
        {
            "operation": "pip_install",
            "component_id": "core-engine-studio",
            "environment": "engine/venv",
            "wheelhouse": "install-inputs/engine/wheelhouse",
            "requirements": [
                "install-inputs/engine/core.offline.lock.txt",
            ],
            "license_index": "install-inputs/engine/runtime-wheel-licenses.json",
        },
    ]
    provider_python = {
        "cpu": "cp314",
        "cuda": "cp314",
        "directml": "cp312",
        "rocm": "cp312",
    }
    by_id = {row["runtime_id"]: row for row in provider_rows}
    for provider, version in provider_python.items():
        component_id = f"provider-{provider}"
        environment = (
            f"toolchains/python/providers/{provider}-"
            f"{by_id[provider]['lock_sha256'][:16]}"
        )
        result.extend(
            [
                {
                    "operation": "create_venv",
                    "component_id": component_id,
                    "interpreter": f"toolchains/python/base/{version}/python.exe",
                    "target": environment,
                },
                {
                    "operation": "pip_install",
                    "component_id": component_id,
                    "environment": environment,
                    "wheelhouse": f"install-inputs/providers/{provider}/wheelhouse",
                    "requirements": [
                        f"install-inputs/providers/{provider}/{provider}.offline.lock.txt"
                    ],
                    "license_index": (
                        f"install-inputs/providers/{provider}/runtime-wheel-licenses.json"
                    ),
                },
            ]
        )
    return result


def build_outputs() -> dict[str, bytes]:
    shared_path = TOOLCHAINS / "shared-toolchain.v4.json"
    catalog_path = TOOLCHAINS / "tool-catalog.v4.json"
    native_path = TOOLCHAINS / "native-tools.v4.json"
    plugin_manifest = PLUGIN / ".codex-plugin/plugin.json"
    plugin_metadata = document(plugin_manifest)
    release_binding_schema_path = (
        PLUGIN / "schemas/install/first-detection-release-binding.v4.schema.json"
    )
    release_binding_schema = document(release_binding_schema_path)
    plugin_version = plugin_metadata["version"]
    release_binding_schema["properties"]["plugin_version"]["const"] = plugin_version
    release_binding_schema["properties"]["release_ref"]["pattern"] = (
        "^refs/tags/evidence-lane-v"
        + re.escape(plugin_version)
        + "-bundle-[0-9a-f]{16}$"
    )
    release_binding_schema_bytes = (
        json.dumps(
            release_binding_schema,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    runtime_lock_path = PLUGIN / "requirements.runtime.lock.txt"
    runtime_lock_receipt = PLUGIN / "requirements.runtime-lock.v4.json"
    shared = document(shared_path)
    catalog = document(catalog_path)
    native = document(native_path)
    node_runtime_path = TOOLCHAINS / "node-runtime.v4.json"
    node_runtime = document(node_runtime_path)
    python_runtimes_path = TOOLCHAINS / "python-runtimes.v4.json"
    python_runtimes = document(python_runtimes_path)
    git_runtime_path = TOOLCHAINS / "git-runtime.v4.json"
    git_runtime = document(git_runtime_path)
    entries = shared["entries"]
    retained_count = catalog.get("counts", {}).get("retained")
    if (
        not isinstance(retained_count, int)
        or retained_count < 1
        or shared.get("retained_tool_count") != retained_count
        or len(entries) != retained_count
        or len({entry["tool_id"] for entry in entries}) != retained_count
        or any(entry.get("installation_scope") != "shared_once_all_projects" for entry in entries)
    ):
        raise RuntimeError("The complete retained shared matrix is required")
    excluded = {"OpenJDK", "Jackcess", "OneNote_Parser"}
    if excluded.intersection(entry["tool_id"] for entry in entries):
        raise RuntimeError("Removed Office-only tools reached the installation plan")
    native_tools = native.get("tools")
    if not isinstance(native_tools, list) or len(native_tools) != 12:
        raise RuntimeError("The complete retained native manifest is required")
    if excluded.intersection(tool["tool_id"] for tool in native_tools):
        raise RuntimeError("Removed Office-only native tools reached the plan")

    provider_rows = []
    for provider in shared["provider_environments"]:
        manifest = PLUGIN / provider["manifest"]
        lock = PLUGIN / provider["lock"]
        if sha256(manifest) != provider["manifest_sha256"] or sha256(lock) != provider[
            "lock_sha256"
        ]:
            raise RuntimeError(f"Provider input changed: {provider['runtime_id']}")
        provider_rows.append(
            {
                **provider,
                "release_component": f"provider-{provider['runtime_id']}",
            }
        )

    model_paths = {
        "embedding_snapshot": TOOLCHAINS / "embedding-model.v4.json",
        "rapidocr_models": TOOLCHAINS / "pdf-ocr-models.v4.json",
        "tesseract_languages": TOOLCHAINS / "tesseract-models.v4.json",
        "docling_models": TOOLCHAINS / "docling-models.v4.json",
    }
    model_rows = []
    for asset_id, path in model_paths.items():
        value = document(path)
        if (
            value.get("asset_id") != asset_id
            or value.get("no_runtime_downloads") is not True
            or not value.get("files")
        ):
            raise RuntimeError(f"Incomplete model file manifest: {asset_id}")
        model_rows.append(
            {
                "asset_id": asset_id,
                "manifest": path.relative_to(PLUGIN).as_posix(),
                "manifest_sha256": sha256(path),
                "file_count": len(value["files"]),
                "total_bytes": sum(row["bytes"] for row in value["files"]),
            }
        )
    asset_groups = [
        {
            "asset_id": "embedding_snapshot",
            "component_id": "model-assets",
            "path": "toolchains/models/embedding_snapshot",
            "source_manifest": "toolchains/embedding-model.v4.json",
        },
        {
            "asset_id": "rapidocr_models",
            "component_id": "model-assets",
            "path": "toolchains/models/rapidocr_models",
            "source_manifest": "toolchains/pdf-ocr-models.v4.json",
        },
        {
            "asset_id": "tesseract_languages",
            "component_id": "model-assets",
            "path": "toolchains/models/tesseract_languages",
            "source_manifest": "toolchains/tesseract-models.v4.json",
        },
        {
            "asset_id": "docling_models",
            "component_id": "model-assets",
            "path": "toolchains/models/docling_models",
            "source_manifest": "toolchains/docling-models.v4.json",
        },
        {
            "asset_id": "parser_grammars",
            "component_id": "model-assets",
            "path": "toolchains/models/parser_grammars",
            "source_manifest": None,
        },
        {
            "asset_id": "poppler_runtime",
            "component_id": "native-tools",
            "path": "toolchains/bin/poppler",
            "source_manifest": None,
        },
        {
            "asset_id": "tesseract_runtime",
            "component_id": "native-tools",
            "path": "toolchains/bin/tesseract",
            "source_manifest": None,
        },
        {
            "asset_id": "ffmpeg_runtime",
            "component_id": "native-tools",
            "path": "toolchains/bin/ffmpeg",
            "source_manifest": None,
        },
        {
            "asset_id": "libreoffice_runtime",
            "component_id": "native-tools",
            "path": "toolchains/bin/libreoffice",
            "source_manifest": None,
        },
        {
            "asset_id": "powerbi_tom_runtime",
            "component_id": "powerbi-runtime",
            "path": "toolchains/bin/powerbi_tom",
            "source_manifest": None,
        },
        {
            "asset_id": "powerbi_pbix_runtime",
            "component_id": "powerbi-runtime",
            "path": "toolchains/bin/powerbi_pbix",
            "source_manifest": "toolchains/powerbi-pbix-runtime.v4.json",
        },
    ]

    license_index = retained_license_index(entries)
    license_bytes = json.dumps(
        license_index, ensure_ascii=False, indent=2
    ).encode("utf-8") + b"\n"
    license_path = "toolchains/licenses/retained-install-license-index.v4.json"
    policy_path = "toolchains/license-policy.v4.json"
    policy = sealed(
        {
            "schema": "evidence-lane.toolchain-license-policy.v4",
            "status": "COMPLETE_PREINSTALL_SOURCE_EVIDENCE",
            "retained_tool_count": len(entries),
            "retained_license_index": license_path,
            "retained_license_index_sha256": hashlib.sha256(license_bytes).hexdigest(),
            "runtime_lock_receipt": "requirements.runtime-lock.v4.json",
            "runtime_lock_receipt_sha256": sha256(runtime_lock_receipt),
            "runtime_wheel_license_index_required": True,
            "component_license_evidence_required": True,
            "license_record_count": len(license_index["records"]),
            "studio_bundle_owns_retained_local_toolchain": True,
            "external_services_remain_explicit": True,
            "plugin_engine_and_host_capabilities_classified_separately": True,
            "tunnel_dependency": False,
            "ghostscript_default_bundled": False,
            "ghostscript_license_grant_required": True,
            "poppler_separate_process_and_source_offer_required": True,
            "removed_tool_ids": list(REMOVED_TOOL_IDS),
            "removed_tool_license_records_packaged": False,
            "installed_execution_claimed": False,
        }
    )
    policy_bytes = json.dumps(
        policy, ensure_ascii=False, indent=2
    ).encode("utf-8") + b"\n"
    source_paths = [
        shared_path,
        catalog_path,
        native_path,
        TOOLCHAINS / "node-runtime.v4.json",
        TOOLCHAINS / "node-runtime.package.json",
        TOOLCHAINS / "node-runtime.package-lock.json",
        TOOLCHAINS / "python-runtimes.v4.json",
        TOOLCHAINS / "git-runtime.v4.json",
        plugin_manifest,
        runtime_lock_path,
        runtime_lock_receipt,
        PLUGIN / ".mcp.json",
        PLUGIN / "authorities/session_authority/installation-layout.v4.json",
        PLUGIN / "schemas/install/first-detection-release-binding.v4.schema.json",
        PLUGIN / "schemas/install/first-detection-bundle-plan.v4.schema.json",
        PLUGIN / "schemas/install/first-detection-component.v4.schema.json",
        PLUGIN / "schemas/install/first-detection-installation.v4.schema.json",
        PLUGIN / "schemas/install/explicit-license-grant.v4.schema.json",
        PLUGIN / "schemas/install/first-detection-source-manifest.v4.schema.json",
        PLUGIN / "schemas/install/installed-file-manifest.v4.schema.json",
        PLUGIN / "schemas/install/runtime-wheel-license-index.v4.schema.json",
        PLUGIN / "schemas/install/first-detection-status.v4.schema.json",
        PLUGIN / "scripts/launch_studio.py",
        PLUGIN / "scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.cs",
        PLUGIN / "scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.exe",
        PLUGIN
        / "scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.build.json",
        PLUGIN
        / "scripts/windows_studio_launcher/Build-EvidenceLaneStudioLauncher.ps1",
        *model_paths.values(),
        *[PLUGIN / row["manifest"] for row in provider_rows],
        *[PLUGIN / row["lock"] for row in provider_rows],
    ]
    source_rows = []
    for path in source_paths:
        content = (
            release_binding_schema_bytes
            if path.resolve() == release_binding_schema_path.resolve()
            else path.read_bytes()
        )
        source_rows.append(
            {
                "path": path.relative_to(PLUGIN).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    source_rows += [
        {
            "path": license_path,
            "sha256": hashlib.sha256(license_bytes).hexdigest(),
            "bytes": len(license_bytes),
        },
        {
            "path": policy_path,
            "sha256": hashlib.sha256(policy_bytes).hexdigest(),
            "bytes": len(policy_bytes),
        },
    ]
    assignments = []
    for entry in entries:
        assignments.append(
            {
                "tool_id": entry["tool_id"],
                "kind": entry["kind"],
                "primary_component": primary_component(entry),
                "model_assets": entry["model_assets"],
                "external_configuration_required": entry[
                    "external_configuration_required"
                ],
                "license_grant_required": entry["license_grant_required"],
                "packages": entry["packages"],
                "base_requirement": entry["base_requirement"],
            }
        )
    external = [
        {
            "tool_id": entry["tool_id"],
            "adapter_component": "core-engine-studio",
            "configuration_status_at_install": "unconfigured",
            "credentials_in_release_assets": False,
        }
        for entry in entries
        if entry["external_configuration_required"]
    ]
    offline_wheel_sets = [
        {
            "environment_id": "engine",
            "component_id": "core-engine-studio",
            "python_version": "3.14",
            "source_lock": "requirements.runtime.lock.txt",
            "source_lock_sha256": sha256(runtime_lock_path),
            "source_lock_receipt": "requirements.runtime-lock.v4.json",
            "source_lock_receipt_sha256": sha256(runtime_lock_receipt),
            "wheelhouse": "install-inputs/engine/wheelhouse",
            "offline_lock": "install-inputs/engine/core.offline.lock.txt",
            "license_index": "install-inputs/engine/runtime-wheel-licenses.json",
            "packages": lock_packages(runtime_lock_path),
        }
    ]
    for provider in provider_rows:
        source_manifest = document(PLUGIN / provider["manifest"])
        offline_wheel_sets.append(
            {
                "environment_id": provider["runtime_id"],
                "component_id": f"provider-{provider['runtime_id']}",
                "python_version": provider["python_version"],
                "source_lock": provider["lock"],
                "source_lock_sha256": provider["lock_sha256"],
                "wheelhouse": (
                    f"install-inputs/providers/{provider['runtime_id']}/wheelhouse"
                ),
                "offline_lock": (
                    f"install-inputs/providers/{provider['runtime_id']}/"
                    f"{provider['runtime_id']}.offline.lock.txt"
                ),
                "license_index": (
                    f"install-inputs/providers/{provider['runtime_id']}/"
                    "runtime-wheel-licenses.json"
                ),
                "packages": [
                    {
                        "name": row["name"],
                        "version": row["version"],
                        "hashes": [row["sha256"]],
                    }
                    for row in source_manifest["packages"]
                ],
            }
        )
    body: dict[str, Any] = {
        "schema": "evidence-lane.first-detection-bundle-plan.v4",
        "status": "COMPLETE_PREINSTALL_SOURCE_PLAN",
        "plugin": {
            "id": "evidence-lane-plugin",
            "version": plugin_version,
            "repository": "https://github.com/rathee000001/evidence_lane_plugin",
            "sparse_root": "plugins/evidence-lane-plugin",
        },
        "target": {
            "system": "Windows",
            "machine": "AMD64",
            "default_root": "C:/Apps/EvidenceLaneStudio",
            "override": "EVIDENCE_LANE_STUDIO_ROOT",
            "shared_once_across_projects": True,
            "mobile_studio": False,
            "macos_studio": False,
        },
        "release_asset_policy": {
            "repository_release_only": True,
            "exact_immutable_tag_required": True,
            "asset_sha256_and_size_required": True,
            "component_file_manifest_required": True,
            "third_party_download_during_first_detection": False,
            "operation_time_downloads": False,
            "maximum_asset_bytes": 2_140_000_000,
            "maximum_selected_total_bytes": 32_000_000_000,
        },
        "components": components(entries),
        "tool_assignments": assignments,
        "retained_tool_count": len(entries),
        "provider_environments": provider_rows,
        "model_assets": model_rows,
        "asset_groups": asset_groups,
        "native_inputs": [
            {
                "tool_id": tool["tool_id"],
                "version": tool["version"],
                "kind": tool["kind"],
                "upstream_sha256": tool.get("download_sha256"),
                "upstream_bytes": tool.get("download_size_bytes"),
                "installed_executables": native_installed_executables(tool),
                "release_component": (
                    primary_component(matches[0]) if matches else "native-tools"
                ),
                "catalog_tool_id": matches[0]["tool_id"] if matches else None,
                "support_dependency_for": (
                    "LibreOffice" if tool["tool_id"] == "lessmsi_extractor" else None
                ),
                "license_grant_required": bool(
                    tool.get("license_grant_reference_required")
                ),
            }
            for tool in native_tools
            for matches in [
                [
                    entry
                    for entry in entries
                    if entry["native_manifest_tool"] == tool["tool_id"]
                ]
            ]
        ]
        + [
            {
                "tool_id": "git",
                "version": git_runtime["version"],
                "kind": "portable_executable_bundle",
                "upstream_sha256": git_runtime["sha256"],
                "upstream_bytes": git_runtime["bytes"],
                "installed_executables": ["git/cmd/git.exe"],
                "release_component": "native-tools",
                "catalog_tool_id": "Git",
                "support_dependency_for": None,
                "license_grant_required": False,
            }
        ],
        "node_runtime": {
            "manifest": "toolchains/node-runtime.v4.json",
            "manifest_sha256": sha256(node_runtime_path),
            "node_version": node_runtime["node"]["version"],
            "mermaid_cli_version": node_runtime["mermaid_cli"]["version"],
            "browser_version": node_runtime["browser"]["version"],
            "release_component": "node-runtime",
        },
        "python_runtimes": {
            "manifest": "toolchains/python-runtimes.v4.json",
            "manifest_sha256": sha256(python_runtimes_path),
            "runtime_ids": [row["runtime_id"] for row in python_runtimes["runtimes"]],
            "versions": {
                row["runtime_id"]: row["version"]
                for row in python_runtimes["runtimes"]
            },
            "release_component": "core-engine-studio",
        },
        "git_runtime": {
            "manifest": "toolchains/git-runtime.v4.json",
            "manifest_sha256": sha256(git_runtime_path),
            "version": git_runtime["version"],
            "release_component": "native-tools",
        },
        "external_service_configuration": external,
        "offline_wheel_sets": offline_wheel_sets,
        "materialization_steps": materialization_steps(provider_rows),
        "entrypoints": {
            "runtime_python": "engine/venv/Scripts/python.exe",
            "runtime_pythonw": "engine/venv/Scripts/pythonw.exe",
            "plugin_root": "plugin",
            "studio_launcher_executable": "app/EvidenceLaneStudio.exe",
            "mcp_launcher": "plugin/scripts/run_mcp.py",
            "engine_launcher": "plugin/scripts/run_engine.py",
            "installation_self_test": "plugin/scripts/verify_installed_runtime.py",
            "installation_registration": "plugin/scripts/register_installed_runtime.py",
        },
        "source_inputs": source_rows,
        "license_index": {
            "path": license_path,
            "sha256": hashlib.sha256(license_bytes).hexdigest(),
            "record_count": len(license_index["records"]),
        },
        "excluded_office": {
            "applications": [
                "Access",
                "Visio",
                "Outlook",
                "Microsoft Project",
                "Publisher",
                "OneNote",
            ],
            "exclusive_tools": sorted(excluded),
            "retained_office": ["Word", "PowerPoint", "Excel"],
            "onedrive_role": "storage_not_office_application",
        },
        "installation_state": "not_installed",
        "installed_native_execution_claimed": False,
    }
    plan = sealed(body)
    plan_bytes = json.dumps(plan, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    template = sealed(
        {
            "schema": "evidence-lane.first-detection-release-binding.v4",
            "status": "SOURCE_TEMPLATE_UNBOUND",
            "installation_enabled": False,
            "plugin_id": "evidence-lane-plugin",
            "plugin_version": body["plugin"]["version"],
            "repository": body["plugin"]["repository"],
            "sparse_root": body["plugin"]["sparse_root"],
            "release_ref": None,
            "bundle_plan": "provisioning/full-bundle-plan.v4.json",
            "bundle_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "source_manifest": None,
            "source_manifest_sha256": None,
            "assets": [],
            "reason": (
                "D086 builds exact component assets and a source manifest; D087 "
                "commits and tags the bound candidate before D088 installation."
            ),
        }
    )
    template_bytes = json.dumps(
        template, ensure_ascii=False, indent=2
    ).encode("utf-8") + b"\n"
    return {
        "schemas/install/first-detection-release-binding.v4.schema.json": release_binding_schema_bytes,
        license_path: license_bytes,
        policy_path: policy_bytes,
        "provisioning/full-bundle-plan.v4.json": plan_bytes,
        "provisioning/release-binding.v4.json": template_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for relative, content in build_outputs().items():
        path = PLUGIN / relative
        if not path.is_file() or path.read_bytes() != content:
            changed.append(relative)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    if args.check and changed:
        raise RuntimeError("First-detection projections changed: " + ", ".join(changed))
    print(json.dumps({"changed": changed, "check": args.check}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
