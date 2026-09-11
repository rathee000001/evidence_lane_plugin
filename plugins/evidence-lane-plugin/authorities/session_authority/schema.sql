-- Projection: runtime applies the real ordered migrations under its project writer.

-- sessions v1, digest f12e2a4708db487d7384c38e8249ceae9c6a85afd4c385731f71e496b0bea3fb
CREATE TABLE sessions_records (session_id TEXT PRIMARY KEY,
       state TEXT NOT NULL CHECK(state IN ('active','closed')), generation INTEGER NOT NULL CHECK(generation>=1),
       owner_client_id TEXT NOT NULL, owner_engine_id TEXT NOT NULL, reported_session_id TEXT NOT NULL,
       flash_digest TEXT NOT NULL, runtime_package_digest TEXT NOT NULL, event_digest TEXT NOT NULL,
       created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX sessions_one_active ON sessions_records(state) WHERE state='active';
CREATE TABLE sessions_current (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       session_id TEXT NOT NULL REFERENCES sessions_records(session_id));
CREATE TABLE sessions_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
       session_id TEXT NOT NULL REFERENCES sessions_records(session_id) DEFERRABLE INITIALLY DEFERRED,
       generation INTEGER NOT NULL, action TEXT NOT NULL, client_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), previous_digest TEXT, digest TEXT NOT NULL UNIQUE,
       created_at TEXT NOT NULL, UNIQUE(session_id,generation));
