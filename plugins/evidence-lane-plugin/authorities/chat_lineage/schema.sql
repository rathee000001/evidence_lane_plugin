-- Projection: runtime applies the real ordered migrations under its project writer.

-- lineage v1, digest 1c3ad8a553193b2ca680e5d247f68d4d848958f378283e3805433005462e763c
CREATE TABLE lineage_events (
        sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, client_id TEXT NOT NULL, provenance TEXT NOT NULL,
        reported_session_id TEXT, reported_turn_id TEXT, tool_use_id TEXT,
        parent_event_id TEXT REFERENCES lineage_events(event_id),
        payload_digest TEXT NOT NULL REFERENCES objects(digest),
        identity_digest TEXT NOT NULL, previous_cursor TEXT, cursor TEXT NOT NULL UNIQUE,
        received_at TEXT NOT NULL);
CREATE INDEX lineage_event_links ON lineage_events(parent_event_id,sequence);
CREATE INDEX lineage_tool_pairs ON lineage_events(reported_session_id,reported_turn_id,tool_use_id,sequence);
CREATE TABLE lineage_chunks (
        chunk_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES lineage_events(event_id),
        ordinal INTEGER NOT NULL, text_digest TEXT NOT NULL, text TEXT NOT NULL, UNIQUE(event_id,ordinal));
CREATE VIRTUAL TABLE lineage_fts USING fts5(chunk_id UNINDEXED,event_id UNINDEXED,text,tokenize='unicode61');
-- continuation v1, digest 0511b2ff2d9ebb4e0ca014ed855fd1b5f8ef9163a1f94af6530a133ae321bd42
CREATE TABLE continuation_offers (sequence INTEGER PRIMARY KEY, continuation_id TEXT NOT NULL UNIQUE,
       participant_id TEXT NOT NULL, source_client_id TEXT NOT NULL,
       destination_client_id TEXT NOT NULL, continuation_digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), state TEXT NOT NULL CHECK(state IN ('offered','accepted','cancelled')),
       result_json TEXT CHECK(json_valid(result_json)), created_at TEXT NOT NULL);
CREATE UNIQUE INDEX continuation_one_offer ON continuation_offers(source_client_id) WHERE state='offered';
CREATE TABLE continuation_bindings (binding_digest TEXT PRIMARY KEY,
       participant_id TEXT NOT NULL, generation INTEGER NOT NULL,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)), UNIQUE(participant_id,generation));
CREATE TABLE continuation_retired_clients (client_id TEXT PRIMARY KEY, continuation_id TEXT NOT NULL REFERENCES continuation_offers(continuation_id),
       destination_client_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE continuation_events (sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
       kind TEXT NOT NULL, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       result_json TEXT NOT NULL CHECK(json_valid(result_json)), previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
-- prompt v1, digest 7bba45c44d77e0513dcadc84594b381a4988db896d49228af8662061a912c24e
CREATE TABLE prompt_entries (
       source_event_id TEXT PRIMARY KEY REFERENCES lineage_events(event_id),client_id TEXT NOT NULL,
       reported_session_id TEXT NOT NULL,prompt_index INTEGER NOT NULL,source_cursor TEXT NOT NULL,
       entry_json TEXT NOT NULL CHECK(json_valid(entry_json)),previous_entry_digest TEXT,
       entry_digest TEXT NOT NULL UNIQUE,UNIQUE(client_id,reported_session_id,prompt_index));
CREATE TABLE prompt_classifications (
       classification_id TEXT PRIMARY KEY,source_event_id TEXT NOT NULL REFERENCES prompt_entries(source_event_id),
       ordinal INTEGER NOT NULL,actor_id TEXT NOT NULL,previous_digest TEXT,
       classification_json TEXT NOT NULL CHECK(json_valid(classification_json)),
       classification_digest TEXT NOT NULL UNIQUE,UNIQUE(source_event_id,ordinal));
CREATE INDEX prompt_client_session ON prompt_entries(client_id,reported_session_id,prompt_index);
-- turn v1, digest e3f18e977dddbfa477c4c2b14ee06adce8faa7e95809370d143a9a492b7261dd
CREATE TABLE turn_events (
       sequence INTEGER PRIMARY KEY, source_event_id TEXT NOT NULL UNIQUE REFERENCES lineage_events(event_id),
       client_id TEXT NOT NULL, reported_session_id TEXT NOT NULL, reported_turn_id TEXT,
       session_digest TEXT, event_name TEXT NOT NULL, body_digest TEXT NOT NULL REFERENCES objects(digest),
       previous_digest TEXT, digest TEXT NOT NULL UNIQUE);
CREATE INDEX turn_scope ON turn_events(client_id,reported_session_id,session_digest,sequence);
CREATE INDEX turn_reported_turn ON turn_events(client_id,reported_session_id,reported_turn_id,session_digest,sequence);
