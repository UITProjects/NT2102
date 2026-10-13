# Scenarios (easy -> hard)

All chosen to be **observable** by SilkETW Kernel-Process/File/Network. Registry-only and
in-memory-only techniques are excluded on purpose: they produce no graph nodes.

| # | File | Tactic | ATT&CK | Node types | Admin | Source |
|---|------|--------|--------|-----------|-------|--------|
| 01 | `01-discovery.ps1` | Discovery | T1033, T1082, T1087.001, T1057 | process | no | ART |
| 02 | `02-lolbin-exec.ps1` | Defense Evasion | T1218.011/.010/.005 | process (+file/net) | no | ART |
| 03 | `03-ingress-transfer.ps1` | C2 | T1105 | process+network+file | no | custom |
| 04 | `04-persistence.ps1` | Persistence | T1053.005, T1547.001 | process+file | partial | ART |
| 05 | `05-credential-access-lsass.ps1` | Credential Access | T1003.001 | process+file | **yes** | ART |
| 06 | `06-c2-beacon.ps1` | C2 | T1071.001 | process+network | no | custom |
| 07 | `07-collection-staging.ps1` | Collection | T1005, T1074.001, T1560.001 | process+file | no | custom |
| 08 | `08-exfiltration.ps1` | Exfiltration | T1041 | process+file+network | no | custom |
| 09 | `09-defense-evasion.ps1` | Defense Evasion | T1070.001, T1070.006 | process+file | **yes** | ART |
| 10 | `10-apt-chain.ps1` | full kill chain | all of the above | process+file+network | **yes** | mixed |

"ART" = runs a real Atomic Red Team test via `Invoke-AtomicTest`. "custom" = a small scripted
action (the C2 sink target and staging paths need to be deterministic to label cleanly; the
corresponding ART atomics hit external test infrastructure you don't control).

Visibility doubts [INFERENCE], check on a capture: **05** LSASS handle access is not in the collected
events (only rundll32 and the `.dmp` write should show); **09** timestomp is a set-information call, not in
the Kernel-File keywords 0x390 (name/create/read/write).

## Atomic Red Team in brief

- ATT&CK *tactic* = the why (Discovery, C2…); *technique* = the how (`T1003.001`). ART ships one YAML per
  technique with *atomic tests*: executor, exact command, inputs, prerequisites, cleanup.
- `Invoke-AtomicTest` is the PowerShell runner. Always read before running; test numbers drift between ART
  versions, so confirm with `-ShowDetailsBrief` and fix `-TestNumbers` in the scenario if needed:

```powershell
Invoke-AtomicTest T1003.001 -ShowDetailsBrief             # list tests + their numbers (like `ls`)
Invoke-AtomicTest T1003.001 -TestNumbers 1 -ShowDetails   # print the literal command (like `cat` before run)
Invoke-AtomicTest T1003.001 -TestNumbers 1 -GetPrereqs    # fetch files the test needs
Invoke-AtomicTest T1003.001 -TestNumbers 1                # run
Invoke-AtomicTest T1003.001 -TestNumbers 1 -Cleanup       # undo
```

## Difficulty, and the alert cut-off

A window alerts only if one subgraph holds roughly ≥ 30 anomalous nodes (DEVIATIONS D8). [INFERENCE] from
the node counts below, single scenarios probably stay under it; 10 is the best candidate. Untested.

- **01–03**: single techniques, few nodes, benign-looking.
- **04–06**: distinctive structure (persistence artefacts, a dump-writing process, a single-endpoint beacon).
- **07–09**: multi-node patterns (collection fan-in, exfil linkage, log clearing).
- **10**: one connected subgraph across all three node types, in kill-chain order: what Alg. 1 recovers.
