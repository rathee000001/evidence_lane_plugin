[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Schedule", "Complete")]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$Archive,
    [Parameter(Mandatory = $true)]
    [string]$PackageReceipt,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseAuthorityReceipt,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseAuthorityReceiptSha256,
    [Parameter(Mandatory = $true)]
    [string]$BaselineInstallationReceipt,
    [Parameter(Mandatory = $true)]
    [string]$BaselineInstallationReceiptSha256,
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
$InstallerScript = if ([string]::IsNullOrWhiteSpace($InstallerScript)) {
    Join-Path $PSScriptRoot "install_codex_stable.py"
}
else {
    $InstallerScript
}
$script:Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$script:CanonicalStableSelector = "evidence-lane-plugin@evidence-lane-github"
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
    $exactReleaseAuthority = (Resolve-Path -LiteralPath $ReleaseAuthorityReceipt).Path
    $exactBaseline = (Resolve-Path -LiteralPath $BaselineInstallationReceipt).Path
    $exactInstaller = (Resolve-Path -LiteralPath $InstallerScript).Path
    $exactCodex = (Resolve-Path -LiteralPath $CodexExecutable).Path
    if ((Get-Sha256 $exactBaseline) -cne $BaselineInstallationReceiptSha256.ToUpperInvariant()) {
        throw "The baseline installation file seal drifted."
    }
    if ((Get-Sha256 $exactReleaseAuthority) -cne $ReleaseAuthorityReceiptSha256.ToUpperInvariant()) {
        throw "The governed Git/CI release-authority file seal drifted."
    }
    $releaseAuthority = Get-Content -LiteralPath $exactReleaseAuthority -Raw | ConvertFrom-Json
    $archiveSha256 = Get-Sha256 $exactArchive
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
    $bindingPath = Get-TaskBindingPath
    $binding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json
    $baseline = Get-Content -LiteralPath $exactBaseline -Raw | ConvertFrom-Json
    $stableMarketplaceName = [string]$baseline.activation.plugin_add.marketplaceName
    if (
        $baseline.schema -cne "evidence-lane.codex-stable-installation.v2" -or
        [string]::IsNullOrWhiteSpace($stableMarketplaceName) -or
        $stableMarketplaceName -eq "evidence-lane-pv11-fallback"
    ) {
        throw "The baseline installation does not identify the exact stable marketplace slot."
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
        throw "The exact task binding does not match this stable update boundary."
    }
    return [ordered]@{
        archive = $exactArchive
        package_receipt = $exactPackageReceipt
        release_authority = $exactReleaseAuthority
        baseline = $exactBaseline
        installer = $exactInstaller
        codex = $exactCodex
        stable_marketplace_name = $stableMarketplaceName
        release_authority_data = $releaseAuthority
        binding_path = $bindingPath
        binding = $binding
    }
}

