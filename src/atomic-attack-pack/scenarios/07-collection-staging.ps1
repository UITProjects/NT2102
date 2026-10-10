<#
  07 — COLLECTION & STAGING  (tactic: Collection)
  Techniques: T1005 (data from local system), T1074.001 (local staging), T1560.001 (archive)
  Implemented CUSTOM so the staged files and archive path are deterministic (easy to label).
  Observable nodes: many file READ edges (collection) + a file WRITE (the archive) under one
  process. Fan-in of reads to a staging dir is a recognisable collection pattern.
  Admin required: no.
#>
. "$PSScriptRoot\..\lib\attack-lib.ps1"
Initialize-AttackLab

$stageDir = "C:\Users\Public\stage"
$archive  = "C:\Users\Public\exfil.zip"

Invoke-LabeledStep -Technique "T1560.001" -Tactic "collection" `
    -ProcessIndicators @("powershell.exe") `
    -FileIndicators @("$stageDir\*", $archive) `
    -Note "Collect local docs, stage, and archive" -Action {
        New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
        # Seed a few fake "sensitive" docs if the host has none, so reads/writes are observable.
        1..5 | ForEach-Object {
            $f = Join-Path $stageDir "secret_$_.txt"
            "confidential sample $_ $(Get-Random)" | Set-Content -Path $f -Encoding utf8
        }
        # Read them back (collection) and archive (staging complete).
        Get-ChildItem $stageDir -File | ForEach-Object { Get-Content $_.FullName | Out-Null }
        if (Test-Path $archive) { Remove-Item $archive -Force }
        Compress-Archive -Path "$stageDir\*" -DestinationPath $archive -Force
    }
