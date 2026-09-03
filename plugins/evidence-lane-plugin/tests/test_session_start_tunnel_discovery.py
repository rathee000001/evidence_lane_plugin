from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SESSION_START_PATH = PLUGIN_ROOT / "hooks" / "session_start.py"
TEST_TUNNEL_COMPATIBILITY_SHA256 = "A" * 64


def _load_session_start():
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_session_start_tunnel_test", SESSION_START_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _current_marker(identity: dict[str, str]) -> dict[str, object]:
    return {
        "schema": "evidence-lane.versioned-secure-mcp-tunnel-installation.v2",
        "release": identity["release"],
        "release_token": identity["release_token"],
        "slot_role": identity["slot_role"],
        "plugin_version": identity["plugin_version"],
        "plugin_version_token": identity["plugin_version_token"],
        "plugin_version_sha256": identity["plugin_version_sha256"],
        "plugin_version_digest": identity["plugin_version_digest"],
        "tunnel_compatibility_schema": (
            "evidence-lane.tunnel-capability-compatibility.v1"
        ),
        "tunnel_compatibility_sha256": identity[
            "tunnel_compatibility_sha256"
        ],
        "tunnel_compatibility_digest": identity[
            "tunnel_compatibility_digest"
        ],
        "tunnel_version_token": identity["tunnel_version_token"],
        "file_prefix": identity["file_prefix"],
        "runtime_root": identity["runtime_root"],
        "profile_name": identity["profile_name"],
        "task_name": identity["task_name"],
        "runtime_identity_matches_release": True,
        "interaction_profile": "CODEX_APP_INTERACTIVE",
        "host_tool_transport": "HOST_TOOL_GAP",
        "host_lifetime": "PERSISTENT",
        "runtime_key_plaintext_written": False,
        "host_wide_project_neutral": True,
        "per_project_or_task_tunnel_allowed": False,
        "scheduled_task_transport_used": True,
        "vm_instance_id_sha256": "NOT_APPLICABLE",
    }


def test_current_package_version_derives_and_accepts_exact_v2_marker(
    tmp_path: Path,
) -> None:
    session_start = _load_session_start()
    identity = session_start._version_bound_tunnel_identity(
        release="3.0.0",
        slot_role="versioned-local-testing",
        plugin_version="3.0.0+codex.20260902183011",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=tmp_path,
    )
    assert identity["tunnel_version_token"] == (
        "v300-versioned-local-testing-abi-aaaaaaaaaaaa"
    )
    assert identity["runtime_root"].endswith(
        "tunnel-runtime-v300-versioned-local-testing-abi-aaaaaaaaaaaa"
    )
    assert session_start._version_bound_tunnel_marker_matches(
        _current_marker(identity),
        identity=identity,
        interaction_profile="CODEX_APP_INTERACTIVE",
        host_lifetime="PERSISTENT",
        vm_instance_id_sha256=None,
    )


