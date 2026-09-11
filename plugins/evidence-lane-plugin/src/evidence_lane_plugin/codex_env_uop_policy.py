"""Direct Codex and Evidence Lane ENV/UOP source contracts.

These catalogs contain only current host, project, workflow, action, tool and
operation-policy concepts. No predecessor behavior catalog is loaded.
"""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _column(name, kind="TEXT", *, primary_key=0, nullable=False):
    return {"name": name, "type": kind, "not_null": not nullable, "primary_key": primary_key}


def _table(columns, rows=()):
    return {"columns": columns, "rows": list(rows)}


def _catalog(role, tables):
    body = {
        "schema": f"evidence-lane.{role}-codex-policy.v4",
        "role": role,
        "source": "src/evidence_lane_plugin/codex_env_uop_policy.py",
        "source_model": "direct_current_codex_and_evidence_lane_contracts",
        "runtime_mutation_allowed": False,
        "project_payload_allowed": False,
        "declared_policy_is_execution_evidence": False,
        "legacy_translation_layer": False,
        "tables": tables,
    }
    return {**body, "catalog_sha256": hashlib.sha256(_json(body).encode()).hexdigest()}


PROJECT_CLASSES = (
    ("CODE_REPOSITORY", ["local_code", "github_code"], "changed code and owning checks", "source-first implementation"),
    ("DATA_PROJECT", ["data", "data_excel", "tableau", "power_bi", "custom"], "schema, value and output checks", "typed data workflow"),
    ("DOCUMENT_PROJECT", ["docs", "ppt", "pdf_ocr"], "content, structure and render checks", "document fidelity workflow"),
    ("MEDIA_PROJECT", ["images_ocr", "artifacts"], "metadata, extraction and output checks", "media provenance workflow"),
    ("RESEARCH_STUDY", ["research", "sources"], "source, citation and attribution checks", "research evidence workflow"),
    ("MIXED_PROJECT", ["local_code", "github_code", "data", "data_excel", "docs", "ppt", "pdf_ocr", "images_ocr", "research", "artifacts", "custom"], "checks selected by affected owning lanes", "ordered mixed workflow"),
    ("CUSTOM_PROJECT", ["custom"], "explicit user-defined schema and acceptance checks", "explicit custom workflow"),
)


WORKFLOW_STAGES = (
    ("USER_INTENT", 1, "User request and current instructions", "Codex host", "attributed intent"),
    ("HOST_CONTEXT", 2, "Measured host and client context", "host_routing and connections", "host observation"),
    ("PROJECT_BINDING", 3, "Explicit project and storage selection", "projects and storage_selection", "project binding"),
    ("SKILL_RESOLUTION", 4, "Business-intent skill selection", "registry.WORKFLOWS", "selected workflow"),
    ("ACTION_CONTRACT", 5, "Typed action and lane contract", "registry.ActionRegistry", "validated request"),
    ("PLAN_STATE", 6, "Current Plan revision and task contract", "plan_runtime", "pinned work state"),
    ("UOP_ADMISSION", 7, "Permission and operation-policy gates", "env_uop_tool_routing", "admission decision"),
    ("TOOL_SELECTION", 8, "Ready primary or same-contract fallback", "tool_routes", "selected route"),
    ("EXECUTION", 9, "One writer and owned OS workers", "coordination and workers", "bounded effect"),
    ("VERIFICATION", 10, "Current acceptance and effect reconciliation", "acceptance and adaptive_delta_exit", "verified result"),
    ("PLAN_PROJECTION", 11, "Plan database then exact linked PLAN.md", "plan_runtime", "full host Plan projection"),
    ("EVIDENCE_PUBLICATION", 12, "Coherent lane heads, receipts, memory and lessons", "lane_transactions", "published evidence"),
    ("STUDIO_OBSERVATION", 13, "Read-only live Studio snapshot", "studio_gateway", "human-visible observation"),
)


