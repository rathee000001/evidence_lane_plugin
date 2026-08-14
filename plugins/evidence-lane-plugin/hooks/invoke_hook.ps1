[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EventName,
    [Parameter(Mandatory = $true)]
    [string]$HandlerName
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Write-HookLaunchFailure {
    param([string]$Code)
    $diagnostic = [ordered]@{
        schema = 'evidence-lane.codex-hook-launch-diagnostic.v1'
        status = 'FAIL_CLOSED'
        event_name = if ($EventName) { $EventName } else { 'UNKNOWN' }
        handler = if ($HandlerName) { $HandlerName } else { $null }
        code = $Code
        process_window_mode = 'HIDDEN_ON_WINDOWS'
        interpreter_resolution = 'SEALED_DERIVED_RUNTIME_ONLY'
        plugin_root_resolution = 'POWERSHELL_SCRIPT_PARENT'
        continue = $false
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
    $result = [ordered]@{
        continue = $false
        stopReason = "Evidence Lane $EventName hook failed closed: $Code."
        hookSpecificOutput = [ordered]@{
            hookEventName = if ($EventName) { $EventName } else { 'UNKNOWN' }
            additionalContext = $additional
        }
    }
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Compress -Depth 8))
    exit 0
}

try {
    $pluginRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
    $lockPath = Join-Path $pluginRoot 'requirements.lock.txt'
    $launcher = Join-Path $PSScriptRoot 'invoke_hook.py'
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
        Write-HookLaunchFailure -Code 'REQUIREMENTS_LOCK_MISSING'
    }
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        Write-HookLaunchFailure -Code 'HOOK_RUNTIME_LAUNCHER_MISSING'
    }
    $configuredRoot = [Environment]::GetEnvironmentVariable('EVIDENCE_LANE_DATA_ROOT')
    if ($null -ne $configuredRoot -and -not $configuredRoot.Trim()) {
        Write-HookLaunchFailure -Code 'EVIDENCE_LANE_DATA_ROOT_EMPTY'
    }
    $dataRoot = if ($configuredRoot) {
        [IO.Path]::GetFullPath($configuredRoot)
    } else {
        Join-Path ([Environment]::GetFolderPath('UserProfile')) 'EvidenceLanePV'
    }
    $lockSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $lockPath).Hash
    $runtimeRoot = Join-Path $dataRoot 'runtime\codex'
    $matches = @()
    if (Test-Path -LiteralPath $runtimeRoot -PathType Container) {
        foreach ($markerPath in Get-ChildItem -LiteralPath $runtimeRoot -Directory -ErrorAction Stop) {
            $marker = Join-Path $markerPath.FullName 'RUNTIME_READY.json'
            if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { continue }
            try {
                $body = Get-Content -Raw -LiteralPath $marker | ConvertFrom-Json
                $identity = $body.runtime_identity
                $python = Join-Path $markerPath.FullName 'venv\Scripts\python.exe'
                if (
                    $body.schema -eq 'evidence-lane.codex-native-runtime-ready.v1' -and
                    $body.status -eq 'PASS' -and
                    $identity.schema -eq 'evidence-lane.codex-native-runtime.v1' -and
                    $identity.requirements_lock_sha256 -eq $lockSha256 -and
                    $markerPath.Name -eq ([string]$identity.runtime_key).Substring(0, 32) -and
                    $identity.platform_system -eq 'Windows' -and
                    (Test-Path -LiteralPath $python -PathType Leaf)
                ) {
                    $matches += $python
                }
            } catch {
                continue
            }
        }
    }
    if ($matches.Count -eq 0) {
        Write-HookLaunchFailure -Code 'SEALED_RUNTIME_INTERPRETER_NOT_FOUND'
    }
    if ($matches.Count -ne 1) {
        Write-HookLaunchFailure -Code 'SEALED_RUNTIME_INTERPRETER_AMBIGUOUS'
    }
    $rawPayload = [Console]::In.ReadToEnd()
    $output = $rawPayload | & $matches[0] $launcher --event $EventName --handler $HandlerName 2>$null
    if ($LASTEXITCODE -ne 0 -or $null -eq $output) {
        Write-HookLaunchFailure -Code 'SEALED_RUNTIME_HOOK_EXECUTION_FAILED'
    }
    [Console]::Out.Write(($output -join [Environment]::NewLine))
    exit 0
} catch {
    Write-HookLaunchFailure -Code 'HOOK_LAUNCHER_UNHANDLED_FAILURE'
}
