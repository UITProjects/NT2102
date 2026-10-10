<#
  05 — CREDENTIAL ACCESS  (tactic: Credential Access)
  Technique: T1003.001 — dump LSASS memory (commonly via rundll32 + comsvcs.dll MiniDump).
  This is a STRONG, unambiguous malicious signal and a textbook OCR-APT target node:
  a benign-looking signed binary (rundll32) touching lsass and writing a .dmp file.
  Observable nodes: process (rundll32.exe) + file (the .dmp) + access to lsass.exe.
  Admin required: YES (reading LSASS needs SeDebug / admin). Run the VM session elevated.

  Defender is already off in your lab per the VM build; on a defended host this is blocked.
  Confirm the comsvcs test number first:  Invoke-AtomicTest T1003.001 -ShowDetailsBrief
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

Invoke-LabeledAtomic -Technique "T1003.001" -TestNumbers "1" -Tactic "credential-access" `
    -ProcessIndicators @("rundll32.exe") `
    -FileIndicators @("C:\Windows\Temp\lsass*.dmp","*\lsass*.dmp") `
    -Note "LSASS memory dump (comsvcs MiniDump)"