REQUIRED_GATES = (
    ("USER_AUTHORITY", "all operations", "current user instructions and explicit decisions retain precedence", "USER_AUTHORITY_UNAVAILABLE"),
    ("CONNECTION_SCOPE", "all operations", "use the authenticated current engine client", "CLIENT_SESSION_EXPIRED"),
    ("PROJECT_BINDING", "project operations", "use the explicitly selected project and current grant", "PROJECT_NOT_SELECTED"),
    ("PERMISSION", "all operations", "require the exact action permission", "PERMISSION_DENIED"),
    ("PLAN_TASK", "task work", "bind execution to the current Plan revision and task", "DELTA_REQUIRED"),
    ("SOURCE_CURRENTNESS", "source effects", "verify selected source bytes and route before effect", "SOURCE_CHANGED"),
    ("TOOL_READINESS", "tool operations", "measure every required tool before invocation", "PIPELINE_NOT_READY"),
    ("SAME_CONTRACT_FALLBACK", "fallback", "use only declared pre-invocation same-contract fallbacks", "FALLBACK_UNAVAILABLE"),
    ("EFFECT_RECONCILIATION", "mutations", "reconcile uncertain external effects before retry", "EFFECT_OUTCOME_UNCERTAIN"),
    ("VERIFICATION", "completion", "require current named acceptance checks and evidence", "VERIFICATION_FAILED"),
    ("HOST_PLAN_PROJECTION", "Plan mutation", "commit Plan database first, then atomically update the exact bound PLAN.md", "HOST_PLAN_PROJECTION_PENDING"),
    ("DISCLOSURE", "all outputs", "emit only permitted attributed data and no raw secrets", "DISCLOSURE_BLOCKED"),
)


WORKFLOW_EVENTS = (
    ("PROMPT_ENTRY", "intent", "visible source is attributed", "PROMPT_SOURCE_MISMATCH"),
    ("BOOT_OR_RESUME", "session", "runtime and selected project binding verify", "SESSION_BINDING_REQUIRED"),
    ("PLAN_MUTATION", "plan", "current Plan revision and exact change contract verify", "PLAN_SOURCE_CHANGED"),
    ("STEER_ENTRY", "plan", "semantic source and safe checkpoint verify", "SEMANTIC_STEER_REQUIRED"),
    ("DELTA_ENTRY", "work", "current task, tools, source and writer verify", "DELTA_ADMISSION_FAILED"),
    ("DELTA_EXIT", "work", "current task acceptance checks and owning-lane commits verify", "DELTA_EXIT_BLOCKED"),
    ("MID_DELTA_QUERY", "query", "read-only bounded query contract verifies", "QUERY_SCOPE_DENIED"),
    ("HOST_PLAN_PROJECTION", "plan", "exact bound path and full row digest verify", "HOST_PLAN_PROJECTION_PENDING"),
    ("WORK_HANDOFF_BOUNDARY", "handoff", "exact current continuation and native destination evidence are independently verified", "WORK_HANDOFF_EVIDENCE_REQUIRED"),
    ("GOAL_COMPLETION_BOUNDARY", "goal", "visible user decision and native Goal completion evidence are independently verified", "GOAL_COMPLETION_EVIDENCE_REQUIRED"),
    ("RECOVERY", "recovery", "effect or storage evidence verifies before action", "RECOVERY_REQUIRED"),
    ("EXIT_BOOT", "session", "safe session boundary verifies", "SESSION_EXIT_BLOCKED"),
)


