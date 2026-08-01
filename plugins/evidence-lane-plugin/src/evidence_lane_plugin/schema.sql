PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS repositories (
    repository_id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    repository_url TEXT NOT NULL,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    tree_sha TEXT NOT NULL,
    worktree_sha256 TEXT NOT NULL,
    is_clean INTEGER NOT NULL CHECK (is_clean IN (0, 1)),
    submodules_json TEXT NOT NULL,
    lfs_state TEXT NOT NULL,
    UNIQUE (repository_url, commit_sha, worktree_sha256)
) STRICT;

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    sha256 TEXT NOT NULL,
    encoding TEXT,
    is_binary INTEGER NOT NULL CHECK (is_binary IN (0, 1)),
    file_type TEXT NOT NULL,
    code_family TEXT NOT NULL,
    line_count INTEGER NOT NULL CHECK (line_count >= 0),
    ingestion_status TEXT NOT NULL,
    exact_bytes BLOB NOT NULL,
    error_code TEXT,
    UNIQUE (repository_id, path)
) STRICT;

CREATE TABLE IF NOT EXISTS source_tombstones (
    tombstone_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    prior_sha256 TEXT NOT NULL,
    prior_size_bytes INTEGER NOT NULL,
    parent_pv TEXT,
    removed_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS source_refresh_events (
    refresh_event_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    classification TEXT NOT NULL,
    prior_sha256 TEXT,
    current_sha256 TEXT,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    text_content TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    UNIQUE (file_id, ordinal)
) STRICT;

CREATE TABLE IF NOT EXISTS chunk_content_cas (
    sha256 TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    text_content TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS chunk_history (
    history_id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(repository_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    chunk_sha256 TEXT NOT NULL REFERENCES chunk_content_cas(sha256),
    observed_at TEXT NOT NULL,
    UNIQUE(repository_id, path, source_sha256, ordinal, chunk_sha256)
) STRICT;

CREATE INDEX IF NOT EXISTS chunk_history_path_idx
ON chunk_history(repository_id, path, observed_at);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    path,
    text_content,
    chunk_id UNINDEXED,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    signature TEXT,
    parser TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols(name);
CREATE INDEX IF NOT EXISTS symbols_qualified_idx ON symbols(qualified_name);

CREATE TABLE IF NOT EXISTS imports (
    import_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    imported_name TEXT,
    alias TEXT,
    line_number INTEGER NOT NULL CHECK (line_number >= 1),
    parser TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS dependencies (
    dependency_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    ecosystem TEXT NOT NULL,
    name TEXT NOT NULL,
    constraint_text TEXT,
    dependency_group TEXT NOT NULL,
    source_path TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS routes (
    route_id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    route_kind TEXT NOT NULL,
    method TEXT,
    path_pattern TEXT NOT NULL,
    handler TEXT,
    line_number INTEGER CHECK (line_number IS NULL OR line_number >= 1),
    parser TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS pointers (
    pointer_id INTEGER PRIMARY KEY,
    pointer_kind TEXT NOT NULL,
    pointer_value TEXT NOT NULL,
    pointer_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS builder_receipts (
    receipt_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    details_json TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_class TEXT NOT NULL,
    requested_outcome TEXT NOT NULL,
    permitted_paths_json TEXT NOT NULL,
    permitted_tools_json TEXT NOT NULL,
    acceptance_checks_json TEXT NOT NULL,
    write_boundary TEXT NOT NULL,
    stop_condition TEXT NOT NULL,
    hil_required INTEGER NOT NULL CHECK (hil_required IN (0, 1)),
    status TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS acceptance_results (
    acceptance_result_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    declaration TEXT NOT NULL,
    command_text TEXT,
    status TEXT NOT NULL,
    returncode INTEGER,
    duration_seconds REAL NOT NULL,
    output_tail TEXT NOT NULL,
    output_truncated INTEGER NOT NULL CHECK (output_truncated IN (0, 1)),
    reason TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(task_id),
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    host_kind TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL,
    source_worktree_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS pv_ancestry (
    child_candidate_id TEXT PRIMARY KEY,
    parent_accepted_pv TEXT,
    proposed_pv TEXT NOT NULL,
    run_id TEXT,
    parent_manifest_sha256 TEXT,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS hil_decisions (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    correction_delta TEXT,
    research_question TEXT,
    reason TEXT,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    pointer_generation_before INTEGER NOT NULL,
    pointer_generation_after INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS provenance (
    provenance_id INTEGER PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    output_kind TEXT NOT NULL,
    output_identity TEXT NOT NULL,
    authority TEXT NOT NULL,
    sha256 TEXT,
    details_json TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS derived_summaries (
    summary_id INTEGER PRIMARY KEY,
    summary_kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    source_query TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS files_path_idx ON files(path);
CREATE INDEX IF NOT EXISTS chunks_file_idx ON chunks(file_id, ordinal);
CREATE INDEX IF NOT EXISTS imports_module_idx ON imports(module);
CREATE INDEX IF NOT EXISTS dependencies_name_idx ON dependencies(name);
CREATE INDEX IF NOT EXISTS routes_path_idx ON routes(path_pattern);
