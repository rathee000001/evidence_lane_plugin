from __future__ import annotations

import base64
import importlib.util
import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import evidence_lane_plugin.lanes as lanes_module
import pytest
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.lane_engine import (
    build_lane_bundle,
    validate_lane_bundle,
)
from evidence_lane_plugin.lanes import (
    CANONICAL_LANE_IDS,
    LANE_REGISTRY,
    LaneRegistryError,
    resolve_lane_id,
    route_batch,
    route_source,
)


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
  <sheetData>
    <row r="1">
      <c r="A1"><v>2</v></c>
      <c r="B1"><f>A1*2</f><v>4</v></c>
    </row>
  </sheetData>
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
 <w:body>
  <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
   <w:r><w:t>Evidence hierarchy</w:t></w:r></w:p>
  <w:p><w:r><w:t>Deterministic document paragraph.</w:t></w:r></w:p>
  <w:tbl><w:tr>
   <w:tc><w:p><w:r><w:t>Key</w:t></w:r></w:p></w:tc>
   <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
  </w:tr></w:tbl>
  <w:p><w:r><w:drawing><a:blip r:embed="rIdImage1"/></w:drawing></w:r></w:p>
 </w:body>
</w:document>""",
        )


def _write_pptx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <p:cSld><p:spTree>
  <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/></p:nvSpPr>
   <p:txBody><a:p><a:r><a:t>Persistent lane</a:t></a:r></a:p></p:txBody>
  </p:sp>
  <p:graphicFrame><a:graphic><a:graphicData><a:tbl>
   <a:tr><a:tc><a:txBody><a:p><a:r><a:t>Gate</a:t></a:r></a:p></a:txBody></a:tc>
   <a:tc><a:txBody><a:p><a:r><a:t>HIL</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
  </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
  <p:pic><p:nvPicPr><p:cNvPr id="3" name="Picture 1" descr="fixture"/>
   </p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic>
 </p:spTree></p:cSld>
</p:sld>""",
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
  Target="../notesSlides/notesSlide1.xml"/>
 <Relationship Id="rId2"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
  Target="../media/image1.png"/>
</Relationships>""",
        )
        archive.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
  <a:t>Speaker evidence note</a:t>
 </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:notes>""",
        )
        archive.writestr("ppt/media/image1.png", _png_bytes())


def _write_pdf(path: Path) -> None:
    content = b"BT /F1 12 Tf 72 720 Td (Evidence lane PDF fixture) Tj ET"
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


def _write_scanned_pdf(path: Path) -> bool:
    if importlib.util.find_spec("fitz") is None:
        _write_pdf(path)
        return False
    import fitz

    document = fitz.open()
    try:
        page = document.new_page(width=900, height=220)
        page.insert_image(page.rect, stream=_ocr_image_bytes())
        document.save(path)
    finally:
        document.close()
    return True


def _png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAusB9Y9ZpAAAAABJRU5ErkJggg=="
    )


