[CmdletBinding()]
param(
    [ValidateSet("Prepare", "Restart", "Relaunch")]
    [string]$Action = "Prepare",
    [Parameter(Mandatory = $true)]
    [string]$InstallReceipt,
    [string]$InstallReceiptSha256,
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceSessionId,
    [Parameter(Mandatory = $true)]
    [string]$TaskId,
    [Parameter(Mandatory = $true)]
    [string]$HostSessionId,
    [int]$TargetProcessId,
    [string]$PreparationReceipt,
    [string]$PreparationReceiptSha256,
    [string]$ReceiptDirectory = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v200\restart",
    [string]$AppId = "OpenAI.Codex_2p2nqsd0c76g0!App",
    [switch]$ConfirmRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required sealed file is missing: $Path"
    }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToUpperInvariant()
}

function Get-RootCodexProcess([int]$ProcessId) {
    $row = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $row) { throw "The exact target process does not exist." }
    $path = [string]$row.ExecutablePath
    $command = [string]$row.CommandLine
    if (
        $row.Name -ne "ChatGPT.exe" -or
        $command -match "--type=" -or
        $path -notmatch "\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\ChatGPT\.exe$"
    ) {
        throw "Only the exact root Codex desktop process may be restarted."
    }
    return $row
}

function Write-JsonReceipt([string]$Path, [System.Collections.IDictionary]$Body) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Path) + "." + [guid]::NewGuid().ToString("N"))
    $Body | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $Path
}

$exactInstallReceipt = (Resolve-Path -LiteralPath $InstallReceipt).Path
$observedInstallSha = Get-Sha256 $exactInstallReceipt
if ($InstallReceiptSha256 -and $observedInstallSha -ne $InstallReceiptSha256.ToUpperInvariant()) {
    throw "The exact install receipt SHA-256 does not match."
}
$install = Get-Content -LiteralPath $exactInstallReceipt -Raw | ConvertFrom-Json
if (
    $install.schema -ne "evidence-lane.codex-stable-installation.v2" -or
    $install.status -ne "PASS" -or
    $install.plugin.version -notlike "2.0.0+codex.*" -or
    $install.activation.state -ne "INSTALLED_RESTART_REQUIRED" -or
    $install.candidate_created_or_accepted -ne $false -or
    $install.pointer_moved -ne $false
) {
    throw "The supplied v2 installation receipt is not restart-eligible."
}

if ($Action -eq "Prepare") {
    if ($TargetProcessId -le 0) { throw "Prepare requires -TargetProcessId." }
    $process = Get-RootCodexProcess $TargetProcessId
    $receiptPath = Join-Path $ReceiptDirectory "CODEX_RESTART_PREPARATION.json"
    $body = [ordered]@{
        schema = "evidence-lane.codex-restart-preparation.v2"
        state = "PREPARED_NOT_RESTARTED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        install_receipt = $exactInstallReceipt
        install_receipt_sha256 = $observedInstallSha
        plugin_version = [string]$install.plugin.version
        target = [ordered]@{
            process_id = [int]$process.ProcessId
            executable_path = [string]$process.ExecutablePath
            command_line_has_renderer_type = $false
            app_id = $AppId
        }
        continuation = [ordered]@{
            same_task_required = $true
            user_reentry_action = "OPEN_THE_SAME_CODEX_TASK"
            goal_resumes_from_persistent_task_and_change_display = $true
            lifecycle_resume_call_required = $false
            state_travel_required = $false
        }
        hot_reload_claimed = $false
        process_stopped = $false
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-JsonReceipt $receiptPath $body
    [ordered]@{
        status = "PASS"
        state = "PREPARED_NOT_RESTARTED"
        receipt_path = $receiptPath
        receipt_sha256 = Get-Sha256 $receiptPath
        next_action = "RUN_RESTART_WITH_EXACT_RECEIPT_SHA_AND_CONFIRMRESTART"
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq "Restart") {
    if (-not $ConfirmRestart) { throw "Restart requires -ConfirmRestart." }
    if (-not $PreparationReceipt -or -not $PreparationReceiptSha256) {
        throw "Restart requires the exact preparation receipt path and SHA-256."
    }
    $exactPreparation = (Resolve-Path -LiteralPath $PreparationReceipt).Path
    if ((Get-Sha256 $exactPreparation) -ne $PreparationReceiptSha256.ToUpperInvariant()) {
        throw "The preparation receipt SHA-256 does not match."
    }
    $prepared = Get-Content -LiteralPath $exactPreparation -Raw | ConvertFrom-Json
    if (
        $prepared.schema -ne "evidence-lane.codex-restart-preparation.v2" -or
        $prepared.state -ne "PREPARED_NOT_RESTARTED" -or
        $prepared.project_id -ne $ProjectId -or
        $prepared.evidence_session_id -ne $EvidenceSessionId -or
        $prepared.task_id -ne $TaskId -or
        $prepared.host_session_id -ne $HostSessionId -or
        $prepared.install_receipt_sha256 -ne $observedInstallSha -or
        [int]$prepared.target.process_id -ne $TargetProcessId
    ) {
        throw "The preparation receipt does not bind this exact task and process."
    }
    $process = Get-RootCodexProcess $TargetProcessId
    if ([string]$process.ExecutablePath -ne [string]$prepared.target.executable_path) {
        throw "The exact target executable changed after preparation."
    }
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Action", "Relaunch",
        "-InstallReceipt", $exactInstallReceipt,
        "-InstallReceiptSha256", $observedInstallSha,
        "-ProjectId", $ProjectId,
        "-EvidenceSessionId", $EvidenceSessionId,
        "-TaskId", $TaskId,
        "-HostSessionId", $HostSessionId,
        "-TargetProcessId", [string]$TargetProcessId,
        "-PreparationReceipt", $exactPreparation,
        "-PreparationReceiptSha256", $PreparationReceiptSha256,
        "-ReceiptDirectory", $ReceiptDirectory,
        "-AppId", $AppId
    )
    Start-Process -FilePath $powershell -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    Stop-Process -Id $TargetProcessId -Force
    exit 0
}

if ($Action -eq "Relaunch") {
    if (-not $PreparationReceipt -or -not $PreparationReceiptSha256) {
        throw "Internal relaunch requires the sealed preparation receipt."
    }
    if ((Get-Sha256 $PreparationReceipt) -ne $PreparationReceiptSha256.ToUpperInvariant()) {
        throw "Internal relaunch receipt mismatch."
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    while (Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue) {
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            throw "The exact Codex root process did not stop within 60 seconds."
        }
        Start-Sleep -Milliseconds 250
    }
    Start-Process -FilePath "explorer.exe" -ArgumentList "shell:AppsFolder\$AppId" -WindowStyle Hidden | Out-Null
    $relaunchPath = Join-Path $ReceiptDirectory "CODEX_RELAUNCH_RECEIPT.json"
    Write-JsonReceipt $relaunchPath ([ordered]@{
        schema = "evidence-lane.codex-relaunch-receipt.v2"
        state = "RELAUNCH_REQUESTED_USER_MUST_OPEN_SAME_TASK"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        prior_host_session_id = $HostSessionId
        preparation_receipt_sha256 = $PreparationReceiptSha256.ToUpperInvariant()
        install_receipt_sha256 = $observedInstallSha
        app_id = $AppId
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        requested_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    })
    exit 0
}
