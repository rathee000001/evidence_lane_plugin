from __future__ import annotations

import base64
import importlib.util
import io
import json
import math
import re
import shutil
import sqlite3
import threading
import zipfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

import evidence_lane_plugin.lane_engine as lane_engine_module
import evidence_lane_plugin.lanes as lanes_module
import pytest
from evidence_lane_plugin.compact_storage import decompress_exact_bytes
from evidence_lane_plugin.forensic_audit import (
    audit_lane_bundle,
    write_forensic_audit_reports,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file
from evidence_lane_plugin.lane_engine import (
    LEGACY_LANE_BUNDLE_SCHEMA,
    build_lane_bundle,
    validate_lane_bundle,
)
from evidence_lane_plugin.lanes import (
    CANONICAL_LANE_IDS,
    CORE_SCHEMA_TABLES,
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


def test_topology_labels_redact_secret_shaped_values() -> None:
    secret = "api_" + "key=" + ("s" * 24)
    rendered = lane_engine_module._topology_text(f"connector {secret}")
    assert secret not in rendered
    assert "[REDACTED]" in rendered


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
    if importlib.util.find_spec("PIL") is None:
        _write_pdf(path)
        return False
    from PIL import Image

    with Image.open(io.BytesIO(_ocr_image_bytes())) as image:
        image.convert("RGB").save(path, format="PDF", resolution=150)
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


def _write_legacy_inline_lane_database(database: Path, lane_id: str) -> None:
    lane = LANE_REGISTRY[lane_id]
    data = f"legacy evidence for {lane_id}\n".encode()
    digest = sha256_bytes(data)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            f"""
            CREATE TABLE lane_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE lane_pointer(
                pointer_kind TEXT PRIMARY KEY,pointer_value TEXT,
                generation INTEGER NOT NULL,recorded_at TEXT NOT NULL
            );
            CREATE TABLE source_registry(
                source_id INTEGER PRIMARY KEY,path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,
                mime_type TEXT NOT NULL,extension TEXT NOT NULL,encoding TEXT,
                parser_state TEXT NOT NULL,exact_bytes BLOB NOT NULL,
                registered_at TEXT NOT NULL
            );
            CREATE TABLE chunk_index(
                chunk_id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL,
                locator TEXT NOT NULL,ordinal INTEGER NOT NULL,
                char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,
                text_content TEXT NOT NULL,sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE chunk_content_cas(
                sha256 TEXT PRIMARY KEY,size_bytes INTEGER NOT NULL,
                text_content TEXT NOT NULL,first_seen_at TEXT NOT NULL
            );
            CREATE TABLE chunk_history(
                history_id INTEGER PRIMARY KEY,source_path TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,locator TEXT NOT NULL,
                ordinal INTEGER NOT NULL,chunk_sha256 TEXT NOT NULL,
                snapshot_ref TEXT NOT NULL,observed_at TEXT NOT NULL,
                content_reused INTEGER NOT NULL
            );
            CREATE TABLE structured_fact(
                fact_id INTEGER PRIMARY KEY,source_id INTEGER,
                kind TEXT NOT NULL,locator TEXT NOT NULL,payload_json TEXT NOT NULL
            );
            CREATE TABLE parser_capability(
                capability TEXT PRIMARY KEY,state TEXT NOT NULL,
                tool TEXT NOT NULL,detail TEXT NOT NULL
            );
            CREATE TABLE refresh_receipt(
                receipt_id INTEGER PRIMARY KEY,build_mode TEXT NOT NULL,
                parent_pv TEXT,proposed_pv TEXT NOT NULL,
                unchanged_reuse INTEGER NOT NULL,changed_rebuild INTEGER NOT NULL,
                new_register INTEGER NOT NULL,removed_tombstone INTEGER NOT NULL,
                blocked_unsupported INTEGER NOT NULL,details_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE mutation_receipt(
                mutation_id INTEGER PRIMARY KEY,mutation_kind TEXT NOT NULL,
                source_path TEXT,prior_sha256 TEXT,current_sha256 TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE {lane.fts_table} USING fts5(
                path,locator,text_content,chunk_id
            );
            """
        )
        connection.execute(
            "INSERT INTO lane_meta VALUES('lane_id',?)", (lane_id,)
        )
        connection.execute(
            "INSERT INTO lane_meta VALUES('schema_version','legacy-inline-v1')"
        )
        connection.execute(
            "INSERT INTO lane_pointer VALUES('entered_from','PV12',12,'2026-09-01T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO source_registry VALUES(1,?,?,?,?,?,?,?,?,?)",
            (
                f"legacy/{lane_id}.txt",
                len(data),
                digest,
                "text/plain",
                ".txt",
                "utf-8",
                "PARSED_TEXT",
                data,
                "2026-09-01T00:00:00Z",
            ),
        )
        text = data.decode()
        connection.execute(
            "INSERT INTO chunk_index VALUES(1,1,?,0,0,?,?,?,?)",
            (
                f"legacy/{lane_id}.txt#L1",
                len(text),
                text,
                digest,
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO chunk_content_cas VALUES(?,?,?,?)",
            (digest, len(data), text, "2026-09-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO chunk_history VALUES(1,?,?,?,?,?,?,?,0)",
            (
                f"legacy/{lane_id}.txt",
                digest,
                f"legacy/{lane_id}.txt#L1",
                0,
                digest,
                "PV12",
                "2026-09-01T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO refresh_receipt VALUES(1,'FULL_PV','PV11','PV12',0,0,1,0,0,'{}','2026-09-01T00:00:00Z')"
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("lane_id", CANONICAL_LANE_IDS)
def test_legacy_inline_lane_migrates_losslessly_to_current_cas(
    tmp_path: Path,
    lane_id: str,
) -> None:
    source = tmp_path / f"{lane_id}-legacy.sqlite"
    target = tmp_path / f"{lane_id}-current.sqlite"
    _write_legacy_inline_lane_database(source, lane_id)

    before = lane_engine_module._validate_lane_database(
        source, LANE_REGISTRY[lane_id]
    )
    assert before["migration_eligible"] is True
    assert before["valid"] is False

    receipt = lane_engine_module._migrate_legacy_inline_lane_database(
        source,
        target,
        LANE_REGISTRY[lane_id],
        recorded_at="2026-09-01T00:00:00Z",
    )

    assert receipt["status"] == "PASS"
    assert receipt["source_database_mutated"] is False
    assert sha256_file(source) == receipt["source_database_sha256"]
    after = lane_engine_module._validate_lane_database(
        target, LANE_REGISTRY[lane_id]
    )
    assert after["valid"] is True
    connection = sqlite3.connect(target)
    try:
        source_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(source_registry)")
        }
        chunk_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(chunk_index)")
        }
        assert "exact_bytes" not in source_columns
        assert "text_content" not in chunk_columns
        assert connection.execute(
            "SELECT COUNT(*) FROM source_content_cas"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM chunk_content_cas"
        ).fetchone()[0] == 1
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
        raw_bm25 = connection.execute(
            f"""
            SELECT c.chunk_id, s.path, c.locator, c.sha256, cas.size_bytes,
                   cas.compression, cas.compressed_text, bm25({fts_table})
            FROM {fts_table} AS f
            JOIN chunk_index AS c ON c.chunk_id=f.rowid
            JOIN source_registry AS s ON s.source_id=c.source_id
            JOIN chunk_content_cas AS cas ON cas.sha256=c.sha256
            WHERE {fts_table} MATCH ?
            ORDER BY bm25({fts_table}), s.path, c.locator, c.chunk_id
            """,
            (term,),
        ).fetchall()
        candidates = []
        for row in raw_bm25:
            text = decompress_exact_bytes(
                compression=str(row[5]),
                payload=bytes(row[6]),
                expected_size=int(row[4]),
                expected_sha256=str(row[3]),
            ).decode("utf-8")
            candidates.append((row, Counter(token.lower() for token in re.findall(r"[\w.-]+", text))))
        query_term = term.lower()
        document_count = len(candidates)
        document_frequency = sum(counter[query_term] > 0 for _row, counter in candidates)
        tfidf = sorted(
            [
                (
                    int(row[0]),
                    str(row[1]),
                    str(row[2]),
                    (counter[query_term] / max(sum(counter.values()), 1))
                    * (
                        math.log(
                            (1 + document_count) / (1 + document_frequency)
                        )
                        + 1.0
                    ),
                )
                for row, counter in candidates
                if counter[query_term]
            ],
            key=lambda value: (-float(value[3]), value[1], value[2], value[0]),
        )
        bm25 = [
            (int(row[0]), str(row[1]), str(row[2]), float(row[7]))
            for row in raw_bm25
        ]
        return bm25, tfidf
    finally:
        connection.close()


def test_registry_requires_explicit_code_mode() -> None:
    assert len(CANONICAL_LANE_IDS) == 18
    ordered_commands = sorted(lane.command for lane in LANE_REGISTRY.values())
    assert ordered_commands == [
        "evi-mode",
        "evi-source-intake --lane analysis",
        "evi-source-intake --lane artifacts",
        "evi-source-intake --lane brain_loader",
        "evi-source-intake --lane chat_lineage",
        "evi-source-intake --lane custom",
        "evi-source-intake --lane data_excel",
        "evi-source-intake --lane discussion",
        "evi-source-intake --lane docs",
        "evi-source-intake --lane github_code",
        "evi-source-intake --lane images_ocr",
        "evi-source-intake --lane local_code",
        "evi-source-intake --lane pdf_ocr",
        "evi-source-intake --lane plan",
        "evi-source-intake --lane ppt",
        "evi-source-intake --lane project_engulf",
        "evi-source-intake --lane research",
        "evi-source-intake --lane sqlite_brain",
    ]
    assert LANE_REGISTRY["brain_loader"].display_label == ("SQLite PV Candidate Loader")
    assert resolve_lane_id("Brain Loader") == "brain_loader"
    try:
        resolve_lane_id("code")
    except LaneRegistryError as exc:
        assert "requires exactly one code mode" in str(exc)
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
    expected_prewarm = ["llama-index-sentence-splitter"] + (
        ["rapidocr+onnxruntime"]
        if importlib.util.find_spec("rapidocr") is not None
        else []
    )
    assert (
        result["parallel_execution"]["prewarmed_dependencies"]
        == expected_prewarm
    )
    assert validate_lane_bundle(lanes_root)["valid"] is True

    modern_missing_execution = tmp_path / "modern-v2-missing-execution"
    shutil.copytree(lanes_root, modern_missing_execution)
    (modern_missing_execution / "execution_receipt.json").unlink()
    modern_members = {
        path.relative_to(modern_missing_execution).as_posix(): sha256_file(path)
        for path in sorted(modern_missing_execution.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    (modern_missing_execution / "SHA256SUMS.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": "evidence-lane.recursive-sha256.v1",
                "members": modern_members,
                "member_count": len(modern_members),
            }
        )
    )
    modern_manifest_path = modern_missing_execution / "manifest.json"
    modern_manifest = json.loads(modern_manifest_path.read_text(encoding="utf-8"))
    modern_manifest["bundle_sha256"] = sha256_bytes(
        canonical_json_bytes(modern_members)
    )
    modern_manifest["member_count"] = len(modern_members) + 2
    modern_manifest_path.write_bytes(canonical_json_bytes(modern_manifest))
    modern_validation = validate_lane_bundle(modern_missing_execution)
    assert modern_validation["valid"] is False
    assert modern_validation["parallel_execution_valid"] is False
    assert (
        modern_validation["parallel_execution_legacy_compatibility"] is False
    )

    pre_v110_root = tmp_path / "pre-v110-v2-compatibility"
    shutil.copytree(lanes_root, pre_v110_root)
    pre_v110_execution_path = pre_v110_root / "execution_receipt.json"
    pre_v110_execution = json.loads(
        pre_v110_execution_path.read_text(encoding="utf-8")
    )
    pre_v110_execution.pop("source_policy")
    pre_v110_execution_path.write_bytes(canonical_json_bytes(pre_v110_execution))
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        lane_root = pre_v110_root / lane_id
        (lane_root / lane.mmd_filename).write_text(
            "flowchart TB\n"
            f'    L["{lane.display_label}"]\n'
            '    L --> DB["SQLite"]\n',
            encoding="utf-8",
        )
        (lane_root / lane.dot_filename).write_text(
            "digraph lane {\n"
            f'  L [label="{lane.display_label}"];\n'
            '  DB [label="SQLite"];\n'
            "  L -> DB;\n"
            "}\n",
            encoding="utf-8",
        )
        lane_manifest_path = lane_root / "lane_manifest.json"
        lane_manifest = json.loads(
            lane_manifest_path.read_text(encoding="utf-8")
        )
        lane_manifest["stable_artifacts"] = {
            filename: sha256_file(lane_root / filename)
            for filename in (
                lane.sqlite_filename,
                lane.mmd_filename,
                lane.dot_filename,
                "tools.json",
            )
        }
        lane_manifest["evidence_artifacts"] = {
            filename: sha256_file(lane_root / filename)
            for filename in (
                lane.sqlite_filename,
                lane.mmd_filename,
                lane.dot_filename,
                "tools.json",
                "lane_pointer.json",
                "refresh_receipt.json",
            )
        }
        lane_manifest_path.write_bytes(canonical_json_bytes(lane_manifest))
    (pre_v110_root / "project_lane_topology.mmd").write_text(
        'flowchart LR\n    P["Project"] --> L["Lanes"]\n',
        encoding="utf-8",
    )
    (pre_v110_root / "project_lane_topology.dot").write_text(
        'digraph project { P [label="Project"]; L [label="Lanes"]; P -> L; }\n',
        encoding="utf-8",
    )
    pre_v110_members = {
        path.relative_to(pre_v110_root).as_posix(): sha256_file(path)
        for path in sorted(pre_v110_root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    (pre_v110_root / "SHA256SUMS.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": "evidence-lane.recursive-sha256.v1",
                "members": pre_v110_members,
                "member_count": len(pre_v110_members),
            }
        )
    )
    pre_v110_manifest_path = pre_v110_root / "manifest.json"
    pre_v110_manifest = json.loads(
        pre_v110_manifest_path.read_text(encoding="utf-8")
    )
    pre_v110_manifest.pop("source_policy")
    pre_v110_manifest["parallel_execution"] = pre_v110_execution
    pre_v110_manifest["bundle_sha256"] = sha256_bytes(
        canonical_json_bytes(pre_v110_members)
    )
    pre_v110_manifest["member_count"] = len(pre_v110_members) + 2
    pre_v110_manifest_path.write_bytes(canonical_json_bytes(pre_v110_manifest))
    pre_v110_validation = validate_lane_bundle(pre_v110_root)
    assert pre_v110_validation["valid"] is True
    assert pre_v110_validation["pre_v110_compatibility"] is True
    assert pre_v110_validation["pre_v110_execution_valid"] is True
    assert pre_v110_validation["source_policy_enforced"] is False
    assert pre_v110_validation["topology_reconciliation_enforced"] is False
    assert pre_v110_validation["topology_reconciliation"]["status"] == "FAIL"

    transitional_v1_root = tmp_path / "pre-v110-v1-with-execution-compatibility"
    shutil.copytree(pre_v110_root, transitional_v1_root)
    transitional_manifest_path = transitional_v1_root / "manifest.json"
    transitional_manifest = json.loads(
        transitional_manifest_path.read_text(encoding="utf-8")
    )
    transitional_manifest["schema"] = LEGACY_LANE_BUNDLE_SCHEMA
    transitional_manifest_path.write_bytes(
        canonical_json_bytes(transitional_manifest)
    )
    transitional_validation = validate_lane_bundle(transitional_v1_root)
    assert transitional_validation["valid"] is True
    assert transitional_validation["pre_v110_compatibility"] is True
    assert transitional_validation["pre_v110_execution_valid"] is True
    assert transitional_validation["topology_reconciliation"]["status"] == "FAIL"

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
        lane_manifest = json.loads(
            (lane_root / "lane_manifest.json").read_text(encoding="utf-8")
        )
        assert lane_manifest["schema"] == "evidence-lane.lane-manifest.v3"
        assert set(lane_manifest["required_artifacts"]) == expected
        assert set(lane_manifest["evidence_artifacts"]) == expected - {
            "lane_manifest.json"
        }
        four_file_contract = lane_manifest["four_file_contract"]
        assert four_file_contract["schema"] == (
            "evidence-lane.lane-four-file-contract.v1"
        )
        assert four_file_contract["ordered_members"] == [
            lane.sqlite_filename,
            lane.mmd_filename,
            lane.dot_filename,
            "tools.json",
        ]
        assert len(four_file_contract["contract_sha256"]) == 64
        mermaid = (lane_root / lane.mmd_filename).read_text(encoding="utf-8")
        dot = (lane_root / lane.dot_filename).read_text(encoding="utf-8")
        for semantic_section in (
            "SOURCE_INTAKE",
            "SEMANTIC_MODEL",
            "SQLITE_PHYSICAL_SCHEMA",
            "RETRIEVAL",
            "LIFECYCLE",
            "OUTPUTS",
        ):
            assert f"subgraph {semantic_section}" in mermaid
            assert f"cluster_{semantic_section.lower()}" in dot
        assert lane.sqlite_filename in mermaid
        assert lane.mmd_filename in mermaid
        assert lane.dot_filename in mermaid
        assert "PHYSICAL_SCHEMA_SECTOR" in mermaid
        assert "projection_sha256=" in mermaid
        for table in lane.schema_contract:
            assert table in mermaid
            assert table in dot
        lane_specific_tables = {
            table
            for table in lane.schema_contract
            if table not in CORE_SCHEMA_TABLES
            and table != lane.fts_table
        }
        for table in lane_specific_tables:
            assert table in mermaid
        if lane_id in {"github_code", "local_code"}:
            assert "subgraph CODE_LOGICAL_TOPOLOGY" in mermaid
            assert "subgraph CODE_EVIDENCE_GRAPH" in mermaid
            assert "Code Sector" in mermaid
            for logical_table in (
                "code_repo",
                "git_commit",
                "code_file",
                "code_symbol",
                "app_route",
                "dependency_item",
                "project_artifact",
            ):
                assert logical_table in mermaid
            assert "git_commit_registry" in mermaid
            for obsolete_table in (
                "code_source_registry",
                "code_file_snapshot",
                "code_chunk",
                "code_semantic_diff",
                "code_snapshot_history",
                "git_patch_hunk",
                "git_exact_line_change",
                "git_push_event",
                    "git_artifact_impact",
                ):
                    assert obsolete_table not in lane.schema_contract
                    assert f'"{obsolete_table}<br/>' not in mermaid
            if lane_id == "github_code":
                assert "subgraph GITHUB_REPOSITORY_GRAPH" in mermaid
                assert "GITHUB_GRAPH_ROOT" in mermaid
                assert "LOCAL_WORKTREE_GRAPH" not in mermaid
                assert "NO_GIT_HISTORY_LOADED" not in mermaid
            else:
                assert "subgraph LOCAL_WORKTREE_GRAPH" in mermaid
                assert "NO_GIT_HISTORY_LOADED" in mermaid
                assert "GITHUB_REPOSITORY_GRAPH" not in mermaid
                assert "COMMIT_BOUNDARY" not in mermaid
        else:
            assert "subgraph SCHEMA_DERIVED_TOPOLOGY" in mermaid
            assert "cluster_schema_derived_topology" in dot
            assert "SCHEMA_SECTOR" in mermaid
            assert "schema sector" in mermaid
        tools = json.loads((lane_root / "tools.json").read_text(encoding="utf-8"))
        conditioned = tools["source_conditioned_toolchain"]
        assert "selected_tools" not in conditioned
        assert "selected_tool_count" not in conditioned
        assert conditioned["eligible_tool_count"] == len(
            conditioned["eligible_tools"]
        )
        conditioned_ordered_tools = [row["tool"] for row in conditioned["rows"]]
        runtime_resolution = tools["runtime_toolchain_resolution"]
        assert conditioned["eligible_tools"] == conditioned_ordered_tools
        assert runtime_resolution["ordered_tools"] == conditioned_ordered_tools
        assert runtime_resolution["ordered_tool_count"] == len(
            conditioned_ordered_tools
        )
        if lane_id == "local_code":
            assert runtime_resolution["ordered_tools"] == [
                "Pydantic",
                "SQLite_CAS",
                "OpenAI_Agents_SDK",
                "hashlib_pathlib",
                "Secret_redactor",
                "Git",
                "Python",
                "NodeJS_TypeScript",
                "GitPython",
                "PyGithub",
                "TreeSitter_LanguagePack",
                "Python_structural_parser",
                "GitHub_MCP_Server",
                "Filesystem_MCP_Server",
                "APSW_SQLite_engine",
                "LlamaIndex_SQLite_indexer",
                "SQLite_FTS5_BM25",
                "sqlite_vec",
                "rank_bm25",
                "SentenceTransformers",
                "FAISS_CPU",
                "Pinecone",
                "Weaviate",
                "Milvus",
                "OpenSearch",
                "deterministic_TFIDF",
                "LangGraph_Mermaid_engine",
                "rustworkx",
                "Python_Graphviz_DOT_engine",
                "Graphviz_dot",
                "Mermaid_CLI_mmdc",
                "FastMCP",
                "MCP_Python_SDK",
                "LangSmith",
                "OpenTelemetry",
                "Langfuse",
                "Docker",
                "Kubernetes",
                "pytest",
                "Ruff",
                "MyPy",
            ]
            assert runtime_resolution["lane_action_ordered_tool_count"] == 32
        assert runtime_resolution["lane_action_ordered_tool_count"] == len(
            runtime_resolution["lane_action_ordered_tools"]
        )
        assert set(runtime_resolution["lane_action_ordered_tools"]) <= set(
            conditioned_ordered_tools
        )
        assert runtime_resolution["availability_scope"] == "LANE_ACTION_ORDERED_TOOLS"
        assert runtime_resolution["lane_action_resolution_sha256"] == (
            runtime_resolution["resolution_sha256"]
        )
        assert runtime_resolution["runnable_tools"] == [
            tool
            for tool in runtime_resolution["lane_action_ordered_tools"]
            if tool in runtime_resolution["runnable_tools"]
        ]
        assert runtime_resolution["unavailable_tools"] == [
            tool
            for tool in runtime_resolution["lane_action_ordered_tools"]
            if tool in runtime_resolution["unavailable_tools"]
        ]
        assert set(runtime_resolution["runnable_tools"]) | set(
            runtime_resolution["unavailable_tools"]
        ) == set(runtime_resolution["lane_action_ordered_tools"])
        execution = tools["tool_execution_evidence"]
        assert execution["status"] == "PASS"
        assert execution[
            "all_condition_true_tools_executed_or_failed_visible"
        ] is True
        assert execution["presence_or_eligibility_is_execution_proof"] is False
        assert execution["eligible_tool_count"] == len(execution["rows"])
        assert [
            row["orchestration_order"] for row in execution["rows"]
        ] == list(range(1, execution["eligible_tool_count"] + 1))
        assert execution["all_rows_in_exact_contract_order"] is True
        assert [
            row["orchestration_order"]
            for row in execution["selected_execution_sequence"]
        ] == sorted(
            row["orchestration_order"]
            for row in execution["selected_execution_sequence"]
        )
        assert execution["eligible_tool_count"] == (
            execution["condition_true_tool_count"]
            + execution["condition_false_tool_count"]
        )
        for tool_row in execution["rows"]:
            assert tool_row["eligibility_state"] == "ELIGIBLE"
            if tool_row["condition_state"] == "CONDITION_TRUE":
                assert tool_row["selection_state"] == (
                    "SELECTED_FOR_CURRENT_ACTION_PHASE"
                )
                assert tool_row["execution_state"] == "EXECUTED" or str(
                    tool_row["execution_state"]
                ).startswith("BLOCKED_")
            else:
                assert tool_row["selection_state"] == "NOT_SELECTED"
                assert tool_row["execution_state"] == "NOT_EXECUTED"
            assert tool_row["network_call_performed"] is False
            assert tool_row["credential_value_read"] is False
        with sqlite3.connect(lane_root / lane.sqlite_filename) as lane_connection:
            source_count = int(
                lane_connection.execute(
                    "SELECT COUNT(*) FROM source_registry"
                ).fetchone()[0]
            )
        if source_count == 0:
            execution_by_tool = {
                row["tool"]: row for row in execution["rows"]
            }
            for source_primitive in ("hashlib_pathlib", "SQLite_CAS"):
                if source_primitive in execution_by_tool:
                    assert execution_by_tool[source_primitive][
                        "condition_state"
                    ] == "CONDITION_FALSE"
                    assert execution_by_tool[source_primitive][
                        "execution_state"
                    ] == "NOT_EXECUTED"
        empty_tables = tools["empty_table_classification"]
        assert empty_tables["status"] == "PASS"
        assert empty_tables["defect_table_count"] == 0
        assert empty_tables["defect_tables"] == []
        assert tools["artifact_authority"]["schema"] == (
            "evidence-lane.tools-artifact-authority.v1"
        )
        assert tools["artifact_authority"]["ordered_members"] == [
            lane.sqlite_filename,
            lane.mmd_filename,
            lane.dot_filename,
        ]
        topology_generator = tools["topology_generator"]
        assert topology_generator["schema"] == (
            "evidence-lane.lane-topology-generator.v5"
        )
        assert len(topology_generator["sha256"]) == 64
        assert topology_generator["mmd_dot_shared_graph"] is True
        assert topology_generator[
            "sqlite_brain_builder_mmd_authority_sha256"
        ] == "1B87064906E8A805C4A69A7A3A14668DCCE963E00928ED3EB23CC186AB8A65EC"
        assert topology_generator[
            "sqlite_brain_builder_master_topology_authority_sha256"
        ] == "E9E610D982B5E855A54C39B7A16E06C6FD4D28A2538C8790CC2B9E34C9FECA01"
        graph_contract = topology_generator["graph_projection_contract"]
        assert graph_contract["implementation"] == (
            "PROJECT_AUTHORED_GRAPHIFY_INFORMED"
        )
        assert graph_contract["stable_node_identity"] is True
        assert graph_contract["stable_edge_identity"] is True
        assert graph_contract["network_or_llm_extraction"] is False
        assert graph_contract["profile"] == (
            "GITHUB_REPOSITORY_HISTORY"
            if lane_id == "github_code"
            else "LOCAL_WORKTREE"
            if lane_id == "local_code"
            else "SQLITE_SCHEMA_RELATION_SAMPLE"
        )
        assert topology_generator["physical_schema_projection_schema"] == (
            "evidence-lane.sqlite-physical-schema-projection.v1"
        )
        assert len(topology_generator["schema_topology_module_sha256"]) == 64
        assert len(topology_generator["topology_reconciliation_module_sha256"]) == 64

    project_topology = (lanes_root / "project_lane_topology.mmd").read_text(
        encoding="utf-8"
    )
    assert "subgraph CONTROL_PLANE" in project_topology
    assert "subgraph PARALLEL_LANES" in project_topology
    assert "bounded parallel compute" in project_topology
    assert "subgraph SERIAL_AUTHORITY" in project_topology
    assert "exact APPROVE" in project_topology
    assert "no implicit acceptance" in project_topology
    assert "user-requested fresh-host recovery only" in project_topology
    for lane_id in CANONICAL_LANE_IDS:
        assert LANE_REGISTRY[lane_id].display_label in project_topology

    legacy_root = tmp_path / "legacy-v1-lane-manifests"
    shutil.copytree(lanes_root, legacy_root)
    (legacy_root / "execution_receipt.json").unlink()
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        lane_root = legacy_root / lane_id
        (lane_root / lane.mmd_filename).write_text(
            "flowchart TB\n"
            f'    L["{lane.display_label}"]\n'
            '    L --> DB["SQLite"]\n',
            encoding="utf-8",
        )
        (lane_root / lane.dot_filename).write_text(
            "digraph lane {\n"
            f'  L [label="{lane.display_label}"];\n'
            '  DB [label="SQLite"];\n'
            "  L -> DB;\n"
            "}\n",
            encoding="utf-8",
        )
        lane_manifest_path = legacy_root / lane_id / "lane_manifest.json"
        legacy_manifest = json.loads(
            lane_manifest_path.read_text(encoding="utf-8")
        )
        legacy_manifest["schema"] = "evidence-lane.lane-manifest.v1"
        legacy_manifest.pop("evidence_artifacts")
        legacy_manifest.pop("required_artifacts")
        legacy_manifest["stable_artifacts"] = {
            filename: sha256_file(lane_root / filename)
            for filename in (
                lane.sqlite_filename,
                lane.mmd_filename,
                lane.dot_filename,
                "tools.json",
            )
        }
        lane_manifest_path.write_bytes(canonical_json_bytes(legacy_manifest))
    (legacy_root / "project_lane_topology.mmd").write_text(
        'flowchart LR\n    P["Project"] --> L["Lanes"]\n',
        encoding="utf-8",
    )
    (legacy_root / "project_lane_topology.dot").write_text(
        'digraph project { P [label="Project"]; L [label="Lanes"]; P -> L; }\n',
        encoding="utf-8",
    )
    members = {
        path.relative_to(legacy_root).as_posix(): sha256_file(path)
        for path in sorted(legacy_root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    (legacy_root / "SHA256SUMS.json").write_bytes(
        canonical_json_bytes(
            {
                "schema": "evidence-lane.recursive-sha256.v1",
                "members": members,
                "member_count": len(members),
            }
        )
    )
    legacy_bundle_manifest_path = legacy_root / "manifest.json"
    legacy_bundle_manifest = json.loads(
        legacy_bundle_manifest_path.read_text(encoding="utf-8")
    )
    legacy_bundle_manifest["schema"] = LEGACY_LANE_BUNDLE_SCHEMA
    legacy_bundle_manifest.pop("parallel_execution")
    legacy_bundle_manifest.pop("source_snapshot_sha256")
    legacy_bundle_manifest["summary"].pop("parallel_execution")
    legacy_bundle_manifest["bundle_sha256"] = sha256_bytes(
        canonical_json_bytes(members)
    )
    legacy_bundle_manifest["member_count"] = len(members) + 2
    legacy_bundle_manifest_path.write_bytes(
        canonical_json_bytes(legacy_bundle_manifest)
    )
    legacy_validation = validate_lane_bundle(legacy_root)
    assert legacy_validation["valid"] is True
    assert legacy_validation["parallel_execution_valid"] is True
    assert (
        legacy_validation["parallel_execution_legacy_compatibility"] is True
    )
    assert legacy_validation["topology_reconciliation_enforced"] is False
    assert legacy_validation["topology_reconciliation"]["status"] == "FAIL"

    forensic = audit_lane_bundle(
        lanes_root,
        subject="18-lane deterministic dummy-source stress audit",
    )
    assert forensic["status"] == "PASS"
    assert forensic["lane_count"] == 18
    assert [lane["lane_id"] for lane in forensic["lanes"]] == list(
        CANONICAL_LANE_IDS
    )
    assert all(lane["status"] == "PASS" for lane in forensic["lanes"])
    assert all(lane["verdict"] == "PURSUE" for lane in forensic["lanes"])
    assert all(
        lane["four_file_contract"]["status"] == "PASS"
        for lane in forensic["lanes"]
    )
    assert all(lane["sqlite"]["every_table_audited"] for lane in forensic["lanes"])
    reports_root = tmp_path / "forensic-reports"
    report_manifest = write_forensic_audit_reports(forensic, reports_root)
    assert report_manifest["lane_report_count"] == 18
    assert report_manifest["status"] == "PASS"
    assert len(list(reports_root.glob("*-forensic-audit.md"))) == 18
    assert (reports_root / "README.md").is_file()
    assert (reports_root / "forensic_audit.json").is_file()

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
        assert {
            "PARSED_DUCKDB_PARQUET_TO_SQLITE",
            "PARSED_POLARS_LAZY_PARQUET_TO_SQLITE",
            "PARSED_PARQUET_PYARROW",
        }.intersection(parquet_states)
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
    if importlib.util.find_spec("pypdf") is not None:
        assert "pdf_page" in pdf_kinds
        assert {
            "PARSED_PYPDF",
            "PARSED_PYMUPDF",
            "PARSED_OCR_LOCAL",
        } & set(pdf_states)
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


def test_llama_sentence_splitter_prewarm_materializes_lazy_punkt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llama_index.core.utils import globals_helper

    monkeypatch.setattr(globals_helper, "_punkt_tokenizer", None)
    monkeypatch.setattr(globals_helper, "_stopwords", None)

    assert (
        lane_engine_module.prewarm_llama_index_sentence_splitter()
        == "llama-index-sentence-splitter"
    )
    assert globals_helper._punkt_tokenizer is not None
    assert globals_helper._stopwords is not None


def test_lane_build_parallelizes_compute_and_serializes_canonical_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "guide.md").write_text(
        "# Parallel lane test\nOne writer, deterministic barrier.\n",
        encoding="utf-8",
    )
    original = lane_engine_module._build_one_lane
    original_prewarm = lane_engine_module.prewarm_llama_index_sentence_splitter
    first_two = threading.Barrier(2, timeout=10)
    tokenizer_ready = threading.Event()
    state_lock = threading.Lock()
    state = {"arrivals": 0, "prewarm_calls": 0}

    def observed_prewarm() -> str:
        assert threading.current_thread() is threading.main_thread()
        result = original_prewarm()
        with state_lock:
            state["prewarm_calls"] += 1
        tokenizer_ready.set()
        return result

    def observed_build(**kwargs):
        assert tokenizer_ready.is_set()
        with state_lock:
            state["arrivals"] += 1
            wait_at_barrier = state["arrivals"] <= 2
        if wait_at_barrier:
            first_two.wait()
        return original(**kwargs)

    monkeypatch.setattr(
        lane_engine_module,
        "prewarm_llama_index_sentence_splitter",
        observed_prewarm,
    )
    monkeypatch.setattr(lane_engine_module, "_build_one_lane", observed_build)
    output = tmp_path / "parallel-bundle"
    manifest = build_lane_bundle(
        repository_root=repository,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV-PARALLEL",
        pointer_generation=0,
        max_lane_workers=4,
    )

    execution = manifest["parallel_execution"]
    emitted = ["chat_lineage", "docs"]
    assert state["prewarm_calls"] == 1
    assert state["arrivals"] == len(emitted)
    assert first_two.broken is False
    assert execution["parallel_lane_compute"] is True
    assert execution["prewarmed_dependencies"] == [
        "llama-index-sentence-splitter"
    ]
    assert execution["worker_count"] == 2
    assert execution["barrier_status"] == "PASS"
    assert execution["source_snapshot_unchanged"] is True
    assert execution["source_binding"]["valid"] is True
    assert execution["deterministic_assembly_order"] == emitted
    assert [row["lane_id"] for row in manifest["reports"]] == emitted
    assert manifest["emitted_lane_ids"] == emitted
    assert not (output / "github_code").exists()
    assert not (output / "local_code").exists()
    assert validate_lane_bundle(output)["parallel_execution_valid"] is True


def test_one_shot_dummy_covers_every_non_git_lane_without_git_placeholder(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "one-shot-non-git-source"
    repository.mkdir()
    (repository / "local.py").write_text("def local():\n    return True\n", encoding="utf-8")
    (repository / "chat.json").write_text('{"turn":"visible"}\n', encoding="utf-8")
    for filename, body in (
        ("discussion.md", "Decision: retain the bounded correction.\n"),
        ("analysis.md", "Claim: the non-Git one-shot is deterministic.\n"),
        ("plan.md", "Task: test every loaded non-Git lane.\n"),
        ("mode.md", "Mode: governed correction.\n"),
        ("research.md", "Finding: absent lanes emit no placeholder.\n"),
    ):
        (repository / filename).write_text(body, encoding="utf-8")
    _write_docx(repository / "document.docx")
    _write_xlsx(repository / "metrics.xlsx")
    _write_pptx(repository / "slides.pptx")
    _write_pdf(repository / "evidence.pdf")
    (repository / "image.png").write_bytes(_ocr_image_bytes())
    (repository / "artifact.json").write_text(
        '{"artifact":"one-shot","status":"candidate"}\n',
        encoding="utf-8",
    )
    (repository / "custom.bin").write_bytes(b"\x00\x01non-git")
    _write_sqlite(repository / "brain-loader.sqlite")
    _write_project_archive(repository / "project.zip")
    _write_sqlite(repository / "sqlite-brain.sqlite")
    overrides = {
        "local.py": "local_code",
        "chat.json": "chat_lineage",
        "discussion.md": "discussion",
        "analysis.md": "analysis",
        "plan.md": "plan",
        "mode.md": "mode",
        "document.docx": "docs",
        "metrics.xlsx": "data_excel",
        "slides.pptx": "ppt",
        "evidence.pdf": "pdf_ocr",
        "image.png": "images_ocr",
        "artifact.json": "artifacts",
        "custom.bin": "custom",
        "brain-loader.sqlite": "brain_loader",
        "research.md": "research",
        "project.zip": "project_engulf",
        "sqlite-brain.sqlite": "sqlite_brain",
    }
    output = tmp_path / "one-shot-non-git-pv"
    manifest = build_lane_bundle(
        repository_root=repository,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV-NON-GIT-ONE-SHOT",
        pointer_generation=0,
        source_overrides=overrides,
    )

    expected = [lane_id for lane_id in CANONICAL_LANE_IDS if lane_id != "github_code"]
    assert manifest["emitted_lane_ids"] == expected
    assert manifest["omitted_lane_ids"] == ["github_code"]
    assert manifest["lane_emission_policy"] == "LOADED_OR_DETECTED_ONLY"
    assert all((output / lane_id).is_dir() for lane_id in expected)
    assert not (output / "github_code").exists()
    validation = validate_lane_bundle(output)
    assert validation["valid"] is True
    assert validation["lane_directory_set_valid"] is True
    assert validation["actual_lane_directory_ids"] == expected


def test_lane_build_fails_closed_when_source_snapshot_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "guide.md").write_text("# Initial source\n", encoding="utf-8")
    original = lane_engine_module._build_one_lane
    state_lock = threading.Lock()
    state = {"mutated": False}

    def mutating_build(**kwargs):
        result = original(**kwargs)
        with state_lock:
            if not state["mutated"]:
                (repository / "mid-build-steer.md").write_text(
                    "This source arrived during the lane barrier.\n",
                    encoding="utf-8",
                )
                state["mutated"] = True
        return result

    monkeypatch.setattr(lane_engine_module, "_build_one_lane", mutating_build)
    output = tmp_path / "changed-source-bundle"
    with pytest.raises(ValueError, match="source snapshot changed"):
        build_lane_bundle(
            repository_root=repository,
            output_directory=output,
            code_mode="local_code",
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV-CHANGED",
            pointer_generation=0,
            max_lane_workers=4,
        )
    assert not (output / "manifest.json").exists()


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
    emitted = [
        "local_code",
        "chat_lineage",
        "discussion",
        "docs",
        "data_excel",
        "custom",
    ]
    assert first["summary"]["full_build_lanes"] == emitted
    assert validate_lane_bundle(pv1)["valid"] is True
    routes = json.loads((pv1 / "routes.json").read_text(encoding="utf-8"))
    assert routes["routes"]["notes.txt"] == "discussion"

    code_db = pv1 / "local_code" / "local_code_sector_v001.sqlite"
    data_db = pv1 / "data_excel" / "data_excel_sector_v001.sqlite"
    assert _count(code_db, "code_symbol") >= 1
    assert _count(code_db, "code_import") >= 1
    assert _count(code_db, "code_route") >= 1
    assert _count(code_db, "code_dependency") >= 1
    assert _count(code_db, "tfidf_term") == 0
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
    assert second["summary"]["byte_reused_lanes"] == emitted
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
    ]
    assert "local_code" in third["summary"]["byte_reused_lanes"]
    assert sha256_file(
        pv2 / "local_code" / "local_code_sector_v001.sqlite"
    ) == sha256_file(pv3 / "local_code" / "local_code_sector_v001.sqlite")
    assert third["summary"]["removed_lane_ids"] == ["custom"]
    assert "custom" in third["omitted_lane_ids"]
    assert not (pv3 / "custom").exists()
    assert validate_lane_bundle(pv3)["valid"] is True

    topology = pv3 / "project_lane_topology.mmd"
    topology.write_text(
        topology.read_text(encoding="utf-8") + "%% tampered\n",
        encoding="utf-8",
    )
    tampered = validate_lane_bundle(pv3)
    assert tampered["valid"] is False
    assert "project_lane_topology.mmd" in tampered["checksum_mismatches"]


def test_invalid_parent_missing_topology_generator_fails_before_output(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "app.py").write_text(
        "def main():\n    return 'exact installed topology runtime'\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent-lanes"
    build_lane_bundle(
        repository_root=repository,
        output_directory=parent,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV1",
        pointer_generation=0,
    )

    # A modern sealed parent cannot be edited into a historical compatibility
    # fixture. Missing generator identity is rejected before candidate output.
    tools_path = parent / "local_code" / "tools.json"
    tools = json.loads(tools_path.read_text(encoding="utf-8"))
    tools.pop("topology_generator")
    tools_path.write_text(
        json.dumps(tools, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    candidate = tmp_path / "candidate-lanes"
    with pytest.raises(ValueError, match="parent lane bundle"):
        build_lane_bundle(
            repository_root=repository,
            output_directory=candidate,
            code_mode="local_code",
            parent_lane_bundle=parent,
            parent_pv="PV1",
            proposed_pv="PV2",
            pointer_generation=1,
        )
    assert not candidate.exists()


def test_unreconciled_parent_topology_fails_before_output(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    (repository / "notes.txt").write_text(
        "Stable governed discussion source.\n", encoding="utf-8"
    )

    pv1 = tmp_path / "pv1-lanes"
    build_lane_bundle(
        repository_root=repository,
        output_directory=pv1,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=None,
        proposed_pv="PV1",
        pointer_generation=0,
        source_overrides={"notes.txt": "discussion"},
    )
    assert validate_lane_bundle(pv1)["valid"] is True

    discussion = pv1 / "discussion"
    (discussion / "discussion.mmd").write_text(
        "flowchart TD\n    L[Lane] --> db[(SQLite)]\n",
        encoding="utf-8",
    )
    (discussion / "discussion.dot").write_text(
        "digraph lane { lane -> db; }\n",
        encoding="utf-8",
    )

    pv2 = tmp_path / "pv2-lanes"
    with pytest.raises(ValueError, match="parent lane bundle"):
        build_lane_bundle(
            repository_root=repository,
            output_directory=pv2,
            code_mode="local_code",
            parent_lane_bundle=pv1,
            parent_pv="PV1",
            proposed_pv="PV2",
            pointer_generation=1,
        )
    assert not pv2.exists()


def test_tool_identity_fallback_replays_parent_and_preserves_unavailable_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    historical = repository / "historical.txt"
    historical.write_text(
        "Accepted historical route evidence must remain queryable.\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent-lanes"
    current_tool_identity = lane_engine_module._tool_identity

    def historical_tool_identity(lane, *, source_paths=()):
        payload = current_tool_identity(lane, source_paths=source_paths)
        if lane.canonical_lane_id != "discussion":
            return payload
        payload = json.loads(json.dumps(payload))
        payload["parser_implementation"]["lane_engine_sha256"] = "0" * 64
        identity_core = {
            key: payload[key]
            for key in (
                "lane",
                "capabilities",
                "lane_schema_version",
                "topology_generator",
                "artifact_contract",
                "parser_implementation",
                "registry_linked_workflow",
                "source_conditioned_tool_identity",
            )
        }
        payload["sha256"] = sha256_bytes(canonical_json_bytes(identity_core))
        return payload

    with monkeypatch.context() as historical_context:
        historical_context.setattr(
            lane_engine_module,
            "_tool_identity",
            historical_tool_identity,
        )
        build_lane_bundle(
            repository_root=repository,
            output_directory=parent,
            code_mode="local_code",
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV1",
            pointer_generation=0,
            source_overrides={"historical.txt": "discussion"},
        )
    assert validate_lane_bundle(parent)["valid"] is True

    historical.unlink()
    current = repository / "current.txt"
    current.write_text(
        "Current route evidence overlays the accepted baseline.\n",
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate-lanes"
    result = build_lane_bundle(
        repository_root=repository,
        output_directory=candidate,
        code_mode="local_code",
        parent_lane_bundle=parent,
        parent_pv="PV1",
        proposed_pv="PV2",
        pointer_generation=1,
        source_overrides={"current.txt": "discussion"},
        source_paths_override=["current.txt"],
        preserve_parent_unmentioned=True,
    )

    report = next(
        row for row in result["reports"] if row["lane_id"] == "discussion"
    )
    replay = report["parent_baseline_replay"]
    assert report["build_mode"] == "FULL_VALIDATION_FALLBACK"
    assert report["full_validation_fallback_reason"] == "TOOL_IDENTITY_CHANGED"
    assert replay["status"] == "PASS"
    assert replay["preserved_unavailable_source_count"] == 1
    assert replay["preserved_unavailable_paths"] == ["historical.txt"]
    assert replay["missing_after_replay"] == []
    database = candidate / "discussion" / "discussion_sector_v001.sqlite"
    connection = sqlite3.connect(database)
    try:
        paths = {
            str(row[0])
            for row in connection.execute(
                "SELECT path FROM source_registry ORDER BY path"
            ).fetchall()
        }
    finally:
        connection.close()
    assert paths == {"current.txt", "historical.txt"}
    assert validate_lane_bundle(candidate)["valid"] is True


def test_tool_identity_fallback_never_replays_unselected_current_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    historical = repository / "historical.txt"
    accepted_text = "Accepted parent bytes must remain immutable.\n"
    historical.write_text(accepted_text, encoding="utf-8")
    parent = tmp_path / "parent-lanes"
    current_tool_identity = lane_engine_module._tool_identity

    def historical_tool_identity(lane, *, source_paths=()):
        payload = current_tool_identity(lane, source_paths=source_paths)
        if lane.canonical_lane_id != "discussion":
            return payload
        payload = json.loads(json.dumps(payload))
        payload["parser_implementation"]["lane_engine_sha256"] = "0" * 64
        identity_core = {
            key: payload[key]
            for key in (
                "lane",
                "capabilities",
                "lane_schema_version",
                "topology_generator",
                "artifact_contract",
                "parser_implementation",
                "registry_linked_workflow",
                "source_conditioned_tool_identity",
            )
        }
        payload["sha256"] = sha256_bytes(canonical_json_bytes(identity_core))
        return payload

    with monkeypatch.context() as historical_context:
        historical_context.setattr(
            lane_engine_module,
            "_tool_identity",
            historical_tool_identity,
        )
        build_lane_bundle(
            repository_root=repository,
            output_directory=parent,
            code_mode="local_code",
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV1",
            pointer_generation=0,
            source_overrides={"historical.txt": "discussion"},
        )
    assert validate_lane_bundle(parent)["valid"] is True
    parent_database = parent / "discussion" / "discussion_sector_v001.sqlite"
    parent_connection = sqlite3.connect(parent_database)
    try:
        parent_row = parent_connection.execute(
            "SELECT sha256 FROM source_registry WHERE path='historical.txt'"
        ).fetchone()
        assert parent_row is not None
        parent_sha256 = str(parent_row[0])
    finally:
        parent_connection.close()

    historical.write_text(
        "Dirty current bytes were explicitly excluded from this refresh.\n",
        encoding="utf-8",
    )
    current = repository / "current.txt"
    current.write_text("Selected current route evidence.\n", encoding="utf-8")
    candidate = tmp_path / "candidate-lanes"
    result = build_lane_bundle(
        repository_root=repository,
        output_directory=candidate,
        code_mode="local_code",
        parent_lane_bundle=parent,
        parent_pv="PV1",
        proposed_pv="PV2",
        pointer_generation=1,
        source_overrides={"current.txt": "discussion"},
        source_paths_override=["current.txt"],
        preserve_parent_unmentioned=True,
    )

    report = next(
        row for row in result["reports"] if row["lane_id"] == "discussion"
    )
    replay = report["parent_baseline_replay"]
    assert replay["replayed_source_count"] == 0
    assert replay["preserved_unavailable_source_count"] == 1
    assert replay["preserved_unavailable_paths"] == ["historical.txt"]
    database = candidate / "discussion" / "discussion_sector_v001.sqlite"
    connection = sqlite3.connect(database)
    try:
        preserved = connection.execute(
            "SELECT sha256 FROM source_registry WHERE path='historical.txt'"
        ).fetchone()
        assert preserved is not None
        assert str(preserved[0]) == parent_sha256
        snippets = "\n".join(
            decompress_exact_bytes(
                compression=str(row[2]),
                payload=bytes(row[3]),
                expected_size=int(row[1]),
                expected_sha256=str(row[0]),
            ).decode("utf-8")
            for row in connection.execute(
                """
                SELECT content.sha256, content.size_bytes,
                       content.compression, content.compressed_text
                FROM chunk_index AS chunk
                JOIN source_registry AS source
                  ON source.source_id = chunk.source_id
                JOIN chunk_content_cas AS content
                  ON content.sha256 = chunk.sha256
                WHERE source.path='historical.txt'
                ORDER BY chunk.ordinal
                """
            ).fetchall()
        )
    finally:
        connection.close()
    assert accepted_text.strip() in snippets
    assert "Dirty current bytes" not in snippets
    assert validate_lane_bundle(candidate)["valid"] is True


def test_tool_identity_fallback_full_snapshot_replays_current_and_removes_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    stable = repository / "stable.txt"
    removed = repository / "removed.txt"
    stable.write_text("Historical stable bytes.\n", encoding="utf-8")
    removed.write_text("Historical removed bytes.\n", encoding="utf-8")
    parent = tmp_path / "parent-lanes"
    current_tool_identity = lane_engine_module._tool_identity

    def historical_tool_identity(lane, *, source_paths=()):
        payload = current_tool_identity(lane, source_paths=source_paths)
        if lane.canonical_lane_id != "discussion":
            return payload
        payload = json.loads(json.dumps(payload))
        payload["parser_implementation"]["lane_engine_sha256"] = "0" * 64
        identity_core = {
            key: payload[key]
            for key in (
                "lane",
                "capabilities",
                "lane_schema_version",
                "topology_generator",
                "artifact_contract",
                "parser_implementation",
                "registry_linked_workflow",
                "source_conditioned_tool_identity",
            )
        }
        payload["sha256"] = sha256_bytes(canonical_json_bytes(identity_core))
        return payload

    with monkeypatch.context() as historical_context:
        historical_context.setattr(
            lane_engine_module,
            "_tool_identity",
            historical_tool_identity,
        )
        build_lane_bundle(
            repository_root=repository,
            output_directory=parent,
            code_mode="local_code",
            parent_lane_bundle=None,
            parent_pv=None,
            proposed_pv="PV1",
            pointer_generation=0,
            source_overrides={
                "stable.txt": "discussion",
                "removed.txt": "discussion",
            },
        )

    stable.write_text("Current stable bytes.\n", encoding="utf-8")
    removed.unlink()
    added = repository / "added.txt"
    added.write_text("Current added bytes.\n", encoding="utf-8")
    candidate = tmp_path / "candidate-lanes"
    result = build_lane_bundle(
        repository_root=repository,
        output_directory=candidate,
        code_mode="local_code",
        parent_lane_bundle=parent,
        parent_pv="PV1",
        proposed_pv="PV1_WORKING",
        pointer_generation=1,
        source_overrides={
            "stable.txt": "discussion",
            "added.txt": "discussion",
        },
        source_paths_override=None,
        preserve_parent_unmentioned=True,
    )

    report = next(
        row for row in result["reports"] if row["lane_id"] == "discussion"
    )
    assert report["build_mode"] == "FULL_VALIDATION_FALLBACK"
    assert report["parent_baseline_replay"]["preserved_unavailable_source_count"] == 0
    assert [
        row["path"] for row in report["classification"]["REMOVED_PURGE"]
    ] == ["removed.txt"]
    assert result["parallel_execution"]["source_binding"]["valid"] is True
    assert (
        result["parallel_execution"]["source_binding"]["observed_superset_allowed"]
        is False
    )
    database = candidate / "discussion" / "discussion_sector_v001.sqlite"
    connection = sqlite3.connect(database)
    try:
        paths = {
            str(row[0])
            for row in connection.execute(
                "SELECT path FROM source_registry ORDER BY path"
            ).fetchall()
        }
    finally:
        connection.close()
    assert paths == {"added.txt", "stable.txt"}
    assert validate_lane_bundle(candidate)["valid"] is True
