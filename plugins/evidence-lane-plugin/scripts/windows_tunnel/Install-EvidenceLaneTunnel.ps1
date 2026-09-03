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
    [switch]$CaptureRuntimeKeyOnly,
    [switch]$RequirePreparedRuntimeKey,
    [switch]$Activate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

function Get-PathSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($Path))
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "")
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Get-VersionedTunnelIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Release,
        [Parameter(Mandatory = $true)][string]$SlotRole,
        [Parameter(Mandatory = $true)][string]$PluginVersion,
        [Parameter(Mandatory = $true)][string]$TunnelCompatibilitySha256
    )

    if (
        $Release -notmatch '^\d+\.\d+\.\d+$' -or
        $PluginVersion -notmatch '^\d+\.\d+\.\d+\+codex\.[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$' -or
        $PluginVersion.Split('+')[0] -ne $Release -or
        $TunnelCompatibilitySha256 -notmatch '^[A-F0-9]{64}$'
    ) {
        throw "The exact plugin version does not match the selected release slot."
    }
    $releaseToken = "v" + ($Release -replace '\.', '')
    $humanPluginToken = ($PluginVersion.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    $slotNameToken = ($SlotRole.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    $pluginVersionSha256 = Get-StringSha256 -Value $PluginVersion
    $pluginVersionDigest = $pluginVersionSha256.Substring(0, 12).ToLowerInvariant()
    $tunnelCompatibilityDigest = $TunnelCompatibilitySha256.Substring(0, 12).ToLowerInvariant()
    $tunnelVersionToken = "${releaseToken}-${slotNameToken}-abi-${tunnelCompatibilityDigest}"
    $filePrefix = "evidence_lane_" + $tunnelVersionToken.Replace("-", "_")
    return [pscustomobject]@{
        release_token = $releaseToken
        plugin_version_token = $humanPluginToken
        plugin_version_sha256 = $pluginVersionSha256
        plugin_version_digest = $pluginVersionDigest
        tunnel_compatibility_sha256 = $TunnelCompatibilitySha256
        tunnel_compatibility_digest = $tunnelCompatibilityDigest
        tunnel_version_token = $tunnelVersionToken
        file_prefix = $filePrefix
        profile_name = "${filePrefix}_transport"
        task_name = "EvidenceLane-Tunnel-$tunnelVersionToken"
        runtime_directory_name = "tunnel-runtime-$tunnelVersionToken"
    }
}

function Get-SealedInstalledPluginBinding {
    param(
        [Parameter(Mandatory = $true)][string]$ExactPluginRoot,
        [Parameter(Mandatory = $true)][string]$PluginVersion,
        [Parameter(Mandatory = $true)][string]$MarketplaceName,
        [Parameter(Mandatory = $true)][string]$ExpectedSelector,
        [Parameter(Mandatory = $true)][string]$ExactRuntimeControlRoot
    )

    $codexHome = Split-Path -Parent (
        Split-Path -Parent (Split-Path -Parent $ExactRuntimeControlRoot)
    )
    $expectedInstalledRoot = [IO.Path]::GetFullPath(
        (Join-Path $codexHome "plugins\cache\$MarketplaceName\evidence-lane-plugin\$PluginVersion")
    )
    $resolvedPluginRoot = [IO.Path]::GetFullPath($ExactPluginRoot)
    if (-not $resolvedPluginRoot.Equals(
        $expectedInstalledRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Tunnel activation requires the exact sealed installed selector cache root."
    }

    $manifestPath = Join-Path $resolvedPluginRoot ".codex-plugin\plugin.json"
    $surfacePath = Join-Path $resolvedPluginRoot "manifests\executable-surface-registry.v1.json"
    $coherencePath = Join-Path $resolvedPluginRoot "manifests\package\package-surface-coherence.json"
    $sourceManifestPath = Join-Path $resolvedPluginRoot "manifests\package\source-manifest.json"
    $tunnelManifestPath = Join-Path $resolvedPluginRoot "tunnel\tunnel-manifest.v1.json"
    foreach ($requiredPath in @($manifestPath, $surfacePath, $coherencePath, $sourceManifestPath, $tunnelManifestPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "The sealed installed plugin binding is missing a required package proof."
        }
    }

    $manifestSha256 = Get-PathSha256 -Path $manifestPath
    $surfaceSha256 = Get-PathSha256 -Path $surfacePath
    $coherenceSha256 = Get-PathSha256 -Path $coherencePath
    $sourceManifestSha256 = Get-PathSha256 -Path $sourceManifestPath
    $tunnelManifestSha256 = Get-PathSha256 -Path $tunnelManifestPath
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $surface = Get-Content -LiteralPath $surfacePath -Raw | ConvertFrom-Json
    $coherence = Get-Content -LiteralPath $coherencePath -Raw | ConvertFrom-Json
    $sourceManifest = Get-Content -LiteralPath $sourceManifestPath -Raw | ConvertFrom-Json
    $tunnelManifest = Get-Content -LiteralPath $tunnelManifestPath -Raw | ConvertFrom-Json
    $manifestMember = @($sourceManifest.members | Where-Object {
        [string]$_.path -eq ".codex-plugin/plugin.json"
    })
    $surfaceMember = @($sourceManifest.members | Where-Object {
        [string]$_.path -eq "manifests/executable-surface-registry.v1.json"
    })
    $runnerMember = @($sourceManifest.members | Where-Object {
        [string]$_.path -eq "scripts/run_mcp.py"
    })
    $tunnelManifestMember = @($sourceManifest.members | Where-Object {
        [string]$_.path -eq "tunnel/tunnel-manifest.v1.json"
    })
    $runnerPath = Join-Path $resolvedPluginRoot "scripts\run_mcp.py"
    if (
        [string]$manifest.version -ne $PluginVersion -or
        [string]$surface.schema -ne "evidence-lane.executable-package-surface-registry.v1" -or
        [string]$surface.status -ne "PASS" -or
        [string]$surface.plugin_version -ne $PluginVersion -or
        [string]$coherence.schema -ne "evidence-lane.package-surface-coherence.v1" -or
        [string]$coherence.status -ne "PASS" -or
        [string]$coherence.plugin_version -ne $PluginVersion -or
        [string]$coherence.executable_surface_registry_sha256 -ne $surfaceSha256 -or
        [string]$tunnelManifest.schema -ne "evidence-lane.installed-tunnel-surface.v1" -or
        [string]$tunnelManifest.status -ne "PASS" -or
        [string]$tunnelManifest.tunnel_compatibility_schema -ne "evidence-lane.tunnel-capability-compatibility.v1" -or
        [string]$tunnelManifest.tunnel_compatibility_sha256 -notmatch '^[A-F0-9]{64}$' -or
        $manifestMember.Count -ne 1 -or
        [string]$manifestMember[0].sha256 -ne $manifestSha256 -or
        $surfaceMember.Count -ne 1 -or
        [string]$surfaceMember[0].sha256 -ne $surfaceSha256 -or
        $runnerMember.Count -ne 1 -or
        -not (Test-Path -LiteralPath $runnerPath -PathType Leaf) -or
        [string]$runnerMember[0].sha256 -ne (Get-PathSha256 -Path $runnerPath) -or
        $tunnelManifestMember.Count -ne 1 -or
        [string]$tunnelManifestMember[0].sha256 -ne $tunnelManifestSha256
    ) {
        throw "The installed package manifest and executable surface do not reconcile."
    }

    $receiptRoot = Join-Path $ExactRuntimeControlRoot "installations\codex-v300"
    $matchingReceipts = @()
    foreach ($receiptPath in @(Get-ChildItem -LiteralPath $receiptRoot -File -Filter "INSTALL_*.json" -ErrorAction SilentlyContinue)) {
        try {
            $candidate = Get-Content -LiteralPath $receiptPath.FullName -Raw | ConvertFrom-Json
            if (
                [string]$candidate.schema -eq "evidence-lane.codex-stable-installation.v2" -and
                [string]$candidate.status -eq "PASS" -and
                [string]$candidate.plugin.plugin_id -eq "evidence-lane-plugin" -and
                [string]$candidate.plugin.version -eq $PluginVersion -and
                [string]$candidate.activation.plugin_selector -eq $ExpectedSelector -and
                [IO.Path]::GetFullPath([string]$candidate.activation.installed_path) -eq $resolvedPluginRoot -and
                [string]$candidate.marketplace.name -eq $MarketplaceName
            ) {
                $matchingReceipts += [pscustomobject]@{
                    path = $receiptPath.FullName
                    body = $candidate
                }
            }
        }
        catch {
            continue
        }
    }
    if ($matchingReceipts.Count -ne 1) {
        throw "Tunnel activation requires one exact sealed installed-selector receipt."
    }
    $installedReceiptPath = [string]$matchingReceipts[0].path
    $installedReceipt = $matchingReceipts[0].body
    $sourceProof = @($installedReceipt.plugin.package_proofs.records | Where-Object {
        [string]$_.path -eq "manifests/package/source-manifest.json"
    })
    $coherenceProof = @($installedReceipt.plugin.package_proofs.records | Where-Object {
        [string]$_.path -eq "manifests/package/package-surface-coherence.json"
    })
    if (
        [string]$installedReceipt.activation.state -notin @(
            "LOCAL_INSTALLED_STATIC_ACCEPTANCE_TUNNEL_PENDING",
            "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED",
            "INSTALLED_RESTART_REQUIRED"
        ) -or
        [string]$installedReceipt.plugin.manifest_sha256 -ne $manifestSha256 -or
        [string]$installedReceipt.plugin.package_proofs.status -ne "PASS" -or
        $sourceProof.Count -ne 1 -or
        [string]$sourceProof[0].sha256 -ne $sourceManifestSha256 -or
        $coherenceProof.Count -ne 1 -or
        [string]$coherenceProof[0].sha256 -ne $coherenceSha256 -or
        [string]$installedReceipt.archive_sha256 -notmatch '^[A-F0-9]{64}$' -or
        [string]$installedReceipt.package_receipt_sha256 -notmatch '^[A-F0-9]{64}$' -or
        [string]$installedReceipt.receipt_sha256 -notmatch '^[A-F0-9]{64}$'
    ) {
        throw "The sealed installed-selector receipt does not reconcile with the cache bytes."
    }
    return [pscustomobject]@{
        schema = "evidence-lane.tunnel-installed-cache-binding.v1"
        status = "PASS"
        selector = $ExpectedSelector
        marketplace_name = $MarketplaceName
        installed_cache_root = $resolvedPluginRoot
        installed_receipt_path = $installedReceiptPath
        installed_receipt_file_sha256 = Get-PathSha256 -Path $installedReceiptPath
        installed_receipt_sha256 = [string]$installedReceipt.receipt_sha256
        installed_package_archive_sha256 = [string]$installedReceipt.archive_sha256
        installed_package_receipt_sha256 = [string]$installedReceipt.package_receipt_sha256
        plugin_manifest_path = $manifestPath
        plugin_manifest_sha256 = $manifestSha256
        executable_surface_registry_path = $surfacePath
        executable_surface_registry_sha256 = $surfaceSha256
        package_surface_coherence_path = $coherencePath
        package_surface_coherence_sha256 = $coherenceSha256
        package_surface_coherence_receipt_sha256 = [string]$coherence.receipt_sha256
        package_source_manifest_path = $sourceManifestPath
        package_source_manifest_sha256 = $sourceManifestSha256
        tunnel_manifest_path = $tunnelManifestPath
        tunnel_manifest_sha256 = $tunnelManifestSha256
        tunnel_compatibility_sha256 = [string]$tunnelManifest.tunnel_compatibility_sha256
        runner_path = $runnerPath
        runner_sha256 = Get-PathSha256 -Path $runnerPath
    }
}

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
$identityPluginRoot = if (-not [string]::IsNullOrWhiteSpace($PluginRoot)) {
    (Resolve-Path -LiteralPath $PluginRoot).Path
} else {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}
$pluginManifestPath = Join-Path $identityPluginRoot ".codex-plugin\plugin.json"
if (-not (Test-Path -LiteralPath $pluginManifestPath -PathType Leaf)) {
    throw "The exact plugin manifest is missing for tunnel identity binding."
}
$pluginManifest = Get-Content -LiteralPath $pluginManifestPath -Raw | ConvertFrom-Json
$pluginVersion = [string]$pluginManifest.version
$tunnelManifestPath = Join-Path $identityPluginRoot "tunnel\tunnel-manifest.v1.json"
if (-not (Test-Path -LiteralPath $tunnelManifestPath -PathType Leaf)) {
    throw "The installed tunnel compatibility manifest is missing."
}
$tunnelManifest = Get-Content -LiteralPath $tunnelManifestPath -Raw | ConvertFrom-Json
$tunnelCompatibilitySha256 = [string]$tunnelManifest.tunnel_compatibility_sha256
if (
    [string]::IsNullOrWhiteSpace($pluginVersion) -or
    $pluginVersion.Split('+')[0] -ne $release -or
    [string]$tunnelManifest.schema -ne "evidence-lane.installed-tunnel-surface.v1" -or
    [string]$tunnelManifest.status -ne "PASS" -or
    [string]$tunnelManifest.tunnel_compatibility_schema -ne "evidence-lane.tunnel-capability-compatibility.v1" -or
    $tunnelCompatibilitySha256 -notmatch '^[A-F0-9]{64}$'
) {
    throw "The tunnel plugin version does not match the selected release slot."
}
$versionIdentity = Get-VersionedTunnelIdentity `
    -Release $release `
    -SlotRole $SlotRole `
    -PluginVersion $pluginVersion `
    -TunnelCompatibilitySha256 $tunnelCompatibilitySha256
$releaseToken = [string]$versionIdentity.release_token
$pluginVersionToken = [string]$versionIdentity.plugin_version_token
$pluginVersionSha256 = [string]$versionIdentity.plugin_version_sha256
$pluginVersionDigest = [string]$versionIdentity.plugin_version_digest
$tunnelCompatibilityDigest = [string]$versionIdentity.tunnel_compatibility_digest
$tunnelVersionToken = [string]$versionIdentity.tunnel_version_token
$filePrefix = [string]$versionIdentity.file_prefix
$slotToken = $SlotRole.Replace("-", "_")
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $RuntimeControlRoot ([string]$versionIdentity.runtime_directory_name)
}
if ([string]::IsNullOrWhiteSpace($ProfileName)) {
    $ProfileName = [string]$versionIdentity.profile_name
}
if ([string]::IsNullOrWhiteSpace($TaskName)) {
    $TaskName = [string]$versionIdentity.task_name
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
$installedTunnelScriptRoot = Join-Path $identityPluginRoot "scripts\windows_tunnel"
$sourceBoot = Join-Path $installedTunnelScriptRoot "EvidenceLaneTunnel.Boot.ps1"
$sourceManage = Join-Path $installedTunnelScriptRoot "Manage-EvidenceLaneTunnel.ps1"
$sourceHost = Join-Path $installedTunnelScriptRoot "EvidenceLaneTunnelHost.exe"
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
                (Get-PathSha256 -Path $_) -eq $expectedClientSha256
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
        if ((Get-PathSha256 -Path $downloadTarget) -ne $expectedClientSha256) {
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
    $actual = Get-PathSha256 -Path $resolved
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

function Stop-AndRetainPriorTunnelRuntimes {
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
            $stopArguments = @(
                "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", $otherManager,
                "-Action", "Stop", "-RuntimeRoot", $otherRoot.FullName
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
                    $stopArguments += @("-$optionalParameter", $markerValue)
                }
            }
            $stopText = & $powershell @stopArguments 2>$null
            $status = ($stopText | Out-String).Trim() | ConvertFrom-Json
            if (
                [string]$status.status -ne "STOPPED_SAVED" -or
                [bool]$status.reusable_without_reinstall -ne $true
            ) {
                throw "Another Evidence Lane tunnel could not be stopped and retained before replacement."
            }
        }
        catch {
            if ($_.Exception.Message -like "Another Evidence Lane tunnel could not be stopped and retained;*") {
                throw
            }
            throw "A sibling Evidence Lane tunnel could not be proven stopped, disabled, and retained; activation is blocked."
        }
        $resolvedOtherRoot = [IO.Path]::GetFullPath($otherRoot.FullName)
        if (-not $resolvedOtherRoot.StartsWith(
            ([IO.Path]::GetFullPath($ExactRuntimeControlRoot) + [IO.Path]::DirectorySeparatorChar),
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "A prior tunnel runtime escaped the hidden runtime-control root."
        }
        $retainedMarker = Get-Content -LiteralPath $otherMarkerPath -Raw | ConvertFrom-Json
        if ([string]$retainedMarker.runtime_root -ne $resolvedOtherRoot) {
            throw "A retained prior tunnel marker no longer matches its versioned runtime root."
        }
    }
}

function Stop-DisableAndRetainPriorTunnelTasks {
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
        Disable-ScheduledTask -TaskName ([string]$priorTask.TaskName) -ErrorAction Stop | Out-Null
        $retainedTask = Get-ScheduledTask -TaskName ([string]$priorTask.TaskName) -ErrorAction Stop
        if ([string]$retainedTask.State -ne "Disabled") {
            throw "A prior versioned Evidence Lane tunnel task was not retained in Disabled state."
        }
    }
}

