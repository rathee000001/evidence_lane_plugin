"""Advisory SessionStart context; deliberately performs no state mutation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

_FLASH_PROMPT_SHA256 = (
    "E5751173A1419137DF57ED4811D82D0ADED227C6D064A0DF603EF7813F539D5F"
)
_ENGINE_VERSION_RE = re.compile(
    r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
    flags=re.MULTILINE,
)


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _store_root() -> Path:
    return Path(
        os.environ.get("EVIDENCE_LANE_DATA_ROOT")
        or os.environ.get("PLUGIN_DATA")
        or Path.home() / "EvidenceLanePV"
    ).resolve()


def _plugin_version_context() -> dict[str, object]:
    root = _plugin_root()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    constants_path = root / "src" / "evidence_lane_plugin" / "constants.py"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        match = _ENGINE_VERSION_RE.search(constants_path.read_text(encoding="utf-8"))
        runtime_version = match.group("version") if match else None
        manifest_version = str(manifest.get("version", ""))
        manifest_base = manifest_version.split("+", 1)[0]
        state = (
            "FRESH"
            if runtime_version and manifest_base == runtime_version
            else "MISMATCH"
        )
        return {
            "plugin_id": manifest.get("name"),
            "plugin_manifest_version": manifest_version,
            "runtime_engine_version": runtime_version,
            "version_state": state,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes())
            .hexdigest()
            .upper(),
            "runtime_constants_sha256": hashlib.sha256(constants_path.read_bytes())
            .hexdigest()
            .upper(),
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "plugin_id": "evidence-lane-plugin",
            "version_state": "UNVERIFIED",
            "error_type": type(exc).__name__,
        }


def _flash_context() -> str:
    prompt_path = (
        _plugin_root()
        / "src"
        / "evidence_lane_plugin"
        / "session_flash"
        / "env15"
        / "UNIVERSAL_FLASH_PROMPT.md"
    )
    if not prompt_path.is_file():
        return (
            "SESSION_FLASH_AUTHORITY_UNAVAILABLE: the universal flash prompt is "
            "missing. Do not start Evidence Lane lifecycle work."
        )
    prompt_bytes = prompt_path.read_bytes()
    actual_hash = hashlib.sha256(prompt_bytes).hexdigest().upper()
    if actual_hash != _FLASH_PROMPT_SHA256:
        return (
            "SESSION_FLASH_AUTHORITY_MISMATCH: the universal flash prompt failed "
            "SHA-256 verification. Do not start Evidence Lane lifecycle work."
        )
    return (
        prompt_bytes.decode("utf-8")
        + "\n\nFLASH_PROMPT_SHA256="
        + actual_hash
        + "\nFLASH_CONTEXT_INSIDE_PV=false"
    )


def _persistent_envelope() -> dict[str, object]:
    """Read a bounded durable status hint; lifecycle tools remain authoritative."""
    root = _store_root()
    projects_root = root / "projects"
    if not projects_root.is_dir():
        return {
            "state": "NO_PERSISTENT_PROJECT_STORE",
            "store_configured": False,
            "store_path": str(root),
            "persistence_class": "USER_OWNED_LOCAL_STORE",
            "projects": [],
        }
    projects: list[dict[str, object]] = []
    warnings: list[dict[str, str]] = []
    for project_root in sorted(projects_root.iterdir(), key=lambda path: path.name)[
        :20
    ]:
        if not project_root.is_dir():
            continue
        try:
            pointer = json.loads(
                (project_root / "active_pointer.json").read_text(encoding="utf-8")
            )
            accepted = sorted(
                (
                    path.name
                    for path in (project_root / "accepted").glob("PV*")
                    if path.is_dir() and path.name[2:].isdigit()
                ),
                key=lambda value: int(value[2:]),
            )
            active_session: dict[str, object] | None = None
            active_path = project_root / "active_session.json"
            if active_path.is_file():
                active = json.loads(active_path.read_text(encoding="utf-8"))
                session_path = (
                    project_root / "sessions" / f"{active.get('session_id', '')}.json"
                )
                if session_path.is_file():
                    session = json.loads(session_path.read_text(encoding="utf-8"))
                    if not session.get("metadata", {}).get("closed_at"):
                        active_session = {
                            "session_id": session.get("session_id"),
                            "state": session.get("state"),
                            "entry_pv": session.get("metadata", {}).get("entry_pv"),
                            "candidate_id": session.get("candidate_id"),
                            "pending_hil": str(session.get("state", "")).endswith(
                                "_CANDIDATE"
                            ),
                            "entry_manifest_sha256": session.get("metadata", {}).get(
                                "entry_manifest_sha256"
                            ),
                            "entry_package_sha256": session.get("metadata", {}).get(
                                "entry_package_sha256"
                            ),
                        }
            backlog_counts = {
                "waiting": 0,
                "active": 0,
                "completed": 0,
                "total": 0,
            }
            backlog_path = project_root / "task_backlog.json"
            if backlog_path.is_file():
                backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
                tasks = backlog.get("tasks", [])
                backlog_counts = {
                    "waiting": sum(
                        1 for task in tasks if task.get("status") == "WAITING"
                    ),
                    "active": sum(
                        1 for task in tasks if task.get("status") == "ACTIVE"
                    ),
                    "completed": sum(
                        1
                        for task in tasks
                        if task.get("status")
                        in {"APPROVED", "REJECTED", "FAILED", "ROLLED_BACK"}
                    ),
                    "total": len(tasks),
                }
            active_lanes: list[str] = []
            accepted_pv = pointer.get("accepted_pv")
            if accepted_pv:
                routes_path = (
                    project_root
                    / "accepted"
                    / str(accepted_pv)
                    / "lanes"
                    / "routes.json"
                )
                if routes_path.is_file():
                    routes = json.loads(routes_path.read_text(encoding="utf-8"))
                    active_lanes = sorted(set(routes.get("routes", {}).values()))
            projects.append(
                {
                    "project_id": project_root.name,
                    "accepted_pv": accepted_pv,
                    "pointer_generation": pointer.get("generation"),
                    "accepted_manifest_sha256": pointer.get("accepted_manifest_sha256"),
                    "accepted_history": accepted,
                    "highest_accepted_ordinal": max(
                        (int(value[2:]) for value in accepted), default=0
                    ),
                    "next_candidate_pv": (
                        f"PV{max((int(value[2:]) for value in accepted), default=0) + 1}"
                    ),
                    "ancestry_depth": max(len(accepted) - 1, 0),
                    "active_lanes": active_lanes,
                    "live_freshness": "VERIFY_WITH_PV_STATUS",
                    "task_backlog": backlog_counts,
                    "active_session": active_session,
                }
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            warnings.append(
                {
                    "project_id": project_root.name,
                    "error_type": type(exc).__name__,
                }
            )
    return {
        "state": "PERSISTENT_STATE_HINT_ONLY_CALL_PV_STATUS_TO_VERIFY",
        "store_configured": True,
        "store_path": str(root),
        "persistence_class": "USER_OWNED_LOCAL_STORE",
        "project_count_returned": len(projects),
        "projects": projects,
        "warnings": warnings,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    source = str(payload.get("source", "startup"))
    host_session_id = str(payload.get("session_id", "")).strip()
    context = (
        _flash_context()
        + "\n\nPLUGIN_RUNTIME_ENVELOPE="
        + json.dumps(
            _plugin_version_context(),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nUNIVERSAL_LIFECYCLE_ADDENDUM="
        + "APPROVE is the only candidate promotion. ROLLBACK may move only "
        + "the accepted pointer among immutable accepted PVs; bare rollback "
        + "uses this session's recorded entry PV. It never accepts a candidate "
        + "or rewrites source. PV1 is the only normal full build; PV2+ uses "
        + "incremental Refresh across the single eighteen-lane registry. Task "
        + "completion automatically confirms source, Refreshes, and seals the "
        + "exit candidate; Exit and Refresh are not user commands."
        + "\n\nDisplay /evi-00-state-travel first. A prepared accepted-PV "
        + "handoff must bind this fresh host session, atomically verify Boot plus "
        + "locked ENV/UOP Flash, pointer, and seals, then wait for the user's next "
        + "command. Otherwise follow /evi: atomic Boot/Flash, all seventeen "
        + "source-intake commands, the separate /evi-mode sidecar, Build PV Entry, "
        + "bounded work, automatic exit-Refresh, HIL, Fuse, Rollback, and explicit "
        + "/evi-exit-boot. A booted session persists until that explicit command. "
        + "Before stopping at HIL or State Travel, visibly render the engine's "
        + "suggested_next_prompt. The composer is host-owned; do not claim the "
        + "MCP wrote it, do not auto-submit it, and do not use a Stop hook to "
        + "continue past a human gate. "
        + "HOST_SESSION_ID="
        + (host_session_id or "UNAVAILABLE")
        + ". Session source: "
        + source
        + ".\nPERSISTENT_STATE_ENVELOPE="
        + json.dumps(
            _persistent_envelope(),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\nThis envelope is a read-only startup hint. Call pv_status before "
        "relying on it; never infer HIL approval."
    )
    print(
        json.dumps(
            {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
