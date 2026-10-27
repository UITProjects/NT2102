<#
  etwctl - start/stop ETW captures and convert them to OCR-APT CSVs.
    etwctl start   -Split train|test [-Run <name>]   new capture run (never appends to an old one)
    etwctl stop                                      stop the collector (python: flush + ship to Loki first)
    etwctl status                                    collector state + current run (+ lag, exclusions, Loki backlog)
    etwctl running                                   exit code 0 = a capture is running, 1 = not (for scripts)
    etwctl convert -Split train|test                 raw\<split>\*\*.json -> <Graph>_<split>_*.csv
  Settings come from C:\ProgramData\etwlab\etwlab.json (written by the win_collector role).

  The collector is etwcollect (src/etw-python), run by the SYSTEM scheduled task CollectorTask; it pushes the
  run's lines to Loki itself (DEVIATIONS D10; it replaced SilkService + Grafana Alloy on 2026-10-26).

  PowerShell notes: Get-ScheduledTask / Start-ScheduledTask read / start a Task Scheduler task (its .State is
  Ready, Running, ...); `& $exe args` runs a program; $LASTEXITCODE is the
  exit code of the last program; ConvertFrom-Json turns JSON text into an object with properties.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet("start", "stop", "status", "running", "convert")][string]$Command,
    [ValidateSet("train", "test")][string]$Split,
    [string]$Run
)
$ErrorActionPreference = "Stop"
$Home_  = "C:\ProgramData\etwlab"
$Cfg    = Get-Content "$Home_\etwlab.json" -Raw | ConvertFrom-Json
$State  = "$Home_\state.json"
$Status = "$Home_\collector-status.json"
$Utf8   = New-Object System.Text.UTF8Encoding($false)   # UTF-8 without BOM

function Get-State { if (Test-Path $State) { Get-Content $State -Raw | ConvertFrom-Json } else { $null } }
function Set-State($obj) { [IO.File]::WriteAllText($State, ($obj | ConvertTo-Json), $Utf8) }

function Test-CollectorRunning { return ((Get-ScheduledTask -TaskName $Cfg.CollectorTask).State -eq "Running") }

function Show-Status {
    $st = Get-State
    Write-Host "collector: task $($Cfg.CollectorTask) = $((Get-ScheduledTask -TaskName $Cfg.CollectorTask).State)"
    if ($st) {
        Write-Host "run     : $($st.split)/$($st.run)  started $($st.started_utc)  stopped $($st.stopped_utc)"
        Get-ChildItem $st.dir -Filter *.json -ErrorAction SilentlyContinue |
            ForEach-Object { "{0,-12} {1,10:N0} KB" -f $_.Name, ($_.Length / 1KB) } | Write-Host
    }
    if (Test-Path $Status) {
        $c = Get-Content $Status -Raw | ConvertFrom-Json
        Write-Host "status  : $($c.state) (pid $($c.pid), updated $($c.updated_utc))"
        foreach ($p in $c.providers.PSObject.Properties) {          # one line per provider: kproc, kfile, knet
            Write-Host ("  {0,-6} lines {1,9}  newest {2}  lag {3} s" -f $p.Name, $p.Value.lines,
                        $p.Value.newest_event_utc, $p.Value.lag_s)
        }
        Write-Host "  excluded: $($c.excluded | ConvertTo-Json -Compress)"
        if ($c.etw) { Write-Host "  ETW lost: events $($c.etw.events_lost)  realtime buffers $($c.etw.realtime_buffers_lost)" }
        if ($c.ship) { Write-Host "  Loki: pushed $($c.ship.pushed_lines)  backlog $($c.ship.backlog_bytes | ConvertTo-Json -Compress)  last error $($c.ship.last_error)" }
        if ($c.last_error) { Write-Host "  last error: $($c.last_error)" }
    }
}

