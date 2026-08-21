[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Prepare", "Switch", "Verify")]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "main-git-release",
        "branch-commit-recovery",
        "mutable-local-testing"
    )]
    [string]$TargetSlot,
    [ValidateSet(
        "EXPLICIT_OPERATOR_SELECTION",
        "MUTABLE_LOCAL_RUNTIME_FAILURE",
        "VERIFIED_BRANCH_RECOVERY_REPAIR"
    )]
    [string]$Reason = "EXPLICIT_OPERATOR_SELECTION",
    [string]$Registry,
    [string]$RegistrySha256,
    [string]$PreparationReceipt,
    [string]$PreparationReceiptSha256,
    [string]$CodexConfig = "$env:USERPROFILE\.codex\config.toml",
    [string]$CodexExecutable = "",
    [string]$ReceiptDirectory = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v220\three-slot",
    [switch]$ConfirmSwitch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:SlotSelectors = [ordered]@{
    "main-git-release" = "evidence-lane-plugin@evidence-lane-github"
    "branch-commit-recovery" = "evidence-lane-plugin@evidence-lane-v220-stable-recovery"
    "mutable-local-testing" = "evidence-lane-plugin@evidence-lane-v220-testing-new"
}
$script:ObsoleteSelectors = @(
    "evidence-lane-plugin@evidence-lane-v220-local-successor",
    "evidence-lane-plugin@evidence-lane-pv11-fallback"
)

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required sealed file is missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-Sha256([string]$Name, [string]$Value) {
    if ($Value -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "$Name must be one exact SHA-256."
    }
    return $Value.ToUpperInvariant()
}

