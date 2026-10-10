<#
  run-all-attacks.ps1 — orchestrator.

  -Mode Chain       (default) runs the single APT story (10-apt-chain.ps1).
  -Mode Individual  runs scenarios 01..09 in sequence (more varied, more attack nodes).

  Intended to run DURING a test-split SilkETW capture:
     1) (optional) start lib\Start-LocalSink.ps1 on a reachable host
     2) etwctl start -Split test          # your collector
     3) .\run-all-attacks.ps1             # this script  (+ run 06-benign-workload.ps1 for noise)
     4) etwctl stop ; etwctl convert
     5) build node labels from C:\lab-labels\attack-labels.jsonl  (see tools\build_node_labels.py)

  Elevation: Chain and scenarios 05/09 need admin. Launch PowerShell "Run as administrator".
#>
param(
    [ValidateSet("Chain","Individual")][string]$Mode = "Chain"
)
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot

if ($Mode -eq "Chain") {
    & "$here\scenarios\10-apt-chain.ps1"
} else {
    Get-ChildItem "$here\scenarios\0*.ps1" | Sort-Object Name | ForEach-Object {
        Write-Host "`n>>> $($_.Name)" -ForegroundColor Cyan
        & $_.FullName
        Start-Sleep -Seconds 10   # separation between scenarios
    }
}
Write-Host "`nDone. Review labels: Get-Content C:\lab-labels\attack-labels.jsonl" -ForegroundColor Green
