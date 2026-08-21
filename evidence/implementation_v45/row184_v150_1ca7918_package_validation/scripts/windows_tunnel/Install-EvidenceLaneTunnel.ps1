[CmdletBinding()]
param(
    [string]$TunnelClientSource = "",
    [string]$TunnelId = "",
    [string]$PluginRoot = "",
    [string]$DataRoot = "$env:USERPROFILE\EvidenceLanePV",
    [string]$RuntimeRoot = "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime-v140",
    [string]$ProfileName = "evidence_lane_v140_chatgpt_read",
    [string]$TaskName = "EvidenceLane-Tunnel-v140",
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
$childTarget = Join-Path $RuntimeRoot "_INTERNAL_CHATGPT_READ_MCP_DO_NOT_RUN.ps1"
$markerFile = Join-Path $RuntimeRoot "evidence-lane-tunnel-installation.json"
$sourceBoot = Join-Path $PSScriptRoot "EvidenceLaneTunnel.Boot.ps1"
$sourceManage = Join-Path $PSScriptRoot "Manage-EvidenceLaneTunnel.ps1"
$profileDir = Join-Path $env:APPDATA "tunnel-client"
$profileFile = Join-Path $profileDir ($ProfileName + ".yaml")
$legacyPidFile = Join-Path "$env:USERPROFILE\EvidenceLanePV\tunnel-runtime" "evidence_lane_v130_tunnel.pid"

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

function Resolve-PluginRoot {
    if (-not [string]::IsNullOrWhiteSpace($PluginRoot)) {
        return (Resolve-Path -LiteralPath $PluginRoot).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Resolve-PythonCommand {
    param([Parameter(Mandatory = $true)][string]$ExactPluginRoot)

    $privatePython = Join-Path $ExactPluginRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $privatePython -PathType Leaf) {
        return $privatePython
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Python 3.11 or newer is required before installing the ChatGPT read tunnel."
    }
    return $command.Source
}

function Protect-SecretDirectory {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    # Preserve the existing owner and security descriptor. Constructing a blank
    # DirectorySecurity object makes Set-Acl attempt privileged owner/SACL work
    # and fails for a normal desktop user with SeSecurityPrivilege missing.
    $acl = Get-Acl -LiteralPath $secretRoot
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $identity,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit",
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $acl.SetAccessRule($rule)
    try {
        Set-Acl -LiteralPath $secretRoot -AclObject $acl
    }
    catch [System.Security.AccessControl.PrivilegeNotHeldException] {
        # icacls changes only this directory's DACL and does not request SACL or
        # owner privileges. Never print the encrypted envelope or its contents.
        & icacls.exe $secretRoot /inheritance:r /grant:r "${identity}:(OI)(CI)F" *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "The tunnel secret directory ACL could not be hardened without elevation."
        }
    }
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

function Resolve-TunnelId {
    $value = $TunnelId.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = (Read-Host "Tunnel ID from the OpenAI Platform tunnel page").Trim()
    }
    if ($value -notmatch '^tunnel_[A-Za-z0-9]+$') {
        throw "The Tunnel ID must use the exact tunnel_<identifier> format."
    }
    return $value
}

function Write-GovernedChildLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Runner,
        [Parameter(Mandatory = $true)][string]$ExactDataRoot
    )

    $escapedPython = $Python.Replace("'", "''")
    $escapedRunner = $Runner.Replace("'", "''")
    $escapedDataRoot = $ExactDataRoot.Replace("'", "''")
    $launcher = @"
