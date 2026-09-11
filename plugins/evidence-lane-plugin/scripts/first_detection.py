"""Standard-library first-detection installer for the release-bound Windows bundle."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Self
from uuid import uuid4

BINDING_SCHEMA = "evidence-lane.first-detection-release-binding.v4"
PLAN_SCHEMA = "evidence-lane.first-detection-bundle-plan.v4"
SOURCE_SCHEMA = "evidence-lane.first-detection-source-manifest.v4"
COMPONENT_SCHEMA = "evidence-lane.first-detection-component.v4"
INSTALLATION_SCHEMA = "evidence-lane.first-detection-installation.v4"
RELEASE_RECEIPT_SCHEMA = "evidence-lane.first-detection-release-receipt.v4"
COMPONENT_MANIFEST = "EVIDENCE_LANE_COMPONENT.json"
REPOSITORY = "https://github.com/rathee000001/evidence_lane_plugin"
SPARSE_ROOT = "plugins/evidence-lane-plugin"
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_COMPONENT_FILES = 100_000
MAX_COMPONENT_BYTES = 2_140_000_000
MAX_SELECTED_BYTES = 32_000_000_000
MAX_INSTALLED_FILES = 300_000
_SOURCE_EXCLUDED = {
    "provisioning/source-manifest.v4.json",
    "provisioning/release-binding.v4.json",
}
_FORBIDDEN_CREDENTIAL_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "secrets.json",
    "pip.conf",
    "id_rsa",
    "id_ed25519",
}


class FirstDetectionError(RuntimeError):
    """A fail-closed public installation reason."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


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


def read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError("JSON byte budget exceeded")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise TypeError("JSON root must be an object")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise FirstDetectionError(
            "INSTALLATION_MANIFEST_INVALID", f"Cannot verify {path.name}: {exc}"
        ) from exc


def verify_seal(value: Mapping[str, Any], *, code: str) -> None:
    body = dict(value)
    receipt = body.pop("receipt_sha256", None)
    if receipt != hashlib.sha256(canonical(body)).hexdigest():
        raise FirstDetectionError(code, "A provisioning receipt seal differs.")


def safe_relative(value: object) -> bool:
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
        part == part.rstrip(" .")
        and ":" not in part
        and part.casefold().split(".", 1)[0] not in reserved
        for part in path.parts
    )


def credential_path(value: str) -> bool:
    name = PurePosixPath(value).name.casefold()
    return (
        name in _FORBIDDEN_CREDENTIAL_NAMES
        or name.startswith(".env.")
        or PurePosixPath(name).suffix in {".pfx", ".p12", ".key"}
    )


def relative_path(root: Path, value: object) -> Path:
    if not safe_relative(value):
        raise FirstDetectionError(
            "INSTALLATION_PATH_INVALID", "A release path escaped its installation."
        )
    target = root.joinpath(*PurePosixPath(str(value)).parts).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise FirstDetectionError(
            "INSTALLATION_PATH_INVALID", "A release path escaped its installation."
        ) from exc
    return target


def reject_links(path: Path, boundary: Path) -> None:
    boundary = boundary.resolve()
    current = path.absolute()
    while True:
        if current.exists() and (
            current.is_symlink()
            or (hasattr(current, "is_junction") and current.is_junction())
        ):
            raise FirstDetectionError(
                "INSTALLATION_LINK_REJECTED",
                "The installation path contains a link or junction.",
            )
        if current == boundary or current.parent == current:
            break
        current = current.parent


def installation_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    raw = values.get("EVIDENCE_LANE_STUDIO_ROOT", "C:/Apps/EvidenceLaneStudio")
    if not isinstance(raw, str) or not raw.strip() or any(char in raw for char in "\r\n\x00"):
        raise FirstDetectionError(
            "STUDIO_INSTALL_ROOT_INVALID", "Select an absolute Studio installation root."
        )
    root = Path(raw).expanduser()
    if not root.is_absolute() or ".." in root.parts:
        raise FirstDetectionError(
            "STUDIO_INSTALL_ROOT_INVALID", "Select an absolute Studio installation root."
        )
    root = Path(os.path.abspath(root))
    reject_links(root, Path(root.anchor))
    return root


