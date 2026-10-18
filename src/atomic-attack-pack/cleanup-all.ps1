<#
  cleanup-all.ps1 — undo the RCE kill chain's artefacts on the target VM.

  Run OUTSIDE the labeled capture window so cleanup activity does not pollute the attack labels — or
  better, just restore the VM snapshot and skip this. There is no Atomic Red Team dependency anymore:
  the one scenario (scenarios/rce-killchain.ps1) uses only custom steps, so cleanup is deterministic.
#>
$taskName = "UpdaterSync"                 # must match scenarios/rce-killchain.ps1
$toolPath = "C:\Users\Public\downloaded_tool.bin"

# Persistence (T1053.005)
schtasks /delete /tn $taskName /f 2>$null

# Ingress-transfer artefact (T1105)
Remove-Item $toolPath -Force -ErrorAction SilentlyContinue

# Kill any shell still running under the exploited service (node.exe -> powershell), best effort.
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
        $p -and $p.Name -eq 'node.exe'
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "cleanup complete ($taskName removed, $toolPath removed, RCE shells killed)." -ForegroundColor Green
Write-Host "Snapshot restore is still the cleanest reset." -ForegroundColor DarkGray
