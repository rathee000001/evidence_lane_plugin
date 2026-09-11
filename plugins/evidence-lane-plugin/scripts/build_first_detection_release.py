"""Build deterministic component archives and bind them to one immutable repository tag."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any

PLUGIN = Path(__file__).resolve().parents[1]
COMPONENT_MANIFEST = "EVIDENCE_LANE_COMPONENT.json"
SOURCE_MANIFEST = "provisioning/source-manifest.v4.json"
RELEASE_BINDING = "provisioning/release-binding.v4.json"
REPOSITORY = "https://github.com/rathee000001/evidence_lane_plugin"
SPARSE_ROOT = "plugins/evidence-lane-plugin"
MAX_COMPONENT_FILES = 100_000
# GitHub release assets must be under 2 GiB. Leave a bounded margin for the
# deterministic component manifest and ZIP framing around the 1.92 GB CUDA wheel.
MAX_COMPONENT_BYTES = 2_140_000_000
MAX_COMPONENT_PAYLOAD_BYTES = 2_100_000_000
MAX_RELEASE_BYTES = 32_000_000_000
MAX_SOURCE_FILES = 32_768
MAX_SOURCE_BYTES = 256 * 1024 * 1024
EXCLUDED_SOURCE_PATHS = {SOURCE_MANIFEST, RELEASE_BINDING}
FORBIDDEN_CREDENTIAL_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "secrets.json",
    "pip.conf",
    "id_rsa",
    "id_ed25519",
}


class ReleaseBuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sealed(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "receipt_sha256": hashlib.sha256(canonical(body)).hexdigest()}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_files(root: Path, *, file_limit: int, byte_limit: int) -> list[Path]:
    root = root.resolve(strict=True)
    result = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ReleaseBuildError(f"Linked release input is forbidden: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ReleaseBuildError(f"Irregular release input is forbidden: {path}")
        relative = path.relative_to(root).as_posix()
        if not safe_relative(relative):
            raise ReleaseBuildError(f"Unsafe release input path: {relative}")
        result.append(path)
        total += path.stat().st_size
        if len(result) > file_limit or total > byte_limit:
            raise ReleaseBuildError("Release input exceeds its file or byte budget")
    if not result:
        raise ReleaseBuildError("A component archive cannot be empty")
    return result


def safe_relative(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    return all(
        part.rstrip(" .").casefold().split(".", 1)[0] not in reserved
        and part == part.rstrip(" .")
        and ":" not in part
        for part in path.parts
    )


def credential_path(value: str) -> bool:
    name = PurePosixPath(value).name.casefold()
    return (
        name in FORBIDDEN_CREDENTIAL_NAMES
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in {".pfx", ".p12", ".key"}
    )


def build_offline_lock(
    packages: list[dict[str, Any]],
    wheelhouse: Path,
    output: Path,
    *,
    license_output: Path | None = None,
) -> dict[str, Any]:
    root = wheelhouse.resolve(strict=True)
    wheels = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    if (
        not 1 <= len(wheels) <= 2_000
        or any(
            not path.is_file()
            or path.is_symlink()
            or path.suffix.casefold() != ".whl"
            for path in wheels
        )
    ):
        raise ReleaseBuildError("An offline wheelhouse must contain wheel files only")
    wheel_hashes = {sha256(path): path for path in wheels}
    if len(wheel_hashes) != len(wheels):
        raise ReleaseBuildError("Duplicate wheel bytes are not allowed")
    lines = []
    selected_hashes = set()
    selected_wheels = []
    names = set()
    for package in packages:
        name = str(package.get("name") or package.get("distribution") or "")
        version = str(package.get("version") or "")
        normalized = re.sub(r"[-_.]+", "-", name).casefold()
        candidates = (
            [str(package["sha256"])]
            if package.get("sha256")
            else [str(value) for value in package.get("hashes") or []]
        )
        matching = sorted(set(candidates).intersection(wheel_hashes))
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}", version) is None
            or normalized in names
            or len(matching) != 1
        ):
            raise ReleaseBuildError(
                f"Package metadata does not select one exact release wheel: {name}"
            )
        names.add(normalized)
        selected_hashes.add(matching[0])
        selected_wheels.append(
            wheel_license_record(
                wheel_hashes[matching[0]], expected_name=name, expected_version=version
            )
        )
        lines.append(f"{name}=={version} --hash=sha256:{matching[0]}")
    if selected_hashes != set(wheel_hashes):
        raise ReleaseBuildError("Every release wheel must have one offline lock owner")
    _link_rocm_release_license_corpus(selected_wheels)
    incomplete = [
        row["name"]
        for row in selected_wheels
        if not row["license_expression_or_classifiers"] and not row["license_files"]
    ]
    if incomplete:
        raise ReleaseBuildError(
            "Release wheels have no license metadata or copied text: "
            + ", ".join(sorted(incomplete, key=str.casefold))
        )
    content = ("\n".join(sorted(lines, key=str.casefold)) + "\n").encode("utf-8")
    output = output.resolve()
    if output.exists():
        raise ReleaseBuildError("Offline lock output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(content)
    result = {
        "schema": "evidence-lane.offline-wheel-lock-build.v4",
        "status": "PASS",
        "path": str(output),
        "sha256": sha256(output),
        "requirements": len(lines),
        "wheels": len(wheels),
        "network_locations": 0,
    }
    if license_output is not None:
        license_output = license_output.resolve()
        if license_output.exists():
            raise ReleaseBuildError("Runtime license output already exists")
        license_index = sealed(
            {
                "schema": "evidence-lane.runtime-wheel-license-index.v4",
                "status": "COMPLETE_RELEASE_WHEEL_LICENSE_EVIDENCE",
                "wheel_count": len(selected_wheels),
                "records": sorted(
                    selected_wheels,
                    key=lambda row: (row["name"].casefold(), row["version"]),
                ),
            }
        )
        license_output.parent.mkdir(parents=True, exist_ok=True)
        with license_output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(license_index, ensure_ascii=False, indent=2) + "\n")
        result["license_index"] = str(license_output)
        result["license_index_sha256"] = sha256(license_output)
    return result


def wheel_license_record(
    path: Path, *, expected_name: str, expected_version: str
) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata_names = [
                name
                for name in names
                if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise ReleaseBuildError("A release wheel must have one METADATA file")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            actual_name = str(metadata.get("Name") or "")
            actual_version = str(metadata.get("Version") or "")
            normalize = lambda value: re.sub(r"[-_.]+", "-", value).casefold()
            if normalize(actual_name) != normalize(expected_name) or actual_version != expected_version:
                raise ReleaseBuildError("A wheel's name or version differs from its lock")
            expressions = [
                value.strip()
                for value in (
                    metadata.get_all("License-Expression", [])
                    + metadata.get_all("License", [])
                    + [
                        value.removeprefix("License :: ").strip()
                        for value in metadata.get_all("Classifier", [])
                        if value.startswith("License :: ")
                    ]
                )
                if value.strip() and value.strip().casefold() != "unknown"
            ]
            dist_info = metadata_names[0].split("/", 1)[0] + "/"
            wheel_sha256 = sha256(path)
            license_files = []
            for name in sorted(names):
                info = archive.getinfo(name)
                basename = PurePosixPath(name).name.casefold()
                if (
                    info.is_dir()
                    or not (
                        "/licenses/" in name.casefold()
                        or "/share/doc/" in name.casefold()
                        and basename.startswith(("license", "copying", "notice"))
                        or name.startswith(dist_info)
                        and basename.startswith(("license", "copying", "notice"))
                    )
                ):
                    continue
                if info.file_size > 4 * 1024 * 1024:
                    raise ReleaseBuildError("A wheel license file exceeds its byte budget")
                content = archive.read(name)
                license_files.append(
                    {
                        "path": name,
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "source_wheel": path.name,
                        "source_wheel_sha256": wheel_sha256,
                        "source_container": "wheel",
                    }
                )
            if actual_name == "rocm-sdk-devel" and actual_version == "7.2.1":
                nested_name = "rocm_sdk_devel/_devel.tar"
                if nested_name not in names:
                    raise ReleaseBuildError("The ROCm devel license container is missing")
                nested_info = archive.getinfo(nested_name)
                if not 1 <= nested_info.file_size <= 1_500_000_000:
                    raise ReleaseBuildError("The ROCm devel license container size differs")
                with archive.open(nested_name) as nested_stream, tarfile.open(
                    fileobj=nested_stream, mode="r|*"
                ) as nested:
                    member_count = 0
                    declared_bytes = 0
                    for member in nested:
                        member_count += 1
                        declared_bytes += max(member.size, 0)
                        relative = PurePosixPath(member.name)
                        if (
                            member_count > 20_000
                            or declared_bytes > 8_000_000_000
                            or relative.is_absolute()
                            or ".." in relative.parts
                            or not (
                                member.isdir()
                                or member.isfile()
                                or member.issym()
                                or member.islnk()
                            )
                        ):
                            raise ReleaseBuildError(
                                "The ROCm devel license container inventory is unsafe"
                            )
                        basename = relative.name.casefold()
                        if (
                            not member.isfile()
                            or not (
                                "/share/doc/" in member.name.casefold()
                                or "/llvm/support/" in member.name.casefold()
                            )
                            or not basename.startswith(("license", "copying", "notice"))
                        ):
                            continue
                        if member.size > 4 * 1024 * 1024:
                            raise ReleaseBuildError(
                                "A nested ROCm license file exceeds its byte budget"
                            )
                        reader = nested.extractfile(member)
                        if reader is None:
                            raise ReleaseBuildError(
                                "A nested ROCm license file cannot be read"
                            )
                        content = reader.read(4 * 1024 * 1024 + 1)
                        if len(content) != member.size:
                            raise ReleaseBuildError(
                                "A nested ROCm license file size differs"
                            )
                        license_files.append(
                            {
                                "path": nested_name + "/" + member.name,
                                "bytes": len(content),
                                "sha256": hashlib.sha256(content).hexdigest(),
                                "source_wheel": path.name,
                                "source_wheel_sha256": wheel_sha256,
                                "source_container": nested_name,
                            }
                        )
            return {
                "name": actual_name,
                "version": actual_version,
                "wheel": path.name,
                "wheel_sha256": wheel_sha256,
                "license_expression_or_classifiers": sorted(set(expressions)),
                "license_files": sorted(
                    license_files, key=lambda row: (row["source_wheel"], row["path"])
                ),
            }
    except zipfile.BadZipFile as exc:
        raise ReleaseBuildError("A release wheel is not a valid ZIP archive") from exc


def _link_rocm_release_license_corpus(records: list[dict[str, Any]]) -> None:
    """Link exact same-release ROCm wheels to AMD's nested license corpus."""

    by_name = {
        re.sub(r"[-_.]+", "-", row["name"]).casefold(): row for row in records
    }
    targets = {
        # amd_comgr and hipcc licenses are direct members of the core wheel.
        # The linked corpus adds the remaining ROCm-core and LLVM texts.
        "rocm-sdk-core": {"rocm-core"},
        "rocm-sdk-libraries-custom": {
            "hipblas",
            "hipblas-common",
            "hipblaslt",
            "hipfft",
            "hiprand",
            "hipsolver",
            "hipsparse",
            "miopen-hip",
            "rocblas",
            "rocfft",
            "rocrand",
            "rocsolver",
            "rocsparse",
        },
    }
    present = set(targets).intersection(by_name)
    if not present:
        return
    devel = by_name.get("rocm-sdk-devel")
    if devel is None or devel.get("version") != "7.2.1":
        raise ReleaseBuildError("The matching ROCm devel license corpus is required")
    nested_files = [
        row
        for row in devel["license_files"]
        if row.get("source_container") == "rocm_sdk_devel/_devel.tar"
    ]
    for target_name in sorted(present):
        target = by_name[target_name]
        if target.get("version") != devel["version"]:
            raise ReleaseBuildError("Linked ROCm license versions differ")
        required = targets[target_name]
        selected = [
            row
            for row in nested_files
            if any(
                f"/share/doc/{project}/" in row["path"].casefold()
                for project in required
            )
        ]
        observed = {
            project
            for project in required
            if any(
                f"/share/doc/{project}/" in row["path"].casefold()
                for row in selected
            )
        }
        if observed != required:
            raise ReleaseBuildError(
                "The ROCm devel wheel does not cover every linked project license"
            )
        if target_name == "rocm-sdk-core":
            llvm = [
                row
                for row in nested_files
                if "/llvm/support/license" in row["path"].casefold()
            ]
            if len(llvm) != 1:
                raise ReleaseBuildError("The ROCm core LLVM license evidence differs")
            selected.extend(llvm)
        combined = {
            (row["source_wheel_sha256"], row["path"], row["sha256"]): row
            for row in target["license_files"] + selected
        }
        target["license_files"] = sorted(
            combined.values(), key=lambda row: (row["source_wheel"], row["path"])
        )
        target["license_evidence_link"] = {
            "kind": "same_release_vendor_nested_license_corpus",
            "source_wheel": devel["wheel"],
            "source_wheel_sha256": devel["wheel_sha256"],
            "source_container": "rocm_sdk_devel/_devel.tar",
            "covered_projects": sorted(required),
        }


