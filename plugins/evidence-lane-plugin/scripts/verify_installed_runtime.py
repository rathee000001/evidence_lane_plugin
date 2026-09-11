"""Read-only self-test for one materialized release before it becomes active."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))


def _first_detection():
    path = PLUGIN / "scripts/first_detection.py"
    spec = importlib.util.spec_from_file_location("evidence_lane_first_detection", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("FIRST_DETECTION_LAUNCHER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(release_root: Path, *, installation_root: Path | None = None) -> dict:
    release = release_root.resolve(strict=True)
    if PLUGIN.resolve() != release / "plugin":
        raise RuntimeError("SELF_TEST_PLUGIN_ROOT_MISMATCH")
    first_detection = _first_detection()
    binding, plan = first_detection.validate_binding(PLUGIN)
    if binding["status"] != "RELEASE_BOUND":
        raise RuntimeError("SELF_TEST_RELEASE_UNBOUND")
    from evidence_lane_plugin.build import runtime_source_identity
    from evidence_lane_plugin.engine import Engine

    identity = runtime_source_identity()
    if Path(identity["package_root"]).resolve() != PLUGIN.resolve():
        raise RuntimeError("SELF_TEST_PACKAGE_ROOT_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="evidence-lane-install-self-test-") as temporary:
        engine = Engine(Path(temporary))
        operation_count = len(engine.registry.schemas())
        engine.clients.close()
        engine.provider_workers.close()
    mcp_manifest = json.loads(
        (PLUGIN / "mcp/mcp-manifest.v4.json").read_text(encoding="utf-8")
    )
    expected_operations = mcp_manifest.get("action_count")
    if (
        not isinstance(expected_operations, int)
        or expected_operations < 1
        or operation_count != expected_operations
    ):
        raise RuntimeError("SELF_TEST_OPERATION_CATALOG_MISMATCH")
    static = PLUGIN / "src/evidence_lane_plugin/studio/assets-manifest.json"
    static_manifest = json.loads(static.read_text(encoding="utf-8"))
    if static_manifest.get("schema_version") != 1 or len(static_manifest.get("assets", {})) != 9:
        raise RuntimeError("SELF_TEST_STUDIO_ASSETS_MISMATCH")
    shared = json.loads(
        (PLUGIN / "toolchains/shared-toolchain.v4.json").read_text(encoding="utf-8")
    )
    if (
        not isinstance(shared.get("retained_tool_count"), int)
        or shared.get("retained_tool_count") < 1
        or plan.get("retained_tool_count") != shared.get("retained_tool_count")
    ):
        raise RuntimeError("SELF_TEST_TOOLCHAIN_COUNT_MISMATCH")
    required_install_records = [
        release / "toolchains/native-installation.v4.json",
        release / "toolchains/asset-installation.v4.json",
        release / "toolchains/provider-installation.v4.json",
    ]
    if not all(path.is_file() for path in required_install_records):
        raise RuntimeError("SELF_TEST_INSTALLATION_RECORD_MISSING")
    runtime_records_verified = False
    provider_count = 0
    native_count = 0
    asset_count = 0
    if installation_root is not None:
        from evidence_lane_plugin.installation_layout import StudioInstallation
        from evidence_lane_plugin.installed_providers import load_installed_providers
        from evidence_lane_plugin.shared_native_tools import resolve_native_tool
        from evidence_lane_plugin.shared_tool_assets import resolve_shared_asset

        installation = StudioInstallation(
            installation_root.resolve(strict=True), selected_release=release
        )
        native_record = json.loads(required_install_records[0].read_text(encoding="utf-8"))
        asset_record = json.loads(required_install_records[1].read_text(encoding="utf-8"))
        provider_record = json.loads(required_install_records[2].read_text(encoding="utf-8"))
        for row in native_record["tools"]:
            resolution = resolve_native_tool(row["tool_id"], runtime_root=release)
            if resolution.executable_sha256 != row["executable_sha256"]:
                raise RuntimeError("SELF_TEST_NATIVE_TOOL_MISMATCH")
        for row in asset_record["assets"]:
            _, observed = resolve_shared_asset(
                row["asset_id"], installation=installation
            )
            if observed["files_sha256"] != row["files_sha256"]:
                raise RuntimeError("SELF_TEST_SHARED_ASSET_MISMATCH")
        providers, provider_state = load_installed_providers(
            installation=installation, contracts=PLUGIN / "toolchains/providers"
        )
        if (
            len(providers) != len(provider_record["providers"])
            or provider_state["state"] != "observed"
            or any(row["state"] != "installed_record_verified" for row in provider_state["providers"])
        ):
            raise RuntimeError("SELF_TEST_PROVIDER_BINDING_MISMATCH")
        native_count = len(native_record["tools"])
        asset_count = len(asset_record["assets"])
        provider_count = len(providers)
        runtime_records_verified = True
    return {
        "status": "PASS",
        "operations": operation_count,
        "retained_tools": shared["retained_tool_count"],
        "studio_assets": len(static_manifest["assets"]),
        "package_source_digest": identity["source_digest"],
        "installation_records": [
            path.relative_to(release).as_posix() for path in required_install_records
        ],
        "runtime_records_verified": runtime_records_verified,
        "native_tool_count": native_count,
        "shared_asset_count": asset_count,
        "provider_count": provider_count,
        "project_state_created": False,
        "installed_native_execution_claimed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--installation-root", type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            verify(
                args.release_root, installation_root=args.installation_root
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
