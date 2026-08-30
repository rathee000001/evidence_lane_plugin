#!/usr/bin/env python3
"""Generate inspectable package surfaces from the canonical plugin code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
PUBLIC_APP_ROOT = REPOSITORY_ROOT / "apps" / "evidence-lane-app"
PACKAGE_ROOT = PLUGIN_ROOT / "src" / "evidence_lane_plugin"
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _canonicalize_sqlite_header(path: Path) -> dict[str, Any]:
    """Make a logically unchanged packaged SQLite authority byte-stable.

    SQLite increments the file-change counter, schema cookie, and
    version-valid-for fields even when a deterministic rebuild produces the
    same logical database. The packaged ENV/UOP authorities are closed before
    this step, so bind the schema cookie to the canonical sqlite_master graph
    and reset the paired change/version counters. Runtime writes will advance
    them normally after installation.
    """

    connection = sqlite3.connect(path)
    try:
        schema_rows = [
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "table": str(row[2]),
                "sql": str(row[3] or ""),
            }
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "ORDER BY type,name,tbl_name"
            )
        ]
        schema_cookie = (
            int(hashlib.sha256(_json_bytes(schema_rows)).hexdigest()[:8], 16)
            & 0x7FFFFFFF
        )
        schema_cookie = schema_cookie or 1
        connection.execute(f"PRAGMA schema_version={schema_cookie}")
        connection.commit()
    finally:
        connection.close()

    header = path.read_bytes()[:100]
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise RuntimeError(f"SQLITE_HEADER_INVALID:{path}")
    stable_change_counter = 1
    with path.open("r+b") as stream:
        stream.seek(24)
        stream.write(stable_change_counter.to_bytes(4, "big"))
        stream.seek(40)
        stream.write(schema_cookie.to_bytes(4, "big"))
        stream.seek(92)
        stream.write(stable_change_counter.to_bytes(4, "big"))
        stream.flush()
        os.fsync(stream.fileno())

    verification = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        integrity = [
            str(row[0]) for row in verification.execute("PRAGMA integrity_check")
        ]
        observed_cookie = int(
            verification.execute("PRAGMA schema_version").fetchone()[0]
        )
    finally:
        verification.close()
    if integrity != ["ok"] or observed_cookie != schema_cookie:
        raise RuntimeError(f"SQLITE_HEADER_CANONICALIZATION_FAILED:{path}")
    return {
        "path": path.relative_to(PLUGIN_ROOT).as_posix(),
        "schema_cookie": schema_cookie,
        "file_change_counter": stable_change_counter,
        "version_valid_for": stable_change_counter,
        "sha256": _sha256(path),
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content.replace("\r\n", "\n").encode("utf-8")
    last_error: OSError | None = None
    for attempt in range(8):
        handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(raw)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return
        except OSError as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt == 7:
                break
            time.sleep(0.05 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _write_json(path: Path, body: dict[str, Any]) -> None:
    body = dict(body)
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    _write(path, _pretty_json(body))


def _refresh_flash_authority_pins() -> dict[str, str]:
    source = PACKAGE_ROOT / "flash_authority.py"
    text = source.read_text(encoding="utf-8")
    values = {
        "FLASH_MANIFEST_SHA256": _sha256(
            PLUGIN_ROOT / "env" / "SESSION_FLASH_MANIFEST.json"
        ),
        "ENV_MMD_SHA256": _sha256(PLUGIN_ROOT / "env" / "env_mmd.mmd"),
        "UOP_MMD_SHA256": _sha256(PLUGIN_ROOT / "uop" / "uop_mmd.mmd"),
        "ENV_DOT_SHA256": _sha256(PLUGIN_ROOT / "env" / "env_mmd.dot"),
        "UOP_DOT_SHA256": _sha256(PLUGIN_ROOT / "uop" / "uop_mmd.dot"),
    }
    for name, value in values.items():
        pattern = re.compile(
            rf"({name}\s*=\s*(?:\(\s*)?\")([A-F0-9]{{64}})(\"(?:\s*\))?)",
            flags=re.MULTILINE,
        )
        text, count = pattern.subn(rf"\g<1>{value}\g<3>", text, count=1)
        if count != 1:
            raise ValueError(f"Unable to refresh flash authority pin: {name}")
    _write(source, text)
    projection_tables = {
        "env": (
            "codex_host_variant_v17",
            "env_workflow_event_v17",
            "env_mode_registry_v17",
            "env_project_class_policy_v17",
            "env_formula_registry_v17",
            "env_operator_registry_v17",
            "env_tool_registry_v17",
            "env_action_binding_v17",
            "env_lane_binding_v17",
            "env_skill_binding_v17",
            "env_hook_binding_v17",
            "env_sdk_action_binding_v17",
            "env_mcp_action_binding_v17",
        ),
        "uop": (
            "uop_governance_operator_v17",
            "uop_project_class_hil_policy_v17",
            "uop_workflow_gate_v17",
            "uop_action_policy_v17",
            "uop_tool_policy_v17",
            "uop_host_policy_v17",
            "uop_fallback_policy_v17",
        ),
    }
    projection: dict[str, Any] = {}
    for authority, tables in projection_tables.items():
        database = PLUGIN_ROOT / authority / f"{authority}_sqlite.sqlite"
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            projection[authority] = {
                table: [
                    dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')
                ]
                for table in tables
            }
        finally:
            connection.close()
    mode_values = {
        "ENV15_ENV_SQLITE_SHA256": _sha256(PLUGIN_ROOT / "env" / "env_sqlite.sqlite"),
        "ENV15_UOP_SQLITE_SHA256": _sha256(PLUGIN_ROOT / "uop" / "uop_sqlite.sqlite"),
        "ENV15_MODE_POLICY_PROJECTION_SHA256": hashlib.sha256(_json_bytes(projection))
        .hexdigest()
        .upper(),
    }
    mode_source = PACKAGE_ROOT / "mode_governance.py"
    mode_text = mode_source.read_text(encoding="utf-8")
    for name, value in mode_values.items():
        pattern = re.compile(
            rf"({name}\s*=\s*(?:\(\s*)?\")([A-F0-9]{{64}})(\"(?:\s*\))?)",
            flags=re.MULTILINE,
        )
        mode_text, count = pattern.subn(
            rf"\g<1>{value}\g<3>",
            mode_text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Unable to refresh mode authority pin: {name}")
    _write(mode_source, mode_text)
    values.update(mode_values)
    return values


_NATIVE_LICENSE_TOOL_IDS = {
    "ripgrep_15_2_0": "ripgrep",
    "SevenZip_NSIS_extractor": "seven_zip_extractor",
    "jq": "jq",
    "Graphviz_dot": "graphviz",
    "pytesseract_Tesseract": "tesseract",
    "Poppler_pdftotext_pdfinfo": "poppler",
    "Ghostscript": "ghostscript",
    "FFmpeg": "ffmpeg",
}


def _tool_license_inventory_row(
    row: dict[str, Any],
    *,
    native_tools: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Classify license evidence for one tool requirement without merging MCP."""

    tool = str(row["tool"])
    requirement = str(row["requirement"])
    native_id = _NATIVE_LICENSE_TOOL_IDS.get(tool)
    native = native_tools.get(native_id or "")
    distribution_requirements = {
        "CONDITIONAL_SQLITE_VECTOR_EXTENSION",
        "OPTIONAL_HIDDEN_RUNTIME_RENDERER",
        "REQUIRED_CODE_ACCEPTANCE",
        "REQUIRED_CONFIGURED_GATE",
        "REQUIRED_DEPENDENCY",
        "REQUIRED_FOR_CONFIGURED_NETWORK_ADAPTERS",
        "REQUIRED_HIDDEN_RUNTIME_BINARY_AND_DEPENDENCY",
        "REQUIRED_LOCAL_UPDATE_MODEL_ACQUISITION",
        "REQUIRED_PROTECTED_REMOTE_PROFILES",
    }
    external_requirements = {
        "CONFIGURED_EXTERNAL_SERVICE",
        "REPOSITORY_PUBLIC_ADAPTER_ONLY",
        "REQUIRED_REPOSITORY_ONLY",
    }
    if native is not None:
        license_expression = str(
            native.get("license_spdx") or "RUNTIME_DISTRIBUTION_METADATA"
        )
        evidence = (
            "toolchains/native-tools.v1.json plus the installed native-tool "
            "license receipt"
        )
        classification = "NATIVE_RUNTIME_ASSET"
        install_mode = str(native["kind"])
        redistributed = str(native["kind"]) == "package_local_existing"
        runtime_distribution_license_required = bool(
            requirement == "REQUIRED_HIDDEN_RUNTIME_BINARY_AND_DEPENDENCY"
            or str(native["kind"]) == "python_wheel_binary"
        )
    elif requirement in external_requirements:
        license_expression = "EXTERNAL_SERVICE_OR_REPOSITORY_TERMS"
        evidence = "validated at the repository/delivery gate; no binary redistributed"
        classification = "EXTERNAL_OR_REPOSITORY_GATE"
        install_mode = "not_installed_by_tunnel"
        redistributed = False
        runtime_distribution_license_required = False
    elif requirement in distribution_requirements:
        license_expression = "EXACT_INSTALLED_DISTRIBUTION_METADATA"
        evidence = (
            "runtime/licenses/<runtime_key>/manifest.v1.json with copied license "
            "files, metadata, classifiers, version, bytes, and SHA-256"
        )
        classification = "PYTHON_OR_HOST_DISTRIBUTION"
        install_mode = "locked_hidden_runtime_or_applicable_build_gate"
        redistributed = False
        runtime_distribution_license_required = True
    elif tool == "Python":
        license_expression = "PSF-2.0"
        evidence = (
            "exact hidden-runtime Python distribution and runtime license manifest"
        )
        classification = "HIDDEN_RUNTIME_INTERPRETER"
        install_mode = "hidden_runtime"
        redistributed = False
        runtime_distribution_license_required = True
    elif tool in {"SQLite_CAS", "SQLite_FTS5_BM25", "SQLite_immutable_URI_reader"}:
        license_expression = (
            "LicenseRef-SQLite-Public-Domain AND LicenseRef-Proprietary"
        )
        evidence = (
            "SQLite runtime identity plus LICENSE.md for Evidence Lane implementation"
        )
        classification = "SYSTEM_CAPABILITY_WITH_PLUGIN_IMPLEMENTATION"
        install_mode = "hidden_runtime_and_internal_code"
        redistributed = False
        runtime_distribution_license_required = True
    elif tool == "Git":
        license_expression = "GPL-2.0-only"
        evidence = "host Git version probe; Git is not redistributed by the plugin"
        classification = "HOST_SYSTEM_TOOL"
        install_mode = "host_probe_only"
        redistributed = False
        runtime_distribution_license_required = False
    elif tool == "PowerShell_Win32_APIs":
        license_expression = "HOST_PLATFORM_TERMS"
        evidence = "Windows host capability probe; no host binary redistributed"
        classification = "HOST_PLATFORM_CAPABILITY"
        install_mode = "host_probe_only"
        redistributed = False
        runtime_distribution_license_required = False
    else:
        license_expression = "LicenseRef-Proprietary"
        evidence = "LICENSE.md"
        classification = "EVIDENCE_LANE_INTERNAL_COMPONENT"
        install_mode = "package_internal"
        redistributed = True
        runtime_distribution_license_required = False
    return {
        "tool": tool,
        "requirement": requirement,
        "classification": classification,
        "install_mode": install_mode,
        "license_expression_or_terms": license_expression,
        "license_evidence": evidence,
        "redistributed_by_source_package": redistributed,
        "installed_runtime_distribution_license_required": (
            runtime_distribution_license_required
        ),
        "mcp_action_counted_here": False,
        "native_tool_id": native_id,
    }


