"""Bounded ecosystem-aware dependency manifest detection.

The detector is deliberately parser-specific. A pnpm lockfile is never routed
through a Python requirements parser, and malformed or unsupported input stays
fail-visible instead of being reported as a successful zero-dependency scan.
"""

from __future__ import annotations

import json
import re
from typing import Any

PNPM_LOCK_DETECTOR_ID = "evidence-lane-pnpm-lock-v1"
PNPM_LOCK_MAX_CHARS = 16 * 1024 * 1024
PNPM_LOCK_MAX_LINES = 500_000
PNPM_DEPENDENCY_GROUPS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
)

_YAML_KEY_RE = re.compile(
    r"^(?P<indent> *)(?P<key>'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"|[^:#][^:]*)\s*:\s*(?P<value>.*)$"
)


def _yaml_scalar(value: str) -> str:
    exact = value.strip()
    if len(exact) >= 2 and exact[0] == exact[-1] and exact[0] in {"'", '"'}:
        if exact[0] == "'":
            return exact[1:-1].replace("''", "'")
        try:
            decoded = json.loads(exact)
        except json.JSONDecodeError:
            return exact[1:-1]
        return decoded if isinstance(decoded, str) else exact[1:-1]
    return exact


def parse_pnpm_lock_dependencies(text: str) -> dict[str, Any]:
    """Parse importer declarations plus bounded resolution/override visibility."""

    if len(text) > PNPM_LOCK_MAX_CHARS:
        return {
            "status": "BLOCKED",
            "detector_id": PNPM_LOCK_DETECTOR_ID,
            "reason": "PNPM_LOCK_CHARACTER_LIMIT_EXCEEDED",
            "lockfile_version": None,
            "dependencies": [],
            "resolved_packages": [],
            "overrides": [],
        }
    lines = text.splitlines()
    if len(lines) > PNPM_LOCK_MAX_LINES:
        return {
            "status": "BLOCKED",
            "detector_id": PNPM_LOCK_DETECTOR_ID,
            "reason": "PNPM_LOCK_LINE_LIMIT_EXCEEDED",
            "lockfile_version": None,
            "dependencies": [],
            "resolved_packages": [],
            "overrides": [],
        }

    lockfile_version: str | None = None
    importers_indent: int | None = None
    importer: str | None = None
    importer_indent: int | None = None
    group: str | None = None
    group_indent: int | None = None
    dependency: dict[str, Any] | None = None
    dependencies: list[dict[str, str]] = []
    root_section: str | None = None
    resolved_packages: list[dict[str, str]] = []
    overrides: list[dict[str, str]] = []

    def flush_dependency() -> None:
        nonlocal dependency
        if dependency is None:
            return
        name = str(dependency.get("name") or "").strip()
        if name:
            specifier = str(dependency.get("specifier") or "").strip()
            resolved = str(dependency.get("resolved_version") or "").strip()
            dependencies.append(
                {
                    "ecosystem": "pnpm",
                    "name": name,
                    "constraint": specifier or resolved,
                    "resolved_version": resolved,
                    "group": str(dependency["group"]),
                    "importer": str(dependency["importer"]),
                    "detector_id": PNPM_LOCK_DETECTOR_ID,
                }
            )
        dependency = None

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = _YAML_KEY_RE.match(raw_line)
        if match is None:
            continue
        indent = len(match.group("indent"))
        key = _yaml_scalar(match.group("key").strip())
        value = _yaml_scalar(match.group("value"))

        if indent == 0 and key == "lockfileVersion":
            lockfile_version = value
            continue
        if indent == 0:
            root_section = key
            if key == "importers":
                importers_indent = indent
                importer = None
                importer_indent = None
                group = None
                group_indent = None
                flush_dependency()
            elif importers_indent is not None:
                flush_dependency()
                importers_indent = None
            continue
        if root_section == "overrides" and indent == 2:
            overrides.append(
                {
                    "selector": key,
                    "replacement": value,
                }
            )
            continue
        if root_section == "packages" and indent == 2:
            package_name, separator, package_version = key.rpartition("@")
            if separator and package_name and package_version:
                resolved_packages.append(
                    {
                        "name": package_name,
                        "resolved_version": package_version.split("(", maxsplit=1)[0],
                        "selector": key,
                    }
                )
            continue
        if importers_indent is None:
            continue

        if importer_indent is None or indent <= importer_indent:
            flush_dependency()
            importer = key
            importer_indent = indent
            group = None
            group_indent = None
            continue
        if importer is None:
            continue
        if indent == importer_indent + 2 and key in PNPM_DEPENDENCY_GROUPS:
            flush_dependency()
            group = key
            group_indent = indent
            continue
        if group is None or group_indent is None:
            continue
        if indent == group_indent + 2:
            flush_dependency()
            dependency = {
                "name": key,
                "group": group,
                "importer": importer,
            }
            if value and not value.startswith("{"):
                dependency["specifier"] = value
            continue
        if dependency is not None and indent == group_indent + 4:
            if key == "specifier":
                dependency["specifier"] = value
            elif key == "version":
                dependency["resolved_version"] = value

    flush_dependency()
    if lockfile_version is None:
        return {
            "status": "FAIL",
            "detector_id": PNPM_LOCK_DETECTOR_ID,
            "reason": "PNPM_LOCK_VERSION_MISSING",
            "lockfile_version": None,
            "dependencies": [],
            "resolved_packages": [],
            "overrides": [],
        }
    if importers_indent is None and not any(
        line.strip() == "importers:" for line in lines
    ):
        return {
            "status": "FAIL",
            "detector_id": PNPM_LOCK_DETECTOR_ID,
            "reason": "PNPM_IMPORTERS_SECTION_MISSING",
            "lockfile_version": lockfile_version,
            "dependencies": [],
            "resolved_packages": sorted(
                resolved_packages,
                key=lambda row: (row["name"], row["resolved_version"]),
            ),
            "overrides": sorted(overrides, key=lambda row: row["selector"]),
        }

    ordered = sorted(
        dependencies,
        key=lambda row: (row["importer"], row["group"], row["name"]),
    )
    return {
        "status": "PASS",
        "detector_id": PNPM_LOCK_DETECTOR_ID,
        "reason": "PNPM_IMPORTER_DEPENDENCIES_PARSED",
        "lockfile_version": lockfile_version,
        "dependencies": ordered,
        "resolved_packages": sorted(
            resolved_packages,
            key=lambda row: (row["name"], row["resolved_version"]),
        ),
        "overrides": sorted(overrides, key=lambda row: row["selector"]),
    }
