# infrastructure/: Proxmox lab (Terraform)

The lab's network and VMs on one Proxmox VE node, `bpg/proxmox` provider. Torn down; the config and the
committed state (no secrets in it) remain as the record.

## Outcome

- One `terraform apply` builds every VM and network; only Windows and Kali need a manual OS install from ISO.
- The lab subnets cannot reach the home LAN or the Proxmox node; attack traffic stays inside the lab.

## Architecture

```
SDN zone nt2102
├── VNet it2102   10.21.3.0/24   ws-etw-0 .10  Windows Server 2022: DC of nt2102.lab + DVWA target
│                                ws-etw-1 .11  Windows Server 2022: react2shell web app
│                                ubuntu-trainer .20  Loki, Grafana, training, live server (cloud image)
│                                nt2102-vpn .50  OpenVPN for remote access (cloud image)
└── VNet it2102k  10.21.4.0/24   kali .30  attacker
```

- Per-VM Proxmox firewall (`it2102:` rules): out to the home LAN and the node dropped; in only from the two lab subnets (plus TCP 1194 on nt2102-vpn).
- VPN: pve-master DNATs external TCP 2102 to nt2102-vpn:1194, and clients keep their real 10.8.0.x source in the lab.
- Ubuntu VMs: cloud image + cloud-init (rebuilt with no manual work). Windows and Kali: ISO + manual install.

## Key files

| file | what |
|---|---|
| `main.tf` | all lab values in `locals`: zone, VNets, VMs, firewall rules |
| `variables.tf` | only credentials (`TF_VAR_*`) |
| `versions.tf` | provider pin, root login via SSH agent |
| `terraform.tfstate` | committed state |
| `../src/ansible/proxmox-firewall.yml` · `proxmox-firewall-teardown.yml` | pve-master DNAT + VPN return route, and their removal |