function Get-ActivePriorTunnelBindings {
    param(
        [Parameter(Mandatory = $true)][string]$ExactRuntimeControlRoot,
        [Parameter(Mandatory = $true)][string]$ExactTaskName
    )

    $activeTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        [string]$_.TaskName -like "EvidenceLane-Tunnel-*" -and
        [string]$_.TaskName -ne $ExactTaskName -and
        [string]$_.State -eq "Running"
    })
    if ($activeTasks.Count -gt 1) {
        throw "Tunnel rotation found more than one active prior tunnel task."
    }
    $bindings = @()
    foreach ($activeTask in $activeTasks) {
        $matchingMarkers = @(
            Get-ChildItem -LiteralPath $ExactRuntimeControlRoot `
                -Directory -Filter "tunnel-runtime-*" -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $markerPath = Join-Path $_.FullName "evidence-lane-tunnel-installation.json"
                    if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
                        try {
                            $markerBody = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
                            if ([string]$markerBody.task_name -eq [string]$activeTask.TaskName) {
                                [pscustomobject]@{
                                    runtime_root = $_.FullName
                                    marker = $markerBody
                                    manager = Join-Path $_.FullName "Manage-EvidenceLaneTunnel.ps1"
                                }
                            }
                        }
                        catch {
                            continue
                        }
                    }
                }
        )
        if (
            $matchingMarkers.Count -ne 1 -or
            -not (Test-Path -LiteralPath $matchingMarkers[0].manager -PathType Leaf)
        ) {
            throw "The active prior tunnel task does not have one retained manager binding."
        }
        $bindings += $matchingMarkers[0]
    }
    return $bindings
}

function Invoke-RetainedTunnelManager {
    param(
        [Parameter(Mandatory = $true)]$Binding,
        [Parameter(Mandatory = $true)][ValidateSet("Start", "Stop")][string]$Action
    )

    $managerCommand = Get-Command -Name ([string]$Binding.manager) `
        -CommandType ExternalScript -ErrorAction Stop
    $arguments = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-File", [string]$Binding.manager,
        "-Action", $Action,
        "-RuntimeRoot", [string]$Binding.runtime_root
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
        $markerValue = [string]$Binding.marker.$markerProperty
        if (-not [string]::IsNullOrWhiteSpace($markerValue)) {
            $arguments += @("-$optionalParameter", $markerValue)
        }
    }
    $resultText = & $powershell @arguments 2>$null
    $exitCode = $LASTEXITCODE
    $result = ($resultText | Out-String).Trim() | ConvertFrom-Json
    if ($exitCode -ne 0) {
        throw "A retained tunnel manager failed the requested $Action operation."
    }
    return $result
}

