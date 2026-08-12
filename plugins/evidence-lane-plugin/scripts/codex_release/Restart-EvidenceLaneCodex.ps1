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

function Get-StringSha256([string]$Value) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
    }
}

function ConvertTo-WindowsCommandLineArgument([AllowEmptyString()][string]$Value) {
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }

    $quoted = [System.Text.StringBuilder]::new()
    [void]$quoted.Append([char]34)
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]34) {
            [void]$quoted.Append([string]::new([char]92, (2 * $backslashes) + 1))
            [void]$quoted.Append([char]34)
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$quoted.Append([string]::new([char]92, $backslashes))
            $backslashes = 0
        }
        [void]$quoted.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$quoted.Append([string]::new([char]92, 2 * $backslashes))
    }
    [void]$quoted.Append([char]34)
    return $quoted.ToString()
}

function Assert-CodexThreadProtocol() {
    $protocolPath = "Registry::HKEY_CLASSES_ROOT\codex"
    if (-not (Test-Path -LiteralPath $protocolPath)) {
        throw "The registered Codex desktop protocol is unavailable."
    }
    $protocol = Get-Item -LiteralPath $protocolPath
    if ($null -eq $protocol.GetValue("URL Protocol", $null)) {
        throw "The registered Codex desktop protocol is invalid."
    }
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

function Get-NewRootCodexProcess([int]$PriorProcessId) {
    $matches = @(
        Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe'" |
            Where-Object {
                [int]$_.ProcessId -ne $PriorProcessId -and
                [string]$_.CommandLine -notmatch "--type=" -and
                [string]$_.ExecutablePath -match "\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\ChatGPT\.exe$"
            }
    )
    if ($matches.Count -gt 1) {
        throw "More than one new Codex desktop root process was observed."
    }
    if ($matches.Count -eq 1) { return $matches[0] }
    return $null
}

function Write-JsonReceipt([string]$Path, [System.Collections.IDictionary]$Body) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Path) + "." + [guid]::NewGuid().ToString("N"))
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText(
        $temporary,
        ($Body | ConvertTo-Json -Depth 12),
        $utf8NoBom
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$exactInstallReceipt = (Resolve-Path -LiteralPath $InstallReceipt).Path
$taskIdPattern = '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
if ($TaskId -notmatch $taskIdPattern) {
    throw "TaskId must be the exact Codex conversation identifier."
}
$taskUri = "codex://threads/$TaskId"
$taskUriSha256 = Get-StringSha256 $taskUri
$installationDirectory = Split-Path -Parent $exactInstallReceipt
$taskBindingDirectory = Join-Path $installationDirectory "task-bindings"
$taskBindingPath = Join-Path $taskBindingDirectory ($TaskId.ToLowerInvariant() + ".json")
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
            user_reentry_action = "NONE_AUTO_OPEN_EXACT_TASK"
            task_navigation_mode = "CODEX_THREAD_DEEPLINK"
            task_uri_sha256 = $taskUriSha256
            coordinate_clicking_used = $false
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
    $preparationReceiptSha256 = Get-Sha256 $receiptPath
    $taskBinding = [ordered]@{
        schema = "evidence-lane.codex-task-binding.v1"
        state = "EXACT_TASK_BINDING_PREPARED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        governed_host_session_id = $HostSessionId
        plugin_version = [string]$install.plugin.version
        task_uri_sha256 = $taskUriSha256
        preparation_receipt = $receiptPath
        preparation_receipt_sha256 = $preparationReceiptSha256
        install_receipt = $exactInstallReceipt
        install_receipt_sha256 = $observedInstallSha
        claim_scope = "EXACT_CODEX_THREAD_ID_ONLY"
        alias_claim_allowed = $true
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-JsonReceipt $taskBindingPath $taskBinding
    [ordered]@{
        status = "PASS"
        state = "PREPARED_NOT_RESTARTED"
        receipt_path = $receiptPath
        receipt_sha256 = $preparationReceiptSha256
        task_binding_receipt_path = $taskBindingPath
        task_binding_receipt_sha256 = Get-Sha256 $taskBindingPath
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
    if (-not (Test-Path -LiteralPath $taskBindingPath -PathType Leaf)) {
        throw "The exact Codex task binding receipt is missing."
    }
    $taskBinding = Get-Content -LiteralPath $taskBindingPath -Raw | ConvertFrom-Json
    if (
        $prepared.schema -ne "evidence-lane.codex-restart-preparation.v2" -or
        $prepared.state -ne "PREPARED_NOT_RESTARTED" -or
        $prepared.project_id -ne $ProjectId -or
        $prepared.evidence_session_id -ne $EvidenceSessionId -or
        $prepared.task_id -ne $TaskId -or
        $prepared.host_session_id -ne $HostSessionId -or
        $prepared.install_receipt_sha256 -ne $observedInstallSha -or
        [int]$prepared.target.process_id -ne $TargetProcessId -or
        $taskBinding.schema -ne "evidence-lane.codex-task-binding.v1" -or
        $taskBinding.state -ne "EXACT_TASK_BINDING_PREPARED" -or
        $taskBinding.project_id -ne $ProjectId -or
        $taskBinding.evidence_session_id -ne $EvidenceSessionId -or
        $taskBinding.task_id -ne $TaskId -or
        $taskBinding.governed_host_session_id -ne $HostSessionId -or
        $taskBinding.task_uri_sha256 -ne $taskUriSha256 -or
        $taskBinding.preparation_receipt_sha256 -ne $PreparationReceiptSha256.ToUpperInvariant() -or
        $taskBinding.install_receipt_sha256 -ne $observedInstallSha -or
        $taskBinding.alias_claim_allowed -ne $true
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
    $argumentLine = ($arguments | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument ([string]$_)
    }) -join " "
    Start-Process -FilePath $powershell -ArgumentList $argumentLine -WindowStyle Hidden | Out-Null
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
    $prepared = Get-Content -LiteralPath $PreparationReceipt -Raw | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath $taskBindingPath -PathType Leaf)) {
        throw "The exact Codex task binding receipt is missing."
    }
    $taskBinding = Get-Content -LiteralPath $taskBindingPath -Raw | ConvertFrom-Json
    if (
        $prepared.schema -ne "evidence-lane.codex-restart-preparation.v2" -or
        $prepared.state -ne "PREPARED_NOT_RESTARTED" -or
        $prepared.project_id -ne $ProjectId -or
        $prepared.evidence_session_id -ne $EvidenceSessionId -or
        $prepared.task_id -ne $TaskId -or
        $prepared.host_session_id -ne $HostSessionId -or
        $prepared.install_receipt_sha256 -ne $observedInstallSha -or
        $prepared.continuation.task_uri_sha256 -ne $taskUriSha256 -or
        [int]$prepared.target.process_id -ne $TargetProcessId -or
        $taskBinding.schema -ne "evidence-lane.codex-task-binding.v1" -or
        $taskBinding.state -ne "EXACT_TASK_BINDING_PREPARED" -or
        $taskBinding.project_id -ne $ProjectId -or
        $taskBinding.evidence_session_id -ne $EvidenceSessionId -or
        $taskBinding.task_id -ne $TaskId -or
        $taskBinding.governed_host_session_id -ne $HostSessionId -or
        $taskBinding.task_uri_sha256 -ne $taskUriSha256 -or
        $taskBinding.preparation_receipt_sha256 -ne $PreparationReceiptSha256.ToUpperInvariant() -or
        $taskBinding.install_receipt_sha256 -ne $observedInstallSha -or
        $taskBinding.alias_claim_allowed -ne $true
    ) {
        throw "The relaunch request does not bind the exact prepared task and process."
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    while (Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue) {
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            throw "The exact Codex root process did not stop within 60 seconds."
        }
        Start-Sleep -Milliseconds 250
    }
    Assert-CodexThreadProtocol
    Start-Process -FilePath $taskUri | Out-Null
    $newRoot = $null
    $launchDeadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    while ($null -eq $newRoot) {
        if ([DateTimeOffset]::UtcNow -ge $launchDeadline) {
            throw "No new Codex desktop root process appeared after exact-task navigation."
        }
        Start-Sleep -Milliseconds 250
        $newRoot = Get-NewRootCodexProcess $TargetProcessId
    }
    $relaunchPath = Join-Path $ReceiptDirectory "CODEX_RELAUNCH_RECEIPT.json"
    Write-JsonReceipt $relaunchPath ([ordered]@{
        schema = "evidence-lane.codex-relaunch-receipt.v2"
        state = "EXACT_TASK_RELAUNCH_REQUESTED_CODEX_ROOT_OBSERVED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        prior_host_session_id = $HostSessionId
        preparation_receipt_sha256 = $PreparationReceiptSha256.ToUpperInvariant()
        task_binding_receipt_sha256 = Get-Sha256 $taskBindingPath
        install_receipt_sha256 = $observedInstallSha
        app_id = $AppId
        task_navigation = [ordered]@{
            mode = "CODEX_THREAD_DEEPLINK"
            task_uri_sha256 = $taskUriSha256
            coordinate_clicking_used = $false
            new_root_process_id = [int]$newRoot.ProcessId
            new_root_executable_path = [string]$newRoot.ExecutablePath
            request_observed = $true
            active_task_ui_independently_proven = $false
        }
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        requested_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    })
    exit 0
}
