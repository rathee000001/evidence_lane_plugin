"""Separately authorized remote Git-write controller."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .errors import require
from .git_adapter import remote_push, validate_remote_ref
from .hashing import atomic_write_json, sha256_bytes
from .ids import prefixed_id
from .store import ProjectStore
from .timeutil import utc_now


class RemoteGitController:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def _path(self, project_id: str, action_id: str) -> Path:
        return (
            self.store.project_root(project_id)
            / "receipts"
            / f"{action_id}.remote-git.json"
        )

    def prepare_push(
        self,
        project_id: str,
        *,
        requested_by: str,
        remote: str,
        local_ref: str,
        remote_branch: str,
    ) -> dict[str, Any]:
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv is not None,
            "REMOTE_WRITE_REQUIRES_ACCEPTED_PV",
            "Remote Git actions require an accepted PV.",
            status="BLOCKED",
        )
        safe_remote = validate_remote_ref(remote, field="remote")
        safe_local = validate_remote_ref(local_ref, field="local_ref")
        safe_branch = validate_remote_ref(remote_branch, field="remote_branch")
        action_id = prefixed_id("remote_action")
        nonce = secrets.token_urlsafe(24)
        confirmation = f"CONFIRM_EVIDENCE_LANE_GIT_PUSH_{action_id}_{nonce}"
        payload = {
            "schema": "evidence-lane.remote-git-action.v1",
            "action_id": action_id,
            "action": "PUSH_BRANCH",
            "project_id": project_id,
            "accepted_pv": pointer.accepted_pv,
            "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
            "pointer_generation": pointer.generation,
            "remote": safe_remote,
            "local_ref": safe_local,
            "remote_branch": safe_branch,
            "requested_by": requested_by,
            "prepared_at": utc_now(),
            "confirmation_sha256": sha256_bytes(confirmation.encode("utf-8")),
            "status": "PREPARED_AWAITING_EXACT_CONFIRMATION",
        }
        atomic_write_json(self._path(project_id, action_id), payload)
        return {
            "status": "PASS",
            "action": payload,
            "confirmation_token": confirmation,
            "warning": "This token authorizes one remote branch push only.",
        }

    def execute_push(
        self,
        project_id: str,
        *,
        action_id: str,
        confirmation_token: str,
        confirmed_by: str,
    ) -> dict[str, Any]:
        path = self._path(project_id, action_id)
        require(
            path.is_file(),
            "REMOTE_ACTION_NOT_FOUND",
            "The prepared remote Git action does not exist.",
            status="MISMATCH",
        )
        action = json.loads(path.read_text(encoding="utf-8"))
        require(
            action["status"] == "PREPARED_AWAITING_EXACT_CONFIRMATION",
            "REMOTE_ACTION_ALREADY_CONSUMED",
            "The remote Git action is no longer pending.",
            status="BLOCKED",
        )
        require(
            sha256_bytes(confirmation_token.encode("utf-8"))
            == action["confirmation_sha256"],
            "REMOTE_ACTION_CONFIRMATION_INVALID",
            "The remote Git confirmation token is not exact.",
            status="BLOCKED",
        )
        pointer = self.store.pointer(project_id)
        require(
            pointer.accepted_pv == action["accepted_pv"]
            and pointer.accepted_manifest_sha256 == action["accepted_manifest_sha256"]
            and pointer.generation == action["pointer_generation"],
            "REMOTE_ACTION_POINTER_STALE",
            "The accepted pointer changed after the remote action was prepared.",
            status="STALE",
        )
        config = self.store.config(project_id)
        result = remote_push(
            config.repository_path,
            remote=action["remote"],
            local_ref=action["local_ref"],
            remote_ref=action["remote_branch"],
        )
        action.update(
            {
                "status": "EXECUTED",
                "confirmed_by": confirmed_by,
                "executed_at": utc_now(),
                "git_returncode": result.returncode,
                "git_stdout": result.stdout[-4000:],
            }
        )
        atomic_write_json(path, action)
        return {"status": "PASS", "action": action}