function Assert-SingleActiveTunnel {
    param(
        [Parameter(Mandatory = $true)][string]$ExactRuntimeControlRoot,
        [Parameter(Mandatory = $true)][string]$ExactTaskName,
        [Parameter(Mandatory = $true)][string]$ExactRuntimeRoot
    )

    $activeTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        [string]$_.TaskName -like "EvidenceLane-Tunnel-*" -and
        [string]$_.State -eq "Running"
    })
    $activeProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        [string]$_.Name -eq "tunnel-client-v0.0.10.exe" -and
        -not [string]::IsNullOrWhiteSpace([string]$_.ExecutablePath) -and
        [IO.Path]::GetFullPath([string]$_.ExecutablePath).StartsWith(
            ([IO.Path]::GetFullPath($ExactRuntimeControlRoot) + [IO.Path]::DirectorySeparatorChar),
            [StringComparison]::OrdinalIgnoreCase
        )
    })
    if (
        $activeTasks.Count -ne 1 -or
        [string]$activeTasks[0].TaskName -ne $ExactTaskName -or
        $activeProcesses.Count -ne 1 -or
        -not [IO.Path]::GetFullPath([string]$activeProcesses[0].ExecutablePath).StartsWith(
            ([IO.Path]::GetFullPath($ExactRuntimeRoot) + [IO.Path]::DirectorySeparatorChar),
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Tunnel activation did not prove exactly one versioned task and process active."
    }
}

