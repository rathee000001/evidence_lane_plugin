-- Projection: runtime applies the real ordered migrations under its project writer.

-- memory v1, digest 0d277f8548d70efecf566af4644273de352f812d261f815bc069d2d6007185e9
CREATE TABLE memory_locators (locator_id TEXT PRIMARY KEY, reference_kind TEXT NOT NULL,
       reference_key TEXT NOT NULL, reference_digest TEXT NOT NULL, reference_revision INTEGER, profile TEXT,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), row_digest TEXT NOT NULL,
       created_at TEXT NOT NULL);
CREATE INDEX memory_reference ON memory_locators(reference_kind,reference_key,reference_revision);
CREATE VIRTUAL TABLE memory_fts USING fts5(locator_id UNINDEXED,label,text,tokenize='unicode61');
CREATE TABLE memory_edges (edge_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES memory_locators(locator_id),
       target_id TEXT NOT NULL REFERENCES memory_locators(locator_id), kind TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), row_digest TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX memory_edge_target ON memory_edges(target_id,kind);
CREATE INDEX memory_edge_source ON memory_edges(source_id,kind);
CREATE TABLE memory_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
       kind TEXT NOT NULL, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       result_json TEXT NOT NULL CHECK(json_valid(result_json)), previous_digest TEXT,
       digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE memory_checkpoints (checkpoint_digest TEXT PRIMARY KEY,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)));
