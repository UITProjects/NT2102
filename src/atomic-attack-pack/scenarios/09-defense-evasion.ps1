<#
  09 — DEFENSE EVASION / ANTI-FORENSICS  (tactic: Defense Evasion)
  Techniques: T1070.001 (clear Windows event logs), T1070.006 (timestomp)
  Observable nodes: process (wevtutil.exe), plus file timestamp changes.
  NOTE: clearing the Windows Event Log does NOT erase your SilkETW capture — SilkETW streams
  events out to C:\lab-out as they happen, so this step is itself recorded. That is the point:
  log-clearing is a high-value late-stage IOC your detector should still see.
  Admin required: YES (wevtutil clear needs admin). Run elevated.

  Confirm test numbers:  Invoke-AtomicTest T1070.001 -ShowDetailsBrief
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1070.001" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("wevtutil.exe") -Note "Clear Windows event logs"

Invoke-LabeledAtomic -Technique "T1070.006" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("powershell.exe") -FileIndicators @("C:\Users\Public\*") `
    -Note "Timestomp"
