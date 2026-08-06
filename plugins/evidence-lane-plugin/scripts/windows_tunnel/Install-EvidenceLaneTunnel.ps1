[CmdletBinding()]
param(
    [string]$TunnelClientSource = "",
    [string]$RuntimeRoot = "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime",
    [string]$ProfileName = "evidence_lane_v120_hil",
    [string]$TaskName = "EvidenceLane-Tunnel-v130",
    [switch]$RotateRuntimeKey,
    [switch]$MigrateCurrentRuntime,
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$stableClient = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$secretRoot = Join-Path $RuntimeRoot "secrets"
$secretFile = Join-Path $secretRoot "control-plane-runtime-key.dpapi"
$bootTarget = Join-Path $RuntimeRoot "EvidenceLaneTunnel.Boot.ps1"
$manageTarget = Join-Path $RuntimeRoot "Manage-EvidenceLaneTunnel.ps1"
$sourceBoot = Join-Path $PSScriptRoot "EvidenceLaneTunnel.Boot.ps1"
$sourceManage = Join-Path $PSScriptRoot "Manage-EvidenceLaneTunnel.ps1"
$legacyPidFile = Join-Path $RuntimeRoot "evidence_lane_v120_tunnel.pid"

function Resolve-TunnelClientSource {
    if (-not [string]::IsNullOrWhiteSpace($TunnelClientSource)) {
        return (Resolve-Path -LiteralPath $TunnelClientSource).Path
    }
    $command = Get-Command tunnel-client -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $historical = Join-Path $env:LOCALAPPDATA "Temp\evi-tunnel-client-v0.0.10\bin\tunnel-client.exe"
    if (Test-Path -LiteralPath $historical -PathType Leaf) {
        return $historical
    }
    throw "Provide -TunnelClientSource with the pinned v0.0.10 tunnel-client binary."
}

function Protect-SecretDirectory {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit",
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $secretRoot -AclObject $acl
}

function Save-RuntimeKeyEnvelope {
    Write-Host "Enter the OpenAI Runtime API key once. Do not enter an Admin key."
    $secureKey = Read-Host "Runtime API key" -AsSecureString
    try {
        if ($secureKey.Length -lt 20) {
            throw "The Runtime API key was empty or implausibly short."
        }
        $encrypted = ConvertFrom-SecureString $secureKey
        Set-Content -LiteralPath $secretFile -Value $encrypted -Encoding ASCII -NoNewline
    }
    finally {
        $secureKey.Dispose()
    }
}

function Stop-VerifiedLegacyRuntime {
    if (-not (Test-Path -LiteralPath $legacyPidFile -PathType Leaf)) {
        return
    }
    $rawPid = (Get-Content -LiteralPath $legacyPidFile -Raw).Trim()
    $parsedPid = 0
    if (-not [int]::TryParse($rawPid, [ref]$parsedPid)) {
        return
    }
    $process = Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.ProcessName -ne "tunnel-client") {
        return
    }
    $processHash = (Get-FileHash -LiteralPath $process.Path -Algorithm SHA256).Hash
    if ($processHash -ne $expectedClientSha256) {
        throw "Refusing to stop an unverified process from the historical PID file."
    }
    Stop-Process -Id $parsedPid
    Wait-Process -Id $parsedPid -Timeout 20 -ErrorAction SilentlyContinue
}

$resolvedSource = Resolve-TunnelClientSource
$sourceHash = (Get-FileHash -LiteralPath $resolvedSource -Algorithm SHA256).Hash
if ($sourceHash -ne $expectedClientSha256) {
    throw "The supplied tunnel-client binary does not match the pinned v0.0.10 SHA-256."
}

New-Item -ItemType Directory -Path (Split-Path -Parent $stableClient) -Force | Out-Null
New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
Copy-Item -LiteralPath $resolvedSource -Destination $stableClient -Force
Copy-Item -LiteralPath $sourceBoot -Destination $bootTarget -Force
Copy-Item -LiteralPath $sourceManage -Destination $manageTarget -Force
Protect-SecretDirectory

if ($RotateRuntimeKey -or -not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
    Save-RuntimeKeyEnvelope
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $bootTarget + '" -RuntimeRoot "' + $RuntimeRoot + '" -ProfileName "' + $ProfileName + '"'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Pinned Evidence Lane OpenAI tunnel; automatic after Windows user sign-in." `
    -Force | Out-Null

if ($MigrateCurrentRuntime) {
    Stop-VerifiedLegacyRuntime
}
if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
}

[ordered]@{
    status = "PASS"
    task_name = $TaskName
    trigger = "AT_LOGON"
    current_user_dpapi = $true
    stable_client = $stableClient
    stable_client_sha256 = (Get-FileHash -LiteralPath $stableClient -Algorithm SHA256).Hash
    profile = $ProfileName
    runtime_key_plaintext_written = $false
    started = -not $NoStart
} | ConvertTo-Json -Depth 4

