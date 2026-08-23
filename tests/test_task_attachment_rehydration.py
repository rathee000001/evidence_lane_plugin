from __future__ import annotations

from copy import deepcopy

import pytest
from evidence_lane_plugin.task_attachment_rehydration import (
    AttachmentRehydrationError,
    plan_global_plugin_update_rehydration,
)

TASK11 = "01a02759-9842-74c1-860e-0c9a64f8258d"
TASK9 = "01a02507-f389-7781-9bff-96f2d5647d95"


def _binding(task_id: str, role: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_uri": f"codex://threads/{task_id}",
        "project_id": "test-codex-evidence-lane-plugin",
        "evidence_session_id": "session_01kz48pm60mt58v5fyzqq6yq1g",
        "governed_host_session_id": task_id,
        "workspace": r"F:\test codex",
        "execution_profile": "gpt-5.6-sol/sol/xhigh/standard",
        "plan_goal_binding_sha256": "A" * 64,
        "authority_role": role,
        "plugin_selector": "evidence-lane-plugin@old",
        "plugin_version": "3.0.0+old",
        "public_surface_sha256": "B" * 64,
        "runtime_instance_attestation": f"old-{task_id}",
        "attachment_generation": 7,
        "candidate_created_or_accepted": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "hooks_enabled": False,
    }


def _surface() -> dict[str, object]:
    return {
        "status": "PASS",
        "plugin_selector": "evidence-lane-plugin@evidence-lane-v300-testing-new",
        "plugin_version": "3.0.0+codex.current",
        "public_surface_sha256": "C" * 64,
        "catalog": {
            "tools": 88,
            "read": 27,
            "write": 61,
            "skills": 17,
            "commands": 6,
            "providers": 1,
        },
        "hooks_enabled": False,
    }


def test_n_tasks_rehydrate_without_changing_identity_or_authority() -> None:
    result = plan_global_plugin_update_rehydration(
        [_binding(TASK9, "READ_ONLY"), _binding(TASK11, "SOLE_WRITER")],
        invoking_task_id=TASK11,
        installed_surface=_surface(),
        server_runtime_attestations={TASK9: "server-new-9", TASK11: "server-new-11"},
    )

    assert result["status"] == "PASS"
    assert result["rehydrated_count"] == 2
    assert result["isolated_failure_count"] == 0
    by_task = {row["task_id"]: row for row in result["migrations"]}
    assert by_task[TASK9]["binding"]["authority_role"] == "READ_ONLY"
    assert by_task[TASK11]["binding"]["authority_role"] == "SOLE_WRITER"
    assert by_task[TASK11]["binding"]["attachment_generation"] == 8
    assert all(row["hooks_enabled"] is False for row in result["migrations"])


def test_one_bad_task_isolated_without_degrading_compatible_task() -> None:
    bad = _binding(TASK9, "READ_ONLY")
    bad["task_uri"] = f"codex://threads/{TASK11}"
    result = plan_global_plugin_update_rehydration(
        [bad, _binding(TASK11, "SOLE_WRITER")],
        invoking_task_id=TASK11,
        installed_surface=_surface(),
        server_runtime_attestations={TASK9: "server-new-9", TASK11: "server-new-11"},
    )

    assert result["status"] == "PASS_WITH_ISOLATED_FAILURES"
    assert result["rehydrated_count"] == 1
    assert result["migrations"][0]["task_id"] == TASK11
    assert result["failures"] == [
        {
            "status": "FAIL_CLOSED",
            "task_id": TASK9,
            "failure": "TASK_DEEP_LINK_MISMATCH",
            "binding_mutated": False,
            "another_task_degraded": False,
        }
    ]


def test_wrong_writer_and_unrotated_runtime_attestation_fail_per_task() -> None:
    wrong_writer = _binding(TASK9, "SOLE_WRITER")
    task11 = _binding(TASK11, "SOLE_WRITER")
    result = plan_global_plugin_update_rehydration(
        [wrong_writer, task11],
        invoking_task_id=TASK11,
        installed_surface=_surface(),
        server_runtime_attestations={
            TASK9: "server-new-9",
            TASK11: str(task11["runtime_instance_attestation"]),
        },
    )

    assert result["rehydrated_count"] == 0
    assert {row["failure"] for row in result["failures"]} == {
        "CROSS_TASK_WRITER_LEAKAGE",
        "RUNTIME_ATTESTATION_NOT_ROTATED",
    }


def test_update_cannot_enable_hooks_or_accept_bad_catalog_totals() -> None:
    enabled = _surface()
    enabled["hooks_enabled"] = True
    with pytest.raises(
        AttachmentRehydrationError, match="PLUGIN_UPDATE_MUST_NOT_ENABLE_HOOKS"
    ):
        plan_global_plugin_update_rehydration(
            [_binding(TASK11, "SOLE_WRITER")],
            invoking_task_id=TASK11,
            installed_surface=enabled,
            server_runtime_attestations={TASK11: "server-new-11"},
        )

    bad = deepcopy(_surface())
    bad["catalog"]["write"] = 60
    with pytest.raises(
        AttachmentRehydrationError, match="PUBLIC_SURFACE_READ_WRITE_TOTAL_MISMATCH"
    ):
        plan_global_plugin_update_rehydration(
            [_binding(TASK11, "SOLE_WRITER")],
            invoking_task_id=TASK11,
            installed_surface=bad,
            server_runtime_attestations={TASK11: "server-new-11"},
        )


def test_duplicate_or_missing_invoking_task_is_rejected_before_migration() -> None:
    with pytest.raises(AttachmentRehydrationError, match="DUPLICATE_TASK_BINDING"):
        plan_global_plugin_update_rehydration(
            [_binding(TASK11, "SOLE_WRITER"), _binding(TASK11, "SOLE_WRITER")],
            invoking_task_id=TASK11,
            installed_surface=_surface(),
            server_runtime_attestations={TASK11: "server-new-11"},
        )
    with pytest.raises(
        AttachmentRehydrationError, match="INVOKING_TASK_BINDING_MISSING"
    ):
        plan_global_plugin_update_rehydration(
            [_binding(TASK9, "READ_ONLY")],
            invoking_task_id=TASK11,
            installed_surface=_surface(),
            server_runtime_attestations={TASK9: "server-new-9"},
        )
