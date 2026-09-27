[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Source = Join-Path $PSScriptRoot 'EvidenceLaneStudioShell.cs'
$Icon = Join-Path $PSScriptRoot 'EvidenceLaneStudio.ico'
$Definition = Join-Path $PSScriptRoot 'EvidenceLaneStudio.iss'
$Compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$Inno = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
foreach ($required in @($Source, $Icon, $Definition, $Compiler, $Inno)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required Studio installer build input is unavailable: $required"
    }
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = $PSScriptRoot
}
$ExactOutput = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $ExactOutput -Force | Out-Null
$Shell = Join-Path $PSScriptRoot 'EvidenceLaneStudioShell.exe'
$Setup = Join-Path $PSScriptRoot 'EvidenceLaneStudioSetup.exe'
if (Test-Path -LiteralPath $Shell) { Remove-Item -LiteralPath $Shell -Force }
if (Test-Path -LiteralPath $Setup) { Remove-Item -LiteralPath $Setup -Force }
& $Compiler /nologo /target:winexe /platform:x64 /optimize+ "/win32icon:$Icon" "/out:$Shell" $Source
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Shell -PathType Leaf)) {
    throw "Studio shell compilation failed with exit code $LASTEXITCODE."
}
& $Inno "/O$ExactOutput" $Definition
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $ExactOutput 'EvidenceLaneStudioSetup.exe') -PathType Leaf)) {
    throw "Studio setup compilation failed with exit code $LASTEXITCODE."
}
$Receipt = [ordered]@{
    schema = 'evidence-lane.windows-studio-setup-build.v4'
    status = 'PASS'
    version = '4.0.10'
    source_sha256 = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant()
    icon_sha256 = (Get-FileHash -LiteralPath $Icon -Algorithm SHA256).Hash.ToLowerInvariant()
    definition_sha256 = (Get-FileHash -LiteralPath $Definition -Algorithm SHA256).Hash.ToLowerInvariant()
    shell_sha256 = (Get-FileHash -LiteralPath $Shell -Algorithm SHA256).Hash.ToLowerInvariant()
    shell_bytes = (Get-Item -LiteralPath $Shell).Length
    setup_sha256 = (Get-FileHash -LiteralPath (Join-Path $ExactOutput 'EvidenceLaneStudioSetup.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
    setup_bytes = (Get-Item -LiteralPath (Join-Path $ExactOutput 'EvidenceLaneStudioSetup.exe')).Length
    csharp_compiler = $Compiler
    inno_compiler = $Inno
    app_user_model_id = 'EvidenceLane.Studio'
}
$Json = ($Receipt | ConvertTo-Json -Depth 4).Replace("`r`n", "`n") + "`n"
if ($ExactOutput.Equals([IO.Path]::GetFullPath($PSScriptRoot), [StringComparison]::OrdinalIgnoreCase)) {
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'EvidenceLaneStudioSetup.build.json'), $Json, [Text.UTF8Encoding]::new($false))
}
$Json
