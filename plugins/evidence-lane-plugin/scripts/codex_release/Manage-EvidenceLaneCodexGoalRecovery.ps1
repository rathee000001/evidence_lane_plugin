[CmdletBinding()]
param(
    [ValidateSet("Probe", "Register", "RecoverNow", "RecoverAtLogon", "Status", "Unregister")]
    [string]$Action = "Status",
    [string]$TaskBindingReceipt,
    [string]$TaskId,
    [string]$ActivePlanTaskId,
    [string]$Release = "3.0.0",
    [string]$RecoveryRoot = "",
    [string]$ThreeSlotRegistry = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v300\three-slot\CODEX_THREE_SLOT_REGISTRY.json",
    [string]$ThreeSlotRegistrySha256 = "",
    [string]$ScheduledTaskName = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Release -notmatch '^\d+\.\d+\.\d+$') {
    throw "The Goal recovery helper requires one exact semantic release."
}
if (
    $Action -in @("Register", "RecoverAtLogon") -and
    $ThreeSlotRegistrySha256 -cnotmatch '^[A-F0-9]{64}$'
) {
    throw "Goal recovery registration requires the exact three-slot registry file seal."
}
$script:Release = $Release
$script:ReleaseToken = "v" + ($Release -replace '\.', '')
if ([string]::IsNullOrWhiteSpace($RecoveryRoot)) {
    $RecoveryRoot = Join-Path $env:USERPROFILE "EvidenceLanePV\installations\helpers\$($script:ReleaseToken)\goal-recovery"
}
if ([string]::IsNullOrWhiteSpace($ScheduledTaskName)) {
    $ScheduledTaskName = "Evidence Lane Codex Goal Recovery $($script:ReleaseToken)"
}

$script:Schema = "evidence-lane.codex-goal-recovery-binding.v1"
$script:ManagerSchema = "evidence-lane.codex-goal-recovery-manager.v1"
$script:RecoverySchema = "evidence-lane.codex-goal-recovery-run.v1"
$script:BindingEventSchema = "evidence-lane.codex-goal-recovery-binding-event.v1"
$script:RecoveryAttemptSchema = "evidence-lane.codex-goal-recovery-attempt.v1"
$script:RecoveryManagerRunSchema = "evidence-lane.codex-goal-recovery-manager-run.v1"
$script:MaxRecoveryAttemptsPerBinding = 2
$script:RecoveryBackoffSeconds = @(0, 2)
$script:CanonicalStableSelector = "evidence-lane-plugin@evidence-lane-github"
$script:LocalTestingSelector = "evidence-lane-plugin@evidence-lane-v300-testing-new"
$script:LocalRecoverySelector = "evidence-lane-plugin@evidence-lane-v300-stable-recovery"
$script:GenerationNeutralFallbackSelector = "evidence-lane-plugin@evidence-lane-fallback"
$script:HostAppProfiles = [ordered]@{
    "OpenAI.Codex_2p2nqsd0c76g0!App" = [ordered]@{
        app_id = "OpenAI.Codex_2p2nqsd0c76g0!App"
        host_application = "CHATGPT_CODEX"
        desktop_release_channel = "CHATGPT_STABLE"
        available_surfaces = @("CHATGPT", "CODEX")
        governed_surface = "CODEX"
        chatgpt_surface_governed = $false
        package_name = "OpenAI.Codex"
        package_family_name = "OpenAI.Codex_2p2nqsd0c76g0"
        start_app_name = "ChatGPT"
    }
    "OpenAI.CodexBeta_2p2nqsd0c76g0!App" = [ordered]@{
        app_id = "OpenAI.CodexBeta_2p2nqsd0c76g0!App"
        host_application = "CHATGPT_BETA_CODEX"
        desktop_release_channel = "CHATGPT_BETA"
        available_surfaces = @("CHATGPT", "CODEX")
        governed_surface = "CODEX"
        chatgpt_surface_governed = $false
        package_name = "OpenAI.CodexBeta"
        package_family_name = "OpenAI.CodexBeta_2p2nqsd0c76g0"
        start_app_name = "ChatGPT (Beta)"
    }
}
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-HostAppProfile([string]$ExactAppId) {
    if ([string]::IsNullOrWhiteSpace($ExactAppId) -or -not $script:HostAppProfiles.Contains($ExactAppId)) {
        throw "The task is not bound to one approved stable/Beta Codex AppUserModelID."
    }
    return $script:HostAppProfiles[$ExactAppId]
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToUpperInvariant()
}

function Get-StringSha256([AllowEmptyString()][string]$Value) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
    }
}

function ConvertTo-WindowsCommandLineArgument([AllowEmptyString()][string]$Value) {
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }

    $quoted = [System.Text.StringBuilder]::new()
    [void]$quoted.Append([char]34)
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]34) {
            [void]$quoted.Append([string]::new([char]92, (2 * $backslashes) + 1))
            [void]$quoted.Append([char]34)
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$quoted.Append([string]::new([char]92, $backslashes))
            $backslashes = 0
        }
        [void]$quoted.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$quoted.Append([string]::new([char]92, 2 * $backslashes))
    }
    [void]$quoted.Append([char]34)
    return $quoted.ToString()
}

