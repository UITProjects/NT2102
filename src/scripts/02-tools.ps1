#Requires -RunAsAdministrator
<#
  Tools provisioner for the ETW collection VM (OCR-APT reproduction lab).

  Installs, in order:
    1. SilkETW (pinned release) + the VC++ runtime it needs (from the release's Dependencies folder)
    2. Invoke-AtomicRedTeam module (pinned PSGallery version) + atomics (pinned commit)
    3. Manifest dumps of the three kernel ETW providers (input to the event-ID -> edge-type table)
  and writes C:\Tools\lab-versions.json recording exactly what is installed.

  Safe to re-run: every step checks current state first and skips if already done.
  Needs internet (NAT adapter on). Runs no attacks.

  PINNING WORKFLOW
    First run with empty pins: the script resolves "latest", installs it, and prints a WARNING per
    unpinned item. Copy the values from lab-versions.json into the param defaults below, commit the
    script, and from then on every rebuild (yours or a teammate's) installs identical bits.

  Manual run (outside Vagrant):
    powershell -ExecutionPolicy Bypass -File .\tools.ps1
#>
param(
    [string]$SilkTag          = "v0.8",  # SilkETW GitHub release tag
    [string]$SilkZipSha256    = "",      # pin after first run (from lab-versions.json)
    [string]$InvokeArtVersion = "",      # pin after first run, PSGallery version of invoke-atomicredteam
    [string]$AtomicsRef       = ""       # pin after first run, full commit SHA of redcanaryco/atomic-red-team
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ToolsDir     = "C:\Tools"
$SilkDir      = "$ToolsDir\SilkETW"
$ArtDir       = "C:\AtomicRedTeam"          # must be a Defender exclusion (base.ps1) before atomics land here
$DumpDir      = "$ToolsDir\provider-dumps"
$VersionsFile = "$ToolsDir\lab-versions.json"
$GhHeaders    = @{ "User-Agent" = "w10-etw-lab" }
$versions     = [ordered]@{ generated_utc = [DateTime]::UtcNow.ToString("o") }
$rebootNeeded = $false

foreach ($d in $ToolsDir, $ArtDir, $DumpDir) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# ---------- helpers ----------
function Save-File([string]$Url, [string]$OutFile) {
    # Download to .part, then rename: an interrupted run never leaves a complete-looking file behind.
    if (Test-Path $OutFile) { Write-Host "[tools] cached: $OutFile"; return }
    Invoke-WebRequest $Url -OutFile "$OutFile.part" -UseBasicParsing
    Move-Item "$OutFile.part" $OutFile -Force
}

function Confirm-Hash([string]$Path, [string]$Expected, [string]$Label) {
    $h = (Get-FileHash $Path -Algorithm SHA256).Hash
    if ($Expected) {
        if ($h -ne $Expected.ToUpper()) {
            Remove-Item $Path -Force
            throw "[tools] $Label SHA256 mismatch: got $h, expected $Expected. File deleted; re-run."
        }
    } else {
        Write-Warning "[tools] $Label not pinned. SHA256=$h -> set it in the param defaults"
    }
    return $h
}

function Test-SilkEtw([string]$Exe) {
    # In Windows PowerShell 5.1, EAP=Stop turns native stderr into a terminating error; relax it locally.
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $out = & $Exe -h 2>&1 | Out-String
    $ErrorActionPreference = $old
    return ($out -notmatch "Unhandled Exception")
}

function Test-Marker([string]$Path, [string]$Value) {
    return ((Test-Path $Path) -and ((Get-Content $Path -Raw).Trim() -eq $Value))
}

# ---------- 0. Pre-flight ----------
$excl = (Get-MpPreference).ExclusionPath
if ($excl -notcontains $ArtDir -or $excl -notcontains $ToolsDir) {
    Write-Warning "[tools] $ArtDir / $ToolsDir not in Defender exclusions. Run base.ps1 first, or atomics may be silently quarantined."
}

# ---------- 1. SilkETW (pinned tag) ----------
$rel   = Invoke-RestMethod "https://api.github.com/repos/mandiant/SilkETW/releases/tags/$SilkTag" -Headers $GhHeaders
$asset = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
if (-not $asset) { throw "[tools] no .zip asset in SilkETW release $SilkTag" }

$silkZip  = "$ToolsDir\$($asset.name)"
Save-File $asset.browser_download_url $silkZip
$silkHash = Confirm-Hash $silkZip $SilkZipSha256 "SilkETW zip"

# Re-extract only if the installed tag differs (marker file), so a version change replaces old files cleanly.
$silkMarker = "$SilkDir\.tag"
if (-not (Test-Marker $silkMarker $SilkTag)) {
    if (Test-Path $SilkDir) { Remove-Item $SilkDir -Recurse -Force }
    Expand-Archive $silkZip -DestinationPath $SilkDir -Force
    Set-Content $silkMarker $SilkTag
    Write-Host "[tools] SilkETW $SilkTag extracted"
}

# Shallowest SilkETW.exe = the CLI (amd64/x86 subfolders hold native libs).
$exe = Get-ChildItem $SilkDir -Recurse -Filter "SilkETW.exe" |
       Sort-Object { $_.FullName.Length } | Select-Object -First 1
if (-not $exe) { throw "[tools] SilkETW.exe not found under $SilkDir" }

# 1b. VC++ runtime: install from the release's Dependencies folder only if SilkETW actually fails to start.
if (-not (Test-SilkEtw $exe.FullName)) {
    $deps = Get-ChildItem $SilkDir -Recurse -Directory -Filter "Dependencies" | Select-Object -First 1
    if (-not $deps) { throw "[tools] SilkETW fails to start and no Dependencies folder found" }
    foreach ($inst in Get-ChildItem $deps.FullName -Filter "vc*redist*.exe") {
        Write-Host "[tools] installing $($inst.Name)"
        $p = Start-Process $inst.FullName -ArgumentList "/install", "/quiet", "/norestart" -Wait -PassThru
        # 0 = ok, 1638 = same/newer already installed, 3010 = ok but reboot required
        if ($p.ExitCode -notin 0, 1638, 3010) { throw "[tools] $($inst.Name) failed, exit code $($p.ExitCode)" }
        if ($p.ExitCode -eq 3010) { $rebootNeeded = $true }
    }
    if (-not (Test-SilkEtw $exe.FullName)) {
        if ($rebootNeeded) { Write-Warning "[tools] SilkETW still fails; reboot, then re-run this script" }
        else { throw "[tools] SilkETW still fails after installing the VC++ runtime" }
    }
}
Write-Host "[tools] SilkETW OK -> $($exe.FullName)"

$vcRuntimes = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\*" -ErrorAction SilentlyContinue |
              ForEach-Object { "$($_.PSChildName)=$($_.Version)" }
$versions.silketw = [ordered]@{ tag = $SilkTag; asset = $asset.name; sha256 = $silkHash; exe = $exe.FullName }
$versions.vc_runtimes = @($vcRuntimes)

# ---------- 2. Invoke-AtomicRedTeam (pinned module) + atomics (pinned commit) ----------
# No Invoke-Expression of a script from 'master': install the module from PSGallery at a fixed version.
if (-not (Get-PackageProvider -ListAvailable -Name NuGet -ErrorAction SilentlyContinue |
          Where-Object { $_.Version -ge [version]"2.8.5.201" })) {
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
}
if ((Get-PSRepository -Name PSGallery).InstallationPolicy -ne "Trusted") {
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
}

if (-not $InvokeArtVersion) {
    $InvokeArtVersion = (Find-Module invoke-atomicredteam -Repository PSGallery).Version.ToString()
    Write-Warning "[tools] invoke-atomicredteam not pinned. Resolved $InvokeArtVersion -> set it in the param defaults"
}
if (-not (Get-Module -ListAvailable invoke-atomicredteam | Where-Object { $_.Version -eq [version]$InvokeArtVersion })) {
    Install-Module invoke-atomicredteam -RequiredVersion $InvokeArtVersion -Scope AllUsers -Force
    Write-Host "[tools] invoke-atomicredteam $InvokeArtVersion installed"
}
$yaml = Get-Module -ListAvailable powershell-yaml | Sort-Object Version -Descending | Select-Object -First 1

if (-not $AtomicsRef) {
    $AtomicsRef = (Invoke-RestMethod "https://api.github.com/repos/redcanaryco/atomic-red-team/commits/master" -Headers $GhHeaders).sha
    Write-Warning "[tools] atomics not pinned. Resolved master = $AtomicsRef -> set it in the param defaults"
}

$artMarker = "$ArtDir\atomics\.ref"
$artZip    = "$ArtDir\art-$($AtomicsRef.Substring(0,12)).zip"   # inside the Defender-excluded folder
if (-not (Test-Marker $artMarker $AtomicsRef)) {
    Save-File "https://github.com/redcanaryco/atomic-red-team/archive/$AtomicsRef.zip" $artZip
    # Extract only the atomics/ subtree with the built-in tar (much faster than Expand-Archive on a big repo).
    $tmp = "$ArtDir\_x"                                           # short path: avoids MAX_PATH issues
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    tar -xf $artZip -C $tmp "atomic-red-team-$AtomicsRef/atomics"
    if ($LASTEXITCODE -ne 0) { throw "[tools] tar failed extracting atomics (exit $LASTEXITCODE)" }
    if (Test-Path "$ArtDir\atomics") { Remove-Item "$ArtDir\atomics" -Recurse -Force }
    Move-Item "$tmp\atomic-red-team-$AtomicsRef\atomics" "$ArtDir\atomics"
    Remove-Item $tmp -Recurse -Force
    Set-Content $artMarker $AtomicsRef
    Write-Host "[tools] atomics @ $AtomicsRef installed in $ArtDir\atomics"
}

# Smoke test: list (not run) one technique's tests. Fails if module or atomics are broken.
Import-Module invoke-atomicredteam -RequiredVersion $InvokeArtVersion -Force
Invoke-AtomicTest T1082 -ShowDetailsBrief -PathToAtomicsFolder "$ArtDir\atomics" | Out-Null
Write-Host "[tools] Invoke-AtomicRedTeam OK (no attacks executed)"

# GitHub archive zips are generated on demand, so their hash is not guaranteed stable: recorded, not enforced.
# The commit SHA is the real pin.
$versions.invoke_atomicredteam = $InvokeArtVersion
$versions.powershell_yaml      = if ($yaml) { $yaml.Version.ToString() } else { $null }
$versions.atomics = [ordered]@{
    commit = $AtomicsRef
    zip_sha256_informational = if (Test-Path $artZip) { (Get-FileHash $artZip -Algorithm SHA256).Hash } else { $null }
}

# ---------- 3. Kernel provider manifest dumps (always regenerated; overwrites) ----------
foreach ($p in "Microsoft-Windows-Kernel-Process", "Microsoft-Windows-Kernel-File", "Microsoft-Windows-Kernel-Network") {
    logman query providers $p | Out-File "$DumpDir\keywords-$p.txt" -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "[tools] logman failed for $p" }
    wevtutil gp $p /ge /gm | Out-File "$DumpDir\events-$p.txt" -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "[tools] wevtutil failed for $p" }
}
Write-Host "[tools] provider dumps -> $DumpDir"

# ---------- 4. Version manifest ----------
# Event IDs/fields can differ between Windows builds, so the build is part of the dataset's identity.
$nt = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
$versions.os = [ordered]@{
    product = $nt.ProductName; display_version = $nt.DisplayVersion
    build = "$($nt.CurrentBuild).$($nt.UBR)"; powershell = $PSVersionTable.PSVersion.ToString()
}
$versions | ConvertTo-Json -Depth 5 | Out-File $VersionsFile -Encoding utf8
Write-Host "[tools] versions -> $VersionsFile"

if (Test-Path "C:\lab-out") {
    Copy-Item "$DumpDir\*", $VersionsFile "C:\lab-out\" -Force
    Write-Host "[tools] provider dumps + lab-versions.json copied to host ./out"
}
if ($rebootNeeded) { Write-Warning "[tools] VC++ runtime requested a reboot: vagrant reload" }
Write-Host "[tools] done"