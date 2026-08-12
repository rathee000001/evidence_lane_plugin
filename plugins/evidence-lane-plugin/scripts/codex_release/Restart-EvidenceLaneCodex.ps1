[CmdletBinding()]
param(
    [ValidateSet("Prepare", "Restart", "Relaunch")]
    [string]$Action = "Prepare",
    [Parameter(Mandatory = $true)]
    [string]$InstallReceipt,
    [string]$InstallReceiptSha256,
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceSessionId,
    [Parameter(Mandatory = $true)]
    [string]$TaskId,
    [Parameter(Mandatory = $true)]
    [string]$HostSessionId,
    [int]$TargetProcessId,
    [string]$PreparationReceipt,
    [string]$PreparationReceiptSha256,
    [string]$ReceiptDirectory = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v200\restart",
    [ValidateSet(
        "OpenAI.Codex_2p2nqsd0c76g0!App",
        "OpenAI.CodexBeta_2p2nqsd0c76g0!App"
    )]
    [string]$AppId,
    [switch]$ConfirmRestart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:HostAppProfiles = [ordered]@{
    "OpenAI.Codex_2p2nqsd0c76g0!App" = [ordered]@{
        app_id = "OpenAI.Codex_2p2nqsd0c76g0!App"
        host_application = "CHATGPT_CODEX"
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
        package_name = "OpenAI.CodexBeta"
        package_family_name = "OpenAI.CodexBeta_2p2nqsd0c76g0"
        start_app_name = "ChatGPT (Beta)"
        process_name = "ChatGPT (Beta).exe"
        manifest_executable = "app/ChatGPT (Beta).exe"
        root_path_pattern = '\\WindowsApps\\OpenAI\.CodexBeta_[^\\]+\\app\\ChatGPT \(Beta\)\.exe$'
        package_path_pattern = '\\WindowsApps\\OpenAI\.CodexBeta_[^\\]+\\app\\'
    }
}

function Get-HostAppProfile([string]$ExactAppId) {
    if ([string]::IsNullOrWhiteSpace($ExactAppId) -or -not $script:HostAppProfiles.Contains($ExactAppId)) {
        throw "The exact Codex AppUserModelID is not in the approved stable/Beta allowlist."
    }
    return $script:HostAppProfiles[$ExactAppId]
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required sealed file is missing: $Path"
    }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToUpperInvariant()
}

function Get-StringSha256([string]$Value) {
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
        throw "The exact target Codex application registration is unavailable."
    }
    $packages = @(
        Get-AppxPackage -Name ([string]$HostProfile.package_name) |
            Where-Object {
                $_.PackageFamilyName -ceq [string]$HostProfile.package_family_name -and
                $_.Status -eq "Ok"
            }
    )
    if ($packages.Count -ne 1) {
        throw "The exact target Codex package is unavailable or unhealthy."
    }
    $manifestPath = Join-Path ([string]$packages[0].InstallLocation) "AppxManifest.xml"
    [xml]$manifest = Get-Content -LiteralPath $manifestPath -Raw
    $expectedApplications = @(
        $manifest.SelectNodes("//*[local-name()='Application']") |
            Where-Object {
                $_.GetAttribute("Id") -ceq "App" -and
                $_.GetAttribute("Executable") -ceq [string]$HostProfile.manifest_executable
            }
    )
    $codexProtocols = @(
        $manifest.SelectNodes("//*[local-name()='Protocol']") |
            Where-Object { $_.GetAttribute("Name") -ceq "codex" }
    )
    if ($expectedApplications.Count -ne 1 -or $codexProtocols.Count -lt 1) {
        throw "The target Codex package does not expose the expected app and Codex protocol."
    }
}

function Get-RootCodexProcess([int]$ProcessId, [object]$HostProfile) {
    $row = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $row) { throw "The exact target process does not exist." }
    $path = [string]$row.ExecutablePath
    $command = [string]$row.CommandLine
    if (
        $row.Name -cne [string]$HostProfile.process_name -or
        $command -match "--type=" -or
        $path -notmatch [string]$HostProfile.root_path_pattern
    ) {
        throw "Only the exact root process for the bound Codex host may be restarted."
    }
    return $row
}

