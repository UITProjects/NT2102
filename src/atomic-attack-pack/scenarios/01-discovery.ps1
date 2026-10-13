<#
  01 - Discovery (T1033, T1082, T1087.001, T1057). Low signal, few process nodes. Details: SCENARIOS.md.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1033"      -TestNumbers "1" -Tactic "discovery" `
    -ProcessIndicators @("whoami.exe")  -Note "System owner/user discovery"

Invoke-LabeledAtomic -Technique "T1082"      -TestNumbers "1" -Tactic "discovery" `
    -ProcessIndicators @("systeminfo.exe","reg.exe") -Note "System information discovery"

Invoke-LabeledAtomic -Technique "T1087.001"  -TestNumbers "1" -Tactic "discovery" `
    -ProcessIndicators @("net.exe","net1.exe") -Note "Local account enumeration"

Invoke-LabeledAtomic -Technique "T1057"      -TestNumbers "1" -Tactic "discovery" `
    -ProcessIndicators @("tasklist.exe") -Note "Process discovery"