class InstallationLock:
    """One OS lock for installation; an existing file never implies stale ownership."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reject_links(self.path.parent, Path(self.path.anchor))
        reject_links(self.path, Path(self.path.anchor))
        self.handle = self.path.open("a+b")
        reject_links(self.path, Path(self.path.anchor))
        self.handle.seek(0)
        if self.handle.read(1) == b"":
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl_module: Any = fcntl
                fcntl_module.flock(
                    self.handle.fileno(),
                    fcntl_module.LOCK_EX | fcntl_module.LOCK_NB,
                )
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise FirstDetectionError(
                "INSTALLATION_IN_PROGRESS",
                "Another first-detection installation owns the shared root.",
            ) from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle is None:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl_module: Any = fcntl
            fcntl_module.flock(self.handle.fileno(), fcntl_module.LOCK_UN)
        self.handle.close()
        self.handle = None


def _ignored_source(relative: Path) -> bool:
    ignored = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
    return bool(ignored.intersection(relative.parts)) or relative.suffix in {".pyc", ".pyo"}


def verify_source_manifest(plugin_root: Path, manifest: Mapping[str, Any]) -> None:
    verify_seal(manifest, code="SOURCE_MANIFEST_INVALID")
    rows = manifest.get("files")
    if (
        manifest.get("schema") != SOURCE_SCHEMA
        or manifest.get("status") != "COMPLETE_EXACT_SPARSE_ROOT"
        or manifest.get("repository") != REPOSITORY
        or manifest.get("sparse_root") != SPARSE_ROOT
        or manifest.get("excluded_self_referential_paths") != sorted(_SOURCE_EXCLUDED)
        or not isinstance(rows, list)
        or len(rows) != manifest.get("file_count")
        or len(rows) > 32_768
        or len({row.get("path") for row in rows}) != len(rows)
        or hashlib.sha256(canonical(rows)).hexdigest() != manifest.get("files_sha256")
    ):
        raise FirstDetectionError(
            "SOURCE_MANIFEST_INVALID", "The exact sparse-root source manifest differs."
        )
    expected = set()
    total = 0
    for row in rows:
        if credential_path(str(row.get("path", ""))):
            raise FirstDetectionError(
                "SOURCE_CREDENTIAL_FILE_REJECTED",
                "Credential-shaped files cannot enter the installed plugin source.",
            )
        path = relative_path(plugin_root, row.get("path"))
        expected.add(str(row["path"]))
        if (
            not isinstance(row.get("bytes"), int)
            or row["bytes"] < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256"))) is None
            or not path.is_file()
            or path.stat().st_size != row["bytes"]
            or sha256(path) != row["sha256"]
        ):
            raise FirstDetectionError(
                "SOURCE_PACKAGE_CHANGED", "A release-bound plugin source member differs."
            )
        reject_links(path, plugin_root)
        total += row["bytes"]
    if total != manifest.get("total_bytes"):
        raise FirstDetectionError(
            "SOURCE_MANIFEST_INVALID", "The source byte total differs."
        )
    observed = set()
    for path in plugin_root.rglob("*"):
        relative = path.relative_to(plugin_root)
        if _ignored_source(relative) or relative.as_posix() in _SOURCE_EXCLUDED:
            continue
        reject_links(path, plugin_root)
        if path.is_file():
            observed.add(relative.as_posix())
        elif not path.is_dir():
            raise FirstDetectionError(
                "SOURCE_PACKAGE_CHANGED", "An irregular plugin source member was found."
            )
    if observed != expected:
        raise FirstDetectionError(
            "SOURCE_PACKAGE_CHANGED", "The release-bound plugin source set differs."
        )


def validate_binding(plugin_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = plugin_root.resolve(strict=True)
    binding_path = root / "provisioning/release-binding.v4.json"
    binding = read_json(binding_path)
    verify_seal(binding, code="RELEASE_BINDING_INVALID")
    plan_path = root / str(binding.get("bundle_plan", ""))
    plan = read_json(plan_path)
    verify_seal(plan, code="BUNDLE_PLAN_INVALID")
    if (
        binding.get("schema") != BINDING_SCHEMA
        or plan.get("schema") != PLAN_SCHEMA
        or plan.get("status") != "COMPLETE_PREINSTALL_SOURCE_PLAN"
        or binding.get("plugin_id") != "evidence-lane-plugin"
        or binding.get("plugin_version") != plan.get("plugin", {}).get("version")
        or binding.get("repository") != REPOSITORY
        or binding.get("sparse_root") != SPARSE_ROOT
        or sha256(plan_path) != binding.get("bundle_plan_sha256")
    ):
        raise FirstDetectionError(
            "RELEASE_BINDING_INVALID", "The release binding and bundle plan differ."
        )
    if binding.get("status") == "SOURCE_TEMPLATE_UNBOUND":
        if (
            binding.get("installation_enabled") is not False
            or binding.get("release_ref") is not None
            or binding.get("assets") != []
        ):
            raise FirstDetectionError(
                "RELEASE_BINDING_INVALID", "The preinstall source template is malformed."
            )
        return binding, plan
    if binding.get("status") != "RELEASE_BOUND" or binding.get("installation_enabled") is not True:
        raise FirstDetectionError(
            "RELEASE_BINDING_INVALID", "A bound installation release is required."
        )
    plugin_manifest = root / ".codex-plugin/plugin.json"
    plugin = read_json(plugin_manifest)
    source_path = root / str(binding.get("source_manifest", ""))
    if (
        plugin.get("name") != binding["plugin_id"]
        or plugin.get("version") != binding["plugin_version"]
        or sha256(plugin_manifest) != binding.get("plugin_manifest_sha256")
        or not source_path.is_file()
        or sha256(source_path) != binding.get("source_manifest_sha256")
    ):
        raise FirstDetectionError(
            "RELEASE_BINDING_INVALID", "The binding does not match the installed sparse root."
        )
    source = read_json(source_path)
    verify_source_manifest(root, source)
    release_ref = binding.get("release_ref")
    version = re.escape(str(binding["plugin_version"]))
    if not isinstance(release_ref, str) or re.fullmatch(
        rf"refs/tags/evidence-lane-v{version}-bundle-[0-9a-f]{{16}}", release_ref
    ) is None:
        raise FirstDetectionError(
            "RELEASE_REF_INVALID", "An exact immutable release tag is required."
        )
    tag = release_ref.removeprefix("refs/tags/")
    component_rows = {
        row["component_id"]: row
        for row in plan.get("components", [])
        if row.get("release_asset_required") is True
    }
    assets = binding.get("assets")
    if (
        not isinstance(assets, list)
        or not 1 <= len(assets) <= 64
        or len({row.get("asset_id") for row in assets}) != len(assets)
        or {row.get("component_id") for row in assets} != set(component_rows)
    ):
        raise FirstDetectionError(
            "RELEASE_ASSET_SET_INVALID", "Every release component is required exactly once."
        )
    unsealed_assets = []
    for asset in assets:
        component = component_rows[asset["component_id"]]
        prefix = f"{REPOSITORY}/releases/download/{tag}/"
        if (
            asset.get("condition") != component.get("condition")
            or not isinstance(asset.get("part_index"), int)
            or not isinstance(asset.get("part_count"), int)
            or not 1 <= asset["part_index"] <= asset["part_count"] <= 999
            or asset.get("asset_id")
            != (
                f"{asset['component_id']}.part-{asset.get('part_index', 0):03d}-"
                f"of-{asset.get('part_count', 0):03d}"
            )
            or not isinstance(asset.get("filename"), str)
            or Path(asset["filename"]).name != asset["filename"]
            or asset.get("url") != prefix + asset["filename"]
            or not isinstance(asset.get("bytes"), int)
            or not 0 < asset["bytes"] <= MAX_COMPONENT_BYTES
            or re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256"))) is None
            or re.fullmatch(
                r"[0-9a-f]{64}", str(asset.get("component_manifest_sha256"))
            )
            is None
            or not isinstance(asset.get("file_count"), int)
            or not 0 < asset["file_count"] <= MAX_COMPONENT_FILES
        ):
            raise FirstDetectionError(
                "RELEASE_ASSET_INVALID", "A release component binding is invalid."
            )
        unsealed_assets.append({key: value for key, value in asset.items() if key != "url"})
    for component_id in component_rows:
        parts = [row for row in assets if row["component_id"] == component_id]
        counts = {row["part_count"] for row in parts}
        if (
            len(counts) != 1
            or len(parts) != next(iter(counts))
            or {row["part_index"] for row in parts}
            != set(range(1, len(parts) + 1))
        ):
            raise FirstDetectionError(
                "RELEASE_ASSET_SET_INVALID",
                "A release component has an incomplete part sequence.",
            )
    if (
        hashlib.sha256(canonical(unsealed_assets)).hexdigest()
        != binding.get("assets_sha256")
    ):
        raise FirstDetectionError(
            "RELEASE_ASSET_SET_INVALID", "The release asset inventory digest differs."
        )
    return binding, plan


def default_gpu_probe() -> dict[str, Any]:
    if platform.system() != "Windows":
        return {"names": [], "nvidia_cuda_compatible": False, "directml_compatible": False,
                "amd_rocm_compatible": False, "basis": "non_windows"}
    powershell = Path(
        os.environ.get(
            "SystemRoot", r"C:\Windows"
        )
    ) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    names: list[str] = []
    try:
        result = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and len(result.stdout) <= 32_768:
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        names = []
    folded = " ".join(names).casefold()
    return {
        "names": names,
        "nvidia_cuda_compatible": "nvidia" in folded,
        "directml_compatible": any(
            vendor in folded for vendor in ("nvidia", "amd", "radeon")
        ),
        "amd_rocm_compatible": False,
        "basis": "bounded_win32_video_controller_names",
    }


def selected_assets(
    binding: Mapping[str, Any],
    *,
    gpu: Mapping[str, Any],
    license_grants: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    grants = set(license_grants)
    for row in binding["assets"]:
        condition = row["condition"]
        include = (
            condition == "always"
            or condition == "nvidia_cuda_compatible"
            and gpu.get("nvidia_cuda_compatible") is True
            or condition == "directml_compatible"
            and gpu.get("directml_compatible") is True
            or condition == "amd_rocm_compatible"
            and gpu.get("amd_rocm_compatible") is True
            or condition == "explicit_ghostscript_license"
            and "ghostscript" in grants
        )
        (selected if include else skipped).append(dict(row))
    if "provider-cpu" not in {row["component_id"] for row in selected}:
        raise FirstDetectionError(
            "CPU_PROVIDER_MISSING", "The required CPU provider component is not selected."
        )
    if sum(row["bytes"] for row in selected) > MAX_SELECTED_BYTES:
        raise FirstDetectionError(
            "RELEASE_ASSET_BUDGET", "Selected release assets exceed the installation budget."
        )
    return selected, skipped


def validate_ghostscript_license_grant(
    path: Path, *, release_binding_sha256: str
) -> dict[str, Any]:
    grant = read_json(path.resolve(strict=True))
    verify_seal(grant, code="LICENSE_GRANT_INVALID")
    if (
        grant.get("schema") != "evidence-lane.explicit-license-grant.v4"
        or grant.get("status") != "ACCEPTED"
        or grant.get("tool_id") != "ghostscript"
        or grant.get("license_expression")
        != "AGPL-3.0-or-later OR LicenseRef-Artifex-Commercial"
        or grant.get("release_binding_sha256") != release_binding_sha256
        or not isinstance(grant.get("accepted_by"), str)
        or not grant["accepted_by"].strip()
        or not isinstance(grant.get("accepted_at"), str)
        or not grant["accepted_at"].strip()
    ):
        raise FirstDetectionError(
            "LICENSE_GRANT_INVALID",
            "The Ghostscript component requires an explicit release-bound license receipt.",
        )
    return grant


def default_fetch(asset: Mapping[str, Any], target: Path) -> None:
    request = urllib.request.Request(
        str(asset["url"]), headers={"User-Agent": "EvidenceLane/4.0 first-detection"}
    )
    temporary = target.with_suffix(target.suffix + f".{uuid4()}.tmp")
    if temporary.exists():
        raise FirstDetectionError(
            "RELEASE_TEMPORARY_COLLISION", "A release download path is occupied."
        )
    received = 0
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("xb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > asset["bytes"]:
                    raise FirstDetectionError(
                        "RELEASE_ASSET_SIZE_MISMATCH",
                        "A release asset exceeded its exact size.",
                    )
                digest.update(block)
                output.write(block)
        if received != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
            raise FirstDetectionError(
                "RELEASE_ASSET_IDENTITY_MISMATCH",
                "A same-repository release asset differs from its binding.",
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def acquire_asset(
    asset: Mapping[str, Any],
    cache: Path,
    fetcher: Callable[[Mapping[str, Any], Path], None],
) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{asset['sha256']}.zip"
    reject_links(cache, Path(cache.anchor))
    if target.is_file() and target.stat().st_size == asset["bytes"] and sha256(target) == asset[
        "sha256"
    ]:
        return target
    if target.exists():
        raise FirstDetectionError(
            "RELEASE_CACHE_COLLISION", "A release cache entry has different bytes."
        )
    fetcher(asset, target)
    if (
        not target.is_file()
        or target.stat().st_size != asset["bytes"]
        or sha256(target) != asset["sha256"]
    ):
        raise FirstDetectionError(
            "RELEASE_ASSET_IDENTITY_MISMATCH",
            "The fetched release asset differs from its exact binding.",
        )
    return target


def _component_manifest(archive: zipfile.ZipFile, asset: Mapping[str, Any]) -> dict[str, Any]:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if (
        len(infos) > MAX_COMPONENT_FILES + 1
        or len(names) != len(set(names))
        or names.count(COMPONENT_MANIFEST) != 1
    ):
        raise FirstDetectionError(
            "COMPONENT_ARCHIVE_INVALID", "A component archive inventory is invalid."
        )
    for info in infos:
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if info.is_dir() or (unix_mode and (unix_mode & 0o170000) != 0o100000):
            raise FirstDetectionError(
                "COMPONENT_ARCHIVE_INVALID", "Release components may contain regular files only."
            )
        if info.filename != COMPONENT_MANIFEST and (
            not info.filename.startswith("payload/")
            or not safe_relative(info.filename.removeprefix("payload/"))
        ):
            raise FirstDetectionError(
                "COMPONENT_ARCHIVE_INVALID", "A component archive path is unsafe."
            )
    raw = archive.read(COMPONENT_MANIFEST)
    if hashlib.sha256(raw).hexdigest() != asset["component_manifest_sha256"]:
        raise FirstDetectionError(
            "COMPONENT_MANIFEST_CHANGED", "A component manifest differs from its binding."
        )
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise FirstDetectionError(
            "COMPONENT_MANIFEST_INVALID", "A component manifest is not valid JSON."
        ) from exc
    verify_seal(manifest, code="COMPONENT_MANIFEST_INVALID")
    files = manifest.get("files")
    if (
        manifest.get("schema") != COMPONENT_SCHEMA
        or manifest.get("status") != "COMPLETE_RELEASE_COMPONENT"
        or manifest.get("component_id") != asset["component_id"]
        or manifest.get("part_index") != asset["part_index"]
        or manifest.get("part_count") != asset["part_count"]
        or not isinstance(files, list)
        or len(files) != asset["file_count"]
        or len(files) != manifest.get("file_count")
        or len({row.get("path") for row in files}) != len(files)
        or hashlib.sha256(canonical(files)).hexdigest() != manifest.get("files_sha256")
        or sum(row.get("bytes", -1) for row in files) != manifest.get("total_bytes")
        or manifest.get("total_bytes") != asset["payload_bytes"]
    ):
        raise FirstDetectionError(
            "COMPONENT_MANIFEST_INVALID", "A component manifest does not reconcile."
        )
    expected = {COMPONENT_MANIFEST} | {"payload/" + row["path"] for row in files}
    if set(names) != expected:
        raise FirstDetectionError(
            "COMPONENT_ARCHIVE_INVALID", "Component members differ from their manifest."
        )
    return manifest


def extract_component(
    archive_path: Path,
    asset: Mapping[str, Any],
    stage: Path,
    occupied: set[str],
) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path) as archive:
        manifest = _component_manifest(archive, asset)
        for row in manifest["files"]:
            relative = row.get("path")
            if (
                not safe_relative(relative)
                or credential_path(str(relative))
                or PurePosixPath(str(relative)).parts[0]
                not in {"install-inputs", "toolchains", "runtime"}
                or relative in occupied
                or not isinstance(row.get("bytes"), int)
                or row["bytes"] < 0
                or re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256"))) is None
            ):
                raise FirstDetectionError(
                    "COMPONENT_FILE_INVALID", "A component file record is invalid or collides."
                )
            target = relative_path(stage, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            reject_links(target.parent, stage)
            if target.exists():
                raise FirstDetectionError(
                    "COMPONENT_FILE_COLLISION", "A release component target already exists."
                )
            digest = hashlib.sha256()
            received = 0
            with archive.open("payload/" + relative) as source, target.open("xb") as output:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > row["bytes"]:
                        raise FirstDetectionError(
                            "COMPONENT_FILE_IDENTITY_MISMATCH",
                            "A component file exceeded its exact size.",
                        )
                    digest.update(block)
                    output.write(block)
            if received != row["bytes"] or digest.hexdigest() != row["sha256"]:
                raise FirstDetectionError(
                    "COMPONENT_FILE_IDENTITY_MISMATCH",
                    "A component file differs from its exact identity.",
                )
            occupied.add(relative)
        record = stage / ".components" / f"{asset['asset_id']}.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest


def copy_plugin_source(
    plugin_root: Path,
    binding: Mapping[str, Any],
    stage: Path,
    occupied: set[str],
) -> dict[str, Any]:
    source_path = plugin_root / str(binding["source_manifest"])
    manifest = read_json(source_path)
    binding_path = plugin_root / "provisioning/release-binding.v4.json"
    binding_file_sha256 = sha256(binding_path)
    if (
        sha256(source_path) != binding["source_manifest_sha256"]
        or read_json(binding_path) != dict(binding)
    ):
        raise FirstDetectionError(
            "SOURCE_PACKAGE_CHANGED",
            "The release binding changed before source materialization.",
        )
    verify_source_manifest(plugin_root, manifest)
    for row in manifest["files"]:
        target_relative = "app/plugin/" + row["path"]
        if target_relative in occupied:
            raise FirstDetectionError(
                "SOURCE_COMPONENT_COLLISION", "Plugin source collides with a release component."
            )
        source = relative_path(plugin_root, row["path"])
        target = relative_path(stage, target_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if target.stat().st_size != row["bytes"] or sha256(target) != row["sha256"]:
            raise FirstDetectionError(
                "SOURCE_COPY_FAILED", "The immutable plugin source copy differs."
            )
        occupied.add(target_relative)
    for relative in sorted(_SOURCE_EXCLUDED):
        source = plugin_root / relative
        target_relative = "app/plugin/" + relative
        target = relative_path(stage, target_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        expected_hash = (
            binding["source_manifest_sha256"]
            if relative.endswith("source-manifest.v4.json")
            else binding_file_sha256
        )
        if sha256(target) != expected_hash:
            raise FirstDetectionError(
                "SOURCE_COPY_FAILED", "A release binding copy differs."
            )
        occupied.add(target_relative)
    launcher_source = (
        plugin_root
        / "scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.exe"
    )
    launcher_relative = "app/EvidenceLaneStudio.exe"
    launcher_target = relative_path(stage, launcher_relative)
    if not launcher_source.is_file() or launcher_relative in occupied:
        raise FirstDetectionError(
            "STUDIO_LAUNCHER_MISSING",
            "The release-bound Windows Studio launcher is missing.",
        )
    launcher_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(launcher_source, launcher_target)
    if sha256(launcher_source) != sha256(launcher_target):
        raise FirstDetectionError(
            "SOURCE_COPY_FAILED", "The Windows Studio launcher copy differs."
        )
    occupied.add(launcher_relative)
    return manifest


def _default_runner(
    command: Sequence[str], cwd: Path, environment: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=1800,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def run_checked(
    runner: Callable[[Sequence[str], Path, Mapping[str, str]], Any],
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    *,
    code: str,
) -> None:
    result = runner(command, cwd, environment)
    returncode = result if isinstance(result, int) else getattr(result, "returncode", None)
    if returncode != 0:
        raise FirstDetectionError(code, "A fixed installation command failed.")


def validate_offline_wheelhouse(
    requirements: Sequence[Path], wheelhouse: Path
) -> dict[str, Any]:
    requirement_pattern = re.compile(
        r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})=="
        r"(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127})"
        r" --hash=sha256:(?P<hash>[0-9a-f]{64})$"
    )
    names: set[str] = set()
    required_hashes: set[str] = set()
    line_count = 0
    for path in requirements:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise FirstDetectionError(
                "OFFLINE_LOCK_INVALID", "An offline dependency lock is too large."
            )
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            line_count += 1
            match = requirement_pattern.fullmatch(line)
            if match is None:
                raise FirstDetectionError(
                    "OFFLINE_LOCK_INVALID",
                    "Offline locks allow exact wheel pins and SHA-256 hashes only.",
                )
            name = re.sub(r"[-_.]+", "-", match.group("name")).casefold()
            if name in names:
                raise FirstDetectionError(
                    "OFFLINE_LOCK_INVALID", "Offline dependency names must be unique."
                )
            names.add(name)
            required_hashes.add(match.group("hash"))
    if not 1 <= line_count <= 2_000:
        raise FirstDetectionError(
            "OFFLINE_LOCK_INVALID", "An offline lock must contain bounded exact pins."
        )
    files = sorted(path for path in wheelhouse.iterdir())
    if (
        not 1 <= len(files) <= 2_000
        or any(
            not path.is_file()
            or path.is_symlink()
            or path.suffix.casefold() != ".whl"
            for path in files
        )
    ):
        raise FirstDetectionError(
            "OFFLINE_WHEELHOUSE_INVALID",
            "The release wheelhouse must contain regular wheel files only.",
        )
    observed_hashes = {sha256(path) for path in files}
    if observed_hashes != required_hashes:
        raise FirstDetectionError(
            "OFFLINE_WHEELHOUSE_INVALID",
            "The offline lock and release wheelhouse file hashes differ.",
        )
    return {
        "requirement_count": line_count,
        "wheel_count": len(files),
        "wheel_hashes_sha256": hashlib.sha256(
            canonical(sorted(observed_hashes))
        ).hexdigest(),
    }


def validate_runtime_license_index(path: Path, wheelhouse: Path) -> dict[str, Any]:
    value = read_json(path)
    verify_seal(value, code="RUNTIME_LICENSE_INDEX_INVALID")
    records = value.get("records")
    if (
        value.get("schema") != "evidence-lane.runtime-wheel-license-index.v4"
        or value.get("status") != "COMPLETE_RELEASE_WHEEL_LICENSE_EVIDENCE"
        or not isinstance(records, list)
        or len(records) != value.get("wheel_count")
        or not 1 <= len(records) <= 2_000
        or len({row.get("wheel_sha256") for row in records}) != len(records)
    ):
        raise FirstDetectionError(
            "RUNTIME_LICENSE_INDEX_INVALID",
            "The runtime wheel license index does not reconcile.",
        )
    observed = {sha256(item): item for item in wheelhouse.iterdir() if item.is_file()}
    for row in records:
        wheel = wheelhouse / str(row.get("wheel", ""))
        expressions = row.get("license_expression_or_classifiers")
        files = row.get("license_files")
        link = row.get("license_evidence_link")
        if (
            not wheel.is_file()
            or sha256(wheel) != row.get("wheel_sha256")
            or row.get("wheel_sha256") not in observed
            or not isinstance(expressions, list)
            or not isinstance(files, list)
            or not expressions
            and not files
            or any(
                re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256"))) is None
                or not isinstance(item.get("bytes"), int)
                or item["bytes"] < 0
                or not safe_relative(item.get("path"))
                or item.get("source_wheel") is not None
                and (
                    not safe_relative(item.get("source_wheel"))
                    or PurePosixPath(item["source_wheel"]).name
                    != item["source_wheel"]
                    or re.fullmatch(
                        r"[0-9a-f]{64}",
                        str(item.get("source_wheel_sha256")),
                    )
                    is None
                    or item["source_wheel_sha256"] not in observed
                )
                or link is not None
                and item.get("source_wheel") is None
                for item in files
            )
            or link is not None
            and (
                not isinstance(link, dict)
                or link.get("kind")
                != "same_release_vendor_nested_license_corpus"
                or not safe_relative(link.get("source_wheel"))
                or PurePosixPath(link["source_wheel"]).name != link["source_wheel"]
                or link.get("source_wheel_sha256") not in observed
                or link.get("source_container") != "rocm_sdk_devel/_devel.tar"
                or not isinstance(link.get("covered_projects"), list)
                or not link["covered_projects"]
            )
        ):
            raise FirstDetectionError(
                "RUNTIME_LICENSE_INDEX_INVALID",
                "A runtime wheel license record is incomplete or stale.",
            )
    if set(observed) != {row["wheel_sha256"] for row in records}:
        raise FirstDetectionError(
            "RUNTIME_LICENSE_INDEX_INVALID",
            "Every release wheel requires one license record.",
        )
    return {
        "wheel_count": len(records),
        "license_file_count": sum(len(row["license_files"]) for row in records),
    }


def materialize(
    plan: Mapping[str, Any],
    stage: Path,
    selected_components: set[str],
    runner: Callable[[Sequence[str], Path, Mapping[str, str]], Any],
) -> None:
    environment = {
        **{key: value for key, value in os.environ.items()
            if not key.upper().startswith("PYTHON")},
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "EVIDENCE_LANE_PLUGIN_ROOT": str(stage / "app/plugin"),
    }
    for step in plan["materialization_steps"]:
        if step["component_id"] not in selected_components:
            continue
        operation = step.get("operation")
        if operation == "create_venv":
            interpreter = relative_path(stage, step.get("interpreter"))
            target = relative_path(stage, step.get("target"))
            if not interpreter.is_file() or target.exists():
                raise FirstDetectionError(
                    "MATERIALIZATION_INPUT_INVALID", "A Python runtime input is missing or occupied."
                )
            run_checked(
                runner,
                [str(interpreter), "-I", "-m", "venv", "--copies", str(target)],
                stage,
                environment,
                code="VENV_CREATION_FAILED",
            )
            continue
        if operation == "pip_install":
            environment_root = relative_path(stage, step.get("environment"))
            python = environment_root / "Scripts/python.exe"
            wheelhouse = relative_path(stage, step.get("wheelhouse"))
            requirements = [
                relative_path(stage, value) for value in step.get("requirements", [])
            ]
            if not python.is_file() or not wheelhouse.is_dir() or not requirements or not all(
                path.is_file() for path in requirements
            ):
                raise FirstDetectionError(
                    "MATERIALIZATION_INPUT_INVALID", "An offline wheelhouse input is missing."
                )
            validate_offline_wheelhouse(requirements, wheelhouse)
            license_index_value = step.get("license_index")
            if license_index_value is not None:
                validate_runtime_license_index(
                    relative_path(stage, license_index_value), wheelhouse
                )
            for requirement in requirements:
                run_checked(
                    runner,
                    [
                        str(python),
                        "-I",
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-index",
                        "--no-deps",
                        "--require-hashes",
                        "--find-links",
                        str(wheelhouse),
                        "-r",
                        str(requirement),
                    ],
                    stage,
                    environment,
                    code="OFFLINE_PACKAGE_INSTALL_FAILED",
                )
            continue
        raise FirstDetectionError(
            "MATERIALIZATION_OPERATION_INVALID",
            "Only fixed virtual-environment and offline-package steps are allowed.",
        )


def rebind_materialized_venvs(
    plan: Mapping[str, Any],
    stage: Path,
    release_root: Path,
    selected_components: set[str],
) -> None:
    """Rewrite Windows venv base paths before the atomic staging move."""

    stage_text = str(stage.resolve())
    release_text = str(release_root.resolve())
    if stage_text == release_text or release_root.exists():
        raise FirstDetectionError(
            "VENV_REBIND_TARGET_INVALID",
            "The final release path must be distinct and unoccupied.",
        )
    targets = [
        relative_path(stage, step["target"])
        for step in plan["materialization_steps"]
        if step.get("operation") == "create_venv"
        and step["component_id"] in selected_components
    ]
    if not targets:
        raise FirstDetectionError(
            "VENV_REBIND_INPUT_MISSING",
            "The selected release has no materialized Python environment.",
        )
    for environment in targets:
        config = environment / "pyvenv.cfg"
        if not config.is_file() or config.is_symlink():
            raise FirstDetectionError(
                "VENV_REBIND_INPUT_MISSING",
                "A materialized Python environment lacks pyvenv.cfg.",
            )
        content = config.read_text(encoding="utf-8")
        if stage_text not in content or release_text in content:
            raise FirstDetectionError(
                "VENV_REBIND_INPUT_INVALID",
                "A materialized Python environment has an unexpected base path.",
            )
        rebound = content.replace(stage_text, release_text)
        if stage_text in rebound or release_text not in rebound:
            raise FirstDetectionError(
                "VENV_REBIND_FAILED",
                "A materialized Python environment retained its staging path.",
            )
        config.write_text(rebound, encoding="utf-8", newline="")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_links(path.parent, Path(path.anchor))
    temporary = path.with_suffix(path.suffix + f".{uuid4()}.tmp")
    if temporary.exists():
        raise FirstDetectionError(
            "INSTALLATION_TEMPORARY_COLLISION", "A temporary receipt path is occupied."
        )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def runtime_sealed(body: dict[str, Any]) -> dict[str, Any]:
    """Seal records consumed by runtime modules whose canonical form includes LF."""
    return {
        **body,
        "receipt_sha256": hashlib.sha256(canonical(body) + b"\n").hexdigest(),
    }


def finalize_installation_records(
    stage: Path,
    final_root: Path,
    installation: Path,
    plan: Mapping[str, Any],
    selected_components: set[str],
    component_manifests: Sequence[Mapping[str, Any]],
) -> None:
    """Bind extracted bytes to the exact paths runtime readers will use."""
    required_keys = {"native_inputs", "asset_groups", "provider_environments"}
    if not required_keys.issubset(plan):
        required = [
            stage / "toolchains/native-installation.v4.json",
            stage / "toolchains/asset-installation.v4.json",
            stage / "toolchains/provider-installation.v4.json",
        ]
        if not all(path.is_file() for path in required):
            raise FirstDetectionError(
                "INSTALLATION_RECORD_INPUT_MISSING",
                "The release fixture omitted required installation records.",
            )
        return
    manifest_by_component: dict[str, dict[str, Any]] = {}
    component_ids = {row["component_id"] for row in component_manifests}
    for component_id in component_ids:
        parts = sorted(
            (
                row
                for row in component_manifests
                if row["component_id"] == component_id
            ),
            key=lambda row: row["part_index"],
        )
        files = [file for part in parts for file in part["files"]]
        if len({row["path"] for row in files}) != len(files):
            raise FirstDetectionError(
                "COMPONENT_FILE_COLLISION",
                "Multipart component file paths must remain distinct.",
            )
        manifest_by_component[component_id] = {
            "files": files,
            "receipt_sha256": hashlib.sha256(
                canonical([part["receipt_sha256"] for part in parts])
            ).hexdigest(),
        }
    license_index = read_json(
        stage
        / "app/plugin/toolchains/licenses/retained-install-license-index.v4.json"
    )
    license_by_tool = {
        row["tool_id"]: row for row in license_index["records"]
    }
    native_rows = []
    for native in plan["native_inputs"]:
        if native["release_component"] not in selected_components:
            continue
        relative = native["installed_executables"][0]
        executable = relative_path(stage, "toolchains/bin/" + relative)
        if not executable.is_file():
            raise FirstDetectionError(
                "NATIVE_INSTALLATION_INPUT_MISSING",
                f"The {native['tool_id']} executable is missing from its component.",
            )
        catalog_id = native.get("catalog_tool_id")
        license_row = license_by_tool.get(catalog_id) if catalog_id else None
        native_rows.append(
            {
                "tool_id": native["tool_id"],
                "status": "verified",
                "executable": executable.relative_to(stage).as_posix(),
                "executable_sha256": sha256(executable),
                "version": native["version"],
                "license_receipt_sha256": (
                    license_row["evidence_sha256"]
                    if license_row is not None
                    else manifest_by_component[native["release_component"]][
                        "receipt_sha256"
                    ]
                ),
            }
        )
    if not native_rows or len({row["tool_id"] for row in native_rows}) != len(
        native_rows
    ):
        raise FirstDetectionError(
            "NATIVE_INSTALLATION_RECORD_INVALID",
            "The selected native tool installation does not reconcile.",
        )
    native_record = runtime_sealed(
        {
            "schema": "evidence-lane.shared-native-installation.v4",
            "status": "verified",
            "tools": native_rows,
        }
    )
    atomic_json(stage / "toolchains/native-installation.v4.json", native_record)

    asset_rows = []
    for group in plan["asset_groups"]:
        if group["component_id"] not in selected_components:
            continue
        folder = relative_path(stage, group["path"])
        component = manifest_by_component[group["component_id"]]
        prefix = group["path"] + "/"
        files = [
            {
                "path": row["path"].removeprefix(prefix),
                "bytes": row["bytes"],
                "sha256": row["sha256"],
            }
            for row in component["files"]
            if row["path"].startswith(prefix)
        ]
        if not folder.is_dir() or not files:
            raise FirstDetectionError(
                "SHARED_ASSET_INPUT_MISSING",
                f"The {group['asset_id']} asset folder is missing.",
            )
        source_manifest = group.get("source_manifest")
        source_sha = None
        if source_manifest:
            source_path = stage / "app/plugin" / source_manifest
            source_value = read_json(source_path)
            source_sha = sha256(source_path)
            expected_files = source_value.get("files")
            if isinstance(expected_files, list) and {
                (row["path"], row["bytes"], row["sha256"]) for row in expected_files
            } != {(row["path"], row["bytes"], row["sha256"]) for row in files}:
                raise FirstDetectionError(
                    "SHARED_ASSET_SOURCE_MISMATCH",
                    f"The {group['asset_id']} asset differs from its source manifest.",
                )
        asset_rows.append(
            {
                "asset_id": group["asset_id"],
                "status": "verified",
                "path": group["path"],
                "files": files,
                "files_sha256": hashlib.sha256(canonical(files) + b"\n").hexdigest(),
                "source_manifest_sha256": source_sha,
            }
        )
    if len({row["asset_id"] for row in asset_rows}) != len(asset_rows):
        raise FirstDetectionError(
            "SHARED_ASSET_RECORD_INVALID", "Shared asset identities must be unique."
        )
    asset_record = runtime_sealed(
        {
            "schema": "evidence-lane.shared-tool-assets.v4",
            "status": "verified",
            "assets": asset_rows,
        }
    )
    atomic_json(stage / "toolchains/asset-installation.v4.json", asset_record)

    provider_rows = []
    steps = {
        row["component_id"]: row
        for row in plan["materialization_steps"]
        if row["operation"] == "create_venv"
        and row["component_id"].startswith("provider-")
    }
    for provider in plan["provider_environments"]:
        component_id = f"provider-{provider['runtime_id']}"
        if component_id not in selected_components:
            continue
        environment_relative = steps[component_id]["target"]
        environment_stage = relative_path(stage, environment_relative)
        environment_final = relative_path(final_root, environment_relative)
        python_final = environment_final / "Scripts/python.exe"
        manifest_final = (
            final_root / "app/plugin" / provider["manifest"]
        ).resolve()
        record = {
            "runtime_id": provider["runtime_id"],
            "lock_sha256": provider["lock_sha256"],
            "python": str(python_final),
            "manifest_path": str(manifest_final),
            "manifest_sha256": provider["manifest_sha256"],
            "installation_state": "installed_from_locked_wheels",
            "execution_state": "not_probed",
        }
        atomic_json(environment_stage / "environment.json", record)
        provider_rows.append(
            {
                "runtime_id": provider["runtime_id"],
                "environment": environment_final.relative_to(installation).as_posix(),
                "manifest_sha256": provider["manifest_sha256"],
                "lock_sha256": provider["lock_sha256"],
            }
        )
    if "provider-cpu" in selected_components and not any(
        row["runtime_id"] == "cpu" for row in provider_rows
    ):
        raise FirstDetectionError(
            "PROVIDER_INSTALLATION_RECORD_INVALID",
            "The required CPU provider record is missing.",
        )
    provider_record = {
        "schema": "evidence-lane.provider-installation.v4",
        "installation_root": str(installation),
        "providers": provider_rows,
    }
    atomic_json(stage / "toolchains/provider-installation.v4.json", provider_record)


def installed_files_manifest(release_root: Path) -> dict[str, Any]:
    root = release_root.resolve(strict=True)
    selected_roots = [
        root / "app",
        root / "toolchains",
        root / "install-inputs",
        root / "runtime/engine/venv",
        root / ".components",
    ]
    rows = []
    total = 0
    for selected in selected_roots:
        if not selected.exists():
            continue
        reject_links(selected, root)
        for path in sorted(selected.rglob("*"), key=lambda item: item.as_posix()):
            reject_links(path, root)
            if path.is_dir():
                continue
            if not path.is_file():
                raise FirstDetectionError(
                    "INSTALLED_FILE_INVALID",
                    "An installed runtime member is not a regular file.",
                )
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            rows.append(
                {"path": relative, "bytes": size, "sha256": sha256(path)}
            )
            total += size
            if len(rows) > MAX_INSTALLED_FILES or total > MAX_SELECTED_BYTES:
                raise FirstDetectionError(
                    "INSTALLED_FILE_BUDGET",
                    "The installed immutable runtime exceeds its file or byte budget.",
                )
    if not rows or len({row["path"] for row in rows}) != len(rows):
        raise FirstDetectionError(
            "INSTALLED_FILE_INVALID",
            "The installed immutable file set is empty or ambiguous.",
        )
    return sealed(
        {
            "schema": "evidence-lane.installed-file-manifest.v4",
            "status": "COMPLETE_IMMUTABLE_RELEASE",
            "file_count": len(rows),
            "total_bytes": total,
            "files_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
            "files": rows,
            "mutable_engine_state_excluded": True,
        }
    )


def verify_installed_files(release_root: Path) -> dict[str, Any]:
    root = release_root.resolve(strict=True)
    path = root / ".evidence-lane-installed-files.json"
    manifest = read_json(path)
    verify_seal(manifest, code="INSTALLED_FILE_MANIFEST_INVALID")
    rows = manifest.get("files")
    if (
        manifest.get("schema") != "evidence-lane.installed-file-manifest.v4"
        or manifest.get("status") != "COMPLETE_IMMUTABLE_RELEASE"
        or manifest.get("mutable_engine_state_excluded") is not True
        or not isinstance(rows, list)
        or len(rows) != manifest.get("file_count")
        or not 1 <= len(rows) <= MAX_INSTALLED_FILES
        or len({row.get("path") for row in rows}) != len(rows)
        or hashlib.sha256(canonical(rows)).hexdigest() != manifest.get("files_sha256")
        or sum(row.get("bytes", -1) for row in rows) != manifest.get("total_bytes")
    ):
        raise FirstDetectionError(
            "INSTALLED_FILE_MANIFEST_INVALID",
            "The installed immutable file manifest does not reconcile.",
        )
    expected = set()
    for row in rows:
        member = relative_path(root, row.get("path"))
        expected.add(row["path"])
        if (
            not isinstance(row.get("bytes"), int)
            or row["bytes"] < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256"))) is None
            or not member.is_file()
            or member.stat().st_size != row["bytes"]
            or sha256(member) != row["sha256"]
        ):
            raise FirstDetectionError(
                "INSTALLED_RELEASE_CHANGED",
                "An immutable installed runtime file differs from its manifest.",
            )
    observed = set()
    for selected in (
        root / "app",
        root / "toolchains",
        root / "install-inputs",
        root / "runtime/engine/venv",
        root / ".components",
    ):
        if not selected.exists():
            continue
        for member in selected.rglob("*"):
            reject_links(member, root)
            if member.is_file():
                observed.add(member.relative_to(root).as_posix())
            elif not member.is_dir():
                raise FirstDetectionError(
                    "INSTALLED_RELEASE_CHANGED",
                    "An immutable installed runtime member is irregular.",
                )
    if observed != expected:
        raise FirstDetectionError(
            "INSTALLED_RELEASE_CHANGED",
            "The immutable installed runtime file set differs from its manifest.",
        )
    return manifest


def _entrypoints(release_root: Path, plan: Mapping[str, Any]) -> dict[str, Path]:
    result = {
        name: relative_path(release_root, relative)
        for name, relative in plan["entrypoints"].items()
    }
    for name, path in result.items():
        present = path.is_dir() if name == "plugin_root" else path.is_file()
        if not present:
            raise FirstDetectionError(
                "INSTALLATION_ENTRYPOINT_MISSING",
                f"The installed {name} entrypoint is missing.",
            )
        reject_links(path, release_root)
    return result


def _critical_hashes(
    release_root: Path, plan: Mapping[str, Any]
) -> dict[str, str]:
    paths = _entrypoints(release_root, plan)
    paths.update(
        {
            "plugin_manifest": release_root / "app/plugin/.codex-plugin/plugin.json",
            "bundle_plan": release_root / "app/plugin/provisioning/full-bundle-plan.v4.json",
            "release_binding": release_root
            / "app/plugin/provisioning/release-binding.v4.json",
            "source_manifest": release_root
            / "app/plugin/provisioning/source-manifest.v4.json",
            "installed_file_manifest": release_root
            / ".evidence-lane-installed-files.json",
        }
    )
    for name in (
        "native-installation.v4.json",
        "asset-installation.v4.json",
        "provider-installation.v4.json",
    ):
        paths["installation_" + name] = release_root / "toolchains" / name
    for index, path in enumerate(
        sorted((release_root / "toolchains/python/providers").glob("*/environment.json"))
    ):
        paths[f"provider_environment_{index}"] = path
    return {
        path.relative_to(release_root).as_posix(): sha256(path)
        for name, path in paths.items()
        if name != "plugin_root"
    }


def _release_receipt(
    release_root: Path,
    *,
    binding: Mapping[str, Any],
    plan: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, Any]],
    component_manifests: Sequence[Mapping[str, Any]],
    gpu: Mapping[str, Any],
) -> dict[str, Any]:
    critical = _critical_hashes(release_root, plan)
    external = plan["external_service_configuration"]
    if any(row.get("credentials_in_release_assets") is not False for row in external):
        raise FirstDetectionError(
            "EXTERNAL_CONFIGURATION_INVALID",
            "Release assets cannot carry external-service credentials.",
        )
    body = {
        "schema": RELEASE_RECEIPT_SCHEMA,
        "status": "MATERIALIZED_AND_SELF_TESTED",
        "plugin_id": binding["plugin_id"],
        "plugin_version": binding["plugin_version"],
        "repository": binding["repository"],
        "release_ref": binding["release_ref"],
        "sparse_root": binding["sparse_root"],
        "release_binding_sha256": sha256(
            release_root / "app/plugin/provisioning/release-binding.v4.json"
        ),
        "bundle_plan_sha256": sha256(
            release_root / "app/plugin/provisioning/full-bundle-plan.v4.json"
        ),
        "source_manifest_sha256": sha256(
            release_root / "app/plugin/provisioning/source-manifest.v4.json"
        ),
        "source_file_count": source_manifest["file_count"],
        "source_files_sha256": source_manifest["files_sha256"],
        "installed_file_manifest_sha256": sha256(
            release_root / ".evidence-lane-installed-files.json"
        ),
        "selected_components": sorted(
            {row["component_id"] for row in selected}
        ),
        "skipped_components": [
            {"component_id": component_id, "condition": condition}
            for component_id, condition in sorted(
                {
                    (row["component_id"], row["condition"])
                    for row in skipped
                }
            )
        ],
        "selected_release_assets": [
            {
                "component_id": row["component_id"],
                "asset_id": row["asset_id"],
                "part_index": row["part_index"],
                "part_count": row["part_count"],
                "bytes": row["bytes"],
                "sha256": row["sha256"],
                "component_manifest_sha256": row["component_manifest_sha256"],
            }
            for row in selected
        ],
        "component_file_manifests": [
            {
                "component_id": row["component_id"],
                "part_index": row["part_index"],
                "part_count": row["part_count"],
                "file_count": row["file_count"],
                "total_bytes": row["total_bytes"],
                "files_sha256": row["files_sha256"],
                "receipt_sha256": row["receipt_sha256"],
            }
            for row in component_manifests
        ],
        "host_provider_selection": dict(gpu),
        "external_service_configuration": [
            {
                "tool_id": row["tool_id"],
                "status": "unconfigured",
                "credentials_stored": False,
            }
            for row in external
        ],
        "critical_file_sha256": critical,
        "shared_once_across_projects": True,
        "project_database_count": 0,
        "project_files_copied": False,
        "installed_native_execution_claimed": False,
    }
    return sealed(body)


def validate_release_root(
    release_root: Path,
    *,
    expected_binding_sha256: str,
) -> dict[str, Any]:
    receipt_path = release_root / ".evidence-lane-release.json"
    receipt = read_json(receipt_path)
    verify_seal(receipt, code="INSTALLED_RELEASE_RECEIPT_INVALID")
    installed_files = verify_installed_files(release_root)
    critical = receipt.get("critical_file_sha256")
    if (
        receipt.get("schema") != RELEASE_RECEIPT_SCHEMA
        or receipt.get("status") != "MATERIALIZED_AND_SELF_TESTED"
        or receipt.get("release_binding_sha256") != expected_binding_sha256
        or receipt.get("shared_once_across_projects") is not True
        or receipt.get("project_database_count") != 0
        or receipt.get("project_files_copied") is not False
        or receipt.get("installed_file_manifest_sha256")
        != sha256(release_root / ".evidence-lane-installed-files.json")
        or installed_files.get("status") != "COMPLETE_IMMUTABLE_RELEASE"
        or not isinstance(critical, dict)
    ):
        raise FirstDetectionError(
            "INSTALLED_RELEASE_RECEIPT_INVALID",
            "The installed release receipt does not match the selected binding.",
        )
    for relative, expected in critical.items():
        path = relative_path(release_root, relative)
        if (
            re.fullmatch(r"[0-9a-f]{64}", str(expected)) is None
            or not path.is_file()
            or sha256(path) != expected
        ):
            raise FirstDetectionError(
                "INSTALLED_RELEASE_CHANGED",
                "A critical installed runtime file differs from its receipt.",
            )
        reject_links(path, release_root)
    return receipt


def validate_active_installation(
    root: Path, *, expected_binding_sha256: str | None = None
) -> dict[str, Any]:
    root = root.resolve()
    reject_links(root, Path(root.anchor))
    pointer_path = root / "installation.json"
    pointer = read_json(pointer_path)
    verify_seal(pointer, code="INSTALLATION_POINTER_INVALID")
    if (
        pointer.get("schema") != INSTALLATION_SCHEMA
        or pointer.get("status") != "ACTIVE_RELEASE"
        or pointer.get("installation_root") != str(root)
        or not safe_relative(pointer.get("active_release"))
    ):
        raise FirstDetectionError(
            "INSTALLATION_POINTER_INVALID", "The active installation pointer is invalid."
        )
    release_root = relative_path(root, pointer["active_release"])
    if release_root.parent != root / "releases" or not release_root.is_dir():
        raise FirstDetectionError(
            "INSTALLATION_POINTER_INVALID", "The active release root is invalid."
        )
    receipt_path = release_root / ".evidence-lane-release.json"
    if (
        not receipt_path.is_file()
        or sha256(receipt_path) != pointer.get("release_receipt_sha256")
    ):
        raise FirstDetectionError(
            "INSTALLATION_POINTER_INVALID", "The active release receipt differs."
        )
    expected = expected_binding_sha256 or str(pointer.get("release_binding_sha256"))
    if expected_binding_sha256 is not None and pointer.get("release_binding_sha256") != expected:
        raise FirstDetectionError(
            "INSTALLED_RELEASE_DIFFERENT",
            "The active release belongs to another exact plugin binding.",
        )
    receipt = validate_release_root(
        release_root, expected_binding_sha256=expected
    )
    entrypoints = pointer.get("entrypoints")
    if not isinstance(entrypoints, dict):
        raise FirstDetectionError(
            "INSTALLATION_POINTER_INVALID", "Installed entrypoints are missing."
        )
    resolved = {
        name: relative_path(release_root, relative)
        for name, relative in entrypoints.items()
    }
    if not all(
        path.is_dir() if name == "plugin_root" else path.is_file()
        for name, path in resolved.items()
    ):
        raise FirstDetectionError(
            "INSTALLED_RELEASE_CHANGED", "An installed entrypoint is missing."
        )
    return {
        "pointer": pointer,
        "release_receipt": receipt,
        "release_root": release_root,
        "runtime_python": resolved["runtime_python"],
        "runtime_pythonw": resolved["runtime_pythonw"],
        "plugin_root": resolved["plugin_root"],
        "studio_launcher_executable": resolved["studio_launcher_executable"],
        "mcp_launcher": resolved["mcp_launcher"],
        "engine_launcher": resolved["engine_launcher"],
    }


class FirstDetectionInstaller:
    def __init__(
        self,
        plugin_root: Path,
        root: Path,
        *,
        fetcher: Callable[[Mapping[str, Any], Path], None] = default_fetch,
        runner: Callable[
            [Sequence[str], Path, Mapping[str, str]], Any
        ] = _default_runner,
        gpu_probe: Callable[[], Mapping[str, Any]] = default_gpu_probe,
        license_grants: Sequence[str] = (),
        system: str | None = None,
        machine: str | None = None,
    ):
        self.plugin_root = plugin_root.resolve(strict=True)
        self.root = root.resolve()
        self.fetcher = fetcher
        self.runner = runner
        self.gpu_probe = gpu_probe
        self.license_grants = tuple(license_grants)
        self.system = platform.system() if system is None else system
        self.machine = platform.machine() if machine is None else machine

    def ensure(self) -> dict[str, Any]:
        if self.system != "Windows" or self.machine.casefold() not in {
            "amd64",
            "x86_64",
        }:
            raise FirstDetectionError(
                "WINDOWS_BUNDLE_UNSUPPORTED",
                "The shared Studio bundle requires Windows AMD64.",
            )
        binding, plan = validate_binding(self.plugin_root)
        if binding["status"] != "RELEASE_BOUND":
            raise FirstDetectionError(
                "RELEASE_BINDING_UNBOUND",
                "Commit and tag the exact release assets before first detection.",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        reject_links(self.root, Path(self.root.anchor))
        binding_sha = sha256(
            self.plugin_root / "provisioning/release-binding.v4.json"
        )
        with InstallationLock(self.root / "installation.lock"):
            pointer_path = self.root / "installation.json"
            if pointer_path.exists():
                active = validate_active_installation(
                    self.root, expected_binding_sha256=binding_sha
                )
                self._write_status(
                    "ACTIVE_EXACT_RELEASE",
                    binding,
                    active["release_receipt"]["selected_components"],
                )
                return {**active, "installation_state": "REUSED_EXACT_RELEASE"}
            gpu = dict(self.gpu_probe())
            selected, skipped = selected_assets(
                binding, gpu=gpu, license_grants=self.license_grants
            )
            self._write_status(
                "ACQUIRING_RELEASE_ASSETS",
                binding,
                sorted({row["component_id"] for row in selected}),
            )
            release_name = (
                f"v{binding['plugin_version']}-{binding['assets_sha256'][:16]}"
            )
            release_root = self.root / "releases" / release_name
            if release_root.exists():
                receipt = validate_release_root(
                    release_root, expected_binding_sha256=binding_sha
                )
                if receipt["selected_components"] != sorted(
                    {row["component_id"] for row in selected}
                ):
                    raise FirstDetectionError(
                        "INSTALLED_RELEASE_DIFFERENT",
                        "The existing release was materialized for another host profile.",
                    )
            else:
                self._materialize_release(
                    release_root,
                    binding=binding,
                    plan=plan,
                    selected=selected,
                    skipped=skipped,
                    gpu=gpu,
                )
            self._write_status(
                "MATERIALIZED_VALIDATING",
                binding,
                sorted({row["component_id"] for row in selected}),
            )
            try:
                self._verify_release_final(release_root, plan)
                self._register_release(release_root, plan)
            except Exception as reason:
                self._write_status(
                    "FAILED",
                    binding,
                    sorted({row["component_id"] for row in selected}),
                    error_code=(
                        reason.code
                        if isinstance(reason, FirstDetectionError)
                        else "UNEXPECTED_INSTALLATION_FAILURE"
                    ),
                )
                raise
            pointer = self._publish_pointer(
                release_root, binding_sha=binding_sha, plan=plan
            )
            self._write_status(
                "ACTIVE_EXACT_RELEASE",
                binding,
                sorted({row["component_id"] for row in selected}),
            )
            active = validate_active_installation(
                self.root, expected_binding_sha256=binding_sha
            )
            return {
                **active,
                "installation_state": "INSTALLED_EXACT_RELEASE",
                "installation_pointer": pointer,
            }

    def _materialize_release(
        self,
        release_root: Path,
        *,
        binding: Mapping[str, Any],
        plan: Mapping[str, Any],
        selected: Sequence[Mapping[str, Any]],
        skipped: Sequence[Mapping[str, Any]],
        gpu: Mapping[str, Any],
    ) -> None:
        stage = self.root / ".staging" / f"{release_root.name}-{uuid4()}"
        if stage.exists():
            raise FirstDetectionError(
                "INSTALLATION_STAGING_COLLISION", "A staging directory is occupied."
            )
        stage.mkdir(parents=True)
        occupied: set[str] = set()
        component_manifests = []
        try:
            for asset in selected:
                archive = acquire_asset(
                    asset, self.root / "cache/release-assets", self.fetcher
                )
                component_manifests.append(
                    extract_component(archive, asset, stage, occupied)
                )
            source_manifest = copy_plugin_source(
                self.plugin_root, binding, stage, occupied
            )
            materialize(
                plan,
                stage,
                {row["component_id"] for row in selected},
                self.runner,
            )
            finalize_installation_records(
                stage,
                release_root,
                self.root,
                plan,
                {row["component_id"] for row in selected},
                component_manifests,
            )
            entrypoints = _entrypoints(stage, plan)
            environment = {
                **{key: value for key, value in os.environ.items()
                    if not key.upper().startswith("PYTHON")},
                "PYTHONDONTWRITEBYTECODE": "1",
                "EVIDENCE_LANE_PLUGIN_ROOT": str(entrypoints["plugin_root"]),
                "EVIDENCE_LANE_STUDIO_ROOT": str(self.root),
                "EVIDENCE_LANE_RUNTIME_ROOT": str(stage / "runtime/engine"),
            }
            run_checked(
                self.runner,
                [
                    str(entrypoints["runtime_python"]),
                    "-I",
                    "-B",
                    str(entrypoints["installation_self_test"]),
                    "--release-root",
                    str(stage),
                ],
                stage,
                environment,
                code="INSTALLATION_SELF_TEST_FAILED",
            )
            rebind_materialized_venvs(
                plan,
                stage,
                release_root,
                {row["component_id"] for row in selected},
            )
            atomic_json(
                stage / ".evidence-lane-installed-files.json",
                installed_files_manifest(stage),
            )
            receipt = _release_receipt(
                stage,
                binding=binding,
                plan=plan,
                source_manifest=source_manifest,
                selected=selected,
                skipped=skipped,
                component_manifests=component_manifests,
                gpu=gpu,
            )
            atomic_json(stage / ".evidence-lane-release.json", receipt)
            release_root.parent.mkdir(parents=True, exist_ok=True)
            if release_root.exists():
                raise FirstDetectionError(
                    "INSTALLATION_RELEASE_COLLISION",
                    "The exact release path changed during materialization.",
                )
            os.replace(stage, release_root)
        except Exception as reason:
            self._write_status(
                "FAILED",
                binding,
                sorted({row["component_id"] for row in selected}),
                error_code=(
                    reason.code
                    if isinstance(reason, FirstDetectionError)
                    else "UNEXPECTED_INSTALLATION_FAILURE"
                ),
            )
            if stage.exists():
                failed = self.root / "failed" / stage.name
                failed.parent.mkdir(parents=True, exist_ok=True)
                if not failed.exists():
                    os.replace(stage, failed)
            raise

    def _write_status(
        self,
        phase: str,
        binding: Mapping[str, Any],
        selected_components: Sequence[str],
        *,
        error_code: str | None = None,
    ) -> None:
        body = {
            "schema": "evidence-lane.first-detection-status.v4",
            "phase": phase,
            "plugin_version": binding["plugin_version"],
            "release_ref": binding["release_ref"],
            "selected_components": list(selected_components),
            "error_code": error_code,
            "credentials_recorded": False,
            "project_state_changed": False,
            "installed_native_execution_claimed": False,
        }
        atomic_json(self.root / "installation-status.json", sealed(body))

    def _register_release(
        self, release_root: Path, plan: Mapping[str, Any]
    ) -> None:
        entrypoints = _entrypoints(release_root, plan)
        environment = {
            **{key: value for key, value in os.environ.items()
                if not key.upper().startswith("PYTHON")},
            "PYTHONDONTWRITEBYTECODE": "1",
            "EVIDENCE_LANE_PLUGIN_ROOT": str(entrypoints["plugin_root"]),
            "EVIDENCE_LANE_STUDIO_ROOT": str(self.root),
            "EVIDENCE_LANE_RUNTIME_ROOT": str(release_root / "runtime/engine"),
        }
        run_checked(
            self.runner,
            [
                str(entrypoints["runtime_python"]),
                "-I",
                "-B",
                str(entrypoints["installation_registration"]),
                "--installation-root",
                str(self.root),
                "--release-root",
                str(release_root),
            ],
            release_root,
            environment,
            code="INSTALLATION_REGISTRATION_FAILED",
        )

    def _verify_release_final(
        self, release_root: Path, plan: Mapping[str, Any]
    ) -> None:
        entrypoints = _entrypoints(release_root, plan)
        environment = {
            **{key: value for key, value in os.environ.items()
                if not key.upper().startswith("PYTHON")},
            "PYTHONDONTWRITEBYTECODE": "1",
            "EVIDENCE_LANE_PLUGIN_ROOT": str(entrypoints["plugin_root"]),
            "EVIDENCE_LANE_STUDIO_ROOT": str(self.root),
            "EVIDENCE_LANE_RUNTIME_ROOT": str(release_root / "runtime/engine"),
        }
        run_checked(
            self.runner,
            [
                str(entrypoints["runtime_python"]),
                "-I",
                "-B",
                str(entrypoints["installation_self_test"]),
                "--release-root",
                str(release_root),
                "--installation-root",
                str(self.root),
            ],
            release_root,
            environment,
            code="FINAL_INSTALLATION_SELF_TEST_FAILED",
        )

    def _publish_pointer(
        self,
        release_root: Path,
        *,
        binding_sha: str,
        plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        entrypoints = {
            name: path.relative_to(release_root).as_posix()
            for name, path in _entrypoints(release_root, plan).items()
            if name
            in {
                "runtime_python",
                "runtime_pythonw",
                "plugin_root",
                "studio_launcher_executable",
                "mcp_launcher",
                "engine_launcher",
            }
        }
        receipt_path = release_root / ".evidence-lane-release.json"
        pointer = sealed(
            {
                "schema": INSTALLATION_SCHEMA,
                "status": "ACTIVE_RELEASE",
                "installation_root": str(self.root),
                "active_release": release_root.relative_to(self.root).as_posix(),
                "release_binding_sha256": binding_sha,
                "release_receipt_sha256": sha256(receipt_path),
                "entrypoints": entrypoints,
                "shared_once_across_projects": True,
                "project_state_root": None,
            }
        )
        atomic_json(self.root / "installation.json", pointer)
        return pointer


def development_checkout(plugin_root: Path) -> bool:
    root = plugin_root.resolve()
    if root.name != "evidence-lane-plugin" or root.parent.name != "plugins":
        return False
    for ancestor in root.parents:
        if (ancestor / ".git").exists() and root == (
            ancestor / "plugins" / "evidence-lane-plugin"
        ).resolve():
            return True
    return False


def development_runtime_python(plugin_root: Path) -> Path | None:
    """Return the verified repository venv only for an actual checkout."""
    root = plugin_root.resolve()
    for ancestor in root.parents:
        if ((ancestor / ".git").exists()
                and root == (ancestor / "plugins" / "evidence-lane-plugin").resolve()):
            candidate = ancestor / ".venv/Scripts/python.exe"
            if not candidate.is_file():
                return None
            reject_links(candidate, ancestor)
            return candidate.resolve(strict=True)
    return None


def installed_process_environment(
    release_root: Path, base: Mapping[str, str]
) -> dict[str, str]:
    release = release_root.resolve(strict=True)
    path_entries = [
        release / "toolchains/node",
        release / "toolchains/node/node_modules/.bin",
        release / "toolchains/bin/git/cmd",
    ]
    for path in path_entries:
        if not path.is_dir():
            raise FirstDetectionError(
                "INSTALLED_TOOL_PATH_MISSING",
                "A required shared command directory is missing.",
            )
        reject_links(path, release)
    browser = (
        release
        / "toolchains/node/chrome-headless-shell/chrome-headless-shell.exe"
    )
    if not browser.is_file() or browser.is_symlink():
        raise FirstDetectionError(
            "INSTALLED_BROWSER_MISSING",
            "The pinned headless browser executable is missing.",
        )
    clean_base = {key: value for key, value in base.items()
        if not key.upper().startswith("PYTHON")}
    existing = str(clean_base.get("PATH") or "")
    return {
        **clean_base,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PATH": os.pathsep.join([*(str(path) for path in path_entries), existing]),
        "EVIDENCE_LANE_NODE_ROOT": str(release / "toolchains/node"),
        "PUPPETEER_EXECUTABLE_PATH": str(browser),
        "PUPPETEER_SKIP_DOWNLOAD": "true",
        "EVIDENCE_LANE_NATIVE_ROOT": str(release / "toolchains/bin"),
        "EVIDENCE_LANE_MODEL_ROOT": str(release / "toolchains/models"),
        "EVIDENCE_LANE_PROVIDER_ROOT": str(
            release / "toolchains/python/providers"
        ),
    }


def prepare_mcp(
    plugin_root: Path,
    argv: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    system: str | None = None,
    machine: str | None = None,
    installer_factory: Callable[..., FirstDetectionInstaller] = FirstDetectionInstaller,
) -> dict[str, Any]:
    root = plugin_root.resolve(strict=True)
    values = dict(os.environ if environment is None else environment)
    binding, _ = validate_binding(root)
    if binding["status"] == "SOURCE_TEMPLATE_UNBOUND":
        if not development_checkout(root):
            raise FirstDetectionError(
                "RELEASE_BINDING_UNBOUND",
                "An installed plugin cannot use an unbound development manifest.",
            )
        if sys.flags.isolated and values.get("EVIDENCE_LANE_DEVELOPMENT_RUNTIME_ACTIVE") != "1":
            executable = development_runtime_python(root)
            if executable is None:
                raise FirstDetectionError(
                    "DEVELOPMENT_RUNTIME_MISSING",
                    "The isolated development launcher requires the repository virtual environment.",
                )
            clean = {key: value for key, value in values.items()
                     if not key.upper().startswith("PYTHON")}
            clean["EVIDENCE_LANE_DEVELOPMENT_RUNTIME_ACTIVE"] = "1"
            clean["PYTHONDONTWRITEBYTECODE"] = "1"
            return {
                "mode": "DEVELOPMENT_SOURCE",
                "reexec": True,
                "command": [str(executable), "-I", "-B", str(root / "scripts/run_mcp.py"), *argv],
                "environment": clean,
            }
        return {
            "mode": "DEVELOPMENT_SOURCE",
            "reexec": False,
            "environment": values,
        }
    current_system = platform.system() if system is None else system
    current_machine = platform.machine() if machine is None else machine
    if current_system != "Windows":
        return {
            "mode": "REDUCED_NON_WINDOWS_HOST",
            "reexec": False,
            "environment": values,
            "studio_installed": False,
        }
    install_root = installation_root(values)
    binding_sha = sha256(root / "provisioning/release-binding.v4.json")
    if values.get("EVIDENCE_LANE_INSTALLED_RUNTIME_ACTIVE") == "1":
        active = validate_active_installation(
            install_root, expected_binding_sha256=binding_sha
        )
        if active["plugin_root"].resolve() != root:
            raise FirstDetectionError(
                "ACTIVE_PLUGIN_ROOT_MISMATCH",
                "The active runtime launcher does not match its immutable plugin copy.",
            )
        return {
            "mode": "ACTIVE_INSTALLED_RELEASE",
            "reexec": False,
            "environment": values,
            **active,
        }
    grants = []
    grant_path = values.get("EVIDENCE_LANE_GHOSTSCRIPT_LICENSE_RECEIPT")
    if grant_path:
        validate_ghostscript_license_grant(
            Path(grant_path), release_binding_sha256=binding_sha
        )
        grants.append("ghostscript")
    installer = installer_factory(
        root,
        install_root,
        license_grants=grants,
        system=current_system,
        machine=current_machine,
    )
    active = installer.ensure()
    active_environment = installed_process_environment(
        active["release_root"],
        {
        **values,
        "EVIDENCE_LANE_INSTALLED_RUNTIME_ACTIVE": "1",
        "EVIDENCE_LANE_STUDIO_ROOT": str(install_root),
        "EVIDENCE_LANE_RUNTIME_ROOT": str(active["release_root"] / "runtime/engine"),
        "EVIDENCE_LANE_PLUGIN_ROOT": str(active["plugin_root"]),
        },
    )
    return {
        "mode": active["installation_state"],
        "reexec": True,
        "command": [
            str(active["runtime_python"]),
            "-I",
            "-B",
            str(active["mcp_launcher"]),
            *argv,
        ],
        "environment": active_environment,
        **active,
    }
