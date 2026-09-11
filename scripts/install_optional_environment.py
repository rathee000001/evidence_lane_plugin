"""Build a new isolated development runtime from its exact selected wheel lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def run(arguments):
    subprocess.run(arguments, check=True, timeout=1800,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", choices=("cpu", "cuda", "directml", "rocm"), required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument('--contracts', type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    contracts = arguments.contracts or root / 'plugins/evidence-lane-plugin/toolchains/providers'
    manifest_path = contracts.resolve(strict=True) / f"{arguments.id}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lock = manifest_path.with_suffix(".lock.txt")
    if hashlib.sha256(lock.read_bytes()).hexdigest() != manifest["lock_sha256"]:
        raise ValueError("The optional runtime lock changed")
    identity = manifest["lock_sha256"][:16]
    destination = root / ".work/optional-envs" / f"{arguments.id}-{identity}"
    if destination.exists():
        raise ValueError("A runtime already exists at this address; inspect it instead of overwriting")
    base = arguments.python.resolve(strict=True)
    version = subprocess.check_output([str(base), "-I", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                                      text=True, timeout=10).strip()
    if version != manifest["python_version"]:
        raise ValueError("The selected interpreter does not match the resolved wheel target")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run([str(base), "-I", "-m", "venv", str(destination)])
    executable = destination / "Scripts/python.exe"
    local_wheels = []
    for package in manifest["packages"]:
        if "wheel_file" in package:
            wheel = manifest_path.parent / package["wheel_file"]
            if (wheel.resolve().parent != (manifest_path.parent / "wheels").resolve()
                    or hashlib.sha256(wheel.read_bytes()).hexdigest() != package["sha256"]):
                raise ValueError("The locally built wheel differs from its locked content")
            local_wheels = ["--find-links", str(wheel.parent)]
    run([str(executable), "-I", "-m", "pip", "install", '--disable-pip-version-check', '--progress-bar', 'off',
         "--require-hashes", "--only-binary=:all:",
         *local_wheels, "-r", str(lock)])
    run([str(executable), "-I", "-m", "pip", "check"])
    record = {"runtime_id": arguments.id, "lock_sha256": manifest["lock_sha256"],
              "python": str(executable), "manifest_path": str(manifest_path),
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "installation_state": "installed_from_locked_wheels", "execution_state": "not_probed"}
    (destination / "environment.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
