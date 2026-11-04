# infrastructure/: Proxmox VE lab VMs (Terraform)

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
2. OUT DROP → `10.21.3.1` (the node via the it2102 VNet gateway: UI :8006, SSH :22), logged. Routing to
   the internet through the gateway still works.
3. OUT DROP → `10.21.4.1` (the node via the **it2102k** VNet gateway), logged. The node has an IP on the
   Kali VNet too, so without this a VM could reach the UI/SSH at `10.21.4.1` and bypass rule 2.
4. IN ACCEPT ← `10.21.3.0/24` (lab subnet: RDP, DVWA)
5. IN ACCEPT ← `10.21.4.0/24` (Kali attacker subnet: cross-subnet DVWA exploit + RCE callback)
6. Defaults: input DROP, output ACCEPT. Replies to the VM's own connections pass via connection tracking.

Check from PowerShell inside the VM:

- `Test-NetConnection 192.168.1.1 -Port 80` → `TcpTestSucceeded : False` (LAN blocked)
- `Test-NetConnection 10.21.3.1 -Port 8006` → `False` (node UI blocked)
- `Test-NetConnection 10.21.4.1 -Port 8006` → `False` (node UI via the Kali-VNet gateway also blocked)
- `Test-NetConnection 1.1.1.1 -Port 443` → `True`, and `Resolve-DnsName microsoft.com` resolves (internet + DNS)

Drops appear in the Proxmox UI under VM 2102 → Firewall → Log.

## ubuntu-trainer (VM 2103)

Ubuntu 24.04 cloud image (pinned `noble/20261017`), 4 cores, 6 GB RAM, 200 GB disk (Loki keeps 72 h at
~2 GB/h), static `10.21.3.20/24` via cloud-init, user `it2102` with the `it2102-vms` key. It gets the same
`it2102:` firewall rules as ws-etw (`local.it2102_rules`): no LAN, no node, inbound only from the lab subnet.

## Reaching the lab from the workstation (routed path)

The workstation reaches `10.21.3.0/24` **directly**: the home router routes the subnet to `pve-master`, which
is the it2102 gateway (`10.21.3.1`). The VM firewall only drops traffic the VMs *start* toward the LAN; your
inbound SSH passes, and its replies pass through connection tracking. Verified 2026-11-02 (`ssh` to
`ubuntu-etw` with no jump):

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa it2102@10.21.3.20   # ubuntu-trainer
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa it2102@10.21.3.11   # ubuntu-etw
```

The first connection asks to trust the host key (cloud-init generates a new one on every rebuild; after a
`destroy`/`apply`, run `ssh-keygen -R <ip>` first). The Ansible inventory `src/ansible/proxmox.ini` uses the
same route (`ansible_host = private_ip`).

Fallback when the route is missing (e.g. away from home): jump through the node, which sits on it2102 as
10.21.3.1:

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa -J root@<pve-master-LAN-IP> it2102@10.21.3.20
```

## ubuntu-etw (VM 2104)

Ubuntu 24.04 endpoint, the Linux counterpart of ws-etw. Built **the same way as the trainer**: the same
cloud image (`proxmox_download_file.ubuntu_image`, downloaded once and shared), and cloud-init sets the
static `10.21.3.11/24`, gateway `10.21.3.1`, DNS and user `it2102` with the `it2102-vms` key. No manual
install: SSH works about 1–2 minutes after `apply`. 2 cores, 4 GB RAM, 40 GB disk. Same `it2102:` firewall
rules as the other VMs.

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa it2102@10.21.3.11   # direct, routed path (above)
```

Check inside: `curl -sI https://1.1.1.1` answers (internet), `curl -m3 http://192.168.1.1` times out (LAN blocked).

The name mirrors ws-etw, but ETW is a Windows-only kernel API: this VM runs no collector yet. A Linux endpoint
would need auditd or eBPF plus its own converter and model.

## nt2102-vpn (VM 2105)

Ubuntu 24.04 VM for an OpenVPN server. It is built the same way as ubuntu-etw: shared cloud image, and
cloud-init sets the static `10.21.3.50/24`, gateway `10.21.3.1` and user `it2102` with the `it2102-vms` key.
It has 2 cores, 1 GB RAM and a 10 GB disk.

```bash
ssh -i ~/OneDrive/credentials/ssh/it2102-vms/id_rsa it2102@10.21.3.50
```

