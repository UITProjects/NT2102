<#
  05 - Credential access (T1003.001, rundll32 + comsvcs MiniDump). Admin: YES. Details: SCENARIOS.md.
  Visible: rundll32 + the .dmp write; the lsass handle access itself is probably not collected [INFERENCE].
  Confirm the comsvcs test number first: Invoke-AtomicTest T1003.001 -ShowDetailsBrief
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1003.001" -TestNumbers "1" -Tactic "credential-access" `
    -ProcessIndicators @("rundll32.exe") `
    -FileIndicators @("C:\Windows\Temp\lsass*.dmp","*\lsass*.dmp") `
    -Note "LSASS memory dump (comsvcs MiniDump)"
