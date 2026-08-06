[CmdletBinding()]
param(
    [string]$RuntimeRoot = "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime",
    [string]$ProfileName = "evidence_lane_v120_hil"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$client = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$secretFile = Join-Path $RuntimeRoot "secrets\control-plane-runtime-key.dpapi"
$healthUrlFile = Join-Path $RuntimeRoot "evidence_lane_v130_health.url"
$pidFile = Join-Path $RuntimeRoot "evidence_lane_v130_tunnel.pid"
$daemonLog = Join-Path $RuntimeRoot "evidence_lane_v130_tunnel.log"
$operatorLog = Join-Path $RuntimeRoot "evidence_lane_v130_operator.log"

function Write-OperatorEvent {
    param(
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Detail = ""
    )

    $row = [ordered]@{
        timestamp = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        status = $Status
        detail = $Detail
    }
    Add-Content -LiteralPath $operatorLog -Value ($row | ConvertTo-Json -Compress) -Encoding UTF8
}

function Get-PinnedTunnelProcess {
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
        return $null
    }
    $rawPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $parsedPid = 0
    if (-not [int]::TryParse($rawPid, [ref]$parsedPid)) {
        return $null
    }
    $process = Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.ProcessName -ne "tunnel-client") {
        return $null
    }
    try {
        $processPath = (Resolve-Path -LiteralPath $process.Path).Path
        $clientPath = (Resolve-Path -LiteralPath $client).Path
        if ($processPath -ne $clientPath) {
            return $null
        }
    }
    catch {
        return $null
    }
    return $process
}

function Test-TunnelReady {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    if (-not (Test-Path -LiteralPath $healthUrlFile -PathType Leaf)) {
        return $false
    }
    & $client health `
        --url-file $healthUrlFile `
        --pid $ProcessId `
        --require-control-plane-poll `
        --json *> $null
    return $LASTEXITCODE -eq 0
}

New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null

if (-not (Test-Path -LiteralPath $client -PathType Leaf)) {
    throw "The stable pinned tunnel-client binary is missing: $client"
}
$actualClientSha256 = (Get-FileHash -LiteralPath $client -Algorithm SHA256).Hash
if ($actualClientSha256 -ne $expectedClientSha256) {
    throw "The stable tunnel-client binary hash does not match the pinned v0.0.10 authority."
}
if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
    throw "The DPAPI runtime-key envelope is missing. Run Install-EvidenceLaneTunnel.ps1 once."
}

$existing = Get-PinnedTunnelProcess
if ($null -ne $existing -and (Test-TunnelReady -ProcessId $existing.Id)) {
    Write-OperatorEvent -Event "BOOT" -Status "ALREADY_READY" -Detail "pid=$($existing.Id)"
    exit 0
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue

$secureKey = $null
$keyPointer = [IntPtr]::Zero
try {
    $encryptedKey = (Get-Content -LiteralPath $secretFile -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($encryptedKey)) {
        throw "The DPAPI runtime-key envelope is empty."
    }
    $secureKey = ConvertTo-SecureString $encryptedKey
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    $env:CONTROL_PLANE_ORGANIZATION_ID = "org-xdT1xXEp2clCPaJHQ1foPC5B"

    Write-OperatorEvent -Event "DOCTOR" -Status "STARTED" -Detail "profile=$ProfileName"
    & $client doctor --profile $ProfileName --json --explain *> $operatorLog
    if ($LASTEXITCODE -ne 0) {
        throw "Tunnel doctor failed with exit code $LASTEXITCODE."
    }
    Write-OperatorEvent -Event "DOCTOR" -Status "PASS" -Detail "profile=$ProfileName"

    Write-OperatorEvent -Event "DAEMON" -Status "STARTED" -Detail "profile=$ProfileName"
    & $client run `
        --profile $ProfileName `
        --health.url-file $healthUrlFile `
        --pid.file $pidFile `
        --log.file $daemonLog
    $daemonExitCode = $LASTEXITCODE
    Write-OperatorEvent -Event "DAEMON" -Status "EXITED" -Detail "exit_code=$daemonExitCode"
    if ($daemonExitCode -ne 0) {
        throw "Tunnel daemon exited with code $daemonExitCode."
    }
}
catch {
    Write-OperatorEvent -Event "BOOT" -Status "FAIL" -Detail $_.Exception.Message
    throw
}
finally {
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:CONTROL_PLANE_ORGANIZATION_ID -ErrorAction SilentlyContinue
    if ($null -ne $secureKey) {
        $secureKey.Dispose()
    }
}

