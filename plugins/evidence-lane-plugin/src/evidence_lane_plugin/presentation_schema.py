"""Presentation versions and native structure in the physical PPT lane only."""
from .migrations import Migration

PPT_TABLES = {'slide': 'ppt_slide', 'notes': 'ppt_notes', 'shape': 'ppt_shape',
    'text_block': 'ppt_text_block', 'table': 'ppt_table', 'image': 'ppt_image_reference',
    'relationship': 'ppt_slide_relationship', 'chart': 'ppt_chart'}

PPT_MIGRATIONS = (Migration('ppt', 1, 'Separate immutable presentation versions, native structure, retrieval and render receipts', (
    """CREATE TABLE ppt_file(presentation_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
       source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
    """CREATE TABLE ppt_version(snapshot_id TEXT PRIMARY KEY, presentation_id TEXT NOT NULL REFERENCES ppt_file(presentation_id),
       generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES ppt_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
       facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
       created_at TEXT NOT NULL, UNIQUE(presentation_id,generation)) STRICT""",
    """CREATE TABLE ppt_current(presentation_id TEXT PRIMARY KEY REFERENCES ppt_file(presentation_id),
       snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id)) STRICT""",
    """CREATE TABLE ppt_structure(snapshot_id TEXT PRIMARY KEY REFERENCES ppt_version(snapshot_id),
       facts_object TEXT NOT NULL REFERENCES objects(digest), fidelity_json TEXT NOT NULL CHECK(json_valid(fidelity_json))) STRICT""",
    *(f"""CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
       payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT"""
      for table in sorted(set(PPT_TABLES.values()))),
    """CREATE TABLE ppt_chunk(snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
       text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT""",
    'CREATE VIRTUAL TABLE ppt_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)',
    """CREATE TABLE ppt_render(render_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
    """CREATE TABLE ppt_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
       effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT""",
    'CREATE INDEX ppt_versions_presentation ON ppt_version(presentation_id,generation)',
    """CREATE TABLE ppt_enrichment(enrichment_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES ppt_version(snapshot_id),
       manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT""",
)),)
