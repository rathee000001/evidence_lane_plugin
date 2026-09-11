"""Build AMD's source-only ROCm selector wheel with explicit license provenance.

AMD publishes the ``rocm`` selector for Windows as an sdist because its
dependencies are chosen at build time. The admitted 7.2.1 sdist omits both
license metadata and copied license text. This builder verifies the published
sdist, verifies that its setup template matches an exact upstream TheRock
revision, restores that revision's MIT license, and builds twice from fresh
trees before admitting byte-identical wheel output.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SOURCE_URL = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/rocm-7.2.1.tar.gz"
SOURCE_HASH = "9084902eaa69213a00a90784ad89e6e5fe73c702df0cc6cc3a70d777c7a6142b"
SOURCE_ROOT = "rocm-7.2.1"
SOURCE_SETUP_HASH = "09340cf0ead19a2adef4ec303456171fb3d4f421703d5f352f4dea92b4f71dcd"
SOURCE_SETUP_NORMALIZED_HASH = (
    "dd5c4f2b98195adfbe825488a785be7c70c3059b0bcba535332d0abe27cf6bd7"
)
UPSTREAM_REPOSITORY = "https://github.com/ROCm/TheRock"
UPSTREAM_COMMIT = "b7533bf7e101239fab6b5894e9b0c72febde2311"
UPSTREAM_SETUP_PATH = "build_tools/packaging/python/templates/rocm/setup.py"
UPSTREAM_LICENSE_PATH = "LICENSE"
UPSTREAM_LICENSE_URL = (
    f"https://raw.githubusercontent.com/ROCm/TheRock/{UPSTREAM_COMMIT}/LICENSE"
)
UPSTREAM_LICENSE_HASH = (
    "7728762ecd3256782c89e28b1887727a484deda6356887ef2f2898598e950cb8"
)
SOURCE_DATE_EPOCH = 1_770_000_000
WHEEL_NAME = "rocm-7.2.1-py3-none-any.whl"
MAX_SOURCE_MEMBERS = 256
MAX_SOURCE_BYTES = 16 * 1024 * 1024


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_extract(source: Path, destination: Path) -> Path:
    """Extract the tiny source archive without accepting links or path escapes."""

    destination = destination.resolve()
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        if not members or len(members) > MAX_SOURCE_MEMBERS:
            raise ValueError("ROCm source archive member budget differs")
        total = 0
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or ".." in relative.parts
                or not (member.isdir() or member.isfile())
            ):
                raise ValueError("ROCm source archive contains an unsafe member")
            target = destination.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("ROCm source archive member escaped its destination")
            if member.isfile():
                total += member.size
        if total > MAX_SOURCE_BYTES:
            raise ValueError("ROCm source archive expanded-byte budget differs")
        archive.extractall(destination, members=members, filter="data")
    root = destination / SOURCE_ROOT
    if not root.is_dir():
        raise ValueError("ROCm source archive root differs")
    return root


def prepare_source(source: Path, license_path: Path, destination: Path) -> dict[str, object]:
    """Verify and minimally transform one fresh copy of AMD's source tree."""

    source_root = _safe_extract(source, destination)
    setup_path = source_root / "setup.py"
    setup_bytes = setup_path.read_bytes()
    if sha256_bytes(setup_bytes) != SOURCE_SETUP_HASH:
        raise ValueError("ROCm setup.py differs from the admitted sdist")
    normalized_setup = setup_bytes.replace(b"\r\n", b"\n")
    if sha256_bytes(normalized_setup) != SOURCE_SETUP_NORMALIZED_HASH:
        raise ValueError("ROCm setup.py differs from the exact upstream template")

    license_bytes = license_path.read_bytes()
    if sha256_bytes(license_bytes) != UPSTREAM_LICENSE_HASH:
        raise ValueError("The copied upstream ROCm license differs")

    newline = b"\r\n" if b"\r\n" in setup_bytes else b"\n"
    needle = b"setup(" + newline + b'    name="rocm",' + newline
    replacement = (
        needle
        + b'    license_expression="MIT",'
        + newline
        + b'    license_files=["LICENSE"],'
        + newline
    )
    if setup_bytes.count(needle) != 1:
        raise ValueError("ROCm setup.py transformation anchor differs")
    transformed_setup = setup_bytes.replace(needle, replacement, 1)
    setup_path.write_bytes(transformed_setup)
    restored_license = source_root / "LICENSE"
    if restored_license.exists():
        raise ValueError("The admitted ROCm sdist unexpectedly contains a license")
    restored_license.write_bytes(license_bytes)
    return {
        "source_root": source_root,
        "source_setup_sha256": sha256_bytes(setup_bytes),
        "source_setup_normalized_sha256": sha256_bytes(normalized_setup),
        "transformed_setup_sha256": sha256_bytes(transformed_setup),
        "restored_license_sha256": sha256_bytes(license_bytes),
    }


