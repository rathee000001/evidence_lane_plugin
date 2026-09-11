"""Pin downloaded wheels and their transitive closure for the current target."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheels", type=Path, default=Path(".work/wheels"))
    args = parser.parse_args()
    wheels = {}
    for path in sorted(args.wheels.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            metadata_files = [
                name
                for name in archive.namelist()
                if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_files) != 1:
                raise ValueError("Wheel metadata must be unique")
            metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
        name = canonicalize_name(metadata["Name"])
        if name in wheels:
            raise ValueError(f"More than one wheel for {name}; use a fresh wheel directory")
        wheels[name] = {
            "name": name,
            "version": metadata["Version"],
            "filename": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "requires": metadata.get_all("Requires-Dist", []),
        }

    def closure(roots):
        pending = [Requirement(root) for root in roots]
        extras = {}
        while pending:
            request = pending.pop()
            name = canonicalize_name(request.name)
            wheel = wheels.get(name)
            if wheel is None or wheel["version"] not in request.specifier:
                raise ValueError(f"Resolved wheel does not satisfy {request}")
            selected_extras = extras.get(name, set()) | request.extras
            if name in extras and extras[name] == selected_extras:
                continue
            extras[name] = selected_extras
            for dependency in wheel["requires"]:
                requirement = Requirement(dependency)
                contexts = [
                    dict(default_environment(), extra=extra) for extra in selected_extras | {""}
                ]
                if requirement.marker is None or any(
                    requirement.marker.evaluate(c) for c in contexts
                ):
                    pending.append(requirement)
        return sorted(extras)

    core = [
        line.strip() for line in Path("requirements.in").read_text().splitlines() if line.strip()
    ]
    development = core + [
        line.strip()
        for line in Path("requirements-dev.in").read_text().splitlines()
        if line.strip() and not line.startswith("-r ")
    ]
    sets = {"runtime": closure(core), "development": closure(development)}
    for name, path in [
        ("runtime", "requirements.lock.txt"),
        ("development", "requirements-dev.lock.txt"),
    ]:
        lines = ["# Windows amd64 / CPython 3.14; generated from verified downloaded wheel bytes."]
        for package in sets[name]:
            wheel = wheels[package]
            lines.append(f"{package}=={wheel['version']} --hash=sha256:{wheel['sha256']}")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "python_version": platform.python_version(),
        "implementation": sys.implementation.name,
        "platform": platform.system(),
        "machine": platform.machine(),
        "dependency_sets": sets,
        "wheels": [
            {k: v for k, v in wheel.items() if k != "requires"} for wheel in wheels.values()
        ],
    }
    Path("contracts/runtime-lock.win-amd64-cp314.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "runtime_packages": len(sets["runtime"]),
                "development_packages": len(sets["development"]),
            }
        )
    )


if __name__ == "__main__":
    main()
