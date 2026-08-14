from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
GOAL_RECOVERY = (
    PLUGIN / "scripts" / "codex_release" / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)
RESTART = PLUGIN / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1"
STABLE_UPDATE = (
    PLUGIN / "scripts" / "codex_release" / "Update-EvidenceLaneCodexStableAndResume.ps1"
)
SLOT_SWITCH = (
    PLUGIN / "scripts" / "codex_release" / "Switch-EvidenceLaneCodexSlot.ps1"
)
TUNNEL_INSTALL = (
    PLUGIN / "scripts" / "windows_tunnel" / "Install-EvidenceLaneTunnel.ps1"
)


def _python_sources() -> list[Path]:
    return sorted((PLUGIN / "scripts").rglob("*.py")) + sorted(
        (PLUGIN / "src").rglob("*.py")
    )


def test_every_plugin_owned_python_child_process_has_no_console_flag() -> None:
    missing: list[str] = []
    forbidden: list[str] = []
    child_methods = {"run", "Popen", "call", "check_call", "check_output"}

    for path in _python_sources():
        if ".venv" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                forbidden.append(f"{path}:{node.lineno}:from-subprocess-import")
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr in child_methods
                ):
                    keywords = {keyword.arg for keyword in node.keywords}
                    if "creationflags" not in keywords:
                        missing.append(f"{path}:{node.lineno}:subprocess.{node.func.attr}")
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in {"system", "popen"}
                ):
                    forbidden.append(f"{path}:{node.lineno}:os.{node.func.attr}")

    assert not missing, "Missing Windows no-console creationflags:\n" + "\n".join(missing)
    assert not forbidden, "Un-governed child-process launch route:\n" + "\n".join(forbidden)

    runner = (PLUGIN / "scripts" / "run_mcp.py").read_text(encoding="utf-8")
    assert 'if args.transport == "stdio"' in runner
    assert "The stdio relay must remain in the MCP client's process group" in runner


def test_powershell_background_routes_are_hidden_and_never_loop_restart() -> None:
    recovery = GOAL_RECOVERY.read_text(encoding="utf-8")
    update = STABLE_UPDATE.read_text(encoding="utf-8")
    switch = SLOT_SWITCH.read_text(encoding="utf-8")
    restart = RESTART.read_text(encoding="utf-8")
    tunnel = TUNNEL_INSTALL.read_text(encoding="utf-8")

    assert '"-WindowStyle", "Hidden"' in recovery
    assert 'windows_console_policy = "POWERSHELL_WINDOWSTYLE_HIDDEN"' in recovery
    assert 'scheduled_task_window_style = "HIDDEN"' in recovery
    assert 'Release = "2.2.0"' in recovery
    assert '"Evidence Lane Codex Goal Recovery $($script:ReleaseToken)"' in recovery
    assert 'helper_audience = "GOVERNED_CODEX_USER"' in recovery
    assert "prior_versioned_helpers_retained = $true" in recovery
    assert "prior_versioned_helpers_disabled = $true" in recovery
    assert "prior_versioned_helpers_deleted = $false" in recovery
    assert "Disable-ScheduledTask -TaskName ([string]$priorTask.TaskName)" in recovery
    assert 'host_owned_initial_mcp_spawn = "HOST_CAPABILITY_UNAVAILABLE"' in recovery
    assert 'restart_loop_allowed = $false' in recovery

    assert '"-WindowStyle", "Hidden"' in update
    assert "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass" in update
    assert "-WindowStyle Hidden | Out-Null" in restart
    assert switch.count("-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass") >= 2
    assert "-WindowStyle Hidden | Out-Null" in switch

    assert (
        tunnel.count("-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass")
        >= 3
    )
    assert 'windows_console_policy = "PERSISTENT_OR_HIDDEN_NO_TRANSIENT_CONSOLE"' in tunnel
    assert 'scheduled_task_window_style = "HIDDEN"' in tunnel
    assert 'prior_versioned_runtimes_retained = $true' in tunnel
    assert 'prior_versioned_runtime_deletion_allowed = $false' in tunnel
    assert "-RestartCount 999" in tunnel
    assert "-MultipleInstances IgnoreNew" in tunnel


def test_stable_and_beta_desktop_channels_both_expose_dual_surfaces() -> None:
    for path in (GOAL_RECOVERY, RESTART, STABLE_UPDATE):
        text = path.read_text(encoding="utf-8")
        assert 'desktop_release_channel = "CHATGPT_STABLE"' in text
        assert 'desktop_release_channel = "CHATGPT_BETA"' in text
        assert text.count('available_surfaces = @("CHATGPT", "CODEX")') == 2
        assert text.count('governed_surface = "CODEX"') == 2
        assert text.count("chatgpt_surface_governed = $false") == 2


def test_goal_recovery_prewarm_is_exact_task_read_only_and_truthful() -> None:
    text = GOAL_RECOVERY.read_text(encoding="utf-8")

    assert 'ValidateSet("Probe", "Register", "RecoverNow"' in text
    assert 'method = "config/mcpServer/reload"' not in text
    assert '-Method "config/mcpServer/reload"' in text
    assert '-Method "plugin/list"' in text
    assert '-Method "mcpServerStatus/list"' in text
    assert '-Method "mcpServer/resource/read"' in text
    assert 'canonical_plugin_selector = $script:CanonicalStableSelector' in text
    assert 'exact_tool_count = $toolCount' in text
    assert '$toolCount -ne 83' in text
    assert 'governedResourceUri = "ui://evidence-lane/governed-console-v5.html"' in text
    assert 'live_desktop_control_plane = "HOST_CAPABILITY_UNAVAILABLE_WINDOWS_APP_SERVER_DAEMON"' in text
    assert 'mcp_inventory_scope = "ISOLATED_APP_SERVER_GLOBAL_RUNTIME"' in text
    assert 'task_continuity_scope = "PERSISTED_EXACT_THREAD_AND_GOAL"' in text
    assert "thread_scoped_mcp_inventory_available = $false" in text
    assert "live_host_next_active_turn_refresh_claimed = $false" in text
    assert "probe_sha256 = $probeSha256" in text
    assert "thread_resume_invoked = $false" in text
    assert "turn_started = $false" in text
    assert "prompt_injected = $false" in text
    assert "app_restarted = $false" in text
    assert "restart_fallback_invoked = $false" in text