def component_manifest(
    component_id: str,
    source: Path,
    *,
    selected_files: list[Path] | None = None,
    part_index: int = 1,
    part_count: int = 1,
) -> dict[str, Any]:
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", component_id) is None:
        raise ReleaseBuildError("Invalid component id")
    root = source.resolve(strict=True)
    files = selected_files or _regular_files(
        root, file_limit=MAX_COMPONENT_FILES, byte_limit=MAX_COMPONENT_PAYLOAD_BYTES
    )
    if (
        not 1 <= part_index <= part_count <= 999
        or len(files) > MAX_COMPONENT_FILES
        or sum(path.stat().st_size for path in files) > MAX_COMPONENT_PAYLOAD_BYTES
        or any(not path.resolve().is_relative_to(root) for path in files)
    ):
        raise ReleaseBuildError("Invalid component part")
    rows: list[dict[str, Any]] = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in files
    ]
    if any(credential_path(row["path"]) for row in rows):
        raise ReleaseBuildError(
            "Credential-shaped files cannot enter a release component"
        )
    if any(
        PurePosixPath(row["path"]).parts[0]
        not in {"install-inputs", "toolchains", "runtime"}
        for row in rows
    ):
        raise ReleaseBuildError(
            "Release component files require an install-input, toolchain, or runtime owner"
        )
    return sealed(
        {
            "schema": "evidence-lane.first-detection-component.v4",
            "status": "COMPLETE_RELEASE_COMPONENT",
            "component_id": component_id,
            "part_index": part_index,
            "part_count": part_count,
            "file_count": len(rows),
            "total_bytes": sum(row["bytes"] for row in rows),
            "files_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
            "files": rows,
        }
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    return info


def build_component_archive(
    component_id: str,
    source: Path,
    output: Path,
    *,
    selected_files: list[Path] | None = None,
    part_index: int = 1,
    part_count: int = 1,
) -> dict[str, Any]:
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ReleaseBuildError("Component output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = component_manifest(
        component_id,
        source,
        selected_files=selected_files,
        part_index=part_index,
        part_count=part_count,
    )
    temporary = output.with_suffix(output.suffix + f".{os.getpid()}.{id(manifest)}.tmp")
    if temporary.exists():
        raise ReleaseBuildError("Component temporary output already exists")
    try:
        with zipfile.ZipFile(
            temporary,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            archive.writestr(
                _zip_info(COMPONENT_MANIFEST),
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                + b"\n",
            )
            for row in manifest["files"]:
                source_path = source / Path(*PurePosixPath(row["path"]).parts)
                with source_path.open("rb") as reader, archive.open(
                    _zip_info("payload/" + row["path"]), "w"
                ) as writer:
                    for block in iter(lambda: reader.read(1024 * 1024), b""):
                        writer.write(block)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    observed = read_component_archive(output)
    if observed != manifest:
        raise ReleaseBuildError("Component archive readback differs")
    return {
        "component_id": component_id,
        "part_index": part_index,
        "part_count": part_count,
        "path": str(output),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "component_manifest_sha256": hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        ).hexdigest(),
        "file_count": manifest["file_count"],
        "payload_bytes": manifest["total_bytes"],
    }


def build_component_parts(
    component_id: str,
    source: Path,
    output_directory: Path,
    *,
    maximum_payload_bytes: int = MAX_COMPONENT_PAYLOAD_BYTES,
) -> list[dict[str, Any]]:
    if not 1 <= maximum_payload_bytes <= MAX_COMPONENT_PAYLOAD_BYTES:
        raise ReleaseBuildError("Invalid component part budget")
    root = source.resolve(strict=True)
    files = _regular_files(
        root, file_limit=MAX_COMPONENT_FILES, byte_limit=MAX_RELEASE_BYTES
    )
    groups: list[list[Path]] = []
    current: list[Path] = []
    current_bytes = 0
    for path in files:
        size = path.stat().st_size
        if size > maximum_payload_bytes:
            raise ReleaseBuildError("One component file exceeds the multipart budget")
        if current and current_bytes + size > maximum_payload_bytes:
            groups.append(current)
            current = []
            current_bytes = 0
        current.append(path)
        current_bytes += size
    if current:
        groups.append(current)
    output_directory.mkdir(parents=True, exist_ok=True)
    results = []
    for index, group in enumerate(groups, 1):
        output = output_directory / (
            f"evidence-lane-{component_id}-part-{index:03d}-of-{len(groups):03d}.zip"
        )
        results.append(
            build_component_archive(
                component_id,
                root,
                output,
                selected_files=group,
                part_index=index,
                part_count=len(groups),
            )
        )
    return results


def read_component_archive(path: Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    if path.stat().st_size > MAX_COMPONENT_BYTES:
        raise ReleaseBuildError("Component archive exceeds the byte budget")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (
            len(infos) > MAX_COMPONENT_FILES + 1
            or len(names) != len(set(names))
            or names.count(COMPONENT_MANIFEST) != 1
        ):
            raise ReleaseBuildError("Component archive inventory is invalid")
        for info in infos:
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if info.is_dir() or (unix_mode and (unix_mode & 0o170000) != 0o100000):
                raise ReleaseBuildError("Only regular files are allowed in components")
            if info.filename != COMPONENT_MANIFEST and (
                not info.filename.startswith("payload/") or not safe_relative(
                    info.filename.removeprefix("payload/")
                )
            ):
                raise ReleaseBuildError("Unsafe component archive path")
        raw = archive.read(COMPONENT_MANIFEST)
        if len(raw) > 64 * 1024 * 1024:
            raise ReleaseBuildError("Component manifest exceeds its byte budget")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise ReleaseBuildError("Component manifest must be an object")
        body = dict(manifest)
        receipt = body.pop("receipt_sha256", None)
        if (
            manifest.get("schema") != "evidence-lane.first-detection-component.v4"
            or manifest.get("status") != "COMPLETE_RELEASE_COMPONENT"
            or not isinstance(manifest.get("part_index"), int)
            or not isinstance(manifest.get("part_count"), int)
            or not 1 <= manifest["part_index"] <= manifest["part_count"] <= 999
            or receipt != hashlib.sha256(canonical(body)).hexdigest()
        ):
            raise ReleaseBuildError("Component manifest seal is invalid")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) != manifest.get("file_count"):
            raise ReleaseBuildError("Component file count differs")
        if len({row.get("path") for row in files}) != len(files):
            raise ReleaseBuildError("Component file paths must be distinct")
        if hashlib.sha256(canonical(files)).hexdigest() != manifest.get("files_sha256"):
            raise ReleaseBuildError("Component file inventory digest differs")
        if sum(row.get("bytes", -1) for row in files) != manifest.get("total_bytes"):
            raise ReleaseBuildError("Component payload size differs")
        expected = {"payload/" + row["path"] for row in files} | {COMPONENT_MANIFEST}
        if set(names) != expected:
            raise ReleaseBuildError("Component archive members differ from its manifest")
        for row in files:
            if (
                not safe_relative(row.get("path"))
                or not isinstance(row.get("bytes"), int)
                or row["bytes"] < 0
                or re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256"))) is None
            ):
                raise ReleaseBuildError("Component file record is invalid")
            digest = hashlib.sha256()
            received = 0
            with archive.open("payload/" + row["path"]) as reader:
                while True:
                    block = reader.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > row["bytes"]:
                        raise ReleaseBuildError(
                            "Component member exceeded its exact size"
                        )
                    digest.update(block)
            if received != row["bytes"] or digest.hexdigest() != row["sha256"]:
                raise ReleaseBuildError("Component member differs from its manifest")
    return manifest


def build_source_manifest(plugin_root: Path = PLUGIN) -> dict[str, Any]:
    root = plugin_root.resolve(strict=True)
    rows = []
    total = 0
    ignored_names = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if ignored_names.intersection(relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if relative.as_posix() in EXCLUDED_SOURCE_PATHS:
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ReleaseBuildError(f"Linked plugin source is forbidden: {relative}")
        if path.is_dir():
            continue
        if not path.is_file() or not safe_relative(relative.as_posix()):
            raise ReleaseBuildError(f"Invalid plugin source member: {relative}")
        if credential_path(relative.as_posix()):
            raise ReleaseBuildError(
                f"Credential-shaped files cannot enter the release source: {relative}"
            )
        size = path.stat().st_size
        rows.append(
            {
                "path": relative.as_posix(),
                "bytes": size,
                "sha256": sha256(path),
            }
        )
        total += size
        if len(rows) > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
            raise ReleaseBuildError("Plugin source exceeds its package budget")
    if not rows:
        raise ReleaseBuildError("Plugin source manifest cannot be empty")
    return sealed(
        {
            "schema": "evidence-lane.first-detection-source-manifest.v4",
            "status": "COMPLETE_EXACT_SPARSE_ROOT",
            "repository": REPOSITORY,
            "sparse_root": SPARSE_ROOT,
            "excluded_self_referential_paths": sorted(EXCLUDED_SOURCE_PATHS),
            "file_count": len(rows),
            "total_bytes": total,
            "files_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
            "files": rows,
        }
    )


def _document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseBuildError(f"Expected a JSON object: {path}")
    return value


def _verify_seal(value: dict[str, Any]) -> None:
    body = dict(value)
    receipt = body.pop("receipt_sha256", None)
    if receipt != hashlib.sha256(canonical(body)).hexdigest():
        raise ReleaseBuildError("JSON receipt seal differs")


def bind_release(
    *,
    plugin_root: Path,
    release_ref: str,
    asset_paths: Mapping[str, Path] | Sequence[tuple[str, Path]],
    output: Path | None = None,
) -> dict[str, Any]:
    root = plugin_root.resolve(strict=True)
    plan_path = root / "provisioning/full-bundle-plan.v4.json"
    plan = _document(plan_path)
    _verify_seal(plan)
    if plan.get("status") != "COMPLETE_PREINSTALL_SOURCE_PLAN":
        raise ReleaseBuildError("A complete bundle plan is required")
    required = {
        row["component_id"]
        for row in plan["components"]
        if row["release_asset_required"]
    }
    pairs = (
        list(asset_paths.items())
        if isinstance(asset_paths, Mapping)
        else list(asset_paths)
    )
    if {component for component, _ in pairs} != required:
        raise ReleaseBuildError("Every planned release component must be supplied")
    component_by_id = {row["component_id"]: row for row in plan["components"]}
    assets = []
    for component_id, raw_path in sorted(
        pairs, key=lambda item: (item[0], item[1].name)
    ):
        path = raw_path.resolve(strict=True)
        manifest = read_component_archive(path)
        if manifest["component_id"] != component_id:
            raise ReleaseBuildError("Component id differs from its release slot")
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, indent=2
        ).encode("utf-8") + b"\n"
        assets.append(
            {
                "component_id": component_id,
                "asset_id": (
                    f"{component_id}.part-{manifest['part_index']:03d}-"
                    f"of-{manifest['part_count']:03d}"
                ),
                "part_index": manifest["part_index"],
                "part_count": manifest["part_count"],
                "condition": component_by_id[component_id]["condition"],
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "component_manifest_sha256": hashlib.sha256(
                    manifest_bytes
                ).hexdigest(),
                "file_count": manifest["file_count"],
                "payload_bytes": manifest["total_bytes"],
            }
        )
    for component_id in required:
        parts_for_component = [
            row for row in assets if row["component_id"] == component_id
        ]
        counts = {row["part_count"] for row in parts_for_component}
        if (
            len(counts) != 1
            or len(parts_for_component) != next(iter(counts))
            or {row["part_index"] for row in parts_for_component}
            != set(range(1, len(parts_for_component) + 1))
        ):
            raise ReleaseBuildError(
                "Component archive parts do not form one complete sequence"
            )
    assets_sha256 = hashlib.sha256(canonical(assets)).hexdigest()
    expected_ref = (
        f"refs/tags/evidence-lane-v{plan['plugin']['version']}-bundle-"
        f"{assets_sha256[:16]}"
    )
    if release_ref != expected_ref:
        raise ReleaseBuildError(f"Release ref must be {expected_ref}")
    tag = release_ref.removeprefix("refs/tags/")
    for asset in assets:
        asset["url"] = f"{REPOSITORY}/releases/download/{tag}/{asset['filename']}"
    source_manifest = build_source_manifest(root)
    source_path = root / SOURCE_MANIFEST
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = json.dumps(
        source_manifest, ensure_ascii=False, indent=2
    ).encode("utf-8") + b"\n"
    source_path.write_bytes(source_bytes)
    plugin_manifest = root / ".codex-plugin/plugin.json"
    binding = sealed(
        {
            "schema": "evidence-lane.first-detection-release-binding.v4",
            "status": "RELEASE_BOUND",
            "installation_enabled": True,
            "plugin_id": plan["plugin"]["id"],
            "plugin_version": plan["plugin"]["version"],
            "plugin_manifest_sha256": sha256(plugin_manifest),
            "repository": REPOSITORY,
            "sparse_root": SPARSE_ROOT,
            "release_ref": release_ref,
            "bundle_plan": "provisioning/full-bundle-plan.v4.json",
            "bundle_plan_sha256": sha256(plan_path),
            "source_manifest": SOURCE_MANIFEST,
            "source_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "assets_sha256": assets_sha256,
            "assets": assets,
            "installed_native_execution_claimed": False,
        }
    )
    destination = (output or root / RELEASE_BINDING).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return binding


def _asset_argument(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Use component-id=archive.zip")
    component, path = value.split("=", 1)
    return component, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    component = commands.add_parser("component")
    component.add_argument("--component-id", required=True)
    component.add_argument("--source", type=Path, required=True)
    component.add_argument("--output", type=Path, required=True)
    parts = commands.add_parser("parts")
    parts.add_argument("--component-id", required=True)
    parts.add_argument("--source", type=Path, required=True)
    parts.add_argument("--output-directory", type=Path, required=True)
    parts.add_argument(
        "--maximum-payload-bytes", type=int, default=MAX_COMPONENT_PAYLOAD_BYTES
    )
    offline = commands.add_parser("offline-lock")
    offline.add_argument("--packages-json", type=Path, required=True)
    offline.add_argument("--wheelhouse", type=Path, required=True)
    offline.add_argument("--output", type=Path, required=True)
    offline.add_argument("--license-output", type=Path, required=True)
    binding = commands.add_parser("bind")
    binding.add_argument("--plugin-root", type=Path, default=PLUGIN)
    binding.add_argument("--release-ref", required=True)
    binding.add_argument("--asset", type=_asset_argument, action="append", required=True)
    binding.add_argument("--output", type=Path)
    source = commands.add_parser("source-manifest")
    source.add_argument("--plugin-root", type=Path, default=PLUGIN)
    source.add_argument("--output", type=Path)
    args = parser.parse_args()
    result: dict[str, Any] | list[dict[str, Any]]
    if args.command == "component":
        result = build_component_archive(args.component_id, args.source, args.output)
    elif args.command == "parts":
        result = build_component_parts(
            args.component_id,
            args.source,
            args.output_directory,
            maximum_payload_bytes=args.maximum_payload_bytes,
        )
    elif args.command == "offline-lock":
        package_value = json.loads(args.packages_json.read_text(encoding="utf-8"))
        packages = (
            package_value["packages"]
            if isinstance(package_value, dict)
            else package_value
        )
        if not isinstance(packages, list):
            raise ReleaseBuildError("The package input must be a list")
        result = build_offline_lock(
            packages,
            args.wheelhouse,
            args.output,
            license_output=args.license_output,
        )
    elif args.command == "bind":
        result = bind_release(
            plugin_root=args.plugin_root,
            release_ref=args.release_ref,
            asset_paths=args.asset,
            output=args.output,
        )
    else:
        result = build_source_manifest(args.plugin_root)
        output = args.output or args.plugin_root / SOURCE_MANIFEST
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
