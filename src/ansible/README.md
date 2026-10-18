# Ansible: two-VM lab provisioning

Provisions the lab the Vagrantfile builds (or any two cloud VMs in one subnet):

| host (group) | address | role |
|---|---|---|
| `ws-etw` (`windows`) | 10.21.2.10 | SilkETW collector, **Grafana Alloy** (ships raw ETW to Loki), benign workload, react2shell vulnlab (optional, `-e vulnlab_enabled=true`). No training. |
| `ubuntu-trainer` (`ubuntu`) | 10.21.2.20 | **Loki + Grafana** (Docker, host network), repo + `uv sync`, training, `live_server.py` (systemd `ocr-live`, follows Loki). |

> The lab also has a **Kali attacker VM** (`10.21.2.30`), but it is **not provisioned by Ansible** — it
> is a local-only Vagrant box that mounts `src/atomic-attack-pack` at `/opt/attack-pack` via a synced
> folder (`src/Vagrantfile`), and runs the exploit launcher directly. See that pack's `README.md`.

```
SilkService -> C:\lab-out\etw\raw\<split>\<run>\{kproc,kfile,knet}.json
  -> Alloy (Windows service) -> Loki :3100 on the trainer  <- Grafana :3000 (browse / LogQL)
       training:  loki_pull.py -> <exp>/raw/  -> build_experiment.py -> features -> train
       live:      live_server.py --loki-url (loki_source.py) -> windows -> UI :8000
```

Loki keeps every SilkETW line exactly as written. Alloy only adds labels next to it:

| kind | keys | use |
|---|---|---|
| labels (indexed) | `job` (`silketw`, `etw-meta`, `workload-ledger`), `host`, `split` (train/test), `run`, `provider` (kproc/kfile/knet) | choose streams: `{job="silketw", run="x-20261017T030000Z"}` |
| structured metadata (not indexed) | `event` (EventName), `pid` (ProcessID), `process` (ProcessName) | filter: `\| event="ProcessStart"`, `\| process="powershell"` |

A label is one more index dimension, so each distinct value creates a new stream. With `pid` as a label,
every process would make its own stream, which is why `pid` is metadata instead. LogQL examples for
Grafana *Explore*:

```logql
{job="silketw", split="train"}                                   # every train line
{job="silketw", run="x-20261017T030000Z", provider="kproc"} | event="ProcessStart/Start"
{job="silketw", split="test"} | process="powershell" | json | line_format "{{.EventName}} {{.ProcessID}}"
sum by (provider) (count_over_time({job="silketw", run="live-20261017T040000Z"}[1m]))   # lines per minute
{job="etw-meta"}                                                 # dos map of each run
```

> The react2shell app is a deliberately unpatched Next.js lab target, installed for **defensive
> detection practice** in an isolated network. `win_vulnlab` installs and serves it (Node pinned to a
> supported LTS so `next build` no longer OOMs); the local Kali box drives the exploit during the demo,
> running the launcher from the mounted pack:
>
> ```bash
> # on Kali (pack mounted at /opt/attack-pack by Vagrant):
> python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://10.21.2.10:3000   # only input is the URL
> python3 -I /opt/attack-pack/tools/launch_exploit.py --selftest                        # serve+fetch, no target
> ```
>
> Confirm react2shell's real injection vector from `C:\lab\react2shell` and pass
> `--exploit-path`/`--exploit-field`; the launcher warns while the placeholders are in use.

> **Ports stay inside the subnet.** ufw enforces this on the trainer:
> - Loki 3100: only from the Windows host.
> - Grafana 3000: only from `admin_cidr`.
> - Live UI 8000.
>
> The vulnlab (3000 on Windows) stays inside the subnet too. Never expose any of these publicly.

> **Run the control node on Linux (or macOS), not Windows.** Ansible has no native Windows control
> node, so run these playbooks from your Linux host (or WSL2 on a Windows machine). Windows is only
> ever a *managed* target here, over WinRM. Never install Ansible or run `ansible-playbook` on Windows.

## Status (2026-10-17)

| level | state |
|---|---|
| it runs (static) | `--syntax-check` passes for every playbook; `alloy fmt` (v1.20.1) accepts the rendered `config.alloy`; 14/14 `./selftest.sh` steps pass |
| Ubuntu side | provisioned on the local `ubuntu-trainer`; a second run reports no changes except the `ocr-live` start (see `repo_branch` below). Verified with the recorded capture `20261009T145632Z` fed through `loki_push.py`: (1) pulled lines = original files (count + sha256 of sorted lines, all 3 providers); (2) `build_experiment.py` edges, node attributes and windows identical to the VM-built ones, line for line; (3) live via `--loki-url`: `compare_replay.py` ALL MATCH, 8/8 windows |
| Windows side | **not run yet**: `win_alloy` and the `etwctl` dos-map change are untested on `ws-etw`. Use the checklist below |
| model | being rebuilt from scratch from Loki-pulled data. `exp01` (Win10 data, older converter) shows `converter_check: MISMATCH`, which is expected |

