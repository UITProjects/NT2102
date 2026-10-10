<#
  04 — PERSISTENCE  (tactic: Persistence)
  Techniques: T1053.005 (scheduled task), T1547.001 (startup folder / Run key)
  Chosen because both are OBSERVABLE by your collector:
    - scheduled task -> schtasks.exe process edge
    - startup folder  -> a FILE written under the Startup directory
  (A pure registry Run-key write is NOT seen by Kernel-Process/File/Network — prefer the
   startup-FOLDER variant of T1547.001 so you get a file node.)
  Observable nodes: process (schtasks.exe) + file (task payload, startup shortcut/script).
  Admin required: scheduled-task creation for SYSTEM context needs admin; user-context tasks do not.
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
