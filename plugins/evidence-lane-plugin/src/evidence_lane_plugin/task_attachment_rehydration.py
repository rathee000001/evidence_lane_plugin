"""Exact-task attachment migration after an Evidence Lane plugin update.

This is deliberately not State Travel.  A plugin update may replace the
installed public surface, but it may not replace or merge task authority.  The
pure planner below validates every task independently and emits only compatible
per-task attachment migrations.  One malformed task is isolated instead of
degrading correctly bound tasks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

GLOBAL_PLUGIN_UPDATE_REHYDRATION_LAW = "GLOBAL_PLUGIN_UPDATE_REHYDRATION_LAW"
_TASK_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9A-F]{64}$")
_AUTHORITY_ROLES = {"SOLE_WRITER", "READ_ONLY", "HISTORICAL"}


class AttachmentRehydrationError(ValueError):
    """One exact task binding is not compatible with the installed release."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _required_text(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AttachmentRehydrationError(code)
    return text


def _validate_surface(surface: Mapping[str, Any]) -> dict[str, Any]:
    if surface.get("status") != "PASS":
        raise AttachmentRehydrationError("INSTALLED_PUBLIC_SURFACE_NOT_PASS")
    selector = _required_text(surface.get("plugin_selector"), "PLUGIN_SELECTOR_MISSING")
    version = _required_text(surface.get("plugin_version"), "PLUGIN_VERSION_MISSING")
    surface_sha256 = _required_text(
        surface.get("public_surface_sha256"), "PUBLIC_SURFACE_SHA256_MISSING"
    ).upper()
    if not _SHA256.fullmatch(surface_sha256):
        raise AttachmentRehydrationError("PUBLIC_SURFACE_SHA256_INVALID")
    catalog = surface.get("catalog")
    if not isinstance(catalog, Mapping):
        raise AttachmentRehydrationError("PUBLIC_SURFACE_CATALOG_MISSING")
    normalized_catalog: dict[str, int] = {}
    for key in ("tools", "read", "write", "skills", "providers"):
        value = catalog.get(key)
        if not isinstance(value, int) or value < 0:
            raise AttachmentRehydrationError(f"PUBLIC_SURFACE_{key.upper()}_INVALID")
        normalized_catalog[key] = value
    if (
        normalized_catalog["read"] + normalized_catalog["write"]
        != normalized_catalog["tools"]
    ):
        raise AttachmentRehydrationError("PUBLIC_SURFACE_READ_WRITE_TOTAL_MISMATCH")
    if surface.get("hooks_enabled") is not False:
        raise AttachmentRehydrationError("PLUGIN_UPDATE_MUST_NOT_ENABLE_HOOKS")
    return {
        "plugin_selector": selector,
        "plugin_version": version,
        "public_surface_sha256": surface_sha256,
        "catalog": normalized_catalog,
        "hooks_enabled": False,
    }


def _validate_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    task_id = _required_text(binding.get("task_id"), "TASK_ID_MISSING")
    if not _TASK_ID.fullmatch(task_id):
        raise AttachmentRehydrationError("TASK_ID_INVALID")
    if binding.get("task_uri") != f"codex://threads/{task_id}":
        raise AttachmentRehydrationError("TASK_DEEP_LINK_MISMATCH")
    role = _required_text(binding.get("authority_role"), "AUTHORITY_ROLE_MISSING")
    if role not in _AUTHORITY_ROLES:
        raise AttachmentRehydrationError("AUTHORITY_ROLE_INVALID")
    required_identity = {
        key: _required_text(binding.get(key), f"{key.upper()}_MISSING")
        for key in (
            "project_id",
            "evidence_session_id",
            "governed_host_session_id",
            "workspace",
            "execution_profile",
            "plan_goal_binding_sha256",
        )
    }
    if not _SHA256.fullmatch(required_identity["plan_goal_binding_sha256"].upper()):
        raise AttachmentRehydrationError("PLAN_GOAL_BINDING_SHA256_INVALID")
    if binding.get("candidate_created_or_accepted") is not False:
        raise AttachmentRehydrationError("CANDIDATE_STATE_CHANGED")
    if binding.get("hil_inferred") is not False:
        raise AttachmentRehydrationError("HIL_STATE_CHANGED")
    if binding.get("pointer_moved") is not False:
        raise AttachmentRehydrationError("POINTER_STATE_CHANGED")
    return {
        "task_id": task_id,
        "authority_role": role,
        **required_identity,
    }


