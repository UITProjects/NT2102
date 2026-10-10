<#
.SYNOPSIS
  06-benign-workload.ps1 - scripted benign "office/developer user" activity for the
  SimulatedW10 ETW lab (NT2102 / OCR-APT POC).

.DESCRIPTION
  Runs a seeded random sequence of benign scenarios (browse, notes, office docs, downloads,
  file ops, network checks, Explorer, Paint) for -DurationMinutes.
  User apps are launched THROUGH explorer.exe (Shell COM), so in the provenance graph their
  parent is explorer.exe like a real user's, not this powershell.exe. Reason: the attack side
  (Invoke-AtomicRedTeam) runs via powershell.exe; if benign training data showed powershell.exe
  spawning everything, the one-class model would learn the attacker's parent process as "normal".

  The script writes NOTHING to disk itself during the run (the ledger is buffered in memory and
  written at the end), so its own footprint in the capture stays small.

.NOTES
  Run INSIDE the VM in the interactive desktop session (console/RDP window that stays open and
  unminimised), NOT via `vagrant powershell`/WinRM: that is a non-interactive session where GUI
  apps and SendKeys don't behave like a user.

  Once per VM (before any capture):  .\06-benign-workload.ps1 -SelfTest
  Warm-up run (not captured), so first-run effects (Edge welcome, Office activation) are gone:
                                     .\06-benign-workload.ps1 -DurationMinutes 5 -Seed 1
  Real capture (manual):  etwctl start -Split train ; .\06-benign-workload.ps1 -DurationMinutes 20 -PauseBeforeLedger
  Real capture (automated): .\07-build-train-dataset.ps1   (passes -BeforeLedger = "stop the capture")
#>
[CmdletBinding()]
param(
  [int]$DurationMinutes = 20,
  [int]$Seed = 42,                       # fixes the scenario sequence and choices (not OS timing)
  [int]$MinGapSec = 10,                  # idle time between scenarios
  [int]$MaxGapSec = 45,
  [string]$WorkDir   = "$env:USERPROFILE\Documents\work",
  [string]$LedgerDir = 'C:\lab-labels\benign',
  [switch]$IncludeUserPowerShell,        # opt-in: benign powershell.exe usage (see DESCRIPTION)
  [switch]$PauseBeforeLedger,            # wait for Enter (after you run `etwctl stop`) before writing the ledger
  [scriptblock]$BeforeLedger,            # automation hook: run before the ledger is written (07 passes "stop capture")
  [switch]$SelfTest                      # check launcher parentage, Office, curl/tar, session; then exit
)
Set-StrictMode -Version Latest           # like "use strict": undefined variables are errors
$ErrorActionPreference = 'Stop'          # make cmdlet errors throw (so try/catch works)

# ---------------------------------------------------------------- helpers
$Rng    = [System.Random]::new($Seed)    # one seeded RNG for every random choice below
$Wsh    = New-Object -ComObject WScript.Shell   # in-process COM: AppActivate/SendKeys, spawns nothing
$Ledger = New-Object System.Collections.Generic.List[object]
$script:Shell = $null
$script:LaunchMode = 'direct'

function Pick([object[]]$Items) { $Items[$Rng.Next($Items.Count)] }
function Dwell([int]$Min, [int]$Max) { Start-Sleep -Seconds $Rng.Next($Min, $Max + 1) }

function Add-Ledger([string]$Scenario, [string]$Action, [string]$Target) {
  $Ledger.Add([pscustomobject]@{
    utc = (Get-Date).ToUniversalTime().ToString('o'); scenario = $Scenario
    action = $Action; target = $Target; launch_mode = $script:LaunchMode })
}

function Initialize-Launcher {
  # ShellWindows / ShellBrowserWindow are COM objects SERVED BY the running explorer.exe.
  # ShellExecute on them asks explorer to start the program => parent = explorer.exe.
  $candidates = @(
    @{ Clsid = '9BA05972-F6A8-11CF-A442-00A0C90A8F39'; Get = { param($o) $o.Item().Document.Application } },  # ShellWindows
    @{ Clsid = 'C08AFD90-F2A1-11D1-8455-00A0C91F3880'; Get = { param($o) $o.Document.Application } }          # ShellBrowserWindow
  )
  foreach ($c in $candidates) {
    try {
      $obj = [Activator]::CreateInstance([Type]::GetTypeFromCLSID($c.Clsid))
      $app = & $c.Get $obj
      if ($null -ne $app) { $script:Shell = $app; $script:LaunchMode = 'explorer-com'; return }
    } catch { }
  }
  Write-Warning 'Explorer COM launcher unavailable -> Start-Process fallback (parent = powershell.exe).'
}

