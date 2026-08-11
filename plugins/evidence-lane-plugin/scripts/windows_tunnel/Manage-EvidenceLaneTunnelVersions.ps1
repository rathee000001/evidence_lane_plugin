[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Register", "List", "VerifyCandidate", "Promote", "Activate")]
    [string]$Action,
    [string]$Release = "",
    [string]$RuntimeRoot = "",
    [ValidateSet("stable", "future-test", "archive")]
    [string]$Channel = "future-test",
    [string]$DataRoot = "$env:USERPROFILE\EvidenceLanePV",
    [int]$ReadyTimeoutSeconds = 90,
    [string]$HealthReceiptSha256 = "",
    [string]$PublicRouteReceiptSha256 = "",
    [string]$HostProofReceiptSha256 = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$registrySchema = "evidence-lane.tunnel-version-registry.v1"
$expectedClientSha256 = "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8"
$exactDataRoot = [IO.Path]::GetFullPath($DataRoot)
$registryRoot = Join-Path $exactDataRoot "tunnel-versions"
$registryFile = Join-Path $registryRoot "registry.json"

function Read-VersionRegistry {
    if (-not (Test-Path -LiteralPath $registryFile -PathType Leaf)) {
        return [ordered]@{
            schema = $registrySchema
            active_release = $null
            stable_release = $null
            future_test_release = $null
            archive_release = $null
            event_sequence = 0
            event_head_sha256 = $null
            events = @()
            versions = @()
        }
    }
    $registry = Get-Content -LiteralPath $registryFile -Raw | ConvertFrom-Json
    if ([string]$registry.schema -ne $registrySchema) {
        throw "The tunnel version registry schema is unsupported."
    }
    $activeReleaseProperty = $registry.PSObject.Properties["active_release"]
    $stableReleaseProperty = $registry.PSObject.Properties["stable_release"]
    $futureTestReleaseProperty = $registry.PSObject.Properties["future_test_release"]
    $archiveReleaseProperty = $registry.PSObject.Properties["archive_release"]
    $eventSequenceProperty = $registry.PSObject.Properties["event_sequence"]
    $eventHeadProperty = $registry.PSObject.Properties["event_head_sha256"]
    $eventsProperty = $registry.PSObject.Properties["events"]
    $versionsProperty = $registry.PSObject.Properties["versions"]
    $activeRelease = if ($null -ne $activeReleaseProperty -and $null -ne $activeReleaseProperty.Value) { [string]$activeReleaseProperty.Value } else { $null }
    return [ordered]@{
        schema = $registrySchema
        active_release = $activeRelease
        stable_release = if ($null -ne $stableReleaseProperty -and $null -ne $stableReleaseProperty.Value) { [string]$stableReleaseProperty.Value } else { $activeRelease }
        future_test_release = if ($null -ne $futureTestReleaseProperty -and $null -ne $futureTestReleaseProperty.Value) { [string]$futureTestReleaseProperty.Value } else { $null }
        archive_release = if ($null -ne $archiveReleaseProperty -and $null -ne $archiveReleaseProperty.Value) { [string]$archiveReleaseProperty.Value } else { $null }
        event_sequence = if ($null -ne $eventSequenceProperty -and $null -ne $eventSequenceProperty.Value) { [int]$eventSequenceProperty.Value } else { 0 }
        event_head_sha256 = if ($null -ne $eventHeadProperty -and $null -ne $eventHeadProperty.Value) { [string]$eventHeadProperty.Value } else { $null }
        events = if ($null -ne $eventsProperty -and $null -ne $eventsProperty.Value) { @($eventsProperty.Value) } else { @() }
        versions = if ($null -ne $versionsProperty -and $null -ne $versionsProperty.Value) { @($versionsProperty.Value) } else { @() }
    }
}

function Write-VersionRegistry {
    param([Parameter(Mandatory = $true)]$Registry)

    New-Item -ItemType Directory -Path $registryRoot -Force | Out-Null
    $temporary = $registryFile + ".tmp"
    $Registry | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $registryFile -Force
}

function Add-VersionEvent {
    param(
        [Parameter(Mandatory = $true)]$Registry,
        [Parameter(Mandatory = $true)][string]$EventType,
        [Parameter(Mandatory = $true)][string]$ExactRelease,
        [string]$PreviousRelease = "",
        [string]$Detail = ""
    )

    $event = [ordered]@{
        schema = "evidence-lane.tunnel-version-event.v1"
        sequence = [int]$Registry.event_sequence + 1
        event_type = $EventType
        release = $ExactRelease
        previous_release = if ([string]::IsNullOrWhiteSpace($PreviousRelease)) { $null } else { $PreviousRelease }
        detail = $Detail
        recorded_at = [DateTimeOffset]::UtcNow.ToString("o")
        previous_event_sha256 = $Registry.event_head_sha256
    }
    $eventBytes = [Text.Encoding]::UTF8.GetBytes(($event | ConvertTo-Json -Depth 6 -Compress))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $eventHash = [BitConverter]::ToString($hasher.ComputeHash($eventBytes)).Replace("-", "")
    }
    finally {
        $hasher.Dispose()
    }
    $event.event_sha256 = $eventHash
    $Registry.events = @($Registry.events) + @($event)
    $Registry.event_sequence = $event.sequence
    $Registry.event_head_sha256 = $eventHash
}

