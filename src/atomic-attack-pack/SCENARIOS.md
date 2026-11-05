# The demo attack: RCE kill chain

One scenario (`scenarios/rce-killchain.ps1`) replaces the old 01–10 pack. It is the end-goal attack
(CLAUDE §8): a real exploit of the lab's vulnerable service, then a compact ATT&CK follow-on, all
observable by SilkETW Kernel-Process/File/Network and self-labelled into
`C:\lab-labels\attack-labels.jsonl` (LABELING.md, DEVIATIONS D6).

## Topology (same subnet, phases split by port)

```
Kali attacker (10.21.2.30)                 ws-etw target (10.21.2.10)
  tools/launch_exploit.py  --target 3000-->  DVWA (httpd.exe -> cmd.exe)  --cmd injection-->
       serves :8080  <---- fetch stage ----  powershell (RCE child)  --runs--> killchain.ps1
                     <---- T1105 GET  :8080-  Invoke-WebRequest tool.bin
```

The exploit comes from Kali; the kill chain runs on the target as `httpd.exe -> cmd.exe -> powershell
-> killchain.ps1`. A separate C2 subnet is deliberately NOT used: the detector sees only `ip:port` hashes
and per-node features, so a subnet buys no signal (DEVIATIONS D8). One Kali box doubles as exploit
source and stage/C2 endpoint; phases are separated by port (exploit → :3000, stage/download → :8080).

## Phases (MITRE ATT&CK)

| Phase | Tactic | ATT&CK | Observable as |
|-------|--------|--------|---------------|
| Delivery | Initial access / Execution | T1190, T1059.001 | `httpd.exe` -> `cmd.exe` spawns `powershell.exe`; NET to Kali |
| Discovery | Discovery | T1033, T1082, T1057, T1016 | `whoami`/`systeminfo`/`hostname`/`tasklist`/`ipconfig`/`netstat`/`net` under the RCE shell |
| Network scan | Discovery | T1046 | `powershell` TcpClient sweep ~50 lab hosts × 7 ports (+ real `nmap.exe -sT -Pn` when staged, best-effort) → **one NET node per `ip:port`** (the big NET signal) |
| Ingress transfer | Command & Control | T1105 | `powershell` downloads **REAL** benign tools `nmap/ncat/PsExec64/rclone.exe` to `C:\Users\Public\tools\` + `downloaded_tool.bin`; NET to `<kali>:8080` |
| Tool execution | Execution / C2 | T1569.002, T1105 | `PsExec64.exe -s` → `PSEXESVC.exe` → `cmd.exe` → `whoami.exe` (SYSTEM); `rclone.exe copyurl` → `pulled.bin` + NET to C2; `ncat.exe` connect (best-effort) — **new unseen tool process + socket nodes** |
| DB attack (read-only) | Credential access / Collection | T1552, T1213 | repeated `mysql.exe -uroot` (no password) → `127.0.0.1:3306`; dumps `dvwa.users` + `mysql.user` |
| C2 beacon | Command & Control | T1071 | `powershell` `GET <kali>:8080/beacon` every ~15 s for the attack duration |
| Persistence | Persistence | T1053.005 | `schtasks.exe` creates task `UpdaterSync` running the tool |

The chain is deliberately **high-volume** (user, 2026-11-04): the scan alone is ~360 connection attempts,
and the beacon + DB bursts repeat for `--attack-seconds` (default 180 ≥ 3 min). That is the point — more
real attacker activity = more flagged nodes = a higher subgraph score **without any detector-side
weighting** (the earlier NET ×2 idea was rolled back). It also means a large NET/process spike: mind the
~7 k ev/s collector ceiling and Loki ingest, and keep the run inside one 5-min window (launch just after
a boundary). The T1105 tools are now **real benign signed utilities (PsExec, rclone; nmap/ncat
best-effort), downloaded and executed** — the added signal is their process + socket nodes, not file
content (the graph hashes paths/`ip:port`, never content). Everything is lab-only and benign: real but
non-malicious tools, read-only DB, isolated host-only subnets, Defender off, disposable VM; no
mimikatz / credential-theft malware.

Only SilkETW-observable techniques are used; registry-only / in-memory-only steps are left out (they
produce no graph nodes). Kernel-File keywords 0x390 cover name/create/read/write, which the FILE
artefacts above fall under.

## The alert cut-off (honesty)

A window alerts only if one subgraph holds roughly ≥ 10 anomalous nodes (the paper's Moderate, DEVIATIONS D8;
it was ≥ 30, set on ONE benign window, until 2026-11-03). The kill chain is sized (a Discovery burst of several processes + the download +
persistence, all rooted at the unseen RCE shell) to clear it, but this is **confirmed on a capture, not
tuned to the attack**: if the window scores < 10, widen the chain or justify a lower `--sg-moderate-from`
from the benign baseline — do not re-tune the threshold to the attack window (that fits the test).

To raise the attack signal **honestly** (not by detector tuning), two [OURS] attack-side levers are used,
disclosed in DEVIATIONS D8: `--attack-seconds` (default 180) loops Discovery + the C2 beacon so the
attack sustains ≥ 3 min of activity (more flagged nodes + recurring NET telemetry) instead of a ~10 s
blip; and the T1105 phase now **downloads and executes real benign tools** (PsExec/rclone; nmap/ncat
best-effort) so their processes + sockets add new unseen nodes. The detector stays paper-faithful SG-Det
(the earlier `--sg-net-weight` NET ×2 idea was **rolled back** 2026-11-04); the Moderate=10 threshold is
unchanged and the live result is still reported as observed.

## Run

See `README.md` for the full recipe. In short: start the capture + benign workload on the target, then
on Kali run `python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://<ws-etw>:3000 --attack-seconds 180`
(only required input is the URL; `--attack-seconds` default 180 sustains the attack ≥ 3 min, 0 = single pass).
