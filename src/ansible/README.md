# Ansible: two-VM lab provisioning

Provisions the lab the Vagrantfile builds (or any two cloud VMs in one subnet):

| host (group) | address | role |
|---|---|---|
| `ws-etw` (`windows`) | 10.21.2.10 | ETW collector **`etwcollect`** (`src/etw-python`, SYSTEM task `etwlab-collector`; it also pushes to Loki), DVWA vulnlab (optional, `-e vulnlab_enabled=true`). No training. |
| `ubuntu-trainer` (`ubuntu`) | 10.21.2.20 | **Loki + Grafana** (Docker, host network), repo + `uv sync`, training, `live_server.py` (systemd `ocr-live`, follows Loki). |

> The lab also has a **Kali attacker VM** (`10.21.2.30`), but it is **not provisioned by Ansible** — it
> is a local-only Vagrant box that mounts `src/atomic-attack-pack` at `/opt/attack-pack` via a synced
> folder (`src/Vagrantfile`), and runs the exploit launcher directly. See that pack's `README.md`.

```
etwcollect (SYSTEM task) -> C:\lab-out\etw\raw\<split>\<run>\{kproc,kfile,knet}.json
  -> its shipper thread -> Loki :3100 on the trainer  <- Grafana :3000 (browse / LogQL)
       training:  loki_pull.py -> <exp>/raw/  -> build_experiment.py -> features -> train
       live:      live_server.py --loki-url (loki_source.py) -> windows -> UI :8000
```

Before 2026-10-26 SilkService wrote the files and Grafana Alloy shipped them; `win_collector` now removes
both (DEVIATIONS D10). The Loki contract did not change: Loki keeps every line exactly as written, with these labels next to it:

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

> DVWA (Damn Vulnerable Web Application) is a deliberately vulnerable PHP lab target, installed for
> **defensive detection practice** in an isolated network. `win_vulnlab` installs XAMPP (Apache + PHP +
> MariaDB) via Chocolatey and serves DVWA on `vulnlab_port`; the local Kali box drives the exploit during
> the demo, running the launcher from the mounted pack:
>
> ```bash
> # on Kali (pack mounted at /opt/attack-pack by Vagrant):
> python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://10.21.2.10:3000   # only input is the URL
> python3 -I /opt/attack-pack/tools/launch_exploit.py --selftest                        # serve+fetch, no target
> ```
>
> The launcher targets DVWA's command-injection sink (`/vulnerabilities/exec/`, field `ip`, security=low)
> by default, logging in as admin/password; pass `--exploit-path`/`--exploit-field` only to override.

> **Ports stay inside the subnet.** ufw enforces this on the trainer:
> - Loki 3100: only from the Windows host.
> - Grafana 3000: only from `admin_cidr`.
> - Live UI 8000.
>
> The vulnlab (3000 on Windows) stays inside the subnet too. Never expose any of these publicly.

> **Run the control node on Linux (or macOS), not Windows.** Ansible has no native Windows control
> node, so run these playbooks from your Linux host (or WSL2 on a Windows machine). Windows is only
> ever a *managed* target here, over WinRM. Never install Ansible or run `ansible-playbook` on Windows.

## Status (2026-10-26)

| level | state |
|---|---|
| it runs (static) | `--syntax-check` passes for every playbook; 15/15 `./selftest.sh` steps pass (incl. `etwcollect selftest`) |
| Ubuntu side | provisioned on the local `ubuntu-trainer`; a second run reports no changes except the `ocr-live` start (see `repo_branch` below). Verified with the recorded capture `20261009T145632Z` fed through `loki_push.py`: (1) pulled lines = original files (count + sha256 of sorted lines, all 3 providers); (2) `build_experiment.py` edges, node attributes and windows identical to the VM-built ones, line for line; (3) live via `--loki-url`: `compare_replay.py` ALL MATCH, 8/8 windows |
| Windows side | verified on `ws-etw` (2026-10-26): `windows.yml` 2nd run `changed=0`, `verify-windows.yml` passes, `etwcollect selftest --live` passes, 32-min live run lossless into Loki (counts = file lines) with kfile lag within ±4 s and 0 lost events (PROGRESS) |
| model | being rebuilt from scratch from Loki-pulled data. `exp01` (Win10 data, older converter) shows `converter_check: MISMATCH`, which is expected |

