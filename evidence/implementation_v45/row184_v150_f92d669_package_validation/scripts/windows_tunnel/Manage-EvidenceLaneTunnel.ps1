[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Status", "Repair", "Remove")]
    [string]$Action,
    [string]$RuntimeRoot = "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v140",
    [string]$ProfileName = "evidence_lane_v140_chatgpt_read",
    [string]$ProfileDir = "$env:APPDATA\tunnel-client",
    [string]$TaskName = "EvidenceLane-Tunnel-v140",
    [int]$ReadyTimeoutSeconds = 90,
    [switch]$ConfirmRemoval
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$client = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$pidFile = Join-Path $RuntimeRoot "evidence_lane_v140_tunnel.pid"
$healthUrlFile = Join-Path $RuntimeRoot "evidence_lane_v140_health.url"
$profileFile = Join-Path $ProfileDir ($ProfileName + ".yaml")
$markerFile = Join-Path $RuntimeRoot "evidence-lane-tunnel-installation.json"

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
        release = "1.5.0"
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
        chatgpt_governed_profile_configured = $profileText.Contains("_INTERNAL_CHATGPT_READ_MCP_DO_NOT_RUN.ps1")
        exposure_profile = "CHATGPT_PRO_GOVERNED"
        data_root = if ($null -ne $marker) { [string]$marker.data_root } else { $null }
        project_binding = "NONE_TRANSPORT_ONLY"
        project_route_argument = "project_id"
        project_route_argument_required = $true
        cross_project_fallback_allowed = $false
        exact_visible_tool_count = 62
        exact_active_read_tool_count = 21
        exact_fail_closed_write_tool_count = 41
        health_url_file = $healthUrlFile
        runtime_key_plaintext_reported = $false
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

if ($Action -eq "Remove") {
    if (-not $ConfirmRemoval) {
        throw "Removal is fail-closed. Repeat with -ConfirmRemoval after reviewing the exact runtime root."
    }
    $approvedParent = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "EvidenceLanePV")) + [IO.Path]::DirectorySeparatorChar
    $exactRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
    if (-not $exactRuntimeRoot.StartsWith($approvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a runtime outside the user-owned EvidenceLanePV directory."
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
