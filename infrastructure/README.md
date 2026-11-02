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
