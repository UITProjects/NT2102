# Turning attack runs into ground-truth labels

Each scenario appends a JSON line to `C:\lab-labels\attack-labels.jsonl`:

```json
{"host":"SimulatedW10","tactic":"credential-access","technique":"T1003.001","source":"atomic-red-team",
 "note":"LSASS memory dump","start_utc":"2026-10-10T07:15:03.12Z","end_utc":"2026-10-10T07:15:41.88Z",
 "malicious":true,"process_indicators":["rundll32.exe"],"file_indicators":["*\\lsass*.dmp"],"network_indicators":[]}
```

That file is the **label oracle**; it is joined against the converted graph to produce labels.

## Two granularities

- **Window-level (easy):** a window is `attacked` if `[window_start, window_end]` overlaps any attack's
  `[start_utc, end_utc]`. One boolean per window; coarse.
- **Node-level (paper-aligned, OCR-APT's node P/R/F1):** a node is malicious iff it **matches an indicator**
  AND is **active inside the attack time range**. Output `node_labels.csv` (`node_id,label,matched_technique`).

Converter node attributes are `node,node_type,node_attr` with types `PROCESS/FILE/NET` and **no per-node
timestamp**, so the activity interval is the node's window: `node → window → windows.csv` times.
Indicator-only matching (no time constraint) over-labels; avoid it. `tools/build_node_labels.py` is a sketch
that does not match these columns yet (`docs/PROGRESS.md` Open items).

**Live session** (end-goal path): per window, `src/live/sessions/<id>/windows/<wid>/node_attrs.csv` plus the
window times in `sessions/<id>/windows.csv`; same join.

## Limits (keep in mind for any claim)

- **Weak oracle:** a benign `powershell.exe` inside an attack window matches the attacker's indicator. The time
  range narrows this; a process key (PID + start time) would narrow it further.
- **Oracle errors move the metric:** over-labelling hurts the detector's apparent precision, under-labelling
  flatters it.
- **Embed attacks in benign noise:** run the attack while the always-on live capture is recording natural
  desktop activity (browse, docs), or every node in an attack window is malicious and the task collapses to
  "did anything happen".
- **Class imbalance:** malicious nodes are a tiny fraction; prefer P/R/F1 over accuracy.

[PAPER] OCR-APT labels come from DARPA ground truth / analyst reports (§7 flags label-provenance issues even
there). [OURS] injection gives exact knowledge of *what* ran, with a coarse *which-nodes* mapping. Claim:
detection of injected ATT&CK techniques on our capture, not parity with the paper. Logged as DEVIATIONS D6.
