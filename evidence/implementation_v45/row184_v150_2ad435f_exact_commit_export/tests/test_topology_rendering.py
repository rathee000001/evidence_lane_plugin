from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin import topology
from PIL import Image, PngImagePlugin


def _fake_render_run(counter: dict[str, int]):
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        counter["calls"] += 1
        output = Path(command[command.index("--output") + 1])
        fmt = command[command.index("--outputFormat") + 1]
        if fmt == "svg":
            output.write_text(
                f"<!-- volatile-{counter['calls']} -->\r\n"
                '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">\r\n'
                '  <rect width="8" height="8" fill="white"/>   \r\n'
                "</svg>\r\n",
                encoding="utf-8",
            )
        else:
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("volatile", str(counter["calls"]))
            Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(
                output,
                format="PNG",
                pnginfo=metadata,
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_render_mermaid_normalizes_volatile_svg_png_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "topology.mmd"
    source.write_text('flowchart TB\n  A["one"] --> B["two"]\n', encoding="utf-8")
    renderer = tmp_path / "mmdc.exe"
    renderer.write_bytes(b"fixed-renderer")
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"fixed-browser")
    monkeypatch.setenv("EVIDENCE_LANE_MERMAID_CLI", str(renderer))
    monkeypatch.setattr(
        topology,
        "_renderer_environment",
        lambda: (dict(os.environ), str(browser)),
    )
    counter = {"calls": 0}
    monkeypatch.setattr(topology.subprocess, "run", _fake_render_run(counter))

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = topology.render_mermaid(
        source,
        svg_path=first_root / "project_master_topology.svg",
        png_path=first_root / "project_master_topology.png",
    )
    second = topology.render_mermaid(
        source,
        svg_path=second_root / "project_master_topology.svg",
        png_path=second_root / "project_master_topology.png",
    )

    assert first == second
    assert first["status"] == "PASS"
    assert (first_root / "project_master_topology.svg").read_bytes() == (
        second_root / "project_master_topology.svg"
    ).read_bytes()
    assert (first_root / "project_master_topology.png").read_bytes() == (
        second_root / "project_master_topology.png"
    ).read_bytes()
    assert b"volatile" not in (
        first_root / "project_master_topology.svg"
    ).read_bytes()
    assert topology.validate_render_receipt(
        source,
        first,
        svg_path=first_root / "project_master_topology.svg",
        png_path=first_root / "project_master_topology.png",
    )["valid"] is True


def test_render_receipt_rejects_changed_source_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "topology.mmd"
    source.write_text('flowchart TB\n  A --> B\n', encoding="utf-8")
    renderer = tmp_path / "mmdc.exe"
    renderer.write_bytes(b"fixed-renderer")
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"fixed-browser")
    monkeypatch.setenv("EVIDENCE_LANE_MERMAID_CLI", str(renderer))
    monkeypatch.setattr(
        topology,
        "_renderer_environment",
        lambda: (dict(os.environ), str(browser)),
    )
    monkeypatch.setattr(
        topology.subprocess,
        "run",
        _fake_render_run({"calls": 0}),
    )
    svg = tmp_path / "topology.svg"
    png = tmp_path / "topology.png"
    receipt = topology.render_mermaid(source, svg_path=svg, png_path=png)

    source.write_text('flowchart TB\n  A --> C\n', encoding="utf-8")
    source_result = topology.validate_render_receipt(
        source,
        receipt,
        svg_path=svg,
        png_path=png,
    )
    assert source_result["valid"] is False
    assert "SOURCE_SHA256_MISMATCH" in {
        row["kind"] for row in source_result["errors"]
    }

    source.write_text('flowchart TB\n  A --> B\n', encoding="utf-8")
    png.write_bytes(png.read_bytes() + b"tamper")
    output_result = topology.validate_render_receipt(
        source,
        receipt,
        svg_path=svg,
        png_path=png,
    )
    assert output_result["valid"] is False
    assert "OUTPUT_SHA256_MISMATCH" in {
        row["kind"] for row in output_result["errors"]
    }


def test_skipped_render_receipt_still_binds_authoritative_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "topology.mmd"
    source.write_text("flowchart TB\n  A --> B\n", encoding="utf-8")
    monkeypatch.delenv("EVIDENCE_LANE_MERMAID_CLI", raising=False)
    svg = tmp_path / "topology.svg"
    png = tmp_path / "topology.png"

    receipt = topology.render_mermaid(source, svg_path=svg, png_path=png)
    validation = topology.validate_render_receipt(
        source,
        receipt,
        svg_path=svg,
        png_path=png,
    )

    assert receipt["status"] == "RENDER_SKIPPED"
    assert validation["valid"] is True
