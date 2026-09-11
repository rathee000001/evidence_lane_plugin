-- Projection: runtime applies the real ordered migrations under its project writer.

-- plan v1, digest 894079043dd019dfeba959e2775a2d8c7503ad08b384ff8e7c5ac5ad7387666c
CREATE TABLE plan_revisions (
        revision INTEGER PRIMARY KEY, plan_id TEXT NOT NULL, title TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE, document_json TEXT NOT NULL CHECK(json_valid(document_json)),
        document_digest TEXT NOT NULL, parent_revision INTEGER REFERENCES plan_revisions(revision),
        actor_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE plan_current (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL REFERENCES plan_revisions(revision), event_head TEXT);
CREATE TABLE plan_tasks (
        revision INTEGER NOT NULL REFERENCES plan_revisions(revision), task_id TEXT NOT NULL,
        position INTEGER NOT NULL, definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
        contract_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
          ('queued','active','completed','blocked','failed','cancelled','superseded')),
        last_event TEXT, PRIMARY KEY(revision,task_id), UNIQUE(revision,position));
CREATE UNIQUE INDEX plan_one_active ON plan_tasks(revision) WHERE state='active';
CREATE TABLE plan_dependencies (
        revision INTEGER NOT NULL, task_id TEXT NOT NULL, dependency_id TEXT NOT NULL,
        PRIMARY KEY(revision,task_id,dependency_id),
        FOREIGN KEY(revision,task_id) REFERENCES plan_tasks(revision,task_id),
        FOREIGN KEY(revision,dependency_id) REFERENCES plan_tasks(revision,task_id));
