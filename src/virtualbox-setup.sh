#!/usr/bin/env bash
# One-time Linux host preparation for the w10-etw VirtualBox lab.
set -euo pipefail

# 1. VirtualBox >= 6.1.28 only allows host-only ranges listed in /etc/vbox/networks.conf
#    (default 192.168.56.0/21), so 10.21.2.0/24 must be whitelisted.
sudo mkdir -p /etc/vbox
if ! grep -qs '10.21.2.0/24' /etc/vbox/networks.conf; then
  echo '* 10.21.2.0/24' | sudo tee -a /etc/vbox/networks.conf
fi
echo "networks.conf:"; cat /etc/vbox/networks.conf

# 2. KVM and VirtualBox fight over AMD-V/VT-x (error VERR_SVM_IN_USE / VERR_VMX_IN_VMX_ROOT_MODE).
if lsmod | grep -qE '^kvm_(amd|intel)'; then
  echo "WARNING: KVM modules loaded. Shut down libvirt guests, then:"
  echo "  sudo systemctl stop libvirtd && sudo modprobe -r kvm_amd kvm   # (kvm_intel on Intel)"
fi

mkdir -p out
vagrant --version
VBoxManage --version

# 3. OPTIONAL (after 'vagrant up'): DHCP pool .128-.254 on the host-only interface Vagrant created.
#    Find the interface:  VBoxManage list hostonlyifs | grep -B3 10.21.2.1
#    VBoxManage dhcpserver add --interface=vboxnetN --server-ip=10.21.2.2 --netmask=255.255.255.0 \
#      --lower-ip=10.21.2.128 --upper-ip=10.21.2.254 --enable