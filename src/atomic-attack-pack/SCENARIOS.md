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
| Ingress transfer | Command & Control | T1105 | `powershell` writes `C:\Users\Public\downloaded_tool.bin`; NET to `<kali>:8080` |
| Persistence | Persistence | T1053.005 | `schtasks.exe` creates task `UpdaterSync` running the tool |

Only SilkETW-observable techniques are used; registry-only / in-memory-only steps are left out (they
produce no graph nodes). Kernel-File keywords 0x390 cover name/create/read/write, which the FILE
artefacts above fall under.

## The alert cut-off (honesty)

A window alerts only if one subgraph holds roughly ≥ 30 anomalous nodes (DEVIATIONS D8), a threshold set
on ONE benign window. The kill chain is sized (a Discovery burst of several processes + the download +
persistence, all rooted at the unseen RCE shell) to clear it, but this is **confirmed on a capture, not
tuned to the attack**: if the window scores < 30, widen the chain or justify a lower `--sg-moderate-from`
from the benign baseline — do not re-tune the threshold to the attack window (that fits the test).

## Run

See `README.md` for the full recipe. In short: start the capture + benign workload on the target, then
on Kali run `python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://<ws-etw>:3000` (only input is the URL).
