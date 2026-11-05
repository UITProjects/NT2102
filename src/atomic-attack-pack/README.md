# Attack pack — the RCE kill chain

The demo's ONE attack (CLAUDE §8 end goal): a Kali VM exploits the lab's vulnerable service
(`DVWA`, OS command injection → RCE) and delivers a compact MITRE ATT&CK chain that runs on the Windows
target as a child of the RCE shell. Everything is visible to the SilkETW Kernel-Process/File/Network collector and
self-labelled into `C:\lab-labels\attack-labels.jsonl`. Phase/ATT&CK table: `SCENARIOS.md`. Labels and
their limits: `LABELING.md`.

## Safety

- Lab only. The target (`ws-etw`) is isolated by the Proxmox SDN firewall rules
  (`infrastructure/README.md`), not by unplugging a NIC: `it2102`/`it2102k` only route to each other and
  to the internet for provisioning, never to the home LAN. Defender is off on `ws-etw`. `DVWA` is opt-in
  (`vulnlab_enabled=true`) and firewalled to the lab subnets only.
- **No snapshot/rollback exists yet** (known gap — nothing in `infrastructure/` automates a Proxmox
  snapshot restore). `cleanup-all.ps1` is the only reset today and is best effort.
- Kali keeps its own NAT (for apt) but reaches the target only over the routed lab subnets.

## Pieces

```
scenarios/rce-killchain.ps1   the ONE attack: runs on the target (RCE child); scan + REAL tool
                              download+exec + MySQL enumeration + C2 beacon + persistence; custom + labels
tools/launch_exploit.py       Kali side: serves the stage (lib, chain, real tool binaries, /beacon) + fires
                              the DVWA command-injection exploit
lib/attack-lib.ps1            Initialize-AttackLab, Write-AttackLabel, Invoke-LabeledStep/Atomic
tools/win/                    real Windows tool binaries staged by Ansible (gitignored; marker fallback)
cleanup-all.ps1               undo the task + tool procs/PSEXESVC/files + RCE shells (snapshot is better)
manifest.json                 machine-readable technique/indicator map
SCENARIOS.md                  phase table, topology, alert cut-off
LABELING.md                   labels.jsonl -> node labels, honesty caveats
tools/build_node_labels.py    labels.jsonl + converted graph -> node labels (sketch; columns not fixed yet)
```

Ansible copies this pack to the Kali VM at `/opt/attack-pack` (`src/ansible/kali.yml` → `kali_attack`
role; Kali is `10.21.4.30` on its own subnet `it2102k`, the target is `http://10.21.3.10:3000`). The
launcher is run directly from `/opt/attack-pack` and auto-detects the attacker IP, so the only required
flag is `--target`.

## Run during a live session (end-goal path)

1. **Target VM (`ws-etw`)**, RDP desktop:
   ```powershell
   C:\scripts\start-live.ps1 -Status    # confirm the always-on live capture is recording (etwcollect -> Loki)
   ```
   Keep the desktop **idle** (the idle benign baseline); the attack window's own background activity is
   the benign noise. (The always-on live task usually already runs; `start-live.ps1 -Rotate` starts a fresh
   session for the demo.)
2. **Kali attacker** (pack at `/opt/attack-pack`, Ansible-managed, target `http://10.21.3.10:3000`),
   the only required input is the vulnerable web URL:
   ```bash
   python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://10.21.3.10:3000 --attack-seconds 180
   # DVWA's command-injection sink (/vulnerabilities/exec/, field 'ip', security=low) is the default;
   # override only if you changed it: --exploit-path /<path> --exploit-field <field>
   # --attack-seconds ([OURS], default 180 = >= 3 min) loops Discovery + the C2 beacon so the attack
   #   sustains activity across the window instead of a ~10 s blip; 0 = single fast pass. The stage is
   #   served for attack-seconds + 30 automatically. Persistence (schtask) is created once.
   ```
3. Watch that window in the live UI (trainer `ocr-live`): the RCE subtree should push the window to
   **red (≥ Moderate, sg_score ≥ 10, the paper's scale)** while benign windows stay green; `--llm-provider` adds a verdict.
   The detector is paper-faithful SG-Det (no per-type weighting; the earlier NET ×2 idea was rolled back).

Start just after a 5-min window boundary (a 180 s attack fits inside one 5-min window). Whether the chain
clears the cut-off is confirmed here, not tuned (`SCENARIOS.md`, DEVIATIONS D8).

## Selftests (no VM)

```bash
python3 -I tools/launch_exploit.py --selftest       # stage serves lib+chain+tool, contacts no target
pwsh scenarios/rce-killchain.ps1 -SelfTest          # (on the VM) one well-formed label line written
```

Offline route (test capture → convert → `tools/build_node_labels.py`) is not runnable yet: the label
script's columns do not match the converter output (`docs/PROGRESS.md` Open items). The live demo does
not need it.

Labels come from injection + indicator/time matching, not analyst ground truth (`LABELING.md`, DEVIATIONS D6).