Firewall: the shared `it2102:` rules, plus one rule for this VM only (`local.vpn_rules`): **IN ACCEPT TCP 1194
from any source** (OpenVPN clients come from outside the lab subnet, DNAT'd as TCP by pve-master). The VM's
`input_policy` is ACCEPT today, so this rule is what keeps the VPN reachable if that policy is ever set to DROP;
OpenVPN must listen on TCP (`proto tcp-server`), not the default UDP.

- **OpenVPN is not installed yet.** Terraform builds only the bare VM. The server, PKI and NAT/forwarding
  come in a later step.
- Clients on the internet reach it on **external TCP 2102**: the home router forwards TCP 2102 → pve-master
  `192.168.1.120:2102`, and pve-master DNATs that to `10.21.3.50:1194`
  (`src/ansible/proxmox-firewall.yml`, iptables rules saved with `netfilter-persistent`). Client configs use `remote <public-ip> 2102`.
- The shared OUT DROP to `192.168.1.0/24` also applies to traffic this VM forwards for VPN clients.
  Through the tunnel they reach the lab subnet, not the home LAN.

## kali (VM 2106)

Kali attacker on its **own** SDN VNet `it2102k` → subnet `10.21.4.0/24` (gateway `10.21.4.1`, SNAT),
separate from the lab subnet `10.21.3.0/24`. 2 cores, 4 GB RAM, 40 GB disk. Built from the **installer
ISO** (there is no Kali cloud image for this path), so — like `ws-etw` — it is installed by hand and has
**no cloud-init**: Terraform builds only the hardware. A dedicated `proxmox_sdn_applier.kali` applies the
new VNet to the node on `apply` (the main applier does not re-run for a newly added VNet).

`kali_iso_url` in `main.tf` is **version-pinned** (`kali-2026.3` when written); confirm/bump it against
<https://www.kali.org/get-kali> before `apply`, like the Windows `iso_url`.

One-time manual steps after `apply` (noVNC console):

1. Install Kali from the ISO (guided install; create the user **`kali`** and its password — this password
   goes in `src/ansible/group_vars/vault.yml` as `kali_password`).
2. Static networking (no DHCP on `it2102k`): IP `10.21.4.30/24`, gateway `10.21.4.1`, DNS e.g. `1.1.1.1`
   (output `kali_ip`).
3. Enable SSH: `sudo systemctl enable --now ssh` (Kali allows password auth by default).

Reaching Kali from the workstation: the home router currently routes **only** `10.21.3.0/24` to
pve-master. Add the same static route for `10.21.4.0/24 → 192.168.1.120` (pve-master LAN IP), **or** jump
through the node:

```bash
ssh -J root@<pve-master-LAN-IP> kali@10.21.4.30
```

Then provision it (user/password auth, so `sshpass` must be on the control node):

```bash
sudo apt install sshpass
ansible-playbook -i proxmox.ini kali.yml -e @group_vars/vault.yml --ask-vault-pass
```

### Firewall (VM 2106) + the cross-subnet attack path

Kali gets the shared `it2102:` rules (`local.it2102_rules`): no home LAN, no node, inbound only from the
lab + Kali subnets. To let the attack cross between `10.21.4.0/24` and `10.21.3.0/24`, a rule was added to
`it2102_rules` for **all** VMs: `IN ACCEPT ← 10.21.4.0/24`. So `ws-etw` accepts Kali's DVWA exploit, and
Kali accepts the `ws-etw → Kali` stage callback (covered by the existing `IN ACCEPT ← 10.21.3.0/24`).
pve-master routes between the two VNet gateways and does not masquerade inter-subnet traffic, so `ws-etw`
sees Kali's real `10.21.4.30` source. (Note: this README describes the effective input default as DROP
while `firewall_options` sets `input_policy = ACCEPT` — a doc/code mismatch; the explicit accept rule makes
the path work under either.)

## Cloud image vs ISO install

The two Ubuntu VMs use a cloud image; ws-etw uses an installer ISO (there's no Windows Server eval cloud image).

| | Cloud image + cloud-init (`ubuntu-trainer`, `ubuntu-etw`) | Installer ISO + manual install (`ws-etw`) |
|---|---|---|
| What Proxmox downloads | A finished disk (`.img`, ~600 MB), imported as the VM disk (`import_from`) | An installer (`.iso`, ~5 GB) attached as a CD-ROM |
| Who sets IP, user, SSH key | Terraform, via the `initialization` block → cloud-init on first boot | You, in the installer / Windows settings |
| Manual steps after `apply` | None | Whole install in the console, then static IP + WinRM bootstrap |
| `destroy` + `apply` | Same VM again, nobody needs to touch it | Empty disk again: reinstall by hand |
| Guest config in Terraform | Yes (IP/user/key are in `main.tf`) | No, only the hardware |
| Reuse | One downloaded image serves both Ubuntu VMs | One ISO per OS |

Takeaway: the cloud image is the default for anything rebuilt often. The ISO path is the fallback when no
cloud image exists. It can be automated too (`autounattend.xml` or a Packer-built template), but isn't here.
