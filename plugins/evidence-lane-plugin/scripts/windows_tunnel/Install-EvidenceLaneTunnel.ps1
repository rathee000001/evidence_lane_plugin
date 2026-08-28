[CmdletBinding()]
param(
    [string]$TunnelClientSource = "",
    [string]$TunnelClientDownloadUri = "$env:EVIDENCE_LANE_TUNNEL_CLIENT_DOWNLOAD_URI",
    [string]$TunnelClientLicenseSource = "$env:EVIDENCE_LANE_TUNNEL_CLIENT_LICENSE_SOURCE",
    [string]$TunnelClientLicenseDownloadUri = "$env:EVIDENCE_LANE_TUNNEL_CLIENT_LICENSE_URI",
    [string]$ExpectedTunnelClientLicenseSha256 = "$env:EVIDENCE_LANE_TUNNEL_CLIENT_LICENSE_SHA256",
    [string]$TunnelId = "",
    [string]$PluginRoot = "",
    [string]$RuntimeControlRoot = "$env:USERPROFILE\.codex\plugins\runtime\evidence-lane-plugin",
    [ValidateSet(
        "main-git-release",
        "versioned-local-testing"
    )]
    [string]$SlotRole = "main-git-release",
    [string]$RuntimeRoot = "",
    [string]$RuntimePython = "",
    [string]$ProfileName = "",
    [string]$TaskName = "",
    [string]$RuntimeKeyEnvelopeSource = "",
    [ValidateSet("Auto", "Persistent", "Ephemeral")]
    [string]$HostLifetime = "Auto",
    [string]$VmInstanceId = "$env:EVIDENCE_LANE_VM_INSTANCE_ID",
    [ValidateSet("CODEX_APP_INTERACTIVE", "CODEX_CLI_NATIVE", "HEADLESS_API", "DIRECT_CLI_API")]
    [string]$InteractionProfile = "CODEX_APP_INTERACTIVE",
    [ValidateSet("NATIVE_MCP_AVAILABLE", "HOST_TOOL_GAP")]
    [string]$HostToolTransport = "NATIVE_MCP_AVAILABLE",
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
    "versioned-local-testing" = "local_testing"
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
    $RuntimeRoot = Join-Path $RuntimeControlRoot "tunnel-runtime-$releaseToken-stable-build"
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
        host_tool_transport = "API_DIRECT"
        tunnel_requirement = "NOT_REQUIRED_FOR_API_LAYER"
        tunnel_installed = $false
        local_pv_storage_allowed_when_durable = $true
        account_tier_affects_routing = $false
        api_billing_affects_routing = $false
        runtime_key_requested = $false
    } | ConvertTo-Json -Depth 4
    exit 0
}
if ($HostToolTransport -eq "NATIVE_MCP_AVAILABLE") {
    [ordered]@{
        status = "PASS"
        release = $release
        interaction_profile = $InteractionProfile
        account_tier = $AccountTier
        host_tool_transport = $HostToolTransport
        native_mcp_available = $true
        tunnel_requirement = "NOT_REQUIRED_NATIVE_MCP_AVAILABLE"
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
        Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($RuntimeControlRoot)) `
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
        $dependencyRoot = Join-Path ([IO.Path]::GetFullPath($RuntimeControlRoot)) "dependency-cache"
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

function Resolve-TunnelClientLicenseSource {
    if (-not [string]::IsNullOrWhiteSpace($TunnelClientLicenseSource)) {
        $resolved = (Resolve-Path -LiteralPath $TunnelClientLicenseSource).Path
    }
    elseif (-not [string]::IsNullOrWhiteSpace($TunnelClientLicenseDownloadUri)) {
        $licenseUri = [Uri]$TunnelClientLicenseDownloadUri
        if (
            $licenseUri.Scheme -ne "https" -or
            -not [string]::IsNullOrWhiteSpace($licenseUri.UserInfo)
        ) {
            throw "The tunnel-client license URI must be credential-free HTTPS."
        }
        $dependencyRoot = Join-Path $RuntimeControlRoot "dependency-downloads"
        New-Item -ItemType Directory -Path $dependencyRoot -Force | Out-Null
        $resolved = Join-Path $dependencyRoot "tunnel-client-v0.0.10.LICENSE"
        Invoke-WebRequest -Uri $licenseUri -OutFile $resolved -UseBasicParsing
    }
    else {
        throw "The pinned tunnel-client requires an exact license source or credential-free license URI."
    }
    $expected = $ExpectedTunnelClientLicenseSha256.Trim().ToUpperInvariant()
    if ($expected -notmatch '^[A-F0-9]{64}$') {
        throw "The tunnel-client license requires an exact expected SHA-256."
    }
    $actual = (Get-FileHash -LiteralPath $resolved -Algorithm SHA256).Hash
    if ($actual -ne $expected) {
        throw "The tunnel-client license text does not match its expected SHA-256."
    }
    return [pscustomobject]@{
        path = [IO.Path]::GetFullPath($resolved)
        sha256 = $actual
    }
}

function Resolve-PluginRoot {
    if (-not [string]::IsNullOrWhiteSpace($PluginRoot)) {
        return (Resolve-Path -LiteralPath $PluginRoot).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Resolve-PrewarmedRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$ExactPluginRoot,
        [Parameter(Mandatory = $true)][string]$ExactRuntimeControlRoot,
        [string]$RequestedRuntimePython
    )

    $runner = Join-Path $ExactPluginRoot "scripts\run_mcp.py"
    $bootstrapPython = $RequestedRuntimePython
    if ([string]::IsNullOrWhiteSpace($bootstrapPython)) {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            throw "The plugin bootstrap Python is unavailable; install the plugin runtime before the tunnel."
        }
        $bootstrapPython = $command.Source
    }
    $bootstrapPython = [IO.Path]::GetFullPath($bootstrapPython)
    if (-not (Test-Path -LiteralPath $bootstrapPython -PathType Leaf)) {
        throw "The requested plugin runtime Python does not exist."
    }
    $prewarmText = (& $bootstrapPython $runner --prewarm-only `
        --runtime-control-root $ExactRuntimeControlRoot `
        --host-profile CODEX_DESKTOP | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($prewarmText)) {
        throw "The complete hidden plugin runtime/toolchain prewarm failed before tunnel setup."
    }
    $prewarm = ($prewarmText -split "`r?`n" | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    } | Select-Object -Last 1) | ConvertFrom-Json
    $runtimePython = [IO.Path]::GetFullPath([string]$prewarm.runtime_python)
    $approvedRuntimeParent = [IO.Path]::GetFullPath(
        (Join-Path $ExactRuntimeControlRoot "runtime\codex")
    ) + [IO.Path]::DirectorySeparatorChar
    if (
        [string]$prewarm.schema -ne "evidence-lane.codex-native-runtime-prewarm.v1" -or
        [string]$prewarm.status -ne "PASS" -or
        [string]$prewarm.runtime_toolchain.schema -ne "evidence-lane.runtime-toolchain-prewarm.v1" -or
        [string]$prewarm.runtime_toolchain.status -ne "PASS" -or
        [int]$prewarm.runtime_toolchain.failure_count -ne 0 -or
        -not $runtimePython.StartsWith($approvedRuntimeParent, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $runtimePython -PathType Leaf)
    ) {
        throw "The tunnel cannot use an incomplete, unprewarmed, or non-hidden plugin runtime."
    }
    return [pscustomobject]@{
        python = $runtimePython
        prewarm = $prewarm
        toolchain = $prewarm.runtime_toolchain
    }
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
            Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($RuntimeControlRoot)) `
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
    if (
        [string]::IsNullOrWhiteSpace($value) -and
        (Test-Path -LiteralPath $profileFile -PathType Leaf)
    ) {
        $profileTunnelId = @(
            Get-Content -LiteralPath $profileFile |
            Where-Object { $_ -match '^\s*tunnel_id\s*:\s*"?(tunnel_[A-Za-z0-9]+)"?\s*$' } |
            ForEach-Object { $Matches[1] }
        )
        if ($profileTunnelId.Count -eq 1) {
            $value = [string]$profileTunnelId[0]
            $script:tunnelIdReused = $true
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
        Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($RuntimeControlRoot)) `
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
        [Parameter(Mandatory = $true)][string]$ExactRuntimeControlRoot
    )

    $escapedPython = $Python.Replace("'", "''")
    $escapedRunner = $Runner.Replace("'", "''")
    $escapedRuntimeControlRoot = $ExactRuntimeControlRoot.Replace("'", "''")
    $launcher = @"
`$ErrorActionPreference = "Stop"
`$env:EVIDENCE_LANE_MCP_EXPOSURE_PROFILE = "CODEX_INTERACTIVE_SUPPORT"
`$env:EVIDENCE_LANE_PUBLIC_SITE_URL = "https://evidencelane.org"
`$env:EVIDENCE_LANE_RUNTIME_CONTROL_ROOT = '$escapedRuntimeControlRoot'
`$env:EVIDENCE_LANE_HOST_PROFILE = 'CODEX_DESKTOP'
& '$escapedPython' '$escapedRunner' --transport stdio
exit `$LASTEXITCODE
"@
    Set-Content -LiteralPath $childTarget -Value $launcher -Encoding UTF8
}

function Remove-StoppedPriorTunnelRuntimes {
    param(
        [Parameter(Mandatory = $true)][string]$ExactRuntimeControlRoot,
        [Parameter(Mandatory = $true)][string]$ExactRuntimeRoot
    )

    $otherRuntimeRoots = @(
        Get-ChildItem -LiteralPath $ExactRuntimeControlRoot `
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
                throw "Another Evidence Lane tunnel is active; it must stop before replacement."
            }
        }
        catch {
            if ($_.Exception.Message -like "Another Evidence Lane tunnel is active;*") {
                throw
            }
            throw "A sibling Evidence Lane tunnel could not be proven stopped; activation is blocked."
        }
        $resolvedOtherRoot = [IO.Path]::GetFullPath($otherRoot.FullName)
        if (-not $resolvedOtherRoot.StartsWith(
            ([IO.Path]::GetFullPath($ExactRuntimeControlRoot) + [IO.Path]::DirectorySeparatorChar),
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "A prior tunnel runtime escaped the hidden runtime-control root."
        }
        Remove-Item -LiteralPath $resolvedOtherRoot -Recurse -Force
    }
}

