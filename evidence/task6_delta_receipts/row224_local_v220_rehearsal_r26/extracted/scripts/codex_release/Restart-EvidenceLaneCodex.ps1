[CmdletBinding()]
param(
    [ValidateSet("Prepare", "Restart", "Relaunch")]
    [string]$Action = "Prepare",
    [Parameter(Mandatory = $true)]
    [string]$InstallReceipt,
    [string]$InstallReceiptSha256,
    [string]$LocalTestCommitReceipt,
    [string]$LocalTestCommitReceiptSha256,
    [string]$CodexConfig,
    [string]$LocalRecoveryRegistry,
    [string]$LocalRecoveryRegistrySha256,
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$EvidenceSessionId,
    [Parameter(Mandatory = $true)]
    [string]$TaskId,
    [Parameter(Mandatory = $true)]
    [string]$HostSessionId,
    [string]$ActivePlanTaskId,
    [int]$TargetProcessId,
    [string]$PreparationReceipt,
    [string]$PreparationReceiptSha256,
    [string]$ReceiptDirectory = "$env:USERPROFILE\EvidenceLanePV\installations\codex-v200\restart",
    [string]$DataRoot = "$env:USERPROFILE\EvidenceLanePV",
    [string]$RestartLeasePath,
    [string]$RestartLeaseToken,
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

function Write-NewJsonLease([string]$Path, [System.Collections.IDictionary]$Body) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $payload = $Body | ConvertTo-Json -Depth 12
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($payload)
    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}

function Get-InstalledPluginRoot([object]$Install) {
    $pluginAdd = $Install.activation.PSObject.Properties["plugin_add"]
    $installedPath = $Install.activation.PSObject.Properties["installed_path"]
    $candidate = if ($null -ne $pluginAdd) {
        [string]$pluginAdd.Value.installedPath
    }
    elseif ($null -ne $installedPath) {
        [string]$installedPath.Value
    }
    else {
        ""
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw "The completed installation receipt has no exact installed plugin root."
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}

function Get-VersionMatchedTunnelBoundary {
    param(
        [Parameter(Mandatory = $true)][string]$ExactPluginRoot,
        [Parameter(Mandatory = $true)][string]$PluginVersion,
        [Parameter(Mandatory = $true)][string]$ExactDataRoot
    )

    $release = $PluginVersion.Split("+", 2)[0]
    if ($release -notmatch '^\d+\.\d+\.\d+$') {
        throw "The installed plugin version cannot bind a versioned tunnel."
    }
    $releaseToken = "v" + ($release -replace '\.', '')
    $runtimeRoot = [IO.Path]::GetFullPath(
        (Join-Path $ExactDataRoot "tunnel-runtime-$releaseToken-stable-build")
    )
    $markerPath = Join-Path $runtimeRoot "evidence-lane-tunnel-installation.json"
    $runtimeManager = Join-Path $runtimeRoot "Manage-EvidenceLaneTunnel.ps1"
    $runtimeHost = Join-Path $runtimeRoot "EvidenceLaneTunnelHost.exe"
    $sourceManager = Join-Path $ExactPluginRoot "scripts\windows_tunnel\Manage-EvidenceLaneTunnel.ps1"
    $sourceHost = Join-Path $ExactPluginRoot "scripts\windows_tunnel\EvidenceLaneTunnelHost.exe"
    foreach ($required in @($markerPath, $runtimeManager, $runtimeHost, $sourceManager, $sourceHost)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "The already-installed version-matched tunnel boundary is incomplete: $required"
        }
    }
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    $task = Get-ScheduledTask -TaskName ([string]$marker.task_name) -ErrorAction SilentlyContinue
    $managerSha = Get-Sha256 $runtimeManager
    $sourceManagerSha = Get-Sha256 $sourceManager
    $hostSha = Get-Sha256 $runtimeHost
    $sourceHostSha = Get-Sha256 $sourceHost
    if (
        $marker.schema -cne "evidence-lane.versioned-secure-mcp-tunnel-installation.v1" -or
        [string]$marker.release -cne $release -or
        [string]$marker.release_token -cne $releaseToken -or
        [string]$marker.slot_role -cnotin @(
            "main-git-release",
            "branch-commit-recovery",
            "mutable-local-testing"
        ) -or
        [IO.Path]::GetFullPath([string]$marker.plugin_root) -cne [IO.Path]::GetFullPath($ExactPluginRoot) -or
        [IO.Path]::GetFullPath([string]$marker.runtime_root) -cne $runtimeRoot -or
        [string]$marker.windows_console_policy -cne "WINDOWS_GUI_HOST_CREATE_NO_WINDOW" -or
        [string]$marker.scheduled_task_window_style -cne "HIDDEN" -or
        $managerSha -cne $sourceManagerSha -or
        $hostSha -cne $sourceHostSha -or
        [string]$marker.scheduled_task_launcher_sha256 -cne $hostSha -or
        $null -eq $task -or
        [string]$task.State -ceq "Running"
    ) {
        throw "The staged tunnel does not match the already-installed plugin or is already running."
    }
    return [ordered]@{
        schema = "evidence-lane.codex-version-matched-tunnel-boundary.v1"
        release = $release
        release_token = $releaseToken
        plugin_root = [IO.Path]::GetFullPath($ExactPluginRoot)
        runtime_root = $runtimeRoot
        marker_path = $markerPath
        marker_sha256 = Get-Sha256 $markerPath
        manager_path = $runtimeManager
        manager_sha256 = $managerSha
        host_path = $runtimeHost
        host_sha256 = $hostSha
        task_name = [string]$marker.task_name
        profile_name = [string]$marker.profile_name
        profile_dir = Split-Path -Parent ([string]$marker.profile_file)
        stable_client = [string]$marker.stable_client
        stable_client_sha256 = [string]$marker.stable_client_sha256
        pid_file = [string]$marker.pid_file
        health_url_file = [string]$marker.health_url_file
        staged_not_started = $true
        helper_installs_plugin = $false
    }
}

