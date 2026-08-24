PRAGMA foreign_keys=ON;

CREATE TABLE graph_metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE consequence_node(
    node_id TEXT PRIMARY KEY,
    node_kind TEXT NOT NULL,
    canonical_locator TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE consequence_edge(
    edge_id TEXT PRIMARY KEY,
    relation TEXT NOT NULL,
    source_node_id TEXT NOT NULL REFERENCES consequence_node(node_id),
    destination_node_id TEXT NOT NULL REFERENCES consequence_node(node_id),
    canonical_locator TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL UNIQUE
) STRICT;

CREATE INDEX consequence_node_kind_idx
ON consequence_node(node_kind, node_id);

CREATE INDEX consequence_edge_relation_idx
ON consequence_edge(relation, source_node_id, destination_node_id);

CREATE VIRTUAL TABLE consequence_graph_fts USING fts5(
    record_id UNINDEXED,
    record_type UNINDEXED,
    record_kind,
    canonical_locator,
    searchable_text,
    tokenize='unicode61 remove_diacritics 2'
);
