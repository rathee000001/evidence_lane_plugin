from __future__ import annotations

import re
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


def test_one_shared_manager_uses_one_mutable_row_per_exact_task() -> None:
    text = _text()
    assert 'manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"' in text
    assert 'binding_scope = "MUTABLE_EXACT_TASK_ROW"' in text
    assert (
        'scope = "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_ON_THIS_WINDOWS_USER"'
        in text
    )
    assert (
        '($ExactTaskId.ToLowerInvariant() + ".json")'
        in text
    )
    assert '"-Action", "RecoverAtLogon"' in text
    install_manager = text[
        text.index("function Install-RecoveryManager") : text.index(
            "function Get-BootIdSha256"
        )
    ]
    assert '"-TaskId"' not in install_manager
    assert 'task_binding_scope = "SEPARATE_MUTABLE_REGISTRY"' in text
    assert "registry_origin_identity_used_for_authorization = $false" in text


def test_each_registration_revises_only_its_row_and_seals_an_event_receipt() -> None:
    text = _text()
    register = text[text.index('if ($Action -eq "Register")') : text.index(
        'if ($Action -eq "Unregister")'
    )]
    assert "$bindingPath = Get-BindingPath $TaskId" in register
    assert "$previousRecord = Read-SealedBinding $bindingPath" in register
    assert "$bindingRevision = (Get-BindingRevision $previousRecord) + 1" in register
    assert "previous_binding_sha256 = $previousBindingSha256" in register
    assert "last_binding_correlation_id = $bindingCorrelationId" in register
    assert "Write-BindingEventReceipt" in register
    assert '"GOAL_BINDING_CREATED"' in register
    assert '"GOAL_BINDING_REFRESHED"' in register
    assert 'schema = $script:BindingEventSchema' in text
    assert "after_binding_sha256 = $AfterBindingSha256" in text
    assert "another_binding_mutated = $false" in text
    assert "task_binding_receipt_sha256 = Get-Sha256 $exactBindingPath" in register
    assert (
        "An exact task binding cannot be refreshed across project or governed session identity."
        in register
    )


def test_logon_enumeration_isolates_each_binding_before_host_resolution() -> None:
    text = _text()
    start = text.index('if ($Action -eq "RecoverAtLogon")')
    end = text.index(
        "$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)", start
    )
    logon = text[start:end]
    assert "foreach ($bindingFile in $bindingFiles)" in logon
    assert "Read-SealedBinding $bindingFile.FullName" in logon
    assert 'state = "INVALID_BINDING_ISOLATED"' in logon
    assert "continue" in logon
    per_binding = logon[logon.index("foreach ($bindingFile") :]
    assert per_binding.index("try {") < per_binding.index(
        "$boundHostProfile = Resolve-GoalBindingHostProfile $record"
    )
    assert 'manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"' in logon
    assert 'another_binding_mutated = $false' in logon
    assert re.search(
        r"catch \{[\s\S]*?RECOVERY_ATTEMPT_FAILED_BACKOFF_PENDING"
        r"[\s\S]*?RECOVERY_ATTEMPTS_EXHAUSTED_EXACT_BINDING_KILL_SWITCHED"
        r"[\s\S]*?\}[\s\S]*?Write-AtomicJson",
        logon,
    )


def test_missing_or_ended_goal_tombstones_only_that_binding_without_retry() -> None:
    text = _text()
    tombstone = text[text.index("function Set-ExactBindingTombstone") : text.index(
        "function Read-TaskHostProfile"
    )]
    assert "$path = Get-BindingPath $exactTaskId" in tombstone
    assert 'state = "TOMBSTONED_GOAL_NOT_ACTIVE"' in tombstone
    assert 'Event "GOAL_BINDING_TOMBSTONED"' in tombstone
    assert "another_binding_mutated = $false" in tombstone
    assert "retry_allowed = $false" in tombstone
    assert "Remove-Item" not in tombstone

    assert 'state = "SKIPPED_GOAL_NOT_ACTIVE"' not in text
    assert text.count("Set-ExactBindingTombstone") == 3
    assert text.count('state = "EXACT_TASK_BINDING_TOMBSTONED_GOAL_NOT_ACTIVE"') == 2
    assert text.count("retry_allowed = $false") >= 3


def test_goal_recovery_manager_parses_without_live_registration() -> None:
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