## Addresses are inventory-driven (cloud-ready)

Nothing hardcodes an IP. Each host has two addresses:

- `ansible_host`: how Ansible connects (WinRM 5985 / SSH 22). Public IP/DNS on cloud.
- `private_ip`: the subnet/VPC IP the VMs use to reach each other (Alloy → Loki).

`trainer_host` defaults to the Ubuntu host's `private_ip`, and `loki_url` = `http://<trainer_host>:3100`.
The trainer also needs `admin_cidr` (who may open Grafana) and `grafana_admin_password`. Both are in
`inventory.local.ini` for the lab; for cloud, `admin_cidr` goes in the cloud inventory and the password in
the vault. To move to cloud, `cp inventory.cloud.ini.example inventory.cloud.ini`, fill in both addresses
per host, and add `-i inventory.cloud.ini` to every command.

## One-time setup

```bash
cd src/ansible
python3 -m venv .venv && source .venv/bin/activate        # dedicated control-node venv (keep it off system Python)
pip install -r requirements.txt                           # ansible-core + pywinrm (WinRM will NOT work without pywinrm)
ansible-galaxy collection install -r requirements.yml     # win modules, chocolatey, posix, docker
```

Re-activate the venv (`source .venv/bin/activate`) in any new shell before running `ansible` or
`ansible-playbook`.

**Local mode needs no vault.** `inventory.local.ini` already carries:
- the box's public default account (`vagrant` / `vagrant`)
- a lab-only Grafana password (`labadmin`)

**Cloud creds (cloud mode only):**
```bash
cp group_vars/vault.yml.example group_vars/vault.yml      # fill win_user / win_password / grafana_admin_password
ansible-vault encrypt group_vars/vault.yml                # vault.yml is gitignored
# then add to every cloud command:  -e @group_vars/vault.yml --ask-vault-pass
```

### Tamper Protection (Windows, manual, once)

Defender **Tamper Protection** cannot be toggled remotely. Before the first run, RDP into `ws-etw` and
turn it OFF: *Windows Security → Virus & threat protection → Manage settings → Tamper Protection = Off*.
The `win_base` role can then disable real-time monitoring; if Tamper Protection is still on, it warns and
continues.

## Run

```bash
# local lab (default inventory.local.ini, no vault needed). Ubuntu FIRST: Alloy needs Loki to push to.
vagrant up                                   # from src/, builds both bare VMs
ansible ubuntu -m ping
ansible windows -m ansible.windows.win_ping  # use win_ping on Windows, NOT ping
ansible-playbook site.yml --limit ubuntu     # Loki + Grafana + repo + ocr-live
ansible-playbook site.yml --limit windows    # SilkETW + Alloy + scripts (+ vulnlab with -e vulnlab_enabled=true)

# cloud (the vault supplies the Windows + Grafana creds):
ansible-playbook -i inventory.cloud.ini site.yml -e @group_vars/vault.yml --ask-vault-pass
```

**`repo_branch` must contain the Loki code.** The trainer clones `repo_url` at `repo_branch`
(`group_vars/all.yml`, default `main`), and `ocr-live` starts `live_server.py --loki-url`. A branch
without `src/live/loki_source.py` makes the service fail with an unknown-argument error. Push this branch
and set `repo_branch` to it, or merge it first.

After the Windows run, pin the download hashes the roles print (`silketw_sha256`, `python_embed_sha256` in
`group_vars/all.yml`). `alloy_sha256` is already pinned from the release's `SHA256SUMS`.

## Rebuild training data (from scratch, through Loki)

```powershell
# on ws-etw, elevated PowerShell in the RDP desktop session (not WinRM):
C:\scripts\06-benign-workload.ps1 -SelfTest           # once per VM
C:\scripts\07-build-train-dataset.ps1                 # 20-min benign capture, run x-<UTC>; Alloy ships it live
```
`07` still converts on the VM. That copy is now a cross-check; the training source is Loki. After the
stop, `07` waits `-ShipWaitSeconds` (default 15) so Alloy can push the last lines, then **copies** the raw
files into the experiment folder. It used to move them; copying leaves the files Alloy tails in place.

```bash
# on ubuntu-trainer (ssh in; LOKI_URL is set by /etc/profile.d/lokistack.sh):
cd ~/NT2102 && source .venv/bin/activate   # `lab` comes with uv sync (src/lab/cli.py)
lab status                                  # current model, experiments, models; provider coverage
lab loop                                    # interactive: pick the capture in Loki -> build -> features -> train
lab loop --latest --model exp02 --yes       # the same without questions (lab loop -h: all options)
lab promote exp02 && sudo systemctl restart ocr-live    # the live server now loads exp02
```
`lab loop` = `loki_pull.py --build` (convert, window, check, `experiment.json`: `07` steps 4–7 on the
trainer) → `encode_to_features.py` → `train_gnn_models.py`, all into one run folder
`src/artifacts/runs/<model>/` (models, features + scaler, a copy of `experiment.json`, `lineage.json`).
`lab promote` writes `src/artifacts/current.local.json` (gitignored), which `ocr-live` reads; nothing in
`group_vars` names the model. To share a model, commit its model folder and `lab promote <model> --shared`
(writes the tracked `current.json`).

