# OCR-APT Attack Pack (Atomic Red Team, beginner)

Generate labeled **`attacked`** telemetry for the NT2102 / OCR-APT reproduction, from simple
single techniques up to a full APT kill chain — every scenario chosen to be visible to your
SilkETW Kernel-Process/File/Network collector, and every run self-labeled into
`C:\lab-labels\attack-labels.jsonl`.

## 0. Safety (read once)

- Run **only** inside the throwaway W10 lab VM (host-only adapter, Defender off — already true
  in your build). Never on your work machine or anything on a real network.
- **Snapshot the VM before running.** Restoring the snapshot is the cleanest reset; `cleanup-all.ps1`
  is a best-effort alternative.
- Scenarios 05 (LSASS dump) and 09 (clear logs) and the full chain (10) perform **real** actions
  and need an **elevated** PowerShell ("Run as administrator").

## 1. Atomic Red Team in one screen

- **MITRE ATT&CK** is the taxonomy: *tactics* (the why: Discovery, Persistence, C2, Exfil…) and
  *techniques* (the how: `T1003.001`).
- **Atomic Red Team** ships one folder per technique under `atomics/`, each with a YAML defining
  *atomic tests*: an executor, the exact command, input arguments, prerequisites, and a cleanup
  command. Inspectable and deterministic.
- **`Invoke-AtomicTest`** is the PowerShell runner. The lifecycle you repeat:

  ```powershell
  Invoke-AtomicTest T1003.001 -ShowDetailsBrief           # list the tests + numbers
  Invoke-AtomicTest T1003.001 -TestNumbers 1 -ShowDetails # read the exact command (don't run blind)
  Invoke-AtomicTest T1003.001 -TestNumbers 1 -GetPrereqs  # fetch files it needs
  Invoke-AtomicTest T1003.001 -TestNumbers 1              # run
  Invoke-AtomicTest T1003.001 -TestNumbers 1 -Cleanup     # undo
  ```

  PowerShell notes (you come from Linux): `-ShowDetails` is `cat`-before-`run`. `-TestNumbers`
  picks which test in the YAML — **numbering drifts**, so always confirm with `-ShowDetailsBrief`.
  `Txxxx` and the flags are just string/switch arguments.

- **Your collector only sees process / file / network.** Techniques touching only the registry or
  living purely in memory produce **no graph nodes**. This pack avoids them on purpose.

- **Simple -> APT = atoms -> molecule.** One atomic = a few nodes. An APT = a chain of atomics in
  kill-chain order = a connected malicious subgraph (what OCR-APT Alg. 1 recovers). You chain by
  hand here, which also gives you precise labels. (Caldera automates chaining but costs label control.)

## 2. Install (you already have this in the VM — here for reproducibility)

```powershell
# from an elevated PowerShell, inside the lab VM only
Set-ExecutionPolicy Bypass -Scope Process -Force
IEX (IWR 'https://raw.githubusercontent.com/redcanaryco/invoke-atomicredteam/master/install-atomicredteam.ps1' -UseBasicParsing)
Install-AtomicRedTeam -getAtomics -Force
Import-Module "C:\AtomicRedTeam\invoke-atomicredteam\Invoke-AtomicRedTeam.psd1" -Force
```

`IWR` = Invoke-WebRequest (curl), `IEX` = Invoke-Expression (pipe-to-shell — only acceptable
here because it's a throwaway lab). Atomics land in `C:\AtomicRedTeam\atomics`.

## 3. Run

```powershell
# 1) (optional but recommended) start the telemetry sink on a reachable host / this VM:
.\lib\Start-LocalSink.ps1              # binds 0.0.0.0:8888

# 2) start your SilkETW TEST capture (your tooling):
#    etwctl start -Split test

# 3) run the attacks (elevated). Point beacons at the sink if not the default 10.21.2.10:
$env:C2_SINK_HOST = "10.21.2.10"; $env:C2_SINK_PORT = "8888"
.\run-all-attacks.ps1                  # APT chain (default)
# or:  .\run-all-attacks.ps1 -Mode Individual   # scenarios 01..09 in sequence
#    run 06-benign-workload.ps1 at the same time so attacks sit in realistic noise

# 4) stop + convert (your tooling):  etwctl stop ; etwctl convert

# 5) build labels:
python .\tools\build_node_labels.py C:\lab-labels\attack-labels.jsonl `
       C:\lab-out\etw\SimulatedW10_test_node_attrs.csv node_labels.csv
```

## 4. Files

```
README.md                 this file
SCENARIOS.md              per-scenario detail + how to verify each
LABELING.md               labels.jsonl -> node labels, with honesty caveats (READ THIS)
manifest.json             machine-readable scenario/technique/indicator map
run-all-attacks.ps1       orchestrator (Chain | Individual)
cleanup-all.ps1           best-effort cleanup (snapshot restore is better)
lib/attack-lib.ps1        Initialize-AttackLab, Write-AttackLabel, Invoke-LabeledAtomic/Step
lib/Start-LocalSink.ps1   throwaway TCP/HTTP sink for C2 + exfil telemetry
scenarios/01..10          the scenarios, easy -> hard
tools/build_node_labels.py   join labels.jsonl with your converted graph (adapt column names)
```

## 5. Honesty reminder

These labels come from **known-attack injection + indicator/time matching**, not analyst-verified
ground truth. You can claim detection of injected ATT&CK techniques on your own capture; you cannot
claim parity with OCR-APT's DARPA numbers. Keep attacks embedded in benign noise or your metrics are
meaningless. Details in `LABELING.md`.
