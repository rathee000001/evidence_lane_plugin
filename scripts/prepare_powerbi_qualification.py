"""Compose previously built Power BI assets in an isolated qualification root."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tom", type=Path, required=True)
    parser.add_argument("--pbix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    scratch = (ROOT / ".work").resolve()
    sources = [
        ("powerbi_tom_runtime", args.tom.resolve()),
        ("powerbi_pbix_runtime", args.pbix.resolve()),
    ]
    if not output.is_relative_to(scratch) or output.exists():
        raise ValueError("Select a new isolated qualification directory under .work.")
    for _, source in sources:
        if not source.is_relative_to(scratch) or not (source / "build-receipt.json").is_file():
            raise ValueError("Build each candidate artifact under .work first.")
        if (
            source.is_symlink()
            or source.is_junction()
            or any(path.is_symlink() or path.is_junction() for path in source.rglob("*"))
        ):
            raise ValueError("Qualification artifacts must not contain links.")
    pbix_receipt = json.loads((args.pbix / "build-receipt.json").read_text(encoding="utf-8"))
    child = ROOT / "plugins/evidence-lane-plugin/src/evidence_lane_plugin/powerbi_pbix_child.py"
    if pbix_receipt["adapter_sha256"] != digest(child) or digest(args.pbix / child.name) != digest(
        child
    ):
        raise ValueError("Rebuild the PBIX artifact from the current worker source.")
    output.mkdir(parents=True)
    records = []
    for identity, source in sources:
        target = output / identity
        shutil.copytree(source, target)
        files = [
            {
                "path": path.relative_to(target).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            for path in sorted(target.rglob("*"))
            if path.is_file()
        ]
        records.append(
            {
                "asset_id": identity,
                "path": identity,
                "status": "verified",
                "files": files,
                "qualification": "isolated_candidate_files_only",
            }
        )
    manifest = {
        "schema": "evidence-lane.shared-tool-assets.v4",
        "status": "verified",
        "assets": records,
        "user_installation_performed": False,
    }
    canonical = (
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        + "\n"
    ).encode()
    manifest["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
    (output / "toolchains").mkdir()
    (output / "toolchains/asset-installation.v4.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "fixture": str(output),
                "receipt_sha256": manifest["receipt_sha256"],
                "user_installation_performed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