function Resolve-RootCodexTarget([int]$ProcessId, [string]$ExpectedAppId = "") {
    $candidateProfiles = if ([string]::IsNullOrWhiteSpace($ExpectedAppId)) {
        @($script:HostAppProfiles.Values)
    }
    else {
        @(Get-HostAppProfile $ExpectedAppId)
    }
    $matches = @()
    foreach ($profile in $candidateProfiles) {
        try {
            $process = Get-RootCodexProcess -ProcessId $ProcessId -HostProfile $profile
            $matches += [ordered]@{ process = $process; profile = $profile }
        }
        catch {}
    }
    if ($matches.Count -ne 1) {
        throw "The exact target process does not resolve to one approved stable/Beta Codex host."
    }
    return $matches[0]
}

function Get-NewRootCodexProcess([int]$PriorProcessId, [object]$HostProfile) {
    $matches = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                [int]$_.ProcessId -ne $PriorProcessId -and
                [string]$_.Name -ceq [string]$HostProfile.process_name -and
                [string]$_.CommandLine -notmatch "--type=" -and
                [string]$_.ExecutablePath -match [string]$HostProfile.root_path_pattern
            }
    )
    if ($matches.Count -gt 1) {
        throw "More than one new root process for the bound Codex host was observed."
    }
    if ($matches.Count -eq 1) { return $matches[0] }
    return $null
}

function Get-CodexHostPackageProcesses([object]$HostProfile) {
    return @(
        Get-CimInstance Win32_Process |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace([string]$_.ExecutablePath) -and
                [string]$_.ExecutablePath -match [string]$HostProfile.package_path_pattern
            }
    )
}