def _ocr_image_bytes() -> bytes:
    if importlib.util.find_spec("PIL") is None:
        return _png_bytes()
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((30, 60), "EVIDENCE LANE 123", fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _write_sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
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
            INSERT INTO parent(name) VALUES ('root');
            INSERT INTO child(parent_id, note) VALUES (1, 'linear evidence');
            INSERT INTO note_fts(note) VALUES ('linear evidence');
            """
        )
        connection.commit()
    finally:
        connection.close()


def _write_project_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("src/main.py", "print('project engulf')\n")
        archive.writestr("docs/readme.md", "# Engulf fixture\n")
        archive.writestr("data/metrics.csv", "name,value\npv,3\n")


def _write_brain_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", '{"schema":"brain-package.v1"}')
        archive.writestr("pointer.json", '{"accepted":"PV2"}')
        archive.writestr("brain.sqlite", b"fixture-not-opened-from-archive")


def _write_parquet_or_blocker(path: Path) -> bool:
    if importlib.util.find_spec("pyarrow") is None:
        path.write_bytes(b"PAR1fixture-without-optional-readerPAR1")
        return False
    import pyarrow as pa
    from pyarrow import parquet

    table = pa.table({"name": ["alpha", "beta"], "value": [1, 2]})
    parquet.write_table(table, path)
    return True


def _count(database: Path, table: str) -> int:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        connection.close()


def _fact_kinds(database: Path) -> set[str]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT kind FROM structured_fact ORDER BY kind"
            )
        }
    finally:
        connection.close()


def _parser_states(database: Path) -> list[str]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT parser_state FROM source_registry ORDER BY path"
            )
        ]
    finally:
        connection.close()


def _retrieval_rows(
    database: Path,
    fts_table: str,
    term: str,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        bm25 = connection.execute(
            f"""
            SELECT CAST(chunk_id AS INTEGER), path, locator, bm25({fts_table})
            FROM {fts_table}
            WHERE {fts_table} MATCH ?
            ORDER BY bm25({fts_table}), path, locator, CAST(chunk_id AS INTEGER)
            """,
            (term,),
        ).fetchall()
        tfidf = connection.execute(
            """
            SELECT c.chunk_id, s.path, c.locator, SUM(v.tfidf) AS score
            FROM tfidf_vector v
            JOIN chunk_index c ON c.chunk_id=v.chunk_id
            JOIN source_registry s ON s.source_id=c.source_id
            WHERE v.term=?
            GROUP BY c.chunk_id, s.path, c.locator
            ORDER BY score DESC, s.path, c.locator, c.chunk_id
            """,
            (term.lower(),),
        ).fetchall()
        return bm25, tfidf
    finally:
        connection.close()


def test_registry_requires_explicit_code_mode() -> None:
    assert len(CANONICAL_LANE_IDS) == 18
    ordered_commands = sorted(lane.command for lane in LANE_REGISTRY.values())
    assert ordered_commands == [
        "evi-02-git",
        "evi-03-local",
        "evi-04-sqlite-pv-candidate-loader",
        "evi-05-chat-lineage",
        "evi-06-discussion",
        "evi-07-analysis",
        "evi-08-plan",
        "evi-09-docs",
        "evi-10-data-excel",
        "evi-11-ppt",
        "evi-12-pdf-ocr",
        "evi-13-images-ocr",
        "evi-14-artifacts",
        "evi-15-custom",
        "evi-16-research",
        "evi-17-project-engulf",
        "evi-18-sqlite-brain",
        "evi-mode",
    ]
    assert LANE_REGISTRY["brain_loader"].display_label == ("SQLite PV Candidate Loader")
    assert resolve_lane_id("Brain Loader") == "brain_loader"
    try:
        resolve_lane_id("code")
    except LaneRegistryError as exc:
        assert "requires exactly one mode" in str(exc)
    else:
        raise AssertionError("/code resolved without a mode")
    assert resolve_lane_id("code", code_mode="local_code") == "local_code"
    assert route_source("package.json", code_mode="local_code") == "local_code"
    assert route_source("metrics.xlsx", code_mode="local_code") == "data_excel"
    routes = route_batch(
        ["docs/one.md", "docs/one.md", "src/app.py"],
        code_mode="local_code",
    )
    assert routes == {
        "docs/one.md": "docs",
        "src/app.py": "local_code",
    }
    assert len(routes) == len(set(routes))


def test_registry_rejects_duplicate_ids_and_ambiguous_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = lanes_module._DEFINITIONS
    monkeypatch.setattr(lanes_module, "_DEFINITIONS", (*original, original[0]))
    try:
        lanes_module._validate()
    except LaneRegistryError as exc:
        assert "Duplicate lane ID" in str(exc)
    else:
        raise AssertionError("Duplicate canonical lane ID was accepted")

    duplicate_alias = replace(
        original[0],
        canonical_lane_id="ambiguous_fixture",
        aliases=("docs",),
        command="ambiguous-fixture",
        fts_table="ambiguous_fixture_fts",
    )
    monkeypatch.setattr(
        lanes_module,
        "_DEFINITIONS",
        (*original, duplicate_alias),
    )
    try:
        lanes_module._validate()
    except LaneRegistryError as exc:
        assert "Ambiguous lane aliases" in str(exc)
    else:
        raise AssertionError("Ambiguous non-/code alias was accepted")


def test_all_eighteen_lanes_emit_full_contract_and_fixture_facts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "universal-source"
    repository.mkdir()
    (repository / "github.py").write_text(
        "import json\n\ndef remote_symbol():\n    return json.dumps({'remote': True})\n",
        encoding="utf-8",
    )
    (repository / "local.py").write_text(
        "from pathlib import Path\n\ndef local_symbol():\n    return Path('.')\n",
        encoding="utf-8",
    )
    (repository / "chat.json").write_text(
        json.dumps(
            [
                {
                    "prompt": "Preserve the accepted pointer.",
                    "response": "Pointer remains unchanged pending APPROVE.",
                },
                {
                    "prompt": "Refresh this lane.",
                    "response": "Only changed evidence is rebuilt.",
                },
            ]
        ),
        encoding="utf-8",
    )
    (repository / "discussion.md").write_text(
        "Decision: preserve the current pointer\n"
        "Delta: add deterministic lane facts\n"
        "Next action: run fixture validation\n"
        "Gate: explicit APPROVE only\n",
        encoding="utf-8",
    )
    (repository / "analysis.md").write_text(
        "Claim: refresh is incremental\n"
        "Evidence: unchanged lane bytes are reused\n"
        "Risk: optional OCR may be unavailable\n"
        "Open question: external ChatGPT connection\n",
        encoding="utf-8",
    )
    (repository / "plan.md").write_text(
        "Phase: extraction\n"
        "Milestone: eighteen lane proof\n"
        "Task: run deterministic retrieval\n"
        "Owner: one writer\n"
        "Acceptance criteria: recursive seal passes\n",
        encoding="utf-8",
    )
    (repository / "mode.md").write_text(
        "Scope: F-drive authority only\n"
        "Rule: one writer\n"
        "Gate: explicit HIL decision\n"
        "Blocked: remote push\n",
        encoding="utf-8",
    )
    _write_docx(repository / "document.docx")
    _write_xlsx(repository / "metrics.xlsx")
    (repository / "metrics.csv").write_text(
        "name,value\nalpha,1\nbeta,2\n", encoding="utf-8"
    )
    (repository / "records.json").write_text(
        '[{"name":"alpha","value":1},{"name":"beta","value":2}]',
        encoding="utf-8",
    )
    parquet_available = _write_parquet_or_blocker(repository / "records.parquet")
    _write_pptx(repository / "slides.pptx")
    _write_pdf(repository / "evidence.pdf")
    scanned_pdf = _write_scanned_pdf(repository / "scan.pdf")
    (repository / "image.png").write_bytes(_ocr_image_bytes())
    (repository / "artifact.json").write_text(
        '{"artifact":"receipt","status":"candidate"}', encoding="utf-8"
    )
    (repository / "custom.bin").write_bytes(b"\x00\x01custom")
    _write_sqlite(repository / "brain-loader.sqlite")
    _write_brain_archive(repository / "brain-package.zip")
    (repository / "research.md").write_text(
        "Question: does Refresh preserve unchanged bytes?\n"
        "Method: compare recursive hashes\n"
        "Finding: unchanged lanes are byte-reused\n"
        "Limitation: external parity still needs a live host\n",
        encoding="utf-8",
    )
    _write_project_archive(repository / "project.zip")
    _write_sqlite(repository / "sqlite-brain.sqlite")

    overrides = {
        "github.py": "github_code",
        "local.py": "local_code",
        "chat.json": "chat_lineage",
        "discussion.md": "discussion",
        "analysis.md": "analysis",
        "plan.md": "plan",
        "mode.md": "mode",
        "document.docx": "docs",
        "metrics.xlsx": "data_excel",
        "metrics.csv": "data_excel",
        "records.json": "data_excel",
        "records.parquet": "data_excel",
        "slides.pptx": "ppt",
        "evidence.pdf": "pdf_ocr",
        "scan.pdf": "pdf_ocr",
        "image.png": "images_ocr",
        "artifact.json": "artifacts",
        "custom.bin": "custom",
        "brain-loader.sqlite": "brain_loader",
        "brain-package.zip": "brain_loader",
        "research.md": "research",
        "project.zip": "project_engulf",
        "sqlite-brain.sqlite": "sqlite_brain",
    }
    lanes_root = tmp_path / "universal-pv1"
    result = build_lane_bundle(
        repository_root=repository,
        output_directory=lanes_root,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV1",
        pointer_generation=0,
        source_overrides=overrides,
    )
    assert result["summary"]["full_build_lanes"] == list(CANONICAL_LANE_IDS)
    assert validate_lane_bundle(lanes_root)["valid"] is True

    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        lane_root = lanes_root / lane_id
        expected = {
            lane.sqlite_filename,
            lane.mmd_filename,
            lane.dot_filename,
            "lane_pointer.json",
            "tools.json",
            "refresh_receipt.json",
            "lane_manifest.json",
        }
        assert expected <= {item.name for item in lane_root.iterdir()}

    expected_facts = {
        "github_code": {"code_symbol", "code_import"},
        "local_code": {"code_symbol", "code_import"},
        "chat_lineage": {
            "prompt_raw_exact",
            "response_raw_visible_exact",
            "turn_commit",
            "lineage_head",
            "state_hash_chain",
        },
        "discussion": {
            "discussion_source",
            "discussion_decision",
            "discussion_delta",
            "discussion_hard_gate",
        },
        "analysis": {
            "analysis_source",
            "analysis_claim",
            "analysis_evidence",
            "analysis_risk",
        },
        "plan": {
            "plan_source",
            "plan_phase",
            "plan_milestone",
            "plan_task",
            "plan_acceptance_criteria",
        },
        "mode": {"mode_source", "mode_scope", "mode_rule", "mode_gate"},
        "docs": {
            "doc_file",
            "doc_structure",
            "doc_heading",
            "doc_table_extract",
            "doc_image_reference",
            "source_structure_signature",
        },
        "data_excel": {
            "data_source",
            "sheet_workbook",
            "sheet_range",
            "sheet_cell_sample",
            "sheet_formula",
            "csv_header",
            "csv_row_sample",
            "json_structure",
            "json_record_sample",
            "parquet_schema",
            "data_structure_signature",
        },
        "ppt": {
            "ppt_file",
            "ppt_slide",
            "ppt_shape",
            "ppt_text_block",
            "ppt_notes",
            "ppt_table",
            "ppt_image_reference",
            "ppt_slide_relationship",
            "ppt_structure_signature",
        },
        "pdf_ocr": {"pdf_file", "pdf_structure_signature"},
        "images_ocr": {"image_file"},
        "artifacts": {
            "project_artifact",
            "artifact_metadata",
            "artifact_text_extract",
        },
        "custom": {"custom_source", "custom_item"},
        "brain_loader": {
            "brain_loader_source",
            "brain_loader_database",
            "brain_loader_schema_object",
            "brain_loader_receipt",
            "brain_loader_package",
            "brain_loader_member",
        },
        "research": {
            "research_source",
            "research_question",
            "research_method",
            "research_finding",
            "research_limitation",
        },
        "project_engulf": {
            "project_engulf_source",
            "project_engulf_file",
            "project_engulf_component",
            "project_engulf_sector_target",
            "project_engulf_receipt",
        },
        "sqlite_brain": {
            "loaded_sqlite_brain_source",
            "loaded_sqlite_brain_database",
            "loaded_sqlite_brain_schema_object",
            "loaded_sqlite_brain_table_stat",
            "loaded_sqlite_brain_foreign_key",
            "loaded_sqlite_brain_fts_table",
            "loaded_sqlite_brain_integrity_result",
            "loaded_sqlite_brain_compatibility",
            "loaded_sqlite_brain_receipt",
        },
    }
    for lane_id, required in expected_facts.items():
        lane = LANE_REGISTRY[lane_id]
        database = lanes_root / lane_id / lane.sqlite_filename
        assert required <= _fact_kinds(database)

    data_database = (
        lanes_root / "data_excel" / LANE_REGISTRY["data_excel"].sqlite_filename
    )
    parquet_states = _parser_states(data_database)
    if parquet_available:
        assert "PARSED_PARQUET_PYARROW" in parquet_states
        assert "parquet_row_sample" in _fact_kinds(data_database)
    else:
        assert (
            "BLOCKED_PARQUET_TOOL_UNAVAILABLE_EXACT_BYTES_PRESERVED" in parquet_states
        )

    pdf_database = lanes_root / "pdf_ocr" / LANE_REGISTRY["pdf_ocr"].sqlite_filename
    pdf_kinds = _fact_kinds(pdf_database)
    pdf_states = _parser_states(pdf_database)
    assert "pdf_page" in pdf_kinds or "pdf_review_region" in pdf_kinds
    assert any(
        state.startswith(("PARSED_", "BLOCKED_", "PARSE_FAILED_"))
        for state in pdf_states
    )
    if importlib.util.find_spec("fitz") is not None:
        assert "pdf_page" in pdf_kinds
        assert "PARSED_PYMUPDF" in pdf_states
    if (
        scanned_pdf
        and importlib.util.find_spec("rapidocr") is not None
        and importlib.util.find_spec("onnxruntime") is not None
    ):
        assert "PARSED_OCR_LOCAL" in pdf_states
        assert {"pdf_ocr_run", "pdf_ocr_block", "pdf_ocr_line"} <= pdf_kinds

    image_database = (
        lanes_root / "images_ocr" / LANE_REGISTRY["images_ocr"].sqlite_filename
    )
    image_kinds = _fact_kinds(image_database)
    assert {"image_metadata", "image_ocr_run", "image_review_region"} & image_kinds
    if (
        importlib.util.find_spec("rapidocr") is not None
        and importlib.util.find_spec("onnxruntime") is not None
    ):
        assert "PARSED_OCR_LOCAL" in _parser_states(image_database)
        assert {"image_ocr_run", "image_ocr_block", "image_ocr_line"} <= image_kinds
        tools = json.loads(
            (lanes_root / "images_ocr" / "tools.json").read_text(encoding="utf-8")
        )
        capability_states = {
            row["capability"]: row["state"] for row in tools["capabilities"]
        }
        assert capability_states["ocr_rapidocr"] == "ACTIVE"
        assert capability_states["ocr_onnxruntime"] == "ACTIVE"

    docs_database = lanes_root / "docs" / LANE_REGISTRY["docs"].sqlite_filename
    first_bm25, first_tfidf = _retrieval_rows(
        docs_database,
        LANE_REGISTRY["docs"].fts_table,
        "evidence",
    )
    second_bm25, second_tfidf = _retrieval_rows(
        docs_database,
        LANE_REGISTRY["docs"].fts_table,
        "evidence",
    )
    assert first_bm25 and first_tfidf
    assert first_bm25 == second_bm25
    assert first_tfidf == second_tfidf


def test_pv1_full_build_and_pvn_incremental_lane_reuse(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    (repository / "src").mkdir(parents=True)
    (repository / "docs").mkdir()
    (repository / "src" / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (repository / "package.json").write_text(
        '{"dependencies":{"flask-proxy":"1.0.0"}}\n', encoding="utf-8"
    )
    (repository / "docs" / "guide.md").write_text(
        "# Guide\nEvidence lane deterministic retrieval.\n", encoding="utf-8"
    )
    (repository / "misc.bin").write_bytes(b"\x00\x01\x02")
    (repository / "notes.txt").write_text(
        "A routed discussion note.\n", encoding="utf-8"
    )
    _write_xlsx(repository / "metrics.xlsx")

    pv1 = tmp_path / "pv1-lanes"
    first = build_lane_bundle(
        repository_root=repository,
        output_directory=pv1,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV1",
        pointer_generation=0,
        source_overrides={"notes.txt": "discussion"},
    )
    assert first["summary"]["full_build_lanes"] == list(CANONICAL_LANE_IDS)
    assert validate_lane_bundle(pv1)["valid"] is True
    routes = json.loads((pv1 / "routes.json").read_text(encoding="utf-8"))
    assert routes["routes"]["notes.txt"] == "discussion"

    code_db = pv1 / "local_code" / "local_code_sector_v001.sqlite"
    data_db = pv1 / "data_excel" / "data_excel_sector_v001.sqlite"
    assert _count(code_db, "code_symbol") >= 1
    assert _count(code_db, "code_import") >= 1
    assert _count(code_db, "code_route") >= 1
    assert _count(code_db, "code_dependency") >= 1
    assert _count(code_db, "tfidf_term") >= 1
    assert _count(data_db, "sheet_workbook") == 1
    assert _count(data_db, "sheet_formula") == 1
    assert _count(data_db, "sheet_formula_dependency_edge") >= 1
    assert _count(data_db, "sheet_table") == 1
    assert _count(data_db, "sheet_chart_metadata") == 1

    pv2 = tmp_path / "pv2-lanes"
    second = build_lane_bundle(
        repository_root=repository,
        output_directory=pv2,
        code_mode="local_code",
        parent_lane_bundle=pv1,
        parent_pv="PV1",
        proposed_pv="PV2",
        pointer_generation=1,
    )
    assert second["summary"]["byte_reused_lanes"] == list(CANONICAL_LANE_IDS)
    assert sha256_file(code_db) == sha256_file(
        pv2 / "local_code" / "local_code_sector_v001.sqlite"
    )
    assert sha256_file(data_db) == sha256_file(
        pv2 / "data_excel" / "data_excel_sector_v001.sqlite"
    )
    inherited_routes = json.loads((pv2 / "routes.json").read_text(encoding="utf-8"))
    assert inherited_routes["routes"]["notes.txt"] == "discussion"
    assert inherited_routes["inherited_route_count"] == len(inherited_routes["routes"])

    (repository / "docs" / "guide.md").write_text(
        "# Guide\nOnly this document changed during Refresh.\n", encoding="utf-8"
    )
    (repository / "misc.bin").unlink()
    (repository / "notes.txt").write_text(
        "This routed discussion note changed.\n", encoding="utf-8"
    )
    pv3 = tmp_path / "pv3-lanes"
    third = build_lane_bundle(
        repository_root=repository,
        output_directory=pv3,
        code_mode="local_code",
        parent_lane_bundle=pv2,
        parent_pv="PV2",
        proposed_pv="PV3",
        pointer_generation=2,
    )
    assert third["summary"]["incremental_lanes"] == [
        "discussion",
        "docs",
        "custom",
    ]
    assert "local_code" in third["summary"]["byte_reused_lanes"]
    assert sha256_file(
        pv2 / "local_code" / "local_code_sector_v001.sqlite"
    ) == sha256_file(pv3 / "local_code" / "local_code_sector_v001.sqlite")
    assert _count(pv3 / "custom" / "custom_sector_v001.sqlite", "source_tombstone") == 1
    assert validate_lane_bundle(pv3)["valid"] is True

    topology = pv3 / "project_lane_topology.mmd"
    topology.write_text(
        topology.read_text(encoding="utf-8") + "%% tampered\n",
        encoding="utf-8",
    )
    tampered = validate_lane_bundle(pv3)
    assert tampered["valid"] is False
    assert "project_lane_topology.mmd" in tampered["checksum_mismatches"]
