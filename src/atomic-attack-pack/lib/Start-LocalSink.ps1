<#
    Start-LocalSink.ps1 - throwaway HTTP sink for 03 (ingress), 06 (C2 beacon) and 08 (exfil). Not a real C2:
    it accepts and replies 200 OK so Kernel-Network logs a real, non-loopback connection.
    Run on the VM's own IP 10.21.2.10 (or any host on the host-only net); avoid 127.0.0.1, which kernel
    network ETW logs differently. TcpListener, so no netsh urlacl needed. Ctrl+C to stop.
#>
param(
    [string]$BindIP = "0.0.0.0",   # listen on all interfaces of this host
    [int]$Port = 8888
)

$addr = [System.Net.IPAddress]::Parse($BindIP)
$listener = [System.Net.Sockets.TcpListener]::new($addr, $Port)
$listener.Start()
Write-Host "[sink] listening on ${BindIP}:$Port  (Ctrl+C to stop)" -ForegroundColor Green

$resp = "HTTP/1.1 200 OK`r`nContent-Length: 2`r`nConnection: close`r`n`r`nok"
$respBytes = [System.Text.Encoding]::ASCII.GetBytes($resp)

try {
    while ($true) {
        $client = $listener.AcceptTcpClient()
        $stream = $client.GetStream()
        $buf = New-Object byte[] 1024
        try { $null = $stream.Read($buf, 0, $buf.Length) } catch {}
        $peer = $client.Client.RemoteEndPoint
        Write-Host ("[sink] {0:HH:mm:ss}  conn from {1}" -f (Get-Date), $peer)
        try { $stream.Write($respBytes, 0, $respBytes.Length) } catch {}
        $client.Close()
    }
} finally {
    $listener.Stop()
    Write-Host "[sink] stopped"
}
