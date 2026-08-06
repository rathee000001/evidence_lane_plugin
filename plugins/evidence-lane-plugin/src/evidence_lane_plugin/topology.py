"""Deterministic Mermaid source and optional side-product rendering."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3

# Required for one explicitly configured renderer; shell is never used.
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from .hashing import (
    atomic_write_bytes,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .redaction import redact_text

RENDER_RECEIPT_SCHEMA = "evidence-lane.mermaid-render-receipt.v2"
_SVG_COMMENT = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _escape(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _renderer_environment() -> tuple[dict[str, str], str | None]:
    """Bind Mermaid CLI to an installed browser without downloading one."""

    environment = dict(os.environ)
    configured = environment.get("PUPPETEER_EXECUTABLE_PATH", "").strip()
    if configured and Path(configured).is_file():
        return environment, str(Path(configured).resolve())
    candidates: list[Path] = []
    if os.name == "nt":
        for variable, suffixes in (
            (
                "ProgramFiles",
                (
                    "Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe",
                ),
            ),
            (
                "ProgramFiles(x86)",
                (
                    "Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe",
                ),
            ),
            (
                "LOCALAPPDATA",
                ("Google/Chrome/Application/chrome.exe",),
            ),
        ):
            root = environment.get(variable, "").strip()
            if root:
                candidates.extend(Path(root) / suffix for suffix in suffixes)
        candidate_drives = {
            drive
            for drive in (
                environment.get("SystemDrive", "").strip(),
                Path(sys.executable).drive,
                "C:",
            )
            if drive
        }
        for drive in sorted(candidate_drives):
            candidates.extend(
                (
                    Path(drive)
                    / "Program Files"
                    / "Google"
                    / "Chrome"
                    / "Application"
                    / "chrome.exe",
                    Path(drive)
                    / "Program Files"
                    / "Microsoft"
                    / "Edge"
                    / "Application"
                    / "msedge.exe",
                    Path(drive)
                    / "Program Files (x86)"
                    / "Google"
                    / "Chrome"
                    / "Application"
                    / "chrome.exe",
                    Path(drive)
                    / "Program Files (x86)"
                    / "Microsoft"
                    / "Edge"
                    / "Application"
                    / "msedge.exe",
                )
            )
    elif sys.platform == "darwin":
        candidates.append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        )
    else:
        for executable in ("google-chrome", "chromium", "chromium-browser"):
            resolved = shutil.which(executable)
            if resolved:
                candidates.append(Path(resolved))
    browser = next(
        (candidate.resolve() for candidate in candidates if candidate.is_file()),
        None,
    )
    if browser:
        environment["PUPPETEER_EXECUTABLE_PATH"] = str(browser)
    return environment, str(browser) if browser else None


def build_mermaid(
    connection: sqlite3.Connection, output_path: str | Path
) -> dict[str, Any]:
    repository = connection.execute(
        "SELECT owner, name, branch, commit_sha, tree_sha FROM repositories LIMIT 1"
    ).fetchone()
    files = connection.execute(
        """
        SELECT path, code_family, size_bytes
        FROM files
        ORDER BY path COLLATE NOCASE
        """
    ).fetchall()
    families = connection.execute(
        """
        SELECT code_family, COUNT(*) AS file_count, SUM(size_bytes) AS bytes
        FROM files
        GROUP BY code_family
        ORDER BY code_family
        """
    ).fetchall()
    routes = connection.execute(
        """
        SELECT f.path, r.method, r.path_pattern
        FROM routes r JOIN files f ON f.file_id = r.file_id
        ORDER BY f.path, r.path_pattern
        LIMIT 100
        """
    ).fetchall()
    lines = [
        "---",
        'title: "Evidence Lane Git Code Project Topology"',
        "---",
        "flowchart LR",
        (
            f'  repo["{_escape(repository["owner"])}/{_escape(repository["name"])}'
            f"<br/>branch: {_escape(repository['branch'])}"
            f"<br/>commit: {_escape(repository['commit_sha'][:12])}"
            f'<br/>tree: {_escape(repository["tree_sha"][:12])}"]'
        ),
    ]
    for index, row in enumerate(families):
        node_id = f"family_{index}"
        lines.append(
            f'  {node_id}["{_escape(row["code_family"])}'
            f'<br/>{row["file_count"]} files / {row["bytes"]} bytes"]'
        )
        lines.append(f"  repo --> {node_id}")
    top_directories: dict[str, int] = {}
    for row in files:
        part = row["path"].split("/", 1)[0]
        top_directories[part] = top_directories.get(part, 0) + 1
    for index, (directory, count) in enumerate(sorted(top_directories.items())):
        node_id = f"path_{index}"
        lines.append(f'  {node_id}["{_escape(directory)}<br/>{count} files"]')
        lines.append(f"  repo -.-> {node_id}")
    for index, row in enumerate(routes):
        node_id = f"route_{index}"
        method = row["method"] or "ROUTE"
        lines.append(
            f'  {node_id}["{_escape(method)} {_escape(row["path_pattern"])}'
            f'<br/>{_escape(row["path"])}"]'
        )
        lines.append(f"  repo --> {node_id}")
    lines.extend(
        [
            "  classDef repository fill:#e9fbff,stroke:#15a6c8,color:#17324d,stroke-width:2px;",
            "  classDef node fill:#f8fbfc,stroke:#8bb5c2,color:#17324d;",
            "  class repo repository;",
        ]
    )
    output = Path(output_path)
    atomic_write_bytes(output, ("\n".join(lines) + "\n").encode("utf-8"))
    return {
        "status": "PASS",
        "path": output.name,
        "sha256": sha256_file(output),
        "files": len(files),
        "families": len(families),
        "routes": len(routes),
    }


def _render_configuration(source_sha256: str) -> dict[str, Any]:
    return {
        "schema": "evidence-lane.mermaid-render-configuration.v1",
        "background_color": "white",
        "width": 2400,
        "height": 1600,
        "scale": 1,
        "timezone": "UTC",
        "locale": "C",
        "source_date_epoch": "0",
        "mermaid": {
            "deterministicIds": True,
            "deterministicIDSeed": source_sha256,
            "securityLevel": "strict",
            "theme": "neutral",
            "flowchart": {
                "htmlLabels": True,
                "useMaxWidth": False,
            },
        },
        "puppeteer": {
            "headless": True,
            "args": [
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
            ],
        },
        "normalization": {
            "svg": "utf8-lf-no-comments-no-trailing-space",
            "png": "pillow-rgba-png-compress-level-9-no-metadata",
        },
    }


def _seal_render_receipt(core: dict[str, Any]) -> dict[str, Any]:
    return {
        **core,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def _normalize_svg(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    if "<svg" not in text:
        raise ValueError("Rendered SVG has no <svg> root.")
    text = _SVG_COMMENT.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    atomic_write_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))


def _normalize_png(path: Path) -> None:
    with Image.open(path) as image:
        image.load()
        normalized = image.convert("RGBA")
        buffer = io.BytesIO()
        normalized.save(
            buffer,
            format="PNG",
            optimize=False,
            compress_level=9,
        )
    atomic_write_bytes(path, buffer.getvalue())


def _binary_identity(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    resolved = Path(path).resolve()
    if not resolved.is_file():
        return None
    return {
        "name": resolved.name,
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def validate_render_receipt(
    source_path: str | Path,
    receipt: dict[str, Any],
    *,
    svg_path: str | Path,
    png_path: str | Path,
) -> dict[str, Any]:
    """Recompute a render receipt's source/output binding without rendering."""

    source = Path(source_path)
    expected_outputs = {
        "svg": Path(svg_path),
        "png": Path(png_path),
    }
    errors: list[dict[str, Any]] = []
    if receipt.get("schema") != RENDER_RECEIPT_SCHEMA:
        return {
            "schema": RENDER_RECEIPT_SCHEMA,
            "status": "LEGACY_UNBOUND",
            "valid": False,
            "errors": [{"kind": "RENDER_RECEIPT_SCHEMA_UNBOUND"}],
        }
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    computed_receipt_sha256 = sha256_bytes(canonical_json_bytes(core))
    if receipt.get("receipt_sha256") != computed_receipt_sha256:
        errors.append(
            {
                "kind": "RECEIPT_SHA256_MISMATCH",
                "expected": computed_receipt_sha256,
                "actual": receipt.get("receipt_sha256"),
            }
        )
    actual_source_sha256 = sha256_file(source) if source.is_file() else None
    if receipt.get("source_sha256") != actual_source_sha256:
        errors.append(
            {
                "kind": "SOURCE_SHA256_MISMATCH",
                "expected": receipt.get("source_sha256"),
                "actual": actual_source_sha256,
            }
        )
    configuration = receipt.get("configuration")
    expected_configuration = (
        _render_configuration(actual_source_sha256)
        if actual_source_sha256 is not None
        else None
    )
    if configuration != expected_configuration:
        errors.append({"kind": "RENDER_CONFIGURATION_MISMATCH"})
    computed_configuration_sha256 = (
        sha256_bytes(canonical_json_bytes(configuration))
        if isinstance(configuration, dict)
        else None
    )
    if receipt.get("configuration_sha256") != computed_configuration_sha256:
        errors.append(
            {
                "kind": "CONFIGURATION_SHA256_MISMATCH",
                "expected": computed_configuration_sha256,
                "actual": receipt.get("configuration_sha256"),
            }
        )

    status = str(receipt.get("status") or "")
    output_rows = receipt.get("outputs")
    if status == "PASS":
        if not isinstance(output_rows, list):
            errors.append({"kind": "OUTPUT_RECEIPTS_MISSING"})
            output_rows = []
        by_format = {
            str(row.get("format")): row
            for row in output_rows
            if isinstance(row, dict)
        }
        if set(by_format) != set(expected_outputs):
            errors.append(
                {
                    "kind": "OUTPUT_FORMAT_SET_MISMATCH",
                    "expected": sorted(expected_outputs),
                    "actual": sorted(by_format),
                }
            )
        for fmt, path in expected_outputs.items():
            row = by_format.get(fmt)
            if row is None:
                continue
            actual_sha256 = sha256_file(path) if path.is_file() else None
            actual_bytes = path.stat().st_size if path.is_file() else None
            if row.get("path") != path.name:
                errors.append({"kind": "OUTPUT_PATH_MISMATCH", "format": fmt})
            if row.get("sha256") != actual_sha256:
                errors.append(
                    {
                        "kind": "OUTPUT_SHA256_MISMATCH",
                        "format": fmt,
                        "expected": row.get("sha256"),
                        "actual": actual_sha256,
                    }
                )
            if row.get("bytes") != actual_bytes:
                errors.append({"kind": "OUTPUT_SIZE_MISMATCH", "format": fmt})
            if path.is_file() and fmt == "svg":
                try:
                    svg_text = path.read_text(encoding="utf-8")
                except UnicodeError:
                    svg_text = ""
                if "<svg" not in svg_text:
                    errors.append({"kind": "SVG_ROOT_INVALID"})
            if (
                path.is_file()
                and fmt == "png"
                and not path.read_bytes().startswith(_PNG_SIGNATURE)
            ):
                errors.append({"kind": "PNG_SIGNATURE_INVALID"})
    elif status in {"RENDER_SKIPPED", "RENDER_FAILED"}:
        if output_rows not in (None, []):
            errors.append({"kind": "NONPASS_OUTPUT_RECEIPTS_PRESENT"})
        stale_outputs = [
            path.name for path in expected_outputs.values() if path.is_file()
        ]
        if stale_outputs:
            errors.append(
                {"kind": "NONPASS_STALE_OUTPUTS_PRESENT", "paths": stale_outputs}
            )
    else:
        errors.append({"kind": "RENDER_STATUS_INVALID", "actual": status})
    return {
        "schema": RENDER_RECEIPT_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "render_status": status,
        "valid": not errors,
        "source_sha256": actual_source_sha256,
        "receipt_sha256": computed_receipt_sha256,
        "errors": errors,
    }


