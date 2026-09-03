[CmdletBinding()]
param(
    [string]$RuntimeRoot = "",
    [string]$ProfileName = "",
    [string]$ProfileDir = "$env:APPDATA\tunnel-client",
    [string]$ReleaseToken = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = $PSScriptRoot
}
$exactRuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
$expectedRuntimeControlRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:USERPROFILE ".codex\plugins\runtime\evidence-lane-plugin")
)
$approvedRuntimeParent = $expectedRuntimeControlRoot + [IO.Path]::DirectorySeparatorChar
if (-not $exactRuntimeRoot.StartsWith($approvedRuntimeParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Tunnel boot requires the exact hidden Evidence Lane Codex runtime boundary."
}

$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
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

$markerFile = Join-Path $RuntimeRoot "evidence-lane-tunnel-installation.json"
if (-not (Test-Path -LiteralPath $markerFile -PathType Leaf)) {
    throw "The version-bound Evidence Lane tunnel marker is missing."
}
$marker = Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($ProfileName)) {
    $ProfileName = [string]$marker.profile_name
}
if ([string]::IsNullOrWhiteSpace($ReleaseToken)) {
    $ReleaseToken = [string]$marker.release_token
}
$markerPluginVersion = [string]$marker.plugin_version
$expectedPluginVersionSha256 = Get-StringSha256 -Value $markerPluginVersion
$expectedPluginVersionDigest = $expectedPluginVersionSha256.Substring(0, 12).ToLowerInvariant()
$expectedPluginVersionToken = ($markerPluginVersion.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
$expectedSlotToken = ([string]$marker.slot_role).ToLowerInvariant() -replace '[^a-z0-9]+', '-'
$expectedTunnelCompatibilitySha256 = [string]$marker.tunnel_compatibility_sha256
$expectedTunnelCompatibilityDigest = $expectedTunnelCompatibilitySha256.Substring(0, 12).ToLowerInvariant()
$expectedTunnelVersionToken = "${ReleaseToken}-${expectedSlotToken}-abi-${expectedTunnelCompatibilityDigest}"
if (
    $marker.schema -ne "evidence-lane.versioned-secure-mcp-tunnel-installation.v2" -or
    [string]$marker.release_token -ne $ReleaseToken -or
    [IO.Path]::GetFullPath([string]$marker.runtime_root) -ne [IO.Path]::GetFullPath($RuntimeRoot) -or
    [string]$marker.profile_name -ne $ProfileName -or
    [bool]$marker.host_wide_project_neutral -ne $true -or
    [bool]$marker.per_project_or_task_tunnel_allowed -ne $false -or
    [bool]$marker.scheduled_task_transport_used -ne $true -or
    [string]$marker.plugin_version -notmatch '^\d+\.\d+\.\d+\+codex\.[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$' -or
    ([string]$marker.plugin_version).Split('+')[0] -ne [string]$marker.release -or
    [string]$marker.plugin_version_token -ne $expectedPluginVersionToken -or
    [string]$marker.plugin_version_sha256 -ne $expectedPluginVersionSha256 -or
    [string]$marker.plugin_version_digest -ne $expectedPluginVersionDigest -or
    [string]$marker.tunnel_compatibility_schema -ne "evidence-lane.tunnel-capability-compatibility.v1" -or
    $expectedTunnelCompatibilitySha256 -notmatch '^[A-F0-9]{64}$' -or
    [string]$marker.tunnel_compatibility_digest -ne $expectedTunnelCompatibilityDigest -or
    [string]$marker.tunnel_version_token -ne $expectedTunnelVersionToken -or
    [string]$marker.tunnel_version_token -notmatch '^v[0-9]+-[a-z0-9-]+$' -or
    [string]$marker.file_prefix -notmatch '^evidence_lane_v[0-9]+_[a-z0-9_]+$'
) {
    throw "The boot request does not match the exact release-bound tunnel marker."
}
$filePrefix = [string]$marker.file_prefix
$client = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$secretFile = Join-Path $RuntimeRoot "secrets\control-plane-runtime-key.dpapi"
$healthUrlFile = [IO.Path]::GetFullPath([string]$marker.health_url_file)
$pidFile = [IO.Path]::GetFullPath([string]$marker.pid_file)
$daemonLog = [IO.Path]::GetFullPath([string]$marker.daemon_log)
$operatorLog = [IO.Path]::GetFullPath([string]$marker.operator_log)

function Get-Sha256 {
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

function Assert-InstalledCacheBinding {
    param([Parameter(Mandatory = $true)]$BoundMarker)

    $selector = [string]$BoundMarker.installed_selector
    $selectorParts = $selector.Split('@')
    if ($selectorParts.Count -ne 2 -or $selectorParts[0] -ne "evidence-lane-plugin") {
        throw "The tunnel installed selector identity is invalid."
    }
    $expectedCacheRoot = [IO.Path]::GetFullPath(
        (Join-Path $env:USERPROFILE (
            ".codex\plugins\cache\" + $selectorParts[1] +
            "\evidence-lane-plugin\" + [string]$BoundMarker.plugin_version
        ))
    )
    $pluginRoot = [IO.Path]::GetFullPath([string]$BoundMarker.plugin_root)
    if (
        -not $pluginRoot.Equals($expectedCacheRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not $pluginRoot.Equals([IO.Path]::GetFullPath([string]$BoundMarker.installed_cache_root), [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "The tunnel is not bound to the exact installed selector cache root."
    }
    $boundFiles = @{
        plugin_manifest = [string](Join-Path $pluginRoot ".codex-plugin\plugin.json")
        executable_surface = [string]$BoundMarker.executable_surface_registry_path
        package_coherence = [string]$BoundMarker.package_surface_coherence_path
        source_manifest = [string]$BoundMarker.package_source_manifest_path
        installed_receipt = [string]$BoundMarker.installed_receipt_path
        tunnel_manifest = [string]$BoundMarker.tunnel_manifest_path
        runner = [string]$BoundMarker.installed_runner_path
        installed_installer = [string]$BoundMarker.installed_tunnel_installer_path
        installed_boot = [string](Join-Path $pluginRoot "scripts\windows_tunnel\EvidenceLaneTunnel.Boot.ps1")
        installed_manager = [string](Join-Path $pluginRoot "scripts\windows_tunnel\Manage-EvidenceLaneTunnel.ps1")
        installed_host = [string](Join-Path $pluginRoot "scripts\windows_tunnel\EvidenceLaneTunnelHost.exe")
        runtime_boot = [string]$PSCommandPath
        runtime_manager = [string](Join-Path $exactRuntimeRoot "Manage-EvidenceLaneTunnel.ps1")
        runtime_host = [string](Join-Path $exactRuntimeRoot "EvidenceLaneTunnelHost.exe")
    }
    foreach ($path in $boundFiles.Values) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "A tunnel-installed package proof is missing."
        }
    }
    if (
        (Get-Sha256 -Path $boundFiles.plugin_manifest) -ne [string]$BoundMarker.plugin_manifest_sha256 -or
        (Get-Sha256 -Path $boundFiles.executable_surface) -ne [string]$BoundMarker.executable_surface_registry_sha256 -or
        (Get-Sha256 -Path $boundFiles.package_coherence) -ne [string]$BoundMarker.package_surface_coherence_sha256 -or
        (Get-Sha256 -Path $boundFiles.source_manifest) -ne [string]$BoundMarker.package_source_manifest_sha256 -or
        (Get-Sha256 -Path $boundFiles.installed_receipt) -ne [string]$BoundMarker.installed_receipt_file_sha256 -or
        (Get-Sha256 -Path $boundFiles.tunnel_manifest) -ne [string]$BoundMarker.tunnel_manifest_sha256 -or
        (Get-Sha256 -Path $boundFiles.runner) -ne [string]$BoundMarker.installed_runner_sha256 -or
        (Get-Sha256 -Path $boundFiles.installed_installer) -ne [string]$BoundMarker.installed_tunnel_installer_sha256 -or
        (Get-Sha256 -Path $boundFiles.installed_boot) -ne [string]$BoundMarker.installed_tunnel_boot_sha256 -or
        (Get-Sha256 -Path $boundFiles.installed_manager) -ne [string]$BoundMarker.installed_tunnel_manager_sha256 -or
        (Get-Sha256 -Path $boundFiles.installed_host) -ne [string]$BoundMarker.installed_tunnel_host_sha256 -or
        (Get-Sha256 -Path $boundFiles.runtime_boot) -ne [string]$BoundMarker.runtime_tunnel_boot_sha256 -or
        (Get-Sha256 -Path $boundFiles.runtime_manager) -ne [string]$BoundMarker.runtime_tunnel_manager_sha256 -or
        (Get-Sha256 -Path $boundFiles.runtime_host) -ne [string]$BoundMarker.runtime_tunnel_host_sha256
    ) {
        throw "The exact installed plugin binding was modified after tunnel activation."
    }
    $manifest = Get-Content -LiteralPath $boundFiles.plugin_manifest -Raw | ConvertFrom-Json
    $surface = Get-Content -LiteralPath $boundFiles.executable_surface -Raw | ConvertFrom-Json
    $coherence = Get-Content -LiteralPath $boundFiles.package_coherence -Raw | ConvertFrom-Json
    $sourceManifest = Get-Content -LiteralPath $boundFiles.source_manifest -Raw | ConvertFrom-Json
    $receipt = Get-Content -LiteralPath $boundFiles.installed_receipt -Raw | ConvertFrom-Json
    $tunnelManifest = Get-Content -LiteralPath $boundFiles.tunnel_manifest -Raw | ConvertFrom-Json
    $manifestMember = @($sourceManifest.members | Where-Object { [string]$_.path -eq ".codex-plugin/plugin.json" })
    $surfaceMember = @($sourceManifest.members | Where-Object { [string]$_.path -eq "manifests/executable-surface-registry.v1.json" })
    $runnerMember = @($sourceManifest.members | Where-Object { [string]$_.path -eq "scripts/run_mcp.py" })
    if (
        [string]$manifest.version -ne [string]$BoundMarker.plugin_version -or
        [string]$surface.schema -ne "evidence-lane.executable-package-surface-registry.v1" -or
        [string]$surface.status -ne "PASS" -or
        [string]$surface.plugin_version -ne [string]$BoundMarker.plugin_version -or
        [string]$coherence.schema -ne "evidence-lane.package-surface-coherence.v1" -or
        [string]$coherence.status -ne "PASS" -or
        [string]$coherence.plugin_version -ne [string]$BoundMarker.plugin_version -or
        [string]$coherence.executable_surface_registry_sha256 -ne [string]$BoundMarker.executable_surface_registry_sha256 -or
        [string]$coherence.receipt_sha256 -ne [string]$BoundMarker.package_surface_coherence_receipt_sha256 -or
        $manifestMember.Count -ne 1 -or [string]$manifestMember[0].sha256 -ne [string]$BoundMarker.plugin_manifest_sha256 -or
        $surfaceMember.Count -ne 1 -or [string]$surfaceMember[0].sha256 -ne [string]$BoundMarker.executable_surface_registry_sha256 -or
        $runnerMember.Count -ne 1 -or [string]$runnerMember[0].sha256 -ne [string]$BoundMarker.installed_runner_sha256 -or
        [string]$receipt.status -ne "PASS" -or
        [string]$receipt.plugin.version -ne [string]$BoundMarker.plugin_version -or
        [string]$receipt.activation.plugin_selector -ne $selector -or
        -not ([IO.Path]::GetFullPath([string]$receipt.activation.installed_path).Equals($pluginRoot, [StringComparison]::OrdinalIgnoreCase)) -or
        [string]$receipt.plugin.manifest_sha256 -ne [string]$BoundMarker.plugin_manifest_sha256 -or
        [string]$receipt.archive_sha256 -ne [string]$BoundMarker.installed_package_archive_sha256 -or
        [string]$receipt.package_receipt_sha256 -ne [string]$BoundMarker.installed_package_receipt_sha256 -or
        [string]$receipt.receipt_sha256 -ne [string]$BoundMarker.installed_receipt_sha256 -or
        [string]$tunnelManifest.tunnel_compatibility_sha256 -ne [string]$BoundMarker.tunnel_compatibility_sha256
    ) {
        throw "The installed selector receipt and package surfaces no longer reconcile."
    }
}

Assert-InstalledCacheBinding -BoundMarker $marker

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
    if ($null -eq $process) {
        return $null
    }
    try {
        $processPath = (Resolve-Path -LiteralPath $process.Path).Path
        $clientPath = (Resolve-Path -LiteralPath $client).Path
        if ($processPath -ne $clientPath) {
            return $null
        }
        if ((Get-Sha256 -Path $processPath) -ne $expectedClientSha256) {
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
$actualClientSha256 = Get-Sha256 -Path $client
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
if ($null -ne $existing) {
    Write-OperatorEvent -Event "BOOT" -Status "RECOVER_STALE_PROCESS" -Detail "pid=$($existing.Id)"
    Stop-Process -Id $existing.Id
    Wait-Process -Id $existing.Id -Timeout 20 -ErrorAction SilentlyContinue
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

    Write-OperatorEvent -Event "DOCTOR" -Status "STARTED" -Detail "profile=$ProfileName"
    & $client doctor --profile-dir $ProfileDir --profile $ProfileName --json --explain *>> $operatorLog
    if ($LASTEXITCODE -ne 0) {
        throw "Tunnel doctor failed with exit code $LASTEXITCODE."
    }
    Write-OperatorEvent -Event "DOCTOR" -Status "PASS" -Detail "profile=$ProfileName"

    Write-OperatorEvent -Event "DAEMON" -Status "STARTED" -Detail "profile=$ProfileName"
    & $client run `
        --profile-dir $ProfileDir `
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
    if ($null -ne $secureKey) {
        $secureKey.Dispose()
    }
}
