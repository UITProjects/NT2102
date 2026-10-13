<#
  run-all-attacks.ps1 - orchestrator. -Mode Chain (default) = 10-apt-chain.ps1; -Mode Individual = 01..09.
  Run during a capture: live = 08-live-shipper.ps1 (see README.md); offline = etwctl start -Split test,
  then etwctl stop ; etwctl convert -Split test. Run 06-benign-workload.ps1 alongside for noise.
  Elevation: Chain and scenarios 05/09 need admin ("Run as administrator").
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
