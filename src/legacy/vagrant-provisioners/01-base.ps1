# Base OS configuration for the ETW collection VM. Safe to re-run.
param([string]$LabIp = "10.21.2.10")
$ErrorActionPreference = "Stop"
Write-Host "[base] lab IP = $LabIp"

# 1. Host-only adapter -> Private profile (Public profile restricts WinRM/RDP firewall rules)
$ip = Get-NetIPAddress -IPAddress $LabIp -ErrorAction SilentlyContinue
if ($ip) {
    Set-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -NetworkCategory Private
    Write-Host "[base] interface $($ip.InterfaceAlias) set to Private"
} else {
    Write-Warning "[base] $LabIp not found; run 'ipconfig' and check the host-only adapter"
}

# 2. UTC clock: ETW timestamps must line up with the attack/label log you keep on the host
Set-TimeZone -Id "UTC"
w32tm /resync /force 2>$null | Out-Null

# 3. Never sleep during a capture
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off

# 4. RDP for interactive benign activity (allowed on the Private profile)
Set-ItemProperty "HKLM:\System\CurrentControlSet\Control\Terminal Server" -Name fDenyTSConnections -Value 0
Enable-NetFirewallRule -DisplayGroup "Remote Desktop"

# 5. Lab folders (C:\etw is unused now: captures go to C:\lab-out\etw, see 04-collector)
"C:\Tools", "C:\etw", "C:\lab-labels" | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }

# 6. Freeze Windows Update so captures stay comparable
Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
Set-Service wuauserv -StartupType Disabled

# 7. Defender: exclusions + real-time off.
#    With Tamper Protection ON these calls do not throw, they are silently ignored.
#    Turn Tamper Protection off in the Windows Security GUI, then re-run this script.
Add-MpPreference -ExclusionPath "C:\Tools", "C:\AtomicRedTeam", "C:\etw"
Set-MpPreference -DisableRealtimeMonitoring $true
$s = Get-MpComputerStatus
Write-Host "[base] Defender RealTimeProtectionEnabled=$($s.RealTimeProtectionEnabled) IsTamperProtected=$($s.IsTamperProtected)"
if ($s.RealTimeProtectionEnabled) { Write-Warning "[base] Real-time protection still ON -> disable Tamper Protection, then: vagrant provision --provision-with base" }