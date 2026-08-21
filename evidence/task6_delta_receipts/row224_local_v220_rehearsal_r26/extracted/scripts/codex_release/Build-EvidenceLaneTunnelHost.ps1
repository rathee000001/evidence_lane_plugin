[CmdletBinding()]
param(
    [string]$SourceRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$Compiler = "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
)

$ErrorActionPreference = 'Stop'
$sourceRootPath = [IO.Path]::GetFullPath($SourceRoot)
$compilerPath = [IO.Path]::GetFullPath($Compiler)
$sourcePath = Join-Path $PSScriptRoot 'windows_tunnel_host\EvidenceLaneTunnelHost.cs'
$outputPath = Join-Path $sourceRootPath 'scripts\windows_tunnel\EvidenceLaneTunnelHost.exe'

if (-not (Test-Path -LiteralPath $compilerPath -PathType Leaf)) {
    throw 'The Windows .NET Framework compiler is unavailable.'
}
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw 'The Evidence Lane no-visible-console tunnel-host source is unavailable.'
}
if (-not (Test-Path -LiteralPath (Split-Path -Parent $outputPath) -PathType Container)) {
    throw 'The Evidence Lane Windows tunnel scripts directory is unavailable.'
}

& $compilerPath /nologo /target:winexe /platform:anycpu /optimize+ /out:$outputPath $sourcePath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw 'The Evidence Lane no-visible-console tunnel host did not compile.'
}

$bytes = [IO.File]::ReadAllBytes($outputPath)
$peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
$optionalHeaderOffset = $peOffset + 24
$magic = [BitConverter]::ToUInt16($bytes, $optionalHeaderOffset)
$subsystemOffset = if ($magic -eq 0x20b) { $optionalHeaderOffset + 88 } else { $optionalHeaderOffset + 68 }
$subsystem = [BitConverter]::ToUInt16($bytes, $subsystemOffset)
if ($subsystem -ne 2) {
    throw 'The compiled tunnel host is not a Windows GUI-subsystem executable.'
}

[ordered]@{
    schema = 'evidence-lane.windows-tunnel-host-build.v1'
    status = 'PASS'
    output = $outputPath
    output_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $outputPath).Hash
    pe_subsystem = 'WINDOWS_GUI_NO_VISIBLE_CONSOLE'
    pe_subsystem_value = $subsystem
    create_no_window = $true
    raw_payload_persisted = $false
} | ConvertTo-Json -Depth 5