function Write-AtomicJson([string]$Path, [object]$Value) {
    $directory = Split-Path -Parent $Path
    [void](New-Item -ItemType Directory -Force -Path $directory)
    $json = $Value | ConvertTo-Json -Depth 32
    $temporary = Join-Path $directory ((Split-Path -Leaf $Path) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, $script:Utf8NoBom)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function ConvertTo-CanonicalJson([object]$Value) {
    return ($Value | ConvertTo-Json -Depth 32 -Compress)
}

function Assert-TaskId([string]$Value) {
    if ($Value -notmatch '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$') {
        throw "TaskId must be an exact Codex task UUID."
    }
}

function Get-BindingPath([string]$ExactTaskId) {
    return Join-Path (Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "bindings") ($ExactTaskId.ToLowerInvariant() + ".json")
}

function Read-SealedBinding([string]$Path) {
    $record = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($record.schema -ne $script:Schema -or $null -eq $record.payload) {
        throw "Goal recovery binding schema mismatch: $Path"
    }
    $actual = Get-StringSha256 (ConvertTo-CanonicalJson $record.payload)
    if ($actual -ne [string]$record.payload_sha256) {
        throw "Goal recovery binding seal mismatch: $Path"
    }
    Assert-TaskId ([string]$record.payload.task_id)
    if ($record.payload.task_uri_sha256 -ne (Get-StringSha256 ("codex://threads/" + [string]$record.payload.task_id))) {
        throw "Goal recovery task URI seal mismatch: $Path"
    }
    return $record
}

function Copy-BindingPayload([object]$Record) {
    $payload = [ordered]@{}
    foreach ($property in $Record.payload.PSObject.Properties) {
        $payload[$property.Name] = $property.Value
    }
    return $payload
}

function Get-BindingRevision([object]$Record) {
    if ($null -eq $Record) { return 0 }
    $revision = $Record.payload.PSObject.Properties["binding_revision"]
    if ($null -eq $revision -or [int]$revision.Value -lt 1) { return 1 }
    return [int]$revision.Value
}

function Write-BindingEventReceipt(
    [string]$Event,
    [string]$ExactTaskId,
    [string]$ProjectId,
    [string]$EvidenceSessionId,
    [int]$Revision,
    [string]$CorrelationId,
    [string]$BeforeBindingSha256,
    [string]$AfterBindingSha256,
    [string]$Reason
) {
    $receiptDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "receipts"
    [void](New-Item -ItemType Directory -Force -Path $receiptDirectory)
    $receipt = [ordered]@{
        schema = $script:BindingEventSchema
        status = "PASS"
        event = $Event
        manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"
        binding_scope = "MUTABLE_EXACT_TASK_ROW"
        task_id = $ExactTaskId
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        binding_revision = $Revision
        correlation_id = $CorrelationId
        before_binding_sha256 = $BeforeBindingSha256
        after_binding_sha256 = $AfterBindingSha256
        reason = $Reason
        another_binding_mutated = $false
        task_opened = $false
        prompt_submitted = $false
        lifecycle_mutated = $false
        candidate_hil_or_pointer_mutated = $false
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $path = Join-Path $receiptDirectory (
        "BINDING_" + $ExactTaskId.ToLowerInvariant() + "_R" +
        $Revision.ToString("D6") + "_" + $CorrelationId + ".json"
    )
    Write-AtomicJson $path $receipt
    return [ordered]@{
        path = $path
        file_sha256 = Get-Sha256 $path
        correlation_id = $CorrelationId
        binding_revision = $Revision
    }
}

function Set-ExactBindingTombstone(
    [object]$Record,
    [string]$GoalStatus,
    [string]$Reason
) {
    $exactTaskId = [string]$Record.payload.task_id
    Assert-TaskId $exactTaskId
    $path = Get-BindingPath $exactTaskId
    $beforeSha256 = Get-Sha256 $path
    $revision = (Get-BindingRevision $Record) + 1
    $correlationId = "binding_" + (Get-StringSha256 (
        $exactTaskId + "|" + $revision + "|TOMBSTONE|" + $beforeSha256
    )).Substring(0, 40).ToLowerInvariant()
    $payload = Copy-BindingPayload $Record
    $payload.state = "TOMBSTONED_GOAL_NOT_ACTIVE"
    $payload.binding_revision = $revision
    $payload.previous_binding_sha256 = $beforeSha256
    $payload.last_binding_correlation_id = $correlationId
    $payload.terminal_goal_status = $GoalStatus
    $payload.terminal_reason = $Reason
    $payload.tombstoned_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $closed = [ordered]@{
        schema = $script:Schema
        payload = $payload
        payload_sha256 = Get-StringSha256 (ConvertTo-CanonicalJson $payload)
    }
    Write-AtomicJson $path $closed
    $afterSha256 = Get-Sha256 $path
    $eventReceipt = Write-BindingEventReceipt `
        -Event "GOAL_BINDING_TOMBSTONED" `
        -ExactTaskId $exactTaskId `
        -ProjectId ([string]$payload.project_id) `
        -EvidenceSessionId ([string]$payload.evidence_session_id) `
        -Revision $revision `
        -CorrelationId $correlationId `
        -BeforeBindingSha256 $beforeSha256 `
        -AfterBindingSha256 $afterSha256 `
        -Reason $Reason
    return [ordered]@{
        state = [string]$payload.state
        task_id = $exactTaskId
        project_id = [string]$payload.project_id
        evidence_session_id = [string]$payload.evidence_session_id
        goal_status = $GoalStatus
        binding_revision = $revision
        binding_sha256 = $afterSha256
        event_receipt = $eventReceipt
        another_binding_mutated = $false
        retry_allowed = $false
    }
}

function Get-RecoveryAttemptCorrelationId(
    [string]$ExactTaskId,
    [string]$BootIdSha256,
    [string]$BindingSha256,
    [int]$Attempt,
    [string]$ManagerRunCorrelationId
) {
    return "recovery_" + (Get-StringSha256 (
        $ExactTaskId + "|" + $BootIdSha256 + "|" + $BindingSha256 + "|" +
        $Attempt + "|" + $ManagerRunCorrelationId
    )).Substring(0, 40).ToLowerInvariant()
}

function Get-RecoveryAttemptReceiptPath(
    [string]$ExactTaskId,
    [string]$BootIdSha256,
    [int]$Attempt,
    [string]$CorrelationId
) {
    $receiptDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "receipts"
    return Join-Path $receiptDirectory (
        "RECOVERY_" + $ExactTaskId.ToLowerInvariant() + "_" +
        $BootIdSha256.Substring(0, 16).ToLowerInvariant() + "_A" +
        $Attempt.ToString("D2") + "_" + $CorrelationId + ".json"
    )
}

function Read-RecoveryAttemptReceipt(
    [string]$Path,
    [string]$ExpectedCorrelationId,
    [string]$ExpectedTaskId,
    [string]$ExpectedBootIdSha256,
    [int]$ExpectedAttempt
) {
    $receipt = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if (
        [string]$receipt.schema -ne $script:RecoveryAttemptSchema -or
        [string]$receipt.correlation_id -ne $ExpectedCorrelationId -or
        [string]$receipt.task_id -ne $ExpectedTaskId -or
        [string]$receipt.boot_id_sha256 -ne $ExpectedBootIdSha256 -or
        [int]$receipt.attempt -ne $ExpectedAttempt
    ) {
        throw "An existing Goal recovery attempt receipt does not match its exact correlation envelope."
    }
    return $receipt
}

function Set-ExactBindingRecoveryKillSwitch(
    [object]$Record,
    [string]$FailureCorrelationId,
    [string]$Reason
) {
    $exactTaskId = [string]$Record.payload.task_id
    Assert-TaskId $exactTaskId
    $path = Get-BindingPath $exactTaskId
    $beforeSha256 = Get-Sha256 $path
    $revision = (Get-BindingRevision $Record) + 1
    $correlationId = "binding_" + (Get-StringSha256 (
        $exactTaskId + "|" + $revision + "|RECOVERY_KILL_SWITCH|" +
        $beforeSha256 + "|" + $FailureCorrelationId
    )).Substring(0, 40).ToLowerInvariant()
    $payload = Copy-BindingPayload $Record
    $payload.state = "RECOVERY_KILL_SWITCHED"
    $payload.binding_revision = $revision
    $payload.previous_binding_sha256 = $beforeSha256
    $payload.last_binding_correlation_id = $correlationId
    $payload.recovery_failure_correlation_id = $FailureCorrelationId
    $payload.kill_switch_scope = "EXACT_TASK_BINDING_ONLY"
    $payload.kill_switch_reason = $Reason
    $payload.kill_switched_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $closed = [ordered]@{
        schema = $script:Schema
        payload = $payload
        payload_sha256 = Get-StringSha256 (ConvertTo-CanonicalJson $payload)
    }
    Write-AtomicJson $path $closed
    $afterSha256 = Get-Sha256 $path
    $eventReceipt = Write-BindingEventReceipt `
        -Event "GOAL_BINDING_RECOVERY_KILL_SWITCHED" `
        -ExactTaskId $exactTaskId `
        -ProjectId ([string]$payload.project_id) `
        -EvidenceSessionId ([string]$payload.evidence_session_id) `
        -Revision $revision `
        -CorrelationId $correlationId `
        -BeforeBindingSha256 $beforeSha256 `
        -AfterBindingSha256 $afterSha256 `
        -Reason $Reason
    return [ordered]@{
        state = [string]$payload.state
        task_id = $exactTaskId
        binding_revision = $revision
        binding_sha256 = $afterSha256
        event_receipt = $eventReceipt
        kill_switch_scope = "EXACT_TASK_BINDING_ONLY"
        another_binding_mutated = $false
        retry_allowed = $false
    }
}

function Read-TaskHostProfile([object]$TaskBinding) {
    $preparationPath = (Resolve-Path -LiteralPath ([string]$TaskBinding.preparation_receipt)).Path
    if ((Get-Sha256 $preparationPath) -ne [string]$TaskBinding.preparation_receipt_sha256) {
        throw "The exact task preparation receipt seal drifted."
    }
    $preparation = Get-Content -LiteralPath $preparationPath -Raw | ConvertFrom-Json
    $profile = Get-HostAppProfile ([string]$preparation.target.app_id)
    if (
        $preparation.schema -ne "evidence-lane.codex-restart-preparation.v2" -or
        $preparation.task_id -ne [string]$TaskBinding.task_id -or
        $preparation.project_id -ne [string]$TaskBinding.project_id -or
        $preparation.evidence_session_id -ne [string]$TaskBinding.evidence_session_id -or
        $preparation.target.host_application -ne [string]$profile.host_application -or
        $preparation.target.package_family_name -ne [string]$profile.package_family_name
    ) {
        throw "The task preparation receipt does not bind one exact approved Codex host."
    }
    return [ordered]@{
        app_id = [string]$profile.app_id
        host_application = [string]$profile.host_application
        package_name = [string]$profile.package_name
        package_family_name = [string]$profile.package_family_name
        start_app_name = [string]$profile.start_app_name
        preparation_receipt_sha256 = Get-Sha256 $preparationPath
    }
}

function Resolve-TaskBindingRuntimeSelector([object]$TaskBinding) {
    $authorityProperty = $TaskBinding.PSObject.Properties["restart_authority"]
    if ($null -eq $authorityProperty -or $null -eq $authorityProperty.Value) {
        return $script:CanonicalStableSelector
    }
    $authority = $authorityProperty.Value
    $selector = if (
        [string]$authority.mode -in @(
            "LOCAL_TEST_STAGED_INSTALL_PLUS_CAS_COMMIT",
            "LOCAL_TEST_DISABLED_HOOK_RECOVERY_INSTALL_RECEIPT"
        )
    ) {
        [string]$authority.candidate_selector
    }
    elseif ([string]$authority.mode -ceq "GIT_STABLE_INSTALL_RECEIPT") {
        $script:CanonicalStableSelector
    }
    else {
        throw "The task binding has an unsupported restart authority mode."
    }
    Assert-RecoveryRuntimeSelector $selector
    return $selector
}

function Resolve-GoalBindingRuntimeSelector([object]$GoalBinding) {
    $selectorProperty = $GoalBinding.payload.PSObject.Properties["runtime_plugin_selector"]
    $selector = if (
        $null -ne $selectorProperty -and
        -not [string]::IsNullOrWhiteSpace([string]$selectorProperty.Value)
    ) {
        [string]$selectorProperty.Value
    }
    else {
        $script:CanonicalStableSelector
    }
    Assert-RecoveryRuntimeSelector $selector
    return $selector
}

function Read-TaskLocalRecoveryAuthority([object]$TaskBinding) {
    $authority = $TaskBinding.restart_authority
    if ([string]$authority.mode -cne "LOCAL_TEST_DISABLED_HOOK_RECOVERY_INSTALL_RECEIPT") {
        return $null
    }
    $path = [string]$authority.three_slot_registry
    $expectedSha256 = [string]$authority.three_slot_registry_sha256
    if (
        [string]::IsNullOrWhiteSpace($path) -or
        $expectedSha256 -cnotmatch '^[A-F0-9]{64}$' -or
        [string]$authority.mutable_local_selector -cne $script:LocalTestingSelector -or
        [string]$authority.branch_commit_recovery_selector -cne $script:LocalRecoverySelector -or
        [string]$authority.mutable_local_failure_target -cne $script:LocalRecoverySelector -or
        $authority.mutable_local_failure_never_targets_main_git -ne $true -or
        $authority.branch_commit_recovery_remains_prior_checkpoint -ne $true -or
        $authority.branch_commit_recovery_byte_identical_before_checkpoint -ne $false -or
        $authority.pre_3_0_recovery_allowed -ne $false
    ) {
        throw "The branch-commit recovery authority is incomplete."
    }
    $exact = (Resolve-Path -LiteralPath $path).Path
    $observedSha256 = Get-Sha256 $exact
    $registry = Get-Content -LiteralPath $exact -Raw | ConvertFrom-Json
    $main = $registry.slots.'main-git-release'
    $branch = $registry.slots.'branch-commit-recovery'
    $local = $registry.slots.'mutable-local-testing'
    if (
        $observedSha256 -cne $expectedSha256 -or
        $registry.schema -cne "evidence-lane.codex-three-slot-registry.v1" -or
        $registry.status -cne "PASS" -or
        [int]$registry.exact_live_slot_count -ne 3 -or
        [string]$registry.active_slot -cne "mutable-local-testing" -or
        [string]$registry.failure_target_slot -cne "branch-commit-recovery" -or
        [string]$main.plugin_selector -cne $script:CanonicalStableSelector -or
        [string]$branch.plugin_selector -cne $script:LocalRecoverySelector -or
        [string]$local.plugin_selector -cne $script:LocalTestingSelector -or
        $main.enabled -ne $false -or
        $branch.enabled -ne $false -or
        $local.enabled -ne $true -or
        $branch.byte_frozen -ne $true -or
        $local.byte_frozen -ne $false -or
        $registry.mutable_local_failure_never_targets_main_git -ne $true -or
        $registry.pre_3_0_fallback_allowed -ne $false -or
        $registry.candidate_created_or_accepted -ne $false -or
        $registry.pointer_moved -ne $false -or
        $registry.hil_inferred -ne $false
    ) {
        throw "The three-slot recovery authority no longer matches its sealed registry."
    }
    return [ordered]@{
        schema = "evidence-lane.codex-goal-branch-recovery-authority.v1"
        registry_path = $exact
        registry_sha256 = $observedSha256
        primary_selector = $script:LocalTestingSelector
        recovery_selector = $script:LocalRecoverySelector
        byte_identical = $false
        recovery_is_prior_branch_checkpoint = $true
        recovery_enabled = $false
        main_git_recovery_allowed_for_mutable_local_failure = $false
        pre_3_0_recovery_allowed = $false
        selector_switch_requires_exact_registry_and_host_restart = $true
        restart_loop_allowed = $false
    }
}

function Read-GoalLocalRecoveryAuthority([object]$GoalBinding) {
    $property = $GoalBinding.payload.PSObject.Properties["local_recovery_authority"]
    if ($null -eq $property -or $null -eq $property.Value) {
        return $null
    }
    $authority = $property.Value
    $exact = (Resolve-Path -LiteralPath ([string]$authority.registry_path)).Path
    if (
        (Get-Sha256 $exact) -cne [string]$authority.registry_sha256 -or
        [string]$authority.schema -cne "evidence-lane.codex-goal-branch-recovery-authority.v1" -or
        [string]$authority.primary_selector -cne $script:LocalTestingSelector -or
        [string]$authority.recovery_selector -cne $script:LocalRecoverySelector -or
        $authority.byte_identical -ne $false -or
        $authority.recovery_is_prior_branch_checkpoint -ne $true -or
        $authority.recovery_enabled -ne $false -or
        $authority.main_git_recovery_allowed_for_mutable_local_failure -ne $false -or
        $authority.pre_3_0_recovery_allowed -ne $false -or
        $authority.selector_switch_requires_exact_registry_and_host_restart -ne $true -or
        $authority.restart_loop_allowed -ne $false
    ) {
        throw "The Goal-bound branch-commit recovery authority drifted."
    }
    return $authority
}

function Resolve-GoalBindingHostProfile([object]$GoalBinding) {
    $hostProperty = $GoalBinding.payload.PSObject.Properties["host_application"]
    if ($null -ne $hostProperty -and $null -ne $hostProperty.Value) {
        $profile = Get-HostAppProfile ([string]$hostProperty.Value.app_id)
        if (
            [string]$hostProperty.Value.host_application -ne [string]$profile.host_application -or
            [string]$hostProperty.Value.package_family_name -ne [string]$profile.package_family_name
        ) {
            throw "The sealed Goal binding host identity no longer matches the approved host profile."
        }
        return $profile
    }
    $taskBindingPath = (Resolve-Path -LiteralPath ([string]$GoalBinding.payload.task_binding_receipt)).Path
    $taskBindingFileSha256 = Get-Sha256 $taskBindingPath
    $taskBinding = Get-Content -LiteralPath $taskBindingPath -Raw | ConvertFrom-Json
    $taskBindingWasSuperseded = (
        $taskBindingFileSha256 -ne [string]$GoalBinding.payload.task_binding_receipt_sha256
    )
    if (
        $taskBinding.schema -ne "evidence-lane.codex-task-binding.v1" -or
        $taskBinding.state -ne "EXACT_TASK_BINDING_PREPARED" -or
        $taskBinding.claim_scope -ne "EXACT_CODEX_THREAD_ID_ONLY" -or
        $taskBinding.task_id -ne [string]$GoalBinding.payload.task_id -or
        $taskBinding.project_id -ne [string]$GoalBinding.payload.project_id -or
        $taskBinding.evidence_session_id -ne [string]$GoalBinding.payload.evidence_session_id -or
        $taskBinding.candidate_created_or_accepted -ne $false -or
        $taskBinding.pointer_moved -ne $false -or
        $taskBinding.hil_inferred -ne $false
    ) {
        throw "The current task-binding receipt cannot safely supersede the legacy Goal binding."
    }
    if (
        $taskBindingWasSuperseded -and
        [DateTimeOffset]::Parse([string]$taskBinding.prepared_at_utc) -le
            [DateTimeOffset]::Parse([string]$GoalBinding.payload.registered_at_utc)
    ) {
        throw "A changed task-binding receipt is not a newer exact-task supersession."
    }
    return Read-TaskHostProfile $taskBinding
}

function Get-ExactCodexLauncher() {
    $command = Get-Command codex.cmd -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "The supported Codex command launcher is unavailable."
    }
    $commandPath = [IO.Path]::GetFullPath([string]$command.Source)
    $commandRoot = Split-Path -Parent $commandPath
    $codexJs = Join-Path $commandRoot "node_modules\@openai\codex\bin\codex.js"
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($null -eq $node -or -not (Test-Path -LiteralPath $codexJs -PathType Leaf)) {
        throw "The Codex app-server launcher is incomplete."
    }
    return [ordered]@{
        command = $commandPath
        command_sha256 = Get-Sha256 $commandPath
        node = [IO.Path]::GetFullPath([string]$node.Source)
        node_sha256 = Get-Sha256 ([string]$node.Source)
        codex_js = [IO.Path]::GetFullPath($codexJs)
        codex_js_sha256 = Get-Sha256 $codexJs
    }
}

function Read-AppServerResponse([Diagnostics.Process]$Process, [int]$RequestId, [int]$TimeoutSeconds) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $remaining = [Math]::Max(1, [int]($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds)
        $readTask = $Process.StandardOutput.ReadLineAsync()
        if (-not $readTask.Wait($remaining)) {
            throw "Timed out waiting for Codex app-server request $RequestId."
        }
        $line = $readTask.Result
        if ($null -eq $line) {
            throw "Codex app-server closed before request $RequestId completed."
        }
        try {
            $message = $line | ConvertFrom-Json
        }
        catch {
            continue
        }
        $idProperty = $message.PSObject.Properties["id"]
        if ($null -ne $idProperty -and [int]$idProperty.Value -eq $RequestId) {
            $errorProperty = $message.PSObject.Properties["error"]
            if ($null -ne $errorProperty -and $null -ne $errorProperty.Value) {
                throw "Codex app-server request $RequestId failed: $($errorProperty.Value.message)"
            }
            return $message.PSObject.Properties["result"].Value
        }
    }
    throw "Timed out waiting for Codex app-server request $RequestId."
}

