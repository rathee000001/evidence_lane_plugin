"""Deterministic Mermaid source and optional side-product rendering."""

from __future__ import annotations

import os
import shutil
import sqlite3

# Required for one explicitly configured renderer; shell is never used.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from .hashing import atomic_write_bytes, sha256_file


def _escape(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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

    configured = os.environ.get("EVIDENCE_LANE_MERMAID_CLI", "").strip()
    if not configured:
        return {
            "status": "RENDER_SKIPPED",
            "warning": "MERMAID_RENDERER_NOT_CONFIGURED",
            "authoritative_source": Path(source_path).name,
        }
    executable = shutil.which(configured)
    if not executable:
        candidate = Path(configured).resolve()
        executable = str(candidate) if candidate.is_file() else ""
    if not executable:
        return {
            "status": "RENDER_FAILED",
            "warning": "MERMAID_RENDERER_NOT_FOUND",
            "authoritative_source": Path(source_path).name,
        }
    receipts = []
    for output, fmt in ((Path(svg_path), "svg"), (Path(png_path), "png")):
        command = [
            executable,
            "--input",
            str(Path(source_path).resolve()),
            "--output",
            str(output.resolve()),
            "--outputFormat",
            fmt,
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
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if completed.returncode != 0 or not output.is_file():
            for created in (Path(svg_path), Path(png_path)):
                if created.exists():
                    created.unlink()
            return {
                "status": "RENDER_FAILED",
                "warning": "MERMAID_SIDE_PRODUCT_FAILED",
                "authoritative_source": Path(source_path).name,
                "returncode": completed.returncode,
                "stderr": completed.stderr[-2000:],
            }
        receipts.append(
            {
                "format": fmt,
                "path": output.name,
                "sha256": sha256_file(output),
                "bytes": output.stat().st_size,
            }
        )
    return {
        "status": "PASS",
        "authoritative_source": Path(source_path).name,
        "outputs": receipts,
    }