function Invoke-CodexHostActivation([object]$HostProfile, [string]$Arguments) {
    if (-not ("EvidenceLaneCodexHostActivation" -as [type])) {
        $activationSource = @'
using System;
using System.Runtime.InteropServices;

public static class EvidenceLaneCodexHostActivation
{
    [Flags]
    private enum ActivateOptions : uint
    {
        None = 0,
        DesignMode = 0x1,
        NoErrorUI = 0x2,
        NoSplashScreen = 0x4
    }

    [ComImport]
    [Guid("2e941141-7f97-4756-ba1d-9decde894a3d")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IApplicationActivationManager
    {
        [PreserveSig]
        int ActivateApplication(
            [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
            [MarshalAs(UnmanagedType.LPWStr)] string arguments,
            ActivateOptions options,
            out uint processId);

        [PreserveSig]
        int ActivateForFile(IntPtr appUserModelId, IntPtr itemArray, IntPtr verb, out uint processId);

        [PreserveSig]
        int ActivateForProtocol(IntPtr appUserModelId, IntPtr itemArray, out uint processId);
    }

    public static uint Activate(string appUserModelId, string arguments)
    {
        Type managerType = Type.GetTypeFromCLSID(
            new Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C"));
        var manager = (IApplicationActivationManager)Activator.CreateInstance(managerType);
        uint processId;
        int result = manager.ActivateApplication(
            appUserModelId,
            arguments,
            ActivateOptions.NoErrorUI,
            out processId);
        Marshal.ThrowExceptionForHR(result);
        return processId;
    }
}
'@
        Add-Type -TypeDefinition $activationSource
    }
    try {
        return [uint32][EvidenceLaneCodexHostActivation]::Activate(
            [string]$HostProfile.app_id,
            $Arguments
        )
    }
    catch {
        throw "The exact bound Codex host activation failed: $($_.Exception.Message)"
    }
}

function Write-JsonReceipt([string]$Path, [System.Collections.IDictionary]$Body) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Path) + "." + [guid]::NewGuid().ToString("N"))
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText(
        $temporary,
        ($Body | ConvertTo-Json -Depth 12),
        $utf8NoBom
    )
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

$exactInstallReceipt = (Resolve-Path -LiteralPath $InstallReceipt).Path
$taskIdPattern = '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
if ($TaskId -notmatch $taskIdPattern) {
    throw "TaskId must be the exact Codex conversation identifier."
}
$taskUri = "codex://threads/$TaskId"
$taskUriSha256 = Get-StringSha256 $taskUri
$installationDirectory = Split-Path -Parent $exactInstallReceipt
$taskBindingRoot = $installationDirectory
if ((Split-Path -Leaf $installationDirectory) -eq "two-slot") {
    $taskBindingRoot = Split-Path -Parent $installationDirectory
}
$taskBindingDirectory = Join-Path $taskBindingRoot "task-bindings"
$taskBindingPath = Join-Path $taskBindingDirectory ($TaskId.ToLowerInvariant() + ".json")
$observedInstallSha = Get-Sha256 $exactInstallReceipt
if ($InstallReceiptSha256 -and $observedInstallSha -ne $InstallReceiptSha256.ToUpperInvariant()) {
    throw "The exact install receipt SHA-256 does not match."
}
$install = Get-Content -LiteralPath $exactInstallReceipt -Raw | ConvertFrom-Json
$installedPluginVersion = [string]$install.plugin.version
if (
    $install.schema -ne "evidence-lane.codex-stable-installation.v2" -or
    $install.status -ne "PASS" -or
    $installedPluginVersion -notmatch '^\d+\.\d+\.\d+\+codex\.[0-9A-Za-z.-]+$' -or
    [string]$install.activation.plugin_add.version -ne $installedPluginVersion -or
    $install.activation.state -ne "INSTALLED_RESTART_REQUIRED" -or
    $install.candidate_created_or_accepted -ne $false -or
    $install.pointer_moved -ne $false
) {
    throw "The supplied v2 installation receipt is not restart-eligible."
}

if ($Action -eq "Prepare") {
    if ($TargetProcessId -le 0) { throw "Prepare requires -TargetProcessId." }
    $target = Resolve-RootCodexTarget -ProcessId $TargetProcessId -ExpectedAppId $AppId
    $process = $target.process
    $hostProfile = $target.profile
    $AppId = [string]$hostProfile.app_id
    Assert-CodexThreadProtocol $hostProfile
    $receiptPath = Join-Path $ReceiptDirectory "CODEX_RESTART_PREPARATION.json"
    $body = [ordered]@{
        schema = "evidence-lane.codex-restart-preparation.v2"
        state = "PREPARED_NOT_RESTARTED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        install_receipt = $exactInstallReceipt
        install_receipt_sha256 = $observedInstallSha
        plugin_version = [string]$install.plugin.version
        target = [ordered]@{
            host_application = [string]$hostProfile.host_application
            package_family_name = [string]$hostProfile.package_family_name
            process_id = [int]$process.ProcessId
            process_name = [string]$process.Name
            executable_path = [string]$process.ExecutablePath
            command_line_has_renderer_type = $false
            app_id = [string]$hostProfile.app_id
        }
        continuation = [ordered]@{
            same_task_required = $true
            task_2_used = $false
            user_reentry_action = "NONE_AUTO_OPEN_EXACT_TASK"
            task_navigation_mode = "CODEX_THREAD_DEEPLINK"
            task_uri_sha256 = $taskUriSha256
            coordinate_clicking_used = $false
            native_workspace_binding_source = "EXISTING_CODEX_TASK_STATE"
            native_workspace_binding_mutated = $false
            codex_native_changes_ui_owned_by_host = $true
            goal_resumes_from_persistent_task_and_change_display = $true
            lifecycle_resume_call_required = $false
            state_travel_required = $false
        }
        hot_reload_claimed = $false
        process_stopped = $false
        source_mutated = $false
        candidate_created_or_accepted = $false
        pointer_moved = $false
        hil_inferred = $false
        prepared_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-JsonReceipt $receiptPath $body
    $preparationReceiptSha256 = Get-Sha256 $receiptPath
    $taskBinding = [ordered]@{
        schema = "evidence-lane.codex-task-binding.v1"
        state = "EXACT_TASK_BINDING_PREPARED"
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        governed_host_session_id = $HostSessionId
        plugin_version = [string]$install.plugin.version
        task_uri_sha256 = $taskUriSha256
        preparation_receipt = $receiptPath
        preparation_receipt_sha256 = $preparationReceiptSha256
        install_receipt = $exactInstallReceipt
        install_receipt_sha256 = $observedInstallSha
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
    Write-JsonReceipt $taskBindingPath $taskBinding
    [ordered]@{
        status = "PASS"
        state = "PREPARED_NOT_RESTARTED"
        receipt_path = $receiptPath
        receipt_sha256 = $preparationReceiptSha256
        task_binding_receipt_path = $taskBindingPath
        task_binding_receipt_sha256 = Get-Sha256 $taskBindingPath
        next_action = "RUN_RESTART_WITH_EXACT_RECEIPT_SHA_AND_CONFIRMRESTART"
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($Action -eq "Restart") {
    if (-not $ConfirmRestart) { throw "Restart requires -ConfirmRestart." }
    if (-not $PreparationReceipt -or -not $PreparationReceiptSha256) {
        throw "Restart requires the exact preparation receipt path and SHA-256."
    }
    $exactPreparation = (Resolve-Path -LiteralPath $PreparationReceipt).Path
    if ((Get-Sha256 $exactPreparation) -ne $PreparationReceiptSha256.ToUpperInvariant()) {
        throw "The preparation receipt SHA-256 does not match."
    }
    $prepared = Get-Content -LiteralPath $exactPreparation -Raw | ConvertFrom-Json
    $hostProfile = Get-HostAppProfile $AppId
    if (-not (Test-Path -LiteralPath $taskBindingPath -PathType Leaf)) {
        throw "The exact Codex task binding receipt is missing."
    }
    $taskBinding = Get-Content -LiteralPath $taskBindingPath -Raw | ConvertFrom-Json
    if (
        $prepared.schema -ne "evidence-lane.codex-restart-preparation.v2" -or
        $prepared.state -ne "PREPARED_NOT_RESTARTED" -or
        $prepared.project_id -ne $ProjectId -or
        $prepared.evidence_session_id -ne $EvidenceSessionId -or
        $prepared.task_id -ne $TaskId -or
        $prepared.host_session_id -ne $HostSessionId -or
        $prepared.install_receipt_sha256 -ne $observedInstallSha -or
        $prepared.target.host_application -ne [string]$hostProfile.host_application -or
        $prepared.target.package_family_name -ne [string]$hostProfile.package_family_name -or
        $prepared.target.process_name -ne [string]$hostProfile.process_name -or
        $prepared.target.app_id -ne $AppId -or
        [int]$prepared.target.process_id -ne $TargetProcessId -or
        $taskBinding.schema -ne "evidence-lane.codex-task-binding.v1" -or
        $taskBinding.state -ne "EXACT_TASK_BINDING_PREPARED" -or
        $taskBinding.project_id -ne $ProjectId -or
        $taskBinding.evidence_session_id -ne $EvidenceSessionId -or
        $taskBinding.task_id -ne $TaskId -or
        $taskBinding.governed_host_session_id -ne $HostSessionId -or
        $taskBinding.task_uri_sha256 -ne $taskUriSha256 -or
        $taskBinding.preparation_receipt_sha256 -ne $PreparationReceiptSha256.ToUpperInvariant() -or
        $taskBinding.install_receipt_sha256 -ne $observedInstallSha -or
        $taskBinding.alias_claim_allowed -ne $true
    ) {
        throw "The preparation receipt does not bind this exact task and process."
    }
    $process = Get-RootCodexProcess -ProcessId $TargetProcessId -HostProfile $hostProfile
    if ([string]$process.ExecutablePath -ne [string]$prepared.target.executable_path) {
        throw "The exact target executable changed after preparation."
    }
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Action", "Relaunch",
        "-InstallReceipt", $exactInstallReceipt,
        "-InstallReceiptSha256", $observedInstallSha,
        "-ProjectId", $ProjectId,
        "-EvidenceSessionId", $EvidenceSessionId,
        "-TaskId", $TaskId,
        "-HostSessionId", $HostSessionId,
        "-TargetProcessId", [string]$TargetProcessId,
        "-PreparationReceipt", $exactPreparation,
        "-PreparationReceiptSha256", $PreparationReceiptSha256,
        "-ReceiptDirectory", $ReceiptDirectory,
        "-AppId", $AppId
    )
    $argumentLine = ($arguments | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument ([string]$_)
    }) -join " "
    Start-Process -FilePath $powershell -ArgumentList $argumentLine -WindowStyle Hidden | Out-Null
    Stop-Process -Id $TargetProcessId -Force
    exit 0
}

if ($Action -eq "Relaunch") {
    $relaunchPath = Join-Path $ReceiptDirectory "CODEX_RELAUNCH_RECEIPT.json"
    $failurePath = Join-Path $ReceiptDirectory "CODEX_RELAUNCH_FAILURE.json"
    try {
        if (-not $PreparationReceipt -or -not $PreparationReceiptSha256) {
            throw "Internal relaunch requires the sealed preparation receipt."
        }
        if ((Get-Sha256 $PreparationReceipt) -ne $PreparationReceiptSha256.ToUpperInvariant()) {
            throw "Internal relaunch receipt mismatch."
        }
        $prepared = Get-Content -LiteralPath $PreparationReceipt -Raw | ConvertFrom-Json
        $hostProfile = Get-HostAppProfile $AppId
        if (-not (Test-Path -LiteralPath $taskBindingPath -PathType Leaf)) {
            throw "The exact Codex task binding receipt is missing."
        }
        $taskBinding = Get-Content -LiteralPath $taskBindingPath -Raw | ConvertFrom-Json
        if (
            $prepared.schema -ne "evidence-lane.codex-restart-preparation.v2" -or
            $prepared.state -ne "PREPARED_NOT_RESTARTED" -or
            $prepared.project_id -ne $ProjectId -or
            $prepared.evidence_session_id -ne $EvidenceSessionId -or
            $prepared.task_id -ne $TaskId -or
            $prepared.host_session_id -ne $HostSessionId -or
            $prepared.install_receipt_sha256 -ne $observedInstallSha -or
            $prepared.continuation.task_uri_sha256 -ne $taskUriSha256 -or
            $prepared.target.host_application -ne [string]$hostProfile.host_application -or
            $prepared.target.package_family_name -ne [string]$hostProfile.package_family_name -or
            $prepared.target.process_name -ne [string]$hostProfile.process_name -or
            $prepared.target.app_id -ne $AppId -or
            [int]$prepared.target.process_id -ne $TargetProcessId -or
            $taskBinding.schema -ne "evidence-lane.codex-task-binding.v1" -or
            $taskBinding.state -ne "EXACT_TASK_BINDING_PREPARED" -or
            $taskBinding.project_id -ne $ProjectId -or
            $taskBinding.evidence_session_id -ne $EvidenceSessionId -or
            $taskBinding.task_id -ne $TaskId -or
            $taskBinding.governed_host_session_id -ne $HostSessionId -or
            $taskBinding.task_uri_sha256 -ne $taskUriSha256 -or
            $taskBinding.preparation_receipt_sha256 -ne $PreparationReceiptSha256.ToUpperInvariant() -or
            $taskBinding.install_receipt_sha256 -ne $observedInstallSha -or
            $taskBinding.alias_claim_allowed -ne $true
        ) {
            throw "The relaunch request does not bind the exact prepared task and process."
        }
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
        while (Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue) {
            if ([DateTimeOffset]::UtcNow -ge $deadline) {
                throw "The exact Codex root process did not stop within 60 seconds."
            }
            Start-Sleep -Milliseconds 250
        }
        $treeDeadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
        while (@(Get-CodexHostPackageProcesses $hostProfile).Count -gt 0) {
            if ([DateTimeOffset]::UtcNow -ge $treeDeadline) {
                throw "The exact bound Codex host process tree did not stop within 60 seconds."
            }
            Start-Sleep -Milliseconds 250
        }
        Assert-CodexThreadProtocol $hostProfile
        $launchRequestProcessId = Invoke-CodexHostActivation -HostProfile $hostProfile -Arguments $taskUri
        $newRoot = $null
        $launchDeadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
        while ($null -eq $newRoot) {
            if ([DateTimeOffset]::UtcNow -ge $launchDeadline) {
                throw "No new root process for the bound Codex host appeared after exact AppUserModelID activation."
            }
            Start-Sleep -Milliseconds 250
            $newRoot = Get-NewRootCodexProcess -PriorProcessId $TargetProcessId -HostProfile $hostProfile
        }
        $verifiedRoot = Get-RootCodexProcess -ProcessId ([int]$newRoot.ProcessId) -HostProfile $hostProfile
        $navigationRequestProcessId = Invoke-CodexHostActivation -HostProfile $hostProfile -Arguments $taskUri
        Start-Sleep -Milliseconds 500
        [void](Get-RootCodexProcess -ProcessId ([int]$verifiedRoot.ProcessId) -HostProfile $hostProfile)
        Write-JsonReceipt $relaunchPath ([ordered]@{
            schema = "evidence-lane.codex-relaunch-receipt.v2"
            state = "BOUND_CODEX_HOST_ROOT_RELAUNCHED_EXACT_TASK_REQUESTED_AWAITING_NATIVE_PROOF"
            project_id = $ProjectId
            evidence_session_id = $EvidenceSessionId
            task_id = $TaskId
            prior_host_session_id = $HostSessionId
            preparation_receipt_sha256 = $PreparationReceiptSha256.ToUpperInvariant()
            task_binding_receipt_sha256 = Get-Sha256 $taskBindingPath
            install_receipt_sha256 = $observedInstallSha
            host_application = [string]$hostProfile.host_application
            package_family_name = [string]$hostProfile.package_family_name
            app_id = [string]$hostProfile.app_id
            prior_bound_host_process_tree_fully_stopped = $true
            task_navigation = [ordered]@{
                mode = "EXACT_BOUND_APPUSERMODELID_WITH_CODEX_THREAD_ARGUMENT"
                task_uri_sha256 = $taskUriSha256
                coordinate_clicking_used = $false
                launch_request_process_id = [uint32]$launchRequestProcessId
                navigation_request_process_id = [uint32]$navigationRequestProcessId
                new_root_process_id = [int]$verifiedRoot.ProcessId
                new_root_process_name = [string]$verifiedRoot.Name
                new_root_executable_path = [string]$verifiedRoot.ExecutablePath
                request_observed = $true
                user_opened_host_manually = $false
                task_2_used = $false
                active_task_ui_independently_proven = $false
                native_local_workspace_and_changes_proof_pending = $true
                codex_native_changes_ui_mutated = $false
                native_catalog_and_project_session_proof_pending = $true
            }
            source_mutated = $false
            candidate_created_or_accepted = $false
            pointer_moved = $false
            hil_inferred = $false
            requested_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        })
    }
    catch {
        Write-JsonReceipt $failurePath ([ordered]@{
            schema = "evidence-lane.codex-relaunch-failure.v1"
            state = "BOUND_CODEX_HOST_RELAUNCH_FAILED"
            project_id = $ProjectId
            evidence_session_id = $EvidenceSessionId
            task_id = $TaskId
            prior_host_session_id = $HostSessionId
            app_id = $AppId
            failure = $_.Exception.Message
            operator_recovery_required = $true
            manual_open_can_satisfy_helper_success = $false
            native_proof_claimed = $false
            candidate_created_or_accepted = $false
            pointer_moved = $false
            hil_inferred = $false
            failed_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        })
        throw
    }
    exit 0
}