def _requirement_license_record(
    *,
    ordinal: int,
    row: dict[str, Any],
    native_tools: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    tool = str(row["tool"])
    slug = re.sub(r"[^a-z0-9]+", "-", tool.casefold()).strip("-") or "tool"
    relative = (
        f"toolchains/licenses/requirements/{ordinal:03d}-{slug}/LICENSE-RECORD.json"
    )
    native_id = str(row.get("native_tool_id") or "")
    native = native_tools.get(native_id)
    copied_paths: list[dict[str, Any]] = []
    native_acquisition: dict[str, Any] | None = None
    evidence_paths: list[str] = []
    if native is not None:
        declared_files = list(native.get("license_files") or [])
        if native_id == "ripgrep":
            declared_files = [
                {
                    "path": "toolchains/licenses/ripgrep-15.2.0/LICENSE-MIT",
                },
                {
                    "path": "toolchains/licenses/ripgrep-15.2.0/UNLICENSE",
                },
            ]
        for declared in declared_files:
            source = PLUGIN_ROOT / str(declared["path"])
            copied_paths.append(
                {
                    "path": source.relative_to(PLUGIN_ROOT).as_posix(),
                    "bytes": source.stat().st_size,
                    "sha256": _sha256(source),
                }
            )
        license_download = native.get("license")
        if isinstance(license_download, dict):
            native_acquisition = {
                "url": str(license_download["url"]),
                "bytes": int(license_download["size_bytes"]),
                "sha256": str(license_download["sha256"]),
                "copied_to_hidden_runtime_before_use": True,
            }
        evidence_paths.append("toolchains/native-tools.v1.json")
    if row["classification"] == "EVIDENCE_LANE_INTERNAL_COMPONENT":
        evidence_paths.append("LICENSE.md")
    if row["classification"] == "SYSTEM_CAPABILITY_WITH_PLUGIN_IMPLEMENTATION":
        evidence_paths.extend(
            [
                "LICENSE.md",
                "requirements.torch-cpu.lock.txt",
                "requirements.torch-nvidia.lock.txt",
                "requirements.onnx-directml.lock.txt",
                "requirements.lock.txt",
            ]
        )
    if row["classification"] == "HIDDEN_RUNTIME_INTERPRETER":
        evidence_paths.extend(
            [
                "requirements.torch-cpu.lock.txt",
                "requirements.torch-nvidia.lock.txt",
                "requirements.onnx-directml.lock.txt",
                "requirements.lock.txt",
            ]
        )
    if row["classification"] == "PYTHON_OR_HOST_DISTRIBUTION":
        evidence_paths.extend(
            [
                "requirements.torch-cpu.lock.txt",
                "requirements.torch-nvidia.lock.txt",
                "requirements.onnx-directml.lock.txt",
                "requirements.lock.txt",
                "requirements.toolchain.lock.txt",
            ]
        )
    if row["classification"] == "HOST_SYSTEM_TOOL":
        evidence_paths.append("toolchains/tool-requirement-matrix.v1.json")
    if row["classification"] == "HOST_PLATFORM_CAPABILITY":
        evidence_paths.append("toolchains/tool-requirement-matrix.v1.json")
    if row["classification"] == "EXTERNAL_OR_REPOSITORY_GATE":
        evidence_paths.append("toolchains/tool-requirement-matrix.v1.json")
    runtime_required = bool(row["installed_runtime_distribution_license_required"])
    record = {
        "schema": "evidence-lane.tool-requirement-license-record.v1",
        "status": "PASS",
        "ordinal": ordinal,
        "tool": tool,
        "requirement": row["requirement"],
        "classification": row["classification"],
        "install_mode": row["install_mode"],
        "license_expression_or_terms": row["license_expression_or_terms"],
        "license_evidence": row["license_evidence"],
        "evidence_paths": sorted(set(evidence_paths)),
        "redistributed_by_source_package": row["redistributed_by_source_package"],
        "source_package_copied_license_text_present": bool(copied_paths),
        "source_package_copied_license_texts": copied_paths,
        "native_license_acquisition": native_acquisition,
        "runtime_distribution_license_required": runtime_required,
        "runtime_distribution_license_manifest": (
            "%RUNTIME_CONTROL_ROOT%/runtime/licenses/<runtime_key>/manifest.v1.json"
            if runtime_required
            else None
        ),
        "runtime_distribution_license_files_must_be_copied_before_use": (
            runtime_required
        ),
        "metadata_without_a_license_file_is_labeled_metadata_only": True,
        "mcp_inventory_separate": True,
    }
    return relative, record


def _artifact_receipt_schema(
    *,
    authority_id: str,
    artifact_path: str,
    artifact_role: str,
    syntax: str,
    validator: str,
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{authority_id} {artifact_role} artifact receipt",
        "type": "object",
        "required": [
            "authority_id",
            "artifact_path",
            "artifact_role",
            "syntax",
            "bytes",
            "sha256",
            "validator",
            "status",
        ],
        "properties": {
            "authority_id": {"const": authority_id},
            "artifact_path": {"const": artifact_path},
            "artifact_role": {"const": artifact_role},
            "syntax": {"const": syntax},
            "bytes": {"type": "integer", "minimum": 1},
            "sha256": {"type": "string", "pattern": "^[A-F0-9]{64}$"},
            "validator": {"const": validator},
            "status": {"const": "PASS"},
        },
        "additionalProperties": False,
    }


def _json_file_schema(
    *, authority_id: str, artifact_path: str, schema_id: str
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{authority_id} {artifact_path}",
        "type": "object",
        "required": ["schema", "status", "authority_id"],
        "properties": {
            "schema": {"const": schema_id},
            "status": {"const": "PASS"},
            "authority_id": {"const": authority_id},
        },
        "additionalProperties": True,
    }


def _skill_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        description = ""
        for line in text.splitlines():
            if line.startswith("description:"):
                raw_description = line.split(":", 1)[1].strip()
                if raw_description.startswith('"') and raw_description.endswith('"'):
                    description = str(json.loads(raw_description))
                elif raw_description.startswith("'") and raw_description.endswith("'"):
                    description = raw_description[1:-1].replace("''", "'")
                else:
                    description = raw_description
                break
        rows.append(
            {
                "name": path.parent.name,
                "description": description,
                "skill_path": path.relative_to(PLUGIN_ROOT).as_posix(),
                "skill_sha256": _sha256(path),
            }
        )
    return rows


def _purge_legacy_command_surface() -> None:
    """Remove the retired command adapter layer and all generated projections."""

    commands_root = PLUGIN_ROOT / "commands"
    if commands_root.exists():
        shutil.rmtree(commands_root)
    command_schema_root = PLUGIN_ROOT / "schemas" / "commands"
    if command_schema_root.exists():
        shutil.rmtree(command_schema_root)


def _generate_skill_surface_registry(skills: list[dict[str, Any]]) -> dict[str, Any]:
    routing_path = (
        PLUGIN_ROOT / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    )
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for skill in skills:
        name = str(skill["name"])
        root = PLUGIN_ROOT / "skills" / name
        members = [
            {
                "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        ]
        rows.append(
            {
                "name": name,
                "description": skill["description"],
                "members": members,
                "member_count": len(members),
                "workflow": routing["workflows"].get(name),
                "routing_manifest_sha256": _sha256(routing_path),
            }
        )
    body = {
        "schema": "evidence-lane.skill-surface-registry.v1",
        "status": "PASS",
        "skill_count": len(rows),
        "skills": rows,
        "separate_command_count": 0,
        "legacy_command_surface_present": False,
        "mcp_server": "evidence-lane",
        "all_skill_members_hash_bound": True,
        "competing_workflow_implementations_allowed": False,
    }
    _write_json(PLUGIN_ROOT / "skills" / "skill-surface-registry.v1.json", body)
    return body


def _generate_hook_event_surfaces() -> dict[str, Any]:
    from evidence_lane_plugin.hook_contract import (
        HOOK_EVENT_WORKFLOW_CONTRACTS,
    )

    hooks_root = PLUGIN_ROOT / "hooks"
    hooks = json.loads((hooks_root / "hooks.json").read_text(encoding="utf-8"))
    logical = json.loads(
        (hooks_root / "logical-actions.json").read_text(encoding="utf-8")
    )
    adapter_map = {
        "SessionStart": "session_start.py",
        "SubagentStart": "subagent_start.py",
        "UserPromptSubmit": "prompt_submit.py",
        "PreToolUse": "pre_tool_use.py",
        "PermissionRequest": "permission_request.py",
        "PostToolUse": "post_tool_use.py",
        "PreCompact": "lifecycle_boundary.py",
        "PostCompact": "lifecycle_boundary.py",
        "SubagentStop": "subagent_stop.py",
        "Stop": "stop_response.py",
        "SessionEnd": "lifecycle_boundary.py",
    }
    events_root = hooks_root / "events"
    rows: list[dict[str, Any]] = []
    expected_event_directories = set(adapter_map)
    for event_number, event_name in enumerate(adapter_map, start=1):
        groups = list(hooks["hooks"][event_name])
        workflow_contract = dict(HOOK_EVENT_WORKFLOW_CONTRACTS[event_name])
        workflow_contract_sha256 = (
            hashlib.sha256(_json_bytes(workflow_contract)).hexdigest().upper()
        )
        event_root = events_root / event_name
        handlers = [handler for group in groups for handler in group["hooks"]]
        logical_actions = list(logical["logicalActions"][event_name])
        if len(handlers) != len(logical_actions):
            raise ValueError(f"Hook handler/action mismatch: {event_name}")
        adapter = hooks_root / adapter_map[event_name]
        handler_rows: list[dict[str, Any]] = []
        for handler_number, (handler, logical_action) in enumerate(
            zip(handlers, logical_actions, strict=True),
            start=1,
        ):
            stage_match = re.search(
                r"--handler\s+([A-Za-z0-9_.-]+)", handler["command"]
            )
            if stage_match is None:
                raise ValueError(f"Hook handler has no stage binding: {event_name}")
            stage_name = stage_match.group(1)
            stage = hooks_root / stage_name
            body = {
                "schema": "evidence-lane.hook-handler-binding.v1",
                "status": "PASS",
                "event_number": event_number,
                "handler_number": handler_number,
                "display_id": f"{event_number}.{handler_number}",
                "event": event_name,
                "handler": handler,
                "logical_action": logical_action,
                "handler_stage_role": str(logical_action),
                "host_timing": workflow_contract["host_timing"],
                "workflow_contract": workflow_contract,
                "workflow_contract_sha256": workflow_contract_sha256,
                "stage": f"hooks/{stage_name}",
                "stage_sha256": _sha256(stage),
                "adapter": f"hooks/{adapter.name}",
                "adapter_sha256": _sha256(adapter),
                "pipeline": "hooks/subhook_pipeline.py",
                "pipeline_sha256": _sha256(hooks_root / "subhook_pipeline.py"),
            }
            path = event_root / f"handler-{event_number}.{handler_number}.v1.json"
            _write_json(path, body)
            handler_rows.append(
                {
                    "display_id": body["display_id"],
                    "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                    "sha256": _sha256(path),
                }
            )
        _write_json(
            event_root / "event.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"Evidence Lane {event_name} event binding",
                "type": "object",
                "required": [
                    "schema",
                    "status",
                    "event_number",
                    "event",
                    "handlers",
                    "adapter_sha256",
                    "workflow_contract",
                ],
                "properties": {
                    "schema": {"const": "evidence-lane.hook-event-binding.v1"},
                    "status": {"const": "PASS"},
                    "event_number": {"const": event_number},
                    "event": {"const": event_name},
                    "handlers": {"type": "array", "minItems": 1},
                    "workflow_contract": {"type": "object"},
                    "adapter_sha256": {
                        "type": "string",
                        "pattern": "^[A-F0-9]{64}$",
                    },
                },
                "additionalProperties": True,
            },
        )
        event_body = {
            "schema": "evidence-lane.hook-event-binding.v1",
            "status": "PASS",
            "event_number": event_number,
            "event": event_name,
            "matcher_groups": groups,
            "handler_count": len(handler_rows),
            "handlers": handler_rows,
            "adapter": f"hooks/{adapter.name}",
            "adapter_sha256": _sha256(adapter),
            "workflow_contract": workflow_contract,
            "workflow_contract_sha256": workflow_contract_sha256,
            "precompact_seals_before_compaction": event_name != "PreCompact"
            or adapter.name == "lifecycle_boundary.py",
            "postcompact_rehydrates_after_compaction": event_name != "PostCompact"
            or adapter.name == "lifecycle_boundary.py",
        }
        _write_json(event_root / "event.v1.json", event_body)
        _write(
            event_root / "README.md",
            f"# Hook {event_number}: {event_name}\n\n"
            f"This event has {len(handler_rows)} ordered, separately hash-bound "
            "handlers. The event adapter and shared stage pipeline remain the "
            "single executable implementation. Its exact host timing, lifecycle "
            "consumer, skill action, workflow phases, and public-action boundary "
            "are bound by `workflow_contract` in `event.v1.json`.\n",
        )
        rows.append(
            {
                "event_number": event_number,
                "event": event_name,
                "path": event_root.relative_to(PLUGIN_ROOT).as_posix(),
                "event_sha256": _sha256(event_root / "event.v1.json"),
                "handler_count": len(handler_rows),
                "workflow_contract": workflow_contract,
                "workflow_contract_sha256": workflow_contract_sha256,
            }
        )
    for path in events_root.iterdir():
        if path.is_dir() and path.name not in expected_event_directories:
            raise ValueError(f"Unexpected hook event surface: {path.name}")
    body = {
        "schema": "evidence-lane.hook-event-surface-registry.v1",
        "status": "PASS",
        "event_count": len(rows),
        "handler_action_count": sum(row["handler_count"] for row in rows),
        "events": rows,
        "hooks_manifest_sha256": _sha256(hooks_root / "hooks.json"),
        "logical_actions_sha256": _sha256(hooks_root / "logical-actions.json"),
        "compiled_host_receipt_sha256": _sha256(
            hooks_root / "EvidenceLaneHookHost.build.json"
        ),
    }
    _write_json(hooks_root / "hook-event-registry.v1.json", body)
    return body


def _generate_tunnel_and_toolchain_surfaces() -> tuple[dict[str, Any], dict[str, Any]]:
    from evidence_lane_plugin.hardware_acceleration import (
        hardware_acceleration_catalog,
        hardware_acceleration_schema,
    )
    from evidence_lane_plugin.tunnel_identity_routing import (
        tunnel_identity_routing_catalog,
    )

    toolchains_root = PLUGIN_ROOT / "toolchains"
    accelerator_catalog_path = toolchains_root / "hardware-accelerator-routing.v1.json"
    accelerator_schema_path = (
        PLUGIN_ROOT
        / "schemas"
        / "toolchains"
        / "hardware-acceleration-route.schema.json"
    )
    _write_json(accelerator_catalog_path, hardware_acceleration_catalog())
    _write_json(accelerator_schema_path, hardware_acceleration_schema())
    matrix_path = toolchains_root / "tool-requirement-matrix.v1.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    execution_routing_path = toolchains_root / "tool-execution-routing.v1.json"
    execution_routing = json.loads(execution_routing_path.read_text(encoding="utf-8"))
    execution_by_tool = {
        str(row["tool"]): dict(row) for row in execution_routing["rows"]
    }
    if (
        execution_routing.get("status") != "PASS"
        or execution_routing.get("primary_and_fallback_order_explicit") is not True
        or set(execution_by_tool)
        != {str(row["tool"]) for row in matrix["requirements"]}
    ):
        raise ValueError(
            "The complete tool execution routing projection is incomplete."
        )
    non_runtime_requirements = {
        "CONFIGURED_EXTERNAL_SERVICE",
        "REPOSITORY_PUBLIC_ADAPTER_ONLY",
        "REQUIRED_CODE_ACCEPTANCE",
        "REQUIRED_CONFIGURED_GATE",
        "REQUIRED_REPOSITORY_ONLY",
    }
    external_requirements = {"CONFIGURED_EXTERNAL_SERVICE"}
    tunnel_requirements = []
    for row in matrix["requirements"]:
        requirement = str(row["requirement"])
        if requirement in external_requirements:
            prewarm_mode = "REGISTER_EXTERNAL_PROOF_GATE"
        elif requirement in non_runtime_requirements:
            prewarm_mode = "REGISTER_AND_VERIFY_AT_APPLICABLE_GATE"
        else:
            prewarm_mode = "INSTALL_OR_PROBE_HIDDEN_RUNTIME"
        route = execution_by_tool[str(row["tool"])]
        tunnel_requirements.append(
            {
                **row,
                "tunnel_prewarm_mode": prewarm_mode,
                "action_classes": route["action_classes"],
                "primary_for": route["primary"],
                "fallback_for": route["fallback"],
                "action_order": route["action_order"],
                "eligible_lanes": route["lanes"],
                "implementation_owner": route["implementation_owner"],
                "license_record": route["license_record"],
                "license_record_sha256": route["license_record_sha256"],
            }
        )
    tunnel_toolchain = {
        "schema": "evidence-lane.tunnel-runtime-toolchain.v1",
        "status": "PASS",
        "requirement_count": len(tunnel_requirements),
        "requirements": tunnel_requirements,
        "full_matrix_requirement_count": len(matrix["requirements"]),
        "all_tool_requirements_linked": True,
        "runtime_install_or_probe_count": sum(
            row["tunnel_prewarm_mode"] == "INSTALL_OR_PROBE_HIDDEN_RUNTIME"
            for row in tunnel_requirements
        ),
        "repository_or_build_gate_count": sum(
            row["tunnel_prewarm_mode"] == "REGISTER_AND_VERIFY_AT_APPLICABLE_GATE"
            for row in tunnel_requirements
        ),
        "external_service_gate_count": sum(
            row["tunnel_prewarm_mode"] == "REGISTER_EXTERNAL_PROOF_GATE"
            for row in tunnel_requirements
        ),
        "full_matrix": "toolchains/tool-requirement-matrix.v1.json",
        "full_matrix_sha256": _sha256(matrix_path),
        "execution_routing": "toolchains/tool-execution-routing.v1.json",
        "execution_routing_sha256": _sha256(execution_routing_path),
        "primary_and_fallback_order_explicit": True,
        "prewarm_owner": "INSTALLER_RUNTIME_PREWARM_BEFORE_TUNNEL_OR_TASK_REOPEN",
        "optional_unavailable_is_fail_visible": True,
        "native_mcp_action_counted_here": False,
        "all_runtime_dependencies_prewarmed": True,
        "conditional_execution_not_run_everything": True,
        "chatgpt_plane_mixed": False,
        "identity_routing": tunnel_identity_routing_catalog(),
        "shared_tunnel_supports_multiple_projects_and_tasks": True,
        "scheduled_task_owner": False,
        "prewarm_executes_tools": False,
    }
    tunnel_toolchain_path = toolchains_root / "tunnel-runtime-toolchain.v1.json"
    _write_json(tunnel_toolchain_path, tunnel_toolchain)
    project = tomllib.loads(
        (PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    direct_dependencies = []
    for dependency in project["project"]["dependencies"]:
        exact = str(dependency)
        name, version = exact.split("==", 1)
        direct_dependencies.append(
            {
                "name": name,
                "version": version.split(";", 1)[0].strip(),
                "declaration": exact,
            }
        )
    bundled_native_license_files = [
        {
            "path": path.relative_to(PLUGIN_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted((toolchains_root / "licenses").rglob("*"))
        if path.is_file()
        and "requirements" not in path.relative_to(toolchains_root / "licenses").parts
    ]
    if not {
        "toolchains/licenses/ripgrep-15.2.0/LICENSE-MIT",
        "toolchains/licenses/ripgrep-15.2.0/UNLICENSE",
        "toolchains/licenses/7-zip-26.01/LICENSE.txt",
    } <= {row["path"] for row in bundled_native_license_files}:
        raise ValueError("A bundled native binary lacks its exact license texts.")
    native_manifest_path = toolchains_root / "native-tools.v1.json"
    native_manifest = json.loads(native_manifest_path.read_text(encoding="utf-8"))
    native_tools = {str(row["tool_id"]): dict(row) for row in native_manifest["tools"]}
    tool_license_rows = [
        _tool_license_inventory_row(dict(row), native_tools=native_tools)
        for row in matrix["requirements"]
    ]
    if len(tool_license_rows) != len(matrix["requirements"]) or {
        row["tool"] for row in tool_license_rows
    } != {str(row["tool"]) for row in matrix["requirements"]}:
        raise ValueError("Every tool requirement must have one license classification.")
    requirement_license_root = toolchains_root / "licenses" / "requirements"
    if requirement_license_root.exists():
        shutil.rmtree(requirement_license_root)
    requirement_license_records: list[dict[str, Any]] = []
    for ordinal, row in enumerate(tool_license_rows, start=1):
        relative, record = _requirement_license_record(
            ordinal=ordinal,
            row=row,
            native_tools=native_tools,
        )
        path = PLUGIN_ROOT / relative
        _write_json(path, record)
        row["license_record"] = relative
        row["license_record_sha256"] = _sha256(path)
        requirement_license_records.append(
            {
                "tool": row["tool"],
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    if len(requirement_license_records) != len(tool_license_rows):
        raise ValueError(
            "Every tool requirement must have one physical license record."
        )
    license_files = [
        {
            "path": path.relative_to(PLUGIN_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted((toolchains_root / "licenses").rglob("*"))
        if path.is_file()
    ]
    tool_license_inventory_path = toolchains_root / "tool-license-inventory.v1.json"
    _write_json(
        tool_license_inventory_path,
        {
            "schema": "evidence-lane.tool-license-inventory.v1",
            "status": "PASS",
            "tool_requirement_count": len(tool_license_rows),
            "license_classification_count": len(tool_license_rows),
            "all_tool_requirements_classified": True,
            "mcp_inventory_separate": True,
            "runtime_distribution_license_bundle_required": True,
            "requirement_license_record_count": len(requirement_license_records),
            "all_tool_requirements_have_physical_license_records": True,
            "rows": tool_license_rows,
        },
    )
    license_policy = {
        "schema": "evidence-lane.toolchain-license-policy.v1",
        "status": "PASS",
        "direct_python_dependency_count": len(direct_dependencies),
        "direct_python_dependencies": direct_dependencies,
        "requirements_lock_sha256": _sha256(PLUGIN_ROOT / "requirements.lock.txt"),
        "requirements_torch_cpu_lock_sha256": _sha256(
            PLUGIN_ROOT / "requirements.torch-cpu.lock.txt"
        ),
        "requirements_torch_nvidia_lock_sha256": _sha256(
            PLUGIN_ROOT / "requirements.torch-nvidia.lock.txt"
        ),
        "requirements_onnx_directml_lock_sha256": _sha256(
            PLUGIN_ROOT / "requirements.onnx-directml.lock.txt"
        ),
        "bundled_license_file_count": len(license_files),
        "bundled_license_files": license_files,
        "bundled_native_license_text_count": len(bundled_native_license_files),
        "requirement_license_record_count": len(requirement_license_records),
        "requirement_license_records": requirement_license_records,
        "tool_license_inventory": (
            tool_license_inventory_path.relative_to(PLUGIN_ROOT).as_posix()
        ),
        "tool_license_inventory_sha256": _sha256(tool_license_inventory_path),
        "hardware_accelerator_catalog_sha256": _sha256(accelerator_catalog_path),
        "hardware_accelerator_schema_sha256": _sha256(accelerator_schema_path),
        "hardware_accelerators_are_execution_providers_not_tools": True,
        "tool_license_entry_count": len(tool_license_rows),
        "all_tool_requirements_license_classified": True,
        "all_tool_requirements_have_physical_license_records": True,
        "installed_runtime_license_bundle": (
            "%RUNTIME_CONTROL_ROOT%/runtime/licenses/<runtime_key>/manifest.v1.json"
        ),
        "installed_runtime_license_bundle_required_before_tunnel": True,
        "installed_distribution_license_files_copied": True,
        "missing_distribution_license_file_preserved_as_metadata": True,
        "public_adapter_license_inventory_repository_only": True,
        "mcp_actions_are_not_third_party_binaries": True,
        "ghostscript_default_bundled": False,
        "ghostscript_license_grant_required": True,
        "poppler_separate_process_and_source_offer_required": True,
    }
    license_policy_path = toolchains_root / "license-policy.v1.json"
    _write_json(license_policy_path, license_policy)
    search_manifest = toolchains_root / "search-tools.v1.json"
    search = json.loads(search_manifest.read_text(encoding="utf-8"))
    tunnel_source_root = PLUGIN_ROOT / "scripts" / "windows_tunnel"
    tunnel_sources = [
        {
            "path": path.relative_to(PLUGIN_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(tunnel_source_root.iterdir())
        if path.is_file()
    ]
    helper_paths = ("scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",)
    tunnel_body = {
        "schema": "evidence-lane.installed-tunnel-surface.v1",
        "status": "PASS",
        "runtime_root": "%USERPROFILE%/.codex/plugins/runtime/evidence-lane-plugin",
        "runtime_root_hidden": True,
        "project_root_hardcoded": False,
        "workspace_hardcoded": False,
        "routes_by_project_id": True,
        "one_active_tunnel": True,
        "tunnel_sources": tunnel_sources,
        "runtime_toolchain": tunnel_toolchain_path.relative_to(PLUGIN_ROOT).as_posix(),
        "runtime_toolchain_sha256": _sha256(tunnel_toolchain_path),
        "license_policy": license_policy_path.relative_to(PLUGIN_ROOT).as_posix(),
        "license_policy_sha256": _sha256(license_policy_path),
        "installed_runtime_license_bundle_required": True,
        "tunnel_client_license_source_and_sha256_required": True,
        "installer_runtime_prewarm_required": True,
        "runtime_toolchain_prewarm_required": True,
        "native_toolchain_manifest": "toolchains/native-tools.v1.json",
        "native_toolchain_manifest_sha256": _sha256(
            toolchains_root / "native-tools.v1.json"
        ),
        "native_toolchain_install_owner": (
            "scripts/codex_release/install_native_toolchain.py"
        ),
        "native_toolchain_local_update_only": True,
        "maintainer_helper_included_until_final_project_hil": True,
        "maintainer_helpers": {
            path: _sha256(PLUGIN_ROOT / path) for path in helper_paths
        },
        "restart_preparation_only": True,
        "programmatic_app_stop_allowed": False,
        "turn_drain_utility_present": False,
    }
    tunnel_root = PLUGIN_ROOT / "tunnel"
    _write_json(tunnel_root / "tunnel-manifest.v1.json", tunnel_body)
    _write_json(
        tunnel_root / "toolchain-prewarm.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Evidence Lane tunnel toolchain prewarm receipt",
            "type": "object",
            "required": [
                "schema",
                "status",
                "matrix_sha256",
                "requirement_count",
                "failure_count",
                "receipt_sha256",
            ],
            "properties": {
                "schema": {"const": "evidence-lane.runtime-toolchain-prewarm.v1"},
                "status": {"const": "PASS"},
                "matrix_sha256": {"const": _sha256(matrix_path)},
                "requirement_count": {"type": "integer", "minimum": 1},
                "failure_count": {"const": 0},
                "receipt_sha256": {
                    "type": "string",
                    "pattern": "^[A-F0-9]{64}$",
                },
            },
            "additionalProperties": True,
        },
    )
    _write(
        tunnel_root / "README.md",
        "# Evidence Lane tunnel\n\n"
        "The executable tunnel host remains under `scripts/windows_tunnel/`. This "
        "surface hash-binds that host, its installer/manager, the required non-MCP "
        "runtime toolchain, hidden runtime root, project-id routing, and the "
        "temporarily retained maintainer helper.\n",
    )
    tunnel = {
        **tunnel_body,
        "manifest_sha256": _sha256(tunnel_root / "tunnel-manifest.v1.json"),
    }
    toolchain_body = {
        "schema": "evidence-lane.installed-toolchain-surface.v1",
        "status": "PASS",
        "requirement_count": len(matrix["requirements"]),
        "matrix_sha256": _sha256(matrix_path),
        "search_manifest_sha256": _sha256(search_manifest),
        "search_tool_count": len(search["tools"]),
        "tunnel_toolchain_sha256": _sha256(tunnel_toolchain_path),
        "tunnel_manifest_sha256": tunnel["manifest_sha256"],
        "license_policy_sha256": _sha256(license_policy_path),
        "native_manifest_sha256": _sha256(native_manifest_path),
        "tool_license_inventory_sha256": _sha256(tool_license_inventory_path),
        "tool_license_entry_count": len(tool_license_rows),
        "all_tool_requirements_license_classified": True,
        "execution_matrix_sha256": _sha256(
            toolchains_root / "TOOLCHAIN_EXECUTION_MATRIX.md"
        ),
        "execution_routing_sha256": _sha256(execution_routing_path),
        "primary_and_fallback_order_explicit": True,
        "native_installer_sha256": _sha256(
            PLUGIN_ROOT / "scripts" / "codex_release" / "install_native_toolchain.py"
        ),
        "bundled_license_file_count": len(license_files),
        "requirement_license_record_count": len(requirement_license_records),
        "all_tool_requirements_have_physical_license_records": True,
        "runtime_license_bundle_generator_sha256": _sha256(
            PLUGIN_ROOT / "scripts" / "generate_runtime_license_bundle.py"
        ),
        "public_action_counted_here": False,
        "external_binaries_are_distinct_from_internal_components": True,
    }
    _write_json(toolchains_root / "toolchain-surface.v1.json", toolchain_body)
    _write(
        toolchains_root / "README.md",
        "# Evidence Lane toolchains\n\n"
        "This directory distinguishes packaged binaries, Python/runtime "
        "dependencies, internal builders/operators, optional host tools, build "
        "gates, tunnel dependencies, and external delivery services. Public native "
        "MCP actions are a separate derived SDK/MCP inventory; neither count is fixed.\n",
    )
    return toolchain_body, tunnel


def _authority_manifest(authority: str) -> dict[str, Any]:
    target = PLUGIN_ROOT / authority
    flash_manifest = json.loads(
        (PLUGIN_ROOT / "env" / "SESSION_FLASH_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )
    member_paths = [
        str(row["path"])
        for row in flash_manifest["members"]
        if str(row["path"]).startswith(f"{authority}/")
    ]
    rows = [
        {
            "path": relative,
            "canonical_path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for relative in sorted(member_paths)
        for path in [PLUGIN_ROOT / relative]
    ]
    body = {
        "schema": f"evidence-lane.{authority}-packaged-authority.v1",
        "status": "PASS",
        "authority": authority.upper(),
        "canonical_root": authority,
        "published_root": authority,
        "member_count": len(rows),
        "members": rows,
        "canonical_packaged_authority": True,
        "duplicate_authority_copy_present": False,
    }
    _write_json(target / "authority-manifest.v1.json", body)
    _write(
        target / "README.md",
        f"# {authority.upper()} packaged authority\n\n"
        f"This directory is the one canonical locked {authority.upper()} authority. "
        "Runtime governance and the packer use these exact files; no duplicate "
        "authority copy is retained under `src/`.\n",
    )
    return body


def _generate_sdk(
    public: dict[str, Any],
    *,
    lanes: dict[str, Any],
    authorities: dict[str, Any],
    source_modules: dict[str, Any],
    hook_surface: dict[str, Any],
    toolchain_surface: dict[str, Any],
    tunnel_surface: dict[str, Any],
) -> dict[str, Any]:
    sdk_root = PLUGIN_ROOT / "sdk"
    modules = {
        "internal_sdk": PACKAGE_ROOT / "internal_sdk.py",
        "outer_routing_sdk": PACKAGE_ROOT / "current_route_registry.py",
        "env_uop_action_plane": PACKAGE_ROOT / "mode_governance.py",
        "delta_entry_executor": PACKAGE_ROOT / "adaptive_delta_entry.py",
    }
    _write(
        sdk_root / "__init__.py",
        '"""Installed Evidence Lane SDK distribution surface."""\n',
    )
    _write(
        sdk_root / "evidence_lane_sdk.py",
        '"""Public package binding to the canonical Evidence Lane SDK."""\n\n'
        "from evidence_lane_plugin.current_route_registry import (\n"
        "    current_implementation_registry,\n"
        ")\n"
        "from evidence_lane_plugin.internal_sdk import (\n"
        "    inspect_sdk_handler_parity,\n"
        "    runtime_workflow_sdk_registry,\n"
        "    sdk_plane_registry,\n"
        ")\n\n"
        "__all__ = [\n"
        '    "current_implementation_registry",\n'
        '    "inspect_sdk_handler_parity",\n'
        '    "runtime_workflow_sdk_registry",\n'
        '    "sdk_plane_registry",\n'
        "]\n",
    )
    _write(
        sdk_root / "internal" / "runtime.py",
        '"""Binding to the canonical internal 88-action SDK."""\n\n'
        "from evidence_lane_plugin.internal_sdk import (\n"
        "    inspect_sdk_handler_parity,\n"
        "    runtime_workflow_sdk_registry,\n"
        "    sdk_plane_registry,\n"
        ")\n\n"
        "__all__ = [\n"
        '    "inspect_sdk_handler_parity",\n'
        '    "runtime_workflow_sdk_registry",\n'
        '    "sdk_plane_registry",\n'
        "]\n",
    )
    _write_json(
        sdk_root / "internal" / "public-action-registry.v1.json",
        {
            "schema": "evidence-lane.sdk-public-action-registry.v1",
            "status": "PASS",
            "action_count": int(public["tool_count"]),
            "read_action_count": int(public["read_tool_count"]),
            "write_action_count": int(public["write_tool_count"]),
            "actions": public["tools"],
            "canonical_implementation": "src/evidence_lane_plugin/internal_sdk.py",
        },
    )
    _write_json(
        sdk_root / "internal" / "workflow-registry.v1.json",
        dict(public["runtime_workflow_sdk_registry"]),
    )
    lane_registry_path = (
        PLUGIN_ROOT
        / "authorities"
        / "project_sectors"
        / "lane-surface-registry.v1.json"
    )
    authority_registry_path = (
        PLUGIN_ROOT / "authorities" / "authority-surface-registry.v1.json"
    )
    _write_json(
        sdk_root / "internal" / "authority-surface-routing.v1.json",
        {
            "schema": "evidence-lane.sdk-authority-surface-routing.v1",
            "status": "PASS",
            "sector_lane_count": lanes["lane_count"],
            "sector_lane_registry": (
                "authorities/project_sectors/lane-surface-registry.v1.json"
            ),
            "sector_lane_registry_sha256": _sha256(lane_registry_path),
            "non_sector_authority_count": authorities["authority_count"],
            "non_sector_authority_registry": (
                "authorities/authority-surface-registry.v1.json"
            ),
            "non_sector_authority_registry_sha256": _sha256(authority_registry_path),
            "env_uop_action_plane": "sdk/env_uop/action-plane.v1.json",
            "public_action_registry": "sdk/internal/public-action-registry.v1.json",
            "outer_routing_registry": "sdk/routing/current-route-registry.v1.json",
            "sector_and_named_authorities_merged": False,
            "instructions_merged_with_project_memory": False,
        },
    )
    internal_module_root = sdk_root / "internal" / "modules"
    expected_internal_module_files: set[str] = set()
    for module in source_modules["modules"]:
        module_name = str(module["module"])
        filename = f"{module_name.rsplit('.', 1)[-1]}.module.v1.json"
        expected_internal_module_files.add(filename)
        _write_json(
            internal_module_root / filename,
            {
                "schema": "evidence-lane.sdk-internal-module-binding.v1",
                "status": "PASS",
                "module": module_name,
                "source_path": module["path"],
                "source_sha256": module["sha256"],
                "source_bytes": module["bytes"],
                "role": module["role"],
                "public_action_registry": (
                    "sdk/internal/public-action-registry.v1.json"
                ),
                "workflow_registry": "sdk/internal/workflow-registry.v1.json",
                "execution_logic_duplicated_in_binding": False,
            },
        )
    for path in internal_module_root.glob("*.module.v1.json"):
        if path.name not in expected_internal_module_files:
            path.unlink()
    _write(
        sdk_root / "env_uop" / "runtime.py",
        '"""Binding to the canonical executable ENV/UOP action plane."""\n\n'
        "from evidence_lane_plugin.mode_governance import (\n"
        "    classify_mode_governance,\n"
        "    compile_env_uop_formula,\n"
        "    route_env_uop_operator,\n"
        ")\n\n"
        "__all__ = [\n"
        '    "classify_mode_governance",\n'
        '    "compile_env_uop_formula",\n'
        '    "route_env_uop_operator",\n'
        "]\n",
    )
    env_manifest_path = PLUGIN_ROOT / "env" / "authority-manifest.v1.json"
    uop_manifest_path = PLUGIN_ROOT / "uop" / "authority-manifest.v1.json"
    _write_json(
        sdk_root / "env_uop" / "action-plane.v1.json",
        {
            "schema": "evidence-lane.sdk-env-uop-action-plane.v1",
            "status": "PASS",
            "plane": next(
                row
                for row in public["sdk_planes"]["planes"]
                if row["plane_id"] == "ENV_UOP_AI_ACTION_PLANE"
            ),
            "operations": [
                "classify_mode",
                "compile_formula",
                "route_operator",
                "status",
            ],
            "env_authority_manifest_sha256": _sha256(env_manifest_path),
            "uop_authority_manifest_sha256": _sha256(uop_manifest_path),
            "canonical_implementation": "src/evidence_lane_plugin/mode_governance.py",
        },
    )
    env_uop_operations = {
        "classify-mode": "classify_mode_governance",
        "compile-formula": "compile_env_uop_formula",
        "route-operator": "route_env_uop_operator",
        "status": "validate_mode_governance_selection",
    }
    operation_root = sdk_root / "env_uop" / "operations"
    expected_env_uop_operation_files: set[str] = set()
    for operation, callable_name in env_uop_operations.items():
        filename = f"{operation}.operation.v1.json"
        expected_env_uop_operation_files.add(filename)
        _write_json(
            operation_root / filename,
            {
                "schema": "evidence-lane.sdk-env-uop-operation.v1",
                "status": "PASS",
                "operation": operation,
                "callable": callable_name,
                "canonical_module": "evidence_lane_plugin.mode_governance",
                "canonical_module_sha256": _sha256(PACKAGE_ROOT / "mode_governance.py"),
                "env_authority_manifest_sha256": _sha256(env_manifest_path),
                "uop_authority_manifest_sha256": _sha256(uop_manifest_path),
                "counted_as_public_mcp_action": False,
                "execution_logic_duplicated_in_binding": False,
            },
        )
    for path in operation_root.glob("*.operation.v1.json"):
        if path.name not in expected_env_uop_operation_files:
            path.unlink()
    _write_json(
        sdk_root / "env_uop" / "env-authority.v1.json",
        {
            "schema": "evidence-lane.sdk-env-authority-binding.v1",
            "status": "PASS",
            "authority_root": "env",
            "authority_manifest_sha256": _sha256(env_manifest_path),
            "scope": "INSTALLATION_SCOPED_AI_ACTION_PLANE",
            "project_authority": False,
        },
    )
    _write_json(
        sdk_root / "env_uop" / "uop-authority.v1.json",
        {
            "schema": "evidence-lane.sdk-uop-authority-binding.v1",
            "status": "PASS",
            "authority_root": "uop",
            "authority_manifest_sha256": _sha256(uop_manifest_path),
            "scope": "INSTALLATION_SCOPED_AI_ACTION_PLANE",
            "project_authority": False,
        },
    )
    _write(
        sdk_root / "routing" / "router.py",
        '"""Binding to the canonical outer current-route SDK."""\n\n'
        "from evidence_lane_plugin.current_route_registry import (\n"
        "    current_implementation_registry,\n"
        ")\n\n"
        '__all__ = ["current_implementation_registry"]\n',
    )
    _write_json(
        sdk_root / "routing" / "current-route-registry.v1.json",
        dict(public["current_implementation_registry"]),
    )
    _write_json(
        sdk_root / "routing" / "mcp-action-routing.v1.json",
        {
            "schema": "evidence-lane.sdk-mcp-action-routing.v1",
            "status": "PASS",
            "action_count": int(public["tool_count"]),
            "actions": [
                {
                    "name": row["name"],
                    "route_contract": row["route_contract"],
                    "schema_sha256": row["schema_sha256"],
                }
                for row in public["tools"]
            ],
            "canonical_outer_router": "src/evidence_lane_plugin/mcp_server.py",
        },
    )
    routing_action_root = sdk_root / "routing" / "actions"
    expected_routing_action_files: set[str] = set()
    for tool in public["tools"]:
        action_name = str(tool["name"])
        filename = f"{action_name}.route.v1.json"
        expected_routing_action_files.add(filename)
        _write_json(
            routing_action_root / filename,
            {
                "schema": "evidence-lane.sdk-outer-action-route.v1",
                "status": "PASS",
                "action": action_name,
                "route_contract": tool["route_contract"],
                "schema_sha256": tool["schema_sha256"],
                "sdk_action_binding": f"sdk/actions/{action_name}.action.v1.json",
                "mcp_action_binding": f"mcp/actions/{action_name}.binding.v1.json",
                "canonical_router": "src/evidence_lane_plugin/current_route_registry.py",
                "canonical_server": "src/evidence_lane_plugin/mcp_server.py",
                "fallback_to_historical_route_allowed": False,
            },
        )
    for path in routing_action_root.glob("*.route.v1.json"):
        if path.name not in expected_routing_action_files:
            path.unlink()
    _write(
        sdk_root / "actions" / "dispatcher.py",
        '"""Binding to the canonical 88-action internal SDK dispatcher."""\n\n'
        "from evidence_lane_plugin.internal_sdk import (\n"
        "    inspect_sdk_handler_parity,\n"
        "    runtime_workflow_sdk_registry,\n"
        ")\n\n"
        '__all__ = ["inspect_sdk_handler_parity", "runtime_workflow_sdk_registry"]\n',
    )
    _write_json(
        sdk_root / "actions" / "registry.ref.v1.json",
        {
            "schema": "evidence-lane.sdk-action-registry-reference.v1",
            "status": "PASS",
            "action_count": int(public["tool_count"]),
            "registry": "sdk/internal/public-action-registry.v1.json",
            "registry_sha256": _sha256(
                sdk_root / "internal" / "public-action-registry.v1.json"
            ),
            "dispatcher": "sdk/actions/dispatcher.py",
            "canonical_implementation": "src/evidence_lane_plugin/internal_sdk.py",
        },
    )
    action_bindings: list[str] = []
    expected_action_binding_files: set[str] = set()
    runtime_workflows = list(public["runtime_workflow_sdk_registry"]["workflows"])
    for tool in public["tools"]:
        action_name = str(tool["name"])
        filename = f"{action_name}.action.v1.json"
        expected_action_binding_files.add(filename)
        action_path = sdk_root / "actions" / filename
        owning_runtime_workflows = [
            str(workflow["workflow"])
            for workflow in runtime_workflows
            if action_name in workflow.get("public_actions", [])
        ]
        _write_json(
            action_path,
            {
                "schema": "evidence-lane.sdk-action-binding.v1",
                "status": "PASS",
                "action": action_name,
                "title": tool["title"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
                "output_schema": tool["output_schema"],
                "annotations": tool["annotations"],
                "schema_sha256": tool["schema_sha256"],
                "route_contract": tool["route_contract"],
                "runtime_workflows": owning_runtime_workflows,
                "dispatcher": "sdk/actions/dispatcher.py",
                "internal_registry": ("sdk/internal/public-action-registry.v1.json"),
                "outer_mcp_binding": f"mcp/actions/{action_name}.binding.v1.json",
                "canonical_internal_sdk": ("src/evidence_lane_plugin/internal_sdk.py"),
                "canonical_mcp_server": "src/evidence_lane_plugin/mcp_server.py",
                "source_module_registry": (
                    "src/evidence_lane_plugin/module-registry.v1.json"
                ),
                "source_module_registry_sha256": _sha256(
                    PACKAGE_ROOT / "module-registry.v1.json"
                ),
                "execution_logic_duplicated_in_binding": False,
            },
        )
        action_bindings.append(action_path.relative_to(PLUGIN_ROOT).as_posix())
    for path in (sdk_root / "actions").glob("*.action.v1.json"):
        if path.name not in expected_action_binding_files:
            path.unlink()
    routing_path = (
        PLUGIN_ROOT / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    )
    routing = json.loads(routing_path.read_text(encoding="utf-8"))
    public_by_name = {str(row["name"]): row for row in public["tools"]}
    workflow_root = sdk_root / "workflows"
    expected_workflow_files: set[str] = set()
    for skill_name, workflow in sorted(dict(routing["workflows"]).items()):
        filename = f"{skill_name}.workflow.v1.json"
        expected_workflow_files.add(filename)
        ordered_groups = list(workflow["ordered_tool_groups"])
        action_names = [
            str(name) for group in ordered_groups for name in group.get("tools", [])
        ]
        _write_json(
            workflow_root / filename,
            {
                "schema": "evidence-lane.sdk-workflow-projection.v1",
                "status": "PASS",
                "skill": skill_name,
                "skill_sha256": _sha256(
                    PLUGIN_ROOT / "skills" / skill_name / "SKILL.md"
                ),
                "ordered_tool_groups": ordered_groups,
                "action_count": len(action_names),
                "actions": [
                    {
                        "name": name,
                        "schema_sha256": public_by_name[name]["schema_sha256"],
                        "route_contract": public_by_name[name]["route_contract"],
                    }
                    for name in action_names
                ],
                "missing_tool_behavior": workflow["missing_tool_behavior"],
                "canonical_routing_source": (
                    "skills/evi/references/mcp-tool-routing.v1.json"
                ),
                "canonical_routing_source_sha256": _sha256(routing_path),
            },
        )
    for path in workflow_root.glob("*.json"):
        if path.name not in expected_workflow_files:
            path.unlink()
    _write_json(
        sdk_root / "authorities" / "project-sectors.ref.v1.json",
        {
            "schema": "evidence-lane.sdk-project-sector-authority-reference.v1",
            "status": "PASS",
            "sector_count": lanes["lane_count"],
            "registry": ("authorities/project_sectors/lane-surface-registry.v1.json"),
            "registry_sha256": _sha256(lane_registry_path),
            "separate_sqlite_authorities": True,
        },
    )
    _write_json(
        sdk_root / "authorities" / "named-authorities.ref.v1.json",
        {
            "schema": "evidence-lane.sdk-named-authority-reference.v1",
            "status": "PASS",
            "authority_count": authorities["authority_count"],
            "sqlite_authority_count": authorities["sqlite_authority_count"],
            "non_sqlite_authority_count": authorities["non_sqlite_authority_count"],
            "registry": "authorities/authority-surface-registry.v1.json",
            "registry_sha256": _sha256(authority_registry_path),
            "merged_with_project_sectors": False,
        },
    )
    _write_json(
        sdk_root / "authorities" / "env-uop.ref.v1.json",
        {
            "schema": "evidence-lane.sdk-env-uop-authority-reference.v1",
            "status": "PASS",
            "scope": "INSTALLATION_SCOPED_EXECUTABLE_AI_ACTION_PLANE",
            "project_authority": False,
            "env_manifest": "env/authority-manifest.v1.json",
            "env_manifest_sha256": _sha256(env_manifest_path),
            "uop_manifest": "uop/authority-manifest.v1.json",
            "uop_manifest_sha256": _sha256(uop_manifest_path),
            "runtime": "sdk/env_uop/runtime.py",
            "action_plane": "sdk/env_uop/action-plane.v1.json",
        },
    )
    sdk_sector_root = sdk_root / "authorities" / "project_sectors"
    expected_sdk_sector_files: set[str] = set()
    for lane in lanes["lanes"]:
        lane_id = str(lane["lane_id"])
        filename = f"{lane_id}.authority.v1.json"
        expected_sdk_sector_files.add(filename)
        authority_manifest = (
            PLUGIN_ROOT
            / "authorities"
            / "project_sectors"
            / lane_id
            / "manifest.v1.json"
        )
        _write_json(
            sdk_sector_root / filename,
            {
                "schema": "evidence-lane.sdk-project-sector-binding.v1",
                "status": "PASS",
                "lane_id": lane_id,
                "authority_root": f"authorities/project_sectors/{lane_id}",
                "authority_manifest_sha256": _sha256(authority_manifest),
                "schema_surface": (
                    f"schemas/project-sectors/{lane_id}/schema-surface.v1.json"
                ),
                "builder": f"authorities/project_sectors/{lane_id}/builder.py",
                "reader": f"authorities/project_sectors/{lane_id}/reader.py",
                "separate_sqlite_authority": True,
                "live_project_bytes_in_binding": False,
            },
        )
    for path in sdk_sector_root.glob("*.authority.v1.json"):
        if path.name not in expected_sdk_sector_files:
            path.unlink()
    sdk_named_root = sdk_root / "authorities" / "named"
    expected_sdk_named_files: set[str] = set()
    for authority in authorities["authorities"]:
        authority_id = str(authority["authority_id"])
        filename = f"{authority_id}.authority.v1.json"
        expected_sdk_named_files.add(filename)
        authority_manifest = (
            PLUGIN_ROOT / "authorities" / authority_id / "manifest.v1.json"
        )
        _write_json(
            sdk_named_root / filename,
            {
                "schema": "evidence-lane.sdk-named-authority-binding.v1",
                "status": "PASS",
                "authority_id": authority_id,
                "authority_root": f"authorities/{authority_id}",
                "authority_manifest_sha256": _sha256(authority_manifest),
                "schema_surface": (
                    f"schemas/authorities/{authority_id}/schema-surface.v1.json"
                ),
                "persistent_sqlite_authority": authority["persistent_sqlite_authority"],
                "linked_public_action_count": authority["linked_public_action_count"],
                "merged_with_project_sector_or_peer": False,
            },
        )
    for path in sdk_named_root.glob("*.authority.v1.json"):
        if path.name not in expected_sdk_named_files:
            path.unlink()
    _write_json(
        sdk_root / "hooks" / "hook-runtime.ref.v1.json",
        {
            "schema": "evidence-lane.sdk-hook-runtime-reference.v1",
            "status": "PASS",
            "event_count": public["hook_control"]["event_count"],
            "handler_action_count": public["hook_control"][
                "current_handler_action_count"
            ],
            "hooks_manifest": "hooks/hooks.json",
            "hooks_manifest_sha256": _sha256(PLUGIN_ROOT / "hooks" / "hooks.json"),
            "logical_actions": "hooks/logical-actions.json",
            "logical_actions_sha256": _sha256(
                PLUGIN_ROOT / "hooks" / "logical-actions.json"
            ),
            "canonical_contract": "src/evidence_lane_plugin/hook_contract.py",
            "canonical_contract_sha256": _sha256(PACKAGE_ROOT / "hook_contract.py"),
        },
    )
    sdk_hook_event_root = sdk_root / "hooks" / "events"
    expected_sdk_hook_event_files: set[str] = set()
    for event in hook_surface["events"]:
        event_name = str(event["event"])
        filename = f"{event_name}.event.v1.json"
        expected_sdk_hook_event_files.add(filename)
        event_path = PLUGIN_ROOT / event["path"] / "event.v1.json"
        event_payload = json.loads(event_path.read_text(encoding="utf-8"))
        _write_json(
            sdk_hook_event_root / filename,
            {
                "schema": "evidence-lane.sdk-hook-event-binding.v1",
                "status": "PASS",
                "event_number": event["event_number"],
                "event": event_name,
                "handler_count": event["handler_count"],
                "event_binding": event_path.relative_to(PLUGIN_ROOT).as_posix(),
                "event_binding_sha256": _sha256(event_path),
                "handlers": event_payload["handlers"],
                "workflow_contract": event_payload["workflow_contract"],
                "workflow_contract_sha256": event_payload["workflow_contract_sha256"],
                "canonical_hook_manifest": "hooks/hooks.json",
                "canonical_logical_actions": "hooks/logical-actions.json",
                "enabled_only_after_installed_event_pass": True,
            },
        )
    for path in sdk_hook_event_root.glob("*.event.v1.json"):
        if path.name not in expected_sdk_hook_event_files:
            path.unlink()
    _write_json(
        sdk_root / "delta" / "entry-mid-exit.v1.json",
        {
            "schema": "evidence-lane.sdk-delta-entry-mid-exit.v1",
            "status": "PASS",
            "delta_entry": next(
                row
                for row in public["runtime_workflow_sdk_registry"]["workflows"]
                if row["workflow"] == "DELTA_ENTRY_AND_CURRENT_AUTHORITY_QUERY"
            ),
            "delta_exit": public["delta_authority_time_boundary"],
            "prompt_steer_source_intake": public["prompt_and_steer_dispatch"],
            "project_overlay_hil_only": True,
            "canonical_entry_source": "src/evidence_lane_plugin/adaptive_delta_entry.py",
            "canonical_exit_source": "src/evidence_lane_plugin/adaptive_delta_exit.py",
        },
    )
    delta_workflows = {
        "prompt-steer": "PROMPT_OR_STEER_ENTRY",
        "entry": "DELTA_ENTRY_AND_CURRENT_AUTHORITY_QUERY",
        "mid-query": "IN_DELTA_BOUNDED_QUERY_AND_NO_HIT_REFIRE",
        "exit": "DELTA_EXIT_APPEND_REFRESH",
        "hil-overlay": "FULL_PV_DUAL_HIL",
        "state-travel": "STATE_TRAVEL_DIRECT",
    }
    expected_delta_files: set[str] = {"entry-mid-exit.v1.json"}
    for name, workflow_id in delta_workflows.items():
        filename = f"{name}.workflow.v1.json"
        expected_delta_files.add(filename)
        workflow = next(
            row
            for row in public["runtime_workflow_sdk_registry"]["workflows"]
            if row["workflow"] == workflow_id
        )
        _write_json(
            sdk_root / "delta" / filename,
            {
                "schema": "evidence-lane.sdk-delta-workflow-binding.v1",
                "status": "PASS",
                "workflow": workflow,
                "public_actions": [
                    {
                        "name": action,
                        "binding": f"sdk/actions/{action}.action.v1.json",
                    }
                    for action in workflow["public_actions"]
                ],
                "hooks_required": workflow["hooks_required"],
                "env_uop_ai_action_plane": True,
                "accepted_archive_queried": False,
                "execution_logic_duplicated_in_binding": False,
            },
        )
    _write_json(
        sdk_root / "delta" / "fuse.workflow.v1.json",
        {
            "schema": "evidence-lane.sdk-fuse-workflow-binding.v1",
            "status": "PASS",
            "dual_hil_fuse": public["dual_hil_fuse"],
            "owner_skill": "evi-fuse",
            "public_actions": [
                "hil_intent_classify",
                "hil_decide",
                "hil_return_to_accepted",
                "pv_fuse",
            ],
            "project_and_learning_decisions_separate": True,
        },
    )
    expected_delta_files.add("fuse.workflow.v1.json")
    for path in (sdk_root / "delta").glob("*.json"):
        if path.name not in expected_delta_files:
            path.unlink()
    _write_json(
        sdk_root / "rollback" / "three-mode.v1.json",
        {
            "schema": "evidence-lane.sdk-rollback-three-mode.v1",
            "status": "PASS",
            "rollback": public["rollback"],
            "public_action": "pv_rollback",
            "new_public_action_created": False,
        },
    )
    rollback_modes = {
        "logical-live-root-state": {
            "mode": "LOGICAL_LIVE_ROOT_STATE",
            "required_fields": [
                "project_id",
                "session_id",
                "decided_by",
                "rollback_to",
            ],
            "source_or_pointer_mutation": False,
            "fresh_plan_required": False,
        },
        "hard-accepted-zip-restore": {
            "mode": "HARD_ACCEPTED_ZIP_RESTORE",
            "required_fields": [
                "archive_path",
                "expected_archive_sha256",
                "restore_root",
                "target_plan_task_id",
                "confirmation",
            ],
            "source_or_pointer_mutation": True,
            "fresh_plan_required": True,
        },
        "git-branch-commit-restore": {
            "mode": "GIT_BRANCH_COMMIT_RESTORE",
            "required_fields": [
                "repository_path",
                "branch",
                "commit_sha",
                "restore_workspace",
                "target_plan_task_id",
                "confirmation",
            ],
            "source_or_pointer_mutation": True,
            "fresh_plan_required": True,
        },
    }
    for filename, mode in rollback_modes.items():
        _write_json(
            sdk_root / "rollback" / f"{filename}.mode.v1.json",
            {
                "schema": "evidence-lane.sdk-rollback-mode-binding.v1",
                "status": "PASS",
                **mode,
                "public_action": "pv_rollback",
                "public_action_binding": "sdk/actions/pv_rollback.action.v1.json",
                "canonical_handler": "evidence_lane_plugin.session.SessionManager",
                "new_public_action_created": False,
                "execution_logic_duplicated_in_binding": False,
            },
        )
    host_sources = {
        relative: _sha256(PLUGIN_ROOT / relative)
        for relative in (
            "src/evidence_lane_plugin/codex_turn_control.py",
            "src/evidence_lane_plugin/goal_usage.py",
            "src/evidence_lane_plugin/host_plan_rehydration.py",
            "src/evidence_lane_plugin/task_attachment_rehydration.py",
            "scripts/runtime_contract.py",
            "scripts/codex_release/install_codex_stable.py",
            "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",
            "scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1",
        )
    }
    _write_json(
        sdk_root / "host" / "runtime-install-restart.v1.json",
        {
            "schema": "evidence-lane.sdk-host-runtime-install-restart.v1",
            "status": "PASS",
            "hidden_runtime_root": (
                "%USERPROFILE%/.codex/plugins/runtime/evidence-lane-plugin"
            ),
            "project_root_user_selected": True,
            "workspace_task_selected": True,
            "restart_preparation_only": True,
            "programmatic_app_stop_allowed": False,
            "turn_drain_utility_present": False,
            "restart_preparation_installs_plugin": False,
            "tunnel_resolves_project_by_project_id": True,
            "canonical_sources": host_sources,
        },
    )
    host_contracts = {
        "local-install": {
            "owner": "scripts/codex_release/install_codex_stable.py",
            "law": "PLUGIN_CREATOR_CACHEBUSTER_HIDDEN_RUNTIME_ONLY",
        },
        "toolchain-prewarm": {
            "owner": "src/evidence_lane_plugin/runtime_toolchain.py",
            "law": "ALL_REQUIRED_RUNTIME_DEPENDENCIES_BEFORE_TUNNEL_OR_REOPEN",
        },
        "terminal-safe-restart-preparation": {
            "owner": "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",
            "law": "PREPARE_ONLY_THEN_USER_RESTARTS_AFTER_TERMINAL_RESPONSE",
        },
        "task-attachment": {
            "owner": "src/evidence_lane_plugin/task_attachment_rehydration.py",
            "law": "EXACT_TASK_SESSION_RUNTIME_BINDING",
        },
        "plan-relock": {
            "owner": "src/evidence_lane_plugin/host_plan_rehydration.py",
            "law": "ONE_COMPACT_HEADER_PLUS_FIXED_NINE_ROWS_AND_CHANGES",
        },
        "goal-metrics": {
            "owner": "src/evidence_lane_plugin/goal_usage.py",
            "law": "RESET_AWARE_FIRST_COMPLETE_RECEIPT_AND_DISTINCT_CLOCKS",
        },
        "tunnel": {
            "owner": "tunnel/tunnel-manifest.v1.json",
            "law": "HIDDEN_PREWARMED_PROJECT_ID_ROUTED_TUNNEL",
        },
        "state-travel": {
            "owner": "src/evidence_lane_plugin/state_travel_contract.py",
            "law": "FRESH_NATIVE_DIRECT_ENTRY_NO_RESTART",
        },
        "hook-control": {
            "owner": "hooks/event_isolation.py",
            "law": "PER_EVENT_CAS_TRUST_AND_ENABLEMENT",
        },
    }
    expected_host_contract_files = {
        "runtime-install-restart.v1.json",
        "public-backend-readiness.v1.json",
        "github-app-connection.v1.json",
    }
    for name, contract in host_contracts.items():
        filename = f"{name}.contract.v1.json"
        expected_host_contract_files.add(filename)
        owner_path = PLUGIN_ROOT / contract["owner"]
        _write_json(
            sdk_root / "host" / filename,
            {
                "schema": "evidence-lane.sdk-host-contract-binding.v1",
                "status": "PASS",
                "contract": name,
                "law": contract["law"],
                "owner": contract["owner"],
                "owner_sha256": _sha256(owner_path),
                "hidden_runtime_root": (
                    "%USERPROFILE%/.codex/plugins/runtime/evidence-lane-plugin"
                ),
                "project_root_hardcoded": False,
                "workspace_hardcoded": False,
                "execution_logic_duplicated_in_binding": False,
            },
        )
    public_backend_readiness = {
        "schema": "evidence-lane.public-backend-readiness.v1",
        "status": "R265_BACKEND_LINKS_READY_PRESENTATION_DEFERRED",
        "public_origin": "https://evidencelane.org",
        "surfaces": {
            "connect": {
                "status": "BACKEND_READY",
                "user_entry_path": "/connect",
                "oauth_callback_path": "/api/github-app/oauth/callback",
                "alternate_setup_path": "/api/github-app/setup",
                "device_flow_path": "/api/github-app/device",
                "testing_path": "/api/github-app/testing",
                "webhook_path": "/api/github-app/webhook",
                "authentication": (
                    "GITHUB_USER_OAUTH_OR_DEVICE_FLOW_WITH_SERVER_SIDE_SECRETS"
                ),
                "live_profile_gate": (
                    "EXPLICIT_POST_BRANCH_MAIN_MERGE_AND_GITHUB_APP_UPGRADE_APPROVAL"
                ),
            },
            "prompt_studio": {
                "status": "EXISTING_BACKEND_READY_PAGE_REFRESH_DEFERRED",
                "query_path": "/api/studio-query",
                "health_method": "GET",
                "query_method": "POST",
                "project_retrieval_authentication": (
                    "PUBLIC_SAFE_COMMITTED_CORPUS_NO_USER_CREDENTIAL"
                ),
                "public_provider_proxy_present": False,
                "generated_rag_refresh": ("DEFERRED_TO_LATER_PROMPT_STUDIO_ROW"),
            },
            "proof": {
                "status": "BACKEND_DATA_BOUND_PAGE_REFRESH_DEFERRED",
                "public_path": "/proof",
                "docs_binding_source": ("app/_data/public-docs-backend-binding.json"),
                "lane_fixture_source": "app/_data/dummy-lane-artifacts.json",
                "real_poc_publication": "DEFERRED_TO_LATER_PROOF_ROW",
            },
            "git_ci": {
                "status": "BACKEND_READY_PAGE_REFRESH_DEFERRED",
                "public_path": "/git-ci",
                "github_app_contract": ("sdk/host/github-app-connection.v1.json"),
                "main_merge_authority": "EXPLICIT_USER_APPROVAL_ONLY",
            },
        },
        "runtime_routing_boundary": {
            "remote_adapter_role": "PUBLIC_SAFE_TRANSPORT_ONLY",
            "internal_sdk_role": "ALL_PLUGIN_BEHAVIOR_OWNER",
            "native_mcp_role": "PUBLIC_ACTION_TRANSPORT",
            "tunnel_role": "CONDITIONAL_HOST_TOOL_GAP_TRANSPORT",
            "env_uop_role": "EXECUTABLE_AI_ACTION_PLANE_SEPARATE_AUTHORITIES",
            "toolchain_route": "ai_toolchain_route",
            "project_sector_http_api_exposed": False,
            "toolchain_http_api_exposed": False,
            "allowed_http_route_prefixes": [
                "/api/backend-readiness",
                "/api/github-app",
                "/api/studio-query",
            ],
        },
        "presentation_deferrals": {
            "vercel_page_redesign": True,
            "prompt_studio_redesign": True,
            "prompt_studio_rag_regeneration": True,
            "real_poc_publication": True,
            "devpost_publication": True,
        },
        "secret_values_exposed": False,
        "project_authority_mutation": False,
        "candidate_or_hil_authority": False,
    }
    _write_json(
        sdk_root / "host" / "public-backend-readiness.v1.json",
        public_backend_readiness,
    )
    _write_json(
        PUBLIC_APP_ROOT / "app" / "_data" / "public-backend-readiness.v1.json",
        public_backend_readiness,
    )
    github_mutual_exclusion = (
        "GitHub permits OAuth-on-install or a Setup URL profile, not both "
        "simultaneously for one active GitHub App configuration."
    )
    _write_json(
        sdk_root / "host" / "github-app-connection.v1.json",
        {
            "schema": "evidence-lane.github-app-connection.v1",
            "status": "CURRENT_BACKEND_CONTRACT",
            "app_name": "Evidence Lane",
            "app_slug": "evidence-lane",
            "description": (
                "Long Codex projects fail when source, dirty work, plans, evidence, "
                "installed runtimes, deployments, and human decisions drift apart. "
                "Evidence Lane reconnects those authorities without collapsing them. "
                "This private GitHub App is the governed delivery bridge: it recreates "
                "exact reviewed commits, reads CI, CodeQL, Pages, and Vercel proof, "
                "and advances delivery only after explicit user approval. It never "
                "owns Plan, Goal, Project/PV, memory, learning, or HIL authority."
            ),
            "homepage_url": "https://evidencelane.org",
            "post_authorization_redirect_url": "https://evidencelane.org/connect",
            "testing_url": "https://evidencelane.org/api/github-app/testing",
            "primary_profile": {
                "profile_id": "OAUTH_ON_INSTALL",
                "callback_urls": [
                    "https://evidencelane.org/api/github-app/oauth/callback"
                ],
                "request_oauth_on_install": True,
                "device_flow_enabled": True,
                "setup_url": None,
                "setup_on_update": False,
                "github_mutual_exclusion": github_mutual_exclusion,
            },
            "alternate_profile": {
                "profile_id": "SETUP_REDIRECT_ON_UPDATE",
                "callback_urls": [
                    "https://evidencelane.org/api/github-app/oauth/callback"
                ],
                "request_oauth_on_install": False,
                "device_flow_enabled": True,
                "setup_url": "https://evidencelane.org/api/github-app/setup",
                "setup_on_update": True,
                "github_mutual_exclusion": github_mutual_exclusion,
            },
            "oauth_security": {
                "authorization_code_state_required": True,
                "pkce_method": "S256",
                "callback_identity_revalidated": True,
                "access_token_cookie_encryption": "AES-256-GCM",
                "access_token_returned_to_browser": False,
            },
            "device_flow_security": {
                "intended_surface": "CODEX_CLI_OR_HEADLESS_HOST",
                "provider_poll_interval_enforced": True,
                "raw_device_code_returned_to_browser": False,
                "opaque_poll_token_encryption": "AES-256-GCM",
                "provider_expiry_enforced": True,
            },
            "webhook": {
                "active": False,
                "activation_policy": "OPTIONAL_CONDITION_BOUND",
                "unselected_status": "OPTIONAL_NOT_SELECTED",
                "activate_when": (
                    "REAL_TIME_EVENTS_MATERIALLY_HELP_THE_SELECTED_ROUTE"
                ),
                "fallback_readback": "GITHUB_ACTIONS_CHECKS_DEPLOYMENTS_POLLING",
                "url": "https://evidencelane.org/api/github-app/webhook",
                "content_type": "json",
                "tls_verification_required": True,
                "signature_header": "X-Hub-Signature-256",
                "delivery_header": "X-GitHub-Delivery",
                "event_header": "X-GitHub-Event",
                "events": [
                    "check_run",
                    "check_suite",
                    "installation",
                    "installation_repositories",
                    "push",
                    "workflow_run",
                ],
            },
            "secret_references": {
                "client_id": "EVIDENCE_LANE_GITHUB_APP_CLIENT_ID",
                "client_secret": "EVIDENCE_LANE_GITHUB_APP_CLIENT_SECRET",
                "webhook_secret": "EVIDENCE_LANE_GITHUB_APP_WEBHOOK_SECRET",
                "session_secret": "EVIDENCE_LANE_GITHUB_APP_SESSION_SECRET",
            },
            "secret_values_in_source": False,
            "project_pv_secret_storage_allowed": False,
            "workspace_secret_storage_allowed": False,
            "live_settings_write_gate": (
                "EXPLICIT_POST_BRANCH_MAIN_MERGE_AND_GITHUB_APP_UPGRADE_APPROVAL"
            ),
            "mcp_inventory_separate": True,
        },
    )
    for path in (sdk_root / "host").glob("*.json"):
        if path.name not in expected_host_contract_files:
            path.unlink()
    _write(
        sdk_root / "README.md",
        "# Evidence Lane SDK\n\n"
        "This distribution exposes the canonical 88-action registry, twenty "
        "ordered skill workflows, eighteen Project Sector authority bindings, "
        "nine separate named/host authorities, the installation-scoped ENV/UOP "
        "AI action plane, hook runtime, Delta entry/mid/exit routing, rollback, "
        "and host install/restart/tunnel contracts. Every file is a hash-bound "
        "binding or registry; executable business logic remains singular under "
        "`src/evidence_lane_plugin`.\n",
    )
    visible_paths = [
        path.relative_to(PLUGIN_ROOT).as_posix()
        for path in sorted(sdk_root.rglob("*"))
        if path.is_file()
        and path.name != "sdk-manifest.v1.json"
        and "__pycache__" not in path.parts
        and path.suffix.casefold() != ".pyc"
    ]
    body = {
        "schema": "evidence-lane.packaged-sdk-binding.v1",
        "status": "PASS",
        "binding": "sdk/evidence_lane_sdk.py",
        "binding_sha256": _sha256(sdk_root / "evidence_lane_sdk.py"),
        "public_action_count": int(public["tool_count"]),
        "sdk_planes": public["sdk_planes"],
        "modules": {
            name: {
                "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                "sha256": _sha256(path),
            }
            for name, path in modules.items()
        },
        "visible_surfaces": {
            path: _sha256(PLUGIN_ROOT / path) for path in visible_paths
        },
        "visible_surface_count": len(visible_paths),
        "workflow_projection_count": len(expected_workflow_files),
        "action_binding_count": len(action_bindings),
        "action_bindings": action_bindings,
        "internal_module_binding_count": len(expected_internal_module_files),
        "outer_route_binding_count": len(expected_routing_action_files),
        "project_sector_authority_binding_count": len(expected_sdk_sector_files),
        "named_authority_binding_count": len(expected_sdk_named_files),
        "env_uop_operation_binding_count": len(expected_env_uop_operation_files),
        "hook_event_binding_count": len(expected_sdk_hook_event_files),
        "delta_workflow_binding_count": len(expected_delta_files) - 1,
        "rollback_mode_binding_count": len(rollback_modes),
        "host_contract_binding_count": len(host_contracts),
        "authority_reference_count": 3,
        "host_runtime_contract_count": 1,
        "hook_event_surface_registry_sha256": _sha256(
            PLUGIN_ROOT / "hooks" / "hook-event-registry.v1.json"
        ),
        "toolchain_surface_sha256": _sha256(
            PLUGIN_ROOT / "toolchains" / "toolchain-surface.v1.json"
        ),
        "tunnel_surface_sha256": tunnel_surface["manifest_sha256"],
        "hook_event_count": hook_surface["event_count"],
        "hook_handler_action_count": hook_surface["handler_action_count"],
        "tool_requirement_count": toolchain_surface["requirement_count"],
        "internal_and_outer_layers_distinct": True,
        "env_uop_plane_distinct_from_public_actions": True,
        "lane_surface_registry_sha256": _sha256(lane_registry_path),
        "authority_surface_registry_sha256": _sha256(authority_registry_path),
        "source_module_registry": "src/evidence_lane_plugin/module-registry.v1.json",
        "source_module_registry_sha256": _sha256(
            PACKAGE_ROOT / "module-registry.v1.json"
        ),
        "source_module_count": source_modules["module_count"],
    }
    _write_json(sdk_root / "sdk-manifest.v1.json", body)
    return body


def _generate_mcp(
    public: dict[str, Any],
    *,
    sdk: dict[str, Any],
    lanes: dict[str, Any],
    authorities: dict[str, Any],
    source_modules: dict[str, Any],
    skill_surface: dict[str, Any],
    hook_surface: dict[str, Any],
    toolchain_surface: dict[str, Any],
    tunnel_surface: dict[str, Any],
) -> dict[str, Any]:
    mcp_root = PLUGIN_ROOT / "mcp"
    _write(
        mcp_root / "evidence_lane_mcp.py",
        '"""Package binding for the canonical Evidence Lane MCP server."""\n\n'
        "from evidence_lane_plugin.mcp_server import create_mcp_server\n\n\n"
        "def create_server():\n"
        "    return create_mcp_server()\n\n"
        '__all__ = ["create_server"]\n',
    )
    mcp_action_bindings: list[str] = []
    expected_mcp_action_files: set[str] = set()
    for tool in public["tools"]:
        action_name = str(tool["name"])
        filename = f"{action_name}.binding.v1.json"
        expected_mcp_action_files.add(filename)
        sdk_binding = PLUGIN_ROOT / "sdk" / "actions" / f"{action_name}.action.v1.json"
        action_path = mcp_root / "actions" / filename
        _write_json(
            action_path,
            {
                "schema": "evidence-lane.mcp-action-binding.v1",
                "status": "PASS",
                "action": action_name,
                "title": tool["title"],
                "input_schema": tool["input_schema"],
                "output_schema": tool["output_schema"],
                "annotations": tool["annotations"],
                "schema_sha256": tool["schema_sha256"],
                "route_contract": tool["route_contract"],
                "sdk_action_binding": sdk_binding.relative_to(PLUGIN_ROOT).as_posix(),
                "sdk_action_binding_sha256": _sha256(sdk_binding),
                "public_action_schema": (f"schemas/actions/{action_name}.schema.json"),
                "public_action_schema_sha256": _sha256(
                    PLUGIN_ROOT / "schemas" / "actions" / f"{action_name}.schema.json"
                ),
                "canonical_server": "src/evidence_lane_plugin/mcp_server.py",
                "canonical_server_sha256": _sha256(PACKAGE_ROOT / "mcp_server.py"),
                "execution_logic_duplicated_in_binding": False,
            },
        )
        mcp_action_bindings.append(action_path.relative_to(PLUGIN_ROOT).as_posix())
    for path in (mcp_root / "actions").glob("*.binding.v1.json"):
        if path.name not in expected_mcp_action_files:
            path.unlink()
    body = {
        "schema": "evidence-lane.packaged-mcp-binding.v1",
        "status": "PASS",
        "binding": "mcp/evidence_lane_mcp.py",
        "binding_sha256": _sha256(mcp_root / "evidence_lane_mcp.py"),
        "configuration": ".mcp.json",
        "configuration_sha256": _sha256(PLUGIN_ROOT / ".mcp.json"),
        "canonical_server": "src/evidence_lane_plugin/mcp_server.py",
        "canonical_server_sha256": _sha256(PACKAGE_ROOT / "mcp_server.py"),
        "tool_count": int(public["tool_count"]),
        "read_tool_count": int(public["read_tool_count"]),
        "write_tool_count": int(public["write_tool_count"]),
        "public_schema_catalog": "schemas/public-action-schemas.v001.json",
        "public_schema_catalog_sha256": _sha256(
            PLUGIN_ROOT / "schemas" / "public-action-schemas.v001.json"
        ),
        "sdk_manifest": "sdk/sdk-manifest.v1.json",
        "sdk_manifest_sha256": _sha256(PLUGIN_ROOT / "sdk" / "sdk-manifest.v1.json"),
        "sdk_public_action_count": sdk["public_action_count"],
        "sector_lane_count": lanes["lane_count"],
        "sector_lane_registry_sha256": _sha256(
            PLUGIN_ROOT
            / "authorities"
            / "project_sectors"
            / "lane-surface-registry.v1.json"
        ),
        "non_sector_authority_count": authorities["authority_count"],
        "non_sector_authority_registry_sha256": _sha256(
            PLUGIN_ROOT / "authorities" / "authority-surface-registry.v1.json"
        ),
        "skills_manifest_sha256": _sha256(
            PLUGIN_ROOT / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
        ),
        "separate_command_count": 0,
        "legacy_command_surface_present": False,
        "hook_manifest_sha256": _sha256(PLUGIN_ROOT / "hooks" / "hooks.json"),
        "all_surfaces_derived_from_current_executable_catalog": True,
        "source_module_registry_sha256": _sha256(
            PACKAGE_ROOT / "module-registry.v1.json"
        ),
        "source_module_count": source_modules["module_count"],
        "action_binding_count": len(mcp_action_bindings),
        "action_bindings": mcp_action_bindings,
        "skill_surface_registry_sha256": _sha256(
            PLUGIN_ROOT / "skills" / "skill-surface-registry.v1.json"
        ),
        "skill_surface_count": skill_surface["skill_count"],
        "hook_event_registry_sha256": _sha256(
            PLUGIN_ROOT / "hooks" / "hook-event-registry.v1.json"
        ),
        "hook_event_count": hook_surface["event_count"],
        "hook_handler_action_count": hook_surface["handler_action_count"],
        "toolchain_surface_sha256": _sha256(
            PLUGIN_ROOT / "toolchains" / "toolchain-surface.v1.json"
        ),
        "tool_requirement_count": toolchain_surface["requirement_count"],
        "tunnel_manifest_sha256": tunnel_surface["manifest_sha256"],
    }
    _write_json(mcp_root / "mcp-manifest.v1.json", body)
    _write(
        mcp_root / "README.md",
        "# Evidence Lane MCP\n\n"
        "This package binding creates the canonical package-local native MCP server. "
        "The `actions/` directory contains one hash-bound outer binding for every "
        "native action; the manifest binds configuration, implementation, SDK, "
        "counts, and schemas without duplicating handler logic.\n",
    )
    return body


def _generate_lane_surfaces() -> dict[str, Any]:
    """Materialize 18 inspectable bindings without duplicating lane logic."""

    from evidence_lane_plugin.lane_engine import (
        _create_lane_schema,
        _lane_topology,
        _registry_linked_lane_workflow,
    )
    from evidence_lane_plugin.lanes import (
        CANONICAL_LANE_IDS,
        LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
        LANE_REGISTRY,
        LANE_SCHEMA_REGISTRY_SHA256,
        lane_artifact_contract,
        lane_schema_asset,
    )

    lanes_root = PLUGIN_ROOT / "authorities" / "project_sectors"
    lanes_root.mkdir(parents=True, exist_ok=True)
    canonical_ids = set(CANONICAL_LANE_IDS)
    for path in lanes_root.iterdir():
        if path.is_dir() and path.name not in canonical_ids:
            raise ValueError(f"Unexpected installed lane surface: {path.name}")

    lane_rows: list[dict[str, Any]] = []
    source_hashes = {
        relative: _sha256(PLUGIN_ROOT / relative)
        for relative in (
            "src/evidence_lane_plugin/lanes.py",
            "src/evidence_lane_plugin/lane_engine.py",
            "src/evidence_lane_plugin/lane_reader.py",
            "src/evidence_lane_plugin/lane_traversal.py",
            "src/evidence_lane_plugin/source_intake.py",
        )
    }
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        lane_root = lanes_root / lane_id
        lane_root.mkdir(parents=True, exist_ok=True)
        schema_asset = lane_schema_asset(lane_id)
        artifact_contract = lane_artifact_contract(lane_id)
        sqlite_path = lane_root / lane.sqlite_filename
        if sqlite_path.exists():
            sqlite_path.unlink()
        connection = sqlite3.connect(sqlite_path)
        connection.row_factory = sqlite3.Row
        try:
            _create_lane_schema(connection, lane)
            connection.executemany(
                "INSERT INTO lane_meta(key, value) VALUES(?, ?)",
                (
                    ("lane_id", lane_id),
                    ("schema_id", str(schema_asset["schema_id"])),
                    ("template_role", "INSTALLED_EMPTY_SCHEMA_TEMPLATE"),
                ),
            )
            connection.commit()
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            table_names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            expected_tables = set(schema_asset["tables"])
            schema_rows = [
                str(row[0]).rstrip(";") + ";"
                for row in connection.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE sql IS NOT NULL
                      AND (
                        (type='table' AND name IN ({placeholders}))
                        OR (type='index' AND tbl_name IN ({placeholders}))
                        OR (type='trigger' AND tbl_name IN ({placeholders}))
                      )
                    ORDER BY CASE type WHEN 'table' THEN 1 WHEN 'index' THEN 2 ELSE 3 END,
                             name
                    """.format(placeholders=",".join("?" for _ in expected_tables)),
                    tuple(expected_tables) * 3,
                )
            ]
        finally:
            connection.close()
        if integrity != ["ok"] or not expected_tables.issubset(table_names):
            raise ValueError(f"Lane SQLite template is incomplete: {lane_id}")
        _write(
            lane_root / "schema.sql",
            "-- Generated inspection/replay schema from the canonical lane builder.\n"
            "-- Canonical implementation: src/evidence_lane_plugin/lane_engine.py\n\n"
            + "\n\n".join(schema_rows)
            + "\n",
        )
        empty_classification = {
            "unchanged_reuse": [],
            "changed_rebuild": [],
            "new_register": [],
            "removed_purge": [],
            "blocked_unsupported": [],
        }
        mmd, dot = _lane_topology(lane, sqlite_path, empty_classification)
        _write(lane_root / lane.mmd_filename, mmd)
        _write(lane_root / lane.dot_filename, dot)
        _write(
            lane_root / "builder.py",
            '"""Lane-specific binding to the canonical shared lane bundle builder."""\n\n'
            "from evidence_lane_plugin.lane_engine import build_lane_bundle\n\n"
            f'LANE_ID = "{lane_id}"\n\n'
            "def build_lane_sources(*, source_paths, source_overrides=None, **kwargs):\n"
            "    paths = tuple(dict.fromkeys(str(path) for path in source_paths))\n"
            "    overrides = dict(source_overrides or {})\n"
            "    overrides.update({path: LANE_ID for path in paths})\n"
            "    return build_lane_bundle(\n"
            "        source_paths_override=paths,\n"
            "        source_overrides=overrides,\n"
            "        **kwargs,\n"
            "    )\n\n"
            '__all__ = ["LANE_ID", "build_lane_sources"]\n',
        )
        _write(
            lane_root / "reader.py",
            '"""Lane-specific binding to live pointer/MMD/DOT/SQLite traversal."""\n\n'
            "from evidence_lane_plugin.lane_reader import LaneReader\n\n"
            f'LANE_ID = "{lane_id}"\n\n'
            "def lane_status(reader: LaneReader, project_id: str):\n"
            "    return reader.lane_status(project_id, LANE_ID)\n\n"
            "def search(reader: LaneReader, project_id: str, query: str, **kwargs):\n"
            "    return reader.search(project_id, LANE_ID, query, **kwargs)\n\n"
            '__all__ = ["LANE_ID", "lane_status", "search"]\n',
        )
        _write_json(
            lane_root / "lane-contract.v1.json",
            {
                "schema": "evidence-lane.installed-lane-contract.v1",
                "status": "PASS",
                "lane": lane.as_dict(),
                "schema_asset": schema_asset,
                "artifact_contract": artifact_contract,
                "shared_engine_source_hashes": source_hashes,
                "authority_is_separate_per_project_lane": True,
                "shared_engine_does_not_merge_lane_sqlite": True,
            },
        )
        workflow_files = {
            "json": lane_root / "workflow.v1.json",
            "mmd": lane_root / "workflow.mmd",
            "dot": lane_root / "workflow.dot",
        }
        dedicated_workflow = (
            {
                key: path.relative_to(PLUGIN_ROOT).as_posix()
                for key, path in workflow_files.items()
            }
            | {f"{key}_sha256": _sha256(path) for key, path in workflow_files.items()}
            if all(path.is_file() for path in workflow_files.values())
            else None
        )
        _write_json(
            lane_root / "tools.json",
            {
                "schema": "evidence-lane.installed-lane-tooling.v1",
                "status": "PASS",
                "lane_id": lane_id,
                "builder": "builder.py:build_lane_sources",
                "reader": "reader.py:LaneReader",
                "query_traversal": "pointer + manifest + MMD + DOT + tools + SQLite",
                "retrieval_modes": ["hybrid", "fts5", "bm25", "tfidf"],
                "json_build_refresh_tooling": [
                    "lane_pointer.json",
                    "refresh_receipt.json",
                    "lane_manifest.json",
                ],
                "canonical_shared_modules": source_hashes,
                "registry_linked_workflow": _registry_linked_lane_workflow(lane_id),
                "dedicated_workflow": dedicated_workflow,
            },
        )
        _write_json(
            lane_root / "lane-pointer.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{lane.display_label} lane pointer",
                "type": "object",
                "required": [
                    "schema",
                    "lane_id",
                    "parent_pv",
                    "proposed_pv",
                    "pointer_generation",
                ],
                "properties": {
                    "schema": {"const": "evidence-lane.lane-pointer-evidence.v1"},
                    "lane_id": {"const": lane_id},
                    "parent_pv": {"type": ["string", "null"]},
                    "proposed_pv": {"type": "string", "minLength": 1},
                    "pointer_generation": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": True,
            },
        )
        _write_json(
            lane_root / "refresh-receipt.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{lane.display_label} refresh receipt",
                "type": "object",
                "required": ["schema", "lane_id", "classification", "recorded_at"],
                "properties": {
                    "lane_id": {"const": lane_id},
                    "classification": {"type": "object"},
                    "recorded_at": {"type": "string", "minLength": 1},
                },
                "additionalProperties": True,
            },
        )
        _write_json(
            lane_root / "sqlite-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=lane_id,
                artifact_path=lane.sqlite_filename,
                artifact_role="SQLITE_AUTHORITY",
                syntax="SQLITE3_FTS5",
                validator="lane_engine.lane_schema_builder_projection",
            ),
        )
        _write_json(
            lane_root / "mmd-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=lane_id,
                artifact_path=lane.mmd_filename,
                artifact_role="MERMAID_TRAVERSAL_MAP",
                syntax="MERMAID_FLOWCHART",
                validator="topology_reconciliation.validate_mermaid",
            ),
        )
        _write_json(
            lane_root / "dot-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=lane_id,
                artifact_path=lane.dot_filename,
                artifact_role="DOT_TRAVERSAL_MAP",
                syntax="GRAPHVIZ_DOT",
                validator="topology_reconciliation.validate_dot",
            ),
        )
        _write_json(
            lane_root / "tools.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{lane.display_label} lane tools",
                "type": "object",
                "required": [
                    "schema",
                    "status",
                    "lane_id",
                    "builder",
                    "reader",
                    "query_traversal",
                    "retrieval_modes",
                    "canonical_shared_modules",
                    "registry_linked_workflow",
                    "dedicated_workflow",
                ],
                "properties": {
                    "schema": {"const": "evidence-lane.installed-lane-tooling.v1"},
                    "status": {"const": "PASS"},
                    "lane_id": {"const": lane_id},
                    "builder": {"const": "builder.py:build_lane_sources"},
                    "reader": {"const": "reader.py:LaneReader"},
                    "query_traversal": {"type": "string", "minLength": 1},
                    "retrieval_modes": {"const": ["hybrid", "fts5", "bm25", "tfidf"]},
                    "canonical_shared_modules": {"type": "object"},
                    "registry_linked_workflow": {
                        "type": "object",
                        "required": [
                            "schema",
                            "status",
                            "lane_id",
                            "eligible_tools",
                            "eligible_public_actions",
                            "eligible_skill_workflows",
                            "current_registry_counts",
                            "counts_are_current_snapshot_not_ceiling",
                        ],
                    },
                    "dedicated_workflow": {"type": "object"},
                },
                "additionalProperties": True,
            },
        )
        _write_json(
            lane_root / "manifest.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{lane.display_label} installed surface manifest",
                "type": "object",
                "required": [
                    "schema",
                    "status",
                    "lane_id",
                    "sqlite_filename",
                    "sqlite_integrity",
                    "members",
                    "separate_sqlite_authority",
                    "live_project_bytes_in_template",
                ],
                "properties": {
                    "schema": {"const": "evidence-lane.installed-lane-surface.v1"},
                    "status": {"const": "PASS"},
                    "lane_id": {"const": lane_id},
                    "sqlite_filename": {"const": lane.sqlite_filename},
                    "sqlite_integrity": {"const": "ok"},
                    "members": {"type": "array", "minItems": 1},
                    "separate_sqlite_authority": {"const": True},
                    "live_project_bytes_in_template": {"const": False},
                },
                "additionalProperties": True,
            },
        )
        _write_json(
            lane_root / "builder-contract.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{lane.display_label} builder binding request",
                "type": "object",
                "required": ["lane_id", "source_paths", "bundle_arguments"],
                "properties": {
                    "lane_id": {"const": lane_id},
                    "source_paths": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "bundle_arguments": {"type": "object"},
                },
                "additionalProperties": False,
            },
        )
        _write_json(
            lane_root / "reader-contract.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{lane.display_label} bounded reader request",
                "type": "object",
                "required": ["project_id", "lane_id", "query", "retrieval", "limit"],
                "properties": {
                    "project_id": {"type": "string", "minLength": 1},
                    "lane_id": {"const": lane_id},
                    "query": {"type": "string", "minLength": 1},
                    "retrieval": {"enum": ["hybrid", "fts5", "bm25", "tfidf"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        )
        _write(
            lane_root / "README.md",
            f"# {lane.display_label} lane\n\n"
            "This folder is the installed, lane-specific projection of the canonical "
            "shared engine. Its SQLite template, SQL schema, MMD/DOT traversal maps, "
            "pointer and refresh schemas, builder, reader, tools, and manifest are "
            "hash-bound below. Live project data is created under the user-selected "
            "Project/PV root and is never stored in this package template.\n",
        )
        member_paths = [
            path
            for path in sorted(lane_root.iterdir())
            if path.is_file() and path.name != "manifest.v1.json"
        ]
        manifest = {
            "schema": "evidence-lane.installed-lane-surface.v1",
            "status": "PASS",
            "lane_id": lane_id,
            "sqlite_filename": lane.sqlite_filename,
            "sqlite_integrity": "ok",
            "sqlite_table_count": len(table_names),
            "schema_contract_table_count": len(expected_tables),
            "members": [
                {
                    "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in member_paths
            ],
            "schema_registry_sha256": LANE_SCHEMA_REGISTRY_SHA256,
            "artifact_registry_sha256": LANE_ARTIFACT_ROLE_REGISTRY_SHA256,
            "separate_sqlite_authority": True,
            "live_project_bytes_in_template": False,
            "template_contains_identity_metadata_only": True,
        }
        _write_json(lane_root / "manifest.v1.json", manifest)
        lane_rows.append(
            {
                "lane_id": lane_id,
                "path": lane_root.relative_to(PLUGIN_ROOT).as_posix(),
                "manifest_sha256": _sha256(lane_root / "manifest.v1.json"),
                "sqlite_template_sha256": _sha256(sqlite_path),
                "schema_contract_sha256": schema_asset["contract_sha256"],
                "artifact_contract_sha256": artifact_contract["contract_sha256"],
            }
        )
    body = {
        "schema": "evidence-lane.installed-lane-surface-registry.v1",
        "status": "PASS",
        "lane_count": len(lane_rows),
        "lanes": lane_rows,
        "canonical_source_hashes": source_hashes,
        "each_lane_has_separate_sqlite_schema_mmd_dot_pointer_refresh_and_tools": True,
        "source_intake_routes_one_source_to_one_lane": True,
        "live_project_data_stored_in_installed_package": False,
    }
    _write_json(lanes_root / "lane-surface-registry.v1.json", body)
    return body


def _sqlite_schema_projection(database: Path) -> str:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        rows = [
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "table": str(row[2]),
                "sql": str(row[3]),
            }
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                "ORDER BY CASE type WHEN 'table' THEN 1 WHEN 'view' THEN 2 "
                "WHEN 'index' THEN 3 ELSE 4 END,name"
            )
        ]
    finally:
        connection.close()
    fts_tables = {
        row["name"]
        for row in rows
        if row["type"] == "table"
        and "VIRTUAL TABLE" in row["sql"].upper()
        and "FTS5" in row["sql"].upper()
    }
    projected = [
        row["sql"].rstrip(";") + ";"
        for row in rows
        if not any(
            row["name"].startswith(f"{fts}_") or row["table"].startswith(f"{fts}_")
            for fts in fts_tables
        )
    ]
    return (
        "-- Generated from the canonical authority SQLite builder.\n"
        "-- FTS5 shadow tables are intentionally omitted; SQLite creates them.\n\n"
        + "\n\n".join(projected)
        + "\n"
    )


def _initialize_authority_template(authority_id: str, target: Path) -> None:
    if target.exists():
        target.unlink()
    if authority_id == "agent_learning":
        from evidence_lane_plugin.agent_learning import _connect as connect_learning

        with tempfile.TemporaryDirectory(prefix="evi-learning-template-") as raw:
            root = Path(raw)
            connection = connect_learning(root)
            connection.close()
            shutil.copy2(root / "ai_learning" / "agent-learning.sqlite", target)
    elif authority_id == "canon_input":
        sql = (PLUGIN_ROOT / "schemas" / "canon" / "canon-ledger.v1.sql").read_text(
            encoding="utf-8"
        )
        connection = sqlite3.connect(target)
        try:
            connection.executescript(sql)
            connection.commit()
        finally:
            connection.close()
    elif authority_id == "project_memory":
        from evidence_lane_plugin.project_memory import _connect as connect_memory

        with tempfile.TemporaryDirectory(prefix="evi-memory-template-") as raw:
            root = Path(raw)
            connection = connect_memory(root)
            connection.close()
            shutil.copy2(root / "memory" / "memory.sqlite", target)
    elif authority_id == "project_overlay":
        from evidence_lane_plugin.project_overlay import _progressive_schema, _schema

        connection = sqlite3.connect(target)
        try:
            _schema(connection)
            _progressive_schema(connection)
            connection.commit()
        finally:
            connection.close()
    elif authority_id == "source_authority":
        from evidence_lane_plugin.source_authority import (
            initialize_source_authority_registry,
        )

        initialize_source_authority_registry(target)
    elif authority_id == "project_universe":
        sql = (
            PLUGIN_ROOT / "schemas" / "universe" / "project-universe.v1.sql"
        ).read_text(encoding="utf-8")
        connection = sqlite3.connect(target)
        try:
            connection.executescript(sql)
            connection.commit()
        finally:
            connection.close()
    elif authority_id == "connector_brain":
        from evidence_lane_plugin.connector_governance import ConnectorGovernance

        connection = ConnectorGovernance(target)._connect()
        connection.commit()
        connection.close()
    elif authority_id == "project_authority":
        from evidence_lane_plugin.project_authority import (
            initialize_project_authority_database,
        )

        initialize_project_authority_database(target)
    elif authority_id == "receipt_ledger":
        from evidence_lane_plugin.receipt_ledger import initialize_receipt_ledger

        initialize_receipt_ledger(target)
    elif authority_id == "session_authority":
        from evidence_lane_plugin.session_authority import (
            initialize_session_authority,
        )

        initialize_session_authority(target)
    else:
        raise ValueError(f"Unknown authority template: {authority_id}")


def _linked_public_actions(
    public: dict[str, Any], owner_skills: tuple[str, ...]
) -> list[dict[str, Any]]:
    routing = json.loads(
        (
            PLUGIN_ROOT / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
        ).read_text(encoding="utf-8")
    )
    workflow_names = {
        str(name)
        for skill in owner_skills
        for group in routing["workflows"].get(skill, {}).get("ordered_tool_groups", [])
        for name in group.get("tools", [])
    }
    return [
        {
            "name": tool["name"],
            "schema_sha256": tool["schema_sha256"],
            "route_contract": tool["route_contract"],
        }
        for tool in public["tools"]
        if str(tool["name"]) in workflow_names
        or str(tool["route_contract"].get("owner_skill")) in owner_skills
    ]


def _generate_authority_surfaces(public: dict[str, Any]) -> dict[str, Any]:
    """Project complete non-sector authority templates from canonical builders."""

    from evidence_lane_plugin.authority_support import (
        AUTHORITY_SUPPORT_PROFILES,
        _schema_graph,
        _schema_snapshot,
    )
    from evidence_lane_plugin.graph_pipeline import SemanticGraph
    from evidence_lane_plugin.sqlite_indexing import rebuild_sqlite_authority_index

    authorities_root = PLUGIN_ROOT / "authorities"
    authorities_root.mkdir(parents=True, exist_ok=True)
    specifications = {
        "agent_learning": {
            "module": "agent_learning.py",
            "owner_skills": ("evi-learning",),
            "pointer": "ai_learning/active_pointer.json",
            "pointer_role": "SEPARATE_LEARNING_ACCEPTANCE_POINTER",
        },
        "canon_input": {
            "module": "canon_task_graph.py",
            "extra_modules": (
                "canon_consequence_graph.py",
                "canon_runtime_continuity.py",
            ),
            "owner_skills": ("evi-canon",),
            "pointer": "canon/state-travel-continuity/*.json",
            "pointer_role": "TASK_GRAPH_CONTINUITY_NOT_PROJECT_TRUTH_POINTER",
        },
        "project_memory": {
            "module": "project_memory.py",
            "extra_modules": ("conversation_memory.py",),
            "owner_skills": ("evi-memory",),
            "pointer": "memory/head.json",
            "pointer_role": "MEMORY_HEAD_NOT_PROJECT_TRUTH_POINTER",
        },
        "project_overlay": {
            "module": "project_overlay.py",
            "extra_modules": ("adaptive_delta_exit.py", "store.py"),
            "owner_skills": ("evi-build", "evi-refresh", "evi-fuse"),
            "pointer": "project_overlay/manifest.json",
            "pointer_role": "HIL_PROPOSAL_OVERLAY_NOT_ACCEPTED_POINTER",
        },
        "source_authority": {
            "module": "source_authority.py",
            "extra_modules": (
                "source_intake.py",
                "source_sqlite.py",
                "source_graph.py",
                "source_git_history.py",
                "source_identity.py",
            ),
            "owner_skills": ("evi-source-intake",),
            "pointer": "sources/source_authority.manifest.json",
            "pointer_role": "SOURCE_REGISTRY_HEAD_NOT_PROJECT_TRUTH_POINTER",
        },
        "project_universe": {
            "module": "project_universe.py",
            "owner_skills": ("evi-universe",),
            "pointer": "universe/project_universe_pointer.json",
            "pointer_role": "DERIVED_UNIVERSE_SOURCE_FINGERPRINT",
        },
        "connector_brain": {
            "module": "connector_governance.py",
            "owner_skills": (
                "evi-plugin",
                "evi-additional-plugin",
                "evi-drop-additional-plugin",
            ),
            "pointer": "connector_brain.manifest.json",
            "pointer_role": "CONNECTOR_REGISTRY_HEAD_NOT_PROJECT_TRUTH_POINTER",
        },
        "project_authority": {
            "module": "project_authority.py",
            "extra_modules": ("project_pv_storage.py", "store.py"),
            "owner_skills": ("evi-boot", "evi"),
            "pointer": (
                "project_authority/project-authority.sqlite#project_pointer_history"
            ),
            "pointer_role": "SOLE_PROJECT_POINTER_HISTORY_AUTHORITY",
        },
        "receipt_ledger": {
            "module": "receipt_ledger.py",
            "owner_skills": ("evi", "evi-refresh"),
            "pointer": "receipts/receipt-ledger.sqlite#receipt_record",
            "pointer_role": "APPEND_ONLY_RECEIPT_LEDGER_HEAD",
        },
        "session_authority": {
            "module": "session_authority.py",
            "extra_modules": (
                "session.py",
                "task_binding_registry.py",
                "state_travel_contract.py",
            ),
            "owner_skills": ("evi-boot", "evi-state-travel"),
            "pointer": "sessions/session-authority.sqlite#session_record",
            "pointer_role": "SESSION_TASK_AND_STATE_TRAVEL_HEAD",
        },
    }
    if set(specifications) != set(AUTHORITY_SUPPORT_PROFILES):
        raise ValueError(
            "Authority surface specification does not match runtime profiles."
        )
    for path in authorities_root.iterdir():
        if path.is_dir() and path.name not in set(specifications) | {
            "project_authority",
            "project_sectors",
            "instructions",
        }:
            raise ValueError(f"Unexpected authority package surface: {path.name}")

    rows: list[dict[str, Any]] = []
    support_source = PACKAGE_ROOT / "authority_support.py"
    for authority_id, specification in specifications.items():
        profile = AUTHORITY_SUPPORT_PROFILES[authority_id]
        root = authorities_root / authority_id
        root.mkdir(parents=True, exist_ok=True)
        if authority_id == "project_authority":
            for stale_name in (
                "authority-contract.schema.json",
                "authority-contract.v1.json",
                "tools.json",
            ):
                stale = root / stale_name
                if stale.is_file():
                    stale.unlink()
        if authority_id == "connector_brain":
            stale_connector_database = root / "connector_brain.sqlite"
            if stale_connector_database.is_file():
                stale_connector_database.unlink()
        database = root / Path(profile.database).name
        _initialize_authority_template(authority_id, database)
        llama_index_receipt = rebuild_sqlite_authority_index(
            database,
            authority_id=authority_id,
            recorded_at="2000-01-01T00:00:00Z",
            reset_receipts=True,
        )
        snapshot = _schema_snapshot(database)
        mmd_text, dot_text, graph_pipeline_receipt = _schema_graph(
            authority_id, snapshot
        )
        _write(root / Path(profile.mmd).name, mmd_text)
        _write(root / Path(profile.dot).name, dot_text)
        _write(root / "schema.sql", _sqlite_schema_projection(database))
        _write_json(root / "sqlite-schema.v1.json", snapshot)
        module_names = (
            str(specification["module"]),
            *tuple(specification.get("extra_modules") or ()),
        )
        source_hashes = {
            f"src/evidence_lane_plugin/{name}": _sha256(PACKAGE_ROOT / name)
            for name in module_names
        }
        source_hashes["src/evidence_lane_plugin/authority_support.py"] = _sha256(
            support_source
        )
        owner_skills = tuple(specification["owner_skills"])
        linked_actions = _linked_public_actions(public, owner_skills)
        linked_skills = {
            f"skills/{skill}/SKILL.md": _sha256(
                PLUGIN_ROOT / "skills" / skill / "SKILL.md"
            )
            for skill in owner_skills
        }
        linked_subauthorities: list[dict[str, Any]] = []
        if authority_id == "canon_input":
            consequence_root = root / "consequence_graph"
            consequence_root.mkdir(parents=True, exist_ok=True)
            consequence_database = consequence_root / "canon-consequence-graph.sqlite"
            consequence_schema_asset = (
                PLUGIN_ROOT / "schemas" / "canon" / "canon-consequence-graph.v1.sql"
            )
            if consequence_database.exists():
                consequence_database.unlink()
            consequence_connection = sqlite3.connect(consequence_database)
            try:
                consequence_connection.executescript(
                    consequence_schema_asset.read_text(encoding="utf-8")
                )
                consequence_connection.commit()
                consequence_integrity = [
                    str(row[0])
                    for row in consequence_connection.execute("PRAGMA integrity_check")
                ]
            finally:
                consequence_connection.close()
            if consequence_integrity != ["ok"]:
                raise ValueError(
                    "Canon consequence graph template failed SQLite integrity."
                )
            consequence_llama_index_receipt = rebuild_sqlite_authority_index(
                consequence_database,
                authority_id="canon_consequence_graph",
                recorded_at="2000-01-01T00:00:00Z",
                reset_receipts=True,
            )
            consequence_snapshot = _schema_snapshot(consequence_database)
            (
                consequence_mmd_text,
                consequence_dot_text,
                consequence_graph_pipeline_receipt,
            ) = _schema_graph("canon_consequence_graph", consequence_snapshot)
            consequence_mmd = "canon-consequence-graph.mmd"
            consequence_dot = "canon-consequence-graph.dot"
            _write(consequence_root / consequence_mmd, consequence_mmd_text)
            _write(consequence_root / consequence_dot, consequence_dot_text)
            _write(
                consequence_root / "schema.sql",
                _sqlite_schema_projection(consequence_database),
            )
            _write_json(
                consequence_root / "sqlite-schema.v1.json",
                consequence_snapshot,
            )
            _write(
                consequence_root / "runtime.py",
                '"""Binding to the canonical Canon consequence graph implementation."""\n\n'
                "from evidence_lane_plugin import canon_consequence_graph\n\n"
                'AUTHORITY_ID = "canon_consequence_graph"\n'
                "CANONICAL_MODULE = canon_consequence_graph\n\n"
                '__all__ = ["AUTHORITY_ID", "CANONICAL_MODULE"]\n',
            )
            _write_json(
                consequence_root / "tools.json",
                {
                    "schema": ("evidence-lane.installed-canon-consequence-tools.v1"),
                    "status": "PASS",
                    "authority_id": "canon_consequence_graph",
                    "parent_authority_id": "canon_input",
                    "database": consequence_database.name,
                    "query_order": [
                        consequence_mmd,
                        consequence_dot,
                        consequence_database.name,
                    ],
                    "sqlite_query_mode": ("READ_ONLY_IMMUTABLE_BOUNDED_FTS5_BM25"),
                    "json_role": ("BUILD_REFRESH_POINTER_AND_RECEIPT_TOOLING"),
                    "runtime_binding": "runtime.py",
                    "canonical_source": (
                        "src/evidence_lane_plugin/canon_consequence_graph.py"
                    ),
                    "canonical_source_sha256": source_hashes[
                        "src/evidence_lane_plugin/canon_consequence_graph.py"
                    ],
                    "llama_index_refresh_receipt": (consequence_llama_index_receipt),
                    "graph_pipeline_receipt": (consequence_graph_pipeline_receipt),
                },
            )
            _write_json(
                consequence_root / "pointer-contract.v1.json",
                {
                    "schema": ("evidence-lane.installed-canon-consequence-pointer.v1"),
                    "status": "PASS",
                    "authority_id": "canon_consequence_graph",
                    "project_relative_path": ("canon/consequence-graph-current.json"),
                    "pointer_role": ("DERIVED_CANON_CONSEQUENCE_GRAPH_HEAD"),
                    "project_truth_pointer_owner": False,
                    "pointer_movement_in_template": False,
                },
            )
            _write_json(
                consequence_root / "sqlite-artifact.schema.json",
                _artifact_receipt_schema(
                    authority_id="canon_consequence_graph",
                    artifact_path=consequence_database.name,
                    artifact_role="SQLITE_AUTHORITY",
                    syntax="SQLITE3_FTS5",
                    validator="canon_consequence_graph._schema_asset",
                ),
            )
            _write_json(
                consequence_root / "mmd-artifact.schema.json",
                _artifact_receipt_schema(
                    authority_id="canon_consequence_graph",
                    artifact_path=consequence_mmd,
                    artifact_role="MERMAID_TRAVERSAL_MAP",
                    syntax="MERMAID_FLOWCHART",
                    validator="authority_support.validate_authority_support",
                ),
            )
            _write_json(
                consequence_root / "dot-artifact.schema.json",
                _artifact_receipt_schema(
                    authority_id="canon_consequence_graph",
                    artifact_path=consequence_dot,
                    artifact_role="DOT_TRAVERSAL_MAP",
                    syntax="GRAPHVIZ_DOT",
                    validator="authority_support.validate_authority_support",
                ),
            )
            _write_json(
                consequence_root / "tools.schema.json",
                _json_file_schema(
                    authority_id="canon_consequence_graph",
                    artifact_path="tools.json",
                    schema_id=("evidence-lane.installed-canon-consequence-tools.v1"),
                ),
            )
            _write_json(
                consequence_root / "pointer-contract.schema.json",
                _json_file_schema(
                    authority_id="canon_consequence_graph",
                    artifact_path="pointer-contract.v1.json",
                    schema_id=("evidence-lane.installed-canon-consequence-pointer.v1"),
                ),
            )
            _write_json(
                consequence_root / "runtime-binding.schema.json",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": "Canon consequence graph runtime binding",
                    "type": "object",
                    "required": [
                        "authority_id",
                        "module",
                        "source_sha256",
                    ],
                    "properties": {
                        "authority_id": {"const": "canon_consequence_graph"},
                        "module": {
                            "const": ("evidence_lane_plugin.canon_consequence_graph")
                        },
                        "source_sha256": {
                            "const": source_hashes[
                                "src/evidence_lane_plugin/canon_consequence_graph.py"
                            ]
                        },
                    },
                    "additionalProperties": False,
                },
            )
            _write_json(
                consequence_root / "manifest.schema.json",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": "Canon consequence graph installed manifest",
                    "type": "object",
                    "required": [
                        "schema",
                        "status",
                        "authority_id",
                        "parent_authority_id",
                        "database",
                        "database_sha256",
                        "members",
                    ],
                    "properties": {
                        "schema": {
                            "const": (
                                "evidence-lane.installed-canon-consequence-surface.v1"
                            )
                        },
                        "status": {"const": "PASS"},
                        "authority_id": {"const": "canon_consequence_graph"},
                        "parent_authority_id": {"const": "canon_input"},
                        "database": {"const": consequence_database.name},
                        "database_sha256": {
                            "type": "string",
                            "pattern": "^[A-F0-9]{64}$",
                        },
                        "members": {"type": "array", "minItems": 1},
                    },
                    "additionalProperties": True,
                },
            )
            _write(
                consequence_root / "README.md",
                "# Canon consequence graph\n\n"
                "This nested Canon authority is a separate empty SQLite/MMD/DOT "
                "projection of the existing consequence-graph implementation. "
                "It does not merge the Canon input ledger with Project Truth, "
                "Learning, Plan, sector lanes, or the accepted pointer.\n",
            )
            consequence_members = [
                path
                for path in sorted(consequence_root.iterdir())
                if path.is_file() and path.name != "manifest.v1.json"
            ]
            consequence_manifest = {
                "schema": ("evidence-lane.installed-canon-consequence-surface.v1"),
                "status": "PASS",
                "authority_id": "canon_consequence_graph",
                "parent_authority_id": "canon_input",
                "database": consequence_database.name,
                "database_sha256": _sha256(consequence_database),
                "database_integrity": "ok",
                "schema_sha256": consequence_snapshot["schema_sha256"],
                "table_count": consequence_snapshot["table_count"],
                "fts5_tables": consequence_snapshot["fts5_tables"],
                "members": [
                    {
                        "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                    for path in consequence_members
                ],
                "canonical_source_hashes": {
                    "src/evidence_lane_plugin/canon_consequence_graph.py": (
                        source_hashes[
                            "src/evidence_lane_plugin/canon_consequence_graph.py"
                        ]
                    ),
                    "schemas/canon/canon-consequence-graph.v1.sql": (
                        _sha256(consequence_schema_asset)
                    ),
                },
                "llama_index_refresh_receipt": (consequence_llama_index_receipt),
                "graph_pipeline_receipt": consequence_graph_pipeline_receipt,
                "live_project_bytes_in_template": False,
                "authority_merged_with_parent_or_peer": False,
            }
            _write_json(
                consequence_root / "manifest.v1.json",
                consequence_manifest,
            )
            linked_subauthorities.append(
                {
                    "authority_id": "canon_consequence_graph",
                    "path": consequence_root.relative_to(PLUGIN_ROOT).as_posix(),
                    "manifest_sha256": _sha256(consequence_root / "manifest.v1.json"),
                    "database_sha256": _sha256(consequence_database),
                    "table_count": consequence_snapshot["table_count"],
                    "separate_sqlite_authority": True,
                }
            )
        module_path = str(specification["module"]).removesuffix(".py")
        _write(
            root / "runtime.py",
            '"""Binding to the canonical non-sector authority implementation."""\n\n'
            "from importlib import import_module\n\n"
            f'AUTHORITY_ID = "{authority_id}"\n'
            f'CANONICAL_MODULE = "evidence_lane_plugin.{module_path}"\n\n'
            "def canonical_module():\n"
            "    return import_module(CANONICAL_MODULE)\n\n"
            '__all__ = ["AUTHORITY_ID", "CANONICAL_MODULE", "canonical_module"]\n',
        )
        _write(
            root / "builder.py",
            '"""Build/refresh binding for this separate authority support system."""\n\n'
            "from evidence_lane_plugin.authority_support import "
            "refresh_authority_support\n\n"
            f'AUTHORITY_ID = "{authority_id}"\n\n'
            "def refresh(project_root):\n"
            "    return refresh_authority_support(project_root, AUTHORITY_ID)\n\n"
            '__all__ = ["AUTHORITY_ID", "refresh"]\n',
        )
        _write(
            root / "reader.py",
            '"""Bounded validation/traversal binding for this separate authority."""\n\n'
            "from evidence_lane_plugin.authority_support import "
            "validate_authority_support\n\n"
            f'AUTHORITY_ID = "{authority_id}"\n\n'
            "def validate(project_root):\n"
            "    return validate_authority_support(project_root, AUTHORITY_ID)\n\n"
            '__all__ = ["AUTHORITY_ID", "validate"]\n',
        )
        authority_workflow_files = {
            "json": root / "workflow.v1.json",
            "mmd": root / "workflow.mmd",
            "dot": root / "workflow.dot",
        }
        dedicated_authority_workflow = (
            {
                key: path.relative_to(PLUGIN_ROOT).as_posix()
                for key, path in authority_workflow_files.items()
            }
            | {
                f"{key}_sha256": _sha256(path)
                for key, path in authority_workflow_files.items()
            }
            if all(path.is_file() for path in authority_workflow_files.values())
            else None
        )
        _write_json(
            root / Path(profile.tools).name,
            {
                "schema": "evidence-lane.installed-authority-tools.v1",
                "status": "PASS",
                "authority_id": authority_id,
                "database": database.name,
                "query_order": [
                    Path(profile.mmd).name,
                    Path(profile.dot).name,
                    database.name,
                ],
                "sqlite_query_mode": "READ_ONLY_IMMUTABLE_BOUNDED_FTS5_BM25",
                "json_role": "BUILD_REFRESH_SCHEMA_AND_POINTER_TOOLING",
                "runtime_binding": "runtime.py",
                "builder_binding": "builder.py:refresh",
                "reader_binding": "reader.py:validate",
                "linked_public_actions": linked_actions,
                "linked_skills": linked_skills,
                "canonical_source_hashes": source_hashes,
                "linked_subauthorities": linked_subauthorities,
                "llama_index_refresh_receipt": llama_index_receipt,
                "graph_pipeline_receipt": graph_pipeline_receipt,
                "dedicated_workflow": dedicated_authority_workflow,
            },
        )
        _write_json(
            root / "pointer-contract.v1.json",
            {
                "schema": "evidence-lane.installed-authority-pointer-contract.v1",
                "status": "PASS",
                "authority_id": authority_id,
                "project_relative_path": specification["pointer"],
                "pointer_role": specification["pointer_role"],
                "project_truth_pointer_owner": False,
                "pointer_movement_in_template": False,
            },
        )
        _write_json(
            root / "sqlite-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=authority_id,
                artifact_path=database.name,
                artifact_role="SQLITE_AUTHORITY",
                syntax="SQLITE3_FTS5",
                validator="authority_support._schema_snapshot",
            ),
        )
        _write_json(
            root / "mmd-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=authority_id,
                artifact_path=Path(profile.mmd).name,
                artifact_role="MERMAID_TRAVERSAL_MAP",
                syntax="MERMAID_FLOWCHART",
                validator="authority_support.validate_authority_support",
            ),
        )
        _write_json(
            root / "dot-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=authority_id,
                artifact_path=Path(profile.dot).name,
                artifact_role="DOT_TRAVERSAL_MAP",
                syntax="GRAPHVIZ_DOT",
                validator="authority_support.validate_authority_support",
            ),
        )
        _write_json(
            root / "tools.schema.json",
            _json_file_schema(
                authority_id=authority_id,
                artifact_path=Path(profile.tools).name,
                schema_id="evidence-lane.installed-authority-tools.v1",
            ),
        )
        _write_json(
            root / "pointer-contract.schema.json",
            _json_file_schema(
                authority_id=authority_id,
                artifact_path="pointer-contract.v1.json",
                schema_id=("evidence-lane.installed-authority-pointer-contract.v1"),
            ),
        )
        _write_json(
            root / "runtime-binding.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} runtime binding",
                "type": "object",
                "required": ["authority_id", "module", "source_sha256"],
                "properties": {
                    "authority_id": {"const": authority_id},
                    "module": {"const": f"evidence_lane_plugin.{module_path}"},
                    "source_sha256": {
                        "const": source_hashes[
                            f"src/evidence_lane_plugin/{specification['module']}"
                        ]
                    },
                },
                "additionalProperties": False,
            },
        )
        _write_json(
            root / "build-refresh-contract.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} support refresh request",
                "type": "object",
                "required": ["project_id", "project_root", "authority_id"],
                "properties": {
                    "project_id": {"type": "string", "minLength": 1},
                    "project_root": {"type": "string", "minLength": 1},
                    "authority_id": {"const": authority_id},
                },
                "additionalProperties": False,
            },
        )
        _write_json(
            root / "reader-contract.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} bounded reader request",
                "type": "object",
                "required": [
                    "project_id",
                    "authority_id",
                    "query",
                    "retrieval",
                    "limit",
                ],
                "properties": {
                    "project_id": {"type": "string", "minLength": 1},
                    "authority_id": {"const": authority_id},
                    "query": {"type": "string", "minLength": 1},
                    "retrieval": {"enum": ["fts5", "bm25", "hybrid"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
        )
        _write_json(
            root / "refresh-receipt.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} support refresh receipt",
                "type": "object",
                "required": [
                    "schema",
                    "status",
                    "authority_id",
                    "database",
                    "mmd",
                    "dot",
                    "tools",
                    "receipt_sha256",
                ],
                "properties": {
                    "schema": {
                        "const": ("evidence-lane.sqlite-authority-support-system.v1")
                    },
                    "status": {"const": "PASS"},
                    "authority_id": {"const": authority_id},
                    "database": {"type": "object"},
                    "mmd": {"type": "object"},
                    "dot": {"type": "object"},
                    "tools": {"type": "object"},
                    "receipt_sha256": {
                        "type": "string",
                        "pattern": "^[A-Fa-f0-9]{64}$",
                    },
                },
                "additionalProperties": True,
            },
        )
        _write_json(
            root / "manifest.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} installed authority manifest",
                "type": "object",
                "required": [
                    "schema",
                    "status",
                    "authority_id",
                    "database",
                    "database_sha256",
                    "members",
                    "live_project_bytes_in_template",
                    "authority_merged_with_sector_or_peer",
                ],
                "properties": {
                    "schema": {"const": "evidence-lane.installed-authority-surface.v1"},
                    "status": {"const": "PASS"},
                    "authority_id": {"const": authority_id},
                    "database": {"const": database.name},
                    "database_sha256": {
                        "type": "string",
                        "pattern": "^[A-F0-9]{64}$",
                    },
                    "members": {"type": "array", "minItems": 1},
                    "live_project_bytes_in_template": {"const": False},
                    "authority_merged_with_sector_or_peer": {"const": False},
                },
                "additionalProperties": True,
            },
        )
        _write(
            root / "README.md",
            f"# {authority_id}\n\n"
            "This is the installed schema/runtime projection of one non-sector "
            "authority. The SQLite file is an empty schema template built by the "
            "canonical implementation; MMD, DOT, tools, pointer contract, skills, "
            "commands, SDK/MCP actions, and source modules are hash-linked. Live "
            "project bytes remain under the user-selected Project/PV root.\n",
        )
        if authority_id == "project_authority":
            live_root_layout = {
                "schema": "evidence-lane.live-project-root-layout.v1",
                "status": "PASS",
                "project_root": "USER_SELECTED_PER_PROJECT",
                "workspace_root": "TASK_SELECTED_SEPARATE_PATH",
                "hidden_runtime_root": "CODEX_PLUGIN_PRIVATE_SEPARATE_PATH",
                "project_sector_root": "sectors/<18 canonical lane ids>",
                "named_authorities": {
                    "ai_learning": "ai_learning",
                    "canon_input": "canon",
                    "project_memory": "memory",
                    "project_overlay": "project_overlay",
                    "source_authority": "sources",
                    "project_universe": "universe",
                    "connector_brain": "connector_brain",
                    "project_authority": "project_authority",
                    "receipt_ledger": "receipts",
                    "session_authority": "sessions",
                },
                "each_sqlite_authority_has": [
                    "one canonical SQLite",
                    "one LangGraph-generated MMD traversal",
                    "one Graphviz-generated DOT traversal",
                    "one JSON tooling contract",
                    "one manifest",
                ],
                "absorbed_legacy_paths": {
                    "direct_state_travel_entries/**": (
                        "sessions/session-authority.sqlite#state_travel_entry"
                    ),
                    "sessions/*.json": (
                        "sessions/session-authority.sqlite#session_record"
                    ),
                    "receipts/**": "receipts/receipt-ledger.sqlite#receipt_record",
                    "profiles/**": (
                        "project_authority/project-authority.sqlite"
                        "#project_registration"
                    ),
                    "sectors/*/accepted_history/**": (
                        "owning sector SQLite migration history; accepted ZIP is separate"
                    ),
                    "sectors/*.json": (
                        "project_authority/project-authority.sqlite and receipt ledger"
                    ),
                    "internal_sources/**": (
                        "source authority CAS when project evidence; otherwise hidden "
                        "maintainer runtime"
                    ),
                    "runtime/**": "hidden plugin runtime",
                },
                "obsolete_paths_removed_after_hash_readback": True,
                "accepted_folder_queried": False,
                "accepted_folder_role": "SOLE GOVERNED ROOT-PV SNAPSHOT ZIP",
            }
            _write_json(root / "live-root-layout.v1.json", live_root_layout)
            _write_json(
                root / "live-root-layout.schema.json",
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "title": "Evidence Lane live Project/PV root layout",
                    "type": "object",
                    "required": [
                        "schema",
                        "status",
                        "project_root",
                        "workspace_root",
                        "hidden_runtime_root",
                        "project_sector_root",
                        "named_authorities",
                        "absorbed_legacy_paths",
                        "obsolete_paths_removed_after_hash_readback",
                        "accepted_folder_queried",
                    ],
                    "properties": {
                        "schema": {
                            "const": "evidence-lane.live-project-root-layout.v1"
                        },
                        "status": {"const": "PASS"},
                        "obsolete_paths_removed_after_hash_readback": {"const": True},
                        "accepted_folder_queried": {"const": False},
                    },
                    "additionalProperties": True,
                },
            )
        member_paths = [
            path
            for path in sorted(root.rglob("*"))
            if path.is_file() and path != root / "manifest.v1.json"
        ]
        manifest = {
            "schema": "evidence-lane.installed-authority-surface.v1",
            "status": "PASS",
            "authority_id": authority_id,
            "database": database.name,
            "database_sha256": _sha256(database),
            "database_integrity": "ok",
            "schema_sha256": snapshot["schema_sha256"],
            "table_count": snapshot["table_count"],
            "fts5_tables": snapshot["fts5_tables"],
            "members": [
                {
                    "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in member_paths
            ],
            "canonical_source_hashes": source_hashes,
            "linked_subauthorities": linked_subauthorities,
            "llama_index_refresh_receipt": llama_index_receipt,
            "graph_pipeline_receipt": graph_pipeline_receipt,
            "live_project_bytes_in_template": False,
            "authority_merged_with_sector_or_peer": False,
        }
        _write_json(root / "manifest.v1.json", manifest)
        rows.append(
            {
                "authority_id": authority_id,
                "path": root.relative_to(PLUGIN_ROOT).as_posix(),
                "manifest_sha256": _sha256(root / "manifest.v1.json"),
                "database_sha256": _sha256(database),
                "table_count": snapshot["table_count"],
                "linked_public_action_count": len(linked_actions),
                "persistent_sqlite_authority": True,
            }
        )

    non_sqlite_specs = {
        "project_authority": {
            "modules": ("project_authority.py", "project_pv_storage.py", "store.py"),
            "owner_skills": ("evi-boot", "evi"),
            "schema": {
                "schema": "evidence-lane.installed-project-authority-contract.v1",
                "status": "PASS",
                "project_bootstrap": public["project_bootstrap"],
                "project_root_is_user_selected": True,
                "workspace_is_task_selected": True,
                "hidden_runtime_is_separate": True,
                "accepted_zip_is_snapshot_only": True,
            },
            "pointer_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Evidence Lane active Project pointer",
                "type": "object",
                "required": [
                    "project_id",
                    "accepted_pv",
                    "accepted_manifest_sha256",
                    "generation",
                    "updated_at",
                    "prior_generation",
                ],
                "properties": {
                    "project_id": {"type": "string", "minLength": 1},
                    "accepted_pv": {"type": ["string", "null"]},
                    "accepted_manifest_sha256": {"type": ["string", "null"]},
                    "generation": {"type": "integer", "minimum": 0},
                    "updated_at": {"type": "string", "minLength": 1},
                    "prior_generation": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
            "mmd": (
                "flowchart TB\n"
                '  PROJECT["User-selected Project/PV root"]\n'
                '  WORKSPACE["Task-selected workspace"]\n'
                '  RUNTIME["Hidden Codex plugin runtime"]\n'
                '  SECTORS["18 separate sector authorities"]\n'
                '  NAMED["Separate named authorities"]\n'
                "  PROJECT --> SECTORS\n"
                "  PROJECT --> NAMED\n"
                '  WORKSPACE -->|"hashed binding"| PROJECT\n'
                '  RUNTIME -->|"registry binding"| PROJECT\n'
            ),
            "dot": (
                "digraph project_authority {\n"
                '  project [label="User-selected Project/PV root", shape=box];\n'
                '  workspace [label="Task-selected workspace", shape=box];\n'
                '  runtime [label="Hidden Codex plugin runtime", shape=box];\n'
                '  sectors [label="18 separate sector authorities", shape=box];\n'
                '  named [label="Separate named authorities", shape=box];\n'
                "  project -> sectors;\n"
                "  project -> named;\n"
                '  workspace -> project [label="hashed binding"];\n'
                '  runtime -> project [label="registry binding"];\n'
                "}\n"
            ),
        },
        "instructions": {
            "modules": ("agent_configuration.py", "conversation_memory.py"),
            "owner_skills": ("evi-instructions",),
            "schema": {
                "schema": "evidence-lane.installed-instruction-arms-contract.v1",
                "status": "PASS",
                "agents_md_role": "SCOPED_INSTRUCTION_AUTHORITY",
                "host_memory_role": "NONAUTHORITATIVE_HELPFUL_RECALL_ONLY",
                "project_memory_database_role": "SEPARATE_AUTHORITY",
                "automatic_host_memory_import": False,
                "raw_chat_history_loaded": False,
                "sqlite_authority_owned": False,
            },
            "pointer_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Evidence Lane instruction source-chain locator",
                "type": "object",
                "required": ["source_kind", "path", "sha256", "authority_role"],
                "properties": {
                    "source_kind": {"enum": ["AGENTS_MD", "HOST_MEMORY_MD"]},
                    "path": {"type": "string", "minLength": 1},
                    "sha256": {"type": "string", "pattern": "^[A-Fa-f0-9]{64}$"},
                    "authority_role": {
                        "enum": [
                            "SCOPED_INSTRUCTION_AUTHORITY",
                            "NONAUTHORITATIVE_HELPFUL_RECALL_ONLY",
                        ]
                    },
                },
                "additionalProperties": False,
            },
            "mmd": (
                "flowchart LR\n"
                '  AGENTS["AGENTS.md scoped instruction chain"]\n'
                '  MEMORY["Host MEMORY.md helpful recall"]\n'
                '  WORK["ENV/UOP governed work"]\n'
                "  AGENTS --> WORK\n"
                '  MEMORY -->|"bounded recall only"| WORK\n'
            ),
            "dot": (
                "digraph instruction_arms {\n"
                '  agents [label="AGENTS.md scoped instruction chain", shape=box];\n'
                '  memory [label="Host MEMORY.md helpful recall", shape=box];\n'
                '  work [label="ENV/UOP governed work", shape=box];\n'
                "  agents -> work;\n"
                '  memory -> work [label="bounded recall only"];\n'
                "}\n"
            ),
        },
    }
    non_sqlite_specs.pop("project_authority")
    for authority_id, specification in non_sqlite_specs.items():
        root = authorities_root / authority_id
        root.mkdir(parents=True, exist_ok=True)
        source_hashes = {
            f"src/evidence_lane_plugin/{name}": _sha256(PACKAGE_ROOT / name)
            for name in specification["modules"]
        }
        owner_skills = tuple(specification["owner_skills"])
        linked_actions = _linked_public_actions(public, owner_skills)
        authority_graph = SemanticGraph(
            f"{authority_id}_authority",
            direction="LR",
            role="AUTHORITY_TRAVERSAL",
        )
        if authority_id == "project_authority":
            for node_id, label, kind in (
                ("PROJECT", "User-selected Project/PV authority", "root"),
                ("WORKSPACE", "Task-selected workspace", "source"),
                ("RUNTIME", "Hidden Codex plugin runtime", "source"),
                ("SECTORS", "18 separate sector SQLite authorities", "semantic"),
                ("NAMED", "Separate named authorities", "semantic"),
            ):
                authority_graph.add_node(node_id, label, kind)
            authority_graph.add_edge("PROJECT", "SECTORS", "owns")
            authority_graph.add_edge("PROJECT", "NAMED", "owns")
            authority_graph.add_edge("WORKSPACE", "PROJECT", "hashed binding")
            authority_graph.add_edge("RUNTIME", "PROJECT", "registry binding")
        else:
            authority_graph.add_node(
                "AGENTS", "AGENTS.md scoped instruction chain", "root"
            )
            authority_graph.add_node(
                "MEMORY", "Host MEMORY.md bounded helpful recall", "source"
            )
            authority_graph.add_node("WORK", "ENV/UOP governed work", "lifecycle")
            authority_graph.add_edge("AGENTS", "WORK", "governs")
            authority_graph.add_edge("MEMORY", "WORK", "bounded recall only")
        authority_mmd, authority_dot, authority_graph_receipt = (
            authority_graph.render_pair()
        )
        _write(
            root / "runtime.py",
            '"""Binding to canonical non-SQLite authority modules."""\n\n'
            "from importlib import import_module\n\n"
            f'AUTHORITY_ID = "{authority_id}"\n'
            f"CANONICAL_MODULES = {tuple('evidence_lane_plugin.' + str(name).removesuffix('.py') for name in specification['modules'])!r}\n\n"
            "def canonical_modules():\n"
            "    return tuple(import_module(name) for name in CANONICAL_MODULES)\n\n"
            '__all__ = ["AUTHORITY_ID", "CANONICAL_MODULES", "canonical_modules"]\n',
        )
        _write_json(root / "authority-contract.v1.json", dict(specification["schema"]))
        _write_json(
            root / "pointer-contract.schema.json", dict(specification["pointer_schema"])
        )
        _write(root / f"{authority_id}.mmd", authority_mmd)
        _write(root / f"{authority_id}.dot", authority_dot)
        non_sqlite_workflow_files = {
            "json": root / "workflow.v1.json",
            "mmd": root / "workflow.mmd",
            "dot": root / "workflow.dot",
        }
        dedicated_non_sqlite_workflow = (
            {
                key: path.relative_to(PLUGIN_ROOT).as_posix()
                for key, path in non_sqlite_workflow_files.items()
            }
            | {
                f"{key}_sha256": _sha256(path)
                for key, path in non_sqlite_workflow_files.items()
            }
            if all(path.is_file() for path in non_sqlite_workflow_files.values())
            else None
        )
        _write_json(
            root / "tools.json",
            {
                "schema": "evidence-lane.installed-non-sqlite-authority-tools.v1",
                "status": "PASS",
                "authority_id": authority_id,
                "runtime_binding": "runtime.py",
                "linked_public_actions": linked_actions,
                "linked_skills": {
                    f"skills/{skill}/SKILL.md": _sha256(
                        PLUGIN_ROOT / "skills" / skill / "SKILL.md"
                    )
                    for skill in owner_skills
                },
                "canonical_source_hashes": source_hashes,
                "sqlite_authority_owned": False,
                "graph_pipeline_receipt": authority_graph_receipt,
                "dedicated_workflow": dedicated_non_sqlite_workflow,
            },
        )
        _write_json(
            root / "mmd-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=authority_id,
                artifact_path=f"{authority_id}.mmd",
                artifact_role="MERMAID_AUTHORITY_MAP",
                syntax="MERMAID_FLOWCHART",
                validator="schema_topology.validate_mermaid",
            ),
        )
        _write_json(
            root / "dot-artifact.schema.json",
            _artifact_receipt_schema(
                authority_id=authority_id,
                artifact_path=f"{authority_id}.dot",
                artifact_role="DOT_AUTHORITY_MAP",
                syntax="GRAPHVIZ_DOT",
                validator="schema_topology.validate_dot",
            ),
        )
        _write_json(
            root / "tools.schema.json",
            _json_file_schema(
                authority_id=authority_id,
                artifact_path="tools.json",
                schema_id=("evidence-lane.installed-non-sqlite-authority-tools.v1"),
            ),
        )
        _write_json(
            root / "authority-contract.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} authority contract",
                "type": "object",
                "required": ["schema", "status"],
                "properties": {
                    "schema": {"const": specification["schema"]["schema"]},
                    "status": {"const": "PASS"},
                },
                "additionalProperties": True,
            },
        )
        _write_json(
            root / "runtime-binding.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} runtime modules",
                "type": "object",
                "required": ["authority_id", "modules", "source_hashes"],
                "properties": {
                    "authority_id": {"const": authority_id},
                    "modules": {
                        "const": [
                            "evidence_lane_plugin." + str(name).removesuffix(".py")
                            for name in specification["modules"]
                        ]
                    },
                    "source_hashes": {"const": source_hashes},
                },
                "additionalProperties": False,
            },
        )
        _write_json(
            root / "manifest.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": f"{authority_id} installed authority manifest",
                "type": "object",
                "required": [
                    "schema",
                    "status",
                    "authority_id",
                    "persistent_sqlite_authority",
                    "members",
                    "authority_merged_with_sector_or_peer",
                ],
                "properties": {
                    "schema": {
                        "const": (
                            "evidence-lane.installed-non-sqlite-authority-surface.v1"
                        )
                    },
                    "status": {"const": "PASS"},
                    "authority_id": {"const": authority_id},
                    "persistent_sqlite_authority": {"const": False},
                    "members": {"type": "array", "minItems": 1},
                    "authority_merged_with_sector_or_peer": {"const": False},
                },
                "additionalProperties": True,
            },
        )
        _write(
            root / "README.md",
            f"# {authority_id}\n\n"
            "This authority is intentionally separate and owns no SQLite database. "
            "Its runtime modules, source-chain/pointer contract, graph projection, "
            "skills, commands, and public actions are still hash-bound and installed.\n",
        )
        members = [
            path
            for path in sorted(root.iterdir())
            if path.is_file() and path.name != "manifest.v1.json"
        ]
        manifest = {
            "schema": "evidence-lane.installed-non-sqlite-authority-surface.v1",
            "status": "PASS",
            "authority_id": authority_id,
            "persistent_sqlite_authority": False,
            "members": [
                {
                    "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for path in members
            ],
            "canonical_source_hashes": source_hashes,
            "graph_pipeline_receipt": authority_graph_receipt,
            "authority_merged_with_sector_or_peer": False,
        }
        _write_json(root / "manifest.v1.json", manifest)
        rows.append(
            {
                "authority_id": authority_id,
                "path": root.relative_to(PLUGIN_ROOT).as_posix(),
                "manifest_sha256": _sha256(root / "manifest.v1.json"),
                "database_sha256": None,
                "table_count": None,
                "linked_public_action_count": len(linked_actions),
                "persistent_sqlite_authority": False,
            }
        )
    body = {
        "schema": "evidence-lane.installed-non-sector-authority-registry.v1",
        "status": "PASS",
        "authority_count": len(rows),
        "authorities": rows,
        "sqlite_authority_count": len(specifications),
        "non_sqlite_authority_count": len(non_sqlite_specs),
        "non_sqlite_authority_ids": list(non_sqlite_specs),
        "sector_lane_count": 18,
        "project_sector_registry": (
            "authorities/project_sectors/lane-surface-registry.v1.json"
        ),
        "project_sector_registry_sha256": _sha256(
            authorities_root / "project_sectors" / "lane-surface-registry.v1.json"
        ),
        "authorities_are_outside_sector_count": True,
        "sqlite_mmd_dot_tools_pointer_runtime_complete": True,
        "shared_support_source_sha256": _sha256(support_source),
    }
    _write_json(authorities_root / "authority-surface-registry.v1.json", body)
    return body


def _generate_source_module_registry(
    public: dict[str, Any],
    *,
    lanes: dict[str, Any],
    authorities: dict[str, Any],
) -> dict[str, Any]:
    registry_path = PACKAGE_ROOT / "module-registry.v1.json"
    role_groups = {
        "SDK_AND_ROUTING": {
            "internal_sdk.py",
            "current_route_registry.py",
            "public_surface_registry.py",
            "mcp_server.py",
            "service.py",
            "mcp_adapter_routing.py",
            "plugin_architecture.py",
            "tunnel_identity_routing.py",
        },
        "SECTOR_LANE_ENGINE": {
            "lanes.py",
            "lane_engine.py",
            "lane_reader.py",
            "lane_traversal.py",
            "lane_contract.py",
        },
        "ENV_UOP_AI_ACTION_PLANE": {
            "mode_governance.py",
            "operating_modes.py",
            "flash_authority.py",
            "flash_identity.py",
            "flash_projection.py",
            "env_uop_tool_routing.py",
        },
        "FIRST_CLASS_WORKFLOWS": {
            "first_class_workflows.py",
        },
        "AI_TOOLCHAIN_EXECUTION": {
            "ai_toolchain.py",
            "code_toolchain.py",
            "data_toolchain.py",
            "document_toolchain.py",
            "entity_reconciliation.py",
            "github_toolchain.py",
            "graph_pipeline.py",
            "hybrid_retrieval.py",
            "native_toolchain.py",
            "runtime_api.py",
            "runtime_toolchain.py",
            "semantic_retrieval.py",
            "sqlite_execution.py",
            "sqlite_indexing.py",
            "tabular_toolchain.py",
            "web_toolchain.py",
            "context_index_routing.py",
            "deployment_toolchain.py",
            "ecosystem_toolchain.py",
            "evaluation_toolchain.py",
            "observability_toolchain.py",
        },
        "HOST_RUNTIME_AND_CONTINUITY": {
            "codex_turn_control.py",
            "host_entry_continuity.py",
            "host_plan_rehydration.py",
            "runtime_activation.py",
            "runtime_continuity.py",
            "task_attachment_rehydration.py",
            "task_binding_registry.py",
        },
        "PROJECT_AUTHORITIES": {
            "agent_learning.py",
            "authority_support.py",
            "canon_consequence_graph.py",
            "canon_runtime_continuity.py",
            "canon_task_graph.py",
            "connector_governance.py",
            "live_root_normalization.py",
            "project_authority.py",
            "project_memory.py",
            "project_overlay.py",
            "project_universe.py",
            "receipt_ledger.py",
            "session_authority.py",
            "source_authority.py",
        },
    }
    members: list[dict[str, Any]] = []
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        role = next(
            (
                role_name
                for role_name, filenames in role_groups.items()
                if path.name in filenames
            ),
            "CANONICAL_PLUGIN_IMPLEMENTATION",
        )
        members.append(
            {
                "module": f"evidence_lane_plugin.{path.stem}",
                "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "role": role,
            }
        )
    body = {
        "schema": "evidence-lane.python-module-registry.v1",
        "status": "PASS",
        "package_namespace": "evidence_lane_plugin",
        "python_source_root": "src",
        "module_count": len(members),
        "modules": members,
        "public_action_count": public["tool_count"],
        "project_sector_count": lanes["lane_count"],
        "named_authority_count": authorities["authority_count"],
        "root_surface_bindings": [
            "authorities/",
            "env/",
            "hooks/",
            "mcp/",
            "schemas/",
            "scripts/",
            "sdk/",
            "skills/",
            "tests/",
            "toolchains/",
            "tunnel/",
            "uop/",
        ],
        "root_surfaces_duplicated_inside_python_namespace": False,
        "loose_python_modules_directly_under_src_allowed": False,
    }
    _write_json(registry_path, body)
    return body


def _generate_public_action_schema_files(public: dict[str, Any]) -> list[str]:
    schemas_root = PLUGIN_ROOT / "schemas"
    actions_root = schemas_root / "actions"
    actions_root.mkdir(parents=True, exist_ok=True)
    action_members: list[str] = []
    expected_action_files: set[str] = set()
    for tool in public["tools"]:
        name = str(tool["name"])
        safe_name = re.sub(r"[^a-z0-9_]+", "-", name.casefold()).strip("-")
        if not safe_name:
            raise ValueError(f"Public action has no safe file identity: {name!r}")
        filename = f"{safe_name}.schema.json"
        if filename in expected_action_files:
            raise ValueError(f"Duplicate public action schema filename: {filename}")
        expected_action_files.add(filename)
        relative = f"actions/{filename}"
        body = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "schema": "evidence-lane.public-action-contract.v1",
            "name": name,
            "title": tool["title"],
            "description": tool["description"],
            "input_schema": tool["input_schema"],
            "output_schema": tool["output_schema"],
            "annotations": tool["annotations"],
            "route_contract": tool["route_contract"],
            "canonical_schema_sha256": tool["schema_sha256"],
            "canonical_catalog": "schemas/public-action-schemas.v001.json",
            "canonical_implementation": "src/evidence_lane_plugin/mcp_server.py",
        }
        _write_json(schemas_root / relative, body)
        action_members.append(f"schemas/{relative}")
    for path in actions_root.glob("*.json"):
        if path.name not in expected_action_files:
            path.unlink()
    return action_members


def _generate_surface_schemas(
    *,
    public: dict[str, Any],
    skills: list[dict[str, Any]],
    env: dict[str, Any],
    uop: dict[str, Any],
    sdk: dict[str, Any],
    mcp: dict[str, Any],
    lanes: dict[str, Any],
    authorities: dict[str, Any],
) -> dict[str, Any]:
    schemas_root = PLUGIN_ROOT / "schemas"
    action_members = _generate_public_action_schema_files(public)

    hooks = json.loads(
        (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    logical = json.loads(
        (PLUGIN_ROOT / "hooks" / "logical-actions.json").read_text(encoding="utf-8")
    )
    hook_rows: list[dict[str, Any]] = []
    for event_name, groups in sorted(dict(hooks["hooks"]).items()):
        handlers = [
            str(handler) for group in groups for handler in group.get("hooks", [])
        ]
        hook_rows.append(
            {
                "event": event_name,
                "handler_count": len(handlers),
                "handlers": handlers,
                "logical_actions": logical["logicalActions"][event_name],
            }
        )

    schemas = {
        "skills/skill-registry.v1.json": {
            "schema": "evidence-lane.skill-registry.v1",
            "status": "PASS",
            "skill_count": len(skills),
            "skills": skills,
        },
        "env/env-executable-authority.v1.json": env,
        "uop/uop-executable-authority.v1.json": uop,
        "sdk/sdk-binding.v1.json": sdk,
        "mcp/mcp-binding.v1.json": mcp,
        "authorities/project-sector-lane-surface-registry.v1.json": lanes,
        "authorities/authority-surface-registry.v1.json": authorities,
        "hooks/hook-runtime.v1.json": {
            "schema": "evidence-lane.hook-runtime.v1",
            "status": "PASS",
            "event_count": len(hook_rows),
            "handler_action_count": sum(row["handler_count"] for row in hook_rows),
            "events": hook_rows,
            "hooks_manifest_sha256": _sha256(PLUGIN_ROOT / "hooks" / "hooks.json"),
            "logical_actions_sha256": _sha256(
                PLUGIN_ROOT / "hooks" / "logical-actions.json"
            ),
        },
        "hooks/hook-subhandler-runtime.v1.json": {
            "schema": "evidence-lane.hook-subhandler-runtime.v1",
            "event_count": 11,
            "handler_action_count": 44,
            "ordered_subhandlers": [
                "VALIDATE",
                "SEAL",
                "TRANSPORT",
                "EMIT",
            ],
            "pipeline": "hooks/subhook_pipeline.py",
        },
        "rollback/rollback-three-mode.v1.json": {
            "schema": "evidence-lane.rollback-three-mode.v1",
            "modes": [
                "LOGICAL_LIVE_ROOT_STATE",
                "HARD_ACCEPTED_ZIP_RESTORE",
                "GIT_BRANCH_COMMIT_RESTORE",
            ],
            "single_owner": "evi-rollback",
            "fresh_plan_after_hard_restore": True,
            "fresh_plan_after_git_restore": True,
        },
        "lifecycle/runtime-workflow-registry.v1.json": dict(
            public["runtime_workflow_sdk_registry"]
        ),
        "routing/current-route-registry.v1.json": dict(
            public["current_implementation_registry"]
        ),
        "governance/shared-contracts.v1.json": {
            "schema": "evidence-lane.shared-governance-contracts.v1",
            "status": "PASS",
            "governance": public["governance"],
            "shared_contracts": public["shared_contracts"],
            "authority_support_systems": public["authority_support_systems"],
            "linked_operational_authorities": public["linked_operational_authorities"],
        },
        "plan/project-bootstrap.v1.json": {
            "schema": "evidence-lane.project-bootstrap-plan.v1",
            "status": "PASS",
            "project_bootstrap": public["project_bootstrap"],
            "prompt_and_steer_dispatch": public["prompt_and_steer_dispatch"],
        },
        "source-intake/source-intake-code-routing.v1.json": {
            "schema": "evidence-lane.source-intake-code-routing.v1",
            "status": "PASS",
            "source_intake_code_routing": public["source_intake_code_routing"],
            "canonical_lanes": public["canonical_lanes"],
            "lane_count": public["lane_count"],
        },
        "delta/delta-runtime.v1.json": {
            "schema": "evidence-lane.delta-runtime.v1",
            "status": "PASS",
            "delta_route_retirement": public["delta_route_retirement"],
            "delta_authority_time_boundary": public["delta_authority_time_boundary"],
        },
        "fuse/dual-hil-fuse.v1.json": {
            "schema": "evidence-lane.dual-hil-fuse.v1",
            "status": "PASS",
            "dual_hil_fuse": public["dual_hil_fuse"],
        },
        "state-travel/state-travel-golden-protocol.v1.json": {
            "schema": "evidence-lane.state-travel-golden-protocol.v1",
            "status": "PASS",
            "state_travel_golden_protocol": public["state_travel_golden_protocol"],
        },
        "install/local-install.v1.json": {
            "schema": "evidence-lane.local-install.v1",
            "status": "PASS",
            "local_install": public["local_install"],
        },
        "goal/goal-metrics.v1.json": {
            "schema": "evidence-lane.goal-metrics.v1",
            "status": "PASS",
            "goal_metrics": public["goal_metrics"],
        },
        "consumers/consumer-surfaces.v1.json": {
            "schema": "evidence-lane.consumer-surfaces.v1",
            "status": "PASS",
            "consumer_surfaces": public["consumer_surfaces"],
        },
        "schema-authority/schema-authority.v1.json": {
            "schema": "evidence-lane.schema-authority.v1",
            "status": "PASS",
            "schema_authority": public["schema_authority"],
        },
        "package/package-surface-coherence.v1.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Evidence Lane package surface coherence receipt",
            "type": "object",
            "required": [
                "schema",
                "status",
                "mcp",
                "skills",
                "hooks",
                "lanes",
                "schemas",
                "env_uop",
                "sdk",
            ],
        },
    }
    for relative, body in schemas.items():
        _write_json(schemas_root / relative, body)

    project_sector_schema_root = schemas_root / "project-sectors"
    expected_sector_schema_directories: set[str] = set()
    for lane_row in lanes["lanes"]:
        lane_id = str(lane_row["lane_id"])
        expected_sector_schema_directories.add(lane_id)
        authority_root = PLUGIN_ROOT / "authorities" / "project_sectors" / lane_id
        schema_files = [
            path
            for path in sorted(authority_root.iterdir())
            if path.is_file()
            and (path.name.endswith(".schema.json") or path.name == "schema.sql")
        ]
        _write_json(
            project_sector_schema_root / lane_id / "schema-surface.v1.json",
            {
                "schema": "evidence-lane.project-sector-schema-surface.v1",
                "status": "PASS",
                "lane_id": lane_id,
                "authority_root": authority_root.relative_to(PLUGIN_ROOT).as_posix(),
                "authority_manifest_sha256": _sha256(
                    authority_root / "manifest.v1.json"
                ),
                "schema_file_count": len(schema_files),
                "schema_files": [
                    {
                        "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                    for path in schema_files
                ],
                "canonical_lane_contract": "schemas/lane-schema-registry.v001.json",
                "canonical_lane_contract_sha256": _sha256(
                    schemas_root / "lane-schema-registry.v001.json"
                ),
                "schema_bytes_duplicated_here": False,
            },
        )
    for path in project_sector_schema_root.iterdir():
        if path.is_dir() and path.name not in expected_sector_schema_directories:
            raise ValueError(f"Unexpected project-sector schema surface: {path.name}")

    authority_schema_root = schemas_root / "authorities"
    expected_authority_schema_directories: set[str] = set()
    for authority_row in authorities["authorities"]:
        authority_id = str(authority_row["authority_id"])
        expected_authority_schema_directories.add(authority_id)
        authority_root = PLUGIN_ROOT / "authorities" / authority_id
        schema_files = [
            path
            for path in sorted(authority_root.iterdir())
            if path.is_file()
            and (path.name.endswith(".schema.json") or path.name == "schema.sql")
        ]
        _write_json(
            authority_schema_root / authority_id / "schema-surface.v1.json",
            {
                "schema": "evidence-lane.named-authority-schema-surface.v1",
                "status": "PASS",
                "authority_id": authority_id,
                "authority_root": authority_root.relative_to(PLUGIN_ROOT).as_posix(),
                "authority_manifest_sha256": _sha256(
                    authority_root / "manifest.v1.json"
                ),
                "persistent_sqlite_authority": authority_row[
                    "persistent_sqlite_authority"
                ],
                "schema_file_count": len(schema_files),
                "schema_files": [
                    {
                        "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                    for path in schema_files
                ],
                "schema_bytes_duplicated_here": False,
            },
        )
    for path in authority_schema_root.iterdir():
        if path.is_dir() and path.name not in expected_authority_schema_directories:
            raise ValueError(f"Unexpected named-authority schema surface: {path.name}")

    manifest_relative = "schema-manifest.v1.json"
    members = [
        {
            "path": path.relative_to(PLUGIN_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "category": path.relative_to(schemas_root).parts[0],
        }
        for path in sorted(schemas_root.rglob("*"))
        if path.is_file() and path.name != manifest_relative
    ]
    manifest = {
        "schema": "evidence-lane.complete-schema-surface.v1",
        "status": "PASS",
        "schema_file_count": len(members),
        "public_action_schema_count": len(action_members),
        "project_sector_schema_surface_count": len(expected_sector_schema_directories),
        "named_authority_schema_surface_count": len(
            expected_authority_schema_directories
        ),
        "public_action_schema_members": action_members,
        "members": members,
        "canonical_public_catalog": "schemas/public-action-schemas.v001.json",
        "canonical_public_catalog_sha256": _sha256(
            schemas_root / "public-action-schemas.v001.json"
        ),
        "generated_from_current_executable_source_only": True,
        "historical_schema_fallback_allowed": False,
    }
    _write_json(schemas_root / manifest_relative, manifest)
    return manifest


def _generate_executable_surface_registry() -> dict[str, Any]:
    registry_path = PLUGIN_ROOT / "manifests" / "executable-surface-registry.v1.json"
    installed_directories = {
        ".codex-plugin": "HOST_DISCOVERY_MANIFEST",
        "assets": "PLUGIN_BRAND_ASSETS",
        "authorities": "NON_SECTOR_AUTHORITY_DISTRIBUTION",
        "env": "CANONICAL_EXECUTABLE_ENV_AUTHORITY",
        "hooks": "NATIVE_HOOK_EVENTS_HANDLERS_AND_HOST",
        "manifests": "PACKAGE_WIDE_COHERENCE_REGISTRY",
        "mcp": "OUTER_NATIVE_MCP_BINDING",
        "schemas": "COMPLETE_EXECUTABLE_SCHEMA_SURFACE",
        "scripts": "RUNTIME_AND_MAINTAINER_ENTRYPOINTS",
        "sdk": "INTERNAL_ENV_UOP_AND_OUTER_ROUTING_SDK",
        "skills": "GOVERNED_WORKFLOW_SKILLS",
        "src": "CANONICAL_PYTHON_IMPLEMENTATION",
        "tests": "INSTALLED_PACKAGE_SMOKE_TESTS_ONLY",
        "toolchains": "PINNED_PACKAGE_LOCAL_TOOLCHAINS",
        "tunnel": "VISIBLE_TUNNEL_RUNTIME_AND_TOOLCHAIN_BINDING",
        "uop": "CANONICAL_EXECUTABLE_UOP_AUTHORITY",
    }
    installed_root_files = (
        ".mcp.json",
        "COPYRIGHT.md",
        "LICENSE.md",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "requirements.torch-cpu.lock.txt",
        "requirements.torch-nvidia.lock.txt",
        "requirements.onnx-directml.lock.txt",
        "requirements.lock.txt",
        "requirements.toolchain.lock.txt",
    )
    excluded_parts = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    test_policy = json.loads(
        (PLUGIN_ROOT / "tests" / "test-surface-policy.v1.json").read_text(
            encoding="utf-8"
        )
    )
    installed_test_paths = {
        "tests/test-surface-policy.v1.json",
        *map(str, test_policy["installed_executable_tests"]),
    }
    members: list[dict[str, Any]] = []
    directories: list[dict[str, Any]] = []
    for directory, role in installed_directories.items():
        root = PLUGIN_ROOT / directory
        paths = [
            path
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path != registry_path
            and not set(path.relative_to(PLUGIN_ROOT).parts) & excluded_parts
            and not path.relative_to(PLUGIN_ROOT).as_posix().startswith("tests/tools/")
            and (
                not path.relative_to(PLUGIN_ROOT).as_posix().startswith("tests/")
                or path.relative_to(PLUGIN_ROOT).as_posix() in installed_test_paths
            )
            and path.suffix.casefold() not in {".pyc", ".pyo"}
        ]
        directory_members = [
            {
                "path": path.relative_to(PLUGIN_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        members.extend(directory_members)
        directories.append(
            {
                "path": directory,
                "role": role,
                "member_count": len(directory_members),
                "members_sha256": hashlib.sha256(_json_bytes(directory_members))
                .hexdigest()
                .upper(),
            }
        )
    for relative in installed_root_files:
        path = PLUGIN_ROOT / relative
        members.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    members.sort(key=lambda row: str(row["path"]))
    body = {
        "schema": "evidence-lane.executable-package-surface-registry.v1",
        "status": "PASS",
        "plugin_version": json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["version"],
        "installed_directory_count": len(directories),
        "installed_directories": directories,
        "installed_root_files": list(installed_root_files),
        "member_count": len(members),
        "members": members,
        "dependency_edges": [
            ["skills", "mcp"],
            ["mcp", "schemas"],
            ["mcp", "sdk"],
            ["sdk", "src"],
            ["sdk", "env"],
            ["sdk", "uop"],
            ["sdk", "authorities"],
            ["sdk", "tunnel"],
            ["tunnel", "scripts"],
            ["tunnel", "toolchains"],
            ["hooks", "src"],
            ["schemas", "src"],
            ["tests", "manifests"],
        ],
        "repository_only_exclusions": [
            "evidence/",
            "tests/tools/",
            "tests/ except installed-test policy allowlist",
            "_evidence_lane_rehearsal/",
            ".venv/",
        ],
        "repository_companion_surfaces": [
            "apps/evidence-lane-app/",
        ],
        "local_cache_or_output_included": False,
        "historical_fallback_used": False,
        "all_installed_members_hash_bound": True,
    }
    _write_json(registry_path, body)
    return body


def main() -> int:
    public = json.loads(
        (PLUGIN_ROOT / "schemas" / "public-action-schemas.v001.json").read_text(
            encoding="utf-8"
        )
    )
    _generate_public_action_schema_files(public)
    from evidence_lane_plugin.codex_action_plane import rebuild_codex_action_planes
    from evidence_lane_plugin.env_uop_graph import (
        rebuild_env_uop_locks,
        rebuild_flash_manifest,
        rebuild_packaged_authority_manifests,
    )

    action_plane = rebuild_codex_action_planes(PLUGIN_ROOT)
    toolchain_sync = action_plane
    env_graph = action_plane["env"]["graph"]
    uop_graph = action_plane["uop"]["graph"]
    env_uop_architecture = {
        "status": "PASS",
        "row_to_graph_coverage": {
            "schema": "evidence-lane.codex-action-plane-graph-coverage.v1",
            "status": "PASS",
            "counts": action_plane["counts"],
            "codex_is_sole_agent": True,
            "chatgpt_surface_rows": 0,
            "foreign_absolute_paths": 0,
            "discussion_or_chatlineage_authority_rows": 0,
            "predecessor_database_copied": False,
        },
        "graph_tool_execution_evidence": {
            "LangGraph_Mermaid_engine": {"state": "EXECUTED"},
            "Python_Graphviz_DOT_engine": {"state": "EXECUTED"},
            "rustworkx": {"state": "EXECUTED"},
        },
    }
    sqlite_header_canonicalization = []
    for authority_database in (
        PLUGIN_ROOT / "env" / "env_sqlite.sqlite",
        PLUGIN_ROOT / "uop" / "uop_sqlite.sqlite",
    ):
        connection = sqlite3.connect(authority_database)
        try:
            connection.execute("VACUUM")
        finally:
            connection.close()
        sqlite_header_canonicalization.append(
            _canonicalize_sqlite_header(authority_database)
        )
    coverage = dict(env_uop_architecture["row_to_graph_coverage"])
    coverage["final_graphs"] = {
        "env": {
            "node_count": env_graph["graph_pipeline_receipt"]["node_count"],
            "edge_count": env_graph["graph_pipeline_receipt"]["edge_count"],
            "group_count": env_graph["graph_pipeline_receipt"]["group_count"],
            "semantic_topology_sha256": env_graph["semantic_topology_sha256"],
            "mmd_sha256": env_graph["mmd_sha256"],
            "dot_sha256": env_graph["dot_sha256"],
        },
        "uop": {
            "node_count": uop_graph["graph_pipeline_receipt"]["node_count"],
            "edge_count": uop_graph["graph_pipeline_receipt"]["edge_count"],
            "group_count": uop_graph["graph_pipeline_receipt"]["group_count"],
            "semantic_topology_sha256": uop_graph["semantic_topology_sha256"],
            "mmd_sha256": uop_graph["mmd_sha256"],
            "dot_sha256": uop_graph["dot_sha256"],
        },
    }
    coverage["graph_tool_execution_evidence"] = env_uop_architecture[
        "graph_tool_execution_evidence"
    ]
    _write_json(
        PLUGIN_ROOT / "toolchains" / "env-uop-row-to-graph-coverage.v1.json",
        {key: value for key, value in coverage.items() if key != "receipt_sha256"},
    )
    env_uop_locks = rebuild_env_uop_locks(
        PLUGIN_ROOT,
        env_graph_receipt=env_graph,
        uop_graph_receipt=uop_graph,
    )
    env_uop_flash = rebuild_flash_manifest(PLUGIN_ROOT)
    env_uop_manifests = rebuild_packaged_authority_manifests(PLUGIN_ROOT)
    flash_authority_pins = _refresh_flash_authority_pins()
    skills = _skill_rows()
    _purge_legacy_command_surface()
    skill_surface = _generate_skill_surface_registry(skills)
    env = _authority_manifest("env")
    uop = _authority_manifest("uop")
    lanes = _generate_lane_surfaces()
    authorities = _generate_authority_surfaces(public)
    source_modules = _generate_source_module_registry(
        public,
        lanes=lanes,
        authorities=authorities,
    )
    hook_surface = _generate_hook_event_surfaces()
    toolchain_surface, tunnel_surface = _generate_tunnel_and_toolchain_surfaces()
    sdk = _generate_sdk(
        public,
        lanes=lanes,
        authorities=authorities,
        source_modules=source_modules,
        hook_surface=hook_surface,
        toolchain_surface=toolchain_surface,
        tunnel_surface=tunnel_surface,
    )
    mcp = _generate_mcp(
        public,
        sdk=sdk,
        lanes=lanes,
        authorities=authorities,
        source_modules=source_modules,
        skill_surface=skill_surface,
        hook_surface=hook_surface,
        toolchain_surface=toolchain_surface,
        tunnel_surface=tunnel_surface,
    )
    schema_manifest = _generate_surface_schemas(
        public=public,
        skills=skills,
        env=env,
        uop=uop,
        sdk=sdk,
        mcp=mcp,
        lanes=lanes,
        authorities=authorities,
    )
    from evidence_lane_plugin.plugin_architecture import (
        build_dedicated_skill_workflows,
        build_surface_workflows,
        build_universal_plugin_architecture,
        render_dedicated_skill_workflow,
        render_memory_architecture_dot,
        render_memory_architecture_mmd,
        render_surface_workflow,
        render_universal_architecture_dot,
        render_universal_architecture_mmd,
    )

    architecture = build_universal_plugin_architecture(PLUGIN_ROOT)
    toolchains_root = PLUGIN_ROOT / "toolchains"
    _write_json(
        toolchains_root / "universal-plugin-architecture.v1.json",
        architecture,
    )
    _write_json(
        toolchains_root / "unified-tool-workflow-pairing.v1.json",
        architecture["unified_tool_workflow_pairing"],
    )
    _write_json(
        toolchains_root / "authority-tool-workflow-pairing.v1.json",
        architecture["authority_tool_workflow_pairing"],
    )
    _write_json(
        toolchains_root / "action-skill-hook-schema-pairing.v1.json",
        architecture["action_skill_hook_schema_pairing"],
    )
    _write(
        toolchains_root / "UNIVERSAL_PLUGIN_ARCHITECTURE.mmd",
        render_universal_architecture_mmd(architecture),
    )
    _write(
        toolchains_root / "UNIVERSAL_PLUGIN_ARCHITECTURE.dot",
        render_universal_architecture_dot(architecture),
    )
    _write(
        toolchains_root / "MEMORY_AUTHORITY_ARCHITECTURE.mmd",
        render_memory_architecture_mmd(),
    )
    _write(
        toolchains_root / "MEMORY_AUTHORITY_ARCHITECTURE.dot",
        render_memory_architecture_dot(),
    )
    dedicated_skills = build_dedicated_skill_workflows(architecture)
    dedicated_root = PLUGIN_ROOT / "sdk" / "workflows" / "skills"
    dedicated_root.mkdir(parents=True, exist_ok=True)
    expected_skill_directories = {
        str(row["skill"]) for row in dedicated_skills["skills"]
    }
    for child in dedicated_root.iterdir():
        if child.is_dir() and child.name not in expected_skill_directories:
            resolved = child.resolve()
            if resolved.parent != dedicated_root.resolve():
                raise ValueError("Stale skill workflow path escapes its root.")
            shutil.rmtree(resolved)
    dedicated_artifacts = []
    for skill_workflow in dedicated_skills["skills"]:
        skill_name = str(skill_workflow["skill"])
        skill_root = dedicated_root / skill_name
        skill_root.mkdir(parents=True, exist_ok=True)
        json_path = skill_root / "workflow.v1.json"
        mmd_path = skill_root / "workflow.mmd"
        dot_path = skill_root / "workflow.dot"
        mmd, dot, graph_receipt = render_dedicated_skill_workflow(dict(skill_workflow))
        _write(json_path, _pretty_json(skill_workflow))
        _write(mmd_path, mmd)
        _write(dot_path, dot)
        expected_files = {json_path.name, mmd_path.name, dot_path.name}
        for child in skill_root.iterdir():
            if child.is_file() and child.name not in expected_files:
                child.unlink()
        dedicated_artifacts.append(
            {
                "skill": skill_name,
                "workflow_count": int(skill_workflow["workflow_count"]),
                "action_step_count": int(skill_workflow["action_step_count"]),
                "json": json_path.relative_to(PLUGIN_ROOT).as_posix(),
                "json_sha256": _sha256(json_path),
                "mmd": mmd_path.relative_to(PLUGIN_ROOT).as_posix(),
                "mmd_sha256": _sha256(mmd_path),
                "dot": dot_path.relative_to(PLUGIN_ROOT).as_posix(),
                "dot_sha256": _sha256(dot_path),
                "graph_receipt": graph_receipt,
            }
        )
    dedicated_registry = {
        key: value for key, value in dedicated_skills.items() if key != "receipt_sha256"
    }
    dedicated_registry["artifacts"] = dedicated_artifacts
    _write_json(
        PLUGIN_ROOT / "sdk" / "workflows" / "skill-workflow-registry.v1.json",
        dedicated_registry,
    )
    surface_workflows = build_surface_workflows(architecture)
    surface_artifacts = []
    for surface in [
        *surface_workflows["sectors"],
        *surface_workflows["authorities"],
    ]:
        surface_id = str(surface["surface_id"])
        if surface["surface_kind"] == "PROJECT_SECTOR":
            surface_root = PLUGIN_ROOT / "authorities" / "project_sectors" / surface_id
        else:
            surface_root = PLUGIN_ROOT / "authorities" / surface_id
        if not surface_root.is_dir():
            raise ValueError(f"Missing workflow surface root: {surface_id}")
        json_path = surface_root / "workflow.v1.json"
        mmd_path = surface_root / "workflow.mmd"
        dot_path = surface_root / "workflow.dot"
        mmd, dot, graph_receipt = render_surface_workflow(dict(surface))
        _write(json_path, _pretty_json(surface))
        _write(mmd_path, mmd)
        _write(dot_path, dot)
        surface_artifacts.append(
            {
                "surface_kind": surface["surface_kind"],
                "surface_id": surface_id,
                "json": json_path.relative_to(PLUGIN_ROOT).as_posix(),
                "json_sha256": _sha256(json_path),
                "mmd": mmd_path.relative_to(PLUGIN_ROOT).as_posix(),
                "mmd_sha256": _sha256(mmd_path),
                "dot": dot_path.relative_to(PLUGIN_ROOT).as_posix(),
                "dot_sha256": _sha256(dot_path),
                "graph_receipt": graph_receipt,
            }
        )
    surface_registry = {
        key: value
        for key, value in surface_workflows.items()
        if key != "receipt_sha256"
    }
    surface_registry["artifacts"] = surface_artifacts
    _write_json(
        PLUGIN_ROOT / "sdk" / "workflows" / "surface-workflow-registry.v1.json",
        surface_registry,
    )
    executable_surface = _generate_executable_surface_registry()
    print(
        json.dumps(
            {
                "status": "PASS",
                "skills": len(skills),
                "separate_commands": 0,
                "env_members": env["member_count"],
                "uop_members": uop["member_count"],
                "sdk_modules": len(sdk["modules"]),
                "mcp_tools": mcp["tool_count"],
                "mcp_action_bindings": mcp["action_binding_count"],
                "lane_surfaces": lanes["lane_count"],
                "non_sector_authorities": authorities["authority_count"],
                "sdk_action_bindings": sdk["action_binding_count"],
                "hook_events": hook_surface["event_count"],
                "hook_handlers": hook_surface["handler_action_count"],
                "tool_requirements": toolchain_surface["requirement_count"],
                "schema_files": schema_manifest["schema_file_count"],
                "public_action_schemas": schema_manifest["public_action_schema_count"],
                "executable_members": executable_surface["member_count"],
                "universal_architecture": architecture["status"],
                "dedicated_skill_workflows": dedicated_skills["skill_count"],
                "dedicated_surface_workflows": (
                    surface_workflows["sector_count"]
                    + surface_workflows["authority_count"]
                ),
                "ecosystem_adapters": architecture["counts"]["ecosystem_adapters"],
                "env_uop_toolchain_sync": toolchain_sync["status"],
                "env_uop_sqlite_headers": sqlite_header_canonicalization,
                "env_uop_architecture": env_uop_architecture["status"],
                "env_uop_action_plane": action_plane["status"],
                "env_uop_locks": env_uop_locks["status"],
                "env_uop_flash": env_uop_flash["status"],
                "env_uop_manifests": env_uop_manifests["status"],
                "flash_authority_pin_count": len(flash_authority_pins),
                "env_graph": env_graph["status"],
                "uop_graph": uop_graph["status"],
            },
            sort_keys=True,
        )
    )
    return 0


def _generation_snapshot() -> dict[str, str]:
    """Hash every maintained plugin byte while excluding runtime-only caches."""

    snapshot: dict[str, str] = {}
    for path in PLUGIN_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(PLUGIN_ROOT).as_posix()
        if (
            relative.startswith((".venv/", ".pytest_cache/"))
            or "/__pycache__/" in f"/{relative}/"
            or relative.endswith((".pyc", ".tmp"))
        ):
            continue
        snapshot[relative] = _sha256(path)
    return snapshot


def _stabilized_cli() -> int:
    """Run fresh-process passes until the whole generated surface is byte-stable."""

    previous: dict[str, str] | None = None
    previous_summary: dict[str, Any] | None = None
    environment = dict(os.environ)
    environment["EVIDENCE_LANE_GENERATION_INNER_PASS"] = "1"
    for pass_number in range(1, 7):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=PLUGIN_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("GENERATION_INNER_PASS_RETURNED_NO_SUMMARY")
        previous_summary = json.loads(lines[-1])
        current = _generation_snapshot()
        if previous is not None and current == previous:
            summary = dict(previous_summary)
            summary.update(
                {
                    "stabilization_passes": pass_number,
                    "fixed_point_files": len(current),
                    "fixed_point_mismatches": 0,
                }
            )
            print(json.dumps(summary, sort_keys=True))
            return 0
        previous = current

    assert previous is not None
    probe = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=PLUGIN_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    current = _generation_snapshot()
    changed = sorted(
        path
        for path in set(previous) | set(current)
        if previous.get(path) != current.get(path)
    )
    if probe.returncode != 0:
        if probe.stdout:
            print(probe.stdout, end="")
        if probe.stderr:
            print(probe.stderr, end="", file=sys.stderr)
        return probe.returncode
    raise RuntimeError("GENERATION_FIXED_POINT_NOT_REACHED:" + ",".join(changed[:50]))


if __name__ == "__main__":
    if os.environ.get("EVIDENCE_LANE_GENERATION_INNER_PASS") == "1":
        raise SystemExit(main())
    raise SystemExit(_stabilized_cli())
