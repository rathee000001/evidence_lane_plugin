[CmdletBinding()]
param(
    [string]$TunnelClientSource = "",
    [string]$TunnelClientDownloadUri = "$env:EVIDENCE_LANE_TUNNEL_CLIENT_DOWNLOAD_URI",
    [string]$TunnelId = "",
    [string]$PluginRoot = "",
    [string]$DataRoot = "$env:USERPROFILE\EvidenceLanePV",
    [ValidateSet(
        "main-git-release",
        "branch-commit-recovery",
        "mutable-local-testing"
    )]
    [string]$SlotRole = "main-git-release",
    [string]$RuntimeRoot = "",
    [string]$ProfileName = "",
    [string]$TaskName = "",
    [string]$RuntimeKeyEnvelopeSource = "",
    [ValidateSet("Auto", "Persistent", "Ephemeral")]
    [string]$HostLifetime = "Auto",
    [string]$VmInstanceId = "$env:EVIDENCE_LANE_VM_INSTANCE_ID",
    [ValidateSet("CODEX_APP_INTERACTIVE", "HEADLESS_API", "DIRECT_CLI_API")]
    [string]$InteractionProfile = "CODEX_APP_INTERACTIVE",
    [ValidateSet("UNSPECIFIED", "PRO", "PLUS", "BUSINESS", "EDU", "ENTERPRISE")]
    [string]$AccountTier = "UNSPECIFIED",
    [switch]$RotateRuntimeKey,
    [switch]$Activate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$releaseChannelPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\codex-release-channel.json"))
if (-not (Test-Path -LiteralPath $releaseChannelPath -PathType Leaf)) {
    throw "The exact Evidence Lane release-channel contract is missing."
}
$releaseChannel = Get-Content -LiteralPath $releaseChannelPath -Raw | ConvertFrom-Json
$slotContractKey = @{
    "main-git-release" = "stable"
    "branch-commit-recovery" = "branch_recovery"
    "mutable-local-testing" = "local_testing"
}[$SlotRole]
$slotContract = $releaseChannel.$slotContractKey
$slotRelease = if ($null -ne $slotContract.PSObject.Properties["release"]) {
    [string]$slotContract.release
} else {
    [string]$slotContract.release_line
}
if (
    $releaseChannel.schema -ne "evidence-lane.codex-release-channel.v2" -or
    [string]$slotContract.slot_role -ne $SlotRole -or
    $slotRelease -notmatch '^\d+\.\d+\.\d+$'
) {
    throw "The requested tunnel slot does not match the exact release-channel contract."
}
$release = $slotRelease
$releaseToken = "v" + ($release -replace '\.', '')
$filePrefix = "evidence_lane_${releaseToken}"
$slotToken = $SlotRole.Replace("-", "_")
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $env:USERPROFILE "EvidenceLanePV\tunnel-runtime-$releaseToken-stable-build"
}
if ([string]::IsNullOrWhiteSpace($ProfileName)) {
    $ProfileName = "${filePrefix}_stable_build_transport"
}
if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = "EvidenceLane-Tunnel-$releaseToken-stable-build"
}

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$stableClient = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$secretRoot = Join-Path $RuntimeRoot "secrets"
$secretFile = Join-Path $secretRoot "control-plane-runtime-key.dpapi"
$bootTarget = Join-Path $RuntimeRoot "EvidenceLaneTunnel.Boot.ps1"
$manageTarget = Join-Path $RuntimeRoot "Manage-EvidenceLaneTunnel.ps1"
$hostTarget = Join-Path $RuntimeRoot "EvidenceLaneTunnelHost.exe"
$childTarget = Join-Path $RuntimeRoot "_INTERNAL_EVIDENCE_LANE_MCP_LAYER_DO_NOT_RUN.ps1"
$markerFile = Join-Path $RuntimeRoot "evidence-lane-tunnel-installation.json"
$sourceBoot = Join-Path $PSScriptRoot "EvidenceLaneTunnel.Boot.ps1"
$sourceManage = Join-Path $PSScriptRoot "Manage-EvidenceLaneTunnel.ps1"
$sourceHost = Join-Path $PSScriptRoot "EvidenceLaneTunnelHost.exe"
$profileDir = Join-Path $env:APPDATA "tunnel-client"
$profileFile = Join-Path $profileDir ($ProfileName + ".yaml")
$runtimeKeyEnvelopeReused = $false
$tunnelIdReused = $false
$dependencyAcquisition = "EXISTING_VERIFIED_CLIENT"
if ($InteractionProfile -in @("HEADLESS_API", "DIRECT_CLI_API")) {
    [ordered]@{
        status = "PASS"
        release = $release
        interaction_profile = $InteractionProfile
        account_tier = $AccountTier
        tunnel_requirement = "NOT_REQUIRED_FOR_API_LAYER"
        tunnel_installed = $false
        local_pv_storage_allowed_when_durable = $true
        account_tier_affects_routing = $false
        api_billing_affects_routing = $false
        runtime_key_requested = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}

$exactHostLifetime = if ($HostLifetime -ne "Auto") {
    $HostLifetime
}
elseif ($env:EVIDENCE_LANE_VM_LIFETIME -eq "EPHEMERAL") {
    "Ephemeral"
}
else {
    "Persistent"
}

function Get-StringSha256 {
    param([Parameter(Mandatory = $true)][string]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha.Dispose()
    }
}

$exactVmInstanceId = $VmInstanceId.Trim()
$vmInstanceIdSha256 = "NOT_APPLICABLE"
if ($exactHostLifetime -eq "Ephemeral") {
    if ([string]::IsNullOrWhiteSpace($exactVmInstanceId)) {
        throw "Ephemeral interactive setup requires -VmInstanceId or EVIDENCE_LANE_VM_INSTANCE_ID."
    }
    if (-not [string]::IsNullOrWhiteSpace($RuntimeKeyEnvelopeSource)) {
        throw "An ephemeral VM cannot import a Runtime key envelope from durable storage."
    }
    $vmInstanceIdSha256 = Get-StringSha256 -Value $exactVmInstanceId
    if (Test-Path -LiteralPath $markerFile -PathType Leaf) {
        $existingVmMarker = Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
        if ([string]$existingVmMarker.vm_instance_id_sha256 -ne $vmInstanceIdSha256) {
            throw "This ephemeral RuntimeRoot belongs to another VM instance; use a fresh VM-local RuntimeRoot."
        }
    }
    elseif (Test-Path -LiteralPath $secretFile -PathType Leaf) {
        throw "An unbound encrypted key exists in the ephemeral RuntimeRoot; use a fresh VM-local RuntimeRoot."
    }
}

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
    $priorClients = @(
        Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($DataRoot)) `
            -Directory -Filter "tunnel-runtime-*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                Join-Path $_.FullName "bin\tunnel-client-v0.0.10.exe"
            } |
            Where-Object {
                (Test-Path -LiteralPath $_ -PathType Leaf) -and
                (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash -eq $expectedClientSha256
            }
    )
    if ($priorClients.Count -gt 0) {
        return [string]$priorClients[0]
    }
    if (-not [string]::IsNullOrWhiteSpace($TunnelClientDownloadUri)) {
        $downloadUri = [Uri]$TunnelClientDownloadUri
        if (
            $downloadUri.Scheme -ne "https" -or
            -not [string]::IsNullOrWhiteSpace($downloadUri.UserInfo)
        ) {
            throw "The tunnel-client dependency URI must be credential-free HTTPS."
        }
        $dependencyRoot = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "dependency-cache"
        New-Item -ItemType Directory -Path $dependencyRoot -Force | Out-Null
        $downloadTarget = Join-Path $dependencyRoot "tunnel-client-v0.0.10.exe"
        Invoke-WebRequest -Uri $downloadUri -OutFile $downloadTarget -UseBasicParsing
        if ((Get-FileHash -LiteralPath $downloadTarget -Algorithm SHA256).Hash -ne $expectedClientSha256) {
            Remove-Item -LiteralPath $downloadTarget -Force -ErrorAction SilentlyContinue
            throw "The downloaded tunnel-client does not match the pinned v0.0.10 SHA-256."
        }
        $script:dependencyAcquisition = "DOWNLOADED_FROM_CONFIGURED_HTTPS_AND_HASH_VERIFIED"
        return $downloadTarget
    }
    throw "The pinned tunnel-client dependency is missing. Configure EVIDENCE_LANE_TUNNEL_CLIENT_DOWNLOAD_URI or provide -TunnelClientSource; no unverified binary will be installed."
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
        throw "Python 3.11 or newer is required before installing the versioned secure MCP transport."
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
    if (
        [string]::IsNullOrWhiteSpace($value) -and
        $exactHostLifetime -ne "Ephemeral"
    ) {
        $value = [string]$env:EVIDENCE_LANE_TUNNEL_ID
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        $priorMarkers = @(
            Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($DataRoot)) `
                -Directory -Filter "tunnel-runtime-*" -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName "evidence-lane-tunnel-installation.json" } |
                Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
        )
        foreach ($priorMarkerPath in $priorMarkers) {
            try {
                $priorMarker = Get-Content -LiteralPath $priorMarkerPath -Raw | ConvertFrom-Json
                $priorId = [string]$priorMarker.tunnel_id
                if ($priorId -match '^tunnel_[A-Za-z0-9]+$') {
                    $value = $priorId
                    $script:tunnelIdReused = $true
                    break
                }
            }
            catch {
                continue
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = (Read-Host "Tunnel ID from the OpenAI Platform tunnel page").Trim()
    }
    if ($value -notmatch '^tunnel_[A-Za-z0-9]+$') {
        throw "The Tunnel ID must use the exact tunnel_<identifier> format."
    }
    return $value
}