function Write-JsonReceipt([string]$Path, [System.Collections.IDictionary]$Body) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Path) + "." + [guid]::NewGuid().ToString("N"))
    [IO.File]::WriteAllText(
        $temporary,
        ($Body | ConvertTo-Json -Depth 16),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Get-CodexExecutable {
    if (-not [string]::IsNullOrWhiteSpace($CodexExecutable)) {
        return (Resolve-Path -LiteralPath $CodexExecutable).Path
    }
    $command = Get-Command codex.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { $command = Get-Command codex -ErrorAction Stop }
    return $command.Source
}

function Get-PluginInventory {
    $raw = & (Get-CodexExecutable) plugin list --json
    if ($LASTEXITCODE -ne 0) { throw "Codex plugin inventory failed." }
    $payload = $raw | ConvertFrom-Json
    return @(
        $payload.installed |
            Where-Object { [string]$_.name -ceq "evidence-lane-plugin" }
    )
}

function Assert-ExactInstalledThreeSlots {
    $rows = @(Get-PluginInventory)
    $bySelector = @{}
    foreach ($row in $rows) { $bySelector[[string]$row.pluginId] = $row }
    $expected = @($script:SlotSelectors.Values)
    if (
        $rows.Count -ne 3 -or
        @($bySelector.Keys | Where-Object { $_ -notin $expected }).Count -ne 0 -or
        @($expected | Where-Object { -not $bySelector.ContainsKey($_) }).Count -ne 0 -or
        @($script:ObsoleteSelectors | Where-Object { $bySelector.ContainsKey($_) }).Count -ne 0
    ) {
        throw "The live Codex inventory is not the exact three-slot Evidence Lane boundary."
    }
    return $bySelector
}

function Read-SealedRegistry {
    if ([string]::IsNullOrWhiteSpace($Registry) -or [string]::IsNullOrWhiteSpace($RegistrySha256)) {
        throw "Prepare and Switch require the exact sealed three-slot registry."
    }
    $expectedSha = Assert-Sha256 -Name "RegistrySha256" -Value $RegistrySha256
    $exact = (Resolve-Path -LiteralPath $Registry).Path
    if ((Get-Sha256 $exact) -cne $expectedSha) {
        throw "The three-slot registry SHA-256 does not match."
    }
    $body = Get-Content -LiteralPath $exact -Raw | ConvertFrom-Json
    if ($body.schema -cne "evidence-lane.codex-three-slot-registry.v1") {
        throw "The supplied registry is not the three-slot authority."
    }
    foreach ($slot in $script:SlotSelectors.Keys) {
        $row = $body.slots.PSObject.Properties[$slot]
        if (
            $null -eq $row -or
            [string]$row.Value.plugin_selector -cne [string]$script:SlotSelectors[$slot] -or
            [string]$row.Value.plugin_version -notmatch '^2\.2\.0\+codex\.[0-9A-Za-z.-]+$'
        ) {
            throw "The three-slot registry has a missing or mismatched slot."
        }
    }
    return [ordered]@{ path = $exact; sha256 = $expectedSha; body = $body }
}

function Read-Activation {
    $exactConfig = (Resolve-Path -LiteralPath $CodexConfig).Path
    $raw = Get-Content -LiteralPath $exactConfig -Raw
    $section = [regex]'(?m)^\[plugins\."(?<selector>[^"]+)"(?<tail>[^\]]*)\]\s*$'
    $matches = @($section.Matches($raw))
    $roots = @{}
    $mcps = @{}
    for ($index = 0; $index -lt $matches.Count; $index++) {
        $match = $matches[$index]
        $selector = $match.Groups["selector"].Value
        if ($selector -cnotlike "evidence-lane-plugin@*") { continue }
        $start = $match.Index + $match.Length
        $end = if ($index + 1 -lt $matches.Count) { $matches[$index + 1].Index } else { $raw.Length }
        $enabled = [regex]::Match($raw.Substring($start, $end - $start), '(?m)^\s*enabled\s*=\s*(?<value>true|false)\s*$')
        if (-not $enabled.Success) { throw "An Evidence Lane config section lacks an explicit enabled flag." }
        $value = $enabled.Groups["value"].Value -ceq "true"
        $tail = $match.Groups["tail"].Value
        if ([string]::IsNullOrWhiteSpace($tail)) { $roots[$selector] = $value }
        elseif ($tail -cin @('.mcp_servers."evidence-lane"', '.mcp_servers.evidence-lane')) { $mcps[$selector] = $value }
    }
    return [ordered]@{ path = $exactConfig; raw = $raw; sha256 = Get-Sha256 $exactConfig; roots = $roots; mcps = $mcps }
}

function Assert-Activation([string]$ExpectedSlot, [switch]$AllowAllDisabled) {
    $activation = Read-Activation
    foreach ($obsolete in $script:ObsoleteSelectors) {
        if (($activation.roots[$obsolete] -eq $true) -or ($activation.mcps[$obsolete] -eq $true)) {
            throw "An obsolete Evidence Lane selector is active."
        }
    }
    $enabled = @()
    foreach ($slot in $script:SlotSelectors.Keys) {
        $selector = [string]$script:SlotSelectors[$slot]
        if (-not $activation.roots.ContainsKey($selector) -or -not $activation.mcps.ContainsKey($selector)) {
            throw "The Codex config is missing an exact three-slot plugin/MCP pair."
        }
        if ([bool]$activation.roots[$selector] -ne [bool]$activation.mcps[$selector]) {
            throw "An Evidence Lane plugin/MCP activation pair disagrees."
        }
        if ([bool]$activation.roots[$selector]) { $enabled += $slot }
    }
    if ($AllowAllDisabled -and $enabled.Count -eq 0) { return $activation }
    if ($enabled.Count -ne 1 -or [string]$enabled[0] -cne $ExpectedSlot) {
        throw "Exactly the requested three-slot selector must be enabled."
    }
    return $activation
}

function Set-ExclusiveActivation([string]$SelectedSlot) {
    $before = Read-Activation
    $lines = @($before.raw -split '(?<=\n)')
    $section = [regex]'^\[plugins\."(?<selector>[^"]+)"(?<tail>[^\]]*)\]\s*$'
    $anySection = [regex]'^\[[^\r\n]+\]\s*$'
    $enabled = [regex]'^(?<indent>\s*)enabled\s*=\s*(?:true|false)\s*$'
    $currentSelector = ""
    $currentTail = ""
    $seen = @{}
    for ($index = 0; $index -lt $lines.Count; $index++) {
        $body = $lines[$index].TrimEnd("`r", "`n")
        $match = $section.Match($body)
        if ($match.Success) {
            $currentSelector = $match.Groups["selector"].Value
            $currentTail = $match.Groups["tail"].Value
            continue
        }
        if ($anySection.IsMatch($body)) { $currentSelector = ""; $currentTail = ""; continue }
        $enabledMatch = $enabled.Match($body)
        if (-not $enabledMatch.Success -or $currentSelector -cnotlike "evidence-lane-plugin@*") { continue }
        $isRoot = [string]::IsNullOrWhiteSpace($currentTail)
        $isMcp = $currentTail -cin @('.mcp_servers."evidence-lane"', '.mcp_servers.evidence-lane')
        if (-not $isRoot -and -not $isMcp) { continue }
        $slot = @($script:SlotSelectors.Keys | Where-Object { [string]$script:SlotSelectors[$_] -ceq $currentSelector })
        if ($slot.Count -eq 0) {
            $value = "false"
        } else {
            $value = ([string]$slot[0] -ceq $SelectedSlot).ToString().ToLowerInvariant()
        }
        $newline = if ($lines[$index].EndsWith("`r`n")) { "`r`n" } elseif ($lines[$index].EndsWith("`n")) { "`n" } else { "" }
        $lines[$index] = $enabledMatch.Groups["indent"].Value + "enabled = $value$newline"
        $seen["$currentSelector|$isRoot|$isMcp"] = $true
    }
    foreach ($selector in $script:SlotSelectors.Values) {
        if (-not $seen.ContainsKey("$selector|True|False") -or -not $seen.ContainsKey("$selector|False|True")) {
            throw "The exact plugin and MCP sections were not both present before switching."
        }
    }
    $backupRoot = Join-Path $ReceiptDirectory "config-backups"
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $backup = Join-Path $backupRoot ("config-" + $before.sha256 + ".toml")
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
        [IO.File]::WriteAllText($backup, $before.raw, [Text.UTF8Encoding]::new($false))
    }
    $temporary = Join-Path (Split-Path -Parent $before.path) (".config.toml." + [guid]::NewGuid().ToString("N"))
    [IO.File]::WriteAllText($temporary, ($lines -join ""), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $before.path -Force
    $after = Assert-Activation -ExpectedSlot $SelectedSlot
    return [ordered]@{ before_sha256 = $before.sha256; after_sha256 = $after.sha256; backup = $backup }
}

$inventory = Assert-ExactInstalledThreeSlots
if ($Reason -ceq "MUTABLE_LOCAL_RUNTIME_FAILURE" -and $TargetSlot -cne "branch-commit-recovery") {
    throw "A mutable local runtime failure may switch only to branch-commit-recovery."
}

if ($Action -ceq "Verify") {
    $activation = Assert-Activation -ExpectedSlot $TargetSlot
    [ordered]@{
        schema = "evidence-lane.codex-three-slot-verification.v1"
        status = "PASS"
        active_slot = $TargetSlot
        active_selector = [string]$script:SlotSelectors[$TargetSlot]
        installed_slot_count = $inventory.Count
        obsolete_selector_active = $false
        pre_2_2_fallback_allowed = $false
        config_sha256 = $activation.sha256
    } | ConvertTo-Json -Depth 8
    exit 0
}

$sealedRegistry = Read-SealedRegistry
$targetSelector = [string]$script:SlotSelectors[$TargetSlot]
$targetInventory = $inventory[$targetSelector]
$targetRegistry = $sealedRegistry.body.slots.PSObject.Properties[$TargetSlot].Value
if ([string]$targetInventory.version -cne [string]$targetRegistry.plugin_version) {
    throw "The installed target version does not match the sealed three-slot registry."
}

if ($Action -ceq "Prepare") {
    $activation = Assert-Activation -ExpectedSlot $TargetSlot -AllowAllDisabled
    $receiptPath = Join-Path $ReceiptDirectory ("PREPARE_" + [guid]::NewGuid().ToString("N") + ".json")
    Write-JsonReceipt $receiptPath ([ordered]@{
        schema = "evidence-lane.codex-three-slot-switch-preparation.v1"
        status = "PASS"
        state = "PREPARED_NOT_SWITCHED"
        target_slot = $TargetSlot
        target_selector = $targetSelector
        target_version = [string]$targetInventory.version
        reason = $Reason
        registry_path = $sealedRegistry.path
        registry_sha256 = $sealedRegistry.sha256
        config_sha256 = $activation.sha256
        obsolete_selector_activation_allowed = $false
        mutable_local_failure_never_targets_main_git = $true
        pre_2_2_fallback_allowed = $false
        switched = $false
    })
    [ordered]@{
        status = "PASS"
        state = "PREPARED_NOT_SWITCHED"
        receipt_path = $receiptPath
        receipt_sha256 = Get-Sha256 $receiptPath
    } | ConvertTo-Json -Depth 8
    exit 0
}

if (-not $ConfirmSwitch) { throw "Switch requires -ConfirmSwitch." }
if ([string]::IsNullOrWhiteSpace($PreparationReceipt) -or [string]::IsNullOrWhiteSpace($PreparationReceiptSha256)) {
    throw "Switch requires the exact preparation receipt and SHA-256."
}
$expectedPreparationSha = Assert-Sha256 -Name "PreparationReceiptSha256" -Value $PreparationReceiptSha256
$exactPreparation = (Resolve-Path -LiteralPath $PreparationReceipt).Path
if ((Get-Sha256 $exactPreparation) -cne $expectedPreparationSha) { throw "Preparation receipt SHA-256 mismatch." }
$prepared = Get-Content -LiteralPath $exactPreparation -Raw | ConvertFrom-Json
$before = Read-Activation
if (
    $prepared.schema -cne "evidence-lane.codex-three-slot-switch-preparation.v1" -or
    $prepared.state -cne "PREPARED_NOT_SWITCHED" -or
    [string]$prepared.target_slot -cne $TargetSlot -or
    [string]$prepared.target_selector -cne $targetSelector -or
    [string]$prepared.reason -cne $Reason -or
    [string]$prepared.registry_sha256 -cne $sealedRegistry.sha256 -or
    [string]$prepared.config_sha256 -cne $before.sha256
) {
    throw "The preparation receipt does not bind the current switch boundary."
}
$mutation = Set-ExclusiveActivation -SelectedSlot $TargetSlot
$receiptPath = Join-Path $ReceiptDirectory ("SWITCH_" + [guid]::NewGuid().ToString("N") + ".json")
Write-JsonReceipt $receiptPath ([ordered]@{
    schema = "evidence-lane.codex-three-slot-switch-transition.v1"
    status = "PASS"
    state = "TARGET_SELECTED_RESTART_REQUIRED"
    target_slot = $TargetSlot
    target_selector = $targetSelector
    target_version = [string]$targetInventory.version
    reason = $Reason
    registry_sha256 = $sealedRegistry.sha256
    preparation_receipt_sha256 = $expectedPreparationSha
    config = $mutation
    exact_enabled_plugin_count = 1
    exact_enabled_mcp_count = 1
    obsolete_selector_active = $false
    mutable_local_failure_never_targets_main_git = $true
    pre_2_2_fallback_allowed = $false
    restart_required = $true
    helper_installs_plugin = $false
})
[ordered]@{
    status = "PASS"
    state = "TARGET_SELECTED_RESTART_REQUIRED"
    receipt_path = $receiptPath
    receipt_sha256 = Get-Sha256 $receiptPath
    next_action = "STAGE_TARGET_VERSION_TUNNEL_THEN_RUN_RESTART_ONLY_HELPER"
} | ConvertTo-Json -Depth 8
