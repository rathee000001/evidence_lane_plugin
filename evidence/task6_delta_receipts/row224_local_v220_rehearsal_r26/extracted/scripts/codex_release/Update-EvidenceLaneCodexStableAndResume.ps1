[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Schedule", "Complete")]
    [string]$Action,
    [ValidateSet("GitStable", "LocalDisabledHookRecovery")]
    [string]$UpdateMode = "GitStable",
    [Parameter(Mandatory = $true)]
    [string]$Archive,
    [Parameter(Mandatory = $true)]
    [string]$PackageReceipt,
    [string]$ReleaseAuthorityReceipt,
    [string]$ReleaseAuthorityReceiptSha256,
    [Parameter(Mandatory = $true)]
    [string]$BaselineInstallationReceipt,
    [Parameter(Mandatory = $true)]
    [string]$BaselineInstallationReceiptSha256,
    [string]$PriorLocalTestCommitReceipt,
    [string]$PriorLocalTestCommitReceiptSha256,
    [string]$SuccessorStageReceipt,
    [string]$SuccessorStageReceiptSha256,
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceSessionId,
    [Parameter(Mandatory = $true)]
    [string]$TaskId,
    [Parameter(Mandatory = $true)]
    [string]$HostSessionId,
    [Parameter(Mandatory = $true)]
    [string]$ActivePlanTaskId,
    [Parameter(Mandatory = $true)]
    [int]$TargetProcessId,
    [string]$CodexHome = "$env:USERPROFILE\.codex",
    [string]$DataRoot = "$env:USERPROFILE\EvidenceLanePV",
    [string]$CodexExecutable = "$env:APPDATA\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe",
    [string]$PythonExecutable = "python.exe",
    [string]$InstallerScript,
    [string]$HookCwd = (Get-Location).Path,
    [string]$ReceiptDirectory = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v200\same-slot-update",
    [string]$ScheduledTaskName,
    [ValidateSet(
        "OpenAI.Codex_2p2nqsd0c76g0!App",
        "OpenAI.CodexBeta_2p2nqsd0c76g0!App"
    )]
    [string]$HostAppId,
    [string]$HostExecutableSha256,
    [switch]$ConfirmRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
throw (
    "RETIRED_COMBINED_INSTALL_RESTART_HELPER: install and verify the exact package " +
    "in its existing marketplace slot before invoking Restart-EvidenceLaneCodex.ps1. " +
    "No helper may install plugin bytes after the Codex host stops."
)
$InstallerScript = if ([string]::IsNullOrWhiteSpace($InstallerScript)) {
    Join-Path $PSScriptRoot "install_codex_stable.py"
}
else {
    $InstallerScript
}
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:CanonicalStableSelector = "evidence-lane-plugin@evidence-lane-github"
$script:LocalTestingMarketplaceName = "evidence-lane-v220-testing-new"
$script:LocalTestingSelector = "evidence-lane-plugin@$($script:LocalTestingMarketplaceName)"
$script:LocalRecoverySelector = "evidence-lane-plugin@evidence-lane-v220-stable-recovery"
$script:LocalSuccessorMarketplaceName = "evidence-lane-v220-local-successor"
$script:LocalSuccessorSelector = "evidence-lane-plugin@$($script:LocalSuccessorMarketplaceName)"
$script:ExpectedGitRepository = "rathee000001/evidence_lane_plugin"
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
        process_name = "ChatGPT.exe"
        manifest_executable = "app/ChatGPT.exe"
        root_path_pattern = '\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\ChatGPT\.exe$'
        package_path_pattern = '\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\'
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
        process_name = "ChatGPT (Beta).exe"
        manifest_executable = "app/ChatGPT (Beta).exe"
        root_path_pattern = '\\WindowsApps\\OpenAI\.CodexBeta_[^\\]+\\app\\ChatGPT \(Beta\)\.exe$'
        package_path_pattern = '\\WindowsApps\\OpenAI\.CodexBeta_[^\\]+\\app\\'
    }
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required sealed file is missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Get-StringSha256([AllowEmptyString()][string]$Value) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $hasher.Dispose()
    }
}

function Write-Json([string]$Path, [object]$Value) {
    $parent = Split-Path -Parent $Path
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $json = $Value | ConvertTo-Json -Depth 32
    [IO.File]::WriteAllText($Path, $json + "`n", $script:Utf8NoBom)
}

function ConvertTo-WindowsArgument([AllowEmptyString()][string]$Value) {
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

function Get-HostAppProfile([string]$ExactAppId) {
    if ([string]::IsNullOrWhiteSpace($ExactAppId) -or -not $script:HostAppProfiles.Contains($ExactAppId)) {
        throw "The exact Codex host AppUserModelID is not in the approved stable/Beta allowlist."
    }
    return $script:HostAppProfiles[$ExactAppId]
}

function Get-ExactRootProcess([int]$ProcessId, [string]$ExpectedAppId = "") {
    $row = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $row) { throw "The exact target Codex process is absent." }
    $matches = @(
        $script:HostAppProfiles.Values |
            Where-Object {
                ([string]::IsNullOrWhiteSpace($ExpectedAppId) -or $_.app_id -ceq $ExpectedAppId) -and
                [string]$row.Name -ceq [string]$_.process_name -and
                [string]$row.ExecutablePath -match [string]$_.root_path_pattern -and
                [string]$row.CommandLine -notmatch '--type='
            }
    )
    if ($matches.Count -ne 1) {
        throw "The supplied process is not one exact approved root Codex Desktop process."
    }
    return [ordered]@{ process = $row; profile = $matches[0] }
}

function Get-LockingCodexProcesses([string]$StableMarketplaceName, [object]$HostProfile) {
    $escapedMarketplaceName = [Regex]::Escape($StableMarketplaceName)
    return @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $executablePath = [string]$_.ExecutablePath
                $commandLine = [string]$_.CommandLine
                $isCodexDesktopProcess = (
                    $executablePath -match [string]$HostProfile.package_path_pattern -and
                    [string]$_.Name -in @(
                        [string]$HostProfile.process_name,
                        "codex.exe",
                        "codex-code-mode-host.exe"
                    )
                )
                $referencesStableSlot = (
                    $executablePath -match $escapedMarketplaceName -or
                    $commandLine -match $escapedMarketplaceName
                )
                $isCodexDesktopProcess -or $referencesStableSlot
            } |
            Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine
    )
}

function Assert-HostAppRegistration([object]$HostProfile) {
    $registeredApps = @(
        Get-StartApps |
            Where-Object {
                $_.AppID -ceq [string]$HostProfile.app_id -and
                $_.Name -ceq [string]$HostProfile.start_app_name
            }
    )
    if ($registeredApps.Count -ne 1) {
        throw "The exact target Codex host application registration is unavailable."
    }
    $packages = @(
        Get-AppxPackage -Name ([string]$HostProfile.package_name) |
            Where-Object {
                $_.PackageFamilyName -ceq [string]$HostProfile.package_family_name -and
                $_.Status -eq "Ok"
            }
    )
    if ($packages.Count -ne 1) {
        throw "The exact target Codex host package is unavailable or unhealthy."
    }
}

function Invoke-ExactHostTaskActivation([object]$HostProfile, [string]$TaskUri) {
    Assert-HostAppRegistration $HostProfile
    if (-not ("EvidenceLaneStableUpdateActivation" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class EvidenceLaneStableUpdateActivation {
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
        IntPtr result = manager.ActivateApplication(appUserModelId, arguments, 2, out processId);
        if (result.ToInt64() != 0) Marshal.ThrowExceptionForHR(result.ToInt32());
        return processId;
    }
}
'@
    }
    return [uint32][EvidenceLaneStableUpdateActivation]::Activate(
        [string]$HostProfile.app_id,
        $TaskUri
    )
}

function Get-TaskBindingPath() {
    return Join-Path (Join-Path ([IO.Path]::GetFullPath($DataRoot)) "installations\codex-v200\task-bindings") ($TaskId.ToLowerInvariant() + ".json")
}

