"""PDF versions and native structure in the physical PDF lane only."""

from .migrations import Migration
from .pdf_parsers import KINDS

PDF_TABLES = {kind: "pdf_" + kind for kind in KINDS}


PDF_MIGRATIONS = (
    Migration(
        "pdfocr",
        1,
        "Separate immutable pdf versions, native structure and retrieval",
        (
            """CREATE TABLE pdf_file(pdf_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE pdf_version(snapshot_id TEXT PRIMARY KEY, pdf_id TEXT NOT NULL REFERENCES pdf_file(pdf_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES pdf_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(pdf_id,generation)) STRICT""",
            """CREATE TABLE pdf_current(pdf_id TEXT PRIMARY KEY REFERENCES pdf_file(pdf_id),
       snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id)) STRICT""",
            """CREATE TABLE pdf_structure(snapshot_id TEXT PRIMARY KEY REFERENCES pdf_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT""",
            *(
                f"""CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT"""
                for table in sorted(set(PDF_TABLES.values()))
            ),
            """CREATE TABLE pdf_chunk(snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT""",
            "CREATE VIRTUAL TABLE pdf_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)",
            """CREATE TABLE pdf_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
            "CREATE INDEX pdf_versions_pdf ON pdf_version(pdf_id,generation)",
        ),
    ),
    Migration(
        "pdfocr",
        2,
        "Separate PDF raster, OCR and Docling derivatives",
        (
            """CREATE TABLE pdf_render(render_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id),
           manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE pdf_ocr_run(ocr_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id),
           manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE pdf_ocr_line(ocr_id TEXT NOT NULL REFERENCES pdf_ocr_run(ocr_id), line_id TEXT NOT NULL,
           page INTEGER NOT NULL, ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
           PRIMARY KEY(ocr_id,line_id)) STRICT""",
            """CREATE TABLE pdf_review_region(ocr_id TEXT NOT NULL, line_id TEXT NOT NULL,
           PRIMARY KEY(ocr_id,line_id), FOREIGN KEY(ocr_id,line_id) REFERENCES pdf_ocr_line(ocr_id,line_id)) STRICT""",
            "CREATE INDEX pdf_ocr_run_snapshot ON pdf_ocr_run(snapshot_id)",
            """CREATE TABLE docling_extraction(enrichment_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES pdf_version(snapshot_id),
           manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
        ),
    ),
)
