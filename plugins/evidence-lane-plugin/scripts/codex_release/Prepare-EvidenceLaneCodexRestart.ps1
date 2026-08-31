[CmdletBinding()]
param(
    [ValidateSet("Prepare")]
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
    [string]$ActivePlanTaskId,
    [Parameter(Mandatory = $true)]
    [int]$TargetProcessId,
    [string]$ReceiptDirectory = "$env:USERPROFILE\.codex\plugins\runtime\evidence-lane-plugin\installations\codex-v300\restart",
    [string]$RuntimeControlRoot = "$env:USERPROFILE\.codex\plugins\runtime\evidence-lane-plugin",
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "OpenAI.Codex_2p2nqsd0c76g0!App",
        "OpenAI.CodexBeta_2p2nqsd0c76g0!App"
    )]
    [string]$AppId,
    [ValidateSet("NATIVE_MCP_AVAILABLE", "HOST_TOOL_GAP")]
    [string]$HostToolTransport = "NATIVE_MCP_AVAILABLE"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:HostAppProfiles = [ordered]@{
    "OpenAI.Codex_2p2nqsd0c76g0!App" = [ordered]@{
        app_id = "OpenAI.Codex_2p2nqsd0c76g0!App"
        host_application = "CHATGPT_CODEX"
        desktop_release_channel = "CHATGPT_STABLE"
        package_family_name = "OpenAI.Codex_2p2nqsd0c76g0"
        process_name = "ChatGPT.exe"
        root_path_pattern = '\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\ChatGPT\.exe$'
    }
    "OpenAI.CodexBeta_2p2nqsd0c76g0!App" = [ordered]@{
        app_id = "OpenAI.CodexBeta_2p2nqsd0c76g0!App"
        host_application = "CHATGPT_BETA_CODEX"
        desktop_release_channel = "CHATGPT_BETA"
        package_family_name = "OpenAI.CodexBeta_2p2nqsd0c76g0"
        process_name = "ChatGPT (Beta).exe"
        root_path_pattern = '\\WindowsApps\\OpenAI\.CodexBeta_[^\\]+\\app\\ChatGPT \(Beta\)\.exe$'
    }
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required sealed file is missing: $Path"
    }
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Get-StringSha256([string]$Value) {
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
    }
}

