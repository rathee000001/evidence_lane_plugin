PRAGMA foreign_keys = ON;

CREATE TABLE universe_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE universe_node(
    node_id TEXT PRIMARY KEY,
    node_kind TEXT NOT NULL,
    canonical_locator TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    attributes_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL
) STRICT;

CREATE INDEX universe_node_kind_idx
ON universe_node(node_kind, node_id);

CREATE TABLE universe_edge(
    edge_id TEXT PRIMARY KEY,
    relation TEXT NOT NULL,
    source_node_id TEXT NOT NULL REFERENCES universe_node(node_id),
    destination_node_id TEXT NOT NULL REFERENCES universe_node(node_id),
    attributes_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    UNIQUE(relation, source_node_id, destination_node_id, attributes_json)
) STRICT;

CREATE INDEX universe_edge_source_idx
ON universe_edge(source_node_id, relation, destination_node_id);

CREATE INDEX universe_edge_destination_idx
ON universe_edge(destination_node_id, relation, source_node_id);

CREATE TABLE universe_metric(
    metric_key TEXT PRIMARY KEY,
    metric_value INTEGER NOT NULL CHECK(metric_value >= 0)
) STRICT;

CREATE TABLE universe_refresh(
    refresh_id TEXT PRIMARY KEY,
    source_fingerprint_sha256 TEXT NOT NULL,
    graph_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    edge_count INTEGER NOT NULL
) STRICT;

CREATE VIRTUAL TABLE universe_fts USING fts5(
    node_id UNINDEXED,
    node_kind,
    label,
    canonical_locator,
    attributes
);
