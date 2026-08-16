from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGER = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)


def _text() -> str:
    return MANAGER.read_text(encoding="utf-8")


def _slice(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    return text[start_at : text.index(end, start_at)]


def test_scheduler_cannot_retry_the_shared_manager() -> None:
    text = _text()
    install = _slice(text, "function Install-RecoveryManager", "function Get-BootIdSha256")
    assert "-RestartCount" not in install
    assert "-RestartInterval" not in install
    assert "scheduler_restart_count = 0" in install
    assert "scheduler_retry_is_authority = $false" in install
    assert 'recovery_retry_owner = "IN_PROCESS_EXACT_TASK_BINDING"' in install
    assert 'kill_switch_scope = "EXACT_TASK_BINDING_ONLY"' in install
    assert "restart_count_used_for_hook_or_helper_causation = $false" in install


def test_each_binding_has_a_fixed_attempt_ceiling_and_bounded_backoff() -> None:
    text = _text()
    logon = _slice(
        text,
        'if ($Action -eq "RecoverAtLogon")',
        "$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)",
    )
    assert "$script:MaxRecoveryAttemptsPerBinding = 2" in text
    assert "$script:RecoveryBackoffSeconds = @(0, 2)" in text
    assert (
        "for ($attempt = 1; $attempt -le $script:MaxRecoveryAttemptsPerBinding; "
        "$attempt += 1)"
    ) in logon
    assert "Start-Sleep -Seconds $backoffSeconds" in logon
    assert 'state = "RECOVERY_ATTEMPT_FAILED_BACKOFF_PENDING"' in logon
    assert "$receipt.next_backoff_seconds" in logon
    assert "scheduler_restart_requested = $false" in logon


def test_attempts_use_stable_per_owner_correlation_and_idempotent_receipts() -> None:
    text = _text()
    correlation = _slice(
        text,
        "function Get-RecoveryAttemptCorrelationId",
        "function Get-RecoveryAttemptReceiptPath",
    )
    assert "$ExactTaskId" in correlation
    assert "$BootIdSha256" in correlation
    assert "$BindingSha256" in correlation
    assert "$Attempt" in correlation
    assert "$ManagerRunCorrelationId" in correlation

    logon = _slice(
        text,
        'if ($Action -eq "RecoverAtLogon")',
        "$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)",
    )
    assert "$bindingInventorySha256" in logon
    assert "$managerRunCorrelationId" in logon
    assert "Read-RecoveryAttemptReceipt" in logon
    assert 'schema = $script:RecoveryAttemptSchema' in logon
    assert "if (Test-Path -LiteralPath $receiptPath -PathType Leaf)" in logon
    assert "Write-AtomicJson $receiptPath $receipt" in logon


def test_ended_goal_is_terminal_without_entering_retry_or_kill_switch() -> None:
    text = _text()
    logon = _slice(
        text,
        'if ($Action -eq "RecoverAtLogon")',
        "$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)",
    )
    ended = logon[
        logon.index('if ($probe.goal_status -ne "active")') : logon.index(
            "catch {", logon.index('if ($probe.goal_status -ne "active")')
        )
    ]
    assert "Set-ExactBindingTombstone" in ended
    assert 'state = "EXACT_TASK_BINDING_TOMBSTONED_GOAL_NOT_ACTIVE"' in ended
    assert "$receipt.retry_allowed = $false" in ended
    assert "$tombstonedCount += 1" in ended
    assert "Set-ExactBindingRecoveryKillSwitch" not in ended


def test_exhausted_failure_kill_switches_only_the_exact_binding() -> None:
    text = _text()
    kill_switch = _slice(
        text,
        "function Set-ExactBindingRecoveryKillSwitch",
        "function Read-TaskHostProfile",
    )
    assert "$path = Get-BindingPath $exactTaskId" in kill_switch
    assert 'state = "RECOVERY_KILL_SWITCHED"' in kill_switch
    assert 'kill_switch_scope = "EXACT_TASK_BINDING_ONLY"' in kill_switch
    assert 'Event "GOAL_BINDING_RECOVERY_KILL_SWITCHED"' in kill_switch
    assert "another_binding_mutated = $false" in kill_switch
    assert "retry_allowed = $false" in kill_switch
    assert "Remove-Item" not in kill_switch

    logon = _slice(
        text,
        'if ($Action -eq "RecoverAtLogon")',
        "$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)",
    )
    assert "if ($attempt -lt $script:MaxRecoveryAttemptsPerBinding)" in logon
    assert "Set-ExactBindingRecoveryKillSwitch" in logon
    assert 'state = "RECOVERY_ATTEMPTS_EXHAUSTED_EXACT_BINDING_KILL_SWITCHED"' in logon
    assert "if ($failures -gt 0) { exit 1 }" not in logon
    assert "process_exit_code = 0" in logon
    assert logon.rstrip().endswith("}")


def test_invalid_bindings_are_deduped_and_do_not_block_other_bindings() -> None:
    text = _text()
    logon = _slice(
        text,
        'if ($Action -eq "RecoverAtLogon")',
        "$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)",
    )
    assert "$invalidCorrelationId" in logon
    assert 'state = "INVALID_BINDING_ISOLATED"' in logon
    assert "RECOVERY_INVALID_BINDING_" in logon
    assert "if (-not (Test-Path -LiteralPath $invalidPath -PathType Leaf))" in logon
    assert "continue" in logon
    assert '"NO_RECOVERABLE_BINDINGS"' in logon
    assert '"RECOVERY_RUN_COMPLETED_WITH_ISOLATED_BINDING_FAILURES"' in logon
    assert "exit 0" in logon


def test_goal_recovery_retry_manager_parses_without_live_execution() -> None:
    command = (
        "$errors=$null; "
        f"[Management.Automation.Language.Parser]::ParseFile('{MANAGER}',"
        "[ref]$null,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