function Write-SealedJson([string]$Path, [System.Collections.IDictionary]$Body) {
    $core = $Body | ConvertTo-Json -Depth 16 -Compress
    $sealed = [ordered]@{}
    foreach ($key in $Body.Keys) {
        $sealed[$key] = $Body[$key]
    }
    $sealed.receipt_sha256 = Get-StringSha256 ($core + "`n")
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Path) + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $json = $sealed | ConvertTo-Json -Depth 16
        [IO.File]::WriteAllText($temporary, $json + "`n", [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

if ($Action -cne "Prepare") {
    throw "Only terminal-safe restart preparation is supported."
}
if ($TaskId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
    throw "The exact Codex task UUID is malformed."
}
if (-not $script:HostAppProfiles.Contains($AppId)) {
    throw "The exact Codex AppUserModelID is not allowlisted."
}

$exactRuntimeControlRoot = [IO.Path]::GetFullPath($RuntimeControlRoot)
$expectedRuntimeControlRoot = [IO.Path]::GetFullPath(
    "$env:USERPROFILE\.codex\plugins\runtime\evidence-lane-plugin"
)
if ($exactRuntimeControlRoot -cne $expectedRuntimeControlRoot) {
    throw "Restart preparation requires the exact hidden runtime-control root."
}
$exactReceiptDirectory = [IO.Path]::GetFullPath($ReceiptDirectory)
if (-not $exactReceiptDirectory.StartsWith($exactRuntimeControlRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Restart preparation receipts must stay under the hidden runtime-control root."
}

$exactInstallReceipt = (Resolve-Path -LiteralPath $InstallReceipt).Path
$observedInstallSha256 = Get-Sha256 $exactInstallReceipt
if (
    [string]::IsNullOrWhiteSpace($InstallReceiptSha256) -or
    $observedInstallSha256 -cne $InstallReceiptSha256.ToUpperInvariant()
) {
    throw "The installation receipt SHA-256 does not match."
}
$install = Get-Content -LiteralPath $exactInstallReceipt -Raw | ConvertFrom-Json
if (
    [string]$install.schema -cne "evidence-lane.codex-stable-installation.v2" -or
    [string]$install.status -cne "PASS" -or
    [string]$install.activation.state -cne "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED" -or
    $install.restart_required -ne $true -or
    $install.state_travel_invoked -ne $false -or
    $install.hooks_enabled_by_update -ne $false -or
    $install.candidate_created_or_accepted -ne $false -or
    $install.pointer_moved -ne $false -or
    $install.hil_inferred -ne $false -or
    [string]$install.plugin.version -cnotmatch '^3\.0\.0\+codex\.[0-9]{14}$'
) {
    throw "The installation receipt is not eligible for terminal-safe local restart preparation."
}

$hostProfile = $script:HostAppProfiles[$AppId]
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetProcessId"
if ($null -eq $process) {
    throw "The exact Codex root process is absent."
}
$executablePath = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
if (
    [IO.Path]::GetFileName($executablePath) -cne [string]$hostProfile.process_name -or
    $executablePath -cnotmatch [string]$hostProfile.root_path_pattern -or
    [string]$process.CommandLine -match '(?:^|\s)--type='
) {
    throw "The target process is not the exact bound Codex root for the selected channel."
}

$taskUri = "codex://threads/$TaskId"
$taskUriSha256 = Get-StringSha256 $taskUri
$preparedAtUtc = [DateTimeOffset]::UtcNow.ToString("o")
$preparationPath = Join-Path $exactReceiptDirectory "CODEX_RESTART_PREPARATION.json"
$taskBindingDirectory = Join-Path (Split-Path -Parent $exactReceiptDirectory) "task-bindings"
$taskBindingPath = Join-Path $taskBindingDirectory ($TaskId.ToLowerInvariant() + ".json")

$preparation = [ordered]@{
    schema = "evidence-lane.codex-terminal-safe-restart-preparation.v1"
    status = "PASS"
    state = "PREPARED_AWAITING_CURRENT_TURN_TERMINAL_RESPONSE"
    project_id = $ProjectId
    evidence_session_id = $EvidenceSessionId
    task_id = $TaskId
    host_session_id = $HostSessionId
    active_plan_task_id = $ActivePlanTaskId
    install_receipt_path = $exactInstallReceipt
    install_receipt_sha256 = $observedInstallSha256
    plugin_version = [string]$install.plugin.version
    target = [ordered]@{
        app_id = [string]$hostProfile.app_id
        host_application = [string]$hostProfile.host_application
        desktop_release_channel = [string]$hostProfile.desktop_release_channel
        package_family_name = [string]$hostProfile.package_family_name
        process_id = $TargetProcessId
        executable_path = $executablePath
    }
    continuation = [ordered]@{
        task_uri_sha256 = $taskUriSha256
        task_uri_persisted = $false
        exact_task_reopen_required = $true
        reopen_proof_owner = "POST_RESTART_NATIVE_TASK_READBACK"
    }
    boundary = [ordered]@{
        current_turn_terminal_event_required_before_app_close = $true
        current_turn_may_be_interrupted = $false
        drain_utility_used = $false
        drain_utility_allowed = $false
        programmatic_process_stop_used = $false
        programmatic_process_stop_allowed = $false
        scheduled_restart_child_used = $false
        scheduled_restart_child_allowed = $false
        automatic_protocol_activation_used = $false
        machine_wide_protocol_handler_allowed = $false
        exact_channel_selected = $true
        user_closes_and_reopens_after_terminal_response = $true
        state_travel_replayed = $false
        hook_state_mutated = $false
        tunnel_started = $false
    }
    host_tool_transport = $HostToolTransport
    prepared_at_utc = $preparedAtUtc
}
Write-SealedJson -Path $preparationPath -Body $preparation
$preparationFileSha256 = Get-Sha256 $preparationPath

$taskBinding = [ordered]@{
    schema = "evidence-lane.codex-task-binding.v1"
    status = "PASS"
    state = "EXACT_TASK_TERMINAL_SAFE_RESTART_PREPARED"
    project_id = $ProjectId
    evidence_session_id = $EvidenceSessionId
    task_id = $TaskId
    governed_host_session_id = $HostSessionId
    active_plan_task_id = $ActivePlanTaskId
    plugin_version = [string]$install.plugin.version
    app_id = [string]$hostProfile.app_id
    desktop_release_channel = [string]$hostProfile.desktop_release_channel
    task_uri_sha256 = $taskUriSha256
    preparation_receipt_path = $preparationPath
    preparation_receipt_file_sha256 = $preparationFileSha256
    install_receipt_path = $exactInstallReceipt
    install_receipt_sha256 = $observedInstallSha256
    native_workspace_binding_source = "EXISTING_CODEX_TASK_STATE"
    native_workspace_binding_mutated = $false
    programmatic_restart_authorized = $false
    post_restart_native_proof_required = $true
    source_mutated = $false
    candidate_created_or_accepted = $false
    pointer_moved = $false
    hil_inferred = $false
    prepared_at_utc = $preparedAtUtc
}
Write-SealedJson -Path $taskBindingPath -Body $taskBinding

[ordered]@{
    status = "PASS"
    state = "TERMINAL_SAFE_RESTART_PREPARED_NOT_EXECUTED"
    task_id = $TaskId
    app_id = [string]$hostProfile.app_id
    desktop_release_channel = [string]$hostProfile.desktop_release_channel
    preparation_receipt_path = $preparationPath
    preparation_receipt_file_sha256 = $preparationFileSha256
    task_binding_receipt_path = $taskBindingPath
    task_binding_receipt_file_sha256 = Get-Sha256 $taskBindingPath
    drain_utility_used = $false
    scheduled_child_used = $false
    app_stopped = $false
    task_reopened = $false
    next_action = "FINISH_CURRENT_TURN_THEN_USER_CLOSE_AND_REOPEN_EXACT_APP_CHANNEL"
} | ConvertTo-Json -Depth 10