`collect-train.yml` (tar the VM's experiment folder and fetch it) is the **legacy fallback**, for when Loki
is down.

**Converter hash rule.**
- `experiment.json` records the sha256 of the converter's raw file bytes. The live server compares it with
  its own copy and reports the result as `converter_check`.
- Both now run on the trainer, from the same file.
- Ansible copies the same file to the VM, and `.gitattributes` pins it to LF.
- Edit the converter **before** a training capture, never after. After an edit, build again.

## Demo (on `ws-etw`, elevated PowerShell in the RDP desktop session)

```powershell
C:\scripts\06-benign-workload.ps1 -SelfTest            # once per VM
C:\scripts\start-live.ps1                              # window 1: capture test/live-<UTC>; Alloy ships it
C:\scripts\06-benign-workload.ps1 -DurationMinutes 20  # window 2: benign activity (green windows)
```

The vulnlab serves on `http://<windows>:3000/` from boot (scheduled task `react2shell-vulnlab`). Watch
the live UI at `http://<ubuntu>:8000/`, and the raw lines in Grafana at `http://<ubuntu>:3000/`. Benign
windows stay green; the teammate's attack turns its window red.

## Checks

**Loki behaviour to know (observed on 3.7.8, handled in `loki.yaml.j2`):**
- Lines with an OLD event time (a late backlog, a replay) are queryable only after their chunk is flushed.
  This config flushes idle chunks after 2 min; `loki_push.py` calls `/flush` itself.
- After a Loki restart, queries return nothing for about a minute.

**Ubuntu (automated by the role + verified 2026-10-17):**
- `docker compose -f /opt/lokistack/compose.yaml ps` shows `loki` and `grafana` up.
- `curl 127.0.0.1:3100/ready` returns `ready`.
- `ufw status` shows 3100 only from the Windows `private_ip` and 3000 only from `admin_cidr`.

**Windows (by hand, after `site.yml --limit windows`):**
1. `Get-Service Alloy` shows Running.
2. After `etwctl start -Split test`, the run folder has `dosmap.meta`: one JSON line such as
   `{"\\device\\harddiskvolume4":"c:"}`.
3. Grafana Explore `{job="silketw", host="ws-etw"}` shows lines labelled with `split`, `run` and `provider`,
   and the line details show `event`, `pid` and `process`. If `split`/`run` are missing, Alloy's
   `filename` regex did not match the Windows path form: check `stage.regex` in `config.alloy`.
4. A raw `TimeStamp` ends in `+07:00`, and the Loki time of a line equals its `TimeStamp`. The first
   checks the zone; the second shows the timestamp stage works.
5. `07` → `loki_pull.py --run x-<UTC> --yes --build` on the trainer. Its `SimulatedW10_train_edges.csv`
   should equal the VM's own `07` result, compared as multisets (sorted lines). The converter summary
   lists `alloy` under excluded edges, not as process nodes.
6. `start-live.ps1` + `06`: the live UI shows `session live-<UTC> (loki, ws-etw)` and windows close.
   `converter_check: match` appears once `experiment.json` comes from a capture built with this converter.
7. react2shell's `package.json` `engines` matches the installed Node (the role prints both).

## Layout

```
ansible.cfg  requirements.yml  requirements.txt  site.yml  windows.yml  ubuntu.yml  collect-train.yml (legacy)
inventory.local.ini  inventory.cloud.ini.example
group_vars/   all.yml  windows.yml  ubuntu.yml  vault.yml.example
roles/
  win_base        OS config (01-base.ps1 rewritten): net profile, UTC+7, no-sleep, RDP, folders, Defender
  win_collector   SilkETW + VC++ + embeddable Python + etw2nodlink.py/etwwindow.py/etwship.py/etwctl + SilkService
  win_alloy       Grafana Alloy (native installer, service) + config.alloy -> Loki
  win_shipper     start-live.ps1 (start/stop a live test capture)
  win_benign      06-benign-workload.ps1 + 07-build-train-dataset.ps1
  win_vulnlab     Node LTS + react2shell + build + serve on vulnlab_port (opt-in: vulnlab_enabled;
                  `next build` currently runs out of heap on Node 24, unresolved)
  ubuntu_base     UTC+7 + timesyncd, apt deps + uv + ufw rule for the live server
  ubuntu_loki     Docker + Loki + Grafana (compose, host network) + ufw (Loki from Windows, Grafana from admin_cidr)
  ubuntu_project  git clone + uv sync + ocr-live systemd unit (--loki-url)
```

The converter and windowing tool (`win_collector`) are deployed from `src/pipeline/`, the same files
`build_experiment.py` runs on the trainer. See the converter hash rule above.