def env_catalog():
    columns = {
        "env_authority_meta": [_column("key", primary_key=1), _column("value")],
        "codex_host_variant_v4": [_column("host_id", primary_key=1), _column("host_profile"), _column("app_variant"), _column("application_id"), _column("lifetime"), _column("native_mcp", "INTEGER"), _column("tunnel_policy"), _column("status")],
        "env_accelerator_profile_v4": [_column("provider_id", primary_key=1), _column("vendor_plugin"), _column("runtime"), _column("eligible_action_classes_json"), _column("default_memory_budget_percent", "INTEGER"), _column("selection_rule"), _column("provider_is_tool", "INTEGER"), _column("provider_is_agent", "INTEGER"), _column("status")],
        "env_tool_registry_v4": [_column("tool_id", primary_key=1), _column("requirement"), _column("surfaces_json"), _column("role"), _column("agent_authority", "INTEGER"), _column("status")],
        "env_action_binding_v4": [_column("action_name", primary_key=1), _column("workflow_classes_json"), _column("owner_skill"), _column("internal_sdk_json"), _column("mcp_json"), _column("skill_workflows_json"), _column("entry_event"), _column("ordered_tools_json"), _column("schema_sha256"), _column("binding_sha256"), _column("status")],
        "env_sdk_action_binding_v4": [_column("action_name", primary_key=1), _column("internal_sdk_json"), _column("outer_route_json"), _column("status")],
        "env_mcp_action_binding_v4": [_column("action_name", primary_key=1), _column("server_identity"), _column("tool_name"), _column("status")],
        "env_lane_binding_v4": [_column("lane_id", primary_key=1), _column("source_types_json"), _column("parser_id"), _column("chunker_version"), _column("fts_table"), _column("ordered_tools_json"), _column("binding_sha256"), _column("status"), _column("action_names_json"), _column("action_classes_json")],
        "env_skill_binding_v4": [_column("skill_name", primary_key=1), _column("description"), _column("workflow_json"), _column("member_count", "INTEGER"), _column("routing_manifest_sha256"), _column("status")],
        "env_hook_binding_v4": [_column("event_name", primary_key=1), _column("event_number", "INTEGER"), _column("handler_count", "INTEGER"), _column("event_path"), _column("event_sha256"), _column("status")],
        "env_project_class_policy_v4": [_column("project_class", primary_key=1), _column("lane_ids_json"), _column("ci_strategy"), _column("decision_strategy"), _column("status")],
        "env_source_lane_classification_v4": [_column("lane_id", primary_key=1), _column("lane_kind"), _column("source_types_json"), _column("owner_folder"), _column("status")],
        "env_workflow_policy_v4": [_column("workflow_id", primary_key=1), _column("skill_name"), _column("title"), _column("description"), _column("action_names_json"), _column("status")],
        "env_work_policy_v4": [_column("work_id", primary_key=1), _column("work_name"), _column("lane_templates_json"), _column("scan_order"), _column("unit_of_work"), _column("workflow_order"), _column("recursive_loop"), _column("validation_gate"), _column("exit_write_target"), _column("action_classes_json"), _column("ci_cd_required", "INTEGER"), _column("status")],
        "env_codex_workflow_stage_v4": [_column("stage_id", primary_key=1), _column("ordinal", "INTEGER"), _column("label"), _column("source_owner"), _column("output_contract"), _column("status")],
        "env_codex_workflow_edge_v4": [_column("edge_id", primary_key=1), _column("source_stage"), _column("target_stage"), _column("condition"), _column("status")],
        "env_studio_binding_v4": [_column("binding_id", primary_key=1), _column("platform"), _column("human_access"), _column("source_owner"), _column("mutation_allowed", "INTEGER"), _column("status")],
        "env_plan_projection_binding_v4": [_column("binding_id", primary_key=1), _column("source_owner"), _column("destination"), _column("full_list", "INTEGER"), _column("partial_window", "INTEGER"), _column("write_order"), _column("collision_policy"), _column("status")],
        "env_action_plane_build_receipt": [_column("sequence", "INTEGER", primary_key=1), _column("table_count", "INTEGER"), _column("tool_count", "INTEGER"), _column("action_count", "INTEGER"), _column("lane_count", "INTEGER"), _column("skill_count", "INTEGER"), _column("hook_event_count", "INTEGER"), _column("host_variant_count", "INTEGER"), _column("foreign_surface_row_count", "INTEGER"), _column("receipt_json"), _column("receipt_sha256"), _column("recorded_at")],
    }
    tables = {name: _table(value) for name, value in columns.items()}
    tables["env_project_class_policy_v4"]["rows"] = [
        {"project_class": name, "lane_ids_json": _json(lanes), "ci_strategy": checks,
         "decision_strategy": decision, "status": "ACTIVE"}
        for name, lanes, checks, decision in PROJECT_CLASSES
    ]
    tables["env_codex_workflow_stage_v4"]["rows"] = [
        {"stage_id": identity, "ordinal": ordinal, "label": label, "source_owner": owner,
         "output_contract": output, "status": "ACTIVE"}
        for identity, ordinal, label, owner, output in WORKFLOW_STAGES
    ]
    tables["env_codex_workflow_edge_v4"]["rows"] = [
        {"edge_id": f"CODEX_STAGE_{index:02d}", "source_stage": left[0], "target_stage": right[0],
         "condition": "current contract and preceding stage pass", "status": "ACTIVE"}
        for index, (left, right) in enumerate(pairwise(WORKFLOW_STAGES), 1)
    ]
    tables["env_studio_binding_v4"]["rows"] = [{"binding_id": "WINDOWS_STUDIO", "platform": "Windows",
        "human_access": "visible_read_only", "source_owner": "apps/evidence-lane-studio and studio_gateway",
        "mutation_allowed": 0, "status": "DECLARED_REQUIRES_INSTALLATION"}]
    tables["env_plan_projection_binding_v4"]["rows"] = [{"binding_id": "PROJECT_PLAN_TO_CODEX_PLAN",
        "source_owner": "project Plan lane database", "destination": "exact explicitly bound Codex PLAN.md",
        "full_list": 1, "partial_window": 0, "write_order": "database commit then atomic host file replace",
        "collision_policy": "preserve changed host file and keep projection pending", "status": "ACTIVE"}]
    return _catalog("env", tables)


