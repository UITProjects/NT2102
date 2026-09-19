#!/usr/bin/env bash
# c2.nt2102.ch — minimal fake C2 server. Serves a tiny HTTP endpoint the bot pings.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# shellcheck source=/vagrant/provision/_lib.sh
source /vagrant/provision/_lib.sh

mount_data_disk /data

# Reverse route back to the LAN so TCP responses find their way home.
install_lab_routes \
    "10.21.2.0/24 via 203.0.113.11" \
    "198.51.100.0/24 via 203.0.113.11"

apt-get update -y
apt-get install -y --no-install-recommends python3 curl

mkdir -p /opt/c2
cat > /opt/c2/server.py <<'PY'
#!/usr/bin/env python3
# Tiny fake C2: logs every beacon and returns a canned command.
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

class C2Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ts = datetime.now(timezone.utc).isoformat()
        print(f"[{ts}] beacon from {self.client_address[0]} path={self.path}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"sleep\n")

    def log_message(self, *args, **kwargs):
        pass  # we do our own logging above

if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), C2Handler).serve_forever()
PY
chmod +x /opt/c2/server.py

cat > /etc/systemd/system/c2.service <<'UNIT'
[Unit]
Description=NT2102 fake C2 server
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/c2/server.py
Restart=always
StandardOutput=append:/data/c2.log
StandardError=append:/data/c2.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now c2.service

echo "c2.nt2102.ch provisioning done. Beacon log: /data/c2.log"
