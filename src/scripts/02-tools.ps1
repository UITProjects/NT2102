# Installs SilkETW + Invoke-AtomicRedTeam and dumps ETW provider manifests. Needs internet (NAT on).
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# 1. SilkETW: resolve the latest release asset via the GitHub API (no hard-coded version)
$rel   = Invoke-RestMethod "https://api.github.com/repos/mandiant/SilkETW/releases/latest" -Headers @{ "User-Agent" = "w10-etw-lab" }
$asset = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
if (-not $asset) { throw "[tools] no .zip asset in SilkETW release $($rel.tag_name)" }
$zip = "C:\Tools\$($asset.name)"
Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
Expand-Archive $zip -DestinationPath "C:\Tools\SilkETW" -Force
$exe = Get-ChildItem "C:\Tools\SilkETW" -Recurse -Filter "SilkETW.exe" | Select-Object -First 1
Write-Host "[tools] SilkETW $($rel.tag_name) -> $($exe.FullName)"

# 2. Invoke-AtomicRedTeam + atomics (attack generation; do NOT run attacks yet)
Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
Invoke-Expression (Invoke-WebRequest "https://raw.githubusercontent.com/redcanaryco/invoke-atomicredteam/master/install-atomicredteam.ps1" -UseBasicParsing)
Install-AtomicRedTeam -InstallPath "C:\AtomicRedTeam" -getAtomics -Force
Write-Host "[tools] Invoke-AtomicRedTeam installed in C:\AtomicRedTeam"

# 3. Dump keywords + event IDs of the three kernel providers (verifies the edge-type mapping table)
$dump = "C:\Tools\provider-dumps"
New-Item -ItemType Directory -Force -Path $dump | Out-Null
foreach ($p in "Microsoft-Windows-Kernel-Process", "Microsoft-Windows-Kernel-File", "Microsoft-Windows-Kernel-Network") {
    logman query providers $p | Out-File "$dump\keywords-$p.txt" -Encoding utf8
    wevtutil gp $p /ge /gm   | Out-File "$dump\events-$p.txt"   -Encoding utf8
}
if (Test-Path "C:\lab-out") { Copy-Item "$dump\*" "C:\lab-out\" -Force; Write-Host "[tools] provider dumps copied to host ./out" }