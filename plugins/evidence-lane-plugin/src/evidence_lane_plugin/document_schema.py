"""Document versions and native structure in the physical Docs lane only."""
from .migrations import Migration

DOC_TABLES = {'paragraph': 'doc_paragraph', 'heading': 'doc_heading', 'table': 'doc_table_extract',
    'image': 'doc_image_reference', 'relationship': 'doc_relationship', 'content_control': 'doc_content_control',
    'insertion': 'doc_revision', 'deletion': 'doc_revision', 'field': 'doc_field', 'field_instruction': 'doc_field',
    'hyperlink': 'doc_hyperlink', 'bookmark': 'doc_bookmark', 'embedded_object': 'doc_embedded_object'}

DOC_MIGRATIONS = (Migration('docs', 1, 'Separate immutable document versions, native structure, retrieval and render receipts', (
    """CREATE TABLE doc_file(document_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
    """CREATE TABLE doc_version(snapshot_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES doc_file(document_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES doc_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(document_id,generation)) STRICT""",
    """CREATE TABLE doc_current(document_id TEXT PRIMARY KEY REFERENCES doc_file(document_id),
       snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id)) STRICT""",
    """CREATE TABLE doc_structure(snapshot_id TEXT PRIMARY KEY REFERENCES doc_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT""",
    *(f"""CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT"""
      for table in sorted(set(DOC_TABLES.values()))),
    """CREATE TABLE doc_chunk(snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT""",
    'CREATE VIRTUAL TABLE doc_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)',
    """CREATE TABLE doc_render(render_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
    """CREATE TABLE doc_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
    'CREATE INDEX doc_versions_document ON doc_version(document_id,generation)',
)), Migration('docs', 2, 'Separate attributed rich document enrichment from native structure and page rendering', (
    """CREATE TABLE docling_extraction(enrichment_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES doc_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
)))