CREATE TABLE plan_events (
        sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
        revision INTEGER NOT NULL REFERENCES plan_revisions(revision), task_id TEXT,
        kind TEXT NOT NULL, actor_id TEXT NOT NULL, payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        previous_digest TEXT, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE INDEX plan_task_events ON plan_events(revision,task_id,sequence);
-- plan v2, digest cb9132a93a80e1dc059d9cf60e1a12cd7b0bbf636db1d22cc0334b56e9ae3543
CREATE INDEX plan_task_identity ON plan_tasks(task_id);
-- plan v3, digest d8bca37d7340c6873d3a4d3dc58cc43bbe157c124b02d708f5d303ee74bbdb79
CREATE TABLE plan_host_bindings (
        binding_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        host_task_id TEXT NOT NULL, host_plan_id TEXT NOT NULL, plan_path TEXT NOT NULL,
        current_sha256 TEXT NOT NULL CHECK(length(current_sha256)=64),
        bound_by TEXT NOT NULL, binding_digest TEXT NOT NULL UNIQUE,
        active INTEGER NOT NULL CHECK(active IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX plan_one_active_host_binding ON plan_host_bindings(active) WHERE active=1;
CREATE TABLE plan_host_projections (
        sequence INTEGER PRIMARY KEY, projection_id TEXT NOT NULL UNIQUE,
        binding_id TEXT REFERENCES plan_host_bindings(binding_id),
        plan_revision INTEGER NOT NULL REFERENCES plan_revisions(revision),
        plan_event_head TEXT NOT NULL, document_digest TEXT NOT NULL,
        rows_json TEXT NOT NULL CHECK(json_valid(rows_json)),
        rows_digest TEXT NOT NULL CHECK(length(rows_digest)=64),
        markdown_text TEXT NOT NULL, markdown_sha256 TEXT NOT NULL CHECK(length(markdown_sha256)=64),
        state TEXT NOT NULL CHECK(state IN ('unbound','pending','publishing','confirmed','superseded')),
        previous_projection_id TEXT, prepared_by TEXT NOT NULL, prepared_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        file_before_sha256 TEXT, file_after_sha256 TEXT,
        confirmed_at TEXT);
CREATE INDEX plan_host_projection_source ON plan_host_projections(plan_revision,plan_event_head,sequence);
-- steer v1, digest 8f581fcc260acd8ac31bcae96e672128a7c359cd5f4bf15f55236c9b17effc88
CREATE TABLE steer_requests (
        request_id TEXT PRIMARY KEY, source_event_id TEXT NOT NULL, source_cursor TEXT NOT NULL,
        actor_id TEXT NOT NULL, expected_revision INTEGER NOT NULL, intent TEXT NOT NULL CHECK(intent IN ('semantic','stop')),
        affected_json TEXT NOT NULL CHECK(json_valid(affected_json)), rationale TEXT NOT NULL,
        identity_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
           ('pending','checkpointing','ready','applied','rejected','superseded')),
        checkpoint_object TEXT REFERENCES objects(digest), applied_revision INTEGER,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX steer_pending_order ON steer_requests(state,created_at,request_id);
-- steer v2, digest f18464473503f7c5f47eb38b76e29ddbc0599f031cd4d58cc44da50632ba8794
CREATE TABLE steer_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), paused INTEGER NOT NULL CHECK(paused IN (0,1)),
        source_event_id TEXT NOT NULL, source_cursor TEXT NOT NULL, actor_id TEXT NOT NULL, updated_at TEXT NOT NULL);
-- steer v3, digest badaa0d992b56a5754a2030e8b3c8b75f1ac708cc887b4049fa6c21af90cd725
CREATE TABLE steer_interrupts (
        event_id TEXT PRIMARY KEY, source_cursor TEXT NOT NULL, actor_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL, created_at TEXT NOT NULL);
-- validation v1, digest 9324f52e96ea1399d043542b060878bf4df406a6b9574d4f23707cc70d7057fc
CREATE TABLE validation_policy_revisions (
        revision INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        request_digest TEXT NOT NULL, policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
        policy_digest TEXT NOT NULL, previous_digest TEXT, revision_digest TEXT NOT NULL UNIQUE,
        actor_id TEXT NOT NULL, receipt_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE validation_policy_current (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        revision INTEGER NOT NULL REFERENCES validation_policy_revisions(revision));
-- validationrun v1, digest 7339ca8cc9855102dd8192a6e87ec75fda0d2a80e6dbde7f4af58ef4ade4da2a
CREATE TABLE validationrun_summaries (
        job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
        contract_digest TEXT NOT NULL, summary_object TEXT NOT NULL, receipt_id TEXT NOT NULL UNIQUE);
-- jobs v1, digest 302141ce9d37d1d0dc59824f808b4cd733884e8a9f785ee827d657029d756e60
CREATE TABLE jobs_jobs (
            job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            client_id TEXT NOT NULL, request_digest TEXT NOT NULL,
            request_json TEXT NOT NULL CHECK(json_valid(request_json)),
            action TEXT NOT NULL, plan_revision INTEGER,
            state TEXT NOT NULL CHECK(state IN
                ('queued','running','checkpointed','cancelled','succeeded','failed','uncertain','superseded')),
            owner_fence INTEGER, execution_id TEXT, phase TEXT NOT NULL DEFAULT 'admitted',
            checkpoint_object TEXT REFERENCES objects(digest), resumable INTEGER NOT NULL DEFAULT 0,
            cancellation_reason TEXT, result_json TEXT CHECK(json_valid(result_json)),
            error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX jobs_queue_order ON jobs_jobs(state,created_at,job_id);
CREATE TABLE jobs_effects (
            effect_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs_jobs(job_id),
            effect_key TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','confirmed','absent')),
            description TEXT NOT NULL, evidence_object TEXT REFERENCES objects(digest),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id,effect_key));
CREATE INDEX jobs_effect_state ON jobs_effects(job_id,state);
-- delta v1, digest 9b8208d409cb3e1576262dc0e73e33cb67da5ca79809fc6eeb872fe51221235e
CREATE TABLE delta_runs (
       job_id TEXT PRIMARY KEY REFERENCES jobs_jobs(job_id), request_id TEXT NOT NULL UNIQUE,
       actor_id TEXT NOT NULL, task_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
       contract_digest TEXT NOT NULL, binding_digest TEXT NOT NULL, entry_object TEXT NOT NULL REFERENCES objects(digest),
       state TEXT NOT NULL CHECK(state IN ('queued','running','awaiting_verification','blocked','verified')),
       result_object TEXT REFERENCES objects(digest), error_code TEXT,
       created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(plan_revision,task_id),
       FOREIGN KEY(plan_revision,task_id) REFERENCES plan_tasks(revision,task_id));
-- delta v2, digest 0850746cd0b0c4d395f7cc8aa09ff28acaa1eb70fdea8c2551690e6d41f3242f
CREATE TABLE delta_exits (job_id TEXT PRIMARY KEY REFERENCES delta_runs(job_id),
       verification_object TEXT NOT NULL REFERENCES objects(digest),
       receipt_id TEXT NOT NULL UNIQUE, next_task_id TEXT, created_at TEXT NOT NULL);
