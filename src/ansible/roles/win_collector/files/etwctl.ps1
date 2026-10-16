<#
  etwctl - start/stop SilkService captures and convert them to OCR-APT CSVs.
    etwctl start   -Split train|test [-Run <name>]   new capture run (never appends to an old one)
    etwctl stop                                      stop the collector
    etwctl status                                    service state + current run file sizes
    etwctl convert -Split train|test                 raw\<split>\*\*.json -> <Graph>_<split>_*.csv
  Settings come from C:\ProgramData\etwlab\etwlab.json (written by the collector provisioner).
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet("start", "stop", "status", "convert")][string]$Command,
    [ValidateSet("train", "test")][string]$Split,
    [string]$Run
)
$ErrorActionPreference = "Stop"
$Home_  = "C:\ProgramData\etwlab"
$Cfg    = Get-Content "$Home_\etwlab.json" -Raw | ConvertFrom-Json
$State  = "$Home_\state.json"
$Utf8   = New-Object System.Text.UTF8Encoding($false)   # UTF-8 without BOM

function Get-State { if (Test-Path $State) { Get-Content $State -Raw | ConvertFrom-Json } else { $null } }
function Set-State($obj) { [IO.File]::WriteAllText($State, ($obj | ConvertTo-Json), $Utf8) }

function Show-Status {
    $svc = Get-Service $Cfg.ServiceName
    $st  = Get-State
    Write-Host "service : $($svc.Name) = $($svc.Status)"
    if ($st) {
        Write-Host "run     : $($st.split)/$($st.run)  started $($st.started_utc)  stopped $($st.stopped_utc)"
        Get-ChildItem $st.dir -Filter *.json -ErrorAction SilentlyContinue |
            ForEach-Object { "{0,-12} {1,10:N0} KB" -f $_.Name, ($_.Length / 1KB) } | Write-Host
    }
}

switch ($Command) {
    "start" {
        if (-not $Split) { throw "start needs -Split train or -Split test" }
        $svc = Get-Service $Cfg.ServiceName
        if ($svc.Status -ne "Stopped") { throw "collector is $($svc.Status). Run 'etwctl stop' first." }
        if (-not $Run) { $Run = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'") }
        $dir = Join-Path $Cfg.RawRoot "$Split\$Run"
        if (Test-Path $dir) { throw "run folder already exists: $dir (choose another -Run)" }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null

        # SilkService reads SilkServiceConfig.xml from its own folder, once, at service start.
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine("<SilkServiceConfig>")
        foreach ($c in $Cfg.Collectors) {
            [void]$sb.AppendLine("  <ETWCollector>")
            [void]$sb.AppendLine("    <Guid>$($c.Guid)</Guid>")
            [void]$sb.AppendLine("    <CollectorType>user</CollectorType>")
            [void]$sb.AppendLine("    <ProviderName>$($c.Provider)</ProviderName>")
            [void]$sb.AppendLine("    <UserKeywords>$($c.Keywords)</UserKeywords>")
            [void]$sb.AppendLine("    <OutputType>file</OutputType>")
            [void]$sb.AppendLine("    <Path>$dir\$($c.Stem).json</Path>")
            [void]$sb.AppendLine("  </ETWCollector>")
        }
        [void]$sb.AppendLine("</SilkServiceConfig>")
        [IO.File]::WriteAllText((Join-Path $Cfg.SilkServiceDir "SilkServiceConfig.xml"), $sb.ToString(), $Utf8)

        Set-State ([ordered]@{ split = $Split; run = $Run; dir = $dir;
                               started_utc = (Get-Date).ToUniversalTime().ToString("o"); stopped_utc = $null })
        Start-Service $Cfg.ServiceName

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
            Write-Warning "check the service log in $($Cfg.SilkServiceDir) and the Application event log"
            exit 1
        }
        Write-Host "[etwctl] capturing $Split/$Run -> $dir"
        Show-Status
    }
    "stop" {
        Stop-Service $Cfg.ServiceName -ErrorAction SilentlyContinue
        $st = Get-State
        if ($st -and -not $st.stopped_utc) {
            $st.stopped_utc = (Get-Date).ToUniversalTime().ToString("o"); Set-State $st
        }
        Show-Status
    }
    "status" { Show-Status }
    "convert" {
        if (-not $Split) { throw "convert needs -Split train or -Split test" }
        $st = Get-State
        if ((Get-Service $Cfg.ServiceName).Status -ne "Stopped" -and $st -and $st.split -eq $Split) {
            throw "a $Split capture is still running; 'etwctl stop' first (files are still being written)"
        }
        & $Cfg.Python $Cfg.Converter --raw-root $Cfg.RawRoot --split $Split --graph $Cfg.Graph --out $Cfg.OutDir
        if ($LASTEXITCODE -ne 0) { throw "converter exited with $LASTEXITCODE (see $($Cfg.OutDir)\$($Cfg.Graph)_$($Split)_summary.json)" }
    }
}
