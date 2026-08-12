[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Prepare", "Switch", "Verify")]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [ValidateSet("stable-build", "fallback")]
    [string]$TargetSlot,
    [Parameter(Mandatory = $true)]
    [string]$Registry,
    [string]$RegistrySha256,
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceSessionId,
    [Parameter(Mandatory = $true)]
    [string]$TaskId,
    [Parameter(Mandatory = $true)]
    [string]$HostSessionId,
    [ValidateSet(
        "EXPLICIT_OPERATOR_FAILOVER",
        "DETERMINISTIC_STABLE_HEALTH_FAILURE",
        "VERIFIED_STABLE_REPAIR"
    )]
    [string]$Reason,
    [string]$DecisionReceipt,
    [string]$DecisionReceiptSha256,
    [int]$TargetProcessId,
    [string]$PreparationReceipt,
    [string]$PreparationReceiptSha256,
    [string]$CodexConfig = "$env:USERPROFILE\.codex\config.toml",
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$DataRoot = "$env:USERPROFILE\EvidenceLanePV",
    [string]$RestartHelper = "$PSScriptRoot\Restart-EvidenceLaneCodex.ps1",
    [string]$ReceiptDirectory = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v200\two-slot",
    [string]$PowerShellExecutable = "powershell.exe",
    [switch]$ConfirmExplicitOperator,
    [switch]$ConfirmSwitch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:PluginName = "evidence-lane-plugin"
$script:ServerName = "evidence-lane"
$script:ExpectedTaskId = "019fedc7-cb86-7b40-94ce-1784a999f12b"
$script:ZeroHash = "0" * 64

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required sealed file is missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-ExpectedHash([string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "$Name must be one exact SHA-256."
    }
    return $Value.ToUpperInvariant()
}

function Assert-ContainedPath([string]$Name, [string]$Path, [string]$Parent) {
    $exactPath = [IO.Path]::GetFullPath($Path)
    $exactParent = [IO.Path]::GetFullPath($Parent).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    if (-not $exactPath.StartsWith($exactParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name is outside its approved root."
    }
    return $exactPath
}

function Write-JsonReceipt([string]$Path, [System.Collections.IDictionary]$Body) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent (
        "." + [IO.Path]::GetFileName($Path) + "." + [guid]::NewGuid().ToString("N")
    )
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText(
        $temporary,
        ($Body | ConvertTo-Json -Depth 16),
        $utf8NoBom
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-SealedJson([string]$Path, [string]$ExpectedSha256) {
    $expected = Assert-ExpectedHash -Name "sealed receipt SHA-256" -Value $ExpectedSha256
    $exact = (Resolve-Path -LiteralPath $Path).Path
    if ((Get-Sha256 $exact) -ne $expected) {
        throw "A sealed receipt SHA-256 does not match."
    }
    return [ordered]@{
        path = $exact
        sha256 = $expected
        body = Get-Content -LiteralPath $exact -Raw | ConvertFrom-Json
    }
}

function Get-Slot([object]$TwoSlotRegistry, [string]$Name) {
    $property = $TwoSlotRegistry.slots.PSObject.Properties[$Name]
    if ($null -eq $property) {
        throw "The two-slot registry is missing $Name."
    }
    return $property.Value
}

function Get-OtherSlot([string]$Name) {
    if ($Name -eq "stable-build") { return "fallback" }
    return "stable-build"
}

function Assert-InstallReceipt([object]$Slot, [string]$SlotName) {
    $sealed = Read-SealedJson `
        -Path ([string]$Slot.install_receipt) `
        -ExpectedSha256 ([string]$Slot.install_receipt_sha256)
    $receipt = $sealed.body
    if (
        $receipt.schema -ne "evidence-lane.codex-stable-installation.v2" -or
        $receipt.status -ne "PASS" -or
        $receipt.plugin.version -ne [string]$Slot.plugin_version -or
        $receipt.archive_sha256 -ne [string]$Slot.package_sha256 -or
        $receipt.activation.state -ne "INSTALLED_RESTART_REQUIRED" -or
        $receipt.candidate_created_or_accepted -ne $false -or
        $receipt.pointer_moved -ne $false -or
        $receipt.hil_inferred -ne $false
    ) {
        throw "The $SlotName installation receipt is not switch-eligible."
    }
    return $sealed
}

function Assert-TunnelSlot([object]$Slot, [string]$SlotName) {
    $runtimeRoot = Assert-ContainedPath `
        -Name "$SlotName tunnel runtime" `
        -Path ([string]$Slot.tunnel.runtime_root) `
        -Parent $DataRoot
    $manager = Assert-ContainedPath `
        -Name "$SlotName tunnel manager" `
        -Path ([string]$Slot.tunnel.manager) `
        -Parent $runtimeRoot
    if (-not (Test-Path -LiteralPath $manager -PathType Leaf)) {
        throw "The $SlotName tunnel manager is missing."
    }
    $marker = Assert-ContainedPath `
        -Name "$SlotName tunnel marker" `
        -Path ([string]$Slot.tunnel.marker) `
        -Parent $runtimeRoot
    $markerSha = Assert-ExpectedHash `
        -Name "$SlotName tunnel marker SHA-256" `
        -Value ([string]$Slot.tunnel.marker_sha256)
    if ((Get-Sha256 $marker) -ne $markerSha) {
        throw "The $SlotName tunnel marker drifted."
    }
    $markerBody = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if (
        $markerBody.schema -ne
        "evidence-lane.versioned-secure-mcp-tunnel-installation.v1" -or
        $markerBody.slot_role -ne [string]$Slot.slot_role -or
        $markerBody.legacy_version_manager_authoritative -ne $false -or
        [bool]$markerBody.byte_frozen -ne ([string]$Slot.slot_role -eq "fallback")
    ) {
        throw "The $SlotName tunnel marker does not bind the exact two-slot role."
    }
    return [ordered]@{
        runtime_root = $runtimeRoot
        manager = $manager
        marker = $marker
        marker_sha256 = $markerSha
        task_name = [string]$Slot.tunnel.task_name
        profile_name = [string]$Slot.tunnel.profile_name
    }
}

function Read-TwoSlotRegistry() {
    $expectedRegistrySha = Assert-ExpectedHash `
        -Name "RegistrySha256" `
        -Value $RegistrySha256
    $exactRegistry = (Resolve-Path -LiteralPath $Registry).Path
    if ((Get-Sha256 $exactRegistry) -ne $expectedRegistrySha) {
        throw "The exact two-slot registry SHA-256 does not match."
    }
    $body = Get-Content -LiteralPath $exactRegistry -Raw | ConvertFrom-Json
    $stable = Get-Slot -TwoSlotRegistry $body -Name "stable-build"
    $fallback = Get-Slot -TwoSlotRegistry $body -Name "fallback"
    if (
        $body.schema -ne "evidence-lane.codex-two-slot-registry.v1" -or
        $body.status -ne "PASS" -or
        $body.post_fuse_materialized -ne $true -or
        $body.accepted_pv -ne "PV11" -or
        [int]$body.accepted_generation -ne 11 -or
        $body.project_id -ne $ProjectId -or
        $body.evidence_session_id -ne $EvidenceSessionId -or
        $body.task_id -ne $TaskId -or
        $body.host_session_id -ne $HostSessionId -or
        [int]$body.exact_live_slot_count -ne 2 -or
        [int]$body.max_enabled_plugin_count -ne 1 -or
        [int]$body.max_active_tunnel_count -ne 1 -or
        $body.secret_material_present -ne $false -or
        @($body.slots.PSObject.Properties).Count -ne 2 -or
        $stable.plugin_selector -eq $fallback.plugin_selector -or
        $stable.slot_role -ne "stable-build" -or
        $fallback.slot_role -ne "fallback" -or
        $stable.plugin_selector -notlike "$script:PluginName@*" -or
        $fallback.plugin_selector -notlike "$script:PluginName@*" -or
        $stable.byte_frozen -ne $false -or
        $fallback.byte_frozen -ne $true -or
        $fallback.accepted_pv -ne "PV11" -or
        [int]$fallback.accepted_generation -ne 11 -or
        $fallback.package_sha256 -ne $body.accepted_package_sha256 -or
        $fallback.plugin_version -ne $body.accepted_plugin_version -or
        $TaskId -ne $script:ExpectedTaskId
    ) {
        throw "The two-slot registry is not the exact accepted-PV11 boundary."
    }
    $cacheRoot = Join-Path $CodexHome "plugins\cache"
    foreach ($name in @("stable-build", "fallback")) {
        $slot = Get-Slot -TwoSlotRegistry $body -Name $name
        $exactCache = Assert-ContainedPath `
            -Name "$name cache" `
            -Path ([string]$slot.cache_root) `
            -Parent $cacheRoot
        if (-not (Test-Path -LiteralPath $exactCache -PathType Container)) {
            throw "The $name live plugin cache is missing."
        }
        [void](Assert-InstallReceipt -Slot $slot -SlotName $name)
        [void](Assert-TunnelSlot -Slot $slot -SlotName $name)
    }
    return [ordered]@{
        path = $exactRegistry
        sha256 = $expectedRegistrySha
        body = $body
    }
}

function Read-PluginActivation() {
    $raw = Get-Content -LiteralPath $CodexConfig -Raw
    $header = [regex]'(?m)^\[plugins\."(?<selector>[^"]+)"(?<tail>[^\]]*)\]\s*$'
    $allHeaders = [regex]'(?m)^\[[^\r\n]+\]\s*$'
    $matches = @($header.Matches($raw))
    $sectionMatches = @($allHeaders.Matches($raw))
    $evidenceMatches = @(
        $matches | Where-Object { $_.Groups["selector"].Value -like "$script:PluginName@*" }
    )
    $roots = @{}
    $mcps = @{}
    foreach ($match in $evidenceMatches) {
        $selector = $match.Groups["selector"].Value
        $tail = $match.Groups["tail"].Value
        $start = $match.Index + $match.Length
        $next = @(
            $sectionMatches |
                Where-Object { $_.Index -gt $match.Index } |
                Select-Object -First 1
        )
        $end = if ($next.Count -eq 1) { $next[0].Index } else { $raw.Length }
        $body = $raw.Substring($start, $end - $start)
        $enabledMatch = [regex]::Match($body, '(?m)^\s*enabled\s*=\s*(?<value>true|false)\s*$')
        if (-not $enabledMatch.Success) {
            throw "An Evidence Lane plugin section has no explicit enabled flag."
        }
        $enabled = $enabledMatch.Groups["value"].Value -eq "true"
        if ([string]::IsNullOrWhiteSpace($tail)) {
            $roots[$selector] = $enabled
        }
        elseif ($tail -eq '.mcp_servers."evidence-lane"') {
            $mcps[$selector] = $enabled
        }
    }
    return [ordered]@{
        raw = $raw
        sha256 = Get-Sha256 $CodexConfig
        roots = $roots
        mcps = $mcps
    }
}

function Assert-ExclusiveActivation([object]$RegistryBody, [string]$ExpectedActiveSlot) {
    $activation = Read-PluginActivation
    $stable = Get-Slot -TwoSlotRegistry $RegistryBody -Name "stable-build"
    $fallback = Get-Slot -TwoSlotRegistry $RegistryBody -Name "fallback"
    $expectedSelectors = @([string]$stable.plugin_selector, [string]$fallback.plugin_selector)
    if (
        @($activation.roots.Keys).Count -ne 2 -or
        @($activation.mcps.Keys).Count -ne 2 -or
        @($activation.roots.Keys | Where-Object { $_ -notin $expectedSelectors }).Count -ne 0 -or
        @($activation.mcps.Keys | Where-Object { $_ -notin $expectedSelectors }).Count -ne 0
    ) {
        throw "The Codex config does not contain exactly the two registered Evidence Lane slots."
    }
    foreach ($name in @("stable-build", "fallback")) {
        $slot = Get-Slot -TwoSlotRegistry $RegistryBody -Name $name
        $expected = $name -eq $ExpectedActiveSlot
        if (
            [bool]$activation.roots[[string]$slot.plugin_selector] -ne $expected -or
            [bool]$activation.mcps[[string]$slot.plugin_selector] -ne $expected
        ) {
            throw "Codex plugin and MCP activation are not mutually exclusive."
        }
    }
    return $activation
}

function Set-ExclusiveActivation([object]$RegistryBody, [string]$ActiveSlot) {
    $before = Read-PluginActivation
    $stable = Get-Slot -TwoSlotRegistry $RegistryBody -Name "stable-build"
    $fallback = Get-Slot -TwoSlotRegistry $RegistryBody -Name "fallback"
    $selectors = @{
        ([string]$stable.plugin_selector) = $ActiveSlot -eq "stable-build"
        ([string]$fallback.plugin_selector) = $ActiveSlot -eq "fallback"
    }
    $section = [regex]'^(?<header>\[plugins\."(?<selector>[^"]+)"(?<tail>[^\]]*)\])$'
    $anySection = [regex]'^\[[^\r\n]+\]$'
    $enabled = [regex]'^(?<indent>\s*)enabled\s*=\s*(?:true|false)\s*$'
    $lines = @($before.raw -split '(?<=\n)')
    $currentSelector = $null
    $currentTail = $null
    $seen = @{}
    for ($index = 0; $index -lt $lines.Count; $index++) {
        $lineBody = $lines[$index].TrimEnd("`r", "`n")
        $match = $section.Match($lineBody)
        if ($match.Success) {
            $currentSelector = $match.Groups["selector"].Value
            $currentTail = $match.Groups["tail"].Value
            continue
        }
        if ($anySection.IsMatch($lineBody)) {
            $currentSelector = $null
            $currentTail = $null
            continue
        }
        $enabledMatch = $enabled.Match($lineBody)
        if (
            -not $enabledMatch.Success -or
            [string]::IsNullOrWhiteSpace($currentSelector) -or
            -not $selectors.ContainsKey($currentSelector)
        ) {
            continue
        }
        if (
            -not [string]::IsNullOrWhiteSpace($currentTail) -and
            $currentTail -ne '.mcp_servers."evidence-lane"'
        ) {
            continue
        }
        $newline = if ($lines[$index].EndsWith("`r`n")) { "`r`n" } elseif ($lines[$index].EndsWith("`n")) { "`n" } else { "" }
        $value = ([bool]$selectors[$currentSelector]).ToString().ToLowerInvariant()
        $lines[$index] = $enabledMatch.Groups["indent"].Value + "enabled = $value$newline"
        $seen["$currentSelector|$currentTail"] = $true
    }
    foreach ($selector in $selectors.Keys) {
        if (
            -not $seen.ContainsKey("$selector|") -or
            -not $seen.ContainsKey("$selector|.mcp_servers.`"evidence-lane`"")
        ) {
            throw "Both exact plugin and MCP sections must exist before switching."
        }
    }
    $backupRoot = Join-Path $ReceiptDirectory "config-backups"
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $backup = Join-Path $backupRoot ("config-" + $before.sha256 + ".toml")
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
        [IO.File]::WriteAllText($backup, $before.raw, [Text.UTF8Encoding]::new($false))
    }
    $temporary = Join-Path (Split-Path -Parent $CodexConfig) (
        ".config.toml." + [guid]::NewGuid().ToString("N")
    )
    [IO.File]::WriteAllText($temporary, ($lines -join ""), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $CodexConfig -Force
    $verified = Assert-ExclusiveActivation -RegistryBody $RegistryBody -ExpectedActiveSlot $ActiveSlot
    return [ordered]@{
        before_sha256 = $before.sha256
        after_sha256 = $verified.sha256
        backup = $backup
    }
}

function Invoke-Tunnel([object]$Slot, [string]$TunnelAction) {
    $tunnel = Assert-TunnelSlot -Slot $Slot -SlotName "selected"
    $output = & $PowerShellExecutable `
        -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $tunnel.manager `
        -Action $TunnelAction `
        -RuntimeRoot $tunnel.runtime_root `
        -ProfileName $tunnel.profile_name `
        -TaskName $tunnel.task_name 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $payload = $null
    if (-not [string]::IsNullOrWhiteSpace($text)) {
        try { $payload = $text | ConvertFrom-Json } catch { $payload = $null }
    }
    return [ordered]@{
        action = $TunnelAction
        exit_code = $exitCode
        payload = $payload
        secret_output_retained = $false
    }
}

function Get-TunnelSnapshot([object]$RegistryBody) {
    $rows = [ordered]@{}
    $readyCount = 0
    $runningCount = 0
    foreach ($name in @("stable-build", "fallback")) {
        $slot = Get-Slot -TwoSlotRegistry $RegistryBody -Name $name
        $status = Invoke-Tunnel -Slot $slot -TunnelAction "Status"
        $ready = $null -ne $status.payload -and $status.payload.control_plane_poll_ready -eq $true
        $running = $null -ne $status.payload -and $status.payload.process_running -eq $true
        if ($ready) { $readyCount += 1 }
        if ($running) { $runningCount += 1 }
        $rows[$name] = [ordered]@{
            ready = $ready
            process_running = $running
            task_registered = if ($null -ne $status.payload) { $status.payload.task_registered } else { $false }
            manager_exit_code = $status.exit_code
        }
    }
    if ($readyCount -gt 1 -or $runningCount -gt 1) {
        throw "More than one Evidence Lane tunnel is active."
    }
    return [ordered]@{
        slots = $rows
        ready_count = $readyCount
        running_count = $runningCount
    }
}

function Get-LiveSlotBinding([object]$RegistryBody) {
    $activation = Read-PluginActivation
    $stable = Get-Slot -TwoSlotRegistry $RegistryBody -Name "stable-build"
    $fallback = Get-Slot -TwoSlotRegistry $RegistryBody -Name "fallback"
    $expectedSelectors = @([string]$stable.plugin_selector, [string]$fallback.plugin_selector)
    if (
        @($activation.roots.Keys).Count -ne 2 -or
        @($activation.mcps.Keys).Count -ne 2 -or
        @($activation.roots.Keys | Where-Object { $_ -notin $expectedSelectors }).Count -ne 0 -or
        @($activation.mcps.Keys | Where-Object { $_ -notin $expectedSelectors }).Count -ne 0
    ) {
        throw "The Codex config does not contain exactly the two registered Evidence Lane slots."
    }
    $enabledSlots = @()
    foreach ($name in @("stable-build", "fallback")) {
        $slot = Get-Slot -TwoSlotRegistry $RegistryBody -Name $name
        $selector = [string]$slot.plugin_selector
        $rootEnabled = [bool]$activation.roots[$selector]
        $mcpEnabled = [bool]$activation.mcps[$selector]
        if ($rootEnabled -ne $mcpEnabled) {
            throw "The $name plugin and MCP flags disagree."
        }
        if ($rootEnabled) { $enabledSlots += $name }
    }
    if ($enabledSlots.Count -ne 1) {
        throw "Exactly one Evidence Lane plugin and MCP slot must be enabled."
    }
    $activeSlot = [string]$enabledSlots[0]
    $tunnels = Get-TunnelSnapshot -RegistryBody $RegistryBody
    if (
        $tunnels.ready_count -ne 1 -or
        $tunnels.running_count -ne 1 -or
        $tunnels.slots[$activeSlot].ready -ne $true -or
        $tunnels.slots[(Get-OtherSlot $activeSlot)].process_running -ne $false
    ) {
        throw "The enabled plugin and sole ready tunnel do not form one matching slot."
    }
    return [ordered]@{
        active_slot = $activeSlot
        activation = $activation
        tunnels = $tunnels
        active_slot_source = "LIVE_CODEX_CONFIG_AND_TUNNEL_MATCH"
    }
}

function Assert-DecisionEvidence([object]$RegistryBody, [string]$SourceSlot) {
    if ($Reason -eq "EXPLICIT_OPERATOR_FAILOVER") {
        if ($TargetSlot -ne "fallback" -or -not $ConfirmExplicitOperator) {
            throw "Explicit fallback requires -ConfirmExplicitOperator."
        }
        if ($DecisionReceipt -or $DecisionReceiptSha256) {
            throw "Explicit operator failover does not accept a substituted health receipt."
        }
        return [ordered]@{
            type = "EXPLICIT_OPERATOR_FAILOVER"
            receipt_sha256 = $null
            transient_auto_switch_rejected = $true
        }
    }
    if (-not $DecisionReceipt -or -not $DecisionReceiptSha256) {
        throw "A deterministic health or repair decision requires one sealed receipt."
    }
    $sealed = Read-SealedJson -Path $DecisionReceipt -ExpectedSha256 $DecisionReceiptSha256
    $body = $sealed.body
    $source = Get-Slot -TwoSlotRegistry $RegistryBody -Name $SourceSlot
    if ($Reason -eq "DETERMINISTIC_STABLE_HEALTH_FAILURE") {
        if (
            $SourceSlot -ne "stable-build" -or
            $TargetSlot -ne "fallback" -or
            $body.schema -ne "evidence-lane.stable-health-failure.v1" -or
            $body.status -ne "DETERMINISTIC_STABLE_FAILURE" -or
            $body.plugin_selector -ne [string]$source.plugin_selector -or
            $body.plugin_version -ne [string]$source.plugin_version -or
            [int]$body.consecutive_failures -lt 3 -or
            [int]$body.sample_window_seconds -lt 30 -or
            [int]$body.distinct_probe_types -lt 2 -or
            $body.single_transient_error -ne $false -or
            $body.secret_material_present -ne $false
        ) {
            throw "The stable failure evidence is transient, stale, or incomplete."
        }
    }
    elseif ($Reason -eq "VERIFIED_STABLE_REPAIR") {
        if (
            $SourceSlot -ne "fallback" -or
            $TargetSlot -ne "stable-build" -or
            $body.schema -ne "evidence-lane.stable-repair-proof.v1" -or
            $body.status -ne "PASS" -or
            $body.plugin_selector -ne [string](Get-Slot -TwoSlotRegistry $RegistryBody -Name "stable-build").plugin_selector -or
            $body.package_verified -ne $true -or
            $body.installed_bytes_verified -ne $true -or
            $body.native_catalog_verified -ne $true -or
            $body.clean_ci_verified -ne $true -or
            $body.secret_material_present -ne $false
        ) {
            throw "The repaired stable build is not fully verified."
        }
    }
    else {
        throw "Reason does not authorize this slot transition."
    }
    return [ordered]@{
        type = $Reason
        receipt = $sealed.path
        receipt_sha256 = $sealed.sha256
        transient_auto_switch_rejected = $true
    }
}

function Write-FailureReceipt(
    [string]$SourceSlot,
    [string]$Failure,
    [bool]$SourceRestored,
    [bool]$TargetDisabled,
    [bool]$ConfigRestored
) {
    $rollbackVerified = $SourceRestored -and $TargetDisabled -and $ConfigRestored
    $body = [ordered]@{
        schema = "evidence-lane.codex-two-slot-switch-failure.v1"
        status = if ($rollbackVerified) {
            "FAILED_ROLLED_BACK"
        } else {
            "FAILED_RECOVERY_REQUIRED"
        }
        source_slot = $SourceSlot
        target_slot = $TargetSlot
        reason = $Reason
        failure = $Failure
        source_restored = $SourceRestored
        target_disabled = $TargetDisabled
        config_restored = $ConfigRestored
        rollback_verified = $rollbackVerified
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        secret_material_present = $false
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $path = Join-Path $ReceiptDirectory "LAST_FAILED_SWITCH.json"
    Write-JsonReceipt -Path $path -Body $body
    return $path
}

$registrySeal = Read-TwoSlotRegistry
$registryBody = $registrySeal.body
$liveBinding = Get-LiveSlotBinding -RegistryBody $registryBody
$sourceSlot = [string]$liveBinding.active_slot
$source = Get-Slot -TwoSlotRegistry $registryBody -Name $sourceSlot
$target = Get-Slot -TwoSlotRegistry $registryBody -Name $TargetSlot

if ($Action -eq "Verify") {
    [ordered]@{
        status = "PASS"
        schema = "evidence-lane.codex-two-slot-verification.v1"
        active_slot = $sourceSlot
        active_slot_source = $liveBinding.active_slot_source
        enabled_plugin_count = 1
        active_tunnel_count = 1
        installed_slot_count = 2
        fallback_byte_frozen = $true
        fallback_accepted_pv = "PV11"
        config_sha256 = $liveBinding.activation.sha256
        registry_sha256 = $registrySeal.sha256
        exact_task_uri = "codex://threads/$TaskId"
        native_proof_required_after_restart = $true
        secret_material_present = $false
    } | ConvertTo-Json -Depth 10
    exit 0
}

if ($TargetSlot -eq $sourceSlot) {
    throw "The requested target slot is already active."
}
if ([string]::IsNullOrWhiteSpace($Reason)) {
    throw "Prepare and Switch require one exact transition reason."
}

if ($Action -eq "Prepare") {
    if ($TargetProcessId -le 0) {
        throw "Prepare requires the exact Codex root process ID."
    }
    $activation = $liveBinding.activation
    $decision = Assert-DecisionEvidence `
        -RegistryBody $registryBody `
        -SourceSlot $sourceSlot
    $restartHelperSha = Get-Sha256 $RestartHelper
    $body = [ordered]@{
        schema = "evidence-lane.codex-two-slot-switch-preparation.v1"
        state = "PREPARED_NOT_SWITCHED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        source_slot = $sourceSlot
        source_slot_authority = $liveBinding.active_slot_source
        target_slot = $TargetSlot
        reason = $Reason
        decision = $decision
        registry = $registrySeal.path
        registry_sha256 = $registrySeal.sha256
        codex_config = (Resolve-Path -LiteralPath $CodexConfig).Path
        codex_config_sha256 = $activation.sha256
        restart_helper = (Resolve-Path -LiteralPath $RestartHelper).Path
        restart_helper_sha256 = $restartHelperSha
        target_process_id = $TargetProcessId
        exact_task_uri = "codex://threads/$TaskId"
        stop_source_before_start_target = $true
        max_active_mcp_servers = 1
        max_active_tunnels = 1
        transient_single_error_auto_switch_allowed = $false
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        secret_material_present = $false
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $path = Join-Path $ReceiptDirectory "CODEX_TWO_SLOT_SWITCH_PREPARATION.json"
    Write-JsonReceipt -Path $path -Body $body
    [ordered]@{
        status = "PASS"
        state = "PREPARED_NOT_SWITCHED"
        receipt_path = $path
        receipt_sha256 = Get-Sha256 $path
        next_action = "SWITCH_WITH_EXACT_PREPARATION_SHA_AND_CONFIRMSWITCH"
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq "Switch") {
    if (-not $ConfirmSwitch) {
        throw "Switch requires -ConfirmSwitch."
    }
    if (-not $PreparationReceipt -or -not $PreparationReceiptSha256) {
        throw "Switch requires the exact preparation receipt and SHA-256."
    }
    $sealedPreparation = Read-SealedJson `
        -Path $PreparationReceipt `
        -ExpectedSha256 $PreparationReceiptSha256
    $prepared = $sealedPreparation.body
    $activation = Assert-ExclusiveActivation `
        -RegistryBody $registryBody `
        -ExpectedActiveSlot $sourceSlot
    if (
        $prepared.schema -ne "evidence-lane.codex-two-slot-switch-preparation.v1" -or
        $prepared.state -ne "PREPARED_NOT_SWITCHED" -or
        $prepared.project_id -ne $ProjectId -or
        $prepared.evidence_session_id -ne $EvidenceSessionId -or
        $prepared.task_id -ne $TaskId -or
        $prepared.host_session_id -ne $HostSessionId -or
        $prepared.source_slot -ne $sourceSlot -or
        $prepared.target_slot -ne $TargetSlot -or
        $prepared.reason -ne $Reason -or
        $prepared.registry_sha256 -ne $registrySeal.sha256 -or
        $prepared.codex_config_sha256 -ne $activation.sha256 -or
        $prepared.restart_helper_sha256 -ne (Get-Sha256 $RestartHelper) -or
        [int]$prepared.target_process_id -ne $TargetProcessId -or
        $TargetProcessId -le 0
    ) {
        throw "The switch preparation is stale or cross-boundary."
    }
    [void](Assert-DecisionEvidence -RegistryBody $registryBody -SourceSlot $sourceSlot)
    $sourceStopped = $false
    $configSwitched = $false
    try {
        $stop = Invoke-Tunnel -Slot $source -TunnelAction "Stop"
        if ($stop.exit_code -ne 0) { throw "The source tunnel did not stop cleanly." }
        $sourceStopped = $true
        $afterStop = Get-TunnelSnapshot -RegistryBody $registryBody
        if ($afterStop.running_count -ne 0) {
            throw "A tunnel remains active after stopping the source slot."
        }
        $start = Invoke-Tunnel -Slot $target -TunnelAction "Start"
        if (
            $start.exit_code -ne 0 -or
            $null -eq $start.payload -or
            $start.payload.control_plane_poll_ready -ne $true
        ) {
            throw "The target tunnel failed readiness."
        }
        $afterStart = Get-TunnelSnapshot -RegistryBody $registryBody
        if (
            $afterStart.ready_count -ne 1 -or
            $afterStart.slots[$TargetSlot].ready -ne $true -or
            $afterStart.slots[$sourceSlot].process_running -ne $false
        ) {
            throw "The target tunnel did not become the sole active tunnel."
        }
        $configReceipt = Set-ExclusiveActivation `
            -RegistryBody $registryBody `
            -ActiveSlot $TargetSlot
        $configSwitched = $true
        $restartOutput = & $PowerShellExecutable `
            -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $RestartHelper `
            -Action Prepare `
            -InstallReceipt ([string]$target.install_receipt) `
            -InstallReceiptSha256 ([string]$target.install_receipt_sha256) `
            -ProjectId $ProjectId `
            -EvidenceSessionId $EvidenceSessionId `
            -TaskId $TaskId `
            -HostSessionId $HostSessionId `
            -TargetProcessId $TargetProcessId `
            -ReceiptDirectory (Join-Path $ReceiptDirectory "restart") 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "The exact-task restart helper rejected preparation."
        }
        $restartPrepared = (($restartOutput | Out-String).Trim() | ConvertFrom-Json)
        if ($restartPrepared.status -ne "PASS") {
            throw "The exact-task restart preparation did not pass."
        }
        $transition = [ordered]@{
            schema = "evidence-lane.codex-two-slot-switch-transition.v1"
            state = "SWITCHED_RESTART_PENDING_NATIVE_PROOF"
            project_id = $ProjectId
            evidence_session_id = $EvidenceSessionId
            task_id = $TaskId
            host_session_id = $HostSessionId
            source_slot = $sourceSlot
            target_slot = $TargetSlot
            active_slot_authority = "LIVE_CODEX_CONFIG_AND_TUNNEL_MATCH"
            reason = $Reason
            registry_sha256 = $registrySeal.sha256
            preparation_receipt_sha256 = $sealedPreparation.sha256
            config = $configReceipt
            source_tunnel_stopped_first = $true
            target_tunnel_ready_before_plugin_switch = $true
            enabled_plugin_count = 1
            active_tunnel_count = 1
            restart_preparation_receipt = $restartPrepared.receipt_path
            restart_preparation_receipt_sha256 = $restartPrepared.receipt_sha256
            exact_task_uri = "codex://threads/$TaskId"
            native_catalog_and_binding_proof_pending = $true
            candidate_created_or_accepted = $false
            pointer_moved = $false
            hil_inferred = $false
            secret_material_present = $false
            switched_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $transitionPath = Join-Path $ReceiptDirectory "CODEX_TWO_SLOT_SWITCH_TRANSITION.json"
        Write-JsonReceipt -Path $transitionPath -Body $transition
        & $PowerShellExecutable `
            -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $RestartHelper `
            -Action Restart `
            -InstallReceipt ([string]$target.install_receipt) `
            -InstallReceiptSha256 ([string]$target.install_receipt_sha256) `
            -ProjectId $ProjectId `
            -EvidenceSessionId $EvidenceSessionId `
            -TaskId $TaskId `
            -HostSessionId $HostSessionId `
            -TargetProcessId $TargetProcessId `
            -PreparationReceipt ([string]$restartPrepared.receipt_path) `
            -PreparationReceiptSha256 ([string]$restartPrepared.receipt_sha256) `
            -ReceiptDirectory (Join-Path $ReceiptDirectory "restart") `
            -ConfirmRestart
        exit 0
    }
    catch {
        $failure = $_.Exception.Message
        $configRestored = -not $configSwitched
        try {
            [void](Set-ExclusiveActivation `
                -RegistryBody $registryBody `
                -ActiveSlot $sourceSlot)
            $configRestored = $true
        }
        catch { $configRestored = $false }
        $targetDisabled = $false
        try {
            $targetStop = Invoke-Tunnel -Slot $target -TunnelAction "Stop"
            $targetDisabled = $targetStop.exit_code -eq 0
        }
        catch { $targetDisabled = $false }
        $sourceRestored = -not $sourceStopped
        if ($sourceStopped) {
            try {
                $sourceStart = Invoke-Tunnel -Slot $source -TunnelAction "Start"
                $sourceRestored = (
                    $sourceStart.exit_code -eq 0 -and
                    $null -ne $sourceStart.payload -and
                    $sourceStart.payload.control_plane_poll_ready -eq $true
                )
            }
            catch { $sourceRestored = $false }
        }
        if ($sourceRestored -and $targetDisabled -and $configRestored) {
            try {
                $restored = Get-LiveSlotBinding -RegistryBody $registryBody
                $sourceRestored = $restored.active_slot -eq $sourceSlot
            }
            catch { $sourceRestored = $false }
        }
        $failurePath = Write-FailureReceipt `
            -SourceSlot $sourceSlot `
            -Failure $failure `
            -SourceRestored $sourceRestored `
            -TargetDisabled $targetDisabled `
            -ConfigRestored $configRestored
        if ($sourceRestored -and $targetDisabled -and $configRestored) {
            throw "$failure Rolled back to $sourceSlot. Failure receipt: $failurePath"
        }
        throw "$failure Automatic rollback could not be proven; manual recovery is required. Failure receipt: $failurePath"
    }
}

throw "Unsupported two-slot action."
