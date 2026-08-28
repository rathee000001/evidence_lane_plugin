#!/usr/bin/env python3
"""Fail when internal or historical files enter the public GitHub docs surface."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ALLOWED_DOCS = frozenset(
    {
        "AI_LEARNING.md",
        "CANON_TASK_GRAPH_AND_INPUT_HIL.md",
        "CODEX_V300_LOCAL_INSTALL_AND_RELOAD.md",
        "COPYRIGHT.md",
        "CREDITS_AND_CONTRIBUTIONS.md",
        "GIT_AND_CI_CD.md",
        "HOOKS.md",
        "HOST_AND_STORAGE_MATRIX.md",
        "LIFECYCLE_AND_HIL.md",
        "MCP.md",
        "MEMORY.md",
        "PLAN_AND_CHANGE_DISPLAY.md",
        "PROJECT_PV_CONTENT_ADDRESSED_STORAGE.md",
        "PROJECT_UNIVERSE.md",
        "RELEASE_AND_COMPATIBILITY.md",
        "REPOSITORY_MAP.md",
        "SKILLS.md",
        "SOURCE_INTAKE_AND_LANES.md",
        "TERMS_AND_CONDITIONS.md",
        "THIRD_PARTY_LICENSES.md",
        "TOOLS.md",
        "UPSTREAM_REFERENCE_PROVENANCE.md",
        "USER_TUNNEL_GUIDE.md",
    }
)
PUBLIC_ROOT_DOCS = frozenset(
    {"README.md", "ARCHITECTURE.md", "LICENSE.md", "SECURITY.md"}
)
FORBIDDEN_REFERENCE_NAMES = frozenset(
    {
        "CODEX_V200_PV11_STABLE_REGRESSION_FORENSIC.md",
        "CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260824.json",
        "DEPENDENCY_LICENSE_AUDIT.md",
        "FIRST_HIL_RUNBOOK.md",
        "GITHUB_APP_PRE_HIL_CONTRACT.md",
        "HOST_CAPABILITY_MATRIX.md",
        "HOST_STORAGE_ENV_MODE_CONTINUITY.md",
        "IMPLEMENTATION_TRACEABILITY.md",
        "INTERNAL_CODEX_SDK.md",
        "PUBLIC_SITE_SOURCE_MAP.md",
        "WINDOWS_TUNNEL_PERSISTENCE.md",
    }
)


def validate() -> dict[str, object]:
    observed = {path.name for path in DOCS.iterdir() if path.is_file()}
    extra = sorted(observed - ALLOWED_DOCS)
    missing = sorted(ALLOWED_DOCS - observed)
    if extra or missing:
        raise RuntimeError(
            f"PUBLIC_GITHUB_DOCS_ALLOWLIST_MISMATCH extra={extra} missing={missing}"
        )
    public_paths = [ROOT / name for name in PUBLIC_ROOT_DOCS] + [
        DOCS / name for name in sorted(ALLOWED_DOCS)
    ]
    broken_internal_references: list[dict[str, object]] = []
    for path in public_paths:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_REFERENCE_NAMES:
            if name in text:
                broken_internal_references.append(
                    {"path": path.relative_to(ROOT).as_posix(), "reference": name}
                )
    if broken_internal_references:
        raise RuntimeError(
            "PUBLIC_GITHUB_DOCS_INTERNAL_REFERENCE="
            + json.dumps(broken_internal_references, sort_keys=True)
        )
    route_source = (
        ROOT
        / "apps"
        / "evidence-lane-remote-adapter"
        / "app"
        / "_data"
        / "repository-documents.ts"
    ).read_text(encoding="utf-8")
    route_paths = set(re.findall(r'path: "([^"]+)"', route_source))
    allowed_route_paths = {
        *PUBLIC_ROOT_DOCS,
        *(f"docs/{name}" for name in ALLOWED_DOCS),
    }
    invalid_routes = sorted(route_paths - allowed_route_paths)
    if invalid_routes:
        raise RuntimeError(f"PUBLIC_GITHUB_DOCS_ROUTE_INVALID={invalid_routes}")
    return {
        "schema": "evidence-lane.public-github-docs-allowlist.v1",
        "status": "PASS",
        "public_docs_count": len(ALLOWED_DOCS),
        "public_root_docs": sorted(PUBLIC_ROOT_DOCS),
        "route_source_count": len(route_paths),
        "historical_or_internal_docs_in_public_root": 0,
        "broken_internal_references": 0,
    }


def main() -> int:
    print(json.dumps(validate(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