function Resolve-PriorRuntimeKeyEnvelope {
    if ($exactHostLifetime -eq "Ephemeral") {
        return ""
    }
    if (-not [string]::IsNullOrWhiteSpace($RuntimeKeyEnvelopeSource)) {
        return $RuntimeKeyEnvelopeSource
    }
    $priorEnvelopes = @(
        Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($DataRoot)) `
            -Directory -Filter "tunnel-runtime-*" -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "secrets\control-plane-runtime-key.dpapi" } |
            Where-Object {
                (Test-Path -LiteralPath $_ -PathType Leaf) -and
                ([IO.Path]::GetFullPath($_) -ne [IO.Path]::GetFullPath($secretFile))
            }
    )
    if ($priorEnvelopes.Count -gt 0) {
        return [string]$priorEnvelopes[0]
    }
    return ""
}

function Write-LayeredChildLauncher {
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
`$env:EVIDENCE_LANE_MCP_EXPOSURE_PROFILE = "CODEX_INTERACTIVE_SUPPORT"
`$env:EVIDENCE_LANE_PUBLIC_SITE_URL = "https://evidencelane.org"
`$env:EVIDENCE_LANE_DATA_ROOT = '$escapedDataRoot'
& '$escapedPython' '$escapedRunner' --transport stdio
exit `$LASTEXITCODE
"@
    Set-Content -LiteralPath $childTarget -Value $launcher -Encoding UTF8
}

function Assert-NoOtherActiveTunnel {
    param(
        [Parameter(Mandatory = $true)][string]$ExactDataRoot,
        [Parameter(Mandatory = $true)][string]$ExactRuntimeRoot
    )

    $otherRuntimeRoots = @(
        Get-ChildItem -LiteralPath $ExactDataRoot `
            -Directory -Filter "tunnel-runtime-*" -ErrorAction SilentlyContinue |
            Where-Object {
                [IO.Path]::GetFullPath($_.FullName) -ne $ExactRuntimeRoot
            }
    )
    foreach ($otherRoot in $otherRuntimeRoots) {
        $otherMarkerPath = Join-Path $otherRoot.FullName "evidence-lane-tunnel-installation.json"
        $otherManager = Join-Path $otherRoot.FullName "Manage-EvidenceLaneTunnel.ps1"
        if (
            -not (Test-Path -LiteralPath $otherMarkerPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $otherManager -PathType Leaf)
        ) {
            continue
        }
        try {
            $otherMarker = Get-Content -LiteralPath $otherMarkerPath -Raw | ConvertFrom-Json
            $managerCommand = Get-Command -Name $otherManager -CommandType ExternalScript -ErrorAction Stop
            $statusArguments = @(
                "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", $otherManager,
                "-Action", "Status", "-RuntimeRoot", $otherRoot.FullName
            )
            foreach ($optionalParameter in @("ProfileName", "TaskName", "ReleaseToken")) {
                if (-not $managerCommand.Parameters.ContainsKey($optionalParameter)) {
                    continue
                }
                $markerProperty = switch ($optionalParameter) {
                    "ProfileName" { "profile_name" }
                    "TaskName" { "task_name" }
                    default { "release_token" }
                }
                $markerValue = [string]$otherMarker.$markerProperty
                if (-not [string]::IsNullOrWhiteSpace($markerValue)) {
                    $statusArguments += @("-$optionalParameter", $markerValue)
                }
            }
            $statusText = & $powershell @statusArguments 2>$null
            $status = ($statusText | Out-String).Trim() | ConvertFrom-Json
            if (
                $status.control_plane_poll_ready -eq $true -or
                $status.process_running -eq $true
            ) {
                throw "Another Evidence Lane tunnel is active. Stop it through the sealed slot operator before activating this slot."
            }
        }
        catch {
            if ($_.Exception.Message -like "Another Evidence Lane tunnel is active.*") {
                throw
            }
            throw "A sibling Evidence Lane tunnel could not be proven stopped; activation is blocked."
        }
    }
}

