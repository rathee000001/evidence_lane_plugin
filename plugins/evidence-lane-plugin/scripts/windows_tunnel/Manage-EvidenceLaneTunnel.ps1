[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Status", "Repair", "Remove")]
    [string]$Action,
    [string]$RuntimeControlRoot = "$env:USERPROFILE\.codex\plugins\runtime\evidence-lane-plugin",
    [string]$RuntimeRoot = "",
    [string]$ProfileName = "",
    [string]$ProfileDir = "$env:APPDATA\tunnel-client",
    [string]$ReleaseToken = "",
    [string]$TaskName = "",
    [int]$ReadyTimeoutSeconds = 90,
    [switch]$ConfirmRemoval
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

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = $PSScriptRoot
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

$markerFile = Join-Path $RuntimeRoot "evidence-lane-tunnel-installation.json"
$identityMarker = if (Test-Path -LiteralPath $markerFile -PathType Leaf) {
    Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
} else {
    $null
}
if ($null -ne $identityMarker) {
    if ([string]::IsNullOrWhiteSpace($ProfileName)) {
        $ProfileName = [string]$identityMarker.profile_name
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseToken)) {
        $ReleaseToken = [string]$identityMarker.release_token
    }
    if ([string]::IsNullOrWhiteSpace($TaskName)) {
        $TaskName = [string]$identityMarker.task_name
    }
}
if (
    [string]::IsNullOrWhiteSpace($ProfileName) -or
    [string]::IsNullOrWhiteSpace($ReleaseToken) -or
    [string]::IsNullOrWhiteSpace($TaskName)
) {
    throw "Versioned tunnel management requires an exact marker or explicit identity arguments."
}
$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$client = Join-Path $RuntimeRoot "bin\tunnel-client-v0.0.10.exe"
$filePrefix = [string]$identityMarker.file_prefix
$pidFile = [IO.Path]::GetFullPath([string]$identityMarker.pid_file)
$healthUrlFile = [IO.Path]::GetFullPath([string]$identityMarker.health_url_file)
$profileFile = Join-Path $ProfileDir ($ProfileName + ".yaml")

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
        runtime_boot = [string](Join-Path $exactRuntimeRoot "EvidenceLaneTunnel.Boot.ps1")
        runtime_manager = [string]$PSCommandPath
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
    $receipt = Get-Content -LiteralPath $boundFiles.installed_receipt -Raw | ConvertFrom-Json
    $tunnelManifest = Get-Content -LiteralPath $boundFiles.tunnel_manifest -Raw | ConvertFrom-Json
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

function Get-BoundMarker {
    param([switch]$SkipInstalledBindingValidation)
    if (-not (Test-Path -LiteralPath $markerFile -PathType Leaf)) {
        return $null
    }
    $marker = Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
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
        [IO.Path]::GetFullPath([string]$marker.runtime_root) -ne $exactRuntimeRoot -or
        [string]$marker.profile_name -ne $ProfileName -or
        [string]$marker.task_name -ne $TaskName -or
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
        [string]$marker.file_prefix -ne $filePrefix -or
        [bool]$marker.host_wide_project_neutral -ne $true -or
        [bool]$marker.per_project_or_task_tunnel_allowed -ne $false -or
        [bool]$marker.scheduled_task_transport_used -ne $true -or
        [int]$marker.exact_visible_tool_count -le 0 -or
        [int]$marker.exact_visible_tool_count -ne (
            [int]$marker.exact_active_read_tool_count +
            [int]$marker.exact_fail_closed_write_tool_count
        )
    ) {
        throw "The management request does not match the exact release-bound host-wide tunnel marker."
    }
    if (-not $SkipInstalledBindingValidation) {
        Assert-InstalledCacheBinding -BoundMarker $marker
    }
    return $marker
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
        if (
            $processPath -ne $clientPath -or
            (Get-Sha256 -Path $processPath) -ne $expectedClientSha256
        ) {
            return $null
        }
    }
    catch {
        return $null
    }
    return $process
}

