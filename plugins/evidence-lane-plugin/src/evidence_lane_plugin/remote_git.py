"""Policy-authorized remote Git test-branch push controller."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .git_adapter import (
    inspect_repository,
    remote_push,
    resolve_local_ref_identity,
    resolve_named_remote_identity,
    validate_remote_ref,
)
from .github_automation_governance import inspect_agent_output
from .hashing import atomic_write_json
from .ids import prefixed_id
from .store import ProjectStore
from .timeutil import utc_now
from .website_plan_projection import require_website_plan_projection_for_push


class RemoteGitController:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def _path(self, project_id: str, action_id: str) -> Path:
        return (
            self.store.project_root(project_id)
            / "receipts"
            / f"{action_id}.remote-git.json"
        )

    @staticmethod
    def _branch_name(value: str) -> str:
        return value.removeprefix("refs/heads/")

    @classmethod
    def _require_automatic_test_branch(
        cls,
        *,
        local_ref: str,
        remote_branch: str,
        allowed_branches: list[str],
    ) -> str:
        local_branch = cls._branch_name(local_ref)
        exact_branch = cls._branch_name(remote_branch)
        allowed = [cls._branch_name(value) for value in allowed_branches]
        protected = {
            "main",
            "master",
            "develop",
            "development",
            "production",
            "prod",
            "release",
            "stable",
        }
        test_prefixes = (
            "agent/",
            "test/",
            "tests/",
            "feature/",
            "fix/",
            "chore/",
        )
        require(
            allowed == [exact_branch],
            "REMOTE_TEST_BRANCH_NOT_EXACT_PROJECT_AUTHORITY",
            "Automatic remote push requires the exact sole registered project branch.",
            status="BLOCKED",
            registered_branches=allowed,
            requested_branch=exact_branch,
        )
        require(
            local_branch == exact_branch,
            "REMOTE_TEST_BRANCH_LOCAL_REF_MISMATCH",
            "Automatic remote push requires the local and remote branch names to match.",
            status="BLOCKED",
            local_branch=local_branch,
            remote_branch=exact_branch,
        )
        require(
            exact_branch not in protected
            and not exact_branch.startswith(("release/", "hotfix/"))
            and exact_branch.startswith(test_prefixes),
            "REMOTE_PROTECTED_OR_NON_TEST_BRANCH_BLOCKED",
            "Automatic remote push is limited to a named non-protected test branch.",
            status="BLOCKED",
            remote_branch=exact_branch,
            supported_prefixes=list(test_prefixes),
        )
        return exact_branch

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
        config = self.store.config(project_id)
        exact_branch = self._require_automatic_test_branch(
            local_ref=safe_local,
            remote_branch=safe_branch,
            allowed_branches=config.allowed_branches,
        )
        local_commit, local_tree = resolve_local_ref_identity(
            config.repository_path,
            local_ref=safe_local,
        )
        repository_identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
            expected_branch=exact_branch,
            expected_commit=local_commit,
        )
        require(
            repository_identity.tree_sha == local_tree,
            "REMOTE_REPOSITORY_TREE_IDENTITY_MISMATCH",
            "The governed repository tree does not match the prepared local ref.",
            status="MISMATCH",
        )
        website_plan_gate = require_website_plan_projection_for_push(
            self.store,
            project_id=project_id,
            repository=config.repository_path,
            commit=local_commit,
        )
        remote_identity = resolve_named_remote_identity(
            config.repository_path,
            remote=safe_remote,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
        )
        action_id = prefixed_id("remote_action")
        payload = {
            "schema": "evidence-lane.remote-git-action.v2",
            "action_id": action_id,
            "action": "PUSH_BRANCH",
            "project_id": project_id,
            "accepted_pv": pointer.accepted_pv,
            "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
            "pointer_generation": pointer.generation,
            "remote": safe_remote,
            "remote_identity": remote_identity,
            "repository_identity": {
                "owner": repository_identity.owner,
                "name": repository_identity.name,
                "branch": repository_identity.branch,
                "commit_sha": repository_identity.commit_sha,
                "tree_sha": repository_identity.tree_sha,
            },
            "local_ref": safe_local,
            "local_commit": local_commit,
            "local_tree": local_tree,
            "remote_branch": exact_branch,
            "website_plan_gate": website_plan_gate,
            "requested_by": requested_by,
            "prepared_at": utc_now(),
            "authorization": {
                "policy": "EXACT_REGISTERED_NON_PROTECTED_TEST_BRANCH",
                "automatic_branch_push_authorized": True,
                "one_use_confirmation_required": False,
                "credentials_source": "HOST_MANAGED_GIT_CREDENTIAL_PROVIDER",
                "credential_requested_or_stored": False,
                "main_branch_push_authorized": False,
                "merge_authorized": False,
                "pull_request_acceptance_authorized": False,
            },
            "status": "PREPARED_AUTO_AUTHORIZED_TEST_BRANCH",
        }
        atomic_write_json(self._path(project_id, action_id), payload)
        return {
            "status": "PASS",
            "action": payload,
            "confirmation_token": None,
            "next_action": "EXECUTE_PREAUTHORIZED_EXACT_TEST_BRANCH_PUSH",
        }

    def execute_push(
        self,
        project_id: str,
        *,
        action_id: str,
        executed_by: str,
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
            action["status"] == "PREPARED_AUTO_AUTHORIZED_TEST_BRANCH",
            "REMOTE_ACTION_ALREADY_CONSUMED",
            "The remote Git action is no longer pending.",
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
        self._require_automatic_test_branch(
            local_ref=action["local_ref"],
            remote_branch=action["remote_branch"],
            allowed_branches=config.allowed_branches,
        )
        pinned_commit = action.get("local_commit")
        pinned_tree = action.get("local_tree")
        if not (
            isinstance(pinned_commit, str)
            and isinstance(pinned_tree, str)
            and len(pinned_commit) in {40, 64}
            and len(pinned_tree) in {40, 64}
        ):
            action.update(
                {
                    "status": "BLOCKED_MISSING_COMMIT_BINDING",
                    "executed_by": executed_by,
                    "blocked_at": utc_now(),
                }
            )
            atomic_write_json(path, action)
            raise EvidenceLaneError(
                "REMOTE_ACTION_COMMIT_BINDING_MISSING",
                "The prepared remote Git action is not bound to an exact commit and tree.",
                status="BLOCKED",
            )
        observed_commit, observed_tree = resolve_local_ref_identity(
            config.repository_path,
            local_ref=action["local_ref"],
        )
        if observed_commit != pinned_commit or observed_tree != pinned_tree:
            action.update(
                {
                    "status": "STALE_LOCAL_REF_MOVED",
                    "executed_by": executed_by,
                    "stale_at": utc_now(),
                    "observed_local_commit": observed_commit,
                    "observed_local_tree": observed_tree,
                }
            )
            atomic_write_json(path, action)
            raise EvidenceLaneError(
                "REMOTE_ACTION_SOURCE_STALE",
                "The prepared local Git ref moved after the push was prepared.",
                status="STALE",
                details={
                    "expected_commit": pinned_commit,
                    "observed_commit": observed_commit,
                },
            )
        repository_identity = inspect_repository(
            config.repository_path,
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
            expected_branch=action["remote_branch"],
            expected_commit=pinned_commit,
        )
        current_remote_identity = resolve_named_remote_identity(
            config.repository_path,
            remote=action["remote"],
            expected_owner=config.expected_owner,
            expected_name=config.expected_name,
        )
        require(
            repository_identity.tree_sha == pinned_tree
            and action.get("remote_identity") == current_remote_identity
            and action.get("repository_identity")
            == {
                "owner": repository_identity.owner,
                "name": repository_identity.name,
                "branch": repository_identity.branch,
                "commit_sha": repository_identity.commit_sha,
                "tree_sha": repository_identity.tree_sha,
            },
            "REMOTE_ACTION_REPOSITORY_OR_REMOTE_STALE",
            "The governed repository or selected remote changed after preparation.",
            status="STALE",
        )
        current_website_plan_gate = require_website_plan_projection_for_push(
            self.store,
            project_id=project_id,
            repository=config.repository_path,
            commit=pinned_commit,
        )
        require(
            action.get("website_plan_gate") == current_website_plan_gate,
            "REMOTE_WEBSITE_PLAN_GATE_STALE_AFTER_PREPARE",
            "The canonical Plan changed after the remote push was prepared.",
            status="STALE",
            prepared_website_plan_gate=action.get("website_plan_gate"),
            current_website_plan_gate=current_website_plan_gate,
        )
        result = remote_push(
            config.repository_path,
            remote=action["remote"],
            local_ref=pinned_commit,
            remote_ref=action["remote_branch"],
        )
        combined_output = result.stdout
        if result.stderr:
            combined_output = f"{combined_output}\n[stderr]\n{result.stderr}"
        output_security = inspect_agent_output(combined_output)
        action.update(
            {
                "status": "EXECUTED" if result.returncode == 0 else "FAILED",
                "executed_by": executed_by,
                "executed_at": utc_now(),
                "git_returncode": result.returncode,
                "git_stdout": output_security["safe_output"],
                "output_security": output_security,
            }
        )
        atomic_write_json(path, action)
        if result.returncode != 0:
            raise EvidenceLaneError(
                "REMOTE_GIT_PUSH_FAILED",
                "The exact preauthorized test-branch push failed and the prepared action was consumed.",
                status="FAIL",
                details={
                    "action_id": action_id,
                    "git_returncode": result.returncode,
                    "output_security_receipt_sha256": output_security[
                        "receipt_sha256"
                    ],
                },
            )
        return {"status": "PASS", "action": action}
