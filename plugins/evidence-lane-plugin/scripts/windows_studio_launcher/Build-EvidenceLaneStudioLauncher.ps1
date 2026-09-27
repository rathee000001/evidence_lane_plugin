[CmdletBinding()]
param(
    [string]$OutputPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Source = Join-Path $PSScriptRoot 'EvidenceLaneStudioLauncher.cs'
$Icon = Join-Path (Split-Path -Parent $PSScriptRoot) 'windows_studio_installer\EvidenceLaneStudio.ico'
$Compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw 'The pinned Windows .NET Framework x64 compiler is unavailable.'
}
if (-not (Test-Path -LiteralPath $Icon -PathType Leaf)) {
    throw 'The Evidence Lane Studio icon is unavailable.'
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot 'EvidenceLaneStudioLauncher.exe'
}
$ExactOutput = [IO.Path]::GetFullPath($OutputPath)
$OutputDirectory = Split-Path -Parent $ExactOutput
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
if (Test-Path -LiteralPath $ExactOutput) {
    Remove-Item -LiteralPath $ExactOutput -Force
}
& $Compiler /nologo /target:winexe /platform:x64 /optimize+ "/win32icon:$Icon" "/out:$ExactOutput" $Source
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ExactOutput -PathType Leaf)) {
    throw "Studio launcher compilation failed with exit code $LASTEXITCODE."
}
$SourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash.ToLowerInvariant()
$IconHash = (Get-FileHash -LiteralPath $Icon -Algorithm SHA256).Hash.ToLowerInvariant()
$BinaryHash = (Get-FileHash -LiteralPath $ExactOutput -Algorithm SHA256).Hash.ToLowerInvariant()
$Receipt = [ordered]@{
    schema = 'evidence-lane.windows-studio-launcher-build.v4'
    status = 'PASS'
    source = 'EvidenceLaneStudioLauncher.cs'
    source_sha256 = $SourceHash
    icon_sha256 = $IconHash
    output = [IO.Path]::GetFileName($ExactOutput)
    output_sha256 = $BinaryHash
    output_bytes = (Get-Item -LiteralPath $ExactOutput).Length
    compiler = $Compiler
    target = 'winexe-x64'
    version = '4.0.10.0'
}
$ReceiptJson = ($Receipt | ConvertTo-Json -Depth 4).Replace("`r`n", "`n")
$DefaultOutput = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'EvidenceLaneStudioLauncher.exe'))
if ($ExactOutput.Equals($DefaultOutput, [StringComparison]::OrdinalIgnoreCase)) {
    $ReceiptPath = Join-Path $PSScriptRoot 'EvidenceLaneStudioLauncher.build.json'
    [IO.File]::WriteAllText($ReceiptPath, $ReceiptJson + "`n", (New-Object Text.UTF8Encoding($false)))
}
$ReceiptJson