def test_host_activation_discovers_the_exact_current_version_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_start = _load_session_start()
    plugin_root = tmp_path / "installed-plugin"
    store_root = tmp_path / "runtime-control"
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    (plugin_root / "scripts" / "windows_tunnel").mkdir(parents=True)
    (plugin_root / "tunnel").mkdir(parents=True)
    (plugin_root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "evidence-lane-plugin",
                "version": "3.0.0+codex.20260902183011",
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "scripts" / "codex-release-channel.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.codex-release-channel.v2",
                "stable": {
                    "release": "3.0.0",
                    "slot_role": "main-git-release",
                },
                "local_testing": {
                    "release_line": "3.0.0",
                    "slot_role": "versioned-local-testing",
                    "codex_marketplace_slot": "evidence-lane-v300-testing-new",
                },
            }
        ),
        encoding="utf-8",
    )
    (plugin_root / "tunnel" / "tunnel-manifest.v1.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.installed-tunnel-surface.v1",
                "status": "PASS",
                "tunnel_compatibility_schema": (
                    "evidence-lane.tunnel-capability-compatibility.v1"
                ),
                "tunnel_compatibility_sha256": (
                    TEST_TUNNEL_COMPATIBILITY_SHA256
                ),
            }
        ),
        encoding="utf-8",
    )
    project_root = store_root / "projects" / "project-one"
    (project_root / "sessions").mkdir(parents=True)
    (project_root / "active_session.json").write_text(
        json.dumps({"session_id": "session-one"}), encoding="utf-8"
    )
    (project_root / "sessions" / "session-one.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "persistence_route": {
                        "interaction_profile": "CODEX_APP_INTERACTIVE",
                        "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
                        "vm_lifetime": "LOCAL_OR_PERSISTENT",
                        "runtime_classifier": {
                            "schema": "evidence-lane.runtime-host-classifier.v1",
                            "active_surface": "CODEX",
                            "evidence_lane_execution_scope": "CODEX_LAYER_ONLY",
                            "native_capabilities": {"native_mcp": False},
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    identity = session_start._version_bound_tunnel_identity(
        release="3.0.0",
        slot_role="versioned-local-testing",
        plugin_version="3.0.0+codex.20260902183011",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=store_root,
    )
    runtime_root = Path(identity["runtime_root"])
    runtime_root.mkdir(parents=True)
    (runtime_root / "evidence-lane-tunnel-installation.json").write_text(
        json.dumps(_current_marker(identity)), encoding="utf-8"
    )
    monkeypatch.setattr(session_start, "_plugin_root", lambda: plugin_root)
    monkeypatch.setattr(session_start, "_store_root", lambda: store_root)
    monkeypatch.setenv(
        "EVIDENCE_LANE_CODEX_SLOT_ROLE", "versioned-local-testing"
    )

    result = session_start._host_activation_context("project-one")

    assert result["state"] == "TUNNEL_INSTALLATION_PRESENT_HOST_MANAGED"
    assert result["plugin_version"] == "3.0.0+codex.20260902183011"
    assert result["runtime_root"] == identity["runtime_root"]
    assert result["expected_task_name"] == identity["task_name"]
    assert result["expected_profile_name"] == identity["profile_name"]
    assert result["tunnel_mutated"] is False


def test_identity_derivation_is_not_hard_coded_to_v300(tmp_path: Path) -> None:
    session_start = _load_session_start()
    identity = session_start._version_bound_tunnel_identity(
        release="3.1.0",
        slot_role="main-git-release",
        plugin_version="3.1.0+codex.local-20260902-190000",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=tmp_path,
    )
    assert identity["release_token"] == "v310"
    assert identity["tunnel_version_token"].startswith(
        "v310-main-git-release-abi-"
    )
    assert identity["plugin_version_digest"] == "9b4e6ad700d7"


def test_stale_package_version_marker_is_rejected(tmp_path: Path) -> None:
    session_start = _load_session_start()
    identity = session_start._version_bound_tunnel_identity(
        release="3.0.0",
        slot_role="versioned-local-testing",
        plugin_version="3.0.0+codex.20260902183011",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=tmp_path,
    )
    marker = _current_marker(identity)
    marker["plugin_version"] = "3.0.0+codex.20260901041630"
    assert not session_start._version_bound_tunnel_marker_matches(
        marker,
        identity=identity,
        interaction_profile="CODEX_APP_INTERACTIVE",
        host_lifetime="PERSISTENT",
        vm_instance_id_sha256=None,
    )


def test_legacy_non_versioned_marker_is_rejected(tmp_path: Path) -> None:
    session_start = _load_session_start()
    identity = session_start._version_bound_tunnel_identity(
        release="3.0.0",
        slot_role="versioned-local-testing",
        plugin_version="3.0.0+codex.20260902183011",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=tmp_path,
    )
    legacy_marker = {
        "schema": "evidence-lane.versioned-secure-mcp-tunnel-installation.v2",
        "release": "3.0.0",
        "release_token": "v300",
        "slot_role": "versioned-local-testing",
        "runtime_root": str(tmp_path / "tunnel-runtime-v300-stable-build"),
        "profile_name": "evidence_lane_v300_stable_build_transport",
        "task_name": "EvidenceLane-Tunnel-v300-stable-build",
        "scheduled_task_transport_used": True,
    }
    assert not session_start._version_bound_tunnel_marker_matches(
        legacy_marker,
        identity=identity,
        interaction_profile="CODEX_APP_INTERACTIVE",
        host_lifetime="PERSISTENT",
        vm_instance_id_sha256=None,
    )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("runtime_root", "stale-runtime-root"),
        ("profile_name", "stale-profile"),
        ("task_name", "stale-task"),
    ],
)
def test_runtime_profile_and_task_must_match_current_identity(
    tmp_path: Path,
    field: str,
    bad_value: str,
) -> None:
    session_start = _load_session_start()
    identity = session_start._version_bound_tunnel_identity(
        release="3.0.0",
        slot_role="versioned-local-testing",
        plugin_version="3.0.0+codex.20260902183011",
        tunnel_compatibility_sha256=TEST_TUNNEL_COMPATIBILITY_SHA256,
        runtime_control_root=tmp_path,
    )
    marker = _current_marker(identity)
    marker[field] = bad_value
    assert not session_start._version_bound_tunnel_marker_matches(
        marker,
        identity=identity,
        interaction_profile="CODEX_APP_INTERACTIVE",
        host_lifetime="PERSISTENT",
        vm_instance_id_sha256=None,
    )
