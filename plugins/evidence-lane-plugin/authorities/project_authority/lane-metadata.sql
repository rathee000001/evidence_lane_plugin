
CREATE TABLE lane_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL,
    lane_id TEXT NOT NULL, kind TEXT NOT NULL, storage_layout TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE objects (
    digest TEXT PRIMARY KEY CHECK(length(digest)=64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes>=0), created_at TEXT NOT NULL);
CREATE TABLE schema_migrations (
    owner TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL,
    description TEXT NOT NULL, applied_at TEXT NOT NULL, PRIMARY KEY(owner,version));
CREATE TABLE schema_ownership (
    object_name TEXT PRIMARY KEY, owner TEXT NOT NULL, object_type TEXT NOT NULL);