function Disable-StoppedPriorTunnelTasks {
    param([Parameter(Mandatory = $true)][string]$ExactTaskName)

    foreach ($priorTask in @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        [string]$_.TaskName -like "EvidenceLane-Tunnel-*" -and
        [string]$_.TaskName -ne $ExactTaskName
    })) {
        if ([string]$priorTask.State -eq "Running") {
            throw "A prior Evidence Lane tunnel task is still running; use its sealed manager before activating this release."
        }
        Disable-ScheduledTask -TaskName ([string]$priorTask.TaskName) -ErrorAction Stop | Out-Null
    }
}

$exactPluginRoot = Resolve-PluginRoot
$runner = Join-Path $exactPluginRoot "scripts\run_mcp.py"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "The exact Evidence Lane MCP launcher is missing: $runner"
}
if (-not (Test-Path -LiteralPath $sourceHost -PathType Leaf)) {
    throw "The no-visible-console Evidence Lane tunnel host is missing: $sourceHost"
}
$python = Resolve-PythonCommand -ExactPluginRoot $exactPluginRoot
$exactDataRoot = [IO.Path]::GetFullPath($DataRoot)
if (Test-Path -LiteralPath $exactDataRoot -PathType Leaf) {
    throw "The configured Evidence Lane data root is a file, not a durable directory."
}
New-Item -ItemType Directory -Path $exactDataRoot -Force | Out-Null
$resolvedSource = Resolve-TunnelClientSource
$sourceHash = (Get-FileHash -LiteralPath $resolvedSource -Algorithm SHA256).Hash
if ($sourceHash -ne $expectedClientSha256) {
    throw "The supplied tunnel-client binary does not match the pinned v0.0.10 SHA-256."
}
$exactTunnelId = Resolve-TunnelId