function Get-ReleaseToken {
    param([Parameter(Mandatory = $true)][string]$ExactRelease)

    if ($ExactRelease -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
        throw "A saved tunnel release must use exact major.minor.patch versioning."
    }
    return "v$($Matches[1])$($Matches[2])$($Matches[3])"
}

function Read-InstallationMarker {
    param([Parameter(Mandatory = $true)][string]$ExactRuntimeRoot)

    $fullRuntimeRoot = [IO.Path]::GetFullPath($ExactRuntimeRoot)
    $approvedParent = $exactDataRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $fullRuntimeRoot.StartsWith($approvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "A saved tunnel runtime must remain inside the configured EvidenceLanePV data root."
    }
    $markerFile = Join-Path $fullRuntimeRoot "evidence-lane-tunnel-installation.json"
    if (-not (Test-Path -LiteralPath $markerFile -PathType Leaf)) {
        throw "The saved tunnel installation marker is missing."
    }
    $marker = Get-Content -LiteralPath $markerFile -Raw | ConvertFrom-Json
    if ([string]$marker.schema -notin @(
        "evidence-lane.chatgpt-read-tunnel-installation.v1",
        "evidence-lane.chatgpt-governed-tunnel-installation.v1",
        "evidence-lane.versioned-secure-mcp-tunnel-installation.v1"
    )) {
        throw "The saved tunnel installation marker schema is unsupported."
    }
    if ([IO.Path]::GetFullPath([string]$marker.runtime_root) -ne $fullRuntimeRoot) {
        throw "The saved tunnel marker does not bind its exact runtime root."
    }
    return [ordered]@{
        marker = $marker
        marker_file = $markerFile
        runtime_root = $fullRuntimeRoot
    }
}

