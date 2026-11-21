# src/atomic-attack-pack/: RCE kill chain

The demo attack: from a Kali VM, exploit DVWA's OS command injection on `ws-etw-0`, then run an
ATT&CK-mapped post-exploitation chain as a child of the RCE shell, using real signed tools rather than malware.
Lab only: the target subnets cannot reach the home LAN.

## Outcome

- 3 of 3 live runs turned their window red with an LLM `attack` verdict, with no detection rule written for it.
- High-volume by design (scan ≈ 360 connection attempts, beacon + DB bursts over 180 s), so the attack stands
  out on its own, with no detector-side weighting.
- Log clearing (`wevtutil.exe`) is captured but not flagged on its own; it looks normal by its own behaviour.

## Architecture

```
Kali  launch_exploit.py ─► DVWA /vulnerabilities/exec/ (command injection) ─► PowerShell cradle on ws-etw-0
      serves stage (lib, chain, tool binaries, /beacon) ◄─────────────────── rce-killchain.ps1 downloads + runs
Chain Discovery → T1046 scan → T1105 tool download + run (PsExec, rclone, nmap, ncat) → MySQL dump
      → C2 beacon → T1053.005 scheduled task → T1070 clear logs + delete tools; every step self-labelled
```

Labels come from injection + indicator/time matching, not analyst ground truth (`LABELING.md`, DEVIATIONS D6).

## Key files

| file | what |
|---|---|
| `scenarios/rce-killchain.ps1` | the chain (runs on the target) |
| `tools/launch_exploit.py` | Kali side: stage server + exploit |
| `lib/attack-lib.ps1` · `cleanup-all.ps1` | labelled-step helpers · best-effort reset |
| `SCENARIOS.md` · `LABELING.md` · `manifest.json` | phase table · label scheme and caveats · technique map |