function Open-ExactTask([object]$HostProfile) {
    $uri = "codex://threads/$TaskId"
    $activationProcessId = Invoke-ExactHostTaskActivation -HostProfile $HostProfile -TaskUri $uri
    return [ordered]@{
        task_uri = $uri
        host_app_id = [string]$HostProfile.app_id
        activation_request_process_id = $activationProcessId
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
        schema = "evidence-lane.codex-stable-same-slot-update-schedule.v1"
        status = "PASS"
        state = "SCHEDULED_BEFORE_EXACT_APP_RESTART"
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
        release_authority_receipt_sha256 = Get-Sha256 $boundary.release_authority
        baseline_installation_receipt_sha256 = Get-Sha256 $boundary.baseline
        stable_selector_reuse_after_canonical_migration_required = $true
        one_time_canonical_git_selector_migration_allowed = $true
        new_selector_allowed = $false
        fallback_activation_allowed = $false
        exact_task_reopen_required = $true
        scheduled_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $schedulePath = Join-Path $exactReceiptDirectory ("SCHEDULE_" + $scheduled.archive_sha256.Substring(0, 16) + ".json")
    $transientTaskName = "EvidenceLaneCodexStableUpdate-" + $scheduled.archive_sha256.Substring(0, 16)
    if ($null -ne (Get-ScheduledTask -TaskName $transientTaskName -ErrorAction SilentlyContinue)) {
        throw "The exact transient stable-update Scheduled Task already exists."
    }
    $scheduled.transient_scheduled_task_name = $transientTaskName
    Write-Json $schedulePath $scheduled

    $arguments = @(
        "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Action", "Complete",
        "-Archive", $boundary.archive,
        "-PackageReceipt", $boundary.package_receipt,
        "-ReleaseAuthorityReceipt", $boundary.release_authority,
        "-ReleaseAuthorityReceiptSha256", $ReleaseAuthorityReceiptSha256,
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
            -Description "One-use canonical Git stable-slot update and exact bound Codex task reopen." |
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
    Stop-Process -Id $TargetProcessId -Force
    exit 0
}

$resultPath = Join-Path $exactReceiptDirectory ("RESULT_" + (Get-Sha256 $boundary.archive).Substring(0, 16) + ".json")
try {
    $hostProfile = Get-HostAppProfile $HostAppId
    if ($HostExecutableSha256 -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "The exact target Codex executable SHA-256 was not carried into the completion action."
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
        "--release-authority-receipt", $boundary.release_authority,
        "--release-authority-receipt-sha256", $ReleaseAuthorityReceiptSha256,
        "--baseline-installation-receipt", $boundary.baseline,
        "--baseline-installation-receipt-sha256", $BaselineInstallationReceiptSha256,
        "--codex-home", ([IO.Path]::GetFullPath($CodexHome)),
        "--data-root", ([IO.Path]::GetFullPath($DataRoot)),
        "--codex-executable", $boundary.codex,
        "--activate", "--trust-sealed-hooks",
        "--hook-cwd", ([IO.Path]::GetFullPath($HookCwd))
    )
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
        throw ("The exact GitLane installer failed:`n" + $boundedOutput)
    }
    $install = ($rawOutput -join "`n") | ConvertFrom-Json
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
    $installReceipt = (Resolve-Path -LiteralPath ([string]$install.receipt_path)).Path
    $installReceiptSha256 = Get-Sha256 $installReceipt
    $archiveSha256 = Get-Sha256 $boundary.archive
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
        release_authority_receipt_sha256 = Get-Sha256 $boundary.release_authority
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
        release_authority_receipt_sha256 = Get-Sha256 $boundary.release_authority
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
    $goalRecoveryRoot = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "installations\codex-v200\goal-recovery"
    $twoSlotRegistry = Join-Path ([IO.Path]::GetFullPath($DataRoot)) "installations\codex-v200\two-slot\CODEX_TWO_SLOT_REGISTRY.json"
    $goalRecoveryOutput = @(
        & powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass `
            -File $goalRecoveryScript `
            -Action Register `
            -TaskBindingReceipt $boundary.binding_path `
            -ActivePlanTaskId $ActivePlanTaskId `
            -RecoveryRoot $goalRecoveryRoot `
            -TwoSlotRegistry $twoSlotRegistry 2>&1
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
        $goalRecovery.host_app_id -cne [string]$hostProfile.app_id
    ) {
        throw "The installed general Goal recovery manager did not rebind this exact active task and host app."
    }

    $pluginList = & $boundary.codex plugin list --json | ConvertFrom-Json
    $evidencePlugins = @($pluginList.installed | Where-Object { $_.pluginId -like "evidence-lane-plugin@*" })
    $enabled = @($evidencePlugins | Where-Object { $_.enabled -eq $true })
    $fallback = @($evidencePlugins | Where-Object { $_.pluginId -eq "evidence-lane-plugin@evidence-lane-pv11-fallback" })
    if (
        $evidencePlugins.Count -ne 2 -or
        $enabled.Count -ne 1 -or
        $enabled[0].pluginId -cne [string]$install.activation.plugin_add.pluginId -or
        $fallback.Count -ne 1 -or
        $fallback[0].enabled -ne $false
    ) {
        throw "The post-update Codex plugin list is not exactly one stable plus one disabled fallback."
    }
    $result = [ordered]@{
        schema = "evidence-lane.codex-stable-same-slot-update-result.v1"
        status = "PASS"
        state = "SAME_STABLE_SELECTOR_UPDATED_EXACT_TASK_REOPEN_REQUESTED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        stable_selector = [string]$enabled[0].pluginId
        stable_selector_reused = [bool]$install.stable_selector_reused
        stable_selector_migrated_to_canonical_git = [bool]$install.stable_selector_migrated_to_canonical_git
        fallback_selector = [string]$fallback[0].pluginId
        fallback_enabled = $false
        archive_sha256 = $archiveSha256
        install_receipt = $installReceipt
        install_receipt_sha256 = $installReceiptSha256
        release_authority_receipt_sha256 = Get-Sha256 $boundary.release_authority
        runtime_prewarm_receipt_sha256 = [string]$install.activation.runtime_prewarm.receipt_sha256
        runtime_ready_before_task_reopen = $true
        task_binding_receipt_sha256 = Get-Sha256 $boundary.binding_path
        new_stable_selector_created = $false
        post_proof_obsolete_cleanup_completed = $true
        host_application = [string]$hostProfile.host_application
        host_app_id = [string]$hostProfile.app_id
        goal_recovery_manager_rebound = $true
        goal_recovery_binding_sha256 = [string]$goalRecovery.binding_sha256
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
    if (-not [string]::IsNullOrWhiteSpace($ScheduledTaskName)) {
        try {
            Unregister-ScheduledTask -TaskName $ScheduledTaskName -Confirm:$false -ErrorAction Stop
            $transientTaskRemoved = $true
        }
        catch {}
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
        schema = "evidence-lane.codex-stable-same-slot-update-failure.v1"
        status = "FAIL"
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
        operator_recovery_required = $null -eq $taskActivation
        candidate_created = $false
        pointer_moved = $false
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-Json $resultPath $failure
    exit 1
}