New-Item -ItemType Directory -Path (Split-Path -Parent $stableClient) -Force | Out-Null
New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
Copy-Item -LiteralPath $resolvedSource -Destination $stableClient -Force
Copy-Item -LiteralPath $sourceBoot -Destination $bootTarget -Force
Copy-Item -LiteralPath $sourceManage -Destination $manageTarget -Force
Copy-Item -LiteralPath $sourceHost -Destination $hostTarget -Force
Protect-SecretDirectory

if ($RotateRuntimeKey -or -not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
    $effectiveEnvelopeSource = Resolve-PriorRuntimeKeyEnvelope
    if (-not [string]::IsNullOrWhiteSpace($effectiveEnvelopeSource) -and -not $RotateRuntimeKey) {
        $exactEnvelopeSource = [IO.Path]::GetFullPath($effectiveEnvelopeSource)
        $approvedEnvelopeParent = $exactDataRoot + [IO.Path]::DirectorySeparatorChar
        if (
            -not $exactEnvelopeSource.StartsWith(
                $approvedEnvelopeParent,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            [IO.Path]::GetFileName($exactEnvelopeSource) -ne "control-plane-runtime-key.dpapi" -or
            -not (Test-Path -LiteralPath $exactEnvelopeSource -PathType Leaf)
        ) {
            throw "The reusable DPAPI envelope must be an existing saved Evidence Lane tunnel envelope."
        }
        Copy-Item -LiteralPath $exactEnvelopeSource -Destination $secretFile -Force
        $runtimeKeyEnvelopeReused = $true
    }
    else {
        Save-RuntimeKeyEnvelope
    }
}

Write-LayeredChildLauncher -Python $python -Runner $runner -ExactDataRoot $exactDataRoot
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$powershellForCommand = $powershell.Replace('\', '/')
$childForCommand = $childTarget.Replace('\', '/')
# tunnel-client parses this value as a portable command line. Raw Windows
# backslashes are escape characters there, so always supply normalized absolute
# paths and quote them for user profiles that contain spaces.
$mcpCommand = '"' + $powershellForCommand + '" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $childForCommand + '"'
& $stableClient init `
    --profile-dir $profileDir `
    --profile $ProfileName `
    --tunnel-id $exactTunnelId `
    --control-plane-api-key-ref "env:CONTROL_PLANE_API_KEY" `
    --mcp-command $mcpCommand `
    --health-listen-addr "127.0.0.1:0" `
    --force *> $null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $profileFile -PathType Leaf)) {
    throw "The exact versioned secure MCP transport profile could not be created."
}

$marker = [ordered]@{
    schema = "evidence-lane.versioned-secure-mcp-tunnel-installation.v1"
    release = $release
    release_token = $releaseToken
    release_identity_source = "CODEX_RELEASE_CHANNEL_CONTRACT"
    runtime_identity_matches_release = $true
    runtime_root = [IO.Path]::GetFullPath($RuntimeRoot)
    profile_name = $ProfileName
    profile_file = $profileFile
    task_name = $TaskName
    scheduled_task_launcher = $hostTarget
    scheduled_task_launcher_sha256 = (Get-FileHash -LiteralPath $hostTarget -Algorithm SHA256).Hash
    scheduled_task_launcher_subsystem = "WINDOWS_GUI_NO_VISIBLE_CONSOLE"
    scheduled_task_launcher_create_no_window = $true
    slot_role = $SlotRole
    byte_frozen = $SlotRole -eq "branch-commit-recovery"
    exposure_profile = "CODEX_INTERACTIVE_SUPPORT"
    transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"
    served_exposure_layer = "CODEX_INTERACTIVE_SUPPORT"
    chatgpt_is_layer_not_transport_identity = $true
    codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"
    codex_tunnel_lifecycle_proof_allowed = $false
    plugin_root = $exactPluginRoot
    data_root = $exactDataRoot
    project_binding = "NONE_TRANSPORT_ONLY"
    project_route_argument = "project_id"
    project_route_argument_required = $true
    cross_project_fallback_allowed = $false
    tunnel_id = $exactTunnelId
    stable_client = $stableClient
    stable_client_sha256 = $expectedClientSha256
    pid_file = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_tunnel.pid"
    health_url_file = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_health.url"
    live_slot_authority = "SEALED_POST_PV11_TWO_SLOT_REGISTRY"
    legacy_version_manager_authoritative = $false
    saved_version = $true
    reusable_without_reinstall = $true
    runtime_key_envelope_reused = $runtimeKeyEnvelopeReused
    tunnel_id_reused = $tunnelIdReused
    interaction_profile = $InteractionProfile
    account_tier = $AccountTier
    account_tier_affects_routing = $false
    api_billing_affects_routing = $false
    host_lifetime = $exactHostLifetime.ToUpperInvariant()
    vm_instance_id_sha256 = $vmInstanceIdSha256
    raw_vm_instance_id_stored = $false
    tunnel_setup_frequency = if ($exactHostLifetime -eq "Ephemeral") { "ONCE_PER_EPHEMERAL_VM_INSTANCE" } else { "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE" }
    tunnel_key_retention = if ($exactHostLifetime -eq "Ephemeral") { "CURRENT_VM_LIFETIME_ONLY" } else { "CURRENT_WINDOWS_USER_DPAPI_PROFILE" }
    tunnel_runtime_lifetime = if ($exactHostLifetime -eq "Ephemeral") { "CURRENT_VM_LIFETIME_ONLY" } else { "WINDOWS_LOGON_MANAGED_PERSISTENT_HOST" }
    dependency_acquisition = $dependencyAcquisition
    runtime_key_plaintext_written = $false
    windows_console_policy = "WINDOWS_GUI_HOST_CREATE_NO_WINDOW"
    scheduled_task_window_style = "HIDDEN"
    distribution_audience = if ($SlotRole -eq "branch-commit-recovery") { "MAINTAINER_RECOVERY_ONLY" } else { "USER_OR_MAINTAINER_ACTIVE_2_2_RUNTIME" }
    prior_versioned_runtimes_retained = $true
    prior_versioned_tasks_retained = $true
    prior_versioned_runtime_deletion_allowed = $false
    one_active_version_required = $true
}
$marker | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $markerFile -Encoding UTF8

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '"' + $RuntimeRoot + '" "' + $ProfileName + '" "' + $profileDir + '" "' + $releaseToken + '"'
$action = New-ScheduledTaskAction -Execute $hostTarget -Argument $arguments
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
    -Description "Pinned Evidence Lane $release $SlotRole secure MCP tunnel; automatic only while this exact slot is enabled." `
    -Force | Out-Null

Disable-ScheduledTask -TaskName $TaskName | Out-Null
if ($Activate) {
    Assert-NoOtherActiveTunnel `
        -ExactDataRoot $exactDataRoot `
        -ExactRuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot))
    Disable-StoppedPriorTunnelTasks -ExactTaskName $TaskName
    & $manageTarget `
        -Action Start `
        -RuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot)) `
        -ProfileName $ProfileName `
        -ProfileDir $profileDir `
        -ReleaseToken $releaseToken `
        -TaskName $TaskName *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "The version-matched Evidence Lane tunnel did not reach readiness."
    }
}

