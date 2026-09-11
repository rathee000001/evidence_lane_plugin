param([Parameter(Mandatory=$true)][string]$SdkRoot,
      [Parameter(Mandatory=$true)][string]$PackagePath)
$ErrorActionPreference = 'Stop'
Add-Type -LiteralPath (Join-Path $SdkRoot 'NuGet.Common.dll')
Add-Type -LiteralPath (Join-Path $SdkRoot 'NuGet.Versioning.dll')
Add-Type -LiteralPath (Join-Path $SdkRoot 'NuGet.Packaging.dll')
$packageReader = [NuGet.Packaging.PackageArchiveReader]::new((Resolve-Path -LiteralPath $PackagePath).Path)
try { $packageReader.GetContentHash([System.Threading.CancellationToken]::None, $null) }
finally { $packageReader.Dispose() }
