# Attack pack — the RCE kill chain

The demo's ONE attack (CLAUDE §8 end goal): a Kali VM exploits the lab's vulnerable service
(`DVWA`, OS command injection → RCE) and delivers a compact MITRE ATT&CK chain that runs on the Windows
target as a child of the RCE shell. Everything is visible to the SilkETW Kernel-Process/File/Network collector and
self-labelled into `C:\lab-labels\attack-labels.jsonl`. Phase/ATT&CK table: `SCENARIOS.md`. Labels and
their limits: `LABELING.md`.

## Safety

- Lab only. The target (`ws-etw`) is isolated during the run: `LAB_ISOLATED=1 vagrant reload ws-etw`
  (NAT unplugged, host-only network only). Defender is off there. `DVWA` is opt-in
  (`vulnlab_enabled=true`) and firewalled to the lab subnet only.
- **Snapshot before running**; `vagrant snapshot restore ws-etw clean-baseline` is the clean reset.
  `cleanup-all.ps1` is best effort.
- Kali keeps its NAT (for apt) but attacks the target only over the host-only subnet.

## Pieces

```
scenarios/rce-killchain.ps1   the ONE attack: runs on the target (RCE child); custom steps + labels
tools/launch_exploit.py       Kali side: serves the stage + fires the DVWA command-injection exploit
lib/attack-lib.ps1            Initialize-AttackLab, Write-AttackLabel, Invoke-LabeledStep/Atomic
cleanup-all.ps1               undo the task + downloaded tool + RCE shells (snapshot restore is better)
manifest.json                 machine-readable technique/indicator map
SCENARIOS.md                  phase table, topology, alert cut-off
LABELING.md                   labels.jsonl -> node labels, honesty caveats
tools/build_node_labels.py    labels.jsonl + converted graph -> node labels (sketch; columns not fixed yet)
```

The launcher + scripts are mounted into the Kali VM at `/opt/attack-pack` by Vagrant (a synced folder,
`src/Vagrantfile`); the launcher is run directly from there. Kali is a local-only box, not Ansible-managed.

## Run during a live session (end-goal path)

1. **Target VM (`ws-etw`)**, RDP desktop, two shells:
   ```powershell
   C:\scripts\start-live.ps1            # shell 1: start the test capture; Alloy ships it to Loki
   C:\scripts\06-benign-workload.ps1    # shell 2: benign noise, so the window is not all-malicious
   ```
2. **Kali attacker** (pack mounted at `/opt/attack-pack`), the only required input is the vulnerable web URL:
   ```bash
   python3 -I /opt/attack-pack/tools/launch_exploit.py --target http://10.21.2.10:3000
   # DVWA's command-injection sink (/vulnerabilities/exec/, field 'ip', security=low) is the default;
   # override only if you changed it: --exploit-path /<path> --exploit-field <field>
   ```
3. Watch that window in the live UI (trainer `ocr-live`): the RCE subtree should push the window to
   **red (≥ Moderate, sg_score ≥ 30)** while benign windows stay green; `--llm-provider` adds a verdict.

Start just after a 5-min window boundary. Whether the chain clears the cut-off is confirmed here, not
tuned (`SCENARIOS.md`, DEVIATIONS D8).

## Selftests (no VM)

```bash
python3 -I tools/launch_exploit.py --selftest       # stage serves lib+chain+tool, contacts no target
pwsh scenarios/rce-killchain.ps1 -SelfTest          # (on the VM) one well-formed label line written
```

Offline route (test capture → convert → `tools/build_node_labels.py`) is not runnable yet: the label
script's columns do not match the converter output (`docs/PROGRESS.md` Open items). The live demo does
not need it.

Labels come from injection + indicator/time matching, not analyst ground truth (`LABELING.md`, DEVIATIONS D6).
