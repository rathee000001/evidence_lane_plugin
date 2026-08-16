from __future__ import annotations

import importlib.util
import json
import os
import site
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins/evidence-lane-plugin"
VERIFIER = (
    PLUGIN_ROOT
    / "scripts/codex_release/verify_isolated_hook_runtime.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("row210_verifier", VERIFIER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_is_hard_bounded_away_from_live_host_and_helper() -> None:
    source = VERIFIER.read_text(encoding="utf-8")

    assert '"plugin_enabled": False' in source
    assert '"live_codex_config_written": False' in source
    assert '"live_plugin_slot_written": False' in source
    assert '"installer_helper_invoked": False' in source
    assert '"task_or_goal_binding_mutated": False' in source
    assert '"tunnel_invoked": False' in source
    assert '"candidate_created": False' in source
    assert '"hil_inferred": False' in source
    assert '"pointer_moved": False' in source
    assert "config.toml" not in source
    assert '"plugin", "add"' not in source
    assert '"plugin", "enable"' not in source
    assert "install_codex_stable" not in source


@pytest.mark.skipif(os.name != "nt", reason="exact Windows hook command proof")
def test_exact_disabled_projection_runs_all_eight_and_recovery_controls(
    tmp_path: Path,
) -> None:
    module = _module()
    dependency_root = Path(site.getsitepackages()[-1]).resolve()
    receipt = module.verify_isolated_installed_runtime(
        source_plugin_root=PLUGIN_ROOT,
        isolated_root=tmp_path / "row210-isolated",
        dependency_site_packages=dependency_root,
    )

    assert receipt["schema"] == module.RECEIPT_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["projection"]["exact_bytes_verified"] is True
    assert receipt["projection"]["file_count"] > 300
    assert receipt["installed_manifest"]["event_count"] == 8
    assert receipt["event_correlation"]["events"] == list(module.EVENT_ORDER)
    assert receipt["event_correlation"]["unique_correlation_count"] == 8
    assert receipt["stop_no_loop"]["handler_execution_count"] == 1
    assert receipt["stop_no_loop"]["first_and_replay_output"] == {}
    assert receipt["reentrancy"] == {
        "status": "PASS",
        "denial_code": "HOOK_EVENT_REENTRANCY_DENIED",
        "database_row_created": False,
        "handler_executed": False,
    }
    assert receipt["kill_switch"]["active_state_denial_code"] == (
        "HOOK_KILL_SWITCH_ACTIVE"
    )
    assert receipt["restart_recovery"]["receipt_count_after_restart"] == 9
    assert receipt["restart_recovery"]["runtime_marker_valid"] is True
    assert receipt["launcher_process_count"] == 12
    assert receipt["unique_launcher_process_count"] == 12
    assert receipt["live_codex_home_opened"] is False
    assert receipt["live_codex_config_written"] is False
    assert receipt["live_plugin_slot_written"] is False
    assert receipt["plugin_enabled"] is False
    assert receipt["installer_helper_invoked"] is False
    assert receipt["task_or_goal_binding_mutated"] is False
    assert receipt["tunnel_invoked"] is False
    assert receipt["candidate_created"] is False
    assert receipt["hil_inferred"] is False
    assert receipt["pointer_moved"] is False
    assert len(receipt["receipt_sha256"]) == 64
    assert json.loads(
        Path(receipt["install_binding"]["receipt_path"]).read_text("utf-8")
    )["plugin_enabled"] is False
