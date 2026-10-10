<#
  06 — C2 BEACON  (tactic: Command & Control)
  Technique: T1071.001 (application-layer protocol: web) — implemented CUSTOM so the beacon
  hits YOUR sink at a fixed cadence. Periodicity + single remote endpoint is the signature
  of a C2 node; this is the clearest "network" malicious node for your graph.
  Observable nodes: process (powershell.exe) + repeated network edges to one dst.
  Admin required: no.

  Start lib\Start-LocalSink.ps1 first (on a reachable host / the VM's own 10.21.2.10).
  Tunables below; env override $env:C2_SINK_HOST / _PORT.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

$SinkHost   = if ($env:C2_SINK_HOST) { $env:C2_SINK_HOST } else { "10.21.2.10" }
$SinkPort   = if ($env:C2_SINK_PORT) { [int]$env:C2_SINK_PORT } else { 8888 }
$Beacons    = 8      # number of check-ins
$IntervalSec = 15    # cadence

Invoke-LabeledStep -Technique "T1071.001" -Tactic "command-and-control" `
    -ProcessIndicators @("powershell.exe") `
    -NetworkIndicators @("${SinkHost}:${SinkPort}") `
    -Note "Periodic web beacon ($Beacons x ${IntervalSec}s)" -Action {
        for ($i = 0; $i -lt $Beacons; $i++) {
            $uri = "http://$($SinkHost):$($SinkPort)/beacon?id=w10&seq=$i"
            try { Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 3 | Out-Null } catch {}
            Start-Sleep -Seconds $IntervalSec
        }
    }