function New-VersionEntry {
    param(
        [Parameter(Mandatory = $true)][string]$ExactRuntimeRoot,
        [Parameter(Mandatory = $true)][ValidateSet("stable", "future-test", "archive")][string]$ExactChannel
    )

    $installation = Read-InstallationMarker -ExactRuntimeRoot $ExactRuntimeRoot
    $marker = $installation.marker
    $exactRelease = [string]$marker.release
    $token = Get-ReleaseToken -ExactRelease $exactRelease
    $client = Join-Path $installation.runtime_root "bin\tunnel-client-v0.0.10.exe"
    if (-not (Test-Path -LiteralPath $client -PathType Leaf)) {
        throw "The saved tunnel client is missing."
    }
    if ((Get-FileHash -LiteralPath $client -Algorithm SHA256).Hash -ne $expectedClientSha256) {
        throw "The saved tunnel client does not match the pinned v0.0.10 authority."
    }
    $task = Get-ScheduledTask -TaskName ([string]$marker.task_name) -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        throw "The saved tunnel scheduled task is missing."
    }
    $tunnelIdBytes = [Text.Encoding]::UTF8.GetBytes([string]$marker.tunnel_id)
    $tunnelIdHasher = [Security.Cryptography.SHA256]::Create()
    try {
        $tunnelIdSha256 = [BitConverter]::ToString($tunnelIdHasher.ComputeHash($tunnelIdBytes)).Replace("-", "")
    }
    finally {
        $tunnelIdHasher.Dispose()
    }
    return [ordered]@{
        release = $exactRelease
        channel = $ExactChannel
        runtime_root = $installation.runtime_root
        marker_file = $installation.marker_file
        profile_name = [string]$marker.profile_name
        profile_file = [string]$marker.profile_file
        task_name = [string]$marker.task_name
        plugin_root = [string]$marker.plugin_root
        exposure_profile = [string]$marker.exposure_profile
        stable_client = $client
        stable_client_sha256 = $expectedClientSha256
        tunnel_id_sha256 = $tunnelIdSha256
        pid_file = Join-Path $installation.runtime_root "evidence_lane_${token}_tunnel.pid"
        health_url_file = Join-Path $installation.runtime_root "evidence_lane_${token}_health.url"
        registered_at = [DateTimeOffset]::UtcNow.ToString("o")
        promotion_receipts = $null
        secret_material_in_registry = $false
    }
}

