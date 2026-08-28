[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EventName,
    [Parameter(Mandatory = $true)]
    [string]$HandlerName
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [IO.File]::Open(
        $LiteralPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '')
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Write-HookLaunchFailure {
    param([string]$Code)
    $diagnostic = [ordered]@{
        schema = 'evidence-lane.codex-hook-launch-diagnostic.v1'
        status = 'FAIL_CLOSED'
        event_name = if ($EventName) { $EventName } else { 'UNKNOWN' }
        handler = if ($HandlerName) { $HandlerName } else { $null }
        code = $Code
        process_window_mode = 'HOST_MANAGED_NO_CHILD_WINDOW'
        interpreter_resolution = 'SEALED_DERIVED_RUNTIME_ONLY'
        plugin_root_resolution = 'POWERSHELL_SCRIPT_PARENT'
        host_control_fields_emitted = $false
        source_mutation_authorized = $false
        lifecycle_mutated = $false
        candidate_created = $false
        hil_inferred = $false
        pointer_moved = $false
        raw_payload_stored = $false
        raw_secret_stored = $false
        private_reasoning_stored = $false
    }
    $additional = 'EVIDENCE_LANE_HOOK_LAUNCH_DIAGNOSTIC=' + (
        $diagnostic | ConvertTo-Json -Compress -Depth 8
    )
    $reason = "Evidence Lane $EventName hook failed closed: $Code."
    if ($EventName -in @('SessionEnd', 'Stop')) {
        # Terminal hook launch failures are output-inert. Stop must never
        # block the host or create a continuation loop.
        $result = [ordered]@{}
    } elseif ($EventName -eq 'PreToolUse') {
        # PreToolUse does not support the universal continue/stopReason fields.
        # A fail-closed result must use its event-specific permission contract.
        $result = [ordered]@{
            systemMessage = $additional
            hookSpecificOutput = [ordered]@{
                hookEventName = 'PreToolUse'
                permissionDecision = 'deny'
                permissionDecisionReason = $reason
            }
        }
    } else {
        $result = [ordered]@{
            systemMessage = $additional
        }
    }
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 8))
    exit 0
}

