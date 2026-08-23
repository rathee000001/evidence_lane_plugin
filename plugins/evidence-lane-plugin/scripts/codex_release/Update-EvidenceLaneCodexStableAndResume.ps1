[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Schedule", "Complete")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

throw (
    "RETIRED_COMBINED_INSTALL_RESTART_HELPER: install and verify the exact package " +
    "in either the stable Git-main or versioned local-testing slot before invoking " +
    "Restart-EvidenceLaneCodex.ps1. No helper may install plugin bytes after the " +
    "Codex host stops; branch-recovery and local-successor selectors are retired " +
    "and cannot be installed, selected, or restored."
)
