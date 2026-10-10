# Scenarios (easy -> hard)

All chosen to be **observable** by SilkETW Kernel-Process/File/Network. Registry-only and
in-memory-only techniques are deliberately excluded — they produce no nodes in your graph.

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

## Verify a scenario before you trust it

For every ART technique, read the exact command first (never run blind):

```powershell
Invoke-AtomicTest T1003.001 -ShowDetailsBrief   # which tests exist, and their numbers
Invoke-AtomicTest T1003.001 -TestNumbers 1 -ShowDetails   # the literal command, inputs, cleanup
```

If the test number in a scenario doesn't match what `-ShowDetailsBrief` shows (the repo
reorders over time), fix the `-TestNumbers` value in that scenario. This is expected.

## Difficulty, and why

- **01–03** are low/medium signal: single techniques, few nodes, benign-looking. They test
  whether your detector stays quiet on ordinary activity (precision).
- **04–06** are clear malicious signals with distinctive structure (persistence artifacts, an
  LSASS-touching process writing a dump, a periodic single-endpoint beacon).
- **07–09** add multi-node patterns (collection fan-in, exfil linkage, late-stage log-clearing).
- **10** is the real target: a connected subgraph across all three node types with temporal
  ordering — what OCR-APT's anomalous-subgraph construction (Alg. 1) is designed to recover.
