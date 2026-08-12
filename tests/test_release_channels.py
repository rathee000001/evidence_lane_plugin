from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def test_v200_is_stable_and_v150_is_retained_archive() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = contract["stable"]
    future = contract["future_test"]
    archive = contract["archive"]

    assert stable == {
        "release": "2.0.0",
        "codex_marketplace_slot": "evidence-lane-v200-github",
        "enabled": True,
        "native_server_identity": "evidence-lane",
        "native_tool_count": 62,
        "native_read_tool_count": 21,
        "native_write_tool_count": 41,
        "skill_count": 15,
        "codex_apps_allowed": False,
        "generated_namespace_allowed": False,
        "direct_stdio_fallback_allowed": False,
        "google_drive_bundled": False,
        "tunnel_release_must_match": True,
        "tunnel_channel": "stable",
    }
    assert future["codex_marketplace_slot"] == "evidence-lane-next-test"
    assert future["enabled"] is False
    assert future["may_replace_stable_before_acceptance"] is False
    assert future["candidate_mutates_stable_slot"] is False
    assert future["starts_and_proves_readiness_without_stopping_stable"] is True
    assert future["failed_candidate_leaves_stable_untouched"] is True
    assert archive["retain_previous_stable_plugin"] is True
    assert archive["retain_previous_tunnel_runtime"] is True
    assert archive["disable_instead_of_delete"] is True
    assert archive["release"] == "1.5.0"


def test_promotion_requires_matching_cross_surface_receipts_and_hil() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    promotion = contract["promotion_gate"]
    assert promotion["explicit_six_way_hil_required"] is True
    assert promotion["required_catalog"] == {
        "tools": 62,
        "read": 21,
        "write": 41,
        "skills": 15,
    }
    assert promotion["fail_closed_on_version_mismatch"] is True
    assert promotion["mode"] == "CODE"
    assert promotion["ci_cd_law"] == "CONTROLLED_REQUIRED"


def test_v200_host_storage_tunnel_matrix_keeps_routing_axes_independent() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    matrix = contract["host_storage_tunnel_matrix"]

    assert matrix["routing_axes_independent"] is True
    assert matrix["account_tier_affects_routing"] is False
    assert matrix["api_billing_affects_routing"] is False
    assert matrix["headless_api"] == {
        "local_or_persistent_pv_storage": "LOCAL_SQLITE_WHEN_DURABLE",
        "ephemeral_pv_storage": (
            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
        ),
        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
        "flash_frequency": "EVERY_INVOCATION_ENTRY",
    }
    assert matrix["interactive_codex_app_local_or_persistent"] == {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
    }
    assert matrix["interactive_codex_app_ephemeral_vm"] == {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
    }


def test_v200_remote_git_policy_supersedes_only_historical_flash_sentence() -> None:
    contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    policy = contract["remote_git_policy"]

    assert policy == {
        "effective_release": "2.0.0",
        "v150_flash_confirmation_sentence": (
            "HISTORICAL_HASH_LOCKED_COMPATIBILITY_BYTE_NOT_EFFECTIVE_V2_POLICY"
        ),
        "prepare_receipt_required": True,
        "per_push_confirmation_token_required": False,
        "automatic_push_scope": (
            "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        ),
        "host_managed_credentials_only": True,
        "main_push_allowed": False,
        "merge_allowed": False,
        "pull_request_acceptance_allowed": False,
        "force_push_allowed": False,
    }


def test_tunnel_version_history_is_append_only_and_hash_chained() -> None:
    manager = (
        PLUGIN / "scripts" / "windows_tunnel" / "Manage-EvidenceLaneTunnelVersions.ps1"
    ).read_text("utf-8")
    assert 'schema = "evidence-lane.tunnel-version-event.v1"' in manager
    assert "previous_event_sha256" in manager
    assert "event_sha256" in manager
    assert 'EventType "REGISTERED_SAVED_VERSION"' in manager
    assert 'EventType "ACTIVATION_STARTED"' in manager
    assert 'EventType "ACTIVATED"' in manager
    assert 'EventType "ACTIVATION_FAILED"' in manager
    assert 'EventType "ROLLBACK_ACTIVATED"' in manager


def test_candidate_tunnel_cannot_interrupt_stable_before_promotion() -> None:
    channel_manager = {
        "candidate_action": "VerifyCandidate",
        "promotion_action": "Promote",
        "promotion_receipts": [
            "HEALTH_SHA256",
            "PUBLIC_ROUTE_SHA256",
            "HOST_PROOF_SHA256",
        ],
        "codex_native_lifecycle_route_eligible": False,
    }
    manager = (
        PLUGIN / "scripts" / "windows_tunnel" / "Manage-EvidenceLaneTunnelVersions.ps1"
    ).read_text("utf-8")

    assert channel_manager["candidate_action"] == "VerifyCandidate"
    assert channel_manager["promotion_action"] == "Promote"
    assert channel_manager["promotion_receipts"] == [
        "HEALTH_SHA256",
        "PUBLIC_ROUTE_SHA256",
        "HOST_PROOF_SHA256",
    ]
    assert channel_manager["codex_native_lifecycle_route_eligible"] is False
    assert 'EventType "FUTURE_TEST_FAILED_STABLE_UNTOUCHED"' in manager
    assert 'EventType "PROMOTION_STARTED_STABLE_STILL_READY"' in manager
    assert 'EventType "PROMOTED_TO_STABLE"' in manager
    assert 'EventType "MOVED_TO_ARCHIVE"' in manager
    candidate_block = manager.split('if ($Action -eq "VerifyCandidate")', 1)[1].split(
        'if ($Action -eq "Promote")', 1
    )[0]
    assert "Stop-SavedVersion -Entry $oldStable" not in candidate_block
    assert "stable_untouched = $true" in candidate_block