def render_mermaid(
    source_path: str | Path,
    *,
    svg_path: str | Path,
    png_path: str | Path,
    timeout: int = 120,
) -> dict[str, Any]:
    """Render only through an explicitly configured or already installed CLI.

    The valid MMD source is authoritative. No browser or rendering dependency is
    installed automatically, and any failure is returned as a bounded warning.
    """

    source = Path(source_path).resolve()
    source_sha256 = sha256_file(source)
    configuration = _render_configuration(source_sha256)
    configuration_sha256 = sha256_bytes(canonical_json_bytes(configuration))
    base_receipt: dict[str, Any] = {
        "schema": RENDER_RECEIPT_SCHEMA,
        "authoritative_source": source.name,
        "source_sha256": source_sha256,
        "configuration": configuration,
        "configuration_sha256": configuration_sha256,
    }
    configured = os.environ.get("EVIDENCE_LANE_MERMAID_CLI", "").strip()
    if not configured:
        return _seal_render_receipt({
            **base_receipt,
            "status": "RENDER_SKIPPED",
            "warning": "MERMAID_RENDERER_NOT_CONFIGURED",
            "renderer": None,
            "outputs": [],
        })
    executable = shutil.which(configured)
    if not executable:
        candidate = Path(configured).resolve()
        executable = str(candidate) if candidate.is_file() else ""
    if not executable:
        return _seal_render_receipt({
            **base_receipt,
            "status": "RENDER_FAILED",
            "warning": "MERMAID_RENDERER_NOT_FOUND",
            "renderer": None,
            "outputs": [],
        })
    render_environment, browser_executable = _renderer_environment()
    render_environment.update(
        {
            "TZ": "UTC",
            "LANG": "C",
            "LC_ALL": "C",
            "SOURCE_DATE_EPOCH": "0",
        }
    )
    renderer_identity = {
        "cli": _binary_identity(executable),
        "browser": _binary_identity(browser_executable),
    }
    receipts: list[dict[str, Any]] = []
    outputs = ((Path(svg_path).resolve(), "svg"), (Path(png_path).resolve(), "png"))
    try:
        with tempfile.TemporaryDirectory(prefix="evidence_lane_render_") as tmp:
            config_path = Path(tmp) / "mermaid-config.json"
            puppeteer_config_path = Path(tmp) / "puppeteer-config.json"
            atomic_write_bytes(
                config_path,
                canonical_json_bytes(configuration["mermaid"]),
            )
            puppeteer_configuration = dict(configuration["puppeteer"])
            if browser_executable:
                puppeteer_configuration["executablePath"] = browser_executable
            atomic_write_bytes(
                puppeteer_config_path,
                canonical_json_bytes(puppeteer_configuration),
            )
            for output, fmt in outputs:
                output.parent.mkdir(parents=True, exist_ok=True)
                command = [
                    executable,
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--outputFormat",
                    fmt,
                    "--configFile",
                    str(config_path),
                    "--puppeteerConfigFile",
                    str(puppeteer_config_path),
                    "--backgroundColor",
                    str(configuration["background_color"]),
                    "--width",
                    str(configuration["width"]),
                    "--height",
                    str(configuration["height"]),
                    "--scale",
                    str(configuration["scale"]),
                    "--quiet",
                ]
                # The configured executable is resolved and receives only list argv.
                completed = subprocess.run(  # nosec B603
                    command,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    close_fds=True,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    ),
                    env=render_environment,
                )
                if completed.returncode != 0 or not output.is_file():
                    raise RuntimeError(
                        json.dumps(
                            {
                                "returncode": completed.returncode,
                                "stderr": redact_text(completed.stderr[-2000:]),
                            },
                            sort_keys=True,
                        )
                    )
                if fmt == "svg":
                    _normalize_svg(output)
                else:
                    _normalize_png(output)
                receipts.append(
                    {
                        "format": fmt,
                        "path": output.name,
                        "sha256": sha256_file(output),
                        "bytes": output.stat().st_size,
                    }
                )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        for created, _fmt in outputs:
            created.unlink(missing_ok=True)
        error_text = redact_text(str(exc))
        return _seal_render_receipt({
            **base_receipt,
            "status": "RENDER_FAILED",
            "warning": "MERMAID_SIDE_PRODUCT_FAILED",
            "renderer": renderer_identity,
            "error_sha256": sha256_bytes(error_text.encode("utf-8")),
            "error": error_text[-2000:],
            "outputs": [],
        })
    return _seal_render_receipt({
        **base_receipt,
        "status": "PASS",
        "renderer": renderer_identity,
        "outputs": receipts,
    })
