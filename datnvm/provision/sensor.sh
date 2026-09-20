#!/usr/bin/env bash
# sensor.nt2102.ch — Zeek + Suricata + Python CICFlowMeter, also acts as the router
# between LAN / attacker "Internet" / C2 "Internet".
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

DATA_DIR=/vagrant/sensor-data

# --- IP forwarding (router role) --------------------------------------------
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-nt2102-forward.conf
sysctl --system

grep -q "nt2102.ch" /etc/hosts || cat >> /etc/hosts <<'EOF'
10.21.2.12    web-victim.nt2102.ch  web-victim
10.21.2.13    srv-victim.nt2102.ch  srv-victim
10.21.2.14    bot.nt2102.ch         bot
198.51.100.102 attacker.nt2102.ch   attacker
203.0.113.102  c2.nt2102.ch         c2
EOF

# --- Pre-seed postfix so zeek's dependency chain does not prompt -------------
echo "postfix postfix/main_mailer_type select Local only"          | debconf-set-selections
echo "postfix postfix/mailname string sensor.nt2102.ch"            | debconf-set-selections

# --- Packages ---------------------------------------------------------------
apt-get update -y
apt-get install -y --no-install-recommends software-properties-common gnupg lsb-release \
    curl wget git tcpdump tshark suricata jq iproute2 net-tools \
    python3 python3-pip

# Zeek (via OpenSUSE OBS repo — official binary packages route)
UBU_VER=$(lsb_release -rs)  # "22.04"
echo "deb http://download.opensuse.org/repositories/security:/zeek/xUbuntu_${UBU_VER}/ /" \
    > /etc/apt/sources.list.d/security-zeek.list
curl -fsSL "https://download.opensuse.org/repositories/security:zeek/xUbuntu_${UBU_VER}/Release.key" \
    | gpg --dearmor > /etc/apt/trusted.gpg.d/security_zeek.gpg
apt-get update -y
apt-get install -y --no-install-recommends zeek

if ! grep -q '/opt/zeek/bin' /etc/profile.d/zeek.sh 2>/dev/null; then
    echo 'export PATH=$PATH:/opt/zeek/bin' > /etc/profile.d/zeek.sh
fi

# --- CICFlowMeter (Python port — proper CLI, no jnetpcap headaches) ---------
pip3 install --no-cache-dir cicflowmeter

# --- Suricata: leave stock config; user can pin an interface via
#     /etc/suricata/suricata.yaml (af-packet section) if desired.
systemctl enable --now suricata || true

# --- Working dirs in the shared Vagrant project folder ----------------------
# This path is configured by Vagrant as the host's datnvm directory. Do not
# chown it here: VirtualBox shared folders do not support ownership changes.
mkdir -p "$DATA_DIR"/{captures,flows,zeek-logs}
ln -snf "$DATA_DIR/captures"  /home/vagrant/captures
ln -snf "$DATA_DIR/flows"     /home/vagrant/flows
ln -snf "$DATA_DIR/zeek-logs" /home/vagrant/zeek-logs
chown -h vagrant:vagrant /home/vagrant/captures /home/vagrant/flows /home/vagrant/zeek-logs

# --- Helper scripts ---------------------------------------------------------
LAN_IF=$(ip -o -4 addr show | awk '/10\.21\.2\.11/ {print $2; exit}')

cat > /home/vagrant/capture.sh <<EOF
#!/usr/bin/env bash
# Rotating packet capture on the LAN interface, 100 MB per file, keep last 20.
# Output lands in the shared Vagrant project folder.
sudo tcpdump -i "${LAN_IF:-enp0s8}" -C 100 -W 20 -w "$DATA_DIR/captures/lab.pcap"
EOF

cat > /home/vagrant/run-zeek.sh <<EOF
#!/usr/bin/env bash
# Run Zeek offline over a captured PCAP; logs land in the shared Vagrant project folder.
cd "$DATA_DIR/zeek-logs"
/opt/zeek/bin/zeek -r "\$1"
EOF

cat > /home/vagrant/run-cicflow.sh <<EOF
#!/usr/bin/env bash
# Convert a PCAP into CICFlowMeter's 80-feature CSV (offline mode, Python port).
# Usage: ./run-cicflow.sh /vagrant/sensor-data/captures/lab.pcap0
in="\$1"
out="$DATA_DIR/flows/\$(basename "\$in" .pcap).csv"
cicflowmeter -f "\$in" -c "\$out"
echo "wrote: \$out"
EOF

cat > /home/vagrant/run-cicflow-live.sh <<EOF
#!/usr/bin/env bash
# Live CICFlowMeter feature extraction on the LAN interface, streaming to CSV.
cicflowmeter -i "${LAN_IF:-enp0s8}" -c "$DATA_DIR/flows/live.csv"
EOF

chmod +x /home/vagrant/*.sh
chown vagrant:vagrant /home/vagrant/*.sh

echo "sensor.nt2102.ch provisioning done."
echo "  LAN interface (10.21.2.11): ${LAN_IF:-<unknown>}"
echo "  Captures / flows:  $DATA_DIR/{captures,flows,zeek-logs}"
echo "  Zeek:              /opt/zeek/bin/zeek"
echo "  Suricata service:  $(systemctl is-active suricata 2>/dev/null || echo 'not running')"
echo "  CICFlowMeter:      cicflowmeter (Python port)"
iptables -t nat -A POSTROUTING -s 10.21.2.14/24 -j MASQUERADE
apt install iptables-persistent -y
netfilter-persistent save
if [ -f /var/run/reboot-required ]; then
    echo "  Reboot:            required"
fi
