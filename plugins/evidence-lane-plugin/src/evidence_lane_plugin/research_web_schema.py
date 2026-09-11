"""Web response history in the Research lane, separate from filesystem sources."""
from .migrations import Migration

WEB_MIGRATION = Migration('research', 2, 'Immutable HTTP response chains and selected offline text projections', (
    '''CREATE TABLE research_web_source(source_id TEXT PRIMARY KEY,url TEXT NOT NULL,created_at TEXT NOT NULL) STRICT''',
    '''CREATE TABLE research_web_version(snapshot_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES research_web_source(source_id),generation INTEGER NOT NULL CHECK(generation>0),
        previous_snapshot TEXT REFERENCES research_web_version(snapshot_id),
        manifest_object TEXT NOT NULL REFERENCES objects(digest),body_object TEXT NOT NULL REFERENCES objects(digest),
        wire_object TEXT NOT NULL REFERENCES objects(digest),facts_object TEXT NOT NULL REFERENCES objects(digest),
        extraction_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,created_at TEXT NOT NULL,
        UNIQUE(source_id,generation)) STRICT''',
    '''CREATE TABLE research_web_current(source_id TEXT PRIMARY KEY REFERENCES research_web_source(source_id),
        snapshot_id TEXT NOT NULL REFERENCES research_web_version(snapshot_id)) STRICT''',
    '''CREATE TABLE research_web_fact(snapshot_id TEXT NOT NULL REFERENCES research_web_version(snapshot_id),
        item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT''',
    '''CREATE TABLE research_web_chunk(snapshot_id TEXT NOT NULL REFERENCES research_web_version(snapshot_id),
        chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
        text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT''',
    'CREATE VIRTUAL TABLE research_web_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)',
    'CREATE INDEX research_web_versions_source ON research_web_version(source_id,generation)',
))