function Assert-Boundary() {
    if ($TaskId -notmatch '^[0-9A-Fa-f-]{36}$') { throw "TaskId is not an exact Codex task UUID." }
    $exactArchive = (Resolve-Path -LiteralPath $Archive).Path
    $exactPackageReceipt = (Resolve-Path -LiteralPath $PackageReceipt).Path
    $exactBaseline = (Resolve-Path -LiteralPath $BaselineInstallationReceipt).Path
    $exactInstaller = (Resolve-Path -LiteralPath $InstallerScript).Path
    $exactCodex = (Resolve-Path -LiteralPath $CodexExecutable).Path
    if ((Get-Sha256 $exactBaseline) -cne $BaselineInstallationReceiptSha256.ToUpperInvariant()) {
        throw "The baseline installation file seal drifted."
    }
    $archiveSha256 = Get-Sha256 $exactArchive
    $bindingPath = Get-TaskBindingPath
    $binding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
    $baseline = Get-Content -LiteralPath $exactBaseline -Raw | ConvertFrom-Json
    if (
        $baseline.schema -cne "evidence-lane.codex-stable-installation.v2" -or
        $baseline.status -cne "PASS" -or
        [string]$baseline.plugin.version -notmatch '^2\.'
    ) {
        throw "The baseline installation is not one exact passing v2 installation."
    }
    if (
        $binding.schema -cne "evidence-lane.codex-task-binding.v1" -or
        $binding.state -cne "EXACT_TASK_BINDING_PREPARED" -or
        $binding.project_id -cne $ProjectId -or
        $binding.evidence_session_id -cne $EvidenceSessionId -or
        $binding.task_id -cne $TaskId -or
        $binding.governed_host_session_id -cne $HostSessionId -or
        $binding.install_receipt_sha256 -cne $BaselineInstallationReceiptSha256.ToUpperInvariant()
    ) {
        throw "The exact task binding does not match this update boundary."
    }

    $exactReleaseAuthority = $null
    $releaseAuthority = $null
    $exactPriorLocalCommit = $null
    $priorLocalCommit = $null
    $exactSuccessorStage = $null
    $successorStage = $null
    $tunnelRuntimeRoot = $null
    $tunnelMarkerPath = $null
    $tunnelMarker = $null
    $tunnelManager = $null
    $tunnelClient = $null
    $lockingMarketplaceName = ""
    if ($UpdateMode -eq "GitStable") {
        if (
            [string]::IsNullOrWhiteSpace($ReleaseAuthorityReceipt) -or
            [string]::IsNullOrWhiteSpace($ReleaseAuthorityReceiptSha256) -or
            -not [string]::IsNullOrWhiteSpace($PriorLocalTestCommitReceipt) -or
            -not [string]::IsNullOrWhiteSpace($PriorLocalTestCommitReceiptSha256) -or
            -not [string]::IsNullOrWhiteSpace($SuccessorStageReceipt) -or
            -not [string]::IsNullOrWhiteSpace($SuccessorStageReceiptSha256)
        ) {
            throw "Git-stable update requires only its sealed release authority."
        }
        $exactReleaseAuthority = (Resolve-Path -LiteralPath $ReleaseAuthorityReceipt).Path
        if ((Get-Sha256 $exactReleaseAuthority) -cne $ReleaseAuthorityReceiptSha256.ToUpperInvariant()) {
            throw "The governed Git/CI release-authority file seal drifted."
        }
        $releaseAuthority = Get-Content -LiteralPath $exactReleaseAuthority -Raw | ConvertFrom-Json
        if (
            $releaseAuthority.schema -cne "evidence-lane.codex-git-ci-vercel-release-authority.v2" -or
            $releaseAuthority.status -cne "PASS" -or
            $releaseAuthority.boundary -cne "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT" -or
            $releaseAuthority.archive_sha256 -cne $archiveSha256 -or
            [string]$releaseAuthority.github_ci.repository -cne $script:ExpectedGitRepository -or
            [string]$releaseAuthority.github_ci.head_sha -cne [string]$releaseAuthority.source.commit -or
            $releaseAuthority.github_ci.required_checks_complete -ne $true -or
            [int]$releaseAuthority.github_ci.failed_check_count -ne 0 -or
            [string]$releaseAuthority.vercel_preview.repository -cne $script:ExpectedGitRepository -or
            [string]$releaseAuthority.vercel_preview.branch -cne [string]$releaseAuthority.source.branch -or
            [string]$releaseAuthority.vercel_preview.head_sha -cne [string]$releaseAuthority.source.commit -or
            $releaseAuthority.vercel_preview.state -cne "READY" -or
            $releaseAuthority.vercel_preview.target -cne "PREVIEW" -or
            $releaseAuthority.vercel_preview.git_integration -ne $true -or
            $releaseAuthority.vercel_preview.manual_deploy -ne $false -or
            $releaseAuthority.vercel_preview.production_deployment -ne $false
        ) {
            throw "The release authority does not prove this exact Git commit, existing CI, and Git-integrated Vercel preview."
        }
        $lockingMarketplaceName = [string]$baseline.activation.plugin_add.marketplaceName
        if (
            [string]::IsNullOrWhiteSpace($lockingMarketplaceName) -or
            $lockingMarketplaceName -match '^evidence-lane-(?:pv[1-9][0-9]*-)?fallback$'
        ) {
            throw "The baseline installation does not identify the exact stable marketplace slot."
        }
    }
    else {
        if (
            -not [string]::IsNullOrWhiteSpace($ReleaseAuthorityReceipt) -or
            -not [string]::IsNullOrWhiteSpace($ReleaseAuthorityReceiptSha256) -or
            [string]::IsNullOrWhiteSpace($PriorLocalTestCommitReceipt) -or
            [string]::IsNullOrWhiteSpace($PriorLocalTestCommitReceiptSha256) -or
            [string]::IsNullOrWhiteSpace($SuccessorStageReceipt) -or
            [string]::IsNullOrWhiteSpace($SuccessorStageReceiptSha256)
        ) {
            throw "Disabled-hook local recovery requires only its exact prior local commit authority."
        }
        $exactPriorLocalCommit = (Resolve-Path -LiteralPath $PriorLocalTestCommitReceipt).Path
        if ((Get-Sha256 $exactPriorLocalCommit) -cne $PriorLocalTestCommitReceiptSha256.ToUpperInvariant()) {
            throw "The prior local-test commit file seal drifted."
        }
        $priorLocalCommit = Get-Content -LiteralPath $exactPriorLocalCommit -Raw | ConvertFrom-Json
        if (
            $priorLocalCommit.schema -cne "evidence-lane.codex-local-test-cas-commit.v1" -or
            $priorLocalCommit.status -cne "PASS" -or
            $priorLocalCommit.state -cne "CANDIDATE_SWITCHED_ONCE_RESTART_OR_RELOAD_REQUIRED" -or
            [string]$priorLocalCommit.candidate_selector -cne $script:LocalTestingSelector -or
            [string]$priorLocalCommit.last_known_good_selector -cne $script:CanonicalStableSelector -or
            $priorLocalCommit.candidate_enabled -ne $true -or
            $priorLocalCommit.candidate_created_or_accepted -ne $false -or
            $priorLocalCommit.pointer_moved -ne $false -or
            $priorLocalCommit.hil_inferred -ne $false -or
            [string]$baseline.activation.plugin_add.pluginId -cne $script:LocalTestingSelector
        ) {
            throw "The prior local commit and baseline do not authorize exact disabled-hook recovery."
        }
        $exactSuccessorStage = (Resolve-Path -LiteralPath $SuccessorStageReceipt).Path
        if ((Get-Sha256 $exactSuccessorStage) -cne $SuccessorStageReceiptSha256.ToUpperInvariant()) {
            throw "The pre-stop local successor stage receipt seal drifted."
        }
        $successorStage = Get-Content -LiteralPath $exactSuccessorStage -Raw | ConvertFrom-Json
        $successorHookRecords = @($successorStage.successor.hook_trust.records)
        if (
            $successorStage.schema -cne "evidence-lane.codex-local-successor-stage.v1" -or
            $successorStage.status -cne "PASS" -or
            $successorStage.state -cne "SUCCESSOR_INSTALLED_DISABLED_EIGHT_HOOKS_VERIFIED_HOST_STILL_OPEN" -or
            [string]$successorStage.package.archive_sha256 -cne $archiveSha256 -or
            [string]$successorStage.primary.selector -cne $script:LocalTestingSelector -or
            [string]$successorStage.primary.installation_receipt_sha256 -cne $BaselineInstallationReceiptSha256.ToUpperInvariant() -or
            [string]$successorStage.successor.selector -cne $script:LocalSuccessorSelector -or
            $successorStage.successor.enabled -ne $false -or
            $successorStage.successor.hook_trust.status -cne "PASS" -or
            [int]$successorStage.successor.hook_trust.hook_count -ne 8 -or
            $successorHookRecords.Count -ne 8 -or
            @($successorHookRecords | Where-Object { $_.enabled -ne $false -or $_.trust_status -cne "trusted" }).Count -ne 0 -or
            $successorStage.successor.runtime_prewarm.status -cne "PASS" -or
            [int]$successorStage.successor.runtime_prewarm.tool_count -ne 83 -or
            $successorStage.restart_gate.helper_may_be_scheduled -ne $true -or
            $successorStage.restart_gate.exact_host_stop_occurred -ne $false -or
            $successorStage.accepted_two_slot_registry.mutated -ne $false -or
            $successorStage.candidate_created_or_accepted -ne $false -or
            $successorStage.pointer_moved -ne $false -or
            $successorStage.hil_inferred -ne $false
        ) {
            throw "The local successor was not fully installed and eight-hook verified before helper scheduling."
        }
        $tunnelRuntimeRoot = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "tunnel-runtime-v220-stable-build"
        $tunnelMarkerPath = Join-Path $tunnelRuntimeRoot "evidence-lane-tunnel-installation.json"
        $tunnelManager = Join-Path $tunnelRuntimeRoot "Manage-EvidenceLaneTunnel.ps1"
        $tunnelClient = Join-Path $tunnelRuntimeRoot "bin\tunnel-client-v0.0.10.exe"
        foreach ($requiredTunnelFile in @($tunnelMarkerPath, $tunnelManager, $tunnelClient)) {
            if (-not (Test-Path -LiteralPath $requiredTunnelFile -PathType Leaf)) {
                throw "The exact saved v2.2 tunnel rollover authority is incomplete."
            }
        }
        $tunnelMarker = Get-Content -LiteralPath $tunnelMarkerPath -Raw | ConvertFrom-Json
        $tunnelTask = Get-ScheduledTask -TaskName ([string]$tunnelMarker.task_name) -ErrorAction SilentlyContinue
        if (
            $tunnelMarker.schema -cne "evidence-lane.versioned-secure-mcp-tunnel-installation.v1" -or
            [string]$tunnelMarker.release -cne "2.2.0" -or
            [string]$tunnelMarker.release_token -cne "v220" -or
            [string]$tunnelMarker.slot_role -cne "stable-build" -or
            [IO.Path]::GetFullPath([string]$tunnelMarker.runtime_root) -cne [IO.Path]::GetFullPath($tunnelRuntimeRoot) -or
            [string]$tunnelMarker.task_name -cne "EvidenceLane-Tunnel-v220-stable-build" -or
            [string]$tunnelMarker.profile_name -cne "evidence_lane_v220_stable_build_transport" -or
            [string]$tunnelMarker.windows_console_policy -cne "WINDOWS_GUI_HOST_CREATE_NO_WINDOW" -or
            [string]$tunnelMarker.host_lifetime -cne "PERSISTENT" -or
            [string]$tunnelMarker.interaction_profile -cne "CODEX_APP_INTERACTIVE" -or
            [string]$tunnelMarker.data_root -cne [IO.Path]::GetFullPath($DataRoot) -or
            $null -eq $tunnelTask -or
            [string]$tunnelTask.State -cne "Running"
        ) {
            throw "The exact current v2.2 host transport tunnel is not running under its sealed persistent authority."
        }
        $lockingMarketplaceName = $script:LocalTestingMarketplaceName
    }
    $twoSlotPath = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "installations\codex-v200\two-slot\CODEX_TWO_SLOT_REGISTRY.json"
    $twoSlotSha256 = if (Test-Path -LiteralPath $twoSlotPath -PathType Leaf) { Get-Sha256 $twoSlotPath } else { $null }
    return [ordered]@{
        update_mode = $UpdateMode
        archive = $exactArchive
        package_receipt = $exactPackageReceipt
        release_authority = $exactReleaseAuthority
        prior_local_commit = $exactPriorLocalCommit
        successor_stage = $exactSuccessorStage
        baseline = $exactBaseline
        installer = $exactInstaller
        codex = $exactCodex
        stable_marketplace_name = $lockingMarketplaceName
        release_authority_data = $releaseAuthority
        prior_local_commit_data = $priorLocalCommit
        successor_stage_data = $successorStage
        tunnel_runtime_root = $tunnelRuntimeRoot
        tunnel_marker_path = $tunnelMarkerPath
        tunnel_marker_sha256 = if ($null -eq $tunnelMarkerPath) { $null } else { Get-Sha256 $tunnelMarkerPath }
        tunnel_marker_data = $tunnelMarker
        tunnel_manager = $tunnelManager
        tunnel_manager_sha256 = if ($null -eq $tunnelManager) { $null } else { Get-Sha256 $tunnelManager }
        tunnel_client = $tunnelClient
        tunnel_client_sha256 = if ($null -eq $tunnelClient) { $null } else { Get-Sha256 $tunnelClient }
        two_slot_path = $twoSlotPath
        two_slot_sha256 = $twoSlotSha256
        binding_path = $bindingPath
        binding = $binding
    }
}