`$ErrorActionPreference = "Stop"
`$env:EVIDENCE_LANE_MCP_EXPOSURE_PROFILE = "CHATGPT_PRO_GOVERNED"
`$env:EVIDENCE_LANE_PUBLIC_SITE_URL = "https://evidencelane.org"
`$env:EVIDENCE_LANE_DATA_ROOT = '$escapedDataRoot'
& '$escapedPython' '$escapedRunner' --transport stdio
exit `$LASTEXITCODE
"@
    Set-Content -LiteralPath $childTarget -Value $launcher -Encoding UTF8
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
    if ($null -eq $process) {
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
$exactTunnelId = Resolve-TunnelId
$exactPluginRoot = Resolve-PluginRoot
$runner = Join-Path $exactPluginRoot "scripts\run_mcp.py"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "The exact Evidence Lane MCP launcher is missing: $runner"
}
$python = Resolve-PythonCommand -ExactPluginRoot $exactPluginRoot
$exactDataRoot = [IO.Path]::GetFullPath($DataRoot)
if (Test-Path -LiteralPath $exactDataRoot -PathType Leaf) {
    throw "The configured Evidence Lane data root is a file, not a durable directory."
}
New-Item -ItemType Directory -Path $exactDataRoot -Force | Out-Null

New-Item -ItemType Directory -Path (Split-Path -Parent $stableClient) -Force | Out-Null
New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
Copy-Item -LiteralPath $resolvedSource -Destination $stableClient -Force
Copy-Item -LiteralPath $sourceBoot -Destination $bootTarget -Force
Copy-Item -LiteralPath $sourceManage -Destination $manageTarget -Force
Protect-SecretDirectory

if ($RotateRuntimeKey -or -not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
    Save-RuntimeKeyEnvelope
}

Write-GovernedChildLauncher -Python $python -Runner $runner -ExactDataRoot $exactDataRoot
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$powershellForCommand = $powershell.Replace('\', '/')
$childForCommand = $childTarget.Replace('\', '/')
# tunnel-client parses this value as a portable command line. Raw Windows
# backslashes are escape characters there, so always supply normalized absolute
# paths and quote them for user profiles that contain spaces.
$mcpCommand = '"' + $powershellForCommand + '" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $childForCommand + '"'
& $stableClient init `
    --profile-dir $profileDir `
    --profile $ProfileName `
    --tunnel-id $exactTunnelId `
    --control-plane-api-key-ref "env:CONTROL_PLANE_API_KEY" `
    --mcp-command $mcpCommand `
    --health-listen-addr "127.0.0.1:0" `
    --force *> $null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $profileFile -PathType Leaf)) {
    throw "The exact ChatGPT read-tunnel profile could not be created."
}

$marker = [ordered]@{
    schema = "evidence-lane.chatgpt-governed-tunnel-installation.v1"
    release = "1.5.0"
    runtime_root = [IO.Path]::GetFullPath($RuntimeRoot)
    profile_name = $ProfileName
    profile_file = $profileFile
    task_name = $TaskName
    exposure_profile = "CHATGPT_PRO_GOVERNED"
    plugin_root = $exactPluginRoot
    data_root = $exactDataRoot
    project_binding = "NONE_TRANSPORT_ONLY"
    project_route_argument = "project_id"
    project_route_argument_required = $true
    cross_project_fallback_allowed = $false
    tunnel_id = $exactTunnelId
    runtime_key_plaintext_written = $false
}
$marker | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $markerFile -Encoding UTF8

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $bootTarget + '" -RuntimeRoot "' + $RuntimeRoot + '" -ProfileName "' + $ProfileName + '" -ProfileDir "' + $profileDir + '"'
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
    -Description "Pinned Evidence Lane 1.5.0 governed ChatGPT Pro tunnel; automatic after Windows user sign-in." `
    -Force | Out-Null

if ($MigrateCurrentRuntime) {
    Stop-VerifiedLegacyRuntime
}
if (-not $NoStart) {
    Start-ScheduledTask -TaskName $TaskName
}

[ordered]@{
    status = "PASS"
    release = "1.5.0"
    task_name = $TaskName
    trigger = "AT_LOGON"
    current_user_dpapi = $true
    stable_client = $stableClient
    stable_client_sha256 = (Get-FileHash -LiteralPath $stableClient -Algorithm SHA256).Hash
    profile = $ProfileName
    exposure_profile = "CHATGPT_PRO_GOVERNED"
    data_root = $exactDataRoot
    project_binding = "NONE_TRANSPORT_ONLY"
    project_route_argument = "project_id"
    project_route_argument_required = $true
    cross_project_fallback_allowed = $false
    exact_visible_tool_count = 62
    exact_active_read_tool_count = 21
    exact_fail_closed_write_tool_count = 41
    tunnel_id_recorded = $true
    runtime_key_plaintext_written = $false
    chatgpt_link_required_once = $true
    started = -not $NoStart
} | ConvertTo-Json -Depth 4