function Start-VersionMatchedTunnel([System.Collections.IDictionary]$Boundary) {
    $task = Get-ScheduledTask -TaskName ([string]$Boundary.task_name) -ErrorAction Stop
    if ([string]$task.State -ceq "Running") {
        throw "The version-matched tunnel was already running before the one restart helper started it."
    }
    Enable-ScheduledTask -TaskName ([string]$Boundary.task_name) -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName ([string]$Boundary.task_name) -ErrorAction Stop
    return [ordered]@{
        start_requested = $true
        task_name = [string]$Boundary.task_name
        requested_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Wait-VersionMatchedTunnelReady([System.Collections.IDictionary]$Boundary) {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(45)
    do {
        $task = Get-ScheduledTask -TaskName ([string]$Boundary.task_name) -ErrorAction SilentlyContinue
        $pidValue = 0
        $pidValid = (
            (Test-Path -LiteralPath ([string]$Boundary.pid_file) -PathType Leaf) -and
            [int]::TryParse(
                (Get-Content -LiteralPath ([string]$Boundary.pid_file) -Raw).Trim(),
                [ref]$pidValue
            )
        )
        $process = if ($pidValid) { Get-Process -Id $pidValue -ErrorAction SilentlyContinue } else { $null }
        $clientHashValid = (
            (Test-Path -LiteralPath ([string]$Boundary.stable_client) -PathType Leaf) -and
            (Get-Sha256 ([string]$Boundary.stable_client)) -cne "" -and
            (Get-Sha256 ([string]$Boundary.stable_client)) -ceq ([string]$Boundary.stable_client_sha256).ToUpperInvariant()
        )
        if (
            $null -ne $task -and
            [string]$task.State -ceq "Running" -and
            $null -ne $process -and
            $clientHashValid -and
            (Test-Path -LiteralPath ([string]$Boundary.health_url_file) -PathType Leaf)
        ) {
            & ([string]$Boundary.stable_client) health `
                --url-file ([string]$Boundary.health_url_file) `
                --pid $pidValue `
                --require-control-plane-poll `
                --json *> $null
            if ($LASTEXITCODE -eq 0) {
                return [ordered]@{
                    status = "PASS"
                    task_name = [string]$Boundary.task_name
                    task_state = [string]$task.State
                    process_id = $pidValue
                    control_plane_poll_ready = $true
                    fixed_delay_used = $false
                }
            }
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "The version-matched tunnel did not reach control-plane readiness after the app restart boundary."
}

function Set-ExactHostWindowMaximized([int]$RootProcessId, [object]$HostProfile) {
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
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    do {
        $windowProcesses = @(
            Get-CodexHostPackageProcesses $HostProfile |
                ForEach-Object { Get-Process -Id ([int]$_.ProcessId) -ErrorAction SilentlyContinue } |
                Where-Object { $null -ne $_ -and $_.MainWindowHandle -ne 0 }
        )
        $window = @($windowProcesses | Where-Object { $_.Id -eq $RootProcessId }) | Select-Object -First 1
        if ($null -eq $window) {
            $window = $windowProcesses | Select-Object -First 1
        }
        if ($null -ne $window) {
            $handle = [IntPtr]$window.MainWindowHandle
            [void][EvidenceLaneExactHostWindow]::ShowWindowAsync($handle, 3)
            [void][EvidenceLaneExactHostWindow]::SetForegroundWindow($handle)
            if ([EvidenceLaneExactHostWindow]::IsZoomed($handle)) {
                return [ordered]@{
                    status = "PASS"
                    window_process_id = [int]$window.Id
                    window_state = "MAXIMIZED_FULL_WINDOW"
                    maximized_verified = $true
                }
            }
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "The exact reopened Codex host did not reach its maximized full-window state."
}

function Remove-ExactRestartLease([string]$Path, [string]$Token) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    $lease = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ([string]$lease.token -cne $Token) {
        throw "Refusing to release another restart invocation's single-flight lease."
    }
    Remove-Item -LiteralPath $Path -Force
}

function Sync-GoalRecoveryBindingAfterTaskBinding {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExactTaskBindingPath,
        [Parameter(Mandatory = $true)]
        [string]$ExactTaskBindingSha256
    )

    $goalRecoveryScript = Join-Path $PSScriptRoot "Manage-EvidenceLaneCodexGoalRecovery.ps1"
    $releaseChannelPath = Join-Path (Split-Path -Parent $PSScriptRoot) "codex-release-channel.json"
    if (
        -not (Test-Path -LiteralPath $goalRecoveryScript -PathType Leaf) -or
        -not (Test-Path -LiteralPath $releaseChannelPath -PathType Leaf)
    ) {
        throw "The installed restart helper is missing its version-matched Goal recovery authority."
    }
    $releaseChannel = Get-Content -LiteralPath $releaseChannelPath -Raw | ConvertFrom-Json
    $release = [string]$releaseChannel.stable.release
    if ($release -notmatch '^\d+\.\d+\.\d+$') {
        throw "The installed restart helper has no exact Goal recovery release identity."
    }
    $releaseToken = "v" + ($release -replace '\.', '')
    $installationRoot = Split-Path -Parent $taskBindingRoot
    $dataRoot = Split-Path -Parent $installationRoot
    $goalRecoveryRoot = Join-Path $dataRoot "installations\helpers\$releaseToken\goal-recovery"
    $goalRecoveryTaskName = "Evidence Lane Codex Goal Recovery $releaseToken"
    $twoSlotRegistry = Join-Path $taskBindingRoot "two-slot\CODEX_TWO_SLOT_REGISTRY.json"
    $goalBindingPath = Join-Path (Join-Path $goalRecoveryRoot "bindings") ($TaskId.ToLowerInvariant() + ".json")

    $exactActivePlanTaskId = [string]$ActivePlanTaskId
    if ([string]::IsNullOrWhiteSpace($exactActivePlanTaskId) -and (Test-Path -LiteralPath $goalBindingPath -PathType Leaf)) {
        $priorGoalBinding = Get-Content -LiteralPath $goalBindingPath -Raw | ConvertFrom-Json
        if (
            $priorGoalBinding.schema -ne "evidence-lane.codex-goal-recovery-binding.v1" -or
            [string]$priorGoalBinding.payload.task_id -ne $TaskId -or
            [string]$priorGoalBinding.payload.project_id -ne $ProjectId -or
            [string]$priorGoalBinding.payload.evidence_session_id -ne $EvidenceSessionId
        ) {
            throw "The existing exact-task Goal binding cannot supply this restart's active Plan row."
        }
        if ([string]$priorGoalBinding.payload.state -eq "ACTIVE_GOAL_BOUND") {
            $exactActivePlanTaskId = [string]$priorGoalBinding.payload.active_plan_task_id
        }
    }
    if ([string]::IsNullOrWhiteSpace($exactActivePlanTaskId)) {
        return [ordered]@{
            status = "PASS"
            state = "NO_EXISTING_ACTIVE_GOAL_BINDING_TO_REFRESH"
            task_id = $TaskId
            active_plan_task_id = $null
            task_binding_receipt_sha256 = $ExactTaskBindingSha256
            goal_binding_mutated = $false
        }
    }

    $goalRecoveryOutput = @(
        & powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass `
            -File $goalRecoveryScript `
            -Action Register `
            -TaskBindingReceipt $ExactTaskBindingPath `
            -ActivePlanTaskId $exactActivePlanTaskId `
            -Release $release `
            -RecoveryRoot $goalRecoveryRoot `
            -TwoSlotRegistry $twoSlotRegistry `
            -ScheduledTaskName $goalRecoveryTaskName 2>&1
    )
    if ($LASTEXITCODE -ne 0) {
        throw ("The exact-task Goal recovery binding refresh failed after task binding: " + ($goalRecoveryOutput -join "`n"))
    }
    $goalRecovery = ($goalRecoveryOutput -join "`n") | ConvertFrom-Json
    if (
        $goalRecovery.status -cne "PASS" -or
        $goalRecovery.state -cne "ACTIVE_GOAL_REGISTERED_FOR_WINDOWS_LOGON_RECOVERY" -or
        $goalRecovery.task_id -cne $TaskId -or
        $goalRecovery.active_plan_task_id -cne $exactActivePlanTaskId -or
        $goalRecovery.release -cne $release -or
        $goalRecovery.release_token -cne $releaseToken -or
        $goalRecovery.task_binding_receipt_sha256 -cne $ExactTaskBindingSha256
    ) {
        throw "The Goal recovery manager did not seal the newly written exact task binding."
    }
    return [ordered]@{
        status = "PASS"
        state = "ACTIVE_GOAL_REFRESHED_AFTER_EXACT_TASK_BINDING"
        task_id = $TaskId
        active_plan_task_id = $exactActivePlanTaskId
        task_binding_receipt_sha256 = $ExactTaskBindingSha256
        binding_path = [string]$goalRecovery.binding_path
        binding_sha256 = [string]$goalRecovery.binding_sha256
        binding_revision = [int]$goalRecovery.binding_revision
        binding_event_receipt = [string]$goalRecovery.binding_event_receipt
        goal_binding_mutated = $true
    }
}

function Get-EvidenceLaneConfigState([string]$Path) {
    $states = @{}
    $currentSelector = $null
    $currentKind = $null
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[plugins\."(?<selector>evidence-lane-plugin@[^\"]+)"\]$') {
            $currentSelector = [string]$Matches.selector
            $currentKind = "plugin"
        }
        elseif ($trimmed -match '^\[plugins\."(?<selector>evidence-lane-plugin@[^\"]+)"\.mcp_servers\.[^\]]+\]$') {
            $currentSelector = [string]$Matches.selector
            $currentKind = "mcp"
        }
        elseif ($trimmed -match '^\[') {
            $currentSelector = $null
            $currentKind = $null
        }
        elseif (
            $null -ne $currentSelector -and
            $trimmed -match '^enabled\s*=\s*(?<value>true|false)\s*(?:#.*)?$'
        ) {
            if (-not $states.ContainsKey($currentSelector)) {
                $states[$currentSelector] = [ordered]@{
                    plugin_values = @()
                    mcp_values = @()
                }
            }
            $value = [string]$Matches.value -ceq "true"
            if ($currentKind -eq "plugin") {
                $states[$currentSelector].plugin_values = @(
                    $states[$currentSelector].plugin_values
                ) + $value
            }
            elseif ($currentKind -eq "mcp") {
                $states[$currentSelector].mcp_values = @(
                    $states[$currentSelector].mcp_values
                ) + $value
            }
        }
    }

    $records = @(
        foreach ($selector in @($states.Keys | Sort-Object)) {
            $row = $states[$selector]
            if (
                @($row.plugin_values).Count -ne 1 -or
                @($row.mcp_values).Count -ne 1
            ) {
                throw "Every Evidence Lane selector must expose one plugin bit and one MCP bit."
            }
            [pscustomobject]@{
                selector = [string]$selector
                plugin_enabled = [bool]$row.plugin_values[0]
                mcp_enabled = [bool]$row.mcp_values[0]
            }
        }
    )
    if ($records.Count -eq 0) {
        throw "The Codex config exposes no Evidence Lane selector state."
    }
    [pscustomobject]@{
        records = $records
        enabled_plugin_selectors = @(
            $records | Where-Object { $_.plugin_enabled } | ForEach-Object { $_.selector }
        )
        enabled_mcp_selectors = @(
            $records | Where-Object { $_.mcp_enabled } | ForEach-Object { $_.selector }
        )
        mismatched_selectors = @(
            $records | Where-Object { $_.plugin_enabled -ne $_.mcp_enabled } |
                ForEach-Object { $_.selector }
        )
    }
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
$pluginAddProperty = $install.activation.PSObject.Properties["plugin_add"]
$pluginSelectorProperty = $install.activation.PSObject.Properties["plugin_selector"]
$installedPathProperty = $install.activation.PSObject.Properties["installed_path"]
$activationVersionBound = $false
if ($null -ne $pluginAddProperty) {
    $activationVersionBound = (
        [string]$pluginAddProperty.Value.version -eq $installedPluginVersion
    )
}
elseif (
    $null -ne $pluginSelectorProperty -and
    [string]$pluginSelectorProperty.Value -like "evidence-lane-plugin@*" -and
    $null -ne $installedPathProperty -and
    -not [string]::IsNullOrWhiteSpace([string]$installedPathProperty.Value) -and
    (Test-Path -LiteralPath ([string]$installedPathProperty.Value) -PathType Container)
) {
    # Accepted fallback receipts created before plugin_add was emitted still bind
    # the exact selector, installed cache, version, receipt hash, and registry slot.
    $activationVersionBound = $true
}
elseif (
    $null -ne $install.activation.PSObject.Properties["transaction"] -and
    $null -ne $install.activation_authority.PSObject.Properties["selector"] -and
    [string]$install.activation.transaction.candidate_selector -like "evidence-lane-plugin@*" -and
    [string]$install.activation_authority.selector -ceq [string]$install.activation.transaction.candidate_selector -and
    [string]$install.marketplace.state -ceq "STAGED" -and
    [string]$install.marketplace.name -ceq (
        ([string]$install.activation.transaction.candidate_selector).Split("@", 2)[1]
    )
) {
    # A local-test stage deliberately leaves the candidate disabled. Its exact
    # version binding is the sealed marketplace plus transaction selector; the
    # separate CAS commit below proves the one-time activation.
    $activationVersionBound = $true
}
if (
    $install.schema -ne "evidence-lane.codex-stable-installation.v2" -or
    $install.status -ne "PASS" -or
    $installedPluginVersion -notmatch '^\d+\.\d+\.\d+\+codex\.[0-9A-Za-z.-]+$' -or
    -not $activationVersionBound -or
    $install.candidate_created_or_accepted -ne $false -or
    $install.pointer_moved -ne $false
) {
    throw "The supplied v2 installation receipt is not restart-eligible."
}

$restartAuthorityMode = "GIT_STABLE_INSTALL_RECEIPT"
$exactLocalTestCommit = $null
$observedLocalTestCommitSha = $null
$exactCodexConfig = $null
$observedCodexConfigSha = $null
$localTestCommit = $null
$candidateSelector = $null
$lastKnownGoodSelector = $null
$exactLocalRecoveryRegistry = $null
$observedLocalRecoveryRegistrySha = $null
$localRecoveryRegistryBody = $null
$isLocalCasRestart = (
    [string]$install.activation.state -ceq
        "CANDIDATE_STAGED_DISABLED_RESTART_TRUST_REQUIRED"
)
$isDisabledHookRecoveryRestart = (
    [string]$install.activation.state -ceq
        "LOCAL_2_2_HOOK_RECOVERY_SWITCHED_RESTART_REQUIRED"
)
$isLocalTestRestart = $isLocalCasRestart -or $isDisabledHookRecoveryRestart

if ($isLocalCasRestart) {
    if (
        [string]::IsNullOrWhiteSpace($LocalTestCommitReceipt) -or
        [string]::IsNullOrWhiteSpace($LocalTestCommitReceiptSha256) -or
        [string]::IsNullOrWhiteSpace($CodexConfig)
    ) {
        throw "A local-test restart requires the exact CAS commit receipt, its SHA-256, and -CodexConfig."
    }
    $exactLocalTestCommit = (Resolve-Path -LiteralPath $LocalTestCommitReceipt).Path
    $observedLocalTestCommitSha = Get-Sha256 $exactLocalTestCommit
    if ($observedLocalTestCommitSha -cne $LocalTestCommitReceiptSha256.ToUpperInvariant()) {
        throw "The exact local-test CAS commit receipt SHA-256 does not match."
    }
    $exactCodexConfig = (Resolve-Path -LiteralPath $CodexConfig).Path
    $observedCodexConfigSha = Get-Sha256 $exactCodexConfig
    $localTestCommit = Get-Content -LiteralPath $exactLocalTestCommit -Raw | ConvertFrom-Json
    $candidateSelector = [string]$install.activation.transaction.candidate_selector
    $lastKnownGoodSelector = [string]$install.activation.transaction.last_known_good_selector
    $expectedHookEvents = @(
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "userPromptSubmit"
    )
    $hookRecords = @($localTestCommit.hook_trust.records)
    $actualHookEvents = @($hookRecords | ForEach-Object { [string]$_.event_name } | Sort-Object -Unique)
    $expectedSortedEvents = @($expectedHookEvents | Sort-Object)
    $hookEventDifference = @(
        Compare-Object -ReferenceObject $expectedSortedEvents -DifferenceObject $actualHookEvents
    )
    $invalidHookRecords = @(
        $hookRecords | Where-Object {
            [string]$_.hook_key -cnotlike ($candidateSelector + ":hooks/hooks.json:*") -or
            [string]$_.trust_status -cne "trusted" -or
            $_.enabled -ne $true -or
            [string]$_.current_hash -cnotmatch '^sha256:[0-9a-f]{64}$'
        }
    )
    $rollbackBackup = [string]$localTestCommit.rollback_config_backup
    if (
        $install.activation.transaction.schema -cne "evidence-lane.codex-local-test-cas-transaction.v1" -or
        $install.activation.transaction.state -cne "CANDIDATE_STAGED_DISABLED" -or
        $install.activation.transaction.compare_and_swap -ne $true -or
        $install.activation.transaction.candidate_enabled -ne $false -or
        [int]$install.activation.transaction.switch_count -ne 0 -or
        $install.activation.transaction.rollback_capable -ne $true -or
        $install.activation_authority.status -cne "PASS" -or
        [string]$install.activation_authority.selector -cne $candidateSelector -or
        $localTestCommit.schema -cne "evidence-lane.codex-local-test-cas-commit.v1" -or
        $localTestCommit.status -cne "PASS" -or
        $localTestCommit.state -cne "CANDIDATE_SWITCHED_ONCE_RESTART_OR_RELOAD_REQUIRED" -or
        [string]$localTestCommit.stage_installation_receipt_sha256 -cne $observedInstallSha -or
        [string]$localTestCommit.transaction_id -cne [string]$install.activation.transaction.transaction_id -or
        [string]$localTestCommit.candidate_selector -cne $candidateSelector -or
        [string]$localTestCommit.last_known_good_selector -cne $lastKnownGoodSelector -or
        $localTestCommit.compare_and_swap -ne $true -or
        $localTestCommit.candidate_enabled -ne $true -or
        $localTestCommit.last_known_good_enabled -ne $false -or
        [int]$localTestCommit.switch_count -ne 1 -or
        $localTestCommit.rollback_capable -ne $true -or
        $localTestCommit.accepted_two_slot_registry_mutated -ne $false -or
        $localTestCommit.task_binding_used_for_authorization -ne $false -or
        $localTestCommit.goal_recovery_invoked -ne $false -or
        $localTestCommit.tunnel_invoked -ne $false -or
        $localTestCommit.candidate_created_or_accepted -ne $false -or
        $localTestCommit.pointer_moved -ne $false -or
        $localTestCommit.hil_inferred -ne $false -or
        $localTestCommit.config.activation_mode -cne "COMMIT_CANDIDATE" -or
        $localTestCommit.config.compare_and_swap -ne $true -or
        [int]$localTestCommit.config.switch_count -ne 1 -or
        $localTestCommit.config.candidate_enabled_after_write -ne $true -or
        $localTestCommit.config.last_known_good_enabled_after_write -ne $false -or
        [string]$localTestCommit.config.candidate_selector -cne $candidateSelector -or
        [string]$localTestCommit.config.last_known_good_selector -cne $lastKnownGoodSelector -or
        [string]$localTestCommit.config.after_sha256 -cne $observedCodexConfigSha -or
        $localTestCommit.hook_trust.schema -cne "evidence-lane.codex-hook-trust.v1" -or
        $localTestCommit.hook_trust.status -cne "PASS" -or
        [string]$localTestCommit.hook_trust.plugin_selector -cne $candidateSelector -or
        $localTestCommit.hook_trust.candidate_enabled -ne $true -or
        [int]$localTestCommit.hook_trust.hook_count -ne 8 -or
        $localTestCommit.hook_trust.unrelated_hook_state_mutated -ne $false -or
        $hookRecords.Count -ne 8 -or
        $hookEventDifference.Count -ne 0 -or
        $invalidHookRecords.Count -ne 0 -or
        [string]$localTestCommit.readiness.state -cne "INSTALLED_RESTART_OR_RELOAD_REQUIRED" -or
        $localTestCommit.readiness.ready -ne $false -or
        $localTestCommit.readiness.gates.hooks_trusted -ne $true -or
        $localTestCommit.readiness.gates.restart_or_reload_completed -ne $false -or
        [string]::IsNullOrWhiteSpace($rollbackBackup) -or
        (Get-Sha256 $rollbackBackup) -cne [string]$localTestCommit.rollback_config_backup_sha256 -or
        [string]$localTestCommit.rollback_config_backup_sha256 -cne
            [string]$install.activation.transaction.rollback_config_backup_sha256
    ) {
        throw "The supplied local-test stage and CAS commit receipts are not restart-eligible."
    }
    $restartAuthorityMode = "LOCAL_TEST_STAGED_INSTALL_PLUS_CAS_COMMIT"
}
elseif ($isDisabledHookRecoveryRestart) {
    if (
        [string]::IsNullOrWhiteSpace($LocalTestCommitReceipt) -or
        [string]::IsNullOrWhiteSpace($LocalTestCommitReceiptSha256) -or
        [string]::IsNullOrWhiteSpace($CodexConfig)
    ) {
        throw "A disabled-hook recovery restart requires the exact prior CAS receipt and live config."
    }
    if (
        -not [string]::IsNullOrWhiteSpace($LocalRecoveryRegistry) -or
        -not [string]::IsNullOrWhiteSpace($LocalRecoveryRegistrySha256)
    ) {
        throw "A mutable local-test restart cannot claim that the branch-commit recovery slot is byte-identical."
    }
    $exactLocalTestCommit = (Resolve-Path -LiteralPath $LocalTestCommitReceipt).Path
    $observedLocalTestCommitSha = Get-Sha256 $exactLocalTestCommit
    if ($observedLocalTestCommitSha -cne $LocalTestCommitReceiptSha256.ToUpperInvariant()) {
        throw "The exact prior local-test CAS receipt SHA-256 does not match."
    }
    $exactCodexConfig = (Resolve-Path -LiteralPath $CodexConfig).Path
    $observedCodexConfigSha = Get-Sha256 $exactCodexConfig
    $localTestCommit = Get-Content -LiteralPath $exactLocalTestCommit -Raw | ConvertFrom-Json
    $candidateSelector = [string]$install.activation.transaction.candidate_selector
    $lastKnownGoodSelector = [string]$install.activation.local_test_reinstall.last_known_good_selector
    $priorAuthority = $install.activation.transaction.prior_commit_authority
    $reinstallPriorAuthority = $install.activation.local_test_reinstall.prior_commit_authority
    $exclusiveChannel = $install.activation.exclusive_channel
    $hookTrust = $install.activation.hook_trust
    $hookIsolation = $install.activation.hook_event_isolation
    $runtimeReadiness = $install.activation.readiness
    $configState = Get-EvidenceLaneConfigState $exactCodexConfig
    $expectedHookEvents = @(
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "userPromptSubmit"
    )
    $hookRecords = @($hookTrust.records)
    $actualHookEvents = @(
        $hookRecords | ForEach-Object { [string]$_.event_name } | Sort-Object -Unique
    )
    $hookEventDifference = @(
        Compare-Object -ReferenceObject @($expectedHookEvents | Sort-Object) -DifferenceObject $actualHookEvents
    )
    $invalidHookRecords = @(
        $hookRecords | Where-Object {
            [string]$_.hook_key -cnotlike ($candidateSelector + ":hooks/hooks.json:*") -or
            [string]$_.trust_status -cne "trusted" -or
            $_.enabled -ne $true -or
            [string]$_.current_hash -cnotmatch '^sha256:[0-9a-f]{64}$'
        }
    )
    $rollbackBackup = [string]$install.activation.transaction.rollback_config_backup
    if (
        $install.activation.transaction.schema -cne "evidence-lane.codex-local-test-disabled-hook-recovery.v1" -or
        $install.activation.transaction.state -cne "LOCAL_SELECTOR_SWITCHED_ONCE" -or
        $install.activation.transaction.compare_and_swap -ne $true -or
        $install.activation.transaction.candidate_enabled -ne $true -or
        [int]$install.activation.transaction.switch_count -ne 1 -or
        $install.activation.transaction.rollback_capable -ne $true -or
        $install.activation.transaction.stable_and_fallback_enabled -ne $false -or
        $install.activation.transaction.exact_eight_hook_hashes_trusted -ne $true -or
        $install.activation.transaction.hook_event_isolation_verified_before_activation -ne $true -or
        $install.activation.transaction.restart_or_reload_required -ne $true -or
        $install.activation.transaction.candidate_created_or_accepted -ne $false -or
        $install.activation.transaction.pointer_moved -ne $false -or
        $install.activation.transaction.hil_inferred -ne $false -or
        [string]$priorAuthority.path -cne $exactLocalTestCommit -or
        [string]$priorAuthority.file_sha256 -cne $observedLocalTestCommitSha -or
        [string]$priorAuthority.transaction_id -cne [string]$localTestCommit.transaction_id -or
        [string]$priorAuthority.failed_candidate_config_sha256 -cne [string]$localTestCommit.config.after_sha256 -or
        [string]$reinstallPriorAuthority.path -cne [string]$priorAuthority.path -or
        [string]$reinstallPriorAuthority.file_sha256 -cne [string]$priorAuthority.file_sha256 -or
        [string]$reinstallPriorAuthority.transaction_id -cne [string]$priorAuthority.transaction_id -or
        $localTestCommit.schema -cne "evidence-lane.codex-local-test-cas-commit.v1" -or
        $localTestCommit.status -cne "PASS" -or
        $localTestCommit.state -cne "CANDIDATE_SWITCHED_ONCE_RESTART_OR_RELOAD_REQUIRED" -or
        [string]$localTestCommit.candidate_selector -cne $candidateSelector -or
        [string]$localTestCommit.last_known_good_selector -cne $lastKnownGoodSelector -or
        $localTestCommit.candidate_enabled -ne $true -or
        [int]$localTestCommit.switch_count -ne 1 -or
        $localTestCommit.rollback_capable -ne $true -or
        $localTestCommit.candidate_created_or_accepted -ne $false -or
        $localTestCommit.pointer_moved -ne $false -or
        $localTestCommit.hil_inferred -ne $false -or
        $install.activation.local_test_reinstall.schema -cne "evidence-lane.codex-local-test-reinstall.v1" -or
        $install.activation.local_test_reinstall.status -cne "PASS" -or
        $install.activation.local_test_reinstall.transaction_mode -cne "DISABLED_LOCAL_HOOK_RECOVERY" -or
        [string]$install.activation.local_test_reinstall.transaction_id -cne [string]$install.activation.transaction.transaction_id -or
        [string]$install.activation.local_test_reinstall.plugin_selector -cne $candidateSelector -or
        [string]$install.activation.local_test_reinstall.recovery_switch_target -cne $candidateSelector -or
        $install.activation.local_test_reinstall.accepted_two_slot_registry_mutated -ne $false -or
        $exclusiveChannel.status -cne "LOCAL_SELECTOR_SWITCHED_ONCE" -or
        [int]$exclusiveChannel.enabled_evidence_lane_count -ne 1 -or
        [string]$exclusiveChannel.enabled_selector -cne $candidateSelector -or
        [string]$exclusiveChannel.candidate_selector -cne $candidateSelector -or
        $exclusiveChannel.candidate_enabled -ne $true -or
        [int]$exclusiveChannel.switch_count -ne 1 -or
        $exclusiveChannel.stable_and_fallback_enabled -ne $false -or
        $exclusiveChannel.accepted_two_slot_registry_mutated -ne $false -or
        $hookTrust.schema -cne "evidence-lane.codex-hook-trust.v1" -or
        $hookTrust.status -cne "PASS" -or
        [string]$hookTrust.plugin_selector -cne $candidateSelector -or
        $hookTrust.candidate_enabled -ne $true -or
        $hookTrust.disabled_local_recovery -ne $true -or
        [int]$hookTrust.hook_count -ne 8 -or
        $hookTrust.unrelated_hook_state_mutated -ne $false -or
        $hookRecords.Count -ne 8 -or
        $hookEventDifference.Count -ne 0 -or
        $invalidHookRecords.Count -ne 0 -or
        $hookIsolation.schema -cne "evidence-lane.codex-installed-hook-event-isolation.v1" -or
        $hookIsolation.status -cne "PASS" -or
        $hookIsolation.state -cne "INACTIVE_KILL_SWITCH_VERIFIED" -or
        $hookIsolation.persistent_kill_switch -ne $true -or
        $hookIsolation.verified_before_install_activation -ne $true -or
        [string]$hookIsolation.installation_id -cne [string]$install.activation.transaction.transaction_id -or
        $runtimeReadiness.state -cne "INSTALLED_RESTART_OR_RELOAD_REQUIRED" -or
        $runtimeReadiness.ready -ne $false -or
        $runtimeReadiness.runtime_ready_before_task_reopen -ne $false -or
        $runtimeReadiness.gates.hooks_trusted -ne $true -or
        $runtimeReadiness.gates.restart_or_reload_completed -ne $false -or
        $install.activation_authority.status -cne "PASS" -or
        $install.activation_authority.boundary -cne "EXPLICIT_DISABLED_LOCAL_2_2_HOOK_RECOVERY" -or
        [string]$install.activation_authority.selector -cne $candidateSelector -or
        $install.activation_authority.accepted_two_slot_registry_mutated -ne $false -or
        $install.runtime_ready_before_task_reopen -ne $false -or
        $install.restart_required -ne $true -or
        $install.candidate_created_or_accepted -ne $false -or
        $install.pointer_moved -ne $false -or
        $install.hil_inferred -ne $false -or
        @($configState.enabled_plugin_selectors).Count -ne 1 -or
        @($configState.enabled_mcp_selectors).Count -ne 1 -or
        [string]$configState.enabled_plugin_selectors[0] -cne $candidateSelector -or
        [string]$configState.enabled_mcp_selectors[0] -cne $candidateSelector -or
        @($configState.mismatched_selectors).Count -ne 0 -or
        [string]::IsNullOrWhiteSpace($rollbackBackup) -or
        -not (Test-Path -LiteralPath $rollbackBackup -PathType Leaf) -or
        (Get-Sha256 $rollbackBackup) -cne [string]$install.activation.transaction.rollback_config_backup_sha256
    ) {
        throw "The supplied disabled-hook recovery receipt, prior CAS authority, and live selector state are not restart-eligible."
    }
    $restartAuthorityMode = "LOCAL_TEST_DISABLED_HOOK_RECOVERY_INSTALL_RECEIPT"
}
elseif (
    [string]$install.activation.state -cne "INSTALLED_RESTART_REQUIRED" -or
    -not [string]::IsNullOrWhiteSpace($LocalTestCommitReceipt) -or
    -not [string]::IsNullOrWhiteSpace($LocalTestCommitReceiptSha256) -or
    -not [string]::IsNullOrWhiteSpace($CodexConfig)
) {
    throw "The supplied v2 installation receipt is not restart-eligible."
}

$installedPluginRoot = Get-InstalledPluginRoot $install
$tunnelBoundary = $null
if ($Action -ne "Prepare" -or $TargetProcessId -gt 0) {
    $tunnelBoundary = Get-VersionMatchedTunnelBoundary `
        -ExactPluginRoot $installedPluginRoot `
        -PluginVersion $installedPluginVersion `
        -ExactDataRoot ([IO.Path]::GetFullPath($DataRoot))
}

$restartAuthority = [ordered]@{
    schema = "evidence-lane.codex-restart-authority.v1"
    mode = $restartAuthorityMode
    install_receipt_sha256 = $observedInstallSha
    plugin_version = $installedPluginVersion
    installed_plugin_root = $installedPluginRoot
    install_completed_before_restart = $true
    helper_installs_plugin = $false
    fixed_restart_delay_allowed = $false
}
if ($isLocalCasRestart) {
    $restartAuthority.local_test_commit_receipt = $exactLocalTestCommit
    $restartAuthority.local_test_commit_receipt_sha256 = $observedLocalTestCommitSha
    $restartAuthority.codex_config = $exactCodexConfig
    $restartAuthority.codex_config_sha256 = $observedCodexConfigSha
    $restartAuthority.transaction_id = [string]$localTestCommit.transaction_id
    $restartAuthority.candidate_selector = $candidateSelector
    $restartAuthority.last_known_good_selector = $lastKnownGoodSelector
    $restartAuthority.hook_trust_receipt_sha256 = [string]$localTestCommit.hook_trust.receipt_sha256
    $restartAuthority.hook_count = 8
}
elseif ($isDisabledHookRecoveryRestart) {
    $restartAuthority.prior_commit_receipt = $exactLocalTestCommit
    $restartAuthority.prior_commit_receipt_sha256 = $observedLocalTestCommitSha
    $restartAuthority.codex_config = $exactCodexConfig
    $restartAuthority.codex_config_sha256 = $observedCodexConfigSha
    $restartAuthority.transaction_id = [string]$install.activation.transaction.transaction_id
    $restartAuthority.prior_transaction_id = [string]$localTestCommit.transaction_id
    $restartAuthority.candidate_selector = $candidateSelector
    $restartAuthority.last_known_good_selector = $lastKnownGoodSelector
    $restartAuthority.hook_trust_receipt_sha256 = [string]$install.activation.hook_trust.receipt_sha256
    $restartAuthority.hook_count = 8
    $restartAuthority.enabled_evidence_lane_selector_count = 1
    $restartAuthority.branch_commit_recovery_selector = $lastKnownGoodSelector
    $restartAuthority.branch_commit_recovery_remains_prior_checkpoint = $true
    $restartAuthority.branch_commit_recovery_byte_identical_before_checkpoint = $false
    $restartAuthority.pre_2_2_recovery_allowed = $false
}
if ($null -ne $tunnelBoundary) {
    $restartAuthority.tunnel_marker_path = [string]$tunnelBoundary.marker_path
    $restartAuthority.tunnel_marker_sha256 = [string]$tunnelBoundary.marker_sha256
    $restartAuthority.tunnel_manager_sha256 = [string]$tunnelBoundary.manager_sha256
    $restartAuthority.tunnel_host_sha256 = [string]$tunnelBoundary.host_sha256
    $restartAuthority.tunnel_task_name = [string]$tunnelBoundary.task_name
    $restartAuthority.tunnel_release = [string]$tunnelBoundary.release
    $restartAuthority.tunnel_staged_not_started = $true
    $restartAuthority.restart_helper_starts_version_matched_tunnel = $true
}
$restartAuthoritySha256 = Get-StringSha256 (
    $restartAuthority | ConvertTo-Json -Compress -Depth 8
)

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
        restart_authority = $restartAuthority
        restart_authority_sha256 = $restartAuthoritySha256
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
        restart_control = [ordered]@{
            install_completed_before_helper = $true
            helper_installs_plugin = $false
            single_flight_required = $true
            exact_task_reopen_count = 1
            fixed_delay_used = $false
            tunnel_marker_sha256 = [string]$tunnelBoundary.marker_sha256
            tunnel_task_name = [string]$tunnelBoundary.task_name
            tunnel_starts_with_reopened_host = $true
            maximized_full_window_required = $true
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
        restart_authority = $restartAuthority
        restart_authority_sha256 = $restartAuthoritySha256
        install_completed_before_restart = $true
        helper_installs_plugin = $false
        single_flight_required = $true
        tunnel_marker_sha256 = [string]$tunnelBoundary.marker_sha256
        tunnel_task_name = [string]$tunnelBoundary.task_name
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
    $taskBindingSha256 = Get-Sha256 $taskBindingPath
    $goalRecoveryRefresh = Sync-GoalRecoveryBindingAfterTaskBinding `
        -ExactTaskBindingPath $taskBindingPath `
        -ExactTaskBindingSha256 $taskBindingSha256
    [ordered]@{
        status = "PASS"
        state = "PREPARED_NOT_RESTARTED"
        receipt_path = $receiptPath
        receipt_sha256 = $preparationReceiptSha256
        task_binding_receipt_path = $taskBindingPath
        task_binding_receipt_sha256 = $taskBindingSha256
        goal_recovery_refresh = $goalRecoveryRefresh
        app_id = [string]$hostProfile.app_id
        restart_authority_mode = $restartAuthorityMode
        helper_contract = "RESTART_ONLY_SINGLE_FLIGHT_VERSION_MATCHED_TUNNEL_EXACT_TASK_MAXIMIZED"
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
        $prepared.restart_authority_sha256 -ne $restartAuthoritySha256 -or
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
        $taskBinding.restart_authority_sha256 -ne $restartAuthoritySha256 -or
        $taskBinding.alias_claim_allowed -ne $true
    ) {
        throw "The preparation receipt does not bind this exact task and process."
    }
    $process = Get-RootCodexProcess -ProcessId $TargetProcessId -HostProfile $hostProfile
    if ([string]$process.ExecutablePath -ne [string]$prepared.target.executable_path) {
        throw "The exact target executable changed after preparation."
    }
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $leasePath = Join-Path $ReceiptDirectory "CODEX_RESTART_SINGLE_FLIGHT.json"
    if (Test-Path -LiteralPath $leasePath -PathType Leaf) {
        $existingLease = Get-Content -LiteralPath $leasePath -Raw | ConvertFrom-Json
        throw (
            "A restart helper invocation is already in flight for task " +
            [string]$existingLease.task_id + ". Duplicate helpers are forbidden."
        )
    }
    $leaseToken = [guid]::NewGuid().ToString("N")
    Write-NewJsonLease $leasePath ([ordered]@{
        schema = "evidence-lane.codex-restart-single-flight.v1"
        state = "PARENT_OWNS_PRELAUNCH_LEASE"
        token = $leaseToken
        project_id = $ProjectId
        evidence_session_id = $EvidenceSessionId
        task_id = $TaskId
        host_session_id = $HostSessionId
        install_receipt_sha256 = $observedInstallSha
        tunnel_marker_sha256 = [string]$tunnelBoundary.marker_sha256
        target_process_id = $TargetProcessId
        parent_process_id = $PID
        helper_process_id = $null
        created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    })
    $arguments = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Action", "Relaunch",
        "-InstallReceipt", $exactInstallReceipt,
        "-InstallReceiptSha256", $observedInstallSha,
        "-ProjectId", $ProjectId,
        "-EvidenceSessionId", $EvidenceSessionId,
        "-TaskId", $TaskId,
        "-HostSessionId", $HostSessionId,
        "-ActivePlanTaskId", $ActivePlanTaskId,
        "-TargetProcessId", [string]$TargetProcessId,
        "-PreparationReceipt", $exactPreparation,
        "-PreparationReceiptSha256", $PreparationReceiptSha256,
        "-ReceiptDirectory", $ReceiptDirectory,
        "-DataRoot", ([IO.Path]::GetFullPath($DataRoot)),
        "-RestartLeasePath", $leasePath,
        "-RestartLeaseToken", $leaseToken,
        "-AppId", $AppId
    )
    if ($isLocalTestRestart) {
        $arguments += @(
            "-LocalTestCommitReceipt", $exactLocalTestCommit,
            "-LocalTestCommitReceiptSha256", $observedLocalTestCommitSha,
            "-CodexConfig", $exactCodexConfig
        )
    }
    $argumentLine = ($arguments | ForEach-Object {
        ConvertTo-WindowsCommandLineArgument ([string]$_)
    }) -join " "
    $helperProcess = $null
    try {
        $helperProcess = Start-Process `
            -FilePath $powershell `
            -ArgumentList $argumentLine `
            -WindowStyle Hidden `
            -PassThru
        Write-JsonReceipt $leasePath ([ordered]@{
            schema = "evidence-lane.codex-restart-single-flight.v1"
            state = "CHILD_SPAWNED_BEFORE_EXACT_APP_STOP"
            token = $leaseToken
            project_id = $ProjectId
            evidence_session_id = $EvidenceSessionId
            task_id = $TaskId
            host_session_id = $HostSessionId
            install_receipt_sha256 = $observedInstallSha
            tunnel_marker_sha256 = [string]$tunnelBoundary.marker_sha256
            target_process_id = $TargetProcessId
            parent_process_id = $PID
            helper_process_id = [int]$helperProcess.Id
            created_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
        })
        Stop-Process -Id $TargetProcessId -Force
    }
    catch {
        if ($null -ne $helperProcess) {
            Stop-Process -Id ([int]$helperProcess.Id) -Force -ErrorAction SilentlyContinue
        }
        Remove-ExactRestartLease -Path $leasePath -Token $leaseToken
        throw
    }
    exit 0
}

if ($Action -eq "Relaunch") {
    $relaunchPath = Join-Path $ReceiptDirectory "CODEX_RELAUNCH_RECEIPT.json"
    $failurePath = Join-Path $ReceiptDirectory "CODEX_RELAUNCH_FAILURE.json"
    $leaseValidated = $false
    try {
        $expectedLeasePath = [IO.Path]::GetFullPath(
            (Join-Path $ReceiptDirectory "CODEX_RESTART_SINGLE_FLIGHT.json")
        )
        if (
            [string]::IsNullOrWhiteSpace($RestartLeasePath) -or
            [string]::IsNullOrWhiteSpace($RestartLeaseToken) -or
            [IO.Path]::GetFullPath($RestartLeasePath) -cne $expectedLeasePath -or
            -not (Test-Path -LiteralPath $expectedLeasePath -PathType Leaf)
        ) {
            throw "Internal relaunch requires the exact single-flight restart lease."
        }
        $leaseDeadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
        $restartLease = $null
        do {
            $restartLease = Get-Content -LiteralPath $expectedLeasePath -Raw | ConvertFrom-Json
            if (
                $restartLease.schema -ceq "evidence-lane.codex-restart-single-flight.v1" -and
                $restartLease.state -ceq "CHILD_SPAWNED_BEFORE_EXACT_APP_STOP" -and
                [int]$restartLease.helper_process_id -eq $PID
            ) {
                break
            }
            if ([DateTimeOffset]::UtcNow -ge $leaseDeadline) {
                throw "The parent did not atomically transfer the restart lease to this helper."
            }
            Start-Sleep -Milliseconds 50
        } while ($true)
        if (
            [string]$restartLease.token -cne $RestartLeaseToken -or
            [string]$restartLease.project_id -cne $ProjectId -or
            [string]$restartLease.evidence_session_id -cne $EvidenceSessionId -or
            [string]$restartLease.task_id -cne $TaskId -or
            [string]$restartLease.host_session_id -cne $HostSessionId -or
            [string]$restartLease.install_receipt_sha256 -cne $observedInstallSha -or
            [string]$restartLease.tunnel_marker_sha256 -cne ([string]$tunnelBoundary.marker_sha256) -or
            [int]$restartLease.target_process_id -ne $TargetProcessId
        ) {
            throw "The single-flight restart lease does not bind this exact helper and task."
        }
        $restartLeaseSha256 = Get-Sha256 $expectedLeasePath
        $leaseValidated = $true
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
            $prepared.restart_authority_sha256 -ne $restartAuthoritySha256 -or
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
            $taskBinding.restart_authority_sha256 -ne $restartAuthoritySha256 -or
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
        $tunnelStart = Start-VersionMatchedTunnel $tunnelBoundary
        $tunnelReady = Wait-VersionMatchedTunnelReady $tunnelBoundary
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
        $windowProof = Set-ExactHostWindowMaximized `
            -RootProcessId ([int]$verifiedRoot.ProcessId) `
            -HostProfile $hostProfile
        [void](Get-RootCodexProcess -ProcessId ([int]$verifiedRoot.ProcessId) -HostProfile $hostProfile)
        Write-JsonReceipt $relaunchPath ([ordered]@{
            schema = "evidence-lane.codex-relaunch-receipt.v2"
            state = "BOUND_CODEX_HOST_ROOT_RELAUNCHED_ONCE_MAXIMIZED_VERSION_MATCHED_TUNNEL_READY_AWAITING_NATIVE_PROOF"
            project_id = $ProjectId
            evidence_session_id = $EvidenceSessionId
            task_id = $TaskId
            prior_host_session_id = $HostSessionId
            preparation_receipt_sha256 = $PreparationReceiptSha256.ToUpperInvariant()
            task_binding_receipt_sha256 = Get-Sha256 $taskBindingPath
            install_receipt_sha256 = $observedInstallSha
            restart_authority_sha256 = $restartAuthoritySha256
            restart_authority_mode = $restartAuthorityMode
            host_application = [string]$hostProfile.host_application
            package_family_name = [string]$hostProfile.package_family_name
            app_id = [string]$hostProfile.app_id
            prior_bound_host_process_tree_fully_stopped = $true
            restart_lease = [ordered]@{
                schema = [string]$restartLease.schema
                receipt_sha256 = $restartLeaseSha256
                exact_helper_process_id = $PID
                single_flight_verified = $true
                released_after_receipt = $true
            }
            tunnel = [ordered]@{
                marker_path = [string]$tunnelBoundary.marker_path
                marker_sha256 = [string]$tunnelBoundary.marker_sha256
                runtime_root = [string]$tunnelBoundary.runtime_root
                task_name = [string]$tunnelBoundary.task_name
                start = $tunnelStart
                readiness = $tunnelReady
                version_matched_to_installed_plugin = $true
                helper_installed_plugin = $false
            }
            window = $windowProof
            task_navigation = [ordered]@{
                mode = "EXACT_BOUND_APPUSERMODELID_WITH_CODEX_THREAD_ARGUMENT"
                task_uri_sha256 = $taskUriSha256
                coordinate_clicking_used = $false
                launch_request_process_id = [uint32]$launchRequestProcessId
                exact_task_reopen_count = 1
                second_activation_requested = $false
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
            restart_control = [ordered]@{
                install_completed_before_helper = $true
                helper_installs_plugin = $false
                exact_app_stop_count = 1
                exact_task_reopen_count = 1
                fixed_delay_used = $false
                condition_driven_waits_only = $true
                tunnel_started_by_helper = $true
                maximized_full_window_verified = $true
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
    finally {
        if ($leaseValidated) {
            Remove-ExactRestartLease -Path $RestartLeasePath -Token $RestartLeaseToken
        }
    }
    exit 0
}
