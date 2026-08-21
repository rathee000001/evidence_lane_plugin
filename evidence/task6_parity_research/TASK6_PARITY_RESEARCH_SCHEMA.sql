PRAGMA foreign_keys = ON;
PRAGMA journal_mode = DELETE;
PRAGMA synchronous = FULL;

CREATE TABLE IF NOT EXISTS research_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS research_step (
    step_no INTEGER PRIMARY KEY CHECK (step_no BETWEEN 1 AND 25),
    task_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    dependency_task_ids TEXT NOT NULL,
    scope TEXT NOT NULL,
    acceptance_check TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'completed')),
    evidence_locator TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS research_step_history (
    event_id TEXT PRIMARY KEY,
    step_no INTEGER NOT NULL REFERENCES research_step(step_no),
    from_status TEXT CHECK (from_status IS NULL OR from_status IN ('pending', 'in_progress', 'completed')),
    to_status TEXT NOT NULL CHECK (to_status IN ('pending', 'in_progress', 'completed')),
    evidence_locator TEXT,
    occurred_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS source_authority (
    authority_id TEXT PRIMARY KEY,
    authority_kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT,
    byte_count INTEGER CHECK (byte_count IS NULL OR byte_count >= 0),
    freshness TEXT NOT NULL,
    authority_role TEXT NOT NULL,
    notes TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS capability_route (
    route_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    capability TEXT NOT NULL,
    core_symbol TEXT,
    core_status TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    mcp_tool TEXT,
    mcp_status TEXT NOT NULL,
    sdk_module TEXT,
    sdk_operation TEXT,
    sdk_status TEXT NOT NULL,
    skill_path TEXT,
    skill_status TEXT NOT NULL,
    command_path TEXT,
    command_status TEXT NOT NULL,
    installed_status TEXT NOT NULL,
    runtime_status TEXT NOT NULL,
    test_status TEXT NOT NULL,
    test_locators TEXT NOT NULL,
    gap_class TEXT NOT NULL,
    decision TEXT NOT NULL,
    proposed_delta_key TEXT,
    notes TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS delta_crosswalk (
    row_number INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    plan_sequence INTEGER NOT NULL UNIQUE,
    lifecycle_status TEXT NOT NULL,
    task_class TEXT NOT NULL,
    requested_outcome TEXT NOT NULL,
    permitted_paths_json TEXT NOT NULL,
    permitted_tools_json TEXT NOT NULL,
    acceptance_checks_json TEXT NOT NULL,
    stop_condition TEXT NOT NULL,
    panel_role TEXT NOT NULL,
    steer_delta_count INTEGER NOT NULL CHECK (steer_delta_count >= 0),
    dependency_task_ids_json TEXT NOT NULL,
    native_locator TEXT NOT NULL,
    code_match_status TEXT NOT NULL,
    code_locators TEXT NOT NULL,
    test_locators TEXT NOT NULL,
    route_gap_status TEXT NOT NULL,
    research_notes TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS lane_contract (
    lane_id TEXT PRIMARY KEY,
    sqlite_filename TEXT NOT NULL,
    fts_table TEXT NOT NULL,
    mmd_filename TEXT NOT NULL,
    dot_filename TEXT NOT NULL,
    refresh_receipt_filename TEXT NOT NULL,
    project_path_template TEXT NOT NULL,
    schema_source_locator TEXT NOT NULL,
    schema_asset_status TEXT NOT NULL,
    mcp_status TEXT NOT NULL,
    skill_binding_status TEXT NOT NULL,
    query_modes TEXT NOT NULL,
    result_bound TEXT NOT NULL,
    parallel_query_status TEXT NOT NULL,
    cross_lane_status TEXT NOT NULL,
    cross_project_status TEXT NOT NULL,
    test_locators TEXT NOT NULL,
    gap_class TEXT NOT NULL,
    notes TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS test_evidence (
    test_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    path TEXT NOT NULL,
    test_selector TEXT NOT NULL,
    evidence_class TEXT NOT NULL,
    status TEXT NOT NULL,
    proves TEXT NOT NULL,
    does_not_prove TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS gap_finding (
    finding_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    severity TEXT NOT NULL,
    classification TEXT NOT NULL,
    statement TEXT NOT NULL,
    evidence_locator TEXT NOT NULL,
    route_gap TEXT NOT NULL,
    required_contract TEXT NOT NULL,
    proposed_delta_key TEXT,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS delta_proposal (
    proposal_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    task_class TEXT NOT NULL,
    group_name TEXT NOT NULL,
    dependency_keys TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    git_boundary TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'verified', 'appended', 'rejected')),
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS delta_normalization_map (
    finding_id TEXT PRIMARY KEY REFERENCES gap_finding(finding_id),
    proposal_id TEXT NOT NULL REFERENCES delta_proposal(proposal_id),
    disposition TEXT NOT NULL CHECK (
        disposition IN ('APPEND_NEW_ROW', 'LINK_EXISTING_ROW')
    ),
    linked_task_id TEXT,
    normalization_reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS native_append_preflight (
    preflight_id TEXT PRIMARY KEY,
    installed_plugin_identity TEXT NOT NULL,
    live_tool TEXT NOT NULL,
    required_operation TEXT NOT NULL,
    supported_operation TEXT NOT NULL,
    metadata_preserved_json TEXT NOT NULL,
    physical_final_preserved INTEGER NOT NULL CHECK (
        physical_final_preserved IN (0, 1)
    ),
    atomic_batch_supported INTEGER NOT NULL CHECK (
        atomic_batch_supported IN (0, 1)
    ),
    status TEXT NOT NULL CHECK (status IN ('PASS', 'BLOCKED')),
    blocker TEXT NOT NULL,
    evidence_locator TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS delta_append_batch (
    batch_id TEXT PRIMARY KEY,
    proposal_count INTEGER NOT NULL,
    append_new_row_count INTEGER NOT NULL,
    link_existing_row_count INTEGER NOT NULL,
    proposal_sha256 TEXT NOT NULL,
    required_insertions_json TEXT NOT NULL,
    native_receipt_json TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('NORMALIZED', 'APPENDED', 'BLOCKED', 'FAILED')
    ),
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS native_plan_receipt (
    receipt_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES delta_proposal(proposal_id),
    operation TEXT NOT NULL CHECK (
        operation IN ('APPEND_BOOTSTRAP_ROW', 'LINK_EXISTING_ROW', 'APPEND_BATCH')
    ),
    linked_task_id TEXT NOT NULL,
    task_count_changed INTEGER NOT NULL CHECK (task_count_changed IN (0, 1)),
    native_status TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS official_contract (
    contract_id TEXT PRIMARY KEY,
    area TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    requirement TEXT NOT NULL,
    local_evidence TEXT NOT NULL,
    conformance_status TEXT NOT NULL,
    proposed_delta_key TEXT,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS conformance_case (
    case_id TEXT PRIMARY KEY,
    contract_id TEXT REFERENCES official_contract(contract_id),
    capability_route_id TEXT,
    test_layer TEXT NOT NULL,
    test_kind TEXT NOT NULL,
    target TEXT NOT NULL,
    selector_or_scenario TEXT NOT NULL,
    expected_result TEXT NOT NULL,
    current_coverage TEXT NOT NULL,
    execution_phase TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_locator TEXT NOT NULL,
    proposed_delta_key TEXT,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS audit_event (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS audit_event_no_update
BEFORE UPDATE ON audit_event
BEGIN
    SELECT RAISE(ABORT, 'audit_event is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_event_no_delete
BEFORE DELETE ON audit_event
BEGIN
    SELECT RAISE(ABORT, 'audit_event is append-only');
END;

CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING fts5(
    doc_id UNINDEXED,
    doc_type,
    title,
    body,
    evidence_locator UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIEW IF NOT EXISTS research_progress AS
SELECT
    COUNT(*) AS total_steps,
    SUM(status = 'completed') AS completed_steps,
    SUM(status = 'in_progress') AS in_progress_steps,
    SUM(status = 'pending') AS pending_steps
FROM research_step;

CREATE VIEW IF NOT EXISTS unresolved_gaps AS
SELECT *
FROM gap_finding
WHERE status NOT IN ('resolved', 'rejected');

CREATE VIEW IF NOT EXISTS unresolved_conformance AS
SELECT *
FROM conformance_case
WHERE status NOT IN ('PASS', 'NOT_APPLICABLE', 'REJECTED');