function Invoke-App([string]$File, [string]$Arguments = '', [int]$Show = 1) {
  # $Show: 1 = normal window, 0 = hidden (for cmd one-liners)
  if ($script:Shell) { $script:Shell.ShellExecute($File, $Arguments, $WorkDir, 'open', $Show) }
  else {
    $p = @{ FilePath = $File; WorkingDirectory = $WorkDir }   # @{} = hashtable; @p below = "splat" it as named args
    if ($Arguments) { $p.ArgumentList = $Arguments }
    if ($Show -eq 0) { $p.WindowStyle = 'Hidden' }
    Start-Process @p
  }
}

# A "user typing in a terminal": cmd.exe child of explorer.exe, its tools children of cmd.exe.
function Invoke-Cmd([string]$CommandLine, [int]$WaitSec = 5) {
  Invoke-App 'cmd.exe' "/c $CommandLine" 0
  Start-Sleep -Seconds $WaitSec
}

# Politely close windows (WM_CLOSE), like clicking X. No process is spawned to do it.
function Close-Windows([string[]]$Names) {
  Get-Process -Name $Names -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } |
    ForEach-Object { [void]$_.CloseMainWindow() }
}

function Close-ExplorerWindows {
  # Close folder windows we opened (Quit() is a COM call into explorer, no new process).
  (New-Object -ComObject Shell.Application).Windows() |
    Where-Object { $_.LocationURL -like 'file:///*' } | ForEach-Object { $_.Quit() }
}

function Get-OfficeInfo {
  $ap = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths'
  [pscustomobject]@{ Word = Test-Path "$ap\WINWORD.EXE"; Excel = Test-Path "$ap\EXCEL.EXE" }
}

# ---------------------------------------------------------------- content pools (fixed => reproducible)
$Urls = @('https://en.wikipedia.org/wiki/Provenance', 'https://learn.microsoft.com/en-us/windows/',
          'https://github.com/explore', 'https://stackoverflow.com/questions', 'https://www.bbc.com/news',
          'https://docs.python.org/3/tutorial/')
$Downloads = @('https://www.rfc-editor.org/rfc/rfc2616.txt', 'https://www.gutenberg.org/cache/epub/1342/pg1342.txt',
               'https://www.python.org/static/img/python-logo.png')
$Sentences = @('Follow up with the team about the release.', 'Review the quarterly budget draft.',
               'Book a room for the Thursday meeting.', 'Update the onboarding checklist.')  # no SendKeys specials: + ^ % ~ ( ) { } [ ]