switch ($Command) {
    "start" {
        if (-not $Split) { throw "start needs -Split train or -Split test" }
        if (Test-CollectorRunning) { throw "a capture is already running. Run 'etwctl stop' first." }
        if (-not $Run) { $Run = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'") }
        $dir = Join-Path $Cfg.RawRoot "$Split\$Run"
        if (Test-Path $dir) { throw "run folder already exists: $dir (choose another -Run)" }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null

        # dosmap.meta: \Device\HarddiskVolumeN -> C: for THIS machine, one JSON line. It goes to Loki
        # (job="etw-meta") so the converter on the Ubuntu trainer maps paths like it would here. Not *.json,
        # so the converter never reads it as events. (etwship.py prints the map; -Compress = one line.)
        $dmap = (& $Cfg.Python "$Home_\etwship.py" --print-dos-map | Out-String) | ConvertFrom-Json | ConvertTo-Json -Compress
        [IO.File]::WriteAllText((Join-Path $dir "dosmap.meta"), "$dmap`n", $Utf8)

        Set-State ([ordered]@{ split = $Split; run = $Run; dir = $dir;
                               started_utc = (Get-Date).ToUniversalTime().ToString("o"); stopped_utc = $null })
        # The task runs `python -m etwcollect run`, which reads the run folder from state.json (written above).
        Start-ScheduledTask -TaskName $Cfg.CollectorTask

        # Canary: a process start + image loads + file reads we know must show up.
        Start-Sleep -Seconds 5
        & cmd.exe /c "whoami > nul"
        $deadline = (Get-Date).AddSeconds(40)
        $need = $Cfg.Collectors | Where-Object { $_.Required } | ForEach-Object { "$dir\$($_.Stem).json" }
        do {
            Start-Sleep -Seconds 2
            $missing = @($need | Where-Object { -not ((Test-Path $_) -and (Get-Item $_).Length -gt 0) })
        } while ($missing.Count -gt 0 -and (Get-Date) -lt $deadline)
        if ($missing.Count -gt 0) {
            Write-Warning "no events yet in: $($missing -join ', ')"
            Write-Warning "check $Status (last_error) and: & '$($Cfg.EtwPython)' -m etwcollect status"
            exit 1
        }
        Write-Host "[etwctl] capturing $Split/$Run -> $dir"
        Show-Status
    }
    "stop" {
        if (Test-CollectorRunning) {
            # etwcollect stop: stops the ETW session; the collector then writes its last lines, ships them
            # to Loki (bounded) and exits. It waits for that itself (--wait).
            & $Cfg.EtwPython -m etwcollect stop --wait 120 | Out-Host
            $deadline = (Get-Date).AddSeconds(30)
            while ((Test-CollectorRunning) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
            if (Test-CollectorRunning) {
                # Last resort: kills the process (no final flush); the next start ships what is on disk.
                Write-Warning "collector task still running after stop; ending it with Stop-ScheduledTask"
                Stop-ScheduledTask -TaskName $Cfg.CollectorTask
            }
        }
        $st = Get-State
        if ($st -and -not $st.stopped_utc) {
            $st.stopped_utc = (Get-Date).ToUniversalTime().ToString("o"); Set-State $st
        }
        Show-Status
    }
    "status" { Show-Status }
    "running" { if (Test-CollectorRunning) { exit 0 } else { exit 1 } }
    "convert" {
        if (-not $Split) { throw "convert needs -Split train or -Split test" }
        $st = Get-State
        if ((Test-CollectorRunning) -and $st -and $st.split -eq $Split) {
            throw "a $Split capture is still running; 'etwctl stop' first (files are still being written)"
        }
        & $Cfg.Python $Cfg.Converter --raw-root $Cfg.RawRoot --split $Split --graph $Cfg.Graph --out $Cfg.OutDir
        if ($LASTEXITCODE -ne 0) { throw "converter exited with $LASTEXITCODE (see $($Cfg.OutDir)\$($Cfg.Graph)_$($Split)_summary.json)" }
    }
}
