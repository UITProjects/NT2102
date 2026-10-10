#!/usr/bin/env python3
"""
build_node_labels.py  —  turn attack-labels.jsonl (the oracle) into node-level labels.

This is a SKETCH. Adapt the column names (marked TODO) to your actual converter output
(SimulatedW10_test_node_attrs.csv). The labeling rule here is:

    a node is malicious  IFF
        it matches an attack indicator (process image / file path / network endpoint)
        AND it is active within that attack's [start_utc, end_utc] window.

Honesty notes (read LABELING.md):
  * Indicator match alone over-labels (benign powershell.exe during an attack window looks
    identical to the attack's powershell.exe). The time constraint narrows it; PID/GUID would
    narrow it further if your node attrs carry it.
  * This is a weak oracle, not analyst-verified ground truth. Report it as such.
"""
import json, sys, fnmatch
from datetime import datetime, timezone
import pandas as pd

LABELS_JSONL = sys.argv[1] if len(sys.argv) > 1 else "attack-labels.jsonl"
NODE_ATTRS   = sys.argv[2] if len(sys.argv) > 2 else "SimulatedW10_test_node_attrs.csv"
OUT          = sys.argv[3] if len(sys.argv) > 3 else "node_labels.csv"

# --- TODO: map these to your real column names ------------------------------
COL_NODE_ID   = "node_id"      # unique node id in your converted graph
COL_NODE_TYPE = "type"         # 'process' | 'file' | 'network' (your scheme)
COL_NAME      = "name"         # process image name OR file path OR 'ip:port' depending on type
COL_TS        = "timestamp"    # node activity time, UTC. If you only have per-WINDOW times,
                               # join node->window->window_start/end instead (see LABELING.md).
# ---------------------------------------------------------------------------

def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)

def load_labels(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            r["_start"] = parse_utc(r["start_utc"])
            r["_end"]   = parse_utc(r["end_utc"])
            out.append(r)
    return out

def matches(name: str, patterns) -> bool:
    name = (name or "").lower()
    for p in patterns:
        p = p.lower()
        # exact process image, substring, or glob all count as a hit
        if p in name or fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name, f"*{p}*"):
            return True
    return False

def indicators_for_type(rec, ntype):
    if ntype == "process": return rec.get("process_indicators", [])
    if ntype == "file":    return rec.get("file_indicators", [])
    if ntype == "network": return rec.get("network_indicators", [])
    return []

def main():
    labels = load_labels(LABELS_JSONL)
    df = pd.read_csv(NODE_ATTRS)
    df["label"] = 0
    df["matched_technique"] = ""

    has_ts = COL_TS in df.columns
    for i, row in df.iterrows():
        ntype = str(row.get(COL_NODE_TYPE, "")).lower()
        name  = str(row.get(COL_NAME, ""))
        ts    = parse_utc(str(row[COL_TS])) if has_ts and pd.notna(row.get(COL_TS)) else None
        for rec in labels:
            if ts is not None and not (rec["_start"] <= ts <= rec["_end"]):
                continue  # outside this attack's window
            if matches(name, indicators_for_type(rec, ntype)):
                df.at[i, "label"] = 1
                df.at[i, "matched_technique"] = rec["technique"]
                break

    df[[COL_NODE_ID, "label", "matched_technique"]].to_csv(OUT, index=False)
    mal = int(df["label"].sum())
    print(f"wrote {OUT}: {mal} malicious / {len(df)} nodes "
          f"({mal/len(df):.2%}).  time-constrained={has_ts}")
    if not has_ts:
        print("WARNING: no per-node timestamp column — labels are indicator-only and will "
              "over-label. Join node->window times (LABELING.md) to add the time constraint.")

if __name__ == "__main__":
    main()
