"""Prepare the pinned PBIX reader runtime as a candidate asset, never install it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"
MANIFEST = PLUGIN / "toolchains/powerbi-pbix-runtime.v4.json"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def acquire(row, cache):
    name = row.get("filename", "python-3.13.15-embed-amd64.zip")
    path = cache / name
    if not path.exists():
        with urlopen(row["url"], timeout=45) as response, path.open("xb") as output:
            shutil.copyfileobj(response, output)
    if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
        raise ValueError("A pinned runtime input changed: " + name)
    return path


def extract_python(archive, output):
    seen = set()
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or ":" in info.filename
                or "\\" in info.filename
            ):
                raise ValueError("Invalid embedded runtime archive path.")
            if info.filename.casefold() in seen or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Invalid embedded runtime archive member.")
            seen.add(info.filename.casefold())
            target = output.joinpath(*path.parts)
            if not target.resolve().is_relative_to(output):
                raise ValueError("Runtime extraction leaves its candidate directory.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zipped.read(info))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".work/qualification/powerbi-pbix-artifact"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if os.name != "nt" or not output.is_relative_to((ROOT / ".work").resolve()) or output.exists():
        raise ValueError("Select a new candidate directory under .work on Windows.")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache = ROOT / ".work/qualification/pbixray-wheelhouse"
    cache.mkdir(parents=True, exist_ok=True)
    archives = [acquire(row, cache) for row in [manifest["python"], *manifest["packages"]]]
    output.mkdir(parents=True)
    extract_python(archives[0], output)
    site = output / "Lib/site-packages"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-compile",
            "--no-deps",
            "--no-index",
            "--only-binary=:all:",
            "--python-version",
            "3.13",
            "--platform",
            "win_amd64",
            "--find-links",
            str(cache),
            "--target",
            str(site),
            *[row["distribution"] + "==" + row["version"] for row in manifest["packages"]],
        ],
        check=True,
    )
    (output / "python313._pth").write_text(
        "python313.zip\n.\nLib/site-packages\n", encoding="utf-8"
    )
    source = PLUGIN / manifest["worker_entrypoint"]
    shutil.copyfile(source, output / "powerbi_pbix_child.py")
    reported = subprocess.run(
        [
            str(output / "python.exe"),
            "-I",
            "-S",
            "-B",
            str(output / "powerbi_pbix_child.py"),
            "--version",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if reported != "evidence-lane-pbix 4.0.0; PBIXRay 0.15.5; Python 3.13.15; protocol 1":
        raise ValueError("The candidate parser did not identify its pinned runtime.")
    (output / "sources").mkdir()
    shutil.copyfile(MANIFEST, output / "sources/runtime-inputs.json")
    shutil.copyfile(source, output / "sources/powerbi_pbix_child.py")
    receipt = {
        "schema": "evidence-lane.powerbi-pbix-build.v4",
        "input_manifest_sha256": digest(MANIFEST),
        "adapter_sha256": digest(source),
        "reported_version": reported,
        "dependency_count": len(manifest["packages"]),
        "platform": "windows-x86_64",
        "runtime_compilation": False,
        "user_installation_performed": False,
        "licenses": "Python LICENSE.txt plus preserved package dist-info license files and notices.",
    }
    (output / "build-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"artifact": str(output), **receipt}, indent=2))


if __name__ == "__main__":
    main()
