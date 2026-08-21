CREATE TABLE IF NOT EXISTS canon_schema_migration(
    schema_name TEXT NOT NULL,
    from_version INTEGER NOT NULL CHECK(from_version >= 0),
    to_version INTEGER NOT NULL CHECK(to_version > 0),
    asset_sha256 TEXT NOT NULL,
    migration_mode TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(schema_name,to_version)
) STRICT;
CREATE TABLE IF NOT EXISTS canon_contract(
    contract_sha256 TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    contract_version INTEGER NOT NULL CHECK(contract_version > 0),
    destination_project_id TEXT NOT NULL,
    destination_task_uuid TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    contract_json TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_contract_version
ON canon_contract(contract_id,contract_version,destination_task_uuid);
CREATE TABLE IF NOT EXISTS canon_packet(
    canon_id TEXT PRIMARY KEY,
    canon_sha256 TEXT NOT NULL UNIQUE,
    owner_project_id TEXT NOT NULL,
    local_role TEXT NOT NULL,
    source_project_id TEXT NOT NULL,
    source_task_uuid TEXT NOT NULL,
    destination_project_id TEXT NOT NULL,
    destination_task_uuid TEXT NOT NULL,
    destination_contract_sha256 TEXT NOT NULL,
    canon_type TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    idempotency_key TEXT NOT NULL,
    current_state TEXT NOT NULL,
    envelope_json TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_packet_replay
ON canon_packet(owner_project_id,local_role,idempotency_key);
CREATE INDEX IF NOT EXISTS idx_canon_packet_inbox
ON canon_packet(destination_task_uuid,current_state);
CREATE TABLE IF NOT EXISTS canon_event(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    canon_id TEXT NOT NULL REFERENCES canon_packet(canon_id),
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    decision_key_sha256 TEXT,
    previous_event_sha256 TEXT,
    event_sha256 TEXT NOT NULL UNIQUE,
    event_json TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_decision_once
ON canon_event(decision_key_sha256)
WHERE decision_key_sha256 IS NOT NULL;
CREATE TABLE IF NOT EXISTS canon_receipt(
    receipt_sha256 TEXT PRIMARY KEY,
    canon_id TEXT NOT NULL REFERENCES canon_packet(canon_id),
    receipt_type TEXT NOT NULL,
    receipt_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS canon_edge(
    edge_id TEXT PRIMARY KEY,
    edge_sha256 TEXT NOT NULL UNIQUE,
    source_node TEXT NOT NULL,
    destination_node TEXT NOT NULL,
    expected_return_contract_sha256 TEXT NOT NULL,
    edge_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS canon_dispatch(
    dispatch_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS canon_backfire_dedup(
    dedup_key_sha256 TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    canon_id TEXT NOT NULL,
    canon_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS canon_continuity_receipt(
    consumption_key_sha256 TEXT PRIMARY KEY,
    snapshot_sha256 TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    receipt_json TEXT NOT NULL
) STRICT;
