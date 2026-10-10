<#
    attack-lib.ps1  —  shared helpers for the OCR-APT attack pack.

    PowerShell orientation (you come from Linux):
      - "function Name { param(...) ... }" defines a function; params are named flags.
      - "$script:X" is a script-scoped variable (visible to every function in this file
        once dot-sourced).
      - "[DateTime]::UtcNow" is a static method call, like Java's DateTime.utcNow().
      - "ConvertTo-Json -Compress" serialises a hashtable to one JSON line.
      - "Add-Content" appends a line to a file (>> in bash).
      - Dot-source this file from a scenario with:  . "$PSScriptRoot\..\lib\attack-lib.ps1"
        The leading dot means "run in the current scope" so the functions stay defined.
#>

function Initialize-AttackLab {
    # Call once per session. Idempotent. Sets where labels go and imports the ART module.
    param(
        [string]$LabelFile = "C:\lab-labels\attack-labels.jsonl",
        [string]$AttackHost = "SimulatedW10"   # must match the graph/host name your converter asserts
    )
    $script:LabelFile  = $LabelFile
    $script:AttackHost = $AttackHost

    $dir = Split-Path $LabelFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    # Where Invoke-AtomicTest writes its own execution log (separate from our labels).
    $env:ARTLOG = "C:\AtomicRedTeam\logs"

    if (-not (Get-Module -Name Invoke-AtomicRedTeam)) {
        $psd1 = "C:\AtomicRedTeam\invoke-atomicredteam\Invoke-AtomicRedTeam.psd1"
        if (Test-Path $psd1) { Import-Module $psd1 -Force }
        else { Write-Warning "Invoke-AtomicRedTeam not found at $psd1 — install it or fix the path." }
    }
    Write-Host "[lab] labels -> $script:LabelFile  host=$script:AttackHost" -ForegroundColor Cyan
}

function Write-AttackLabel {
    # Appends one ground-truth record. This is YOUR label oracle, not ART's.
    param(
        [Parameter(Mandatory)][string]$Technique,
        [string]$Tactic = "",
        [Parameter(Mandatory)][datetime]$Start,
        [Parameter(Mandatory)][datetime]$End,
        [string[]]$ProcessIndicators = @(),   # process image names, e.g. 'rundll32.exe'
        [string[]]$FileIndicators    = @(),   # file paths/globs the technique creates/reads
        [string[]]$NetworkIndicators = @(),   # 'host:port' endpoints the technique talks to
        [string]$Note = "",
        [string]$Source = "atomic-red-team"   # or 'custom' for non-ART steps
    )
    if (-not $script:LabelFile) { Initialize-AttackLab }

    $rec = [ordered]@{
        host                = $script:AttackHost
        tactic              = $Tactic
        technique           = $Technique
        source              = $Source
        note                = $Note
        start_utc           = $Start.ToUniversalTime().ToString("o")  # ISO-8601, e.g. 2026-10-10T07:15:03.1234567Z
        end_utc             = $End.ToUniversalTime().ToString("o")
        malicious           = $true
        process_indicators  = $ProcessIndicators
        file_indicators     = $FileIndicators
        network_indicators  = $NetworkIndicators
    }
    $json = $rec | ConvertTo-Json -Compress -Depth 5
    Add-Content -Path $script:LabelFile -Value $json -Encoding utf8
    Write-Host ("[label] {0,-12} {1}  ({2:HH:mm:ss}-{3:HH:mm:ss}Z)" -f $Technique, $Note, $Start.ToUniversalTime(), $End.ToUniversalTime()) -ForegroundColor Yellow
}

function Invoke-LabeledAtomic {
    # Run an Atomic Red Team test and label the time window it occupied.
    param(
        [Parameter(Mandatory)][string]$Technique,
        [Parameter(Mandatory)][string]$TestNumbers,     # e.g. "1" or "1,2"
        [string]$Tactic = "",
        [string[]]$ProcessIndicators = @(),
        [string[]]$FileIndicators    = @(),
        [string[]]$NetworkIndicators = @(),
        [string]$Note = "",
        [switch]$GetPrereqs
    )
    $start = [DateTime]::UtcNow
    try {
        if ($GetPrereqs) { Invoke-AtomicTest $Technique -TestNumbers $TestNumbers -GetPrereqs }
        Invoke-AtomicTest $Technique -TestNumbers $TestNumbers -Confirm:$false
    } catch {
        Write-Warning "atomic $Technique #$TestNumbers failed: $($_.Exception.Message)"
    }
    $end = [DateTime]::UtcNow
    Write-AttackLabel -Technique $Technique -Tactic $Tactic -Start $start -End $end `
        -ProcessIndicators $ProcessIndicators -FileIndicators $FileIndicators `
        -NetworkIndicators $NetworkIndicators -Note $Note -Source "atomic-red-team"
}

function Invoke-LabeledStep {
    # Run a custom (non-ART) action and label it. Use when no clean atomic exists,
    # or when you need precise control (e.g. the C2 beacon target).
    param(
        [Parameter(Mandatory)][string]$Technique,
        [Parameter(Mandatory)][scriptblock]$Action,
        [string]$Tactic = "",
        [string[]]$ProcessIndicators = @(),
        [string[]]$FileIndicators    = @(),
        [string[]]$NetworkIndicators = @(),
        [string]$Note = ""
    )
    $start = [DateTime]::UtcNow
    try { & $Action } catch { Write-Warning "step $Technique failed: $($_.Exception.Message)" }
    $end = [DateTime]::UtcNow
    Write-AttackLabel -Technique $Technique -Tactic $Tactic -Start $start -End $end `
        -ProcessIndicators $ProcessIndicators -FileIndicators $FileIndicators `
        -NetworkIndicators $NetworkIndicators -Note $Note -Source "custom"
}
