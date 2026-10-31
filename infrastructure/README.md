# infrastructure/: Proxmox VE Windows lab VM (Terraform)

Creates on one Proxmox node:

- SDN Simple zone `nt2102` → VNet **`it2102`** → subnet `10.21.3.0/24` (gateway `10.21.3.1` on the node,
  SNAT for internet). Separate from the Vagrant host-only lab `10.21.2.0/24` (`src/Vagrantfile`).
- The Windows Server 2022 ISO, downloaded by PVE from `iso_url`.
- One VM (`ws-etw` by default): q35, UEFI (OVMF, Secure Boot keys), TPM 2.0, SATA disk, e1000e NIC on
  `it2102`, booting the ISO. **Install Windows by hand from the console.**

Provider: `bpg/proxmox` pinned to `0.119.0`. SDN and ISO use the current `proxmox_sdn_*` /
`proxmox_download_file` names (the `proxmox_virtual_environment_*` ones are deprecated there); the VM stays
`proxmox_virtual_environment_vm` (not deprecated; its successor `proxmox_vm` is experimental).

## Credentials (the only variables, `variables.tf`)

All lab settings are hardcoded in the `locals` block of `main.tf`. The provider logs in as `root@pam`
(`versions.tf`, like `~/code/home-lab/tf/proxmox`) and uses the node SSH key
`~/OneDrive/credentials/ssh/proxmox/id_ed25519` via the SSH agent. Only the endpoint and password are
variables, with no values in git:

```bash
export TF_VAR_proxmox_endpoint="https://<pve-host>:8006/"
export TF_VAR_proxmox_root_password='<root password>'
```

## State

`terraform.tfstate` is committed to git (user, 2026-10-31): no secrets in it (no password/token/private key),
but it does hold the lab topology, MACs and the it2102-vms public key, and the repo is public. Commit it
after every apply, and never run apply from two machines at once (git state has no locking).

## Use

```bash
# set local.iso_url in main.tf first (placeholder)
terraform init
terraform plan
terraform apply
```

## Install notes

- The disk is empty, so the firmware falls through to the CD. In the noVNC console, **press a key at
  "Press any key to boot from CD or DVD"** or setup is skipped.
- No VirtIO drivers are needed (SATA + e1000e). The VM resource allows only one CD-ROM, so a virtio-win ISO can't
  be attached as well. After install, you can switch to VirtIO with drivers installed (`disk_interface`, `nic_model`).
- No DHCP on `it2102`. Set a static IP in Windows: `10.21.3.10/24`, gateway `10.21.3.1`, DNS e.g. `1.1.1.1`
  (output `suggested_static_ip`).
- The SDN gateway lives on the Proxmox node, not your laptop. To reach the VM from elsewhere, route
  `10.21.3.0/24` via the node or use the console.

## Firewall (VM 2102, Proxmox VM-level)

The rules sit on the VM's NIC (`network_device.firewall = true`). They work only while the **datacenter
firewall stays enabled**: it is enabled on `pve-master` (`policy_in: ACCEPT`) and is deliberately not
managed here, because the home-lab VMs share it. First match wins:

1. OUT DROP → `192.168.1.0/24` (home LAN), logged
2. OUT DROP → `10.21.3.1` (the node itself via the VNet gateway: UI :8006, SSH :22), logged. Routing to
   the internet through the gateway still works.
3. IN ACCEPT ← `10.21.3.0/24` (lab subnet: RDP, DVWA)
4. Defaults: input DROP, output ACCEPT. Replies to the VM's own connections pass via connection tracking.

Check from PowerShell inside the VM:

- `Test-NetConnection 192.168.1.1 -Port 80` → `TcpTestSucceeded : False` (LAN blocked)
- `Test-NetConnection 10.21.3.1 -Port 8006` → `False` (node UI blocked)
- `Test-NetConnection 1.1.1.1 -Port 443` → `True`, and `Resolve-DnsName microsoft.com` resolves (internet + DNS)

Drops appear in the Proxmox UI under VM 2102 → Firewall → Log.

## ubuntu-trainer (VM 2103)

Ubuntu 24.04 cloud image (pinned `noble/20261017`), 4 cores, 6 GB RAM, 200 GB disk (Loki keeps 72 h at
~2 GB/h), static `10.21.3.20/24` via cloud-init, user `it2102` with the `it2102-vms` key. It gets the same
`it2102:` firewall rules as ws-etw (`local.it2102_rules`): no LAN, no node, inbound only from the lab subnet.

Until the VPN exists, reach it by jumping through the node. The node sits on it2102 as 10.21.3.1, and replies
to inbound connections pass the firewall via connection tracking:

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa -J root@<pve-master-LAN-IP> it2102@10.21.3.20
```