function Remove-StoppedPriorTunnelTasks {
    param([Parameter(Mandatory = $true)][string]$ExactTaskName)

    foreach ($priorTask in @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        [string]$_.TaskName -like "EvidenceLane-Tunnel-*" -and
        [string]$_.TaskName -ne $ExactTaskName
    })) {
        if ([string]$priorTask.State -eq "Running") {
            Stop-ScheduledTask `
                -TaskName ([string]$priorTask.TaskName) `
                -ErrorAction Stop
        }
        Unregister-ScheduledTask -TaskName ([string]$priorTask.TaskName) -Confirm:$false -ErrorAction Stop
    }
}

$exactPluginRoot = Resolve-PluginRoot
$exactRuntimeControlRoot = [IO.Path]::GetFullPath($RuntimeControlRoot)
$expectedRuntimeControlRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:USERPROFILE ".codex\plugins\runtime\evidence-lane-plugin")
)
if ($exactRuntimeControlRoot -cne $expectedRuntimeControlRoot) {
    throw "The tunnel installer must use the exact hidden Evidence Lane Codex runtime root."
}
$exactRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$approvedRuntimeParent = $exactRuntimeControlRoot + [IO.Path]::DirectorySeparatorChar
if (-not $exactRuntimeRoot.StartsWith($approvedRuntimeParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The versioned tunnel runtime must remain inside the Evidence Lane runtime control root."
}
$runner = Join-Path $exactPluginRoot "scripts\run_mcp.py"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "The exact Evidence Lane MCP launcher is missing: $runner"
}
$catalogContract = $releaseChannel.stable
$exactVisibleToolCount = [int]$catalogContract.native_tool_count
$exactActiveReadToolCount = [int]$catalogContract.native_read_tool_count
$exactFailClosedWriteToolCount = [int]$catalogContract.native_write_tool_count
$exactSkillCount = @(
    Get-ChildItem -LiteralPath (Join-Path $exactPluginRoot "skills") -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "SKILL.md") -PathType Leaf }
).Count
$hookConfiguration = Get-Content -LiteralPath (Join-Path $exactPluginRoot "hooks\hooks.json") -Raw | ConvertFrom-Json
$exactHookEventCount = @($hookConfiguration.hooks.PSObject.Properties).Count
$exactHookHandlerCount = 0
foreach ($eventProperty in $hookConfiguration.hooks.PSObject.Properties) {
    foreach ($group in @($eventProperty.Value)) {
        $exactHookHandlerCount += @($group.hooks).Count
    }
}
$providerConfiguration = Get-Content -LiteralPath (Join-Path $exactPluginRoot ".mcp.json") -Raw | ConvertFrom-Json
$exactProviderCount = @($providerConfiguration.mcpServers.PSObject.Properties).Count
if (
    $exactVisibleToolCount -le 0 -or
    $exactActiveReadToolCount -le 0 -or
    $exactFailClosedWriteToolCount -le 0 -or
    $exactVisibleToolCount -ne ($exactActiveReadToolCount + $exactFailClosedWriteToolCount) -or
    $exactSkillCount -ne [int]$catalogContract.skill_count -or
    (Test-Path -LiteralPath (Join-Path $exactPluginRoot "commands")) -or
    (Test-Path -LiteralPath (Join-Path $exactPluginRoot ".codex-plugin\migrated-command-skills")) -or
    $exactHookEventCount -le 0 -or
    $exactHookHandlerCount -le 0 -or
    $exactProviderCount -le 0
) {
    throw "The package public-surface registries do not reconcile for tunnel activation."
}
if (-not (Test-Path -LiteralPath $sourceHost -PathType Leaf)) {
    throw "The no-visible-console Evidence Lane tunnel host is missing: $sourceHost"
}
$toolMatrixPath = Join-Path $exactPluginRoot "toolchains\tool-requirement-matrix.v1.json"
$tunnelToolchainPath = Join-Path $exactPluginRoot "toolchains\tunnel-runtime-toolchain.v1.json"
if (
    -not (Test-Path -LiteralPath $toolMatrixPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $tunnelToolchainPath -PathType Leaf)
) {
    throw "The tunnel runtime/toolchain manifests are missing from the installed plugin."
}
$runtimePrewarm = Resolve-PrewarmedRuntime `
    -ExactPluginRoot $exactPluginRoot `
    -ExactRuntimeControlRoot $exactRuntimeControlRoot `
    -RequestedRuntimePython $RuntimePython
$python = [string]$runtimePrewarm.python
$runtimeKey = [string]$runtimePrewarm.prewarm.runtime_identity.runtime_key
if ($runtimeKey -notmatch '^[A-F0-9]{64}$') {
    throw "The tunnel prewarm did not return an exact hidden runtime key."
}
$licenseScript = Join-Path $exactPluginRoot "scripts\generate_runtime_license_bundle.py"
$licenseRoot = Join-Path $exactRuntimeControlRoot ("runtime\licenses\" + $runtimeKey)
$licenseManifestPath = Join-Path $licenseRoot "manifest.v1.json"
if (-not (Test-Path -LiteralPath $licenseManifestPath -PathType Leaf)) {
    & $python $licenseScript --output $licenseRoot --plugin-root $exactPluginRoot *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "The exact installed runtime license bundle failed before tunnel setup."
    }
}
$runtimeLicenseManifest = Get-Content -LiteralPath $licenseManifestPath -Raw | ConvertFrom-Json
$toolLicenseInventoryPath = Join-Path $exactPluginRoot "toolchains\tool-license-inventory.v1.json"
$toolLicenseInventory = Get-Content -LiteralPath $toolLicenseInventoryPath -Raw | ConvertFrom-Json
if (
    [string]$runtimeLicenseManifest.schema -ne "evidence-lane.installed-runtime-license-bundle.v1" -or
    [string]$runtimeLicenseManifest.status -ne "PASS" -or
    [int]$runtimeLicenseManifest.tool_license_entry_count -ne [int]$runtimePrewarm.toolchain.requirement_count -or
    [bool]$runtimeLicenseManifest.all_tool_requirements_license_classified -ne $true -or
    [bool]$runtimeLicenseManifest.mcp_inventory_separate -ne $true -or
    [string]$toolLicenseInventory.schema -ne "evidence-lane.tool-license-inventory.v1" -or
    [string]$toolLicenseInventory.status -ne "PASS" -or
    [int]$toolLicenseInventory.tool_requirement_count -ne [int]$runtimePrewarm.toolchain.requirement_count -or
    [bool]$toolLicenseInventory.all_tool_requirements_classified -ne $true -or
    [bool]$toolLicenseInventory.mcp_inventory_separate -ne $true -or
    [int]$runtimeLicenseManifest.distribution_count -lt 1 -or
    [string]$runtimeLicenseManifest.receipt_sha256 -notmatch '^[A-F0-9]{64}$'
) {
    throw "The exact installed runtime license bundle is incomplete."
}
$resolvedSource = Resolve-TunnelClientSource
$resolvedLicense = Resolve-TunnelClientLicenseSource
$sourceHash = (Get-FileHash -LiteralPath $resolvedSource -Algorithm SHA256).Hash
if ($sourceHash -ne $expectedClientSha256) {
    throw "The supplied tunnel-client binary does not match the pinned v0.0.10 SHA-256."
}
$exactTunnelId = Resolve-TunnelId

New-Item -ItemType Directory -Path (Split-Path -Parent $stableClient) -Force | Out-Null
New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot "licenses\tunnel-client-v0.0.10") -Force | Out-Null
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
Copy-Item -LiteralPath $resolvedSource -Destination $stableClient -Force
Copy-Item -LiteralPath $sourceBoot -Destination $bootTarget -Force
Copy-Item -LiteralPath $sourceManage -Destination $manageTarget -Force
Copy-Item -LiteralPath $sourceHost -Destination $hostTarget -Force
Copy-Item -LiteralPath ([string]$resolvedLicense.path) `
    -Destination (Join-Path $RuntimeRoot "licenses\tunnel-client-v0.0.10\LICENSE") -Force