function Invoke-AppServerRequest(
    [Diagnostics.Process]$Process,
    [int]$RequestId,
    [string]$Method,
    [object]$Params,
    [int]$TimeoutSeconds
) {
    $request = [ordered]@{
        method = $Method
        id = $RequestId
        params = $Params
    }
    $Process.StandardInput.WriteLine((ConvertTo-CanonicalJson $request))
    $Process.StandardInput.Flush()
    return Read-AppServerResponse `
        -Process $Process `
        -RequestId $RequestId `
        -TimeoutSeconds $TimeoutSeconds
}

function Assert-RecoveryRuntimeSelector([string]$Selector) {
    if (
        $Selector -cne $script:CanonicalStableSelector -and
        $Selector -cne $script:LocalTestingSelector -and
        $Selector -cne $script:LocalRecoverySelector
    ) {
        throw "The Goal recovery runtime selector is outside the approved stable/local allowlist."
    }
}

function Invoke-CodexGoalProbe(
    [string]$ExactTaskId,
    [string]$ExpectedPluginSelector = $script:CanonicalStableSelector
) {
    Assert-TaskId $ExactTaskId
    Assert-RecoveryRuntimeSelector $ExpectedPluginSelector
    $launcher = Get-ExactCodexLauncher
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = [string]$launcher.node
    $start.Arguments = (ConvertTo-WindowsCommandLineArgument ([string]$launcher.codex_js)) + " app-server --stdio"
    $start.UseShellExecute = $false
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.CreateNoWindow = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    [void]$process.Start()
    try {
        $initialize = [ordered]@{
            method = "initialize"
            id = 0
            params = [ordered]@{
                clientInfo = [ordered]@{
                    name = "evidence_lane_goal_recovery"
                    title = "Evidence Lane Goal Recovery"
                    version = "3.0.0"
                }
            }
        }
        $process.StandardInput.WriteLine((ConvertTo-CanonicalJson $initialize))
        $process.StandardInput.Flush()
        [void](Read-AppServerResponse -Process $process -RequestId 0 -TimeoutSeconds 15)
        $process.StandardInput.WriteLine((ConvertTo-CanonicalJson ([ordered]@{ method = "initialized"; params = [ordered]@{} })))
        $threadResult = Invoke-AppServerRequest `
            -Process $process `
            -RequestId 1 `
            -Method "thread/read" `
            -Params ([ordered]@{ threadId = $ExactTaskId; includeTurns = $false }) `
            -TimeoutSeconds 20
        $goalResult = Invoke-AppServerRequest `
            -Process $process `
            -RequestId 2 `
            -Method "thread/goal/get" `
            -Params ([ordered]@{ threadId = $ExactTaskId }) `
            -TimeoutSeconds 15
        if ($null -eq $threadResult.thread -or [string]$threadResult.thread.id -ne $ExactTaskId) {
            throw "Codex returned a mismatched persisted task."
        }
        if ($null -eq $goalResult.goal -or [string]$goalResult.goal.threadId -ne $ExactTaskId) {
            throw "The exact Codex task has no persisted Goal."
        }
        [void](Invoke-AppServerRequest `
            -Process $process `
            -RequestId 3 `
            -Method "config/mcpServer/reload" `
            -Params ([ordered]@{}) `
            -TimeoutSeconds 30)
        $pluginResult = Invoke-AppServerRequest `
            -Process $process `
            -RequestId 4 `
            -Method "plugin/list" `
            -Params ([ordered]@{
                cwds = @([string]$threadResult.thread.cwd)
                marketplaceKinds = @("local")
            }) `
            -TimeoutSeconds 30
        $pluginMatches = @(
            foreach ($marketplace in @($pluginResult.marketplaces)) {
                foreach ($plugin in @($marketplace.plugins)) {
                    if ([string]$plugin.id -ceq $ExpectedPluginSelector) {
                        [ordered]@{
                            marketplace_name = [string]$marketplace.name
                            marketplace_path = if ($null -eq $marketplace.path) { $null } else { [string]$marketplace.path }
                            plugin = $plugin
                        }
                    }
                }
            }
        )
        if ($pluginMatches.Count -ne 1) {
            throw "The isolated Codex app-server did not resolve the one exact bound Evidence Lane runtime plugin."
        }
        $pluginMatch = $pluginMatches[0]
        if ($pluginMatch.plugin.installed -ne $true -or $pluginMatch.plugin.enabled -ne $true) {
            throw "The exact bound Evidence Lane runtime plugin is not both installed and enabled."
        }
        $mcpStatus = Invoke-AppServerRequest `
            -Process $process `
            -RequestId 5 `
            -Method "mcpServerStatus/list" `
            -Params ([ordered]@{
                detail = "full"
                limit = 100
            }) `
            -TimeoutSeconds 120
        $evidenceServers = @(
            @($mcpStatus.data) | Where-Object { [string]$_.name -ceq "evidence-lane" }
        )
        if ($evidenceServers.Count -ne 1) {
            throw "The isolated Codex app-server did not resolve exactly one Evidence Lane MCP server."
        }
        $evidenceServer = $evidenceServers[0]
        $toolCount = @($evidenceServer.tools.PSObject.Properties).Count
        if ($toolCount -ne 87) {
            throw "The Evidence Lane MCP catalog did not expose the exact 87-tool contract."
        }
        $governedResourceUri = "ui://evidence-lane/governed-console-v6.html"
        $governedResources = @(
            @($evidenceServer.resources) |
                Where-Object { [string]$_.uri -ceq $governedResourceUri }
        )
        if ($governedResources.Count -ne 1) {
            throw "The Evidence Lane governed-console resource is missing or duplicated."
        }
        $resourceResult = Invoke-AppServerRequest `
            -Process $process `
            -RequestId 6 `
            -Method "mcpServer/resource/read" `
            -Params ([ordered]@{
                server = "evidence-lane"
                uri = $governedResourceUri
            }) `
            -TimeoutSeconds 60
        if (@($resourceResult.contents).Count -lt 1) {
            throw "The Evidence Lane governed-console resource returned no content."
        }
        $goal = $goalResult.goal
        return [ordered]@{
            task_id = $ExactTaskId
            task_status = [string]$threadResult.thread.status.type
            task_cwd_sha256 = Get-StringSha256 ([string]$threadResult.thread.cwd)
            raw_task_cwd_stored = $false
            goal_status = [string]$goal.status
            goal_objective_sha256 = Get-StringSha256 ([string]$goal.objective)
            raw_goal_objective_stored = $false
            goal_tokens_used = [long]$goal.tokensUsed
            goal_time_used_seconds = [long]$goal.timeUsedSeconds
            goal_updated_at = [long]$goal.updatedAt
            query_route = "CODEX_APP_SERVER_THREAD_READ_PLUS_THREAD_GOAL_GET"
            runtime_prewarm = [ordered]@{
                status = "PASS"
                route = "ISOLATED_OFFICIAL_CODEX_APP_SERVER"
                app_server_process_hidden = $true
                config_mcp_server_reload_request_passed = $true
                canonical_plugin_selector = $ExpectedPluginSelector
                bound_plugin_selector = $ExpectedPluginSelector
                canonical_plugin_installed = [bool]$pluginMatch.plugin.installed
                canonical_plugin_enabled = [bool]$pluginMatch.plugin.enabled
                canonical_plugin_local_version = [string]$pluginMatch.plugin.localVersion
                marketplace_name = [string]$pluginMatch.marketplace_name
                marketplace_path_sha256 = if ($null -eq $pluginMatch.marketplace_path) { $null } else { Get-StringSha256 ([string]$pluginMatch.marketplace_path) }
                raw_marketplace_path_stored = $false
                mcp_server_name = [string]$evidenceServer.name
                mcp_server_version = [string]$evidenceServer.serverInfo.version
                exact_tool_count = $toolCount
                mcp_inventory_scope = "ISOLATED_APP_SERVER_GLOBAL_RUNTIME"
                task_continuity_scope = "PERSISTED_EXACT_THREAD_AND_GOAL"
                thread_scoped_mcp_inventory_available = $false
                governed_resource_uri = $governedResourceUri
                governed_resource_payload_sha256 = Get-StringSha256 (ConvertTo-CanonicalJson $resourceResult)
                live_desktop_process_reconfigured = $false
                live_desktop_control_plane = "HOST_CAPABILITY_UNAVAILABLE_WINDOWS_APP_SERVER_DAEMON"
                live_host_next_active_turn_refresh_claimed = $false
            }
            thread_resume_invoked = $false
            turn_started = $false
            prompt_injected = $false
            app_restarted = $false
            restart_fallback_invoked = $false
            launcher = $launcher
        }
    }
    finally {
        try { $process.StandardInput.Close() } catch {}
        if (-not $process.HasExited) { $process.Kill() }
        $process.Dispose()
    }
}

function Assert-CodexThreadProtocol([object]$HostProfile) {
    $protocolPath = "Registry::HKEY_CLASSES_ROOT\codex"
    if (-not (Test-Path -LiteralPath $protocolPath)) {
        throw "The registered Codex desktop protocol is unavailable."
    }
    $protocol = Get-Item -LiteralPath $protocolPath
    if ($null -eq $protocol.GetValue("URL Protocol", $null)) {
        throw "The registered Codex desktop protocol is invalid."
    }
    $registeredApps = @(
        Get-StartApps |
            Where-Object {
                $_.AppID -ceq [string]$HostProfile.app_id -and
                $_.Name -ceq [string]$HostProfile.start_app_name
            }
    )
    if ($registeredApps.Count -ne 1) {
        throw "The exact bound Codex app registration is unavailable."
    }
    $packages = @(
        Get-AppxPackage -Name ([string]$HostProfile.package_name) |
            Where-Object {
                $_.PackageFamilyName -ceq [string]$HostProfile.package_family_name -and
                $_.Status -eq "Ok"
            }
    )
    if ($packages.Count -ne 1) {
        throw "The exact bound Codex package is unavailable or unhealthy."
    }
}

function Invoke-CodexTaskActivation([string]$ExactTaskId, [object]$HostProfile) {
    Assert-CodexThreadProtocol $HostProfile
    if (-not ("EvidenceLaneGoalRecoveryActivation" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class EvidenceLaneGoalRecoveryActivation {
    [ComImport, Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C")]
    private class ApplicationActivationManager {}
    [ComImport, Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IApplicationActivationManager {
        IntPtr ActivateApplication([MarshalAs(UnmanagedType.LPWStr)] string appUserModelId, [MarshalAs(UnmanagedType.LPWStr)] string arguments, uint options, out uint processId);
        IntPtr ActivateForFile([MarshalAs(UnmanagedType.LPWStr)] string appUserModelId, IntPtr itemArray, [MarshalAs(UnmanagedType.LPWStr)] string verb, out uint processId);
        IntPtr ActivateForProtocol([MarshalAs(UnmanagedType.LPWStr)] string appUserModelId, IntPtr itemArray, out uint processId);
    }
    public static uint Activate(string appUserModelId, string arguments) {
        IApplicationActivationManager manager = (IApplicationActivationManager)new ApplicationActivationManager();
        uint processId;
        IntPtr result = manager.ActivateApplication(appUserModelId, arguments, 0, out processId);
        if (result.ToInt64() != 0) Marshal.ThrowExceptionForHR(result.ToInt32());
        return processId;
    }
}
'@
    }
    $taskUri = "codex://threads/$ExactTaskId"
    $processId = [EvidenceLaneGoalRecoveryActivation]::Activate(
        [string]$HostProfile.app_id,
        $taskUri
    )
    return [ordered]@{
        app_id = [string]$HostProfile.app_id
        host_application = [string]$HostProfile.host_application
        desktop_release_channel = [string]$HostProfile.desktop_release_channel
        available_surfaces = @($HostProfile.available_surfaces)
        governed_surface = [string]$HostProfile.governed_surface
        chatgpt_surface_governed = [bool]$HostProfile.chatgpt_surface_governed
        package_family_name = [string]$HostProfile.package_family_name
        task_uri_sha256 = Get-StringSha256 $taskUri
        activation_request_process_id = [uint32]$processId
        coordinate_clicking_used = $false
        request_observed = $true
    }
}

function Get-OptionalPropertyText([object]$Object, [string]$Name) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) {
        return ""
    }
    return [string]$property.Value
}

function Get-FallbackReleaseAuthority([object]$Registry, [object]$Fallback) {
    $acceptedPv = [string]$Registry.accepted_pv
    $acceptedGeneration = [int]$Registry.accepted_generation
    $acceptedPackageSha256 = ([string]$Registry.accepted_package_sha256).ToUpperInvariant()
    $acceptedPluginVersionText = Get-OptionalPropertyText -Object $Registry -Name "accepted_plugin_version"
    $acceptedPluginVersion = if (
        -not [string]::IsNullOrWhiteSpace($acceptedPluginVersionText)
    ) {
        $acceptedPluginVersionText
    }
    else {
        [string]$Fallback.plugin_version
    }
    $selector = [string]$Fallback.plugin_selector
    $selectorClass = if ($selector -ceq $script:GenerationNeutralFallbackSelector) {
        "GENERATION_NEUTRAL_LOCATOR"
    }
    elseif ($selector -match '^evidence-lane-plugin@evidence-lane-pv[1-9][0-9]*-fallback$') {
        "LEGACY_GENERATION_ALIAS_LOCATOR"
    }
    else {
        throw "The fallback selector is not a governed operational locator."
    }
    if (
        $acceptedPv -notmatch '^PV[1-9][0-9]*$' -or
        $acceptedPv -cne ("PV" + $acceptedGeneration) -or
        [string]$Fallback.accepted_pv -cne $acceptedPv -or
        [int]$Fallback.accepted_generation -ne $acceptedGeneration -or
        $acceptedPackageSha256 -notmatch '^[A-F0-9]{64}$' -or
        ([string]$Fallback.package_sha256).ToUpperInvariant() -cne $acceptedPackageSha256 -or
        [string]$Fallback.plugin_version -cne $acceptedPluginVersion -or
        [string]$Fallback.slot_role -cne "fallback" -or
        $Fallback.byte_frozen -ne $true -or
        $Fallback.enabled -ne $false -or
        $Fallback.native_mcp_enabled -ne $false
    ) {
        throw "The fallback release authority does not match the sealed PV/package boundary."
    }
    $optionalHashes = [ordered]@{
        accepted_manifest_sha256 = (Get-OptionalPropertyText -Object $Registry -Name "accepted_manifest_sha256").ToUpperInvariant()
        accepted_universal_pv_package_sha256 = (Get-OptionalPropertyText -Object $Registry -Name "accepted_universal_pv_package_sha256").ToUpperInvariant()
        cache_authority_manifest_sha256 = (Get-OptionalPropertyText -Object $Fallback -Name "cache_authority_manifest_sha256").ToUpperInvariant()
        install_receipt_sha256 = (Get-OptionalPropertyText -Object $Fallback -Name "install_receipt_sha256").ToUpperInvariant()
        plugin_manifest_sha256 = (Get-OptionalPropertyText -Object $Fallback -Name "plugin_manifest_sha256").ToUpperInvariant()
    }
    foreach ($hash in $optionalHashes.Values) {
        if (-not [string]::IsNullOrWhiteSpace([string]$hash) -and [string]$hash -notmatch '^[A-F0-9]{64}$') {
            throw "A sealed fallback authority hash is malformed."
        }
    }
    # Alphabetical property order matches install_codex_stable.py exactly.
    $payload = [ordered]@{
        accepted_generation = $acceptedGeneration
        accepted_manifest_sha256 = [string]$optionalHashes.accepted_manifest_sha256
        accepted_package_sha256 = $acceptedPackageSha256
        accepted_plugin_version = $acceptedPluginVersion
        accepted_pv = $acceptedPv
        accepted_universal_pv_package_sha256 = [string]$optionalHashes.accepted_universal_pv_package_sha256
        byte_frozen = $true
        cache_authority_manifest_sha256 = [string]$optionalHashes.cache_authority_manifest_sha256
        enabled = $false
        install_receipt_sha256 = [string]$optionalHashes.install_receipt_sha256
        native_mcp_enabled = $false
        package_sha256 = $acceptedPackageSha256
        plugin_manifest_sha256 = [string]$optionalHashes.plugin_manifest_sha256
        plugin_version = $acceptedPluginVersion
        schema = "evidence-lane.fallback-release-authority.v1"
        slot_role = "fallback"
    }
    $authoritySha256 = Get-StringSha256 (ConvertTo-CanonicalJson $payload)
    $manifestEvidenceComplete = $true
    foreach ($hash in $optionalHashes.Values) {
        if ([string]::IsNullOrWhiteSpace([string]$hash)) {
            $manifestEvidenceComplete = $false
        }
    }
    $publishedProperty = $Registry.PSObject.Properties["fallback_release_authority"]
    if (
        $null -ne $publishedProperty -and
        [string]$publishedProperty.Value.fallback_authority_sha256 -cne $authoritySha256
    ) {
        throw "The published fallback release authority seal does not match its PV/package/manifest fields."
    }
    return [ordered]@{
        authority_id = "fallback_" + $authoritySha256.Substring(0, 40).ToLowerInvariant()
        fallback_authority_sha256 = $authoritySha256
        manifest_evidence_complete = $manifestEvidenceComplete
        selector = $selector
        selector_class = $selectorClass
        selector_is_authority = $false
        payload = $payload
    }
}

function Read-TwoSlotAuthority([string]$Path) {
    $exact = (Resolve-Path -LiteralPath $Path).Path
    $registry = Get-Content -LiteralPath $exact -Raw | ConvertFrom-Json
    $registrySha256 = Get-Sha256 $exact
    $stable = $registry.slots.'stable-build'
    $fallback = $registry.slots.fallback
    $fallbackAuthority = Get-FallbackReleaseAuthority -Registry $registry -Fallback $fallback
    if (
        $registry.schema -ne "evidence-lane.codex-two-slot-registry.v1" -or
        $registry.status -ne "PASS" -or
        $registry.exact_live_slot_count -ne 2 -or
        $registry.max_enabled_plugin_count -ne 1 -or
        $registry.active_slot -ne "stable-build" -or
        $stable.enabled -ne $true -or
        $stable.byte_frozen -ne $false -or
        $fallback.enabled -ne $false -or
        $fallback.byte_frozen -ne $true -or
        [string]$stable.plugin_selector -eq [string]$fallback.plugin_selector
    ) {
        throw "The exact stable/fallback two-slot authority is not recoverable."
    }
    return [ordered]@{
        authority_scope = "SHARED_RELEASE_SLOT_AUTHORITY"
        task_binding_scope = "SEPARATE_MUTABLE_REGISTRY"
        authority_id = "two_slot_" + $registrySha256.Substring(0, 40).ToLowerInvariant()
        registry_path = $exact
        registry_sha256 = $registrySha256
        release_authority_sha256 = $registrySha256
        registry_origin_identity_used_for_authorization = $false
        accepted_pv = [string]$registry.accepted_pv
        accepted_generation = [int]$registry.accepted_generation
        stable_selector = [string]$stable.plugin_selector
        stable_enabled = $true
        stable_byte_frozen = $false
        fallback_selector = [string]$fallback.plugin_selector
        fallback_selector_class = [string]$fallbackAuthority.selector_class
        fallback_selector_is_authority = $false
        fallback_authority_id = [string]$fallbackAuthority.authority_id
        fallback_authority_sha256 = [string]$fallbackAuthority.fallback_authority_sha256
        fallback_manifest_evidence_complete = [bool]$fallbackAuthority.manifest_evidence_complete
        fallback_enabled = $false
        fallback_byte_frozen = $true
        exact_live_slot_count = 2
        max_enabled_plugin_count = 1
    }
}

function Read-ThreeSlotAuthority([string]$Path, [string]$ExpectedSha256 = "") {
    $exact = (Resolve-Path -LiteralPath $Path).Path
    $registrySha256 = Get-Sha256 $exact
    if (
        -not [string]::IsNullOrWhiteSpace($ExpectedSha256) -and
        $registrySha256 -cne $ExpectedSha256.ToUpperInvariant()
    ) {
        throw "The exact three-slot registry file seal drifted."
    }
    $registry = Get-Content -LiteralPath $exact -Raw | ConvertFrom-Json
    $main = $registry.slots.'main-git-release'
    $branch = $registry.slots.'branch-commit-recovery'
    $local = $registry.slots.'mutable-local-testing'
    if (
        $registry.schema -cne "evidence-lane.codex-three-slot-registry.v1" -or
        $registry.status -cne "PASS" -or
        [int]$registry.exact_live_slot_count -ne 3 -or
        [int]$registry.max_enabled_plugin_count -ne 1 -or
        [int]$registry.max_active_native_mcp_count -ne 1 -or
        [string]$registry.active_slot -cne "mutable-local-testing" -or
        [string]$registry.active_selector -cne $script:LocalTestingSelector -or
        [string]$registry.failure_target_slot -cne "branch-commit-recovery" -or
        $registry.mutable_local_failure_never_targets_main_git -ne $true -or
        $registry.branch_recovery_must_remain_prior_checkpoint_until_commit -ne $true -or
        $registry.pre_3_0_fallback_allowed -ne $false -or
        $registry.obsolete_live_selectors_allowed -ne $false -or
        [string]$main.plugin_selector -cne $script:CanonicalStableSelector -or
        [string]$branch.plugin_selector -cne $script:LocalRecoverySelector -or
        [string]$local.plugin_selector -cne $script:LocalTestingSelector -or
        $main.enabled -ne $false -or
        $branch.enabled -ne $false -or
        $local.enabled -ne $true -or
        $branch.byte_frozen -ne $true -or
        $local.byte_frozen -ne $false -or
        $registry.candidate_created_or_accepted -ne $false -or
        $registry.pointer_moved -ne $false -or
        $registry.hil_inferred -ne $false
    ) {
        throw "The exact main/branch/local three-slot authority is not recoverable."
    }
    return [ordered]@{
        authority_scope = "SHARED_THREE_SLOT_RELEASE_AUTHORITY"
        task_binding_scope = "SEPARATE_MUTABLE_EXACT_TASK_REGISTRY"
        authority_id = "three_slot_" + $registrySha256.Substring(0, 40).ToLowerInvariant()
        registry_path = $exact
        registry_sha256 = $registrySha256
        release_authority_sha256 = $registrySha256
        main_git_selector = [string]$main.plugin_selector
        main_git_version = [string]$main.plugin_version
        branch_recovery_selector = [string]$branch.plugin_selector
        branch_recovery_version = [string]$branch.plugin_version
        branch_recovery_byte_frozen = $true
        mutable_local_selector = [string]$local.plugin_selector
        mutable_local_version = [string]$local.plugin_version
        mutable_local_enabled = $true
        mutable_local_failure_target = [string]$branch.plugin_selector
        mutable_local_failure_never_targets_main_git = $true
        pre_3_0_fallback_allowed = $false
        exact_live_slot_count = 3
        max_enabled_plugin_count = 1
    }
}

function Assert-BindingReleaseAuthority([object]$Binding, [object]$Authority) {
    $releaseProperty = $Binding.slot_authority.PSObject.Properties["release_authority_sha256"]
    $boundReleaseSha256 = if ($null -ne $releaseProperty) {
        [string]$releaseProperty.Value
    }
    else {
        [string]$Binding.slot_authority.registry_sha256
    }
    if (
        [string]::IsNullOrWhiteSpace($boundReleaseSha256) -or
        $boundReleaseSha256 -ne [string]$Authority.release_authority_sha256 -or
        [string]$Binding.slot_authority.branch_recovery_selector -cne
            [string]$Authority.branch_recovery_selector -or
        [string]$Binding.slot_authority.mutable_local_selector -cne
            [string]$Authority.mutable_local_selector -or
        $Binding.slot_authority.mutable_local_failure_never_targets_main_git -ne $true
    ) {
        throw "The mutable task binding must be refreshed against the current sealed three-slot authority."
    }
}

function Install-RecoveryManager() {
    $exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)
    [void](New-Item -ItemType Directory -Force -Path $exactRoot)
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $exactRoot "bindings"))
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $exactRoot "receipts"))
    $durableScript = Join-Path $exactRoot "Manage-EvidenceLaneCodexGoalRecovery.ps1"
    $sourceScript = [IO.Path]::GetFullPath($PSCommandPath)
    if ($sourceScript -cne $durableScript) {
        Copy-Item -LiteralPath $sourceScript -Destination $durableScript -Force
    }
    $powershell = (Get-Command powershell.exe).Source
    $argumentValues = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", $durableScript,
        "-Action", "RecoverAtLogon",
        "-Release", $script:Release,
        "-RecoveryRoot", $exactRoot,
        "-ThreeSlotRegistry", ([IO.Path]::GetFullPath($ThreeSlotRegistry)),
        "-ThreeSlotRegistrySha256", $ThreeSlotRegistrySha256,
        "-ScheduledTaskName", $ScheduledTaskName
    )
    $arguments = ($argumentValues | ForEach-Object { ConvertTo-WindowsCommandLineArgument ([string]$_) }) -join " "
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $existing = Get-ScheduledTask -TaskName $ScheduledTaskName -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $expectedScriptToken = ConvertTo-WindowsCommandLineArgument $durableScript
        $actionMatches = @(
            $existing.Actions |
                Where-Object {
                    [string]$_.Execute -ieq [string]$powershell -and
                    [string]$_.Arguments -like "*$expectedScriptToken*" -and
                    [string]$_.Arguments -like "*-Action RecoverAtLogon*"
                }
        ).Count -eq 1
        if (-not $actionMatches) {
            throw "An unrelated scheduled task already owns the recovery task name."
        }
    }
    $scheduledAction = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask `
        -TaskName $ScheduledTaskName `
        -Action $scheduledAction `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Reopen exact active Evidence Lane governed Codex Goal tasks after Windows logon; never submits a prompt or changes lifecycle state." `
        -Force | Out-Null
    $retainedPriorManagers = @()
    foreach ($priorTask in @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        [string]$_.TaskName -like "Evidence Lane Codex Goal Recovery*" -and
        [string]$_.TaskName -ne $ScheduledTaskName
    })) {
        $ownedAction = @($priorTask.Actions | Where-Object {
            [string]$_.Execute -like "*powershell*" -and
            [string]$_.Arguments -like "*Manage-EvidenceLaneCodexGoalRecovery.ps1*" -and
            [string]$_.Arguments -like "*-Action*RecoverAtLogon*"
        })
        if ($ownedAction.Count -ne 1) {
            continue
        }
        Stop-ScheduledTask -TaskName ([string]$priorTask.TaskName) -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName ([string]$priorTask.TaskName) -ErrorAction Stop | Out-Null
        $retainedPriorManagers += [ordered]@{
            task_name = [string]$priorTask.TaskName
            retained = $true
            disabled = $true
            deleted = $false
        }
    }
    $manager = [ordered]@{
        schema = $script:ManagerSchema
        status = "PASS"
        release = $script:Release
        release_token = $script:ReleaseToken
        helper_audience = "GOVERNED_CODEX_USER"
        public_marketplace_runtime_helper = $true
        maintainer_release_helper = $false
        scope = "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_ON_THIS_WINDOWS_USER"
        durable_script = $durableScript
        durable_script_sha256 = Get-Sha256 $durableScript
        scheduled_task_name = $ScheduledTaskName
        trigger = "AT_LOGON_CURRENT_WINDOWS_USER"
        start_when_available = $true
        max_instances = 1
        scheduler_restart_count = 0
        scheduler_retry_is_authority = $false
        recovery_retry_owner = "IN_PROCESS_EXACT_TASK_BINDING"
        max_recovery_attempts_per_binding = $script:MaxRecoveryAttemptsPerBinding
        recovery_backoff_seconds = $script:RecoveryBackoffSeconds
        kill_switch_scope = "EXACT_TASK_BINDING_ONLY"
        restart_count_used_for_hook_or_helper_causation = $false
        windows_console_policy = "POWERSHELL_WINDOWSTYLE_HIDDEN"
        scheduled_task_window_style = "HIDDEN"
        survives_windows_logon = $true
        prior_versioned_helpers_retained = $true
        prior_versioned_helpers_disabled = $true
        prior_versioned_helpers_deleted = $false
        retained_prior_managers = $retainedPriorManagers
        host_owned_initial_mcp_spawn = "HOST_CAPABILITY_UNAVAILABLE"
        raw_goal_objective_stored = $false
        synthetic_prompt_allowed = $false
        turn_start_allowed = $false
        state_travel_allowed = $false
        hil_or_pointer_mutation_allowed = $false
        installed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-AtomicJson (Join-Path $exactRoot "MANAGER.json") $manager
    return $manager
}

