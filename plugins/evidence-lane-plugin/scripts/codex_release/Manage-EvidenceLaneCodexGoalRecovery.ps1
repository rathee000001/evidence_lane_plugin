[CmdletBinding()]
param(
    [ValidateSet("Probe", "Register", "RecoverNow", "RecoverAtLogon", "Status", "Unregister")]
    [string]$Action = "Status",
    [string]$TaskBindingReceipt,
    [string]$TaskId,
    [string]$ActivePlanTaskId,
    [string]$Release = "2.2.0",
    [string]$RecoveryRoot = "",
    [string]$TwoSlotRegistry = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v200\two-slot\CODEX_TWO_SLOT_REGISTRY.json",
    [string]$ScheduledTaskName = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Release -notmatch '^\d+\.\d+\.\d+$') {
    throw "The Goal recovery helper requires one exact semantic release."
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
$script:CanonicalStableSelector = "evidence-lane-plugin@evidence-lane-github"
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

function Invoke-CodexGoalProbe([string]$ExactTaskId) {
    Assert-TaskId $ExactTaskId
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
                    version = "2.2.0"
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
                    if ([string]$plugin.id -ceq $script:CanonicalStableSelector) {
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
            throw "The isolated Codex app-server did not resolve exactly one canonical Evidence Lane stable plugin."
        }
        $pluginMatch = $pluginMatches[0]
        if ($pluginMatch.plugin.installed -ne $true -or $pluginMatch.plugin.enabled -ne $true) {
            throw "The canonical Evidence Lane stable plugin is not both installed and enabled."
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
        if ($toolCount -ne 83) {
            throw "The Evidence Lane MCP catalog did not expose the exact 83-tool contract."
        }
        $governedResourceUri = "ui://evidence-lane/governed-console-v5.html"
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
                canonical_plugin_selector = $script:CanonicalStableSelector
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

function Read-TwoSlotAuthority([string]$Path, [object]$Binding) {
    $exact = (Resolve-Path -LiteralPath $Path).Path
    $registry = Get-Content -LiteralPath $exact -Raw | ConvertFrom-Json
    $stable = $registry.slots.'stable-build'
    $fallback = $registry.slots.fallback
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
        [string]$stable.plugin_selector -eq [string]$fallback.plugin_selector -or
        [string]$registry.project_id -ne [string]$Binding.project_id -or
        [string]$registry.evidence_session_id -ne [string]$Binding.evidence_session_id -or
        [string]$registry.task_id -ne [string]$Binding.task_id
    ) {
        throw "The exact stable/fallback two-slot authority is not recoverable."
    }
    return [ordered]@{
        registry_path = $exact
        registry_sha256 = Get-Sha256 $exact
        accepted_pv = [string]$registry.accepted_pv
        accepted_generation = [int]$registry.accepted_generation
        stable_selector = [string]$stable.plugin_selector
        stable_enabled = $true
        stable_byte_frozen = $false
        fallback_selector = [string]$fallback.plugin_selector
        fallback_enabled = $false
        fallback_byte_frozen = $true
        exact_live_slot_count = 2
        max_enabled_plugin_count = 1
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
        "-TwoSlotRegistry", ([IO.Path]::GetFullPath($TwoSlotRegistry)),
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
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
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
                $receipt.schema -eq $script:RecoverySchema -and
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
    $slotAuthority = Read-TwoSlotAuthority -Path $TwoSlotRegistry -Binding $binding
    $hostProfile = Read-TaskHostProfile $binding
    $goalProbe = Invoke-CodexGoalProbe -ExactTaskId $TaskId
    if ($goalProbe.goal_status -ne "active") {
        throw "Only an active persisted Codex Goal may be registered for logon recovery."
    }
    $manager = Install-RecoveryManager
    $payload = [ordered]@{
        state = "ACTIVE_GOAL_BOUND"
        project_id = [string]$binding.project_id
        evidence_session_id = [string]$binding.evidence_session_id
        task_id = $TaskId
        governed_host_session_id = [string]$binding.governed_host_session_id
        active_plan_task_id = $ActivePlanTaskId
        canonical_authority = "PLAN_LANE"
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
            fallback_must_remain_disabled = $true
            stable_selector_growth_allowed = $false
            exact_bound_host_app_required = $true
            stable_and_beta_hosts_supported = $true
            both_desktop_channels_expose_chatgpt_and_codex_surfaces = $true
            evidence_lane_governs_codex_surface_only = $true
            helper_and_tunnel_console_windows_allowed = $false
            isolated_runtime_prewarm_before_task_open = $true
            exact_task_deeplink_is_primary_hot_reattach = $true
            restart_is_bounded_fallback_only = $true
            restart_loop_allowed = $false
        }
        registered_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $record = [ordered]@{
        schema = $script:Schema
        payload = $payload
        payload_sha256 = Get-StringSha256 (ConvertTo-CanonicalJson $payload)
    }
    $bindingPath = Get-BindingPath $TaskId
    Write-AtomicJson $bindingPath $record
    [ordered]@{
        status = "PASS"
        state = "ACTIVE_GOAL_REGISTERED_FOR_WINDOWS_LOGON_RECOVERY"
        release = $script:Release
        release_token = $script:ReleaseToken
        helper_audience = "GOVERNED_CODEX_USER"
        task_id = $TaskId
        active_plan_task_id = $ActivePlanTaskId
        binding_path = $bindingPath
        binding_sha256 = Get-Sha256 $bindingPath
        manager = $manager
        goal_status = $goalProbe.goal_status
        stable_selector = $slotAuthority.stable_selector
        fallback_selector = $slotAuthority.fallback_selector
        host_app_id = [string]$hostProfile.app_id
        host_application = [string]$hostProfile.host_application
        exact_live_slot_count = 2
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
    $slotAuthority = Read-TwoSlotAuthority `
        -Path ([string]$record.payload.slot_authority.registry_path) `
        -Binding $record.payload
    $probe = Invoke-CodexGoalProbe -ExactTaskId $TaskId
    if ($probe.goal_status -ne "active") {
        throw "RecoverNow may open only an active persisted Codex Goal."
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
        stable_selector = [string]$slotAuthority.stable_selector
        fallback_selector = [string]$slotAuthority.fallback_selector
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
    $failures = 0
    try {
        $locked = $mutex.WaitOne([TimeSpan]::FromSeconds(10))
        if (-not $locked) { throw "Another Goal recovery run already owns the exact manager mutex." }
        $bootIdSha256 = Get-BootIdSha256
        $bindingDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "bindings"
        $receiptDirectory = Join-Path ([IO.Path]::GetFullPath($RecoveryRoot)) "receipts"
        [void](New-Item -ItemType Directory -Force -Path $receiptDirectory)
        $records = @(
            Get-ChildItem -LiteralPath $bindingDirectory -Filter "*.json" -File |
                ForEach-Object { Read-SealedBinding $_.FullName } |
                Where-Object { $_.payload.state -eq "ACTIVE_GOAL_BOUND" }
        )
        foreach ($record in $records) {
            $exactTaskId = [string]$record.payload.task_id
            if (Test-RecoveredThisBoot -ExactTaskId $exactTaskId -BootIdSha256 $bootIdSha256) {
                continue
            }
            $boundHostProfile = Resolve-GoalBindingHostProfile $record
            $receipt = [ordered]@{
                schema = $script:RecoverySchema
                task_id = $exactTaskId
                project_id = [string]$record.payload.project_id
                evidence_session_id = [string]$record.payload.evidence_session_id
                active_plan_task_id = [string]$record.payload.active_plan_task_id
                host_app_id = [string]$boundHostProfile.app_id
                host_application = [string]$boundHostProfile.host_application
                boot_id_sha256 = $bootIdSha256
                binding_payload_sha256 = [string]$record.payload_sha256
                state = "RECOVERY_STARTED"
                goal = $null
                activation = [ordered]@{ request_observed = $false }
                source_mutated = $false
                prompt_submitted = $false
                turn_started = $false
                state_travel_invoked = $false
                candidate_hil_or_pointer_mutated = $false
                hot_reattach = $null
                recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
            }
            try {
                $slotAuthority = Read-TwoSlotAuthority -Path ([string]$record.payload.slot_authority.registry_path) -Binding $record.payload
                $registeredStableSelector = [string]$record.payload.slot_authority.stable_selector
                $stableSelectorMigratedToCanonical = (
                    $slotAuthority.stable_selector -eq $script:CanonicalStableSelector -and
                    $registeredStableSelector -ne $script:CanonicalStableSelector
                )
                if (
                    (
                        $slotAuthority.stable_selector -ne $registeredStableSelector -and
                        -not $stableSelectorMigratedToCanonical
                    ) -or
                    $slotAuthority.fallback_selector -ne [string]$record.payload.slot_authority.fallback_selector
                ) {
                    throw "The registered stable/fallback selectors changed without rebinding this Goal."
                }
                $receipt.stable_selector_migrated_to_canonical_git = $stableSelectorMigratedToCanonical
                $probe = Invoke-CodexGoalProbe -ExactTaskId $exactTaskId
                $receipt.goal = $probe
                if ($probe.goal_status -ne "active") {
                    $receipt.state = "SKIPPED_GOAL_NOT_ACTIVE"
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
                }
            }
            catch {
                $failures += 1
                $receipt.state = "RECOVERY_FAILED_CLOSED"
                $receipt.failure = $_.Exception.Message
                $receipt.activation = [ordered]@{ request_observed = $false }
            }
            $receiptPath = Join-Path $receiptDirectory ("RECOVERY_" + $exactTaskId.ToLowerInvariant() + "_" + [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ") + ".json")
            Write-AtomicJson $receiptPath $receipt
        }
        if ($failures -gt 0) { exit 1 }
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
                stable_selector = [string]$_.payload.slot_authority.stable_selector
                fallback_selector = [string]$_.payload.slot_authority.fallback_selector
                payload_sha256 = [string]$_.payload_sha256
            }
        }
    )
    raw_goal_objective_stored = $false
    synthetic_prompt_allowed = $false
    lifecycle_mutated = $false
} | ConvertTo-Json -Depth 32
