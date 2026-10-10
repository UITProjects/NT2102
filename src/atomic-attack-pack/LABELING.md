# Turning attack runs into ground-truth labels

Each scenario appends a JSON line to `C:\lab-labels\attack-labels.jsonl`:

```json
{"host":"SimulatedW10","tactic":"credential-access","technique":"T1003.001","source":"atomic-red-team",
 "note":"LSASS memory dump","start_utc":"2026-10-10T07:15:03.12Z","end_utc":"2026-10-10T07:15:41.88Z",
 "malicious":true,"process_indicators":["rundll32.exe"],"file_indicators":["*\\lsass*.dmp"],"network_indicators":[]}
```

That file is your **label oracle**. You join it against your converted graph to produce labels.

## Two granularities

**Window-level (easy).** A capture window is `attacked` if its `[window_start, window_end]`
overlaps any attack's `[start_utc, end_utc]`. One boolean per window. Good for a first
end-to-end run; weak as a metric because a window is a coarse unit.

**Node-level (paper-aligned).** A node is malicious iff it **matches an indicator** AND is
**active inside the attack window**. This matches OCR-APT's node-level P/R/F1. Use
`tools/build_node_labels.py` (adapt the TODO column names) to produce `node_labels.csv`
(`node_id,label,matched_technique`).

If your node attributes carry no per-node timestamp, join `node -> window -> windows.csv`
times and use the window's time range as the node's activity interval. Indicator-only
matching (no time constraint) will over-label and should be avoided.

## Why this matters for your claims (do not skip)

- **This is a weak oracle.** Indicator + time is not analyst-verified truth. A benign
  `powershell.exe` running inside an attack window matches the same indicator as the
  attacker's `powershell.exe`. The time window narrows this; a PID/process-GUID column
  (if your converter keeps one) narrows it further and is worth adding.
- **Precision vs recall of the ORACLE, not the detector.** Over-labeling (false malicious)
  inflates the malicious class and makes the detector look worse on precision; under-labeling
  makes it look better than it is. Either way the metric is only as trustworthy as the oracle.
- **Embed attacks in benign noise.** If your test capture is attacks-only, every node in an
  attack window is malicious and the task collapses to "did anything happen" — F1 looks great
  and means nothing. Run the attack pack *alongside* `06-benign-workload.ps1` so benign and
  malicious nodes coexist in the same windows. This is the single biggest honesty lever.
- **Class imbalance.** Even embedded, malicious nodes are a tiny fraction. Report the malicious
  rate printed by `build_node_labels.py`, and prefer P/R/F1 over accuracy (OCR-APT does).

## Relation to the paper

[PAPER] OCR-APT labels come from DARPA ground-truth / analyst reports; §7 flags label-provenance
problems even there. [OURS] Known-attack injection gives you exact knowledge of *what* you ran,
traded against a coarse *which-nodes* mapping. Net: you can claim detection of injected ATT&CK
techniques on your own capture; you cannot claim parity with the paper's DARPA numbers. Log this
in the DEVIATIONS record.
