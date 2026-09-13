-- Projection: runtime applies the real ordered migrations under its project writer.

CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
    body_json TEXT NOT NULL CHECK(json_valid(body_json)), created_at TEXT NOT NULL);
CREATE INDEX receipts_kind_time ON receipts(kind,created_at);

-- access v1, digest 3b13d260a0ddf92fd0975facd96d9a8a77f8fe2ee46564a3dc4dc7108b9cc931
CREATE TABLE access_grants (
        grant_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
        permissions_json TEXT NOT NULL CHECK(json_valid(permissions_json)),
        roots_json TEXT NOT NULL CHECK(json_valid(roots_json)),
        expires_at TEXT, revoked_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX access_principal ON access_grants(principal_id,revoked_at);
-- access v2, digest 577ea803dfc2ad489971466697e398d1e80f319a86e46863a2243144cb515a20
ALTER TABLE access_grants ADD COLUMN parent_grant_id TEXT REFERENCES access_grants(grant_id);
CREATE INDEX access_parent ON access_grants(parent_grant_id);
-- extensions v1, digest 102ea20426b23b0e9a8d8ac28804ee6a02deed19f438b52041252ceb7464e4c6
CREATE TABLE extensions_registration (
            plugin_id TEXT PRIMARY KEY, current_version INTEGER NOT NULL,
            revoked_at TEXT);
CREATE TABLE extensions_versions (
            plugin_id TEXT NOT NULL REFERENCES extensions_registration(plugin_id),
            version INTEGER NOT NULL, registration_json TEXT NOT NULL CHECK(json_valid(registration_json)),
            digest TEXT NOT NULL, configured_at TEXT NOT NULL, actor_id TEXT NOT NULL,
            PRIMARY KEY(plugin_id,version));
CREATE TABLE extensions_events (
            sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
            plugin_id TEXT NOT NULL REFERENCES extensions_registration(plugin_id),
            event_type TEXT NOT NULL, actor_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
            details_json TEXT NOT NULL CHECK(json_valid(details_json)),
            prior_digest TEXT, digest TEXT NOT NULL);
-- accelerator v1, digest fe67ae66b30a4f7af57871de306fb5ab461c86ef3083630a8f8612fcb9ee9eed
CREATE TABLE accelerator_settings (
            revision INTEGER PRIMARY KEY, config_json TEXT NOT NULL CHECK(json_valid(config_json)),
            digest TEXT NOT NULL, actor_id TEXT NOT NULL, configured_at TEXT NOT NULL);
-- remote v1, digest b9c373cfd117a3bab3e8601df3d716a8d42373569bde00caa1a16655ca7e5658
CREATE TABLE remote_probe (
        principal_id TEXT PRIMARY KEY, policy_digest TEXT NOT NULL,
        nonce_digest TEXT NOT NULL, object_digest TEXT NOT NULL,
        engine_id TEXT NOT NULL, created_at TEXT NOT NULL);
-- recovery v1, digest 6ef8c33aa2e21276d3cc7bee8dc6f25532996a96e0018079c787fbdb7124e596
CREATE TABLE recovery_backups (request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
       input_digest TEXT NOT NULL, result_json TEXT NOT NULL CHECK(json_valid(result_json)));
CREATE TABLE recovery_history (sequence INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)));
CREATE TABLE recovery_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       recovery_digest TEXT NOT NULL, required_after_revision INTEGER NOT NULL, cleared_by_revision INTEGER);
-- gitpush v1, digest 64c9cf8ea51ac372041060d68f94f0745d06c76ca1bd14eeef873eac24554705
CREATE TABLE gitpush_events (action_id TEXT NOT NULL, sequence INTEGER NOT NULL,
       digest TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       PRIMARY KEY(action_id,sequence));
CREATE TABLE gitpush_current (action_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
       digest TEXT NOT NULL, FOREIGN KEY(action_id,sequence) REFERENCES gitpush_events(action_id,sequence));
-- capture v1, digest ce16fd00779be73e5122cd75948b11269fc92eca33ca2e7896e99d46d5530860
CREATE TABLE capture_decisions (
       sequence INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,
       previous_digest TEXT,digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)));
-- hostmemory v1, digest 30bede0b4dd3f7634a29b2ccb890c33ec0dc509b0a6978e2ff10248d83ee1aa3
CREATE TABLE hostmemory_imports (
       request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       receipt_id TEXT NOT NULL UNIQUE REFERENCES receipts(receipt_id), receipt_digest TEXT NOT NULL,
       result_json TEXT NOT NULL CHECK(json_valid(result_json)), result_digest TEXT NOT NULL);
-- storage v1, digest a47bb0877ae5f3d44866e04ee05766eca59af045fb07c7618917470bd197135b
CREATE TABLE storage_selection_events (sequence INTEGER PRIMARY KEY,
       request_id TEXT NOT NULL UNIQUE, client_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       previous_digest TEXT, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