$exactPluginRoot = $identityPluginRoot
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
$marketplaceName = [string]$slotContract.codex_marketplace_slot
$installedSelector = "evidence-lane-plugin@$marketplaceName"
$installedBinding = $null
$compatibleTunnelRuntimeRebound = $false
if ($Activate -or $CaptureRuntimeKeyOnly) {
    $installedBinding = Get-SealedInstalledPluginBinding `
        -ExactPluginRoot $exactPluginRoot `
        -PluginVersion $pluginVersion `
        -MarketplaceName $marketplaceName `
        -ExpectedSelector $installedSelector `
        -ExactRuntimeControlRoot $exactRuntimeControlRoot
    $expectedInstalledInstallerRoot = [IO.Path]::GetFullPath(
        (Join-Path $exactPluginRoot "scripts\windows_tunnel")
    )
    if (-not [IO.Path]::GetFullPath($PSScriptRoot).Equals(
        $expectedInstalledInstallerRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Tunnel activation must execute the installer from the sealed installed plugin cache."
    }
}
$existingCompatibleMarkerPath = Join-Path $exactRuntimeRoot "evidence-lane-tunnel-installation.json"
if ($Activate -and (Test-Path -LiteralPath $existingCompatibleMarkerPath -PathType Leaf)) {
    $existingCompatibleMarker = Get-Content -LiteralPath $existingCompatibleMarkerPath -Raw | ConvertFrom-Json
    $existingCompatibleManager = Join-Path $exactRuntimeRoot "Manage-EvidenceLaneTunnel.ps1"
    if (
        [string]$existingCompatibleMarker.tunnel_compatibility_sha256 -ne $tunnelCompatibilitySha256 -or
        -not (Test-Path -LiteralPath $existingCompatibleManager -PathType Leaf)
    ) {
        throw "The existing capability-bound tunnel runtime cannot be safely rebound."
    }
    $stopText = & $existingCompatibleManager `
        -Action Stop `
        -RuntimeRoot $exactRuntimeRoot `
        -ProfileName ([string]$existingCompatibleMarker.profile_name) `
        -ProfileDir $profileDir `
        -ReleaseToken ([string]$existingCompatibleMarker.release_token) `
        -TaskName ([string]$existingCompatibleMarker.task_name) 2>$null
    $stopResult = ($stopText | Out-String).Trim() | ConvertFrom-Json
    if ([string]$stopResult.status -ne "STOPPED_SAVED") {
        throw "The compatible tunnel could not be stopped before exact plugin rebinding."
    }
    $compatibleTunnelRuntimeRebound = $true
}
$runner = Join-Path $exactPluginRoot "scripts\run_mcp.py"
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "The exact Evidence Lane MCP launcher is missing: $runner"
}
if ($CaptureRuntimeKeyOnly) {
    if ($Activate -or $RequirePreparedRuntimeKey) {
        throw "Interactive Runtime-key capture is a separate pre-activation phase."
    }
    New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
    Protect-SecretDirectory
    $interactiveKeyEntry = $RotateRuntimeKey -or -not (Test-Path -LiteralPath $secretFile -PathType Leaf)
    if ($interactiveKeyEntry) {
        Save-RuntimeKeyEnvelope
    }
    [ordered]@{
        status = "PASS"
        state = if ($interactiveKeyEntry) { "LOCAL_RUNTIME_KEY_CAPTURED_FOR_TUNNEL_CAPABILITY" } else { "COMPATIBLE_TUNNEL_RUNTIME_KEY_REUSED" }
        plugin_version = $pluginVersion
        tunnel_version_token = $tunnelVersionToken
        tunnel_compatibility_sha256 = $tunnelCompatibilitySha256
        runtime_root = $exactRuntimeRoot
        interactive_terminal_required = $interactiveKeyEntry
        runtime_key_reused_from_compatible_tunnel = -not $interactiveKeyEntry
        runtime_key_plaintext_written = $false
        runtime_key_argument_used = $false
        runtime_key_environment_output = $false
        persistent_runtime_started = $false
    } | ConvertTo-Json -Depth 4
    exit 0
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
$sourceHash = Get-PathSha256 -Path $resolvedSource
if ($sourceHash -ne $expectedClientSha256) {
    throw "The supplied tunnel-client binary does not match the pinned v0.0.10 SHA-256."
}
$exactTunnelId = Resolve-TunnelId

New-Item -ItemType Directory -Path (Split-Path -Parent $stableClient) -Force | Out-Null
New-Item -ItemType Directory -Path $secretRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $RuntimeRoot "licenses\tunnel-client-v0.0.10") -Force | Out-Null
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
$resolvedSourcePath = [IO.Path]::GetFullPath([string]$resolvedSource)
$stableClientPath = [IO.Path]::GetFullPath($stableClient)
$tunnelClientMaterialization = "COPIED_FROM_VERIFIED_SOURCE"
if ($resolvedSourcePath.Equals($stableClientPath, [StringComparison]::OrdinalIgnoreCase)) {
    if ((Get-PathSha256 -Path $stableClientPath) -ne $expectedClientSha256) {
        throw "The in-place compatible tunnel client no longer matches its pinned SHA-256."
    }
    $tunnelClientMaterialization = "REUSED_VERIFIED_IN_PLACE"
}
else {
    Copy-Item -LiteralPath $resolvedSourcePath -Destination $stableClientPath -Force
}
Copy-Item -LiteralPath $sourceBoot -Destination $bootTarget -Force
Copy-Item -LiteralPath $sourceManage -Destination $manageTarget -Force
Copy-Item -LiteralPath $sourceHost -Destination $hostTarget -Force
$resolvedLicensePath = [IO.Path]::GetFullPath([string]$resolvedLicense.path)
$stableLicensePath = [IO.Path]::GetFullPath(
    (Join-Path $RuntimeRoot "licenses\tunnel-client-v0.0.10\LICENSE")
)
$tunnelClientLicenseMaterialization = "COPIED_FROM_VERIFIED_SOURCE"
if ($resolvedLicensePath.Equals($stableLicensePath, [StringComparison]::OrdinalIgnoreCase)) {
    if ((Get-PathSha256 -Path $stableLicensePath) -ne [string]$resolvedLicense.sha256) {
        throw "The in-place compatible tunnel-client license no longer matches its pinned SHA-256."
    }
    $tunnelClientLicenseMaterialization = "REUSED_VERIFIED_IN_PLACE"
}
else {
    Copy-Item -LiteralPath $resolvedLicensePath -Destination $stableLicensePath -Force
}
$runtimePrewarm.prewarm | ConvertTo-Json -Depth 100 | Set-Content `
    -LiteralPath (Join-Path $RuntimeRoot "runtime-toolchain-prewarm.json") -Encoding UTF8
$runtimeLicenseManifest | ConvertTo-Json -Depth 100 | Set-Content `
    -LiteralPath (Join-Path $RuntimeRoot "runtime-license-manifest.json") -Encoding UTF8
Protect-SecretDirectory

if ($RequirePreparedRuntimeKey) {
    if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
        throw "The first-registration Runtime-key phase has not produced a reusable encrypted envelope."
    }
}
elseif ($RotateRuntimeKey -or -not (Test-Path -LiteralPath $secretFile -PathType Leaf)) {
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
    plugin_version = $pluginVersion
    plugin_version_token = $pluginVersionToken
    plugin_version_sha256 = $pluginVersionSha256
    plugin_version_digest = $pluginVersionDigest
    tunnel_compatibility_schema = "evidence-lane.tunnel-capability-compatibility.v1"
    tunnel_compatibility_sha256 = $tunnelCompatibilitySha256
    tunnel_compatibility_digest = $tunnelCompatibilityDigest
    tunnel_version_token = $tunnelVersionToken
    file_prefix = $filePrefix
    plugin_manifest_sha256 = Get-PathSha256 -Path $pluginManifestPath
    installed_cache_binding_schema = if ($null -ne $installedBinding) { [string]$installedBinding.schema } else { "NOT_ACTIVATED" }
    installed_selector = if ($null -ne $installedBinding) { [string]$installedBinding.selector } else { $installedSelector }
    installed_marketplace_name = $marketplaceName
    installed_cache_root = if ($null -ne $installedBinding) { [string]$installedBinding.installed_cache_root } else { "NOT_ACTIVATED" }
    installed_receipt_path = if ($null -ne $installedBinding) { [string]$installedBinding.installed_receipt_path } else { "NOT_ACTIVATED" }
    installed_receipt_file_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.installed_receipt_file_sha256 } else { "NOT_ACTIVATED" }
    installed_receipt_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.installed_receipt_sha256 } else { "NOT_ACTIVATED" }
    installed_package_archive_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.installed_package_archive_sha256 } else { "NOT_ACTIVATED" }
    installed_package_receipt_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.installed_package_receipt_sha256 } else { "NOT_ACTIVATED" }
    executable_surface_registry_path = if ($null -ne $installedBinding) { [string]$installedBinding.executable_surface_registry_path } else { "NOT_ACTIVATED" }
    executable_surface_registry_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.executable_surface_registry_sha256 } else { "NOT_ACTIVATED" }
    package_surface_coherence_path = if ($null -ne $installedBinding) { [string]$installedBinding.package_surface_coherence_path } else { "NOT_ACTIVATED" }
    package_surface_coherence_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.package_surface_coherence_sha256 } else { "NOT_ACTIVATED" }
    package_surface_coherence_receipt_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.package_surface_coherence_receipt_sha256 } else { "NOT_ACTIVATED" }
    package_source_manifest_path = if ($null -ne $installedBinding) { [string]$installedBinding.package_source_manifest_path } else { "NOT_ACTIVATED" }
    package_source_manifest_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.package_source_manifest_sha256 } else { "NOT_ACTIVATED" }
    tunnel_manifest_path = if ($null -ne $installedBinding) { [string]$installedBinding.tunnel_manifest_path } else { $tunnelManifestPath }
    tunnel_manifest_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.tunnel_manifest_sha256 } else { Get-PathSha256 -Path $tunnelManifestPath }
    installed_runner_path = if ($null -ne $installedBinding) { [string]$installedBinding.runner_path } else { $runner }
    installed_runner_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.runner_sha256 } else { Get-PathSha256 -Path $runner }
    installed_tunnel_installer_path = Join-Path $installedTunnelScriptRoot "Install-EvidenceLaneTunnel.ps1"
    installed_tunnel_installer_sha256 = Get-PathSha256 -Path (Join-Path $installedTunnelScriptRoot "Install-EvidenceLaneTunnel.ps1")
    installed_tunnel_boot_sha256 = Get-PathSha256 -Path $sourceBoot
    installed_tunnel_manager_sha256 = Get-PathSha256 -Path $sourceManage
    installed_tunnel_host_sha256 = Get-PathSha256 -Path $sourceHost
    runtime_tunnel_boot_sha256 = Get-PathSha256 -Path $bootTarget
    runtime_tunnel_manager_sha256 = Get-PathSha256 -Path $manageTarget
    runtime_tunnel_host_sha256 = Get-PathSha256 -Path $hostTarget
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
    scheduled_task_launcher_sha256 = Get-PathSha256 -Path $hostTarget
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
    runtime_python_sha256 = Get-PathSha256 -Path $python
    runtime_key = $runtimeKey
    tool_requirement_matrix = $toolMatrixPath
    tool_requirement_matrix_sha256 = Get-PathSha256 -Path $toolMatrixPath
    tunnel_runtime_toolchain = $tunnelToolchainPath
    tunnel_runtime_toolchain_sha256 = Get-PathSha256 -Path $tunnelToolchainPath
    runtime_toolchain_prewarm_receipt = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "runtime-toolchain-prewarm.json"
    runtime_toolchain_prewarm_receipt_sha256 = Get-PathSha256 -Path (Join-Path $RuntimeRoot "runtime-toolchain-prewarm.json")
    runtime_toolchain_requirement_count = [int]$runtimePrewarm.toolchain.requirement_count
    runtime_toolchain_failure_count = [int]$runtimePrewarm.toolchain.failure_count
    runtime_license_manifest = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "runtime-license-manifest.json"
    runtime_license_manifest_sha256 = Get-PathSha256 -Path (Join-Path $RuntimeRoot "runtime-license-manifest.json")
    runtime_license_distribution_count = [int]$runtimeLicenseManifest.distribution_count
    tool_license_inventory = $toolLicenseInventoryPath
    tool_license_inventory_sha256 = Get-PathSha256 -Path $toolLicenseInventoryPath
    tool_license_entry_count = [int]$runtimeLicenseManifest.tool_license_entry_count
    all_94_tool_licenses_classified = [bool]$runtimeLicenseManifest.all_tool_requirements_license_classified
    mcp_inventory_separate_from_toolchain = [bool]$runtimeLicenseManifest.mcp_inventory_separate
    all_required_tunnel_dependencies_prewarmed = [int]$runtimePrewarm.toolchain.failure_count -eq 0
    tunnel_id = $exactTunnelId
    tunnel_client_license = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "licenses\tunnel-client-v0.0.10\LICENSE"
    tunnel_client_license_sha256 = [string]$resolvedLicense.sha256
    stable_client = $stableClient
    stable_client_sha256 = $expectedClientSha256
    tunnel_client_materialization = $tunnelClientMaterialization
    tunnel_client_license_materialization = $tunnelClientLicenseMaterialization
    pid_file = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_tunnel.pid"
    health_url_file = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_health.url"
    daemon_log = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_tunnel.log"
    operator_log = Join-Path ([IO.Path]::GetFullPath($RuntimeRoot)) "${filePrefix}_operator.log"
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
    prior_versioned_runtimes_retained = $true
    prior_versioned_tasks_retained = $true
    prior_versioned_tasks_disabled = $true
    prior_versioned_runtime_deletion_required = $false
    one_active_version_required = $true
    exact_plugin_rebind_required_every_install = $true
    tunnel_rebuild_trigger = "CAPABILITY_FINGERPRINT_CHANGED_ONLY"
    compatible_runtime_key_and_prewarm_reused = $true
    compatible_tunnel_runtime_rebound = $compatibleTunnelRuntimeRebound
    runtime_key_prompt_policy = "FIRST_REGISTRATION_OR_MISSING_INVALID_ONLY"
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
$priorActiveBindings = @()
if ($Activate) {
    $priorActiveBindings = @(Get-ActivePriorTunnelBindings `
        -ExactRuntimeControlRoot $exactRuntimeControlRoot `
        -ExactTaskName $TaskName)
    try {
        Stop-AndRetainPriorTunnelRuntimes `
            -ExactRuntimeControlRoot $exactRuntimeControlRoot `
            -ExactRuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot))
        Stop-DisableAndRetainPriorTunnelTasks -ExactTaskName $TaskName
        $startText = & $manageTarget `
            -Action Start `
            -RuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot)) `
            -ProfileName $ProfileName `
            -ProfileDir $profileDir `
            -ReleaseToken $releaseToken `
            -TaskName $TaskName 2>$null
        $startExitCode = $LASTEXITCODE
        $startStatus = ($startText | Out-String).Trim() | ConvertFrom-Json
        if (
            $startExitCode -ne 0 -or
            [string]$startStatus.status -ne "PASS" -or
            [string]$startStatus.plugin_version -ne $pluginVersion -or
            [string]$startStatus.tunnel_version_token -ne $tunnelVersionToken -or
            [bool]$startStatus.control_plane_poll_ready -ne $true
        ) {
            throw "The version-matched Evidence Lane tunnel did not reach readiness."
        }
        Assert-SingleActiveTunnel `
            -ExactRuntimeControlRoot $exactRuntimeControlRoot `
            -ExactTaskName $TaskName `
            -ExactRuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot))
    }
    catch {
        $activationFailure = $_.Exception.Message
        try {
            & $manageTarget `
                -Action Stop `
                -RuntimeRoot ([IO.Path]::GetFullPath($RuntimeRoot)) `
                -ProfileName $ProfileName `
                -ProfileDir $profileDir `
                -ReleaseToken $releaseToken `
                -TaskName $TaskName *> $null
        }
        catch {
            $activationFailure += "; failed new-version detach: $($_.Exception.Message)"
        }
        Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
        $rollbackFailures = @()
        foreach ($priorBinding in $priorActiveBindings) {
            try {
                $restored = Invoke-RetainedTunnelManager `
                    -Binding $priorBinding `
                    -Action Start
                if (
                    [string]$restored.status -ne "PASS" -or
                    [bool]$restored.control_plane_poll_ready -ne $true
                ) {
                    throw "The retained prior tunnel did not return to readiness."
                }
                Assert-SingleActiveTunnel `
                    -ExactRuntimeControlRoot $exactRuntimeControlRoot `
                    -ExactTaskName ([string]$priorBinding.marker.task_name) `
                    -ExactRuntimeRoot ([string]$priorBinding.runtime_root)
            }
            catch {
                $rollbackFailures += $_.Exception.Message
            }
        }
        if ($rollbackFailures.Count -gt 0) {
            [ordered]@{
                status = "FAIL"
                state = "NEW_LOCAL_TUNNEL_FAILED_ROLLBACK_FAILED"
                plugin_version = $pluginVersion
                tunnel_version_token = $tunnelVersionToken
                failed_new_local_tunnel_disabled = $true
                prior_local_tunnel_rollback_status = "FAIL"
                prior_local_tunnel_restore_count = 0
                failure_sha256 = Get-StringSha256 -Value ($activationFailure + ($rollbackFailures -join ";"))
                remote_crud_invoked = $false
            } | ConvertTo-Json -Depth 5
            exit 1
        }
        if ($priorActiveBindings.Count -eq 0) {
            [ordered]@{
                status = "FAIL"
                state = "NEW_LOCAL_TUNNEL_FAILED_NO_PRIOR_ACTIVE"
                plugin_version = $pluginVersion
                tunnel_version_token = $tunnelVersionToken
                failed_new_local_tunnel_disabled = $true
                prior_local_tunnel_rollback_status = "NOT_APPLICABLE"
                prior_local_tunnel_restore_count = 0
                failure_sha256 = Get-StringSha256 -Value $activationFailure
                remote_crud_invoked = $false
            } | ConvertTo-Json -Depth 5
            exit 1
        }
        [ordered]@{
            status = "FAIL"
            state = "NEW_LOCAL_TUNNEL_FAILED_PRIOR_RESTORED"
            plugin_version = $pluginVersion
            tunnel_version_token = $tunnelVersionToken
            failed_new_local_tunnel_disabled = $true
            prior_local_tunnel_rollback_status = "PASS"
            prior_local_tunnel_restore_count = $priorActiveBindings.Count
            prior_local_task_names = @($priorActiveBindings | ForEach-Object { [string]$_.marker.task_name })
            failure_sha256 = Get-StringSha256 -Value $activationFailure
            remote_crud_invoked = $false
        } | ConvertTo-Json -Depth 5
        exit 1
    }
}

[ordered]@{
    status = "PASS"
    release = $release
    release_token = $releaseToken
    plugin_version = $pluginVersion
    plugin_version_token = $pluginVersionToken
    plugin_version_sha256 = $pluginVersionSha256
    plugin_version_digest = $pluginVersionDigest
    tunnel_compatibility_sha256 = $tunnelCompatibilitySha256
    tunnel_compatibility_digest = $tunnelCompatibilityDigest
    compatible_tunnel_runtime_rebound = $compatibleTunnelRuntimeRebound
    tunnel_version_token = $tunnelVersionToken
    release_identity_source = "CODEX_RELEASE_CHANNEL_CONTRACT"
    runtime_identity_matches_release = $true
    slot_role = $SlotRole
    installed_selector = $installedSelector
    installed_cache_root = if ($null -ne $installedBinding) { [string]$installedBinding.installed_cache_root } else { "NOT_ACTIVATED" }
    runtime_root = [IO.Path]::GetFullPath($RuntimeRoot)
    installed_receipt_file_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.installed_receipt_file_sha256 } else { "NOT_ACTIVATED" }
    installed_package_archive_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.installed_package_archive_sha256 } else { "NOT_ACTIVATED" }
    executable_surface_registry_sha256 = if ($null -ne $installedBinding) { [string]$installedBinding.executable_surface_registry_sha256 } else { "NOT_ACTIVATED" }
    byte_frozen = $SlotRole -eq "main-git-release"
    task_name = $TaskName
    trigger = "AT_LOGON"
    scheduled_task_transport_used = $true
    current_user_dpapi = $true
    stable_client = $stableClient
    stable_client_sha256 = Get-PathSha256 -Path $stableClient
    tunnel_client_materialization = $tunnelClientMaterialization
    tunnel_client_license_materialization = $tunnelClientLicenseMaterialization
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
    prior_versioned_runtimes_retained = $true
    prior_versioned_tasks_retained = $true
    prior_versioned_tasks_disabled = $true
    prior_versioned_runtime_deletion_required = $false
    one_active_version_required = $true
    prior_local_bindings = @($priorActiveBindings | ForEach-Object {
        [ordered]@{
            runtime_root = [string]$_.runtime_root
            task_name = [string]$_.marker.task_name
            profile_name = [string]$_.marker.profile_name
            release_token = [string]$_.marker.release_token
            plugin_version = [string]$_.marker.plugin_version
        }
    })
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
