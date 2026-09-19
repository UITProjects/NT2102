#!/usr/bin/env bash
# web-victim.nt2102.ch — Apache + PHP + MySQL + DVWA
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
10.21.2.13    srv-victim.nt2102.ch  srv-victim
10.21.2.14    bot.nt2102.ch         bot
198.51.100.102 attacker.nt2102.ch   attacker
203.0.113.102  c2.nt2102.ch         c2
EOF

apt-get update -y
apt-get install -y --no-install-recommends \
    apache2 mariadb-server php php-mysql php-gd libapache2-mod-php \
    git curl

systemctl enable --now mariadb apache2

if [ ! -d /var/www/html/dvwa ]; then
    git clone --depth=1 https://github.com/digininja/DVWA.git /var/www/html/dvwa
    chown -R www-data:www-data /var/www/html/dvwa
    chmod -R 755 /var/www/html/dvwa
    cp /var/www/html/dvwa/config/config.inc.php.dist /var/www/html/dvwa/config/config.inc.php
    sed -i "s/p@ssw0rd/dvwa_pw/" /var/www/html/dvwa/config/config.inc.php
fi

mysql <<'SQL'
CREATE DATABASE IF NOT EXISTS dvwa;
CREATE USER IF NOT EXISTS 'dvwa'@'localhost' IDENTIFIED BY 'dvwa_pw';
GRANT ALL PRIVILEGES ON dvwa.* TO 'dvwa'@'localhost';
FLUSH PRIVILEGES;
SQL

PHP_INI=$(php -i | awk -F"=> " '/Loaded Configuration File/ {print $2}' | head -n1)
if [ -n "$PHP_INI" ] && [ -f "$PHP_INI" ]; then
    sed -i 's/^allow_url_include.*/allow_url_include = On/' "$PHP_INI"
    sed -i 's/^allow_url_fopen.*/allow_url_fopen = On/'     "$PHP_INI"
fi

systemctl restart apache2

echo "web-victim.nt2102.ch provisioning done."
echo "From the attacker VM: http://10.21.2.12/dvwa/setup.php then set security = Low."
