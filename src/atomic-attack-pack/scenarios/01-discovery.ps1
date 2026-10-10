<#
  01 — DISCOVERY  (tactic: Discovery)
  Techniques: T1033 (whoami), T1082 (system info), T1087.001 (local accounts), T1057 (process)
  Signal level: LOW. These spawn short-lived processes and look almost benign — a good test
  of whether your detector over-flags ordinary admin activity (precision stressor).
  Observable nodes: process (whoami.exe, systeminfo.exe, net.exe, tasklist.exe).
  Admin required: no.
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
