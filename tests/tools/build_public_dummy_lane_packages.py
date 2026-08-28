"""Repository-only PoC: publish full dummy lane packages and renders.

The public Proof surface must show the same full lane topology produced by the
engine, not a simplified diagram of the four output files. This command builds
one temporary synthetic repository, routes fixtures through all eighteen
canonical lanes, copies each lane's exact SQLite/MMD/DOT/Refresh artifacts, and
renders the complete MMD onto a 7680x4320 canvas plus a matching lossless SVG.
The SVG exists only as a derived inspection surface for deep zoom. It never touches a governed
source repository, candidate, or accepted pointer.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "evidence-lane-plugin"
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from build_one_shot_dummy_poc import DUMMY_GIT_LANE, _build_synthetic_repository
from evidence_lane_plugin.lane_engine import build_lane_bundle, validate_lane_bundle
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY

ADAPTER_ROOT = REPOSITORY_ROOT / "apps" / "evidence-lane-app"
PUBLIC_ROOT = ADAPTER_ROOT / "public" / "dummy-lane-packages"
INDEX_PATH = ADAPTER_ROOT / "app" / "_data" / "dummy-lane-artifacts.json"
PNG_SIZE = (7680, 4320)
SCHEMA = "evidence-lane.public-full-lane-dummy-proof.v4"
PUBLIC_FIXTURE_RECORDED_AT = "2026-01-03T00:00:00.000000Z"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _browser_executable() -> Path:
    configured = os.environ.get("PUPPETEER_EXECUTABLE_PATH", "").strip()
    candidates = [
        Path(configured) if configured else Path("__missing__"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    browser = next(
        (candidate.resolve() for candidate in candidates if candidate.is_file()), None
    )
    if browser is None:
        raise RuntimeError(
            "An already-installed Chrome or Edge executable is required for Mermaid rendering."
        )
    return browser


def _mermaid_cli() -> str:
    configured = os.environ.get("EVIDENCE_LANE_MERMAID_CLI", "").strip()
    executable = shutil.which(configured or "mmdc")
    if executable:
        return executable
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    raise RuntimeError(
        "The already-installed Mermaid CLI is required; this command never installs it."
    )


def _renderer_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout.strip()


def _renderer_environment(browser: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PUPPETEER_EXECUTABLE_PATH": str(browser),
        "TZ": "UTC",
        "LANG": "C",
        "LC_ALL": "C",
        "SOURCE_DATE_EPOCH": "0",
    }


def _write_mermaid_config(source: Path, destination: Path) -> None:
    destination.write_text(
        json.dumps(
            {
                "deterministicIds": True,
                "deterministicIDSeed": _sha256(source),
                "flowchart": {"htmlLabels": False, "useMaxWidth": False},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _rasterize_stable_svg_png(
    source_svg: Path,
    destination: Path,
    *,
    browser: Path,
) -> None:
    """Rasterize stable SVG bytes on an exact deterministic Chromium canvas."""

    environment = _renderer_environment(browser)
    with tempfile.TemporaryDirectory(
        prefix="evidence-lane-full-svg-raster-",
        ignore_cleanup_errors=True,
    ) as raw_temp:
        temporary = Path(raw_temp)
        rendered_path = temporary / "full-lane-topology.png"
        html_path = temporary / "render.html"
        html_path.write_text(
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            "html,body{margin:0;width:7680px;height:4320px;overflow:hidden;background:#fff}"
            "body{display:flex;align-items:center;justify-content:center}"
            "img{width:7600px;height:4240px;object-fit:contain}"
            '</style></head><body><img alt="" src="'
            + html.escape(source_svg.resolve().as_uri(), quote=True)
            + '"></body></html>\n',
            encoding="utf-8",
            newline="\n",
        )
        last_problem = ""
        for attempt in range(1, 4):
            profile_path = temporary / f"browser-profile-{attempt}"
            rendered_path.unlink(missing_ok=True)
            try:
                completed = subprocess.run(
                    [
                        str(browser),
                        "--headless=new",
                        "--disable-background-networking",
                        "--disable-background-timer-throttling",
                        "--disable-component-update",
                        "--disable-extensions",
                        "--disable-renderer-backgrounding",
                        "--disable-sync",
                        "--hide-scrollbars",
                        "--no-first-run",
                        "--allow-file-access-from-files",
                        "--run-all-compositor-stages-before-draw",
                        "--virtual-time-budget=1000",
                        "--force-device-scale-factor=1",
                        f"--window-size={PNG_SIZE[0]},{PNG_SIZE[1]}",
                        f"--user-data-dir={profile_path}",
                        f"--screenshot={rendered_path}",
                        html_path.resolve().as_uri(),
                    ],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    close_fds=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                last_problem = f"attempt {attempt} timed out after 90 seconds"
                continue
            if completed.returncode == 0 and rendered_path.is_file():
                break
            last_problem = (
                f"attempt {attempt} returncode={completed.returncode}; "
                f"stderr={completed.stderr[-2000:]}"
            )
        else:
            raise RuntimeError(
                f"Full Mermaid SVG screenshot failed for {source_svg.name}: {last_problem}"
            )
        with Image.open(rendered_path) as rendered:
            rendered.load()
            if rendered.size != PNG_SIZE:
                raise RuntimeError(
                    f"Expected exact {PNG_SIZE} SVG raster, got {rendered.size}."
                )
            normalized = rendered.convert("RGB")
        normalized.save(destination, format="PNG", optimize=False, compress_level=9)


def _public_artifact_path(url: str) -> Path:
    """Resolve one indexed public artifact without allowing path traversal."""

    prefix = "/dummy-lane-packages/"
    if not url.startswith(prefix):
        raise RuntimeError(f"Unexpected public artifact URL: {url!r}")
    candidate = (PUBLIC_ROOT / url.removeprefix(prefix)).resolve()
    public_root = PUBLIC_ROOT.resolve()
    if candidate == public_root or public_root not in candidate.parents:
        raise RuntimeError(f"Public artifact URL escaped its root: {url!r}")
    return candidate


def rerasterize_existing() -> dict[str, Any]:
    """Replace only existing derived PNGs and their indexed provenance.

    This bounded migration intentionally reuses the already-generated vector
    artifacts. It does not rebuild lane fixtures, invoke Git, or change any
    SQLite, MMD, DOT, receipt, or SVG bytes.
    """

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    lanes = index.get("lanes")
    if index.get("schema") != SCHEMA or not isinstance(lanes, list):
        raise RuntimeError("The existing public lane index has an unsupported schema.")
    if len(lanes) != len(CANONICAL_LANE_IDS):
        raise RuntimeError(
            "The existing public lane index is not the canonical 18-lane set."
        )
    if {lane.get("lane_id") for lane in lanes} != set(CANONICAL_LANE_IDS):
        raise RuntimeError(
            "The existing public lane index has unexpected lane identities."
        )

    browser = _browser_executable()
    staged_outputs: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory(
        prefix=".evidence-lane-reraster-",
        dir=PUBLIC_ROOT,
        ignore_cleanup_errors=True,
    ) as raw_temp:
        temporary = Path(raw_temp)
        for lane in lanes:
            lane_id = str(lane["lane_id"])
            vector = lane.get("vector_render")
            render = lane.get("render")
            if not isinstance(vector, dict) or not isinstance(render, dict):
                raise TypeError(f"Lane {lane_id!r} is missing indexed render metadata.")
            source_svg = _public_artifact_path(str(vector.get("url", "")))
            destination = _public_artifact_path(str(render.get("url", "")))
            if not source_svg.is_file() or source_svg.suffix.lower() != ".svg":
                raise RuntimeError(
                    f"Lane {lane_id!r} is missing its indexed vector SVG."
                )
            if destination.suffix.lower() != ".png":
                raise RuntimeError(f"Lane {lane_id!r} has an invalid PNG destination.")

            staged_png = temporary / f"{lane_id}.png"
            _rasterize_stable_svg_png(source_svg, staged_png, browser=browser)
            with Image.open(staged_png) as image:
                image.load()
                if image.size != PNG_SIZE:
                    raise RuntimeError(
                        f"Lane {lane_id!r} produced {image.size}, expected {PNG_SIZE}."
                    )
            render.update(
                {
                    "bytes": staged_png.stat().st_size,
                    "height": PNG_SIZE[1],
                    "rasterizer": "stable_svg_chromium_screenshot",
                    "sha256": _sha256(staged_png),
                    "width": PNG_SIZE[0],
                }
            )
            staged_outputs.append((staged_png, destination))

        staged_index = temporary / INDEX_PATH.name
        staged_index.write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for staged_png, destination in staged_outputs:
            os.replace(staged_png, destination)
        os.replace(staged_index, INDEX_PATH)

    return {
        "browser": browser.name,
        "index": str(INDEX_PATH),
        "lane_count": len(lanes),
        "mode": "RERASTERIZE_EXISTING_NO_GIT",
        "rasterizer": "stable_svg_chromium_screenshot",
    }


def _render_full_lane_png(
    source: Path,
    destination: Path,
    *,
    renderer: str,
    browser: Path,
) -> None:
    """Render one full Mermaid topology on an exact 8K publication canvas."""

    environment = _renderer_environment(browser)
    with tempfile.TemporaryDirectory(
        prefix="evidence-lane-full-mmd-render-"
    ) as raw_temp:
        temporary = Path(raw_temp)
        rendered_path = temporary / "full-lane-topology.png"
        config_path = temporary / "mermaid-config.json"
        _write_mermaid_config(source, config_path)
        completed = subprocess.run(
            [
                renderer,
                "--input",
                str(source),
                "--output",
                str(rendered_path),
                "--outputFormat",
                "png",
                "--backgroundColor",
                "white",
                "--width",
                "3600",
                "--height",
                "2000",
                "--configFile",
                str(config_path),
                "--scale",
                "1",
                "--quiet",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
        if completed.returncode != 0 or not rendered_path.is_file():
            raise RuntimeError(
                f"Full Mermaid render failed for {source.name}: "
                f"returncode={completed.returncode}; stderr={completed.stderr[-2000:]}"
            )
        with Image.open(rendered_path) as rendered:
            normalized = rendered.convert("RGB")
            fitted = normalized.resize(
                _fit_size(normalized.size, (PNG_SIZE[0] - 80, PNG_SIZE[1] - 80)),
                Image.Resampling.LANCZOS,
            )
            canvas = Image.new("RGB", PNG_SIZE, "white")
            offset = (
                (PNG_SIZE[0] - fitted.width) // 2,
                (PNG_SIZE[1] - fitted.height) // 2,
            )
            canvas.paste(fitted, offset)
            canvas.save(destination, format="PNG", optimize=False, compress_level=9)


def _render_full_lane_svg(
    source: Path,
    destination: Path,
    *,
    renderer: str,
    browser: Path,
) -> None:
    environment = _renderer_environment(browser)
    with tempfile.TemporaryDirectory(
        prefix="evidence-lane-full-mmd-vector-"
    ) as raw_temp:
        temporary = Path(raw_temp)
        rendered_path = temporary / "full-lane-topology.svg"
        config_path = temporary / "mermaid-config.json"
        _write_mermaid_config(source, config_path)
        completed = subprocess.run(
            [
                renderer,
                "--input",
                str(source),
                "--output",
                str(rendered_path),
                "--outputFormat",
                "svg",
                "--backgroundColor",
                "white",
                "--width",
                "7200",
                "--height",
                "4000",
                "--configFile",
                str(config_path),
                "--quiet",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=environment,
        )
        if completed.returncode != 0 or not rendered_path.is_file():
            raise RuntimeError(
                f"Full Mermaid vector render failed for {source.name}: "
                f"returncode={completed.returncode}; stderr={completed.stderr[-2000:]}"
            )
        svg = rendered_path.read_text(encoding="utf-8")
        lowered = svg.casefold()
        if "<svg" not in lowered or "viewbox=" not in lowered:
            raise RuntimeError(
                f"Mermaid vector render is missing an SVG viewBox for {source.name}."
            )
        if any(
            forbidden in lowered for forbidden in ("<script", "javascript:", " onload=")
        ):
            raise RuntimeError(
                f"Mermaid vector render contains forbidden active content for {source.name}."
            )
        destination.write_text(svg, encoding="utf-8", newline="\n")


def _fit_size(source: tuple[int, int], boundary: tuple[int, int]) -> tuple[int, int]:
    width, height = source
    max_width, max_height = boundary
    scale = min(max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _artifact(path: Path, kind: str, label: str) -> dict[str, Any]:
    relative = path.relative_to(ADAPTER_ROOT / "public").as_posix()
    return {
        "kind": kind,
        "label": label,
        "filename": path.name,
        "url": f"/{relative}",
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _full_mmd_preview(path: Path, line_limit: int = 72) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    preview = lines[:line_limit]
    if len(lines) > line_limit:
        preview.extend(
            (
                "",
                f"%% Preview stops at line {line_limit}; download the full {len(lines)}-line lane MMD above.",
            )
        )
    return "\n".join(preview) + "\n"


def _git_history(database: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        commits = connection.execute(
            "SELECT commit_sha, ordinal, authored_at, message "
            "FROM git_commit_registry ORDER BY ordinal"
        ).fetchall()
        parent_rows = connection.execute(
            "SELECT commit_sha, parent_sha, parent_ordinal "
            "FROM git_commit_parent ORDER BY commit_sha, parent_ordinal"
        ).fetchall()
        change_rows = connection.execute(
            "SELECT commit_sha, status, path FROM git_file_change "
            "ORDER BY commit_sha, status, path"
        ).fetchall()
    parents: dict[str, list[str]] = {commit[0]: [] for commit in commits}
    changes: dict[str, list[dict[str, str]]] = {commit[0]: [] for commit in commits}
    for commit_sha, parent_sha, _ordinal in parent_rows:
        parents[commit_sha].append(parent_sha)
    for commit_sha, status, path in change_rows:
        changes[commit_sha].append({"status": status, "path": path})
    return [
        {
            "commit": commit_sha,
            "ordinal": ordinal,
            "authored_at": authored_at,
            "subject": message,
            "parents": parents[commit_sha],
            "file_changes": changes[commit_sha],
        }
        for commit_sha, ordinal, authored_at, message in commits
    ]


def _validate_lane_database(path: Path, lane_id: str) -> None:
    expected_tables = set(LANE_REGISTRY[lane_id].schema_contract)
    with sqlite3.connect(path) as connection:
        actual_tables = {
            row[1]
            for row in connection.execute("PRAGMA table_list")
            if row[2] in {"table", "virtual"} and not row[1].startswith("sqlite_")
        }
        metadata = dict(connection.execute("SELECT key, value FROM lane_meta"))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if actual_tables != expected_tables:
        raise RuntimeError(f"Lane schema mismatch for {lane_id}.")
    if metadata.get("lane_id") != lane_id or integrity != ("ok",) or foreign_keys:
        raise RuntimeError(f"Lane database validation failed for {lane_id}.")


def build() -> dict[str, Any]:
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    renderer = _mermaid_cli()
    renderer_version = _renderer_version(renderer)
    browser = _browser_executable()

    lanes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="evidence-lane-public-full-lanes-"
    ) as raw_temp:
        temporary = Path(raw_temp)
        repository = temporary / "source"
        overrides = _build_synthetic_repository(repository)
        bundle = temporary / "PV1_FULL_LANE_PROOF"
        result = build_lane_bundle(
            repository_root=repository,
            output_directory=bundle,
            code_mode=DUMMY_GIT_LANE,
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV-PUBLIC-DUMMY-1",
            pointer_generation=0,
            source_overrides=overrides,
            max_lane_workers=1,
            recorded_at_override=PUBLIC_FIXTURE_RECORDED_AT,
        )
        validation = validate_lane_bundle(bundle)
        if not validation["valid"]:
            raise RuntimeError(
                f"Full public lane bundle failed validation: {validation!r}"
            )
        if (
            result["emitted_lane_ids"] != list(CANONICAL_LANE_IDS)
            or result["omitted_lane_ids"]
        ):
            raise RuntimeError(f"Expected all eighteen public dummy lanes: {result!r}")

        for ordinal, lane_id in enumerate(CANONICAL_LANE_IDS, start=1):
            lane = LANE_REGISTRY[lane_id]
            source_root = bundle / lane_id
            lane_root = PUBLIC_ROOT / lane_id
            if lane_root.exists():
                shutil.rmtree(lane_root)
            lane_root.mkdir(parents=True, exist_ok=True)
            sqlite_path = lane_root / lane.sqlite_filename
            mmd_path = lane_root / lane.mmd_filename
            dot_path = lane_root / lane.dot_filename
            receipt_path = lane_root / "refresh_receipt.json"
            png_path = lane_root / f"{lane_id}.mmd.8k.png"
            svg_path = lane_root / f"{lane_id}.mmd.vector.svg"

            for filename, destination in (
                (lane.sqlite_filename, sqlite_path),
                (lane.mmd_filename, mmd_path),
                (lane.dot_filename, dot_path),
                ("refresh_receipt.json", receipt_path),
            ):
                shutil.copyfile(source_root / filename, destination)
            _validate_lane_database(sqlite_path, lane_id)
            _render_full_lane_svg(
                mmd_path,
                svg_path,
                renderer=renderer,
                browser=browser,
            )
            # Mermaid's direct PNG output can vary at subpixel boundaries even
            # when deterministic IDs are enabled. The already-normalized SVG is
            # the authoritative render, so every lane is rasterized from those
            # stable bytes on the same exact 7680 x 4320 Chromium canvas.
            _rasterize_stable_svg_png(svg_path, png_path, browser=browser)
            rasterizer = "stable_svg_chromium_screenshot"

            expected = {
                sqlite_path.name,
                mmd_path.name,
                dot_path.name,
                receipt_path.name,
                png_path.name,
                svg_path.name,
            }
            actual = {path.name for path in lane_root.iterdir() if path.is_file()}
            if actual != expected:
                raise RuntimeError(
                    f"Unexpected files in {lane_root}: expected {sorted(expected)}, got {sorted(actual)}"
                )

            history = _git_history(sqlite_path) if lane_id == DUMMY_GIT_LANE else None
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            topology_identity = receipt["topology_generator"]["current"]
            lanes.append(
                {
                    "ordinal": ordinal,
                    "lane_id": lane_id,
                    "label": lane.display_label,
                    "command": lane.command,
                    "parser_id": lane.parser_id,
                    "chunker_version": lane.chunker_version,
                    "fts_table": lane.fts_table,
                    "schema_table_count": len(lane.schema_contract),
                    "schema_tables": list(lane.schema_contract),
                    "graph_profile": topology_identity["graph_projection_contract"][
                        "profile"
                    ],
                    "topology_generator_sha256": topology_identity["sha256"],
                    "canonical_artifacts": [
                        _artifact(sqlite_path, "sqlite", "SQLite sector"),
                        _artifact(mmd_path, "mmd", "Full lane Mermaid topology"),
                        _artifact(dot_path, "dot", "Full lane DOT topology"),
                        _artifact(receipt_path, "receipt", "Refresh receipt"),
                    ],
                    "render": {
                        **_artifact(png_path, "mmd_8k_png", "Full lane MMD 8K render"),
                        "width": PNG_SIZE[0],
                        "height": PNG_SIZE[1],
                        "source_mmd_sha256": _sha256(mmd_path),
                        "rasterizer": rasterizer,
                    },
                    "vector_render": {
                        **_artifact(
                            svg_path, "mmd_vector_svg", "Full lane MMD vector render"
                        ),
                        "source_mmd_sha256": _sha256(mmd_path),
                    },
                    "mmd_preview": _full_mmd_preview(mmd_path),
                    "git_history": history,
                }
            )

    actual_lane_directories = {
        path.name for path in PUBLIC_ROOT.iterdir() if path.is_dir()
    }
    if actual_lane_directories != set(CANONICAL_LANE_IDS):
        raise RuntimeError(
            f"Unexpected public lane directories: {actual_lane_directories!r}"
        )

    index = {
        "schema": SCHEMA,
        "lane_count": len(lanes),
        "canonical_file_count_per_lane": 4,
        "derived_render_count_per_lane": 2,
        "artifact_storage": "WEBSITE_STATIC_PUBLIC",
        "fixture_boundary": "SYNTHETIC_ONLY_NO_PROJECT_OR_ACCEPTED_PV_BYTES",
        "topology_boundary": "ACTUAL_FULL_LANE_ENGINE_MMD_AND_DOT_NOT_FOUR_FILE_OVERVIEW",
        "graph_projection_boundary": (
            "SQLITE_DERIVED_STABLE_IDENTITY_GRAPH_WITH_DISTINCT_GITHUB_AND_LOCAL_CODE_PROFILES"
        ),
        "engine_bundle_sha256": validation["bundle_sha256"],
        "renderer": {
            "tool": "mmdc",
            "version": renderer_version,
            "browser": browser.name,
            "canvas": list(PNG_SIZE),
            "install_performed": False,
        },
        "lanes": lanes,
    }
    INDEX_PATH.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rerasterize-existing",
        action="store_true",
        help=(
            "replace only the indexed 8K PNGs from existing vector SVGs; "
            "never rebuild fixtures or invoke Git"
        ),
    )
    arguments = parser.parse_args()
    built = rerasterize_existing() if arguments.rerasterize_existing else build()
    print(
        json.dumps(
            {
                "status": "PASS",
                "lane_count": built["lane_count"],
                "index": str(INDEX_PATH),
                "public_root": str(PUBLIC_ROOT),
                **(
                    {"topology_boundary": built["topology_boundary"]}
                    if "topology_boundary" in built
                    else {
                        "mode": built["mode"],
                        "rasterizer": built["rasterizer"],
                    }
                ),
            },
            sort_keys=True,
        )
    )
