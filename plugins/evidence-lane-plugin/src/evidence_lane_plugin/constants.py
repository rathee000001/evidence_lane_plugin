"""Stable schema and protocol constants."""

from __future__ import annotations

ENGINE_NAME = "evidence-lane-universal-pv-engine"
ENGINE_VERSION = "2.2.0"
SCHEMA_VERSION = "3.0.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"2.0.0", "2.1.0", SCHEMA_VERSION})
TOOL_RESULT_SCHEMA = "evidence-lane.pv.tool-result.v1"
LIFECYCLE_RESULT_SCHEMA = "evidence-lane.pv.lifecycle-result.v1"
PV_MANIFEST_SCHEMA = "evidence-lane.universal-pv.manifest.v2"
PV_RECEIPT_SCHEMA = "evidence-lane.universal-pv.receipt.v2"
SESSION_SCHEMA = "evidence-lane.session.v1"
LINEAGE_SCHEMA = "evidence-lane.chat-lineage.event.v1"
POINTER_SCHEMA = "evidence-lane.active-pointer.v1"
PROJECT_REGISTRY_SCHEMA = "evidence-lane.project-registry.v1"

READ_STATUSES = {
    "PASS",
    "PARTIAL",
    "EMPTY",
    "STALE",
    "BLOCKED",
    "MISMATCH",
    "FAIL",
}

PV_REQUIRED_FILES = (
    "code.sqlite",
    "project_master_topology.mmd",
    "project_master_topology.dot",
    "active_pointer.json",
    "project_identity.json",
    "manifest.json",
    "SHA256SUMS.txt",
    "chat_lineage.jsonl",
    "entry_slip.json",
    "exit_slip.json",
    "pv_receipt.json",
    "lanes/manifest.json",
    "lanes/registry.json",
    "lanes/routes.json",
)

PV_OPTIONAL_FILES = (
    "project_master_topology.svg",
    "project_master_topology.png",
)

ENV_UOP_FORBIDDEN_NAMES = {
    "env.json",
    "uop.json",
    "env.sqlite",
    "uop.sqlite",
    "environment_package.json",
    "operator_profile.json",
}

ENV_UOP_FORBIDDEN_PATH_PARTS = {
    "env",
    "uop",
    "session_flash",
}

DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_PACKAGE_BYTES = 512 * 1024 * 1024
DEFAULT_CHUNK_LINES = 80
DEFAULT_CHUNK_OVERLAP = 10
