#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Vagrant provisioner "workload": install + autostart benign server software on the
  ETW lab VM, approximating the benign process profile of NODLINK SimulatedW10.
.NOTES
  Idempotent (safe to re-run). Installs only: for benign activity use 06-benign-workload.ps1.
  Core   : Apache httpd (httpd.exe), PostgreSQL 16 (postgres.exe)  -> always installed
  -Extras: Chrome (chrome.exe + GoogleUpdate*), Temurin 17 JRE (java.exe), Everything
  Skipped on purpose: Windows built-ins (explorer, svchost, csrss, dwm, ...) already exist;
  devenv/msvsmon (Visual Studio, ~20 GB) and phpstudy are out of scope.
  Resolved versions are merged into $VersionsFile under key "workload".
#>
param(
  [string]$PgPassword   = 'labpg-ChangeMe!',
  [string]$LabSubnet    = '10.21.2.0/24',
  [string]$VersionsFile = 'C:\lab-out\lab-versions.json',
  [string]$LabelFile    = 'C:\lab-labels\events.jsonl',
  [switch]$Extras
)
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# ---- Version pins --------------------------------------------------------------
# Empty Version = latest on first build. After the first build, copy the resolved
# versions from lab-versions.json back into this table to freeze the VM.
$Core = [ordered]@{
  'apache-httpd' = @{ Version = ''; Params = '/installLocation:C:\Apache24 /port:80' }
  'postgresql16' = @{ Version = ''; Params = "/Password:$PgPassword /Port:5432" }
}
$Optional = [ordered]@{
  # Google always serves the latest MSI, so the choco checksum often mismatches.
  'googlechrome' = @{ Version = ''; Params = ''; Extra = '--ignore-checksums' }
  'temurin17jre' = @{ Version = ''; Params = '' }
  'everything'   = @{ Version = ''; Params = '' }
}

