"""Image/media versions, OCR and extracted files belong only to images_ocr."""

from .media_parsers import KINDS
from .migrations import Migration

MEDIA_TABLES = {kind: "media_" + kind for kind in KINDS}

MEDIA_MIGRATIONS = (
    Migration(
        "media",
        1,
        "Immutable media versions and typed image/media facts",
        (
            """CREATE TABLE media_file(media_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE media_version(snapshot_id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media_file(media_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES media_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(media_id,generation)) STRICT""",
            """CREATE TABLE media_current(media_id TEXT PRIMARY KEY REFERENCES media_file(media_id),
       snapshot_id TEXT NOT NULL REFERENCES media_version(snapshot_id)) STRICT""",
            """CREATE TABLE media_structure(snapshot_id TEXT PRIMARY KEY REFERENCES media_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT""",
            *(
                f"""CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES media_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT"""
                for table in sorted(MEDIA_TABLES.values())
            ),
            """CREATE TABLE media_chunk(snapshot_id TEXT NOT NULL REFERENCES media_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT""",
            "CREATE VIRTUAL TABLE media_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)",
            """CREATE TABLE media_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES media_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE media_extraction(extraction_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES media_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE media_ocr_run(ocr_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES media_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
            """CREATE TABLE media_ocr_line(ocr_id TEXT NOT NULL REFERENCES media_ocr_run(ocr_id), line_id TEXT NOT NULL,
       frame INTEGER NOT NULL, ordinal INTEGER NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
       PRIMARY KEY(ocr_id,line_id)) STRICT""",
            """CREATE TABLE media_review_region(ocr_id TEXT NOT NULL, line_id TEXT NOT NULL,
       PRIMARY KEY(ocr_id,line_id), FOREIGN KEY(ocr_id,line_id) REFERENCES media_ocr_line(ocr_id,line_id)) STRICT""",
            "CREATE INDEX media_ocr_snapshot ON media_ocr_run(snapshot_id)",
            "CREATE VIRTUAL TABLE media_ocr_fts USING fts5(ocr_id UNINDEXED,line_id UNINDEXED,text_content,tokenize=unicode61)",
            "CREATE INDEX media_versions_source ON media_version(media_id,generation)",
        ),
    ),
    Migration(
        "media", 2, "Separate full-frame review evidence for empty OCR results",
        ("""CREATE TABLE media_review_frame(ocr_id TEXT NOT NULL REFERENCES media_ocr_run(ocr_id),
       frame INTEGER NOT NULL CHECK(frame>0), payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
       PRIMARY KEY(ocr_id,frame)) STRICT""",),
    ),
)