function Get-BootIdSha256() {
    $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")
    return Get-StringSha256 $boot
}

function Test-RecoveredThisBoot([string]$ExactTaskId, [string]$BootIdSha256) {
    $receipts = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "receipts"
    if (-not (Test-Path -LiteralPath $receipts -PathType Container)) { return $false }
    $prefix = "RECOVERY_" + $ExactTaskId.ToLowerInvariant() + "_"
    foreach ($file in Get-ChildItem -LiteralPath $receipts -Filter ($prefix + "*.json") -File) {
        try {
            $receipt = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
            if (
                $receipt.schema -in @($script:RecoverySchema, $script:RecoveryAttemptSchema) -and
                $receipt.boot_id_sha256 -eq $BootIdSha256 -and
                $receipt.activation.request_observed -eq $true
            ) {
                return $true
            }
        }
        catch {}
    }
    return $false
}

if ($Action -eq "Probe") {
    Assert-TaskId $TaskId
    $probe = Invoke-CodexGoalProbe -ExactTaskId $TaskId
    $probeSha256 = Get-StringSha256 (ConvertTo-CanonicalJson $probe)
    [ordered]@{
        status = "PASS"
        state = "ISOLATED_RUNTIME_PREWARM_PROVEN_LIVE_DESKTOP_RELOAD_UNAVAILABLE"
        task_id = $TaskId
        goal_status = [string]$probe.goal_status
        runtime_prewarm = $probe.runtime_prewarm
        probe_sha256 = $probeSha256
        source_mutated = $false
        task_opened = $false
        app_restarted = $false
        prompt_submitted = $false
        lifecycle_mutated = $false
    } | ConvertTo-Json -Depth 32
    exit 0
}