[ordered]@{
    status = "PASS"
    release = $release
    release_token = $releaseToken
    release_identity_source = "CODEX_RELEASE_CHANNEL_CONTRACT"
    runtime_identity_matches_release = $true
    slot_role = $SlotRole
    byte_frozen = $SlotRole -eq "branch-commit-recovery"
    task_name = $TaskName
    trigger = "AT_LOGON"
    current_user_dpapi = $true
    stable_client = $stableClient
    stable_client_sha256 = (Get-FileHash -LiteralPath $stableClient -Algorithm SHA256).Hash
    profile = $ProfileName
    exposure_profile = "CODEX_INTERACTIVE_SUPPORT"
    transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"
    served_exposure_layer = "CODEX_INTERACTIVE_SUPPORT"
    chatgpt_is_layer_not_transport_identity = $true
    codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"
    codex_tunnel_lifecycle_proof_allowed = $false
    data_root = $exactDataRoot
    project_binding = "NONE_TRANSPORT_ONLY"
    project_route_argument = "project_id"
    project_route_argument_required = $true
    cross_project_fallback_allowed = $false
    exact_visible_tool_count = 83
    exact_active_read_tool_count = 26
    exact_fail_closed_write_tool_count = 57
    tunnel_id_recorded = $true
    runtime_key_plaintext_written = $false
    windows_console_policy = "PERSISTENT_OR_HIDDEN_NO_TRANSIENT_CONSOLE"
    scheduled_task_window_style = "HIDDEN"
    distribution_audience = if ($SlotRole -eq "branch-commit-recovery") { "MAINTAINER_RECOVERY_ONLY" } else { "USER_OR_MAINTAINER_ACTIVE_2_2_RUNTIME" }
    prior_versioned_runtimes_retained = $true
    prior_versioned_tasks_retained = $true
    prior_versioned_runtime_deletion_allowed = $false
    one_active_version_required = $true
    runtime_key_envelope_reused = $runtimeKeyEnvelopeReused
    tunnel_id_reused = $tunnelIdReused
    dependency_acquisition = $dependencyAcquisition
    interaction_profile = $InteractionProfile
    account_tier = $AccountTier
    account_tier_affects_routing = $false
    api_billing_affects_routing = $false
    host_lifetime = $exactHostLifetime.ToUpperInvariant()
    vm_instance_id_sha256 = $vmInstanceIdSha256
    raw_vm_instance_id_stored = $false
    tunnel_setup_frequency = if ($exactHostLifetime -eq "Ephemeral") { "ONCE_PER_EPHEMERAL_VM_INSTANCE" } else { "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE" }
    tunnel_key_retention = if ($exactHostLifetime -eq "Ephemeral") { "CURRENT_VM_LIFETIME_ONLY" } else { "CURRENT_WINDOWS_USER_DPAPI_PROFILE" }
    tunnel_runtime_lifetime = if ($exactHostLifetime -eq "Ephemeral") { "CURRENT_VM_LIFETIME_ONLY" } else { "WINDOWS_LOGON_MANAGED_PERSISTENT_HOST" }
    codex_platform_tunnel_setup_required_once = $true
    saved_slot = $true
    reusable_without_reinstall = $true
    three_slot_registry_authority = "SEALED_MAIN_BRANCH_RECOVERY_LOCAL_TESTING_REGISTRY"
    legacy_version_manager_authoritative = $false
    registry_materialization_gate = "EXACT_STANDALONE_APPROVE_PLUS_NATIVE_FUSE_ACCEPTING_PV11"
    branch_commit_recovery_preserved = $true
    registered_slot = $SlotRole
    pre_2_2_fallback_allowed = $false
    branch_recovery_is_selected = $SlotRole -eq "branch-commit-recovery"
    failover_requires_sealed_two_slot_operator = $true
    activated = [bool]$Activate
    started = [bool]$Activate
} | ConvertTo-Json -Depth 4
