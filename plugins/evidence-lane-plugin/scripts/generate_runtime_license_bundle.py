#!/usr/bin/env python3
"""Seal licenses/notices for the exact installed Python runtime."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
from pathlib import Path
from typing import Any


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


def _safe_name(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-" for character in value
    ).strip("-")
    return "-".join(part for part in normalized.split("-") if part)


def build_runtime_license_bundle(output_root: Path, plugin_root: Path) -> dict[str, Any]:
    output = output_root.resolve()
    plugin = plugin_root.resolve()
    if output.exists():
        raise RuntimeError("The runtime license output must be fresh.")
    staging = output.parent / f".{output.name}.staging"
    if staging.exists():
        raise RuntimeError("A runtime license staging directory already exists.")
    staging.mkdir(parents=True)
    distributions: list[dict[str, Any]] = []
    try:
        tool_license_path = plugin / "toolchains" / "tool-license-inventory.v1.json"
        tool_license_inventory = json.loads(
            tool_license_path.read_text(encoding="utf-8")
        )
        if (
            tool_license_inventory.get("schema")
            != "evidence-lane.tool-license-inventory.v1"
            or tool_license_inventory.get("status") != "PASS"
            or tool_license_inventory.get("all_tool_requirements_classified")
            is not True
            or int(tool_license_inventory.get("tool_requirement_count") or 0)
            != len(tool_license_inventory.get("rows") or [])
            or int(
                tool_license_inventory.get("requirement_license_record_count")
                or 0
            )
            != int(tool_license_inventory.get("tool_requirement_count") or 0)
            or tool_license_inventory.get(
                "all_tool_requirements_have_physical_license_records"
            )
            is not True
        ):
            raise RuntimeError("The tool license inventory is incomplete.")
        for row in tool_license_inventory["rows"]:
            record_path = plugin / str(row.get("license_record") or "")
            expected_record_sha256 = str(
                row.get("license_record_sha256") or ""
            ).upper()
            if (
                not record_path.is_file()
                or _sha256(record_path) != expected_record_sha256
            ):
                raise RuntimeError(
                    f"The physical license record drifted for {row.get('tool')}."
                )
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if (
                record.get("schema")
                != "evidence-lane.tool-requirement-license-record.v1"
                or record.get("status") != "PASS"
                or record.get("tool") != row.get("tool")
                or record.get(
                    "metadata_without_a_license_file_is_labeled_metadata_only"
                )
                is not True
            ):
                raise RuntimeError(
                    f"The physical license record is invalid for {row.get('tool')}."
                )
        installed = sorted(
            importlib.metadata.distributions(),
            key=lambda item: (
                str(item.metadata.get("Name") or "").casefold(),
                str(item.version),
            ),
        )
        for distribution in installed:
            name = str(distribution.metadata.get("Name") or "UNKNOWN")
            version = str(distribution.version)
            target = staging / f"{_safe_name(name)}-{_safe_name(version)}"
            license_rows: list[dict[str, Any]] = []
            for relative in distribution.files or ():
                parts = [part.casefold() for part in Path(str(relative)).parts]
                filename = Path(str(relative)).name.casefold()
                metadata_boundary = any(
                    part.endswith(".dist-info") or part == "licenses"
                    for part in parts
                )
                is_license = (
                    metadata_boundary
                    and Path(filename).suffix not in {".py", ".pyc", ".pyo"}
                    and (
                        "licenses" in parts
                        or filename.startswith("license")
                        or filename.startswith("copying")
                        or filename.startswith("notice")
                        or filename.startswith("authors")
                    )
                )
                if not is_license:
                    continue
                source = Path(distribution.locate_file(relative)).resolve()
                if not source.is_file():
                    continue
                target.mkdir(parents=True, exist_ok=True)
                destination = target / f"{len(license_rows) + 1:03d}-{source.name}"
                shutil.copy2(source, destination)
                license_rows.append(
                    {
                        "path": destination.relative_to(staging).as_posix(),
                        "bytes": destination.stat().st_size,
                        "sha256": _sha256(destination),
                    }
                )
            classifiers = [
                value
                for value in distribution.metadata.get_all("Classifier", [])
                if value.startswith("License ::")
            ]
            distributions.append(
                {
                    "name": name,
                    "version": version,
                    "license_expression": distribution.metadata.get(
                        "License-Expression"
                    ),
                    "license_metadata": distribution.metadata.get("License"),
                    "license_classifiers": classifiers,
                    "license_file_count": len(license_rows),
                    "license_files": license_rows,
                }
            )
        manifest_body = {
            "schema": "evidence-lane.installed-runtime-license-bundle.v1",
            "status": "PASS",
            "plugin_root_sha256": hashlib.sha256(
                str(plugin).encode("utf-8")
            ).hexdigest().upper(),
            "requirements_lock_sha256": _sha256(plugin / "requirements.lock.txt"),
            "requirements_toolchain_lock_sha256": _sha256(
                plugin / "requirements.toolchain.lock.txt"
            ),
            "tool_license_inventory_sha256": _sha256(tool_license_path),
            "tool_license_entry_count": int(
                tool_license_inventory["tool_requirement_count"]
            ),
            "all_tool_requirements_license_classified": True,
            "all_tool_requirements_have_physical_license_records": True,
            "tool_requirement_license_record_count": int(
                tool_license_inventory["requirement_license_record_count"]
            ),
            "mcp_inventory_separate": True,
            "distribution_count": len(distributions),
            "distribution_with_license_files_count": sum(
                row["license_file_count"] > 0 for row in distributions
            ),
            "distributions": distributions,
            "missing_license_files_preserved_as_metadata_not_zero": True,
        }
        manifest = {
            **manifest_body,
            "receipt_sha256": hashlib.sha256(_json_bytes(manifest_body))
            .hexdigest()
            .upper(),
        }
        manifest_path = staging / "manifest.v1.json"
        manifest_path.write_bytes(_json_bytes(manifest))
        staging.replace(output)
        return {
            **manifest,
            "output": str(output),
            "manifest_sha256": _sha256(output / "manifest.v1.json"),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plugin-root", required=True, type=Path)
    args = parser.parse_args()
    result = build_runtime_license_bundle(args.output, args.plugin_root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
