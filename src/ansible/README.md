# src/ansible/: lab provisioning

Configures every lab VM idempotently: a re-run on a configured box reports `changed=0`. Addresses come from the
inventory, so the same roles also target cloud VMs (`inventory.cloud.ini.example` + vault).

## Outcome

- Two Windows collectors, one trainer/live server, one Kali attacker and the VPN, all from playbooks.
- Trainer resource isolation: SSH and training work capped at 50 % RAM in `user.slice`, monitoring reserved and
  never swapped, `ocr-live` killed before monitoring.
- Teardown: `proxmox-firewall-teardown.yml` removes the only state that outlives `terraform destroy`
  (pve-master DNAT, FORWARD accepts, VPN return route).

## Architecture

```
ws-etw-0 / ws-etw-1   etwcollect (SYSTEM task) ─push─► Loki :3100 on ubuntu-trainer ◄─ Grafana :3000
                      Sysmon EID 1 (command lines, UI + LLM only)
ubuntu-trainer        Loki + Grafana (Docker, host network, ufw) · Prometheus stack (127.0.0.1)
                      repo + uv · ocr-live (systemd, follows Loki, web UI :8000)
kali                  /opt/attack-pack (RCE kill chain + launcher)
nt2102-vpn · pve      pushed lab routes, no SNAT to the lab · DNAT TCP 2102 → OpenVPN
```

## Playbooks and roles

| playbook | roles |
|---|---|
| `windows.yml` | `win_base` (OS, RDP, Defender), `win_sysmon`, `win_collector` (uv, etwcollect, converter), `win_shipper` (always-on capture), `win_vulnlab` (XAMPP + DVWA, opt-in) |
| `ubuntu.yml` | `ubuntu_base` (deps, uv, swap), `ubuntu_loki`, `ubuntu_monitoring`, `ubuntu_project` (repo, `ocr-live`) |
| `kali.yml` | `kali_attack` |
| `vpn.yml` | `vpn_routing` |
| `proxmox-firewall.yml` · `proxmox-firewall-teardown.yml` | pve-master rules, and their removal |
| `site.yml` | `windows.yml` + `ubuntu.yml` |
| `verify-windows.yml` | read-only check of every installed tool |
| `target.yml` | retired Linux endpoint (`linux_auditd`, `linux_auditship`) |

Inventory: `proxmox.ini` (lab, committed; logins are lab-only). Shared values: `group_vars/all.yml`.
