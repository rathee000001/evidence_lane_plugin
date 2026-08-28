#!/usr/bin/env python3
"""Install hash-pinned native tools into the hidden Codex plugin runtime.

This route is callable only from the local-update workflow. It never mutates
PATH and never writes into the user's project workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404 - pinned assets and fixed installer arguments
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_SCHEMA = "evidence-lane.native-toolchain-manifest.v1"
POINTER_SCHEMA = "evidence-lane.installed-native-toolchain.v1"


class InstallError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_runtime_root(path: Path) -> Path:
    root = path.resolve()
    folded = [part.casefold() for part in root.parts]
    required = [".codex", "plugins", "runtime", "evidence-lane-plugin"]
    cursor = 0
    for part in folded:
        if cursor < len(required) and part == required[cursor]:
            cursor += 1
    if cursor != len(required):
        raise InstallError("HIDDEN_CODEX_PLUGIN_RUNTIME_ROOT_REQUIRED")
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if (
        value.get("schema") != MANIFEST_SCHEMA
        or value.get("plane") != "CODEX"
        or value.get("workspace_install_allowed") is not False
        or value.get("path_mutation_allowed") is not False
        or value.get("acquisition_gate") != "LOCAL_UPDATE_ONLY"
    ):
        raise InstallError("NATIVE_TOOLCHAIN_MANIFEST_INVALID")
    return value


def download(url: str, target: Path, expected_sha256: str, expected_size: int) -> Path:
    if target.is_file() and target.stat().st_size == expected_size and sha256(target) == expected_sha256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    request = urllib.request.Request(url, headers={"User-Agent": "EvidenceLane/3.0"})
    urllib_succeeded = False
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        urllib_succeeded = (
            temporary.stat().st_size == expected_size
            and sha256(temporary) == expected_sha256
        )
    except Exception:  # noqa: BLE001 - bounded curl fallback below
        urllib_succeeded = False
    if not urllib_succeeded:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise InstallError("NATIVE_TOOL_DOWNLOAD_FAILED_AND_CURL_UNAVAILABLE")
        completed = subprocess.run(  # nosec B603
            [curl, "-L", "--fail", "--silent", "--show-error", "--output", str(temporary), url],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=180,
            shell=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
        if completed.returncode != 0:
            raise InstallError("NATIVE_TOOL_DOWNLOAD_FAILED")
    if temporary.stat().st_size != expected_size or sha256(temporary) != expected_sha256:
        raise InstallError("NATIVE_TOOL_DOWNLOAD_IDENTITY_MISMATCH")
    os.replace(temporary, target)
    return target


def safe_extract(archive: Path, destination: Path, prefix: str) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            name = member.filename.replace("\\", "/")
            if not name.startswith(prefix):
                raise InstallError("NATIVE_TOOL_ARCHIVE_PREFIX_MISMATCH")
            relative_text = name[len(prefix) :]
            if not relative_text:
                continue
            relative = PurePosixPath(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                raise InstallError("NATIVE_TOOL_ARCHIVE_PATH_UNSAFE")
            target = destination.joinpath(*relative.parts).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise InstallError("NATIVE_TOOL_ARCHIVE_PATH_ESCAPE") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)


def fresh_version_directory(root: Path, tool_id: str, version: str) -> Path:
    target = root / "toolchains" / tool_id / version
    if target.exists():
        quarantine = target.with_name(target.name + f".superseded.{os.getpid()}")
        if quarantine.exists():
            raise InstallError("NATIVE_TOOL_QUARANTINE_COLLISION")
        os.replace(target, quarantine)
    return target


def find_executable(root: Path, name: str) -> Path:
    matches = sorted(path.resolve() for path in root.rglob(name) if path.is_file())
    if len(matches) != 1:
        raise InstallError(f"NATIVE_TOOL_EXECUTABLE_CARDINALITY:{name}:{len(matches)}")
    return matches[0]


def install_asset(
    row: dict[str, Any],
    *,
    plugin_root: Path,
    runtime_root: Path,
    downloads: Path,
    license_grant_reference: str | None,
) -> list[dict[str, Any]]:
    tool_id = str(row["tool_id"])
    version = str(row["version"])
    if row.get("license_grant_reference_required") is True and not license_grant_reference:
        return [
            {
                "tool_id": tool_id,
                "status": "SKIPPED_LICENSE_GRANT_REQUIRED",
                "version": version,
                "default_acquisition_allowed": False,
            }
        ]
    if row["kind"] == "python_wheel_binary":
        import imageio_ffmpeg  # type: ignore[import-not-found]

        executable = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve(strict=True)
        return [
            {
                "tool_id": "ffmpeg",
                "status": "PASS",
                "version": version,
                "executable": str(executable),
                "executable_sha256": sha256(executable),
                "license_receipt_sha256": "PYTHON_WHEEL_METADATA",
                "path_mutated": False,
            }
        ]
    if row["kind"] == "package_local_existing":
        source = plugin_root / "toolchains" / "bin" / "windows-x86_64" / "rg.exe"
        search_manifest = json.loads(
            (plugin_root / "toolchains" / "search-tools.v1.json").read_text(encoding="utf-8")
        )
        binary = search_manifest["tools"][0]["package_binaries"]["windows-x86_64"]
        if source.stat().st_size != int(binary["size_bytes"]) or sha256(source) != binary["sha256"]:
            raise InstallError("RIPGREP_PACKAGE_IDENTITY_MISMATCH")
        target = fresh_version_directory(runtime_root, tool_id, version)
        destination = target / "bin" / "rg.exe"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        licenses = []
        for license_path in binary["licenses"]:
            source_license = plugin_root / license_path
            target_license = runtime_root / "toolchains" / "licenses" / tool_id / version / source_license.name
            target_license.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_license, target_license)
            licenses.append({"path": str(target_license), "sha256": sha256(target_license)})
        license_receipt = hashlib.sha256(canonical(licenses)).hexdigest().upper()
        return [{
            "tool_id": "ripgrep",
            "status": "PASS",
            "version": version,
            "executable": str(destination.resolve()),
            "executable_sha256": sha256(destination),
            "license_receipt_sha256": license_receipt,
            "path_mutated": False,
        }]
    if row["kind"] == "package_local_support_bundle":
        target = fresh_version_directory(runtime_root, tool_id, version)
        records: list[dict[str, Any]] = []
        for file_row in row["package_files"]:
            source = plugin_root / str(file_row["path"])
            if (
                not source.is_file()
                or source.stat().st_size != int(file_row["size_bytes"])
                or sha256(source) != str(file_row["sha256"])
            ):
                raise InstallError(f"PACKAGE_LOCAL_SUPPORT_IDENTITY_MISMATCH:{tool_id}")
            destination = target / "bin" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        license_rows = []
        for license_row in row["license_files"]:
            source = plugin_root / str(license_row["path"])
            if (
                not source.is_file()
                or source.stat().st_size != int(license_row["size_bytes"])
                or sha256(source) != str(license_row["sha256"])
            ):
                raise InstallError(f"PACKAGE_LOCAL_LICENSE_IDENTITY_MISMATCH:{tool_id}")
            destination = (
                runtime_root
                / "toolchains"
                / "licenses"
                / tool_id
                / version
                / source.name
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            license_rows.append(
                {"path": str(destination), "sha256": sha256(destination)}
            )
        license_receipt = hashlib.sha256(canonical(license_rows)).hexdigest().upper()
        for alias, executable_name in dict(row["command_aliases"]).items():
            executable = find_executable(target, str(executable_name))
            completed = subprocess.run(  # nosec B603
                [str(executable), *[str(value) for value in row["version_arguments"]]],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                shell=False,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
            )
            if completed.returncode != 0:
                raise InstallError(f"PACKAGE_LOCAL_SUPPORT_PROBE_FAILED:{alias}")
            records.append(
                {
                    "tool_id": str(alias),
                    "status": "PASS",
                    "version": version,
                    "executable": str(executable),
                    "executable_sha256": sha256(executable),
                    "version_output_sha256": hashlib.sha256(
                        completed.stdout + completed.stderr
                    ).hexdigest().upper(),
                    "license_receipt_sha256": license_receipt,
                    "path_mutated": False,
                }
            )
        return records

    archive_name = Path(str(row["download_url"]).split("?", 1)[0]).name or f"{tool_id}-{version}.asset"
    asset = download(
        str(row["download_url"]),
        downloads / archive_name,
        str(row["download_sha256"]),
        int(row["download_size_bytes"]),
    )
    license_row = dict(row["license"])
    license_file = download(
        str(license_row["url"]),
        runtime_root / "toolchains" / "licenses" / tool_id / version / "LICENSE",
        str(license_row["sha256"]),
        int(license_row["size_bytes"]),
    )
    license_core = {
        "tool_id": tool_id,
        "version": version,
        "license_spdx": row["license_spdx"],
        "license_sha256": sha256(license_file),
        "license_grant_reference_sha256": (
            hashlib.sha256(license_grant_reference.encode("utf-8")).hexdigest().upper()
            if license_grant_reference and row.get("license_grant_reference_required")
            else None
        ),
        "source_offer_required": bool(row.get("source_offer_required")),
    }
    license_receipt = hashlib.sha256(canonical(license_core)).hexdigest().upper()
    target = fresh_version_directory(runtime_root, tool_id, version)
    kind = str(row["kind"])
    if kind == "portable_executable":
        destination = target / "bin" / Path(str(row["installed_executables"][0])).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, destination)
    elif kind == "portable_zip":
        safe_extract(asset, target, str(row["archive_prefix"]))
    elif kind == "silent_installer":
        target.mkdir(parents=True, exist_ok=False)
        install_directory_argument = str(
            row.get("install_directory_argument") or "/D={target}"
        ).format(target=str(target))
        arguments = [
            str(asset),
            *[str(value) for value in row["silent_arguments"]],
            install_directory_argument,
        ]
        completed = subprocess.run(  # nosec B603
            arguments,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=300,
            shell=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
        if completed.returncode != 0:
            raise InstallError(f"NATIVE_TOOL_SILENT_INSTALL_FAILED:{tool_id}")
    elif kind == "portable_nsis_extract":
        extractor = plugin_root / str(row["extractor_path"])
        if (
            not extractor.is_file()
            or sha256(extractor) != str(row["extractor_sha256"])
        ):
            raise InstallError("NSIS_EXTRACTOR_IDENTITY_MISMATCH")
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(  # nosec B603
            [str(extractor), "x", str(asset), f"-o{target}", "-y"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=300,
            shell=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
        if completed.returncode != 0:
            raise InstallError("NATIVE_TOOL_NSIS_EXTRACTION_FAILED")
    else:
        raise InstallError(f"NATIVE_TOOL_KIND_UNSUPPORTED:{kind}")

    records = []
    for alias, executable_name in dict(row["command_aliases"]).items():
        executable = find_executable(target, str(executable_name))
        completed = subprocess.run(  # nosec B603
            [str(executable), *[str(value) for value in row["version_arguments"]]],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            shell=False,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0),
        )
        if completed.returncode != 0:
            raise InstallError(f"NATIVE_TOOL_VERSION_PROBE_FAILED:{alias}")
        records.append({
            "tool_id": str(alias),
            "status": "PASS",
            "version": version,
            "executable": str(executable),
            "executable_sha256": sha256(executable),
            "version_output_sha256": hashlib.sha256(completed.stdout + completed.stderr).hexdigest().upper(),
            "license_receipt_sha256": license_receipt,
            "path_mutated": False,
        })
    return records


def install_tree_sitter_languages(
    *,
    plugin_root: Path,
    runtime_root: Path,
    allow_network: bool,
) -> dict[str, Any]:
    if not allow_network:
        raise InstallError("TREE_SITTER_LANGUAGE_PREFETCH_NETWORK_GRANT_REQUIRED")
    source_root = plugin_root / "src"
    sys.path.insert(0, str(source_root))
    try:
        from evidence_lane_plugin.code_toolchain import CODE_TOOLCHAIN_LANGUAGES
        from tree_sitter_language_pack import (  # type: ignore[import-not-found]
            PackConfig,
            available_languages,
            init,
            prefetch,
        )

        cache = (
            runtime_root
            / "toolchains"
            / "tree-sitter-language-pack"
            / "1.14.3"
            / "libs"
        )
        cache.mkdir(parents=True, exist_ok=True)
        init(PackConfig(cache_dir=str(cache)))
        grammar_download_ids = [
            "csharp" if language == "c_sharp" else language
            for language in CODE_TOOLCHAIN_LANGUAGES
        ]
        prefetch(grammar_download_ids)
        available = {str(value) for value in available_languages()}
        missing = sorted(set(CODE_TOOLCHAIN_LANGUAGES) - available)
        if missing:
            raise InstallError(
                "TREE_SITTER_LANGUAGE_PREFETCH_INCOMPLETE:" + ",".join(missing)
            )
        files = [
            {
                "path_sha256": hashlib.sha256(
                    str(path.relative_to(runtime_root)).encode("utf-8")
                ).hexdigest().upper(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in sorted(cache.rglob("*"))
            if path.is_file()
        ]
        core = {
            "tool_id": "tree_sitter_languages",
            "status": "PASS",
            "version": "tree-sitter-language-pack==1.14.3",
            "language_count": len(CODE_TOOLCHAIN_LANGUAGES),
            "languages": list(CODE_TOOLCHAIN_LANGUAGES),
            "grammar_download_ids": grammar_download_ids,
            "download_and_runtime_aliases_distinct": True,
            "file_count": len(files),
            "files_sha256": hashlib.sha256(canonical(files)).hexdigest().upper(),
            "cache_path_disclosed": False,
            "runtime_auto_download_allowed": False,
            "local_update_prefetch": True,
            "license_receipt_sha256": "PYTHON_DISTRIBUTION_AND_GRAMMAR_METADATA",
        }
        return {
            **core,
            "receipt_sha256": hashlib.sha256(canonical(core)).hexdigest().upper(),
        }
    finally:
        if sys.path and sys.path[0] == str(source_root):
            sys.path.pop(0)


def install_embedding_model(
    *,
    runtime_root: Path,
    allow_network: bool,
) -> dict[str, Any]:
    from huggingface_hub import snapshot_download  # type: ignore[import-not-found]

    repository = "BAAI/bge-small-en-v1.5"
    revision = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
    target = (
        runtime_root
        / "toolchains"
        / "models"
        / "BAAI-bge-small-en-v1.5"
        / revision
    )
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repository,
        revision=revision,
        local_dir=target,
        local_files_only=not allow_network,
        allow_patterns=[
            "1_Pooling/config.json",
            "README.md",
            "config_sentence_transformers.json",
            "config.json",
            "model.safetensors",
            "modules.json",
            "sentence_bert_config.json",
            "special_tokens_map.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
        ],
    )
    expected = {
        "1_Pooling/config.json",
        "README.md",
        "config_sentence_transformers.json",
        "config.json",
        "model.safetensors",
        "modules.json",
        "sentence_bert_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    }
    files = [
        {
            "path": path.relative_to(target).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(target.rglob("*"))
        if path.is_file() and ".cache" not in path.parts
    ]
    observed = {str(row["path"]) for row in files}
    if not expected.issubset(observed):
        raise InstallError(
            "EMBEDDING_MODEL_SNAPSHOT_INCOMPLETE:"
            + ",".join(sorted(expected - observed))
        )
    from sentence_transformers import (
        SentenceTransformer,  # type: ignore[import-not-found]
    )

    model = SentenceTransformer(str(target), local_files_only=True)
    vectors = model.encode(
        ["Evidence Lane exact source", "Governed SQLite retrieval"],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    if tuple(vectors.shape) != (2, 384):
        raise InstallError("EMBEDDING_MODEL_RUNTIME_PROBE_DIMENSION_MISMATCH")
    core = {
        "tool_id": "embedding_model_bge_small_en_v1_5",
        "status": "PASS",
        "repository": repository,
        "revision": revision,
        "dimension": 384,
        "license_spdx": "MIT",
        "model_path": str(target.resolve()),
        "model_path_disclosed_publicly": False,
        "file_count": len(files),
        "files_sha256": hashlib.sha256(canonical(files) + b"\n")
        .hexdigest()
        .upper(),
        "runtime_probe": "SentenceTransformer local_files_only=True",
        "runtime_network_allowed": False,
        "local_update_download_allowed": bool(allow_network),
        "license_receipt_sha256": hashlib.sha256(
            canonical(
                {
                    "repository": repository,
                    "revision": revision,
                    "license_spdx": "MIT",
                    "readme_sha256": next(
                        row["sha256"] for row in files if row["path"] == "README.md"
                    ),
                }
            )
        ).hexdigest().upper(),
    }
    return {
        **core,
        "receipt_sha256": hashlib.sha256(canonical(core)).hexdigest().upper(),
    }


def install(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = validate_runtime_root(args.runtime_root)
    plugin_root = args.plugin_root.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    manifest = validate_manifest(manifest_path)
    downloads = runtime_root / "toolchains" / "downloads"
    records: list[dict[str, Any]] = []
    for row in manifest["tools"]:
        if row.get("default_acquisition_allowed") is not True and not args.ghostscript_license_grant_reference:
            records.extend(install_asset(
                dict(row), plugin_root=plugin_root, runtime_root=runtime_root,
                downloads=downloads, license_grant_reference=None,
            ))
            continue
        if not args.allow_network and row["kind"] not in {"package_local_existing", "python_wheel_binary"}:
            raise InstallError("NATIVE_TOOLCHAIN_NETWORK_GRANT_REQUIRED")
        records.extend(install_asset(
            dict(row), plugin_root=plugin_root, runtime_root=runtime_root,
            downloads=downloads,
            license_grant_reference=args.ghostscript_license_grant_reference,
        ))
    records.append(
        install_tree_sitter_languages(
            plugin_root=plugin_root,
            runtime_root=runtime_root,
            allow_network=bool(args.allow_network),
        )
    )
    records.append(
        install_embedding_model(
            runtime_root=runtime_root,
            allow_network=bool(args.allow_network),
        )
    )
    failures = [row for row in records if row["status"] not in {"PASS", "SKIPPED_LICENSE_GRANT_REQUIRED"}]
    core = {
        "schema": POINTER_SCHEMA,
        "status": "PASS" if not failures else "FAIL",
        "plane": "CODEX",
        "host_profiles": manifest["host_profiles"],
        "manifest_sha256": sha256(manifest_path),
        "tools": records,
        "hidden_runtime_only": True,
        "workspace_install_used": False,
        "path_mutated": False,
        "local_update_only": True,
        "ghostscript_default_bundled": False,
    }
    receipt = {
        **core,
        "receipt_sha256": hashlib.sha256(canonical(core) + b"\n")
        .hexdigest()
        .upper(),
    }
    pointer = runtime_root / "toolchains" / "CURRENT_NATIVE_TOOLCHAIN.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(pointer.suffix + f".{os.getpid()}.tmp")
    temporary.write_bytes(canonical(receipt) + b"\n")
    os.replace(temporary, pointer)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--ghostscript-license-grant-reference")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = install(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + f".{os.getpid()}.tmp")
        temporary.write_bytes(canonical(receipt) + b"\n")
        os.replace(temporary, args.output)
        print(json.dumps({"status": receipt["status"], "receipt_sha256": receipt["receipt_sha256"]}))
        return 0
    except (InstallError, OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
