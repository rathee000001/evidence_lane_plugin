"""Explicit diagnostic entrypoint for the same first-detection installer used by MCP."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def _module():
    path = PLUGIN / "scripts/first_detection.py"
    spec = importlib.util.spec_from_file_location("evidence_lane_first_detection", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("FIRST_DETECTION_LAUNCHER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation-root", type=Path)
    parser.add_argument("--ghostscript-license-receipt", type=Path)
    args = parser.parse_args(argv)
    module = _module()
    environment = dict(os.environ)
    if args.installation_root is not None:
        environment["EVIDENCE_LANE_STUDIO_ROOT"] = str(
            args.installation_root.expanduser().absolute()
        )
    grants = []
    if args.ghostscript_license_receipt is not None:
        if not args.ghostscript_license_receipt.is_file():
            parser.error("The explicit Ghostscript license receipt does not exist.")
        environment["EVIDENCE_LANE_GHOSTSCRIPT_LICENSE_RECEIPT"] = str(
            args.ghostscript_license_receipt.absolute()
        )
        grants.append("ghostscript")
    try:
        binding, _ = module.validate_binding(PLUGIN)
        if binding["status"] != "RELEASE_BOUND":
            raise module.FirstDetectionError(
                "RELEASE_BINDING_UNBOUND",
                "This development source awaits its exact candidate release assets and tag.",
            )
        if args.ghostscript_license_receipt is not None:
            module.validate_ghostscript_license_grant(
                args.ghostscript_license_receipt,
                release_binding_sha256=module.sha256(
                    PLUGIN / "provisioning/release-binding.v4.json"
                ),
            )
        installer = module.FirstDetectionInstaller(
            PLUGIN,
            module.installation_root(environment),
            license_grants=grants,
        )
        result = installer.ensure()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "installation_state": result["installation_state"],
                    "release_root": str(result["release_root"]),
                    "runtime_python": str(result["runtime_python"]),
                    "plugin_root": str(result["plugin_root"]),
                    "project_data_changed": False,
                }
            )
        )
        return 0
    except module.FirstDetectionError as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": {"code": error.code, "message": str(error)},
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
