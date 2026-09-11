-- Projection: runtime applies the real ordered migrations under its project writer.

-- learning v1, digest ef20a7527722366f6784e2213f262f242256169a296a86a2af902adb4ef27e38
CREATE TABLE learning_versions (
       version_id TEXT PRIMARY KEY, lesson_key TEXT NOT NULL, version INTEGER NOT NULL,
       profile TEXT NOT NULL, action TEXT NOT NULL, scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
       content_digest TEXT NOT NULL REFERENCES objects(digest),
       source_job_id TEXT NOT NULL UNIQUE,
       source_receipt_id TEXT NOT NULL,
       state TEXT NOT NULL CHECK(state IN ('active','superseded','revoked')), created_at TEXT NOT NULL,
       UNIQUE(lesson_key,version));
CREATE INDEX learning_scope ON learning_versions(profile,action,state);
CREATE TABLE learning_current (lesson_key TEXT PRIMARY KEY,
       version_id TEXT NOT NULL UNIQUE REFERENCES learning_versions(version_id));
CREATE TABLE learning_controls (lesson_key TEXT PRIMARY KEY,
       revoked_by TEXT NOT NULL, reason TEXT NOT NULL, revoked_at TEXT NOT NULL);
CREATE TABLE learning_events (sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
       version_id TEXT NOT NULL REFERENCES learning_versions(version_id), kind TEXT NOT NULL,
       actor_id TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE VIRTUAL TABLE learning_fts USING fts5(version_id UNINDEXED,text,tokenize='unicode61');
