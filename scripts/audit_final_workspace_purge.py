"""Audit the final v4 source tree for stale executable owners and projections."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"
RETAINED_AUTHORITIES = {
    "agent_learning",
    "canon_input",
    "chat_lineage",
    "instructions",
    "plan",
    "project_authority",
    "project_memory",
    "project_sectors",
    "project_universe",
    "receipt_ledger",
    "session_authority",
    "source_authority",
}
RETAINED_SECTORS = {
    "artifacts",
    "custom",
    "data",
    "data_excel",
    "docs",
    "github_code",
    "images_ocr",
    "local_code",
    "pdf_ocr",
    "power_bi",
    "ppt",
    "research",
    "tableau",
}
REMOVED_TOOL_IDS = {
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
}
REMOVED_SOURCE_MODULES = {
    "access_workers.py",
    "onenote_contracts.py",
    "onenote_native.py",
    "onenote_parsers.py",
    "onenote_profile.py",
    "onenote_schema.py",
    "onenote_views.py",
    "onenote_workers.py",
    "visio_authoring.py",
    "visio_contracts.py",
    "visio_conversion.py",
    "visio_drawing.py",
    "visio_parsers.py",
    "visio_profile.py",
    "visio_rendering.py",
    "visio_schema.py",
    "visio_views.py",
    "visio_workers.py",
    "project_overlay.py",
    "project_pv_storage.py",
    "pv_package.py",
    "lane_engine.py",
    "runtime_toolchain.py",
    "deployment_toolchain.py",
    "mcp_adapter_routing.py",
    "entity_reconciliation.py",
}
REMOVED_EXECUTABLE_PATHS = {
    "scripts/codex_release/install_native_toolchain.py",
}
REMOVED_STUDIO_ADMIN_MARKERS = {
    "class RegisterProject(",
    "class AcceleratorCommand(",
    "class PluginCommand(",
    "class RevokePlugin(",
    "class CancelJob(",
    "class RestoreGitSource(",
    "class BackupProject(",
    "class RevokeLearning(",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected an object: {path}")
    return value


def relative_files(path: Path) -> set[str]:
    return {
        item.relative_to(PLUGIN).as_posix()
        for item in path.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts
    }


def manifest_members(path: Path) -> set[str]:
    return {row["path"] for row in load(path)["members"]}


def audit(root: Path = ROOT) -> dict[str, Any]:
    if root.resolve() != ROOT.resolve():
        raise ValueError("The final purge audit is repository-bound")
    violations: list[dict[str, Any]] = []

    def reject(condition: bool, code: str, **details: Any) -> None:
        if condition:
            violations.append({"code": code, **details})

    if (ROOT / '.git').exists():
        completed = subprocess.run(
            ['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True,
            encoding='utf-8', errors='strict', check=True)
        public_paths = {line.replace('\\', '/') for line in completed.stdout.splitlines() if line}
    else:
        public_paths = {
            path.relative_to(ROOT).as_posix() for path in ROOT.rglob('*') if path.is_file()
        }

    reject((PLUGIN / "contracts").exists(), "PARALLEL_CONTRACT_ROOT_PRESENT")
    reject((PLUGIN / "root_pv").exists(), "PARALLEL_ROOT_PV_PRESENT")
    reject((PLUGIN / "sectors").exists(), "PARALLEL_SECTOR_ROOT_PRESENT")
    reject((PLUGIN / "tunnel").exists(), "LEGACY_TUNNEL_ROOT_PRESENT")
    reject((ROOT / ".agents/skills").exists(), "ROOT_SKILL_COPY_PRESENT")
    reject("AGENTS.md" in public_paths, "LOCAL_AGENTS_FILE_PUBLIC")
    reject("MEMORY.md" in public_paths, "LOCAL_MEMORY_FILE_PUBLIC")
    reject("step-task-list.json" in public_paths, "LOCAL_STEP_LIST_PUBLIC")
    reject(any(path.startswith('docs/internal/') for path in public_paths), "LOCAL_INTERNAL_DOC_PUBLIC")
    reject(any(path.startswith('.work/preservation/') for path in public_paths), "PRESERVATION_COPY_PRESENT")
    reject("Dockerfile" in public_paths, "SUPERSEDED_DOCKERFILE_PRESENT")
    reject(".dockerignore" in public_paths, "SUPERSEDED_DOCKERIGNORE_PRESENT")
    reject(
        (ROOT / ".github/evidence-lane-repository-fingerprints.v1.json").exists(),
        "STALE_REPOSITORY_FINGERPRINT_PRESENT",
    )

    authorities = {
        path.name for path in (PLUGIN / "authorities").iterdir() if path.is_dir()
    }
    sectors = {
        path.name
        for path in (PLUGIN / "authorities/project_sectors").iterdir()
        if path.is_dir()
    }
    reject(
        authorities != RETAINED_AUTHORITIES,
        "AUTHORITY_OWNER_SET_MISMATCH",
        observed=sorted(authorities),
    )
    reject(
        sectors != RETAINED_SECTORS,
        "SECTOR_OWNER_SET_MISMATCH",
        observed=sorted(sectors),
    )

    sdk_manifest = load(PLUGIN / "sdk/sdk-manifest.v4.json")
    sdk_action_manifest = load(PLUGIN / "sdk/actions/action-manifest.v4.json")
    mcp_manifest = load(PLUGIN / "mcp/mcp-manifest.v4.json")
    sdk_files = {path for path in relative_files(PLUGIN / "sdk/actions") if path.endswith('.action.v4.json')}
    mcp_files = {path for path in relative_files(PLUGIN / "mcp/actions") if path.endswith('.binding.v4.json')}
    sdk_members = {row["path"] for row in sdk_manifest["members"]}
    sdk_action_members = {row["path"] for row in sdk_action_manifest["members"]}
    mcp_members = {row["path"] for row in mcp_manifest["members"]}
    reject(sdk_files != sdk_action_members, "SDK_ACTION_MEMBER_MISMATCH")
    reject(mcp_files != mcp_members, "MCP_ACTION_MEMBER_MISMATCH")
    action_count = len(load(PLUGIN / 'schemas/public-action-schemas.v4.json')['actions'])
    reject(
        sdk_manifest.get("action_count") != action_count
        or sdk_action_manifest.get("action_count") != action_count
        or mcp_manifest.get("action_count") != action_count,
        "ACTION_COUNT_MISMATCH",
    )
    contracts = {
        load(PLUGIN / row["path"])["contract"] for row in sdk_action_manifest["members"]
    }
    schema_files = {path for path in relative_files(PLUGIN / "schemas/actions") if path.endswith('.v4.schema.json')}
    reject(schema_files != contracts, "ACTION_SCHEMA_MEMBER_MISMATCH")
    for manifest, members in ((sdk_manifest, sdk_members), (sdk_action_manifest, sdk_action_members),
                              (mcp_manifest, mcp_members)):
        for row in manifest["members"]:
            path = PLUGIN / row["path"]
            reject(
                not path.is_file() or sha256(path) != row["sha256"],
                "GENERATED_MEMBER_HASH_MISMATCH",
                path=row["path"],
            )

    owner_registry = load(PLUGIN / "authorities/authority-surface-registry.v4.json")
    owner_manifests = {
        row["manifest"]["path"]
        for row in [
            *owner_registry["authorities"],
            *owner_registry["workflow_owners"],
        ]
    }
    owner_manifests.add(owner_registry["root_pv"])
    for relative in owner_manifests:
        manifest = load(PLUGIN / relative)
        for row in manifest.get("members", []):
            path = PLUGIN / row["path"]
            reject(
                not path.is_file() or sha256(path) != row["sha256"],
                "AUTHORITY_MEMBER_HASH_MISMATCH",
                path=row["path"],
            )
    for lane_id in RETAINED_SECTORS:
        manifest_path = (
            PLUGIN / f"authorities/project_sectors/{lane_id}/manifest.v4.json"
        )
        manifest = load(manifest_path)
        for row in manifest["members"]:
            path = PLUGIN / row["path"]
            reject(
                not path.is_file() or sha256(path) != row["sha256"],
                "SECTOR_MEMBER_HASH_MISMATCH",
                path=row["path"],
            )

    definitions_path = PLUGIN / "toolchains/tool-definitions.v4.json"
    definitions = load(definitions_path)
    catalog = load(PLUGIN / "toolchains/tool-catalog.v4.json")
    tools = {row["tool_id"] for row in definitions["entries"]}
    reject(
        definitions.get("entry_count") != 103
        or len(definitions["entries"]) != 103
        or len(tools) != 103,
        "TOOL_DEFINITION_COUNT_MISMATCH",
    )
    reject(bool(tools & REMOVED_TOOL_IDS), "REMOVED_TOOL_DECLARATION_PRESENT")
    reject(
        any(row.get("lifecycle") != "retained" for row in definitions["entries"]),
        "NONRETAINED_TOOL_DECLARATION_PRESENT",
    )
    reject(
        catalog.get("schema_version") != 4
        or catalog.get("counts") != {"retained": 103}
        or catalog.get("source_sha256") != sha256(definitions_path),
        "TOOL_CATALOG_SOURCE_MISMATCH",
    )
    license_index = load(
        PLUGIN / "toolchains/licenses/retained-install-license-index.v4.json"
    )
    reject(
        license_index.get("retained_tool_count") != 103
        or license_index.get("record_count", 103) != 103,
        "LICENSE_INDEX_COUNT_MISMATCH",
    )
    reject(
        {row["tool_id"] for row in license_index["records"]} != tools,
        "LICENSE_INDEX_TOOL_SET_MISMATCH",
    )
    for path in (PLUGIN / "toolchains/licenses/requirements").glob(
        "*/LICENSE-RECORD.json"
    ):
        value = load(path)
        receipt = value.pop("receipt_sha256", None)
        expected = hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        reject(
            value.get("schema") != "evidence-lane.tool-requirement-license-record.v4"
            or receipt != expected,
            "LICENSE_RECORD_INVALID",
            path=path.relative_to(ROOT).as_posix(),
        )

    source = PLUGIN / "src/evidence_lane_plugin"
    reject(
        bool(REMOVED_SOURCE_MODULES & {path.name for path in source.glob("*.py")}),
        "REMOVED_SOURCE_MODULE_PRESENT",
    )
    reject(
        any((PLUGIN / relative).exists() for relative in REMOVED_EXECUTABLE_PATHS),
        "SUPERSEDED_EXECUTABLE_PATH_PRESENT",
    )
    studio_gateway = (source / "studio_gateway.py").read_text(encoding="utf-8")
    reject(
        any(marker in studio_gateway for marker in REMOVED_STUDIO_ADMIN_MARKERS),
        "STUDIO_ADMIN_IMPLEMENTATION_PRESENT",
    )
    local_transport = (source / "local_transport.py").read_text(encoding="utf-8")
    reject(
        any(
            f'"{name}"' in local_transport
            for name in (
                "project",
                "accelerator",
                "plugin",
                "revoke-plugin",
                "cancel-job",
                "revoke-learning",
                "view-export",
                "backup",
                "git-restore",
            )
        ),
        "STUDIO_ADMIN_ROUTE_PRESENT",
    )
    reject(
        "def remote_push(" in (source / "git_adapter.py").read_text(encoding="utf-8"),
        "SUPERSEDED_REMOTE_PUSH_HELPER_PRESENT",
    )
    reject(
        any((ROOT / "tests/fixtures" / name).exists() for name in ("onenote", "visio"))
        or (ROOT / "tests/fixtures/AccessRelationshipFixture.java").exists(),
        "REMOVED_OFFICE_FIXTURE_PRESENT",
    )
    maintainer_inventory = load(ROOT / ".github/maintainer-ci.v4.json")
    missing_tests = sorted({
        path for profile in maintainer_inventory.get('profiles', {}).values()
        for path in profile.get('tests', []) if not (ROOT / path).is_file()
    })
    reject(
        bool(missing_tests),
        "MAINTAINER_TEST_MISSING",
        paths=missing_tests,
    )
    package_test_policy = load(PLUGIN / 'tests/test-surface-policy.v4.json')
    missing_package_tests = sorted(
        name for name in package_test_policy.get('tests', [])
        if not (PLUGIN / 'tests' / name).is_file())
    reject(bool(missing_package_tests), 'PACKAGE_SELF_TEST_MISSING', paths=missing_package_tests)
    reject(
        any(path.startswith('apps/evidence-lane-app/') for path in public_paths),
        "PREMATURE_WEBSITE_SOURCE_PRESENT",
    )
    reject(
        (ROOT / "apps/evidence-lane-studio/src/hil").exists(),
        "LEGACY_STUDIO_HIL_SOURCE_ROOT_PRESENT",
    )
    reject(
        not (ROOT / "apps/evidence-lane-studio/src/design-system").is_dir(),
        "STUDIO_DESIGN_SYSTEM_ROOT_MISSING",
    )
    public_docs = sorted(
        path.name for path in (ROOT / "docs").glob("*.md")
    )
    allowed_public_docs = ["TOOLCHAIN_EXECUTION_MATRIX.md"]
    reject(
        public_docs != allowed_public_docs,
        "STALE_PUBLIC_DOC_PRESENT",
        paths=public_docs,
    )
    reject(
        sha256(ROOT / "docs/TOOLCHAIN_EXECUTION_MATRIX.md")
        != sha256(PLUGIN / "toolchains/TOOLCHAIN_EXECUTION_MATRIX.md"),
        "TOOLCHAIN_EXECUTION_MATRIX_PROJECTION_MISMATCH",
    )
    reject((ROOT / "github-pages").exists(), "GITHUB_PAGES_ROOT_PRESENT")
    reject(
        (ROOT / ".github/workflows/evidence-lane-github-pages.yml").exists(),
        "GITHUB_PAGES_WORKFLOW_PRESENT",
    )
    preview = (ROOT / ".github/workflows/evidence-lane-preview-build.yml").read_text(
        encoding="utf-8"
    )
    reject("docker build" in preview.casefold(), "DOCKER_PREVIEW_ROUTE_PRESENT")

    return {
        "schema": "evidence-lane.final-workspace-purge-audit.v4",
        "status": "PASS" if not violations else "FAIL",
        "violations": violations,
        "counts": {
            "actions": sdk_manifest["action_count"],
            "skills": load(PLUGIN / "skills/skill-surface-registry.v4.json")[
                "skill_count"
            ],
            "authorities": owner_registry["authority_count"],
            "sectors": len(RETAINED_SECTORS),
            "tools": len(tools),
            "license_records": len(license_index["records"]),
        },
        "project_data_changed": False,
        "installed_execution_claimed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    result = audit()
    if options.output is not None:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