## Addresses are inventory-driven (cloud-ready)

Nothing hardcodes an IP. Each host has two addresses:

- `ansible_host`: how Ansible connects (WinRM 5985 / SSH 22). Public IP/DNS on cloud.
- `private_ip`: the subnet/VPC IP the VMs use to reach each other (etwcollect → Loki).

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
# local lab (default inventory.local.ini, no vault needed). Ubuntu FIRST: etwcollect needs Loki to push to.
vagrant up                                   # from src/, builds both bare VMs
ansible ubuntu -m ping
ansible windows -m ansible.windows.win_ping  # use win_ping on Windows, NOT ping
ansible-playbook site.yml --limit ubuntu     # Loki + Grafana + repo + ocr-live
ansible-playbook site.yml --limit windows    # etwcollect (uv) + start-live.ps1 (+ vulnlab with -e vulnlab_enabled=true)

# cloud (the vault supplies the Windows + Grafana creds):
ansible-playbook -i inventory.cloud.ini site.yml -e @group_vars/vault.yml --ask-vault-pass
```

**`repo_branch` must contain the Loki code.** The trainer clones `repo_url` at `repo_branch`
(`group_vars/all.yml`, default `main`), and `ocr-live` starts `live_server.py --loki-url`. A branch
without `src/live/loki_source.py` makes the service fail with an unknown-argument error. Push this branch
and set `repo_branch` to it, or merge it first. One-off, without editing the file:
`ansible-playbook -i inventory.local.ini ubuntu.yml --tags project -e repo_branch=<branch>`.

**LLM on `ocr-live`: `live_llm_args`** (default empty = off; `inventory.local.ini` sets the DeepSeek flags).
Non-empty also makes `uv sync` install the `llm` group. The API key is never an Ansible variable: save it once
on the trainer (`alg2.py --ping`, `src/live/README.md` §5) BEFORE the flags are applied, or `ocr-live` exits
at start (no terminal to prompt). `-e live_llm_args=` turns the LLM off for one run.

`python_embed_sha256` and `uv_sha256` in `group_vars/all.yml` are pinned (the latter from the release's
checksum file).

**Collector source (`etwpy_mode`).** Local lab: `inventory.local.ini` sets `etwpy_mode=mount`, and
`src/Vagrantfile` shares `src/etw-python` as `C:\etw-python` (needs `vagrant reload ws-etw` once). A host
edit is live after `etwctl stop; etwctl start`; re-run `--tags collector` only when `pyproject.toml` /
`uv.lock` change. Cloud: the default `clone` git-clones `repo_url@repo_branch` to `C:\lab\NT2102`. Either way
`uv sync --frozen` builds the venv at `C:\Tools\etw-python-venv` (never in the share).

## Rebuild training data (from scratch, through Loki)

```powershell
# on ws-etw, elevated PowerShell in the RDP desktop session (not WinRM):
# benign = IDLE pulled from Loki (no capture script): keep the desktop idle so the always-on live capture
# ships idle background activity, then on the trainer pull a clean idle range and train:
#   lab pull --start 2026-10-31T12:45:00Z --end 2026-10-31T13:02:00Z --split test --yes
#   # a Loki idle pull is --split test -> files are SimulatedW10_test_*; copy them to _train_ before training:
#   cp <exp>/SimulatedW10_test_edges.csv <exp>/SimulatedW10_train_edges.csv   # and _node_attrs.csv
#   lab train --experiment <id> --model exp0X && lab promote exp0X && sudo systemctl restart ocr-live
```
The training source is Loki
(`etwcollect push-ledger`) and **copies** the raw files into the experiment folder.

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

**Converter hash rule.**
- `experiment.json` records the sha256 of the converter's raw file bytes. The live server compares it with
  its own copy and reports the result as `converter_check`.
- Both now run on the trainer, from the same file.
- Ansible copies the same file to the VM, and `.gitattributes` pins it to LF.
- Edit the converter **before** a training capture, never after. After an edit, build again.

## Demo (on `ws-etw`, elevated PowerShell in the RDP desktop session)

```powershell
C:\scripts\start-live.ps1 -Rotate                      # optional: fresh live run = a clean session for the demo
# then just use the desktop normally (browse, docs) to produce benign green windows — no scripted workload
```

The live capture itself is **always on**: the SYSTEM scheduled task `live_task_name` (`etwlab-live`) runs
`start-live.ps1` at boot and every `live_watchdog_minutes`; it keeps a `test/live-<UTC>` run capturing,
rotates it every `live_rotate_hours`, deletes raw `live-*` runs idle for `live_keep_days`, and stays away
while `C:\ProgramData\etwlab\live.hold` exists (`07` sets and clears it; `start-live.ps1 -Pause/-Resume` by
hand). `start-live.ps1 -Status` shows the state and the last lines of `live-watchdog.log`.

The vulnlab (DVWA) serves on `http://<windows>:3000/` from boot (XAMPP's Apache + MariaDB Windows
services). Watch the live UI at `http://<ubuntu>:8000/`, and the raw lines in Grafana at
`http://<ubuntu>:3000/` (Grafana is :3000 on the trainer; DVWA is :3000 on the Windows host — different
machines). Benign
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
1. `etwctl status` shows `collector: task etwlab-collector`, and while a run captures: kfile lag of a few
   seconds, `ETW lost: events 0`, `Loki: ... backlog` near 0. `etwcollect selftest --live` (elevated) passes.
