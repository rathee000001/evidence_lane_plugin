-- Projection: runtime applies the real ordered migrations under its project writer.

-- canon v1, digest f66a8c9d18d78e5739ca35f38cd57cf033963dd02a477f305729bfa2bbb9c5d7
CREATE TABLE canon_participants (participant_id TEXT PRIMARY KEY, owner_client_id TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX canon_participant_owner ON canon_participants(owner_client_id);
CREATE TABLE canon_contracts (contract_digest TEXT PRIMARY KEY, receiver_id TEXT NOT NULL REFERENCES canon_participants(participant_id),
       contract_key TEXT NOT NULL, version INTEGER NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       UNIQUE(receiver_id,contract_key,version));
CREATE TABLE canon_contract_current (receiver_id TEXT NOT NULL REFERENCES canon_participants(participant_id),
       contract_key TEXT NOT NULL, contract_digest TEXT NOT NULL REFERENCES canon_contracts(contract_digest),
       PRIMARY KEY(receiver_id,contract_key));
CREATE TABLE canon_exchanges (sequence INTEGER PRIMARY KEY, exchange_id TEXT NOT NULL UNIQUE,
       sender_id TEXT NOT NULL REFERENCES canon_participants(participant_id), receiver_id TEXT NOT NULL REFERENCES canon_participants(participant_id),
       kind TEXT NOT NULL, envelope_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       state TEXT NOT NULL CHECK(state IN ('received','admitted','rejected','needs_clarification','superseded')),
       version INTEGER NOT NULL, state_digest TEXT NOT NULL, parent_id TEXT REFERENCES canon_exchanges(exchange_id), created_at TEXT NOT NULL);
CREATE INDEX canon_inbox ON canon_exchanges(receiver_id,sequence);
CREATE INDEX canon_outbox ON canon_exchanges(sender_id,sequence);
CREATE TABLE canon_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
       actor_id TEXT NOT NULL, input_digest TEXT NOT NULL, result_json TEXT NOT NULL CHECK(json_valid(result_json)),
       previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
-- canon v2, digest ff076666d6b1ed9d06e3d6275c8cf2fb83d2eb0c4853ffe30de323e101c888eb
CREATE TABLE canon_supersessions (
       exchange_id TEXT PRIMARY KEY REFERENCES canon_exchanges(exchange_id),
       request_id TEXT NOT NULL UNIQUE REFERENCES canon_events(request_id));
-- canon v3, digest 444854a0bb337d894fba3b1165949f4ae102581b82c2e5dac3dc3be7fcf2cc11
CREATE TABLE canon_task_edges (
       sequence INTEGER PRIMARY KEY, edge_id TEXT NOT NULL UNIQUE, edge_digest TEXT NOT NULL UNIQUE,
       source_node TEXT NOT NULL, destination_node TEXT NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), artifact_path TEXT NOT NULL UNIQUE);
CREATE TABLE canon_task_edge_bindings (
       edge_id TEXT PRIMARY KEY REFERENCES canon_task_edges(edge_id),
       request_id TEXT NOT NULL UNIQUE REFERENCES canon_events(request_id));
-- canon v4, digest c104ff36289b95b941907f67214632226617dc02eb0f000ff84fab54279db2da
CREATE TABLE canon_exchange_migration4 AS SELECT * FROM canon_exchanges;
CREATE TABLE canon_supersession_migration4 AS SELECT * FROM canon_supersessions;
DROP TABLE canon_supersessions;
DROP TABLE canon_exchanges;
CREATE TABLE canon_exchanges (sequence INTEGER PRIMARY KEY, exchange_id TEXT NOT NULL UNIQUE,
       sender_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
       kind TEXT NOT NULL, envelope_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       state TEXT NOT NULL CHECK(state IN ('sealed','received','admitted','rejected','needs_clarification','superseded')),
       version INTEGER NOT NULL, state_digest TEXT NOT NULL, parent_id TEXT REFERENCES canon_exchanges(exchange_id), created_at TEXT NOT NULL,
       source_project_id TEXT NOT NULL, destination_project_id TEXT NOT NULL,
       local_role TEXT NOT NULL CHECK(local_role IN ('outbox','inbox','both')), artifact_path TEXT UNIQUE);
INSERT INTO canon_exchanges SELECT *,json_extract(body_json,'$.project_id'),
       json_extract(body_json,'$.project_id'),'both',NULL FROM canon_exchange_migration4 ORDER BY sequence;
CREATE INDEX canon_inbox ON canon_exchanges(destination_project_id,receiver_id,sequence);
CREATE INDEX canon_outbox ON canon_exchanges(source_project_id,sender_id,sequence);
CREATE TABLE canon_supersessions (
       exchange_id TEXT PRIMARY KEY REFERENCES canon_exchanges(exchange_id),
       request_id TEXT NOT NULL UNIQUE REFERENCES canon_events(request_id));
INSERT INTO canon_supersessions SELECT * FROM canon_supersession_migration4;
DROP TABLE canon_supersession_migration4;
DROP TABLE canon_exchange_migration4;
-- canon v5, digest 2e0390f81072ac3222bf57e628156b050a5c91551a57fc19b2613d88954ca393
CREATE UNIQUE INDEX canon_operation_dedup ON canon_events(kind,json_extract(result_json,'$.operation.dedup_key'))
       WHERE kind IN ('task_result','backfire');
