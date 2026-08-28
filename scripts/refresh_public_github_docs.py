#!/usr/bin/env python3
"""Refresh every public GitHub document from current plugin backend contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from validate_public_github_docs import ALLOWED_DOCS, PUBLIC_ROOT_DOCS, ROOT, validate

PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
START = "<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->"
END = "<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->"
PUBLIC_DOC_REFRESH_MARKER = (
    "<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v3 -->"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected object: {path}")
    return value


def _replace_block(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1:
            raise RuntimeError(f"Malformed backend block: {path}")
        before, remainder = text.split(START, 1)
        _, after = remainder.split(END, 1)
        updated = before.rstrip() + "\n\n" + block + after
    else:
        lines = text.splitlines()
        heading = next(
            (index for index, line in enumerate(lines) if line.startswith("# ")),
            None,
        )
        if heading is None:
            raise RuntimeError(f"Public document has no H1: {path}")
        insertion = heading + 1
        updated = "\n".join([*lines[:insertion], "", block, "", *lines[insertion:]])
    updated = updated.rstrip()
    if not updated.startswith("<!-- evidence-lane-public-docs-full-refresh:"):
        updated = PUBLIC_DOC_REFRESH_MARKER + "\n\n" + updated
    path.write_text(updated + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    validate()
    plugin_manifest_path = PLUGIN / ".codex-plugin" / "plugin.json"
    public_schema_path = PLUGIN / "schemas" / "public-action-schemas.v001.json"
    skill_registry_path = PLUGIN / "skills" / "skill-surface-registry.v1.json"
    hook_path = PLUGIN / "hooks" / "hooks.json"
    sdk_path = PLUGIN / "sdk" / "sdk-manifest.v1.json"
    mcp_path = PLUGIN / "mcp" / "mcp-manifest.v1.json"
    env_path = PLUGIN / "env" / "authority-manifest.v1.json"
    uop_path = PLUGIN / "uop" / "authority-manifest.v1.json"
    tool_matrix_path = PLUGIN / "toolchains" / "TOOLCHAIN_EXECUTION_MATRIX.md"
    plugin_manifest = _json(plugin_manifest_path)
    public = _json(public_schema_path)
    skills = _json(skill_registry_path)
    hooks = _json(hook_path)
    sdk = _json(sdk_path)
    env = _json(env_path)
    uop = _json(uop_path)
    event_count = len(dict(hooks.get("hooks") or {}))
    handler_count = int(
        dict(public.get("hook_control") or {}).get("current_handler_action_count") or 0
    )
    common_sources = [
        plugin_manifest_path,
        public_schema_path,
        skill_registry_path,
        hook_path,
        sdk_path,
        mcp_path,
        env_path,
        uop_path,
        tool_matrix_path,
    ]
    source_lines = [
        f"  - `{path.relative_to(ROOT).as_posix()}` — `{_sha256(path)}`"
        for path in common_sources
    ]
    block = "\n".join(
        [
            START,
            "## Current backend contract",
            "",
            "This public document is refreshed from the same source graph used by the installable plugin package.",
            "",
            f"- Plugin package: `{plugin_manifest['version']}`.",
            f"- Native MCP: **{public['tool_count']} actions** (**{public['read_tool_count']} read / {public['write_tool_count']} write**).",
            f"- Native skills: **{skills['skill_count']} governed skills**; the separate command layer is absent.",
            f"- Hooks: **{event_count} events / {handler_count} ordered handler actions**.",
            f"- SDK: internal action SDK and outer routing SDK remain distinct; public action count **{sdk['public_action_count']}**.",
            f"- ENV/UOP: separate executable authorities with **{env['member_count']} ENV members / {uop['member_count']} UOP members**.",
            "- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.",
            "- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.",
            "",
            "Exact backend bindings:",
            *source_lines,
            END,
        ]
    )
    docs = [ROOT / name for name in sorted(PUBLIC_ROOT_DOCS)] + [
        ROOT / "docs" / name for name in sorted(ALLOWED_DOCS)
    ]
    for path in docs:
        _replace_block(path, block)

    tool_matrix = tool_matrix_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    tool_matrix = tool_matrix.replace(
        "# Evidence Lane Codex Toolchain Execution Matrix",
        "## Complete source-derived toolchain execution matrix",
        1,
    )
    tools_path = ROOT / "docs" / "TOOLS.md"
    tools_marker = tools_path.read_text(encoding="utf-8").splitlines()[0]
    tools_path.write_text(
        "\n".join(
            [
                tools_marker,
                "",
                "# Evidence Lane 3.0.0 tools and execution routing",
                "",
                block,
                "",
                "This page is regenerated from the executable toolchain matrix; it does not preserve a separate hand-maintained inventory.",
                "",
                tool_matrix.rstrip(),
                "",
                "## License and installation boundary",
                "",
                "Every retained dependency has a pinned package or host-runtime identity, a license/provenance entry, an owning lane or runtime surface, and a fail-visible availability contract. Presence never means unconditional execution, and no dependency is installed into a user's project workspace.",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    rows = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in docs
    ]
    body = {
        "schema": "evidence-lane.public-docs-backend-binding.v1",
        "status": "PASS",
        "plugin_version": plugin_manifest["version"],
        "document_count": len(rows),
        "documents": rows,
        "backend_sources": {
            path.relative_to(ROOT).as_posix(): _sha256(path) for path in common_sources
        },
        "historical_internal_source_count": 0,
        "main_merge_authorized": False,
    }
    output = (
        ROOT
        / "apps"
        / "evidence-lane-remote-adapter"
        / "app"
        / "_data"
        / "public-docs-backend-binding.json"
    )
    output.write_text(
        json.dumps(body, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "plugin_version": plugin_manifest["version"],
                "documents": len(rows),
                "binding": output.relative_to(ROOT).as_posix(),
                "binding_sha256": _sha256(output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
