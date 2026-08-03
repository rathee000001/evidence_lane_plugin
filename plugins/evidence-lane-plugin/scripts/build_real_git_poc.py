"""Build a sealed eighteen-lane PoC with one exact real Git lane.

The selected Evidence Lane repository commit is projected read-only into the
``github_code`` lane. Every other canonical lane receives only deterministic,
generated fixtures. The command emits an initial bundle, an unchanged Refresh,
and independent forensic reports for both.
"""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from evidence_lane_plugin.forensic_audit import (
    audit_lane_bundle,
    write_forensic_audit_reports,
)
from evidence_lane_plugin.hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.lane_engine import build_lane_bundle, validate_lane_bundle
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS

SCHEMA = "evidence-lane.real-git-poc.v1"
REAL_GIT_LANE = "github_code"
FIXTURE_ROOT = "_poc_fixtures"
SOURCE_SUBTREE = "plugins/evidence-lane-plugin/src/evidence_lane_plugin"


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _write_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Metrics" sheetId="1" r:id="rId1"/></sheets>
 <definedNames><definedName name="BaseCell">Metrics!$A$1</definedName></definedNames>
</workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
  Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData><row r="1"><c r="A1"><v>2</v></c>
 <c r="B1"><f>A1*2</f><v>4</v></c></row></sheetData>
</worksheet>""",
        )
        archive.writestr(
            "xl/tables/table1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 name="MetricsTable" displayName="MetricsTable" ref="A1:B2"/>""",
        )
        archive.writestr(
            "xl/charts/chart1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <c:chart><c:title><c:tx><c:rich><a:p><a:r><a:t>Metric trend</a:t></a:r>
 </a:p></c:rich></c:tx></c:title><c:plotArea><c:lineChart>
 <c:ser><c:val><c:numRef><c:f>Metrics!$B$1:$B$2</c:f></c:numRef></c:val>
 </c:ser></c:lineChart></c:plotArea></c:chart>
</c:chartSpace>""",
        )


def _write_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
 <w:r><w:t>Evidence hierarchy</w:t></w:r></w:p>
 <w:p><w:r><w:t>Deterministic one-shot document fixture.</w:t></w:r></w:p>
 <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Key</w:t></w:r></w:p></w:tc>
 <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
 </w:body></w:document>""",
        )


def _write_pptx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/>
 </p:nvSpPr><p:txBody><a:p><a:r><a:t>Persistent lane fixture</a:t></a:r>
 </a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>""",
        )


def _write_pdf(path: Path) -> None:
    content = b"BT /F1 12 Tf 72 720 Td (Evidence Lane one-shot PDF fixture) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode()
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for ordinal, item in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{ordinal} 0 obj\n".encode())
        payload.extend(item)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(payload))


def _write_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE parent(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE child(
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id),
                note TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE note_fts USING fts5(note);
            INSERT INTO parent(name) VALUES ('fixture-root');
            INSERT INTO child(parent_id, note) VALUES (1, 'linear fixture evidence');
            INSERT INTO note_fts(note) VALUES ('linear fixture evidence');
            """
        )


