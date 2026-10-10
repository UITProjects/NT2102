<#
  cleanup-all.ps1 — run AFTER you have stopped and converted the capture.

  Runs Invoke-AtomicTest -Cleanup for the ART-based techniques and removes the custom
  artifacts (staging dir, archive, downloaded payload). Run this OUTSIDE the labeled
  capture window so cleanup activity does not pollute your attack labels — or better,
  just restore the VM snapshot and skip this entirely.
#>
Import-Module "C:\AtomicRedTeam\invoke-atomicredteam\Invoke-AtomicRedTeam.psd1" -Force -ErrorAction SilentlyContinue

$atomicTechniques = @("T1033","T1082","T1087.001","T1057","T1218.011","T1218.010","T1218.005",
                      "T1053.005","T1547.001","T1003.001","T1070.001","T1070.006")
foreach ($t in $atomicTechniques) {
    try { Invoke-AtomicTest $t -Cleanup -Confirm:$false } catch { Write-Warning "cleanup $t: $($_.Exception.Message)" }
}

# custom artifacts
Remove-Item "C:\Users\Public\stage" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "C:\Users\Public\exfil.zip","C:\Users\Public\downloaded_tool.bin" -Force -ErrorAction SilentlyContinue
Write-Host "cleanup complete (snapshot restore is still the cleanest reset)" -ForegroundColor Green