def uop_catalog():
    columns = {
        "uop_authority_meta": [_column("key", primary_key=1), _column("value")],
        "uop_host_policy_v4": [_column("host_id", primary_key=1), _column("host_profile"), _column("execution_allowed", "INTEGER"), _column("reason"), _column("status")],
        "uop_accelerator_policy_v4": [_column("provider_id", primary_key=1), _column("vendor_plugin_grant_required", "INTEGER"), _column("memory_budget_configurable", "INTEGER"), _column("default_memory_budget_percent", "INTEGER"), _column("max_memory_budget_percent", "INTEGER"), _column("telemetry_required", "INTEGER"), _column("throttle_blocks_execution", "INTEGER"), _column("cpu_fallback_required", "INTEGER"), _column("authority_effect", "INTEGER"), _column("status")],
        "uop_tool_policy_v4": [_column("tool_id", primary_key=1), _column("requirement"), _column("grant_required", "INTEGER"), _column("locality_expiry_required", "INTEGER"), _column("agent_authority", "INTEGER"), _column("selection_rule"), _column("status")],
        "uop_action_policy_v4": [_column("action_name", primary_key=1), _column("read_only", "INTEGER"), _column("destructive", "INTEGER"), _column("idempotent", "INTEGER"), _column("requires_exact_user_decision", "INTEGER"), _column("direct_purge_required", "INTEGER"), _column("authority_effects_json"), _column("status")],
        "uop_fallback_policy_v4": [_column("workflow_class", primary_key=1), _column("fallback_rule"), _column("cross_class_fallback_allowed", "INTEGER"), _column("silent_fallback_allowed", "INTEGER"), _column("status")],
        "uop_required_gate_v4": [_column("gate_id", primary_key=1), _column("applies_to"), _column("rule"), _column("failure_code"), _column("status")],
        "uop_workflow_gate_v4": [_column("event_id", primary_key=1), _column("stage"), _column("predicate"), _column("failure_code"), _column("status")],
        "uop_project_class_validation_policy_v4": [_column("project_class", primary_key=1), _column("project_validation"), _column("learning_validation"), _column("plan_acceptance_separate", "INTEGER"), _column("direct_purge_required", "INTEGER"), _column("status")],
        "uop_permission_policy_v4": [_column("permission", primary_key=1), _column("grant_owner"), _column("checked_at"), _column("may_mutate", "INTEGER"), _column("status")],
        "uop_task_state_policy_v4": [_column("task_state", primary_key=1), _column("host_status"), _column("execution_allowed", "INTEGER"), _column("terminal", "INTEGER"), _column("transition_owner"), _column("status")],
        "uop_verification_policy_v4": [_column("policy_id", primary_key=1), _column("applies_to"), _column("requirement"), _column("status")],
        "uop_plan_projection_policy_v4": [_column("policy_id", primary_key=1), _column("source_owner"), _column("write_order"), _column("full_list_required", "INTEGER"), _column("partial_window_allowed", "INTEGER"), _column("failure_state"), _column("status")],
        "uop_disclosure_policy_v4": [_column("policy_id", primary_key=1), _column("applies_to"), _column("rule"), _column("status")],
        "uop_source_policy_v4": [_column("policy_id", primary_key=1), _column("applies_to"), _column("rule"), _column("status")],
        "uop_work_classification_policy_v4": [_column("work_id", primary_key=1), _column("selection_rule"), _column("authorizes_execution", "INTEGER"), _column("creates_sector", "INTEGER"), _column("status")],
        "uop_action_plane_build_receipt": [_column("sequence", "INTEGER", primary_key=1), _column("table_count", "INTEGER"), _column("gate_count", "INTEGER"), _column("action_count", "INTEGER"), _column("tool_count", "INTEGER"), _column("host_variant_count", "INTEGER"), _column("foreign_surface_row_count", "INTEGER"), _column("receipt_json"), _column("receipt_sha256"), _column("recorded_at")],
    }
    tables = {name: _table(value) for name, value in columns.items()}
    classes = {
        "GOVERNANCE", "RUNTIME", "CODE", "RETRIEVAL", "DOCUMENT", "DATA",
        "OCR_MEDIA", "WEB_RESEARCH", "RECOVERY", "SOURCE_ROUTING",
    }
    tables["uop_fallback_policy_v4"]["rows"] = [{"workflow_class": name,
        "fallback_rule": "declared same-contract route before invocation only",
        "cross_class_fallback_allowed": 0, "silent_fallback_allowed": 0, "status": "ACTIVE"}
        for name in sorted(classes)]
    tables["uop_required_gate_v4"]["rows"] = [{"gate_id": identity, "applies_to": applies,
        "rule": rule, "failure_code": failure, "status": "ACTIVE"}
        for identity, applies, rule, failure in REQUIRED_GATES]
    tables["uop_workflow_gate_v4"]["rows"] = [{"event_id": identity, "stage": stage,
        "predicate": predicate, "failure_code": failure, "status": "ACTIVE"}
        for identity, stage, predicate, failure in WORKFLOW_EVENTS]
    tables["uop_project_class_validation_policy_v4"]["rows"] = [{"project_class": name,
        "project_validation": checks, "learning_validation": "evidence-backed verified work only",
        "plan_acceptance_separate": 1, "direct_purge_required": 0, "status": "ACTIVE"}
        for name, _, checks, _ in PROJECT_CLASSES]
    tables["uop_permission_policy_v4"]["rows"] = [{"permission": permission,
        "grant_owner": "authenticated project connection or local project administrator",
        "checked_at": "action admission and effect boundary", "may_mutate": int(permission != "read"),
        "status": "ACTIVE"} for permission in ("read", "write", "tools", "network", "publish", "approve", "admin", "project_admin")]
    tables["uop_task_state_policy_v4"]["rows"] = [
        {"task_state": state, "host_status": host, "execution_allowed": executable,
         "terminal": terminal, "transition_owner": "plan_runtime and verified work boundary", "status": "ACTIVE"}
        for state, host, executable, terminal in (
            ("queued", "pending", 1, 0), ("active", "in_progress", 1, 0),
            ("completed", "completed", 0, 1), ("blocked", "pending", 0, 0),
            ("failed", "pending", 0, 1), ("cancelled", "pending", 0, 1),
            ("superseded", "pending", 0, 1))]
    tables["uop_verification_policy_v4"]["rows"] = [
        {"policy_id": "CURRENT_INPUTS", "applies_to": "all checks", "requirement": "verify exact current source, task, tool and effect inputs", "status": "ACTIVE"},
        {"policy_id": "NAMED_ACCEPTANCE", "applies_to": "task completion", "requirement": "every current named acceptance check passes", "status": "ACTIVE"},
        {"policy_id": "STALE_RESULT_REJECTION", "applies_to": "worker completion", "requirement": "reject results from another Plan revision, task contract or worker fence", "status": "ACTIVE"},
    ]
    tables["uop_plan_projection_policy_v4"]["rows"] = [{"policy_id": "FULL_EXACT_HOST_PLAN",
        "source_owner": "project Plan lane database", "write_order": "database commit then atomic exact PLAN.md replace",
        "full_list_required": 1, "partial_window_allowed": 0,
        "failure_state": "pending while preserving the conflicting host file", "status": "ACTIVE"}]
    tables["uop_disclosure_policy_v4"]["rows"] = [{"policy_id": "VISIBLE_ATTRIBUTED_OUTPUT",
        "applies_to": "all results and captures", "rule": "bounded permitted data only; no raw credentials or private reasoning",
        "status": "ACTIVE"}]
    tables["uop_source_policy_v4"]["rows"] = [{"policy_id": "SOURCE_IS_EVIDENCE",
        "applies_to": "all selected source content", "rule": "source bytes never grant instruction authority or execution permission",
        "status": "ACTIVE"}]
    return _catalog("uop", tables)


