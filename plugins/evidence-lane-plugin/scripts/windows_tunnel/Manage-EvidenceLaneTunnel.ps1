[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Status", "Repair", "Remove")]
    [string]$Action,
    [string]$RuntimeControlRoot = "$env:USERPROFILE\.codex\plugins\runtime\evidence-lane-plugin",
    [string]$RuntimeRoot = "",
    [string]$ProfileName = "evidence_lane_v300_stable_build_transport",
    [string]$ProfileDir = "$env:APPDATA\tunnel-client",
    [string]$ReleaseToken = "v300",
    [string]$TaskName = "EvidenceLane-Tunnel-v300-stable-build",
    [int]$ReadyTimeoutSeconds = 90,
    [switch]$ConfirmRemoval
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $RuntimeControlRoot "tunnel-runtime-v300-stable-build"
}
$exactRuntimeControlRoot = [IO.Path]::GetFullPath($RuntimeControlRoot)
$exactRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$expectedRuntimeControlRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:USERPROFILE ".codex\plugins\runtime\evidence-lane-plugin")
)
if ($exactRuntimeControlRoot -cne $expectedRuntimeControlRoot) {
    throw "Tunnel management requires the exact hidden Evidence Lane Codex runtime root."
}
$approvedRuntimeParent = $exactRuntimeControlRoot + [IO.Path]::DirectorySeparatorChar
if (-not $exactRuntimeRoot.StartsWith($approvedRuntimeParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The managed tunnel runtime must remain inside Codex's hidden Evidence Lane runtime root."
}

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$client = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$filePrefix = "evidence_lane_${ReleaseToken}"
$pidFile = Join-Path $RuntimeRoot "${filePrefix}_tunnel.pid"
$healthUrlFile = Join-Path $RuntimeRoot "${filePrefix}_health.url"
$profileFile = Join-Path $ProfileDir ($ProfileName + ".yaml")
$markerFile = Join-Path $RuntimeRoot "evidence-lane-tunnel-installation.json"
if (Test-Path -LiteralPath $markerFile -PathType Leaf) {
    $boundMarker = Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
    if (
        $boundMarker.schema -ne "evidence-lane.versioned-secure-mcp-tunnel-installation.v1" -or
        [string]$boundMarker.release_token -ne $ReleaseToken -or
        [IO.Path]::GetFullPath([string]$boundMarker.runtime_root) -ne [IO.Path]::GetFullPath($RuntimeRoot) -or
        [string]$boundMarker.profile_name -ne $ProfileName -or
        [string]$boundMarker.task_name -ne $TaskName -or
        [int]$boundMarker.exact_visible_tool_count -le 0 -or
        [int]$boundMarker.exact_active_read_tool_count -le 0 -or
        [int]$boundMarker.exact_fail_closed_write_tool_count -le 0 -or
        [int]$boundMarker.exact_visible_tool_count -ne (
            [int]$boundMarker.exact_active_read_tool_count +
            [int]$boundMarker.exact_fail_closed_write_tool_count
        )
    ) {
        throw "The management request does not match the exact release-bound tunnel marker."
    }
}

function Get-VerifiedTunnelProcess {
    $parsedPid = 0
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
        return $null
    }
    $rawPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if (-not [int]::TryParse($rawPid, [ref]$parsedPid)) {
        return $null
    }
    $process = Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    try {
        $processPath = (Resolve-Path -LiteralPath $process.Path).Path
        $clientPath = (Resolve-Path -LiteralPath $client).Path
        if ($processPath -ne $clientPath) {
            return $null
        }
        if ((Get-FileHash -LiteralPath $processPath -Algorithm SHA256).Hash -ne $expectedClientSha256) {
            return $null
        }
    }
    catch {
        return $null
    }
    return $process
}

