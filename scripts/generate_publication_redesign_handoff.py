#!/usr/bin/env python3
"""Generate the deferred page-by-page public redesign handoff contract."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "apps" / "evidence-lane-app" / "app"
OUTPUT = APP_ROOT / "_data" / "publication-redesign-handoff.v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _route(path: Path, filename: str) -> str:
    relative = path.relative_to(APP_ROOT)
    parts = relative.parts[:-1]
    if filename == "page.tsx":
        return "/" + "/".join(parts) if parts else "/"
    return "/" + "/".join(parts)


def build_contract() -> dict[str, Any]:
    page_files = sorted(APP_ROOT.rglob("page.tsx"))
    api_files = sorted(APP_ROOT.rglob("route.ts"))
    source_paths = (
        "README.md",
        "ARCHITECTURE.md",
        "docs/AI_LEARNING.md",
        "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md",
        "docs/CODEX_V300_LOCAL_INSTALL_AND_RELOAD.md",
        "docs/GIT_AND_CI_CD.md",
        "docs/HOOKS.md",
        "docs/HOST_AND_STORAGE_MATRIX.md",
        "docs/LIFECYCLE_AND_HIL.md",
        "docs/MCP.md",
        "docs/MEMORY.md",
        "docs/PLAN_AND_CHANGE_DISPLAY.md",
        "docs/PROJECT_PV_CONTENT_ADDRESSED_STORAGE.md",
        "docs/PROJECT_UNIVERSE.md",
        "docs/RELEASE_AND_COMPATIBILITY.md",
        "docs/REPOSITORY_MAP.md",
        "docs/SKILLS.md",
        "docs/SOURCE_INTAKE_AND_LANES.md",
        "docs/TOOLS.md",
        "docs/USER_TUNNEL_GUIDE.md",
        "plugins/evidence-lane-plugin/toolchains/hardware-accelerator-routing.v1.json",
        "plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json",
    )
    source_bindings = {
        path: _sha256(ROOT / path) for path in source_paths if (ROOT / path).is_file()
    }
    pages = [
        {
            "route": _route(path, "page.tsx"),
            "source": path.relative_to(ROOT).as_posix(),
            "status": "AWAITING_VISUAL_CONCEPT_AND_USER_CORRECTION",
            "existing_ui_claim_authority": False,
            "existing_navigation_skeleton_may_be_inspected": True,
            "page_specific_design_required": True,
            "shared_fixed_page_theme_required": False,
            "backend_truth_required_before_design": True,
            "implementation_authorized": False,
            "publication_authorized": False,
        }
        for path in page_files
    ]
    api_routes = [
        {
            "route": _route(path, "route.ts"),
            "source": path.relative_to(ROOT).as_posix(),
            "status": "BACKEND_PARITY_ONLY_NO_PUBLIC_REDESIGN",
            "behavior_change_authorized": False,
            "publication_authorized": False,
        }
        for path in api_files
    ]
    core = {
        "schema": "evidence-lane.publication-redesign-handoff.v1",
        "status": "SEALED_DEFERRED_HANDOFF",
        "page_count": len(pages),
        "pages": pages,
        "api_route_count": len(api_routes),
        "api_routes": api_routes,
        "workflow": [
            "COMMIT_AND_REVIEW_SOURCE_DERIVED_GITHUB_MARKDOWN",
            "GENERATE_ONE_PAGE_SPECIFIC_VISUAL_CONCEPT_FROM_BACKEND_TRUTH",
            "RECEIVE_USER_CORRECTION_AND_DESIGN_DIRECTION",
            "INTAKE_SELECTED_ENVATO_ADOBE_OR_OTHER_ASSETS_WITH_LICENSE_LEDGER",
            "IMPLEMENT_ONLY_THE_APPROVED_PAGE_ROUTE",
            "VERIFY_MOTION_ACCESSIBILITY_RESPONSIVENESS_AND_BACKEND_PARITY",
            "SYNC_REVIEWED_GITHUB_DOCS_TO_GITHUB_PAGES_AND_VERCEL_STORY",
            "PUBLISH_ONLY_AT_SEPARATE_EXPLICIT_RELEASE_GATE",
        ],
        "asset_license_ledger_required_fields": [
            "asset_id",
            "vendor",
            "source_url_or_order_reference",
            "license_name",
            "license_text_or_receipt_sha256",
            "purchaser_or_account_owner",
            "permitted_project",
            "permitted_derivatives",
            "attribution_requirement",
            "redistribution_restriction",
            "original_asset_sha256",
            "derived_asset_sha256",
            "repository_destination",
        ],
        "external_concept_image_directory": (
            "C:/Users/rathe/.codex/generated_images/"
            "01a04074-da0c-7f32-8d38-fe48f7db5df6"
        ),
        "external_concept_images_are_repository_assets": False,
        "envato_or_adobe_asset_selected": False,
        "existing_ui_or_animation_is_design_authority": False,
        "github_markdown_is_current_story_source": True,
        "github_pages_and_vercel_publication_authorized": False,
        "source_bindings": source_bindings,
    }
    return {**core, "receipt_sha256": hashlib.sha256(_json_bytes(core)).hexdigest().upper()}


def main() -> int:
    payload = build_contract()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{OUTPUT.name}.",
            suffix=".tmp",
            dir=OUTPUT.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_json_bytes(payload))
        temporary.replace(OUTPUT)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"status": payload["status"], "page_count": payload["page_count"], "api_route_count": payload["api_route_count"], "output": OUTPUT.as_posix(), "receipt_sha256": payload["receipt_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