2. After `etwctl start -Split test`, the run folder has `dosmap.meta`: one JSON line such as
   `{"\\device\\harddiskvolume4":"c:"}`.
3. Grafana Explore `{job="silketw", host="ws-etw"}` shows lines labelled with `split`, `run` and `provider`,
   and the line details show `event`, `pid` and `process`.
4. The Loki time of a line equals its `TimeStamp` (etwcollect writes UTC, `+00:00`; SilkETW wrote `+07:00`).
5. `07` → `loki_pull.py --run x-<UTC> --yes --build` on the trainer. Its `SimulatedW10_train_edges.csv`
   should equal the VM's own `07` result, compared as multisets (sorted lines).
6. The `etwlab-live` task (or `start-live.ps1 -Rotate`) + `06`: the live UI shows `session live-<UTC> (loki, ws-etw)` and windows close.
   `converter_check: match` appears once `experiment.json` comes from a capture built with this converter.
7. DVWA's login page answers on `http://<windows>:3000/login.php` (Apache + MariaDB services up).

## Layout

```
ansible.cfg  requirements.yml  requirements.txt  site.yml  windows.yml  ubuntu.yml
inventory.local.ini  inventory.cloud.ini.example
group_vars/   all.yml  windows.yml  ubuntu.yml  vault.yml.example
roles/
  win_base        OS config: net profile, UTC+7, no-sleep, RDP, folders, Defender
  win_collector   uv + etw-python venv + SYSTEM task etwlab-collector + embeddable Python + etw2nodlink.py/
                  etwwindow.py/etwship.py/etwctl; removes the old SilkService + Grafana Alloy
  win_shipper     start-live.ps1 watchdog + SYSTEM task etwlab-live (always-on live test capture)
  win_vulnlab     XAMPP (Apache + PHP + MariaDB) + DVWA, served on vulnlab_port (opt-in: vulnlab_enabled)
  ubuntu_base     UTC+7 + timesyncd, apt deps + uv + ufw rule for the live server
  ubuntu_loki     Docker + Loki + Grafana (compose, host network) + ufw (Loki from Windows, Grafana from admin_cidr)
  ubuntu_project  git clone + uv sync + ocr-live systemd unit (--loki-url + live_llm_args) + session cleanup cron
```

The converter and windowing tool (`win_collector`) are deployed from `src/pipeline/`, the same files
`build_experiment.py` runs on the trainer. See the converter hash rule above.
