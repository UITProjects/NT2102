<#
  02 — LOLBIN EXECUTION  (tactic: Defense Evasion / Execution)
  Techniques: T1218.011 (rundll32), T1218.010 (regsvr32), T1218.005 (mshta)
  Idea: signed Microsoft binaries used to execute code. Classic evasion. These produce an
  anomalous PARENT->CHILD process edge (e.g. a document app spawning rundll32).
  Observable nodes: process (rundll32/regsvr32/mshta + whatever they launch), sometimes file/net.
  Admin required: no.

  NOTE: confirm the test numbers first — the YAML ordering changes as the repo updates:
      Invoke-AtomicTest T1218.011 -ShowDetailsBrief
  Pick a test that launches a harmless local target (calc/notepad), not a remote scriptlet,
  unless you intentionally want a network edge here.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1218.011" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("rundll32.exe") -Note "rundll32 proxy execution"

Invoke-LabeledAtomic -Technique "T1218.010" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("regsvr32.exe") -Note "regsvr32 proxy execution"

Invoke-LabeledAtomic -Technique "T1218.005" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("mshta.exe") -Note "mshta proxy execution"
