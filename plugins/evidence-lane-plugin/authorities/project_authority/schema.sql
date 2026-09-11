-- Projection: runtime applies the real ordered migrations under its project writer.

CREATE TABLE project (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL UNIQUE,
    source_root TEXT NOT NULL, format_version INTEGER NOT NULL,
    storage_layout TEXT NOT NULL, created_at TEXT NOT NULL,
    registration_digest TEXT NOT NULL CHECK(length(registration_digest)=64));
CREATE TABLE project_registration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    body_json TEXT NOT NULL CHECK(json_valid(body_json)));
CREATE TABLE root_lane_catalog (
    lane_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('authority','sector')),
    database_path TEXT NOT NULL UNIQUE, initialized_at TEXT NOT NULL);
CREATE TABLE root_lane_heads (
    lane_id TEXT PRIMARY KEY REFERENCES root_lane_catalog(lane_id),
    revision INTEGER NOT NULL, head_digest TEXT NOT NULL, commit_id TEXT NOT NULL);
CREATE TABLE root_pv_head (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
    head_digest TEXT NOT NULL, commit_id TEXT);
CREATE TABLE root_transaction_journal (
    commit_id TEXT PRIMARY KEY, phase TEXT NOT NULL, body_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO root_pv_head VALUES(1,0,'',NULL);

-- writer v1, digest 18322e6daeb8002a70212ca32b6916f278e9779e975a350aa7162daddc27e163
CREATE TABLE writer_lease (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            fence INTEGER NOT NULL CHECK(fence>=1), owner_id TEXT,
            engine_id TEXT, expires_at TEXT);
