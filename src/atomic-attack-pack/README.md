# Attack pack (Atomic Red Team + custom)

Attacks for the live demo: 10 scenarios from single ATT&CK techniques up to a chained kill chain, each
visible to the SilkETW Kernel-Process/File/Network collector and self-labelled into
`C:\lab-labels\attack-labels.jsonl`. Scenario table: `SCENARIOS.md`. Labels and their limits: `LABELING.md`.
The target demo (CVE exploit → RCE → ATT&CK follow-on) is not in this pack yet (`docs/PROGRESS.md`).

## Safety

- Run **only** inside the lab VM, isolated: `LAB_ISOLATED=1 vagrant reload` (NAT unplugged, host-only
  network only). Defender is off there (`01-base.ps1`). Never on another machine.
- **Snapshot before running** (`vagrant snapshot restore clean-baseline` resets); `cleanup-all.ps1` is best effort.
- 05 (LSASS dump), 09 (clear logs) and 10 (chain) do **real** actions and need an elevated PowerShell.
- The pack is not a synced folder (the VM never sees the project dir): copy it in by hand.

## Install

Done by the legacy `src/legacy/vagrant-provisioners/02-tools.ps1` (not yet ported to Ansible): Invoke-AtomicRedTeam (PSGallery) + atomics in `C:\AtomicRedTeam`.
It has pin slots for both (still empty, see `docs/PROGRESS.md`); do not use the upstream `IEX (IWR …)` installer.

## Run during a live session (end-goal path)

```powershell
# host: ./serve.sh            VM shell 1: C:\scripts\08-live-shipper.ps1   (capture + ship)
# VM shell 2: C:\scripts\06-benign-workload.ps1   (benign noise, as in training)
.\lib\Start-LocalSink.ps1                             # VM shell 3, optional: C2/exfil sink on 0.0.0.0:8888
$env:C2_SINK_HOST = "10.21.2.10"; $env:C2_SINK_PORT = "8888"
.\run-all-attacks.ps1                                 # VM shell 4, elevated: chain (default) | -Mode Individual
```

Start just after a 5-min boundary and watch that window in the UI. Whether a scenario is big enough to pass
the alert cut-off (≥ 30) is untested (`SCENARIOS.md`).

Offline route (test capture → `etwctl convert -Split test` → `tools/build_node_labels.py`) is **not runnable
yet**: the label script's columns do not match the converter output (`docs/PROGRESS.md` Open items).

## Files

```
SCENARIOS.md              scenario table, ART workflow, expected visibility
LABELING.md               labels.jsonl → node labels, honesty caveats
manifest.json             machine-readable scenario/technique/indicator map
run-all-attacks.ps1       orchestrator (Chain | Individual)
cleanup-all.ps1           best-effort cleanup (snapshot restore is better)
lib/attack-lib.ps1        Initialize-AttackLab, Write-AttackLabel, Invoke-LabeledAtomic/Step
lib/Start-LocalSink.ps1   throwaway TCP/HTTP sink for C2 + exfil telemetry
scenarios/01..10          the scenarios, easy → hard
tools/build_node_labels.py  labels.jsonl + converted graph → node labels (sketch, see LABELING.md)
```

Labels come from injection + indicator/time matching, not analyst ground truth (`LABELING.md`, DEVIATIONS D6).
