<#
  02 - LOLBin execution (T1218.011/.010/.005). Details: SCENARIOS.md.
  Confirm test numbers first (YAML order drifts): Invoke-AtomicTest T1218.011 -ShowDetailsBrief
  Pick a test with a harmless local target (calc/notepad), not a remote scriptlet, unless you want a net edge.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1218.011" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("rundll32.exe") -Note "rundll32 proxy execution"

Invoke-LabeledAtomic -Technique "T1218.010" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("regsvr32.exe") -Note "regsvr32 proxy execution"

Invoke-LabeledAtomic -Technique "T1218.005" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("mshta.exe") -Note "mshta proxy execution"
