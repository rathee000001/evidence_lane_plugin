[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Status", "Repair")]
    [string]$Action,
    [string]$RuntimeRoot = "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime",
    [string]$TaskName = "EvidenceLane-Tunnel-v130",
    [int]$ReadyTimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$client = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$pidFile = Join-Path $RuntimeRoot "evidence_lane_v130_tunnel.pid"
$healthUrlFile = Join-Path $RuntimeRoot "evidence_lane_v130_health.url"

function Get-TunnelStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = if ($null -ne $task) {
        Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $parsedPid = 0
    $process = $null
    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        $rawPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
        if ([int]::TryParse($rawPid, [ref]$parsedPid)) {
            $process = Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
        }
    }
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
    return [ordered]@{
        status = if ($ready) { "PASS" } else { "BLOCKED" }
        task_name = $TaskName
        task_registered = $null -ne $task
        task_state = if ($null -ne $task) { [string]$task.State } else { $null }
        task_last_result = if ($null -ne $taskInfo) { $taskInfo.LastTaskResult } else { $null }
        pid = if ($null -ne $process) { $parsedPid } else { $null }
        process_running = $null -ne $process
        stable_binary_hash_valid = $binaryHashValid
        control_plane_poll_ready = $ready
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
    if ($before.process_running -and $before.stable_binary_hash_valid) {
        Stop-Process -Id $before.pid
        Wait-Process -Id $before.pid -Timeout 20 -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
}

Start-ScheduledTask -TaskName $TaskName
$after = Wait-TunnelReady
$after | ConvertTo-Json -Depth 4
exit $(if ($after.control_plane_poll_ready) { 0 } else { 1 })

