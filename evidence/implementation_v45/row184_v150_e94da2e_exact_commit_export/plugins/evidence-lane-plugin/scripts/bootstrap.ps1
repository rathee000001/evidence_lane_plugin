[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PluginRoot = Split-Path -Parent $PSScriptRoot
$Bootstrap = Join-Path $PluginRoot 'scripts\bootstrap.py'

python $Bootstrap
if ($LASTEXITCODE -ne 0) {
    throw "Evidence Lane bootstrap failed with exit code $LASTEXITCODE"
}
