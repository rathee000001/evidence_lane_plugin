"""Schema adapter; every evidence sector owns its separate database."""
from .lanes import lane_family
from .migrations import Migration


class _LaneTemplates(dict):
    def __missing__(self, lane_id):
        family = lane_family(lane_id)
        if family == lane_id:
            raise KeyError(lane_id)
        return self[family]

PREFIX = _LaneTemplates({'research': 'research', 'artifacts': 'artifact', 'custom': 'custom'})
ORIGINAL_KINDS = _LaneTemplates({
    'research': ('research_source', 'research_question', 'research_hypothesis', 'research_method',
        'research_evidence', 'research_finding', 'research_limitation', 'research_citation',
        'research_open_question', 'research_receipt'),
    'artifacts': ('project_artifact', 'artifact_metadata', 'artifact_text_extract', 'artifact_relation_edge',
        'artifact_review_required', 'artifact_media_probe'),
    'custom': ('custom_source', 'custom_item', 'custom_evidence', 'custom_decision', 'custom_next_action'),
})
TABLES = _LaneTemplates({lane: {**{kind: kind for kind in kinds}, **{kind: PREFIX[lane] + '_' + kind for kind in (
    'native_fact', 'archive_member', 'sqlite_schema_object', 'sqlite_table', 'sqlite_relationship',
    'sqlite_row', 'sqlite_receipt', 'review_required')}} for lane, kinds in ORIGINAL_KINDS.items()})


def migrations(lane_id):
    prefix, tables = PREFIX[lane_id], TABLES[lane_id]
    base = (Migration(lane_family(lane_id), 1, 'Separate immutable evidence files, retained lane facts and literal retrieval', (
        f'''CREATE TABLE {prefix}_file(source_id TEXT PRIMARY KEY,logical_name TEXT NOT NULL,
            source_path TEXT NOT NULL,created_at TEXT NOT NULL) STRICT''',
        f'''CREATE TABLE {prefix}_version(snapshot_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES {prefix}_file(source_id),
            generation INTEGER NOT NULL CHECK(generation>0),
            previous_snapshot TEXT REFERENCES {prefix}_version(snapshot_id),
            manifest_object TEXT NOT NULL REFERENCES objects(digest),raw_object TEXT NOT NULL REFERENCES objects(digest),
            facts_object TEXT NOT NULL REFERENCES objects(digest),parser_contract TEXT NOT NULL,
            created_at TEXT NOT NULL,UNIQUE(source_id,generation)) STRICT''',
        f'''CREATE TABLE {prefix}_current(source_id TEXT PRIMARY KEY REFERENCES {prefix}_file(source_id),
            snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id)) STRICT''',
        *(f'''CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id),
            item_id TEXT NOT NULL,kind TEXT NOT NULL,ordinal INTEGER NOT NULL,part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),PRIMARY KEY(snapshot_id,item_id)) STRICT'''
            for table in sorted(set(tables.values()))),
        f'''CREATE TABLE {prefix}_chunk(snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id),
            chunk_id TEXT NOT NULL,item_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
            text_object TEXT NOT NULL REFERENCES objects(digest),PRIMARY KEY(snapshot_id,chunk_id)) STRICT''',
        f'CREATE VIRTUAL TABLE {prefix}_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)',
        f'CREATE INDEX {prefix}_versions_source ON {prefix}_version(source_id,generation)',
    )),)
    if lane_id == 'research':
        from .research_discovery_schema import DISCOVERY_MIGRATION
        from .research_web_schema import WEB_MIGRATION
        return (*base, WEB_MIGRATION, DISCOVERY_MIGRATION)
    return base