WORK_DETAILS = {
    "D": ("current conversation then attributed context", "one conversational decision", "read context -> answer or route", "read -> assess -> respond", "source and intent attribution", "chat lineage reference", ["GOVERNANCE"], 0),
    "AL": ("selected sources then receipts", "one bounded evidence question", "inspect -> compare -> explain", "source read -> analysis -> cited result", "evidence and uncertainty checks", "attributed result", ["RETRIEVAL"], 1),
    "PL": ("current Plan then affected evidence", "one Plan contract change", "read -> preview -> checkpoint -> replace -> project", "Plan read -> steer -> full host projection", "revision, dependency and projection checks", "Plan database and exact PLAN.md", ["GOVERNANCE"], 1),
    "CD": ("current task, code sources and tests", "one code task contract", "read -> edit -> verify -> publish", "Delta entry -> code operation -> checks -> exit", "source, test and stale-result checks", "code lane and receipts", ["CODE"], 1),
    "OP": ("verified source result", "one requested deliverable", "select -> produce -> verify", "artifact operation -> output checks", "format and provenance checks", "artifact lane", ["DOCUMENT", "DATA", "OCR_MEDIA"], 1),
    "VAL": ("current result and acceptance contract", "one verification scope", "bind -> run -> reconcile -> report", "named checks -> evidence summary", "all selected checks pass", "receipts lane", ["GOVERNANCE"], 1),
    "RS": ("research question and selected sources", "one bounded research question", "discover -> capture -> extract -> assess", "source discovery -> evidence capture -> citation", "citation, source and recency checks", "research and sources lanes", ["WEB_RESEARCH", "RETRIEVAL"], 1),
    "JD": ("selected job description and project evidence", "one evidence-backed job task", "inspect -> match -> produce -> verify", "source intake -> document/custom operation", "source attribution and claim checks", "document or custom lane", ["DOCUMENT", "DATA"], 1),
    "XL": ("selected workbook or table", "one spreadsheet/data operation", "inspect -> change or generate -> recalculate -> verify", "data operation -> value and render checks", "cell calculation lineage, values and output integrity", "spreadsheet/data lane", ["DATA"], 1),
    "PPT": ("selected presentation sources", "one presentation operation", "inspect -> edit or generate -> render -> verify", "presentation operation -> fidelity checks", "content, structure and render checks", "presentation lane", ["DOCUMENT"], 1),
    "DOC": ("selected document sources", "one document operation", "inspect -> edit or generate -> render -> verify", "document operation -> fidelity checks", "content, structure and render checks", "document lane", ["DOCUMENT"], 1),
    "PB": ("explicitly selected structured source", "one read-only structured-source scope", "inspect schema -> read selected rows -> attribute", "selected SQLite/custom operation", "schema, journal and row-bound checks", "custom lane", ["DATA", "RETRIEVAL"], 1),
    "ENG": ("ordered source selection", "one source registration or refresh", "classify -> register -> inspect -> prepare", "source action -> owning lane route", "identity, byte and route checks", "sources and selected sector lanes", ["RETRIEVAL"], 1),
    "CE": ("current session and work boundary", "one explicit close request", "inspect -> verify safe boundary -> close", "session status -> exit boundary -> session exit", "unfinished work and projection checks", "receipts and chat lineage", ["RUNTIME"], 0),
    "RCV": ("current recovery evidence", "one bounded recovery action", "inspect -> reconcile -> verify", "recovery preview -> explicit action -> readback", "identity, effect and storage checks", "recovery receipts", ["RECOVERY"], 1),
    "X": ("explicit user-defined schema", "one custom work contract", "validate schema -> select lanes -> execute declared action", "custom classification -> Plan binding -> current operation", "explicit dependency and acceptance checks", "selected owning lanes", ["GOVERNANCE"], 1),
}


def work_policy(definition):
    scan, unit, order, loop, gate, target, classes, ci = WORK_DETAILS[definition["id"]]
    return {"work_id": definition["id"], "work_name": definition["name"],
        "lane_templates": list(definition["lanes"]), "scan_order": scan,
        "unit_of_work": unit, "workflow_order": order, "recursive_loop": loop,
        "validation_gate": gate, "exit_write_target": target,
        "action_classes": classes, "ci_cd_required": bool(ci),
        "selection_authorizes_work": False, "creates_sector": False}


__all__ = ["env_catalog", "uop_catalog", "work_policy"]
