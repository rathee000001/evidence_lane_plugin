"""Seal the reproducible Windows CPython 3.14 combined runtime lock inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
OUTPUT = PLUGIN / "requirements.runtime-lock.v4.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def build() -> dict:
    output = PLUGIN / "requirements.runtime.lock.txt"
    text = output.read_text(encoding="utf-8")
    packages = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text, re.MULTILINE)
    if (
        not packages
        or len({re.sub(r"[-_.]+", "-", name).casefold() for name, _ in packages})
        != len(packages)
        or " @ " in text
        or "://" in text
        or "--index-url" in text
    ):
        raise RuntimeError("The combined runtime lock is not exact and offline-source ready")
    inputs = [
        ROOT / "requirements.runtime.in",
        ROOT / "requirements.in",
        ROOT / "requirements.toolchain.in",
        PLUGIN / "requirements.lock.txt",
    ]
    body = {
        "schema": "evidence-lane.resolved-runtime-lock.v4",
        "status": "COMPLETE_PREINSTALL_LOCK",
        "target": {
            "python_version": "3.14",
            "python_platform": "x86_64-pc-windows-msvc",
        },
        "compile_command": [
            "python",
            "-B",
            "-m",
            "uv",
            "pip",
            "compile",
            "requirements.runtime.in",
            "--python-version",
            "3.14",
            "--python-platform",
            "x86_64-pc-windows-msvc",
            "--generate-hashes",
            "--quiet",
            "--no-header",
            "--output-file",
            "plugins/evidence-lane-plugin/requirements.runtime.lock.txt",
        ],
        "inputs": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in inputs
        ],
        "output": "requirements.runtime.lock.txt",
        "output_bytes": output.stat().st_size,
        "output_sha256": sha256(output),
        "package_count": len(packages),
        "direct_network_locations": 0,
        "release_builder_rewrites_to_single-wheel_offline_lock": True,
    }
    return {
        **body,
        "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise RuntimeError("The combined runtime lock receipt changed")
    else:
        OUTPUT.write_text(expected, encoding="utf-8")
    print(json.dumps({"check": args.check, "status": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
