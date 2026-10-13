<#
  04 - Persistence (T1053.005 scheduled task, T1547.001 startup FOLDER: a registry-only Run key is
  invisible to the collector). Admin only for SYSTEM-context tasks. Details: SCENARIOS.md.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1053.005" -TestNumbers "1" -Tactic "persistence" `
    -ProcessIndicators @("schtasks.exe") `
    -FileIndicators @("C:\Windows\System32\Tasks\*") `
    -Note "Scheduled task persistence"

# Confirm which test number is the STARTUP FOLDER variant (not the registry-only one):
#   Invoke-AtomicTest T1547.001 -ShowDetailsBrief
Invoke-LabeledAtomic -Technique "T1547.001" -TestNumbers "1" -Tactic "persistence" `
    -FileIndicators @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\*") `
    -Note "Startup folder persistence"
