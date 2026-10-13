<#
  09 - Defense evasion (T1070.001 clear event logs, T1070.006 timestomp). Admin: YES. Details: SCENARIOS.md.
  Clearing event logs does not touch the SilkETW capture (already streamed out). Timestomp may be invisible
  to the file keywords [INFERENCE]. Confirm test numbers: Invoke-AtomicTest T1070.001 -ShowDetailsBrief
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1070.001" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("wevtutil.exe") -Note "Clear Windows event logs"

Invoke-LabeledAtomic -Technique "T1070.006" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("powershell.exe") -FileIndicators @("C:\Users\Public\*") `
    -Note "Timestomp"