def inspect_wheel(wheel: Path, expected_license: bytes) -> dict[str, object]:
    """Require PEP 639 metadata and the exact copied license in the wheel."""

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("ROCm wheel metadata inventory differs")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
        expressions = [
            line.removeprefix("License-Expression: ")
            for line in metadata.splitlines()
            if line.startswith("License-Expression: ")
        ]
        if expressions != ["MIT"]:
            raise ValueError("ROCm wheel lacks the exact MIT license expression")
        license_names = [
            name
            for name in names
            if ".dist-info/licenses/" in name and PurePosixPath(name).name == "LICENSE"
        ]
        if len(license_names) != 1 or archive.read(license_names[0]) != expected_license:
            raise ValueError("ROCm wheel lacks the exact copied upstream license")
        return {
            "metadata_path": metadata_names[0],
            "license_expression": "MIT",
            "license_path": license_names[0],
            "license_sha256": sha256_bytes(expected_license),
        }


def _build_once(
    *, source: Path, license_path: Path, build_root: Path, index: int
) -> tuple[Path, dict[str, object]]:
    tree = build_root / f"build-{index}" / "source"
    wheel_directory = build_root / f"build-{index}" / "wheelhouse"
    tree.mkdir(parents=True)
    wheel_directory.mkdir(parents=True)
    transformation = prepare_source(source, license_path, tree)
    command = [
        sys.executable,
        "-I",
        "-m",
        "pip",
        "wheel",
        "--no-index",
        "--no-deps",
        "--no-build-isolation",
        "--wheel-dir",
        str(wheel_directory),
        str(transformation["source_root"]),
    ]
    environment = {
        **os.environ,
        "PIP_CONFIG_FILE": os.devnull,
        "PYTHONHASHSEED": "0",
        "ROCM_SDK_TARGET_FAMILY": "custom",
        "SOURCE_DATE_EPOCH": str(SOURCE_DATE_EPOCH),
    }
    completed = subprocess.run(
        command,
        env=environment,
        check=False,
        timeout=120,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise RuntimeError("ROCm wheel build failed:\n" + completed.stdout[-8_000:])
    wheels = sorted(wheel_directory.glob("*.whl"))
    if len(wheels) != 1 or wheels[0].name != WHEEL_NAME:
        raise ValueError("ROCm wheel build output inventory differs")
    return wheels[0], transformation


def _replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _project_provider_contract(
    root: Path, receipt: dict[str, object], receipt_bytes: bytes
) -> None:
    """Update the exact wheel hash in both provider contract projections."""

    contract_root = root / "contracts/optional-runtimes"
    source_path = contract_root / "rocm.json"
    manifest = json.loads(source_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("runtime_id") != "rocm"
        or manifest.get("python_version") != "3.12"
    ):
        raise ValueError("The ROCm provider contract identity differs")
    packages = manifest.get("packages")
    if not isinstance(packages, list):
        raise TypeError("The ROCm provider package inventory differs")
    selected = [row for row in packages if row.get("name") == "rocm"]
    if len(selected) != 1:
        raise ValueError("The ROCm selector package inventory differs")
    package = selected[0]
    if (
        package.get("version") != "7.2.1"
        or package.get("wheel_file") != receipt["wheel_file"]
        or package.get("source_build_receipt") != "rocm-source-build.json"
    ):
        raise ValueError("The ROCm selector package contract differs")
    package["sha256"] = receipt["wheel_sha256"]
    package["source_build_receipt_sha256"] = sha256_bytes(receipt_bytes)
    packages.sort(key=lambda row: row["name"])

    lines = ["# Exact resolved wheels; install only in the matching isolated runtime."]
    for row in packages:
        requirement = (
            f"{row['name']} @ {row['url']}"
            if "url" in row
            else f"{row['name']}=={row['version']}"
        )
        lines.append(requirement + f" --hash=sha256:{row['sha256']}")
    lock_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    manifest["lock_sha256"] = sha256_bytes(lock_bytes)
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    destinations = [
        contract_root,
        root / "plugins/evidence-lane-plugin/toolchains/providers",
    ]
    for destination in destinations:
        _replace(destination / "rocm.lock.txt", lock_bytes)
        _replace(destination / "rocm.json", manifest_bytes)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / ".work/optional-wheels/rocm-source/rocm-7.2.1.tar.gz"
    if sha256_bytes(source.read_bytes()) != SOURCE_HASH:
        raise ValueError("AMD's reviewed metadata source changed")
    build_versions = {
        package: importlib.metadata.version(package)
        for package in ("pip", "setuptools", "wheel")
    }
    if build_versions != {"pip": "25.3", "setuptools": "83.0.0", "wheel": "0.46.3"}:
        raise ValueError("Use the locked ROCm wheel build environment")

    license_path = root / "contracts/optional-runtimes/licenses/rocm/LICENSE"
    license_bytes = license_path.read_bytes()
    if sha256_bytes(license_bytes) != UPSTREAM_LICENSE_HASH:
        raise ValueError("The copied upstream ROCm license differs")

    temporary_parent = root / ".work/optional-wheels/rocm-build"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="double-build-", dir=temporary_parent) as raw:
        build_root = Path(raw)
        first, first_transformation = _build_once(
            source=source,
            license_path=license_path,
            build_root=build_root,
            index=1,
        )
        second, second_transformation = _build_once(
            source=source,
            license_path=license_path,
            build_root=build_root,
            index=2,
        )
        first_bytes = first.read_bytes()
        second_bytes = second.read_bytes()
        wheel_hashes = [sha256_bytes(first_bytes), sha256_bytes(second_bytes)]
        if first_bytes != second_bytes:
            raise ValueError("The two fresh ROCm wheel builds are not byte-identical")
        first_evidence = {
            key: value
            for key, value in first_transformation.items()
            if key != "source_root"
        }
        second_evidence = {
            key: value
            for key, value in second_transformation.items()
            if key != "source_root"
        }
        if first_evidence != second_evidence:
            raise ValueError("The two fresh ROCm source transformations differ")
        wheel_license = inspect_wheel(first, license_bytes)

        wheel_targets = [
            root / "contracts/optional-runtimes/wheels" / WHEEL_NAME,
            root
            / "plugins/evidence-lane-plugin/toolchains/providers/wheels"
            / WHEEL_NAME,
        ]
        for target in wheel_targets:
            _replace(target, first_bytes)

    receipt_body = {
        "schema": "evidence-lane.rocm-source-wheel-build.v1",
        "status": "REPRODUCIBLE_LICENSE_RESTORED_WHEEL",
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_HASH,
        "source_setup_sha256": SOURCE_SETUP_HASH,
        "source_setup_normalized_sha256": SOURCE_SETUP_NORMALIZED_HASH,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_setup_path": UPSTREAM_SETUP_PATH,
        "upstream_setup_sha256": SOURCE_SETUP_NORMALIZED_HASH,
        "upstream_license_path": UPSTREAM_LICENSE_PATH,
        "upstream_license_url": UPSTREAM_LICENSE_URL,
        "upstream_license_sha256": UPSTREAM_LICENSE_HASH,
        "license_restoration_reason": (
            "The AMD-published source archive omitted license metadata and copied "
            "license text; its setup.py matches the exact upstream template after "
            "newline normalization."
        ),
        "transformation": {
            "files_added": [{"path": "LICENSE", "sha256": UPSTREAM_LICENSE_HASH}],
            "files_modified": [
                {
                    "path": "setup.py",
                    "before_sha256": SOURCE_SETUP_HASH,
                    "after_sha256": first_transformation["transformed_setup_sha256"],
                    "added_setup_arguments": [
                        'license_expression="MIT"',
                        'license_files=["LICENSE"]',
                    ],
                }
            ],
        },
        "build_backend": "setuptools==83.0.0",
        "build_environment": build_versions,
        "build_isolation": False,
        "network_during_build": False,
        "target_family": "custom",
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "reproducibility": {
            "fresh_build_count": 2,
            "wheel_sha256s": wheel_hashes,
            "byte_identical": True,
        },
        "wheel_file": "wheels/" + WHEEL_NAME,
        "wheel_bytes": len(first_bytes),
        "wheel_sha256": wheel_hashes[0],
        "wheel_license": wheel_license,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical(receipt_body)),
    }
    receipt_bytes = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
    receipt_targets = [
        root / "contracts/optional-runtimes/rocm-source-build.json",
        root
        / "plugins/evidence-lane-plugin/toolchains/providers/rocm-source-build.json",
    ]
    for target in receipt_targets:
        _replace(target, receipt_bytes)

    _project_provider_contract(root, receipt, receipt_bytes)

    for target in wheel_targets:
        if sha256_bytes(target.read_bytes()) != receipt["wheel_sha256"]:
            raise ValueError("A projected ROCm wheel differs after write")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