if ($Action -eq "Register") {
    if (-not $TaskBindingReceipt -or -not $ActivePlanTaskId) {
        throw "Register requires -TaskBindingReceipt and -ActivePlanTaskId."
    }
    $exactBindingPath = (Resolve-Path -LiteralPath $TaskBindingReceipt).Path
    $binding = Get-Content -LiteralPath $exactBindingPath -Raw | ConvertFrom-Json
    if (
        $binding.schema -ne "evidence-lane.codex-task-binding.v1" -or
        $binding.state -ne "EXACT_TASK_BINDING_PREPARED" -or
        $binding.claim_scope -ne "EXACT_CODEX_THREAD_ID_ONLY" -or
        $binding.candidate_created_or_accepted -ne $false -or
        $binding.pointer_moved -ne $false -or
        $binding.hil_inferred -ne $false
    ) {
        throw "The exact Codex task binding is not recovery-eligible."
    }
    $TaskId = [string]$binding.task_id
    Assert-TaskId $TaskId
    $taskUriSha256 = Get-StringSha256 "codex://threads/$TaskId"
    if ($binding.task_uri_sha256 -ne $taskUriSha256) {
        throw "The task binding URI seal does not match the exact task."
    }
    $slotAuthority = Read-ThreeSlotAuthority `
        -Path $ThreeSlotRegistry `
        -ExpectedSha256 $ThreeSlotRegistrySha256
    $hostProfile = Read-TaskHostProfile $binding
    $runtimePluginSelector = Resolve-TaskBindingRuntimeSelector $binding
    $localRecoveryAuthority = Read-TaskLocalRecoveryAuthority $binding
    $goalProbe = Invoke-CodexGoalProbe `
        -ExactTaskId $TaskId `
        -ExpectedPluginSelector $runtimePluginSelector
    if ($goalProbe.goal_status -ne "active") {
        throw "Only an active persisted Codex Goal may be registered for logon recovery."
    }
    $manager = Install-RecoveryManager
    $bindingPath = Get-BindingPath $TaskId
    $previousRecord = $null
    $previousBindingSha256 = ""
    if (Test-Path -LiteralPath $bindingPath -PathType Leaf) {
        $previousRecord = Read-SealedBinding $bindingPath
        if (
            [string]$previousRecord.payload.task_id -ne $TaskId -or
            [string]$previousRecord.payload.project_id -ne [string]$binding.project_id -or
            [string]$previousRecord.payload.evidence_session_id -ne [string]$binding.evidence_session_id
        ) {
            throw "An exact task binding cannot be refreshed across project or governed session identity."
        }
        $previousBindingSha256 = Get-Sha256 $bindingPath
    }
    $bindingRevision = (Get-BindingRevision $previousRecord) + 1
    $bindingCorrelationId = "binding_" + (Get-StringSha256 (
        $TaskId + "|" + $bindingRevision + "|REGISTER|" +
        (Get-Sha256 $exactBindingPath) + "|" + $ActivePlanTaskId
    )).Substring(0, 40).ToLowerInvariant()
    $payload = [ordered]@{
        state = "ACTIVE_GOAL_BOUND"
        manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"
        binding_scope = "MUTABLE_EXACT_TASK_ROW"
        binding_revision = $bindingRevision
        previous_binding_sha256 = $previousBindingSha256
        last_binding_correlation_id = $bindingCorrelationId
        project_id = [string]$binding.project_id
        evidence_session_id = [string]$binding.evidence_session_id
        task_id = $TaskId
        governed_host_session_id = [string]$binding.governed_host_session_id
        active_plan_task_id = $ActivePlanTaskId
        canonical_authority = "PLAN_LANE"
        runtime_plugin_selector = $runtimePluginSelector
        local_recovery_authority = $localRecoveryAuthority
        task_binding_receipt = $exactBindingPath
        task_binding_receipt_sha256 = Get-Sha256 $exactBindingPath
        task_uri_sha256 = $taskUriSha256
        host_application = $hostProfile
        goal = $goalProbe
        slot_authority = $slotAuthority
        recovery_law = [ordered]@{
            release = $script:Release
            release_token = $script:ReleaseToken
            helper_audience = "GOVERNED_CODEX_USER"
            public_marketplace_runtime_helper = $true
            maintainer_release_helper = $false
            prior_versioned_helpers_retained = $true
            prior_versioned_helpers_disabled = $true
            prior_versioned_helpers_deleted = $false
            exact_task_only = $true
            all_governed_goal_tasks_supported = $true
            open_exact_task_after_logon = $true
            persisted_goal_must_remain_active = $true
            report_implemented_active_and_queued_after_host_continues = $true
            raw_goal_objective_stored = $false
            synthetic_prompt_allowed = $false
            turn_start_allowed = $false
            thread_resume_writer_allowed = $false
            state_travel_allowed = $false
            candidate_hil_pointer_or_git_mutation_allowed = $false
            main_git_must_remain_disabled_during_mutable_local_testing = $true
            branch_commit_recovery_must_remain_disabled_until_sealed_switch = $true
            branch_commit_recovery_remains_prior_checkpoint = $true
            mutable_local_failure_may_target_only_branch_recovery = $true
            pre_3_0_automatic_recovery_allowed = $false
            recovery_switch_requires_host_restart = $true
            stable_selector_growth_allowed = $false
            exact_bound_host_app_required = $true
            stable_and_beta_hosts_supported = $true
            both_desktop_channels_expose_chatgpt_and_codex_surfaces = $true
            evidence_lane_governs_codex_surface_only = $true
            helper_and_tunnel_console_windows_allowed = $false
            isolated_runtime_prewarm_before_task_open = $true
            exact_task_deeplink_is_primary_hot_reattach = $true
            restart_is_bounded_branch_recovery_only = $true
            restart_loop_allowed = $false
        }
        registered_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $record = [ordered]@{
        schema = $script:Schema
        payload = $payload
        payload_sha256 = Get-StringSha256 (ConvertTo-CanonicalJson $payload)
    }
    Write-AtomicJson $bindingPath $record
    $bindingSha256 = Get-Sha256 $bindingPath
    $bindingEventReceipt = Write-BindingEventReceipt `
        -Event $(if ($null -eq $previousRecord) { "GOAL_BINDING_CREATED" } else { "GOAL_BINDING_REFRESHED" }) `
        -ExactTaskId $TaskId `
        -ProjectId ([string]$binding.project_id) `
        -EvidenceSessionId ([string]$binding.evidence_session_id) `
        -Revision $bindingRevision `
        -CorrelationId $bindingCorrelationId `
        -BeforeBindingSha256 $previousBindingSha256 `
        -AfterBindingSha256 $bindingSha256 `
        -Reason "EXACT_TASK_INVOCATION_REGISTERED"
    [ordered]@{
        status = "PASS"
        state = "ACTIVE_GOAL_REGISTERED_FOR_WINDOWS_LOGON_RECOVERY"
        release = $script:Release
        release_token = $script:ReleaseToken
        helper_audience = "GOVERNED_CODEX_USER"
        task_id = $TaskId
        active_plan_task_id = $ActivePlanTaskId
        task_binding_receipt = $exactBindingPath
        task_binding_receipt_sha256 = Get-Sha256 $exactBindingPath
        binding_path = $bindingPath
        binding_sha256 = $bindingSha256
        binding_revision = $bindingRevision
        binding_correlation_id = $bindingCorrelationId
        binding_event_receipt = $bindingEventReceipt
        manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"
        binding_scope = "MUTABLE_EXACT_TASK_ROW"
        manager = $manager
        goal_status = $goalProbe.goal_status
        main_git_selector = $slotAuthority.main_git_selector
        branch_recovery_selector = $slotAuthority.branch_recovery_selector
        mutable_local_selector = $slotAuthority.mutable_local_selector
        mutable_local_failure_target = $slotAuthority.mutable_local_failure_target
        mutable_local_failure_never_targets_main_git = $true
        local_recovery_selector = if ($null -eq $localRecoveryAuthority) { $null } else { [string]$localRecoveryAuthority.recovery_selector }
        pre_3_0_automatic_recovery_allowed = $false
        host_app_id = [string]$hostProfile.app_id
        host_application = [string]$hostProfile.host_application
        exact_live_slot_count = 3
        app_restarted = $false
        task_opened = $false
        prompt_submitted = $false
        lifecycle_mutated = $false
    } | ConvertTo-Json -Depth 32
    exit 0
}

if ($Action -eq "Unregister") {
    Assert-TaskId $TaskId
    $path = Get-BindingPath $TaskId
    $record = Read-SealedBinding $path
    $payload = [ordered]@{}
    foreach ($property in $record.payload.PSObject.Properties) {
        $payload[$property.Name] = $property.Value
    }
    $payload.state = "CLOSED_PRESERVED_HISTORY"
    $payload.closed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    $closed = [ordered]@{
        schema = $script:Schema
        payload = $payload
        payload_sha256 = Get-StringSha256 (ConvertTo-CanonicalJson $payload)
    }
    Write-AtomicJson $path $closed
    $remaining = @(
        Get-ChildItem -LiteralPath (Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "bindings") -Filter "*.json" -File |
            ForEach-Object { Read-SealedBinding $_.FullName } |
            Where-Object { $_.payload.state -eq "ACTIVE_GOAL_BOUND" }
    )
    if ($remaining.Count -eq 0) {
        Disable-ScheduledTask -TaskName $ScheduledTaskName -ErrorAction SilentlyContinue | Out-Null
    }
    [ordered]@{
        status = "PASS"
        state = "GOAL_RECOVERY_BINDING_CLOSED_HISTORY_PRESERVED"
        task_id = $TaskId
        active_binding_count = $remaining.Count
        scheduled_task_disabled = $remaining.Count -eq 0
    } | ConvertTo-Json -Depth 16
    exit 0
}

if ($Action -eq "RecoverNow") {
    Assert-TaskId $TaskId
    $bindingPath = Get-BindingPath $TaskId
    $record = Read-SealedBinding $bindingPath
    if ($record.payload.state -ne "ACTIVE_GOAL_BOUND") {
        throw "RecoverNow requires one exact active governed Goal binding."
    }
    $boundHostProfile = Resolve-GoalBindingHostProfile $record
    $slotAuthority = Read-ThreeSlotAuthority `
        -Path ([string]$record.payload.slot_authority.registry_path) `
        -ExpectedSha256 ([string]$record.payload.slot_authority.registry_sha256)
    Assert-BindingReleaseAuthority -Binding $record.payload -Authority $slotAuthority
    $runtimePluginSelector = Resolve-GoalBindingRuntimeSelector $record
    $localRecoveryAuthority = Read-GoalLocalRecoveryAuthority $record
    $probe = Invoke-CodexGoalProbe `
        -ExactTaskId $TaskId `
        -ExpectedPluginSelector $runtimePluginSelector
    if ($probe.goal_status -ne "active") {
        $tombstone = Set-ExactBindingTombstone `
            -Record $record `
            -GoalStatus ([string]$probe.goal_status) `
            -Reason "EXPLICIT_RECOVERY_FOUND_GOAL_NOT_ACTIVE"
        [ordered]@{
            status = "PASS"
            state = "EXACT_TASK_BINDING_TOMBSTONED_GOAL_NOT_ACTIVE"
            task_id = $TaskId
            project_id = [string]$record.payload.project_id
            evidence_session_id = [string]$record.payload.evidence_session_id
            goal_status = [string]$probe.goal_status
            tombstone = $tombstone
            another_binding_mutated = $false
            retry_allowed = $false
            task_opened = $false
            prompt_submitted = $false
            lifecycle_mutated = $false
        } | ConvertTo-Json -Depth 32
        exit 0
    }
    $activation = Invoke-CodexTaskActivation `
        -ExactTaskId $TaskId `
        -HostProfile $boundHostProfile
    $receipt = [ordered]@{
        schema = $script:RecoverySchema
        status = "PASS"
        state = "EXACT_TASK_OPEN_REQUESTED_ACTIVE_GOAL_PERSISTED_HOST_CONTINUATION_PENDING"
        recovery_mode = "EXPLICIT_EXACT_TASK_NOW"
        task_id = $TaskId
        project_id = [string]$record.payload.project_id
        evidence_session_id = [string]$record.payload.evidence_session_id
        active_plan_task_id = [string]$record.payload.active_plan_task_id
        host_app_id = [string]$boundHostProfile.app_id
        host_application = [string]$boundHostProfile.host_application
        binding_payload_sha256 = [string]$record.payload_sha256
        main_git_selector = [string]$slotAuthority.main_git_selector
        branch_recovery_selector = [string]$slotAuthority.branch_recovery_selector
        mutable_local_selector = [string]$slotAuthority.mutable_local_selector
        mutable_local_failure_target = [string]$slotAuthority.mutable_local_failure_target
        mutable_local_failure_never_targets_main_git = $true
        local_recovery_selector = if ($null -eq $localRecoveryAuthority) { $null } else { [string]$localRecoveryAuthority.recovery_selector }
        pre_3_0_automatic_recovery_allowed = $false
        goal = $probe
        activation = $activation
        hot_reattach = [ordered]@{
            runtime_prewarm = $probe.runtime_prewarm
            exact_task_deeplink_requested = $true
            live_desktop_control_plane = "HOST_CAPABILITY_UNAVAILABLE_WINDOWS_APP_SERVER_DAEMON"
            app_restart_required = $false
            restart_fallback_invoked = $false
            restart_loop_allowed = $false
        }
        source_mutated = $false
        prompt_submitted = $false
        turn_started = $false
        state_travel_invoked = $false
        candidate_hil_or_pointer_mutated = $false
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $receiptDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "receipts"
    [void](New-Item -ItemType Directory -Force -Path $receiptDirectory)
    $receiptPath = Join-Path $receiptDirectory ("RECOVERY_NOW_" + $TaskId.ToLowerInvariant() + "_" + [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ") + ".json")
    Write-AtomicJson $receiptPath $receipt
    [ordered]@{
        status = "PASS"
        state = [string]$receipt.state
        task_id = $TaskId
        active_plan_task_id = [string]$record.payload.active_plan_task_id
        host_app_id = [string]$boundHostProfile.app_id
        activation = $activation
        receipt_path = $receiptPath
        receipt_sha256 = Get-Sha256 $receiptPath
        prompt_submitted = $false
        lifecycle_mutated = $false
    } | ConvertTo-Json -Depth 32
    exit 0
}

if ($Action -eq "RecoverAtLogon") {
    $mutexName = "Local\EvidenceLaneCodexGoalRecovery-" + (Get-StringSha256 ([IO.Path]::GetFullPath($RecoveryRoot))).Substring(0, 24)
    $mutex = [Threading.Mutex]::new($false, $mutexName)
    $locked = $false
    try {
        $locked = $mutex.WaitOne([TimeSpan]::FromSeconds(10))
        if (-not $locked) { throw "Another Goal recovery run already owns the exact manager mutex." }
        $bootIdSha256 = Get-BootIdSha256
        $bindingDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "bindings"
        $receiptDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "receipts"
        [void](New-Item -ItemType Directory -Force -Path $receiptDirectory)
        $bindingFiles = if (Test-Path -LiteralPath $bindingDirectory -PathType Container) {
            @(
                Get-ChildItem -LiteralPath $bindingDirectory -Filter "*.json" -File |
                    Sort-Object FullName
            )
        }
        else { @() }
        $bindingInventory = @(
            $bindingFiles | ForEach-Object {
                [ordered]@{
                    locator_sha256 = Get-StringSha256 ([IO.Path]::GetFullPath($_.FullName))
                    file_sha256 = Get-Sha256 $_.FullName
                }
            }
        )
        $bindingInventorySha256 = Get-StringSha256 (ConvertTo-CanonicalJson $bindingInventory)
        $managerRunCorrelationId = "manager_" + (Get-StringSha256 (
            ([IO.Path]::GetFullPath($RecoveryRoot)) + "|" + $bootIdSha256 + "|" +
            $bindingInventorySha256 + "|" + $script:Release
        )).Substring(0, 40).ToLowerInvariant()
        $managerRunReceiptPath = Join-Path $receiptDirectory (
            "MANAGER_RUN_" + $bootIdSha256.Substring(0, 16).ToLowerInvariant() +
            "_" + $managerRunCorrelationId + ".json"
        )
        $activeBindingCount = 0
        $alreadyRecoveredCount = 0
        $recoveredCount = 0
        $tombstonedCount = 0
        $killSwitchedCount = 0
        $invalidBindingCount = 0
        $isolatedFailureCount = 0
        $attemptReceiptCount = 0
        foreach ($bindingFile in $bindingFiles) {
            $record = $null
            try {
                $record = Read-SealedBinding $bindingFile.FullName
            }
            catch {
                $invalidBindingCount += 1
                $isolatedFailureCount += 1
                $invalidId = (Get-StringSha256 ([IO.Path]::GetFullPath($bindingFile.FullName))).Substring(0, 24).ToLowerInvariant()
                $invalidCorrelationId = "invalid_" + (Get-StringSha256 (
                    $managerRunCorrelationId + "|" + $invalidId + "|" +
                    (Get-Sha256 $bindingFile.FullName)
                )).Substring(0, 40).ToLowerInvariant()
                $invalidReceipt = [ordered]@{
                    schema = $script:RecoverySchema
                    status = "FAIL_CLOSED"
                    state = "INVALID_BINDING_ISOLATED"
                    correlation_id = $invalidCorrelationId
                    manager_run_correlation_id = $managerRunCorrelationId
                    manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"
                    binding_scope = "ONE_INVALID_FILE_ONLY"
                    binding_locator_sha256 = Get-StringSha256 ([IO.Path]::GetFullPath($bindingFile.FullName))
                    binding_file_sha256 = Get-Sha256 $bindingFile.FullName
                    failure = $_.Exception.Message
                    another_binding_mutated = $false
                    retry_allowed = $false
                    scheduler_restart_requested = $false
                    restart_count_used_for_causation = $false
                    task_opened = $false
                    prompt_submitted = $false
                    lifecycle_mutated = $false
                    recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
                }
                $invalidPath = Join-Path $receiptDirectory (
                    "RECOVERY_INVALID_BINDING_" + $invalidId + "_" +
                    $invalidCorrelationId + ".json"
                )
                if (-not (Test-Path -LiteralPath $invalidPath -PathType Leaf)) {
                    Write-AtomicJson $invalidPath $invalidReceipt
                }
                continue
            }
            if ($record.payload.state -ne "ACTIVE_GOAL_BOUND") { continue }
            $activeBindingCount += 1
            $exactTaskId = [string]$record.payload.task_id
            if (Test-RecoveredThisBoot -ExactTaskId $exactTaskId -BootIdSha256 $bootIdSha256) {
                $alreadyRecoveredCount += 1
                continue
            }
            $bindingSha256 = Get-Sha256 $bindingFile.FullName
            for ($attempt = 1; $attempt -le $script:MaxRecoveryAttemptsPerBinding; $attempt += 1) {
                $correlationId = Get-RecoveryAttemptCorrelationId `
                    -ExactTaskId $exactTaskId `
                    -BootIdSha256 $bootIdSha256 `
                    -BindingSha256 $bindingSha256 `
                    -Attempt $attempt `
                    -ManagerRunCorrelationId $managerRunCorrelationId
                $receiptPath = Get-RecoveryAttemptReceiptPath `
                    -ExactTaskId $exactTaskId `
                    -BootIdSha256 $bootIdSha256 `
                    -Attempt $attempt `
                    -CorrelationId $correlationId
                if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
                    $receipt = Read-RecoveryAttemptReceipt `
                        -Path $receiptPath `
                        -ExpectedCorrelationId $correlationId `
                        -ExpectedTaskId $exactTaskId `
                        -ExpectedBootIdSha256 $bootIdSha256 `
                        -ExpectedAttempt $attempt
                    $attemptReceiptCount += 1
                    if ($receipt.status -eq "PASS" -or $receipt.retry_allowed -ne $true) {
                        break
                    }
                    continue
                }
                $backoffSeconds = [int]$script:RecoveryBackoffSeconds[$attempt - 1]
                if ($backoffSeconds -gt 0) {
                    Start-Sleep -Seconds $backoffSeconds
                }
                $receipt = [ordered]@{
                    schema = $script:RecoveryAttemptSchema
                    status = "PASS"
                    state = "RECOVERY_ATTEMPT_STARTED"
                    correlation_id = $correlationId
                    manager_run_correlation_id = $managerRunCorrelationId
                    manager_scope = "SHARED_MULTI_PROJECT_MULTI_TASK"
                    binding_scope = "MUTABLE_EXACT_TASK_ROW"
                    owner_scope = "EXACT_TASK_BINDING_ONLY"
                    task_id = $exactTaskId
                    project_id = [string]$record.payload.project_id
                    evidence_session_id = [string]$record.payload.evidence_session_id
                    active_plan_task_id = [string]$record.payload.active_plan_task_id
                    attempt = $attempt
                    max_attempts = $script:MaxRecoveryAttemptsPerBinding
                    backoff_seconds_before_attempt = $backoffSeconds
                    retry_allowed = $false
                    host_app_id = $null
                    host_application = $null
                    boot_id_sha256 = $bootIdSha256
                    binding_sha256 = $bindingSha256
                    binding_payload_sha256 = [string]$record.payload_sha256
                    goal = $null
                    activation = [ordered]@{ request_observed = $false }
                    source_mutated = $false
                    prompt_submitted = $false
                    turn_started = $false
                    state_travel_invoked = $false
                    candidate_hil_or_pointer_mutated = $false
                    another_binding_mutated = $false
                    scheduler_restart_requested = $false
                    restart_count_used_for_causation = $false
                    hot_reattach = $null
                    recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
                }
                try {
                    $boundHostProfile = Resolve-GoalBindingHostProfile $record
                    $receipt.host_app_id = [string]$boundHostProfile.app_id
                    $receipt.host_application = [string]$boundHostProfile.host_application
                    $slotAuthority = Read-ThreeSlotAuthority `
                        -Path ([string]$record.payload.slot_authority.registry_path) `
                        -ExpectedSha256 ([string]$record.payload.slot_authority.registry_sha256)
                    Assert-BindingReleaseAuthority -Binding $record.payload -Authority $slotAuthority
                    $receipt.release_authorized_by_sealed_registry = $true
                    $receipt.main_git_selector = [string]$slotAuthority.main_git_selector
                    $receipt.branch_recovery_selector = [string]$slotAuthority.branch_recovery_selector
                    $receipt.mutable_local_selector = [string]$slotAuthority.mutable_local_selector
                    $receipt.mutable_local_failure_target = [string]$slotAuthority.mutable_local_failure_target
                    $receipt.mutable_local_failure_never_targets_main_git = $true
                    $runtimePluginSelector = Resolve-GoalBindingRuntimeSelector $record
                    $localRecoveryAuthority = Read-GoalLocalRecoveryAuthority $record
                    $receipt.local_recovery_selector = if ($null -eq $localRecoveryAuthority) { $null } else { [string]$localRecoveryAuthority.recovery_selector }
                    $receipt.pre_3_0_automatic_recovery_allowed = $false
                    $probe = Invoke-CodexGoalProbe `
                        -ExactTaskId $exactTaskId `
                        -ExpectedPluginSelector $runtimePluginSelector
                    $receipt.goal = $probe
                    if ($probe.goal_status -ne "active") {
                        $tombstone = Set-ExactBindingTombstone `
                            -Record $record `
                            -GoalStatus ([string]$probe.goal_status) `
                            -Reason "LOGON_RECOVERY_FOUND_GOAL_NOT_ACTIVE"
                        $receipt.state = "EXACT_TASK_BINDING_TOMBSTONED_GOAL_NOT_ACTIVE"
                        $receipt.binding_tombstone = $tombstone
                        $receipt.retry_allowed = $false
                        $tombstonedCount += 1
                    }
                    else {
                        $receipt.activation = Invoke-CodexTaskActivation -ExactTaskId $exactTaskId -HostProfile $boundHostProfile
                        $receipt.hot_reattach = [ordered]@{
                            runtime_prewarm = $probe.runtime_prewarm
                            exact_task_deeplink_requested = $true
                            live_desktop_control_plane = "HOST_CAPABILITY_UNAVAILABLE_WINDOWS_APP_SERVER_DAEMON"
                            app_restart_required = $false
                            restart_fallback_invoked = $false
                            restart_loop_allowed = $false
                        }
                        $receipt.state = "EXACT_TASK_OPEN_REQUESTED_ACTIVE_GOAL_PERSISTED_HOST_CONTINUATION_PENDING"
                        $recoveredCount += 1
                    }
                }
                catch {
                    $isolatedFailureCount += 1
                    $receipt.status = "FAIL_CLOSED"
                    $receipt.failure = $_.Exception.Message
                    $receipt.activation = [ordered]@{ request_observed = $false }
                    if ($attempt -lt $script:MaxRecoveryAttemptsPerBinding) {
                        $receipt.state = "RECOVERY_ATTEMPT_FAILED_BACKOFF_PENDING"
                        $receipt.retry_allowed = $true
                        $receipt.next_backoff_seconds = [int]$script:RecoveryBackoffSeconds[$attempt]
                    }
                    else {
                        $killSwitch = Set-ExactBindingRecoveryKillSwitch `
                            -Record $record `
                            -FailureCorrelationId $correlationId `
                            -Reason "EXACT_BINDING_RECOVERY_ATTEMPTS_EXHAUSTED"
                        $receipt.state = "RECOVERY_ATTEMPTS_EXHAUSTED_EXACT_BINDING_KILL_SWITCHED"
                        $receipt.binding_kill_switch = $killSwitch
                        $receipt.retry_allowed = $false
                        $killSwitchedCount += 1
                    }
                }
                Write-AtomicJson $receiptPath $receipt
                $attemptReceiptCount += 1
                if ($receipt.status -eq "PASS" -or $receipt.retry_allowed -ne $true) {
                    break
                }
            }
        }
        $recoverableBindingCount = 0
        if (Test-Path -LiteralPath $bindingDirectory -PathType Container) {
            foreach ($bindingFile in Get-ChildItem -LiteralPath $bindingDirectory -Filter "*.json" -File) {
                try {
                    $current = Read-SealedBinding $bindingFile.FullName
                    if ($current.payload.state -eq "ACTIVE_GOAL_BOUND") {
                        $recoverableBindingCount += 1
                    }
                }
                catch {}
            }
        }
        $managerState = if ($recoverableBindingCount -eq 0) {
            "NO_RECOVERABLE_BINDINGS"
        }
        elseif ($isolatedFailureCount -gt 0) {
            "RECOVERY_RUN_COMPLETED_WITH_ISOLATED_BINDING_FAILURES"
        }
        else {
            "RECOVERY_RUN_COMPLETED"
        }
        $managerRunReceipt = [ordered]@{
            schema = $script:RecoveryManagerRunSchema
            status = "PASS"
            state = $managerState
            correlation_id = $managerRunCorrelationId
            boot_id_sha256 = $bootIdSha256
            binding_inventory_sha256 = $bindingInventorySha256
            binding_file_count = $bindingFiles.Count
            active_binding_count = $activeBindingCount
            already_recovered_count = $alreadyRecoveredCount
            recovered_count = $recoveredCount
            tombstoned_count = $tombstonedCount
            kill_switched_count = $killSwitchedCount
            invalid_binding_count = $invalidBindingCount
            isolated_failure_count = $isolatedFailureCount
            recoverable_binding_count = $recoverableBindingCount
            attempt_receipt_count = $attemptReceiptCount
            max_attempts_per_binding = $script:MaxRecoveryAttemptsPerBinding
            backoff_seconds = $script:RecoveryBackoffSeconds
            scheduler_restart_requested = $false
            scheduler_retry_is_authority = $false
            restart_count_used_for_hook_or_helper_causation = $false
            manager_kill_switch_scope = "NONE"
            binding_kill_switch_scope = "EXACT_TASK_BINDING_ONLY"
            another_binding_mutated_by_failure = $false
            process_exit_code = 0
            recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        }
        if (-not (Test-Path -LiteralPath $managerRunReceiptPath -PathType Leaf)) {
            Write-AtomicJson $managerRunReceiptPath $managerRunReceipt
        }
        exit 0
    }
    finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

$exactRoot = [IO.Path]::GetFullPath($RecoveryRoot)
$bindingDirectory = Join-Path $exactRoot "bindings"
$bindings = @()
if (Test-Path -LiteralPath $bindingDirectory -PathType Container) {
    $bindings = @(
        Get-ChildItem -LiteralPath $bindingDirectory -Filter "*.json" -File |
            ForEach-Object { Read-SealedBinding $_.FullName }
    )
}
$scheduled = Get-ScheduledTask -TaskName $ScheduledTaskName -ErrorAction SilentlyContinue
[ordered]@{
    status = "PASS"
    state = "GOAL_RECOVERY_STATUS"
    release = $script:Release
    release_token = $script:ReleaseToken
    helper_audience = "GOVERNED_CODEX_USER"
    recovery_root = $exactRoot
    manager_installed = Test-Path -LiteralPath (Join-Path $exactRoot "MANAGER.json") -PathType Leaf
    scheduled_task_present = $null -ne $scheduled
    scheduled_task_state = if ($null -ne $scheduled) { [string]$scheduled.State } else { "ABSENT" }
    binding_count = $bindings.Count
    active_binding_count = @($bindings | Where-Object { $_.payload.state -eq "ACTIVE_GOAL_BOUND" }).Count
    bindings = @(
        $bindings | ForEach-Object {
            $boundHostProfile = Resolve-GoalBindingHostProfile $_
            [ordered]@{
                task_id = [string]$_.payload.task_id
                project_id = [string]$_.payload.project_id
                evidence_session_id = [string]$_.payload.evidence_session_id
                active_plan_task_id = [string]$_.payload.active_plan_task_id
                host_app_id = [string]$boundHostProfile.app_id
                host_application = [string]$boundHostProfile.host_application
                state = [string]$_.payload.state
                goal_status_at_registration = [string]$_.payload.goal.goal_status
                main_git_selector = [string]$_.payload.slot_authority.main_git_selector
                branch_recovery_selector = [string]$_.payload.slot_authority.branch_recovery_selector
                mutable_local_selector = [string]$_.payload.slot_authority.mutable_local_selector
                payload_sha256 = [string]$_.payload_sha256
            }
        }
    )
    raw_goal_objective_stored = $false
    synthetic_prompt_allowed = $false
    lifecycle_mutated = $false
} | ConvertTo-Json -Depth 32