def _fixture_sources(repository: Path) -> dict[str, str]:
    root = repository / FIXTURE_ROOT
    root.mkdir()
    (root / "local.py").write_text(
        "from pathlib import Path\n\ndef fixture_symbol():\n    return Path('.')\n",
        encoding="utf-8",
    )
    (root / "chat.json").write_text(
        json.dumps(
            [
                {
                    "prompt": "Preserve the accepted pointer.",
                    "response": "The fixture records no approval or pointer movement.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "discussion.md").write_text(
        "Decision: preserve PV3\nDelta: test exact Git evidence\nGate: explicit HIL only\n",
        encoding="utf-8",
    )
    (root / "analysis.md").write_text(
        "Claim: only GitHub Code is real\nEvidence: route policy receipt\nRisk: host proof is external\n",
        encoding="utf-8",
    )
    (root / "plan.md").write_text(
        "Phase: PoC\nTask: run 18 lane audit\nOwner: one writer\nAcceptance: all gates pass\n",
        encoding="utf-8",
    )
    (root / "mode.md").write_text(
        "Scope: exact commit\nRule: fixtures are not production evidence\nGate: six-way HIL\n",
        encoding="utf-8",
    )
    _write_docx(root / "document.docx")
    _write_xlsx(root / "metrics.xlsx")
    (root / "metrics.csv").write_text(
        "name,value\ninitial,1\nrefresh,1\n", encoding="utf-8"
    )
    _write_pptx(root / "slides.pptx")
    _write_pdf(root / "evidence.pdf")
    (root / "image.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
            "/x8AAusB9Y9ZpAAAAABJRU5ErkJggg=="
        )
    )
    (root / "artifact.json").write_text(
        '{"artifact":"one-shot-fixture","authority":"none"}', encoding="utf-8"
    )
    (root / "custom.bin").write_bytes(b"\x00\x01bounded-fixture")
    _write_sqlite(root / "brain-loader.sqlite")
    with zipfile.ZipFile(
        root / "brain-package.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("manifest.json", '{"schema":"fixture-brain.v1"}')
        archive.writestr("pointer.json", '{"accepted":"none"}')
    (root / "research.md").write_text(
        "Question: are unchanged bytes reused?\nMethod: compare sealed Refresh hashes\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(
        root / "project.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("src/main.py", "print('fixture')\n")
        archive.writestr("docs/readme.md", "# One-shot project fixture\n")
    _write_sqlite(root / "sqlite-brain.sqlite")

    lane_by_name = {
        "local.py": "local_code",
        "chat.json": "chat_lineage",
        "discussion.md": "discussion",
        "analysis.md": "analysis",
        "plan.md": "plan",
        "mode.md": "mode",
        "document.docx": "docs",
        "metrics.xlsx": "data_excel",
        "metrics.csv": "data_excel",
        "slides.pptx": "ppt",
        "evidence.pdf": "pdf_ocr",
        "image.png": "images_ocr",
        "artifact.json": "artifacts",
        "custom.bin": "custom",
        "brain-loader.sqlite": "brain_loader",
        "brain-package.zip": "brain_loader",
        "research.md": "research",
        "project.zip": "project_engulf",
        "sqlite-brain.sqlite": "sqlite_brain",
    }
    return {f"{FIXTURE_ROOT}/{name}": lane for name, lane in lane_by_name.items()}


def _route_policy(bundle: Path) -> dict[str, Any]:
    routes = json.loads((bundle / "routes.json").read_text(encoding="utf-8"))[
        "routes"
    ]
    by_lane = {
        lane: sorted(path for path, routed_lane in routes.items() if routed_lane == lane)
        for lane in CANONICAL_LANE_IDS
    }
    invalid_real = [
        path for path in by_lane[REAL_GIT_LANE] if path.startswith(f"{FIXTURE_ROOT}/")
    ]
    invalid_fixture = {
        lane: [
            path
            for path in by_lane[lane]
            if not path.startswith(f"{FIXTURE_ROOT}/")
        ]
        for lane in CANONICAL_LANE_IDS
        if lane != REAL_GIT_LANE
    }
    invalid_fixture = {lane: paths for lane, paths in invalid_fixture.items() if paths}
    empty_lanes = [lane for lane, paths in by_lane.items() if not paths]
    passed = not invalid_real and not invalid_fixture and not empty_lanes
    return {
        "status": "PASS" if passed else "FAIL",
        "real_git_lane": REAL_GIT_LANE,
        "real_git_source_count": len(by_lane[REAL_GIT_LANE]),
        "fixture_lane_count": len(CANONICAL_LANE_IDS) - 1,
        "fixture_source_count": sum(
            len(paths) for lane, paths in by_lane.items() if lane != REAL_GIT_LANE
        ),
        "sources_by_lane": by_lane,
        "invalid_real_git_sources": invalid_real,
        "invalid_fixture_sources": invalid_fixture,
        "empty_lanes": empty_lanes,
    }


def _package_manifest(output: Path) -> dict[str, Any]:
    excluded = {"PACKAGE_MANIFEST.json"}
    members = {
        path.relative_to(output).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name not in excluded
    }
    manifest = {
        "schema": "evidence-lane.real-git-poc-package.v1",
        "member_count": len(members),
        "members": members,
        "package_sha256": sha256_bytes(canonical_json_bytes(members)),
    }
    atomic_write_json(output / "PACKAGE_MANIFEST.json", manifest)
    return manifest


def build_poc(repository: Path, output: Path, ref: str, subject: str) -> dict[str, Any]:
    repository = repository.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Output path already exists: {output}")
    commit = _git(repository, "rev-parse", f"{ref}^{{commit}}")
    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}")
    branch = _git(repository, "branch", "--show-current") or "DETACHED"

    with tempfile.TemporaryDirectory(prefix="evidence-lane-real-git-poc-") as temp:
        source = Path(temp) / "source"
        subprocess.run(
            ["git", "clone", "--no-checkout", "--no-hardlinks", str(repository), str(source)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        tracked_python = [
            path
            for path in _git(
                source,
                "ls-tree",
                "-r",
                "--name-only",
                commit,
                SOURCE_SUBTREE,
            ).splitlines()
            if path.endswith(".py")
        ]
        if not tracked_python:
            raise ValueError("The selected commit has no Python source in the plugin subtree.")
        _git(source, "sparse-checkout", "init", "--no-cone")
        _git(source, "sparse-checkout", "set", "--no-cone", *tracked_python)
        _git(source, "checkout", "--detach", commit)
        if _git(source, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError("Sparse exact-commit projection is unexpectedly dirty.")
        if _git(source, "rev-parse", "HEAD") != commit:
            raise ValueError("Sparse projection did not preserve the exact requested commit.")

        overrides = _fixture_sources(source)
        output.mkdir(parents=True)
        initial_root = output / "PV1_INITIAL"
        refresh_root = output / "PV2_REFRESH"
        initial = build_lane_bundle(
            repository_root=source,
            output_directory=initial_root,
            code_mode=REAL_GIT_LANE,
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV1",
            pointer_generation=0,
            source_overrides=overrides,
        )
        refresh = build_lane_bundle(
            repository_root=source,
            output_directory=refresh_root,
            code_mode=REAL_GIT_LANE,
            parent_lane_bundle=initial_root,
            parent_pv="PV1",
            proposed_pv="PV2",
            pointer_generation=1,
        )
        initial_validation = validate_lane_bundle(initial_root)
        refresh_validation = validate_lane_bundle(refresh_root)
        initial_policy = _route_policy(initial_root)
        refresh_policy = _route_policy(refresh_root)

        initial_audit = audit_lane_bundle(initial_root, subject=f"{subject} - initial")
        refresh_audit = audit_lane_bundle(refresh_root, subject=f"{subject} - Refresh")
        initial_reports = write_forensic_audit_reports(
            initial_audit, output / "FORENSIC_INITIAL"
        )
        refresh_reports = write_forensic_audit_reports(
            refresh_audit, output / "FORENSIC_REFRESH"
        )

    all_reused = refresh["summary"]["byte_reused_lanes"] == list(CANONICAL_LANE_IDS)
    passed = all(
        (
            initial_validation["valid"],
            refresh_validation["valid"],
            initial_policy["status"] == "PASS",
            refresh_policy["status"] == "PASS",
            initial_audit["status"] == "PASS",
            refresh_audit["status"] == "PASS",
            all_reused,
        )
    )
    receipt = {
        "schema": SCHEMA,
        "subject": subject,
        "source": {
            "repository": str(repository),
            "branch": branch,
            "commit": commit,
            "tree": tree,
            "projection": SOURCE_SUBTREE,
            "tracked_python_files": tracked_python,
            "tracked_python_file_count": len(tracked_python),
        },
        "authority_policy": {
            "real_git_lane": REAL_GIT_LANE,
            "fixture_lanes": [lane for lane in CANONICAL_LANE_IDS if lane != REAL_GIT_LANE],
            "fixture_statement": (
                "Every non-Git lane is deterministic one-shot test evidence and carries no "
                "production-source authority."
            ),
        },
        "initial": {
            "bundle": "PV1_INITIAL",
            "bundle_sha256": initial_validation["bundle_sha256"],
            "valid": initial_validation["valid"],
            "summary": initial["summary"],
            "route_policy": initial_policy,
            "forensic_status": initial_audit["status"],
            "forensic_audit_sha256": initial_audit["audit_sha256"],
            "forensic_package_sha256": initial_reports["package_sha256"],
        },
        "refresh": {
            "bundle": "PV2_REFRESH",
            "bundle_sha256": refresh_validation["bundle_sha256"],
            "valid": refresh_validation["valid"],
            "summary": refresh["summary"],
            "route_policy": refresh_policy,
            "all_eighteen_lanes_byte_reused": all_reused,
            "forensic_status": refresh_audit["status"],
            "forensic_audit_sha256": refresh_audit["audit_sha256"],
            "forensic_package_sha256": refresh_reports["package_sha256"],
        },
        "status": "PASS" if passed else "FAIL",
        "verdict": "PURSUE" if passed else "FIX_THEN_PURSUE",
        "confidence_percent": 99,
        "evidence_that_would_change_verdict": (
            "Any commit/tree mismatch, non-fixture input in a non-Git lane, fixture input in "
            "the Git lane, bundle/hash/SQLite/FTS/topology failure, or Refresh byte-reuse failure."
        ),
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    atomic_write_json(output / "POC_RECEIPT.json", receipt)
    readme = f"""# Evidence Lane v1.2 exact-Git PoC

- Status: **{receipt['status']}**
- Real source lane: `{REAL_GIT_LANE}` only
- Exact commit: `{commit}`
- Exact tree: `{tree}`
- Fixture lanes: `{len(CANONICAL_LANE_IDS) - 1}`
- Initial forensic reports: `18`
- Refresh forensic reports: `18`
- Unchanged Refresh byte reuse: `{all_reused}`

Every lane except `{REAL_GIT_LANE}` is deterministic one-shot test evidence, not
production evidence. The authoritative machine-readable receipt is
[`POC_RECEIPT.json`](POC_RECEIPT.json).

## Verdict

**{receipt['verdict']} - {receipt['confidence_percent']}% confidence.**

Evidence that would change the verdict: {receipt['evidence_that_would_change_verdict']}
"""
    atomic_write_bytes(output / "README.md", readme.encode("utf-8"))
    package = _package_manifest(output)
    result = {
        "status": receipt["status"],
        "output": str(output),
        "commit": commit,
        "tree": tree,
        "receipt_sha256": receipt["receipt_sha256"],
        "package_sha256": package["package_sha256"],
        "member_count": package["member_count"],
    }
    if not passed:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument(
        "--subject",
        default="Evidence Lane v1.2 exact plugin commit and seventeen fixture lanes",
    )
    args = parser.parse_args()
    result = build_poc(args.repository, args.output_directory, args.ref, args.subject)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
