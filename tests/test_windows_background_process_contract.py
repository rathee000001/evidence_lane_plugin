from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
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
                        missing.append(
                            f"{path}:{node.lineno}:subprocess.{node.func.attr}"
                        )
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in {"system", "popen"}
                ):
                    forbidden.append(f"{path}:{node.lineno}:os.{node.func.attr}")

    assert not missing, "Missing Windows no-console creationflags:\n" + "\n".join(missing)
    assert not forbidden, "Un-governed child-process launch route:\n" + "\n".join(forbidden)


def test_background_routes_are_hidden_single_version_and_exact_task_only() -> None:
    tunnel = TUNNEL_INSTALL.read_text(encoding="utf-8")

    assert not (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
    ).exists()
    assert not (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Switch-EvidenceLaneCodexSlot.ps1"
    ).exists()
    assert not (
        PLUGIN
        / "scripts"
        / "windows_tunnel"
        / "Manage-EvidenceLaneTunnelVersions.ps1"
    ).exists()

    assert not (
        PLUGIN / "scripts" / "codex_release" / "drain_codex_task_turns.py"
    ).exists()
    assert not (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "Prepare-EvidenceLaneCodexRestart.ps1"
    ).exists()

    assert "Stop-AndRetainPriorTunnelRuntimes" in tunnel
    assert "Stop-DisableAndRetainPriorTunnelTasks" in tunnel
    assert "prior_versioned_runtimes_retained = $true" in tunnel
    assert "prior_versioned_tasks_retained = $true" in tunnel
    assert "prior_versioned_runtime_deletion_required = $false" in tunnel
    assert "one_active_version_required = $true" in tunnel
    assert ".codex\\plugins\\runtime\\evidence-lane-plugin" in tunnel
    assert "project_authority_root_hardcoded = $false" in tunnel
    assert "workspace_hardcoded = $false" in tunnel
    assert "New-ScheduledTaskTrigger -AtLogOn" in tunnel
    assert "scheduled_task_transport_used = $true" in tunnel
