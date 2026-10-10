<#
    Start-LocalSink.ps1  —  a throwaway HTTP sink for the C2 beacon (06) and exfil (08).

    WHY: so the "C2 server" is a real, non-loopback endpoint you control and your
    SilkETW Kernel-Network provider logs the outbound connections. This is NOT a real
    C2 — it just accepts the connection and replies 200 OK so the beacon completes.

    WHERE TO RUN: on any host the VM can reach on the host-only network. Running it
    inside the SAME VM, bound to the VM's own IP (10.21.2.10), is fine and keeps the
    lab self-contained. Loopback (127.0.0.1) is NOT recommended — kernel network ETW
    often does not log loopback the way it logs real NIC traffic.

    Uses TcpListener (not HttpListener) to avoid needing a netsh urlacl reservation.
    Ctrl+C to stop.
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