# ---- Helpers -------------------------------------------------------------------
function Ensure-Choco {
  if (Get-Command choco.exe -ErrorAction SilentlyContinue) { return }
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
  Invoke-Expression ((New-Object Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
  $env:Path += ";$env:ProgramData\chocolatey\bin"
}

function Get-ChocoVersion([string]$Name) {
  # choco v2: 'list' is local-only; --limit-output prints "name|version"
  $line = choco list --exact $Name --limit-output 2>$null | Select-Object -First 1
  if ($line) { return ($line -split '\|')[1] }
  return $null
}

$script:Changed = $false   # set when this run actually changed the VM

# Native exes don't throw under $ErrorActionPreference='Stop' in PS 5.1 -> check explicitly
function Assert-Exit([string]$What) {
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

function Install-Pkg([string]$Name, [hashtable]$Spec) {
  $have = Get-ChocoVersion $Name
  # Skip only if present AND (unpinned OR already at the pinned version)
  if ($have -and (-not $Spec.Version -or $have -eq $Spec.Version)) {
    Write-Host "[skip]    $Name $have already installed"; return
  }
  # Missing -> install; present at wrong version -> converge (up or down) to the pin
  $verb = if ($have) { 'upgrade' } else { 'install' }
  $a = @($verb, $Name, '-y', '--no-progress')
  if ($Spec.Version) { $a += @('--version', $Spec.Version, '--allow-downgrade') }
  if ($Spec.Params)  { $a += @('--params', "`"$($Spec.Params)`"") }
  if ($Spec.Extra)   { $a += $Spec.Extra }
  Write-Host "[$verb] $Name $have -> $($Spec.Version)"   # don't echo params (password)
  & choco @a
  if ($LASTEXITCODE -notin 0, 1641, 3010) { throw "choco $verb $Name failed (exit $LASTEXITCODE)" }
  $script:Changed = $true
}

function Get-ServiceByBinary([string]$ExeName) {
  Get-CimInstance Win32_Service |
    Where-Object { $_.PathName -match [regex]::Escape($ExeName) } |
    Select-Object -First 1
}

function Ensure-Running($Svc) {
  Set-Service -Name $Svc.Name -StartupType Automatic
  if ((Get-Service $Svc.Name).Status -ne 'Running') { Start-Service $Svc.Name }
}

# ---- Install -------------------------------------------------------------------
Ensure-Choco
choco feature enable -n allowGlobalConfirmation | Out-Null
foreach ($k in $Core.Keys) { Install-Pkg $k $Core[$k] }
if ($Extras) { foreach ($k in $Optional.Keys) { Install-Pkg $k $Optional[$k] } }

# ---- Apache httpd --------------------------------------------------------------
$apache = Get-ServiceByBinary 'httpd.exe'
if (-not $apache) { throw 'Apache service not found - check apache-httpd params with: choco info apache-httpd' }
$httpdExe   = $apache.PathName -replace '^"?([^"]*httpd\.exe).*$', '$1'
$apacheRoot = Split-Path (Split-Path $httpdExe)
$htdocs     = Join-Path $apacheRoot 'htdocs'

# Static content of mixed sizes, so each request causes real file reads in ETW
New-Item -ItemType Directory -Force "$htdocs\assets" | Out-Null
foreach ($kb in 4, 64, 512, 2048) {
  $f = "$htdocs\assets\blob_$kb.bin"
  if (-not (Test-Path $f)) {
    $bytes = New-Object byte[] ($kb * 1KB)
    (New-Object Random 42).NextBytes($bytes)
    [IO.File]::WriteAllBytes($f, $bytes)
  }
}
foreach ($p in 'index', 'about', 'products', 'contact') {
  $f = "$htdocs\$p.html"
  if (-not (Test-Path $f)) {
    Set-Content $f "<html><body><h1>$p</h1><a href='assets/blob_64.bin'>dl</a></body></html>" -Encoding ASCII
  }
}
Ensure-Running $apache
Write-Host "[ok]      httpd service '$($apache.Name)' root=$apacheRoot"

# ---- PostgreSQL ----------------------------------------------------------------
$pg = Get-ServiceByBinary 'pg_ctl.exe'
if (-not $pg) { throw 'PostgreSQL service not found' }
$pgBin  = Split-Path ($pg.PathName -replace '^"?([^"]*pg_ctl\.exe).*$', '$1')
$pgData = [regex]::Match($pg.PathName, '-D\s+"([^"]+)"').Groups[1].Value
$psql   = Join-Path $pgBin 'psql.exe'

# pg_hba: one lab-managed line, rewritten if -LabSubnet changes (no stale rules piling up)
$pgChanged = $false
$hba     = Join-Path $pgData 'pg_hba.conf'
$hbaWant = "host    all    all    $LabSubnet    scram-sha-256    # lab-managed"
$hbaOld  = @(Get-Content $hba)
$hbaNew  = @($hbaOld | Where-Object {
             $_ -notmatch '# lab-managed$' -and
             $_ -ne "host    all    all    $LabSubnet    scram-sha-256" }) + $hbaWant   # 2nd clause: also drops the unmarked line older runs wrote
if (($hbaOld -join "`n") -ne ($hbaNew -join "`n")) { Set-Content $hba $hbaNew -Encoding ASCII; $pgChanged = $true }

$conf = Join-Path $pgData 'postgresql.conf'
if (-not (Select-String -Path $conf -Pattern "^\s*listen_addresses\s*=\s*'\*'" -Quiet)) {
  Add-Content $conf "listen_addresses = '*'"; $pgChanged = $true
}
Ensure-Running $pg
if ($pgChanged) { Restart-Service $pg.Name; $script:Changed = $true }   # restart only when config changed

$env:PGPASSWORD = $PgPassword
try {
  $exists = & $psql -U postgres -h 127.0.0.1 -tAc "SELECT 1 FROM pg_database WHERE datname='labapp'"
  Assert-Exit 'psql connect (wrong -PgPassword? choco does not change the password of an existing install)'
  if ("$exists".Trim() -ne '1') {
    & $psql -U postgres -h 127.0.0.1 -c 'CREATE DATABASE labapp'; Assert-Exit 'CREATE DATABASE'
    $script:Changed = $true
  }
  & $psql -U postgres -h 127.0.0.1 -d labapp -c `
    'CREATE TABLE IF NOT EXISTS orders(id bigserial PRIMARY KEY, sku text, qty int, ts timestamptz DEFAULT now());'
  Assert-Exit 'CREATE TABLE'
} finally {
  Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "[ok]      postgres service '$($pg.Name)' data=$pgData db=labapp"

# ---- Firewall: only the lab subnet may reach the servers ------------------------
foreach ($r in @(@{ N = 'lab-httpd'; P = 80 }, @{ N = 'lab-postgres'; P = 5432 })) {
  $rule = Get-NetFirewallRule -Name $r.N -ErrorAction SilentlyContinue
  if (-not $rule) {
    New-NetFirewallRule -Name $r.N -DisplayName $r.N -Direction Inbound -Protocol TCP `
      -LocalPort $r.P -RemoteAddress $LabSubnet -Action Allow | Out-Null
    $script:Changed = $true
  } else {
    # Re-apply every run: idempotent, and follows -LabSubnet changes.
    # (Windows stores "/24" as "/255.255.255.0", so a string compare would always differ.)
    $rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $LabSubnet
  }
}

# ---- Extras: autostart + freeze updates ------------------------------------------
if ($Extras) {
  # Keep GoogleUpdate running (it is in the profile) but block real updates so the pin holds
  $gu = 'HKLM:\SOFTWARE\Policies\Google\Update'
  if (-not (Test-Path $gu)) { New-Item $gu -Force | Out-Null }
  Set-ItemProperty $gu -Name UpdateDefault -Value 0 -Type DWord

  # Everything is a user-session app in the profile -> start at every interactive logon
  $ev = 'C:\Program Files\Everything\Everything.exe'
  if (Test-Path $ev) {
    Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name Everything -Value "`"$ev`" -startup"
  }
}

# ---- Record versions (merge, never drop entries) ----------------------------------
$names = @($Core.Keys)
if ($Extras) { $names += @($Optional.Keys) }
New-Item -ItemType Directory -Force (Split-Path $VersionsFile) | Out-Null
$doc  = if (Test-Path $VersionsFile) { Get-Content $VersionsFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
$prev = if ($doc.PSObject.Properties['workload']) { $doc.workload } else { [pscustomobject]@{} }
$resolved = [ordered]@{}
foreach ($n in $names) {
  $resolved[$n] = Get-ChocoVersion $n
  $prev | Add-Member -NotePropertyName $n -NotePropertyValue $resolved[$n] -Force
}
$doc | Add-Member -NotePropertyName workload -NotePropertyValue $prev -Force
# UTF-8 *without* BOM: PS 5.1 '-Encoding UTF8' writes a BOM that breaks Python json.load
[IO.File]::WriteAllText($VersionsFile, ($doc | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding $false))

# ---- Label: only when this run actually changed the VM ---------------------------
if ($script:Changed) {
  New-Item -ItemType Directory -Force (Split-Path $LabelFile) | Out-Null
  $label = [ordered]@{
    ts_utc   = (Get-Date).ToUniversalTime().ToString('o')
    event    = 'workload-provisioned'
    class    = 'benign'
    packages = $resolved
  }
  Add-Content $LabelFile ($label | ConvertTo-Json -Compress -Depth 4)
} else {
  Write-Host '[noop]    no changes; label not written'
}

Get-Process httpd, postgres -ErrorAction SilentlyContinue |
  Group-Object ProcessName | ForEach-Object { Write-Host "[run]     $($_.Name) x$($_.Count)" }