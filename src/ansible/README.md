# Ansible: two-VM lab provisioning

Provisions the lab the Vagrantfile builds (or any two cloud VMs in one subnet):

| host (group) | address | role |
|---|---|---|
| `ws-etw` (`windows`) | 10.21.2.10 | SilkETW collector, shipper, benign workload, react2shell vulnlab. Pushes ETW only; no training. |
| `ubuntu-trainer` (`ubuntu`) | 10.21.2.20 | clones the repo, `uv sync`, runs `live_server.py` (systemd `ocr-live`). Receives the pushed data. |

The Windows shipper POSTs raw ETW to the Ubuntu server; the browser UI is at
`http://<ubuntu>:8000/`. All conversion/windowing/scoring runs on Ubuntu.

> The react2shell app is a deliberately-unpatched Next.js lab target, installed for **defensive
> detection practice** in an isolated network. This role installs and serves it; the exploit is the
> teammate's to run during the demo. Keep ports 8000/3000 inside the subnet — never public.

> **Run the control node on Linux (or macOS), not Windows.** Ansible has no native Windows control
> node — these playbooks are launched from your Linux host (or WSL2 on a Windows machine). Windows is
> only ever a *managed* target here (over WinRM), never where you install Ansible or run
> `ansible-playbook`.

## Addresses are inventory-driven (cloud-ready)

Nothing hardcodes an IP. Each host has two addresses:

- `ansible_host` — how Ansible connects (WinRM 5985 / SSH 22). Public IP/DNS on cloud.
- `private_ip` — the subnet/VPC IP the VMs use to reach each other (shipper → server).

`trainer_host` (what the shipper posts to) defaults to the Ubuntu host's `private_ip`. `server_bind`
defaults to `0.0.0.0`. To move to cloud: `cp inventory.cloud.ini.example inventory.cloud.ini`, fill both
addresses per host, and add `-i inventory.cloud.ini` to every command. See `group_vars/all.yml`.

## One-time setup

```bash
cd src/ansible
python3 -m venv .venv && source .venv/bin/activate        # dedicated control-node venv (keep it off system Python)
pip install -r requirements.txt                           # ansible-core + pywinrm (WinRM will NOT work without pywinrm)
ansible-galaxy collection install -r requirements.yml     # win modules, chocolatey, posix
```

Re-activate the venv (`source .venv/bin/activate`) in any new shell before running `ansible` /
`ansible-playbook`.

**Local mode needs no vault.** `inventory.local.ini` already carries the box's public default account
(`vagrant` / `vagrant`) as `win_user` / `win_password`. The vault below is only for cloud.

**Cloud creds (cloud mode only):**
```bash
cp group_vars/vault.yml.example group_vars/vault.yml      # fill win_user / win_password (RDP = WinRM)
ansible-vault encrypt group_vars/vault.yml                # vault.yml is gitignored
# then add to every cloud command:  -e @group_vars/vault.yml --ask-vault-pass
```

### Tamper Protection (Windows, manual, once)

Defender **Tamper Protection** cannot be toggled remotely. Before the first run, RDP into `ws-etw` and
turn it OFF: *Windows Security → Virus & threat protection → Manage settings → Tamper Protection = Off*.
Then the `win_base` role can disable real-time monitoring (it warns and continues if TP is still on).

## Run

```bash
# local lab (default inventory.local.ini — no vault needed):
vagrant up                                   # from src/, builds both bare VMs
ansible windows -m ansible.windows.win_ping  # smoke-test WinRM (use win_ping, NOT ping, on Windows)
ansible-playbook site.yml                    # provision both

# just one side:
ansible-playbook site.yml --limit windows
ansible-playbook site.yml --limit ubuntu

# cloud (vault supplies the Windows creds):
ansible-playbook -i inventory.cloud.ini site.yml -e @group_vars/vault.yml --ask-vault-pass
```

After the Windows run, pin the download hashes the roles print (`silketw_sha256`, `python_embed_sha256`
in `group_vars/all.yml`) so rebuilds are immutable.

## Demo (on `ws-etw`, elevated PowerShell in the RDP desktop session)

```powershell
C:\scripts\06-benign-workload.ps1 -SelfTest          # once per VM
C:\scripts\ship-live.ps1                             # window 1: capture + ship to the trainer
C:\scripts\06-benign-workload.ps1 -DurationMinutes 20  # window 2: benign activity (green windows)
```

The vulnlab serves on `http://<windows>:3000/` from boot (scheduled task `react2shell-vulnlab`). Watch
the UI at `http://<ubuntu>:8000/`; benign windows stay green, the teammate's attack turns its window red.

## Layout

```
ansible.cfg  requirements.yml  site.yml  windows.yml  ubuntu.yml
inventory.local.ini  inventory.cloud.ini.example
group_vars/   all.yml  windows.yml  ubuntu.yml  vault.yml.example
roles/
  win_base        OS config (01-base.ps1 rewritten): net profile, UTC, no-sleep, RDP, folders, Defender
  win_collector   SilkETW + VC++ + embeddable Python + etw2nodlink.py/etwship.py/etwctl + SilkService
  win_shipper     ship-live.ps1 (templated with trainer_host/server_port)
  win_benign      06-benign-workload.ps1
  win_vulnlab     Node LTS + react2shell + build + serve on vulnlab_port
  ubuntu_base     apt deps + uv + ufw
  ubuntu_project  git clone + uv sync + ocr-live systemd unit
```

The converter (`win_collector`) is deployed from `src/pipeline/etw2nodlink.py`, the same file the live
server hashes, so the trained model (`exp01`) keeps validating.