# ---------------------------------------------------------------- scenarios
$Scenarios = @{
  browse = {
    $u = Pick $Urls; Invoke-App $u; Add-Ledger 'browse' 'open_url' $u     # URL -> default browser (Edge)
    Dwell 20 60
    if ($Rng.Next(2) -eq 0) { $u2 = Pick $Urls; Invoke-App $u2; Add-Ledger 'browse' 'open_url' $u2; Dwell 15 40 }
    Close-Windows 'msedge'
  }
  notes = {
    $f = Join-Path $WorkDir ('note_{0:yyyyMMdd_HHmmss}.txt' -f (Get-Date))
    Invoke-Cmd "echo Notes>`"$f`"" 3                                       # cmd.exe creates the file
    Invoke-App 'notepad.exe' "`"$f`""; Add-Ledger 'notes' 'edit' $f
    Start-Sleep -Seconds 3
    if ($Wsh.AppActivate([IO.Path]::GetFileName($f))) {                     # focus by window-title prefix
      $Wsh.SendKeys('^{END}{ENTER}' + (Pick $Sentences)); Start-Sleep 1
      $Wsh.SendKeys('^s')                                                  # notepad.exe writes the file
    } else { Add-Ledger 'notes' 'warn_no_focus' $f }
    Dwell 5 20; Close-Windows 'notepad'
  }
  office = {
    if ($Rng.Next(2) -eq 0) {
      $f = Join-Path $WorkDir 'report.rtf'
      Invoke-Cmd "echo {\rtf1\ansi Weekly report draft.}>`"$f`"" 3
      $handler = if ((Get-OfficeInfo).Word) { 'winword' } else { 'wordpad' }
    } else {
      $f = Join-Path $WorkDir 'inventory.csv'
      Invoke-Cmd "(echo item,qty& echo laptop,3& echo monitor,5)>`"$f`"" 3
      $handler = if ((Get-OfficeInfo).Excel) { 'excel' } else { 'notepad' }
    }
    Invoke-App $f; Add-Ledger 'office' "open_via_$handler" $f              # file association picks the app
    Dwell 15 45; Close-Windows @('winword', 'wordpad', 'excel', 'notepad')
  }
  download = {
    $u = Pick $Downloads; $dst = Join-Path $WorkDir ('dl\' + [IO.Path]::GetFileName($u))
    Invoke-Cmd "mkdir `"$WorkDir\dl`" 2>nul & curl.exe -sSL -o `"$dst`" $u" 10
    Add-Ledger 'download' 'curl' $u
  }
  files = {
    $stamp = '{0:HHmmss}' -f (Get-Date)
    Invoke-Cmd ("cd /d `"$WorkDir`" & xcopy /y /q *.txt backup_$stamp\ >nul & " +
                "tar -a -c -f archive_$stamp.zip backup_$stamp & rmdir /s /q backup_$stamp") 8
    Add-Ledger 'files' 'copy_zip_cleanup' "archive_$stamp.zip"
  }
  netcheck = {
    Invoke-Cmd 'ipconfig /all >nul & ping -n 2 1.1.1.1 >nul & nslookup github.com >nul' 8
    Add-Ledger 'netcheck' 'ipconfig_ping_nslookup' ''
  }
  explore = {
    $d = Pick @($WorkDir, "$env:USERPROFILE\Downloads", "$env:USERPROFILE\Pictures")
    Invoke-App $d; Add-Ledger 'explore' 'open_folder' $d
    Dwell 5 20; Close-ExplorerWindows
  }
  paint = {
    Invoke-App 'mspaint.exe'; Add-Ledger 'paint' 'open' 'mspaint.exe'
    Dwell 10 30; Close-Windows 'mspaint'
  }
  user_powershell = {
    Invoke-App 'powershell.exe' "-NoProfile -Command Get-ChildItem '$WorkDir' -Recurse | Measure-Object" 0
    Add-Ledger 'user_powershell' 'dir_count' $WorkDir; Start-Sleep 5
  }
}

# Ordered list = weights (an ordered array, not a hashtable, so the pick sequence is deterministic).
$Weighted = @('browse','browse','browse','notes','notes','office','office','download',
              'files','netcheck','explore','paint')
if ($IncludeUserPowerShell) { $Weighted += 'user_powershell' }

# ---------------------------------------------------------------- self-test
if ($SelfTest) {
  Initialize-Launcher
  $since = Get-Date
  Invoke-App 'notepad.exe'; Start-Sleep 3
  $np = Get-Process notepad -ErrorAction SilentlyContinue | Where-Object { $_.StartTime -ge $since } | Select-Object -First 1
  $parent = if ($np) {
    $ppid = (Get-CimInstance Win32_Process -Filter "ProcessId=$($np.Id)").ParentProcessId
    (Get-Process -Id $ppid -ErrorAction SilentlyContinue).ProcessName } else { '<notepad not found>' }
  Close-Windows 'notepad'
  [pscustomobject]@{                     # returned as an object (07 reads it); the console shows it as a list
    LaunchMode       = $script:LaunchMode
    NotepadParent    = $parent                     # expect: explorer
    InteractiveSess  = (Get-Process -Id $PID).SessionId -ne 0
    WordInstalled    = (Get-OfficeInfo).Word
    ExcelInstalled   = (Get-OfficeInfo).Excel
    CurlPresent      = [bool](Get-Command curl.exe -ErrorAction SilentlyContinue)
    TarPresent       = [bool](Get-Command tar.exe  -ErrorAction SilentlyContinue)
  }
  return
}

# ---------------------------------------------------------------- main loop
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null   # before capture ideally; idempotent
Initialize-Launcher
$started  = (Get-Date).ToUniversalTime()
$deadline = (Get-Date).AddMinutes($DurationMinutes)
Write-Host "Benign workload: seed=$Seed, $DurationMinutes min, launcher=$($script:LaunchMode)"
try {
  while ((Get-Date) -lt $deadline) {
    $name = Pick $Weighted
    Write-Host ("{0:HH:mm:ss} {1}" -f (Get-Date), $name)
    try { & $Scenarios[$name] } catch { Add-Ledger $name 'error' $_.Exception.Message }
    Start-Sleep -Seconds $Rng.Next($MinGapSec, $MaxGapSec + 1)
  }
}
finally {   # runs even on Ctrl+C, so the ledger is never lost
  if ($BeforeLedger) { & $BeforeLedger }            # automated: stop the capture first (07)
  elseif ($PauseBeforeLedger) { Read-Host 'Run `etwctl stop` now, then press Enter to write the ledger' | Out-Null }
  New-Item -ItemType Directory -Force -Path $LedgerDir | Out-Null
  $base = Join-Path $LedgerDir ('benign_{0:yyyyMMddTHHmmssZ}' -f $started)
  $Ledger | Export-Csv "$base.csv" -NoTypeInformation
  [pscustomobject]@{ seed = $Seed; duration_min = $DurationMinutes; started_utc = $started.ToString('o')
                     launch_mode = $script:LaunchMode; office = Get-OfficeInfo
                     os_build = [Environment]::OSVersion.Version.ToString(); scenarios = ($Weighted | Select-Object -Unique)
  } | ConvertTo-Json -Depth 3 | Set-Content "$base.json"
  Write-Host "Ledger: $base.csv ($($Ledger.Count) actions)"
}