function Get-TunnelStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = if ($null -ne $task) {
        Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $process = Get-VerifiedTunnelProcess
    $parsedPid = if ($null -ne $process) { $process.Id } else { $null }
    $binaryHashValid = $false
    if (Test-Path -LiteralPath $client -PathType Leaf) {
        $binaryHashValid = (Get-FileHash -LiteralPath $client -Algorithm SHA256).Hash -eq $expectedClientSha256
    }
    $ready = $false
    if ($null -ne $process -and $binaryHashValid -and (Test-Path -LiteralPath $healthUrlFile -PathType Leaf)) {
        & $client health `
            --url-file $healthUrlFile `
            --pid $parsedPid `
            --require-control-plane-poll `
            --json *> $null
        $ready = $LASTEXITCODE -eq 0
    }
    $profileText = if (Test-Path -LiteralPath $profileFile -PathType Leaf) {
        Get-Content -LiteralPath $profileFile -Raw
    } else {
        ""
    }
    $marker = if (Test-Path -LiteralPath $markerFile -PathType Leaf) {
        Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
    } else {
        $null
    }
    return [ordered]@{
        status = if ($ready) { "PASS" } else { "BLOCKED" }
        release = if ($null -ne $marker) { [string]$marker.release } else { $null }
        release_token = if ($null -ne $marker) { [string]$marker.release_token } else { $ReleaseToken }
        runtime_identity_matches_release = if ($null -ne $marker) { [bool]$marker.runtime_identity_matches_release } else { $false }
        slot_role = if ($null -ne $marker) { [string]$marker.slot_role } else { $null }
        byte_frozen = if ($null -ne $marker) { [bool]$marker.byte_frozen } else { $false }
        task_name = $TaskName
        task_registered = $null -ne $task
        task_state = if ($null -ne $task) { [string]$task.State } else { $null }
        task_last_result = if ($null -ne $taskInfo) { $taskInfo.LastTaskResult } else { $null }
        pid = $parsedPid
        process_running = $null -ne $process
        stable_binary_hash_valid = $binaryHashValid
        control_plane_poll_ready = $ready
        profile_file = $profileFile
        profile_exists = -not [string]::IsNullOrWhiteSpace($profileText)
        evidence_lane_layer_launcher_configured = $profileText.Contains("_INTERNAL_EVIDENCE_LANE_MCP_LAYER_DO_NOT_RUN.ps1")
        exposure_profile = "CODEX_INTERACTIVE_SUPPORT"
        transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"
        served_exposure_layer = "CODEX_INTERACTIVE_SUPPORT"
        chatgpt_is_layer_not_transport_identity = $true
        codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"
        codex_tunnel_lifecycle_proof_allowed = $false
        data_root = if ($null -ne $marker) { [string]$marker.data_root } else { $null }
        project_binding = "NONE_TRANSPORT_ONLY"
        project_route_argument = "project_id"
        project_route_argument_required = $true
        cross_project_fallback_allowed = $false
        exact_visible_tool_count = if ($null -ne $marker) { [int]$marker.exact_visible_tool_count } else { 0 }
        exact_active_read_tool_count = if ($null -ne $marker) { [int]$marker.exact_active_read_tool_count } else { 0 }
        exact_fail_closed_write_tool_count = if ($null -ne $marker) { [int]$marker.exact_fail_closed_write_tool_count } else { 0 }
        exact_skill_count = if ($null -ne $marker) { [int]$marker.exact_skill_count } else { 0 }
        exact_command_count = if ($null -ne $marker) { [int]$marker.exact_command_count } else { 0 }
        exact_hook_event_count = if ($null -ne $marker) { [int]$marker.exact_hook_event_count } else { 0 }
        exact_hook_handler_count = if ($null -ne $marker) { [int]$marker.exact_hook_handler_count } else { 0 }
        exact_provider_count = if ($null -ne $marker) { [int]$marker.exact_provider_count } else { 0 }
        health_url_file = $healthUrlFile
        runtime_key_plaintext_reported = $false
        windows_console_policy = "PERSISTENT_OR_HIDDEN_NO_TRANSIENT_CONSOLE"
        prior_versioned_runtimes_retained = $true
        prior_versioned_tasks_retained = $true
        one_active_version_required = $true
    }
}

function Wait-TunnelReady {
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        $status = Get-TunnelStatus
        if ($status.control_plane_poll_ready) {
            return $status
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    return Get-TunnelStatus
}

if ($Action -eq "Status") {
    $status = Get-TunnelStatus
    $status | ConvertTo-Json -Depth 4
    exit $(if ($status.control_plane_poll_ready) { 0 } else { 1 })
}

if ($Action -eq "Stop") {
    $priorStatus = Get-TunnelStatus
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $process = Get-VerifiedTunnelProcess
    if ($null -ne $process) {
        Stop-Process -Id $process.Id
        Wait-Process -Id $process.Id -Timeout 20 -ErrorAction SilentlyContinue
    }
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
    [ordered]@{
        status = "STOPPED_SAVED"
        release = $priorStatus.release
        release_token = $priorStatus.release_token
        task_name = $TaskName
        runtime_root = [IO.Path]::GetFullPath($RuntimeRoot)
        reusable_without_reinstall = $true
    } | ConvertTo-Json -Depth 4
    exit 0
}

if ($Action -eq "Remove") {
    if (-not $ConfirmRemoval) {
        throw "Removal is fail-closed. Repeat with -ConfirmRemoval after reviewing the exact runtime root."
    }
    $approvedParent = $exactRuntimeControlRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $exactRuntimeRoot.StartsWith($approvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a runtime outside Codex's hidden Evidence Lane runtime directory."
    }
    if (-not (Test-Path -LiteralPath $markerFile -PathType Leaf)) {
        throw "Refusing removal because the Evidence Lane installation marker is missing."
    }
    $marker = Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
    $exactProfileFile = [IO.Path]::GetFullPath($profileFile)
    $markerProfileFile = [IO.Path]::GetFullPath([string]$marker.profile_file)
    if (
        [IO.Path]::GetFullPath([string]$marker.runtime_root) -ne $exactRuntimeRoot -or
        [string]$marker.task_name -ne $TaskName -or
        [string]$marker.profile_name -ne $ProfileName -or
        $markerProfileFile -ne $exactProfileFile
    ) {
        throw "Refusing removal because the installation marker does not bind this exact target."
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $process = Get-VerifiedTunnelProcess
    if ($null -ne $process) {
        Stop-Process -Id $process.Id
        Wait-Process -Id $process.Id -Timeout 20 -ErrorAction SilentlyContinue
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $profileFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $exactRuntimeRoot -Recurse -Force
    [ordered]@{
        status = "REMOVED"
        task_name = $TaskName
        profile_file = $profileFile
        runtime_root = $exactRuntimeRoot
        recoverable = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "The scheduled task is missing. Run Install-EvidenceLaneTunnel.ps1 first."
}

Enable-ScheduledTask -TaskName $TaskName | Out-Null

if ($Action -eq "Repair") {
    $before = Get-TunnelStatus
    if ($before.control_plane_poll_ready -and $before.task_state -eq "Running") {
        $before | ConvertTo-Json -Depth 4
        exit 0
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $process = Get-VerifiedTunnelProcess
    if ($null -ne $process) {
        Stop-Process -Id $process.Id
        Wait-Process -Id $process.Id -Timeout 20 -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
}

Start-ScheduledTask -TaskName $TaskName
$after = Wait-TunnelReady
$after | ConvertTo-Json -Depth 4
exit $(if ($after.control_plane_poll_ready) { 0 } else { 1 })
