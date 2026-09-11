"""Independent Excel and structured-data schemas in their owning lanes."""
from .migrations import Migration

LANE_TABLES = {
    'data_excel': {'workbook': 'sheet_workbook', 'sheet': 'sheet_tab', 'cell': 'sheet_cell_sample',
        'formula': 'sheet_formula', 'defined_name': 'sheet_defined_name', 'merged_range': 'sheet_range',
        'validation': 'sheet_validation', 'hyperlink': 'sheet_hyperlink', 'relationship': 'sheet_relationship',
        'table': 'sheet_table', 'chart': 'sheet_chart_metadata'},
    'data': {'table': 'data_table', 'column': 'data_column', 'row': 'data_sample', 'lineage': 'data_lineage'},
}
PREFIX = {'data_excel': 'sheet', 'data': 'data'}
OWNER = {'data_excel': 'dataexcel', 'data': 'data'}


def tabular_migrations(lane_id):
    prefix, tables = PREFIX[lane_id], LANE_TABLES[lane_id]
    return (Migration(OWNER[lane_id], 1, 'Separate immutable ' + lane_id + ' versions, native facts, query projections and effects', (
        f'''CREATE TABLE {prefix}_source(source_id TEXT PRIMARY KEY, logical_name TEXT NOT NULL,
            source_path TEXT, origin TEXT NOT NULL, created_at TEXT NOT NULL) STRICT''',
        f'''CREATE TABLE {prefix}_version(snapshot_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES {prefix}_source(source_id),
            generation INTEGER NOT NULL CHECK(generation>0), previous_snapshot TEXT REFERENCES {prefix}_version(snapshot_id),
            manifest_object TEXT NOT NULL REFERENCES objects(digest), raw_object TEXT NOT NULL REFERENCES objects(digest),
            facts_object TEXT NOT NULL REFERENCES objects(digest), parser_contract TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(source_id,generation)) STRICT''',
        f'''CREATE TABLE {prefix}_current(source_id TEXT PRIMARY KEY REFERENCES {prefix}_source(source_id),
            snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id)) STRICT''',
        *(f'''CREATE TABLE {table}(snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id),
            item_id TEXT NOT NULL, kind TEXT NOT NULL, ordinal INTEGER NOT NULL, part TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), PRIMARY KEY(snapshot_id,item_id)) STRICT'''
          for table in sorted(set(tables.values()))),
        f'''CREATE TABLE {prefix}_chunk(snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id),
            chunk_id TEXT NOT NULL, item_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            text_object TEXT NOT NULL REFERENCES objects(digest), PRIMARY KEY(snapshot_id,chunk_id)) STRICT''',
        f'CREATE VIRTUAL TABLE {prefix}_chunk_fts USING fts5(snapshot_id UNINDEXED,chunk_id UNINDEXED,text_content,tokenize=unicode61)',
        f'''CREATE TABLE {prefix}_export(export_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id),
            destination TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL, effect_id TEXT NOT NULL, created_at TEXT NOT NULL) STRICT''',
        f'''CREATE TABLE {prefix}_derivative(derivative_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES {prefix}_version(snapshot_id),
            kind TEXT NOT NULL, manifest_object TEXT NOT NULL REFERENCES objects(digest), created_at TEXT NOT NULL) STRICT''',
        f'CREATE INDEX {prefix}_versions_source ON {prefix}_version(source_id,generation)',
    )),)