$failureStage = 'PATH_RESOLUTION'
try {
    $pluginRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
    $lockPath = Join-Path $pluginRoot 'requirements.lock.txt'
    $launcher = Join-Path $PSScriptRoot 'invoke_hook.py'
    $isolationPolicyPath = Join-Path $PSScriptRoot 'event_isolation_policy.json'
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        Write-HookLaunchFailure -Code 'REQUIREMENTS_LOCK_MISSING'
    }
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        Write-HookLaunchFailure -Code 'HOOK_RUNTIME_LAUNCHER_MISSING'
    }
    if (-not (Test-Path -LiteralPath $isolationPolicyPath -PathType Leaf)) {
        Write-HookLaunchFailure -Code 'HOOK_EVENT_ISOLATION_POLICY_MISSING'
    }
    $failureStage = 'DATA_ROOT_RESOLUTION'
    $configuredRoot = [Environment]::GetEnvironmentVariable('EVIDENCE_LANE_RUNTIME_CONTROL_ROOT')
    if ($null -ne $configuredRoot -and -not $configuredRoot.Trim()) {
        Write-HookLaunchFailure -Code 'EVIDENCE_LANE_RUNTIME_CONTROL_ROOT_EMPTY'
    }
    $failureStage = 'DATA_ROOT_PATH'
    $dataRoot = if ($configuredRoot) {
        [IO.Path]::GetFullPath($configuredRoot)
    } else {
        Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex\plugins\runtime\evidence-lane-plugin'
    }
    $failureStage = 'LOCK_HASH'
    $lockSha256 = Get-Sha256 -LiteralPath $lockPath
    $failureStage = 'RUNTIME_PATHS'
    $runtimeRoot = Join-Path $dataRoot 'runtime\codex'
    $installReceiptRoot = Join-Path $dataRoot 'installations\codex-v200'
    $failureStage = 'PLUGIN_ROOT_KEY'
    $pluginRootKey = [IO.Path]::GetFullPath($pluginRoot).TrimEnd('\').ToLowerInvariant()
    $failureStage = 'RUNTIME_ROOT_PREFIX'
    $runtimeRootPrefix = [IO.Path]::GetFullPath($runtimeRoot).TrimEnd('\') + '\'
    # `$Matches` is PowerShell's case-insensitive automatic regex-capture
    # variable.  Reusing that name here lets any `-match`/`-notmatch` check
    # mutate the runtime inventory and can turn one valid binding into a false
    # ambiguous-runtime failure.
    $failureStage = 'RUNTIME_INVENTORY'
    $runtimeMatches = @{}
    if (Test-Path -LiteralPath $installReceiptRoot -PathType Container) {
        foreach ($receiptPath in Get-ChildItem -LiteralPath $installReceiptRoot -Filter 'INSTALL_*.json' -File -ErrorAction Stop) {
            try {
                $receipt = Get-Content -Raw -LiteralPath $receiptPath.FullName | ConvertFrom-Json
                if (
                    $receipt.status -ne 'PASS' -or
                    $receipt.activation.runtime_prewarm.status -ne 'PASS'
                ) {
                    continue
                }
                $installedPath = [string]$receipt.activation.plugin_add.installedPath
                $boundRuntimeRoot = [string]$receipt.activation.runtime_prewarm.runtime_projection_root
                $hookIsolation = $receipt.activation.hook_event_isolation
                if (
                    -not $installedPath -or
                    -not $boundRuntimeRoot -or
                    $hookIsolation.status -ne 'PASS' -or
                    $hookIsolation.verified_before_install_activation -ne $true -or
                    $hookIsolation.persistent_kill_switch -ne $true
                ) { continue }
                $installedPathKey = [IO.Path]::GetFullPath($installedPath).TrimEnd('\').ToLowerInvariant()
                if ($installedPathKey -ne $pluginRootKey) { continue }
                $boundRuntimeRoot = [IO.Path]::GetFullPath($boundRuntimeRoot).TrimEnd('\')
                if (-not $boundRuntimeRoot.StartsWith($runtimeRootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                    continue
                }
                $marker = Join-Path $boundRuntimeRoot 'RUNTIME_READY.json'
                if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { continue }
                $killSwitchReceiptPath = [IO.Path]::GetFullPath(
                    [string]$hookIsolation.kill_switch_receipt_path
                )
                $killSwitchReceiptSha256 = (
                    [string]$hookIsolation.kill_switch_receipt_sha256
                ).ToUpperInvariant()
                $isolationPolicySha256 = (
                    [string]$hookIsolation.policy_sha256
                ).ToUpperInvariant()
                if (
                    -not (Test-Path -LiteralPath $killSwitchReceiptPath -PathType Leaf) -or
                    $killSwitchReceiptSha256 -notmatch '^[A-F0-9]{64}$' -or
                    $isolationPolicySha256 -notmatch '^[A-F0-9]{64}$' -or
                    (Get-Sha256 -LiteralPath $killSwitchReceiptPath) -ne $killSwitchReceiptSha256 -or
                    (Get-Sha256 -LiteralPath $isolationPolicyPath) -ne $isolationPolicySha256
                ) { continue }
                $killSwitchBody = Get-Content -Raw -LiteralPath $killSwitchReceiptPath | ConvertFrom-Json
                if (
                    $killSwitchBody.schema -ne 'evidence-lane.codex-hook-kill-switch.v1' -or
                    $killSwitchBody.payload.schema -ne 'evidence-lane.codex-hook-kill-switch.v1' -or
                    $killSwitchBody.payload.state -notin @('INACTIVE', 'ACTIVE') -or
                    $killSwitchBody.payload.policy_sha256 -ne $isolationPolicySha256 -or
                    $killSwitchBody.payload.owner -ne 'EVIDENCE_LANE_INSTALLED_HOOK_RUNTIME'
                ) { continue }
                $body = Get-Content -Raw -LiteralPath $marker | ConvertFrom-Json
                $identity = $body.runtime_identity
                $python = Join-Path $boundRuntimeRoot 'venv\Scripts\python.exe'
                $runtimeDirectoryName = Split-Path -Leaf $boundRuntimeRoot
                if (
                    $body.schema -eq 'evidence-lane.codex-native-runtime-ready.v1' -and
                    $body.status -eq 'PASS' -and
                    $identity.schema -eq 'evidence-lane.codex-native-runtime.v1' -and
                    $identity.requirements_lock_sha256 -eq $lockSha256 -and
                    $runtimeDirectoryName -eq ([string]$identity.runtime_key).Substring(0, 32) -and
                    $identity.platform_system -eq 'Windows' -and
                    (Test-Path -LiteralPath $python -PathType Leaf)
                ) {
                    $runtimeMatches[$boundRuntimeRoot.ToLowerInvariant()] = [ordered]@{
                        python = $python
                        runtime_root = $boundRuntimeRoot
                        kill_switch_receipt_path = $killSwitchReceiptPath
                        kill_switch_receipt_sha256 = $killSwitchReceiptSha256
                        isolation_policy_sha256 = $isolationPolicySha256
                    }
                }
            } catch {
                continue
            }
        }
    }
    $failureStage = 'RUNTIME_SELECTION'
    if ($runtimeMatches.Count -eq 0) {
        Write-HookLaunchFailure -Code 'SEALED_RUNTIME_INTERPRETER_NOT_FOUND'
    }
    if ($runtimeMatches.Count -ne 1) {
        Write-HookLaunchFailure -Code 'SEALED_RUNTIME_INTERPRETER_AMBIGUOUS'
    }
    $runtimeBinding = $runtimeMatches.Values | Select-Object -First 1
    $runtimePython = [string]$runtimeBinding.python
    $env:EVIDENCE_LANE_HOOK_BOUND_RUNTIME_ROOT = [string]$runtimeBinding.runtime_root
    $env:EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT = [string]$runtimeBinding.kill_switch_receipt_path
    $env:EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256 = [string]$runtimeBinding.kill_switch_receipt_sha256
    $env:EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256 = [string]$runtimeBinding.isolation_policy_sha256
    $failureStage = 'PAYLOAD_EXECUTION'
    $rawPayload = [Console]::In.ReadToEnd()
    $output = $rawPayload | & $runtimePython $launcher --event $EventName --handler $HandlerName 2>$null
    if ($LASTEXITCODE -ne 0 -or $null -eq $output) {
        Write-HookLaunchFailure -Code 'SEALED_RUNTIME_HOOK_EXECUTION_FAILED'
    }
    $failureStage = 'OUTPUT_VALIDATION'
    $serialized = ($output -join [Environment]::NewLine).Trim()
    if (-not $serialized -or $serialized.Length -gt 32768) {
        Write-HookLaunchFailure -Code 'HOOK_OUTPUT_BOUND_INVALID'
    }
    try {
        $parsed = $serialized | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-HookLaunchFailure -Code 'HOOK_OUTPUT_JSON_INVALID'
    }
    if ($null -eq $parsed -or $parsed -is [System.Array]) {
        Write-HookLaunchFailure -Code 'HOOK_OUTPUT_OBJECT_REQUIRED'
    }
    if ($EventName -eq 'PreToolUse') {
        $unsupported = @('continue', 'stopReason', 'suppressOutput') | Where-Object {
            $null -ne $parsed.PSObject.Properties[$_]
        }
        if ($unsupported.Count -gt 0) {
            Write-HookLaunchFailure -Code 'PRE_TOOL_USE_OUTPUT_SCHEMA_INVALID'
        }
    }
    if (
        $EventName -eq 'Stop' -and
        @($parsed.PSObject.Properties).Count -ne 0
    ) {
        Write-HookLaunchFailure -Code 'STOP_OUTPUT_MUST_BE_EMPTY'
    }
    if (
        $EventName -eq 'SessionEnd' -and
        @($parsed.PSObject.Properties).Count -ne 0
    ) {
        Write-HookLaunchFailure -Code 'SESSION_END_OUTPUT_MUST_BE_EMPTY'
    }
    [Console]::Out.Write($serialized)
    exit 0
} catch {
    Write-HookLaunchFailure -Code ("HOOK_LAUNCHER_UNHANDLED_FAILURE_$failureStage")
}