function Open-ExactTask([object]$HostProfile) {
    $uri = "codex://threads/$TaskId"
    $activationProcessId = Invoke-ExactHostTaskActivation -HostProfile $HostProfile -TaskUri $uri
    if (-not ("EvidenceLaneExactHostWindow" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class EvidenceLaneExactHostWindow {
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsZoomed(IntPtr hWnd);
}
'@
    }
    $windowDeadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    $windowHandle = [IntPtr]::Zero
    $windowMaximized = $false
    do {
        $activatedProcess = Get-Process -Id $activationProcessId -ErrorAction SilentlyContinue
        if ($null -ne $activatedProcess -and $activatedProcess.MainWindowHandle -ne 0) {
            $windowHandle = [IntPtr]$activatedProcess.MainWindowHandle
            [void][EvidenceLaneExactHostWindow]::ShowWindowAsync($windowHandle, 3)
            [void][EvidenceLaneExactHostWindow]::SetForegroundWindow($windowHandle)
            Start-Sleep -Milliseconds 150
            $windowMaximized = [EvidenceLaneExactHostWindow]::IsZoomed($windowHandle)
            if ($windowMaximized) { break }
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $windowDeadline)
    if (-not $windowMaximized) {
        throw "The exact reopened Codex host did not reach its maximized full-window state."
    }
    return [ordered]@{
        task_uri = $uri
        host_app_id = [string]$HostProfile.app_id
        activation_request_process_id = $activationProcessId
        exact_task_reopen_count = 1
        window_state = "MAXIMIZED_FULL_WINDOW"
        maximized_verified = $true
    }
}

$boundary = Assert-Boundary
$exactReceiptDirectory = [IO.Path]::GetFullPath($ReceiptDirectory)
[IO.Directory]::CreateDirectory($exactReceiptDirectory) | Out-Null

if ($Action -eq "Schedule") {
    if (-not $ConfirmRestart) { throw "Schedule requires -ConfirmRestart." }
    $target = Get-ExactRootProcess -ProcessId $TargetProcessId -ExpectedAppId $HostAppId
    $process = $target.process
    $hostProfile = $target.profile
    $scheduled = [ordered]@{
        schema = if ($UpdateMode -eq "GitStable") {
            "evidence-lane.codex-stable-same-slot-update-schedule.v1"
        }
        else {
            "evidence-lane.codex-local-disabled-hook-recovery-schedule.v1"
        }
        status = "PASS"
        state = "SCHEDULED_BEFORE_EXACT_APP_RESTART"
        update_mode = $UpdateMode
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        active_plan_task_id = $ActivePlanTaskId
        target_process_id = $TargetProcessId
        target_executable_sha256 = Get-Sha256 ([string]$process.ExecutablePath)
        target_host_application = [string]$hostProfile.host_application
        target_host_app_id = [string]$hostProfile.app_id
        target_package_family_name = [string]$hostProfile.package_family_name
        archive_sha256 = Get-Sha256 $boundary.archive
        package_receipt_sha256 = Get-Sha256 $boundary.package_receipt
        release_authority_receipt_sha256 = if ($null -eq $boundary.release_authority) { $null } else { Get-Sha256 $boundary.release_authority }
        prior_local_test_commit_receipt_sha256 = if ($null -eq $boundary.prior_local_commit) { $null } else { Get-Sha256 $boundary.prior_local_commit }
        successor_stage_receipt_sha256 = if ($null -eq $boundary.successor_stage) { $null } else { Get-Sha256 $boundary.successor_stage }
        host_transport_tunnel_marker_sha256 = $boundary.tunnel_marker_sha256
        host_transport_tunnel_task_name = if ($null -eq $boundary.tunnel_marker_data) { $null } else { [string]$boundary.tunnel_marker_data.task_name }
        app_stop_owner = "ONE_USE_HIDDEN_MAINTAINER_HELPER"
        app_stop_count = 1
        baseline_installation_receipt_sha256 = Get-Sha256 $boundary.baseline
        stable_selector_reuse_after_canonical_migration_required = $UpdateMode -eq "GitStable"
        one_time_canonical_git_selector_migration_allowed = $UpdateMode -eq "GitStable"
        exact_local_selector_reinstall_after_host_stop_required = $UpdateMode -eq "LocalDisabledHookRecovery"
        exact_eight_hooks_required = $UpdateMode -eq "LocalDisabledHookRecovery"
        new_selector_allowed = $false
        fallback_activation_allowed = $false
        exact_task_reopen_required = $true
        scheduled_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $schedulePath = Join-Path $exactReceiptDirectory ("SCHEDULE_" + $scheduled.archive_sha256.Substring(0, 16) + ".json")
    $transientTaskName = "EvidenceLaneCodexPostStopUpdate-" + $scheduled.archive_sha256.Substring(0, 16)
    if ($null -ne (Get-ScheduledTask -TaskName $transientTaskName -ErrorAction SilentlyContinue)) {
        throw "The exact transient stable-update Scheduled Task already exists."
    }
    $scheduled.transient_scheduled_task_name = $transientTaskName
    Write-Json $schedulePath $scheduled

    $arguments = @(
        "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Action", "Complete",
        "-UpdateMode", $UpdateMode,
        "-Archive", $boundary.archive,
        "-PackageReceipt", $boundary.package_receipt,
        "-BaselineInstallationReceipt", $boundary.baseline,
        "-BaselineInstallationReceiptSha256", $BaselineInstallationReceiptSha256,
        "-ProjectId", $ProjectId,
        "-EvidenceSessionId", $EvidenceSessionId,
        "-TaskId", $TaskId,
        "-HostSessionId", $HostSessionId,
        "-ActivePlanTaskId", $ActivePlanTaskId,
        "-TargetProcessId", [string]$TargetProcessId,
        "-CodexHome", ([IO.Path]::GetFullPath($CodexHome)),
        "-DataRoot", ([IO.Path]::GetFullPath($DataRoot)),
        "-CodexExecutable", $boundary.codex,
        "-PythonExecutable", $PythonExecutable,
        "-InstallerScript", $boundary.installer,
        "-HookCwd", ([IO.Path]::GetFullPath($HookCwd)),
        "-ReceiptDirectory", $exactReceiptDirectory,
        "-ScheduledTaskName", $transientTaskName,
        "-HostAppId", ([string]$hostProfile.app_id),
        "-HostExecutableSha256", ([string]$scheduled.target_executable_sha256)
    )
    if ($UpdateMode -eq "GitStable") {
        $arguments += @(
            "-ReleaseAuthorityReceipt", $boundary.release_authority,
            "-ReleaseAuthorityReceiptSha256", $ReleaseAuthorityReceiptSha256
        )
    }
    else {
        $arguments += @(
            "-PriorLocalTestCommitReceipt", $boundary.prior_local_commit,
            "-PriorLocalTestCommitReceiptSha256", $PriorLocalTestCommitReceiptSha256,
            "-SuccessorStageReceipt", $boundary.successor_stage,
            "-SuccessorStageReceiptSha256", $SuccessorStageReceiptSha256
        )
    }
    $argumentLine = ($arguments | ForEach-Object { ConvertTo-WindowsArgument ([string]$_) }) -join " "
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $scheduledAction = New-ScheduledTaskAction -Execute $powershell -Argument $argumentLine
    $scheduledPrincipal = New-ScheduledTaskPrincipal `
        -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive `
        -RunLevel Limited
    $scheduledSettings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask `
            -TaskName $transientTaskName `
            -Action $scheduledAction `
            -Principal $scheduledPrincipal `
            -Settings $scheduledSettings `
            -Description "One-use post-stop Evidence Lane update and exact bound Codex task reopen." |
            Out-Null
        Start-ScheduledTask -TaskName $transientTaskName
        $startDeadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
        do {
            $scheduledTaskState = [string](Get-ScheduledTask -TaskName $transientTaskName).State
            if ($scheduledTaskState -eq "Running") { break }
            Start-Sleep -Milliseconds 100
        } while ([DateTimeOffset]::UtcNow -lt $startDeadline)
        if ($scheduledTaskState -ne "Running") {
            throw "The transient stable-update Scheduled Task did not enter Running state."
        }
    }
    catch {
        Unregister-ScheduledTask -TaskName $transientTaskName -Confirm:$false -ErrorAction SilentlyContinue
        throw
    }
    $scheduled | ConvertTo-Json -Depth 16
    exit 0
}

$resultPath = Join-Path $exactReceiptDirectory ("RESULT_" + (Get-Sha256 $boundary.archive).Substring(0, 16) + ".json")
$hostStop = $null
$tunnelStop = $null
$tunnelRollover = $null
try {
    $hostProfile = Get-HostAppProfile $HostAppId
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    if ($HostExecutableSha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "The exact target Codex executable SHA-256 was not carried into the completion action."
    }
    $exactTarget = Get-ExactRootProcess -ProcessId $TargetProcessId -ExpectedAppId $HostAppId
    $targetExecutableSha256 = Get-Sha256 ([string]$exactTarget.process.ExecutablePath)
    if ($targetExecutableSha256 -cne $HostExecutableSha256.ToUpperInvariant()) {
        throw "The exact target Codex executable changed before the helper stop boundary."
    }
    Stop-Process -Id $TargetProcessId -Force
    $stopDeadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    do {
        $remainingTarget = Get-CimInstance Win32_Process -Filter "ProcessId=$TargetProcessId" -ErrorAction SilentlyContinue
        if ($null -eq $remainingTarget) { break }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $stopDeadline)
    if ($null -ne $remainingTarget) {
        throw "The one-use helper could not stop the exact bound Codex host."
    }
    $hostStop = [ordered]@{
        status = "PASS"
        owner = "ONE_USE_HIDDEN_MAINTAINER_HELPER"
        stop_count = 1
        target_process_id = $TargetProcessId
        target_host_app_id = [string]$hostProfile.app_id
        target_executable_sha256 = $targetExecutableSha256
        successor_installed_and_eight_hooks_verified_before_stop = $true
    }
    if ($UpdateMode -eq "LocalDisabledHookRecovery") {
        $tunnelStopOutput = @(
            & $powershell `
                -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden `
                -ExecutionPolicy Bypass -File $boundary.tunnel_manager `
                -Action Stop `
                -RuntimeRoot $boundary.tunnel_runtime_root `
                -ProfileName ([string]$boundary.tunnel_marker_data.profile_name) `
                -ProfileDir (Split-Path -Parent ([string]$boundary.tunnel_marker_data.profile_file)) `
                -ReleaseToken ([string]$boundary.tunnel_marker_data.release_token) `
                -TaskName ([string]$boundary.tunnel_marker_data.task_name) 2>&1 |
                ForEach-Object { [string]$_ }
        )
        $tunnelStopExitCode = $LASTEXITCODE
        if ($tunnelStopExitCode -ne 0) {
            throw ("The one-use helper could not stop the exact saved v2.2 tunnel:`n" + ($tunnelStopOutput -join "`n"))
        }
        $tunnelStopResult = ($tunnelStopOutput -join "`n") | ConvertFrom-Json
        $stoppedTunnelTask = Get-ScheduledTask -TaskName ([string]$boundary.tunnel_marker_data.task_name) -ErrorAction SilentlyContinue
        if (
            $tunnelStopResult.status -cne "STOPPED_SAVED" -or
            [string]$tunnelStopResult.task_name -cne [string]$boundary.tunnel_marker_data.task_name -or
            $null -eq $stoppedTunnelTask -or
            [string]$stoppedTunnelTask.State -cne "Disabled"
        ) {
            throw "The exact saved v2.2 tunnel did not reach its disabled rollover boundary."
        }
        $tunnelStop = [ordered]@{
            status = "PASS"
            owner = "ONE_USE_HIDDEN_MAINTAINER_HELPER"
            stop_count = 1
            task_name = [string]$boundary.tunnel_marker_data.task_name
            marker_sha256 = [string]$boundary.tunnel_marker_sha256
            saved_runtime_preserved = $true
            app_stop_count_unchanged = 1
        }
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
    while (@(Get-LockingCodexProcesses -StableMarketplaceName $boundary.stable_marketplace_name -HostProfile $hostProfile).Count -gt 0) {
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            $remaining = @(
                Get-LockingCodexProcesses -StableMarketplaceName $boundary.stable_marketplace_name -HostProfile $hostProfile |
                    ForEach-Object { "{0}:{1}" -f $_.Name, $_.ProcessId }
            ) -join ", "
            throw "Codex or the stable MCP slot did not release before the update deadline: $remaining"
        }
        Start-Sleep -Milliseconds 250
    }
    $python = (Get-Command $PythonExecutable -ErrorAction Stop).Source
    $installerArguments = @(
        $boundary.installer,
        "--archive", $boundary.archive,
        "--package-receipt", $boundary.package_receipt,
        "--baseline-installation-receipt", $boundary.baseline,
        "--baseline-installation-receipt-sha256", $BaselineInstallationReceiptSha256,
        "--codex-home", ([IO.Path]::GetFullPath($CodexHome)),
        "--data-root", ([IO.Path]::GetFullPath($DataRoot)),
        "--codex-executable", $boundary.codex,
        "--hook-cwd", ([IO.Path]::GetFullPath($HookCwd))
    )
    if ($UpdateMode -eq "GitStable") {
        $installerArguments += @(
            "--release-authority-receipt", $boundary.release_authority,
            "--release-authority-receipt-sha256", $ReleaseAuthorityReceiptSha256,
            "--activate", "--trust-sealed-hooks"
        )
    }
    else {
        $installerArguments += @(
            "--marketplace-name", $script:LocalTestingMarketplaceName,
            "--activate-local-test",
            "--confirm-local-test-rotation", "EXPLICIT_DISABLED_LOCAL_2_2_HOOK_RECOVERY",
            "--recover-disabled-local-hooks-from", $boundary.prior_local_commit,
            "--recover-disabled-local-hooks-from-sha256", $PriorLocalTestCommitReceiptSha256
        )
    }
    $priorErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell converts native stderr into ErrorRecord objects.  With
        # Stop semantics that truncated the installer failure to only "Traceback".
        # Capture the complete bounded native output before restoring fail-closed
        # PowerShell semantics so the sealed failure receipt remains diagnostic.
        $ErrorActionPreference = "Continue"
        $rawOutput = @(
            & $python @installerArguments 2>&1 |
                ForEach-Object { [string]$_ }
        )
        $installerExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    if ($installerExitCode -ne 0) {
        $boundedOutput = @($rawOutput | Select-Object -Last 80) -join "`n"
        if ($boundedOutput.Length -gt 24000) {
            $boundedOutput = $boundedOutput.Substring($boundedOutput.Length - 24000)
        }
        if ($UpdateMode -eq "GitStable") {
            throw ("The exact GitLane installer failed:`n" + $boundedOutput)
        }
        throw ("The exact post-stop Evidence Lane installer failed:`n" + $boundedOutput)
    }
    $install = ($rawOutput -join "`n") | ConvertFrom-Json
    if ($UpdateMode -eq "GitStable") {
        $selectorReusedOrMigrated = (
            ($install.stable_selector_reused -eq $true) -xor
            ($install.stable_selector_migrated_to_canonical_git -eq $true)
        )
        if (
            $install.status -cne "PASS" -or
            -not $selectorReusedOrMigrated -or
            $install.new_stable_selector_created -ne $false -or
            [string]$install.activation.plugin_add.pluginId -cne $script:CanonicalStableSelector -or
            [string]$install.activation.git_marketplace_source.source_type -cne "git" -or
            [string]$install.activation.git_marketplace_source.commit -cne [string]$boundary.release_authority_data.source.commit -or
            $install.activation.post_proof_cleanup.status -cne "PASS" -or
            $install.post_proof_obsolete_cleanup_completed -ne $true -or
            $install.runtime_ready_before_task_reopen -ne $true -or
            $install.activation.runtime_ready_before_task_reopen -ne $true -or
            $install.activation.runtime_prewarm.status -cne "PASS" -or
            $install.two_slot_registry_update.status -cne "PASS"
        ) {
            throw "The installer did not prove the one canonical Git stable selector, post-proof cleanup, and two intact slots."
        }
    }
    else {
        $hookRecords = @($install.activation.hook_trust.records)
        $registeredEvents = @($install.activation.hook_trust.registered_events | Sort-Object)
        $expectedEvents = @("postCompact", "postToolUse", "preCompact", "preToolUse", "sessionEnd", "sessionStart", "stop", "userPromptSubmit")
        $invalidHookRecords = @(
            $hookRecords | Where-Object {
                [string]$_.hook_key -cnotlike ($script:LocalTestingSelector + ":hooks/hooks.json:*") -or
                [string]$_.trust_status -cne "trusted" -or
                $_.enabled -ne $true -or
                [string]$_.current_hash -cnotmatch '^sha256:[0-9a-f]{64}$'
            }
        )
        if (
            $install.status -cne "PASS" -or
            [string]$install.activation.plugin_add.pluginId -cne $script:LocalTestingSelector -or
            [string]$install.activation.state -cne "LOCAL_2_2_HOOK_RECOVERY_SWITCHED_RESTART_REQUIRED" -or
            $install.activation.transaction.schema -cne "evidence-lane.codex-local-test-disabled-hook-recovery.v1" -or
            $install.activation.transaction.state -cne "LOCAL_SELECTOR_SWITCHED_ONCE" -or
            $install.activation.transaction.compare_and_swap -ne $true -or
            [int]$install.activation.transaction.switch_count -ne 1 -or
            $install.activation.transaction.rollback_capable -ne $true -or
            $install.activation.transaction.stable_and_fallback_enabled -ne $false -or
            $install.activation.transaction.exact_eight_hook_hashes_trusted -ne $true -or
            $install.activation.transaction.candidate_created_or_accepted -ne $false -or
            $install.activation.transaction.pointer_moved -ne $false -or
            $install.activation.transaction.hil_inferred -ne $false -or
            [string]$install.activation.transaction.prior_commit_authority.path -cne $boundary.prior_local_commit -or
            [string]$install.activation.transaction.prior_commit_authority.file_sha256 -cne $PriorLocalTestCommitReceiptSha256.ToUpperInvariant() -or
            $install.activation.local_test_reinstall.status -cne "PASS" -or
            $install.activation.local_test_reinstall.transaction_mode -cne "DISABLED_LOCAL_HOOK_RECOVERY" -or
            $install.activation.local_test_reinstall.accepted_two_slot_registry_mutated -ne $false -or
            $install.activation.hook_trust.status -cne "PASS" -or
            $install.activation.hook_trust.disabled_local_recovery -ne $true -or
            [int]$install.activation.hook_trust.hook_count -ne 8 -or
            $hookRecords.Count -ne 8 -or
            $invalidHookRecords.Count -ne 0 -or
            (($registeredEvents -join "|") -cne ($expectedEvents -join "|")) -or
            $install.activation.runtime_prewarm.status -cne "PASS" -or
            [int]$install.activation.runtime_prewarm.tool_count -ne 83 -or
            $install.activation.runtime_ready_before_task_reopen -ne $false -or
            $install.runtime_ready_before_task_reopen -ne $false -or
            $install.restart_required -ne $true -or
            $install.activation_authority.boundary -cne "EXPLICIT_DISABLED_LOCAL_2_2_HOOK_RECOVERY" -or
            $install.activation_authority.accepted_two_slot_registry_mutated -ne $false -or
            $install.two_slot_registry_update.status -cne "NOT_APPLICABLE" -or
            $install.candidate_created_or_accepted -ne $false -or
            $install.pointer_moved -ne $false -or
            $install.hil_inferred -ne $false
        ) {
            throw "The installer did not prove the exact local 2.2 selector and all eight corrected hooks."
        }
    }
    $installReceipt = (Resolve-Path -LiteralPath ([string]$install.receipt_path)).Path
    $installReceiptSha256 = Get-Sha256 $installReceipt
    $archiveSha256 = Get-Sha256 $boundary.archive
    $localRecovery = $null
    $localRecoveryRegistry = $null
    $localRecoveryRegistrySha256 = $null
    $successorCleanup = $null
    if ($UpdateMode -eq "LocalDisabledHookRecovery") {
        $recoveryArguments = @(
            $boundary.installer,
            "--materialize-local-recovery-copy",
            "--archive", $boundary.archive,
            "--package-receipt", $boundary.package_receipt,
            "--primary-installation-receipt", $installReceipt,
            "--primary-installation-receipt-sha256", $installReceiptSha256,
            "--confirm-local-recovery-copy", "EXPLICIT_BYTE_IDENTICAL_LOCAL_2_2_RECOVERY",
            "--codex-home", ([IO.Path]::GetFullPath($CodexHome)),
            "--data-root", ([IO.Path]::GetFullPath($DataRoot)),
            "--codex-executable", $boundary.codex,
            "--hook-cwd", ([IO.Path]::GetFullPath($HookCwd))
        )
        $priorErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $rawRecoveryOutput = @(
                & $python @recoveryArguments 2>&1 |
                    ForEach-Object { [string]$_ }
            )
            $recoveryExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $priorErrorActionPreference
        }
        if ($recoveryExitCode -ne 0) {
            $boundedRecoveryOutput = @($rawRecoveryOutput | Select-Object -Last 80) -join "`n"
            if ($boundedRecoveryOutput.Length -gt 24000) {
                $boundedRecoveryOutput = $boundedRecoveryOutput.Substring($boundedRecoveryOutput.Length - 24000)
            }
            throw ("The byte-identical local recovery materialization failed:`n" + $boundedRecoveryOutput)
        }
        $localRecovery = ($rawRecoveryOutput -join "`n") | ConvertFrom-Json
        $localRecoveryRegistry = (Resolve-Path -LiteralPath ([string]$localRecovery.current_registry_path)).Path
        $localRecoveryRegistrySha256 = Get-Sha256 $localRecoveryRegistry
        if (
            $localRecovery.schema -cne "evidence-lane.codex-local-v220-recovery-registry.v1" -or
            $localRecovery.status -cne "PASS" -or
            $localRecovery.state -cne "PRIMARY_LOCAL_ACTIVE_RECOVERY_DISABLED_BYTE_IDENTICAL" -or
            [string]$localRecovery.package.archive_sha256 -cne $archiveSha256 -or
            [string]$localRecovery.primary.selector -cne $script:LocalTestingSelector -or
            $localRecovery.primary.enabled -ne $true -or
            $localRecovery.primary.native_mcp_enabled -ne $true -or
            [string]$localRecovery.primary.installation_receipt -cne $installReceipt -or
            [string]$localRecovery.primary.installation_receipt_sha256 -cne $installReceiptSha256 -or
            [string]$localRecovery.recovery.selector -cne $script:LocalRecoverySelector -or
            $localRecovery.recovery.enabled -ne $false -or
            $localRecovery.recovery.native_mcp_enabled -ne $false -or
            $localRecovery.recovery.byte_identical_to_primary -ne $true -or
            [int]$localRecovery.recovery.hook_trust.hook_count -ne 8 -or
            $localRecovery.accepted_two_slot_registry.mutated -ne $false -or
            $localRecovery.selection_law.accepted_2_1_stable_or_fallback_automatic_recovery_allowed -ne $false -or
            $localRecovery.selection_law.host_restart_required_after_selector_switch -ne $true -or
            $localRecovery.selection_law.restart_loop_allowed -ne $false -or
            $localRecovery.candidate_created_or_accepted -ne $false -or
            $localRecovery.pointer_moved -ne $false -or
            $localRecovery.hil_inferred -ne $false -or
            $localRecoveryRegistrySha256 -cne [string]$localRecovery.current_registry_file_sha256
        ) {
            throw "The local recovery registry did not prove one active primary and one disabled byte-identical recovery copy."
        }
        $priorErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $successorPluginOutput = @(
                & $boundary.codex plugin remove $script:LocalSuccessorSelector --json 2>&1 |
                    ForEach-Object { [string]$_ }
            )
            $successorPluginExitCode = $LASTEXITCODE
            if ($successorPluginExitCode -ne 0) {
                throw ("The verified successor selector cleanup failed: " + ($successorPluginOutput -join "`n"))
            }
            $successorMarketplaceOutput = @(
                & $boundary.codex plugin marketplace remove $script:LocalSuccessorMarketplaceName --json 2>&1 |
                    ForEach-Object { [string]$_ }
            )
            $successorMarketplaceExitCode = $LASTEXITCODE
            if ($successorMarketplaceExitCode -ne 0) {
                throw ("The verified successor marketplace cleanup failed: " + ($successorMarketplaceOutput -join "`n"))
            }
        }
        finally {
            $ErrorActionPreference = $priorErrorActionPreference
        }
        $successorCleanup = [ordered]@{
            status = "PASS"
            selector = $script:LocalSuccessorSelector
            marketplace = $script:LocalSuccessorMarketplaceName
            selector_removed_after_primary_and_recovery_proof = $true
            marketplace_removed_after_primary_and_recovery_proof = $true
            source_stage_receipt_sha256 = $SuccessorStageReceiptSha256.ToUpperInvariant()
        }
        $newPrimaryPluginRoot = [IO.Path]::GetFullPath([string]$install.activation.plugin_add.installedPath)
        $expectedPrimaryCacheRoot = [IO.Path]::GetFullPath(
            (Join-Path ([IO.Path]::GetFullPath($CodexHome)) "plugins\cache\$($script:LocalTestingMarketplaceName)\evidence-lane-plugin")
        )
        $newTunnelInstaller = Join-Path $newPrimaryPluginRoot "scripts\windows_tunnel\Install-EvidenceLaneTunnel.ps1"
        if (
            -not $newPrimaryPluginRoot.StartsWith(
                $expectedPrimaryCacheRoot + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not (Test-Path -LiteralPath $newTunnelInstaller -PathType Leaf)
        ) {
            throw "The reinstalled local primary does not expose its exact tunnel installer."
        }
        $tunnelClientCopy = Join-Path $exactReceiptDirectory ("TUNNEL_CLIENT_" + $archiveSha256.Substring(0, 16) + ".exe")
        if (Test-Path -LiteralPath $tunnelClientCopy) {
            throw "The exact one-use tunnel rollover client copy already exists."
        }
        Copy-Item -LiteralPath $boundary.tunnel_client -Destination $tunnelClientCopy
        if ((Get-Sha256 $tunnelClientCopy) -cne [string]$boundary.tunnel_client_sha256) {
            throw "The one-use tunnel rollover client copy drifted."
        }
        try {
            $tunnelInstallOutput = @(
                & $powershell `
                    -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden `
                    -ExecutionPolicy Bypass -File $newTunnelInstaller `
                    -TunnelClientSource $tunnelClientCopy `
                    -PluginRoot $newPrimaryPluginRoot `
                    -DataRoot ([IO.Path]::GetFullPath($DataRoot)) `
                    -SlotRole "stable-build" `
                    -RuntimeRoot $boundary.tunnel_runtime_root `
                    -ProfileName ([string]$boundary.tunnel_marker_data.profile_name) `
                    -TaskName ([string]$boundary.tunnel_marker_data.task_name) `
                    -HostLifetime "Persistent" `
                    -InteractionProfile ([string]$boundary.tunnel_marker_data.interaction_profile) `
                    -AccountTier ([string]$boundary.tunnel_marker_data.account_tier) `
                    -Activate 2>&1 |
                    ForEach-Object { [string]$_ }
            )
            $tunnelInstallExitCode = $LASTEXITCODE
        }
        finally {
            Remove-Item -LiteralPath $tunnelClientCopy -Force -ErrorAction SilentlyContinue
        }
        if ($tunnelInstallExitCode -ne 0) {
            throw ("The exact hidden v2.2 tunnel rollover failed:`n" + ($tunnelInstallOutput -join "`n"))
        }
        $tunnelInstall = ($tunnelInstallOutput -join "`n") | ConvertFrom-Json
        $newTunnelMarker = Get-Content -LiteralPath $boundary.tunnel_marker_path -Raw | ConvertFrom-Json
        $newTunnelManager = Join-Path $boundary.tunnel_runtime_root "Manage-EvidenceLaneTunnel.ps1"
        $tunnelStatusOutput = @(
            & $powershell `
                -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden `
                -ExecutionPolicy Bypass -File $newTunnelManager `
                -Action Status `
                -RuntimeRoot $boundary.tunnel_runtime_root `
                -ProfileName ([string]$newTunnelMarker.profile_name) `
                -ProfileDir (Split-Path -Parent ([string]$newTunnelMarker.profile_file)) `
                -ReleaseToken ([string]$newTunnelMarker.release_token) `
                -TaskName ([string]$newTunnelMarker.task_name) 2>&1 |
                ForEach-Object { [string]$_ }
        )
        $tunnelStatusExitCode = $LASTEXITCODE
        $tunnelStatus = ($tunnelStatusOutput -join "`n") | ConvertFrom-Json
        if (
            $tunnelStatusExitCode -ne 0 -or
            $tunnelInstall.status -cne "PASS" -or
            $tunnelInstall.activated -ne $true -or
            $tunnelInstall.started -ne $true -or
            [int]$tunnelInstall.exact_visible_tool_count -ne 83 -or
            [string]$tunnelInstall.windows_console_policy -cne "PERSISTENT_OR_HIDDEN_NO_TRANSIENT_CONSOLE" -or
            $tunnelStatus.status -cne "PASS" -or
            $tunnelStatus.control_plane_poll_ready -ne $true -or
            $tunnelStatus.process_running -ne $true -or
            [string]$tunnelStatus.task_state -cne "Running" -or
            [string]$newTunnelMarker.plugin_root -cne $newPrimaryPluginRoot -or
            [string]$newTunnelMarker.task_name -cne [string]$boundary.tunnel_marker_data.task_name -or
            [string]$newTunnelMarker.tunnel_id -cne [string]$boundary.tunnel_marker_data.tunnel_id -or
            [string]$newTunnelMarker.windows_console_policy -cne "WINDOWS_GUI_HOST_CREATE_NO_WINDOW"
        ) {
            throw "The saved v2.2 tunnel did not restart hidden from the reinstalled local primary."
        }
        $tunnelRollover = [ordered]@{
            status = "PASS"
            task_name = [string]$newTunnelMarker.task_name
            release = [string]$newTunnelMarker.release
            slot_role = [string]$newTunnelMarker.slot_role
            marker_sha256_before = [string]$boundary.tunnel_marker_sha256
            marker_sha256_after = Get-Sha256 $boundary.tunnel_marker_path
            manager_sha256_after = Get-Sha256 $newTunnelManager
            exact_visible_tool_count = 83
            control_plane_poll_ready = $true
            hidden_window_verified_by_contract = $true
            persistent_logon_task_running = $true
            tunnel_id_preserved = $true
            plugin_root_rebound_to_reinstalled_primary = $true
            app_stop_count = 1
        }
    }
    if (
        $null -ne $boundary.two_slot_sha256 -and
        (Get-Sha256 $boundary.two_slot_path) -cne [string]$boundary.two_slot_sha256
    ) {
        throw "The accepted stable/fallback two-slot registry changed during this local operation."
    }
    $restartAuthority = if ($UpdateMode -eq "GitStable") {
        [ordered]@{
            schema = "evidence-lane.codex-restart-authority.v1"
            mode = "GIT_STABLE_INSTALL_RECEIPT"
            install_receipt_sha256 = $installReceiptSha256
            plugin_version = [string]$install.plugin.version
        }
    }
    else {
        [ordered]@{
            schema = "evidence-lane.codex-restart-authority.v1"
            mode = "LOCAL_TEST_DISABLED_HOOK_RECOVERY_INSTALL_RECEIPT"
            install_receipt_sha256 = $installReceiptSha256
            plugin_version = [string]$install.plugin.version
            prior_commit_receipt = $boundary.prior_local_commit
            prior_commit_receipt_sha256 = $PriorLocalTestCommitReceiptSha256.ToUpperInvariant()
            pre_stop_successor_stage_receipt = $boundary.successor_stage
            pre_stop_successor_stage_receipt_sha256 = $SuccessorStageReceiptSha256.ToUpperInvariant()
            pre_stop_successor_selector = $script:LocalSuccessorSelector
            pre_stop_successor_eight_hooks_verified = $true
            transaction_id = [string]$install.activation.transaction.transaction_id
            prior_transaction_id = [string]$boundary.prior_local_commit_data.transaction_id
            candidate_selector = $script:LocalTestingSelector
            last_known_good_selector = [string]$boundary.prior_local_commit_data.last_known_good_selector
            hook_trust_receipt_sha256 = [string]$install.activation.hook_trust.receipt_sha256
            hook_count = 8
            enabled_evidence_lane_selector_count = 1
            local_recovery_registry = $localRecoveryRegistry
            local_recovery_registry_sha256 = $localRecoveryRegistrySha256
            local_recovery_selector = $script:LocalRecoverySelector
            local_recovery_byte_identical_to_primary = $true
            host_transport_tunnel_marker_sha256 = [string]$tunnelRollover.marker_sha256_after
            host_transport_tunnel_restarted_hidden = $true
            host_transport_tunnel_task_name = [string]$tunnelRollover.task_name
            accepted_2_1_automatic_recovery_allowed = $false
        }
    }
    $restartAuthoritySha256 = Get-StringSha256 ($restartAuthority | ConvertTo-Json -Compress -Depth 12)
    $preparationPath = Join-Path $exactReceiptDirectory ("PREPARE_" + $archiveSha256.Substring(0, 16) + ".json")
    $oldPreparation = Get-Content -LiteralPath ([string]$boundary.binding.preparation_receipt) -Raw | ConvertFrom-Json
    $preparation = [ordered]@{
        schema = "evidence-lane.codex-restart-preparation.v2"
        state = "PREPARED_NOT_RESTARTED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        install_receipt = $installReceipt
        install_receipt_sha256 = $installReceiptSha256
        release_authority_receipt_sha256 = if ($null -eq $boundary.release_authority) { $null } else { Get-Sha256 $boundary.release_authority }
        restart_authority = $restartAuthority
        restart_authority_sha256 = $restartAuthoritySha256
        runtime_prewarm_receipt_sha256 = [string]$install.activation.runtime_prewarm.receipt_sha256
        runtime_ready_before_task_reopen = $true
        plugin_version = [string]$install.plugin.version
        target = [ordered]@{
            host_application = [string]$hostProfile.host_application
            package_family_name = [string]$hostProfile.package_family_name
            process_id = $TargetProcessId
            process_name = [string]$hostProfile.process_name
            executable_path_sha256 = $HostExecutableSha256.ToUpperInvariant()
            executable_path_stored = $false
            command_line_has_renderer_type = $false
            app_id = [string]$hostProfile.app_id
        }
        continuation = $oldPreparation.continuation
        hot_reload_claimed = $false
        process_stopped = $true
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-Json $preparationPath $preparation
    $taskUri = "codex://threads/$TaskId"
    $updatedBinding = [ordered]@{
        schema = "evidence-lane.codex-task-binding.v1"
        state = "EXACT_TASK_BINDING_PREPARED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        governed_host_session_id = $HostSessionId
        plugin_version = [string]$install.plugin.version
        task_uri_sha256 = Get-StringSha256 $taskUri
        preparation_receipt = $preparationPath
        preparation_receipt_sha256 = Get-Sha256 $preparationPath
        install_receipt = $installReceipt
        install_receipt_sha256 = $installReceiptSha256
        release_authority_receipt_sha256 = if ($null -eq $boundary.release_authority) { $null } else { Get-Sha256 $boundary.release_authority }
        restart_authority = $restartAuthority
        restart_authority_sha256 = $restartAuthoritySha256
        runtime_prewarm_receipt_sha256 = [string]$install.activation.runtime_prewarm.receipt_sha256
        runtime_ready_before_task_reopen = $true
        claim_scope = "EXACT_CODEX_THREAD_ID_ONLY"
        alias_claim_allowed = $true
        native_workspace_binding_source = "EXISTING_CODEX_TASK_STATE"
        native_workspace_binding_mutated = $false
        codex_native_changes_ui_mutated = $false
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-Json $boundary.binding_path $updatedBinding

    $installedPluginRoot = (Resolve-Path -LiteralPath ([string]$install.activation.plugin_add.installedPath)).Path
    $goalRecoveryScript = Join-Path $installedPluginRoot "scripts\codex_release\Manage-EvidenceLaneCodexGoalRecovery.ps1"
    if (-not (Test-Path -LiteralPath $goalRecoveryScript -PathType Leaf)) {
        throw "The installed canonical stable package is missing the general Goal recovery manager."
    }
    $installedReleaseChannel = Get-Content -LiteralPath (Join-Path $installedPluginRoot "scripts\codex-release-channel.json") -Raw | ConvertFrom-Json
    $stableRelease = [string]$installedReleaseChannel.stable.release
    if ($stableRelease -notmatch '^\d+\.\d+\.\d+$') {
        throw "The installed stable package does not expose one exact helper release identity."
    }
    $stableReleaseToken = "v" + ($stableRelease -replace '\.', '')
    $goalRecoveryRoot = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "installations\helpers\$stableReleaseToken\goal-recovery"
    $goalRecoveryTaskName = "Evidence Lane Codex Goal Recovery $stableReleaseToken"
    $twoSlotRegistry = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "installations\codex-v200\two-slot\CODEX_TWO_SLOT_REGISTRY.json"
    $goalRecoveryOutput = @(
        & powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass `
            -File $goalRecoveryScript `
            -Action Register `
            -TaskBindingReceipt $boundary.binding_path `
            -ActivePlanTaskId $ActivePlanTaskId `
            -Release $stableRelease `
            -RecoveryRoot $goalRecoveryRoot `
            -TwoSlotRegistry $twoSlotRegistry `
            -ScheduledTaskName $goalRecoveryTaskName 2>&1
    )
    if ($LASTEXITCODE -ne 0) {
        throw ("The general Goal recovery manager rebind failed: " + ($goalRecoveryOutput -join "`n"))
    }
    $goalRecovery = ($goalRecoveryOutput -join "`n") | ConvertFrom-Json
    if (
        $goalRecovery.status -cne "PASS" -or
        $goalRecovery.state -cne "ACTIVE_GOAL_REGISTERED_FOR_WINDOWS_LOGON_RECOVERY" -or
        $goalRecovery.task_id -cne $TaskId -or
        $goalRecovery.active_plan_task_id -cne $ActivePlanTaskId -or
        $goalRecovery.release -cne $stableRelease -or
        $goalRecovery.release_token -cne $stableReleaseToken -or
        $goalRecovery.helper_audience -cne "GOVERNED_CODEX_USER" -or
        $goalRecovery.host_app_id -cne [string]$hostProfile.app_id -or
        [string]$goalRecovery.task_binding_receipt_sha256 -cne (Get-Sha256 $boundary.binding_path)
    ) {
        throw "The installed general Goal recovery manager did not rebind this exact active task and host app."
    }

    $pluginList = & $boundary.codex plugin list --json | ConvertFrom-Json
    $evidencePlugins = @($pluginList.installed | Where-Object { $_.pluginId -like "evidence-lane-plugin@*" })
    $enabled = @($evidencePlugins | Where-Object { $_.enabled -eq $true })
    $fallbackUpdate = $install.two_slot_registry_update
    $fallbackSelector = $null
    $fallback = @()
    $acceptedStableSelector = $null
    $acceptedFallbackSelector = $null
    if ($UpdateMode -eq "GitStable") {
        $fallbackSelector = [string]$fallbackUpdate.fallback_selector
        $fallback = @($evidencePlugins | Where-Object { $_.pluginId -ceq $fallbackSelector })
        if (
            $fallbackUpdate.status -cne "PASS" -or
            $fallbackUpdate.fallback_selector_is_authority -ne $false -or
            [string]$fallbackUpdate.fallback_authority_sha256 -notmatch '^[A-F0-9]{64}$' -or
            [string]::IsNullOrWhiteSpace($fallbackSelector) -or
            $evidencePlugins.Count -ne 2 -or
            $enabled.Count -ne 1 -or
            $enabled[0].pluginId -cne [string]$install.activation.plugin_add.pluginId -or
            $fallback.Count -ne 1 -or
            $fallback[0].enabled -ne $false
        ) {
            throw "The post-update Codex plugin list is not exactly one stable plus one disabled fallback."
        }
    }
    else {
        $acceptedRegistry = Get-Content -LiteralPath $boundary.two_slot_path -Raw | ConvertFrom-Json
        $acceptedStableSelector = [string]$acceptedRegistry.slots.'stable-build'.plugin_selector
        $acceptedFallbackSelector = [string]$acceptedRegistry.slots.fallback.plugin_selector
        $expectedSelectors = @(
            $acceptedStableSelector,
            $acceptedFallbackSelector,
            $script:LocalTestingSelector,
            $script:LocalRecoverySelector
        ) | Sort-Object -Unique
        $observedSelectors = @($evidencePlugins.pluginId | Sort-Object -Unique)
        $primary = @($evidencePlugins | Where-Object { $_.pluginId -ceq $script:LocalTestingSelector })
        $recovery = @($evidencePlugins | Where-Object { $_.pluginId -ceq $script:LocalRecoverySelector })
        $acceptedStable = @($evidencePlugins | Where-Object { $_.pluginId -ceq $acceptedStableSelector })
        $acceptedFallback = @($evidencePlugins | Where-Object { $_.pluginId -ceq $acceptedFallbackSelector })
        if (
            (($observedSelectors -join "|") -cne ($expectedSelectors -join "|")) -or
            $enabled.Count -ne 1 -or
            $enabled[0].pluginId -cne $script:LocalTestingSelector -or
            $primary.Count -ne 1 -or
            $primary[0].enabled -ne $true -or
            $recovery.Count -ne 1 -or
            $recovery[0].enabled -ne $false -or
            $acceptedStable.Count -ne 1 -or
            $acceptedStable[0].enabled -ne $false -or
            $acceptedFallback.Count -ne 1 -or
            $acceptedFallback[0].enabled -ne $false -or
            $acceptedRegistry.tunnel_required -ne $false -or
            [int]$acceptedRegistry.max_active_tunnel_count -ne 0
        ) {
            throw "The post-recovery plugin list is not one active local 2.2 slot, one disabled byte-identical local recovery, and two untouched disabled accepted slots."
        }
        $fallbackSelector = $acceptedFallbackSelector
        $fallback = $acceptedFallback
    }
    $result = [ordered]@{
        schema = if ($UpdateMode -eq "GitStable") {
            "evidence-lane.codex-stable-same-slot-update-result.v1"
        }
        else {
            "evidence-lane.codex-local-disabled-hook-recovery-result.v1"
        }
        status = "PASS"
        state = if ($UpdateMode -eq "GitStable") {
            "SAME_STABLE_SELECTOR_UPDATED_EXACT_TASK_REOPEN_REQUESTED"
        }
        else {
            "LOCAL_2_2_REINSTALLED_EIGHT_HOOKS_TRUSTED_EXACT_TASK_REOPEN_REQUESTED"
        }
        update_mode = $UpdateMode
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        stable_selector = [string]$enabled[0].pluginId
        stable_selector_reused = [bool]$install.stable_selector_reused
        stable_selector_migrated_to_canonical_git = [bool]$install.stable_selector_migrated_to_canonical_git
        fallback_selector = [string]$fallback[0].pluginId
        fallback_selector_class = [string]$fallbackUpdate.fallback_selector_class
        fallback_selector_used_for_authorization = $false
        fallback_authority_sha256 = [string]$fallbackUpdate.fallback_authority_sha256
        fallback_enabled = $false
        local_primary_selector = if ($UpdateMode -eq "LocalDisabledHookRecovery") { $script:LocalTestingSelector } else { $null }
        local_recovery_selector = if ($UpdateMode -eq "LocalDisabledHookRecovery") { $script:LocalRecoverySelector } else { $null }
        local_recovery_registry = $localRecoveryRegistry
        local_recovery_registry_sha256 = $localRecoveryRegistrySha256
        local_recovery_byte_identical = if ($UpdateMode -eq "LocalDisabledHookRecovery") { $true } else { $null }
        pre_stop_successor_stage_receipt_sha256 = if ($UpdateMode -eq "LocalDisabledHookRecovery") { $SuccessorStageReceiptSha256.ToUpperInvariant() } else { $null }
        successor_cleanup = $successorCleanup
        accepted_stable_selector = $acceptedStableSelector
        accepted_fallback_selector = $acceptedFallbackSelector
        accepted_two_slot_registry_mutated = $false
        exact_hook_count = if ($UpdateMode -eq "LocalDisabledHookRecovery") { 8 } else { [int]$install.activation.hook_trust.hook_count }
        archive_sha256 = $archiveSha256
        install_receipt = $installReceipt
        install_receipt_sha256 = $installReceiptSha256
        release_authority_receipt_sha256 = if ($null -eq $boundary.release_authority) { $null } else { Get-Sha256 $boundary.release_authority }
        runtime_prewarm_receipt_sha256 = [string]$install.activation.runtime_prewarm.receipt_sha256
        runtime_ready_before_task_reopen = $true
        task_binding_receipt_sha256 = Get-Sha256 $boundary.binding_path
        new_stable_selector_created = $false
        post_proof_obsolete_cleanup_completed = $UpdateMode -eq "GitStable"
        host_application = [string]$hostProfile.host_application
        host_app_id = [string]$hostProfile.app_id
        host_stop = $hostStop
        host_transport_tunnel_stop = $tunnelStop
        host_transport_tunnel_rollover = $tunnelRollover
        host_transport_tunnel_restarted = $UpdateMode -eq "LocalDisabledHookRecovery"
        goal_recovery_manager_rebound = $true
        goal_recovery_release = $stableRelease
        goal_recovery_release_token = $stableReleaseToken
        goal_recovery_task_name = $goalRecoveryTaskName
        goal_recovery_helper_audience = "GOVERNED_CODEX_USER"
        maintainer_update_helper_audience = "EVIDENCE_LANE_MAINTAINER_ONLY"
        prior_versioned_helpers_retained_disabled = $true
        goal_recovery_binding_sha256 = [string]$goalRecovery.binding_sha256
        goal_recovery_task_binding_sha256 = [string]$goalRecovery.task_binding_receipt_sha256
        native_mcp_route = $true
        external_tunnel_required = $false
        candidate_created = $false
        pending_hil = $false
        pointer_moved = $false
        task_uri_sha256 = Get-StringSha256 $taskUri
        transient_scheduled_task_removed = $false
        completed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    if (-not [string]::IsNullOrWhiteSpace($ScheduledTaskName)) {
        Unregister-ScheduledTask -TaskName $ScheduledTaskName -Confirm:$false -ErrorAction Stop
        $result.transient_scheduled_task_removed = $true
    }
    $result.task_activation = Open-ExactTask $hostProfile
    Write-Json $resultPath $result
    exit 0
}
catch {
    $installationError = $_
    $transientTaskRemoved = $false
    $tunnelRecovery = $null
    if (-not [string]::IsNullOrWhiteSpace($ScheduledTaskName)) {
        try {
            Unregister-ScheduledTask -TaskName $ScheduledTaskName -Confirm:$false -ErrorAction Stop
            $transientTaskRemoved = $true
        }
        catch {}
    }
    if (
        $UpdateMode -eq "LocalDisabledHookRecovery" -and
        $null -ne $tunnelStop -and
        $null -eq $tunnelRollover
    ) {
        try {
            $recoveryManager = Join-Path $boundary.tunnel_runtime_root "Manage-EvidenceLaneTunnel.ps1"
            $recoveryOutput = @(
                & $powershell `
                    -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden `
                    -ExecutionPolicy Bypass -File $recoveryManager `
                    -Action Repair `
                    -RuntimeRoot $boundary.tunnel_runtime_root `
                    -ProfileName ([string]$boundary.tunnel_marker_data.profile_name) `
                    -ProfileDir (Split-Path -Parent ([string]$boundary.tunnel_marker_data.profile_file)) `
                    -ReleaseToken ([string]$boundary.tunnel_marker_data.release_token) `
                    -TaskName ([string]$boundary.tunnel_marker_data.task_name) 2>&1 |
                    ForEach-Object { [string]$_ }
            )
            $recoveryExitCode = $LASTEXITCODE
            $recoveryStatus = ($recoveryOutput -join "`n") | ConvertFrom-Json
            $tunnelRecovery = [ordered]@{
                attempted = $true
                status = if (
                    $recoveryExitCode -eq 0 -and
                    $recoveryStatus.control_plane_poll_ready -eq $true
                ) { "PASS" } else { "FAIL" }
                control_plane_poll_ready = [bool]$recoveryStatus.control_plane_poll_ready
                task_name = [string]$boundary.tunnel_marker_data.task_name
            }
        }
        catch {
            $tunnelRecovery = [ordered]@{
                attempted = $true
                status = "FAIL"
                error = $_.Exception.Message
                task_name = [string]$boundary.tunnel_marker_data.task_name
            }
        }
    }
    $taskActivation = $null
    $taskActivationError = $null
    if (-not [string]::IsNullOrWhiteSpace($HostAppId)) {
        try {
            $taskActivation = Open-ExactTask (Get-HostAppProfile $HostAppId)
        }
        catch {
            $taskActivationError = $_.Exception.Message
        }
    }
    $failure = [ordered]@{
        schema = if ($UpdateMode -eq "GitStable") {
            "evidence-lane.codex-stable-same-slot-update-failure.v1"
        }
        else {
            "evidence-lane.codex-local-disabled-hook-recovery-failure.v1"
        }
        status = "FAIL"
        update_mode = $UpdateMode
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        error_type = $installationError.Exception.GetType().Name
        error = $installationError.Exception.Message
        fallback_activated = $false
        transient_scheduled_task_removed = $transientTaskRemoved
        exact_task_reopen_requested = $null -ne $taskActivation
        task_activation = $taskActivation
        task_activation_error = $taskActivationError
        host_transport_tunnel_recovery = $tunnelRecovery
        operator_recovery_required = $null -eq $taskActivation
        candidate_created = $false
        pointer_moved = $false
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-Json $resultPath $failure
    exit 1
}
