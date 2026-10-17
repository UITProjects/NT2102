"""check_rdf_input.py - verify a <graph>_<split>_{edges,node_attrs}.csv pair meets what
OCR-APT's transform_to_RDF.py (--dataset nodlink) and encode_to_PyG.py assume.

  python check_rdf_input.py --dir <folder> [--graph SimulatedW10] [--split train] [--out check.json]
  exit 0 = OK (warnings allowed), 1 = contract violated, 2 = files missing

Contract (repo refs = OCR-APT transform_to_RDF.py / encode_to_PyG.py):
  edges   header source-id,source-type,destination-id,destination-type,edge-type,timestamp
          (pd.read_csv with header, l.82; column names used l.89, l.219-227, l.253-260)
  attrs   header node,node_type,node_attr (l.158-162; node_type only read with --adjust-uuid)
  ids, types, edge types are pasted into IRIs: prefix + "node/" + id, prefix + edge-type (l.134, l.222)
          -> no whitespace or IRI-illegal characters
  node_attr is pasted between double quotes into N-Triples (l.162, l.236-237) -> no " tab or newline
          (backslashes are fine: the .nt writer doubles them, l.403-404)
  timestamp integer epoch MILLISECONDS for SimulatedW10 (encode_to_PyG.py l.167-168 divides gaps by 1000)
  one type per node id, or get_all_nodes_df (l.253-260) emits two node-type triples for it
Stdlib only.
"""
import argparse, collections, csv, json, os, re, sys
from datetime import datetime, timezone

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
NODE_HEADER = ["node", "node_type", "node_attr"]
IRI_BAD = re.compile(r'[\s<>"{}|^`\\]')          # chars not allowed raw inside an IRI
MS_LO, MS_HI = 946684800000, 4102444800000      # 2000-01-01 .. 2100-01-01 in epoch ms
MIN_NODE_REPR = 5                               # transform_to_RDF --min-node-representation default (l.20)


def check(d, graph, split):
    edges = os.path.join(d, f"{graph}_{split}_edges.csv")
    attrs = os.path.join(d, f"{graph}_{split}_node_attrs.csv")
    for p in (edges, attrs):
        if not os.path.isfile(p):
            return {"ok": False, "errors": [f"missing {p}"], "warnings": []}, 2
    err, warn = collections.Counter(), collections.Counter()
    ex = {}                                     # first example per problem

    def bad(kind, sample, hard=True):
        (err if hard else warn)[kind] += 1
        ex.setdefault(kind, sample)

    node_type, edge_types = {}, collections.Counter()
    ts_min, ts_max, n_edges = None, None, 0
    with open(edges, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        hdr = next(r, None)
        if hdr != EDGE_HEADER:
            return {"ok": False, "errors": [f"edges header {hdr} != {EDGE_HEADER}"], "warnings": []}, 1
        for row in r:
            n_edges += 1
            if len(row) != 6:
                bad("edge_row_width", row); continue
            s, st, o, ot, et, ts = row
            for nid, nt in ((s, st), (o, ot)):
                if not nid or IRI_BAD.search(nid):
                    bad("node_id_not_iri_safe", nid)
                if not nt or IRI_BAD.search(nt):
                    bad("node_type_not_iri_safe", nt)
                prev = node_type.setdefault(nid, nt)
                if prev != nt:
                    bad("node_has_two_types", f"{nid}: {prev} / {nt}")
            et_last = et.split("/")[-1]         # SimulatedW10: edge-type split on '/' (l.89-90)
            if not et_last or IRI_BAD.search(et_last):
                bad("edge_type_not_iri_safe", et)
            edge_types[et_last] += 1
            try:
                t = int(ts)
            except ValueError:
                bad("timestamp_not_int", ts); continue
            if not MS_LO <= t <= MS_HI:
                bad("timestamp_not_epoch_ms", ts); continue
            ts_min = t if ts_min is None else min(ts_min, t)
            ts_max = t if ts_max is None else max(ts_max, t)

    attr_nodes, n_attrs = set(), 0
    with open(attrs, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        hdr = next(r, None)
        if hdr != NODE_HEADER:
            bad("attrs_header", hdr)
        else:
            for row in r:
                n_attrs += 1
                if len(row) != 3:
                    bad("attr_row_width", row); continue
                n, nt, a = row
                if n in attr_nodes:
                    bad("attr_duplicate_node", n, hard=False)
                attr_nodes.add(n)
                if any(c in a for c in '"\t\r\n'):
                    bad("attr_breaks_ntriples", a)
                if n in node_type and node_type[n] != nt:
                    bad("attr_type_differs_from_edges", f"{n}: {node_type[n]} / {nt}")
                if n not in node_type:
                    bad("attr_for_node_without_edges", n, hard=False)

    missing = [n for n in node_type if n not in attr_nodes]
    if missing:
        warn["node_without_attr"] += len(missing)
        ex.setdefault("node_without_attr", missing[0])
    type_counts = collections.Counter(node_type.values())
    for t, c in type_counts.items():
        if c <= MIN_NODE_REPR:                  # would be dropped by get_remove_nodeType_lst (l.266-288)
            bad(f"node_type_rare:{t}", c, hard=False)

    def utc(ms):
        return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

    res = {
        "ok": not err, "dir": d, "graph": graph, "split": split,
        "edges": n_edges, "attr_rows": n_attrs, "nodes": len(node_type),
        "node_types": dict(type_counts.most_common()), "edge_types": dict(edge_types.most_common()),
        "time_utc": [utc(ts_min), utc(ts_max)],
        "minutes": None if ts_min is None else round((ts_max - ts_min) / 60000, 1),
        "errors": dict(err), "warnings": dict(warn), "examples": {k: str(v)[:200] for k, v in ex.items()},
    }
    return res, (0 if not err else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True)
    ap.add_argument("--graph", default="SimulatedW10")
    ap.add_argument("--split", default="train")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res, code = check(a.dir, a.graph, a.split)
    txt = json.dumps(res, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(txt)
    print(json.dumps({k: res.get(k) for k in ("ok", "edges", "nodes", "minutes", "errors", "warnings")}))
    return code


if __name__ == "__main__":
    sys.exit(main())
