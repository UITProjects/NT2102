#!/usr/bin/env bash
# srv-victim.nt2102.ch — OpenSSH + vsftpd + MySQL, weak creds so brute-force succeeds
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
10.21.2.14    bot.nt2102.ch         bot
198.51.100.102 attacker.nt2102.ch   attacker
203.0.113.102  c2.nt2102.ch         c2
EOF

apt-get update -y
apt-get install -y --no-install-recommends openssh-server vsftpd mariadb-server

if ! id -u lab >/dev/null 2>&1; then
    useradd -m -s /bin/bash lab
    echo 'lab:letmein' | chpasswd
fi

sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin no/'                 /etc/ssh/sshd_config
systemctl enable --now ssh

sed -i 's/^#*\s*write_enable=.*/write_enable=YES/' /etc/vsftpd.conf
sed -i 's/^#*\s*local_enable=.*/local_enable=YES/' /etc/vsftpd.conf
systemctl enable --now vsftpd

sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' /etc/mysql/mariadb.conf.d/50-server.cnf
systemctl enable --now mariadb
systemctl restart mariadb

echo "srv-victim.nt2102.ch provisioning done. Weak login: lab / letmein"
