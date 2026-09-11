#!/usr/bin/env python3
"""Generate the explicit plugin-to-Studio linkage package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def ref(path: str) -> dict[str, object]:
    target = PLUGIN / path
    if not target.is_file():
        raise RuntimeError("Studio linkage input is missing: " + path)
    return {"path": path, "bytes": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def encoded(value) -> bytes:
    if isinstance(value, str):
        return value.encode()
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def install_source() -> str:
    return '''"""Discoverable Studio installer alias; all work remains in scripts/bootstrap.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def main(argv=None) -> int:
    source = Path(__file__).resolve().parents[1] / "scripts/bootstrap.py"
    spec = importlib.util.spec_from_file_location("evidence_lane_studio_bootstrap", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("STUDIO_BOOTSTRAP_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def outputs() -> dict[str, object]:
    installation = {
        "schema": "evidence-lane.studio-installation-link.v4",
        "managed_plugin_owner": "codex_plugin_manager",
        "managed_plugin_source": "plugins/evidence-lane-plugin",
        "studio_source": "apps/evidence-lane-studio",
        "windows_installation_root": "C:/Apps/EvidenceLaneStudio",
        "stable_direct_folders": ["app", "plugin", "engine", "toolchains"],
        "canonical_installer": ref("scripts/first_detection.py"),
        "explicit_bootstrap": ref("scripts/bootstrap.py"),
        "discoverable_alias": "studio/install.py",
        "bundle_plan": ref("provisioning/full-bundle-plan.v4.json"),
        "release_binding": {
            "path": "provisioning/release-binding.v4.json",
            "verified_at_runtime_by": "scripts/first_detection.py:validate_binding",
            "embedded_digest": False,
        },
        "mcp_first_detection": ref("scripts/run_mcp.py"),
        "first_detection_outside_initialize": True,
        "project_state_changed": False,
        "native_installation_verified": False,
    }
    engine = {
        "schema": "evidence-lane.studio-engine-api-link.v4",
        "engine_launcher": ref("scripts/run_engine.py"),
        "engine_service": ref("src/evidence_lane_plugin/service.py"),
        "studio_gateway": ref("src/evidence_lane_plugin/studio_gateway.py"),
        "local_transport": ref("src/evidence_lane_plugin/local_transport.py"),
        "mcp_bridge": ref("mcp/server.mjs"),
        "mcp_manifest": ref(".mcp.json"),
        "connection_manifest": ref("manifests/engine-connection.v4.json"),
        "studio_boundary": "visible_read_only_observer",
        "human_mutation_routes": [],
        "engine_owns_operations": True,
        "installed_execution_claimed": False,
    }
    toolchain = {
        "schema": "evidence-lane.studio-toolchain-license-link.v4",
        "shared_toolchain": ref("toolchains/shared-toolchain.v4.json"),
        "license_index": ref("toolchains/licenses/retained-install-license-index.v4.json"),
        "license_policy": ref("toolchains/license-policy.v4.json"),
        "third_party_notices": ref("THIRD_PARTY_NOTICES.md"),
        "retained_tool_count": 103,
        "studio_bundle_owns_retained_local_toolchain": True,
        "external_services_require_explicit_configuration": True,
        "tunnel_dependency": False,
        "installed_execution_claimed": False,
    }
    windows = {
        "schema": "evidence-lane.studio-windows-integration-link.v4",
        "registration": ref("scripts/register_installed_runtime.py"),
        "shortcut_owner": ref("src/evidence_lane_plugin/shortcuts.py"),
        "startup_owner": ref("src/evidence_lane_plugin/startup.py"),
        "launcher_source": ref("scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.cs"),
        "launcher_binary": ref("scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.exe"),
        "launcher_build": ref("scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.build.json"),
        "desktop_shortcut_count": 1,
        "start_menu_shortcut_count": 1,
        "studio_visible": True,
        "engine_console_hidden": True,
        "installed_verification": ref("scripts/verify_installed_runtime.py"),
        "native_installation_verified": False,
    }
    result: dict[str, object] = {
        "studio/install.py": install_source(),
        "studio/installation.contract.v4.json": installation,
        "studio/engine-api.contract.v4.json": engine,
        "studio/toolchain-license.contract.v4.json": toolchain,
        "studio/windows-integration.contract.v4.json": windows,
    }
    readme = """# Evidence Lane Studio linkage

This package binds the Codex-managed Evidence Lane plugin to the separately
installed Windows Studio, persistent engine and complete shared local toolchain.
`install.py` is a discoverable thin alias for `scripts/bootstrap.py`; all
provisioning, release verification and stable-root publication remain in the
canonical first-detection installer. MCP startup may start that installer in a
detached process, then returns without blocking an unrelated Codex task.

Studio is a visible read-only observer. Codex calls the engine through the
plugin MCP/SDK routes. The Studio bundle installs local dependencies once under
`C:/Apps/EvidenceLaneStudio`; external services still require explicit
configuration. The retired tunnel and Codex runtime/dedup tree have no role.

The contracts here are source bindings. Their installed flags stay false until
the exact packaged candidate is installed and read back.
"""
    result["studio/README.md"] = readme
    members = [{"path": path, "sha256": digest(value), "bytes": len(encoded(value))} for path, value in sorted(result.items())]
    result["studio/studio-linkage.v4.json"] = {
        "schema": "evidence-lane.studio-linkage.v4",
        "status": "COMPLETE_PREINSTALL_SOURCE_LINKAGE",
        "member_count": len(members),
        "members": members,
        "canonical_installer": "scripts/first_detection.py",
        "canonical_bootstrap": "scripts/bootstrap.py",
        "stable_installation_root": "C:/Apps/EvidenceLaneStudio",
        "parallel_installer": False,
        "human_mutation_routes": [],
        "tunnel_dependency": False,
        "native_installation_verified": False,
        "installed_execution_claimed": False,
    }
    return result


def generate(*, check: bool = False) -> dict:
    changed = []
    values = outputs()
    for relative, value in values.items():
        target = PLUGIN / relative
        content = encoded(value)
        if target.is_file() and target.read_bytes() == content:
            continue
        changed.append(relative)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    if check and changed:
        raise RuntimeError("Studio linkage requires regeneration: " + ", ".join(changed))
    return {"status": "PASS", "member_count": len(values), "changed": changed, "check": check}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(generate(check=args.check)))


if __name__ == "__main__":
    main()