def plan_global_plugin_update_rehydration(
    bindings: Iterable[Mapping[str, Any]],
    *,
    invoking_task_id: str,
    installed_surface: Mapping[str, Any],
    server_runtime_attestations: Mapping[str, str],
) -> dict[str, Any]:
    """Plan isolated compatible attachment migrations for all bound tasks.

    ``server_runtime_attestations`` contains opaque values obtained after the
    reconnect.  No PID or caller-derived runtime instance is accepted by this
    interface.  Returned migrations preserve task-local authority byte-for-byte
    except for the explicit attachment/release fields.
    """

    if not _TASK_ID.fullmatch(invoking_task_id):
        raise AttachmentRehydrationError("INVOKING_TASK_ID_INVALID")
    surface = _validate_surface(installed_surface)
    rows = [copy.deepcopy(dict(binding)) for binding in bindings]
    ids = [str(row.get("task_id") or "") for row in rows]
    if len(ids) != len(set(ids)):
        raise AttachmentRehydrationError("DUPLICATE_TASK_BINDING")
    if invoking_task_id not in ids:
        raise AttachmentRehydrationError("INVOKING_TASK_BINDING_MISSING")

    migrations: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in rows:
        task_id = str(row.get("task_id") or "")
        try:
            identity = _validate_binding(row)
            if (
                task_id == invoking_task_id
                and identity["authority_role"] != "SOLE_WRITER"
            ):
                raise AttachmentRehydrationError("INVOKING_TASK_NOT_SOLE_WRITER")
            if (
                task_id != invoking_task_id
                and identity["authority_role"] == "SOLE_WRITER"
            ):
                raise AttachmentRehydrationError("CROSS_TASK_WRITER_LEAKAGE")
            attestation = _required_text(
                server_runtime_attestations.get(task_id),
                "SERVER_RUNTIME_ATTESTATION_MISSING",
            )
            if attestation == str(row.get("runtime_instance_attestation") or ""):
                raise AttachmentRehydrationError("RUNTIME_ATTESTATION_NOT_ROTATED")

            before_identity = {
                key: row.get(key)
                for key in (
                    "task_id",
                    "task_uri",
                    "project_id",
                    "evidence_session_id",
                    "governed_host_session_id",
                    "workspace",
                    "execution_profile",
                    "plan_goal_binding_sha256",
                    "authority_role",
                )
            }
            migrated = copy.deepcopy(row)
            migrated.update(
                {
                    "plugin_selector": surface["plugin_selector"],
                    "plugin_version": surface["plugin_version"],
                    "public_surface_sha256": surface["public_surface_sha256"],
                    "public_surface_catalog": dict(surface["catalog"]),
                    "runtime_instance_attestation": attestation,
                    "attachment_generation": int(row.get("attachment_generation") or 0)
                    + 1,
                    "hooks_enabled": False,
                }
            )
            after_identity = {key: migrated.get(key) for key in before_identity}
            if before_identity != after_identity:
                raise AttachmentRehydrationError("TASK_IDENTITY_OR_AUTHORITY_CHANGED")
            migrations.append(
                {
                    "status": "PASS",
                    "task_id": task_id,
                    "authority_role": identity["authority_role"],
                    "binding": migrated,
                    "binding_sha256": _sha256(migrated),
                    "identity_sha256": _sha256(before_identity),
                    "runtime_attestation_source": "SERVER_DERIVED_AFTER_RECONNECT",
                    "catalog_rehydrated": True,
                    "skills_rehydrated": True,
                    "sdk_routes_rehydrated": True,
                    "mcp_connection_rehydrated": True,
                    "goal_plan_preserved": True,
                    "authority_role_preserved": True,
                    "task_created": False,
                    "task_merged": False,
                    "state_travel_invoked": False,
                    "one_shot_receipt_replayed": False,
                    "candidate_hil_or_pointer_mutated": False,
                    "hooks_enabled": False,
                }
            )
        except (AttachmentRehydrationError, TypeError, ValueError) as exc:
            failures.append(
                {
                    "status": "FAIL_CLOSED",
                    "task_id": task_id,
                    "failure": str(exc),
                    "binding_mutated": False,
                    "another_task_degraded": False,
                }
            )

    return {
        "schema": "evidence-lane.global-plugin-update-rehydration.v1",
        "status": "PASS" if not failures else "PASS_WITH_ISOLATED_FAILURES",
        "law_id": GLOBAL_PLUGIN_UPDATE_REHYDRATION_LAW,
        "invoking_task_id": invoking_task_id,
        "installed_surface": surface,
        "binding_count": len(rows),
        "rehydrated_count": len(migrations),
        "isolated_failure_count": len(failures),
        "migrations": migrations,
        "failures": failures,
        "task_creation_allowed": False,
        "task_merge_allowed": False,
        "state_travel_allowed": False,
        "caller_supplied_pid_or_runtime_instance_allowed": False,
        "cross_task_authority_allowed": False,
        "hooks_enabled_by_update": False,
    }
