#!/usr/bin/env python3
"""[OURS] Post-conversion node filters that are NOT part of the converter (etw2nodlink.py).

Why a separate module: the converter's sha256 (experiment.json tools.converter) guarantees that live
edges are built by the same event->edge code as the training edges, and the live engine checks it
(engine.load_converter). Filters that run AFTER conversion -- dropping specific already-built nodes --
are not event->edge conversion, so they live here, outside that hash. This matters for the lab-labels
filter below: it is a no-op on an idle training baseline (the file exists only during an attack), so it
does not change any trained model's data and must not force a converter_check MISMATCH / a retrain.

(The watchdog filter, by contrast, lives in etw2nodlink.py and IS hash-covered, because it removes
watchdog nodes that DO appear in idle, so it changes the training baseline -- see etw2nodlink.own_tooling_*
and DEVIATIONS D8. Different place, on purpose.)

Used by:
  - src/pipeline/build_experiment.py  (training build, before windowing)
  - src/live/engine.py::_score        (live scoring, before Alg. 1 and the serialized LLM document)
"""
import csv
import os
import sys
import tempfile


def is_lab_artifact_path(attr):
    """[OURS] our own ground-truth labelling file (c:\\lab-labels\\attack-labels.jsonl, LABELING.md),
    written by the attack itself. Lab plumbing that would not exist in a real deployment; it is dropped
    from live scoring + the serialized LLM document so the literal filename ("attack-labels") cannot talk
    the LLM overlay out of a true alert (DEVIATIONS D9). NOTE: this drives a FILE-NODE drop, NOT a process
    drop (cf. etw2nodlink.is_own_tooling_path) -- the writer here is the attacker's RCE powershell, kept."""
    s = str(attr or "").lower().replace("/", "\\")
    return "\\lab-labels\\" in s


def lab_artifact_node_ids(attrs):
    """attrs: {node_id: attr}. Returns the set of node ids that are our own lab-labels file (FILE nodes)."""
    return {nid for nid, a in attrs.items() if is_lab_artifact_path(a)}


def drop_lab_artifact_nodes(edges_csv, node_attrs_csv):
    """In-place drop of lab-artifact (c:\\lab-labels\\*) FILE nodes + their incident edges from a converted
    <graph>_<split>_edges.csv / _node_attrs.csv pair (build path, before windowing). The writing process is
    kept (only the file node and the edges touching it are removed). Returns stats. No-op on idle baselines,
    where the file does not exist; it guards attack-split / replay builds."""
    attrs = {}
    with open(node_attrs_csv, newline="", encoding="utf-8") as fh:
        rr = csv.reader(fh)
        next(rr, None)
        for row in rr:
            if len(row) >= 3:
                attrs[row[0]] = row[2]
    drop = lab_artifact_node_ids(attrs)
    stats = {"lab_artifact_nodes": len(drop), "edges_dropped": 0, "nodes_dropped": 0}
    if not drop:
        return stats
    etmp = edges_csv + ".tmp"
    with open(edges_csv, newline="", encoding="utf-8") as fin, \
            open(etmp, "w", newline="", encoding="utf-8") as fout:
        rr, ww = csv.reader(fin), csv.writer(fout)
        ww.writerow(next(rr, None))
        for row in rr:
            if row[0] in drop or row[2] in drop:
                stats["edges_dropped"] += 1
                continue
            ww.writerow(row)
    os.replace(etmp, edges_csv)
    ntmp = node_attrs_csv + ".tmp"
    with open(node_attrs_csv, newline="", encoding="utf-8") as fin, \
            open(ntmp, "w", newline="", encoding="utf-8") as fout:
        rr, ww = csv.reader(fin), csv.writer(fout)
        ww.writerow(next(rr, None))
        for row in rr:
            if row and row[0] in drop:
                stats["nodes_dropped"] += 1
                continue
            ww.writerow(row)
    os.replace(ntmp, node_attrs_csv)
    return stats


def _selftest():
    # the ground-truth file node c:\lab-labels\* is dropped, but the attacker PROCESS that wrote it (and
    # its OTHER edges) is kept -- a FILE-node drop, not a process drop. So "attack-labels" never reaches
    # the LLM document, yet the attack stays.
    ok = (lab_artifact_node_ids({"fL": "C:\\lab-labels\\attack-labels.jsonl", "fK": "c:\\users\\pub\\x"}) == {"fL"}
          and is_lab_artifact_path("C:\\lab-labels\\attack-labels.jsonl")
          and is_lab_artifact_path("c:/lab-labels/x.jsonl")                 # forward slashes normalised
          and not is_lab_artifact_path("c:\\users\\pub\\killchain.ps1")
          and not is_lab_artifact_path(""))
    header = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
    with tempfile.TemporaryDirectory() as tmp:
        ep, np_ = os.path.join(tmp, "e.csv"), os.path.join(tmp, "n.csv")
        with open(ep, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerow(["pA", "PROCESS", "fL", "FILE", "Write", 1])   # attacker -> lab-labels (dropped)
            w.writerow(["pA", "PROCESS", "fK", "FILE", "Read", 2])    # attacker -> ordinary file (kept)
        with open(np_, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["node", "node_type", "node_attr"])
            w.writerow(["pA", "PROCESS", "c:\\windows\\system32\\windowspowershell\\v1.0\\powershell.exe"])
            w.writerow(["fL", "FILE", "c:\\lab-labels\\attack-labels.jsonl"])
            w.writerow(["fK", "FILE", "c:\\users\\pub\\x"])
        st = drop_lab_artifact_nodes(ep, np_)
        with open(ep, encoding="utf-8") as fh:
            e_after = list(csv.reader(fh))
        with open(np_, encoding="utf-8") as fh:
            n_after = fh.read()
    ok = (ok and st == {"lab_artifact_nodes": 1, "edges_dropped": 1, "nodes_dropped": 1}
          and len(e_after) == 2                              # header + the kept Read edge
          and "pA" in n_after and "fK" in n_after            # attacker process + its other file kept
          and "lab-labels" not in n_after)                   # the ground-truth node is gone
    # a no-op when no lab-labels node is present (the idle-baseline case)
    with tempfile.TemporaryDirectory() as tmp:
        ep, np_ = os.path.join(tmp, "e.csv"), os.path.join(tmp, "n.csv")
        with open(ep, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerow(["pA", "PROCESS", "fK", "FILE", "Read", 1])
        with open(np_, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["node", "node_type", "node_attr"])
            w.writerow(["fK", "FILE", "c:\\users\\pub\\x"])
        st0 = drop_lab_artifact_nodes(ep, np_)
    ok = ok and st0 == {"lab_artifact_nodes": 0, "edges_dropped": 0, "nodes_dropped": 0}
    print("selftest", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else _selftest())