function Get-VerifiedTunnelProcess {
    param([Parameter(Mandatory = $true)]$Entry)

    if (-not (Test-Path -LiteralPath ([string]$Entry.pid_file) -PathType Leaf)) {
        return $null
    }
    $rawPid = (Get-Content -LiteralPath ([string]$Entry.pid_file) -Raw).Trim()
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
        $clientPath = (Resolve-Path -LiteralPath ([string]$Entry.stable_client)).Path
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

function Get-VersionStatus {
    param([Parameter(Mandatory = $true)]$Entry)

    $task = Get-ScheduledTask -TaskName ([string]$Entry.task_name) -ErrorAction SilentlyContinue
    $process = Get-VerifiedTunnelProcess -Entry $Entry
    $ready = $false
    if ($null -ne $process -and (Test-Path -LiteralPath ([string]$Entry.health_url_file) -PathType Leaf)) {
        & ([string]$Entry.stable_client) health `
            --url-file ([string]$Entry.health_url_file) `
            --pid $process.Id `
            --require-control-plane-poll `
            --json *> $null
        $ready = $LASTEXITCODE -eq 0
    }
    return [ordered]@{
        release = [string]$Entry.release
        channel = if ($null -ne $Entry.PSObject.Properties["channel"]) { [string]$Entry.channel } else { "archive" }
        runtime_root = [string]$Entry.runtime_root
        task_name = [string]$Entry.task_name
        task_state = if ($null -ne $task) { [string]$task.State } else { "MISSING" }
        process_id = if ($null -ne $process) { $process.Id } else { $null }
        ready = $ready
        reusable_without_reinstall = $true
    }
}

function Stop-SavedVersion {
    param([Parameter(Mandatory = $true)]$Entry)

    Stop-ScheduledTask -TaskName ([string]$Entry.task_name) -ErrorAction SilentlyContinue
    $process = Get-VerifiedTunnelProcess -Entry $Entry
    if ($null -ne $process) {
        Stop-Process -Id $process.Id
        Wait-Process -Id $process.Id -Timeout 20 -ErrorAction SilentlyContinue
    }
    Disable-ScheduledTask -TaskName ([string]$Entry.task_name) -ErrorAction SilentlyContinue | Out-Null
    Remove-Item -LiteralPath ([string]$Entry.pid_file) -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ([string]$Entry.health_url_file) -Force -ErrorAction SilentlyContinue
}

function Start-SavedVersion {
    param([Parameter(Mandatory = $true)]$Entry)

    Enable-ScheduledTask -TaskName ([string]$Entry.task_name) | Out-Null
    Start-ScheduledTask -TaskName ([string]$Entry.task_name)
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        $status = Get-VersionStatus -Entry $Entry
        if ($status.ready) {
            return $status
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    return Get-VersionStatus -Entry $Entry
}

function Assert-ReceiptHash {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ($Value -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "$Name must be one exact SHA-256 receipt identity."
    }
    return $Value.ToUpperInvariant()
}

function Get-RegisteredRelease {
    param(
        [Parameter(Mandatory = $true)]$Registry,
        [Parameter(Mandatory = $true)][string]$ExactRelease
    )

    $matches = @($Registry.versions | Where-Object { [string]$_.release -eq $ExactRelease })
    if ($matches.Count -ne 1) {
        throw "The requested tunnel release is not registered exactly once."
    }
    return $matches[0]
}

function Set-EntryChannel {
    param(
        [Parameter(Mandatory = $true)]$Entry,
        [Parameter(Mandatory = $true)][ValidateSet("stable", "future-test", "archive")][string]$ExactChannel
    )

    $channelProperty = $Entry.PSObject.Properties["channel"]
    if ($null -eq $channelProperty) {
        Add-Member -InputObject $Entry -NotePropertyName "channel" -NotePropertyValue $ExactChannel
    }
    else {
        $Entry.channel = $ExactChannel
    }
}

function Set-PromotionReceipts {
    param(
        [Parameter(Mandatory = $true)]$Entry,
        [Parameter(Mandatory = $true)]$Receipts
    )

    $receiptProperty = $Entry.PSObject.Properties["promotion_receipts"]
    if ($null -eq $receiptProperty) {
        Add-Member -InputObject $Entry -NotePropertyName "promotion_receipts" -NotePropertyValue $Receipts
    }
    else {
        $Entry.promotion_receipts = $Receipts
    }
}

function Get-ChannelSnapshot {
    param([Parameter(Mandatory = $true)]$Registry)

    return [ordered]@{
        stable = $Registry.stable_release
        future_test = $Registry.future_test_release
        archive_fallback = $Registry.archive_release
    }
}

function Start-IsolatedCandidate {
    param(
        [Parameter(Mandatory = $true)]$Registry,
        [Parameter(Mandatory = $true)]$Entry
    )

    if ([string]$Entry.release -eq [string]$Registry.stable_release) {
        throw "The stable release cannot be verified as its own future-test candidate."
    }
    Set-EntryChannel -Entry $Entry -ExactChannel "future-test"
    $status = Get-VersionStatus -Entry $Entry
    if (-not $status.ready) {
        Stop-SavedVersion -Entry $Entry
        $status = Start-SavedVersion -Entry $Entry
    }
    if (-not $status.ready) {
        Stop-SavedVersion -Entry $Entry
        throw "The isolated future-test tunnel did not reach control-plane readiness; stable was not touched."
    }
    $Registry.future_test_release = [string]$Entry.release
    return $status
}

# Channel-aware dispatcher. The earlier single-active-release dispatcher is
# retained below only as unreachable source history for saved v1.3/v1.4
# runtimes. Every current invocation exits through this fail-closed path.
$channelRegistry = Read-VersionRegistry
if ([string]::IsNullOrWhiteSpace([string]$channelRegistry.archive_release)) {
    $latestSavedFallback = @(
        $channelRegistry.versions |
            Where-Object { [string]$_.release -ne [string]$channelRegistry.stable_release } |
            Sort-Object { [version]$_.release } -Descending |
            Select-Object -First 1
    )
    if ($latestSavedFallback.Count -eq 1) {
        $channelRegistry.archive_release = [string]$latestSavedFallback[0].release
    }
}
foreach ($savedEntry in $channelRegistry.versions) {
    $savedChannel = if ([string]$savedEntry.release -eq [string]$channelRegistry.stable_release) {
        "stable"
    }
    elseif ([string]$savedEntry.release -eq [string]$channelRegistry.future_test_release) {
        "future-test"
    }
    else {
        "archive"
    }
    Set-EntryChannel -Entry $savedEntry -ExactChannel $savedChannel
}

if ($Action -eq "Register") {
    if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
        throw "Register requires one exact saved RuntimeRoot."
    }
    if ($Channel -eq "stable" -and -not [string]::IsNullOrWhiteSpace([string]$channelRegistry.stable_release)) {
        throw "A newly registered release cannot overwrite the stable channel. Register it as future-test."
    }
    $channelEntry = New-VersionEntry -ExactRuntimeRoot $RuntimeRoot -ExactChannel $Channel
    $priorEntries = @($channelRegistry.versions | Where-Object { [string]$_.release -eq [string]$channelEntry.release })
    if ($priorEntries.Count -gt 0) {
        $channelEntry.registered_at = [string]$priorEntries[0].registered_at
        if ($null -ne $priorEntries[0].PSObject.Properties["promotion_receipts"]) {
            $channelEntry.promotion_receipts = $priorEntries[0].promotion_receipts
        }
    }
    $channelRegistry.versions = @(
        @($channelRegistry.versions | Where-Object { [string]$_.release -ne [string]$channelEntry.release })
        $channelEntry
    ) | Sort-Object { [version]$_.release }
    if ($Channel -eq "future-test") {
        $channelRegistry.future_test_release = [string]$channelEntry.release
    }
    elseif ($Channel -eq "archive") {
        $channelRegistry.archive_release = [string]$channelEntry.release
    }
    Add-VersionEvent `
        -Registry $channelRegistry `
        -EventType "REGISTERED_SAVED_VERSION" `
        -ExactRelease ([string]$channelEntry.release) `
        -PreviousRelease ([string]$channelRegistry.stable_release) `
        -Detail "Versioned marker, task, profile, tunnel identity hash, runtime root, and pinned client verified; channel=$Channel."
    Write-VersionRegistry -Registry $channelRegistry
    [ordered]@{
        status = "REGISTERED"
        release = [string]$channelEntry.release
        channel = $Channel
        channels = Get-ChannelSnapshot -Registry $channelRegistry
        stable_untouched = $true
        registry_file = $registryFile
        reusable_without_reinstall = $true
        secret_material_in_registry = $false
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq "List") {
    [ordered]@{
        status = "PASS"
        registry_file = $registryFile
        active_release = $channelRegistry.stable_release
        channels = Get-ChannelSnapshot -Registry $channelRegistry
        versions = @($channelRegistry.versions | ForEach-Object { Get-VersionStatus -Entry $_ })
        event_sequence = $channelRegistry.event_sequence
        event_head_sha256 = $channelRegistry.event_head_sha256
        candidate_can_interrupt_stable = $false
        archive_reusable_without_reinstall = $true
        secret_material_in_registry = $false
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Release)) {
    throw "$Action requires one exact registered Release."
}
$channelTarget = Get-RegisteredRelease -Registry $channelRegistry -ExactRelease $Release
$targetChannel = if ($null -ne $channelTarget.PSObject.Properties["channel"]) { [string]$channelTarget.channel } else { "archive" }
New-VersionEntry -ExactRuntimeRoot ([string]$channelTarget.runtime_root) -ExactChannel $targetChannel | Out-Null

if ($Action -eq "VerifyCandidate") {
    Add-VersionEvent `
        -Registry $channelRegistry `
        -EventType "FUTURE_TEST_STARTED" `
        -ExactRelease $Release `
        -PreviousRelease ([string]$channelRegistry.stable_release) `
        -Detail "Candidate starts on its independent task, profile, tunnel ID, and route while stable remains running."
    Write-VersionRegistry -Registry $channelRegistry
    try {
        $candidateStatus = Start-IsolatedCandidate -Registry $channelRegistry -Entry $channelTarget
        Add-VersionEvent `
            -Registry $channelRegistry `
            -EventType "FUTURE_TEST_READY" `
            -ExactRelease $Release `
            -PreviousRelease ([string]$channelRegistry.stable_release) `
            -Detail "Pinned candidate process and control-plane poll are ready; stable was never stopped."
        Write-VersionRegistry -Registry $channelRegistry
        [ordered]@{
            status = "FUTURE_TEST_READY"
            release = $Release
            candidate = $candidateStatus
            channels = Get-ChannelSnapshot -Registry $channelRegistry
            stable_untouched = $true
            promotion_performed = $false
            secret_material_in_registry = $false
        } | ConvertTo-Json -Depth 8
        exit 0
    }
    catch {
        $candidateFailure = $_.Exception.Message
        Stop-SavedVersion -Entry $channelTarget
        Add-VersionEvent `
            -Registry $channelRegistry `
            -EventType "FUTURE_TEST_FAILED_STABLE_UNTOUCHED" `
            -ExactRelease $Release `
            -PreviousRelease ([string]$channelRegistry.stable_release) `
            -Detail $candidateFailure
        Write-VersionRegistry -Registry $channelRegistry
        throw
    }
}

if ($Action -eq "Promote") {
    if ([string]$channelRegistry.future_test_release -ne $Release) {
        throw "Only the exact verified future-test release may be promoted."
    }
    $candidateStatus = Get-VersionStatus -Entry $channelTarget
    if (-not $candidateStatus.ready) {
        throw "Promotion is blocked because the future-test tunnel is not currently ready; stable remains untouched."
    }
    $healthReceipt = Assert-ReceiptHash -Name "HealthReceiptSha256" -Value $HealthReceiptSha256
    $publicReceipt = Assert-ReceiptHash -Name "PublicRouteReceiptSha256" -Value $PublicRouteReceiptSha256
    $hostReceipt = Assert-ReceiptHash -Name "HostProofReceiptSha256" -Value $HostProofReceiptSha256
    $oldStableRelease = [string]$channelRegistry.stable_release
    $oldStableEntry = if ([string]::IsNullOrWhiteSpace($oldStableRelease)) { $null } else { Get-RegisteredRelease -Registry $channelRegistry -ExactRelease $oldStableRelease }
    if ($null -ne $oldStableEntry) {
        $oldStableStatus = Get-VersionStatus -Entry $oldStableEntry
        if (-not $oldStableStatus.ready) {
            throw "Promotion is blocked because the recorded stable route is not ready."
        }
    }
    $promotionReceipts = [ordered]@{
        health_sha256 = $healthReceipt
        public_route_sha256 = $publicReceipt
        host_proof_sha256 = $hostReceipt
        sealed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Set-PromotionReceipts -Entry $channelTarget -Receipts $promotionReceipts
    Add-VersionEvent `
        -Registry $channelRegistry `
        -EventType "PROMOTION_STARTED_STABLE_STILL_READY" `
        -ExactRelease $Release `
        -PreviousRelease $oldStableRelease `
        -Detail "Three exact promotion receipt hashes accepted; candidate and stable are both ready before channel commit."
    Write-VersionRegistry -Registry $channelRegistry

    if ($null -ne $oldStableEntry) {
        Set-EntryChannel -Entry $oldStableEntry -ExactChannel "archive"
    }
    Set-EntryChannel -Entry $channelTarget -ExactChannel "stable"
    $channelRegistry.archive_release = if ($null -ne $oldStableEntry) { $oldStableRelease } else { $channelRegistry.archive_release }
    $channelRegistry.stable_release = $Release
    $channelRegistry.active_release = $Release
    $channelRegistry.future_test_release = $null
    Add-VersionEvent `
        -Registry $channelRegistry `
        -EventType "PROMOTED_TO_STABLE" `
        -ExactRelease $Release `
        -PreviousRelease $oldStableRelease `
        -Detail "Atomic registry channel commit completed only after candidate, stable, health, public-route, and host proof passed."
    Write-VersionRegistry -Registry $channelRegistry

    if ($null -ne $oldStableEntry) {
        Stop-SavedVersion -Entry $oldStableEntry
        Add-VersionEvent `
            -Registry $channelRegistry `
            -EventType "MOVED_TO_ARCHIVE" `
            -ExactRelease $oldStableRelease `
            -PreviousRelease $Release `
            -Detail "Displaced stable runtime retained intact, disabled, and reusable without reinstall."
        Write-VersionRegistry -Registry $channelRegistry
    }
    [ordered]@{
        status = "PROMOTED_TO_STABLE"
        release = $Release
        previous_stable = if ([string]::IsNullOrWhiteSpace($oldStableRelease)) { $null } else { $oldStableRelease }
        channels = Get-ChannelSnapshot -Registry $channelRegistry
        candidate_ready_before_switch = $true
        stable_ready_before_switch = $true
        archive_reusable_without_reinstall = $true
        receipts = $promotionReceipts
        secret_material_in_registry = $false
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq "Activate") {
    if ([string]$channelRegistry.stable_release -eq $Release) {
        $stableStatus = Get-VersionStatus -Entry $channelTarget
        if (-not $stableStatus.ready) {
            $stableStatus = Start-SavedVersion -Entry $channelTarget
        }
        if (-not $stableStatus.ready) {
            throw "The recorded stable release did not reach readiness."
        }
        [ordered]@{
            status = "STABLE_READY"
            release = $Release
            channels = Get-ChannelSnapshot -Registry $channelRegistry
            stable = $stableStatus
            secret_material_in_registry = $false
        } | ConvertTo-Json -Depth 8
        exit 0
    }
    if ([string]$channelRegistry.archive_release -ne $Release) {
        throw "Activate may restart the current stable or explicitly reactivate the proven archive; future-test promotion requires Promote and three receipts."
    }
    if ($null -eq $channelTarget.PSObject.Properties["promotion_receipts"] -or $null -eq $channelTarget.promotion_receipts) {
        throw "The archive has no sealed prior promotion receipts and cannot be reactivated as fallback."
    }
    $fallbackStatus = Get-VersionStatus -Entry $channelTarget
    if (-not $fallbackStatus.ready) {
        $fallbackStatus = Start-SavedVersion -Entry $channelTarget
    }
    if (-not $fallbackStatus.ready) {
        Stop-SavedVersion -Entry $channelTarget
        throw "Archive fallback readiness failed; current stable remains untouched."
    }
    $displacedStableRelease = [string]$channelRegistry.stable_release
    $displacedStable = Get-RegisteredRelease -Registry $channelRegistry -ExactRelease $displacedStableRelease
    Set-EntryChannel -Entry $displacedStable -ExactChannel "archive"
    Set-EntryChannel -Entry $channelTarget -ExactChannel "stable"
    $channelRegistry.stable_release = $Release
    $channelRegistry.active_release = $Release
    $channelRegistry.archive_release = $displacedStableRelease
    Add-VersionEvent `
        -Registry $channelRegistry `
        -EventType "ROLLBACK_ACTIVATED" `
        -ExactRelease $Release `
        -PreviousRelease $displacedStableRelease `
        -Detail "Previously proven archive became ready before the prior stable was disabled."
    Write-VersionRegistry -Registry $channelRegistry
    Stop-SavedVersion -Entry $displacedStable
    [ordered]@{
        status = "FALLBACK_ACTIVATED"
        release = $Release
        channels = Get-ChannelSnapshot -Registry $channelRegistry
        fallback_ready_before_switch = $true
        displaced_stable_reusable_without_reinstall = $true
        secret_material_in_registry = $false
    } | ConvertTo-Json -Depth 8
    exit 0
}

throw "Unsupported channel action."

<#
Legacy v1.5.0 pre-channel dispatcher retained as non-executable migration
evidence. Saved v1.3/v1.4 runtime copies remain independently reusable.
$registry = Read-VersionRegistry

if ($Action -eq "Register") {
    if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
        throw "Register requires one exact saved RuntimeRoot."
    }
    $entry = New-VersionEntry -ExactRuntimeRoot $RuntimeRoot
    $prior = @($registry.versions | Where-Object { [string]$_.release -eq [string]$entry.release })
    if ($prior.Count -gt 0) {
        $entry.registered_at = [string]$prior[0].registered_at
    }
    $registry.versions = @(
        @($registry.versions | Where-Object { [string]$_.release -ne [string]$entry.release })
        $entry
    ) | Sort-Object { [version]$_.release }
    $status = Get-VersionStatus -Entry $entry
    if ($status.ready) {
        $registry.active_release = [string]$entry.release
    }
    Add-VersionEvent `
        -Registry $registry `
        -EventType "REGISTERED_SAVED_VERSION" `
        -ExactRelease ([string]$entry.release) `
        -PreviousRelease ([string]$registry.active_release) `
        -Detail "Marker, task, profile, runtime root, and pinned client verified."
    Write-VersionRegistry -Registry $registry
    [ordered]@{
        status = "REGISTERED"
        release = [string]$entry.release
        active_release = $registry.active_release
        registry_file = $registryFile
        reusable_without_reinstall = $true
        secret_material_in_registry = $false
    } | ConvertTo-Json -Depth 6
    exit 0
}

if ($Action -eq "List") {
    [ordered]@{
        status = "PASS"
        registry_file = $registryFile
        active_release = $registry.active_release
        versions = @($registry.versions | ForEach-Object { Get-VersionStatus -Entry $_ })
        event_sequence = $registry.event_sequence
        event_head_sha256 = $registry.event_head_sha256
        secret_material_in_registry = $false
    } | ConvertTo-Json -Depth 6
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Release)) {
    throw "Activate requires one exact registered Release."
}
$target = @($registry.versions | Where-Object { [string]$_.release -eq $Release })
if ($target.Count -ne 1) {
    throw "The requested tunnel release is not registered exactly once."
}
$target = $target[0]
New-VersionEntry -ExactRuntimeRoot ([string]$target.runtime_root) | Out-Null
$previousRelease = $registry.active_release
$previous = @($registry.versions | Where-Object { [string]$_.release -eq [string]$previousRelease })
Add-VersionEvent `
    -Registry $registry `
    -EventType "ACTIVATION_STARTED" `
    -ExactRelease $Release `
    -PreviousRelease ([string]$previousRelease) `
    -Detail "Exactly one saved tunnel may remain active."
Write-VersionRegistry -Registry $registry

try {
    foreach ($entry in $registry.versions) {
        if ([string]$entry.release -ne $Release) {
            Stop-SavedVersion -Entry $entry
        }
    }
    $targetStatus = Get-VersionStatus -Entry $target
    if (-not $targetStatus.ready) {
        Stop-SavedVersion -Entry $target
        $targetStatus = Start-SavedVersion -Entry $target
    }
    if (-not $targetStatus.ready) {
        throw "The requested tunnel release did not reach control-plane readiness."
    }
    $registry.active_release = $Release
    Add-VersionEvent `
        -Registry $registry `
        -EventType "ACTIVATED" `
        -ExactRelease $Release `
        -PreviousRelease ([string]$previousRelease) `
        -Detail "Pinned process and control-plane poll are ready."
    Write-VersionRegistry -Registry $registry
}
catch {
    $failureDetail = $_.Exception.Message
    Stop-SavedVersion -Entry $target
    Add-VersionEvent `
        -Registry $registry `
        -EventType "ACTIVATION_FAILED" `
        -ExactRelease $Release `
        -PreviousRelease ([string]$previousRelease) `
        -Detail $failureDetail
    if ($previous.Count -eq 1 -and [string]$previous[0].release -ne $Release) {
        $rollback = Start-SavedVersion -Entry $previous[0]
        if ($rollback.ready) {
            $registry.active_release = [string]$previous[0].release
            Add-VersionEvent `
                -Registry $registry `
                -EventType "ROLLBACK_ACTIVATED" `
                -ExactRelease ([string]$previous[0].release) `
                -PreviousRelease $Release `
                -Detail "Previous saved tunnel restored after failed activation."
        }
    }
    Write-VersionRegistry -Registry $registry
    throw
}

[ordered]@{
    status = "PASS"
    active_release = $registry.active_release
    previous_release = $previousRelease
    rollback_on_failed_activation = $true
    versions = @($registry.versions | ForEach-Object { Get-VersionStatus -Entry $_ })
    event_sequence = $registry.event_sequence
    event_head_sha256 = $registry.event_head_sha256
    reusable_without_reinstall = $true
    secret_material_in_registry = $false
} | ConvertTo-Json -Depth 6
#>