function Stop-VerifiedTunnelProcess {
    $process = Get-VerifiedTunnelProcess
    if ($null -ne $process) {
        Stop-Process -Id $process.Id
        Wait-Process -Id $process.Id -Timeout 20 -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $healthUrlFile -Force -ErrorAction SilentlyContinue
}

function Get-TunnelStatus {
    param([switch]$SkipInstalledBindingValidation)
    $marker = Get-BoundMarker `
        -SkipInstalledBindingValidation:$SkipInstalledBindingValidation
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = if ($null -ne $task) {
        Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    } else {
        $null
    }
    $process = Get-VerifiedTunnelProcess
    $parsedPid = if ($null -ne $process) { $process.Id } else { $null }
    $binaryHashValid = $false
    if (Test-Path -LiteralPath $client -PathType Leaf) {
        $binaryHashValid = (Get-Sha256 -Path $client) -eq $expectedClientSha256
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
        release = if ($null -ne $marker) { [string]$marker.release } else { $null }
        release_token = if ($null -ne $marker) { [string]$marker.release_token } else { $ReleaseToken }
        plugin_version = if ($null -ne $marker) { [string]$marker.plugin_version } else { $null }
        tunnel_version_token = if ($null -ne $marker) { [string]$marker.tunnel_version_token } else { $null }
        runtime_identity_matches_release = if ($null -ne $marker) { [bool]$marker.runtime_identity_matches_release } else { $false }
        slot_role = if ($null -ne $marker) { [string]$marker.slot_role } else { $null }
        byte_frozen = if ($null -ne $marker) { [bool]$marker.byte_frozen } else { $false }
        task_name = $TaskName
        task_registered = $null -ne $task
        task_state = if ($null -ne $task) { [string]$task.State } else { $null }
        task_last_result = if ($null -ne $taskInfo) { $taskInfo.LastTaskResult } else { $null }
        trigger = "AT_LOGON_CURRENT_USER"
        scheduled_task_transport_used = $true
        pid = $parsedPid
        process_running = $null -ne $process
        stable_binary_hash_valid = $binaryHashValid
        control_plane_poll_ready = $ready
        profile_file = $profileFile
        profile_exists = Test-Path -LiteralPath $profileFile -PathType Leaf
        exposure_profile = "CODEX_INTERACTIVE_SUPPORT"
        transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"
        host_wide_project_neutral = $true
        multi_project_and_task_routing = "EXPLICIT_PLUGIN_PROJECT_ID_AND_TASK_BINDINGS"
        per_project_or_task_tunnel_allowed = $false
        codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"
        codex_tunnel_lifecycle_proof_allowed = $false
        project_binding = "NONE_TRANSPORT_ONLY"
        project_route_argument = "project_id"
        project_route_argument_required = $true
        cross_project_fallback_allowed = $false
        exact_visible_tool_count = if ($null -ne $marker) { [int]$marker.exact_visible_tool_count } else { 0 }
        exact_active_read_tool_count = if ($null -ne $marker) { [int]$marker.exact_active_read_tool_count } else { 0 }
        exact_fail_closed_write_tool_count = if ($null -ne $marker) { [int]$marker.exact_fail_closed_write_tool_count } else { 0 }
        exact_skill_count = if ($null -ne $marker) { [int]$marker.exact_skill_count } else { 0 }
        separate_command_layer_present = if ($null -ne $marker) { [bool]$marker.separate_command_layer_present } else { $false }
        exact_hook_event_count = if ($null -ne $marker) { [int]$marker.exact_hook_event_count } else { 0 }
        exact_hook_handler_count = if ($null -ne $marker) { [int]$marker.exact_hook_handler_count } else { 0 }
        exact_provider_count = if ($null -ne $marker) { [int]$marker.exact_provider_count } else { 0 }
        health_url_file = $healthUrlFile
        runtime_key_plaintext_reported = $false
        windows_console_policy = "WINDOWS_GUI_HOST_HIDDEN_NO_TRANSIENT_CONSOLE"
        prior_versioned_runtimes_retained = if ($null -ne $marker) { [bool]$marker.prior_versioned_runtimes_retained } else { $false }
        prior_versioned_tasks_retained = if ($null -ne $marker) { [bool]$marker.prior_versioned_tasks_retained } else { $false }
        prior_versioned_tasks_disabled = if ($null -ne $marker) { [bool]$marker.prior_versioned_tasks_disabled } else { $false }
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
    $priorStatus = Get-TunnelStatus -SkipInstalledBindingValidation
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-VerifiedTunnelProcess
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
    [ordered]@{
        status = "STOPPED_SAVED"
        release = $priorStatus.release
        release_token = $priorStatus.release_token
        task_name = $TaskName
        runtime_root = $exactRuntimeRoot
        reusable_without_reinstall = $true
    } | ConvertTo-Json -Depth 4
    exit 0
}

if ($Action -eq "Remove") {
    if (-not $ConfirmRemoval) {
        throw "Removal is fail-closed. Repeat with -ConfirmRemoval after reviewing the exact runtime root."
    }
    $marker = Get-BoundMarker
    if ($null -eq $marker) {
        throw "Refusing removal because the Evidence Lane installation marker is missing."
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-VerifiedTunnelProcess
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
    throw "The host-wide at-logon tunnel task is missing. Run Install-EvidenceLaneTunnel.ps1 first."
}
Enable-ScheduledTask -TaskName $TaskName | Out-Null

$before = Get-TunnelStatus
if ($before.control_plane_poll_ready -and $before.task_state -eq "Running") {
    $before | ConvertTo-Json -Depth 4
    exit 0
}
if ($Action -eq "Repair") {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-VerifiedTunnelProcess
}

Start-ScheduledTask -TaskName $TaskName
$after = Wait-TunnelReady
$after | ConvertTo-Json -Depth 4
exit $(if ($after.control_plane_poll_ready) { 0 } else { 1 })