$runtimePrewarm.prewarm | ConvertTo-Json -Depth 100 | Set-Content `
    -LiteralPath (Join-Path $RuntimeRoot "runtime-toolchain-prewarm.json") -Encoding UTF8
$runtimeLicenseManifest | ConvertTo-Json -Depth 100 | Set-Content `
    -LiteralPath (Join-Path $RuntimeRoot "runtime-license-manifest.json") -Encoding UTF8
Protect-SecretDirectory

if ($RotateRuntimeKey -or -not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
    $effectiveEnvelopeSource = Resolve-PriorRuntimeKeyEnvelope
    if (-not [string]::IsNullOrWhiteSpace($effectiveEnvelopeSource) -and -not $RotateRuntimeKey) {
        $exactEnvelopeSource = [IO.Path]::GetFullPath($effectiveEnvelopeSource)
        $approvedEnvelopeParent = $exactRuntimeControlRoot + [IO.Path]::DirectorySeparatorChar
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

Write-LayeredChildLauncher `
    -Python $python `
    -Runner $runner `
    -ExactRuntimeControlRoot $exactRuntimeControlRoot
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
    schema = "evidence-lane.versioned-secure-mcp-tunnel-installation.v2"
    release = $release
    release_token = $releaseToken
    release_identity_source = "CODEX_RELEASE_CHANNEL_CONTRACT"
    runtime_identity_matches_release = $true
    runtime_root = [IO.Path]::GetFullPath($RuntimeRoot)
    runtime_control_root = $exactRuntimeControlRoot
    runtime_control_root_hidden = $true
    project_data_root_separate = $true
    workspace_root_separate = $true
    project_pv_root_user_defined = $true
    profile_name = $ProfileName
    profile_file = $profileFile
    task_name = $TaskName
    scheduled_task_launcher = $hostTarget
    scheduled_task_launcher_sha256 = (Get-FileHash -LiteralPath $hostTarget -Algorithm SHA256).Hash
    scheduled_task_launcher_subsystem = "WINDOWS_GUI_NO_VISIBLE_CONSOLE"
    scheduled_task_launcher_create_no_window = $true
    scheduled_task_transport_used = $true
    slot_role = $SlotRole
    byte_frozen = $SlotRole -eq "main-git-release"
    exposure_profile = "CODEX_INTERACTIVE_SUPPORT"
    transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"
    served_exposure_layer = "CODEX_INTERACTIVE_SUPPORT"
    chatgpt_is_layer_not_transport_identity = $true
    codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"
    codex_tunnel_lifecycle_proof_allowed = $false
    plugin_root = $exactPluginRoot
    project_authority_lookup = "HIDDEN_REGISTRY_BY_PROJECT_ID"
    project_authority_root_hardcoded = $false
    workspace_hardcoded = $false
    project_binding = "NONE_TRANSPORT_ONLY"
    host_wide_project_neutral = $true
    multi_project_and_task_routing = "EXPLICIT_PLUGIN_PROJECT_ID_AND_TASK_BINDINGS"
    per_project_or_task_tunnel_allowed = $false
    project_route_argument = "project_id"
    project_route_argument_required = $true
    cross_project_fallback_allowed = $false
    exact_visible_tool_count = $exactVisibleToolCount
    exact_active_read_tool_count = $exactActiveReadToolCount
    exact_fail_closed_write_tool_count = $exactFailClosedWriteToolCount
    exact_skill_count = $exactSkillCount
    separate_command_layer_present = $false
    exact_hook_event_count = $exactHookEventCount
    exact_hook_handler_count = $exactHookHandlerCount
    exact_provider_count = $exactProviderCount
    runtime_python = $python
    runtime_python_sha256 = (Get-FileHash -LiteralPath $python -Algorithm SHA256).Hash
    runtime_key = $runtimeKey
    tool_requirement_matrix = $toolMatrixPath
    tool_requirement_matrix_sha256 = (Get-FileHash -LiteralPath $toolMatrixPath -Algorithm SHA256).Hash
    tunnel_runtime_toolchain = $tunnelToolchainPath
    tunnel_runtime_toolchain_sha256 = (Get-FileHash -LiteralPath $tunnelToolchainPath -Algorithm SHA256).Hash
    runtime_toolchain_prewarm_receipt = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "runtime-toolchain-prewarm.json"
    runtime_toolchain_prewarm_receipt_sha256 = (Get-FileHash -LiteralPath (Join-Path $RuntimeRoot "runtime-toolchain-prewarm.json") -Algorithm SHA256).Hash
    runtime_toolchain_requirement_count = [int]$runtimePrewarm.toolchain.requirement_count
    runtime_toolchain_failure_count = [int]$runtimePrewarm.toolchain.failure_count
    runtime_license_manifest = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "runtime-license-manifest.json"
    runtime_license_manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $RuntimeRoot "runtime-license-manifest.json") -Algorithm SHA256).Hash
    runtime_license_distribution_count = [int]$runtimeLicenseManifest.distribution_count
    tool_license_inventory = $toolLicenseInventoryPath
    tool_license_inventory_sha256 = (Get-FileHash -LiteralPath $toolLicenseInventoryPath -Algorithm SHA256).Hash
    tool_license_entry_count = [int]$runtimeLicenseManifest.tool_license_entry_count
    all_94_tool_licenses_classified = [bool]$runtimeLicenseManifest.all_tool_requirements_license_classified
    mcp_inventory_separate_from_toolchain = [bool]$runtimeLicenseManifest.mcp_inventory_separate
    all_required_tunnel_dependencies_prewarmed = [int]$runtimePrewarm.toolchain.failure_count -eq 0
    tunnel_id = $exactTunnelId
    tunnel_client_license = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "licenses\tunnel-client-v0.0.10\LICENSE"
    tunnel_client_license_sha256 = [string]$resolvedLicense.sha256
    stable_client = $stableClient
    stable_client_sha256 = $expectedClientSha256
    pid_file = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_tunnel.pid"
    health_url_file = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_health.url"
    live_slot_authority = "SEALED_POST_PV11_TWO_SLOT_REGISTRY"
    saved_version = $true
    reusable_without_reinstall = $true
    runtime_key_envelope_reused = $runtimeKeyEnvelopeReused
    tunnel_id_reused = $tunnelIdReused
    interaction_profile = $InteractionProfile
    host_tool_transport = $HostToolTransport
    native_mcp_available = $false
    tunnel_requirement = "REQUIRED_FOR_HOST_TOOL_GAP"
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
    distribution_audience = "USER_OR_MAINTAINER_ACTIVE_3_0_RUNTIME"
    prior_versioned_runtimes_retained = $false
    prior_versioned_tasks_retained = $false
    prior_versioned_runtime_deletion_required = $true
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
    -Description "Pinned Evidence Lane $release $SlotRole host-wide secure MCP tunnel." `
    -Force | Out-Null

Disable-ScheduledTask -TaskName $TaskName | Out-Null
if ($Activate) {
    Remove-StoppedPriorTunnelRuntimes `
        -ExactRuntimeControlRoot $exactRuntimeControlRoot `
        -ExactRuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot))
    Remove-StoppedPriorTunnelTasks -ExactTaskName $TaskName
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
    byte_frozen = $SlotRole -eq "main-git-release"
    task_name = $TaskName
    trigger = "AT_LOGON"
    scheduled_task_transport_used = $true
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
    runtime_control_root = $exactRuntimeControlRoot
    runtime_control_root_hidden = $true
    project_data_root_separate = $true
    workspace_root_separate = $true
    project_pv_root_user_defined = $true
    project_authority_lookup = "HIDDEN_REGISTRY_BY_PROJECT_ID"
    project_authority_root_hardcoded = $false
    workspace_hardcoded = $false
    project_binding = "NONE_TRANSPORT_ONLY"
    host_wide_project_neutral = $true
    multi_project_and_task_routing = "EXPLICIT_PLUGIN_PROJECT_ID_AND_TASK_BINDINGS"
    per_project_or_task_tunnel_allowed = $false
    project_route_argument = "project_id"
    project_route_argument_required = $true
    cross_project_fallback_allowed = $false
    exact_visible_tool_count = $exactVisibleToolCount
    exact_active_read_tool_count = $exactActiveReadToolCount
    exact_fail_closed_write_tool_count = $exactFailClosedWriteToolCount
    exact_skill_count = $exactSkillCount
    separate_command_layer_present = $false
    exact_hook_event_count = $exactHookEventCount
    exact_hook_handler_count = $exactHookHandlerCount
    exact_provider_count = $exactProviderCount
    tunnel_id_recorded = $true
    runtime_key_plaintext_written = $false
    windows_console_policy = "PERSISTENT_OR_HIDDEN_NO_TRANSIENT_CONSOLE"
    scheduled_task_window_style = "HIDDEN"
    distribution_audience = "USER_OR_MAINTAINER_ACTIVE_3_0_RUNTIME"
    prior_versioned_runtimes_retained = $false
    prior_versioned_tasks_retained = $false
    prior_versioned_runtime_deletion_required = $true
    one_active_version_required = $true
    runtime_key_envelope_reused = $runtimeKeyEnvelopeReused
    tunnel_id_reused = $tunnelIdReused
    dependency_acquisition = $dependencyAcquisition
    interaction_profile = $InteractionProfile
    host_tool_transport = $HostToolTransport
    native_mcp_available = $false
    tunnel_requirement = "REQUIRED_FOR_HOST_TOOL_GAP"
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
    active_tunnel_registration = $true
    reusable_without_reinstall = $true
    runtime_selector_source = "CURRENT_ENABLED_PLUGIN_SELECTOR"
    registry_materialization_gate = "CURRENT_PACKAGE_INSTALL_AND_EXPLICIT_ACTIVATE"
    obsolete_selector_present = $false
    registered_slot = $SlotRole
    pre_3_0_fallback_allowed = $false
    activated = [bool]$Activate
    started = [bool]$Activate
} | ConvertTo-Json -Depth 4
